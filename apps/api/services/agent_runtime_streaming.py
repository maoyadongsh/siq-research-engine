"""Streaming run-state owner for the Hermes agent runtime."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from fastapi import Request

from services import agent_runtime_citations, agent_runtime_dedupe, agent_runtime_progress
from services.hermes_client import HermesProfile, HermesRunRoute, RunTerminalResult, normalize_profile


def _env_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = os.getenv(name)
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


STREAM_EVENT_HEARTBEAT_SECONDS = _env_int("SIQ_STREAM_EVENT_HEARTBEAT_SECONDS", 8, minimum=5, maximum=120)


@dataclass
class ActiveRunState:
    profile: HermesProfile
    session_id: str
    run_id: str
    status: str = "running"
    content: str = ""
    events: list[dict[str, str]] = field(default_factory=list)
    progress: dict[str, Any] | None = None
    progress_signature: str | None = None
    started_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    error: str | None = None
    done_payload: dict[str, Any] | None = None
    terminal_result: RunTerminalResult | None = None
    stop_requested: bool = False
    user_stop_requested: bool = False
    message_hash: str | None = None
    original_message: str | None = None
    context: Any | None = None
    consecutive_tool_errors: int = 0
    total_tool_errors: int = 0
    last_tool_error_tool: str | None = None
    last_tool_started_signature: str | None = None
    last_tool_error_signature: str | None = None
    last_tool_signature: str | None = None
    last_tool_label: str | None = None
    last_tool_preview: str | None = None
    consecutive_same_tool_calls: int = 0
    tool_events_since_delta: int = 0
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task | None = None
    owner_id: str | None = None
    lease_heartbeat_task: asyncio.Task | None = None
    run_route: HermesRunRoute | None = None
    runtime_terminal_confirmed: bool = False
    runtime_children_terminal_confirmed: bool = True
    memory_research_identity: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        self.profile = _runtime_profile(self.profile)


@dataclass(frozen=True)
class ToolStartedProjection:
    tool_label: str
    preview: str
    display_tool_label: str
    progress_payload: dict[str, Any]
    state_event_payload: dict[str, Any]
    repeated_call_limit_reached: bool


@dataclass(frozen=True)
class ToolCompletedProjection:
    tool_label: str
    display_tool_label: str
    progress_payload: dict[str, Any]
    state_event_payload: dict[str, Any]
    consecutive_error_limit_reached: bool


ACTIVE_RUNS: dict[tuple[HermesProfile, str], ActiveRunState] = {}
PROGRESS_LINE_RE = agent_runtime_progress.PROGRESS_LINE_RE
PROGRESS_BAR_RE = agent_runtime_progress.PROGRESS_BAR_RE


def _runtime_profile(profile: HermesProfile | str) -> HermesProfile:
    return normalize_profile(profile)


def _active_key(profile: HermesProfile | str, session_id: str) -> tuple[HermesProfile, str]:
    profile = _runtime_profile(profile)
    return (profile, session_id)


def stream_idle_timeout(
    profile: HermesProfile | str,
    *,
    assistant_timeout_seconds: int,
    specialist_timeout_seconds: int,
) -> int:
    if _runtime_profile(profile) == "siq_assistant":
        return assistant_timeout_seconds
    return specialist_timeout_seconds


def _progress_signature(payload: dict[str, Any]) -> str:
    return agent_runtime_progress.progress_signature(payload, hash_text=agent_runtime_dedupe._hash_text)


def _progress_payload(
    *,
    status: str = "running",
    title: str,
    detail: str | None = None,
    current: int | None = None,
    total: int | None = None,
    source: str = "runtime",
    tool: str | None = None,
) -> dict[str, Any]:
    return agent_runtime_progress.progress_payload(
        status=status,
        title=title,
        detail=detail,
        current=current,
        total=total,
        source=source,
        tool=tool,
        clock=datetime.utcnow,
    )


def _extract_progress_from_text(text: str) -> dict[str, Any] | None:
    return agent_runtime_progress.extract_progress_from_text(text, clock=datetime.utcnow)


def project_tool_started(
    state: ActiveRunState,
    *,
    tool: str | None,
    preview: str | None,
    display_tool_label: Callable[[str | None, str | None], str],
    hash_text: Callable[[str], str],
    repeated_tool_call_limit: int,
) -> ToolStartedProjection:
    tool_label = tool or "工具"
    preview_text = preview or ""
    visible_tool_label = display_tool_label(tool_label, preview_text)
    tool_signature = hash_text(f"{tool_label}\n{preview_text}")
    state.last_tool_started_signature = tool_signature
    state.tool_events_since_delta += 1
    if tool_signature == state.last_tool_signature:
        state.consecutive_same_tool_calls += 1
    else:
        state.consecutive_same_tool_calls = 1
        state.last_tool_signature = tool_signature
        state.last_tool_label = tool_label
        state.last_tool_preview = preview_text[:220] if preview_text else ""
    return ToolStartedProjection(
        tool_label=tool_label,
        preview=preview_text,
        display_tool_label=visible_tool_label,
        progress_payload=_progress_payload(
            status="running",
            title=f"正在执行 {visible_tool_label}",
            detail=preview_text[:180] if preview_text else "智能体正在调用工具",
            source="tool",
            tool=visible_tool_label,
        ),
        state_event_payload={"status": "started", "tool": tool, "preview": preview},
        repeated_call_limit_reached=(
            state.consecutive_same_tool_calls >= repeated_tool_call_limit
            and state.tool_events_since_delta >= repeated_tool_call_limit
        ),
    )


def project_tool_completed(
    state: ActiveRunState,
    *,
    tool: str | None,
    duration: Any,
    error: Any,
    display_tool_label: Callable[[str | None, str | None], str],
    hash_text: Callable[[str], str],
    consecutive_tool_error_limit: int,
) -> ToolCompletedProjection:
    state.tool_events_since_delta += 1
    tool_label = tool or "工具"
    visible_tool_label = display_tool_label(tool_label, state.last_tool_preview)
    tool_signature = state.last_tool_started_signature or hash_text(tool_label)
    if error:
        if tool_signature == state.last_tool_error_signature:
            state.consecutive_tool_errors += 1
        else:
            state.consecutive_tool_errors = 1
            state.last_tool_error_signature = tool_signature
        state.total_tool_errors += 1
        state.last_tool_error_tool = tool_label
    else:
        state.consecutive_tool_errors = 0
        state.last_tool_error_signature = None
    return ToolCompletedProjection(
        tool_label=tool_label,
        display_tool_label=visible_tool_label,
        progress_payload=_progress_payload(
            status="error" if error else "running",
            title=f"{visible_tool_label} 执行{'异常' if error else '完成'}",
            detail=f"耗时 {duration:.1f}s" if isinstance(duration, (int, float)) else None,
            source="tool",
            tool=visible_tool_label,
        ),
        state_event_payload={
            "status": "completed",
            "tool": tool,
            "duration": duration,
            "error": error,
        },
        consecutive_error_limit_reached=bool(error)
        and state.consecutive_tool_errors >= consecutive_tool_error_limit,
    )


async def _append_state_event(
    state: ActiveRunState,
    event_name: str,
    payload: dict[str, Any],
) -> None:
    if event_name == "delta":
        state.content += str(payload.get("content", ""))
    elif event_name == "replace":
        state.content = str(payload.get("content", ""))
    elif event_name == "done":
        state.status = "completed"
        state.done_payload = payload
    elif event_name == "error":
        state.status = str(payload.get("status") or "failed")
        state.error = str(payload.get("message") or payload.get("content") or "Unknown error")

    state.updated_at = datetime.utcnow()
    event = {
        "event": event_name,
        "data": json.dumps(payload, ensure_ascii=False),
    }
    async with state.condition:
        state.events.append(event)
        state.condition.notify_all()


async def _append_progress_event(state: ActiveRunState, payload: dict[str, Any]) -> None:
    signature = _progress_signature(payload)
    if signature == state.progress_signature:
        return
    state.progress = payload
    state.progress_signature = signature
    await _append_state_event(state, "progress", payload)


def get_active_run_snapshot(
    profile: HermesProfile,
    session_id: str,
    *,
    diagnose_latest_hermes_session: Callable[[HermesProfile], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    state = ACTIVE_RUNS.get(_active_key(profile, session_id))
    if not state:
        snapshot: dict[str, Any] = {"running": False}
        diagnostic = diagnose_latest_hermes_session(profile) if diagnose_latest_hermes_session else None
        if diagnostic:
            snapshot["diagnostic"] = diagnostic
        return snapshot

    return {
        "running": state.status == "running",
        "status": state.status,
        "run_id": state.run_id,
        "session_id": state.session_id,
        "content": agent_runtime_citations.normalize_evidence_trace_for_display(state.content),
        "progress": state.progress,
        "event_count": len(state.events),
        "started_at": state.started_at.isoformat(),
        "updated_at": state.updated_at.isoformat(),
        "error": state.error,
        "terminal": state.terminal_result.to_payload() if state.terminal_result else None,
        "runtime_target": state.run_route.target if state.run_route is not None else "host",
        "canary_run_id": state.run_route.canary_run_id if state.run_route is not None else None,
    }


def has_active_run(profile: HermesProfile, session_id: str) -> bool:
    state = ACTIVE_RUNS.get(_active_key(profile, session_id))
    return bool(state and state.status == "running")


def _clear_active_run(state: ActiveRunState, *, session_id: str | None = None) -> None:
    ACTIVE_RUNS.pop(_active_key(state.profile, session_id or state.session_id), None)


async def _append_completed_active_run(
    state: ActiveRunState,
    done_payload: dict[str, Any],
    *,
    detail: str = "结果已写入对话并同步历史记录",
) -> None:
    await _append_progress_event(
        state,
        agent_runtime_progress.completed_run_progress_payload(detail),
    )
    await _append_state_event(state, "done", done_payload)


async def _append_user_stopped_active_run(state: ActiveRunState, stopped_message: str) -> None:
    await _append_progress_event(
        state,
        agent_runtime_progress.user_stopped_progress_payload(stopped_message),
    )
    await _append_state_event(
        state,
        "error",
        {
            "message": stopped_message,
            "reason": "user_stop_requested",
            "status": "cancelled",
            "error_code": "hermes_run_cancelled",
            "retryable": False,
            "trace_id": state.run_id,
        },
    )


async def _append_reasoning_active_run(state: ActiveRunState, text: str | None) -> None:
    await _append_state_event(state, "reasoning", {"text": text})
    await _append_progress_event(
        state,
        agent_runtime_progress.reasoning_progress_payload(text),
    )


async def stop_active_run(
    profile: HermesProfile,
    session_id: str,
    *,
    stop_run_call: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    stopped_message: str | None = None,
    orphaned_run_message: str | None = None,
) -> dict:
    if stop_run_call is None or stopped_message is None or orphaned_run_message is None:
        from . import agent_chat_runtime_impl

        stop_run_call = stop_run_call or agent_chat_runtime_impl.stop_run
        stopped_message = stopped_message or agent_chat_runtime_impl.STOPPED_MESSAGE
        orphaned_run_message = orphaned_run_message or agent_chat_runtime_impl.ORPHANED_RUN_MESSAGE

    state = ACTIVE_RUNS.get(_active_key(profile, session_id))
    if not state:
        return {"stopped": False, "detail": "No active Hermes run"}

    if state.user_stop_requested:
        return {"stopped": True, "run_id": state.run_id, "detail": "Stop already requested"}

    state.stop_requested = True
    state.user_stop_requested = True
    await _append_progress_event(
        state,
        agent_runtime_progress.user_stopped_progress_payload(stopped_message),
    )
    await _append_state_event(state, "replace", {"content": stopped_message})
    try:
        if state.run_route is None:
            result = await stop_run_call(state.run_id, profile=state.profile)
        else:
            result = await stop_run_call(
                state.run_id,
                profile=state.profile,
                route=state.run_route,
            )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            await _append_progress_event(
                state,
                agent_runtime_progress.orphaned_run_progress_payload(orphaned_run_message),
            )
            await _append_state_event(state, "replace", {"content": orphaned_run_message})
            await _append_state_event(
                state,
                "error",
                {"message": orphaned_run_message, "reason": "hermes_run_not_found"},
            )
            ACTIVE_RUNS.pop(_active_key(state.profile, session_id), None)
            return {
                "stopped": True,
                "run_id": state.run_id,
                "detail": orphaned_run_message,
                "hermes": {"error": "run_not_found"},
            }
        raise
    return {"stopped": True, "run_id": state.run_id, "hermes": result}


async def stream_active_run_events(
    request: Request,
    *,
    profile: HermesProfile,
    session_id: str,
    offset: int = 0,
    heartbeat_seconds: int | float | None = None,
) -> AsyncGenerator[dict[str, str], None]:
    state = ACTIVE_RUNS.get(_active_key(profile, session_id))
    if not state:
        return

    next_index = max(0, offset)
    while True:
        if await request.is_disconnected():
            return

        heartbeat: dict[str, str] | None = None
        async with state.condition:
            if next_index >= len(state.events) and state.status == "running":
                try:
                    await asyncio.wait_for(
                        state.condition.wait(),
                        timeout=heartbeat_seconds if heartbeat_seconds is not None else STREAM_EVENT_HEARTBEAT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    heartbeat = agent_runtime_progress.heartbeat_progress_payload()

            pending = state.events[next_index:]
            is_terminal = state.status != "running"

        if heartbeat and not pending:
            yield {
                "event": "progress",
                "data": json.dumps(heartbeat, ensure_ascii=False),
            }
            continue

        for event in pending:
            yield event
            next_index += 1

        if is_terminal and next_index >= len(state.events):
            return


def __getattr__(name: str) -> Any:
    if name in {"hermes_timeout", "stream_chat_reply", "stream_idle_timeout"}:
        from . import agent_chat_runtime_impl

        return getattr(agent_chat_runtime_impl, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# These compatibility exports are resolved lazily above to avoid an import cycle.
hermes_timeout: Any
stream_chat_reply: Any


__all__ = [
    "ACTIVE_RUNS",
    "ActiveRunState",
    "PROGRESS_BAR_RE",
    "PROGRESS_LINE_RE",
    "STREAM_EVENT_HEARTBEAT_SECONDS",
    "_active_key",
    "_append_completed_active_run",
    "_append_progress_event",
    "_append_reasoning_active_run",
    "_append_state_event",
    "_append_user_stopped_active_run",
    "_clear_active_run",
    "_extract_progress_from_text",
    "_progress_payload",
    "_progress_signature",
    "_runtime_profile",
    "get_active_run_snapshot",
    "has_active_run",
    "hermes_timeout",
    "project_tool_completed",
    "project_tool_started",
    "stop_active_run",
    "stream_active_run_events",
    "stream_chat_reply",
    "stream_idle_timeout",
]
