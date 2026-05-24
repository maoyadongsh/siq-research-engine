import json
from dataclasses import dataclass
from typing import AsyncGenerator, Literal

import httpx

HERMES_KEY = "Bearer change-me-local-dev"

HERMES_PROFILES = {
    "finsight_assistant": {"base": "http://localhost:8642/v1/runs", "model": "finsight_assistant"},
    "analysis": {"base": "http://localhost:8651/v1/runs", "model": "finsight_analysis"},
    "factchecker": {"base": "http://localhost:8649/v1/runs", "model": "finsight_factchecker"},
    "tracking": {"base": "http://localhost:8650/v1/runs", "model": "finsight_tracking"},
    "legal": {"base": "http://localhost:8652/v1/runs", "model": "finsight_legal"},
}

HermesProfile = Literal["finsight_assistant", "analysis", "factchecker", "tracking", "legal"]


@dataclass
class StreamEvent:
    """Unified event yielded by stream_run."""
    type: str  # "delta" | "tool.started" | "tool.completed" | "reasoning" | "done"
    text: str = ""
    tool: str = ""
    preview: str | None = None
    duration: float | None = None
    error: bool = False


def _get_profile(profile: HermesProfile) -> dict:
    return HERMES_PROFILES[profile]


async def create_run(
    input: str,
    conversation_history: list[dict],
    *,
    profile: HermesProfile = "finsight_assistant",
) -> str:
    """POST /v1/runs, return run_id."""
    cfg = _get_profile(profile)
    headers = {
        "Authorization": HERMES_KEY,
        "Content-Type": "application/json",
    }
    payload: dict = {
        "model": cfg["model"],
        "input": input,
    }
    if conversation_history:
        payload["conversation_history"] = conversation_history

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(cfg["base"], headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["run_id"]


async def stream_run(
    run_id: str,
    *,
    profile: HermesProfile = "finsight_assistant",
    timeout: float | httpx.Timeout | None = None,
) -> AsyncGenerator[StreamEvent, None]:
    """Subscribe to run SSE events, yield structured StreamEvent objects."""
    cfg = _get_profile(profile)
    headers = {"Authorization": HERMES_KEY}
    url = f"{cfg['base']}/{run_id}/events"

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("GET", url, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:]
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("event", "")

                if event_type == "message.delta":
                    delta = event.get("delta", "")
                    if delta:
                        yield StreamEvent(type="delta", text=delta)

                elif event_type == "tool.started":
                    yield StreamEvent(
                        type="tool.started",
                        tool=event.get("tool", ""),
                        preview=event.get("preview"),
                    )

                elif event_type == "tool.completed":
                    yield StreamEvent(
                        type="tool.completed",
                        tool=event.get("tool", ""),
                        duration=event.get("duration"),
                        error=event.get("error", False),
                    )

                elif event_type == "reasoning.available":
                    text = event.get("text", "")
                    if text:
                        yield StreamEvent(type="reasoning", text=text)

                elif event_type in ("run.completed", "run.failed", "run.cancelled"):
                    output = event.get("output", "")
                    yield StreamEvent(
                        type="done",
                        text=output,
                    )
                    break


async def stop_run(
    run_id: str,
    *,
    profile: HermesProfile = "finsight_assistant",
) -> dict:
    """POST /v1/runs/{run_id}/stop and return the Hermes response."""
    cfg = _get_profile(profile)
    headers = {"Authorization": HERMES_KEY}
    url = f"{cfg['base']}/{run_id}/stop"

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, headers=headers)
        resp.raise_for_status()
        return resp.json()


async def collect_run_result(
    run_id: str,
    *,
    profile: HermesProfile = "finsight_assistant",
    timeout: float | httpx.Timeout | None = None,
) -> str:
    """Stream a run to completion and return the full text output."""
    full_text = ""
    async for ev in stream_run(run_id, profile=profile, timeout=timeout):
        if ev.type == "delta":
            full_text += ev.text
        elif ev.type == "done":
            if ev.text:
                return ev.text
    return full_text
