#!/usr/bin/env python3
"""
MinerU 本地服务启动脚本。

默认启动两层服务：
1. VLLM:       http://127.0.0.1:8002/v1/models
2. MinerU API: http://127.0.0.1:8003/health

如果本机存在 user systemd 服务，会优先使用 systemctl --user 管理：
- mineru-vllm.service
- mineru-api.service

常用命令：
    python3 MinerU2.5-Pro-2604-1.2B_up.py start
    python3 MinerU2.5-Pro-2604-1.2B_up.py status
    python3 MinerU2.5-Pro-2604-1.2B_up.py restart
    python3 MinerU2.5-Pro-2604-1.2B_up.py stop
    python3 MinerU2.5-Pro-2604-1.2B_up.py logs
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


# --------------------------- 基础配置 ---------------------------
# VLLM 负责加载 MinerU2.5-Pro 模型，MinerU API 负责对外提供解析接口。
VLLM_HOST = "127.0.0.1"
VLLM_PORT = 8002
MINERU_HOST = "127.0.0.1"
MINERU_PORT = 8003

MODEL_NAME = "MinerU2.5-Pro-2604-1.2B"
MODEL_PATH = Path("/home/maoyd/models/mineru-modelscope/MinerU2.5-Pro-2604-1.2B")
MAX_MODEL_LEN = os.environ.get("MINERU_VLLM_MAX_MODEL_LEN", "4096")
# 财报解析质量主要受模型与上下文长度影响；这里仅压缩 vLLM KV cache 预留池。
GPU_MEMORY_UTILIZATION = os.environ.get("MINERU_VLLM_GPU_MEMORY_UTILIZATION", "0.12")
PYTHON_OVERRIDES = Path("/home/maoyd/modles_setup/python_overrides")
ISOLATION_HOME = Path("/home/maoyd/.cache/mineru_vllm")
TORCH_COMPILE_CACHE = ISOLATION_HOME / "torch_compile_cache"
TRITON_CACHE = ISOLATION_HOME / "triton"
VLLM_CACHE = ISOLATION_HOME / "vllm"
HF_HOME = Path("/home/maoyd/hf_cache_mineru")

# VLLM 使用 conda 环境；MinerU API 使用单独的 venv。
CONDA_ENV = "mineru_vllm_clean"
CONDA_EXE = Path(os.environ.get("CONDA_EXE", "/home/maoyd/miniconda3/bin/conda"))
MINERU_API_BIN = Path("/home/maoyd/.venvs/mineru_native/bin/mineru-api")

VLLM_LOG = Path("/tmp/vllm_mineru.log")
MINERU_LOG = Path("/tmp/mineru_api.log")
VLLM_PID_FILE = Path("/tmp/vllm_service.pid")
MINERU_PID_FILE = Path("/tmp/mineru_api.pid")
MAX_LOG_BYTES = 128 * 1024 * 1024
SYSTEMD_VLLM_SERVICE = "mineru-vllm.service"
SYSTEMD_MINERU_API_SERVICE = "mineru-api.service"

VLLM_MODEL_URL = f"http://{VLLM_HOST}:{VLLM_PORT}/v1/models"
MINERU_HEALTH_URL = f"http://{MINERU_HOST}:{MINERU_PORT}/health"


class ServiceError(RuntimeError):
    """启动前置条件或服务健康检查失败。"""


def info(message: str) -> None:
    print(f"[INFO] {message}")


def warn(message: str) -> None:
    print(f"[WARN] {message}")


def error(message: str) -> None:
    print(f"[ERROR] {message}", file=sys.stderr)


def run_checked(command: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise ServiceError(f"命令不存在: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ServiceError(f"命令超时: {' '.join(command)}") from exc

    if result.returncode != 0:
        output = (result.stderr or result.stdout).strip()
        raise ServiceError(f"命令失败: {' '.join(command)}\n{output}")
    return result


def systemd_service_loaded(service: str) -> bool:
    command = ["systemctl", "--user", "show", "--property=LoadState", "--value", service]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() == "loaded"


def systemd_services_available() -> bool:
    return all(
        systemd_service_loaded(service)
        for service in (SYSTEMD_VLLM_SERVICE, SYSTEMD_MINERU_API_SERVICE)
    )


def systemctl_user(*args: str) -> None:
    run_checked(["systemctl", "--user", *args])


def journal_tail(service: str, line_count: int = 30) -> str | None:
    command = [
        "journalctl",
        "--user",
        "-u",
        service,
        "-n",
        str(line_count),
        "--no-pager",
        "--output=cat",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def http_json(url: str, timeout: float = 2.0) -> dict | None:
    """请求健康检查接口；失败时返回 None，避免启动流程被异常打断。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {}


