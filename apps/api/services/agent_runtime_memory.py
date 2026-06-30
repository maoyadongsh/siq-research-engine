"""Session history and local-memory facade for the Hermes agent runtime."""

from __future__ import annotations

from .agent_chat_runtime_impl import (
    RECENT_COMPLETED_RUNS,
    RecentRunRecord,
    _chat_message_payload,
    _dedupe_hash,
    _dedupe_hash_with_attachments,
    _forget_recent_completed_run,
    _recent_duplicate_reply,
    _remember_completed_run,
    build_local_memory_context,
    build_local_memory_summary,
    chat_history_response,
    ensure_local_memory_context,
    hermes_runs_session_id,
    load_history,
    load_local_memory_context,
    normalize_history,
    refresh_session_memory,
    save_message,
    save_message_in_background,
)

__all__ = [
    "RECENT_COMPLETED_RUNS",
    "RecentRunRecord",
    "_chat_message_payload",
    "_dedupe_hash",
    "_dedupe_hash_with_attachments",
    "_forget_recent_completed_run",
    "_recent_duplicate_reply",
    "_remember_completed_run",
    "build_local_memory_context",
    "build_local_memory_summary",
    "chat_history_response",
    "ensure_local_memory_context",
    "hermes_runs_session_id",
    "load_history",
    "load_local_memory_context",
    "normalize_history",
    "refresh_session_memory",
    "save_message",
    "save_message_in_background",
]
