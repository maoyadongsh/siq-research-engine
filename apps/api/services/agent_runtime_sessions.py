"""Session identity/default-context facade for the Hermes agent runtime."""

from __future__ import annotations

from .agent_chat_runtime_impl import (
    ACTIVE_RUNS,
    _active_key,
    _profile_wiki_context,
    _runtime_profile,
    get_session_default_context,
    hermes_runs_session_id,
)

__all__ = [
    "ACTIVE_RUNS",
    "_active_key",
    "_profile_wiki_context",
    "_runtime_profile",
    "get_session_default_context",
    "hermes_runs_session_id",
]
