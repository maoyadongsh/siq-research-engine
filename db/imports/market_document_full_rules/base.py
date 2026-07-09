from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MarketDocumentFullContext:
    market: str
    document_full_path: Path
    document_full_sha256: str
    source_root: Path | None = None


@dataclass
class MarketDocumentFullRows:
    company: dict[str, Any]
    filing: dict[str, Any]
    parse_run: dict[str, Any]
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)
    pages: list[dict[str, Any]] = field(default_factory=list)
    blocks: list[dict[str, Any]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    xbrl_contexts: list[dict[str, Any]] = field(default_factory=list)
    xbrl_units: list[dict[str, Any]] = field(default_factory=list)
    xbrl_facts_raw: list[dict[str, Any]] = field(default_factory=list)
    statements: list[dict[str, Any]] = field(default_factory=list)
    statement_items: list[dict[str, Any]] = field(default_factory=list)
    key_metrics: list[dict[str, Any]] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    chunks: list[dict[str, Any]] = field(default_factory=list)
    normalization_rules: list[dict[str, Any]] = field(default_factory=list)
    enriched_items: list[dict[str, Any]] = field(default_factory=list)
    wide_rows: list[dict[str, Any]] = field(default_factory=list)
    quality_reports: list[dict[str, Any]] = field(default_factory=list)
    footnotes: list[dict[str, Any]] = field(default_factory=list)
    toc_entries: list[dict[str, Any]] = field(default_factory=list)
    financial_note_links: list[dict[str, Any]] = field(default_factory=list)
    table_relations: list[dict[str, Any]] = field(default_factory=list)
    table_quality_signals: list[dict[str, Any]] = field(default_factory=list)
    raw_payload_refs: list[dict[str, Any]] = field(default_factory=list)


class MarketDocumentFullRule:
    market: str

    def detect(self, document_full: dict[str, Any], path: Path) -> bool:
        raise NotImplementedError

    def build_rows(self, document_full: dict[str, Any], context: MarketDocumentFullContext) -> MarketDocumentFullRows:
        raise NotImplementedError
