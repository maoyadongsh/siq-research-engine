#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CASES_PATH = REPO_ROOT / "eval_datasets" / "market_document_full_postgres" / "cases.json"
DEFAULT_OUTPUT_PATH = REPO_ROOT / "eval_datasets" / "market_document_full_postgres" / "backtest_report.json"
DEFAULT_MARKDOWN_PATH = REPO_ROOT / "docs" / "reports" / "market-document-full-postgres-backtest.md"
IMPORTS_DIR = REPO_ROOT / "db" / "imports"
MARKET_DATABASES = {
    "HK": "siq_hk",
    "JP": "siq_jp",
    "KR": "siq_kr",
    "EU": "siq_eu",
    "US": "siq_us",
}
MARKET_SCHEMAS = {
    "HK": "pdf2md_hk",
    "JP": "edinet_jp",
    "KR": "dart_kr",
    "EU": "eu_ifrs",
    "US": "sec_us",
}
DB_COUNT_TABLES = {
    "parse_runs": ("parse_runs",),
    "facts": ("financial_statement_items", "financial_facts", "xbrl_facts_raw"),
    "tables": ("document_tables", "html_tables", "pdf_tables"),
    "chunks": ("document_chunks", "retrieval_chunks"),
    "evidence": ("evidence_citations",),
}
COMMON_CORE_METRICS = {
    "revenue",
    "gross_profit",
    "operating_profit",
    "profit_before_tax",
    "net_profit",
    "total_assets",
    "total_liabilities",
    "total_equity",
    "operating_cash_flow",
    "capex",
    "cash_and_equivalents",
    "cash_and_cash_equivalents",
    "basic_eps",
    "diluted_eps",
    "roe",
    "gross_margin",
    "current_assets",
}


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


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def safe_sql_ident(value: str) -> str:
    if not value.replace("_", "").isalnum() or value[0].isdigit():
        raise ValueError(f"Unsafe SQL identifier: {value!r}")
    return value


