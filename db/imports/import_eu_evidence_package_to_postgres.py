#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

try:
    import psycopg
    from psycopg.types.json import Jsonb
except ImportError as exc:  # pragma: no cover
    raise SystemExit("psycopg is required: pip install psycopg[binary]") from exc

REPO_ROOT = Path(__file__).resolve().parents[2]
IMPORTS_DIR = Path(__file__).resolve().parent
if str(IMPORTS_DIR) not in sys.path:
    sys.path.insert(0, str(IMPORTS_DIR))
RULES_SRC = REPO_ROOT / "services" / "market-report-rules" / "src"
if str(RULES_SRC) not in sys.path:
    sys.path.insert(0, str(RULES_SRC))

from market_report_rules_service.evidence_package import compute_artifact_hashes, stable_id, stable_parse_run_id, validate_evidence_package
from persistence_validation import validate_package_for_persistence
from quality_gate_guard import assess_persistence_quality, quality_with_gate_audit

DDL_PATH = REPO_ROOT / "db" / "ddl" / "050_create_eu_ifrs_schema.sql"


def read_json(path: Path, default: Any = None) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else ({} if default is None else default)


def parse_date(value: Any) -> Any:
    return str(value)[:10] if value else None


def parse_numeric(value: Any) -> Any:
    if value in (None, "") or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", "").replace("\u00a0", "").replace(" ", "")
    text = re.sub(r"^[€£$]|^(EUR|GBP|CHF|USD)", "", text, flags=re.I)
    if re.fullmatch(r"\([+-]?\d+(\.\d+)?\)", text):
        text = "-" + text[1:-1]
    if not re.fullmatch(r"[+-]?\d+(\.\d+)?", text):
        return None
    return text


def first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def database_url(explicit: str | None) -> str:
    url = explicit or os.environ.get("DATABASE_URL")
    if url:
        return url.replace("postgresql+psycopg://", "postgresql://")
    host = os.environ.get("SIQ_PGHOST") or os.environ.get("PGHOST") or "127.0.0.1"
    port = os.environ.get("SIQ_PGPORT") or os.environ.get("PGPORT") or "15432"
    db = (
        os.environ.get("SIQ_EU_PGDATABASE")
        or os.environ.get("SIQ_PGDATABASE")
        or os.environ.get("PGDATABASE")
        or "siq_eu"
    )
    user = os.environ.get("SIQ_PGUSER") or os.environ.get("PGUSER") or "postgres"
    password = os.environ.get("SIQ_PGPASSWORD") or os.environ.get("PGPASSWORD") or ""
    auth = f"{user}:{password}" if password else user
    return f"postgresql://{auth}@{host}:{port}/{db}"


def validate_schema(schema: str) -> None:
    if schema != "eu_ifrs":
        raise SystemExit("EU imports must target schema eu_ifrs")


def run_ddl(conn: Any) -> None:
    conn.execute(DDL_PATH.read_text(encoding="utf-8"))


