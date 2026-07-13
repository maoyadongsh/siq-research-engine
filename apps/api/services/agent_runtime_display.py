"""Display formatting helpers for the Hermes agent runtime."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

from models import ChatMessage

from services.agent_runtime_message_identity import decode_research_identity_snapshot


def _markdown_link_label(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").replace("[", "(").replace("]", ")").strip()


def _markdown_link_url(value: str) -> str:
    return quote(str(value or "").strip(), safe="/:#?&=%")


def _display_message_with_attachments(
    message: str,
    attachments: Sequence[Mapping[str, Any]] | None,
) -> str:
    text = (message or "").strip()
    if not attachments:
        return text or message
    labels: list[str] = []
    for item in attachments:
        filename = str(item.get("filename") or "").strip()
        if not filename:
            filename = Path(str(item.get("path") or "")).name.strip()
        filename = filename or "attachment"
        kind = str(item.get("kind") or "image").strip().lower()
        if kind == "audio":
            continue
        label = "图片" if kind == "image" else "文档"
        safe_label = _markdown_link_label(f"{label}: {filename}")
        url = _markdown_link_url(item.get("url") or "")
        if url and kind == "image":
            labels.append(f"![{safe_label}]({url})")
        elif url:
            labels.append(f"[{safe_label}]({url})")
        else:
            labels.append(f"[{safe_label}]")
    if not labels:
        return text or message
    prefix = text or ("请分析这些附件" if len(attachments) > 1 else "请分析这个附件")
    return f"{prefix}\n\n" + "\n".join(labels)


def chat_message_payload(
    message: ChatMessage,
    *,
    message_attachments: Callable[[ChatMessage], list[dict[str, Any]]],
    assistant_reply_for_display: Callable[[str], str],
    normalize_evidence_trace_for_display: Callable[[str], str],
) -> dict[str, Any]:
    content = message.content or ""
    if message.role == "assistant":
        content = normalize_evidence_trace_for_display(assistant_reply_for_display(content))
    return {
        "id": message.id,
        "session_id": message.session_id,
        "role": message.role,
        "content": content,
        "created_at": message.created_at,
        "attachments": message_attachments(message),
        "audit_trace_id": getattr(message, "audit_trace_id", None),
        "research_identity": decode_research_identity_snapshot(
            getattr(message, "research_identity_json", None)
        ),
    }


def chat_history_payload(
    messages: Sequence[ChatMessage],
    *,
    limit: int,
    has_visible_payload: Callable[[ChatMessage], bool],
    message_payload: Callable[[ChatMessage], dict[str, Any]],
) -> list[dict[str, Any]]:
    history_limit = max(int(limit or 1), 1)
    visible_messages = [
        message
        for message in reversed(messages)
        if has_visible_payload(message)
    ]
    return [message_payload(message) for message in visible_messages[-history_limit:]]


__all__ = [
    "_display_message_with_attachments",
    "_markdown_link_label",
    "_markdown_link_url",
    "chat_history_payload",
    "chat_message_payload",
]
