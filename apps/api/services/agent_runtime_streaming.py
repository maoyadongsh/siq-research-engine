"""Streaming run-state facade for the Hermes agent runtime."""

from __future__ import annotations

from .agent_chat_runtime_impl import (
    ACTIVE_RUNS,
    ActiveRunState,
    _append_progress_event,
    _append_state_event,
    _extract_progress_from_text,
    _progress_payload,
    _progress_signature,
    get_active_run_snapshot,
    has_active_run,
    hermes_timeout,
    stop_active_run,
    stream_active_run_events,
    stream_chat_reply,
    stream_idle_timeout,
)

__all__ = [
    "ACTIVE_RUNS",
    "ActiveRunState",
    "_append_progress_event",
    "_append_state_event",
    "_extract_progress_from_text",
    "_progress_payload",
    "_progress_signature",
    "get_active_run_snapshot",
    "has_active_run",
    "hermes_timeout",
    "stop_active_run",
    "stream_active_run_events",
    "stream_chat_reply",
    "stream_idle_timeout",
]