def database_url_for_market(market: str, explicit_url: str | None = None) -> str:
    if explicit_url:
        return explicit_url
    env_url = os.environ.get(f"SIQ_{market}_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if env_url:
        return env_url
    database = os.environ.get(f"SIQ_{market}_PGDATABASE") or MARKET_DATABASES[market]
    host = os.environ.get("SIQ_PGHOST") or os.environ.get("PGHOST") or "127.0.0.1"
    port = os.environ.get("SIQ_PGPORT") or os.environ.get("PGPORT") or "15432"
    user = os.environ.get("SIQ_PGUSER") or os.environ.get("PGUSER") or "postgres"
    password = os.environ.get("SIQ_PGPASSWORD") or os.environ.get("PGPASSWORD") or ""
    auth = f"{user}:{password}" if password else user
    return f"postgresql://{auth}@{host}:{port}/{database}"


def table_exists(conn: Any, schema: str, table: str) -> bool:
    row = conn.execute(
        """
        select 1
        from information_schema.tables
        where table_schema = %s
          and table_name = %s
          and table_type = 'BASE TABLE'
        """,
        (schema, table),
    ).fetchone()
    return bool(row)


def table_columns(conn: Any, schema: str, table: str) -> set[str]:
    if not table_exists(conn, schema, table):
        return set()
    rows = conn.execute(
        """
        select column_name
        from information_schema.columns
        where table_schema = %s and table_name = %s
        """,
        (schema, table),
    ).fetchall()
    return {str(row[0]) for row in rows}


def db_count(conn: Any, schema: str, table: str, where_sql: str, params: tuple[Any, ...]) -> int:
    if not table_exists(conn, schema, table):
        return 0
    schema_sql = safe_sql_ident(schema)
    table_sql = safe_sql_ident(table)
    row = conn.execute(f"select count(*) from {schema_sql}.{table_sql} where {where_sql}", params).fetchone()
    return int(row[0] if row else 0)


def stable_json_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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


def _select_existing_columns(
    conn: Any,
    schema: str,
    table: str,
    requested_columns: tuple[str, ...],
    where_sql: str,
    params: tuple[Any, ...],
    order_columns: tuple[str, ...],
) -> list[dict[str, Any]]:
    columns = table_columns(conn, schema, table)
    selected = [column for column in requested_columns if column in columns]
    if not selected:
        return []
    schema_sql = safe_sql_ident(schema)
    table_sql = safe_sql_ident(table)
    order_by = [column for column in order_columns if column in selected]
    order_sql = ", ".join(safe_sql_ident(column) for column in order_by) if order_by else ", ".join(safe_sql_ident(column) for column in selected)
    select_sql = ", ".join(safe_sql_ident(column) for column in selected)
    rows = conn.execute(
        f"select {select_sql} from {schema_sql}.{table_sql} where {where_sql} order by {order_sql}",
        params,
    ).fetchall()
    return [dict(zip(selected, row)) for row in rows]


def db_content_hashes(conn: Any, schema: str, where_sql: str, params: tuple[Any, ...]) -> dict[str, str]:
    statement_rows = _select_existing_columns(
        conn,
        schema,
        "financial_statement_items",
        (
            "statement_type",
            "canonical_name",
            "item_name",
            "concept",
            "period_key",
            "value",
            "raw_value",
            "unit",
            "currency",
            "fact_currency",
            "reporting_currency",
            "source_page_number",
            "source_table_index",
            "evidence_id",
        ),
        where_sql,
        params,
        ("statement_type", "canonical_name", "item_name", "period_key", "concept"),
    )
    xbrl_rows = _select_existing_columns(
        conn,
        schema,
        "xbrl_facts_raw",
        ("concept", "label", "value_text", "value_numeric", "unit", "context_ref", "period_end", "html_anchor", "xpath"),
        where_sql,
        params,
        ("concept", "context_ref", "period_end", "label"),
    )
    hashes = {
        "financial_statement_items": stable_json_hash(statement_rows),
        "xbrl_facts_raw": stable_json_hash(xbrl_rows),
    }
    hashes["critical_content"] = stable_json_hash({"financial_statement_items": statement_rows, "xbrl_facts_raw": xbrl_rows})
    return hashes


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
        result.setdefault("page_number", table.get("page_number"))
        result.setdefault("bbox", table.get("bbox"))
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


def normalize_financial_data_facts(financial_data: dict[str, Any], tables: dict[int, dict[str, Any]]) -> list[NormalizedFact]:
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
                            unit=item.get("unit") or statement_unit,
                            currency=item.get("currency") or statement_currency,
                            fact_currency=item.get("fact_currency") or item.get("currency") or statement_currency,
                            reporting_currency=financial_data.get("reporting_currency") or financial_data.get("presentation_currency") or statement_currency,
                            presentation_currency=financial_data.get("presentation_currency") or financial_data.get("reporting_currency") or statement_currency,
                            scale=item.get("scale") or statement_scale,
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
                    unit=item.get("unit") or statement_unit,
                    currency=item.get("currency") or statement_currency,
                    fact_currency=item.get("fact_currency") or item.get("currency") or statement_currency,
                    reporting_currency=financial_data.get("reporting_currency") or financial_data.get("presentation_currency") or statement_currency,
                    presentation_currency=financial_data.get("presentation_currency") or financial_data.get("reporting_currency") or statement_currency,
                    scale=item.get("scale") or statement_scale,
                    evidence=enriched_evidence(item.get("evidence"), tables),
                )
            )
    return facts


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
    try:
        return Decimal(str(value))
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


def has_reviewable_evidence(evidence: dict[str, Any]) -> bool:
    return any(
        evidence.get(field) is not None
        for field in ("table_index", "page_number", "bbox", "quote_text", "html_anchor", "source_url")
    )


