"""Immutable ResearchIdentity snapshots stored with individual chat messages."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from services import market_document_identity

RESEARCH_IDENTITY_SNAPSHOT_FIELDS = ("market", "company_id", "filing_id", "parse_run_id")


def normalize_research_identity_snapshot(value: Any | None) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}

    snapshot: dict[str, str] = {}
    for field in RESEARCH_IDENTITY_SNAPSHOT_FIELDS:
        raw_value = value.get(field)
        if raw_value is None:
            continue
        text = str(raw_value).strip()
        if not text:
            continue
        snapshot[field] = (
            market_document_identity.normalize_market_code(text)
            if field == "market"
            else text
        )
    return snapshot


def encode_research_identity_snapshot(value: Any | None) -> str | None:
    snapshot = normalize_research_identity_snapshot(value)
    if not snapshot:
        return None
    return json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))


def decode_research_identity_snapshot(value: Any | None) -> dict[str, str] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return None
    snapshot = normalize_research_identity_snapshot(payload)
    return snapshot or None


__all__ = [
    "RESEARCH_IDENTITY_SNAPSHOT_FIELDS",
    "decode_research_identity_snapshot",
    "encode_research_identity_snapshot",
    "normalize_research_identity_snapshot",
]
