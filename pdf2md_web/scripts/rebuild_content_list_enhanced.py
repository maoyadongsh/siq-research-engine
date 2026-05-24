#!/usr/bin/env python3
"""Rebuild content_list_enhanced.json artifacts without touching Markdown/PDF.

Optionally writes result_complete.md, a Markdown companion that keeps the
original result.md intact and appends recoverable PDF structure metadata.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from zoneinfo import ZoneInfo


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

import app  # noqa: E402


def _parse_since(value: str | None, tz_name: str):
    if not value:
        return None
    raw = value.strip()
    try:
        if raw.endswith("Z"):
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(tz_name))
    return parsed.astimezone(timezone.utc)


def _task_completed_at(row: dict):
    value = row.get("completed_at") or row.get("created_at")
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _task_rows(db_path: Path, limit: int, since=None, task_ids: set[str] | None = None):
    if not db_path.exists():
        return []
    query = """
        SELECT task_id, filename, status, created_at, completed_at, pdf_page_count, markdown_path
        FROM tasks
        WHERE status IN ('completed', 'done', 'success')
          AND COALESCE(cancelled, 0) = 0
    """
    params: list[object] = []
    if task_ids:
        placeholders = ",".join("?" for _ in task_ids)
        query += f" AND task_id IN ({placeholders})"
        params.extend(sorted(task_ids))
    query += " ORDER BY COALESCE(completed_at, created_at) DESC"
    if limit and not since:
        query += " LIMIT ?"
        params.append(limit)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(row) for row in conn.execute(query, params).fetchall()]
    if since:
        rows = [row for row in rows if (_task_completed_at(row) and _task_completed_at(row) >= since)]
        if limit:
            rows = rows[:limit]
    return rows


def _json_artifact(path: Path):
    if not path.exists():
        return None
    try:
        return app._coerce_json_artifact(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_complete_markdown(md_path: Path, enhanced: dict) -> Path:
    original = md_path.read_text(encoding="utf-8", errors="ignore").rstrip()
    complete_path = md_path.with_name("result_complete.md")
    complete_path.write_text(original + app._complete_markdown_appendix(enhanced), encoding="utf-8")
    return complete_path


def rebuild_one(task: dict, results_dir: Path, write_complete_markdown: bool = False) -> dict:
    task_id = str(task["task_id"])
    result_dir = results_dir / task_id
    md_path = Path(task["markdown_path"]) if task.get("markdown_path") else result_dir / "result.md"
    fallback_md_path = result_dir / "result.md"
    try:
        md_path.relative_to(results_dir)
    except ValueError:
        if fallback_md_path.exists():
            md_path = fallback_md_path
    if not md_path.exists() and fallback_md_path.exists():
        md_path = fallback_md_path
    if not md_path.exists():
        return {"task_id": task_id, "filename": task.get("filename"), "status": "missing_markdown"}
    content_list = _json_artifact(result_dir / "content_list.json")
    markdown = md_path.read_text(encoding="utf-8", errors="ignore")
    enhanced = app._build_content_list_enhanced(
        markdown,
        content_list=content_list,
        report_year=app._detect_report_year(markdown, file_name=task.get("filename")),
    )
    _write_json(result_dir / "content_list_enhanced.json", enhanced)
    complete_path = None
    if write_complete_markdown:
        complete_path = _write_complete_markdown(md_path, enhanced)
    document_full_path = None
    document_full = _json_artifact(result_dir / "document_full.json")
    if isinstance(document_full, dict):
        document_full["content_list_enhanced"] = enhanced
        document_full.setdefault("artifacts", {})["content_list_enhanced.json"] = {
            "exists": True,
            "path": str(result_dir / "content_list_enhanced.json"),
            "url": f"/api/artifact/{task_id}/content_list_enhanced.json",
        }
        if complete_path:
            document_full.setdefault("source_files", {}).setdefault("complete_markdown", {})["path"] = str(complete_path)
        _write_json(result_dir / "document_full.json", document_full)
        document_full_path = result_dir / "document_full.json"
    signals = enhanced.get("quality_signals") or {}
    payload = {
        "task_id": task_id,
        "filename": task.get("filename"),
        "status": "rebuilt",
        "schema_version": enhanced.get("schema_version"),
        "table_count": enhanced.get("table_count"),
        "table_missing_page_count": signals.get("table_missing_page_count"),
        "footnote_reference_count": signals.get("footnote_reference_count"),
        "toc_candidate_count": signals.get("toc_candidate_count"),
        "financial_note_link_count": signals.get("financial_note_link_count"),
        "image_semantic_block_count": signals.get("image_semantic_block_count"),
        "image_semantic_recognized_count": signals.get("image_semantic_recognized_count"),
        "image_semantic_show_count": signals.get("image_semantic_show_count"),
        "image_semantic_ocr_candidate_count": signals.get("image_semantic_ocr_candidate_count"),
    }
    if complete_path:
        payload["complete_markdown_path"] = str(complete_path)
    if document_full_path:
        payload["document_full_path"] = str(document_full_path)
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", default=app.RESULTS_FOLDER)
    parser.add_argument("--db", default=app.DB_PATH)
    parser.add_argument("--limit", type=int, default=110)
    parser.add_argument("--since", default="")
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--task-id", action="append", default=[])
    parser.add_argument("--details", action="store_true")
    parser.add_argument("--write-complete-md", action="store_true", help="Also write result_complete.md next to result.md.")
    args = parser.parse_args(argv)

    results_dir = Path(args.results_dir)
    since = _parse_since(args.since, args.timezone)
    task_ids = set(args.task_id) if args.task_id else None
    rows = _task_rows(Path(args.db), args.limit, since=since, task_ids=task_ids)
    summary = {
        "tasks": len(rows),
        "rebuilt": 0,
        "missing_markdown": 0,
        "schema_versions": {},
        "tables": 0,
        "missing_page_tables": 0,
        "footnote_references": 0,
        "toc_candidates": 0,
        "financial_note_links": 0,
        "image_semantic_blocks": 0,
        "image_semantic_recognized": 0,
        "image_semantic_show": 0,
        "image_semantic_ocr_candidates": 0,
    }
    for task in rows:
        info = rebuild_one(task, results_dir, write_complete_markdown=args.write_complete_md)
        if args.details:
            print(json.dumps(info, ensure_ascii=False))
        status = info.get("status")
        summary[status] = summary.get(status, 0) + 1
        if status == "rebuilt":
            version = str(info.get("schema_version"))
            summary["schema_versions"][version] = summary["schema_versions"].get(version, 0) + 1
            summary["tables"] += int(info.get("table_count") or 0)
            summary["missing_page_tables"] += int(info.get("table_missing_page_count") or 0)
            summary["footnote_references"] += int(info.get("footnote_reference_count") or 0)
            summary["toc_candidates"] += int(info.get("toc_candidate_count") or 0)
            summary["financial_note_links"] += int((info.get("financial_note_link_count") or 0))
            summary["image_semantic_blocks"] += int((info.get("image_semantic_block_count") or 0))
            summary["image_semantic_recognized"] += int((info.get("image_semantic_recognized_count") or 0))
            summary["image_semantic_show"] += int((info.get("image_semantic_show_count") or 0))
            summary["image_semantic_ocr_candidates"] += int((info.get("image_semantic_ocr_candidate_count") or 0))
            if info.get("complete_markdown_path"):
                summary["complete_markdown"] = summary.get("complete_markdown", 0) + 1
            if info.get("document_full_path"):
                summary["document_full"] = summary.get("document_full", 0) + 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("missing_markdown", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
