"""History loading helpers for the Hermes agent runtime."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol

from models import ChatMessage
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession


class HistoryMessage(Protocol):
    role: str
    content: str


def normalize_history(
    messages: Sequence[HistoryMessage],
    *,
    limit: int,
    chat_message_has_visible_payload: Callable[[Any], bool],
    message_attachments: Callable[[Any], list[dict[str, Any]]],
    attachment_reference_context: Callable[[Any | None], str],
    is_loop_polluted_assistant_message: Callable[[str], bool],
    normalize_evidence_trace_for_display: Callable[[str | None], str],
    sanitize_assistant_history_reply: Callable[[str], str],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for message in messages:
        if message.role not in {"user", "assistant"}:
            continue
        if not chat_message_has_visible_payload(message):
            continue
        content = message.content
        if message.role == "assistant":
            if is_loop_polluted_assistant_message(content):
                continue
            content = normalize_evidence_trace_for_display(sanitize_assistant_history_reply(content))
            if not str(content or "").strip():
                continue
        elif message.role == "user":
            attachment_context = attachment_reference_context(message_attachments(message))
            if attachment_context:
                content = f"{content}\n\n{attachment_context}" if content else attachment_context
        item = {"role": message.role, "content": content}
        if normalized and normalized[-1]["role"] == message.role:
            normalized[-1] = item
        else:
            normalized.append(item)

    while normalized and normalized[0]["role"] != "user":
        normalized.pop(0)
    return normalized[-limit:]


async def load_history(
    async_session: AsyncSession,
    session_id: str,
    *,
    limit: int,
    normalize_messages: Callable[[list[ChatMessage]], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    result = await async_session.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id.desc())
        .limit(limit * 3)
    )
    return normalize_messages(list(reversed(result.all())))


__all__ = [
    "HistoryMessage",
    "load_history",
    "normalize_history",
]