def check_case(case: dict[str, Any], cases_path: Path) -> dict[str, Any]:
    errors: list[str] = []
    document_path = cases_path.parent / str(case["document_full_path"])
    document_full = read_json(document_path)
    identity = document_identity(document_full, fallback_market=case.get("market"))
    facts = normalize_document_facts(document_full)

    for field in ("market", "company_id", "report_year"):
        if field in case and identity.get(field) != case[field]:
            errors.append(f"identity.{field}: expected {case[field]!r}, got {identity.get(field)!r}")
    for field, expected_value in (case.get("expected_identity") or {}).items():
        if identity.get(field) != expected_value:
            errors.append(f"identity.{field}: expected {expected_value!r}, got {identity.get(field)!r}")

    expected_facts = case.get("assertions") or case.get("expected_facts") or []
    for assertion in expected_facts:
        expected = assertion_to_expected_fact(assertion, case)
        fact = find_fact(facts, expected)
        if fact is None:
            errors.append(f"missing fact match: {expected}")
            continue
        tolerance_ratio = expected.get("tolerance_ratio")
        if "value" in expected and tolerance_ratio is not None and not value_within_tolerance(
            fact.value, expected["value"], tolerance_ratio
        ):
            errors.append(
                f"{expected}: value expected {expected['value']!r} within {tolerance_ratio!r}, got {fact.value!r}"
            )
        elif "value" in expected and tolerance_ratio is None and not decimal_equal(fact.value, expected["value"]):
            errors.append(f"{expected}: value expected {expected['value']!r}, got {fact.value!r}")
        for field in ("raw_value", "unit", "currency", "fact_currency", "reporting_currency", "presentation_currency", "scale"):
            if field in expected and getattr(fact, field) != expected[field]:
                errors.append(f"{expected}: {field} expected {expected[field]!r}, got {getattr(fact, field)!r}")
        expected_evidence = expected.get("evidence") if isinstance(expected.get("evidence"), dict) else {}
        observed_evidence = fact.evidence or {}
        if expected.get("required_evidence") is True and not has_reviewable_evidence(observed_evidence):
            errors.append(f"{expected}: expected reviewable evidence, got {observed_evidence!r}")
        for field, expected_value in expected_evidence.items():
            if observed_evidence.get(field) != expected_value:
                errors.append(
                    f"{expected}: evidence.{field} expected {expected_value!r}, got {observed_evidence.get(field)!r}"
                )
    for flag in case.get("expected_flags") or []:
        if flag == "eu_multi_currency_document":
            currencies = {fact.fact_currency or fact.currency for fact in facts if fact.fact_currency or fact.currency}
            if len(currencies) <= 1:
                errors.append(f"expected multi-currency document, got currencies={sorted(currencies)}")
        else:
            errors.append(f"unsupported expected flag: {flag}")
    expected_content_hash = case.get("expected_content_hash")
    observed_content_hash = fact_content_hash(facts)
    if expected_content_hash and observed_content_hash != expected_content_hash:
        errors.append(f"content hash expected {expected_content_hash!r}, got {observed_content_hash!r}")

    return {
        "case_id": case.get("case_id"),
        "market": case.get("market"),
        "passed": not errors,
        "errors": errors,
        "fact_count": len(facts),
        "content_hash": observed_content_hash,
        "document_full_path": str(document_path),
    }


