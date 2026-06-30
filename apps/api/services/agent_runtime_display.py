"""Display formatting helpers for the Hermes agent runtime."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Mapping


def _markdown_link_label(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").replace("[", "(").replace("]", ")").strip()


def _display_message_with_attachments(
    message: str,
    attachments: Sequence[Mapping[str, Any]] | None,
) -> str:
    text = (message or "").strip()
    if not attachments:
        return text or message
    labels: list[str] = []
    for item in attachments:
        filename = str(item.get("filename") or Path(str(item.get("path") or "")).name or "attachment").strip()
        kind = str(item.get("kind") or "image")
        label = "图片" if kind == "image" else "文档"
        safe_label = _markdown_link_label(f"{label}: {filename}")
        url = str(item.get("url") or "").strip()
        if url and kind == "image":
            labels.append(f"![{safe_label}]({url})")
        elif url:
            labels.append(f"[{safe_label}]({url})")
        else:
            labels.append(f"[{safe_label}]")
    prefix = text or ("请分析这些附件" if len(attachments) > 1 else "请分析这个附件")
    return f"{prefix}\n\n" + "\n".join(labels)


__all__ = ["_display_message_with_attachments", "_markdown_link_label"]
