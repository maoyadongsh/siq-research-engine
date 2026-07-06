from __future__ import annotations

from typing import Any

import kr_market_profile as kr


KR_STATEMENT_LABELS = {
    "balance_sheet": "Consolidated Statement of Financial Position",
    "income_statement": "Consolidated Statement of Profit or Loss",
    "cash_flow_statement": "Consolidated Statement of Cash Flows",
}


def merge_kr_quality_candidates(report: dict[str, Any], financial_data: dict[str, Any]) -> dict[str, Any]:
    merged = dict(report or {})
    merged["market"] = "KR"
    merged["market_profile"] = "KR"

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

    for statement_type, label in KR_STATEMENT_LABELS.items():
        existing_rows = key_table_candidates.get(label) or []
        candidate = _candidate_from_statement(label, by_type.get(statement_type), table_lookup, financial_data)
        if candidate and _should_use_financial_candidate(existing_rows, candidate):
            key_table_candidates[label] = _merge_candidate_rows(candidate, existing_rows)

    core_candidates = []
    found = []
    for name in _core_names(merged):
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


def _core_names(report: dict[str, Any]) -> list[str]:
    existing = [
        item.get("name")
        for item in report.get("core_financial_table_candidates") or []
        if isinstance(item, dict) and item.get("name")
    ]
    return [str(name) for name in existing] if existing else list(kr.KR_CORE_FINANCIAL_TABLE_NAMES)


def _has_located_candidate(rows: list[dict[str, Any]]) -> bool:
    return any(row.get("status") == "found" and (row.get("table_index") or row.get("line")) for row in rows)


def _should_use_financial_candidate(existing_rows: list[dict[str, Any]], candidate: dict[str, Any]) -> bool:
    if not _has_located_candidate(existing_rows):
        return True
    primary = existing_rows[0] if existing_rows else {}
    if primary.get("confidence") != "high":
        return True
    if candidate.get("table_index") and candidate.get("table_index") == primary.get("table_index"):
        return True
    return False


def _merge_candidate_rows(candidate: dict[str, Any], existing_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidate_index = candidate.get("table_index")
    merged = [candidate]
    for row in existing_rows:
        if candidate_index is not None and row.get("table_index") == candidate_index:
            continue
        merged.append(row)
    for index, row in enumerate(merged):
        row["is_primary"] = index == 0
    return merged[:5]


def _candidate_from_statement(
    label: str,
    statement: dict[str, Any] | None,
    table_lookup: dict[Any, dict[str, Any]],
    financial_data: dict[str, Any],
) -> dict[str, Any] | None:
    if not statement:
        return None
    for item in statement.get("items") or []:
        if not isinstance(item, dict):
            continue
        evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
        raw = evidence.get("raw") if isinstance(evidence.get("raw"), dict) else {}
        raw_table = raw.get("table") if isinstance(raw.get("table"), dict) else {}
        table_index = evidence.get("table_index") or raw_table.get("table_index")
        table = table_lookup.get(table_index) or raw_table
        candidate = _candidate(label, table_index, raw_table.get("line") or table.get("line"), table, financial_data, statement.get("title"))
        if candidate:
            candidate["_source"] = "financial_data_evidence"
            return candidate

    indexes = statement.get("table_indexes") or []
    table_index = indexes[0] if indexes else None
    table = table_lookup.get(table_index) or {}
    return _candidate(label, table_index, table.get("line"), table, financial_data, statement.get("title"))


def _candidate(
    label: str,
    table_index: Any,
    line: Any,
    table: dict[str, Any],
    financial_data: dict[str, Any],
    title: Any,
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
        "heading": table.get("heading") or table.get("title") or title or label,
        "unit": table.get("unit") or financial_data.get("unit") or "",
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