def port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    """只判断端口是否被占用；健康状态仍以 HTTP 接口为准。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def listener_inodes(host: str, port: int) -> set[str]:
    """从 /proc/net/tcp 找到指定 IPv4 监听端口对应的 socket inode。"""
    path = Path("/proc/net/tcp")
    try:
        rows = path.read_text(encoding="utf-8").splitlines()[1:]
    except OSError:
        return set()

    inodes: set[str] = set()
    for row in rows:
        parts = row.split()
        if len(parts) < 10 or parts[3] != "0A":
            continue

        local_address, local_port_hex = parts[1].split(":")
        if int(local_port_hex, 16) != port:
            continue

        local_host = socket.inet_ntoa(bytes.fromhex(local_address)[::-1])
        if local_host in {host, "0.0.0.0"}:
            inodes.add(parts[9])
    return inodes


def listener_pids(host: str, port: int) -> list[int]:
    """根据监听 socket inode 反查 PID，避免 PID 文件过期时无法停止服务。"""
    inodes = listener_inodes(host, port)
    if not inodes:
        return []

    pids: set[int] = set()
    for fd_dir in Path("/proc").glob("[0-9]*/fd"):
        try:
            pid = int(fd_dir.parent.name)
        except ValueError:
            continue

        try:
            fds = list(fd_dir.iterdir())
        except OSError:
            continue

        for fd in fds:
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if target.startswith("socket:[") and target[8:-1] in inodes:
                pids.add(pid)
                break

    return sorted(pids)


def vllm_healthy() -> bool:
    data = http_json(VLLM_MODEL_URL)
    return bool(data and data.get("object") == "list")


def mineru_healthy() -> bool:
    data = http_json(MINERU_HEALTH_URL)
    return bool(data and data.get("status") == "healthy")


def wait_until(name: str, check, timeout: int, log_path: Path) -> None:
    """等待服务就绪；超时后提示对应日志，方便直接定位问题。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            info(f"{name} 已就绪")
            return
        time.sleep(2)

    raise ServiceError(f"{name} 在 {timeout}s 内未就绪，请查看日志: {log_path}")


def rotate_log_if_large(log_path: Path) -> None:
    """启动新进程前轮转过大的旧日志，避免长期运行后 logs 命令变慢。"""
    try:
        if log_path.stat().st_size <= MAX_LOG_BYTES:
            return
    except FileNotFoundError:
        return

    rotated_path = log_path.with_suffix(log_path.suffix + ".1")
    rotated_path.unlink(missing_ok=True)
    log_path.rename(rotated_path)
    info(f"日志过大，已轮转: {log_path} -> {rotated_path}")


def ensure_paths() -> None:
    """启动前检查本机依赖，避免命令后台失败但终端没有明显报错。"""
    missing: list[str] = []
    if not CONDA_EXE.exists():
        missing.append(f"conda 不存在: {CONDA_EXE}")
    if not MODEL_PATH.exists():
        missing.append(f"模型目录不存在: {MODEL_PATH}")
    if not MINERU_API_BIN.exists():
        missing.append(f"mineru-api 不存在: {MINERU_API_BIN}")
    if not PYTHON_OVERRIDES.exists():
        missing.append(f"Python 隔离依赖目录不存在: {PYTHON_OVERRIDES}")

    if missing:
        raise ServiceError("\n".join(missing))

    for path in (ISOLATION_HOME, TORCH_COMPILE_CACHE, TRITON_CACHE, VLLM_CACHE, HF_HOME):
        path.mkdir(parents=True, exist_ok=True)


