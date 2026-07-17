#!/usr/bin/env python3
"""Exercise the NOT_PRODUCTION siq_analysis/OpenShell observe API contract."""

from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

FIXED_BASE_URL = "http://127.0.0.1:28651"
TOOL_MARKER = "SIQ_OBSERVE_SUM=16"
TERMINAL_EVENTS = {"run.cancelled", "run.completed", "run.failed"}
API_KEY = ""

TOOL_PROMPT = """这是一次隔离的 SIQ OpenShell observe-only 链路测试。
必须且只需调用一次 terminal 工具，执行下面这条只读计算命令：
python -c "values=[3,5,8]; print('SIQ_OBSERVE_SUM='+str(sum(values)))"
读取工具结果后，用一句中文说明计算结论，并原样包含 SIQ_OBSERVE_SUM=16。
不要访问网络，不要读取或写入任何项目文件。
"""

STOP_PROMPT = """这是一次取消链路测试。请立即调用 terminal 工具执行：
python -c "import time; print('SIQ_OBSERVE_STOP_READY', flush=True); time.sleep(60)"
不要访问网络或项目文件。命令完成后再回答。
"""


def request_json(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    authorization: str | None = None,
    timeout: float = 10,
) -> tuple[int, dict[str, Any]]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers: dict[str, str] = {}
    if authorization is not None:
        headers["Authorization"] = authorization
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=body,
        method=method,
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload_value = json.load(response)
        if not isinstance(payload_value, dict):
            raise AssertionError("JSON response must be an object")
        return response.status, payload_value


def require_unauthorized(base_url: str, authorization: str | None) -> None:
    try:
        request_json(
            base_url,
            "POST",
            "/v1/runs",
            {"input": "SIQ_OBSERVE_AUTH_NEGATIVE"},
            authorization=authorization,
        )
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise AssertionError(f"unauthorized request returned HTTP {exc.code}") from exc
    else:
        raise AssertionError("unauthorized request was accepted")


def start_run(base_url: str, prompt: str) -> str:
    status, payload = request_json(
        base_url,
        "POST",
        "/v1/runs",
        {"input": prompt},
        authorization=f"Bearer {API_KEY}",
    )
    run_id = payload.get("run_id")
    if status != 202 or payload.get("status") != "started":
        raise AssertionError(f"run create contract failed: HTTP {status}")
    if not isinstance(run_id, str) or not run_id.startswith("run_"):
        raise AssertionError("run create returned an invalid run_id")
    return run_id


