from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from .base import MarketDocumentFullContext, MarketDocumentFullRows
from .canonical_maps import resolve_canonical
from .common import (
    as_date_text,
    as_decimal,
    as_int,
    compact_text,
    infer_currency,
    normalize_scale,
    stable_id,
)


MARKET_SCHEMA = {
    "HK": "pdf2md_hk",
    "JP": "edinet_jp",
    "KR": "dart_kr",
    "EU": "eu_ifrs",
    "US": "sec_us",
}


def _task(document_full: dict[str, Any]) -> dict[str, Any]:
    return document_full.get("task") if isinstance(document_full.get("task"), dict) else {}


def _metadata(document_full: dict[str, Any]) -> dict[str, Any]:
    return document_full.get("metadata") if isinstance(document_full.get("metadata"), dict) else {}


def _financial_data(document_full: dict[str, Any]) -> dict[str, Any]:
    return document_full.get("financial_data") if isinstance(document_full.get("financial_data"), dict) else {}


def _filing(document_full: dict[str, Any]) -> dict[str, Any]:
    return document_full.get("filing") if isinstance(document_full.get("filing"), dict) else {}


def _source(document_full: dict[str, Any]) -> dict[str, Any]:
    return document_full.get("source") if isinstance(document_full.get("source"), dict) else {}


def _document_format(document_full: dict[str, Any]) -> str | None:
    meta = _metadata(document_full)
    filing = _filing(document_full)
    source = _source(document_full)
    task = _task(document_full)
    submit_config = task.get("submit_config") if isinstance(task.get("submit_config"), dict) else {}
    for value in (
        meta.get("document_format"),
        meta.get("source_format"),
        filing.get("document_format"),
        source.get("document_format"),
        source.get("source_format"),
        submit_config.get("document_format"),
        submit_config.get("source_format"),
    ):
        if str(value or "").strip():
            return str(value).strip().lower()
    return None


def _table_source_format(table: dict[str, Any], document_full: dict[str, Any], market: str) -> str | None:
    raw_format = str(table.get("source_format") or table.get("document_format") or table.get("format") or "").strip().lower()
    if raw_format:
        return raw_format
    if table.get("html_anchor") or table.get("xpath"):
        return "html"
    doc_format = _document_format(document_full)
    if doc_format:
        if any(token in doc_format for token in ("html", "xhtml", "ixbrl")):
            return doc_format
        if "pdf" in doc_format:
            return "pdf"
    return "html" if market == "US" else "pdf"


def _stock_code(value: Any, *, width: int | None = None) -> str:
    text = str(value or "").strip()
    if width and text.isdigit():
        return text.zfill(width)
    return text


def _company_identity(market: str, document_full: dict[str, Any]) -> dict[str, Any]:
    meta = _metadata(document_full)
    financial = _financial_data(document_full)
    filing = _filing(document_full)
    source = _source(document_full)
    task = _task(document_full)
    filename = str(task.get("filename") or source.get("filename") or "")
    stem = Path(filename).stem

    if market == "US":
        cik = str(filing.get("cik") or source.get("cik") or meta.get("cik") or "").strip()
        ticker = str(filing.get("ticker") or meta.get("ticker") or source.get("ticker") or "").strip().upper()
        company_id = filing.get("company_id") or (f"US:CIK{cik.zfill(10)}" if cik else f"US:{ticker or stable_id(stem)}")
        return {
            "company_id": company_id,
            "ticker": ticker or company_id.rsplit(":", 1)[-1],
            "company_name": filing.get("company_name") or meta.get("company_name") or source.get("company_name") or ticker or stem,
            "cik": cik,
            "country": "US",
            "exchange": meta.get("exchange"),
            "raw": {"metadata": meta, "filing": filing, "source": source},
        }

    ticker = (
        financial.get("ticker")
        or meta.get("ticker")
        or meta.get("stock_code")
        or financial.get("stock_code")
        or filing.get("ticker")
        or ""
    )
    company_name = (
        financial.get("company_name")
        or meta.get("company_name")
        or filing.get("company_name")
        or stem
    )
    if market == "HK":
        stock_code = _stock_code(ticker, width=5)
        return {
            "company_id": financial.get("company_id") or meta.get("company_id") or f"HK:{stock_code or stable_id(company_name)}",
            "ticker": stock_code,
            "stock_code": stock_code,
            "hkex_stock_code": stock_code,
            "exchange": meta.get("exchange") or financial.get("exchange") or "HKEX",
            "company_name": company_name,
            "short_name": meta.get("short_name") or financial.get("short_name") or company_name,
            "company_short_name": meta.get("company_short_name") or meta.get("short_name") or company_name,
            "company_name_en": meta.get("company_name_en") or company_name,
            "company_name_zh": meta.get("company_name_zh"),
            "aliases": [item for item in {stock_code, str(company_name)} if item],
            "industry_profile": meta.get("industry_profile") or financial.get("industry_profile") or "general",
            "raw": {"metadata": meta, "financial_data": financial},
        }
    if market == "EU":
        country = str(financial.get("country") or meta.get("country") or filing.get("country") or "EU").upper()
        isin = financial.get("isin") or meta.get("isin") or filing.get("isin")
        lei = financial.get("lei") or meta.get("lei") or filing.get("lei")
        ticker_text = str(ticker or isin or lei or stable_id(company_name)).upper()
        return {
            "company_id": financial.get("company_id") or meta.get("company_id") or f"EU:{country}:{ticker_text}:{isin or lei or stable_id(company_name)}",
            "country": country,
            "ticker": ticker_text,
            "isin": isin,
            "lei": lei,
            "exchange": financial.get("exchange") or meta.get("exchange") or filing.get("exchange"),
            "company_name": company_name,
            "industry_profile": meta.get("industry_profile") or "general",
            "raw": {"metadata": meta, "financial_data": financial},
        }
    if market == "JP":
        edinet = financial.get("edinet_code") or meta.get("edinet_code")
        security = financial.get("security_code") or meta.get("security_code")
        ticker_text = str(ticker or security or edinet or stable_id(company_name)).upper()
        return {
            "company_id": financial.get("company_id") or meta.get("company_id") or f"JP:{edinet or ticker_text}",
            "edinet_code": edinet,
            "security_code": security,
            "ticker": ticker_text,
            "company_name": company_name,
            "raw": {"metadata": meta, "financial_data": financial},
        }
    if market == "KR":
        corp = financial.get("corp_code") or meta.get("corp_code")
        stock = financial.get("stock_code") or meta.get("stock_code") or ticker
        ticker_text = str(stock or corp or stable_id(company_name)).upper()
        return {
            "company_id": financial.get("company_id") or meta.get("company_id") or f"KR:{corp or ticker_text}",
            "corp_code": corp,
            "stock_code": stock,
            "ticker": ticker_text,
            "company_name": company_name,
            "raw": {"metadata": meta, "financial_data": financial},
        }
    raise ValueError(f"unsupported market: {market}")