def start_background(
    command: list[str],
    log_path: Path,
    pid_file: Path,
    env: dict[str, str] | None = None,
) -> int:
    """以独立会话启动后台进程，并把 stdout/stderr 写入日志文件。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    rotate_log_if_large(log_path)
    child_env = os.environ.copy()
    for key in (
        "QWEN36_VLLM_BIN",
        "MODEL_DIR",
        "SERVED_MODEL_NAME",
        "MAX_MODEL_LEN",
        "GPU_MEMORY_UTILIZATION",
        "KV_CACHE_DTYPE",
        "DEFAULT_CHAT_TEMPLATE_KWARGS",
        "REASONING_PARSER",
        "ENABLE_AUTO_TOOL_CHOICE",
        "TOOL_CALL_PARSER",
        "ENABLE_PREFIX_CACHING",
        "LANGUAGE_MODEL_ONLY",
    ):
        child_env.pop(key, None)
    if env:
        child_env.update(env)

    log_file = log_path.open("a", buffering=1)
    process = subprocess.Popen(
        command,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=child_env,
        start_new_session=True,
        text=True,
    )
    pid_file.write_text(str(process.pid), encoding="utf-8")
    return process.pid


def vllm_env() -> dict[str, str]:
    """给 MinerU vLLM 子进程补齐 CUDA 动态库路径。"""
    site_packages = Path(f"/home/maoyd/miniconda3/envs/{CONDA_ENV}/lib/python3.12/site-packages")
    cuda_lib_dirs = [
        site_packages / "torch/lib",
        site_packages / "nvidia/cu13/lib",
        site_packages / "nvidia/cudnn/lib",
        site_packages / "nvidia/nccl/lib",
        site_packages / "nvidia/cusparselt/lib",
        Path("/usr/local/lib/ollama/cuda_v12"),
    ]
    existing = [str(path) for path in cuda_lib_dirs if path.exists()]
    current_ld = os.environ.get("LD_LIBRARY_PATH", "")
    ld_library_path = ":".join(existing + ([current_ld] if current_ld else []))

    python_path_parts = [str(PYTHON_OVERRIDES)] if PYTHON_OVERRIDES.exists() else []
    current_python_path = os.environ.get("PYTHONPATH", "")
    if current_python_path:
        python_path_parts.append(current_python_path)

    return {
        "LD_LIBRARY_PATH": ld_library_path,
        "PYTHONPATH": ":".join(python_path_parts),
        "HF_HOME": str(HF_HOME),
        "HUGGINGFACE_HUB_CACHE": str(HF_HOME / "hub"),
        "VLLM_CACHE_ROOT": str(VLLM_CACHE),
        "TORCHINDUCTOR_CACHE_DIR": str(TORCH_COMPILE_CACHE),
        "TRITON_CACHE_DIR": str(TRITON_CACHE),
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_OFFLINE": "1",
        "FLASHINFER_DISABLE_VERSION_CHECK": "1",
        "VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS": "1",
        "VLLM_NO_USAGE_STATS": "1",
        "PYTHONUNBUFFERED": "1",
    }


def start_vllm(timeout: int) -> None:
    if vllm_healthy():
        sync_pid_file("VLLM", VLLM_HOST, VLLM_PORT, VLLM_PID_FILE)
        info(f"VLLM 已运行: {VLLM_MODEL_URL}")
        return

    if port_open(VLLM_HOST, VLLM_PORT):
        raise ServiceError(f"端口 {VLLM_PORT} 已被占用，但 VLLM 健康检查失败: {VLLM_MODEL_URL}")

    command = [
        str(CONDA_EXE),
        "run",
        "-n",
        CONDA_ENV,
        "--no-capture-output",
        "vllm",
        "serve",
        str(MODEL_PATH),
        "--served-model-name",
        MODEL_NAME,
        "--trust-remote-code",
        "--host",
        VLLM_HOST,
        "--port",
        str(VLLM_PORT),
        "--gpu-memory-utilization",
        GPU_MEMORY_UTILIZATION,
        "--max-model-len",
        MAX_MODEL_LEN,
    ]

    pid = start_background(command, VLLM_LOG, VLLM_PID_FILE, env=vllm_env())
    info(f"正在启动 VLLM，PID={pid}，日志={VLLM_LOG}")
    wait_until("VLLM", vllm_healthy, timeout, VLLM_LOG)
    sync_pid_file("VLLM", VLLM_HOST, VLLM_PORT, VLLM_PID_FILE)


def start_mineru_api(timeout: int) -> None:
    if mineru_healthy():
        sync_pid_file("MinerU API", MINERU_HOST, MINERU_PORT, MINERU_PID_FILE)
        info(f"MinerU API 已运行: {MINERU_HEALTH_URL}")
        return

    if port_open(MINERU_HOST, MINERU_PORT):
        raise ServiceError(f"端口 {MINERU_PORT} 已被占用，但 MinerU API 健康检查失败: {MINERU_HEALTH_URL}")

    command = [
        str(MINERU_API_BIN),
        "--host",
        MINERU_HOST,
        "--port",
        str(MINERU_PORT),
    ]
    env = {
        # 强制使用本地模型，避免启动时联网下载。
        "MINERU_MODEL_SOURCE": "local",
        "PYTHONUNBUFFERED": "1",
    }

    pid = start_background(command, MINERU_LOG, MINERU_PID_FILE, env=env)
    info(f"正在启动 MinerU API，PID={pid}，日志={MINERU_LOG}")
    wait_until("MinerU API", mineru_healthy, timeout, MINERU_LOG)
    sync_pid_file("MinerU API", MINERU_HOST, MINERU_PORT, MINERU_PID_FILE)


def read_pid(pid_file: Path) -> int | None:
    try:
        return int(pid_file.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False

    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return True

    return ") Z " not in stat


def process_command(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", errors="replace").strip()


def sync_pid_file(name: str, host: str, port: int, pid_file: Path) -> None:
    pids = listener_pids(host, port)
    if not pids:
        return
    if len(pids) > 1:
        warn(f"{name} 端口 {port} 有多个监听 PID: {', '.join(map(str, pids))}")
    pid_file.write_text(str(pids[0]), encoding="utf-8")


def stop_pid(pid: int, name: str) -> None:
    """优先停止脚本启动的进程组；失败时再停止单进程。"""
    if not process_alive(pid):
        return

    info(f"停止 {name}，PID={pid}")
    try:
        if os.getsid(pid) == pid:
            os.killpg(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError:
        return

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not process_alive(pid):
            return
        time.sleep(0.5)

    warn(f"{name} 未正常退出，执行强制停止，PID={pid}")
    try:
        if os.getsid(pid) == pid:
            os.killpg(pid, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def wait_for_port_closed(name: str, host: str, port: int, timeout: int = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not port_open(host, port):
            return
        time.sleep(0.5)
    warn(f"{name} 端口 {port} 仍被占用")


def stop_service(name: str, pid_file: Path, host: str, port: int, command_markers: tuple[str, ...]) -> None:
    pid = read_pid(pid_file)
    pids = set(listener_pids(host, port))

    if pid is None:
        warn(f"{name} 没有找到 PID 文件: {pid_file}")
    elif process_alive(pid):
        command = process_command(pid)
        if pid in pids or any(marker in command for marker in command_markers):
            pids.add(pid)
        else:
            warn(f"{name} PID 文件指向非本服务进程，已忽略: PID={pid}")
    else:
        warn(f"{name} PID 文件已过期: PID={pid}")

    if not pids:
        warn(f"{name} 没有找到可停止的监听进程: {host}:{port}")
    for found_pid in sorted(pids):
        stop_pid(found_pid, name)

    wait_for_port_closed(name, host, port)
    pid_file.unlink(missing_ok=True)


def start_services(timeout: int) -> None:
    ensure_paths()
    if systemd_services_available():
        start_systemd_services(timeout)
        return

    start_vllm(timeout)
    start_mineru_api(timeout)
    show_status()


def stop_services() -> None:
    if systemd_services_available():
        stop_systemd_services()
        return

    # 先停 API，再停底层 VLLM，避免 API 仍在处理请求时底层模型先退出。
    stop_service("MinerU API", MINERU_PID_FILE, MINERU_HOST, MINERU_PORT, ("mineru-api",))
    stop_service("VLLM", VLLM_PID_FILE, VLLM_HOST, VLLM_PORT, ("vllm serve", MODEL_NAME, str(MODEL_PATH)))
    show_status()


def start_systemd_services(timeout: int) -> None:
    info("检测到 user systemd 服务，使用 systemctl --user 启动")
    systemctl_user("start", SYSTEMD_VLLM_SERVICE)
    wait_until("VLLM", vllm_healthy, timeout, VLLM_LOG)
    sync_pid_file("VLLM", VLLM_HOST, VLLM_PORT, VLLM_PID_FILE)

    systemctl_user("start", SYSTEMD_MINERU_API_SERVICE)
    wait_until("MinerU API", mineru_healthy, timeout, MINERU_LOG)
    sync_pid_file("MinerU API", MINERU_HOST, MINERU_PORT, MINERU_PID_FILE)
    show_status()


def stop_systemd_services() -> None:
    info("检测到 user systemd 服务，使用 systemctl --user 停止")
    systemctl_user("stop", SYSTEMD_MINERU_API_SERVICE)
    wait_for_port_closed("MinerU API", MINERU_HOST, MINERU_PORT)
    systemctl_user("stop", SYSTEMD_VLLM_SERVICE)
    wait_for_port_closed("VLLM", VLLM_HOST, VLLM_PORT)
    MINERU_PID_FILE.unlink(missing_ok=True)
    VLLM_PID_FILE.unlink(missing_ok=True)
    show_status()


def show_status() -> None:
    vllm_state = "运行中" if vllm_healthy() else "未运行"
    mineru_state = "运行中" if mineru_healthy() else "未运行"
    vllm_pids = listener_pids(VLLM_HOST, VLLM_PORT)
    mineru_pids = listener_pids(MINERU_HOST, MINERU_PORT)
    systemd_mode = systemd_services_available()
    manager = "user systemd" if systemd_mode else "direct/PID"
    print()
    print("================ MinerU 服务状态 ================")
    print(f"管理方式:    {manager}")
    print(f"VLLM:       {vllm_state}  PID={','.join(map(str, vllm_pids)) or '-'}  {VLLM_MODEL_URL}")
    print(f"MinerU API: {mineru_state}  PID={','.join(map(str, mineru_pids)) or '-'}  {MINERU_HEALTH_URL}")
    print("日志:")
    if systemd_mode:
        print(f"  VLLM:       journalctl --user -u {SYSTEMD_VLLM_SERVICE}")
        print(f"  MinerU API: {MINERU_LOG}")
    else:
        print(f"  VLLM:       {VLLM_LOG}")
        print(f"  MinerU API: {MINERU_LOG}")
    print("================================================")
    print()


def tail_lines(path: Path, line_count: int = 30) -> list[str]:
    chunks: list[bytes] = []
    newlines = 0
    block_size = 8192

    with path.open("rb") as file:
        file.seek(0, os.SEEK_END)
        position = file.tell()
        while position > 0 and newlines <= line_count:
            read_size = min(block_size, position)
            position -= read_size
            file.seek(position)
            chunk = file.read(read_size)
            chunks.append(chunk)
            newlines += chunk.count(b"\n")

    data = b"".join(reversed(chunks))
    return data.decode("utf-8", errors="replace").splitlines()[-line_count:]


def show_logs() -> None:
    if systemd_services_available():
        print(f"VLLM 日志:       journalctl --user -u {SYSTEMD_VLLM_SERVICE}")
        print(f"MinerU API 日志: {MINERU_LOG}")
        print()

        print("-------- VLLM 最近日志 --------")
        logs = journal_tail(SYSTEMD_VLLM_SERVICE)
        print(logs if logs else "未能读取 journal 日志")
        print()

        print("-------- MinerU API 最近日志 --------")
        if not MINERU_LOG.exists():
            print("日志文件还不存在")
        else:
            lines = tail_lines(MINERU_LOG)
            print("\n".join(lines) if lines else "日志文件为空")
        print()
        return

    print(f"VLLM 日志:       {VLLM_LOG}")
    print(f"MinerU API 日志: {MINERU_LOG}")
    print()
    for name, path in (("VLLM", VLLM_LOG), ("MinerU API", MINERU_LOG)):
        print(f"-------- {name} 最近日志 --------")
        if not path.exists():
            print("日志文件还不存在")
            continue
        lines = tail_lines(path)
        print("\n".join(lines) if lines else "日志文件为空")
        print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动和管理本机 MinerU 服务")
    parser.add_argument(
        "command",
        nargs="?",
        default="start",
        choices=("start", "status", "stop", "restart", "logs"),
        help="默认 start；status 查看状态；logs 查看最近日志",
    )
    parser.add_argument("--timeout", type=int, default=180, help="启动健康检查等待秒数，默认 180")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "start":
            start_services(args.timeout)
        elif args.command == "status":
            show_status()
        elif args.command == "stop":
            stop_services()
        elif args.command == "restart":
            stop_services()
            start_services(args.timeout)
        elif args.command == "logs":
            show_logs()
    except ServiceError as exc:
        error(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
