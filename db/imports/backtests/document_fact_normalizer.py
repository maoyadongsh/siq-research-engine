"""Pure document_full fact normalization helpers for backtest gates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class NormalizedFact:
    statement_type: str
    period_key: str
    value: Any
    raw_value: Any
    canonical_name: str | None = None
    name: str | None = None
    label: str | None = None
    concept: str | None = None
    unit: str | None = None
    currency: str | None = None
    fact_currency: str | None = None
    reporting_currency: str | None = None
    presentation_currency: str | None = None
    scale: Any = None
    evidence: dict[str, Any] | None = None


def table_lookup(document_full: dict[str, Any]) -> dict[int, dict[str, Any]]:
    enhanced = document_full.get("content_list_enhanced")
    tables = enhanced.get("tables") if isinstance(enhanced, dict) else None
    lookup: dict[int, dict[str, Any]] = {}
    if not isinstance(tables, list):
        return lookup
    for table in tables:
        if not isinstance(table, dict):
            continue
        table_index = table.get("table_index")
        if isinstance(table_index, int):
            lookup[table_index] = table
    return lookup


def enriched_evidence(evidence: Any, tables: dict[int, dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        return {}
    result = dict(evidence)
    table_index = result.get("table_index")
    table = tables.get(table_index) if isinstance(table_index, int) else None
    if isinstance(table, dict):
        if result.get("page_number") in (None, ""):
            result["page_number"] = table.get("page_number") or table.get("pdf_page_number")
        if result.get("bbox") in (None, "", [], {}):
            result["bbox"] = table.get("bbox")
    return result


def document_identity(document_full: dict[str, Any], fallback_market: str | None = None) -> dict[str, Any]:
    filing = document_full.get("filing")
    if isinstance(filing, dict):
        market = filing.get("market") or fallback_market
        filing_id = filing.get("filing_id") or filing.get("report_id")
        company_id = filing.get("company_id")
        if market == "US" and not company_id:
            cik = None
            if isinstance(filing_id, str):
                parts = filing_id.split(":")
                if len(parts) >= 2 and parts[1].isdigit():
                    cik = parts[1]
            if cik:
                company_id = f"US:CIK{cik.zfill(10)}"
        return {
            "market": market,
            "company_id": company_id,
            "filing_id": filing_id,
            "ticker": filing.get("ticker"),
            "period_end": filing.get("period_end"),
            "report_type": filing.get("form"),
            "report_year": filing.get("fiscal_year"),
        }

    financial_data = document_full.get("financial_data")
    if isinstance(financial_data, dict):
        return {
            "market": financial_data.get("market") or fallback_market,
            "company_id": financial_data.get("company_id"),
            "filing_id": financial_data.get("filing_id") or financial_data.get("report_id"),
            "ticker": financial_data.get("ticker"),
            "period_end": financial_data.get("period_end"),
            "report_type": financial_data.get("report_type") or financial_data.get("report_kind"),
            "report_year": financial_data.get("fiscal_year") or financial_data.get("report_year"),
        }
    return {"market": fallback_market}


def normalize_document_facts(document_full: dict[str, Any]) -> list[NormalizedFact]:
    financial_data = document_full.get("financial_data")
    if isinstance(financial_data, dict) and isinstance(financial_data.get("statements"), list):
        return normalize_financial_data_facts(financial_data, table_lookup(document_full))
    if isinstance(document_full.get("facts"), list):
        return normalize_sec_facts(document_full)
    return []


def normalize_financial_data_facts(
    financial_data: dict[str, Any],
    tables: dict[int, dict[str, Any]],
) -> list[NormalizedFact]:
    facts: list[NormalizedFact] = []
    for statement in financial_data.get("statements") or []:
        if not isinstance(statement, dict):
            continue
        statement_type = str(statement.get("statement_type") or "")
        statement_unit = statement.get("unit")
        statement_currency = statement.get("currency")
        statement_scale = statement.get("scale")
        for item in statement.get("items") or []:
            if not isinstance(item, dict):
                continue
            raw_scale = item.get("scale") or statement_scale
            item_unit, item_currency = normalized_fact_unit_currency(
                unit=item.get("unit") or statement_unit,
                currency=item.get("fact_currency") or item.get("currency") or statement_currency,
                scale=raw_scale,
            )
            item_scale = normalized_fact_scale(unit=item_unit, scale=raw_scale)
            reporting_currency = (
                financial_data.get("reporting_currency")
                or financial_data.get("presentation_currency")
                or item_currency
            )
            presentation_currency = (
                financial_data.get("presentation_currency")
                or financial_data.get("reporting_currency")
                or item_currency
            )
            if isinstance(item.get("values"), dict):
                raw_values = item.get("raw_values") if isinstance(item.get("raw_values"), dict) else {}
                sources = item.get("sources") if isinstance(item.get("sources"), dict) else {}
                for period_key, value in item["values"].items():
                    facts.append(
                        NormalizedFact(
                            statement_type=statement_type,
                            period_key=str(period_key),
                            value=value,
                            raw_value=raw_values.get(period_key),
                            canonical_name=item.get("canonical_name"),
                            name=item.get("name") or item.get("local_name"),
                            label=item.get("label"),
                            unit=item_unit,
                            currency=item_currency,
                            fact_currency=item_currency,
                            reporting_currency=reporting_currency,
                            presentation_currency=presentation_currency,
                            scale=item_scale,
                            evidence=enriched_evidence(sources.get(period_key), tables),
                        )
                    )
                continue

            period_key = item.get("period_key") or item.get("period_end")
            if period_key is None:
                continue
            facts.append(
                NormalizedFact(
                    statement_type=str(item.get("statement_type") or statement_type),
                    period_key=str(period_key),
                    value=item.get("value"),
                    raw_value=item.get("raw_value"),
                    canonical_name=item.get("canonical_name"),
                    name=item.get("local_name") or item.get("name"),
                    label=item.get("label"),
                    unit=item_unit,
                    currency=item_currency,
                    fact_currency=item_currency,
                    reporting_currency=reporting_currency,
                    presentation_currency=presentation_currency,
                    scale=item_scale,
                    evidence=enriched_evidence(item.get("evidence"), tables),
                )
            )
    return facts


def normalized_fact_unit_currency(
    *,
    unit: Any,
    currency: Any,
    scale: Any,
) -> tuple[Any, Any]:
    """Recover fact dimensions from explicit unit markers before market defaults.

    Some PDF table headers are flattened into a long ``unit`` string. The
    currency marker and scale remain authoritative even when the surrounding
    header text is noisy.
    """

    raw_unit = str(unit or "").strip()
    raw_currency = str(currency or "").strip() or None
    upper_unit = raw_unit.upper()
    detected_currency = None
    for marker, canonical in (
        ("RMB", "RMB"),
        ("CNY", "CNY"),
        ("HKD", "HKD"),
        ("USD", "USD"),
        ("EUR", "EUR"),
        ("JPY", "JPY"),
        ("KRW", "KRW"),
        ("GBP", "GBP"),
        ("CHF", "CHF"),
    ):
        if marker in upper_unit:
            detected_currency = canonical
            break
    if detected_currency is None and "百万円" in raw_unit:
        detected_currency = "JPY" if raw_currency in {None, "JPY"} else raw_currency
    if detected_currency is None and "백만원" in raw_unit:
        detected_currency = "KRW" if raw_currency in {None, "KRW"} else raw_currency

    resolved_currency = detected_currency or raw_currency
    scale_decimal = as_decimal(scale)
    million_marker = any(
        marker in upper_unit
        for marker in ("MILLION", "MN", "MM")
    ) or any(marker in raw_unit for marker in ("百万円", "백만원"))
    if resolved_currency and scale_decimal == Decimal("1000000") and million_marker:
        return f"{resolved_currency} million", resolved_currency
    return unit, resolved_currency


def normalized_fact_scale(*, unit: Any, scale: Any) -> Any:
    """Return the monetary multiplier encoded by an explicit display unit.

    Legacy EU parser output records values in a declared ``EUR million`` unit
    while leaving the numeric scale at the identity default.  The display unit
    is explicit source metadata, so it is safe to recover the multiplier in the
    read-only normalization layer without changing the extracted value.
    """

    raw_unit = " ".join(str(unit or "").strip().upper().split())
    scale_decimal = as_decimal(scale)
    if scale_decimal not in (None, Decimal("1")):
        return scale
    if "MILLION" in raw_unit:
        return 1000000
    return scale


def normalize_sec_facts(document_full: dict[str, Any]) -> list[NormalizedFact]:
    source = document_full.get("source") if isinstance(document_full.get("source"), dict) else {}
    facts: list[NormalizedFact] = []
    for fact in document_full.get("facts") or []:
        if not isinstance(fact, dict):
            continue
        period_key = fact.get("period_end") or fact.get("context_ref") or ""
        evidence = {
            "html_anchor": fact.get("html_anchor"),
            "table_index": fact.get("table_index"),
            "source_url": source.get("source_url"),
        }
        facts.append(
            NormalizedFact(
                statement_type="xbrl_fact",
                period_key=str(period_key),
                value=fact.get("value_numeric") if fact.get("value_numeric") is not None else fact.get("value_text"),
                raw_value=fact.get("value_text"),
                concept=fact.get("concept"),
                label=fact.get("label"),
                unit=fact.get("unit"),
                fact_currency=fact.get("currency"),
                reporting_currency=fact.get("reporting_currency"),
                presentation_currency=fact.get("presentation_currency"),
                evidence=evidence,
            )
        )
    return facts


def as_decimal(value: Any) -> Decimal | None:
    text = str(value).strip().replace(",", "").replace("，", "")
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def decimal_equal(left: Any, right: Any) -> bool:
    left_decimal = as_decimal(left)
    right_decimal = as_decimal(right)
    if left_decimal is not None and right_decimal is not None:
        return left_decimal == right_decimal
    return str(left) == str(right)


def value_within_tolerance(observed: Any, expected: Any, tolerance_ratio: Any) -> bool:
    observed_decimal = as_decimal(observed)
    expected_decimal = as_decimal(expected)
    tolerance_decimal = as_decimal(tolerance_ratio)
    if observed_decimal is None or expected_decimal is None or tolerance_decimal is None:
        return decimal_equal(observed, expected)
    return abs(observed_decimal - expected_decimal) <= abs(expected_decimal) * tolerance_decimal


def fact_matches(fact: NormalizedFact, expected: dict[str, Any]) -> bool:
    for field in ("statement_type", "period_key", "canonical_name", "name", "label", "concept"):
        if field in expected and getattr(fact, field) != expected[field]:
            return False
    return True


def find_fact(facts: list[NormalizedFact], expected: dict[str, Any]) -> NormalizedFact | None:
    for fact in facts:
        if fact_matches(fact, expected):
            return fact
    return None


def assertion_to_expected_fact(assertion: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    expected = dict(assertion)
    if "expected_value" in expected:
        expected["value"] = expected.pop("expected_value")
    expected.setdefault("period_key", case.get("period_key"))
    return expected


def has_evidence_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, (dict, list, tuple, set)) and not value:
        return False
    return True


def has_reviewable_evidence(evidence: dict[str, Any]) -> bool:
    return any(
        has_evidence_value(evidence.get(field))
        for field in (
            "table_index",
            "page_number",
            "bbox",
            "quote_text",
            "html_anchor",
            "xpath",
            "source_url",
            "local_path",
        )
    )


def stable_json_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def stable_row_list(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True, default=str))


def stable_rows_hash(rows: list[dict[str, Any]]) -> str:
    return stable_json_hash(stable_row_list(rows))


def fact_content_hash(facts: list[NormalizedFact]) -> str:
    rows = [
        {
            "statement_type": fact.statement_type,
            "period_key": fact.period_key,
            "canonical_name": fact.canonical_name,
            "name": fact.name,
            "label": fact.label,
            "concept": fact.concept,
            "value": str(fact.value),
            "raw_value": str(fact.raw_value),
            "unit": fact.unit,
            "currency": fact.currency,
            "fact_currency": fact.fact_currency,
            "reporting_currency": fact.reporting_currency,
            "presentation_currency": fact.presentation_currency,
            "scale": str(fact.scale),
            "evidence": fact.evidence or {},
        }
        for fact in facts
    ]
    return stable_json_hash(sorted(rows, key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)))


__all__ = [
    "NormalizedFact",
    "assertion_to_expected_fact",
    "as_decimal",
    "decimal_equal",
    "document_identity",
    "enriched_evidence",
    "fact_content_hash",
    "fact_matches",
    "find_fact",
    "has_evidence_value",
    "has_reviewable_evidence",
    "normalize_document_facts",
    "normalize_financial_data_facts",
    "normalized_fact_scale",
    "normalized_fact_unit_currency",
    "normalize_sec_facts",
    "stable_json_hash",
    "stable_row_list",
    "stable_rows_hash",
    "table_lookup",
    "value_within_tolerance",
]
