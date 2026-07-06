#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
IMPORTS_DIR = Path(__file__).resolve().parent
if str(IMPORTS_DIR) not in sys.path:
    sys.path.insert(0, str(IMPORTS_DIR))
RULES_SRC = REPO_ROOT / "services" / "market-report-rules" / "src"
if str(RULES_SRC) not in sys.path:
    sys.path.insert(0, str(RULES_SRC))

from market_report_rules_service.evidence_package import compute_artifact_hashes, stable_id, stable_parse_run_id, validate_evidence_package
from quality_gate_guard import enforce_quality_gates, quality_with_gate_audit, should_write_target

DDL_PATH = REPO_ROOT / "db" / "ddl" / "020_create_pdf2md_hk_schema.sql"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def parse_date(value: Any) -> Any:
    return str(value)[:10] if value else None


def parse_numeric(value: Any) -> Any:
    if value in (None, ""):
        return None
    return str(value)


def _stock_code(value: Any) -> str:
    text = str(value or "").strip()
    if text.isdigit():
        return text.zfill(5)
    return text


def _company_id(manifest: dict[str, Any]) -> str:
    company_id = str(manifest.get("company_id") or "").strip()
    if company_id.startswith("HK:"):
        return company_id
    code = _stock_code(manifest.get("hkex_stock_code") or manifest.get("stock_code") or manifest.get("ticker") or company_id)
    return f"HK:{code}"


def _report_id(manifest: dict[str, Any]) -> str:
    if manifest.get("report_id"):
        return str(manifest["report_id"])
    year = str(manifest.get("fiscal_year") or "unknown")
    report_type = str(manifest.get("report_type") or manifest.get("form") or "annual").lower()
    filing_key = str(manifest.get("accession_number") or manifest.get("filing_id") or stable_id(year, report_type))
    filing_key = filing_key.rsplit(":", 1)[-1]
    return f"{year}-{report_type}-{filing_key}"


def _unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if isinstance(value, list):
            candidates = value
        else:
            candidates = [value]
        for candidate in candidates:
            text = str(candidate or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            output.append(text)
    return output


def build_company_record(manifest: dict[str, Any]) -> dict[str, Any]:
    stock_code = _stock_code(manifest.get("hkex_stock_code") or manifest.get("stock_code") or manifest.get("ticker"))
    company_name = manifest.get("company_name") or manifest.get("company_full_name") or manifest.get("company_name_en")
    aliases = _unique_strings(
        [
            stock_code,
            manifest.get("ticker"),
            company_name,
            manifest.get("company_short_name"),
            manifest.get("company_name_en"),
            manifest.get("company_name_zh"),
            manifest.get("aliases") or [],
        ]
    )
    return {
        "company_id": _company_id({**manifest, "hkex_stock_code": stock_code}),
        "ticker": stock_code,
        "stock_code": stock_code,
        "hkex_stock_code": stock_code,
        "exchange": manifest.get("exchange") or "HKEX",
        "company_name": company_name,
        "company_short_name": manifest.get("company_short_name"),
        "company_name_en": manifest.get("company_name_en") or company_name,
        "company_name_zh": manifest.get("company_name_zh"),
        "aliases": aliases,
        "industry_profile": manifest.get("industry_profile") or "general",
        "raw": manifest,
    }


def build_filing_record(manifest: dict[str, Any], package_dir: Path, quality: dict[str, Any]) -> dict[str, Any]:
    stock_code = _stock_code(manifest.get("hkex_stock_code") or manifest.get("stock_code") or manifest.get("ticker"))
    return {
        "filing_id": manifest.get("filing_id") or stable_id("HK", stock_code, manifest.get("accession_number") or _report_id(manifest)),
        "company_id": _company_id({**manifest, "hkex_stock_code": stock_code}),
        "ticker": stock_code,
        "stock_code": stock_code,
        "report_id": _report_id(manifest),
        "accession_number": manifest.get("accession_number"),
        "form": manifest.get("form"),
        "report_type": manifest.get("report_type"),
        "fiscal_year": manifest.get("fiscal_year"),
        "fiscal_period": manifest.get("fiscal_period"),
        "period_end": parse_date(manifest.get("period_end")),
        "published_at": parse_date(manifest.get("published_at")),
        "source_id": manifest.get("source_id"),
        "source_url": manifest.get("source_url"),
        "local_path": str(package_dir / str(manifest.get("local_source_path") or "")),
        "accounting_standard": manifest.get("accounting_standard"),
        "quality_status": quality.get("overall_status") or manifest.get("quality_status"),
        "raw": manifest,
    }


def build_evidence_row(item: dict[str, Any], *, filing_id: str, parse_run_id: str) -> dict[str, Any]:
    return {
        "evidence_id": item.get("evidence_id") or stable_id(parse_run_id, item.get("source_id"), item.get("target"), item.get("page_number")),
        "filing_id": filing_id,
        "parse_run_id": parse_run_id,
        "source_type": item.get("source_type") or "table_cell",
        "source_id": item.get("source_id"),
        "page_number": item.get("page_number"),
        "table_index": item.get("table_index"),
        "row_index": item.get("row_index"),
        "column_index": item.get("column_index"),
        "bbox": item.get("bbox") or item.get("source_bbox"),
        "quote_text": item.get("quote_text"),
        "local_path": item.get("local_path"),
        "source_url": item.get("source_url"),
        "target": item.get("target"),
        "raw": item.get("raw") or item,
    }


def _source_map_by_evidence_id(source_map: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("evidence_id")): item
        for item in source_map.get("entries") or []
        if isinstance(item, dict) and item.get("evidence_id")
    }


