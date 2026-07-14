from __future__ import annotations

import re
from typing import Any

from .base import MarketDocumentFullRows
from .common import infer_currency


def _metadata_from_rows(rows: MarketDocumentFullRows) -> dict[str, Any]:
    raw = rows.filing.get("raw") if isinstance(rows.filing.get("raw"), dict) else {}
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    financial = raw.get("financial_data") if isinstance(raw.get("financial_data"), dict) else {}
    filing = raw.get("filing") if isinstance(raw.get("filing"), dict) else {}
    return {**metadata, **financial, **filing}


def _normalise_hk_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    text = text.removesuffix(".HK").removeprefix("HK")
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.zfill(5) if digits else text


def _is_synthetic_fixture_company_id(value: Any, market: str) -> bool:
    return str(value or "").strip().upper().startswith(f"{market}:FIXTURE:")


def _items(rows: MarketDocumentFullRows) -> list[dict[str, Any]]:
    return rows.statement_items + rows.key_metrics


def _normalized_currency(value: Any) -> str | None:
    return infer_currency(value) or (str(value).strip().upper() if value not in (None, "") else None)


def _standardized_currency_unit(unit: Any, currency: str | None) -> str | None:
    text = str(unit or "").lower()
    if "%" in text or "percent" in text:
        return "ratio"
    if "per share" in text or "/股" in text or "eps" in text:
        return f"{currency or ''}/share".strip("/")
    return currency or (str(unit) if unit else None)


def _apply_hk_currency_precedence(rows: MarketDocumentFullRows) -> None:
    statement_currencies: dict[str, str] = {}
    explicit_statement_currencies: set[str] = set()
    for statement in rows.statements:
        explicit = infer_currency(statement.get("unit"), statement.get("title"), statement.get("statement_name"))
        resolved = explicit or _normalized_currency(statement.get("currency"))
        if not resolved:
            continue
        statement["currency"] = resolved
        statement_id = str(statement.get("statement_id") or "")
        if statement_id:
            statement_currencies[statement_id] = resolved
        if explicit:
            explicit_statement_currencies.add(explicit)

    meta = _metadata_from_rows(rows)
    declared_reporting_currency = _normalized_currency(
        rows.filing.get("reporting_currency")
        or meta.get("reporting_currency")
        or meta.get("presentation_currency")
        or meta.get("currency")
    )
    reporting_currency = (
        next(iter(explicit_statement_currencies))
        if len(explicit_statement_currencies) == 1
        else declared_reporting_currency
    )
    if reporting_currency:
        rows.filing["reporting_currency"] = reporting_currency

    item_currency_by_uid: dict[str, str] = {}
    for item in _items(rows):
        statement_currency = statement_currencies.get(str(item.get("statement_id") or ""))
        resolved = (
            infer_currency(item.get("unit"))
            or _normalized_currency(item.get("currency"))
            or statement_currency
            or reporting_currency
        )
        if not resolved:
            continue
        item["currency"] = resolved
        item_uid = str(item.get("item_uid") or "")
        if item_uid:
            item_currency_by_uid[item_uid] = resolved

    for enriched in rows.enriched_items:
        resolved = (
            infer_currency(enriched.get("unit_raw"), enriched.get("unit"))
            or item_currency_by_uid.get(str(enriched.get("item_uid") or ""))
            or _normalized_currency(enriched.get("currency"))
            or reporting_currency
        )
        if not resolved:
            continue
        enriched["currency"] = resolved
        enriched["unit_standardized"] = _standardized_currency_unit(
            enriched.get("unit_raw") or enriched.get("unit"),
            resolved,
        )

    for table in rows.tables:
        explicit = infer_currency(table.get("unit"), table.get("title"))
        if explicit:
            table["currency"] = explicit

    for period in rows.wide_rows:
        for bucket_name in ("balance_sheet", "income_statement", "cash_flow_statement", "key_metrics", "all_metrics"):
            bucket = period.get(bucket_name)
            if not isinstance(bucket, dict):
                continue
            for payload in bucket.values():
                if not isinstance(payload, dict):
                    continue
                resolved = infer_currency(payload.get("unit")) or _normalized_currency(payload.get("currency"))
                if resolved:
                    payload["currency"] = resolved


def _annotate_currency_model(rows: MarketDocumentFullRows, *, default_currency: str | None = None) -> None:
    meta = _metadata_from_rows(rows)
    reporting_currency = (
        rows.filing.get("reporting_currency")
        or meta.get("reporting_currency")
        or meta.get("presentation_currency")
        or meta.get("currency")
        or default_currency
    )
    if not reporting_currency:
        for item in _items(rows):
            reporting_currency = item.get("currency") or infer_currency(item.get("unit"))
            if reporting_currency:
                break
    for item in _items(rows):
        fact_currency = item.get("currency") or infer_currency(item.get("unit"), reporting_currency) or reporting_currency
        item["fact_currency"] = fact_currency
        item["reporting_currency"] = reporting_currency
        item["presentation_currency"] = reporting_currency
        raw = item.setdefault("raw", {})
        if isinstance(raw, dict):
            raw["currency_model"] = {
                "fact_currency": fact_currency,
                "reporting_currency": reporting_currency,
                "presentation_currency": reporting_currency,
            }
    for enriched in rows.enriched_items:
        fact_currency = enriched.get("currency") or infer_currency(enriched.get("unit_raw"), reporting_currency) or reporting_currency
        enriched["fact_currency"] = fact_currency
        enriched["reporting_currency"] = reporting_currency
        enriched["presentation_currency"] = reporting_currency


