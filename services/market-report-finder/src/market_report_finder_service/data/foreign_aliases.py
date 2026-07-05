from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any


def _normalize(text: str | None) -> str:
    return "".join(ch.lower() for ch in str(text or "") if ch.isalnum())


@lru_cache(maxsize=1)
def _catalog() -> list[dict[str, Any]]:
    payload = resources.files("market_report_finder_service.data").joinpath("foreign_company_aliases.json").read_text(
        encoding="utf-8"
    )
    data = json.loads(payload)
    return data.get("companies", [])


def foreign_alias_entry(market: str | None, query: str | None) -> dict[str, Any] | None:
    normalized = _normalize(query)
    if not normalized:
        return None
    target_market = str(market or "").upper()
    for entry in _catalog():
        if target_market and str(entry.get("market") or "").upper() != target_market:
            continue
        names = [
            entry.get("canonical_name"),
            entry.get("ticker"),
            entry.get("company_id"),
            *(entry.get("aliases") or []),
        ]
        for name in names:
            candidate = _normalize(str(name or ""))
            if candidate and (candidate == normalized or candidate in normalized or normalized in candidate):
                return entry
    return None
