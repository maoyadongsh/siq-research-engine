from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class ChatRequestEnvelope:
    all_attachments: list[dict[str, Any]]
    message_hash: str
    user_display_message: str


@dataclass(frozen=True)
class ChatRunPreflightContext:
    history: list[dict[str, Any]]
    local_memory_context: str | None
    attachments: list[dict[str, Any]]

    @property
    def allow_initialize(self) -> bool:
        return not self.history


@dataclass(frozen=True)
class ChatPreflightShortCircuitPlan:
    forget_recent_completed_run: bool
    should_check_duplicate: bool
    catalog_reply: str | None


def merge_preflight_memory_context(
    local_memory_context: str | None,
    agent_memory_context: str | None,
) -> str | None:
    memory_blocks = [
        block
        for block in [local_memory_context, agent_memory_context]
        if block
    ]
    return "\n\n".join(memory_blocks) if memory_blocks else None


def plan_chat_preflight_short_circuit(
    *,
    catalog_reply: str | None,
    is_general_assistant_request: bool,
) -> ChatPreflightShortCircuitPlan:
    skip_duplicate = bool(catalog_reply or is_general_assistant_request)
    return ChatPreflightShortCircuitPlan(
        forget_recent_completed_run=skip_duplicate,
        should_check_duplicate=not skip_duplicate,
        catalog_reply=catalog_reply,
    )


async def prepare_chat_request_envelope(
    message: str,
    async_session: Any,
    *,
    session_id: str,
    context: Any | None = None,
    display_message: str | None = None,
    attachments: Any | None = None,
    attachment_dicts: Callable[[Any | None], list[dict[str, Any]]],
    should_reuse_recent_attachments: Callable[[str], bool],
    load_recent_session_attachments: Callable[[Any, str], Awaitable[list[dict[str, Any]]]],
    dedupe_hash_with_attachments: Callable[[str, Any | None, list[dict[str, Any]]], str],
    display_message_with_attachments: Callable[[str, list[dict[str, Any]]], str],
) -> ChatRequestEnvelope:
    all_attachments = attachment_dicts(attachments)
    if not all_attachments and should_reuse_recent_attachments(message):
        all_attachments = await load_recent_session_attachments(async_session, session_id)
    message_hash = dedupe_hash_with_attachments(message, context, all_attachments)
    user_display_message = display_message_with_attachments(
        (display_message or message).strip() or message,
        all_attachments,
    )
    return ChatRequestEnvelope(
        all_attachments=all_attachments,
        message_hash=message_hash,
        user_display_message=user_display_message,
    )


async def load_chat_run_preflight_context(
    async_session: Any,
    *,
    session_id: str,
    profile: str,
    attachments: list[dict[str, Any]],
    history_limit: int,
    load_history: Callable[..., Awaitable[list[dict[str, Any]]]],
    ensure_local_memory_context: Callable[..., Awaitable[str | None]],
) -> ChatRunPreflightContext:
    history = await load_history(async_session, session_id, limit=history_limit)
    local_memory_context = await ensure_local_memory_context(async_session, profile, session_id)
    return ChatRunPreflightContext(
        history=history,
        local_memory_context=local_memory_context,
        attachments=attachments,
    )