def _filing_identity(market: str, document_full: dict[str, Any], company: dict[str, Any], context: MarketDocumentFullContext) -> dict[str, Any]:
    meta = _metadata(document_full)
    financial = _financial_data(document_full)
    filing = _filing(document_full)
    source = _source(document_full)
    task = _task(document_full)
    period_end = as_date_text(
        filing.get("period_end") or financial.get("period_end") or meta.get("period_end") or task.get("period_end")
    )
    fiscal_year = as_int(filing.get("fiscal_year") or financial.get("report_year") or financial.get("fiscal_year") or meta.get("fiscal_year"))
    report_type = str(filing.get("report_type") or financial.get("report_kind") or meta.get("report_type") or filing.get("form") or "annual")
    source_id = (
        filing.get("accession_number")
        or filing.get("doc_id")
        or filing.get("rcp_no")
        or filing.get("report_id")
        or source.get("source_id")
        or source.get("accession_number")
        or meta.get("source_id")
        or context.document_full_sha256[:12]
    )
    filing_id = (
        filing.get("filing_id")
        or financial.get("filing_id")
        or filing.get("report_id")
        or financial.get("report_id")
        or meta.get("report_id")
        or f"{market}:{company['company_id'].split(':', 1)[-1]}:{report_type}:{period_end or fiscal_year or 'unknown'}:{source_id}"
    )
    base = {
        "filing_id": filing_id,
        "company_id": company["company_id"],
        "ticker": company.get("ticker"),
        "form": filing.get("form"),
        "report_type": report_type,
        "fiscal_year": fiscal_year,
        "fiscal_period": filing.get("fiscal_period") or financial.get("fiscal_period") or meta.get("fiscal_period") or "FY",
        "period_end": period_end,
        "published_at": as_date_text(filing.get("published_at") or meta.get("published_at")),
        "source_id": source_id,
        "source_url": filing.get("source_url") or source.get("source_url") or meta.get("source_url"),
        "local_path": str(context.document_full_path),
        "accounting_standard": financial.get("accounting_standard") or meta.get("accounting_standard") or filing.get("accounting_standard"),
        "quality_status": (_quality(document_full).get("overall_status") or "warning"),
        "document_full_path": str(context.document_full_path),
        "raw": {"metadata": meta, "financial_data": financial, "filing": filing},
    }
    if market == "HK":
        base.update({"stock_code": company.get("stock_code"), "report_id": filing.get("report_id") or stable_id(filing_id, prefix="report"), "accession_number": filing.get("accession_number")})
    if market == "EU":
        base.update({"country": company.get("country"), "source_tier": meta.get("source_tier"), "landing_url": meta.get("landing_url"), "document_format": meta.get("document_format")})
    if market == "JP":
        base.update({"doc_id": filing.get("doc_id") or meta.get("doc_id")})
    if market == "KR":
        base.update({"rcp_no": filing.get("rcp_no") or meta.get("rcp_no")})
    if market == "US":
        base.update({
            "accession_number": filing.get("accession_number") or source_id,
            "form": filing.get("form") or report_type or "10-K",
            "filing_date": as_date_text(filing.get("filing_date") or meta.get("filing_date")),
            "accepted_at": filing.get("accepted_at") or meta.get("accepted_at"),
        })
    return base


def _quality(document_full: dict[str, Any]) -> dict[str, Any]:
    return document_full.get("quality_report") if isinstance(document_full.get("quality_report"), dict) else {}


def _parse_run(market: str, document_full: dict[str, Any], filing: dict[str, Any], context: MarketDocumentFullContext) -> dict[str, Any]:
    task = _task(document_full)
    quality = _quality(document_full)
    warnings = quality.get("warnings") or quality.get("critical_warnings") or []
    parser_version = document_full.get("parser_version") or task.get("parser_version") or "document_full"
    rules_version = "document_full_v1"
    parse_run_id = stable_id(market, filing["filing_id"], context.document_full_sha256, parser_version, rules_version, prefix="parse")
    return {
        "parse_run_id": parse_run_id,
        "filing_id": filing["filing_id"],
        "parser_version": parser_version,
        "rules_version": rules_version,
        "wiki_package_path": filing.get("document_full_path") or str(context.document_full_path),
        "status": quality.get("overall_status") or filing.get("quality_status") or "warning",
        "warnings": warnings,
        "artifact_hashes": {"document_full.json": context.document_full_sha256},
        "raw": {"task": task, "quality": quality, "document_full_path": str(context.document_full_path)},
    }


def _iter_statements(financial: dict[str, Any]) -> list[dict[str, Any]]:
    statements = [statement for statement in financial.get("statements") or [] if isinstance(statement, dict)]
    key_metrics = [item for item in financial.get("key_metrics") or [] if isinstance(item, dict)]
    if key_metrics:
        statements.append({"statement_id": "key_metrics", "statement_type": "key_metrics", "statement_name": "Key metrics", "items": key_metrics})
    return statements


