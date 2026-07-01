"""Pure message dedupe helpers for the Hermes agent runtime."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _dedupe_context(context: Any | None) -> Any | None:
    if hasattr(context, "model_dump"):
        return context.model_dump(exclude_none=True)
    if isinstance(context, dict):
        return context
    return None


def _dedupe_attachments(attachments: Any | None) -> list[dict[str, Any]]:
    if not attachments:
        return []
    items: list[dict[str, Any]] = []
    for item in attachments:
        if hasattr(item, "model_dump"):
            raw = item.model_dump()
        elif isinstance(item, dict):
            raw = dict(item)
        else:
            continue
        path = str(raw.get("path") or "").strip()
        if not path:
            continue
        items.append(raw)
    return items


def _dedupe_hash(message: str, context: Any | None) -> str:
    payload = {
        "message": re.sub(r"\s+", " ", message).strip(),
        "context": _dedupe_context(context),
    }
    return _hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


def _dedupe_hash_with_attachments(message: str, context: Any | None, attachments: Any | None) -> str:
    payload = {
        "message": re.sub(r"\s+", " ", message).strip(),
        "context": _dedupe_context(context),
        "attachments": [
            {
                "id": str(item.get("id") or ""),
                "path": str(item.get("path") or ""),
                "size": int(item.get("size") or 0),
            }
            for item in _dedupe_attachments(attachments)
        ],
    }
    return _hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


__all__ = [
    "_dedupe_hash",
    "_dedupe_hash_with_attachments",
    "_dedupe_attachments",
    "_dedupe_context",
    "_hash_text",
]
