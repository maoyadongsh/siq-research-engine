#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import psycopg
    from psycopg.types.json import Jsonb
except ImportError as exc:  # pragma: no cover
    raise SystemExit("psycopg is required: pip install psycopg[binary]") from exc

REPO_ROOT = Path(__file__).resolve().parents[2]
DDL_PATH = REPO_ROOT / "db" / "ddl" / "010_create_sec_us_schema.sql"


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def parse_date(value: Any) -> Any:
    if not value:
        return None
    return str(value)[:10]


def parse_numeric(value: Any) -> Any:
    if value in (None, ""):
        return None
    return str(value)


def stable_id(*parts: Any) -> str:
    return hashlib.sha256("\x1f".join("" if p is None else str(p) for p in parts).encode("utf-8")).hexdigest()


def db_fact_id(filing_id: str, local_fact_id: Any) -> str | None:
    if not local_fact_id:
        return None
    return stable_id(filing_id, local_fact_id)


def package_hashes(package_dir: Path) -> dict[str, str]:
    hashes = {}
    for path in sorted(package_dir.rglob("*")):
        if path.is_file():
            hashes[str(path.relative_to(package_dir))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def database_url(explicit: str | None) -> str:
    url = explicit or os.environ.get("SIQ_US_DATABASE_URL")
    if url:
        return url.replace("postgresql+psycopg://", "postgresql://")
    host = os.environ.get("SIQ_PGHOST") or os.environ.get("PGHOST") or "127.0.0.1"
    port = os.environ.get("SIQ_PGPORT") or os.environ.get("PGPORT") or "15432"
    db = (
        os.environ.get("SIQ_US_PGDATABASE")
        or os.environ.get("SIQ_PGDATABASE")
        or os.environ.get("PGDATABASE")
        or "siq_us"
    )
    if not db:
        generic_url = os.environ.get("DATABASE_URL")
        if generic_url:
            return generic_url.replace("postgresql+psycopg://", "postgresql://")
        db = "siq_us"
    user = os.environ.get("SIQ_PGUSER") or os.environ.get("PGUSER") or "postgres"
    password = os.environ.get("SIQ_PGPASSWORD") or os.environ.get("PGPASSWORD") or ""
    auth = f"{user}:{password}" if password else user
    return f"postgresql://{auth}@{host}:{port}/{db}"


def validate_schema(schema: str) -> None:
    if schema != "sec_us":
        raise SystemExit("US SEC imports must target schema sec_us")


def run_ddl(conn: Any) -> None:
    conn.execute(DDL_PATH.read_text(encoding="utf-8"))


def import_package(conn: Any, package_dir: Path, schema: str = "sec_us") -> str:
    validate_schema(schema)
    manifest = read_json(package_dir / "manifest.json")
    if manifest.get("market") != "US":
        raise SystemExit("manifest market must be US")
    filing_id = manifest["filing_id"]
    artifact_hashes = manifest.get("artifact_hashes") or package_hashes(package_dir)
    parse_run_id = stable_id(filing_id, manifest.get("parser_version"), manifest.get("rules_version"), json.dumps(artifact_hashes, sort_keys=True))
    warnings = read_json(package_dir / "qa" / "extraction_warnings.json").get("warnings") or []
    quality = read_json(package_dir / "qa" / "quality_report.json")
    quality_status = quality.get("overall_status") or manifest.get("quality_status") or "warning"
    company_id = f"US:{manifest.get('cik')}"

    with conn.transaction():
        conn.execute(
            f"""
            insert into {schema}.companies (company_id, cik, ticker, company_name, industry_profile, raw, updated_at)
            values (%s, %s, %s, %s, %s, %s, now())
            on conflict (company_id) do update set
              ticker = excluded.ticker,
              company_name = excluded.company_name,
              industry_profile = excluded.industry_profile,
              raw = excluded.raw,
              updated_at = now()
            """,
            (company_id, manifest.get("cik"), manifest.get("ticker"), manifest.get("company_name"), manifest.get("industry_profile"), Jsonb(manifest)),
        )
        conn.execute(
            f"""
            insert into {schema}.filings (
              filing_id, company_id, ticker, form, accession_number, fiscal_year, fiscal_period,
              period_end, filing_date, accepted_at, source_url, local_path, accounting_standard, quality_status, raw, updated_at
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            on conflict (filing_id) do update set
              ticker = excluded.ticker,
              form = excluded.form,
              fiscal_year = excluded.fiscal_year,
              fiscal_period = excluded.fiscal_period,
              period_end = excluded.period_end,
              filing_date = excluded.filing_date,
              accepted_at = excluded.accepted_at,
              source_url = excluded.source_url,
              local_path = excluded.local_path,
              accounting_standard = excluded.accounting_standard,
              quality_status = excluded.quality_status,
              raw = excluded.raw,
              updated_at = now()
            """,
            (
                filing_id,
                company_id,
                manifest.get("ticker"),
                manifest.get("form"),
                manifest.get("accession_number"),
                manifest.get("fiscal_year"),
                manifest.get("fiscal_period"),
                parse_date(manifest.get("period_end")),
                parse_date(manifest.get("filing_date")),
                manifest.get("accepted_at"),
                manifest.get("source_url"),
                str(package_dir / manifest.get("local_source_path", "raw/filing.htm")),
                manifest.get("accounting_standard"),
                quality_status,
                Jsonb(manifest),
            ),
        )
        conn.execute(
            f"""
            insert into {schema}.parse_runs (
              parse_run_id, filing_id, parser_version, rules_version, wiki_package_path, status,
              completed_at, warnings, artifact_hashes, raw
            )
            values (%s, %s, %s, %s, %s, %s, now(), %s, %s, %s)
            on conflict (parse_run_id) do update set
              status = excluded.status,
              completed_at = now(),
              warnings = excluded.warnings,
              artifact_hashes = excluded.artifact_hashes,
              raw = excluded.raw
            """,
            (
                parse_run_id,
                filing_id,
                manifest.get("parser_version"),
                manifest.get("rules_version"),
                manifest.get("wiki_report_path") or manifest.get("package_path") or str(package_dir),
                quality_status,
                Jsonb(warnings),
                Jsonb(artifact_hashes),
                Jsonb({"manifest": manifest, "quality": quality}),
            ),
        )
        _delete_run_rows(conn, schema, parse_run_id)
        _insert_artifacts(conn, schema, package_dir, parse_run_id)
        _insert_sections(conn, schema, package_dir, filing_id, parse_run_id)
        _insert_tables(conn, schema, package_dir, filing_id, parse_run_id)
        _insert_contexts(conn, schema, package_dir, filing_id, parse_run_id)
        _insert_units(conn, schema, package_dir, filing_id, parse_run_id)
        _insert_facts_raw(conn, schema, package_dir, filing_id, parse_run_id, manifest)
        _insert_evidence(conn, schema, package_dir, filing_id, parse_run_id)
        _insert_financial_facts(conn, schema, package_dir, filing_id, parse_run_id)
        _insert_operating_metrics(conn, schema, package_dir, filing_id, parse_run_id, manifest)
    return parse_run_id


def _delete_run_rows(conn: Any, schema: str, parse_run_id: str) -> None:
    for table in (
        "operating_metric_facts",
        "financial_facts",
        "evidence_citations",
        "xbrl_facts_raw",
        "xbrl_units",
        "xbrl_contexts",
        "html_tables",
        "filing_sections",
        "artifacts",
    ):
        conn.execute(f"delete from {schema}.{table} where parse_run_id = %s", (parse_run_id,))


def _insert_artifacts(conn: Any, schema: str, package_dir: Path, parse_run_id: str) -> None:
    for path in sorted(package_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(package_dir))
        artifact_type = rel.replace("/", ".")
        conn.execute(
            f"insert into {schema}.artifacts (parse_run_id, artifact_type, local_path, sha256, size_bytes, raw) values (%s, %s, %s, %s, %s, %s)",
            (parse_run_id, artifact_type, rel, hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size, Jsonb({})),
        )


def _insert_sections(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    payload = read_json(package_dir / "sections.json")
    for section in payload.get("sections") or []:
        conn.execute(
            f"""
            insert into {schema}.filing_sections (
              parse_run_id, filing_id, section_id, section_title, section_order, markdown_path,
              html_anchor, xpath, text_hash, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                parse_run_id,
                filing_id,
                section.get("section_id"),
                section.get("section_title"),
                section.get("section_order"),
                f"sections/{section.get('file')}",
                section.get("html_anchor"),
                section.get("xpath"),
                section.get("text_hash"),
                Jsonb(section),
            ),
        )


def _insert_tables(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    payload = read_json(package_dir / "tables" / "table_index.json")
    for table in payload.get("tables") or []:
        conn.execute(
            f"""
            insert into {schema}.html_tables (
              parse_run_id, filing_id, table_id, section_id, title, row_count, column_count,
              table_json_path, html_anchor, xpath, is_financial_statement_candidate, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                parse_run_id,
                filing_id,
                table.get("table_id"),
                table.get("section_id"),
                table.get("title"),
                table.get("row_count"),
                table.get("column_count"),
                f"tables/table_{int(table.get('table_index')):04d}.json" if table.get("table_index") else None,
                table.get("html_anchor"),
                table.get("xpath"),
                table.get("is_financial_statement_candidate"),
                Jsonb(table),
            ),
        )


def _insert_contexts(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    payload = read_json(package_dir / "xbrl" / "contexts.json")
    for context_ref, context in (payload.get("contexts") or {}).items():
        conn.execute(
            f"""
            insert into {schema}.xbrl_contexts (
              parse_run_id, filing_id, context_ref, period_start, period_end, instant, duration_days, dimensions, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                parse_run_id,
                filing_id,
                context_ref,
                parse_date(context.get("period_start")),
                parse_date(context.get("period_end")),
                parse_date(context.get("instant")),
                context.get("duration_days"),
                Jsonb(context.get("dimensions") or {}),
                Jsonb(context),
            ),
        )


def _insert_units(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    payload = read_json(package_dir / "xbrl" / "units.json")
    for unit_ref, unit in (payload.get("units") or {}).items():
        conn.execute(
            f"insert into {schema}.xbrl_units (parse_run_id, filing_id, unit_ref, unit, raw) values (%s,%s,%s,%s,%s)",
            (parse_run_id, filing_id, unit_ref, unit.get("unit"), Jsonb(unit)),
        )


def _insert_facts_raw(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str, manifest: dict[str, Any]) -> None:
    payload = read_json(package_dir / "xbrl" / "facts_raw.json")
    for fact in payload.get("facts") or []:
        conn.execute(
            f"""
            insert into {schema}.xbrl_facts_raw (
              fact_id, parse_run_id, filing_id, concept, taxonomy, label, value_text, value_numeric,
              unit_ref, unit, decimals, scale, context_ref, period_start, period_end, duration_days,
              instant, fiscal_year, fiscal_period, frame, dimensions, is_extension, html_anchor, xpath, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (fact_id) do update set
              parse_run_id = excluded.parse_run_id,
              filing_id = excluded.filing_id,
              raw = excluded.raw
            """,
            (
                db_fact_id(filing_id, fact.get("fact_id")),
                parse_run_id,
                filing_id,
                fact.get("concept"),
                fact.get("taxonomy"),
                fact.get("label"),
                fact.get("value_text"),
                parse_numeric(fact.get("value_numeric")),
                fact.get("unit_ref"),
                fact.get("unit"),
                fact.get("decimals"),
                fact.get("scale"),
                fact.get("context_ref"),
                parse_date(fact.get("period_start")),
                parse_date(fact.get("period_end")),
                fact.get("duration_days"),
                parse_date(fact.get("instant")),
                fact.get("fiscal_year") or manifest.get("fiscal_year"),
                fact.get("fiscal_period") or manifest.get("fiscal_period"),
                fact.get("frame"),
                Jsonb(fact.get("dimensions") or {}),
                fact.get("is_extension"),
                fact.get("html_anchor"),
                fact.get("xpath"),
                Jsonb(fact),
            ),
        )


def _insert_evidence(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    payload = read_json(package_dir / "qa" / "source_map.json")
    for item in payload.get("entries") or []:
        conn.execute(
            f"""
            insert into {schema}.evidence_citations (
              evidence_id, filing_id, parse_run_id, source_type, section_id, xbrl_tag, html_anchor,
              xpath, source_url, local_path, quote_text, target, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (evidence_id) do update set
              filing_id = excluded.filing_id,
              parse_run_id = excluded.parse_run_id,
              source_type = excluded.source_type,
              section_id = excluded.section_id,
              xbrl_tag = excluded.xbrl_tag,
              html_anchor = excluded.html_anchor,
              xpath = excluded.xpath,
              source_url = excluded.source_url,
              local_path = excluded.local_path,
              quote_text = excluded.quote_text,
              target = excluded.target,
              raw = excluded.raw
            """,
            (
                item.get("evidence_id"),
                filing_id,
                parse_run_id,
                item.get("source_type"),
                item.get("section_id"),
                item.get("xbrl_tag"),
                item.get("html_anchor"),
                item.get("xpath"),
                item.get("source_url"),
                item.get("local_path"),
                item.get("quote_text"),
                item.get("target"),
                Jsonb(item.get("raw") or item),
            ),
        )


def _insert_financial_facts(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    payload = read_json(package_dir / "metrics" / "normalized_metrics.json")
    for item in payload.get("metrics") or []:
        conn.execute(
            f"""
            insert into {schema}.financial_facts (
              metric_id, filing_id, parse_run_id, ticker, statement_type, canonical_name, concept, label,
              value, unit, currency, period_key, period_start, period_end, duration_days, qtd_ytd_type,
              fiscal_year, fiscal_period, segment_key, dimensions, confidence, evidence_id, raw_fact_id, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (metric_id) do update set value = excluded.value, raw = excluded.raw
            """,
            (
                item.get("metric_id") or stable_id(parse_run_id, item.get("canonical_name"), item.get("period_key"), item.get("concept")),
                filing_id,
                parse_run_id,
                item.get("ticker"),
                item.get("statement_type"),
                item.get("canonical_name"),
                item.get("concept"),
                item.get("label"),
                parse_numeric(item.get("value")),
                item.get("unit"),
                item.get("currency"),
                item.get("period_key"),
                parse_date(item.get("period_start")),
                parse_date(item.get("period_end")),
                item.get("duration_days"),
                item.get("qtd_ytd_type"),
                item.get("fiscal_year"),
                item.get("fiscal_period"),
                item.get("segment_key"),
                Jsonb(item.get("dimensions") or {}),
                parse_numeric(item.get("confidence")),
                item.get("evidence_id"),
                db_fact_id(filing_id, item.get("raw_fact_id")),
                Jsonb(item.get("raw") or item),
            ),
        )


def _insert_operating_metrics(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str, manifest: dict[str, Any]) -> None:
    payload = read_json(package_dir / "metrics" / "operating_metrics.json")
    for item in payload.get("metrics") or []:
        conn.execute(
            f"""
            insert into {schema}.operating_metric_facts (
              metric_id, filing_id, parse_run_id, metric_name, canonical_name, industry_profile, value,
              unit, period_key, source_type, evidence_id, confidence, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                item.get("metric_id") or stable_id(parse_run_id, item.get("canonical_name"), item.get("period_key")),
                filing_id,
                parse_run_id,
                item.get("metric_name"),
                item.get("canonical_name"),
                item.get("industry_profile") or manifest.get("industry_profile"),
                parse_numeric(item.get("value")),
                item.get("unit"),
                item.get("period_key"),
                item.get("source_type"),
                item.get("evidence_id"),
                parse_numeric(item.get("confidence")),
                Jsonb(item),
            ),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a US SEC evidence package into PostgreSQL siq_us/sec_us.")
    parser.add_argument("--package", type=Path, help="Evidence package directory")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--schema", default=os.environ.get("SIQ_US_SEC_SCHEMA", "sec_us"))
    parser.add_argument("--ddl", action="store_true", help="Run DDL before importing")
    parser.add_argument("--ddl-only", action="store_true", help="Only run DDL")
    args = parser.parse_args()

    validate_schema(args.schema)
    with psycopg.connect(database_url(args.database_url), autocommit=False) as conn:
        if args.ddl or args.ddl_only:
            run_ddl(conn)
            conn.commit()
        if args.ddl_only:
            print("DDL applied")
            return
        if not args.package:
            raise SystemExit("--package is required unless --ddl-only is set")
        parse_run_id = import_package(conn, args.package.resolve(), args.schema)
        conn.commit()
    print(parse_run_id)


if __name__ == "__main__":
    main()
