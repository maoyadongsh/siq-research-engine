"""Pure message dedupe helpers for the Hermes agent runtime."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass
class RecentRunRecord:
    message_hash: str
    reply: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


RECENT_COMPLETED_RUNS: dict[tuple[Any, str], RecentRunRecord] = {}


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


def recent_duplicate_reply(
    profile: Any,
    session_id: str,
    message_hash: str,
    *,
    active_key: Callable[[Any, str], tuple[Any, str]],
    idempotency_window_seconds: int,
    duplicate_message: str,
    analysis_duplicate_message: str,
    analysis_profile: str = "siq_analysis",
) -> str | None:
    record = RECENT_COMPLETED_RUNS.get(active_key(profile, session_id))
    if not record or record.message_hash != message_hash:
        return None
    created_at = record.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - created_at
    if age > timedelta(seconds=idempotency_window_seconds):
        return None
    fallback = analysis_duplicate_message if profile == analysis_profile else duplicate_message
    return record.reply or fallback


def forget_recent_completed_run(
    profile: Any,
    session_id: str,
    message_hash: str | None = None,
    *,
    active_key: Callable[[Any, str], tuple[Any, str]],
) -> None:
    key = active_key(profile, session_id)
    record = RECENT_COMPLETED_RUNS.get(key)
    if not record:
        return
    if message_hash and record.message_hash != message_hash:
        return
    RECENT_COMPLETED_RUNS.pop(key, None)


def remember_completed_run(
    profile: Any,
    session_id: str,
    message_hash: str | None,
    reply: str,
    *,
    active_key: Callable[[Any, str], tuple[Any, str]],
) -> None:
    if not message_hash:
        return
    RECENT_COMPLETED_RUNS[active_key(profile, session_id)] = RecentRunRecord(message_hash=message_hash, reply=reply)


__all__ = [
    "_dedupe_hash",
    "_dedupe_hash_with_attachments",
    "_dedupe_attachments",
    "_dedupe_context",
    "_hash_text",
    "RECENT_COMPLETED_RUNS",
    "RecentRunRecord",
    "forget_recent_completed_run",
    "recent_duplicate_reply",
    "remember_completed_run",
]
