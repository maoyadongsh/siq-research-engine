#!/usr/bin/env python3
"""Diagnose table coverage between Markdown and MinerU content_list artifacts."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sqlite3
import sys


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

import app  # noqa: E402


TABLE_RE = re.compile(r"<table\b.*?</table>", flags=re.IGNORECASE | re.DOTALL)


def _task_rows(db_path: Path) -> dict[str, dict]:
    if not db_path.exists():
        return {}
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT task_id, filename, status, completed_at, created_at
                FROM tasks
                """
            ).fetchall()
    except sqlite3.Error:
        return {}
    return {
        str(row["task_id"]): {
            "filename": str(row["filename"] or row["task_id"]),
            "status": str(row["status"] or ""),
            "completed_at": str(row["completed_at"] or ""),
            "created_at": str(row["created_at"] or ""),
        }
        for row in rows
    }


def _iter_result_dirs(results_dir: Path, task_ids: set[str] | None = None):
    if not results_dir.exists():
        return
    for child in sorted(results_dir.iterdir()):
        if not child.is_dir():
            continue
        if task_ids and child.name not in task_ids:
            continue
        md_path = child / "result.md"
        if md_path.exists():
            yield child.name, child, md_path


def _json_artifact(path: Path):
    if not path.exists():
        return None
    try:
        return app._coerce_json_artifact(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None


def _artifact_search_text(result_dir: Path, names: tuple[str, ...]) -> dict[str, str]:
    payloads = {}
    for name in names:
        path = result_dir / name
        payload = _json_artifact(path)
        if payload is None:
            payloads[name] = ""
            continue
        try:
            payloads[name] = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            payloads[name] = str(payload)
    return payloads


def _content_list_stats(content_list) -> dict:
    content_list = app._coerce_json_artifact(content_list)
    if not isinstance(content_list, list):
        return {
            "block_count": 0,
            "block_types": {},
            "table_blocks": 0,
            "table_blocks_with_body": 0,
            "table_blocks_without_body": 0,
            "pages": 0,
        }
    block_types = Counter(item.get("type") for item in content_list if isinstance(item, dict))
    pages = {
        item.get("page_idx")
        for item in content_list
        if isinstance(item, dict) and isinstance(item.get("page_idx"), int)
    }
    table_blocks = block_types.get("table", 0)
    with_body = sum(
        1
        for item in content_list
        if isinstance(item, dict) and item.get("type") == "table" and item.get("table_body")
    )
    return {
        "block_count": len(content_list),
        "block_types": dict(block_types),
        "table_blocks": table_blocks,
        "table_blocks_with_body": with_body,
        "table_blocks_without_body": table_blocks - with_body,
        "pages": len(pages),
    }


def _pop_bucket(bucket: list[dict] | None, used_source_ids: set[int]):
    if not bucket:
        return None
    while bucket:
        source = bucket.pop(0)
        source_id = source.get("source_id")
        if source_id in used_source_ids:
            continue
        used_source_ids.add(source_id)
        return source
    return None


def _match_markdown_tables(markdown: str, content_list) -> tuple[Counter, list[dict]]:
    table_sources = app._content_table_sources(content_list)
    exact_sources, normalized_sources = app._content_table_source_maps(table_sources)
    used_source_ids: set[int] = set()
    markers = app._pdf_page_markers_by_line(markdown)
    counts: Counter = Counter()
    extras: list[dict] = []

    for index, match in enumerate(TABLE_RE.finditer(markdown or ""), start=1):
        table_html = match.group(0).strip()
        line = markdown.count("\n", 0, match.start()) + 1
        source = _pop_bucket(exact_sources.get(table_html), used_source_ids)
        match_type = "content_list_body_exact" if source else ""
        if source is None:
            normalized_html = app._normalized_table_html_for_match(table_html)
            source = _pop_bucket(normalized_sources.get(normalized_html), used_source_ids)
            match_type = "content_list_body_normalized" if source else ""
        if source is not None:
            counts[match_type] += 1
            continue

        inferred_page, inferred_reason = app._inferred_pdf_page_for_line(line, markers)
        counts["markdown_extra"] += 1
        if inferred_page:
            counts["markdown_extra_with_marker_page"] += 1
        else:
            counts["markdown_extra_without_page"] += 1
        extras.append(
            {
                "table_index": index,
                "line": line,
                "inferred_page": inferred_page,
                "inferred_reason": inferred_reason,
                "rows": app._count_table_rows(table_html),
                "cells": app._count_table_cells(table_html),
                "text": app._strip_html(table_html)[:180],
                "html": table_html,
            }
        )
    counts["markdown_tables"] = len(list(TABLE_RE.finditer(markdown or "")))
    counts["content_table_bodies"] = len(table_sources)
    return counts, extras


def _counts_from_enhanced(enhanced: dict) -> tuple[Counter, list[dict]]:
    counts: Counter = Counter()
    extras: list[dict] = []
    tables = enhanced.get("tables") or []
    for item in tables:
        source = item.get("source") or "unresolved"
        if source in {"content_list_body_exact", "content_list_body_normalized"}:
            counts[source] += 1
        else:
            counts["markdown_extra"] += 1
            if item.get("pdf_page_number"):
                counts["markdown_extra_with_marker_page"] += 1
            else:
                counts["markdown_extra_without_page"] += 1
            extras.append(
                {
                    "table_index": item.get("table_index"),
                    "line": item.get("line"),
                    "inferred_page": item.get("pdf_page_number"),
                    "inferred_reason": item.get("pdf_page_inference_reason"),
                    "rows": item.get("rows"),
                    "cells": item.get("cells"),
                    "text": item.get("preview") or "",
                }
            )
    counts["markdown_tables"] = len(tables)
    counts["content_table_bodies"] = int(enhanced.get("content_table_body_count") or 0)
    return counts, extras


def _artifact_hits_for_extras(extras: list[dict], artifact_texts: dict[str, str]) -> Counter:
    hits: Counter = Counter()
    for extra in extras:
        needle = re.sub(r"\s+", "", extra.get("text") or "")[:60]
        if len(needle) < 12:
            hits["unsearched_short_needle"] += 1
            continue
        found = False
        for name, text in artifact_texts.items():
            if not text:
                continue
            normalized_text = re.sub(r"\s+", "", text)
            if needle in normalized_text:
                hits[f"found_in_{name}"] += 1
                found = True
                break
        if not found:
            hits["not_found_in_artifacts"] += 1
    return hits


def diagnose_one(
    task_id: str,
    result_dir: Path,
    md_path: Path,
    filename: str,
    search_artifacts: bool = False,
    sample_limit: int = 5,
) -> dict:
    markdown = md_path.read_text(encoding="utf-8", errors="ignore")
    content_list = _json_artifact(result_dir / "content_list.json")
    content_stats = _content_list_stats(content_list)
    enhanced = _json_artifact(result_dir / "content_list_enhanced.json")
    if isinstance(enhanced, dict) and int(enhanced.get("schema_version") or 0) >= 1:
        match_counts, extras = _counts_from_enhanced(enhanced)
    else:
        match_counts, extras = _match_markdown_tables(markdown, content_list)
    source_counts = {
        "content_list_body_exact": match_counts.get("content_list_body_exact", 0),
        "content_list_body_normalized": match_counts.get("content_list_body_normalized", 0),
        "markdown_extra": match_counts.get("markdown_extra", 0),
        "markdown_extra_with_marker_page": match_counts.get("markdown_extra_with_marker_page", 0),
        "markdown_extra_without_page": match_counts.get("markdown_extra_without_page", 0),
    }
    artifact_hits = Counter()
    if search_artifacts and extras:
        artifact_hits = _artifact_hits_for_extras(
            extras,
            _artifact_search_text(result_dir, ("middle.json", "model_output.json")),
        )

    markdown_tables = match_counts.get("markdown_tables", 0)
    precise_tables = source_counts["content_list_body_exact"] + source_counts["content_list_body_normalized"]
    return {
        "task_id": task_id,
        "filename": filename,
        "markdown_chars": len(markdown),
        "markdown_tables": markdown_tables,
        "content_list": content_stats,
        "source_counts": source_counts,
        "artifact_hits": dict(artifact_hits),
        "precise_table_rate": round(precise_tables / markdown_tables, 4) if markdown_tables else 0,
        "extra_table_rate": round(source_counts["markdown_extra"] / markdown_tables, 4) if markdown_tables else 0,
        "samples": [
            {
                key: value
                for key, value in item.items()
                if key not in {"html"}
            }
            for item in extras[:sample_limit]
        ],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", default=app.RESULTS_FOLDER, help="Parsed results directory.")
    parser.add_argument("--db", default=app.DB_PATH, help="Task database path for resolving filenames.")
    parser.add_argument("--task-id", action="append", default=[], help="Only diagnose this task id. Can be repeated.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of result directories to process.")
    parser.add_argument("--order", choices=("path", "recent"), default="recent", help="Process path order or most recent tasks first.")
    parser.add_argument("--search-artifacts", action="store_true", help="Search unmatched tables in middle/model_output JSON text.")
    parser.add_argument("--sample-limit", type=int, default=5, help="Number of unmatched table samples to include per task.")
    args = parser.parse_args(argv)

    results_dir = Path(args.results_dir)
    task_rows = _task_rows(Path(args.db))
    task_ids = set(args.task_id) if args.task_id else None
    items = list(_iter_result_dirs(results_dir, task_ids=task_ids))
    if args.order == "recent":
        items.sort(
            key=lambda item: (
                task_rows.get(item[0], {}).get("completed_at") or task_rows.get(item[0], {}).get("created_at") or "",
                item[0],
            ),
            reverse=True,
        )

    summary = Counter()
    processed = 0
    for task_id, result_dir, md_path in items:
        if args.limit and processed >= args.limit:
            break
        processed += 1
        filename = task_rows.get(task_id, {}).get("filename", md_path.name)
        info = diagnose_one(
            task_id,
            result_dir,
            md_path,
            filename=filename,
            search_artifacts=args.search_artifacts,
            sample_limit=args.sample_limit,
        )
        print(json.dumps(info, ensure_ascii=False))
        summary["tasks"] += 1
        summary["markdown_tables"] += info["markdown_tables"]
        summary["content_list_table_blocks"] += info["content_list"]["table_blocks"]
        summary["content_list_table_bodies"] += info["content_list"]["table_blocks_with_body"]
        for key, value in info["source_counts"].items():
            summary[key] += int(value or 0)
        for key, value in info["artifact_hits"].items():
            summary[f"artifact_{key}"] += int(value or 0)

    precise = summary["content_list_body_exact"] + summary["content_list_body_normalized"]
    total = summary["markdown_tables"]
    summary_payload = {
        "summary": dict(summary),
        "rates": {
            "precise_table_rate": round(precise / total, 4) if total else 0,
            "extra_table_rate": round(summary["markdown_extra"] / total, 4) if total else 0,
            "extra_with_marker_page_rate": round(summary["markdown_extra_with_marker_page"] / summary["markdown_extra"], 4)
            if summary["markdown_extra"]
            else 0,
        },
    }
    print(json.dumps(summary_payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
