#!/usr/bin/env python3
"""
Natural-language query API for the four pdf2md financial tables.

Run:
  uvicorn db.imports.financial_query_api:app --host 0.0.0.0 --port 18188

Example:
  curl -s http://127.0.0.1:18188/query \
    -H 'content-type: application/json' \
    -d '{"question":"查询信达证券2025年利润表营业总收入"}'
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import subprocess
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)

try:  # Support both top-level and package imports.
    from market_ingestion_contract import database_url as _market_database_url
except Exception:  # pragma: no cover - exercised when imported as db.imports.*
    try:
        from .market_ingestion_contract import database_url as _market_database_url
    except Exception:  # pragma: no cover
        _market_database_url = None


def _load_pg_config_from_file(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    spec = importlib.util.spec_from_file_location("siq_pdf2md_pg_config", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load PostgreSQL config from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = getattr(module, "PG_CONFIG", None)
    return config if isinstance(config, dict) else None


def _connection_kwargs_from_env() -> dict[str, Any] | None:
    if not any(os.environ.get(key) for key in ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD")):
        return None
    return {
        "host": os.environ.get("PGHOST", "127.0.0.1"),
        "port": int(os.environ.get("PGPORT", "15432")),
        "dbname": os.environ.get("PGDATABASE", "siq"),
        "user": os.environ.get("PGUSER", "postgres"),
        "password": os.environ.get("PGPASSWORD", ""),
    }


def get_connection():
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return psycopg.connect(database_url, row_factory=dict_row)

    env_config = _connection_kwargs_from_env()
    if env_config:
        return psycopg.connect(**env_config, row_factory=dict_row)

    config_path = (
        Path(os.environ["SIQ_DB_CONFIG_PY"]).expanduser()
        if os.environ.get("SIQ_DB_CONFIG_PY")
        else Path(os.environ["DB_CONFIG_PY"]).expanduser()
        if os.environ.get("DB_CONFIG_PY")
        else Path(os.environ["SIQ_DB_CONFIG_PY"]).expanduser()
        if os.environ.get("SIQ_DB_CONFIG_PY")
        else None
    )
    config = _load_pg_config_from_file(config_path)
    if config:
        return psycopg.connect(**config, row_factory=dict_row)

    return psycopg.connect(
        host="127.0.0.1",
        port=15432,
        dbname="siq",
        user="postgres",
        password="",
        row_factory=dict_row,
    )


HERMES_BIN = os.getenv("HERMES_BIN", "/home/maoyd/.local/bin/hermes")
HERMES_TIMEOUT_SECONDS = int(os.getenv("HERMES_TIMEOUT_SECONDS", "20"))

SOURCE_TABLES = {
    "balance_sheet": "pdf2md.financial_balance_sheet_items",
    "income_statement": "pdf2md.financial_income_statement_items",
    "cash_flow_statement": "pdf2md.financial_cash_flow_statement_items",
    "wide": "pdf2md.financial_all_metrics_wide",
}

MARKET_AGENT_SCHEMAS = {
    "HK": "pdf2md_hk",
    "JP": "edinet_jp",
    "KR": "dart_kr",
    "EU": "eu_ifrs",
    "US": "sec_us",
}

MARKET_AGENT_DATABASES = {
    "HK": "siq_hk",
    "JP": "siq_jp",
    "KR": "siq_kr",
    "EU": "siq_eu",
    "US": "siq_us",
}

MARKET_AGENT_CODE_COLUMNS = (
    "company_ticker",
    "stock_code",
    "hkex_stock_code",
    "security_code",
    "edinet_code",
    "corp_code",
    "cik",
    "company_id",
)

MARKET_AGENT_SELECT_COLUMNS = (
    "company_id",
    "company_ticker",
    "stock_code",
    "hkex_stock_code",
    "security_code",
    "edinet_code",
    "corp_code",
    "cik",
    "country",
    "company_name",
    "filing_id",
    "accession_number",
    "report_type",
    "form",
    "fiscal_year",
    "fiscal_period",
    "filing_period_end",
    "parse_run_id",
    "wiki_package_path",
    "statement_id",
    "statement_type",
    "statement_name",
    "item_index",
    "canonical_name",
    "canonical_label",
    "item_name",
    "item_name_raw",
    "metric_name",
    "metric_name_raw",
    "local_name",
    "label",
    "concept",
    "xbrl_tag",
    "taxonomy_tag",
    "source_ref",
    "context_ref",
    "period_key",
    "period_start",
    "period_end",
    "value",
    "raw_value",
    "unit",
    "currency",
    "fact_currency",
    "reporting_currency",
    "presentation_currency",
    "converted_currency",
    "converted_value",
    "fx_rate_date",
    "fx_rate_source",
    "scale",
    "evidence_id",
    "evidence_page_number",
    "evidence_table_index",
    "evidence_row_index",
    "evidence_column_index",
    "quote_text",
    "source_url",
)

AGENT_FINANCIAL_FACT_FIELDS = (
    "market",
    "schema",
    "company_id",
    "filing_id",
    "parse_run_id",
    "metric_name",
    "canonical_name",
    "period",
    "value",
    "raw_value",
    "unit",
    "currency",
    "source_page",
    "table_index",
    "bbox",
    "evidence_id",
    "quote",
    "source_url",
    "wiki_report_path",
    "source_type",
)

MARKET_AGENT_METRIC_ALIASES = {
    "revenue": (
        "revenue",
        "revenues",
        "sales",
        "operating_revenue",
        "营业收入",
        "营业总收入",
        "营收",
        "收入",
        "売上",
        "売上高",
        "売上収益",
        "매출",
        "매출액",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
    ),
    "gross_profit": ("gross_profit", "gross profit", "毛利", "売上総利益", "매출총이익"),
    "operating_profit": ("operating_profit", "operating income", "营业利润", "営業利益", "영업이익"),
    "profit_before_tax": ("profit_before_tax", "profit before tax", "税前利润", "税引前利益", "법인세비용차감전순이익"),
    "net_profit": ("net_profit", "net income", "net_profit_loss", "净利润", "純利益", "당기순이익"),
    "total_assets": ("total_assets", "assets", "总资产", "资产总计", "総資産", "자산총계"),
    "total_liabilities": ("total_liabilities", "liabilities", "总负债", "负债合计", "負債合計", "부채총계"),
    "total_equity": ("total_equity", "equity", "股东权益", "所有者权益", "資本合計", "자본총계"),
    "current_assets": ("current_assets", "流动资产", "流動資産", "유동자산"),
    "cash_and_cash_equivalents": (
        "cash_and_cash_equivalents",
        "cash_and_equivalents",
        "cash",
        "货币资金",
        "现金及现金等价物",
        "現金及び現金同等物",
        "현금및현금성자산",
    ),
    "operating_cash_flow": (
        "operating_cash_flow",
        "operating_cash_flow_net",
        "经营现金流",
        "经营活动现金流量净额",
        "営業活動によるキャッシュ・フロー",
        "영업활동현금흐름",
    ),
    "basic_eps": ("basic_eps", "eps", "基本每股收益", "每股收益", "1株当たり当期利益", "기본주당이익"),
}


def quote_ident(identifier: str) -> str:
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", identifier):
        raise ValueError(f"Unsafe SQL identifier: {identifier!r}")
    return identifier


def get_market_connection(market: str):
    market_key = str(market or "").upper()
    explicit = os.environ.get(f"SIQ_{market_key}_DATABASE_URL")
    if _market_database_url is not None:
        return psycopg.connect(_market_database_url(explicit, market_key), row_factory=dict_row)

    host = os.environ.get("SIQ_PGHOST") or os.environ.get("PGHOST") or "127.0.0.1"
    port = os.environ.get("SIQ_PGPORT") or os.environ.get("PGPORT") or "15432"
    user = os.environ.get("SIQ_PGUSER") or os.environ.get("PGUSER") or "postgres"
    password = os.environ.get("SIQ_PGPASSWORD") or os.environ.get("PGPASSWORD") or ""
    auth = f"{user}:{password}" if password else user
    database = MARKET_AGENT_DATABASES[market_key]
    return psycopg.connect(f"postgresql://{auth}@{host}:{port}/{database}", row_factory=dict_row)


def infer_market_from_query_text(query_text: str, company_hint: dict[str, Any] | None = None) -> str | None:
    hint_text = " ".join(str(value) for value in (company_hint or {}).values() if value)
    text = f"{query_text}\n{hint_text}".lower()
    path_match = re.search(r"/data/wiki/(hk|jp|kr|eu|us|us_sec)(?:/|$)", text)
    if path_match:
        market = path_match.group(1).upper()
        return "US" if market == "US_SEC" else market
    if "/data/parser-results/us-sec/" in text or "/us-sec/" in text:
        return "US"
    market_terms = {
        "HK": ("港股", "香港", "hkex", "hk:"),
        "JP": ("日股", "日本", "edinet", "jp:"),
        "KR": ("韩股", "韩国", "dart", "kr:"),
        "EU": ("欧股", "欧洲", "ifrs", "esef", "eu:"),
        "US": ("美股", "10-k", "10-q", "nasdaq", "nyse", "us:"),
    }
    for market, terms in market_terms.items():
        if any(term.lower() in text for term in terms):
            return market
    if re.search(r"(?<![a-z0-9])sec(?![a-z0-9])", text):
        return "US"
    return None


def _company_hint_terms(
    query_text: str,
    company_hint: dict[str, Any] | None = None,
    parsed: dict[str, Any] | None = None,
) -> tuple[list[str], list[str]]:
    values = []
    if isinstance(company_hint, dict):
        values.extend(
            company_hint.get(key)
            for key in ("code", "stock_code", "ticker", "company_id", "name", "company_name", "dir")
        )
    if isinstance(parsed, dict):
        values.extend(
            parsed.get(key)
            for key in (
                "code",
                "stock_code",
                "ticker",
                "company_id",
                "company_name",
                "name",
                "resolved_stock_code",
                "resolved_company_id",
                "resolved_stock_name",
            )
        )
    values.append(query_text)
    codes: list[str] = []
    names: list[str] = []
    for index, value in enumerate(values):
        text = str(value or "").strip()
        if not text:
            continue
        company_path = re.search(r"/companies/([^/]+)", text)
        if company_path:
            slug = company_path.group(1)
            if "-" in slug:
                code, name = slug.split("-", 1)
                codes.append(code)
                names.append(name.replace("-", " "))
            else:
                names.append(slug.replace("-", " "))
        for company_id in re.findall(r"\b(?:HK|JP|KR|EU|US):[A-Za-z0-9:._-]+", text, flags=re.IGNORECASE):
            codes.append(company_id.upper())
            tail = company_id.rsplit(":", 1)[-1]
            tail_digits = re.sub(r"\D+", "", tail)
            if tail_digits:
                codes.append(tail_digits.zfill(10) if len(tail_digits) > 6 else tail_digits)
        for code in re.findall(r"(?<!\d)(\d{4,10})(?!\d)", text):
            if re.fullmatch(r"(?:19|20)\d{2}", code):
                continue
            codes.append(code)
        if index == len(values) - 1:
            for ticker in re.findall(r"\b[A-Z]{1,8}\b", text):
                if ticker not in {"HK", "JP", "KR", "EU", "US", "SEC"}:
                    codes.append(ticker)
        if re.fullmatch(r"[A-Za-z]{1,8}|[0-9]{4,10}", text):
            codes.append(text)
        elif index == len(values) - 1:
            names.extend(_query_company_name_candidates(text))
        elif len(text) <= 80:
            names.append(text)
        elif index < len(values) - 1:
            for ticker in re.findall(r"\b[A-Z]{1,8}\b", text):
                codes.append(ticker)
    return list(dict.fromkeys(codes)), list(dict.fromkeys(names))


def _query_company_name_candidates(query_text: str) -> list[str]:
    text = str(query_text or "").strip()
    if not text or "/" in text:
        return []
    cleaned = text
    removable_terms = {
        "港股",
        "香港",
        "日股",
        "日本",
        "韩股",
        "韩国",
        "欧股",
        "欧洲",
        "美股",
        "收入",
        "营收",
        "营业收入",
        "利润",
        "净利润",
        "资产",
        "负债",
        "现金流",
        "年报",
        "季报",
        "年度",
        "多少",
        "是多少",
        "查询",
        "请问",
        "show",
        "me",
        "what",
        "is",
        "the",
        "for",
        "of",
    }
    for aliases in MARKET_AGENT_METRIC_ALIASES.values():
        removable_terms.update(str(alias) for alias in aliases if alias)
    for term in sorted(removable_terms, key=len, reverse=True):
        cleaned = re.sub(re.escape(term), " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:HK|JP|KR|EU|US|SEC)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:19|20)\d{2}\b", " ", cleaned)
    cleaned = re.sub(r"(?<!\d)\d{4,10}(?!\d)", " ", cleaned)
    cleaned = re.sub(r"[：:，,。.?？!！()（）\\[\\]{}]", " ", cleaned)
    parts = [part.strip() for part in re.split(r"\s+", cleaned) if part.strip()]
    candidates: list[str] = []
    for part in parts:
        if len(part) < 2 or len(part) > 40:
            continue
        if re.fullmatch(r"[A-Za-z]{1,2}", part):
            continue
        candidates.append(part)
    return candidates[:3]


def _market_metric_terms(parsed: dict[str, Any], query_text: str) -> tuple[list[str], str | None]:
    raw_terms = [
        parsed.get("metric_name"),
        parsed.get("canonical_name"),
        *(parsed.get("metric_terms") or []),
    ]
    text = re.sub(r"\s+", "", query_text or "").lower()
    matched_alias: str | None = None
    canonical_terms: list[str] = []
    aliases: list[str] = []
    for canonical, candidates in MARKET_AGENT_METRIC_ALIASES.items():
        for candidate in candidates:
            candidate_text = re.sub(r"\s+", "", str(candidate)).lower()
            if candidate_text and candidate_text in text:
                canonical_terms.append(canonical)
                aliases.extend(str(item) for item in candidates)
                matched_alias = str(candidate)
                break
    for raw in raw_terms:
        if raw in (None, ""):
            continue
        raw_text = str(raw)
        aliases.append(raw_text)
        for canonical, candidates in MARKET_AGENT_METRIC_ALIASES.items():
            if raw_text == canonical or any(raw_text.lower() == str(candidate).lower() for candidate in candidates):
                canonical_terms.append(canonical)
    terms = list(dict.fromkeys([*canonical_terms, *aliases]))
    return terms, matched_alias


def _market_period_filters(parsed: dict[str, Any], query_text: str) -> list[tuple[str, Any]]:
    filters: list[tuple[str, Any]] = []
    if parsed.get("period_key"):
        filters.append(("period_key", parsed["period_key"]))
    elif parsed.get("year"):
        filters.append(("fiscal_year", parsed["year"]))
    else:
        year_match = re.search(r"(20[0-9]{2})", query_text or "")
        if year_match:
            filters.append(("fiscal_year", year_match.group(1)))
    return filters


def _first_market_scope_value(*values: Any) -> str | None:
    for value in values:
        if value in (None, ""):
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _nested_mapping(value: Any, key: str) -> dict[str, Any]:
    nested = value.get(key) if isinstance(value, dict) else None
    return nested if isinstance(nested, dict) else {}


def _market_agent_scope_filters(
    parsed: dict[str, Any],
    company_hint: dict[str, Any] | None,
) -> list[tuple[str, str]]:
    hint = company_hint if isinstance(company_hint, dict) else {}
    report = _nested_mapping(hint, "report")
    filing = _nested_mapping(hint, "filing")
    resolved_period = _nested_mapping(hint, "resolved_period")
    postgres = _nested_mapping(hint, "postgres")
    parse_run_id = _first_market_scope_value(
        parsed.get("parse_run_id"),
        hint.get("parse_run_id"),
        postgres.get("parse_run_id"),
        report.get("parse_run_id"),
        filing.get("parse_run_id"),
        resolved_period.get("parse_run_id"),
    )
    filing_id = _first_market_scope_value(
        parsed.get("filing_id"),
        hint.get("filing_id"),
        postgres.get("filing_id"),
        report.get("filing_id"),
        filing.get("filing_id"),
        resolved_period.get("filing_id"),
    )
    filters: list[tuple[str, str]] = []
    if parse_run_id:
        filters.append(("parse_run_id", parse_run_id))
    if filing_id:
        filters.append(("filing_id", filing_id))
    return filters


def _market_view_columns(cur: Any, schema: str) -> set[str]:
    cur.execute(
        """
        select column_name
        from information_schema.columns
        where table_schema = %s and table_name = 'v_agent_financial_facts'
        """,
        (schema,),
    )
    return {str(row["column_name"] if isinstance(row, dict) else row[0]) for row in cur.fetchall()}


def _market_agent_row(row: dict[str, Any], schema: str, matched_alias: str | None) -> dict[str, Any]:
    output = dict(row)
    output["source_table"] = f"{schema}.v_agent_financial_facts"
    output["task_id"] = output.get("parse_run_id") or output.get("filing_id")
    output["metric_name"] = (
        matched_alias
        or output.get("item_name")
        or output.get("item_name_raw")
        or output.get("metric_name")
        or output.get("metric_name_raw")
        or output.get("local_name")
        or output.get("canonical_name")
        or output.get("canonical_label")
    )
    output["stock_code"] = (
        output.get("company_ticker")
        or output.get("stock_code")
        or output.get("hkex_stock_code")
        or output.get("security_code")
        or output.get("cik")
    )
    output["stock_name"] = output.get("company_name")
    output["source_page_number"] = output.get("evidence_page_number")
    output["source_table_index"] = output.get("evidence_table_index")
    return normalize_json(output)


def query_market_agent_view_result(
    query_text: str,
    parsed: dict[str, Any] | None = None,
    company_hint: dict[str, Any] | None = None,
    *,
    limit: int = 20,
    market: str | None = None,
) -> dict[str, Any] | None:
    parsed = dict(parsed or {})
    market_key = str(market or parsed.get("market") or infer_market_from_query_text(query_text, company_hint) or "").upper()
    if market_key not in MARKET_AGENT_SCHEMAS:
        return None
    schema = MARKET_AGENT_SCHEMAS[market_key]
    metric_terms, matched_alias = _market_metric_terms(parsed, query_text)
    codes, names = _company_hint_terms(query_text, company_hint, parsed)
    period_filters = _market_period_filters(parsed, query_text)
    statement_type = parsed.get("statement_type")

    with get_market_connection(market_key) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute("SET TRANSACTION READ ONLY")
            except Exception:
                pass
            columns = _market_view_columns(cur, schema)
            if not columns:
                return None
            selected = [column for column in MARKET_AGENT_SELECT_COLUMNS if column in columns]
            if not selected:
                return None
            where_parts: list[str] = []
            params: list[Any] = []
            for column, value in _market_agent_scope_filters(parsed, company_hint):
                if column in columns:
                    where_parts.append(f"{quote_ident(column)} = %s")
                    params.append(value)
            code_columns = [column for column in MARKET_AGENT_CODE_COLUMNS if column in columns]
            company_name_columns = [column for column in ("company_name",) if column in columns]
            company_parts: list[str] = []
            for code in codes[:4]:
                for column in code_columns:
                    company_parts.append(f"{quote_ident(column)} = %s")
                    params.append(code)
            for name in names[:3]:
                compact = str(name).strip()
                if len(compact) < 2 or "/" in compact:
                    continue
                for column in company_name_columns:
                    company_parts.append(f"{quote_ident(column)} ilike %s")
                    params.append(f"%{compact}%")
            if company_parts:
                where_parts.append("(" + " or ".join(company_parts) + ")")

            if statement_type and "statement_type" in columns:
                where_parts.append("statement_type = %s")
                params.append(statement_type)

            for column, value in period_filters:
                if column in columns:
                    where_parts.append(f"{quote_ident(column)} = %s")
                    params.append(value)

            metric_parts: list[str] = []
            for term in metric_terms[:16]:
                if not term:
                    continue
                for column in ("canonical_name", "canonical_label", "metric_name"):
                    if column in columns:
                        metric_parts.append(f"{quote_ident(column)} = %s")
                        params.append(str(term))
                for column in (
                    "item_name",
                    "item_name_raw",
                    "metric_name_raw",
                    "local_name",
                    "label",
                    "concept",
                    "xbrl_tag",
                    "taxonomy_tag",
                    "source_ref",
                ):
                    if column in columns:
                        metric_parts.append(f"{quote_ident(column)} ilike %s")
                        params.append(f"%{term}%")
            if metric_parts:
                where_parts.append("(" + " or ".join(metric_parts) + ")")
            elif parsed.get("query_type") != "company_all":
                return None

            where_sql = " and ".join(where_parts) if where_parts else "1=1"
            order_columns = [
                column
                for column in (
                    "filing_period_end",
                    "fiscal_year",
                    "parse_completed_at",
                    "parse_run_id",
                    "period_key",
                    "statement_type",
                    "item_index",
                    "canonical_name",
                    "item_name",
                )
                if column in columns
            ]
            desc_order_columns = {"filing_period_end", "fiscal_year", "parse_completed_at", "parse_run_id"}
            order_sql = ", ".join(
                f"{quote_ident(column)} desc nulls last" if column in desc_order_columns else quote_ident(column)
                for column in order_columns
            )
            if not order_sql:
                order_sql = "1"
            select_sql = ", ".join(quote_ident(column) for column in selected)
            schema_sql = quote_ident(schema)
            cur.execute(
                f"""
                select {select_sql}
                from {schema_sql}.v_agent_financial_facts
                where {where_sql}
                order by {order_sql}
                limit %s
                """,
                (*params, limit),
            )
            rows = [_market_agent_row(dict(row), schema, matched_alias) for row in cur.fetchall()]
    if not rows:
        return None
    resolved = {
        "market": market_key,
        "query_type": parsed.get("query_type") or ("metric" if metric_terms else "company_all"),
        "metric_name": matched_alias or parsed.get("metric_name"),
        "canonical_name": parsed.get("canonical_name"),
        "resolved_company_id": rows[0].get("company_id"),
        "resolved_stock_code": rows[0].get("stock_code"),
        "resolved_stock_name": rows[0].get("company_name") or rows[0].get("stock_name"),
        "query_mode": "multi_market_agent_view",
    }
    normalized_parsed = normalize_json({**parsed, **{key: value for key, value in resolved.items() if value not in (None, "")}})
    return {
        "question": query_text,
        "query_text": query_text,
        "parsed": normalized_parsed,
        "source_tables": [f"{schema}.v_agent_financial_facts"],
        "rows": rows[:limit],
        "agent_facts": agent_facts_from_rows(rows[:limit], source_type="postgresql_agent_view"),
    }

STATEMENT_ALIASES = {
    "balance_sheet": ("资产负债表", "资产表", "负债表", "balance sheet", "balance_sheet"),
    "income_statement": ("利润表", "损益表", "income statement", "income_statement", "profit"),
    "cash_flow_statement": ("现金流量表", "现金流表", "cash flow", "cash_flow_statement"),
}

METRIC_HINTS = (
    "营业收入",
    "营业总收入",
    "净利润",
    "归母净利润",
    "基本每股收益",
    "货币资金",
    "资产总计",
    "负债合计",
    "所有者权益",
    "经营活动产生的现金流量净额",
    "现金及现金等价物净增加额",
)

METRIC_ALIASES = {
    "营业总收入": ("营业总收入", "营业收入", "营收", "总收入", "total_operating_revenue", "operating_revenue"),
    "净利润": ("净利润", "利润", "net_profit"),
    "归属于母公司股东的净利润": ("归母净利润", "归母利润", "母公司股东净利润", "归属于母公司股东的净利润"),
    "基本每股收益": ("基本每股收益", "每股收益", "EPS", "eps", "basic_eps"),
    "货币资金": ("货币资金", "货币", "现金余额", "cash_and_cash_equivalents"),
    "资产总计": ("资产总计", "总资产", "total_assets"),
    "负债合计": ("负债合计", "总负债", "负债总计", "total_liabilities"),
    "所有者权益合计": ("所有者权益", "股东权益", "权益合计", "所有者权益合计", "total_equity"),
    "经营活动产生的现金流量净额": ("经营现金流", "经营活动现金流", "经营活动产生的现金流量净额", "operating_cash_flow"),
    "现金及现金等价物净增加额": ("现金净增加额", "现金及现金等价物净增加额", "net_increase_cash"),
}

CANONICAL_ALIASES = {
    "营收": "营业总收入",
    "收入": "营业总收入",
    "总收入": "营业总收入",
    "营业收入": "营业总收入",
    "EPS": "基本每股收益",
    "eps": "基本每股收益",
    "每股收益": "基本每股收益",
    "总资产": "资产总计",
    "总负债": "负债合计",
    "负债总计": "负债合计",
    "股东权益": "所有者权益合计",
    "权益合计": "所有者权益合计",
    "经营现金流": "经营活动产生的现金流量净额",
    "经营活动现金流": "经营活动产生的现金流量净额",
    "现金净增加额": "现金及现金等价物净增加额",
}


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1)
    use_hermes: bool = True
    limit: int = Field(100, ge=1, le=1000)


class QueryResponse(BaseModel):
    question: str
    parsed: dict[str, Any]
    source_tables: list[str]
    rows: list[dict[str, Any]]
    agent_facts: list[dict[str, Any]] = Field(
        default_factory=list,
        description="AgentFinancialFact contract rows with stable keys; missing values are null.",
    )
    row_count: int


app = FastAPI(title="PDF2MD Financial Query API", version="1.0.0")


INDEX_HTML = r"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>PDF2MD 财务查询</title>
  <style>
    :root {
      --bg: #eef3f8;
      --panel: #ffffff;
      --ink: #17202a;
      --muted: #65758b;
      --line: #d8e0ea;
      --accent: #0f766e;
      --accent-2: #155e75;
      --soft: #e7f5f2;
      --danger: #b42318;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(15,118,110,.18), transparent 32rem),
        linear-gradient(135deg, #f8fbfd 0%, var(--bg) 52%, #e8edf4 100%);
      font-family: "Aptos", "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    }
    main {
      max-width: 1180px;
      margin: 0 auto;
      padding: 34px 22px 48px;
    }
    header {
      display: flex;
      justify-content: space-between;
      gap: 18px;
      align-items: flex-end;
      margin-bottom: 18px;
    }
    h1 {
      margin: 0 0 8px;
      font-size: 28px;
      letter-spacing: 0;
    }
    p {
      margin: 0;
      color: var(--muted);
      line-height: 1.7;
    }
    .status {
      padding: 9px 12px;
      border: 1px solid rgba(15,118,110,.28);
      background: var(--soft);
      color: var(--accent);
      border-radius: 8px;
      white-space: nowrap;
      font-size: 14px;
    }
    .panel {
      background: rgba(255,255,255,.88);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 16px 38px rgba(32,50,70,.10);
      padding: 18px;
    }
    .query-row {
      display: grid;
      grid-template-columns: 1fr 100px 130px;
      gap: 10px;
      align-items: center;
    }
    input[type="text"], input[type="number"] {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 13px 14px;
      font-size: 15px;
      background: #fff;
      color: var(--ink);
    }
    button {
      border: 0;
      border-radius: 7px;
      background: var(--accent);
      color: #fff;
      padding: 13px 16px;
      font-size: 15px;
      cursor: pointer;
    }
    button:hover { background: var(--accent-2); }
    .options {
      margin-top: 12px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }
    label {
      color: var(--muted);
      font-size: 14px;
      display: inline-flex;
      gap: 7px;
      align-items: center;
    }
    .chips {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .chip {
      background: #f4f7fa;
      color: #314154;
      border: 1px solid var(--line);
      padding: 7px 10px;
      border-radius: 999px;
      font-size: 13px;
      cursor: pointer;
    }
    .summary {
      margin: 18px 0 10px;
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }
    .summary div {
      background: rgba(255,255,255,.72);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
    }
    .summary strong {
      display: block;
      font-size: 20px;
      margin-bottom: 3px;
    }
    .table-wrap {
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
    }
    table {
      width: 100%;
      min-width: 980px;
      border-collapse: collapse;
      font-size: 13px;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 10px 11px;
      text-align: left;
      vertical-align: top;
      white-space: nowrap;
    }
    th {
      background: #f6f8fb;
      color: #42526a;
      position: sticky;
      top: 0;
      z-index: 1;
    }
    .error {
      margin-top: 14px;
      color: var(--danger);
      white-space: pre-wrap;
    }
    pre {
      margin: 12px 0 0;
      padding: 12px;
      border-radius: 8px;
      background: #111827;
      color: #e5e7eb;
      overflow: auto;
      max-height: 360px;
    }
    @media (max-width: 760px) {
      header { display: block; }
      .status { display: inline-block; margin-top: 12px; }
      .query-row { grid-template-columns: 1fr; }
      .summary { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>PDF2MD 财务查询</h1>
        <p>输入自然语言，查询三大表或指标值。结果会标出数据源表。</p>
      </div>
      <div class="status">API /query 可用</div>
    </header>

    <section class="panel">
      <div class="query-row">
        <input id="question" type="text" value="查信达2025年营收" />
        <input id="limit" type="number" min="1" max="1000" value="10" />
        <button id="run">查询</button>
      </div>
      <div class="options">
        <label><input id="useHermes" type="checkbox" /> 使用 Hermes 解析</label>
        <div class="chips">
          <span class="chip">查询信达证券2025年利润表数据</span>
          <span class="chip">查信达2025年营收</span>
          <span class="chip">给我看比亚迪2025-12-31总资产</span>
          <span class="chip">查询华安证券2025年现金流量表</span>
        </div>
      </div>
    </section>

    <section class="summary">
      <div><strong id="rowCount">0</strong><span>返回行数</span></div>
      <div><strong id="sourceCount">0</strong><span>命中数据源表</span></div>
      <div><strong id="resolvedCompany">-</strong><span>识别公司</span></div>
    </section>

    <section class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>source_table</th>
            <th>stock_name</th>
            <th>period_key</th>
            <th>statement_id</th>
            <th>item/metric</th>
            <th>value</th>
            <th>raw_value</th>
            <th>unit</th>
            <th>source_table_index</th>
          </tr>
        </thead>
        <tbody id="rows"></tbody>
      </table>
    </section>

    <div id="error" class="error"></div>
    <pre id="raw">{}</pre>
  </main>
  <script>
    const question = document.querySelector("#question");
    const limit = document.querySelector("#limit");
    const useHermes = document.querySelector("#useHermes");
    const rowsEl = document.querySelector("#rows");
    const errorEl = document.querySelector("#error");
    const rawEl = document.querySelector("#raw");
    const rowCountEl = document.querySelector("#rowCount");
    const sourceCountEl = document.querySelector("#sourceCount");
    const resolvedCompanyEl = document.querySelector("#resolvedCompany");

    function cell(value) {
      const td = document.createElement("td");
      td.textContent = value ?? "";
      return td;
    }

    function render(data) {
      rowCountEl.textContent = data.row_count ?? 0;
      sourceCountEl.textContent = (data.source_tables || []).length;
      resolvedCompanyEl.textContent = data.parsed?.resolved_stock_name || "-";
      rowsEl.innerHTML = "";
      for (const row of data.rows || []) {
        const metric = row.item_name || row.metric_payload?.item_name || row.metric_payload?.metric_name || row.metric_key || "";
        const tr = document.createElement("tr");
        tr.append(
          cell(row.source_table),
          cell(row.stock_name),
          cell(row.period_key),
          cell(row.statement_id),
          cell(metric),
          cell(row.value),
          cell(row.raw_value),
          cell(row.unit || row.metric_payload?.unit),
          cell(row.source_table_index || row.metric_payload?.source?.table_index)
        );
        rowsEl.appendChild(tr);
      }
      rawEl.textContent = JSON.stringify(data, null, 2);
    }

    async function runQuery() {
      errorEl.textContent = "";
      rowsEl.innerHTML = "";
      try {
        const response = await fetch("/query", {
          method: "POST",
          headers: {"content-type": "application/json"},
          body: JSON.stringify({
            question: question.value,
            use_hermes: useHermes.checked,
            limit: Number(limit.value || 10)
          })
        });
        const rawText = await response.text();
        let data = null;
        try {
          data = rawText ? JSON.parse(rawText) : null;
        } catch (parseError) {
          if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${rawText || "服务器返回了非 JSON 错误"}`);
          }
          throw new Error(`响应不是有效 JSON: ${rawText || "(empty response)"}`);
        }
        if (!response.ok) {
          const detail = data?.detail
            ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail, null, 2))
            : rawText;
          throw new Error(`HTTP ${response.status}: ${detail}`);
        }
        render(data);
      } catch (error) {
        errorEl.textContent = String(error.message || error);
      }
    }

    document.querySelector("#run").addEventListener("click", runQuery);
    question.addEventListener("keydown", (event) => {
      if (event.key === "Enter") runQuery();
    });
    document.querySelectorAll(".chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        question.value = chip.textContent;
        runQuery();
      });
    });
    runQuery();
  </script>
</body>
</html>
"""