def import_package(
    conn: Any,
    package_dir: Path,
    schema: str = "eu_ifrs",
    *,
    force_review: bool = False,
    force_requested_by: str | None = None,
    force_reason: str | None = None,
    force_approved_by: str | None = None,
    force_expires_at: str | None = None,
) -> str:
    validate_schema(schema)
    validation = validate_evidence_package(package_dir)
    persistence = validate_package_for_persistence(package_dir, validation, market="EU")
    manifest = persistence.manifest
    gate_enforcement = assess_persistence_quality(package_dir)

    artifact_hashes = manifest.get("artifact_hashes") or compute_artifact_hashes(package_dir)
    parse_run_id = manifest.get("parse_run_id") or stable_parse_run_id(manifest, artifact_hashes)
    quality = quality_with_gate_audit(read_json(package_dir / "qa" / "quality_report.json", {}), gate_enforcement)
    quality["persistence_validation_warnings"] = persistence.warnings
    warnings = (quality.get("critical_warnings") or []) + (quality.get("parser_warnings") or []) + (quality.get("rule_warnings") or [])

    with conn.transaction():
        _upsert_company(conn, schema, manifest)
        _upsert_filing(conn, schema, manifest, package_dir, quality)
        _upsert_parse_run(conn, schema, manifest, package_dir, parse_run_id, artifact_hashes, quality, warnings)
        _delete_run_rows(conn, schema, parse_run_id)
        _insert_artifacts(conn, schema, package_dir, parse_run_id)
        _insert_sections(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        _insert_pdf_pages(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        _insert_tables(conn, schema, package_dir, manifest["filing_id"], parse_run_id, manifest)
        _insert_xbrl_contexts(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        _insert_xbrl_units(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        fact_id_map = _insert_xbrl_facts(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        _insert_evidence(conn, schema, package_dir, manifest["filing_id"], parse_run_id, manifest)
        _insert_financial_facts(conn, schema, package_dir, manifest["filing_id"], parse_run_id, manifest, fact_id_map)
        _insert_checks(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        _insert_quality_report(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
    return parse_run_id


def _country(manifest: dict[str, Any]) -> str:
    return str(manifest.get("country") or "unknown").upper()


def _upsert_company(conn: Any, schema: str, manifest: dict[str, Any]) -> None:
    conn.execute(
        f"""
        insert into {schema}.companies (
          company_id, country, ticker, isin, lei, company_name, exchange,
          industry_profile, raw, updated_at
        ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
        on conflict (company_id) do update set
          country = excluded.country,
          ticker = excluded.ticker,
          isin = excluded.isin,
          lei = excluded.lei,
          company_name = excluded.company_name,
          exchange = excluded.exchange,
          industry_profile = excluded.industry_profile,
          raw = excluded.raw,
          updated_at = now()
        """,
        (
            manifest["company_id"],
            _country(manifest),
            manifest["ticker"],
            manifest.get("isin"),
            manifest.get("lei"),
            manifest.get("company_name"),
            manifest.get("exchange"),
            manifest.get("industry_profile"),
            Jsonb(manifest),
        ),
    )


def _upsert_filing(conn: Any, schema: str, manifest: dict[str, Any], package_dir: Path, quality: dict[str, Any]) -> None:
    conn.execute(
        f"""
        insert into {schema}.filings (
          filing_id, company_id, country, ticker, form, report_type, fiscal_year,
          fiscal_period, period_end, published_at, source_id, source_tier,
          source_url, landing_url, local_path, document_format, accounting_standard,
          quality_status, raw, updated_at
        ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
        on conflict (filing_id) do update set
          company_id = excluded.company_id,
          country = excluded.country,
          ticker = excluded.ticker,
          form = excluded.form,
          report_type = excluded.report_type,
          fiscal_year = excluded.fiscal_year,
          fiscal_period = excluded.fiscal_period,
          period_end = excluded.period_end,
          published_at = excluded.published_at,
          source_id = excluded.source_id,
          source_tier = excluded.source_tier,
          source_url = excluded.source_url,
          landing_url = excluded.landing_url,
          local_path = excluded.local_path,
          document_format = excluded.document_format,
          accounting_standard = excluded.accounting_standard,
          quality_status = excluded.quality_status,
          raw = excluded.raw,
          updated_at = now()
        """,
        (
            manifest["filing_id"],
            manifest["company_id"],
            _country(manifest),
            manifest["ticker"],
            manifest.get("form"),
            manifest.get("report_type"),
            manifest.get("fiscal_year"),
            manifest.get("fiscal_period"),
            parse_date(manifest.get("period_end")),
            parse_date(manifest.get("published_at")),
            manifest.get("source_id"),
            manifest.get("source_tier"),
            manifest.get("source_url"),
            manifest.get("landing_url"),
            str(package_dir / str(manifest.get("local_source_path") or "")),
            manifest.get("document_format"),
            manifest.get("accounting_standard"),
            quality.get("overall_status") or manifest.get("quality_status"),
            Jsonb(manifest),
        ),
    )


def _upsert_parse_run(
    conn: Any,
    schema: str,
    manifest: dict[str, Any],
    package_dir: Path,
    parse_run_id: str,
    artifact_hashes: dict[str, str],
    quality: dict[str, Any],
    warnings: list[Any],
) -> None:
    conn.execute(
        f"""
        insert into {schema}.parse_runs (
          parse_run_id, filing_id, parser_version, rules_version, wiki_package_path,
          status, completed_at, warnings, artifact_hashes, raw
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
            manifest.get("wiki_report_path") or manifest.get("package_path") or str(package_dir),
            quality.get("overall_status") or manifest.get("quality_status") or "warning",
            Jsonb(warnings),
            Jsonb(artifact_hashes),
            Jsonb({"manifest": manifest, "quality": quality}),
        ),
    )


def _delete_run_rows(conn: Any, schema: str, parse_run_id: str) -> None:
    for table in (
        "retrieval_chunks",
        "quality_reports",
        "quality_checks",
        "operating_metric_facts",
        "financial_facts",
        "evidence_citations",
        "xbrl_facts_raw",
        "xbrl_units",
        "xbrl_contexts",
        "html_tables",
        "pdf_tables",
        "pdf_pages",
        "filing_sections",
        "artifacts",
    ):
        conn.execute(f"delete from {schema}.{table} where parse_run_id = %s", (parse_run_id,))


def _insert_artifacts(conn: Any, schema: str, package_dir: Path, parse_run_id: str) -> None:
    for path in sorted(package_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(package_dir))
        conn.execute(
            f"""
            insert into {schema}.artifacts (parse_run_id, artifact_type, local_path, sha256, size_bytes, raw)
            values (%s,%s,%s,%s,%s,%s)
            on conflict (parse_run_id, artifact_type) do update set
              local_path = excluded.local_path,
              sha256 = excluded.sha256,
              size_bytes = excluded.size_bytes,
              raw = excluded.raw
            """,
            (parse_run_id, rel.replace("/", "."), rel, hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size, Jsonb({})),
        )


def _insert_sections(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    payload = read_json(package_dir / "sections" / "section_index.json", {})
    sections = payload.get("sections") if isinstance(payload, dict) else []
    if not sections and (package_dir / "sections" / "report.md").exists():
        sections = [{"section_id": "report", "title": "Report markdown", "line_start": 1, "char_start": 0, "markdown_path": "sections/report.md"}]
    for index, section in enumerate(sections or [], start=1):
        if not isinstance(section, dict):
            continue
        section_id = section.get("section_id") or stable_id(parse_run_id, "section", index)
        conn.execute(
            f"""
            insert into {schema}.filing_sections (
              parse_run_id, filing_id, section_id, section_title, section_order, markdown_path,
              line_start, line_end, char_start, char_end, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (parse_run_id, section_id) do update set
              section_title = excluded.section_title,
              section_order = excluded.section_order,
              markdown_path = excluded.markdown_path,
              line_start = excluded.line_start,
              line_end = excluded.line_end,
              char_start = excluded.char_start,
              char_end = excluded.char_end,
              raw = excluded.raw
            """,
            (
                parse_run_id,
                filing_id,
                section_id,
                section.get("title") or section.get("section_title"),
                section.get("order") or section.get("section_order") or index,
                section.get("markdown_path") or "sections/report.md",
                section.get("line_start"),
                section.get("line_end"),
                section.get("char_start"),
                section.get("char_end"),
                Jsonb(section),
            ),
        )


def _insert_pdf_pages(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    pages: set[int] = set()
    payload = read_json(package_dir / "tables" / "table_index.json", {})
    for table in payload.get("tables") or []:
        page_number = table.get("page_number") if isinstance(table, dict) else None
        if page_number is None:
            continue
        try:
            pages.add(int(page_number))
        except (TypeError, ValueError):
            continue
    for page in sorted(pages):
        conn.execute(
            f"""
            insert into {schema}.pdf_pages (parse_run_id, filing_id, page_number, raw)
            values (%s,%s,%s,%s)
            on conflict (parse_run_id, page_number) do update set raw = excluded.raw
            """,
            (parse_run_id, filing_id, page, Jsonb({})),
        )


def _insert_tables(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str, manifest: dict[str, Any]) -> None:
    payload = read_json(package_dir / "tables" / "table_index.json", {})
    document_format = str(manifest.get("document_format") or "").lower()
    for table in payload.get("tables") or []:
        if not isinstance(table, dict):
            continue
        if _is_html_table(table, document_format):
            _insert_html_table(conn, schema, filing_id, parse_run_id, table)
        elif table.get("page_number") is not None or document_format == "pdf":
            _insert_pdf_table(conn, schema, filing_id, parse_run_id, table)


def _is_html_table(table: dict[str, Any], document_format: str) -> bool:
    source_type = str(table.get("source_type") or (table.get("raw") or {}).get("source_type") or "").lower()
    if "html" in source_type or "ixbrl" in source_type or "xhtml" in source_type:
        return True
    return document_format in {"html", "ixbrl_xhtml", "xhtml"} and table.get("page_number") is None


def _insert_pdf_table(conn: Any, schema: str, filing_id: str, parse_run_id: str, table: dict[str, Any]) -> None:
    table_id = table.get("table_id") or stable_id(parse_run_id, "pdf_table", table.get("page_number"), table.get("table_index"), table.get("title"))
    conn.execute(
        f"""
        insert into {schema}.pdf_tables (
          parse_run_id, filing_id, table_id, page_number, table_index, title,
          row_count, column_count, table_json_path, unit, currency, raw
        ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict (parse_run_id, table_id) do update set
          page_number = excluded.page_number,
          table_index = excluded.table_index,
          title = excluded.title,
          row_count = excluded.row_count,
          column_count = excluded.column_count,
          table_json_path = excluded.table_json_path,
          unit = excluded.unit,
          currency = excluded.currency,
          raw = excluded.raw
        """,
        (
            parse_run_id,
            filing_id,
            table_id,
            table.get("page_number"),
            table.get("table_index"),
            table.get("title"),
            table.get("row_count"),
            table.get("column_count"),
            table.get("table_json_path"),
            table.get("unit"),
            table.get("currency"),
            Jsonb(table),
        ),
    )


def _insert_html_table(conn: Any, schema: str, filing_id: str, parse_run_id: str, table: dict[str, Any]) -> None:
    raw = table.get("raw") if isinstance(table.get("raw"), dict) else {}
    table_id = table.get("table_id") or stable_id(parse_run_id, "html_table", table.get("html_anchor") or raw.get("html_anchor"), table.get("table_index"), table.get("title"))
    conn.execute(
        f"""
        insert into {schema}.html_tables (
          parse_run_id, filing_id, table_id, html_anchor, xpath, table_index, title,
          row_count, column_count, table_json_path, unit, currency, raw
        ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict (parse_run_id, table_id) do update set
          html_anchor = excluded.html_anchor,
          xpath = excluded.xpath,
          table_index = excluded.table_index,
          title = excluded.title,
          row_count = excluded.row_count,
          column_count = excluded.column_count,
          table_json_path = excluded.table_json_path,
          unit = excluded.unit,
          currency = excluded.currency,
          raw = excluded.raw
        """,
        (
            parse_run_id,
            filing_id,
            table_id,
            table.get("html_anchor") or raw.get("html_anchor"),
            table.get("xpath") or raw.get("xpath"),
            table.get("table_index"),
            table.get("title"),
            table.get("row_count"),
            table.get("column_count"),
            table.get("table_json_path"),
            table.get("unit"),
            table.get("currency"),
            Jsonb(table),
        ),
    )


def _insert_xbrl_contexts(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    for context in _iter_xbrl_records(read_json(package_dir / "xbrl" / "contexts.json", {}), "contexts"):
        context_ref = context.get("context_ref") or context.get("id") or context.get("context_id")
        if not context_ref:
            continue
        conn.execute(
            f"""
            insert into {schema}.xbrl_contexts (
              context_uid, parse_run_id, filing_id, context_ref, entity_identifier,
              period_start, period_end, instant, duration_days, dimensions, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (context_uid) do update set
              entity_identifier = excluded.entity_identifier,
              period_start = excluded.period_start,
              period_end = excluded.period_end,
              instant = excluded.instant,
              duration_days = excluded.duration_days,
              dimensions = excluded.dimensions,
              raw = excluded.raw
            """,
            (
                stable_id(parse_run_id, context_ref),
                parse_run_id,
                filing_id,
                context_ref,
                context.get("entity_identifier") or context.get("entity"),
                parse_date(context.get("period_start") or context.get("start_date")),
                parse_date(context.get("period_end") or context.get("end_date")),
                parse_date(context.get("instant")),
                context.get("duration_days"),
                Jsonb(context.get("dimensions") or {}),
                Jsonb(context),
            ),
        )


def _insert_xbrl_units(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    for unit in _iter_xbrl_records(read_json(package_dir / "xbrl" / "units.json", {}), "units"):
        unit_ref = unit.get("unit_ref") or unit.get("id") or unit.get("unit_id")
        if not unit_ref:
            continue
        conn.execute(
            f"""
            insert into {schema}.xbrl_units (
              unit_uid, parse_run_id, filing_id, unit_ref, measure, numerator,
              denominator, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (unit_uid) do update set
              measure = excluded.measure,
              numerator = excluded.numerator,
              denominator = excluded.denominator,
              raw = excluded.raw
            """,
            (
                stable_id(parse_run_id, unit_ref),
                parse_run_id,
                filing_id,
                unit_ref,
                unit.get("measure"),
                Jsonb(unit.get("numerator") or []),
                Jsonb(unit.get("denominator") or []),
                Jsonb(unit),
            ),
        )


def _insert_xbrl_facts(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> dict[str, str]:
    fact_id_map: dict[str, str] = {}
    for fact in _iter_xbrl_records(read_json(package_dir / "xbrl" / "facts_raw.json", {}), "facts"):
        concept = fact.get("concept") or fact.get("xbrl_tag")
        if not concept:
            continue
        fact_id = fact.get("fact_id") or stable_id(concept, fact.get("context_ref"), fact.get("unit_ref"), first_present(fact.get("value_text"), fact.get("value")))
        raw_fact_id = fact.get("raw_fact_id") or stable_id(parse_run_id, fact_id)
        fact_id_map[str(fact_id)] = str(raw_fact_id)
        fact_id_map[str(raw_fact_id)] = str(raw_fact_id)
        conn.execute(
            f"""
            insert into {schema}.xbrl_facts_raw (
              raw_fact_id, fact_id, parse_run_id, filing_id, concept, label, value_text,
              value_numeric, unit_ref, unit, decimals, scale, context_ref, period_start,
              period_end, instant, duration_days, dimensions, is_extension, source_type,
              source_file, html_anchor, xpath, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (raw_fact_id) do update set
              fact_id = excluded.fact_id,
              concept = excluded.concept,
              label = excluded.label,
              value_text = excluded.value_text,
              value_numeric = excluded.value_numeric,
              unit_ref = excluded.unit_ref,
              unit = excluded.unit,
              decimals = excluded.decimals,
              scale = excluded.scale,
              context_ref = excluded.context_ref,
              period_start = excluded.period_start,
              period_end = excluded.period_end,
              instant = excluded.instant,
              duration_days = excluded.duration_days,
              dimensions = excluded.dimensions,
              is_extension = excluded.is_extension,
              source_type = excluded.source_type,
              source_file = excluded.source_file,
              html_anchor = excluded.html_anchor,
              xpath = excluded.xpath,
              raw = excluded.raw
            """,
            (
                raw_fact_id,
                fact_id,
                parse_run_id,
                filing_id,
                concept,
                fact.get("label"),
                fact.get("value_text") or fact.get("value"),
                parse_numeric(first_present(fact.get("value_numeric"), fact.get("numeric_value"), fact.get("value"))),
                fact.get("unit_ref"),
                fact.get("unit"),
                fact.get("decimals"),
                fact.get("scale"),
                fact.get("context_ref"),
                parse_date(fact.get("period_start") or fact.get("start_date")),
                parse_date(fact.get("period_end") or fact.get("end_date")),
                parse_date(fact.get("instant")),
                fact.get("duration_days"),
                Jsonb(fact.get("dimensions") or {}),
                fact.get("is_extension"),
                fact.get("source_type") or "xbrl_fact",
                fact.get("source_file"),
                fact.get("html_anchor"),
                fact.get("xpath"),
                Jsonb(fact),
            ),
        )
    return fact_id_map


def _iter_xbrl_records(payload: Any, key: str) -> Iterable[dict[str, Any]]:
    if isinstance(payload, dict):
        records = payload.get(key)
        if isinstance(records, dict):
            for record_id, record in records.items():
                if isinstance(record, dict):
                    yield {"id": record_id, **record}
            return
        if isinstance(records, list):
            for record in records:
                if isinstance(record, dict):
                    yield record
            return
    if isinstance(payload, list):
        for record in payload:
            if isinstance(record, dict):
                yield record


def _insert_evidence(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str, manifest: dict[str, Any]) -> None:
    payload = read_json(package_dir / "qa" / "source_map.json", {})
    for item in payload.get("entries") or []:
        if not isinstance(item, dict) or not item.get("evidence_id"):
            continue
        raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
        conn.execute(
            f"""
            insert into {schema}.evidence_citations (
              evidence_id, filing_id, parse_run_id, country, source_type, source_id,
              xbrl_tag, context_ref, unit_ref, fact_id, html_anchor, xpath, page_number,
              table_index, row_index, column_index, quote_text, local_path, source_url,
              target, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (evidence_id) do update set
              parse_run_id = excluded.parse_run_id,
              country = excluded.country,
              source_type = excluded.source_type,
              source_id = excluded.source_id,
              xbrl_tag = excluded.xbrl_tag,
              context_ref = excluded.context_ref,
              unit_ref = excluded.unit_ref,
              fact_id = excluded.fact_id,
              html_anchor = excluded.html_anchor,
              xpath = excluded.xpath,
              page_number = excluded.page_number,
              table_index = excluded.table_index,
              row_index = excluded.row_index,
              column_index = excluded.column_index,
              quote_text = excluded.quote_text,
              local_path = excluded.local_path,
              source_url = excluded.source_url,
              target = excluded.target,
              raw = excluded.raw
            """,
            (
                item.get("evidence_id"),
                filing_id,
                parse_run_id,
                item.get("country") or _country(manifest),
                item.get("source_type") or "unknown",
                item.get("source_id") or manifest.get("source_id"),
                item.get("xbrl_tag") or raw.get("xbrl_tag") or raw.get("concept"),
                item.get("context_ref") or raw.get("context_ref"),
                item.get("unit_ref") or raw.get("unit_ref"),
                item.get("fact_id") or raw.get("fact_id"),
                item.get("html_anchor") or raw.get("html_anchor"),
                item.get("xpath") or raw.get("xpath"),
                item.get("page_number"),
                item.get("table_index"),
                item.get("row_index"),
                item.get("column_index"),
                item.get("quote_text"),
                item.get("local_path"),
                item.get("source_url") or manifest.get("source_url"),
                item.get("target"),
                Jsonb(item.get("raw") or item),
            ),
        )


def _insert_financial_facts(
    conn: Any,
    schema: str,
    package_dir: Path,
    filing_id: str,
    parse_run_id: str,
    manifest: dict[str, Any],
    fact_id_map: dict[str, str],
) -> None:
    payload = read_json(package_dir / "metrics" / "normalized_metrics.json", {})
    for item in payload.get("metrics") or []:
        if not isinstance(item, dict):
            continue
        if item.get("statement_type") == "operating_metrics":
            _insert_operating_metric(conn, schema, filing_id, parse_run_id, manifest, item)
        else:
            _insert_financial_fact(conn, schema, filing_id, parse_run_id, manifest, item, fact_id_map)


def _raw_fact_fk(item: dict[str, Any], fact_id_map: dict[str, str]) -> str | None:
    raw_fact_id = item.get("raw_fact_id") or item.get("fact_id")
    if raw_fact_id in (None, ""):
        return None
    return fact_id_map.get(str(raw_fact_id))


def _insert_financial_fact(
    conn: Any,
    schema: str,
    filing_id: str,
    parse_run_id: str,
    manifest: dict[str, Any],
    item: dict[str, Any],
    fact_id_map: dict[str, str],
) -> None:
    conn.execute(
        f"""
        insert into {schema}.financial_facts (
          metric_id, filing_id, parse_run_id, country, ticker, statement_type,
          canonical_name, local_name, value, raw_value, unit, currency, scale,
          period_key, period_start, period_end, fiscal_year, fiscal_period,
          confidence, evidence_id, raw_fact_id, xbrl_tag, context_ref, source_type, raw
        ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict (metric_id) do update set
          value = excluded.value,
          raw_value = excluded.raw_value,
          unit = excluded.unit,
          currency = excluded.currency,
          scale = excluded.scale,
          confidence = excluded.confidence,
          evidence_id = excluded.evidence_id,
          raw_fact_id = excluded.raw_fact_id,
          xbrl_tag = excluded.xbrl_tag,
          context_ref = excluded.context_ref,
          source_type = excluded.source_type,
          raw = excluded.raw
        """,
        (
            item.get("metric_id") or stable_id(parse_run_id, item.get("canonical_name"), item.get("period_key")),
            filing_id,
            parse_run_id,
            item.get("country") or _country(manifest),
            item.get("ticker") or manifest.get("ticker"),
            item.get("statement_type"),
            item.get("canonical_name"),
            item.get("local_name"),
            parse_numeric(item.get("value")),
            item.get("raw_value"),
            item.get("unit"),
            item.get("currency") or manifest.get("currency"),
            item.get("scale"),
            item.get("period_key"),
            parse_date(item.get("period_start")),
            parse_date(item.get("period_end")),
            item.get("fiscal_year") or manifest.get("fiscal_year"),
            item.get("fiscal_period") or manifest.get("fiscal_period"),
            parse_numeric(item.get("confidence")),
            item.get("evidence_id"),
            _raw_fact_fk(item, fact_id_map),
            item.get("xbrl_tag"),
            item.get("context_ref"),
            item.get("source_type"),
            Jsonb(item),
        ),
    )


def _insert_operating_metric(conn: Any, schema: str, filing_id: str, parse_run_id: str, manifest: dict[str, Any], item: dict[str, Any]) -> None:
    conn.execute(
        f"""
        insert into {schema}.operating_metric_facts (
          metric_id, filing_id, parse_run_id, country, ticker, canonical_name, value,
          raw_value, unit, period_key, period_start, period_end, fiscal_year,
          fiscal_period, confidence, evidence_id, source_type, raw
        ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict (metric_id) do update set
          value = excluded.value,
          raw_value = excluded.raw_value,
          unit = excluded.unit,
          confidence = excluded.confidence,
          evidence_id = excluded.evidence_id,
          source_type = excluded.source_type,
          raw = excluded.raw
        """,
        (
            item.get("metric_id") or stable_id(parse_run_id, item.get("canonical_name"), item.get("period_key")),
            filing_id,
            parse_run_id,
            item.get("country") or _country(manifest),
            item.get("ticker") or manifest.get("ticker"),
            item.get("canonical_name"),
            parse_numeric(item.get("value")),
            item.get("raw_value"),
            item.get("unit"),
            item.get("period_key"),
            parse_date(item.get("period_start")),
            parse_date(item.get("period_end")),
            item.get("fiscal_year") or manifest.get("fiscal_year"),
            item.get("fiscal_period") or manifest.get("fiscal_period"),
            parse_numeric(item.get("confidence")),
            item.get("evidence_id"),
            item.get("source_type"),
            Jsonb(item),
        ),
    )


def _insert_checks(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    payload = read_json(package_dir / "metrics" / "financial_checks.json", {})
    for index, check in enumerate(payload.get("checks") or [], start=1):
        if not isinstance(check, dict):
            continue
        conn.execute(
            f"""
            insert into {schema}.quality_checks (
              check_id, filing_id, parse_run_id, rule_id, rule_name, statement_type,
              period_key, status, diff, tolerance, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (check_id) do update set
              status = excluded.status,
              diff = excluded.diff,
              tolerance = excluded.tolerance,
              raw = excluded.raw
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


def _insert_quality_report(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    quality = read_json(package_dir / "qa" / "quality_report.json", {})
    conn.execute(
        f"""
        insert into {schema}.quality_reports (
          parse_run_id, filing_id, overall_status, parser_status, rule_status,
          section_count, table_count, statement_table_count, raw_cell_count,
          raw_fact_count, normalized_metric_count, evidence_coverage_ratio,
          required_statement_status, critical_warnings, parser_warnings,
          rule_warnings, raw, updated_at
        ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
        on conflict (parse_run_id) do update set
          overall_status = excluded.overall_status,
          parser_status = excluded.parser_status,
          rule_status = excluded.rule_status,
          section_count = excluded.section_count,
          table_count = excluded.table_count,
          statement_table_count = excluded.statement_table_count,
          raw_cell_count = excluded.raw_cell_count,
          raw_fact_count = excluded.raw_fact_count,
          normalized_metric_count = excluded.normalized_metric_count,
          evidence_coverage_ratio = excluded.evidence_coverage_ratio,
          required_statement_status = excluded.required_statement_status,
          critical_warnings = excluded.critical_warnings,
          parser_warnings = excluded.parser_warnings,
          rule_warnings = excluded.rule_warnings,
          raw = excluded.raw,
          updated_at = now()
        """,
        (
            parse_run_id,
            filing_id,
            quality.get("overall_status") or "warning",
            quality.get("parser_status"),
            quality.get("rule_status"),
            quality.get("section_count"),
            quality.get("table_count"),
            quality.get("statement_table_count"),
            quality.get("raw_cell_count"),
            quality.get("raw_fact_count"),
            quality.get("normalized_metric_count"),
            parse_numeric(quality.get("evidence_coverage_ratio")),
            Jsonb(quality.get("required_statement_status") or {}),
            Jsonb(quality.get("critical_warnings") or []),
            Jsonb(quality.get("parser_warnings") or []),
            Jsonb(quality.get("rule_warnings") or []),
            Jsonb(quality),
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a EU market evidence package into PostgreSQL siq/eu_ifrs.")
    parser.add_argument("package", type=Path, nargs="?")
    parser.add_argument("--package", dest="package_opt", type=Path)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--schema", default=os.environ.get("SIQ_EU_SCHEMA", "eu_ifrs"))
    parser.add_argument("--ddl", "--run-ddl", action="store_true", help="Run DDL before importing")
    parser.add_argument("--ddl-only", action="store_true")
    parser.add_argument("--force-review", "--force", dest="force_review", action="store_true", help="Allow a soft-gate review package to write canonical facts with audit")
    parser.add_argument("--force-requested-by", default=None, help="Operator requesting a soft-gate canonical override")
    parser.add_argument("--force-approved-by", default=None, help="Approver for the soft-gate canonical override")
    parser.add_argument("--force-reason", default=None, help="Reason for the soft-gate canonical override")
    parser.add_argument("--force-expires-at", default=None, help="Optional expiry timestamp for the override record")
    args = parser.parse_args()

    package_dir = args.package_opt or args.package
    validate_schema(args.schema)
    with psycopg.connect(database_url(args.database_url), autocommit=False) as conn:
        if args.ddl or args.ddl_only:
            run_ddl(conn)
            conn.commit()
        if args.ddl_only:
            print("DDL applied")
            return
        if not package_dir:
            raise SystemExit("package path is required")
        parse_run_id = import_package(
            conn,
            package_dir.resolve(),
            args.schema,
            force_review=args.force_review,
            force_requested_by=args.force_requested_by,
            force_reason=args.force_reason,
            force_approved_by=args.force_approved_by,
            force_expires_at=args.force_expires_at,
        )
        conn.commit()
    print(parse_run_id)


if __name__ == "__main__":
    main()