def _top_level_context_lookup(document_full: dict[str, Any]) -> dict[str, dict[str, Any]]:
    contexts = document_full.get("contexts")
    if isinstance(contexts, dict):
        return {str(ref): row for ref, row in contexts.items() if isinstance(row, dict)}
    if isinstance(contexts, list):
        lookup: dict[str, dict[str, Any]] = {}
        for row in contexts:
            if not isinstance(row, dict):
                continue
            ref = row.get("context_ref") or row.get("id")
            if ref:
                lookup[str(ref)] = row
        return lookup
    return {}


def _top_level_unit_lookup(document_full: dict[str, Any]) -> dict[str, dict[str, Any]]:
    units = document_full.get("units")
    if isinstance(units, dict):
        return {
            str(ref): (row if isinstance(row, dict) else {"unit": row})
            for ref, row in units.items()
        }
    if isinstance(units, list):
        lookup: dict[str, dict[str, Any]] = {}
        for row in units:
            if not isinstance(row, dict):
                continue
            ref = row.get("unit_ref") or row.get("id")
            if ref:
                lookup[str(ref)] = row
        return lookup
    return {}


_XBRL_STATEMENT_HINTS = {
    "balance_sheet": (
        "assets",
        "liabilities",
        "equity",
        "inventories",
        "receivables",
        "payables",
        "propertyplantandequipment",
        "cashandcashequivalents",
    ),
    "income_statement": (
        "revenue",
        "profitloss",
        "operatingprofit",
        "profitlossfromoperatingactivities",
        "grossprofit",
        "financeincome",
        "financecosts",
        "incometaxexpense",
        "earningspershare",
        "netincomeloss",
        "operatingincomeloss",
        "salesrevenue",
    ),
    "cash_flow_statement": (
        "cashflowsfromusedinoperatingactivities",
        "cashflowsfromusedininvestingactivities",
        "cashflowsfromusedinfinancingactivities",
        "netcashflowsfromusedinoperatingactivities",
        "netcashprovidedbyusedinoperatingactivities",
        "paymentsforproperty",
        "capitalexpenditure",
    ),
}


def _infer_xbrl_statement_type(market: str, *names: Any) -> str:
    key = re.sub(r"[^a-z0-9]+", "", " ".join(str(name or "") for name in names).lower())
    for statement_type, hints in _XBRL_STATEMENT_HINTS.items():
        if any(hint in key for hint in hints):
            return statement_type
    return "xbrl_facts" if market in {"US", "EU", "JP", "KR"} else "unknown"


def _xbrl_fact_statements(document_full: dict[str, Any], *, market: str, statement_name: str, parse_run_id: str) -> list[dict[str, Any]]:
    facts = [fact for fact in document_full.get("facts") or [] if isinstance(fact, dict)]
    if not facts:
        return []
    contexts = _top_level_context_lookup(document_full)
    units = _top_level_unit_lookup(document_full)
    items: list[dict[str, Any]] = []
    for idx, fact in enumerate(facts, start=1):
        source_fact_id = str(fact.get("fact_id") or fact.get("id") or stable_id("fact", idx, prefix="source_fact"))
        fact_id = stable_id(parse_run_id, source_fact_id, prefix="fact")
        context_ref = fact.get("context_ref")
        context_row = contexts.get(str(context_ref)) if context_ref is not None else {}
        context_row = context_row or {}
        unit_ref = fact.get("unit_ref")
        unit_row = units.get(str(unit_ref)) if unit_ref is not None else {}
        unit_row = unit_row or {}
        period_start = fact.get("period_start") or context_row.get("period_start")
        period_end = fact.get("period_end") or context_row.get("period_end") or context_row.get("instant")
        instant = fact.get("instant") or context_row.get("instant")
        dimensions = fact.get("dimensions") if isinstance(fact.get("dimensions"), dict) else context_row.get("dimensions") if isinstance(context_row.get("dimensions"), dict) else {}
        unit = fact.get("unit") or unit_row.get("unit") or unit_row.get("measure")
        value = (
            fact.get("value_numeric")
            if fact.get("value_numeric") is not None
            else fact.get("numeric_value")
            if fact.get("numeric_value") is not None
            else fact.get("value")
        )
        raw_value = (
            fact.get("value_text")
            if fact.get("value_text") is not None
            else fact.get("raw_value")
            if fact.get("raw_value") is not None
            else value
        )
        source = {
            "source_type": "xbrl_fact",
            "evidence_id": fact.get("evidence_id"),
            "table_index": fact.get("table_index"),
            "html_anchor": fact.get("html_anchor"),
            "xpath": fact.get("xpath"),
            "quote_text": raw_value,
        }
        statement_type = fact.get("statement_type") or _infer_xbrl_statement_type(
            market,
            fact.get("concept"),
            fact.get("label"),
            fact.get("canonical_name"),
        )
        items.append(
            {
                "item_name": fact.get("label") or fact.get("concept"),
                "statement_type": statement_type,
                "concept": fact.get("concept"),
                "canonical_name": fact.get("canonical_name"),
                "period_key": fact.get("period_key") or period_end or context_ref or "unknown",
                "value": value,
                "raw_value": raw_value,
                "unit": unit,
                "currency": fact.get("currency") or infer_currency(unit),
                "period_start": period_start,
                "period_end": period_end,
                "instant": instant,
                "duration_days": fact.get("duration_days") or context_row.get("duration_days"),
                "dimensions": dimensions,
                "context_ref": context_ref,
                "unit_ref": unit_ref,
                "fact_id": fact_id,
                "raw_fact_id": source_fact_id,
                "source": source,
                "evidence": source,
            }
        )
    return [
        {
            "statement_id": "sec_facts",
            "statement_type": "xbrl_facts",
            "statement_name": statement_name,
            "items": items,
            "raw": {"facts": facts},
        }
    ]


