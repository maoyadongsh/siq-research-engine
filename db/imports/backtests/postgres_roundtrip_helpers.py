"""PostgreSQL relation/count/content/evidence helpers for document_full gates."""

from __future__ import annotations

from typing import Any

from document_fact_normalizer import (
    assertion_to_expected_fact,
    has_evidence_value,
    stable_json_hash,
    stable_row_list,
    stable_rows_hash,
)


DB_TABLE_FAMILIES = {
    "companies": ("companies",),
    "filings": ("filings", "company_filings"),
    "parse_runs": ("parse_runs",),
    "documents": ("raw_payload_refs", "document_pages", "pdf_pages", "filing_sections", "content_blocks"),
    "artifacts": ("artifacts", "parser_artifacts"),
    "statements": ("financial_statements",),
    "items": (
        "financial_statement_items",
        "financial_balance_sheet_items",
        "financial_income_statement_items",
        "financial_cash_flow_statement_items",
        "financial_key_metrics",
    ),
    "facts": ("financial_statement_items", "financial_facts", "xbrl_facts_raw"),
    "tables": ("document_tables", "html_tables", "pdf_tables"),
    "chunks": ("document_chunks", "retrieval_chunks"),
    "evidence": ("evidence_citations",),
    "normalization": ("financial_items_enriched",),
    "quality": ("financial_checks", "quality_checks", "quality_reports"),
    "wide": ("financial_all_metrics_wide", "financial_all_metrics_wide_detail"),
}
DB_COUNT_TABLES = DB_TABLE_FAMILIES
DB_DEFAULT_REQUIRED_FAMILIES = {
    "parse_runs": "parse_run missing",
    "facts": "financial facts missing",
    "chunks": "retrieval chunks missing",
    "tables": "document tables missing",
    "evidence": "evidence citations missing",
    "documents": "document payload rows missing",
    "artifacts": "artifact rows missing",
    "normalization": "normalization rows missing",
    "quality": "quality rows missing",
    "wide": "wide metrics rows missing",
}
DB_REVIEWABLE_EVIDENCE_COLUMNS = (
    "source_page_number",
    "source_table_index",
    "source_bbox",
    "page_number",
    "table_index",
    "bbox",
    "quote_text",
    "html_anchor",
    "xpath",
    "source_url",
    "local_path",
)
DB_FACT_EVIDENCE_TABLES = (
    "financial_statement_items",
    "financial_facts",
    "financial_key_metrics",
    "financial_balance_sheet_items",
    "financial_income_statement_items",
    "financial_cash_flow_statement_items",
    "xbrl_facts_raw",
)


def safe_sql_ident(value: str) -> str:
    if not value.replace("_", "").isalnum() or value[0].isdigit():
        raise ValueError(f"Unsafe SQL identifier: {value!r}")
    return value


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


