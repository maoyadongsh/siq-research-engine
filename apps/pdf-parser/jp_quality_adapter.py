from __future__ import annotations

from typing import Any

import jp_market_profile as jp


JP_STATEMENT_LABELS = {
    "balance_sheet": "Consolidated Statement of Financial Position",
    "income_statement": "Consolidated Statement of Profit or Loss",
    "cash_flow_statement": "Consolidated Statement of Cash Flows",
}


def merge_jp_quality_candidates(report: dict[str, Any], financial_data: dict[str, Any]) -> dict[str, Any]:
    merged = dict(report or {})
    merged["market"] = "JP"
    merged["market_profile"] = "JP"
    if financial_data.get("accounting_standard"):
        merged["accounting_standard"] = financial_data.get("accounting_standard")
    if financial_data.get("industry_profile"):
        merged["industry_profile"] = financial_data.get("industry_profile")

    table_lookup = {
        item.get("table_index"): item
        for item in merged.get("table_index") or []
        if isinstance(item, dict) and item.get("table_index") is not None
    }
    key_table_candidates = {
        str(name): [dict(row) for row in rows if isinstance(row, dict)]
        for name, rows in (merged.get("key_table_candidates") or {}).items()
        if isinstance(rows, list)
    }
    by_type = {
        statement.get("statement_type"): statement
        for statement in financial_data.get("statements") or []
        if isinstance(statement, dict)
    }

    for statement_type, label in JP_STATEMENT_LABELS.items():
        existing_rows = key_table_candidates.get(label) or []
        if _has_located_candidate(existing_rows):
            continue
        candidate = _candidate_from_statement(label, by_type.get(statement_type), table_lookup, financial_data)
        if not candidate:
            continue
        key_table_candidates[label] = [candidate]

    core_names = _core_names(merged, financial_data)
    core_candidates = []
    found = []
    for name in core_names:
        rows = key_table_candidates.get(name) or []
        if _has_located_candidate(rows):
            primary = dict(rows[0])
            primary["name"] = name
            primary["status"] = "found"
            primary["candidate_count"] = len(rows)
            primary["candidate_group"] = "core"
            primary.setdefault("is_primary", True)
            core_candidates.append(primary)
            found.append(name)
        else:
            core_candidates.append({"name": name, "status": "missing", "candidate_group": "core"})

    merged["key_table_candidates"] = key_table_candidates
    merged["core_financial_table_candidates"] = core_candidates
    merged["found_financial_tables"] = found
    merged["report_kind"] = financial_data.get("report_kind") or merged.get("report_kind")
    return merged


def _core_names(report: dict[str, Any], financial_data: dict[str, Any]) -> list[str]:
    existing = [
        item.get("name")
        for item in report.get("core_financial_table_candidates") or []
        if isinstance(item, dict) and item.get("name")
    ]
    if existing:
        return [str(name) for name in existing]
    return jp.core_financial_table_names_for_report(financial_data.get("report_kind") or report.get("report_kind"))


def _has_located_candidate(rows: list[dict[str, Any]]) -> bool:
    return any(row.get("status") == "found" and (row.get("table_index") or row.get("line")) for row in rows)


def _candidate_from_statement(
    label: str,
    statement: dict[str, Any] | None,
    table_lookup: dict[Any, dict[str, Any]],
    financial_data: dict[str, Any],
) -> dict[str, Any] | None:
    if not statement:
        return None
    evidence_candidate = _candidate_from_statement_evidence(label, statement, table_lookup, financial_data)
    if evidence_candidate:
        return evidence_candidate

    indexes = statement.get("table_indexes") or []
    table_index = indexes[0] if indexes else None
    table = table_lookup.get(table_index) or {}
    line_numbers = statement.get("line_numbers") or []
    line = line_numbers[0] if line_numbers else table.get("line")
    return _candidate(label, table_index, line, table, financial_data, statement.get("unit"))


