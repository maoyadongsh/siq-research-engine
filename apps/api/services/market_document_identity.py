from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MARKET_ALIASES = {"US_SEC": "US", "US-SEC": "US", "US SEC": "US"}
DOCUMENT_FULL_FILENAME = "document_full.json"


def normalize_market_code(value: str | None) -> str:
    code = str(value or "").strip().upper()
    return MARKET_ALIASES.get(code, code)


def document_full_payload_value(payload: Mapping[str, Any]) -> str | None:
    value = payload.get("document_full_path") or payload.get("path") or payload.get("source_path") or payload.get("task_id")
    if value in (None, ""):
        return None
    return str(value)


def ensure_document_full_json(path: Path) -> Path:
    resolved = path / DOCUMENT_FULL_FILENAME if path.is_dir() else path
    if resolved.name != DOCUMENT_FULL_FILENAME:
        raise ValueError("document_full_path must resolve to document_full.json")
    return resolved


def unique_texts(*values: str | Path | None) -> tuple[str, ...]:
    seen: list[str] = []
    for value in values:
        text = str(value or "")
        if text and text not in seen:
            seen.append(text)
    return tuple(seen)


def resolve_document_full_path(
    *,
    market: str,
    value: str,
    safe_market_document_full_path: Callable[[str, str], Path],
) -> Path:
    path = safe_market_document_full_path(normalize_market_code(market), value)
    return ensure_document_full_json(path)


def document_full_path_keys(
    *,
    market: str,
    value: str,
    repo_root: Path,
    market_document_full_roots: Mapping[str, Path],
    safe_market_document_full_path: Callable[[str, str], Path],
) -> tuple[str, ...]:
    market_code = normalize_market_code(market)
    resolved = resolve_document_full_path(
        market=market_code,
        value=value,
        safe_market_document_full_path=safe_market_document_full_path,
    ).resolve()
    root = market_document_full_roots[market_code].resolve()
    candidates: list[str | Path | None] = [value, resolved]
    try:
        candidates.append(resolved.relative_to(repo_root))
    except ValueError:
        pass
    try:
        candidates.append(resolved.relative_to(root))
    except ValueError:
        pass
    return unique_texts(*candidates)


@dataclass(frozen=True)
class MarketDocumentFullIdentity:
    market: str
    parse_run_id: str | None = None
    filing_id: str | None = None
    document_full_path: Path | None = None
    task_id: str | None = None
    path_keys: tuple[str, ...] = ()

    def selector_payload(self) -> dict[str, str]:
        selectors: dict[str, str] = {}
        if self.parse_run_id:
            selectors["parse_run_id"] = self.parse_run_id
        if self.filing_id:
            selectors["filing_id"] = self.filing_id
        if self.document_full_path:
            selectors["document_full_path"] = str(self.document_full_path)
        if self.task_id:
            selectors["task_id"] = self.task_id
        return selectors


def build_import_selector(identity: MarketDocumentFullIdentity) -> dict[str, str]:
    selector = {"market": identity.market}
    if identity.document_full_path:
        selector["document_full_path"] = str(identity.document_full_path)
    if identity.task_id:
        selector["task_id"] = identity.task_id
    return selector


def build_status_selector(identity: MarketDocumentFullIdentity) -> dict[str, str]:
    return identity.selector_payload()


def build_agent_query_scope(identity: MarketDocumentFullIdentity) -> dict[str, str]:
    scope = {"market": identity.market}
    if identity.parse_run_id:
        scope["parse_run_id"] = identity.parse_run_id
    if identity.filing_id:
        scope["filing_id"] = identity.filing_id
    return scope


def document_full_task_path_pattern(task_id: str | None) -> str | None:
    value = str(task_id or "").strip()
    if not value:
        return None
    return f"%/{value}/{DOCUMENT_FULL_FILENAME}"


def status_task_lookup_params(identity: MarketDocumentFullIdentity) -> tuple[str, str] | tuple[()]:
    pattern = document_full_task_path_pattern(identity.task_id)
    if not identity.task_id or pattern is None:
        return ()
    return (identity.task_id, pattern)


def resolve_document_full_identity(
    *,
    market: str,
    repo_root: Path,
    market_document_full_roots: Mapping[str, Path],
    safe_market_document_full_path: Callable[[str, str], Path],
    payload: Mapping[str, Any] | None = None,
    parse_run_id: str | None = None,
    filing_id: str | None = None,
    document_full_path: str | None = None,
    task_id: str | None = None,
) -> MarketDocumentFullIdentity:
    market_code = normalize_market_code(market)
    payload_value = document_full_payload_value(payload or {})
    raw_document_value = document_full_path or payload_value
    resolved_path: Path | None = None
    keys: tuple[str, ...] = ()
    if raw_document_value:
        resolved_path = resolve_document_full_path(
            market=market_code,
            value=raw_document_value,
            safe_market_document_full_path=safe_market_document_full_path,
        )
        keys = document_full_path_keys(
            market=market_code,
            value=raw_document_value,
            repo_root=repo_root,
            market_document_full_roots=market_document_full_roots,
            safe_market_document_full_path=safe_market_document_full_path,
        )
    return MarketDocumentFullIdentity(
        market=market_code,
        parse_run_id=str(parse_run_id) if parse_run_id else None,
        filing_id=str(filing_id) if filing_id else None,
        document_full_path=resolved_path,
        task_id=str(task_id) if task_id else None,
        path_keys=keys,
    )
