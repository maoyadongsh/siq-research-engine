from __future__ import annotations

import json
from difflib import SequenceMatcher
from functools import lru_cache
from importlib import resources
from typing import Any


def _normalize(text: str | None) -> str:
    return "".join(ch.lower() for ch in str(text or "") if ch.isalnum())


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _entry_names(entry: dict[str, Any]) -> list[str]:
    return [
        entry.get("canonical_name"),
        entry.get("ticker"),
        entry.get("company_id"),
        *(entry.get("aliases") or []),
    ]


def _is_exact_or_contained(query: str, candidate: str) -> bool:
    if candidate == query:
        return True
    if len(query) < 2 or len(candidate) < 2:
        return False
    return candidate in query or query in candidate


def _fuzzy_score(query: str, candidate: str) -> float:
    if len(query) < 3 or len(candidate) < 3:
        return 0.0
    if not (_has_cjk(query) or _has_cjk(candidate)):
        return 0.0
    return SequenceMatcher(None, query, candidate).ratio()


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
    best_entry: dict[str, Any] | None = None
    best_score = 0.0
    for entry in _catalog():
        if target_market and str(entry.get("market") or "").upper() != target_market:
            continue
        for name in _entry_names(entry):
            candidate = _normalize(str(name or ""))
            if candidate and _is_exact_or_contained(normalized, candidate):
                return entry
            score = _fuzzy_score(normalized, candidate)
            if score > best_score:
                best_entry = entry
                best_score = score
    if best_score >= 0.78:
        return best_entry
    return None
