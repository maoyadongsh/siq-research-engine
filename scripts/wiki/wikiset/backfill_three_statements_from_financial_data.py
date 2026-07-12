#!/usr/bin/env python3
"""Backfill company three-statement metrics from current financial_data.json.

This repairs the final wiki ingestion layer without rebuilding report assets.
It uses the shared rules in rebuild_wiki_v2.py, so future full rebuilds and
in-place repairs produce the same metric payloads.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import rebuild_wiki_v2 as rebuild


def read_existing_payload(company_dir: Path) -> dict:
    wrapper = rebuild.read_json(company_dir / "metrics" / "three_statements.json", {}) or {}
    data = wrapper.get("data") if isinstance(wrapper, dict) else {}
    return data if isinstance(data, dict) else {}


def table_columns(cur: sqlite3.Cursor, table: str) -> list[str]:
    return [row[1] for row in cur.execute(f"pragma table_info({table})").fetchall()]


def insert_dynamic(cur: sqlite3.Cursor, table: str, values: dict, skip: set[str] | None = None) -> None:
    skip = skip or set()
    columns = [column for column in table_columns(cur, table) if column not in skip]
    placeholders = ",".join("?" for _ in columns)
    column_sql = ",".join(columns)
    cur.execute(
        f"insert or replace into {table} ({column_sql}) values ({placeholders})",
        [values.get(column) for column in columns],
    )


def recreate_sqlite(wiki_root: Path, company_catalog: list[dict], reports: list[dict], payloads: dict[str, dict]) -> None:
    db_path = wiki_root / "derived" / "financial_metrics.db"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("delete from three_statement_metrics")
    cur.execute("delete from validation_anomalies")
    cur.execute("delete from reports")
    cur.execute("delete from companies")

    company_by_code = {company["stock_code"]: company for company in company_catalog}
    report_by_code = {}
    for report in reports:
        report_by_code.setdefault(report["stock_code"], report)

    for company in company_catalog:
        insert_dynamic(cur, "companies", company)
    for report in reports:
        insert_dynamic(cur, "reports", report)

    metric_columns = [column for column in table_columns(cur, "three_statement_metrics") if column != "id"]
    placeholders = ",".join("?" for _ in metric_columns)
    column_sql = ",".join(metric_columns)
    for code, payload in payloads.items():
        report = report_by_code.get(code) or {}
        company = company_by_code.get(code) or {}
        for metric in payload.get("metrics") or []:
            source = metric.get("source") or {}
            page = source.get("pdf_page") or source.get("pdf_page_number")
            table_index = source.get("table_index")
            urls = rebuild.evidence_urls(source.get("task_id") or report.get("task_id"), page, table_index)
            source_kind = source.get("source_kind") or ""
            row = {
                "stock_code": code,
                "company_id": company.get("company_id"),
                "report_id": report.get("report_id") or company.get("primary_report_id"),
                "company_name": payload.get("company") or company.get("company_short_name"),
                "statement_type": metric.get("statement_type"),
                "metric_key": metric.get("metric_key"),
                "raw_value": metric.get("raw_value"),
                "normalized_value": metric.get("normalized_value"),
                "unit": "亿元",
                "md_line": source.get("md_line") or source.get("line"),
                "pdf_page_number": page,
                "table_index": table_index,
                "task_id": source.get("task_id") or report.get("task_id"),
                "open_pdf_page_url": urls["open_pdf_page_url"],
                "open_source_table_url": urls["open_source_table_url"],
                "extraction_method": "financial_data_statement_ingest_v1"
                if source_kind == "financial_data_statement"
                else "v6.41_rebuild",
            }
            cur.execute(
                f"insert into three_statement_metrics ({column_sql}) values ({placeholders})",
                [row.get(column) for column in metric_columns],
            )

    conn.commit()
    conn.close()


def backfill(args: argparse.Namespace) -> dict:
    wiki_root = Path(args.wiki_root)
    pdf2md_root = Path(args.pdf2md_root)
    results_root = Path(args.results_dir)
    tasks = rebuild.load_tasks(pdf2md_root / "tasks.db")
    v641 = {}
    for v641_path in (
        wiki_root / "extracted_three_statements_v6.41_test.json",
        Path("/home/maoyd/wiki_backup_20260522/derived/three_statements_latest.json"),
    ):
        loaded = rebuild.read_json(v641_path, {}) or {}
        if loaded:
            v641 = loaded
            break

    company_dirs = sorted(path for path in (wiki_root / "companies").iterdir() if path.is_dir())
    if args.codes:
        requested = {code.strip() for code in args.codes.split(",") if code.strip()}
        company_dirs = [
            path
            for path in company_dirs
            if (rebuild.read_json(path / "company.json", {}) or {}).get("stock_code") in requested
        ]

    catalog_wrapper = rebuild.read_json(wiki_root / "_meta" / "company_catalog.json", {}) or {}
    catalog_by_code = {
        company.get("stock_code"): company
        for company in catalog_wrapper.get("companies") or []
        if company.get("stock_code")
    }
    report_catalog_wrapper = rebuild.read_json(wiki_root / "_meta" / "report_catalog.json", {}) or {}
    report_catalog_by_key = {
        (report.get("stock_code"), report.get("report_id")): report
        for report in report_catalog_wrapper.get("reports") or []
        if report.get("stock_code") and report.get("report_id")
    }

    payloads: dict[str, dict] = {}
    report_rows: list[dict] = []
    summaries: list[dict] = []
    for company_dir in company_dirs:
        company = rebuild.read_json(company_dir / "company.json", {}) or {}
        code = company.get("stock_code")
        if not code:
            continue
        primary_report_id = company.get("primary_report_id") or ((company.get("reports") or [{}])[0]).get("report_id")
        report_entry = next(
            (report for report in company.get("reports") or [] if report.get("report_id") == primary_report_id),
            (company.get("reports") or [{}])[0],
        )
        task_id = report_entry.get("task_id")
        report_json = rebuild.read_json(company_dir / "reports" / primary_report_id / "report.json", {}) or {}
        result_dir = ((report_json.get("source") or {}).get("result_dir")) or str(results_root / str(task_id))
        row = rebuild.inspect_result_dir(Path(result_dir), tasks)
        row["identity"] = {
            "company_id": company.get("company_id"),
            "stock_code": company.get("stock_code"),
            "exchange": company.get("exchange"),
            "company_short_name": company.get("company_short_name"),
            "company_full_name": company.get("company_full_name"),
            "aliases": company.get("aliases") or [],
        }
        row["report_id"] = primary_report_id
        row["report_year"] = report_entry.get("report_year") or row.get("report_year")
        row["report_kind"] = report_entry.get("report_kind") or row.get("report_kind")

        existing_payload = read_existing_payload(company_dir)
        payload = rebuild.build_three_statement_payload(row, existing_payload)
        source = rebuild.three_statement_payload_source(payload)
        metric_count = len(payload.get("metrics") or [])
        payloads[code] = payload

        rebuild.write_json(
            company_dir / "metrics" / "three_statements.json",
            {
                "schema_version": 1,
                "source": source,
                "unit": "亿元",
                "data": payload,
                "generated_at": rebuild.now_iso(),
            },
        )

        validation_path = company_dir / "metrics" / "validation.json"
        validation = rebuild.read_json(validation_path, {}) or {}
        validation.update(
            {
                "schema_version": validation.get("schema_version") or 1,
                "wiki_v641_available": bool(v641.get(code)),
                "three_statement_source": source,
                "three_statement_metric_count": metric_count,
                "generated_at": rebuild.now_iso(),
            }
        )
        rebuild.write_json(validation_path, validation)

        catalog_entry = dict(catalog_by_code.get(code) or {})
        if not catalog_entry:
            catalog_entry = {
                "company_id": company.get("company_id"),
                "stock_code": code,
                "exchange": company.get("exchange"),
                "company_short_name": company.get("company_short_name"),
                "company_full_name": company.get("company_full_name"),
                "aliases": company.get("aliases") or [],
                "company_path": f"companies/{company.get('company_id')}",
                "primary_report_id": primary_report_id,
                "report_count": len(company.get("reports") or []),
                "status": report_entry.get("status") or "ready",
            }
        catalog_entry.update(
            {
                "has_three_statement_metrics": metric_count > 0,
                "three_statement_source": source,
                "three_statement_metric_count": metric_count,
            }
        )
        catalog_by_code[code] = catalog_entry

        report_row = {
            **{
                "company_id": company.get("company_id"),
                "stock_code": code,
                "exchange": company.get("exchange"),
                "company_short_name": company.get("company_short_name"),
                "company_full_name": company.get("company_full_name"),
                "aliases": company.get("aliases") or [],
                "company_path": f"companies/{company.get('company_id')}",
            },
            **report_entry,
        }
        existing_report_row = dict(report_catalog_by_key.get((code, report_row.get("report_id"))) or {})
        existing_report_row.update(report_row)
        report_row = existing_report_row
        report_rows.append(report_row)
        summaries.append(
            {
                "stock_code": code,
                "company_short_name": company.get("company_short_name"),
                "source": source,
                "metric_count": metric_count,
            }
        )

    company_catalog = [catalog_by_code[code] for code in sorted(payloads)]
    catalog_wrapper["schema_version"] = catalog_wrapper.get("schema_version") or 1
    catalog_wrapper["generated_at"] = rebuild.now_iso()
    catalog_wrapper["company_count"] = len(company_catalog)
    catalog_wrapper["companies"] = company_catalog
    rebuild.write_json(wiki_root / "_meta" / "company_catalog.json", catalog_wrapper)
    report_catalog_wrapper["schema_version"] = report_catalog_wrapper.get("schema_version") or 1
    report_catalog_wrapper["generated_at"] = rebuild.now_iso()
    report_catalog_wrapper["report_count"] = len(report_rows)
    report_catalog_wrapper["reports"] = sorted(report_rows, key=lambda item: (item.get("stock_code") or "", item.get("report_id") or ""))
    rebuild.write_json(wiki_root / "_meta" / "report_catalog.json", report_catalog_wrapper)
    rebuild.write_json(wiki_root / "derived" / "three_statements_latest.json", payloads)
    recreate_sqlite(wiki_root, company_catalog, report_rows, payloads)
    return {
        "wiki_root": str(wiki_root),
        "company_count": len(company_catalog),
        "metric_total": sum(item["metric_count"] for item in summaries),
        "companies": summaries,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wiki-root", default="/home/maoyd/wiki")
    parser.add_argument("--pdf2md-root", default="/home/maoyd/pdf2md_web")
    parser.add_argument("--results-dir", default="/home/maoyd/pdf2md_web/results")
    parser.add_argument("--codes", default="", help="Optional comma-separated stock codes.")
    args = parser.parse_args()
    print(rebuild.json.dumps(backfill(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
