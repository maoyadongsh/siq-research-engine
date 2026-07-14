"""Deterministic helpers for immutable personal meeting lexicon versions."""

from __future__ import annotations

import hashlib
import json
from typing import Any


class LexiconConflictError(ValueError):
    pass


def canonical_entry_snapshot(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = [
        {
            "id": str(item["id"]),
            "canonical_term": str(item["canonical_term"]),
            "misrecognitions": sorted({str(value) for value in item.get("misrecognitions", [])}),
            "aliases": sorted({str(value) for value in item.get("aliases", [])}),
            "entry_type": str(item["entry_type"]),
            "weight": float(item["weight"]),
            "scope": str(item["scope"]),
            "meeting_id": item.get("meeting_id"),
        }
        for item in entries
    ]
    selected.sort(key=lambda item: (item["canonical_term"].casefold(), item["id"]))
    return selected


def entries_hash(entries: list[dict[str, Any]]) -> tuple[str, str]:
    snapshot = canonical_entry_snapshot(entries)
    encoded = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest(), encoded


def assert_unambiguous_confusions(entries: list[dict[str, Any]]) -> None:
    mappings: dict[str, set[str]] = {}
    for entry in entries:
        canonical = str(entry["canonical_term"]).casefold()
        for value in entry.get("misrecognitions", []):
            key = str(value).strip().casefold()
            if key:
                mappings.setdefault(key, set()).add(canonical)
    ambiguous = {key: sorted(values) for key, values in mappings.items() if len(values) > 1}
    if ambiguous:
        raise LexiconConflictError(f"ambiguous misrecognition mappings: {ambiguous!r}")
