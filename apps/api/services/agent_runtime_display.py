"""Display formatting helpers for the Hermes agent runtime."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

from models import ChatMessage


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
        label = "图片" if kind == "image" else "文档"
        safe_label = _markdown_link_label(f"{label}: {filename}")
        url = _markdown_link_url(item.get("url") or "")
        if url and kind == "image":
            labels.append(f"![{safe_label}]({url})")
        elif url:
            labels.append(f"[{safe_label}]({url})")
        else:
            labels.append(f"[{safe_label}]")
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
    }


__all__ = [
    "_display_message_with_attachments",
    "_markdown_link_label",
    "_markdown_link_url",
    "chat_message_payload",
]
