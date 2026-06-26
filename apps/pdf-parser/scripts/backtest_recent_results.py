#!/usr/bin/env python3
"""Backtest recent parsed results with the same quality/financial merge path used by the UI."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
import sys


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

import app  # noqa: E402
from financial_extractor import build_financial_checks, build_financial_data  # noqa: E402


def _task_rows(db_path: Path, limit: int, task_ids: set[str] | None = None) -> list[dict]:
    if not db_path.exists():
        return []
    query = """
        SELECT task_id, filename, status, created_at, completed_at, pdf_page_count
        FROM tasks
        WHERE status IN ('completed', 'done', 'success')
    """
    params: list[object] = []
    if task_ids:
        placeholders = ",".join("?" for _ in task_ids)
        query += f" AND task_id IN ({placeholders})"
        params.extend(sorted(task_ids))
    query += " ORDER BY COALESCE(completed_at, created_at) DESC"
    if limit:
        query += " LIMIT ?"
        params.append(limit)
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(query, params).fetchall()]
    except sqlite3.Error:
        return []


def _merged_quality_report(task: dict, markdown: str, content_list):
    report = app._build_quality_report(
        markdown,
        task,
        file_name=task.get("filename"),
        content_list=content_list,
    )
    financial_data = build_financial_data(
        markdown,
        task_id=task.get("task_id"),
        filename=task.get("filename"),
    )
    financial_checks = build_financial_checks(financial_data)
    report = app._merge_quality_candidates_from_financial_data(report, financial_data)
    report["financial_summary"] = financial_checks.get("summary", {})
    report["financial_overall_status"] = financial_checks.get("overall_status")
    report["financial_statement_count"] = financial_data.get("summary", {}).get("statement_count", 0)
    report["financial_key_metric_count"] = financial_data.get("summary", {}).get("key_metric_count", 0)
    report["warnings"] = app._quality_report_warnings(report, financial_data)
    return report, financial_data, financial_checks


def backtest_one(task: dict, results_dir: Path) -> dict:
    task_id = task["task_id"]
    result_dir = results_dir / task_id
    md_path = result_dir / "result.md"
    if not md_path.exists():
        return {
            "task_id": task_id,
            "filename": task.get("filename"),
            "status": "missing_markdown",
        }

    markdown = md_path.read_text(encoding="utf-8", errors="ignore")
    content_list = app._load_json_artifact(task, "content_list.json")
    enhanced = app._build_content_list_enhanced(
        markdown,
        content_list=content_list,
        report_year=app._detect_report_year(markdown, file_name=task.get("filename")),
    )
    report, financial_data, financial_checks = _merged_quality_report(task, markdown, content_list)
    fail_items = [item for item in financial_checks.get("checks") or [] if item.get("status") == "fail"]
    missing_core = [
        item.get("name")
        for item in report.get("core_financial_table_candidates") or []
        if item.get("status") != "found"
    ]
    source_counts = enhanced.get("source_counts") or {}
    table_count = int(enhanced.get("table_count") or 0)
    exact_count = int(source_counts.get("content_list_body_exact") or 0)
    return {
        "task_id": task_id,
        "filename": task.get("filename"),
        "status": "ok",
        "report_kind": financial_data.get("report_kind") or report.get("report_kind"),
        "markdown_chars": len(markdown),
        "table_count": int(report.get("table_count") or 0),
        "source_counts": source_counts,
        "precise_source_rate": round(exact_count / table_count, 4) if table_count else 0,
        "missing_pdf_page_tables": sum(1 for item in report.get("table_index") or [] if not item.get("pdf_page_number")),
        "bbox_tables": sum(1 for item in report.get("table_index") or [] if item.get("bbox")),
        "missing_core": missing_core,
        "financial_overall_status": financial_checks.get("overall_status"),
        "financial_summary": financial_checks.get("summary", {}),
        "financial_statement_count": financial_data.get("summary", {}).get("statement_count", 0),
        "financial_key_metric_count": financial_data.get("summary", {}).get("key_metric_count", 0),
        "fail_examples": [
            item.get("name") or item.get("formula") or item.get("message") or item.get("rule")
            for item in fail_items[:5]
        ],
        "warning_count": len(report.get("warnings") or [])
        + len(financial_data.get("warnings") or [])
        + len(financial_checks.get("warnings") or []),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", default=app.RESULTS_FOLDER, help="Parsed results directory.")
    parser.add_argument("--db", default=app.DB_PATH, help="Task database path.")
    parser.add_argument("--limit", type=int, default=80, help="Maximum recent tasks to backtest.")
    parser.add_argument("--task-id", action="append", default=[], help="Only backtest this task id. Can be repeated.")
    parser.add_argument("--details", action="store_true", help="Print one JSON line per task before the summary.")
    args = parser.parse_args(argv)

    results_dir = Path(args.results_dir)
    app.RESULTS_FOLDER = str(results_dir)
    task_ids = set(args.task_id) if args.task_id else None
    rows = _task_rows(Path(args.db), args.limit, task_ids=task_ids)

    summary = Counter()
    source_counts = Counter()
    report_kinds = Counter()
    financial_status = Counter()
    fail_examples = []
    quality_gap_examples = []
    low_precise_examples = []

    for task in rows:
        info = backtest_one(task, results_dir)
        if args.details:
            print(json.dumps(info, ensure_ascii=False))
        if info.get("status") != "ok":
            summary[info.get("status") or "error"] += 1
            continue

        summary["tasks"] += 1
        summary["markdown_chars"] += int(info["markdown_chars"])
        summary["tables"] += int(info["table_count"])
        summary["missing_pdf_page_tables"] += int(info["missing_pdf_page_tables"])
        summary["bbox_tables"] += int(info["bbox_tables"])
        summary["financial_checks_total"] += int((info["financial_summary"] or {}).get("total") or 0)
        summary["financial_checks_fail"] += int((info["financial_summary"] or {}).get("fail") or 0)
        summary["financial_checks_warning"] += int((info["financial_summary"] or {}).get("warning") or 0)
        summary["financial_checks_skipped"] += int((info["financial_summary"] or {}).get("skipped") or 0)
        summary["financial_statement_count"] += int(info["financial_statement_count"] or 0)
        summary["financial_key_metric_count"] += int(info["financial_key_metric_count"] or 0)
        source_counts.update(info.get("source_counts") or {})
        report_kinds[info.get("report_kind") or "unknown"] += 1
        financial_status[info.get("financial_overall_status") or "unknown"] += 1

        if info.get("fail_examples"):
            fail_examples.append(
                {
                    "task_id": info["task_id"],
                    "filename": info["filename"],
                    "examples": info["fail_examples"],
                }
            )
        if info.get("missing_core") and info.get("report_kind") == "annual_report":
            quality_gap_examples.append(
                {
                    "task_id": info["task_id"],
                    "filename": info["filename"],
                    "missing_core": info["missing_core"],
                }
            )
        if float(info.get("precise_source_rate") or 0) < 0.6:
            low_precise_examples.append(
                {
                    "task_id": info["task_id"],
                    "filename": info["filename"],
                    "table_count": info["table_count"],
                    "precise_source_rate": info["precise_source_rate"],
                    "source_counts": info["source_counts"],
                }
            )

    total_tables = int(summary["tables"] or 0)
    exact = int(source_counts.get("content_list_body_exact") or 0)
    inferred = int(source_counts.get("markdown_marker_inferred") or 0)
    payload = {
        "summary": dict(summary),
        "source_counts": dict(source_counts),
        "source_rates": {
            "exact_rate": round(exact / total_tables, 4) if total_tables else 0,
            "inferred_rate": round(inferred / total_tables, 4) if total_tables else 0,
            "bbox_rate": round(summary["bbox_tables"] / total_tables, 4) if total_tables else 0,
            "missing_page_rate": round(summary["missing_pdf_page_tables"] / total_tables, 4) if total_tables else 0,
        },
        "report_kinds": dict(report_kinds),
        "financial_status": dict(financial_status),
        "fail_examples": fail_examples[:20],
        "quality_gap_examples": quality_gap_examples[:20],
        "low_precise_source_examples": sorted(
            low_precise_examples,
            key=lambda item: item["precise_source_rate"],
        )[:20],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if fail_examples or quality_gap_examples else 0


if __name__ == "__main__":
    raise SystemExit(main())
