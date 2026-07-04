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
RULES_SRC = REPO_ROOT / "services" / "market-report-rules" / "src"
if str(RULES_SRC) not in sys.path:
    sys.path.insert(0, str(RULES_SRC))

from market_report_rules_service.evidence_package import compute_artifact_hashes, stable_id, stable_parse_run_id, validate_evidence_package

DDL_PATH = REPO_ROOT / "db" / "ddl" / "020_create_pdf2md_hk_schema.sql"


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    payload = json.loads(text)
    return payload if isinstance(payload, dict) else {}


def parse_date(value: Any) -> Any:
    return str(value)[:10] if value else None


def parse_numeric(value: Any) -> Any:
    if value in (None, ""):
        return None
    return str(value)


def _first_value(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None


def _page_number(item: dict[str, Any]) -> Any:
    page_number = _first_value(item, "page_number", "pdf_page_number", "page")
    if page_number not in (None, ""):
        return page_number
    page_idx = item.get("page_idx")
    try:
        return int(page_idx) + 1
    except (TypeError, ValueError):
        return None


def _table_index(item: dict[str, Any]) -> Any:
    return _first_value(item, "table_index", "content_table_source_id", "source_table_index")


def _payload_items(payload: dict[str, Any], *keys: str) -> list[tuple[str, dict[str, Any]]]:
    body = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
    items: list[tuple[str, dict[str, Any]]] = []
    for key in keys:
        values = body.get(key) if isinstance(body, dict) else None
        if isinstance(values, list):
            items.extend((key, item) for item in values if isinstance(item, dict))
    return items


def database_url(explicit: str | None) -> str:
    url = explicit or os.environ.get("DATABASE_URL")
    if url:
        return url.replace("postgresql+psycopg://", "postgresql://")
    db = (
        os.environ.get("SIQ_HK_PGDATABASE")
        or os.environ.get("SIQ_PGDATABASE")
        or os.environ.get("PGDATABASE")
        or "siq_hk"
    )
    host = os.environ.get("SIQ_PGHOST") or os.environ.get("PGHOST") or "127.0.0.1"
    port = os.environ.get("SIQ_PGPORT") or os.environ.get("PGPORT") or "15432"
    user = os.environ.get("SIQ_PGUSER") or os.environ.get("PGUSER") or "postgres"
    password = os.environ.get("SIQ_PGPASSWORD") or os.environ.get("PGPASSWORD") or ""
    auth = f"{user}:{password}" if password else user
    return f"postgresql://{auth}@{host}:{port}/{db}"


def validate_schema(schema: str) -> None:
    if schema != "pdf2md_hk":
        raise SystemExit("HK imports must target schema pdf2md_hk")


def run_ddl(conn: Any) -> None:
    conn.execute(DDL_PATH.read_text(encoding="utf-8"))


def import_package(conn: Any, package_dir: Path, schema: str = "pdf2md_hk") -> str:
    validate_schema(schema)
    validation = validate_evidence_package(package_dir)
    if not validation.ok:
        raise SystemExit("Invalid evidence package: " + "; ".join(validation.errors))
    manifest = validation.manifest
    if manifest.get("market") != "HK":
        raise SystemExit("manifest market must be HK")
    artifact_hashes = manifest.get("artifact_hashes") or compute_artifact_hashes(package_dir)
    parse_run_id = manifest.get("parse_run_id") or stable_parse_run_id(manifest, artifact_hashes)
    quality = read_json(package_dir / "qa" / "quality_report.json")
    warnings = (quality.get("critical_warnings") or []) + (quality.get("parser_warnings") or []) + (quality.get("rule_warnings") or [])

    with conn.transaction():
        _upsert_company(conn, schema, manifest)
        _upsert_filing(conn, schema, manifest, package_dir, quality)
        _upsert_parse_run(conn, schema, manifest, package_dir, parse_run_id, artifact_hashes, quality, warnings)
        _delete_run_rows(conn, schema, parse_run_id)
        _insert_artifacts(conn, schema, package_dir, parse_run_id)
        _insert_parser_artifacts(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        _insert_content_blocks(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        _insert_footnotes(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        _insert_toc_entries(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        _insert_financial_note_links(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        _insert_table_relations(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        _insert_table_quality_signals(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        _insert_sections(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        _insert_pdf_pages(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        _insert_tables(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        _insert_evidence(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        _insert_financial_facts(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        _insert_checks(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
        _insert_quality_report(conn, schema, package_dir, manifest["filing_id"], parse_run_id)
    return parse_run_id


def _upsert_company(conn: Any, schema: str, manifest: dict[str, Any]) -> None:
    conn.execute(
        f"""
        insert into {schema}.companies (
          company_id, ticker, company_name, stock_code, hkex_stock_code, short_name,
          company_name_en, company_name_zh, aliases, raw, updated_at
        ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
        on conflict (company_id) do update set
          ticker = excluded.ticker,
          company_name = excluded.company_name,
          stock_code = excluded.stock_code,
          hkex_stock_code = excluded.hkex_stock_code,
          short_name = excluded.short_name,
          company_name_en = excluded.company_name_en,
          company_name_zh = excluded.company_name_zh,
          aliases = excluded.aliases,
          raw = excluded.raw,
          updated_at = now()
        """,
        (
            manifest["company_id"],
            manifest["ticker"],
            manifest.get("company_name"),
            manifest.get("stock_code"),
            manifest.get("hkex_stock_code"),
            manifest.get("short_name"),
            manifest.get("company_name_en"),
            manifest.get("company_name_zh"),
            Jsonb(manifest.get("aliases") or []),
            Jsonb(manifest),
        ),
    )


def _upsert_filing(conn: Any, schema: str, manifest: dict[str, Any], package_dir: Path, quality: dict[str, Any]) -> None:
    conn.execute(
        f"""
        insert into {schema}.filings (
          filing_id, company_id, ticker, form, report_type, fiscal_year, fiscal_period,
          period_end, published_at, source_id, source_url, local_path, accounting_standard,
          quality_status, raw, updated_at
        ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
        on conflict (filing_id) do update set
          ticker = excluded.ticker,
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
        "quality_reports",
        "financial_checks",
        "operating_metric_facts",
        "financial_facts",
        "evidence_citations",
        "table_quality_signals",
        "table_relations",
        "financial_note_links",
        "toc_entries",
        "footnotes",
        "content_blocks",
        "parser_artifacts",
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


def _insert_parser_artifacts(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    parser_dir = package_dir / "parser"
    if not parser_dir.is_dir():
        return
    for path in sorted(parser_dir.rglob("*.json")):
        payload = read_json(path)
        if not payload:
            continue
        rel = path.relative_to(package_dir).as_posix()
        conn.execute(
            f"""
            insert into {schema}.parser_artifacts (
              parse_run_id, filing_id, artifact_key, local_path, page_number,
              table_index, target, schema_version, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                parse_run_id,
                filing_id,
                rel,
                rel,
                _page_number(payload),
                _table_index(payload),
                payload.get("target"),
                payload.get("schema_version"),
                Jsonb(payload),
            ),
        )


def _insert_content_blocks(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    artifact_key = "parser/content_list_enhanced.json"
    payload = read_json(package_dir / artifact_key)
    body = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
    blocks = body.get("blocks") or body.get("content_blocks") if isinstance(body, dict) else []
    if not blocks:
        artifact_key = "parser/document_full.json"
        document_full = read_json(package_dir / artifact_key)
        blocks = document_full.get("content_list") if isinstance(document_full.get("content_list"), list) else []
    if not isinstance(blocks, list):
        return
    for index, block in enumerate((item for item in blocks if isinstance(item, dict)), start=1):
        page_number = _page_number(block)
        table_index = _table_index(block)
        target_id = _first_value(block, "block_id", "id", "content_id", "target", "type", "block_type")
        block_id = stable_id(parse_run_id, artifact_key, page_number, table_index, target_id, index)
        conn.execute(
            f"""
            insert into {schema}.content_blocks (
              block_id, filing_id, parse_run_id, page_number, table_index, target,
              block_type, block_order, markdown_path, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                block_id,
                filing_id,
                parse_run_id,
                page_number,
                table_index,
                block.get("target"),
                block.get("block_type") or block.get("type"),
                _first_value(block, "block_order", "order", "reading_order") or index,
                block.get("markdown_path"),
                Jsonb(block),
            ),
        )


def _insert_footnotes(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    artifact_key = "qa/footnotes.json"
    payload = read_json(package_dir / artifact_key)
    for index, (group, item) in enumerate(
        _payload_items(payload, "references", "definitions", "bindings", "footnotes"),
        start=1,
    ):
        page_number = _page_number(item)
        table_index = _table_index(item)
        target_id = _first_value(item, "id", "footnote_id", "reference_id", "definition_id", "marker", "note")
        footnote_id = stable_id(parse_run_id, artifact_key, page_number, table_index, target_id, index)
        conn.execute(
            f"""
            insert into {schema}.footnotes (
              footnote_id, filing_id, parse_run_id, page_number, table_index,
              target, footnote_key, content, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                footnote_id,
                filing_id,
                parse_run_id,
                page_number,
                table_index,
                item.get("target"),
                _first_value(item, "footnote_key", "id", "marker", "reference_id", "definition_id", "note"),
                _first_value(item, "content", "text", "definition"),
                Jsonb({"group": group, **item}),
            ),
        )


def _insert_toc_entries(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    artifact_key = "qa/toc.json"
    payload = read_json(package_dir / artifact_key)
    for index, (group, item) in enumerate(
        _payload_items(payload, "headings", "toc_candidates", "content_headings", "entries"),
        start=1,
    ):
        page_number = _page_number(item)
        table_index = _table_index(item)
        title = _first_value(item, "title", "text", "heading")
        toc_entry_id = stable_id(parse_run_id, artifact_key, page_number, table_index, title, index)
        conn.execute(
            f"""
            insert into {schema}.toc_entries (
              toc_entry_id, filing_id, parse_run_id, page_number, table_index,
              target, title, level, destination_page_number, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                toc_entry_id,
                filing_id,
                parse_run_id,
                page_number,
                table_index,
                item.get("target"),
                title,
                item.get("level"),
                _first_value(item, "destination_page_number", "destination_page", "page_number", "page"),
                Jsonb({"group": group, **item}),
            ),
        )


def _insert_financial_note_links(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    artifact_key = "qa/financial_note_links.json"
    payload = read_json(package_dir / artifact_key)
    for index, (_group, item) in enumerate(_payload_items(payload, "links", "financial_note_links"), start=1):
        page_number = _page_number(item)
        table_index = _table_index(item)
        note_key = _first_value(item, "note_key", "note", "marker")
        target_id = _first_value(item, "id", "link_id") or note_key or _first_value(item, "note_target", "target")
        link_id = stable_id(parse_run_id, artifact_key, page_number, table_index, target_id, index)
        conn.execute(
            f"""
            insert into {schema}.financial_note_links (
              link_id, filing_id, parse_run_id, page_number, table_index,
              target, note_key, note_target, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                link_id,
                filing_id,
                parse_run_id,
                page_number,
                table_index,
                item.get("target") or item.get("statement"),
                note_key,
                _first_value(item, "note_target", "target_note", "destination"),
                Jsonb(item),
            ),
        )


def _insert_table_relations(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    artifact_key = "parser/table_relations.json"
    payload = read_json(package_dir / artifact_key)
    for index, (_group, item) in enumerate(_payload_items(payload, "relations", "table_relations"), start=1):
        page_number = _page_number(item)
        table_index = _table_index(item)
        related_table_id = _first_value(item, "related_table_id", "target_table_id", "target_table_index")
        target_id = _first_value(item, "id", "relation_id") or related_table_id or _first_value(item, "target", "relation_type", "type")
        relation_id = stable_id(parse_run_id, artifact_key, page_number, table_index, target_id, index)
        conn.execute(
            f"""
            insert into {schema}.table_relations (
              relation_id, filing_id, parse_run_id, page_number, table_index,
              target, related_table_id, relation_type, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                relation_id,
                filing_id,
                parse_run_id,
                page_number,
                table_index,
                item.get("target") or item.get("source_table_id"),
                related_table_id,
                item.get("relation_type") or item.get("type"),
                Jsonb(item),
            ),
        )


def _insert_table_quality_signals(conn: Any, schema: str, package_dir: Path, filing_id: str, parse_run_id: str) -> None:
    artifact_key = "qa/table_quality_signals.json"
    payload = read_json(package_dir / artifact_key)
    for index, (group, item) in enumerate(_payload_items(payload, "signals", "tables", "table_quality_signals"), start=1):
        page_number = _page_number(item)
        table_index = _table_index(item)
        signal_type = _first_value(item, "signal_type", "type", "status")
        target_id = _first_value(item, "id", "signal_id") or signal_type or _first_value(item, "target", "table_id")
        signal_id = stable_id(parse_run_id, artifact_key, page_number, table_index, target_id, index)
        conn.execute(
            f"""
            insert into {schema}.table_quality_signals (
              signal_id, filing_id, parse_run_id, page_number, table_index,
              target, signal_type, signal_value, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                signal_id,
                filing_id,
                parse_run_id,
                page_number,
                table_index,
                item.get("target") or item.get("table_id"),
                signal_type,
                _first_value(item, "signal_value", "value", "score", "status"),
                Jsonb({"group": group, **item}),
            ),
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
              evidence_id, filing_id, parse_run_id, source_type, source_id, page_number,
              table_index, row_index, column_index, quote_text, local_path, source_url, target, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (evidence_id) do update set
              parse_run_id = excluded.parse_run_id,
              quote_text = excluded.quote_text,
              raw = excluded.raw
            """,
            (
                item.get("evidence_id"),
                filing_id,
                parse_run_id,
                item.get("source_type"),
                item.get("source_id"),
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a HK market evidence package into PostgreSQL siq/pdf2md_hk.")
    parser.add_argument("package", type=Path, nargs="?")
    parser.add_argument("--package", dest="package_opt", type=Path)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--schema", default=os.environ.get("SIQ_HK_SCHEMA", "pdf2md_hk"))
    parser.add_argument("--ddl", "--run-ddl", action="store_true", help="Run DDL before importing")
    parser.add_argument("--ddl-only", action="store_true")
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
        parse_run_id = import_package(conn, package_dir.resolve(), args.schema)
        conn.commit()
    print(parse_run_id)


if __name__ == "__main__":
    main()