def _period_values(item: dict[str, Any], filing: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    values = item.get("values") if isinstance(item.get("values"), dict) else {}
    if values:
        raw_values = item.get("raw_values") if isinstance(item.get("raw_values"), dict) else {}
        sources = item.get("sources") if isinstance(item.get("sources"), dict) else {}
        rows: list[tuple[str, dict[str, Any]]] = []
        for period_key, payload_value in values.items():
            payload = payload_value if isinstance(payload_value, dict) else {"value": payload_value}
            source = sources.get(period_key) or sources.get(str(period_key)) or {}
            rows.append((str(period_key), {**payload, "raw_value": raw_values.get(period_key, payload.get("raw_value", payload_value)), "source": source}))
        return rows
    period_key = str(item.get("period_key") or item.get("period") or item.get("period_end") or filing.get("period_end") or "unknown")
    return [(period_key, item)]


def _source_from_payload(payload: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    item_source = item.get("source") if isinstance(item.get("source"), dict) else {}
    item_evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    return {**item_source, **item_evidence, **source, **evidence}


def _list_payload(value: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in keys:
            nested = value.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
        return [value]
    return []


def _page_number(payload: dict[str, Any]) -> int | None:
    return as_int(payload.get("page_number") or payload.get("pdf_page_number") or payload.get("page"))


def _table_index(payload: dict[str, Any]) -> int | None:
    return as_int(payload.get("table_index") or payload.get("source_table_index"))


def _quality_report_row(document_full: dict[str, Any], parse_run_id: str, filing_id: str) -> list[dict[str, Any]]:
    quality = _quality(document_full)
    if not quality:
        return []
    return [
        {
            "parse_run_id": parse_run_id,
            "filing_id": filing_id,
            "overall_status": quality.get("overall_status") or quality.get("status") or "warning",
            "parser_status": quality.get("parser_status"),
            "rule_status": quality.get("rule_status"),
            "section_count": as_int(quality.get("section_count")),
            "table_count": as_int(quality.get("table_count")),
            "statement_table_count": as_int(quality.get("statement_table_count")),
            "raw_cell_count": as_int(quality.get("raw_cell_count")),
            "raw_fact_count": as_int(quality.get("raw_fact_count")),
            "normalized_metric_count": as_int(quality.get("normalized_metric_count")),
            "evidence_coverage_ratio": as_decimal(quality.get("evidence_coverage_ratio")),
            "required_statement_status": quality.get("required_statement_status") if isinstance(quality.get("required_statement_status"), dict) else {},
            "critical_warnings": quality.get("critical_warnings") if isinstance(quality.get("critical_warnings"), list) else [],
            "parser_warnings": quality.get("parser_warnings") if isinstance(quality.get("parser_warnings"), list) else [],
            "rule_warnings": quality.get("rule_warnings") if isinstance(quality.get("rule_warnings"), list) else [],
            "raw": quality,
        }
    ]


def _enhanced_structure_rows(enhanced: dict[str, Any], parse_run_id: str) -> dict[str, list[dict[str, Any]]]:
    footnotes: list[dict[str, Any]] = []
    for idx, footnote in enumerate(_list_payload(enhanced.get("footnotes"), "references", "footnotes", "items"), start=1):
        footnotes.append(
            {
                "footnote_id": str(footnote.get("footnote_id") or footnote.get("id") or stable_id(parse_run_id, "footnote", idx, prefix="footnote")),
                "page_number": _page_number(footnote),
                "table_index": _table_index(footnote),
                "target": footnote.get("target") or footnote.get("ref"),
                "footnote_key": footnote.get("footnote_key") or footnote.get("key") or footnote.get("id"),
                "content": footnote.get("content") or footnote.get("text") or footnote.get("note"),
                "raw": footnote,
            }
        )

    toc_entries: list[dict[str, Any]] = []
    for idx, entry in enumerate(_list_payload(enhanced.get("toc"), "headings", "entries", "items"), start=1):
        toc_entries.append(
            {
                "toc_entry_id": str(entry.get("toc_entry_id") or entry.get("id") or stable_id(parse_run_id, "toc", idx, prefix="toc")),
                "page_number": _page_number(entry),
                "table_index": _table_index(entry),
                "target": entry.get("target") or entry.get("anchor"),
                "title": entry.get("title") or entry.get("heading") or entry.get("text"),
                "level": as_int(entry.get("level")),
                "destination_page_number": as_int(entry.get("destination_page_number") or entry.get("dest_page") or entry.get("page_number")),
                "raw": entry,
            }
        )

    note_links: list[dict[str, Any]] = []
    for idx, link in enumerate(_list_payload(enhanced.get("financial_note_links"), "links", "items"), start=1):
        note_links.append(
            {
                "link_id": str(link.get("link_id") or link.get("id") or stable_id(parse_run_id, "note-link", idx, prefix="note_link")),
                "page_number": _page_number(link),
                "table_index": _table_index(link),
                "target": link.get("target") or link.get("statement_item") or link.get("canonical_name"),
                "note_key": link.get("note_key") or link.get("note") or link.get("footnote_key"),
                "note_target": link.get("note_target") or link.get("target_note") or link.get("note_href"),
                "raw": link,
            }
        )

    table_relations: list[dict[str, Any]] = []
    for table_idx, table in enumerate(enhanced.get("tables") if isinstance(enhanced.get("tables"), list) else [], start=1):
        if not isinstance(table, dict):
            continue
        parent_table_index = _table_index(table) or table_idx
        for rel_idx, relation in enumerate(_list_payload(table.get("relations"), "relations", "items"), start=1):
            table_relations.append(
                {
                    "relation_id": str(relation.get("relation_id") or relation.get("id") or stable_id(parse_run_id, "table-relation", parent_table_index, rel_idx, prefix="rel")),
                    "page_number": _page_number(relation) or _page_number(table),
                    "table_index": _table_index(relation) or parent_table_index,
                    "target": relation.get("target"),
                    "related_table_id": relation.get("related_table_id") or relation.get("target_table_id"),
                    "relation_type": relation.get("relation_type") or relation.get("type"),
                    "raw": relation,
                }
            )

    quality_signals: list[dict[str, Any]] = []
    signals = enhanced.get("quality_signals") if isinstance(enhanced.get("quality_signals"), dict) else {}
    for idx, signal in enumerate(_list_payload(signals.get("tables"), "signals", "items"), start=1):
        quality_signals.append(
            {
                "signal_id": str(signal.get("signal_id") or signal.get("id") or stable_id(parse_run_id, "table-signal", idx, prefix="signal")),
                "page_number": _page_number(signal),
                "table_index": _table_index(signal),
                "target": signal.get("target"),
                "signal_type": signal.get("signal_type") or signal.get("type") or "table_quality",
                "signal_value": str(signal.get("signal_value") if signal.get("signal_value") is not None else signal.get("score") if signal.get("score") is not None else ""),
                "raw": signal,
            }
        )

    return {
        "footnotes": footnotes,
        "toc_entries": toc_entries,
        "financial_note_links": note_links,
        "table_relations": table_relations,
        "table_quality_signals": quality_signals,
    }


def build_generic_rows(market: str, document_full: dict[str, Any], context: MarketDocumentFullContext) -> MarketDocumentFullRows:
    company = _company_identity(market, document_full)
    filing = _filing_identity(market, document_full, company, context)
    parse_run = _parse_run(market, document_full, filing, context)
    financial = _financial_data(document_full)
    quality = _quality(document_full)
    parse_run_id = parse_run["parse_run_id"]
    filing_id = filing["filing_id"]
    document_format = _document_format(document_full)

    artifacts = [
        {
            "artifact_type": "document_full.json",
            "local_path": str(context.document_full_path),
            "sha256": context.document_full_sha256,
            "size_bytes": context.document_full_path.stat().st_size if context.document_full_path.exists() else None,
            "raw": {"path": str(context.document_full_path)},
        }
    ]
    for name, ref in (document_full.get("artifacts") or {}).items() if isinstance(document_full.get("artifacts"), dict) else []:
        if isinstance(ref, dict):
            artifacts.append({"artifact_type": str(name), "local_path": ref.get("path") or ref.get("local_path") or "", "sha256": ref.get("sha256"), "size_bytes": ref.get("size_bytes"), "raw": ref})

    sections: list[dict[str, Any]] = []
    raw_sections = document_full.get("sections") if isinstance(document_full.get("sections"), list) else []
    for idx, section in enumerate(raw_sections, start=1):
        if not isinstance(section, dict):
            continue
        sections.append(
            {
                "section_id": str(section.get("section_id") or section.get("id") or stable_id(parse_run_id, "section", idx, prefix="section")),
                "section_title": section.get("section_title") or section.get("title") or section.get("heading"),
                "section_order": as_int(section.get("section_order") or section.get("source_order")) or idx,
                "markdown_path": section.get("markdown_path"),
                "html_anchor": section.get("html_anchor"),
                "xpath": section.get("xpath"),
                "text_hash": section.get("text_hash"),
                "line_start": as_int(section.get("line_start")),
                "line_end": as_int(section.get("line_end")),
                "char_start": as_int(section.get("char_start")),
                "char_end": as_int(section.get("char_end")),
                "raw": section,
            }
        )

    pages: list[dict[str, Any]] = []
    markdown = document_full.get("markdown") if isinstance(document_full.get("markdown"), dict) else {}
    for page in markdown.get("pages") or []:
        if isinstance(page, dict):
            pages.append({"page_number": as_int(page.get("page_number") or page.get("page")) or len(pages) + 1, "markdown_path": page.get("markdown_path"), "image_path": page.get("image_path"), "raw": page})

    blocks: list[dict[str, Any]] = []
    for idx, block in enumerate(document_full.get("content_list") or [], start=1):
        if isinstance(block, dict):
            page_number = as_int(block.get("page_number")) or (as_int(block.get("page_idx")) + 1 if as_int(block.get("page_idx")) is not None else None)
            blocks.append({"block_id": stable_id(parse_run_id, idx, prefix="block"), "block_index": idx, "block_type": block.get("type"), "page_number": page_number, "bbox": block.get("bbox"), "text": compact_text(block), "raw": block})
            if page_number and not any(page["page_number"] == page_number for page in pages):
                pages.append({"page_number": page_number, "raw": {"source": "content_list"}})

    enhanced = document_full.get("content_list_enhanced") if isinstance(document_full.get("content_list_enhanced"), dict) else {}
    enhanced_rows = _enhanced_structure_rows(enhanced, parse_run_id)
    raw_tables = enhanced.get("tables") if isinstance(enhanced.get("tables"), list) else document_full.get("tables") or []
    tables: list[dict[str, Any]] = []
    for idx, table in enumerate(raw_tables, start=1):
        if not isinstance(table, dict):
            continue
        table_index = as_int(table.get("table_index")) or idx
        page_number = as_int(table.get("pdf_page_number") or table.get("page_number") or table.get("page"))
        tables.append(
            {
                "table_id": str(table.get("table_id") or stable_id(parse_run_id, table_index, prefix="table")),
                "table_index": table_index,
                "page_number": page_number,
                "title": table.get("title") or table.get("heading") or table.get("caption"),
                "row_count": as_int(table.get("rows") or table.get("row_count")),
                "column_count": as_int(table.get("columns") or table.get("column_count")),
                "bbox": table.get("bbox"),
                "html_anchor": table.get("html_anchor"),
                "xpath": table.get("xpath"),
                "section_id": table.get("section_id"),
                "source_format": _table_source_format(table, document_full, market),
                "document_format": document_format,
                "table_json_path": table.get("table_json_path") or table.get("json_path"),
                "is_financial_statement_candidate": table.get("is_financial_statement_candidate"),
                "unit": table.get("unit"),
                "currency": table.get("currency") or infer_currency(table.get("unit"), table.get("title")),
                "raw": table,
            }
        )
        if page_number and not any(page["page_number"] == page_number for page in pages):
            pages.append({"page_number": page_number, "raw": {"source": "table"}})

    xbrl_contexts: list[dict[str, Any]] = []
    for context_ref, context_row in (document_full.get("contexts") or {}).items() if isinstance(document_full.get("contexts"), dict) else []:
        if isinstance(context_row, dict):
            xbrl_contexts.append(
                {
                    "context_ref": str(context_ref),
                    "period_start": as_date_text(context_row.get("period_start")),
                    "period_end": as_date_text(context_row.get("period_end")),
                    "instant": as_date_text(context_row.get("instant")),
                    "duration_days": as_int(context_row.get("duration_days")),
                    "dimensions": context_row.get("dimensions") if isinstance(context_row.get("dimensions"), dict) else {},
                    "raw": context_row,
                }
            )
    for context_row in document_full.get("contexts") or [] if isinstance(document_full.get("contexts"), list) else []:
        if isinstance(context_row, dict):
            ref = context_row.get("context_ref") or context_row.get("id")
            if ref:
                xbrl_contexts.append(
                    {
                        "context_ref": str(ref),
                        "period_start": as_date_text(context_row.get("period_start")),
                        "period_end": as_date_text(context_row.get("period_end")),
                        "instant": as_date_text(context_row.get("instant")),
                        "duration_days": as_int(context_row.get("duration_days")),
                        "dimensions": context_row.get("dimensions") if isinstance(context_row.get("dimensions"), dict) else {},
                        "raw": context_row,
                    }
                )

    xbrl_units: list[dict[str, Any]] = []
    for unit_ref, unit in (document_full.get("units") or {}).items() if isinstance(document_full.get("units"), dict) else []:
        raw_unit = unit if isinstance(unit, dict) else {"unit": unit}
        xbrl_units.append({"unit_ref": str(unit_ref), "unit": raw_unit.get("unit") or raw_unit.get("measure") or unit, "raw": raw_unit})
    for unit in document_full.get("units") or [] if isinstance(document_full.get("units"), list) else []:
        if isinstance(unit, dict):
            ref = unit.get("unit_ref") or unit.get("id")
            if ref:
                xbrl_units.append({"unit_ref": str(ref), "unit": unit.get("unit") or unit.get("measure"), "raw": unit})

    xbrl_facts_raw: list[dict[str, Any]] = []
    context_lookup = _top_level_context_lookup(document_full)
    unit_lookup = _top_level_unit_lookup(document_full)
    for idx, fact in enumerate(document_full.get("facts") or [], start=1):
        if not isinstance(fact, dict):
            continue
        source_fact_id = str(fact.get("fact_id") or fact.get("id") or stable_id("fact", idx, prefix="source_fact"))
        fact_id = stable_id(parse_run_id, source_fact_id, prefix="fact")
        context_ref = fact.get("context_ref")
        context_row = context_lookup.get(str(context_ref)) if context_ref is not None else {}
        context_row = context_row or {}
        unit_ref = fact.get("unit_ref")
        unit_row = unit_lookup.get(str(unit_ref)) if unit_ref is not None else {}
        unit_row = unit_row or {}
        unit_value = fact.get("unit") or unit_row.get("unit") or unit_row.get("measure")
        dimensions = fact.get("dimensions") if isinstance(fact.get("dimensions"), dict) else context_row.get("dimensions") if isinstance(context_row.get("dimensions"), dict) else {}
        value_numeric = fact.get("value_numeric")
        if value_numeric is None:
            value_numeric = fact.get("numeric_value")
        if value_numeric is None:
            value_numeric = fact.get("value")
        value_text = fact.get("value_text")
        if value_text is None:
            value_text = fact.get("raw_value")
        if value_text is None and value_numeric is not None:
            value_text = str(value_numeric)
        xbrl_facts_raw.append(
            {
                "fact_id": fact_id,
                "raw_fact_id": source_fact_id,
                "concept": fact.get("concept"),
                "taxonomy": fact.get("taxonomy") or (fact.get("raw") or {}).get("taxonomy"),
                "label": fact.get("label"),
                "value_text": value_text,
                "value_numeric": as_decimal(value_numeric),
                "unit_ref": unit_ref,
                "unit": unit_value,
                "decimals": fact.get("decimals") or (fact.get("raw") or {}).get("decimals"),
                "scale": fact.get("scale") or (fact.get("raw") or {}).get("scale"),
                "context_ref": context_ref,
                "period_start": as_date_text(fact.get("period_start") or context_row.get("period_start")),
                "period_end": as_date_text(fact.get("period_end") or context_row.get("period_end")),
                "duration_days": as_int(fact.get("duration_days") or context_row.get("duration_days")),
                "instant": as_date_text(fact.get("instant") or context_row.get("instant")),
                "fiscal_year": as_int(fact.get("fiscal_year") or filing.get("fiscal_year")),
                "fiscal_period": fact.get("fiscal_period") or filing.get("fiscal_period"),
                "dimensions": dimensions,
                "source_type": "document_full_fact",
                "source_file": str(context.document_full_path),
                "is_extension": fact.get("is_extension"),
                "html_anchor": fact.get("html_anchor"),
                "xpath": fact.get("xpath"),
                "raw": fact,
            }
        )

    statements: list[dict[str, Any]] = []
    statement_items: list[dict[str, Any]] = []
    key_metrics: list[dict[str, Any]] = []
    citations: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []
    enriched: list[dict[str, Any]] = []
    wide: dict[str, dict[str, Any]] = {}

    source_statements = _iter_statements(financial)
    if market in {"US", "EU", "JP", "KR"}:
        source_statements.extend(_xbrl_fact_statements(
            document_full,
            market=market,
            statement_name="SEC inline XBRL facts" if market == "US" else "ESEF XBRL facts",
            parse_run_id=parse_run_id,
        ))

    for statement_idx, statement in enumerate(source_statements, start=1):
        statement_id = str(statement.get("statement_id") or stable_id(parse_run_id, statement_idx, prefix="stmt"))
        statement_type = str(statement.get("statement_type") or statement.get("type") or "unknown")
        unit = statement.get("unit")
        currency = statement.get("currency") or infer_currency(unit, financial.get("currency"), filing.get("accounting_standard"))
        scale = normalize_scale(statement.get("scale"), unit)
        statements.append(
            {
                "statement_id": statement_id,
                "statement_type": statement_type,
                "statement_name": statement.get("statement_name") or statement.get("name") or statement.get("title"),
                "scope": statement.get("scope"),
                "scope_name": statement.get("scope_name"),
                "title": statement.get("title"),
                "unit": unit,
                "scale": scale,
                "currency": currency,
                "table_indexes": statement.get("table_indexes") or [],
                "columns": statement.get("columns") or [],
                "raw": statement,
            }
        )
        for item_idx, item in enumerate(statement.get("items") or [], start=1):
            if not isinstance(item, dict):
                continue
            item_name = item.get("item_name") or item.get("name") or item.get("local_name") or item.get("label")
            source_canonical = item.get("canonical_name") or item.get("canonical_label") or item.get("concept")
            canonical_name, canonical_scope = resolve_canonical(
                market,
                source_canonical,
                item_name,
                item.get("taxonomy_tag"),
                item.get("concept"),
                industry_profile=company.get("industry_profile"),
            )
            item_statement_type = str(item.get("statement_type") or statement_type)
            for period_key, payload in _period_values(item, filing):
                row_statement_type = str(payload.get("statement_type") or item_statement_type)
                source = _source_from_payload(payload, item)
                value = payload.get("value", item.get("value"))
                raw_value = payload.get("raw_value", item.get("raw_value", value))
                row_unit = payload.get("unit") or item.get("unit") or unit
                row_currency = payload.get("currency") or item.get("currency") or currency or infer_currency(row_unit)
                row_scale = normalize_scale(payload.get("scale") or item.get("scale") or scale, row_unit)
                evidence_id = source.get("evidence_id") or payload.get("evidence_id") or item.get("evidence_id") or stable_id(parse_run_id, statement_id, item_idx, period_key, prefix="ev")
                table_index = as_int(source.get("table_index") or payload.get("table_index") or item.get("table_index"))
                page_number = as_int(source.get("page_number") or source.get("pdf_page_number") or payload.get("page_number") or item.get("page_number"))
                citation = {
                    "evidence_id": evidence_id,
                    "source_type": source.get("source_type") or "table_cell",
                    "source_id": source.get("source_id"),
                    "xbrl_tag": item.get("xbrl_tag") or item.get("concept"),
                    "context_ref": item.get("context_ref") or payload.get("context_ref"),
                    "unit_ref": item.get("unit_ref") or payload.get("unit_ref"),
                    "fact_id": item.get("fact_id") or payload.get("fact_id"),
                    "html_anchor": source.get("html_anchor") or item.get("html_anchor"),
                    "xpath": source.get("xpath") or item.get("xpath"),
                    "page_number": page_number,
                    "table_index": table_index,
                    "row_index": as_int(source.get("row_index") or payload.get("row_index")),
                    "column_index": as_int(source.get("column_index") or payload.get("column_index")),
                    "bbox": source.get("bbox") or payload.get("bbox") or item.get("bbox"),
                    "quote_text": source.get("quote_text") or payload.get("quote_text") or raw_value,
                    "local_path": source.get("local_path") or str(context.document_full_path),
                    "source_url": source.get("source_url") or filing.get("source_url"),
                    "target": canonical_name,
                    "raw": {"source": source, "item": item, "payload": payload},
                }
                citations.append(citation)
                item_uid = stable_id(parse_run_id, statement_id, item_idx, period_key, canonical_name, prefix="item")
                metric_key = canonical_name or stable_id("unmapped", item_name, item.get("concept"), period_key, prefix="unmapped")
                row = {
                    "item_uid": item_uid,
                    "statement_id": statement_id,
                    "statement_type": row_statement_type,
                    "statement_name": statements[-1]["statement_name"],
                    "scope": statement.get("scope"),
                    "scope_name": statement.get("scope_name"),
                    "item_index": item_idx,
                    "period_key": str(period_key),
                    "item_name": item_name,
                    "canonical_name": canonical_name,
                    "canonical_scope": canonical_scope,
                    "value": as_decimal(value),
                    "raw_value": str(raw_value) if raw_value is not None else None,
                    "unit": row_unit,
                    "currency": row_currency,
                    "scale": row_scale,
                    "period_start": as_date_text(payload.get("period_start") or item.get("period_start")),
                    "period_end": as_date_text(payload.get("period_end") or item.get("period_end") or filing.get("period_end")),
                    "fiscal_year": as_int(payload.get("fiscal_year") or item.get("fiscal_year") or filing.get("fiscal_year")),
                    "fiscal_period": payload.get("fiscal_period") or item.get("fiscal_period") or filing.get("fiscal_period"),
                    "accounting_standard": filing.get("accounting_standard"),
                    "industry_profile": company.get("industry_profile") or "general",
                    "confidence": as_decimal(payload.get("confidence") or item.get("confidence") or 0.7),
                    "source_page_number": page_number,
                    "source_table_index": table_index,
                    "source_row_index": citation["row_index"],
                    "source_column_index": citation["column_index"],
                    "source_bbox": citation["bbox"],
                    "evidence_id": evidence_id,
                    "raw_fact_id": item.get("fact_id") or payload.get("fact_id"),
                    "concept": item.get("concept"),
                    "xbrl_tag": item.get("xbrl_tag") or item.get("concept"),
                    "taxonomy": item.get("taxonomy"),
                    "label": item.get("label") or item_name,
                    "context_ref": item.get("context_ref") or payload.get("context_ref"),
                    "dimensions": item.get("dimensions") if isinstance(item.get("dimensions"), dict) else {},
                    "raw": {"statement": statement, "item": item, "payload": payload},
                }
                if row_statement_type == "key_metrics":
                    key_metrics.append(row)
                else:
                    statement_items.append(row)
                value_standardized = row["value"] * row_scale if row.get("value") is not None and row_scale is not None else None
                flags = []
                if canonical_scope == "unmapped":
                    flags.append("canonical_unmapped")
                if row_unit is None:
                    flags.append("unit_missing")
                enriched.append(
                    {
                        **row,
                        "enriched_id": stable_id(item_uid, "enriched", prefix="enriched"),
                        "canonical_label": canonical_name,
                        "metric_family": _metric_family(canonical_name, row_statement_type),
                        "unit_raw": row_unit,
                        "unit_standardized": _standard_unit(row_unit, row_currency),
                        "unit_scale": row_scale,
                        "value_extracted": row["value"],
                        "value_standardized": value_standardized,
                        "quality_flags": flags,
                        "normalization_confidence": "medium" if not flags else "low",
                    }
                )
                period_bucket = wide.setdefault(
                    str(period_key),
                    {"balance_sheet": {}, "income_statement": {}, "cash_flow_statement": {}, "key_metrics": {}, "all_metrics": {}},
                )
                payload_for_wide = {"value": str(row["value"]) if row["value"] is not None else None, "raw_value": row["raw_value"], "unit": row_unit, "currency": row_currency, "evidence_id": evidence_id, "canonical_scope": canonical_scope, "item_name": item_name}
                if row_statement_type in {"balance_sheet", "statement_of_financial_position"}:
                    period_bucket["balance_sheet"][metric_key] = payload_for_wide
                elif row_statement_type in {"income_statement", "profit_or_loss"}:
                    period_bucket["income_statement"][metric_key] = payload_for_wide
                elif row_statement_type in {"cash_flow_statement", "cash_flows"}:
                    period_bucket["cash_flow_statement"][metric_key] = payload_for_wide
                elif row_statement_type == "key_metrics":
                    period_bucket["key_metrics"][metric_key] = payload_for_wide
                period_bucket["all_metrics"][metric_key] = {**payload_for_wide, "statement_type": row_statement_type}
                text = " | ".join(str(part) for part in (company.get("company_name"), row_statement_type, item_name, period_key, raw_value, row_unit) if part not in (None, ""))
                chunks.append(
                    {
                        "chunk_uid": stable_id(parse_run_id, "fact", metric_key, period_key, evidence_id, prefix="chunk"),
                        "doc_type": "financial_fact",
                        "section_title": statements[-1]["statement_name"],
                        "statement_type": row_statement_type,
                        "evidence_id": evidence_id,
                        "canonical_name": canonical_name,
                        "period_key": str(period_key),
                        "page_number": page_number,
                        "table_index": table_index,
                        "wiki_path": str(context.document_full_path),
                        "source_url": filing.get("source_url"),
                        "text": text,
                        "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        "metadata": {"canonical_scope": canonical_scope, "quality_flags": flags},
                    }
                )

    wide_rows = [
        {"period_key": period_key, **bucket, "raw": {"period_key": period_key}}
        for period_key, bucket in wide.items()
    ]
    checks = []
    financial_checks = document_full.get("financial_checks") if isinstance(document_full.get("financial_checks"), dict) else {}
    for idx, check in enumerate(financial_checks.get("checks") or [], start=1):
        if isinstance(check, dict):
            checks.append({"check_id": stable_id(parse_run_id, check.get("rule_id"), check.get("period"), idx, prefix="check"), **check})

    normalization_rules = [
        {"rule_id": "canonical_common_core_v1", "rule_type": "canonical", "rule_version": "document-full-v1", "description": "Common core cross-market canonical metric aliases.", "preserves_raw_value": True, "confidence_default": "medium", "notes": "Raw facts are never overwritten."},
        {"rule_id": "unit_scale_from_report_unit_v1", "rule_type": "unit", "rule_version": "document-full-v1", "description": "Derive scale from explicit scale or report unit text.", "preserves_raw_value": True, "confidence_default": "medium", "notes": "Unmapped units keep raw value only."},
    ]

    return MarketDocumentFullRows(
        company=company,
        filing=filing,
        parse_run=parse_run,
        artifacts=artifacts,
        sections=sections,
        pages=pages,
        blocks=blocks,
        tables=tables,
        xbrl_contexts=xbrl_contexts,
        xbrl_units=xbrl_units,
        xbrl_facts_raw=xbrl_facts_raw,
        statements=statements,
        statement_items=statement_items,
        key_metrics=key_metrics,
        checks=checks,
        citations=citations,
        chunks=chunks,
        normalization_rules=normalization_rules,
        enriched_items=enriched,
        wide_rows=wide_rows,
        quality_reports=_quality_report_row(document_full, parse_run_id, filing_id),
        footnotes=enhanced_rows["footnotes"],
        toc_entries=enhanced_rows["toc_entries"],
        financial_note_links=enhanced_rows["financial_note_links"],
        table_relations=enhanced_rows["table_relations"],
        table_quality_signals=enhanced_rows["table_quality_signals"],
        raw_payload_refs=[{"payload_name": "document_full", "path": str(context.document_full_path), "sha256": context.document_full_sha256, "summary": {"market": market}}],
    )


def _metric_family(canonical: str | None, statement_type: str | None) -> str | None:
    text = f"{canonical or ''} {statement_type or ''}".lower()
    if "asset" in text:
        return "asset"
    if "liabilit" in text:
        return "liability"
    if "equity" in text:
        return "equity"
    if "revenue" in text or "income" in text:
        return "revenue"
    if "profit" in text:
        return "profit"
    if "cash" in text:
        return "cash_flow"
    if "eps" in text or "per_share" in text:
        return "per_share"
    return None


def _standard_unit(unit: Any, currency: str | None) -> str | None:
    unit_text = str(unit or "").lower()
    if "%" in unit_text or "percent" in unit_text:
        return "ratio"
    if "per share" in unit_text or "/股" in unit_text or "eps" in unit_text:
        return f"{currency or ''}/share".strip("/")
    if currency:
        return currency
    return str(unit) if unit else None
