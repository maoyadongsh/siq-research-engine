from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    import psycopg
    from psycopg.types.json import Jsonb
except ImportError as exc:  # pragma: no cover
    raise SystemExit("psycopg is required: pip install psycopg[binary]") from exc

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES_SRC = REPO_ROOT / "services" / "market-report-rules" / "src"
if str(RULES_SRC) not in sys.path:
    sys.path.insert(0, str(RULES_SRC))

from market_report_rules_service.evidence_package import compute_artifact_hashes, stable_id, stable_parse_run_id, validate_evidence_package


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def parse_date(value: Any) -> Any:
    return str(value)[:10] if value else None


def parse_numeric(value: Any) -> Any:
    if value in (None, ""):
        return None
    return str(value)


def database_url(explicit: str | None) -> str:
    url = explicit or os.environ.get("DATABASE_URL")
    if url:
        return url.replace("postgresql+psycopg://", "postgresql://")
    host = os.environ.get("SIQ_PGHOST") or os.environ.get("PGHOST") or "127.0.0.1"
    port = os.environ.get("SIQ_PGPORT") or os.environ.get("PGPORT") or "15432"
    db = os.environ.get("SIQ_PGDATABASE") or os.environ.get("PGDATABASE") or "siq"
    user = os.environ.get("SIQ_PGUSER") or os.environ.get("PGUSER") or "postgres"
    password = os.environ.get("SIQ_PGPASSWORD") or os.environ.get("PGPASSWORD") or ""
    auth = f"{user}:{password}" if password else user
    return f"postgresql://{auth}@{host}:{port}/{db}"


def run_ddl(conn: Any, ddl_path: Path) -> None:
    conn.execute(ddl_path.read_text(encoding="utf-8"))


