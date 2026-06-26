#!/usr/bin/env python3
"""Shared provenance normalization for SIQ report evidence."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


PUBLIC_ORIGIN = os.environ.get("SIQ_PUBLIC_ORIGIN", "https://arthurmao.synology.me:8276").rstrip("/")
MISSING_TOKENS = {"", "none", "null", "n/a", "na", "nan", "unknown", "未返回"}


def public_api_url(path: str) -> str:
    if path.startswith(("http://", "https://")):
        return path
    if path.startswith("/"):
        return f"{PUBLIC_ORIGIN}{path}"
    return path


def load_json_if_exists(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    return text.lower() in MISSING_TOKENS


def is_positive_int_token(value: Any) -> bool:
    text = str(value).strip()
    return bool(re.fullmatch(r"\d+", text)) and int(text) > 0


def is_nonnegative_int_token(value: Any) -> bool:
    return bool(re.fullmatch(r"\d+", str(value).strip()))


def clean_value(value: Any) -> Any:
    return "未返回" if is_missing(value) else value


def first_present(*values: Any) -> Any:
    for value in values:
        if not is_missing(value):
            return value
    return None


def int_token(value: Any) -> int | None:
    if not is_nonnegative_int_token(value):
        return None
    return int(str(value).strip())


@dataclass
class ProvenanceLookup:
    by_table: dict[int, dict[str, Any]] = field(default_factory=dict)
    by_line: dict[int, dict[str, Any]] = field(default_factory=dict)

    def add(self, raw: dict[str, Any]) -> None:
        table_index = int_token(first_present(raw.get("table_index"), raw.get("content_table_source_id")))
        md_line = int_token(first_present(raw.get("md_line"), raw.get("line"), raw.get("source_md_line")))
        page = first_present(raw.get("pdf_page_number"), raw.get("pdf_page"))
        if is_missing(page):
            page_idx = int_token(raw.get("pdf_page_index"))
            if page_idx is not None:
                page = page_idx + 1
        if is_missing(page):
            page_idx = int_token(raw.get("page_idx"))
            if page_idx is not None:
                page = page_idx + 1
        ref: dict[str, Any] = {}
        for key in (
            "company_id",
            "report_id",
            "stock_code",
            "task_id",
            "pdf_page_number",
            "pdf_page",
            "table_index",
            "md_line",
            "line",
            "open_pdf_page_url",
            "open_source_page_url",
            "open_source_table_url",
        ):
            if key in raw and not is_missing(raw.get(key)):
                ref[key] = raw[key]
        if not is_missing(page):
            ref["pdf_page_number"] = page
        if table_index is not None:
            ref["table_index"] = table_index
        if md_line is not None:
            ref["md_line"] = md_line
        if not ref:
            return
        if table_index is not None:
            self.by_table.setdefault(table_index, ref)
        if md_line is not None:
            self.by_line.setdefault(md_line, ref)

    def find(self, table_index: Any = None, md_line: Any = None) -> dict[str, Any] | None:
        table_key = int_token(table_index)
        if table_key is not None and table_key in self.by_table:
            return self.by_table[table_key]
        line_key = int_token(md_line)
        if line_key is not None and line_key in self.by_line:
            return self.by_line[line_key]
        return None


def load_provenance_lookup(company_dir: Path | None, year: int | str | None = None) -> ProvenanceLookup:
    lookup = ProvenanceLookup()
    if not company_dir:
        return lookup

    pdf_refs = load_json_if_exists(company_dir / "evidence" / "pdf_refs.json")
    if isinstance(pdf_refs, dict):
        for ref in pdf_refs.get("refs", []) or []:
            if isinstance(ref, dict):
                lookup.add(ref)

    report_year = str(year or "2025")
    document_full = load_json_if_exists(company_dir / "reports" / f"{report_year}-annual" / "document_full.json")
    if isinstance(document_full, dict):
        enhanced = document_full.get("content_list_enhanced")
        if isinstance(enhanced, dict):
            for table in enhanced.get("tables", []) or []:
                if isinstance(table, dict):
                    lookup.add(table)
        quality = document_full.get("quality_report")
        if isinstance(quality, dict):
            for key in ("core_financial_table_candidates", "table_candidates"):
                for item in quality.get(key, []) or []:
                    if isinstance(item, dict):
                        lookup.add(item)
    return lookup


def normalize_evidence_record(
    record: dict[str, Any],
    lookup: ProvenanceLookup | None = None,
    default_task_id: Any = None,
    metric_key: str | None = None,
    period: str | None = None,
    source_kind: str | None = None,
    url_builder: Callable[[str], str] = public_api_url,
) -> dict[str, Any]:
    item = dict(record)
    if metric_key and is_missing(item.get("metric_key")):
        item["metric_key"] = metric_key
    if period and is_missing(item.get("period")):
        item["period"] = period
    if source_kind and is_missing(item.get("source_kind")):
        item["source_kind"] = source_kind

    table_index = first_present(item.get("table_index"))
    md_line = first_present(item.get("md_line"), item.get("line"))
    page = first_present(item.get("pdf_page_number"), item.get("pdf_page"))
    task_id = first_present(item.get("task_id"), default_task_id)

    ref = lookup.find(table_index, md_line) if lookup else None
    if ref:
        table_index = first_present(table_index, ref.get("table_index"))
        md_line = first_present(md_line, ref.get("md_line"), ref.get("line"))
        page = first_present(page, ref.get("pdf_page_number"), ref.get("pdf_page"))
        task_id = first_present(task_id, ref.get("task_id"))

    item["task_id"] = clean_value(task_id)
    item["pdf_page_number"] = clean_value(page)
    item["table_index"] = clean_value(table_index)
    item["md_line"] = clean_value(md_line)

    if is_positive_int_token(item["pdf_page_number"]):
        item["pdf_page_number"] = int(str(item["pdf_page_number"]).strip())
    if is_nonnegative_int_token(item["table_index"]):
        item["table_index"] = int(str(item["table_index"]).strip())
    if is_nonnegative_int_token(item["md_line"]):
        item["md_line"] = int(str(item["md_line"]).strip())

    valid_task = not is_missing(item.get("task_id"))
    valid_page = is_positive_int_token(item.get("pdf_page_number"))
    valid_table = is_nonnegative_int_token(item.get("table_index"))
    if valid_task and valid_page:
        item["open_pdf_page_url"] = url_builder(f"/api/pdf_page/{item['task_id']}/{item['pdf_page_number']}")
        item["open_source_page_url"] = url_builder(f"/api/source/{item['task_id']}/page/{item['pdf_page_number']}")
    else:
        item["open_pdf_page_url"] = None
        item["open_source_page_url"] = None
    if valid_task and valid_table:
        item["open_source_table_url"] = url_builder(f"/api/source/{item['task_id']}/table/{item['table_index']}")
    else:
        item["open_source_table_url"] = None

    if valid_page:
        item["provenance_status"] = "resolved"
    elif valid_table:
        item["provenance_status"] = "table_only"
    else:
        item["provenance_status"] = "missing"
    return item


def metric_alias_candidates(metric_key: str, aliases: dict[str, Any] | None = None) -> list[str]:
    result = [metric_key]
    if aliases:
        raw = aliases.get(metric_key)
        if isinstance(raw, str):
            result.append(raw)
        elif isinstance(raw, list):
            result.extend(str(item) for item in raw)
        for key, raw_value in aliases.items():
            values = [raw_value] if isinstance(raw_value, str) else raw_value if isinstance(raw_value, list) else []
            if metric_key in {str(item) for item in values}:
                result.append(str(key))
    deduped: list[str] = []
    for item in result:
        if item and item not in deduped:
            deduped.append(item)
    return deduped


def first_evidence_for_metric(
    financial_evidence: dict[str, Any],
    metric_key: str,
    aliases: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    for key in metric_alias_candidates(metric_key, aliases):
        item = financial_evidence.get(key)
        if not isinstance(item, dict):
            continue
        evidence = item.get("evidence")
        if isinstance(evidence, list):
            for record in evidence:
                if isinstance(record, dict):
                    return record
    return None


def normalize_evidence_package(
    package: dict[str, Any],
    snapshot: dict[str, Any] | None = None,
    lookup: ProvenanceLookup | None = None,
    default_task_id: Any = None,
    year: int | str | None = None,
    aliases: dict[str, Any] | None = None,
    url_builder: Callable[[str], str] = public_api_url,
) -> dict[str, Any]:
    normalized = dict(package)
    financial = normalized.get("financial_evidence")
    if not isinstance(financial, dict):
        return normalized

    target_year = str(year or normalized.get("report_year") or "2025")
    for key, payload in list(financial.items()):
        if not isinstance(payload, dict):
            continue
        evidence = payload.get("evidence")
        if not isinstance(evidence, list):
            evidence = []
        payload["evidence"] = [
            normalize_evidence_record(
                record,
                lookup=lookup,
                default_task_id=default_task_id,
                metric_key=str(key),
                period=target_year,
                url_builder=url_builder,
            )
            for record in evidence
            if isinstance(record, dict)
        ]

    metrics = snapshot.get("metrics", {}) if isinstance(snapshot, dict) else {}
    if isinstance(metrics, dict):
        for key, metric in metrics.items():
            if not isinstance(metric, dict):
                continue
            sources = metric.get("sources") if isinstance(metric.get("sources"), dict) else {}
            source = sources.get(target_year) or sources.get(f"{target_year}-12-31") or {}
            if not isinstance(source, dict):
                continue
            derived_from = source.get("derived_from")
            if not isinstance(derived_from, list) or not derived_from:
                continue
            payload = financial.setdefault(str(key), {"evidence": []})
            evidence = payload.get("evidence") if isinstance(payload.get("evidence"), list) else []
            has_resolved = any(
                isinstance(record, dict) and record.get("provenance_status") == "resolved"
                for record in evidence
            )
            if has_resolved:
                payload["evidence"] = evidence
                continue
            inherited: list[dict[str, Any]] = []
            for component_key in derived_from:
                component = first_evidence_for_metric(financial, str(component_key), aliases)
                if not component:
                    continue
                inherited_record = dict(component)
                inherited_record["metric_key"] = str(key)
                inherited_record["component_metric_key"] = str(component_key)
                inherited_record["derived_metric_key"] = str(key)
                inherited_record["source_kind"] = f"derived_from:{component_key}"
                inherited.append(
                    normalize_evidence_record(
                        inherited_record,
                        lookup=lookup,
                        default_task_id=default_task_id,
                        metric_key=str(key),
                        period=target_year,
                        url_builder=url_builder,
                    )
                )
            if inherited:
                payload["evidence"] = inherited

    normalized["financial_evidence"] = financial
    return normalized


def evidence_id_from_record(metric_key: str, record: dict[str, Any] | None) -> str:
    if not record:
        return f"{metric_key}:missing"
    period = record.get("period", "unknown")
    page = clean_value(first_present(record.get("pdf_page_number"), record.get("pdf_page")))
    table = clean_value(record.get("table_index"))
    page_token = "unknown" if is_missing(page) else page
    table_token = "unknown" if is_missing(table) else table
    return f"{metric_key}:{period}:p{page_token}:t{table_token}"
