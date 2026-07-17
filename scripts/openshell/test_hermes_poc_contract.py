#!/usr/bin/env python3
"""Exercise the SIQ-patched Hermes /v1/runs contract over the PoC forward."""

from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

API_KEY = ""


def request_json(
    base_url: str, method: str, path: str, payload: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Authorization": f"Bearer {API_KEY}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=body,
        method=method,
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, json.load(response)


def start_run(base_url: str, prompt: str) -> str:
    status, payload = request_json(base_url, "POST", "/v1/runs", {"input": prompt})
    if status != 202 or payload.get("status") != "started":
        raise AssertionError(f"run start contract failed: HTTP {status}")
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not run_id.startswith("run_"):
        raise AssertionError("run_id contract failed")
    return run_id


def collect_events(
    base_url: str,
    run_id: str,
    *,
    timeout: int = 30,
    first_delta: threading.Event | None = None,
) -> list[dict[str, Any]]:
    request = urllib.request.Request(
        f"{base_url}/v1/runs/{run_id}/events",
        method="GET",
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    events: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout
    with urllib.request.urlopen(request, timeout=min(timeout, 5)) as response:
        if response.status != 200 or response.headers.get_content_type() != "text/event-stream":
            raise AssertionError("SSE contract failed")
        for raw_line in response:
            if time.monotonic() >= deadline:
                raise AssertionError("SSE stream exceeded its wall-clock deadline")
            line = raw_line.decode("utf-8").strip()
            if not line.startswith("data: "):
                continue
            payload = json.loads(line[6:])
            if not isinstance(payload, dict) or payload.get("run_id") != run_id:
                raise AssertionError("invalid run event envelope")
            events.append(payload)
            if first_delta is not None and payload.get("event") == "message.delta":
                first_delta.set()
    return events


def wait_for_status(base_url: str, run_id: str, expected: set[str], *, timeout: float = 10) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status, last = request_json(base_url, "GET", f"/v1/runs/{run_id}")
        if status == 200 and last.get("status") in expected:
            return last
        time.sleep(0.05)
    raise AssertionError(f"run did not reach {sorted(expected)}; last={last.get('status')}")


def require_event(events: list[dict[str, Any]], name: str) -> None:
    if not any(event.get("event") == name for event in events):
        raise AssertionError(f"missing event: {name}")


def require_run_api_unauthorized(base_url: str, authorization: str | None) -> None:
    headers = {"Content-Type": "application/json"}
    if authorization is not None:
        headers["Authorization"] = authorization
    request = urllib.request.Request(
        f"{base_url}/v1/runs",
        data=json.dumps({"input": "SIQ_POC_AUTH_NEGATIVE"}).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    try:
        urllib.request.urlopen(request, timeout=10)
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise AssertionError(f"run API returned HTTP {exc.code}, expected 401") from exc
    else:
        raise AssertionError("run API accepted an unauthenticated request")


def main() -> None:
    global API_KEY
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:28642")
    parser.add_argument("--api-key-file", type=Path, required=True)
    args = parser.parse_args()
    if args.base_url != "http://127.0.0.1:28642":
        parser.error("the PoC contract test is pinned to the dedicated loopback endpoint")
    if not args.api_key_file.is_file() or args.api_key_file.is_symlink():
        parser.error("the PoC API key must be a regular, non-symlink file")
    API_KEY = args.api_key_file.read_text(encoding="utf-8").strip()
    if len(API_KEY) != 64 or any(character not in "0123456789abcdef" for character in API_KEY):
        parser.error("the PoC API key file is invalid")

    require_run_api_unauthorized(args.base_url, None)
    require_run_api_unauthorized(args.base_url, "Bearer definitely-not-the-poc-key")

    health_status, health = request_json(args.base_url, "GET", "/health")
    if health_status != 200 or health.get("status") != "ok":
        raise AssertionError("Hermes health contract failed")

    normal_id = start_run(args.base_url, "SIQ_POC_NORMAL")
    normal_events = collect_events(args.base_url, normal_id)
    require_event(normal_events, "message.delta")
    require_event(normal_events, "run.completed")
    normal_status = wait_for_status(args.base_url, normal_id, {"completed"})
    if "SIQ OpenShell Hermes PoC completed." not in str(normal_status.get("output", "")):
        raise AssertionError("normal run output mismatch")

    tool_id = start_run(args.base_url, "SIQ_POC_TOOL")
    tool_events = collect_events(args.base_url, tool_id)
    require_event(tool_events, "tool.started")
    require_event(tool_events, "tool.completed")
    require_event(tool_events, "run.completed")
    tool_status = wait_for_status(args.base_url, tool_id, {"completed"})
    if "SIQ_POC_TOOL_OK" not in str(tool_status.get("output", "")):
        raise AssertionError("Hermes tool-call output mismatch")

    slow_id = start_run(args.base_url, "SIQ_POC_SLOW")
    wait_for_status(args.base_url, slow_id, {"running"})
    first_delta = threading.Event()
    slow_events: list[dict[str, Any]] = []
    slow_error: list[BaseException] = []

    def consume_slow_events() -> None:
        try:
            slow_events.extend(collect_events(args.base_url, slow_id, first_delta=first_delta))
        except BaseException as exc:  # surfaced in the main test thread below
            slow_error.append(exc)

    event_thread = threading.Thread(target=consume_slow_events, name="siq-poc-sse", daemon=True)
    event_thread.start()
    if not first_delta.wait(timeout=5):
        raise AssertionError("slow run did not emit a message delta before cancellation")
    stop_status, stop_payload = request_json(args.base_url, "POST", f"/v1/runs/{slow_id}/stop", {})
    if stop_status != 200 or stop_payload != {"run_id": slow_id, "status": "stopping"}:
        raise AssertionError("run stop contract failed")
    event_thread.join(timeout=15)
    if event_thread.is_alive():
        raise AssertionError("cancelled SSE stream did not close")
    if slow_error:
        raise AssertionError(f"cancelled SSE consumer failed: {slow_error[0]}")
    require_event(slow_events, "run.cancelled")
    forbidden_terminal_events = {"run.completed", "run.failed"}
    if any(event.get("event") in forbidden_terminal_events for event in slow_events):
        raise AssertionError("cancelled run produced a success/failure terminal event")
    stopped_status = wait_for_status(args.base_url, slow_id, {"cancelled"})

    summary = {
        "schema": "siq.openshell.hermes_poc_contract.v1",
        "health": "ok",
        "normal": normal_status.get("status"),
        "normal_event_count": len(normal_events),
        "tool": tool_status.get("status"),
        "tool_event_count": len(tool_events),
        "stop": stopped_status.get("status"),
        "stop_event_count": len(slow_events),
    }
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as exc:
        raise SystemExit(f"Hermes PoC endpoint unavailable: {exc.reason}") from exc