def apply_hk_rules(rows: MarketDocumentFullRows) -> MarketDocumentFullRows:
    code = _normalise_hk_code(rows.company.get("stock_code") or rows.company.get("ticker"))
    if code:
        company_id = (
            rows.company.get("company_id")
            if _is_synthetic_fixture_company_id(rows.company.get("company_id"), "HK")
            else f"HK:{code}"
        )
        rows.company.update(
            {
                "company_id": company_id,
                "ticker": code,
                "stock_code": code,
                "hkex_stock_code": code,
            }
        )
        rows.filing.update({"company_id": rows.company["company_id"], "ticker": code, "stock_code": code})
    _apply_hk_currency_precedence(rows)
    _annotate_currency_model(rows, default_currency=rows.filing.get("reporting_currency"))
    return rows


def apply_jp_rules(rows: MarketDocumentFullRows) -> MarketDocumentFullRows:
    meta = _metadata_from_rows(rows)
    edinet = rows.company.get("edinet_code") or meta.get("edinet_code")
    security = rows.company.get("security_code") or meta.get("security_code")
    if edinet:
        rows.company["company_id"] = f"JP:{edinet}"
        rows.company["edinet_code"] = edinet
        rows.filing["company_id"] = rows.company["company_id"]
    if security:
        rows.company["security_code"] = security
        rows.filing["security_code"] = security
    _annotate_currency_model(rows, default_currency="JPY")
    return rows


def apply_kr_rules(rows: MarketDocumentFullRows) -> MarketDocumentFullRows:
    meta = _metadata_from_rows(rows)
    corp_code = rows.company.get("corp_code") or meta.get("corp_code")
    stock_code = rows.company.get("stock_code") or meta.get("stock_code")
    if corp_code:
        rows.company["company_id"] = f"KR:{corp_code}"
        rows.company["corp_code"] = corp_code
        rows.filing["company_id"] = rows.company["company_id"]
    if stock_code:
        rows.company["stock_code"] = stock_code
        rows.filing["stock_code"] = stock_code
    _annotate_currency_model(rows, default_currency="KRW")
    return rows


def apply_eu_rules(rows: MarketDocumentFullRows) -> MarketDocumentFullRows:
    meta = _metadata_from_rows(rows)
    country = str(rows.company.get("country") or meta.get("country") or "EU").upper()
    isin = rows.company.get("isin") or meta.get("isin")
    lei = rows.company.get("lei") or meta.get("lei")
    ticker = str(rows.company.get("ticker") or meta.get("ticker") or isin or lei or "").upper()
    identity_anchor = lei or isin or ticker
    rows.company.update({"country": country, "ticker": ticker, "isin": isin, "lei": lei})
    if identity_anchor and not _is_synthetic_fixture_company_id(
        rows.company.get("company_id"), "EU"
    ):
        rows.company["company_id"] = f"EU:{country}:{ticker}:{identity_anchor}"
    if identity_anchor:
        rows.filing["company_id"] = rows.company["company_id"]
    rows.filing.update({"country": country, "ticker": ticker, "isin": isin, "lei": lei})
    _annotate_currency_model(rows)
    currencies = {item.get("fact_currency") for item in _items(rows) if item.get("fact_currency")}
    if len(currencies) > 1:
        rows.parse_run.setdefault("warnings", []).append("eu_multi_currency_document")
        for enriched in rows.enriched_items:
            flags = enriched.setdefault("quality_flags", [])
            if "multi_currency_document" not in flags:
                flags.append("multi_currency_document")
    return rows


_SEC_STATEMENT_HINTS = {
    "balance_sheet": ("assets", "liabilities", "stockholdersequity", "equity"),
    "income_statement": ("revenue", "salesrevenue", "netincomeloss", "operatingincomeloss", "grossprofit"),
    "cash_flow_statement": ("netcashprovidedbyusedinoperatingactivities", "cashandcashequivalents", "paymentsforproperty"),
}


def _sec_statement_type(concept: Any) -> str:
    key = re.sub(r"[^a-z0-9]+", "", str(concept or "").lower())
    for statement_type, hints in _SEC_STATEMENT_HINTS.items():
        if any(hint in key for hint in hints):
            return statement_type
    return "xbrl_facts"


def apply_us_sec_rules(rows: MarketDocumentFullRows) -> MarketDocumentFullRows:
    cik = str(rows.company.get("cik") or "").strip()
    if cik:
        rows.company["company_id"] = f"US:CIK{cik.zfill(10)}"
        rows.filing["company_id"] = rows.company["company_id"]
    _annotate_currency_model(rows, default_currency="USD")
    for item in _items(rows):
        concept = item.get("concept") or (item.get("raw") or {}).get("item", {}).get("concept")
        statement_type = _sec_statement_type(concept)
        if statement_type != "xbrl_facts":
            item["statement_type"] = statement_type
    for enriched in rows.enriched_items:
        concept = enriched.get("concept") or (enriched.get("raw") or {}).get("item", {}).get("concept")
        statement_type = _sec_statement_type(concept)
        if statement_type != "xbrl_facts":
            enriched["statement_type"] = statement_type
    return rows