def collect_events(
    base_url: str,
    run_id: str,
    *,
    wall_timeout: float = 180,
    signal_event: str | None = None,
    signal: threading.Event | None = None,
) -> list[dict[str, Any]]:
    request = urllib.request.Request(
        f"{base_url}/v1/runs/{run_id}/events",
        method="GET",
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    events: list[dict[str, Any]] = []
    deadline = time.monotonic() + wall_timeout
    with urllib.request.urlopen(request, timeout=min(wall_timeout, 30)) as response:
        if response.status != 200 or response.headers.get_content_type() != "text/event-stream":
            raise AssertionError("SSE contract failed")
        for raw_line in response:
            if time.monotonic() >= deadline:
                raise AssertionError("SSE stream exceeded the wall timeout")
            line = raw_line.decode("utf-8").strip()
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            if not isinstance(event, dict) or event.get("run_id") != run_id:
                raise AssertionError("invalid SSE event envelope")
            events.append(event)
            if signal is not None and event.get("event") == signal_event:
                signal.set()
    return events


def require_event(events: list[dict[str, Any]], event_type: str) -> None:
    if not any(event.get("event") == event_type for event in events):
        raise AssertionError(f"missing SSE event: {event_type}")


def wait_for_status(
    base_url: str, run_id: str, expected: set[str], *, timeout: float = 30
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status, last = request_json(
            base_url,
            "GET",
            f"/v1/runs/{run_id}",
            authorization=f"Bearer {API_KEY}",
        )
        if status == 200 and last.get("status") in expected:
            return last
        time.sleep(0.1)
    raise AssertionError(f"run did not reach {sorted(expected)}; last={last.get('status')}")


def _exercise_tool_run(base_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    run_id = start_run(base_url, TOOL_PROMPT)
    events = collect_events(base_url, run_id)
    for event_type in ("message.delta", "tool.started", "tool.completed", "run.completed"):
        require_event(events, event_type)
    started_tools = [event for event in events if event.get("event") == "tool.started"]
    completed_tools = [event for event in events if event.get("event") == "tool.completed"]
    if len(started_tools) != 1 or len(completed_tools) != 1:
        raise AssertionError("tool run must contain exactly one tool invocation")
    if started_tools[0].get("tool") != "terminal" or completed_tools[0].get("tool") != "terminal":
        raise AssertionError("tool run used a tool other than terminal")
    terminal = [event for event in events if event.get("event") in TERMINAL_EVENTS]
    if len(terminal) != 1 or terminal[0].get("event") != "run.completed":
        raise AssertionError("tool run did not produce exactly one successful terminal event")
    result = wait_for_status(base_url, run_id, {"completed"})
    if TOOL_MARKER not in str(result.get("output") or terminal[0].get("output") or ""):
        raise AssertionError("tool calculation marker is absent from the final output")
    return result, events


def _exercise_stop_run(base_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    run_id = start_run(base_url, STOP_PROMPT)
    tool_started = threading.Event()
    events: list[dict[str, Any]] = []
    errors: list[BaseException] = []

    def consume() -> None:
        try:
            events.extend(
                collect_events(
                    base_url,
                    run_id,
                    wall_timeout=120,
                    signal_event="tool.started",
                    signal=tool_started,
                )
            )
        except BaseException as exc:  # surfaced below in the controlling thread
            errors.append(exc)

    thread = threading.Thread(target=consume, name="siq-observe-stop-sse", daemon=True)
    thread.start()
    if not tool_started.wait(timeout=90):
        raise AssertionError("stop probe did not start its terminal tool")
    status, payload = request_json(
        base_url,
        "POST",
        f"/v1/runs/{run_id}/stop",
        {},
        authorization=f"Bearer {API_KEY}",
    )
    if status != 200 or payload != {"run_id": run_id, "status": "stopping"}:
        raise AssertionError("run stop contract failed")
    thread.join(timeout=45)
    if thread.is_alive():
        raise AssertionError("cancelled SSE stream did not close")
    if errors:
        raise AssertionError(f"cancelled SSE stream failed: {errors[0]}")
    require_event(events, "run.cancelled")
    if any(event.get("event") in {"run.completed", "run.failed"} for event in events):
        raise AssertionError("cancelled run emitted a conflicting terminal event")
    result = wait_for_status(base_url, run_id, {"cancelled"})
    return result, events


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=FIXED_BASE_URL)
    parser.add_argument("--api-key-file", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    global API_KEY
    args = _parser().parse_args(argv)
    if args.base_url != FIXED_BASE_URL:
        raise SystemExit("observe contract is pinned to http://127.0.0.1:28651")
    if not args.api_key_file.is_file() or args.api_key_file.is_symlink():
        raise SystemExit("observe API key must be a regular, non-symlink file")
    API_KEY = args.api_key_file.read_text(encoding="ascii").strip()
    if len(API_KEY) != 64 or any(character not in "0123456789abcdef" for character in API_KEY):
        raise SystemExit("observe API key file is invalid")

    require_unauthorized(args.base_url, None)
    require_unauthorized(args.base_url, "Bearer definitely-not-the-observe-key")
    health_status, health = request_json(
        args.base_url,
        "GET",
        "/health",
        authorization=f"Bearer {API_KEY}",
    )
    if health_status != 200 or health.get("status") != "ok":
        raise AssertionError("Hermes health contract failed")

    tool_result, tool_events = _exercise_tool_run(args.base_url)
    stop_result, stop_events = _exercise_stop_run(args.base_url)
    print(
        json.dumps(
            {
                "schema_version": "siq.openshell.siq_analysis_observe_contract.v1",
                "readiness_effect": "none",
                "runtime": "openshell",
                "profile": "siq_analysis",
                "mode": "NOT_PRODUCTION_OBSERVE_ONLY",
                "health": "ok",
                "tool_run": tool_result.get("status"),
                "tool_event_count": len(tool_events),
                "stop_run": stop_result.get("status"),
                "stop_event_count": len(stop_events),
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.URLError as exc:
        raise SystemExit(f"observe endpoint unavailable: {exc.reason}") from exc