def normalize_json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: normalize_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_json(item) for item in value]
    return value


def _first_present(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def agent_fact_from_row(row: dict[str, Any], *, source_type: str | None = None) -> dict[str, Any]:
    source_table = str(row.get("source_table") or "")
    schema = source_table.split(".", 1)[0] if "." in source_table else row.get("schema")
    period = _first_present(row, "period_key", "period_end", "filing_period_end", "report_period", "report_year", "fiscal_year")
    metric_payload = row.get("metric_payload") if isinstance(row.get("metric_payload"), dict) else {}
    source_payload = metric_payload.get("source") if isinstance(metric_payload.get("source"), dict) else {}
    fact = {
        "market": row.get("market") or _market_from_schema(schema),
        "schema": schema,
        "company_id": row.get("company_id"),
        "filing_id": row.get("filing_id"),
        "parse_run_id": row.get("parse_run_id"),
        "metric_name": _first_present(row, "metric_name", "item_name", "item_name_raw", "metric_key", "label", "local_name")
        or metric_payload.get("metric_name")
        or metric_payload.get("item_name"),
        "canonical_name": _first_present(row, "canonical_name", "canonical_label") or metric_payload.get("canonical_name"),
        "period": period,
        "value": _first_present(row, "value", "converted_value") if _first_present(row, "value", "converted_value") is not None else metric_payload.get("value"),
        "raw_value": row.get("raw_value") if row.get("raw_value") is not None else metric_payload.get("raw_value"),
        "unit": _first_present(row, "unit", "raw_unit", "unit_standardized") or metric_payload.get("unit"),
        "currency": _first_present(
            row,
            "currency",
            "fact_currency",
            "reporting_currency",
            "presentation_currency",
            "converted_currency",
        )
        or metric_payload.get("currency"),
        "source_page": _first_present(row, "source_page_number", "evidence_page_number") or source_payload.get("page_number"),
        "table_index": _first_present(row, "source_table_index", "evidence_table_index") or source_payload.get("table_index"),
        "bbox": _first_present(row, "bbox", "evidence_bbox", "source_bbox") or source_payload.get("bbox"),
        "evidence_id": row.get("evidence_id") or metric_payload.get("evidence_id"),
        "quote": _first_present(row, "quote_text", "quote") or metric_payload.get("quote_text"),
        "source_url": row.get("source_url") or metric_payload.get("source_url"),
        "wiki_report_path": _first_present(row, "wiki_report_path", "wiki_package_path"),
        "source_type": source_type or ("postgresql_agent_view" if source_table.endswith(".v_agent_financial_facts") else "postgresql"),
    }
    stable_fact = {
        field: fact.get(field) if fact.get(field) != "" else None
        for field in AGENT_FINANCIAL_FACT_FIELDS
    }
    return normalize_json(stable_fact)


def agent_facts_from_rows(rows: list[dict[str, Any]], *, source_type: str | None = None) -> list[dict[str, Any]]:
    return [agent_fact_from_row(row, source_type=source_type) for row in rows]


def _market_from_schema(schema: Any) -> str | None:
    schema_text = str(schema or "")
    for market, candidate in MARKET_AGENT_SCHEMAS.items():
        if schema_text == candidate:
            return market
    if schema_text == "pdf2md":
        return "CN"
    return None


def hermes_parse(question: str) -> dict[str, Any]:
    if not Path(HERMES_BIN).exists():
        return {}

    prompt = f"""
你是一个财务数据库查询参数解析器。只输出 JSON，不要解释。
把用户问题解析成以下字段：
company_name: 公司简称或公司名，没有则 null
stock_code: 6位股票代码，没有则 null
statement_type: balance_sheet / income_statement / cash_flow_statement / all / null
query_type: table / metric
metric_name: 中文指标名或项目名，没有则 null
canonical_name: 英文标准指标名，没有则 null
year: 四位年份数字，没有则 null
period_key: 期间键，如 2025、2025-12-31，没有则 null

用户问题：{question}
""".strip()
    try:
        completed = subprocess.run(
            [HERMES_BIN, "--oneshot", prompt],
            check=False,
            capture_output=True,
            text=True,
            timeout=HERMES_TIMEOUT_SECONDS,
        )
    except Exception:
        return {}

    text = completed.stdout.strip()
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def fallback_parse(question: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    code_match = re.search(r"(?<!\d)([036]\d{5})(?!\d)", question)
    if code_match:
        parsed["stock_code"] = code_match.group(1)

    date_match = re.search(r"(20\d{2}-\d{2}-\d{2})", question)
    if date_match:
        parsed["period_key"] = date_match.group(1)

    company_match = re.search(
        r"(?:查询|查|给我看|看一下|看看)?\s*([\u4e00-\u9fffA-Za-z0-9]{2,30}?)(?=20\d{2}|利润表|资产负债表|现金流量表|现金流表|营收|营业收入|总资产|净利润|数据)",
        question,
    )
    if company_match:
        company_name = company_match.group(1).strip()
        company_name = re.sub(r"^(查询|查|给我看|看一下|看看)", "", company_name).strip()
        if company_name and company_name not in {"利润表", "资产负债表", "现金流量表", "现金流表"}:
            parsed["company_name"] = company_name
    else:
        bare_company = re.sub(r"^(查询|查|给我看|看一下|看看)\s*", "", question).strip()
        if re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9]{2,30}", bare_company):
            parsed["company_name"] = bare_company
            parsed["query_type"] = "company_all"

    year_match = re.search(r"(20\d{2})\s*年?", question)
    if year_match:
        parsed["year"] = int(year_match.group(1))

    if any(term in question.lower() for term in ("母公司", "母表", "母公司口径", "parent", "parent company")):
        parsed["statement_scope"] = "parent_company"
    elif any(term in question for term in ("合并", "合并口径", "合并报表")):
        parsed["statement_scope"] = "consolidated"
    else:
        parsed["statement_scope"] = "consolidated"

    for statement_type, aliases in STATEMENT_ALIASES.items():
        if any(alias.lower() in question.lower() for alias in aliases):
            parsed["statement_type"] = statement_type
            break

    metric_terms = []
    for canonical, aliases in METRIC_ALIASES.items():
        if any(alias in question for alias in aliases):
            parsed["metric_name"] = canonical
            parsed["query_type"] = "metric"
            metric_terms = list(dict.fromkeys((canonical, *aliases)))
            break
    if not metric_terms:
        for hint in METRIC_HINTS:
            if hint in question:
                parsed["metric_name"] = hint
                parsed["query_type"] = "metric"
                metric_terms = [hint]
                break
    if metric_terms:
        parsed["metric_terms"] = metric_terms

    if "指标" in question and "query_type" not in parsed:
        parsed["query_type"] = "metric"
    if "query_type" not in parsed:
        parsed["query_type"] = "table" if parsed.get("statement_type") else "metric"
    return parsed


def merge_parse(question: str, use_hermes: bool) -> dict[str, Any]:
    fallback = fallback_parse(question)
    hermes = hermes_parse(question) if use_hermes else {}
    merged = {**fallback, **{k: v for k, v in hermes.items() if v not in (None, "", [])}}
    metric_name = merged.get("metric_name")
    if metric_name in CANONICAL_ALIASES:
        metric_name = CANONICAL_ALIASES[metric_name]
        merged["metric_name"] = metric_name
    if metric_name and "metric_terms" not in merged:
        aliases = METRIC_ALIASES.get(str(metric_name), (metric_name,))
        merged["metric_terms"] = list(dict.fromkeys((metric_name, *aliases)))
    if "query_type" not in merged:
        merged["query_type"] = "metric" if merged.get("metric_name") or merged.get("canonical_name") else "table"
    return merged


def compact_company_text(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", value or "").lower()


def iter_financial_companies(cur) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table in (
        SOURCE_TABLES["balance_sheet"],
        SOURCE_TABLES["income_statement"],
        SOURCE_TABLES["cash_flow_statement"],
    ):
        cur.execute(
            f"""
            SELECT
                company_id, stock_code, stock_name, exchange,
                count(*) AS row_count
            FROM {table}
            WHERE stock_name IS NOT NULL AND stock_name <> ''
            GROUP BY company_id, stock_code, stock_name, exchange
            ORDER BY count(*) DESC, length(stock_name) DESC
            """
        )
        rows.extend(dict(row) for row in cur.fetchall())

    best_by_name: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = row.get("stock_name")
        if not name:
            continue
        current = best_by_name.get(name)
        if not current or row.get("row_count", 0) > current.get("row_count", 0):
            best_by_name[name] = row
    return sorted(best_by_name.values(), key=lambda item: len(item.get("stock_name") or ""), reverse=True)


def resolve_company(cur, parsed: dict[str, Any], question: str) -> dict[str, Any]:
    stock_code = parsed.get("stock_code")
    if stock_code:
        cur.execute(
            """
            SELECT company_id, stock_code, stock_name, exchange
            FROM pdf2md.companies
            WHERE stock_code = %s
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (stock_code,),
        )
        row = cur.fetchone()
        if row:
            return dict(row)

    company_name = parsed.get("company_name")
    if company_name:
        cur.execute(
            """
            SELECT company_id, stock_code, stock_name, exchange
            FROM pdf2md.companies
            WHERE stock_name ILIKE %s OR aliases::text ILIKE %s
            ORDER BY
                CASE
                    WHEN stock_name = %s THEN 0
                    WHEN stock_name ILIKE %s THEN 1
                    ELSE 2
                END,
                length(stock_name),
                updated_at DESC
            LIMIT 1
            """,
            (f"%{company_name}%", f"%{company_name}%", company_name, f"%{company_name}%"),
        )
        row = cur.fetchone()
        if row:
            return dict(row)

        compact_requested = compact_company_text(str(company_name))
        for candidate in iter_financial_companies(cur):
            candidate_name = candidate.get("stock_name") or ""
            compact_candidate = compact_company_text(candidate_name)
            if not compact_candidate:
                continue
            if compact_candidate in compact_requested or compact_requested in compact_candidate:
                return candidate
        return {}

    cur.execute(
        """
        SELECT company_id, stock_code, stock_name, exchange
        FROM pdf2md.companies
        ORDER BY length(stock_name) DESC
        """
    )
    for row in cur.fetchall():
        candidate = dict(row)
        if candidate["stock_name"] and candidate["stock_name"] in question:
            return candidate
    cur.execute(
        """
        SELECT company_id, stock_code, stock_name, exchange
        FROM pdf2md.companies
        ORDER BY length(stock_name) DESC
        """
    )
    compact_question = compact_company_text(question)
    best: dict[str, Any] = {}
    best_score = 0
    for row in cur.fetchall():
        candidate = dict(row)
        name = compact_company_text(candidate.get("stock_name") or "")
        if not name:
            continue
        score = 0
        if name and name in compact_question:
            score = len(name)
        else:
            for size in range(min(len(name), 4), 1, -1):
                if any(name[start : start + size] in compact_question for start in range(0, len(name) - size + 1)):
                    score = size
                    break
        if score > best_score:
            best = candidate
            best_score = score
    if best_score >= 2:
        return best

    financial_companies = iter_financial_companies(cur)
    for candidate in financial_companies:
        if candidate["stock_name"] and candidate["stock_name"] in question:
            return candidate

    compact_question = compact_company_text(question)
    best = {}
    best_score = 0
    for candidate in financial_companies:
        name = compact_company_text(candidate.get("stock_name") or "")
        if not name:
            continue
        score = len(name) if name in compact_question else 0
        if not score:
            for size in range(min(len(name), 4), 1, -1):
                if any(name[start : start + size] in compact_question for start in range(0, len(name) - size + 1)):
                    score = size
                    break
        if score > best_score:
            best = candidate
            best_score = score
    if best_score >= 2:
        return best
    return {}


def period_clause(statement_type: str | None, parsed: dict[str, Any]) -> tuple[str, list[Any]]:
    period_key = parsed.get("period_key")
    year = parsed.get("year")
    if period_key:
        return " AND period_key = %s", [str(period_key)]
    if year:
        if statement_type == "balance_sheet":
            return " AND period_key LIKE %s", [f"{year}%"]
        return " AND (period_key = %s OR period_key LIKE %s)", [str(year), f"{year}-%"]
    return "", []


def company_clause(company: dict[str, Any]) -> tuple[str, list[Any]]:
    if company.get("company_id"):
        return " AND company_id = %s", [company["company_id"]]
    if company.get("stock_code"):
        return " AND stock_code = %s", [company["stock_code"]]
    if company.get("stock_name"):
        return " AND stock_name = %s", [company["stock_name"]]
    return "", []


def scope_clause(parsed: dict[str, Any]) -> tuple[str, list[Any]]:
    scope = parsed.get("statement_scope") or "consolidated"
    if scope in ("consolidated", "parent_company"):
        return " AND scope = %s", [scope]
    return "", []


def require_company_match(parsed: dict[str, Any], company: dict[str, Any]) -> None:
    requested = parsed.get("company_name") or parsed.get("stock_code")
    if requested and not company:
        raise HTTPException(status_code=404, detail=f"未找到公司 {requested} 的入库财务数据，请先导入对应 document_full.json。")


def query_statement_table(cur, parsed: dict[str, Any], company: dict[str, Any], limit: int) -> tuple[list[str], list[dict[str, Any]]]:
    statement_type = parsed.get("statement_type")
    if statement_type not in ("balance_sheet", "income_statement", "cash_flow_statement"):
        raise HTTPException(status_code=400, detail="请指定三大表之一：资产负债表、利润表、现金流量表。")

    table = SOURCE_TABLES[statement_type]
    where = " WHERE 1=1"
    params: list[Any] = []
    extra, extra_params = company_clause(company)
    where += extra
    params += extra_params
    extra, extra_params = period_clause(statement_type, parsed)
    where += extra
    params += extra_params
    extra, extra_params = scope_clause(parsed)
    where += extra
    params += extra_params
    params.append(limit)

    sql = f"""
        SELECT
            '{table}' AS source_table,
            task_id, company_id, stock_code, stock_name, exchange,
            report_year, report_period, statement_id, period_key, item_name,
            canonical_name, value, raw_value, unit, currency,
            source_page_number, source_table_index
        FROM {table}
        {where}
        ORDER BY task_id, statement_id, item_index, period_key
        LIMIT %s
    """
    cur.execute(sql, params)
    return [table], [normalize_json(dict(row)) for row in cur.fetchall()]


def query_company_all_metrics(cur, parsed: dict[str, Any], company: dict[str, Any], limit: int) -> tuple[list[str], list[dict[str, Any]]]:
    tables = [
        SOURCE_TABLES["balance_sheet"],
        SOURCE_TABLES["income_statement"],
        SOURCE_TABLES["cash_flow_statement"],
    ]
    rows: list[dict[str, Any]] = []
    used_tables: list[str] = []
    per_table_limit = max(limit, 50)
    for table in tables:
        where = " WHERE 1=1"
        params: list[Any] = []
        extra, extra_params = company_clause(company)
        where += extra
        params += extra_params
        extra, extra_params = scope_clause(parsed)
        where += extra
        params += extra_params
        if parsed.get("year"):
            where += " AND report_year = %s"
            params.append(parsed["year"])
        params.append(per_table_limit)
        cur.execute(
            f"""
            SELECT
                '{table}' AS source_table,
                task_id, company_id, stock_code, stock_name, exchange,
                report_year, report_period, statement_id, period_key, item_name,
                canonical_name, value, raw_value, unit, currency,
                source_page_number, source_table_index
            FROM {table}
            {where}
            ORDER BY report_year DESC NULLS LAST, period_key DESC NULLS LAST, statement_id, item_index
            LIMIT %s
            """,
            params,
        )
        batch = [normalize_json(dict(row)) for row in cur.fetchall()]
        if batch:
            used_tables.append(table)
            rows.extend(batch)
    return used_tables, dedupe_response_rows(rows, limit)


def infer_metric_from_database(cur, parsed: dict[str, Any], company: dict[str, Any], question: str) -> None:
    if parsed.get("metric_name") or parsed.get("canonical_name"):
        return

    wanted = parsed.get("statement_type")
    if wanted in ("balance_sheet", "income_statement", "cash_flow_statement"):
        tables = [SOURCE_TABLES[wanted]]
        types = [wanted]
    else:
        types = ["balance_sheet", "income_statement", "cash_flow_statement"]
        tables = [SOURCE_TABLES[item] for item in types]

    candidates: list[dict[str, Any]] = []
    for statement_type, table in zip(types, tables):
        where = " WHERE item_name IS NOT NULL"
        params: list[Any] = []
        extra, extra_params = company_clause(company)
        where += extra
        params += extra_params
        extra, extra_params = period_clause(statement_type, parsed)
        where += extra
        params += extra_params
        extra, extra_params = scope_clause(parsed)
        where += extra
        params += extra_params
        cur.execute(
            f"""
            SELECT item_name, canonical_name
            FROM {table}
            {where}
            GROUP BY item_name, canonical_name
            ORDER BY max(length(item_name)) DESC
            """,
            params,
        )
        candidates.extend(dict(row) for row in cur.fetchall())

    compact_question = compact_company_text(question)
    best: dict[str, Any] = {}
    best_score = 0
    for candidate in candidates:
        names = [candidate.get("item_name"), candidate.get("canonical_name")]
        for name in names:
            if not name:
                continue
            compact_name = compact_company_text(str(name))
            if not compact_name:
                continue
            score = len(compact_name) if compact_name in compact_question else 0
            if score > best_score:
                best = candidate
                best_score = score

    if best and best_score >= 2:
        parsed["metric_name"] = best.get("item_name")
        if best.get("canonical_name"):
            parsed["canonical_name"] = best.get("canonical_name")
        parsed["metric_terms"] = list(dict.fromkeys([item for item in (best.get("item_name"), best.get("canonical_name")) if item]))
        parsed["query_type"] = "metric"


def metric_where(metric: str | None, canonical: str | None, metric_terms: list[str] | None = None) -> tuple[str, list[Any]]:
    parts = []
    params: list[Any] = []
    terms = list(dict.fromkeys([item for item in (metric_terms or []) if item]))
    if metric and metric not in terms:
        terms.insert(0, metric)
    for term in terms:
        parts.append("(item_name ILIKE %s OR canonical_name ILIKE %s)")
        params += [f"%{term}%", f"%{term}%"]
    if canonical:
        parts.append("canonical_name = %s")
        params.append(canonical)
    if not parts:
        raise HTTPException(status_code=400, detail="请在问题中指定指标名，例如 营业收入、净利润、基本每股收益。")
    return " AND (" + " OR ".join(parts) + ")", params


def query_metric_from_split_tables(cur, parsed: dict[str, Any], company: dict[str, Any], limit: int) -> tuple[list[str], list[dict[str, Any]]]:
    wanted = parsed.get("statement_type")
    if wanted in ("balance_sheet", "income_statement", "cash_flow_statement"):
        tables = [SOURCE_TABLES[wanted]]
        types = [wanted]
    else:
        types = ["balance_sheet", "income_statement", "cash_flow_statement"]
        tables = [SOURCE_TABLES[item] for item in types]

    rows: list[dict[str, Any]] = []
    used_tables: list[str] = []
    for statement_type, table in zip(types, tables):
        where = " WHERE 1=1"
        params: list[Any] = []
        extra, extra_params = company_clause(company)
        where += extra
        params += extra_params
        extra, extra_params = period_clause(statement_type, parsed)
        where += extra
        params += extra_params
        extra, extra_params = scope_clause(parsed)
        where += extra
        params += extra_params
        extra, extra_params = metric_where(parsed.get("metric_name"), parsed.get("canonical_name"), parsed.get("metric_terms"))
        where += extra
        params += extra_params
        params.append(limit)
        cur.execute(
            f"""
            SELECT
                '{table}' AS source_table,
                task_id, company_id, stock_code, stock_name, exchange,
                report_year, report_period, statement_id, period_key, item_name,
                canonical_name, value, raw_value, unit, currency,
                source_page_number, source_table_index
            FROM {table}
            {where}
            ORDER BY task_id, statement_id, item_index, period_key
            LIMIT %s
            """,
            params,
        )
        batch = [normalize_json(dict(row)) for row in cur.fetchall()]
        if batch:
            used_tables.append(table)
            rows.extend(batch)
    return used_tables, rows[:limit]


def matches_metric_payload(payload: Any, metric: str | None, canonical: str | None, metric_terms: list[str] | None = None) -> bool:
    if not isinstance(payload, dict):
        return False
    haystack = " ".join(str(payload.get(key) or "") for key in ("metric_name", "item_name", "canonical_name"))
    if canonical and payload.get("canonical_name") == canonical:
        return True
    terms = list(dict.fromkeys([item for item in (metric_terms or []) if item]))
    if metric and metric not in terms:
        terms.insert(0, metric)
    return any(term in haystack for term in terms)


def query_metric_from_wide(cur, parsed: dict[str, Any], company: dict[str, Any], limit: int) -> tuple[list[str], list[dict[str, Any]]]:
    table = SOURCE_TABLES["wide"]
    where = " WHERE 1=1"
    params: list[Any] = []
    extra, extra_params = company_clause(company)
    where += extra
    params += extra_params
    extra, extra_params = period_clause(None, parsed)
    where += extra
    params += extra_params
    params.append(max(limit, 50))
    cur.execute(
        f"""
        SELECT
            task_id, company_id, stock_code, stock_name, exchange,
            report_year, report_period, period_key,
            balance_sheet, income_statement, cash_flow_statement, key_metrics, all_metrics
        FROM {table}
        {where}
        ORDER BY task_id, period_key
        LIMIT %s
        """,
        params,
    )
    rows = []
    metric = parsed.get("metric_name")
    canonical = parsed.get("canonical_name")
    metric_terms = parsed.get("metric_terms") or []
    for row in cur.fetchall():
        base = normalize_json(dict(row))
        all_metrics = base.pop("all_metrics") or {}
        for key, payload in all_metrics.items():
            payload_scope = payload.get("scope") if isinstance(payload, dict) else None
            if payload_scope != (parsed.get("statement_scope") or "consolidated"):
                continue
            if matches_metric_payload(payload, metric, canonical, metric_terms):
                rows.append({
                    "source_table": table,
                    **{k: v for k, v in base.items() if k not in ("balance_sheet", "income_statement", "cash_flow_statement", "key_metrics")},
                    "metric_key": key,
                    "metric_payload": normalize_json(payload),
                    "value": normalize_json(payload.get("value") if isinstance(payload, dict) else None),
                    "raw_value": normalize_json(payload.get("raw_value") if isinstance(payload, dict) else None),
                })
                if len(rows) >= limit:
                    return [table], rows
    return ([table] if rows else []), rows


def dedupe_response_rows(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    unique: list[dict[str, Any]] = []
    for row in rows:
        metric_payload = row.get("metric_payload") if isinstance(row.get("metric_payload"), dict) else {}
        key = (
            row.get("stock_code") or row.get("stock_name") or row.get("company_id"),
            row.get("report_year"),
            row.get("report_period"),
            row.get("period_key"),
            row.get("statement_id") or metric_payload.get("statement_id"),
            row.get("item_name") or metric_payload.get("item_name") or row.get("metric_key"),
            row.get("canonical_name") or metric_payload.get("canonical_name"),
            row.get("source_table_index") or metric_payload.get("source", {}).get("table_index"),
            row.get("source_page_number") or metric_payload.get("source", {}).get("page_number"),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
        if len(unique) >= limit:
            break
    return unique


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_HTML


@app.get("/ui", response_class=HTMLResponse)
def new_ui() -> str:
    ui_path = Path(__file__).with_name("financial_query_ui.html")
    return ui_path.read_text(encoding="utf-8")


@app.post("/query", response_model=QueryResponse)
def query_financial_data(request: QueryRequest) -> QueryResponse:
    parsed = merge_parse(request.question, request.use_hermes)
    try:
        market_result = query_market_agent_view_result(request.question, parsed, None, limit=request.limit)
    except Exception as exc:
        logger.info("market agent view query failed; falling back to legacy pdf2md query", exc_info=exc)
        market_result = None
    if market_result:
        rows = market_result.get("rows") or []
        agent_facts = market_result.get("agent_facts") or agent_facts_from_rows(rows, source_type="postgresql_agent_view")
        return QueryResponse(
            question=request.question,
            parsed=normalize_json(market_result.get("parsed") or parsed),
            source_tables=list(market_result.get("source_tables") or []),
            rows=normalize_json(rows),
            agent_facts=normalize_json(agent_facts),
            row_count=len(rows),
        )
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                company = resolve_company(cur, parsed, request.question)
                require_company_match(parsed, company)
                if company:
                    parsed.update({f"resolved_{key}": value for key, value in company.items()})
                if parsed.get("query_type") == "company_all":
                    source_tables, rows = query_company_all_metrics(cur, parsed, company, request.limit)
                else:
                    infer_metric_from_database(cur, parsed, company, request.question)

                    if parsed.get("query_type") == "table":
                        source_tables, rows = query_statement_table(cur, parsed, company, request.limit)
                    else:
                        source_tables, rows = query_metric_from_split_tables(cur, parsed, company, request.limit)
                        if not rows:
                            wide_tables, wide_rows = query_metric_from_wide(cur, parsed, company, request.limit)
                            for item in wide_tables:
                                if item not in source_tables:
                                    source_tables.append(item)
                            rows.extend(wide_rows)
                        rows = dedupe_response_rows(rows, request.limit)
    except psycopg.OperationalError as exc:
        logger.info("legacy pdf2md PostgreSQL query failed", exc_info=exc)
        raise HTTPException(status_code=503, detail="PostgreSQL unavailable") from exc

    return QueryResponse(
        question=request.question,
        parsed=normalize_json(parsed),
        source_tables=source_tables,
        rows=rows,
        agent_facts=agent_facts_from_rows(rows),
        row_count=len(rows),
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "18188")))
