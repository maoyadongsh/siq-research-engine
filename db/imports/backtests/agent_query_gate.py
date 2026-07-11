"""Production Agent-query gates over market PostgreSQL Agent views."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_view_parity_helpers import (
    AGENT_VIEW_REVIEWABLE_EVIDENCE_COLUMNS,
    agent_view_row_errors,
    has_reviewable_agent_view_evidence,
    json_safe,
)
from document_fact_normalizer import assertion_to_expected_fact
from postgres_roundtrip_helpers import relation_columns, safe_sql_ident, scoped_where_for_relation


AGENT_VIEW_SELECT_COLUMNS = (
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
    "statement_type",
    "statement_name",
    "canonical_name",
    "canonical_label",
    "item_name",
    "item_name_raw",
    "local_name",
    "metric_name",
    "metric_name_raw",
    "label",
    "concept",
    "xbrl_tag",
    "taxonomy_tag",
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
    "evidence_bbox",
    "quote_text",
    "source_url",
)
AGENT_VIEW_EXPECTED_VALUE_COLUMNS = {
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
}

DatabaseUrlForMarket = Callable[[str, str | None], str]
DbSelectorForCase = Callable[[dict[str, Any]], tuple[str, tuple[Any, ...]]]


def agent_view_filter_candidates(expected: dict[str, Any]) -> list[tuple[str, tuple[str, ...], Any]]:
    statement_type = expected.get("statement_type")
    if statement_type == "xbrl_fact":
        statement_type = None
    return [
        ("statement_type", ("statement_type",), statement_type),
        ("canonical_name", ("canonical_name", "canonical_label", "metric_name"), expected.get("canonical_name")),
        ("name", ("item_name", "item_name_raw", "local_name", "name", "metric_name", "metric_name_raw"), expected.get("name")),
        ("label", ("label", "item_name", "local_name"), expected.get("label")),
        ("concept", ("concept", "xbrl_tag", "taxonomy_tag"), expected.get("concept")),
        ("period_key", ("period_key", "period_end"), expected.get("period_key")),
    ]


def agent_view_fact_rows(
    conn: Any,
    schema: str,
    case: dict[str, Any],
    where_sql: str,
    params: tuple[Any, ...],
    expected: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    view = "v_agent_financial_facts"
    columns = relation_columns(conn, schema, view)
    if not columns:
        return [], "agent financial facts view missing"
    selected = [column for column in AGENT_VIEW_SELECT_COLUMNS if column in columns]
    if not selected:
        return [], "agent financial facts view has no supported columns"
    missing_expected_columns = sorted(
        field for field in AGENT_VIEW_EXPECTED_VALUE_COLUMNS if field in expected and field not in columns
    )
    if missing_expected_columns:
        return [], f"agent financial facts view missing expected columns: {', '.join(missing_expected_columns)}"
    scoped = scoped_where_for_relation(conn, schema, view, case, where_sql, params)
    if scoped is None:
        return [], "agent financial facts view cannot be scoped to this case"
    scoped_where_sql, scoped_params = scoped

    filters: list[tuple[str, Any]] = []
    for _field, candidate_columns, value in agent_view_filter_candidates(expected):
        if value is None:
            continue
        for column in candidate_columns:
            if column in columns:
                filters.append((column, value))
                break
    if not filters:
        return [], "agent question has no fact filters"

    where_parts = [scoped_where_sql]
    query_params: list[Any] = list(scoped_params)
    for column, value in filters:
        where_parts.append(f"{safe_sql_ident(column)} = %s")
        query_params.append(value)

    order_columns = [
        column
        for column in (
            "filing_period_end",
            "period_key",
            "statement_type",
            "item_index",
            "canonical_name",
            "item_name",
            "concept",
        )
        if column in columns
    ]
    order_sql = ", ".join(safe_sql_ident(column) for column in order_columns) if order_columns else "1"
    schema_sql = safe_sql_ident(schema)
    select_sql = ", ".join(safe_sql_ident(column) for column in selected)
    rows = conn.execute(
        f"""
        select {select_sql}
        from {schema_sql}.{view}
        where {' and '.join(where_parts)}
        order by {order_sql}
        limit 10
        """,
        tuple(query_params),
    ).fetchall()
    return [dict(zip(selected, row)) for row in rows], ""


def check_production_agent_case(
    case: dict[str, Any],
    *,
    market_schemas: dict[str, str],
    database_url_for_market: DatabaseUrlForMarket,
    db_selector_for_case: DbSelectorForCase,
    database_url: str | None = None,
    db_result: dict[str, Any] | None = None,
    connect: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    market = str(case.get("market") or "").upper()
    if market not in market_schemas:
        return {
            "case_id": case.get("case_id"),
            "market": market,
            "passed": True,
            "skipped": True,
            "reason": "legacy_or_unsupported_market",
            "mode": "postgres_agent_view",
        }
    if db_result and db_result.get("skipped"):
        return {
            "case_id": case.get("case_id"),
            "market": market,
            "passed": True,
            "skipped": True,
            "reason": db_result.get("reason") or "db case skipped",
            "mode": "postgres_agent_view",
        }
    if connect is None:
        try:
            import psycopg
        except Exception as exc:
            return {
                "case_id": case.get("case_id"),
                "market": market,
                "passed": False,
                "errors": [f"psycopg unavailable: {exc}"],
                "mode": "postgres_agent_view",
            }
        connect = psycopg.connect

    parse_run_id = str((db_result or {}).get("parse_run_id") or "")
    if parse_run_id:
        where_sql, params = "parse_run_id = %s", (parse_run_id,)
    else:
        where_sql, params = db_selector_for_case(case)

    questions = case.get("agent_questions")
    if not isinstance(questions, list) or not questions:
        questions = [
            {
                "question_id": f"{case.get('case_id')}:postgres_agent_view",
                "expected_fact": assertion_to_expected_fact(assertion, case),
            }
            for assertion in (case.get("assertions") or case.get("expected_facts") or [])
            if isinstance(assertion, dict)
        ]

    schema = market_schemas[market]
    checked = 0
    passed_checks = 0
    errors: list[str] = []
    checked_questions: list[dict[str, Any]] = []
    try:
        with connect(database_url_for_market(market, database_url)) as conn:
            for question in questions:
                if not isinstance(question, dict):
                    continue
                expected = question.get("expected_fact") if isinstance(question.get("expected_fact"), dict) else question
                expected = assertion_to_expected_fact(expected, case)
                question_id = str(question.get("question_id") or f"{case.get('case_id')}:postgres_agent_view")
                checked += 1
                rows, reason = agent_view_fact_rows(conn, schema, case, where_sql, params, expected)
                if not rows:
                    errors.append(f"{question_id}: {reason or 'missing fact match in agent view'} {expected}")
                    checked_questions.append({"question_id": question_id, "passed": False, "row_count": 0, "reason": reason})
                    continue
                row_errors_by_candidate = [agent_view_row_errors(row, expected) for row in rows]
                match_index = next(
                    (index for index, row_errors in enumerate(row_errors_by_candidate) if not row_errors),
                    None,
                )
                if match_index is None:
                    first_errors = row_errors_by_candidate[0] if row_errors_by_candidate else ["missing fact match in agent view"]
                    errors.append(f"{question_id}: {'; '.join(first_errors)}")
                    checked_questions.append(
                        {
                            "question_id": question_id,
                            "passed": False,
                            "row_count": len(rows),
                            "candidate_errors": first_errors,
                            "first_candidate": json_safe(rows[0]),
                        }
                    )
                    continue
                passed_checks += 1
                checked_questions.append(
                    {
                        "question_id": question_id,
                        "passed": True,
                        "row_count": len(rows),
                        "matched_fact": json_safe(rows[match_index]),
                    }
                )
    except Exception as exc:
        errors.append(str(exc))

    return {
        "case_id": case.get("case_id"),
        "market": market,
        "passed": checked > 0 and not errors,
        "checked": checked,
        "passed_checks": passed_checks,
        "errors": errors,
        "schema": schema,
        "view": f"{schema}.v_agent_financial_facts",
        "mode": "postgres_agent_view",
        "questions": checked_questions,
    }


def check_production_sample_agent_view_case(
    case: dict[str, Any],
    *,
    market_schemas: dict[str, str],
    database_url_for_market: DatabaseUrlForMarket,
    database_url: str | None = None,
    db_result: dict[str, Any] | None = None,
    connect: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    market = str(case.get("market") or "").upper()
    mode = "production_sample_agent_view_probe"
    if market not in market_schemas:
        return {
            "case_id": case.get("case_id"),
            "market": market,
            "passed": True,
            "skipped": True,
            "reason": "legacy_or_unsupported_market",
            "mode": mode,
        }
    if not db_result:
        return {
            "case_id": case.get("case_id"),
            "market": market,
            "passed": False,
            "errors": ["production sample DB result missing"],
            "mode": mode,
        }
    if db_result.get("skipped"):
        return {
            "case_id": case.get("case_id"),
            "market": market,
            "passed": True,
            "skipped": True,
            "reason": db_result.get("reason") or "db case skipped",
            "mode": mode,
        }
    if not db_result.get("passed"):
        return {
            "case_id": case.get("case_id"),
            "market": market,
            "passed": False,
            "errors": ["production sample DB result failed; agent view probe not trusted"],
            "mode": mode,
        }
    parse_run_id = str(db_result.get("parse_run_id") or "")
    if not parse_run_id:
        return {
            "case_id": case.get("case_id"),
            "market": market,
            "passed": False,
            "errors": ["production sample DB result has no parse_run_id"],
            "mode": mode,
        }
    if connect is None:
        try:
            import psycopg
        except Exception as exc:
            return {
                "case_id": case.get("case_id"),
                "market": market,
                "passed": False,
                "errors": [f"psycopg unavailable: {exc}"],
                "mode": mode,
            }
        connect = psycopg.connect

    schema = market_schemas[market]
    errors: list[str] = []
    row_count = 0
    value_row_count = 0
    evidence_row_count = 0
    sample_rows: list[dict[str, Any]] = []
    try:
        with connect(database_url_for_market(market, database_url)) as conn:
            columns = relation_columns(conn, schema, "v_agent_financial_facts")
            if not columns:
                errors.append("agent financial facts view missing")
            elif "parse_run_id" not in columns:
                errors.append("agent financial facts view has no parse_run_id scope")
            else:
                selected = [column for column in AGENT_VIEW_SELECT_COLUMNS if column in columns]
                if not selected:
                    errors.append("agent financial facts view has no supported columns")
                else:
                    schema_sql = safe_sql_ident(schema)
                    select_sql = ", ".join(safe_sql_ident(column) for column in selected)
                    row = conn.execute(
                        f"select count(*) from {schema_sql}.v_agent_financial_facts where parse_run_id = %s",
                        (parse_run_id,),
                    ).fetchone()
                    row_count = int(row[0] if row else 0)
                    value_predicates = [
                        f"{safe_sql_ident(column)} is not null"
                        for column in ("value", "raw_value")
                        if column in columns
                    ]
                    if value_predicates:
                        row = conn.execute(
                            f"""
                            select count(*)
                            from {schema_sql}.v_agent_financial_facts
                            where parse_run_id = %s and ({' or '.join(value_predicates)})
                            """,
                            (parse_run_id,),
                        ).fetchone()
                        value_row_count = int(row[0] if row else 0)
                    evidence_predicates = [
                        f"{safe_sql_ident(column)} is not null"
                        for column in AGENT_VIEW_REVIEWABLE_EVIDENCE_COLUMNS
                        if column in columns
                    ]
                    if evidence_predicates:
                        row = conn.execute(
                            f"""
                            select count(*)
                            from {schema_sql}.v_agent_financial_facts
                            where parse_run_id = %s and ({' or '.join(evidence_predicates)})
                            """,
                            (parse_run_id,),
                        ).fetchone()
                        evidence_row_count = int(row[0] if row else 0)
                    order_columns = [
                        column
                        for column in ("filing_period_end", "period_key", "statement_type", "canonical_name", "item_name", "concept")
                        if column in columns
                    ]
                    order_sql = ", ".join(safe_sql_ident(column) for column in order_columns) if order_columns else "1"
                    rows = conn.execute(
                        f"""
                        select {select_sql}
                        from {schema_sql}.v_agent_financial_facts
                        where parse_run_id = %s
                        order by {order_sql}
                        limit 5
                        """,
                        (parse_run_id,),
                    ).fetchall()
                    sample_rows = [dict(zip(selected, result_row)) for result_row in rows]
                    if row_count < 1:
                        errors.append("agent financial facts view returned no rows for production sample parse_run_id")
                    if value_row_count < 1:
                        errors.append("agent financial facts view returned no value/raw_value rows for production sample")
                    if evidence_row_count < 1 and not any(has_reviewable_agent_view_evidence(row) for row in sample_rows):
                        errors.append("agent financial facts view returned no reviewable evidence for production sample")
    except Exception as exc:
        errors.append(str(exc))

    return {
        "case_id": case.get("case_id"),
        "market": market,
        "passed": not errors,
        "errors": errors,
        "schema": schema,
        "view": f"{schema}.v_agent_financial_facts",
        "mode": mode,
        "parse_run_id": parse_run_id,
        "row_count": row_count,
        "value_row_count": value_row_count,
        "evidence_row_count": evidence_row_count,
        "sample_rows": json_safe(sample_rows),
    }


__all__ = [
    "AGENT_VIEW_EXPECTED_VALUE_COLUMNS",
    "AGENT_VIEW_SELECT_COLUMNS",
    "agent_view_fact_rows",
    "agent_view_filter_candidates",
    "check_production_agent_case",
    "check_production_sample_agent_view_case",
]
