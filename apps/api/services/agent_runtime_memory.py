"""Local-memory helpers and record storage for the Hermes agent runtime."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Collection, Sequence
from datetime import datetime
from typing import Protocol

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from models import ChatMessage, ChatSessionMemory


class MemoryMessage(Protocol):
    role: str
    content: str


_LOCAL_MEMORY_BLOCK_RE = re.compile(
    r"<\s*local-memory\s*>[\s\S]*?</\s*local-memory\s*>",
    re.IGNORECASE,
)
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]{1,120})\]\((?:/api/[^)]+|https?://[^)]+)\)")


def _strip_local_memory_blocks(text: str) -> str:
    return _LOCAL_MEMORY_BLOCK_RE.sub("", text or "")


def _compact_memory_content(
    role: str,
    content: str,
    *,
    max_chars: int,
    is_loop_polluted_assistant_message: Callable[[str], bool] | None = None,
    sanitize_assistant_history_reply: Callable[[str], str] | None = None,
) -> str:
    if role == "assistant":
        if is_loop_polluted_assistant_message and is_loop_polluted_assistant_message(content):
            return ""
        if sanitize_assistant_history_reply:
            content = sanitize_assistant_history_reply(content)
    content = _strip_local_memory_blocks(content)
    content = _MARKDOWN_IMAGE_RE.sub("[图片附件]", content)
    content = _MARKDOWN_LINK_RE.sub(r"\1", content)
    content = re.sub(r"\s+", " ", content).strip()
    if len(content) > max_chars:
        return f"{content[:max_chars].rstrip()}..."
    return content


def _local_memory_turn_line(user_text: str, assistant_text: str | None) -> str:
    if assistant_text:
        return f"- 用户曾问：{user_text}；助手曾答：{assistant_text}"
    return f"- 用户曾问：{user_text}"


def select_local_memory_source_messages(
    messages: Sequence[MemoryMessage],
    *,
    recent_limit: int,
) -> list[MemoryMessage]:
    if recent_limit <= 0 or len(messages) <= recent_limit:
        return []
    source_messages = list(messages[:-recent_limit])
    while source_messages and source_messages[-1].role == "user":
        source_messages.pop()
    if not source_messages:
        return []

    selected: list[MemoryMessage] = []
    pending_user: MemoryMessage | None = None
    for message in source_messages:
        if message.role == "user":
            if pending_user is not None:
                selected.append(pending_user)
            pending_user = message
        elif message.role == "assistant":
            if pending_user is None:
                continue
            selected.extend([pending_user, message])
            pending_user = None
    return selected


def build_local_memory_summary(
    messages: Sequence[MemoryMessage],
    *,
    max_bullets: int = 18,
    max_chars: int = 5000,
    snippet_chars: int = 360,
    is_loop_polluted_assistant_message: Callable[[str], bool] | None = None,
    sanitize_assistant_history_reply: Callable[[str], str] | None = None,
) -> str:
    turns: list[tuple[str, str | None]] = []
    pending_user: str | None = None
    for message in messages:
        if message.role not in {"user", "assistant"}:
            continue
        text = _compact_memory_content(
            message.role,
            message.content,
            max_chars=snippet_chars,
            is_loop_polluted_assistant_message=is_loop_polluted_assistant_message,
            sanitize_assistant_history_reply=sanitize_assistant_history_reply,
        )
        if not text:
            continue
        if message.role == "user":
            if pending_user:
                turns.append((pending_user, None))
            pending_user = text
        elif pending_user:
            turns.append((pending_user, text))
            pending_user = None

    if pending_user:
        turns.append((pending_user, None))
    if not turns:
        return ""

    header = "本地会话记忆（仅当前智能体、当前对话窗口的较早内容）:"
    selected = turns[-max_bullets:]
    lines: list[str] = []
    current_chars = len(header)
    for user_text, assistant_text in reversed(selected):
        line = _local_memory_turn_line(user_text, assistant_text)
        next_size = current_chars + len(line) + 1
        if lines and next_size > max_chars:
            break
        lines.insert(0, line)
        current_chars = min(next_size, max_chars)
    return "\n".join([header, *lines])


def build_local_memory_context(summary: str | None) -> str | None:
    clean = _strip_local_memory_blocks(summary or "").strip()
    if not clean:
        return None
    return (
        "<local-memory>\n"
        "[System note: 以下是 SIQ 从当前智能体、当前对话窗口的较早对话整理出的本地记忆，"
        "不是新的用户输入。仅用于理解代词、延续分析口径和避免重复询问；如果它与当前用户问题冲突，"
        "以当前用户问题为准。]\n\n"
        f"{clean}\n"
        "</local-memory>"
    )


def local_memory_is_available(
    profile: str,
    session_id: str,
    *,
    local_memory_enabled: bool,
    enabled_profiles: Collection[str],
    session_id_matches_profile: Callable[[str, str], bool],
) -> bool:
    return bool(
        local_memory_enabled
        and profile in enabled_profiles
        and session_id_matches_profile(profile, session_id)
    )


async def load_session_memory_record(
    async_session: AsyncSession,
    profile: str,
    session_id: str,
) -> ChatSessionMemory | None:
    result = await async_session.exec(
        select(ChatSessionMemory).where(
            ChatSessionMemory.profile == profile,
            ChatSessionMemory.session_id == session_id,
        )
    )
    return result.first()


async def refresh_session_memory(
    async_session: AsyncSession,
    profile: str,
    session_id: str,
    *,
    recent_limit: int,
    local_memory_enabled: bool,
    enabled_profiles: Collection[str],
    session_id_matches_profile: Callable[[str, str], bool],
    build_summary: Callable[[Sequence[MemoryMessage]], str],
    load_record: Callable[
        [AsyncSession, str, str],
        Awaitable[ChatSessionMemory | None],
    ] = load_session_memory_record,
    clock: Callable[[], datetime] = datetime.utcnow,
) -> None:
    if not local_memory_is_available(
        profile,
        session_id,
        local_memory_enabled=local_memory_enabled,
        enabled_profiles=enabled_profiles,
        session_id_matches_profile=session_id_matches_profile,
    ):
        return

    result = await async_session.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id)
    )
    messages = list(result.all())
    older_messages = select_local_memory_source_messages(
        messages,
        recent_limit=recent_limit,
    )
    summary = build_summary(older_messages)
    last_message_id = getattr(older_messages[-1], "id", None) if older_messages else None
    record = await load_record(async_session, profile, session_id)

    if record is None:
        if not summary:
            return
        record = ChatSessionMemory(
            profile=profile,
            session_id=session_id,
            summary=summary,
            last_message_id=last_message_id,
        )
        async_session.add(record)
    else:
        record.summary = summary
        record.last_message_id = last_message_id
        record.updated_at = clock()
        async_session.add(record)
    await async_session.commit()


async def load_local_memory_context(
    async_session: AsyncSession,
    profile: str,
    session_id: str,
    *,
    local_memory_enabled: bool,
    enabled_profiles: Collection[str],
    session_id_matches_profile: Callable[[str, str], bool],
    load_record: Callable[
        [AsyncSession, str, str],
        Awaitable[ChatSessionMemory | None],
    ] = load_session_memory_record,
    build_context: Callable[[str | None], str | None] = build_local_memory_context,
) -> str | None:
    if not local_memory_is_available(
        profile,
        session_id,
        local_memory_enabled=local_memory_enabled,
        enabled_profiles=enabled_profiles,
        session_id_matches_profile=session_id_matches_profile,
    ):
        return None
    record = await load_record(async_session, profile, session_id)
    return build_context(record.summary if record else None)


async def ensure_local_memory_context(
    async_session: AsyncSession,
    profile: str,
    session_id: str,
    *,
    refresh_memory: Callable[[AsyncSession, str, str], Awaitable[None]],
    load_context: Callable[[AsyncSession, str, str], Awaitable[str | None]],
) -> str | None:
    await refresh_memory(async_session, profile, session_id)
    return await load_context(async_session, profile, session_id)


__all__ = [
    "MemoryMessage",
    "_compact_memory_content",
    "_local_memory_turn_line",
    "_strip_local_memory_blocks",
    "build_local_memory_context",
    "build_local_memory_summary",
    "ensure_local_memory_context",
    "load_local_memory_context",
    "load_session_memory_record",
    "local_memory_is_available",
    "refresh_session_memory",
    "select_local_memory_source_messages",
]