def _candidate_from_statement_evidence(
    label: str,
    statement: dict[str, Any],
    table_lookup: dict[Any, dict[str, Any]],
    financial_data: dict[str, Any],
) -> dict[str, Any] | None:
    candidates: list[tuple[float, dict[str, Any]]] = []
    for item in statement.get("items") or []:
        if not isinstance(item, dict):
            continue
        evidences = []
        if isinstance(item.get("evidence"), dict):
            evidences.append(item["evidence"])
        sources = item.get("sources") if isinstance(item.get("sources"), dict) else {}
        evidences.extend(source for source in sources.values() if isinstance(source, dict))
        for evidence in evidences:
            raw = evidence.get("raw") if isinstance(evidence.get("raw"), dict) else {}
            raw_table = raw.get("table") if isinstance(raw.get("table"), dict) else {}
            table = _table_for_evidence(evidence, raw_table, table_lookup)
            table_index = table.get("table_index") or evidence.get("table_index")
            line = raw_table.get("line") or table.get("line")
            candidate = _candidate(label, table_index, line, table, financial_data, statement.get("unit"))
            if not candidate:
                continue
            score = 96.0
            source = str(raw_table.get("source") or "")
            if "statement_table" in source or "formal_statement" in source:
                score += 2.0
            if raw.get("detected_statement_type"):
                score += 1.0
            candidate["candidate_score"] = min(score, 99.0)
            candidate["confidence"] = "high"
            candidate["_source"] = "financial_data_evidence"
            candidates.append((candidate["candidate_score"], candidate))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-(item[0]), item[1].get("line") or 10**9))
    return candidates[0][1]


def _table_for_evidence(
    evidence: dict[str, Any],
    raw_table: dict[str, Any],
    table_lookup: dict[Any, dict[str, Any]],
) -> dict[str, Any]:
    raw_line = _safe_int(raw_table.get("line"))
    if raw_line is not None:
        nearest = _nearest_table_by_line(table_lookup.values(), raw_line)
        if nearest:
            merged = dict(nearest)
            merged.setdefault("heading", raw_table.get("heading"))
            merged.setdefault("preview", raw_table.get("preview"))
            return merged
    table = dict(table_lookup.get(evidence.get("table_index")) or {})
    if table:
        return table
    return {
        "table_index": evidence.get("table_index") or raw_table.get("table_index"),
        "line": raw_line,
        "pdf_page_number": evidence.get("page_number") or raw_table.get("pdf_page_number"),
        "heading": raw_table.get("heading"),
        "title": raw_table.get("heading"),
        "unit": raw_table.get("unit"),
        "preview": raw_table.get("preview"),
        "source": raw_table.get("source"),
    }


def _nearest_table_by_line(tables: Any, line: int) -> dict[str, Any] | None:
    best: tuple[int, dict[str, Any]] | None = None
    for table in tables:
        if not isinstance(table, dict):
            continue
        table_line = _safe_int(table.get("line"))
        if table_line is None:
            continue
        distance = abs(table_line - line)
        if distance > 4:
            continue
        if best is None or distance < best[0]:
            best = (distance, table)
    return best[1] if best else None


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _candidate(
    label: str,
    table_index: Any,
    line: Any,
    table: dict[str, Any],
    financial_data: dict[str, Any],
    unit: Any,
) -> dict[str, Any] | None:
    if not table_index and not line:
        return None
    return {
        "name": label,
        "status": "found",
        "table_index": table_index,
        "line": line,
        "pdf_page_number": table.get("pdf_page_number") or table.get("page_number"),
        "pdf_page_source": table.get("pdf_page_source"),
        "pdf_page_inference_reason": table.get("pdf_page_inference_reason"),
        "bbox": table.get("bbox") or [],
        "rows": table.get("rows"),
        "cells": table.get("cells"),
        "empty_ratio": table.get("empty_ratio"),
        "numeric_ratio": table.get("numeric_ratio"),
        "heading": table.get("heading") or table.get("title") or label,
        "unit": unit or table.get("unit") or "",
        "table_type": table.get("table_type") or "fact",
        "year_binding_required": True,
        "report_year": financial_data.get("report_year"),
        "candidate_group": "core",
        "candidate_score": 99.0,
        "confidence": "high",
        "preview": table.get("preview") or label,
        "is_primary": True,
        "_source": "financial_data",
    }
