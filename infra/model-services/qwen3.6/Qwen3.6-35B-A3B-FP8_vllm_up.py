#!/usr/bin/env python3
"""
Qwen3.6 35B A3B FP8 的 vLLM 管理脚本。

默认推荐使用 systemd 用户服务：
    python3 Qwen3.6-35B-A3B-FP8_vllm_up.py start
    python3 Qwen3.6-35B-A3B-FP8_vllm_up.py status
    python3 Qwen3.6-35B-A3B-FP8_vllm_up.py restart
    python3 Qwen3.6-35B-A3B-FP8_vllm_up.py logs

如果临时不想走 systemd，也可以用手动后台模式：
    python3 Qwen3.6-35B-A3B-FP8_vllm_up.py start-manual
    python3 Qwen3.6-35B-A3B-FP8_vllm_up.py start-manual --no-tools
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


# --------------------------- 固定配置 ---------------------------
SERVICE_NAME = "qwen36-vllm.service"
BASE_DIR = Path(__file__).resolve().parent
START_SCRIPT = BASE_DIR / "serve_qwen36_fp8_vllm_newenv.sh"
MODEL_CONFIG = Path("/home/maoyd/models/Qwen3.6-35B-A3B-FP8-modelscope/config.json")

HOST = "127.0.0.1"
PORT = 8004
MODEL_NAME = "Qwen3.6-35B-A3B-FP8"
MAX_MODEL_LEN = "262144"
GPU_MEMORY_UTILIZATION = "0.39"
DTYPE = "bfloat16"
KV_CACHE_DTYPE = "fp8"
MAX_NUM_BATCHED_TOKENS = "32768"
MAX_NUM_SEQS = "2"
REASONING_PARSER = "qwen3"
DEFAULT_CHAT_TEMPLATE_KWARGS = '{"enable_thinking":true}'

MANUAL_LOG = Path("/tmp/qwen36_vllm_manual.log")
MANUAL_PID_FILE = Path("/tmp/qwen36_vllm_manual.pid")
MANUAL_MODE_FILE = Path("/tmp/qwen36_vllm_manual.mode")
MODELS_URL = f"http://{HOST}:{PORT}/v1/models"


class ServiceError(RuntimeError):
    """服务启动、检查或命令执行失败。"""


def info(message: str) -> None:
    print(f"[INFO] {message}")


def warn(message: str) -> None:
    print(f"[WARN] {message}")


def error(message: str) -> None:
    print(f"[ERROR] {message}", file=sys.stderr)


def run(command: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess[str]:
    """统一执行外部命令，便于输出错误信息。"""
    try:
        return subprocess.run(
            command,
            check=check,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.STDOUT if capture else None,
        )
    except subprocess.CalledProcessError as exc:
        output = (exc.stdout or "").strip()
        raise ServiceError(f"命令失败: {' '.join(command)}\n{output}") from exc


def http_json(url: str, timeout: float = 2.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def vllm_healthy() -> bool:
    data = http_json(MODELS_URL)
    return bool(data and data.get("object") == "list")


def recent_service_logs(lines: int = 80) -> str:
    result = run(
        ["journalctl", "--user", "-u", SERVICE_NAME, "-n", str(lines), "--no-pager"],
        check=False,
        capture=True,
    )
    return (result.stdout or "").strip()


def wait_for_vllm(timeout: int, watch_systemd: bool = False) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if vllm_healthy():
            info(f"Qwen3.6 vLLM 已就绪: {MODELS_URL}")
            return
        if watch_systemd:
            state = systemd_state()
            if state in {"failed", "inactive"}:
                logs = recent_service_logs()
                raise ServiceError(
                    f"{SERVICE_NAME} 已进入 {state} 状态，vLLM 未就绪。\n"
                    f"最近日志:\n{logs}"
                )
        time.sleep(5)
    raise ServiceError(f"Qwen3.6 vLLM 在 {timeout}s 内未就绪，请查看 logs")


def ensure_paths() -> None:
    missing: list[str] = []
    if not START_SCRIPT.exists():
        missing.append(f"启动脚本不存在: {START_SCRIPT}")
    if not MODEL_CONFIG.exists():
        missing.append(f"模型 config 不存在: {MODEL_CONFIG}")
    if missing:
        raise ServiceError("\n".join(missing))


def systemd_state() -> str:
    result = run(["systemctl", "--user", "is-active", SERVICE_NAME], check=False, capture=True)
    return (result.stdout or "").strip() or "unknown"


def service_start(timeout: int) -> None:
    ensure_paths()
    info(f"启动/恢复 systemd 服务: {SERVICE_NAME}")
    # failed 状态下 restart 比 start 更稳，也符合你当前常用命令。
    run(["systemctl", "--user", "restart", SERVICE_NAME])
    wait_for_vllm(timeout, watch_systemd=True)
    show_status()


def service_stop() -> None:
    info(f"停止 systemd 服务: {SERVICE_NAME}")
    run(["systemctl", "--user", "stop", SERVICE_NAME], check=False)
    show_status()


def service_restart(timeout: int) -> None:
    service_start(timeout)


def service_logs(lines: int) -> None:
    run(["journalctl", "--user", "-u", SERVICE_NAME, "-n", str(lines), "--no-pager"], check=False)


def manual_env(enable_tools: bool) -> dict[str, str]:
    """只覆盖手动模式差异，其他默认值交给 shell 脚本兜底。"""
    env = os.environ.copy()
    env["LANGUAGE_MODEL_ONLY"] = "0"

    if enable_tools:
        env["ENABLE_AUTO_TOOL_CHOICE"] = "1"
        env["TOOL_CALL_PARSER"] = "qwen3_coder"
    else:
        env["ENABLE_AUTO_TOOL_CHOICE"] = "0"
        env.pop("TOOL_CALL_PARSER", None)
    return env


def write_manual_mode(enable_tools: bool) -> None:
    MANUAL_MODE_FILE.write_text("tool-call\n" if enable_tools else "no-tools\n", encoding="utf-8")


def read_manual_mode(default: bool) -> bool:
    try:
        mode = MANUAL_MODE_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return default
    if mode == "no-tools":
        return False
    if mode == "tool-call":
        return True
    return default


def start_manual(timeout: int, enable_tools: bool) -> None:
    ensure_paths()
    if vllm_healthy():
        info(f"Qwen3.6 vLLM 已运行: {MODELS_URL}")
        return
    if port_open(HOST, PORT):
        raise ServiceError(f"端口 {PORT} 已被占用，但健康检查失败: {MODELS_URL}")

    command = ["bash", str(START_SCRIPT)]
    write_manual_mode(enable_tools)
    with MANUAL_LOG.open("a", buffering=1) as log_file:
        process = subprocess.Popen(
            command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=manual_env(enable_tools),
            start_new_session=True,
            text=True,
        )
        MANUAL_PID_FILE.write_text(str(process.pid), encoding="utf-8")
        mode = "tool-call" if enable_tools else "no-tools"
        info(f"手动后台启动 Qwen3.6 vLLM，mode={mode}，PID={process.pid}，日志={MANUAL_LOG}")
        try:
            wait_for_vllm(timeout)
        except BaseException:
            stop_pid_group(process.pid)
            MANUAL_PID_FILE.unlink(missing_ok=True)
            MANUAL_MODE_FILE.unlink(missing_ok=True)
            raise
    show_status()


def read_pid(pid_file: Path) -> int | None:
    try:
        return int(pid_file.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def stop_pid_group(pid: int) -> None:
    if not process_alive(pid):
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        os.kill(pid, signal.SIGTERM)

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if not process_alive(pid):
            return
        time.sleep(1)

    warn(f"手动进程未退出，强制停止 PID={pid}")
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def stop_manual() -> None:
    pid = read_pid(MANUAL_PID_FILE)
    if pid is None:
        warn(f"未找到手动模式 PID 文件: {MANUAL_PID_FILE}")
        MANUAL_MODE_FILE.unlink(missing_ok=True)
        return
    info(f"停止手动模式 Qwen3.6 vLLM，PID={pid}")
    stop_pid_group(pid)
    MANUAL_PID_FILE.unlink(missing_ok=True)
    MANUAL_MODE_FILE.unlink(missing_ok=True)
    show_status()


def show_status() -> None:
    state = systemd_state()
    health = "健康" if vllm_healthy() else "未就绪"
    print()
    print("================ Qwen3.6 vLLM 状态 ================")
    print(f"systemd: {state} ({SERVICE_NAME})")
    print(f"HTTP:    {health} {MODELS_URL}")
    print(f"manual:  pid_file={MANUAL_PID_FILE}, log={MANUAL_LOG}")
    print("===================================================")
    print()


def show_manual_logs(lines: int) -> None:
    print(f"手动模式日志: {MANUAL_LOG}")
    if not MANUAL_LOG.exists():
        print("日志文件还不存在")
        return
    content = MANUAL_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    print("\n".join(content))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动和管理 Qwen3.6 FP8 vLLM")
    parser.add_argument(
        "command",
        nargs="?",
        default="start",
        choices=(
            "start",
            "status",
            "stop",
            "restart",
            "logs",
            "start-manual",
            "stop-manual",
            "restart-manual",
            "manual-logs",
        ),
    )
    parser.add_argument("--timeout", type=int, default=900, help="等待 vLLM 就绪的秒数，默认 900")
    parser.add_argument("--lines", type=int, default=120, help="logs/manual-logs 输出行数")
    parser.add_argument("--no-tools", action="store_true", help="手动模式不启用 auto tool choice")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "start":
            service_start(args.timeout)
        elif args.command == "status":
            show_status()
        elif args.command == "stop":
            service_stop()
        elif args.command == "restart":
            service_restart(args.timeout)
        elif args.command == "logs":
            service_logs(args.lines)
        elif args.command == "start-manual":
            start_manual(args.timeout, enable_tools=not args.no_tools)
        elif args.command == "stop-manual":
            stop_manual()
        elif args.command == "restart-manual":
            enable_tools = False if args.no_tools else read_manual_mode(default=True)
            stop_manual()
            start_manual(args.timeout, enable_tools=enable_tools)
        elif args.command == "manual-logs":
            show_manual_logs(args.lines)
    except ServiceError as exc:
        error(str(exc))
        return 1
    except KeyboardInterrupt:
        warn("操作已中断")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