def relation_exists(conn: Any, schema: str, relation: str) -> bool:
    row = conn.execute(
        """
        select 1
        from information_schema.tables
        where table_schema = %s
          and table_name = %s
          and table_type in ('BASE TABLE', 'VIEW')
        union all
        select 1
        from information_schema.views
        where table_schema = %s
          and table_name = %s
        limit 1
        """,
        (schema, relation, schema, relation),
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


def relation_columns(conn: Any, schema: str, relation: str) -> set[str]:
    if not relation_exists(conn, schema, relation):
        return set()
    rows = conn.execute(
        """
        select column_name
        from information_schema.columns
        where table_schema = %s and table_name = %s
        """,
        (schema, relation),
    ).fetchall()
    return {str(row[0]) for row in rows}


def db_count(conn: Any, schema: str, table: str, where_sql: str, params: tuple[Any, ...]) -> int:
    if not table_exists(conn, schema, table):
        return 0
    schema_sql = safe_sql_ident(schema)
    table_sql = safe_sql_ident(table)
    row = conn.execute(f"select count(*) from {schema_sql}.{table_sql} where {where_sql}", params).fetchone()
    return int(row[0] if row else 0)


def simple_selector_column(where_sql: str) -> str | None:
    parts = where_sql.strip().split()
    if len(parts) == 3 and parts[1] == "=" and parts[2] == "%s":
        return parts[0]
    return None


def case_selector_values(case: dict[str, Any], where_sql: str, params: tuple[Any, ...]) -> list[tuple[str, Any]]:
    identity = case.get("expected_identity") if isinstance(case.get("expected_identity"), dict) else {}
    default_selector = simple_selector_column(where_sql)
    values: list[tuple[str, Any]] = []
    if default_selector and params:
        values.append((default_selector, params[0]))
    values.extend(
        [
            ("parse_run_id", case.get("parse_run_id")),
            ("filing_id", identity.get("filing_id") or case.get("filing_id")),
            ("company_id", identity.get("company_id") or case.get("company_id")),
            ("ticker", identity.get("ticker") or case.get("ticker")),
            ("period_end", identity.get("period_end") or case.get("period_end")),
            ("fiscal_year", identity.get("report_year") or case.get("report_year")),
        ]
    )
    seen: set[tuple[str, str]] = set()
    result: list[tuple[str, Any]] = []
    for column, value in values:
        if value is None:
            continue
        key = (column, str(value))
        if key in seen:
            continue
        seen.add(key)
        result.append((column, value))
    return result


def scoped_where_for_table(
    conn: Any,
    schema: str,
    table: str,
    case: dict[str, Any],
    where_sql: str,
    params: tuple[Any, ...],
) -> tuple[str, tuple[Any, ...]] | None:
    columns = table_columns(conn, schema, table)
    if not columns:
        return None
    selector_column = simple_selector_column(where_sql)
    if selector_column in columns:
        return where_sql, params
    for column, value in case_selector_values(case, where_sql, params):
        if column in columns:
            return f"{safe_sql_ident(column)} = %s", (value,)
    return None


def scoped_where_for_relation(
    conn: Any,
    schema: str,
    relation: str,
    case: dict[str, Any],
    where_sql: str,
    params: tuple[Any, ...],
) -> tuple[str, tuple[Any, ...]] | None:
    columns = relation_columns(conn, schema, relation)
    if not columns:
        return None
    selector_column = simple_selector_column(where_sql)
    if selector_column in columns:
        return where_sql, params
    for column, value in case_selector_values(case, where_sql, params):
        if column in columns:
            return f"{safe_sql_ident(column)} = %s", (value,)
        if column == "ticker" and "company_ticker" in columns:
            return "company_ticker = %s", (value,)
        if column == "period_end" and "filing_period_end" in columns:
            return "filing_period_end = %s", (value,)
    return None


def db_count_for_case(
    conn: Any,
    schema: str,
    table: str,
    case: dict[str, Any],
    where_sql: str,
    params: tuple[Any, ...],
) -> int:
    scoped = scoped_where_for_table(conn, schema, table, case, where_sql, params)
    if scoped is None:
        return db_count(conn, schema, table, "1=1", ())
    scoped_where_sql, scoped_params = scoped
    return db_count(conn, schema, table, scoped_where_sql, scoped_params)


def db_family_counts(
    conn: Any,
    schema: str,
    case: dict[str, Any],
    where_sql: str,
    params: tuple[Any, ...],
) -> dict[str, int]:
    return {
        family: sum(db_count_for_case(conn, schema, table, case, where_sql, params) for table in tables)
        for family, tables in DB_TABLE_FAMILIES.items()
    }


def table_name_from_count_key(key: str, schema: str) -> str:
    parts = key.split(".")
    if len(parts) == 1:
        return safe_sql_ident(parts[0])
    if len(parts) == 2 and parts[0] == schema:
        return safe_sql_ident(parts[1])
    raise ValueError(f"Unsupported expected_table_counts key: {key!r}")


def db_table_counts(
    conn: Any,
    schema: str,
    case: dict[str, Any],
    where_sql: str,
    params: tuple[Any, ...],
    expected_table_counts: dict[str, Any],
) -> dict[str, int]:
    tables = {table for family_tables in DB_TABLE_FAMILIES.values() for table in family_tables}
    tables.update(table_name_from_count_key(str(key), schema) for key in expected_table_counts)
    return {
        table: db_count_for_case(conn, schema, table, case, where_sql, params)
        for table in sorted(tables)
    }


def int_count(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError(f"Count expectation must be an integer, got {value!r}")
    return int(value)


def check_count_expectation(
    errors: list[str],
    label: str,
    observed: int,
    expected: Any,
    *,
    default_exact: bool,
) -> None:
    if isinstance(expected, dict):
        if "exact" in expected:
            exact = int_count(expected["exact"])
            if observed != exact:
                errors.append(f"{label}: expected exactly {exact}, got {observed}")
        if "min" in expected:
            minimum = int_count(expected["min"])
            if observed < minimum:
                errors.append(f"{label}: expected at least {minimum}, got {observed}")
        if "max" in expected:
            maximum = int_count(expected["max"])
            if observed > maximum:
                errors.append(f"{label}: expected at most {maximum}, got {observed}")
        if not {"exact", "min", "max"} & set(expected):
            errors.append(f"{label}: unsupported count expectation {expected!r}")
        return
    expected_count = int_count(expected)
    if default_exact:
        if observed != expected_count:
            errors.append(f"{label}: expected exactly {expected_count}, got {observed}")
    elif observed < expected_count:
        errors.append(f"{label}: expected at least {expected_count}, got {observed}")


def check_expected_counts(
    errors: list[str],
    *,
    family_counts: dict[str, int],
    table_counts: dict[str, int],
    case: dict[str, Any],
) -> None:
    legacy_counts = case.get("expected_row_counts") if isinstance(case.get("expected_row_counts"), dict) else {}
    expected_family_counts = case.get("expected_family_counts") if isinstance(case.get("expected_family_counts"), dict) else {}
    expected_table_counts = case.get("expected_table_counts") if isinstance(case.get("expected_table_counts"), dict) else {}

    for family, expected in legacy_counts.items():
        observed = family_counts.get(str(family), 0)
        check_count_expectation(errors, str(family), observed, expected, default_exact=False)
    for family, expected in expected_family_counts.items():
        family_name = str(family)
        if family_name not in DB_TABLE_FAMILIES:
            errors.append(f"unsupported expected_family_counts family: {family_name}")
            continue
        check_count_expectation(
            errors,
            f"family {family_name}",
            family_counts.get(family_name, 0),
            expected,
            default_exact=True,
        )
    for table, expected in expected_table_counts.items():
        table_name = str(table).split(".")[-1]
        check_count_expectation(
            errors,
            f"table {table}",
            table_counts.get(table_name, 0),
            expected,
            default_exact=True,
        )


def select_existing_columns(
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
    order_sql = (
        ", ".join(safe_sql_ident(column) for column in order_by)
        if order_by
        else ", ".join(safe_sql_ident(column) for column in selected)
    )
    select_sql = ", ".join(safe_sql_ident(column) for column in selected)
    rows = conn.execute(
        f"select {select_sql} from {schema_sql}.{table_sql} where {where_sql} order by {order_sql}",
        params,
    ).fetchall()
    return [dict(zip(selected, row)) for row in rows]


def db_content_hashes(conn: Any, schema: str, where_sql: str, params: tuple[Any, ...]) -> dict[str, str]:
    statement_rows = select_existing_columns(
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
    xbrl_rows = select_existing_columns(
        conn,
        schema,
        "xbrl_facts_raw",
        ("concept", "label", "value_text", "value_numeric", "unit", "context_ref", "period_end", "html_anchor", "xpath"),
        where_sql,
        params,
        ("concept", "context_ref", "period_end", "label"),
    )
    evidence_rows = select_existing_columns(
        conn,
        schema,
        "evidence_citations",
        (
            "evidence_id",
            "page_number",
            "table_index",
            "row_index",
            "column_index",
            "bbox",
            "quote_text",
            "html_anchor",
            "xpath",
            "source_url",
            "local_path",
        ),
        where_sql,
        params,
        ("evidence_id", "page_number", "table_index"),
    )
    chunk_rows = []
    for table in ("document_chunks", "retrieval_chunks"):
        chunk_rows.extend(
            {"source_table": table, **row}
            for row in select_existing_columns(
                conn,
                schema,
                table,
                ("chunk_uid", "collection_name", "doc_type", "canonical_name", "period_key", "text_hash"),
                where_sql,
                params,
                ("chunk_uid", "doc_type", "canonical_name", "period_key"),
            )
        )
    table_rows = []
    for table in ("document_tables", "html_tables", "pdf_tables"):
        table_rows.extend(
            {"source_table": table, **row}
            for row in select_existing_columns(
                conn,
                schema,
                table,
                (
                    "table_id",
                    "table_index",
                    "page_number",
                    "section_id",
                    "title",
                    "row_count",
                    "column_count",
                    "html_anchor",
                    "xpath",
                ),
                where_sql,
                params,
                ("table_id", "table_index", "page_number"),
            )
        )
    wide_rows = []
    for table in ("financial_all_metrics_wide", "financial_all_metrics_wide_detail"):
        wide_rows.extend(
            {"source_table": table, **row}
            for row in select_existing_columns(
                conn,
                schema,
                table,
                ("period_key", "company_id", "ticker", "fiscal_year", "fiscal_period"),
                where_sql,
                params,
                ("period_key",),
            )
        )
    enriched_rows = select_existing_columns(
        conn,
        schema,
        "financial_items_enriched",
        (
            "enriched_id",
            "source_table",
            "source_uid",
            "canonical_label",
            "canonical_name",
            "canonical_source",
            "item_name_raw",
            "period_key_raw",
            "value_extracted",
            "unit_raw",
        ),
        where_sql,
        params,
        ("source_table", "source_uid", "canonical_label", "item_name_raw", "period_key_raw"),
    )
    quality_rows = []
    for table in ("financial_checks", "quality_checks", "quality_reports"):
        quality_rows.extend(
            {"source_table": table, **row}
            for row in select_existing_columns(
                conn,
                schema,
                table,
                ("check_id", "severity", "status", "message", "overall_status", "critical_warnings", "raw"),
                where_sql,
                params,
                ("check_id", "severity", "status", "overall_status"),
            )
        )
    hashes = {
        "financial_statement_items": stable_rows_hash(statement_rows),
        "xbrl_facts_raw": stable_rows_hash(xbrl_rows),
        "evidence_citations": stable_rows_hash(evidence_rows),
        "chunks": stable_rows_hash(chunk_rows),
        "tables": stable_rows_hash(table_rows),
        "wide": stable_rows_hash(wide_rows),
        "financial_items_enriched": stable_rows_hash(enriched_rows),
        "quality": stable_rows_hash(quality_rows),
    }
    hashes["critical_content"] = stable_json_hash(
        {
            "financial_statement_items": stable_row_list(statement_rows),
            "xbrl_facts_raw": stable_row_list(xbrl_rows),
            "evidence_citations": stable_row_list(evidence_rows),
            "chunks": stable_row_list(chunk_rows),
            "tables": stable_row_list(table_rows),
            "wide": stable_row_list(wide_rows),
            "financial_items_enriched": stable_row_list(enriched_rows),
            "quality": stable_row_list(quality_rows),
        }
    )
    return hashes


def has_reviewable_db_evidence(row: dict[str, Any]) -> bool:
    return any(has_evidence_value(row.get(field)) for field in DB_REVIEWABLE_EVIDENCE_COLUMNS)


def db_evidence_join_rows(
    conn: Any,
    schema: str,
    evidence_id: Any,
    case: dict[str, Any],
    where_sql: str,
    params: tuple[Any, ...],
) -> list[dict[str, Any]]:
    if not has_evidence_value(evidence_id):
        return []
    table = "evidence_citations"
    columns = table_columns(conn, schema, table)
    if "evidence_id" not in columns:
        return []
    selected = [column for column in DB_REVIEWABLE_EVIDENCE_COLUMNS if column in columns]
    if not selected:
        return []
    where_parts = ["evidence_id = %s"]
    query_params: list[Any] = [evidence_id]
    scoped = scoped_where_for_table(conn, schema, table, case, where_sql, params)
    if scoped is not None:
        scoped_where_sql, scoped_params = scoped
        scoped_column = simple_selector_column(scoped_where_sql)
        if scoped_column != "evidence_id":
            where_parts.append(scoped_where_sql)
            query_params.extend(scoped_params)
    schema_sql = safe_sql_ident(schema)
    table_sql = safe_sql_ident(table)
    select_sql = ", ".join(safe_sql_ident(column) for column in selected)
    rows = conn.execute(
        f"select {select_sql} from {schema_sql}.{table_sql} where {' and '.join(where_parts)}",
        tuple(query_params),
    ).fetchall()
    return [dict(zip(selected, row)) for row in rows]


def fact_filter_candidates(expected: dict[str, Any]) -> list[tuple[str, tuple[str, ...], Any]]:
    statement_type = expected.get("statement_type")
    if statement_type == "xbrl_fact":
        statement_type = None
    return [
        ("statement_type", ("statement_type",), statement_type),
        ("canonical_name", ("canonical_name", "canonical_label", "metric_name"), expected.get("canonical_name")),
        ("name", ("item_name", "local_name", "name", "metric_name"), expected.get("name")),
        ("label", ("label",), expected.get("label")),
        ("concept", ("concept", "xbrl_tag", "taxonomy_tag"), expected.get("concept")),
        ("period_key", ("period_key", "period_end", "context_ref"), expected.get("period_key")),
    ]


def db_fact_evidence_rows(
    conn: Any,
    schema: str,
    table: str,
    case: dict[str, Any],
    where_sql: str,
    params: tuple[Any, ...],
    expected: dict[str, Any],
) -> list[dict[str, Any]]:
    columns = table_columns(conn, schema, table)
    if not columns:
        return []
    filters: list[tuple[str, Any]] = []
    for _field, candidate_columns, value in fact_filter_candidates(expected):
        if value is None:
            continue
        for column in candidate_columns:
            if column in columns:
                filters.append((column, value))
                break
    if not filters:
        return []
    selected = [column for column in DB_REVIEWABLE_EVIDENCE_COLUMNS if column in columns]
    selected.extend(column for column in ("evidence_id",) if column in columns)
    selected.extend(column for column, _value in filters if column not in selected)
    if not selected:
        return []
    scoped = scoped_where_for_table(conn, schema, table, case, where_sql, params)
    if scoped is None:
        return []
    scoped_where_sql, scoped_params = scoped
    where_parts = [scoped_where_sql]
    query_params: list[Any] = list(scoped_params)
    for column, value in filters:
        where_parts.append(f"{safe_sql_ident(column)} = %s")
        query_params.append(value)
    schema_sql = safe_sql_ident(schema)
    table_sql = safe_sql_ident(table)
    select_sql = ", ".join(safe_sql_ident(column) for column in selected)
    rows = conn.execute(
        f"select {select_sql} from {schema_sql}.{table_sql} where {' and '.join(where_parts)} limit 5",
        tuple(query_params),
    ).fetchall()
    return [dict(zip(selected, row)) for row in rows]


def db_required_evidence_check(
    conn: Any,
    schema: str,
    case: dict[str, Any],
    where_sql: str,
    params: tuple[Any, ...],
    assertion: dict[str, Any],
) -> dict[str, Any]:
    expected = assertion_to_expected_fact(assertion, case)
    label = (
        expected.get("canonical_name")
        or expected.get("concept")
        or expected.get("name")
        or expected.get("label")
        or "required_fact"
    )
    inspected_rows = 0
    inspected_tables: list[str] = []
    for table in DB_FACT_EVIDENCE_TABLES:
        rows = db_fact_evidence_rows(conn, schema, table, case, where_sql, params, expected)
        if not rows:
            continue
        inspected_tables.append(table)
        inspected_rows += len(rows)
        for row in rows:
            if has_reviewable_db_evidence(row):
                return {
                    "label": label,
                    "passed": True,
                    "source_table": table,
                    "mode": "fact_location_fields",
                    "inspected_rows": inspected_rows,
                }
            joined_rows = db_evidence_join_rows(conn, schema, row.get("evidence_id"), case, where_sql, params)
            if any(has_reviewable_db_evidence(joined_row) for joined_row in joined_rows):
                return {
                    "label": label,
                    "passed": True,
                    "source_table": table,
                    "mode": "evidence_id_join",
                    "inspected_rows": inspected_rows,
                }
    reason = "required evidence fact missing in DB" if inspected_rows == 0 else "required evidence is not reviewable in DB"
    return {
        "label": label,
        "passed": False,
        "source_tables": inspected_tables,
        "inspected_rows": inspected_rows,
        "reason": reason,
    }


__all__ = [
    "DB_COUNT_TABLES",
    "DB_DEFAULT_REQUIRED_FAMILIES",
    "DB_FACT_EVIDENCE_TABLES",
    "DB_REVIEWABLE_EVIDENCE_COLUMNS",
    "DB_TABLE_FAMILIES",
    "case_selector_values",
    "check_count_expectation",
    "check_expected_counts",
    "db_content_hashes",
    "db_count",
    "db_count_for_case",
    "db_evidence_join_rows",
    "db_fact_evidence_rows",
    "db_family_counts",
    "db_required_evidence_check",
    "db_table_counts",
    "fact_filter_candidates",
    "has_reviewable_db_evidence",
    "int_count",
    "relation_columns",
    "relation_exists",
    "safe_sql_ident",
    "scoped_where_for_relation",
    "scoped_where_for_table",
    "select_existing_columns",
    "simple_selector_column",
    "table_columns",
    "table_exists",
    "table_name_from_count_key",
]