def db_selector_for_case(case: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    if case.get("parse_run_id"):
        return "parse_run_id = %s", (case["parse_run_id"],)
    filing_id = (case.get("expected_identity") or {}).get("filing_id") or case.get("filing_id")
    if filing_id:
        return "filing_id = %s", (filing_id,)
    return "parse_run_id = %s", ("__missing_parse_run__",)


def document_path_for_case(case: dict[str, Any], cases_path: Path) -> Path:
    return (cases_path.parent / str(case["document_full_path"])).resolve()


def _import_case_document_full(
    case: dict[str, Any],
    *,
    cases_path: Path,
    database_url: str | None = None,
    run_ddl: bool = True,
) -> str:
    if str(IMPORTS_DIR) not in sys.path:
        sys.path.insert(0, str(IMPORTS_DIR))
    from import_market_document_full_to_postgres import import_document_full

    return import_document_full(
        document_path_for_case(case, cases_path),
        market=str(case.get("market") or ""),
        database_url_value=database_url,
        run_ddl_flag=run_ddl,
    )


def check_db_case(
    case: dict[str, Any],
    *,
    cases_path: Path,
    database_url: str | None = None,
    import_before_check: bool = False,
    idempotency: bool = False,
) -> dict[str, Any]:
    market = str(case.get("market") or "").upper()
    if market not in MARKET_SCHEMAS:
        return {"case_id": case.get("case_id"), "market": market, "passed": True, "skipped": True, "reason": "legacy_or_unsupported_market"}
    try:
        import psycopg
    except Exception as exc:
        return {"case_id": case.get("case_id"), "market": market, "passed": False, "errors": [f"psycopg unavailable: {exc}"]}

    schema = MARKET_SCHEMAS[market]
    imported_parse_run_id = ""
    errors: list[str] = []
    counts: dict[str, int] = {}
    second_counts: dict[str, int] = {}
    content_hashes: dict[str, str] = {}
    second_content_hashes: dict[str, str] = {}
    try:
        if idempotency and not import_before_check:
            errors.append("--idempotency requires --import-before-db-check")
            raise RuntimeError(errors[-1])
        if import_before_check:
            imported_parse_run_id = _import_case_document_full(case, cases_path=cases_path, database_url=database_url, run_ddl=True)
        if imported_parse_run_id:
            where_sql, params = "parse_run_id = %s", (imported_parse_run_id,)
        else:
            where_sql, params = db_selector_for_case(case)
        with psycopg.connect(database_url_for_market(market, database_url)) as conn:
            for name, tables in DB_COUNT_TABLES.items():
                counts[name] = sum(db_count(conn, schema, table, where_sql, params) for table in tables)
            content_hashes = db_content_hashes(conn, schema, where_sql, params)
        if import_before_check and idempotency:
            imported_again = _import_case_document_full(case, cases_path=cases_path, database_url=database_url, run_ddl=False)
            if imported_again != imported_parse_run_id:
                errors.append(f"idempotency parse_run_id changed: {imported_parse_run_id!r} -> {imported_again!r}")
            with psycopg.connect(database_url_for_market(market, database_url)) as conn:
                for name, tables in DB_COUNT_TABLES.items():
                    second_counts[name] = sum(db_count(conn, schema, table, where_sql, params) for table in tables)
                second_content_hashes = db_content_hashes(conn, schema, where_sql, params)
            if second_counts != counts:
                errors.append(f"idempotency row counts changed: {counts!r} -> {second_counts!r}")
            if second_content_hashes != content_hashes:
                errors.append(f"idempotency content hashes changed: {content_hashes!r} -> {second_content_hashes!r}")
            if counts.get("parse_runs", 0) < 1:
                errors.append("parse_run missing")
            if counts.get("facts", 0) < 1:
                errors.append("financial facts missing")
            if counts.get("chunks", 0) < 1:
                errors.append("retrieval chunks missing")
            if counts.get("tables", 0) < 1:
                errors.append("document tables missing")
            if counts.get("evidence", 0) < 1:
                errors.append("evidence citations missing")
            expected_counts = case.get("expected_row_counts") if isinstance(case.get("expected_row_counts"), dict) else {}
            for key, expected_min in expected_counts.items():
                observed = counts.get(str(key), 0)
                if observed < int(expected_min):
                    errors.append(f"{key}: expected at least {expected_min}, got {observed}")
            expected_hashes = case.get("expected_db_content_hashes") if isinstance(case.get("expected_db_content_hashes"), dict) else {}
            for key, expected_hash in expected_hashes.items():
                observed_hash = content_hashes.get(str(key))
                if observed_hash != expected_hash:
                    errors.append(f"{key}: expected content hash {expected_hash!r}, got {observed_hash!r}")
    except Exception as exc:
        if not errors or str(exc) != errors[-1]:
            errors.append(str(exc))

    return {
        "case_id": case.get("case_id"),
        "market": market,
        "passed": not errors,
        "errors": errors,
        "counts": counts,
        "second_counts": second_counts,
        "content_hashes": content_hashes,
        "second_content_hashes": second_content_hashes,
        "schema": schema,
        "parse_run_id": imported_parse_run_id,
        "imported_before_check": import_before_check,
        "idempotency_checked": idempotency,
        "check_type": "import_idempotency" if import_before_check and idempotency else "import_roundtrip" if import_before_check else "existing_row_check",
    }


def run_cases(
    cases_path: Path = DEFAULT_CASES_PATH,
    *,
    verify_db: bool = False,
    database_url: str | None = None,
    import_before_db_check: bool = False,
    idempotency: bool = False,
) -> dict[str, Any]:
    if idempotency and not import_before_db_check:
        raise ValueError("--idempotency requires --import-before-db-check")
    payload = read_json(cases_path)
    cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(cases, list):
        raise ValueError(f"{cases_path} does not contain a cases list")
    results = [check_case(case, cases_path) for case in cases]
    assertions = [
        assertion
        for case in cases
        for assertion in (case.get("assertions") or case.get("expected_facts") or [])
        if isinstance(assertion, dict)
    ]
    required_evidence_count = sum(1 for assertion in assertions if assertion.get("required_evidence") is True)
    common_core_count = sum(1 for assertion in assertions if assertion.get("canonical_name") in COMMON_CORE_METRICS)
    unit_checked_count = sum(1 for assertion in assertions if "unit" in assertion)
    currency_checked_count = sum(1 for assertion in assertions if "currency" in assertion)
    fact_currency_checked_count = sum(1 for assertion in assertions if "fact_currency" in assertion)
    market_counts: dict[str, int] = {}
    for result in results:
        market = str(result.get("market") or "")
        market_counts[market] = market_counts.get(market, 0) + 1
    db_results = [
        check_db_case(
            case,
            cases_path=cases_path,
            database_url=database_url,
            import_before_check=import_before_db_check,
            idempotency=idempotency,
        )
        for case in cases
    ] if verify_db else []
    db_verified = bool(db_results) and all(result.get("passed") or result.get("skipped") for result in db_results)
    db_passed_count = sum(1 for result in db_results if result.get("passed") and not result.get("skipped"))
    db_case_count = sum(1 for result in db_results if not result.get("skipped"))
    all_passed = all(result["passed"] for result in results) and (db_verified if verify_db else True)
    return {
        "schema_version": "market_document_full_postgres_backtest_results_v1",
        "cases_path": str(cases_path),
        "mode": (
            "document_full_fixture_contract+postgres_import_idempotency"
            if verify_db and import_before_db_check and idempotency
            else "document_full_fixture_contract+postgres_import_roundtrip"
            if verify_db and import_before_db_check
            else "document_full_fixture_contract+postgres_existing_row_check"
            if verify_db
            else "document_full_fixture_contract"
        ),
        "note": "This runner validates document_full row-shape, identity, value, unit/currency, and evidence contracts. With --db it checks PostgreSQL rows; --import-before-db-check first imports document_full fixtures; --idempotency repeats the import and compares row counts. Agent query gates remain explicit follow-up gates.",
        "passed": all_passed,
        "passed_count": sum(1 for result in results if result["passed"]),
        "case_count": len(results),
        "market_counts": market_counts,
        "summary": {
            "assertion_count": len(assertions),
            "common_core_assertion_count": common_core_count,
            "required_evidence_assertion_count": required_evidence_count,
            "evidence_coverage_ratio": 1 if required_evidence_count and all(result["passed"] for result in results) else 0,
            "unit_checked_assertion_count": unit_checked_count,
            "currency_checked_assertion_count": currency_checked_count,
            "fact_currency_checked_assertion_count": fact_currency_checked_count,
            "unit_currency_explainability_ratio": 1 if assertions and unit_checked_count and currency_checked_count and all(result["passed"] for result in results) else 0,
            "postgres_existing_row_check_verified": bool(verify_db and not import_before_db_check and db_verified),
            "postgres_roundtrip_verified": bool(verify_db and import_before_db_check and db_verified),
            "postgres_import_executed": bool(verify_db and import_before_db_check),
            "postgres_idempotency_verified": bool(verify_db and import_before_db_check and idempotency and db_verified),
            "postgres_roundtrip_passed_count": db_passed_count,
            "postgres_roundtrip_case_count": db_case_count,
            "agent_query_verified": False,
        },
        "results": results,
        "db_results": db_results,
    }

def write_report(summary: dict[str, Any], output_path: Path | None, markdown_path: Path | None) -> None:
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if markdown_path is not None:
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(render_markdown_report(summary), encoding="utf-8")


def render_markdown_report(summary: dict[str, Any]) -> str:
    status = "PASS" if summary.get("passed") else "FAIL"
    lines = [
        "# Market Document Full PostgreSQL Backtest",
        "",
        f"Status: **{status}**",
        "",
        "This report is generated by `db/imports/backtests/market_document_full_postgres_backtest.py`.",
        "Current mode validates the `document_full.json` contract fixtures; optional `--db --import-before-db-check --idempotency` performs real PostgreSQL import and row-count stability checks. Agent query gates remain explicit follow-up gates.",
        "",
        "## Summary",
        "",
        f"- Cases: {summary.get('passed_count')}/{summary.get('case_count')} passed",
        f"- Assertions: {summary.get('summary', {}).get('assertion_count', 0)}",
        f"- Common-core assertions: {summary.get('summary', {}).get('common_core_assertion_count', 0)}",
        f"- Required-evidence assertions: {summary.get('summary', {}).get('required_evidence_assertion_count', 0)}",
        f"- Unit checks: {summary.get('summary', {}).get('unit_checked_assertion_count', 0)}",
        f"- Currency checks: {summary.get('summary', {}).get('currency_checked_assertion_count', 0)}",
        f"- PostgreSQL existing-row check verified: {summary.get('summary', {}).get('postgres_existing_row_check_verified')}",
        f"- PostgreSQL roundtrip verified: {summary.get('summary', {}).get('postgres_roundtrip_verified')}",
        f"- PostgreSQL import executed: {summary.get('summary', {}).get('postgres_import_executed')}",
        f"- PostgreSQL idempotency verified: {summary.get('summary', {}).get('postgres_idempotency_verified')}",
        f"- PostgreSQL roundtrip cases: {summary.get('summary', {}).get('postgres_roundtrip_passed_count', 0)}/{summary.get('summary', {}).get('postgres_roundtrip_case_count', 0)}",
        f"- Agent query verified: {summary.get('summary', {}).get('agent_query_verified')}",
        "",
        "## Markets",
        "",
        "| Market | Cases |",
        "| --- | ---: |",
    ]
    for market, count in sorted((summary.get("market_counts") or {}).items()):
        lines.append(f"| {market} | {count} |")
    lines.extend([
        "",
        "## Cases",
        "",
        "| Case | Market | Status | Facts |",
        "| --- | --- | --- | ---: |",
    ])
    for result in summary.get("results") or []:
        case_status = "PASS" if result.get("passed") else "FAIL"
        lines.append(f"| {result.get('case_id')} | {result.get('market')} | {case_status} | {result.get('fact_count')} |")
        for error in result.get("errors") or []:
            lines.append(f"| {result.get('case_id')} error | {result.get('market')} | `{error}` |  |")
    if summary.get("db_results"):
        lines.extend([
            "",
            "## PostgreSQL Roundtrip",
            "",
            "| Case | Market | Status | Counts |",
            "| --- | --- | --- | --- |",
        ])
        for result in summary.get("db_results") or []:
            if result.get("skipped"):
                status = "SKIP"
            else:
                status = "PASS" if result.get("passed") else "FAIL"
            counts = json.dumps(result.get("counts") or {}, ensure_ascii=False, sort_keys=True)
            lines.append(f"| {result.get('case_id')} | {result.get('market')} | {status} | `{counts}` |")
            for error in result.get("errors") or []:
                lines.append(f"| {result.get('case_id')} error | {result.get('market')} | `{error}` |  |")
    lines.extend([
        "",
        "## Remaining Production Gates",
        "",
        "- Run at least three real `document_full.json` samples per market through PostgreSQL.",
        "- Verify idempotent delete-then-insert row counts against each market schema.",
        "- Run fixed Agent questions against the inserted facts and evidence citations.",
        "",
    ])
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run minimal market document_full PostgreSQL backtest cases.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH, help="Path to backtest cases.json.")
    parser.add_argument("--json", action="store_true", help="Print full JSON results.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="Write JSON report.")
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN_PATH, help="Write Markdown report.")
    parser.add_argument("--no-write", action="store_true", help="Do not write report files.")
    parser.add_argument("--db", action="store_true", help="Also verify PostgreSQL row counts for cases with market schemas.")
    parser.add_argument("--import-before-db-check", action="store_true", help="With --db, run the document_full importer before checking row counts.")
    parser.add_argument("--idempotency", action="store_true", help="With --db --import-before-db-check, import each case twice and compare row counts.")
    parser.add_argument("--database-url", default=None, help="Override PostgreSQL URL for --db checks.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.idempotency and not args.import_before_db_check:
        raise SystemExit("--idempotency requires --import-before-db-check")
    summary = run_cases(
        args.cases,
        verify_db=args.db,
        database_url=args.database_url,
        import_before_db_check=args.import_before_db_check,
        idempotency=args.idempotency,
    )
    if not args.no_write:
        write_report(summary, args.output, args.markdown)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(
            f"{'PASS' if summary['passed'] else 'FAIL'} "
            f"{summary['passed_count']}/{summary['case_count']} market document_full PostgreSQL backtest cases"
        )
        for result in summary["results"]:
            status = "PASS" if result["passed"] else "FAIL"
            print(f"{status} {result['case_id']} facts={result['fact_count']}")
            for error in result["errors"]:
                print(f"  - {error}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
