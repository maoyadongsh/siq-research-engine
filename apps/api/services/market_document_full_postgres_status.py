"""PostgreSQL status checks for multi-market document_full imports."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable

from fastapi import HTTPException

from services import market_document_identity


MARKET_DOCUMENT_FULL_SCHEMAS = {
    "US": "sec_us",
    "HK": "pdf2md_hk",
    "JP": "edinet_jp",
    "KR": "dart_kr",
    "EU": "eu_ifrs",
}
MARKET_DOCUMENT_FULL_COUNT_TABLES = {
    "facts": ("financial_statement_items", "financial_facts", "xbrl_facts_raw"),
    "tables": ("document_tables", "html_tables", "pdf_tables"),
    "chunks": ("document_chunks", "retrieval_chunks"),
    "evidence": ("evidence_citations",),
}


def _safe_sql_ident(value: str) -> str:
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", value):
        raise ValueError(f"Unsafe SQL identifier: {value!r}")
    return value


def _status_database_url(market: str, *, market_databases: dict[str, str]) -> str:
    market = market_document_identity.normalize_market_code(market)
    database = market_databases[market]
    host = os.environ.get("SIQ_PGHOST") or os.environ.get("PGHOST") or "127.0.0.1"
    port = os.environ.get("SIQ_PGPORT") or os.environ.get("PGPORT") or "15432"
    user = os.environ.get("SIQ_PGUSER") or os.environ.get("PGUSER") or "postgres"
    password = os.environ.get("SIQ_PGPASSWORD") or os.environ.get("PGPASSWORD") or ""
    auth = f"{user}:{password}" if password else user
    return f"postgresql://{auth}@{host}:{port}/{database}"


def _table_exists(conn: Any, schema: str, table: str) -> bool:
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


def _count(conn: Any, schema: str, table: str, where_sql: str, params: tuple[Any, ...]) -> int:
    if not _table_exists(conn, schema, table):
        return 0
    schema_sql = _safe_sql_ident(schema)
    table_sql = _safe_sql_ident(table)
    row = conn.execute(f"select count(*) from {schema_sql}.{table_sql} where {where_sql}", params).fetchone()
    return int(row[0] if row else 0)


def market_document_full_path_keys(
    market: str,
    value: str | None,
    *,
    repo_root: Path,
    market_document_full_roots: dict[str, Path],
    safe_market_document_full_path: Callable[[str, str | None], Path],
) -> list[str]:
    if not value:
        return []
    try:
        return list(
            market_document_identity.document_full_path_keys(
                market=market,
                value=value,
                repo_root=repo_root,
                market_document_full_roots=market_document_full_roots,
                safe_market_document_full_path=safe_market_document_full_path,
            )
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="document_full_path must resolve to document_full.json")


def market_document_full_db_status(
    market: str,
    *,
    repo_root: Path,
    market_document_full_roots: dict[str, Path],
    safe_market_document_full_path: Callable[[str, str | None], Path],
    market_databases: dict[str, str],
    schemas: dict[str, str] = MARKET_DOCUMENT_FULL_SCHEMAS,
    count_tables: dict[str, tuple[str, ...]] = MARKET_DOCUMENT_FULL_COUNT_TABLES,
    parse_run_id: str | None = None,
    filing_id: str | None = None,
    document_full_path: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    try:
        identity = market_document_identity.resolve_document_full_identity(
            market=market,
            repo_root=repo_root,
            market_document_full_roots=market_document_full_roots,
            safe_market_document_full_path=safe_market_document_full_path,
            parse_run_id=parse_run_id,
            filing_id=filing_id,
            document_full_path=document_full_path,
            task_id=task_id,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="document_full_path must resolve to document_full.json")
    market = identity.market
    parse_run_id = identity.parse_run_id
    filing_id = identity.filing_id
    task_id = identity.task_id
    selectors = market_document_identity.build_status_selector(identity)
    if not selectors:
        return {}
    try:
        import psycopg
    except Exception as exc:
        return {"status": "unknown", "selectors": selectors, "message": f"psycopg unavailable: {exc}"}

    schema = schemas[market]
    try:
        with psycopg.connect(_status_database_url(market, market_databases=market_databases)) as conn:
            where_sql = ""
            params: tuple[Any, ...] = ()
            if parse_run_id:
                where_sql = "parse_run_id = %s"
                params = (parse_run_id,)
            elif filing_id:
                where_sql = "filing_id = %s"
                params = (filing_id,)
            elif identity.document_full_path:
                document_full_keys = list(identity.path_keys)
                if _table_exists(conn, schema, "parse_runs"):
                    schema_sql = _safe_sql_ident(schema)
                    placeholders = ", ".join(["%s"] * len(document_full_keys)) or "%s"
                    row = conn.execute(
                        f"""
                        select parse_run_id, filing_id
                        from {schema_sql}.parse_runs
                        where wiki_package_path in ({placeholders})
                           or raw->>'document_full_path' in ({placeholders})
                        order by completed_at desc nulls last, started_at desc nulls last, parse_run_id desc
                        limit 1
                        """,
                        tuple(document_full_keys + document_full_keys),
                    ).fetchone()
                    if row:
                        parse_run_id = str(row[0])
                        filing_id = str(row[1]) if row[1] is not None else filing_id
                        where_sql = "parse_run_id = %s"
                        params = (parse_run_id,)
                if not where_sql:
                    where_sql = "parse_run_id = %s"
                    params = ("__missing_parse_run__",)
            elif task_id:
                if _table_exists(conn, schema, "parse_runs"):
                    schema_sql = _safe_sql_ident(schema)
                    task_lookup_params = market_document_identity.status_task_lookup_params(identity)
                    row = conn.execute(
                        f"""
                        select parse_run_id, filing_id
                        from {schema_sql}.parse_runs
                        where raw->'task'->>'task_id' = %s
                           or wiki_package_path like %s
                        order by completed_at desc nulls last, started_at desc nulls last, parse_run_id desc
                        limit 1
                        """,
                        task_lookup_params,
                    ).fetchone()
                    if row:
                        parse_run_id = str(row[0])
                        filing_id = str(row[1]) if row[1] is not None else filing_id
                        where_sql = "parse_run_id = %s"
                        params = (parse_run_id,)
                if not where_sql:
                    where_sql = "parse_run_id = %s"
                    params = ("__missing_parse_run__",)

            counts = {
                name: sum(_count(conn, schema, table, where_sql, params) for table in tables)
                for name, tables in count_tables.items()
            }
            parse_runs = _count(conn, schema, "parse_runs", where_sql, params)
            ready = (
                parse_runs > 0
                and counts["facts"] > 0
                and counts["tables"] > 0
                and counts["chunks"] > 0
                and counts["evidence"] > 0
            )
            warning = parse_runs > 0 and counts["facts"] > 0 and not ready
            ready_counts = {
                "parse_runs": parse_runs,
                **counts,
            }
            missing_counts = [
                name
                for name in ("parse_runs", "facts", "tables", "chunks", "evidence")
                if ready_counts[name] <= 0
            ]
            return {
                "status": "postgres_ready" if ready else ("warning" if warning else "missing"),
                "selectors": selectors,
                "database": market_databases[market],
                "schema": schema,
                "parse_run_id": parse_run_id,
                "filing_id": filing_id,
                "parse_runs": parse_runs,
                "missing_counts": missing_counts,
                **counts,
            }
    except Exception as exc:
        return {
            "status": "unknown",
            "selectors": selectors,
            "database": market_databases[market],
            "schema": schema,
            "message": str(exc),
        }
