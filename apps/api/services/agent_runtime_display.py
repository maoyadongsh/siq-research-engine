"""Display formatting helpers for the Hermes agent runtime."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote


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


__all__ = ["_display_message_with_attachments", "_markdown_link_label", "_markdown_link_url"]
