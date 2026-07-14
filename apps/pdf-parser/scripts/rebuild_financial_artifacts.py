#!/usr/bin/env python3
"""Rebuild derived quality and financial artifacts for parsed Markdown results."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

import app  # noqa: E402


def _task_rows(db_path: Path) -> dict[str, dict]:
    if not db_path.exists():
        return {}
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("select * from tasks").fetchall()
    except sqlite3.Error:
        return {}
    return {str(row["task_id"]): dict(row) for row in rows}


def _iter_markdown_results(results_dir: Path, task_ids: set[str] | None = None):
    for child in sorted(results_dir.iterdir()):
        if child.is_dir():
            task_id = child.name
            md_path = child / "result.md"
            result_dir = child
        elif child.suffix.lower() == ".md":
            task_id = child.stem
            md_path = child
            result_dir = child.parent
        else:
            continue
        if task_ids and task_id not in task_ids:
            continue
        if md_path.exists():
            yield task_id, md_path, result_dir


def _json_artifact(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _detect_result_market(result_dir: Path, task: dict | None = None, filename: str | None = None) -> str:
    metadata = _json_artifact(result_dir / "metadata.json")
    if isinstance(metadata, dict) and metadata.get("market"):
        return str(metadata.get("market") or "").upper()
    return app.financial_service.detect_market(task or {"task_id": result_dir.name, "filename": filename}, filename)


def _is_current(result_dir: Path, task: dict | None = None) -> bool:
    financial_data = _json_artifact(result_dir / "financial_data.json")
    financial_checks = _json_artifact(result_dir / "financial_checks.json")
    quality = _json_artifact(result_dir / "quality_report.json")
    task = task or {"task_id": result_dir.name}
    market = _detect_result_market(result_dir, task, task.get("filename"))
    return (
        app._financial_artifacts_are_current(financial_data, financial_checks)
        and app.financial_service.financial_artifacts_match_market(market, financial_data, financial_checks)
        and isinstance(quality, dict)
        and quality.get("schema_version") == app.QUALITY_SCHEMA_VERSION
    )


def rebuild_one(
    task_id: str,
    md_path: Path,
    result_dir: Path,
    filename: str,
    dry_run: bool = False,
    task_row: dict | None = None,
):
    task = {**(task_row or {}), "task_id": task_id, "filename": filename}
    stale = not _is_current(result_dir, task)
    if dry_run:
        return {"task_id": task_id, "status": "stale" if stale else "current", "path": str(md_path)}

    markdown = md_path.read_text(encoding="utf-8", errors="ignore")
    report = app._write_quality_artifacts(
        task,
        markdown,
        file_name=filename,
        content_list=app._load_json_artifact(task, "content_list.json"),
    )
    return {
        "task_id": task_id,
        "status": "rebuilt",
        "path": str(md_path),
        "financial_status": report.get("financial_overall_status"),
        "statement_count": report.get("financial_statement_count", 0),
        "key_metric_count": report.get("financial_key_metric_count", 0),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", default=app.RESULTS_FOLDER, help="Parsed results directory.")
    parser.add_argument("--db", default=app.DB_PATH, help="Task database path for resolving filenames.")
    parser.add_argument("--task-id", action="append", default=[], help="Only rebuild this task id. Can be repeated.")
    parser.add_argument("--market", action="append", default=[], help="Only rebuild results for this market, e.g. EU. Can be repeated.")
    parser.add_argument("--force", action="store_true", help="Rebuild even when artifacts are already current.")
    parser.add_argument("--dry-run", action="store_true", help="Only report which tasks are stale/current.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of markdown results to process.")
    parser.add_argument("--order", choices=("path", "recent"), default="path", help="Process path order or most recent tasks first.")
    args = parser.parse_args(argv)

    results_dir = Path(args.results_dir)
    app.RESULTS_FOLDER = str(results_dir)
    task_rows = _task_rows(Path(args.db))
    task_ids = set(args.task_id) if args.task_id else None
    markets = {str(item).upper() for item in args.market if str(item).strip()}

    processed = 0
    rebuilt = 0
    skipped = 0
    failed = 0
    items = list(_iter_markdown_results(results_dir, task_ids=task_ids))
    if args.order == "recent":
        items.sort(
            key=lambda item: (
                task_rows.get(item[0], {}).get("completed_at") or task_rows.get(item[0], {}).get("created_at") or "",
                item[0],
            ),
            reverse=True,
        )
    for task_id, md_path, result_dir in items:
        if args.limit and processed >= args.limit:
            break
        processed += 1
        try:
            filename = task_rows.get(task_id, {}).get("filename", md_path.name)
            market = _detect_result_market(result_dir, {"task_id": task_id, "filename": filename}, filename)
            if markets and market not in markets:
                continue
            if not args.force and not args.dry_run and _is_current(result_dir, {"task_id": task_id, "filename": filename}):
                skipped += 1
                print(json.dumps({"task_id": task_id, "status": "current", "path": str(md_path)}, ensure_ascii=False))
                continue
            info = rebuild_one(
                task_id,
                md_path,
                result_dir,
                filename=filename,
                dry_run=args.dry_run,
                task_row=task_rows.get(task_id),
            )
            if info["status"] == "rebuilt":
                rebuilt += 1
            else:
                skipped += int(info["status"] == "current")
            print(json.dumps(info, ensure_ascii=False))
        except Exception as exc:
            failed += 1
            print(json.dumps({"task_id": task_id, "status": "error", "error": str(exc), "path": str(md_path)}, ensure_ascii=False))

    print(
        json.dumps(
            {
                "summary": {
                    "processed": processed,
                    "rebuilt": rebuilt,
                    "skipped": skipped,
                    "failed": failed,
                }
            },
            ensure_ascii=False,
        )
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
