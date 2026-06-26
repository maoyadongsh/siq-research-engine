#!/usr/bin/env python3
"""
Qwen3-VL-Reranker-2B Docker 服务管理脚本。

常用命令：
    python3 Qwen3-VL-Reranker-2B_up.py start
    python3 Qwen3-VL-Reranker-2B_up.py status
    python3 Qwen3-VL-Reranker-2B_up.py restart
    python3 Qwen3-VL-Reranker-2B_up.py recreate
    python3 Qwen3-VL-Reranker-2B_up.py logs
    python3 Qwen3-VL-Reranker-2B_up.py test
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


# --------------------------- Docker 配置 ---------------------------
CONTAINER_NAME = "qwen3-vl-reranker-http"
IMAGE = "vllm/vllm-openai:latest"
HOST_PORT = 8001
CONTAINER_PORT = 8000
MAX_MODEL_LEN = "8192"
GPU_MEMORY_UTILIZATION = "0.10"

SERVER_SCRIPT = Path(
    "/home/maoyd/.openclaw/workspace/ic_master_coordinator_workspace/scripts/qwen3_vl_reranker_http.py"
)

# 优先使用实际有 config.json 的模型目录；避免挂载空目录导致容器启动后立刻退出。
MODEL_CANDIDATES = [
    Path("/home/maoyd/models/Qwen3-VL-Reranker-2B"),
    Path("/home/maoyd/Qwen3-VL-Reranker-2B"),
]

HEALTH_URL = f"http://127.0.0.1:{HOST_PORT}/health"
RERANK_URL = f"http://127.0.0.1:{HOST_PORT}/rerank"


class ServiceError(RuntimeError):
    """Docker 服务命令或健康检查失败。"""


def info(message: str) -> None:
    print(f"[INFO] {message}")


def warn(message: str) -> None:
    print(f"[WARN] {message}")


def error(message: str) -> None:
    print(f"[ERROR] {message}", file=sys.stderr)


def run(command: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )
    if check and result.returncode != 0:
        output = (result.stdout or "").strip()
        raise ServiceError(f"命令失败: {' '.join(command)}\n{output}")
    return result


def post_json(url: str, payload: dict, timeout: float = 10.0) -> dict | None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {}


def get_json(url: str, timeout: float = 3.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {}


def resolve_model_dir() -> Path:
    for model_dir in MODEL_CANDIDATES:
        if (model_dir / "config.json").exists():
            return model_dir
    candidates = "\n".join(f"  - {path}" for path in MODEL_CANDIDATES)
    raise ServiceError(f"未找到可用 reranker 模型目录，已检查:\n{candidates}")


def ensure_paths() -> Path:
    if not SERVER_SCRIPT.exists():
        raise ServiceError(f"reranker HTTP 脚本不存在: {SERVER_SCRIPT}")
    return resolve_model_dir()


def container_exists() -> bool:
    result = run(["docker", "inspect", CONTAINER_NAME], check=False, capture=True)
    return result.returncode == 0


def container_running() -> bool:
    result = run(
        ["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER_NAME],
        check=False,
        capture=True,
    )
    return (result.stdout or "").strip() == "true"


def healthy() -> bool:
    data = get_json(HEALTH_URL)
    return bool(data and data.get("ok") is True)


def wait_health(timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if healthy():
            info(f"reranker 已就绪: {HEALTH_URL}")
            return
        time.sleep(2)
    raise ServiceError(f"reranker 在 {timeout}s 内未就绪，请查看 docker logs {CONTAINER_NAME}")


def docker_run_command(model_dir: Path) -> list[str]:
    return [
        "docker",
        "run",
        "-d",
        "--name",
        CONTAINER_NAME,
        "--gpus",
        "all",
        "--ipc=host",
        "-p",
        f"{HOST_PORT}:{CONTAINER_PORT}",
        "-e",
        f"RERANKER_MAX_MODEL_LEN={MAX_MODEL_LEN}",
        "-e",
        f"RERANKER_GPU_MEMORY_UTILIZATION={GPU_MEMORY_UTILIZATION}",
        "-v",
        f"{model_dir}:/model",
        "-v",
        f"{SERVER_SCRIPT}:/app/qwen3_vl_reranker_http.py",
        "--entrypoint",
        "python3",
        IMAGE,
        "/app/qwen3_vl_reranker_http.py",
    ]


def start_service(timeout: int) -> None:
    model_dir = ensure_paths()
    if container_exists():
        if container_running():
            info(f"容器已运行: {CONTAINER_NAME}")
        else:
            info(f"启动已存在容器: {CONTAINER_NAME}")
            run(["docker", "start", CONTAINER_NAME])
    else:
        info(f"创建并启动 reranker 容器，模型目录={model_dir}")
        run(docker_run_command(model_dir))

    wait_health(timeout)
    show_status()


def stop_service() -> None:
    if not container_exists():
        warn(f"容器不存在: {CONTAINER_NAME}")
        return
    info(f"停止容器: {CONTAINER_NAME}")
    run(["docker", "stop", CONTAINER_NAME], check=False)
    show_status()


def restart_service(timeout: int) -> None:
    if container_exists():
        info(f"重启容器: {CONTAINER_NAME}")
        run(["docker", "restart", CONTAINER_NAME])
    else:
        start_service(timeout)
        return
    wait_health(timeout)
    show_status()


def recreate_service(timeout: int) -> None:
    model_dir = ensure_paths()
    if container_exists():
        info(f"删除旧容器: {CONTAINER_NAME}")
        run(["docker", "rm", "-f", CONTAINER_NAME], check=False)
    info(f"重建 reranker 容器，模型目录={model_dir}")
    run(docker_run_command(model_dir))
    wait_health(timeout)
    show_status()


def show_status() -> None:
    exists = container_exists()
    running = container_running() if exists else False
    health = "健康" if healthy() else "未就绪"
    model_dir = None
    try:
        model_dir = resolve_model_dir()
    except ServiceError:
        pass

    print()
    print("================ Reranker Docker 状态 ================")
    print(f"container: {CONTAINER_NAME}")
    print(f"exists:    {exists}")
    print(f"running:   {running}")
    print(f"HTTP:      {health} {HEALTH_URL}")
    print(f"model:     {model_dir or '未找到'}")
    print("======================================================")
    print()


def show_logs(lines: int) -> None:
    run(["docker", "logs", "--tail", str(lines), CONTAINER_NAME], check=False)


def test_rerank() -> None:
    payload = {
        "query": "股权代持法律风险",
        "documents": [
            "股权代持可能涉及隐名股东资格确认、代持协议效力、出资瑕疵与税务风险。",
            "今天的天气适合外出散步。",
        ],
        "top_k": 2,
    }
    result = post_json(RERANK_URL, payload, timeout=30)
    if not result:
        raise ServiceError(f"rerank 测试失败: {RERANK_URL}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动和管理 Qwen3-VL-Reranker-2B Docker 服务")
    parser.add_argument(
        "command",
        nargs="?",
        default="start",
        choices=("start", "status", "stop", "restart", "recreate", "logs", "test"),
    )
    parser.add_argument("--timeout", type=int, default=300, help="等待服务就绪的秒数，默认 300")
    parser.add_argument("--lines", type=int, default=120, help="logs 输出行数")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "start":
            start_service(args.timeout)
        elif args.command == "status":
            show_status()
        elif args.command == "stop":
            stop_service()
        elif args.command == "restart":
            restart_service(args.timeout)
        elif args.command == "recreate":
            recreate_service(args.timeout)
        elif args.command == "logs":
            show_logs(args.lines)
        elif args.command == "test":
            test_rerank()
    except ServiceError as exc:
        error(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