def _value_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {"value": value, "raw_value": value}


def _source_from_item(item: dict[str, Any], value_payload: dict[str, Any], sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    explicit = value_payload.get("source") or value_payload.get("evidence") or item.get("source")
    if isinstance(explicit, dict):
        return explicit
    evidence_id = value_payload.get("evidence_id") or item.get("evidence_id")
    return sources.get(str(evidence_id), {}) if evidence_id else {}


def build_statement_item_rows(
    manifest: dict[str, Any],
    financial_data: dict[str, Any],
    source_map: dict[str, Any],
    parse_run_id: str,
) -> list[dict[str, Any]]:
    sources = _source_map_by_evidence_id(source_map)
    company = build_company_record(manifest)
    filing = build_filing_record(manifest, Path("."), {})
    rows: list[dict[str, Any]] = []
    for statement in financial_data.get("statements") or []:
        if not isinstance(statement, dict):
            continue
        for item_index, item in enumerate(statement.get("items") or [], start=1):
            if not isinstance(item, dict):
                continue
            values = item.get("values") if isinstance(item.get("values"), dict) else {}
            for period_key, raw_value_payload in values.items():
                value_payload = _value_payload(raw_value_payload)
                source = _source_from_item(item, value_payload, sources)
                evidence_id = value_payload.get("evidence_id") or item.get("evidence_id") or source.get("evidence_id")
                canonical_name = item.get("canonical_name") or item.get("item_name") or "unknown"
                row = {
                    "item_uid": stable_id(parse_run_id, statement.get("statement_id"), canonical_name, period_key, item_index),
                    "filing_id": filing["filing_id"],
                    "parse_run_id": parse_run_id,
                    "company_id": company["company_id"],
                    "ticker": company["ticker"],
                    "stock_code": company["stock_code"],
                    "company_name": company["company_name"],
                    "exchange": company["exchange"],
                    "statement_id": statement.get("statement_id"),
                    "statement_type": statement.get("statement_type"),
                    "statement_name": statement.get("statement_name"),
                    "scope": statement.get("scope"),
                    "scope_name": statement.get("scope_name"),
                    "item_index": item_index,
                    "period_key": str(period_key),
                    "item_name": item.get("item_name") or item.get("local_name"),
                    "canonical_name": canonical_name,
                    "value": value_payload.get("value"),
                    "raw_value": value_payload.get("raw_value"),
                    "unit": value_payload.get("unit") or item.get("unit"),
                    "currency": value_payload.get("currency") or item.get("currency"),
                    "scale": value_payload.get("scale") or item.get("scale"),
                    "period_start": parse_date(value_payload.get("period_start") or item.get("period_start")),
                    "period_end": parse_date(value_payload.get("period_end") or item.get("period_end") or manifest.get("period_end")),
                    "fiscal_year": manifest.get("fiscal_year"),
                    "fiscal_period": manifest.get("fiscal_period"),
                    "accounting_standard": manifest.get("accounting_standard"),
                    "industry_profile": manifest.get("industry_profile") or "general",
                    "confidence": value_payload.get("confidence") or item.get("confidence"),
                    "source_page_number": source.get("page_number") or value_payload.get("page_number"),
                    "source_table_index": source.get("table_index") or value_payload.get("table_index"),
                    "source_row_index": source.get("row_index") or value_payload.get("row_index"),
                    "source_column_index": source.get("column_index") or value_payload.get("column_index"),
                    "source_bbox": source.get("bbox") or value_payload.get("bbox"),
                    "evidence_id": evidence_id,
                    "raw": {"statement": statement, "item": item, "value": value_payload, "source": source},
                }
                rows.append(row)
    return rows


def _chunk_text(row: dict[str, Any]) -> str:
    parts = [
        row.get("company_name"),
        row.get("statement_name") or row.get("statement_type"),
        row.get("item_name") or row.get("canonical_name"),
        row.get("period_key"),
        row.get("raw_value") or row.get("value"),
        row.get("unit"),
    ]
    return " | ".join(str(part) for part in parts if part not in (None, ""))


def build_retrieval_chunk_rows(
    manifest: dict[str, Any],
    financial_data: dict[str, Any],
    quality: dict[str, Any],
    source_map: dict[str, Any],
    parse_run_id: str,
    package_dir: Path,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in build_statement_item_rows(manifest, financial_data, source_map, parse_run_id):
        text = _chunk_text(item)
        rows.append(
            {
                "chunk_uid": stable_id(parse_run_id, "financial_fact", item.get("canonical_name"), item.get("period_key"), item.get("evidence_id")),
                "filing_id": item["filing_id"],
                "parse_run_id": parse_run_id,
                "company_id": item["company_id"],
                "ticker": item["ticker"],
                "collection_name": "siq_hk_reports",
                "doc_type": "financial_fact",
                "section_title": item.get("statement_name"),
                "statement_type": item.get("statement_type"),
                "evidence_id": item.get("evidence_id"),
                "canonical_name": item.get("canonical_name"),
                "period_key": item.get("period_key"),
                "page_number": item.get("source_page_number"),
                "table_index": item.get("source_table_index"),
                "wiki_path": str(package_dir / "metrics" / "financial_data.json"),
                "source_url": manifest.get("source_url"),
                "text": text,
                "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "metadata": {"quality_status": quality.get("overall_status"), "raw": item},
            }
        )
    return rows


def database_url(explicit: str | None) -> str:
    url = explicit or os.environ.get("DATABASE_URL")
    if url:
        return url.replace("postgresql+psycopg://", "postgresql://")
    host = os.environ.get("SIQ_PGHOST") or os.environ.get("PGHOST") or "127.0.0.1"
    port = os.environ.get("SIQ_PGPORT") or os.environ.get("PGPORT") or "15432"
    db = os.environ.get("SIQ_HK_PGDATABASE") or os.environ.get("SIQ_PGDATABASE") or os.environ.get("PGDATABASE") or "siq_hk"
    user = os.environ.get("SIQ_PGUSER") or os.environ.get("PGUSER") or "postgres"
    password = os.environ.get("SIQ_PGPASSWORD") or os.environ.get("PGPASSWORD") or ""
    auth = f"{user}:{password}" if password else user
    return f"postgresql://{auth}@{host}:{port}/{db}"


def validate_schema(schema: str) -> None:
    if schema != "pdf2md_hk":
        raise SystemExit("HK imports must target schema pdf2md_hk")


def run_ddl(conn: Any) -> None:
    conn.execute(DDL_PATH.read_text(encoding="utf-8"))


def import_package(
    conn: Any,
    package_dir: Path,
    schema: str = "pdf2md_hk",
    *,
    force_review: bool = False,
    force_requested_by: str | None = None,
    force_reason: str | None = None,
    force_approved_by: str | None = None,
    force_expires_at: str | None = None,
) -> str:
    validate_schema(schema)
    validation = validate_evidence_package(package_dir)
    if not validation.ok:
        raise SystemExit("Invalid evidence package: " + "; ".join(validation.errors))
    manifest = validation.manifest
    if manifest.get("market") != "HK":
        raise SystemExit("manifest market must be HK")
    gate_enforcement = enforce_quality_gates(
        package_dir,
        target="canonical",
        force_review=force_review,
        requested_by=force_requested_by,
        reason=force_reason,
        approved_by=force_approved_by,
        expires_at=force_expires_at,
    )
    artifact_hashes = manifest.get("artifact_hashes") or compute_artifact_hashes(package_dir)
    parse_run_id = manifest.get("parse_run_id") or stable_parse_run_id(manifest, artifact_hashes)
    quality = quality_with_gate_audit(read_json(package_dir / "qa" / "quality_report.json"), gate_enforcement)
    financial_data = read_json(package_dir / "metrics" / "financial_data.json")
    source_map = read_json(package_dir / "qa" / "source_map.json")
    warnings = (quality.get("critical_warnings") or []) + (quality.get("parser_warnings") or []) + (quality.get("rule_warnings") or [])

    with conn.transaction():
        _upsert_company(conn, schema, manifest)
        _upsert_filing(conn, schema, manifest, package_dir, quality)
        _upsert_parse_run(conn, schema, manifest, package_dir, parse_run_id, artifact_hashes, quality, warnings)
        _delete_run_rows(conn, schema, parse_run_id)
        _insert_artifacts(conn, schema, package_dir, parse_run_id)
        _insert_sections(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        _insert_pdf_pages(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        _insert_tables(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        _insert_evidence(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        _insert_financial_facts(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        _insert_statement_items(conn, schema, manifest, financial_data, source_map, parse_run_id)
        _insert_checks(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        _insert_quality_report(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        if should_write_target(gate_enforcement, "retrieval"):
            _insert_retrieval_chunks(conn, schema, manifest, financial_data, quality, source_map, parse_run_id, package_dir)
    return parse_run_id


def _upsert_company(conn: Any, schema: str, manifest: dict[str, Any]) -> None:
    company = build_company_record(manifest)
    conn.execute(
        f"""
        insert into {schema}.companies (
          company_id, ticker, stock_code, hkex_stock_code, exchange, company_name,
          company_short_name, company_name_en, company_name_zh, aliases, industry_profile, raw, updated_at
        ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
        on conflict (company_id) do update set
          ticker = excluded.ticker,
          stock_code = excluded.stock_code,
          hkex_stock_code = excluded.hkex_stock_code,
          exchange = excluded.exchange,
          company_name = excluded.company_name,
          company_short_name = excluded.company_short_name,
          company_name_en = excluded.company_name_en,
          company_name_zh = excluded.company_name_zh,
          aliases = excluded.aliases,
          industry_profile = excluded.industry_profile,
          raw = excluded.raw,
          updated_at = now()
        """,
        (
            company["company_id"],
            company["ticker"],
            company["stock_code"],
            company["hkex_stock_code"],
            company["exchange"],
            company["company_name"],
            company["company_short_name"],
            company["company_name_en"],
            company["company_name_zh"],
            Jsonb(company["aliases"]),
            company["industry_profile"],
            Jsonb(company["raw"]),
        ),
    )


def _upsert_filing(conn: Any, schema: str, manifest: dict[str, Any], package_dir: Path, quality: dict[str, Any]) -> None:
    filing = build_filing_record(manifest, package_dir, quality)
    conn.execute(
        f"""
        insert into {schema}.filings (
          filing_id, company_id, ticker, stock_code, report_id, accession_number, form,
          report_type, fiscal_year, fiscal_period, period_end, published_at, source_id,
          source_url, local_path, accounting_standard, quality_status, raw, updated_at
        ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
        on conflict (filing_id) do update set
          ticker = excluded.ticker,
          stock_code = excluded.stock_code,
          report_id = excluded.report_id,
          accession_number = excluded.accession_number,
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
            filing["filing_id"],
            filing["company_id"],
            filing["ticker"],
            filing["stock_code"],
            filing["report_id"],
            filing["accession_number"],
            filing["form"],
            filing["report_type"],
            filing["fiscal_year"],
            filing["fiscal_period"],
            filing["period_end"],
            filing["published_at"],
            filing["source_id"],
            filing["source_url"],
            filing["local_path"],
            filing["accounting_standard"],
            filing["quality_status"],
            Jsonb(filing["raw"]),
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
        "financial_checks",
        "operating_metric_facts",
        "financial_statement_items",
        "financial_facts",
        "evidence_citations",
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
            f"insert into {schema}.artifacts (parse_run_id, artifact_type, local_path, sha256, size_bytes, raw) values (%s,%s,%s,%s,%s,%s)",
            (parse_run_id, rel.replace("/", "."), rel, hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size, Jsonb({})),
        )


def _insert_sections(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    section_path = package_dir / "sections" / "report.md"
    if section_path.exists():
        conn.execute(
            f"insert into {schema}.filing_sections (parse_run_id, filing_id, section_id, section_title, section_order, markdown_path, raw) values (%s,%s,%s,%s,%s,%s,%s)",
            (parse_run_id, filing_id, "report", "Report markdown", 1, "sections/report.md", Jsonb({"path": "sections/report.md"})),
        )


def _insert_pdf_pages(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    pages: set[int] = set()
    table_index = read_json(package_dir / "tables" / "table_index.json")
    for table in table_index.get("tables") or []:
        if table.get("page_number") is not None:
            pages.add(int(table["page_number"]))
    for page in sorted(pages):
        conn.execute(
            f"insert into {schema}.pdf_pages (parse_run_id, filing_id, page_number, raw) values (%s,%s,%s,%s)",
            (parse_run_id, filing_id, page, Jsonb({})),
        )


def _insert_tables(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    payload = read_json(package_dir / "tables" / "table_index.json")
    for table in payload.get("tables") or []:
        conn.execute(
            f"""
            insert into {schema}.pdf_tables (
              parse_run_id, filing_id, table_id, page_number, table_index, title,
              row_count, column_count, table_json_path, bbox, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
                Jsonb(table.get("bbox") or []),
                Jsonb(table),
            ),
        )


def _insert_evidence(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    payload = read_json(package_dir / "qa" / "source_map.json")
    for item in payload.get("entries") or []:
        row = build_evidence_row(item, filing_id=filing_id, parse_run_id=parse_run_id)
        conn.execute(
            f"""
            insert into {schema}.evidence_citations (
              evidence_id, filing_id, parse_run_id, source_type, source_id, page_number,
              table_index, row_index, column_index, bbox, quote_text, local_path, source_url, target, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (evidence_id) do update set
              parse_run_id = excluded.parse_run_id,
              page_number = excluded.page_number,
              table_index = excluded.table_index,
              row_index = excluded.row_index,
              column_index = excluded.column_index,
              bbox = excluded.bbox,
              quote_text = excluded.quote_text,
              raw = excluded.raw
            """,
            (
                row["evidence_id"],
                row["filing_id"],
                row["parse_run_id"],
                row["source_type"],
                row["source_id"],
                row["page_number"],
                row["table_index"],
                row["row_index"],
                row["column_index"],
                Jsonb(row["bbox"] or []),
                row["quote_text"],
                row["local_path"],
                row["source_url"],
                row["target"],
                Jsonb(row["raw"]),
            ),
        )


def _insert_statement_items(
    conn: Any,
    schema: str,
    manifest: dict[str, Any],
    financial_data: dict[str, Any],
    source_map: dict[str, Any],
    parse_run_id: str,
) -> None:
    known_evidence_ids = set(_source_map_by_evidence_id(source_map))
    for row in build_statement_item_rows(manifest, financial_data, source_map, parse_run_id):
        evidence_id = row["evidence_id"] if row.get("evidence_id") in known_evidence_ids else None
        conn.execute(
            f"""
            insert into {schema}.financial_statement_items (
              item_uid, filing_id, parse_run_id, company_id, ticker, stock_code, company_name,
              exchange, statement_id, statement_type, statement_name, scope, scope_name, item_index,
              period_key, item_name, canonical_name, value, raw_value, unit, currency, scale,
              period_start, period_end, fiscal_year, fiscal_period, accounting_standard,
              industry_profile, confidence, source_page_number, source_table_index, source_row_index,
              source_column_index, source_bbox, evidence_id, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (item_uid) do update set
              value = excluded.value,
              raw_value = excluded.raw_value,
              source_page_number = excluded.source_page_number,
              source_table_index = excluded.source_table_index,
              source_row_index = excluded.source_row_index,
              source_column_index = excluded.source_column_index,
              source_bbox = excluded.source_bbox,
              evidence_id = excluded.evidence_id,
              raw = excluded.raw
            """,
            (
                row["item_uid"],
                row["filing_id"],
                row["parse_run_id"],
                row["company_id"],
                row["ticker"],
                row["stock_code"],
                row["company_name"],
                row["exchange"],
                row["statement_id"],
                row["statement_type"],
                row["statement_name"],
                row["scope"],
                row["scope_name"],
                row["item_index"],
                row["period_key"],
                row["item_name"],
                row["canonical_name"],
                parse_numeric(row["value"]),
                row["raw_value"],
                row["unit"],
                row["currency"],
                row["scale"],
                row["period_start"],
                row["period_end"],
                row["fiscal_year"],
                row["fiscal_period"],
                row["accounting_standard"],
                row["industry_profile"],
                parse_numeric(row["confidence"]),
                row["source_page_number"],
                row["source_table_index"],
                row["source_row_index"],
                row["source_column_index"],
                Jsonb(row["source_bbox"] or []),
                evidence_id,
                Jsonb(row["raw"]),
            ),
        )


def _insert_financial_facts(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    payload = read_json(package_dir / "metrics" / "normalized_metrics.json")
    for item in payload.get("metrics") or []:
        table = "operating_metric_facts" if item.get("statement_type") == "operating_metrics" else "financial_facts"
        if table == "operating_metric_facts":
            conn.execute(
                f"""
                insert into {schema}.operating_metric_facts (
                  metric_id, filing_id, parse_run_id, ticker, canonical_name, value, raw_value,
                  unit, period_key, confidence, evidence_id, raw
                ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (metric_id) do update set value = excluded.value, raw = excluded.raw
                """,
                (
                    item.get("metric_id") or stable_id(parse_run_id, item.get("canonical_name"), item.get("period_key")),
                    filing_id,
                    parse_run_id,
                    item.get("ticker"),
                    item.get("canonical_name"),
                    parse_numeric(item.get("value")),
                    item.get("raw_value"),
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
              fiscal_period, confidence, evidence_id, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (metric_id) do update set value = excluded.value, raw = excluded.raw
            """,
            (
                item.get("metric_id") or stable_id(parse_run_id, item.get("canonical_name"), item.get("period_key")),
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
                Jsonb(item),
            ),
        )


def _insert_checks(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    payload = read_json(package_dir / "metrics" / "financial_checks.json")
    for index, check in enumerate(payload.get("checks") or [], start=1):
        check_id = stable_id(parse_run_id, check.get("rule_id"), check.get("period"), index)
        conn.execute(
            f"""
            insert into {schema}.financial_checks (
              check_id, filing_id, parse_run_id, rule_id, rule_name, statement_type,
              period_key, status, diff, tolerance, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (check_id) do update set status = excluded.status, raw = excluded.raw
            """,
            (
                check_id,
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
    quality = read_json(package_dir / "qa" / "quality_report.json")
    conn.execute(
        f"""
        insert into {schema}.quality_reports (
          parse_run_id, filing_id, overall_status, parser_status, rule_status,
          section_count, table_count, statement_table_count, raw_cell_count,
          normalized_metric_count, evidence_coverage_ratio, required_statement_status,
          critical_warnings, parser_warnings, rule_warnings, raw, updated_at
        ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
        on conflict (parse_run_id) do update set
          overall_status = excluded.overall_status,
          parser_status = excluded.parser_status,
          rule_status = excluded.rule_status,
          section_count = excluded.section_count,
          table_count = excluded.table_count,
          statement_table_count = excluded.statement_table_count,
          raw_cell_count = excluded.raw_cell_count,
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
            quality.get("normalized_metric_count"),
            parse_numeric(quality.get("evidence_coverage_ratio")),
            Jsonb(quality.get("required_statement_status") or {}),
            Jsonb(quality.get("critical_warnings") or []),
            Jsonb(quality.get("parser_warnings") or []),
            Jsonb(quality.get("rule_warnings") or []),
            Jsonb(quality),
        ),
    )


def _insert_retrieval_chunks(
    conn: Any,
    schema: str,
    manifest: dict[str, Any],
    financial_data: dict[str, Any],
    quality: dict[str, Any],
    source_map: dict[str, Any],
    parse_run_id: str,
    package_dir: Path,
) -> None:
    known_evidence_ids = set(_source_map_by_evidence_id(source_map))
    for row in build_retrieval_chunk_rows(manifest, financial_data, quality, source_map, parse_run_id, package_dir):
        evidence_id = row["evidence_id"] if row.get("evidence_id") in known_evidence_ids else None
        conn.execute(
            f"""
            insert into {schema}.retrieval_chunks (
              chunk_uid, filing_id, parse_run_id, company_id, ticker, collection_name, doc_type,
              section_title, statement_type, evidence_id, canonical_name, period_key, page_number,
              table_index, wiki_path, source_url, text, text_hash, metadata, updated_at
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
            on conflict (chunk_uid) do update set
              text = excluded.text,
              text_hash = excluded.text_hash,
              metadata = excluded.metadata,
              page_number = excluded.page_number,
              table_index = excluded.table_index,
              updated_at = now()
            """,
            (
                row["chunk_uid"],
                row["filing_id"],
                row["parse_run_id"],
                row["company_id"],
                row["ticker"],
                row["collection_name"],
                row["doc_type"],
                row["section_title"],
                row["statement_type"],
                evidence_id,
                row["canonical_name"],
                row["period_key"],
                row["page_number"],
                row["table_index"],
                row["wiki_path"],
                row["source_url"],
                row["text"],
                row["text_hash"],
                Jsonb(row["metadata"]),
            ),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a HK market evidence package into PostgreSQL siq/pdf2md_hk.")
    parser.add_argument("package", type=Path, nargs="?")
    parser.add_argument("--package", dest="package_opt", type=Path)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--schema", default=os.environ.get("SIQ_HK_SCHEMA", "pdf2md_hk"))
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
