"""Normalize market-specific Wiki financial artifacts for agent consumption."""

from __future__ import annotations

import re
from typing import Any


def normalize_statement_records(payload: Any) -> list[dict[str, Any]]:
    """Return the common flat metric shape used by the A-share answer path."""
    if not isinstance(payload, dict):
        return []
    statements = payload.get("statements")
    if isinstance(statements, list):
        return _normalize_statement_items(statements)
    return _collect_flat_records(payload)


def _normalize_statement_items(statements: list[Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for statement in statements:
        if not isinstance(statement, dict):
            continue
        statement_type = str(statement.get("statement_type") or statement.get("statement_id") or "")
        items = statement.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            values = item.get("values")
            if not isinstance(values, dict):
                continue
            raw_values = item.get("raw_values") if isinstance(item.get("raw_values"), dict) else {}
            sources = item.get("sources") if isinstance(item.get("sources"), dict) else {}
            periods = item.get("periods") if isinstance(item.get("periods"), dict) else {}
            for period, value in values.items():
                source = sources.get(period) if isinstance(sources.get(period), dict) else {}
                period_meta = periods.get(period) if isinstance(periods.get(period), dict) else {}
                records.append(
                    {
                        "metric_key": item.get("canonical_name") or item.get("metric_key"),
                        "canonical_name": item.get("canonical_name"),
                        "metric_name": item.get("name") or item.get("metric_name"),
                        "statement_type": statement_type,
                        "scope": statement.get("scope") or item.get("scope"),
                        "period": period,
                        "fiscal_year": period_meta.get("fiscal_year"),
                        "raw_value": raw_values.get(period, value),
                        "normalized_value": value,
                        "unit": item.get("unit") or statement.get("unit"),
                        "currency": item.get("currency") or statement.get("currency"),
                        "scale": item.get("scale") or statement.get("scale"),
                        "source": normalize_source(source),
                    }
                )
    return records


def _collect_flat_records(obj: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if isinstance(obj, dict):
        if any(key in obj for key in ("metric_name", "metric_key", "canonical_name", "item_name")):
            records.append(_normalize_flat_record(obj))
        for value in obj.values():
            records.extend(_collect_flat_records(value))
    elif isinstance(obj, list):
        for item in obj:
            records.extend(_collect_flat_records(item))
    return records


def _normalize_flat_record(record: dict[str, Any]) -> dict[str, Any]:
    output = dict(record)
    raw_unit = str(record.get("unit_hint") or record.get("raw_unit") or record.get("unit") or "").strip()
    display_unit = normalize_display_unit(raw_unit)
    if display_unit and display_unit != raw_unit:
        output["raw_unit"] = raw_unit
        output["unit_hint"] = display_unit
    return output


def normalize_display_unit(value: Any) -> str:
    """Clean parser table-heading leakage without altering the recorded raw unit."""
    text = str(value or "").strip()
    if not text:
        return ""
    currency_match = re.search(r"\b(RMB|CNY|HKD|USD|EUR|JPY|KRW|GBP)\b", text, re.IGNORECASE)
    scale_match = re.search(r"\b(thousand|million|billion)\b", text, re.IGNORECASE)
    if currency_match and scale_match:
        return f"{currency_match.group(1).upper()} {scale_match.group(1).lower()}"
    return text


def normalize_source(source: dict[str, Any]) -> dict[str, Any]:
    if not source:
        return {}
    return {
        **source,
        "pdf_page_number": source.get("pdf_page_number")
        or source.get("page_number")
        or source.get("rendered_page_number"),
        "quote_text": source.get("quote_text") or source.get("html_snippet"),
        "source_url": source.get("source_url") or source.get("url"),
        "source_anchor": source.get("source_anchor") or source.get("anchor") or source.get("xpath"),
    }


def validation_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"status": "not_available", "summary": {}}
    checks = payload.get("financial_checks") if isinstance(payload.get("financial_checks"), dict) else payload
    status = str(checks.get("overall_status") or checks.get("status") or "not_available")
    summary = checks.get("summary") if isinstance(checks.get("summary"), dict) else {}
    return {
        "status": status,
        "summary": {
            key: summary.get(key)
            for key in ("total", "pass", "fail", "warning", "skipped")
            if summary.get(key) is not None
        },
        "rule_version": checks.get("rule_version"),
        "profile_id": checks.get("profile_id"),
        "market": checks.get("market"),
    }
