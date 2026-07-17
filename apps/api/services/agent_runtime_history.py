"""History loading helpers for the Hermes agent runtime."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from models import ChatMessage
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from services.agent_runtime_message_identity import (
    decode_research_identity_snapshot,
    normalize_research_identity_snapshot,
)


class HistoryMessage(Protocol):
    role: str
    content: str


def normalize_history_scope(value: Any) -> dict[str, str]:
    """Return a fail-closed company or document scope for history matching."""

    normalized = normalize_research_identity_snapshot(value)
    if not normalized.get("market") or not normalized.get("company_id"):
        raise ValueError("history_scope_requires_market_and_company_id")
    return normalized


def history_message_matches_scope(
    message: HistoryMessage,
    research_identity_scope: Mapping[str, Any],
) -> bool:
    """Match every normalized scope field against one persisted message identity."""

    scope = normalize_history_scope(research_identity_scope)
    return _history_message_matches_normalized_scope(message, scope)


def _history_message_matches_normalized_scope(
    message: HistoryMessage,
    scope: Mapping[str, str],
) -> bool:
    identity = decode_research_identity_snapshot(getattr(message, "research_identity_json", None))
    if identity is None:
        return False
    return all(identity.get(field) == expected for field, expected in scope.items())


def filter_history_by_research_identity(
    messages: Sequence[HistoryMessage],
    *,
    research_identity_scope: Mapping[str, Any] | None,
) -> list[HistoryMessage]:
    """Filter history without leaking identity metadata into the Hermes payload."""

    if research_identity_scope is None:
        return list(messages)
    scope = normalize_history_scope(research_identity_scope)
    return [message for message in messages if _history_message_matches_normalized_scope(message, scope)]


def _filter_complete_role_pairs_by_scope(
    messages: Sequence[HistoryMessage],
    scope: Mapping[str, str],
) -> list[HistoryMessage]:
    """Keep only raw user/assistant pairs whose two sides match the scope."""

    alternating: list[HistoryMessage] = []
    for message in messages:
        if message.role not in {"user", "assistant"}:
            continue
        if alternating and alternating[-1].role == message.role:
            alternating[-1] = message
        else:
            alternating.append(message)

    while alternating and alternating[0].role != "user":
        alternating.pop(0)

    paired: list[HistoryMessage] = []
    for index in range(0, len(alternating) - 1, 2):
        user_message = alternating[index]
        assistant_message = alternating[index + 1]
        if user_message.role != "user" or assistant_message.role != "assistant":
            continue
        if not _history_message_matches_normalized_scope(user_message, scope):
            continue
        if not _history_message_matches_normalized_scope(assistant_message, scope):
            continue
        paired.extend((user_message, assistant_message))
    return paired


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
    research_identity_scope: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    scope = normalize_history_scope(research_identity_scope) if research_identity_scope is not None else None
    normalized: list[tuple[dict[str, Any], bool]] = []
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
        matches_scope = scope is None or _history_message_matches_normalized_scope(message, scope)
        if normalized and normalized[-1][0]["role"] == message.role:
            normalized[-1] = (item, matches_scope)
        else:
            normalized.append((item, matches_scope))

    while normalized and normalized[0][0]["role"] != "user":
        normalized.pop(0)

    if scope is None:
        limited = [item for item, _matches_scope in normalized[-max(0, limit):]]
        while limited and limited[0]["role"] != "user":
            limited.pop(0)
        return limited

    complete_turns: list[dict[str, Any]] = []
    for index in range(0, len(normalized) - 1, 2):
        user_item, user_matches = normalized[index]
        assistant_item, assistant_matches = normalized[index + 1]
        if user_item["role"] != "user" or assistant_item["role"] != "assistant":
            continue
        if not user_matches or not assistant_matches:
            continue
        complete_turns.extend((user_item, assistant_item))

    complete_message_limit = max(0, limit // 2) * 2
    if complete_message_limit == 0:
        return []
    return complete_turns[-complete_message_limit:]


async def load_history(
    async_session: AsyncSession,
    session_id: str,
    *,
    limit: int,
    normalize_messages: Callable[[list[ChatMessage]], list[dict[str, Any]]],
    research_identity_scope: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    statement = select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.id.desc())
    if research_identity_scope is None:
        statement = statement.limit(limit * 3)
    else:
        # Scoped history must not lose an older matching pair merely because
        # newer turns in the same UI session belong to another company.
        normalize_history_scope(research_identity_scope)
        statement = statement.where(ChatMessage.research_identity_json.is_not(None))
    result = await async_session.exec(statement)
    messages = list(reversed(result.all()))
    if research_identity_scope is not None:
        scope = normalize_history_scope(research_identity_scope)
        messages = _filter_complete_role_pairs_by_scope(messages, scope)
    return normalize_messages(messages)


__all__ = [
    "HistoryMessage",
    "filter_history_by_research_identity",
    "history_message_matches_scope",
    "load_history",
    "normalize_history",
    "normalize_history_scope",
]
