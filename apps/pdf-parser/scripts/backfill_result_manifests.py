#!/usr/bin/env python3
"""Backfill metadata and hash manifests for existing PDF parser results."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
import sys


BASE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BASE_DIR.parents[1]
sys.path.insert(0, str(BASE_DIR))

import pdf_parser_result_manifest_service as manifests  # noqa: E402


def load_submit_config(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def task_rows(db_path: Path) -> list[dict]:
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = []
        for row in conn.execute("SELECT * FROM tasks ORDER BY created_at ASC").fetchall():
            task = dict(row)
            task["submit_config"] = load_submit_config(task.get("submit_config_json"))
            rows.append(task)
        return rows


def discover_result_dirs(results_dir: Path, task_ids: list[str] | None = None) -> list[Path]:
    if task_ids:
        result_dirs: list[Path] = []
        seen: set[str] = set()
        for task_id in task_ids:
            if task_id in seen:
                continue
            seen.add(task_id)
            result_dir = results_dir / task_id
            if result_dir.is_dir():
                result_dirs.append(result_dir)
        return result_dirs
    return sorted(path for path in results_dir.iterdir() if path.is_dir())


def fallback_task_from_dir(result_dir: Path) -> dict:
    return {
        "task_id": result_dir.name,
        "filename": None,
        "markdown_path": str(result_dir / "result.md"),
        "status": None,
        "stage": None,
        "submit_config": {},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(REPO_ROOT / "data/pdf-parser/db/tasks.db"))
    parser.add_argument("--results-dir", default=str(REPO_ROOT / "data/pdf-parser/results"))
    parser.add_argument("--task-id", action="append", default=[], help="Backfill one parser result directory. Can be repeated.")
    parser.add_argument("--apply", action="store_true", help="Write metadata.json, artifact_manifest.json, and hash_manifest.json.")
    parser.add_argument("--json-output", help="Optional path for a JSON run report.")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    results_dir = Path(args.results_dir)
    tasks = {task["task_id"]: task for task in task_rows(db_path)}
    result_dirs = discover_result_dirs(results_dir, args.task_id)
    if args.limit:
        result_dirs = result_dirs[: args.limit]

    items = []
    counts = Counter()
    for result_dir in result_dirs:
        task = tasks.get(result_dir.name) or fallback_task_from_dir(result_dir)
        metadata, artifact_manifest, hash_manifest = manifests.build_result_contract(
            task,
            result_dir,
            repo_root=REPO_ROOT,
        )
        if args.apply:
            manifests.write_json(result_dir / "metadata.json", metadata)
            manifests.write_json(result_dir / "artifact_manifest.json", artifact_manifest)
            manifests.write_json(result_dir / "hash_manifest.json", hash_manifest)
        status = (artifact_manifest.get("core") or {}).get("status")
        market = metadata.get("market") or "UNKNOWN"
        counts[f"status:{status}"] += 1
        counts[f"market:{market}"] += 1
        items.append(
            {
                "task_id": result_dir.name,
                "market": market,
                "status": status,
                "missing": (artifact_manifest.get("core") or {}).get("missing") or [],
                "invalid_json": (artifact_manifest.get("core") or {}).get("invalid_json") or [],
                "would_write": [
                    str(result_dir / "metadata.json"),
                    str(result_dir / "artifact_manifest.json"),
                    str(result_dir / "hash_manifest.json"),
                ],
            }
        )

    report = {
        "apply": args.apply,
        "db": str(db_path),
        "results_dir": str(results_dir),
        "scanned": len(result_dirs),
        "counts": dict(sorted(counts.items())),
        "items": items,
    }
    if args.json_output:
        output_path = Path(args.json_output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "items"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
