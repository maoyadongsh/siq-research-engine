#!/usr/bin/env python3
"""Canonical EvidenceRef schema for SIQ artifacts.

All citation-bearing artifacts (analysis reports, factcheck outputs, tracking
items, legal opinions) should normalize their evidence references through
``EvidenceRef`` before serializing. This kills the historical "field name
spaghetti" (pdf_page vs pdf_page_number, md_line vs markdown_line, etc.) and
gives downstream tooling a single shape to validate against.

Usage:

    from citation_schema import EvidenceRef

    ref = EvidenceRef.from_legacy({
        "task_id": "abc...",
        "pdf_page": 132,
        "table_index": 89,
        "md_line": 2497,
        "source_type": "okf_evidence",
    })
    payload = ref.to_dict()  # canonical field names + auto-generated URLs

The dataclass is intentionally permissive: extra fields go into ``extra`` so we
can carry forward analyst-specific metadata without losing it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


VALID_SOURCE_TYPES = {
    "okf_metrics",
    "okf_evidence",
    "okf_semantic",
    "okf_analysis",
    "okf_table",
    "okf_document_links",
    "okf_report",
    "okf_report_table",
    "okf_report_text",
    "okf_report_fulltext",
    "okf_document_full",
    "okf_metadata",
    "wiki_metrics",
    "wiki_evidence",
    "wiki_semantic",
    "wiki_analysis",
    "wiki_table",
    "wiki_document_links",
    "wiki_report",
    "wiki_report_table",
    "wiki_report_text",
    "wiki_report_fulltext",
    "wiki_document_full",
    "wiki_metadata",
    "postgresql",
    "pdf_original",
    "milvus",
    "external",
}
PUBLIC_ORIGIN = os.environ.get("SIQ_PUBLIC_ORIGIN", "").rstrip("/")

# Legacy → canonical field aliases. Add new aliases here when you spot more
# variants in old artifacts.
_FIELD_ALIASES: dict[str, str] = {
    "pdf_page": "pdf_page_number",
    "page": "pdf_page_number",
    "page_no": "pdf_page_number",
    "page_number": "pdf_page_number",
    "md_line": "markdown_line",
    "line": "markdown_line",
    "line_number": "markdown_line",
    "task": "task_id",
    "evidence_id": "evidence_id",
    "source_path": "file",
    "metric_name": "metric_or_claim",
    "canonical_name": "metric_or_claim",
    "metric": "metric_or_claim",
    "metric_key": "metric_or_claim",
    "report_id": "report_id",
}


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "" or value == "未返回":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _public_api_url(path: str) -> str:
    if path.startswith(("http://", "https://")):
        return path
    if path.startswith("/"):
        return f"{PUBLIC_ORIGIN}{path}"
    return path


def _build_urls(task_id: str | None, pdf_page: int | None, table_index: int | None) -> dict[str, str]:
    urls: dict[str, str] = {}
    if task_id and pdf_page:
        urls["open_pdf_page_url"] = _public_api_url(f"/api/pdf_page/{task_id}/{pdf_page}")
        urls["open_source_page_url"] = _public_api_url(f"/api/source/{task_id}/page/{pdf_page}")
    if task_id and table_index:
        urls["open_source_table_url"] = _public_api_url(f"/api/source/{task_id}/table/{table_index}")
    return urls


@dataclass
class EvidenceRef:
    """Canonical evidence reference shape used across SIQ artifacts."""

    source_type: str  # See ``VALID_SOURCE_TYPES``.
    file: str | None = None
    metric_or_claim: str | None = None
    period: str | None = None
    statement_type: str | None = None
    scope: str | None = None  # consolidated / parent / segment
    value: Any = None
    raw_value: Any = None
    unit: str | None = None
    task_id: str | None = None
    report_id: str | None = None
    pdf_page_number: int | None = None
    table_index: int | None = None
    markdown_line: int | None = None
    open_pdf_page_url: str | None = None
    open_source_page_url: str | None = None
    open_source_table_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_legacy(cls, payload: dict[str, Any]) -> "EvidenceRef":
        """Build a canonical ref from any historical/legacy shape."""
        if not isinstance(payload, dict):
            raise TypeError(f"EvidenceRef.from_legacy expects a dict, got {type(payload).__name__}")

        source_type = str(payload.get("source_type") or "okf_metrics")
        if source_type not in VALID_SOURCE_TYPES:
            # Don't reject; just keep the value but flag it via extra so callers
            # can audit unknown sources.
            payload.setdefault("extra", {})["unknown_source_type"] = source_type

        # Pull through canonical fields, applying aliases.
        canonical: dict[str, Any] = {}
        for key, value in payload.items():
            target = _FIELD_ALIASES.get(key, key)
            if target in canonical and canonical[target]:
                continue
            canonical[target] = value

        pdf_page = _coerce_int(canonical.get("pdf_page_number"))
        table_index = _coerce_int(canonical.get("table_index"))
        markdown_line = _coerce_int(canonical.get("markdown_line"))
        task_id = canonical.get("task_id")
        if isinstance(task_id, str) and not task_id.strip():
            task_id = None

        ref = cls(
            source_type=source_type,
            file=canonical.get("file"),
            metric_or_claim=canonical.get("metric_or_claim"),
            period=canonical.get("period") or None,
            statement_type=canonical.get("statement_type"),
            scope=canonical.get("scope"),
            value=canonical.get("value"),
            raw_value=canonical.get("raw_value"),
            unit=canonical.get("unit"),
            task_id=task_id,
            report_id=canonical.get("report_id"),
            pdf_page_number=pdf_page,
            table_index=table_index,
            markdown_line=markdown_line,
            open_pdf_page_url=canonical.get("open_pdf_page_url"),
            open_source_page_url=canonical.get("open_source_page_url"),
            open_source_table_url=canonical.get("open_source_table_url"),
            extra=canonical.get("extra") or {},
        )
        # Auto-fill URLs when we can derive them and they are missing.
        derived = _build_urls(ref.task_id, ref.pdf_page_number, ref.table_index)
        for key, url in derived.items():
            if not getattr(ref, key, None):
                setattr(ref, key, url)
        return ref

    def to_dict(self, drop_none: bool = True) -> dict[str, Any]:
        payload = {
            "source_type": self.source_type,
            "file": self.file,
            "metric_or_claim": self.metric_or_claim,
            "period": self.period,
            "statement_type": self.statement_type,
            "scope": self.scope,
            "value": self.value,
            "raw_value": self.raw_value,
            "unit": self.unit,
            "task_id": self.task_id,
            "report_id": self.report_id,
            "pdf_page_number": self.pdf_page_number,
            "table_index": self.table_index,
            "markdown_line": self.markdown_line,
            "open_pdf_page_url": self.open_pdf_page_url,
            "open_source_page_url": self.open_source_page_url,
            "open_source_table_url": self.open_source_table_url,
        }
        if drop_none:
            payload = {k: v for k, v in payload.items() if v is not None}
        if self.extra:
            payload["extra"] = self.extra
        return payload

    def has_pdf_page(self) -> bool:
        return self.pdf_page_number is not None or bool(self.open_pdf_page_url)

    def is_complete(self) -> bool:
        """A ref is 'complete' when it can resolve back to a PDF page."""
        return bool(self.task_id) and self.pdf_page_number is not None


def normalize_refs(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize a list of legacy refs to canonical dicts in one pass."""
    return [EvidenceRef.from_legacy(item).to_dict() for item in items if isinstance(item, dict)]


__all__ = ["EvidenceRef", "VALID_SOURCE_TYPES", "normalize_refs"]
