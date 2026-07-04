from __future__ import annotations

from typing import Any

HK_STATEMENT_LABELS = {
    "balance_sheet": "Statement of Financial Position",
    "income_statement": "Statement of Profit or Loss",
    "cash_flow_statement": "Statement of Cash Flows",
    "equity_statement": "Statement of Changes in Equity",
}

HK_KEY_METRIC_LABELS = {
    "occupancy_rate": "Occupancy Rate",
    "portfolio_valuation": "Portfolio Valuation",
    "net_property_income": "Net Property Income",
    "distribution_per_unit": "Distribution Per Unit",
    "contracted_sales": "Contracted Sales",
    "gross_floor_area": "Gross Floor Area",
    "loan_balance": "Loans and Advances",
    "deposits": "Customer Deposits",
    "net_interest_margin": "Net Interest Margin",
    "npl_ratio": "Non-performing Loan Ratio",
    "gross_written_premiums": "Gross Written Premiums",
    "combined_ratio": "Combined Ratio",
}


def merge_hk_quality_candidates(report: dict[str, Any], financial_data: dict[str, Any]) -> dict[str, Any]:
    merged = dict(report or {})
    merged["market"] = "HK"
    merged["accounting_standard"] = financial_data.get("accounting_standard") or merged.get("accounting_standard") or "HKFRS"
    merged["industry_profile"] = financial_data.get("industry_profile") or merged.get("industry_profile") or "general"
    table_lookup = {
        item.get("table_index"): item
        for item in merged.get("table_index") or []
        if isinstance(item, dict) and item.get("table_index") is not None
    }
    key_table_candidates: dict[str, list[dict[str, Any]]] = {}
    core_candidates: list[dict[str, Any]] = []
    found: list[str] = []
    by_type = {statement.get("statement_type"): statement for statement in financial_data.get("statements") or [] if isinstance(statement, dict)}
    for statement_type, label in HK_STATEMENT_LABELS.items():
        statement = by_type.get(statement_type)
        row = _candidate_from_statement(label, statement, table_lookup, financial_data) if statement else None
        if row:
            found.append(label)
            key_table_candidates[label] = [row]
            core_candidates.append(row)
        else:
            core_candidates.append({"name": label, "status": "missing", "candidate_group": "core"})
    hk_key_candidates: dict[str, list[dict[str, Any]]] = {}
    for metric in list(financial_data.get("key_metrics") or []) + list(financial_data.get("operating_metrics") or []):
        if not isinstance(metric, dict):
            continue
        label = HK_KEY_METRIC_LABELS.get(metric.get("canonical_name"))
        if not label:
            continue
        candidate = _candidate_from_metric(label, metric, table_lookup, financial_data)
        if candidate:
            hk_key_candidates.setdefault(label, []).append(candidate)
    key_table_candidates.update(hk_key_candidates)
    merged["key_table_candidates"] = key_table_candidates
    merged["hk_key_table_candidates"] = hk_key_candidates
    merged["indicator_table_candidates"] = [
        candidate
        for candidates in hk_key_candidates.values()
        for candidate in candidates
    ]
    merged["core_financial_table_candidates"] = core_candidates
    merged["found_financial_tables"] = found
    merged["report_kind"] = financial_data.get("report_kind") or merged.get("report_kind")
    return merged


def _candidate_from_statement(label: str, statement: dict[str, Any] | None, table_lookup: dict[Any, dict[str, Any]], financial_data: dict[str, Any]) -> dict[str, Any] | None:
    if not statement:
        return None
    indexes = statement.get("table_indexes") or []
    table_index = indexes[0] if indexes else None
    table = table_lookup.get(table_index) or {}
    line_numbers = statement.get("line_numbers") or []
    return _candidate(label, table_index, line_numbers[0] if line_numbers else table.get("line"), table, financial_data, statement.get("unit"))


def _candidate_from_metric(label: str, metric: dict[str, Any], table_lookup: dict[Any, dict[str, Any]], financial_data: dict[str, Any]) -> dict[str, Any] | None:
    evidence = metric.get("evidence") if isinstance(metric.get("evidence"), dict) else {}
    table_index = evidence.get("table_index")
    table = table_lookup.get(table_index) or {}
    return _candidate(label, table_index, evidence.get("line") or table.get("line"), table, financial_data, metric.get("unit"))


def _candidate(label: str, table_index: Any, line: Any, table: dict[str, Any], financial_data: dict[str, Any], unit: Any) -> dict[str, Any] | None:
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
        "candidate_group": "core" if label in HK_STATEMENT_LABELS.values() else "indicator",
        "candidate_score": 99.0,
        "confidence": "high",
        "preview": table.get("preview") or label,
        "is_primary": True,
        "_source": "financial_data",
    }