def import_package(conn: Any, package_dir: Path, *, schema: str, market: str) -> str:
    validation = validate_evidence_package(package_dir)
    if not validation.ok:
        raise SystemExit("Invalid evidence package: " + "; ".join(validation.errors))
    manifest = validation.manifest
    if manifest.get("market") != market:
        raise SystemExit(f"manifest market must be {market}")
    artifact_hashes = manifest.get("artifact_hashes") or compute_artifact_hashes(package_dir)
    parse_run_id = manifest.get("parse_run_id") or stable_parse_run_id(manifest, artifact_hashes)
    quality = read_json(package_dir / "qa" / "quality_report.json")
    warnings = (quality.get("critical_warnings") or []) + (quality.get("parser_warnings") or []) + (quality.get("rule_warnings") or [])

    with conn.transaction():
        _upsert_company(conn, schema, market, manifest)
        _upsert_filing(conn, schema, market, manifest, package_dir, quality)
        _upsert_parse_run(conn, schema, manifest, package_dir, parse_run_id, artifact_hashes, quality, warnings)
        _delete_run_rows(conn, schema, parse_run_id)
        _insert_artifacts(conn, schema, package_dir, parse_run_id)
        _insert_xbrl_facts(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        _insert_tables(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        _insert_evidence(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        _insert_financial_facts(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        _insert_checks(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
    return parse_run_id


def _upsert_company(conn: Any, schema: str, market: str, manifest: dict[str, Any]) -> None:
    if market == "JP":
        conn.execute(
            f"""
            insert into {schema}.companies (company_id, edinet_code, security_code, ticker, company_name, raw, updated_at)
            values (%s,%s,%s,%s,%s,%s,now())
            on conflict (company_id) do update set
              edinet_code = excluded.edinet_code,
              security_code = excluded.security_code,
              ticker = excluded.ticker,
              company_name = excluded.company_name,
              raw = excluded.raw,
              updated_at = now()
            """,
            (manifest["company_id"], manifest.get("edinet_code"), manifest.get("security_code"), manifest["ticker"], manifest.get("company_name"), Jsonb(manifest)),
        )
        return
    conn.execute(
        f"""
        insert into {schema}.companies (company_id, corp_code, stock_code, ticker, company_name, raw, updated_at)
        values (%s,%s,%s,%s,%s,%s,now())
        on conflict (company_id) do update set
          corp_code = excluded.corp_code,
          stock_code = excluded.stock_code,
          ticker = excluded.ticker,
          company_name = excluded.company_name,
          raw = excluded.raw,
          updated_at = now()
        """,
        (manifest["company_id"], manifest.get("corp_code"), manifest.get("stock_code"), manifest["ticker"], manifest.get("company_name"), Jsonb(manifest)),
    )


def _upsert_filing(conn: Any, schema: str, market: str, manifest: dict[str, Any], package_dir: Path, quality: dict[str, Any]) -> None:
    id_col = "doc_id" if market == "JP" else "rcp_no"
    id_val = manifest.get(id_col)
    conn.execute(
        f"""
        insert into {schema}.filings (
          filing_id, company_id, ticker, {id_col}, form, report_type, fiscal_year, fiscal_period,
          period_end, published_at, source_id, source_url, local_path, accounting_standard,
          quality_status, raw, updated_at
        ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
        on conflict (filing_id) do update set
          ticker = excluded.ticker,
          {id_col} = excluded.{id_col},
          form = excluded.form,
          report_type = excluded.report_type,
          fiscal_year = excluded.fiscal_year,
          fiscal_period = excluded.fiscal_period,
          period_end = excluded.period_end,
          published_at = excluded.published_at,
          source_url = excluded.source_url,
          local_path = excluded.local_path,
          accounting_standard = excluded.accounting_standard,
          quality_status = excluded.quality_status,
          raw = excluded.raw,
          updated_at = now()
        """,
        (
            manifest["filing_id"],
            manifest["company_id"],
            manifest["ticker"],
            id_val,
            manifest.get("form"),
            manifest.get("report_type"),
            manifest.get("fiscal_year"),
            manifest.get("fiscal_period"),
            parse_date(manifest.get("period_end")),
            parse_date(manifest.get("published_at")),
            manifest.get("source_id"),
            manifest.get("source_url"),
            str(package_dir / str(manifest.get("local_source_path") or "")),
            manifest.get("accounting_standard"),
            quality.get("overall_status") or manifest.get("quality_status"),
            Jsonb(manifest),
        ),
    )


def _upsert_parse_run(conn: Any, schema: str, manifest: dict[str, Any], package_dir: Path, parse_run_id: str, artifact_hashes: dict[str, str], quality: dict[str, Any], warnings: list[Any]) -> None:
    conn.execute(
        f"""
        insert into {schema}.parse_runs (
          parse_run_id, filing_id, parser_version, rules_version, wiki_package_path, status,
          completed_at, warnings, artifact_hashes, raw
        ) values (%s,%s,%s,%s,%s,%s,now(),%s,%s,%s)
        on conflict (parse_run_id) do update set
          status = excluded.status,
          completed_at = now(),
          warnings = excluded.warnings,
          artifact_hashes = excluded.artifact_hashes,
          raw = excluded.raw
        """,
        (
            parse_run_id,
            manifest["filing_id"],
            manifest.get("parser_version"),
            manifest.get("rules_version"),
            str(package_dir),
            quality.get("overall_status") or manifest.get("quality_status") or "warning",
            Jsonb(warnings),
            Jsonb(artifact_hashes),
            Jsonb({"manifest": manifest, "quality": quality}),
        ),
    )


def _delete_run_rows(conn: Any, schema: str, parse_run_id: str) -> None:
    for table in (
        "retrieval_chunks",
        "financial_checks",
        "operating_metric_facts",
        "financial_facts",
        "evidence_citations",
        "pdf_tables",
        "xbrl_facts_raw",
        "artifacts",
    ):
        conn.execute(f"delete from {schema}.{table} where parse_run_id = %s", (parse_run_id,))


def _insert_artifacts(conn: Any, schema: str, package_dir: Path, parse_run_id: str) -> None:
    for path in sorted(package_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(package_dir))
        conn.execute(
            f"insert into {schema}.artifacts (parse_run_id, artifact_type, local_path, sha256, size_bytes, raw) values (%s,%s,%s,%s,%s,%s)",
            (parse_run_id, rel.replace("/", "."), rel, hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size, Jsonb({})),
        )


def _insert_xbrl_facts(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    payload = read_json(package_dir / "xbrl" / "facts_raw.json")
    for fact in payload.get("facts") or []:
        fact_id = fact.get("fact_id") or stable_id(parse_run_id, fact.get("concept"), fact.get("context_ref"), fact.get("value_text"))
        conn.execute(
            f"""
            insert into {schema}.xbrl_facts_raw (
              fact_id, parse_run_id, filing_id, concept, value_text, value_numeric, unit,
              context_ref, period_start, period_end, instant, duration_days, dimensions,
              source_type, source_file, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (fact_id) do update set parse_run_id = excluded.parse_run_id, raw = excluded.raw
            """,
            (
                fact_id,
                parse_run_id,
                filing_id,
                fact.get("concept"),
                fact.get("value_text"),
                parse_numeric(fact.get("value_text")),
                fact.get("unit"),
                fact.get("context_ref"),
                parse_date(fact.get("period_start")),
                parse_date(fact.get("period_end")),
                parse_date(fact.get("instant")),
                fact.get("duration_days"),
                Jsonb(fact.get("dimensions") or {}),
                fact.get("source_type"),
                fact.get("source_file"),
                Jsonb(fact),
            ),
        )


def _insert_tables(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    payload = read_json(package_dir / "tables" / "table_index.json")
    for table in payload.get("tables") or []:
        conn.execute(
            f"""
            insert into {schema}.pdf_tables (
              parse_run_id, filing_id, table_id, page_number, table_index, title,
              row_count, column_count, table_json_path, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                parse_run_id,
                filing_id,
                table.get("table_id"),
                table.get("page_number"),
                table.get("table_index"),
                table.get("title"),
                table.get("row_count"),
                table.get("column_count"),
                table.get("table_json_path"),
                Jsonb(table),
            ),
        )


def _insert_evidence(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    payload = read_json(package_dir / "qa" / "source_map.json")
    for item in payload.get("entries") or []:
        conn.execute(
            f"""
            insert into {schema}.evidence_citations (
              evidence_id, filing_id, parse_run_id, source_type, source_id, xbrl_tag, context_ref,
              page_number, table_index, row_index, column_index, quote_text, local_path, source_url, target, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (evidence_id) do update set parse_run_id = excluded.parse_run_id, raw = excluded.raw
            """,
            (
                item.get("evidence_id"),
                filing_id,
                parse_run_id,
                item.get("source_type"),
                item.get("source_id"),
                item.get("xbrl_tag"),
                item.get("context_ref"),
                item.get("page_number"),
                item.get("table_index"),
                item.get("row_index"),
                item.get("column_index"),
                item.get("quote_text"),
                item.get("local_path"),
                item.get("source_url"),
                item.get("target"),
                Jsonb(item.get("raw") or item),
            ),
        )


def _insert_financial_facts(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    payload = read_json(package_dir / "metrics" / "normalized_metrics.json")
    for item in payload.get("metrics") or []:
        if item.get("statement_type") == "operating_metrics":
            conn.execute(
                f"""
                insert into {schema}.operating_metric_facts (
                  metric_id, filing_id, parse_run_id, ticker, canonical_name, value, unit,
                  period_key, confidence, evidence_id, raw
                ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (metric_id) do update set value = excluded.value, raw = excluded.raw
                """,
                (
                    item.get("metric_id"),
                    filing_id,
                    parse_run_id,
                    item.get("ticker"),
                    item.get("canonical_name"),
                    parse_numeric(item.get("value")),
                    item.get("unit"),
                    item.get("period_key"),
                    parse_numeric(item.get("confidence")),
                    item.get("evidence_id"),
                    Jsonb(item),
                ),
            )
            continue
        conn.execute(
            f"""
            insert into {schema}.financial_facts (
              metric_id, filing_id, parse_run_id, ticker, statement_type, canonical_name, local_name,
              value, raw_value, unit, currency, period_key, period_start, period_end, fiscal_year,
              fiscal_period, confidence, evidence_id, raw_fact_id, xbrl_tag, context_ref, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (metric_id) do update set value = excluded.value, raw = excluded.raw
            """,
            (
                item.get("metric_id"),
                filing_id,
                parse_run_id,
                item.get("ticker"),
                item.get("statement_type"),
                item.get("canonical_name"),
                item.get("local_name"),
                parse_numeric(item.get("value")),
                item.get("raw_value"),
                item.get("unit"),
                item.get("currency"),
                item.get("period_key"),
                parse_date(item.get("period_start")),
                parse_date(item.get("period_end")),
                item.get("fiscal_year"),
                item.get("fiscal_period"),
                parse_numeric(item.get("confidence")),
                item.get("evidence_id"),
                item.get("raw_fact_id"),
                item.get("xbrl_tag"),
                item.get("context_ref"),
                Jsonb(item),
            ),
        )


def _insert_checks(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    payload = read_json(package_dir / "metrics" / "financial_checks.json")
    for index, check in enumerate(payload.get("checks") or [], start=1):
        conn.execute(
            f"""
            insert into {schema}.financial_checks (
              check_id, filing_id, parse_run_id, rule_id, rule_name, statement_type,
              period_key, status, diff, tolerance, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (check_id) do update set status = excluded.status, raw = excluded.raw
            """,
            (
                stable_id(parse_run_id, check.get("rule_id"), check.get("period"), index),
                filing_id,
                parse_run_id,
                check.get("rule_id"),
                check.get("rule_name"),
                check.get("statement_type"),
                check.get("period"),
                check.get("status"),
                parse_numeric(check.get("diff")),
                parse_numeric(check.get("tolerance")),
                Jsonb(check),
            ),
        )
