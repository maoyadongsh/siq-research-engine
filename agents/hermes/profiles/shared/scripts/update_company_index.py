#!/usr/bin/env python3
"""Maintain ``wiki/companies/<id>/_index.json`` after SIQ artifacts change.

This is the cross-product cleanup recommended in the audit. Rather than having
the frontend (or human reviewers) scan multiple subdirectories per company,
each agent updates a single index file at the end of its run.

Usage:

    python3 update_company_index.py \
        --company-dir /home/maoyd/siq-research-engine/data/wiki/companies/600399-抚顺特钢

It walks ``analysis/``, ``factcheck/``, ``tracking/``, ``legal/`` and emits a
deterministic JSON pointing at the freshest artifact in each, plus the verdict
and warnings (when the artifact already encodes them in JSON).

Idempotent. Designed to be safe to call from cron, hooks, or end-of-run shell.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def latest_file(folder: Path, suffix: str = ".html", *, exclude_dir_names: tuple[str, ...] = ("archive", ".work")) -> Path | None:
    if not folder.exists():
        return None
    best: tuple[float, Path] | None = None
    for item in folder.rglob(f"*{suffix}"):
        if not item.is_file():
            continue
        if any(part in exclude_dir_names for part in item.parts):
            continue
        mtime = item.stat().st_mtime
        if best is None or mtime > best[0]:
            best = (mtime, item)
    return best[1] if best else None


def latest_pair(folder: Path, basename_suffix: str) -> tuple[Path | None, Path | None, Path | None]:
    """Return the latest (.md, .json, .html) trio for a given suffix in folder."""
    if not folder.exists():
        return None, None, None
    md_path = latest_file(folder, ".md")
    json_path = None
    html_path = None
    if md_path is not None:
        stem = md_path.with_suffix("")
        json_candidate = stem.with_suffix(".json")
        html_candidate = stem.with_suffix(".html")
        json_path = json_candidate if json_candidate.exists() else None
        html_path = html_candidate if html_candidate.exists() else None
    return md_path, json_path, html_path


def describe_file(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    stat = path.stat()
    return {
        "path": str(path),
        "size_bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
    }


def collect_analysis(company_dir: Path) -> dict[str, Any]:
    folder = company_dir / "analysis"
    md, jsonf, html = latest_pair(folder, "-analysis")
    summary: dict[str, Any] = {
        "md": describe_file(md),
        "json": describe_file(jsonf),
        "html": describe_file(html),
    }
    if jsonf is not None:
        data = read_json(jsonf) or {}
        meta = data.get("report_meta") or {}
        template = data.get("template") or {}
        quality = data.get("quality_report") or {}
        summary["report_meta"] = {
            "company_id": meta.get("company_id"),
            "report_year": meta.get("report_year"),
            "task_id": meta.get("task_id"),
            "generated_at": meta.get("generated_at"),
        }
        summary["template_id"] = template.get("template_id")
        summary["module_count"] = quality.get("module_count")
        summary["all_key_numbers_have_evidence"] = quality.get("all_key_numbers_have_evidence")
        review_queue = quality.get("review_queue")
        if isinstance(review_queue, list):
            summary["review_queue_count"] = len(review_queue)
    work_dir = folder / ".work"
    if work_dir.exists():
        validations = sorted(work_dir.glob("*/final_validation.json"))
        if validations:
            latest_v = max(validations, key=lambda p: p.stat().st_mtime)
            data = read_json(latest_v) or {}
            summary["validation_ok"] = bool(data.get("ok"))
            summary["validation_failures_count"] = len(data.get("failures") or [])
            summary["validation_warnings_count"] = len(data.get("warnings") or [])
    return summary


def collect_factcheck(company_dir: Path) -> dict[str, Any]:
    folder = company_dir / "factcheck"
    md, jsonf, html = latest_pair(folder, "-factcheck")
    summary: dict[str, Any] = {
        "json": describe_file(jsonf),
        "html": describe_file(html),
    }
    if jsonf is not None:
        data = read_json(jsonf) or {}
        summary["verdict"] = data.get("verdict")
        summary["summary"] = data.get("summary")
        summary["verified_at"] = data.get("verified_at")
    return summary


def collect_tracking(company_dir: Path) -> dict[str, Any]:
    folder = company_dir / "tracking"
    summary: dict[str, Any] = {
        "tracking_items": describe_file(folder / "tracking-items.md") if (folder / "tracking-items.md").exists() else None,
        "latest_html": describe_file(latest_file(folder, ".html")),
        "latest_alert": describe_file(latest_file(folder / "alerts", ".md")),
        "latest_metrics": describe_file(latest_file(folder / "metrics", ".md")),
        "latest_update": describe_file(latest_file(folder / "updates", ".md")),
    }
    return summary


def collect_legal(company_dir: Path) -> dict[str, Any]:
    folder = company_dir / "legal"
    return {
        "latest_opinion": describe_file(latest_file(folder, ".html")),
    }


def collect_data_health(company_dir: Path) -> dict[str, Any]:
    metrics = describe_file(company_dir / "metrics" / "key_metrics.json")
    evidence = describe_file(company_dir / "evidence" / "evidence_index.json")
    company = describe_file(company_dir / "company.json")
    return {
        "company_json": company,
        "key_metrics_json": metrics,
        "evidence_index_json": evidence,
    }


def build_index(company_dir: Path) -> dict[str, Any]:
    company = read_json(company_dir / "company.json") or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "company_id": company.get("company_id") or company_dir.name,
        "stock_code": company.get("stock_code"),
        "company_short_name": company.get("company_short_name"),
        "industry": company.get("industry"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data": collect_data_health(company_dir),
        "analysis": collect_analysis(company_dir),
        "factcheck": collect_factcheck(company_dir),
        "tracking": collect_tracking(company_dir),
        "legal": collect_legal(company_dir),
    }


def write_index(company_dir: Path) -> Path:
    payload = build_index(company_dir)
    output = company_dir / "_index.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Update wiki/companies/<id>/_index.json")
    parser.add_argument("--company-dir", type=Path, required=True)
    parser.add_argument("--print", action="store_true", help="Echo the resulting index to stdout")
    args = parser.parse_args()
    company_dir = args.company_dir
    if not company_dir.exists() or not company_dir.is_dir():
        print(json.dumps({"ok": False, "error": f"company_dir not found: {company_dir}"}, ensure_ascii=False), file=sys.stderr)
        return 2
    output = write_index(company_dir)
    response: dict[str, Any] = {"ok": True, "index_path": str(output)}
    if args.print:
        response["index"] = read_json(output)
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
