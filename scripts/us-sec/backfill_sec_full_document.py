#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import build_sec_wiki_index
from sec_evidence_lib import (
    DEFAULT_PARSER_RESULTS_ROOT,
    compute_artifact_hashes,
    read_json,
    stable_parse_run_id,
    us_sec_parser_task_id,
    write_full_document_layer,
    write_json,
)
from sec_wiki_ingestion_rules import (
    REQUIRED_PARSER_RESULT_FILES,
    REQUIRED_WIKI_READY_FILES,
    WIKI_INGESTION_PLAN_PATH,
    build_wiki_ingestion_plan,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "data" / "wiki" / "us"
REQUIRED_FULL_DOCUMENT_FILES = (*REQUIRED_WIKI_READY_FILES, WIKI_INGESTION_PLAN_PATH)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_csv_set(value: str | None, *, upper: bool = True) -> set[str] | None:
    if not value:
        return None
    items = {item.strip() for item in value.split(",") if item.strip()}
    if upper:
        items = {item.upper() for item in items}
    return items or None


def discover_packages(
    output_root: Path,
    *,
    forms: set[str] | None = None,
    tickers: set[str] | None = None,
    limit: int = 0,
) -> list[Path]:
    form_filter = {item.upper() for item in forms} if forms else None
    ticker_filter = {item.upper() for item in tickers} if tickers else None
    packages: list[Path] = []
    for manifest_path in sorted((output_root / "companies").glob("*/reports/*/manifest.json")):
        manifest = read_json(manifest_path)
        if manifest.get("market") != "US":
            continue
        form = str(manifest.get("form") or "").upper()
        ticker = str(manifest.get("ticker") or "").upper()
        if form_filter and form not in form_filter:
            continue
        if ticker_filter and ticker not in ticker_filter:
            continue
        packages.append(manifest_path.parent)
        if limit and len(packages) >= limit:
            break
    return packages


def backfill_full_documents(
    output_root: Path,
    *,
    forms: set[str] | None = None,
    tickers: set[str] | None = None,
    limit: int = 0,
    dry_run: bool = False,
    force: bool = False,
    no_index: bool = False,
    parser_results_root: Path | None = None,
) -> dict[str, Any]:
    output_root = output_root.resolve()
    parser_results_root = (parser_results_root or DEFAULT_PARSER_RESULTS_ROOT).resolve()
    packages = discover_packages(output_root, forms=forms, tickers=tickers, limit=limit)
    items: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()

    for package_dir in packages:
        try:
            item = _backfill_package(
                package_dir,
                dry_run=dry_run,
                force=force,
                parser_results_root=parser_results_root,
            )
        except Exception as exc:
            item = {
                "status": "failed",
                "package_path": _repo_relative(package_dir),
                "error": str(exc),
            }
        items.append(item)
        status_counts[str(item.get("status") or "unknown")] += 1

    index_summary = None
    if not dry_run and not no_index:
        index_summary = build_sec_wiki_index.build_wiki_index(output_root)

    return {
        "schema_version": "sec_full_document_backfill_report_v1",
        "generated_at": now_iso(),
        "output_root": str(output_root),
        "parser_results_root": str(parser_results_root),
        "dry_run": dry_run,
        "force": force,
        "candidate_count": len(packages),
        "status_counts": dict(status_counts),
        "items": items,
        "index": index_summary,
    }


def _backfill_package(package_dir: Path, *, dry_run: bool, force: bool, parser_results_root: Path) -> dict[str, Any]:
    manifest_path = package_dir / "manifest.json"
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("market") != "US":
        return {"status": "skipped", "package_path": _repo_relative(package_dir), "reason": "not_us_package"}

    raw_path = package_dir / str(manifest.get("local_source_path") or "raw/filing.htm")
    if not raw_path.exists():
        return {
            "status": "failed",
            "package_path": _repo_relative(package_dir),
            "filing_id": manifest.get("filing_id"),
            "reason": "missing_raw_filing",
            "missing": [str(raw_path.relative_to(package_dir))],
        }
    missing = _missing_full_document_files(package_dir, manifest=manifest, raw_path=raw_path, parser_results_root=parser_results_root)
    if not missing and not force:
        return {
            "status": "ready",
            "package_path": _repo_relative(package_dir),
            "filing_id": manifest.get("filing_id"),
            "parser_result_dir": manifest.get("parser_result_dir") or _repo_relative(parser_results_root / us_sec_parser_task_id(manifest, raw_path)),
            "missing": [],
        }
    if dry_run:
        return {
            "status": "would_update",
            "package_path": _repo_relative(package_dir),
            "filing_id": manifest.get("filing_id"),
            "parser_result_dir": manifest.get("parser_result_dir") or _repo_relative(parser_results_root / us_sec_parser_task_id(manifest, raw_path)),
            "missing": missing,
        }

    source_map = read_json(package_dir / "qa" / "source_map.json")
    quality = read_json(package_dir / "qa" / "quality_report.json")
    source_map, quality, manifest = write_full_document_layer(
        package_dir,
        manifest,
        source_map,
        quality,
        parser_results_root=parser_results_root,
    )
    write_json(package_dir / "qa" / "source_map.json", source_map)
    write_json(package_dir / "qa" / "quality_report.json", quality)
    manifest["quality_status"] = quality.get("overall_status") or manifest.get("quality_status")
    manifest["artifact_hashes"] = compute_artifact_hashes(package_dir)
    manifest["parse_run_id"] = stable_parse_run_id(manifest, manifest["artifact_hashes"])
    write_json(manifest_path, manifest)
    return {
        "status": "updated",
        "package_path": _repo_relative(package_dir),
        "filing_id": manifest.get("filing_id"),
        "missing_before": missing,
        "parser_result_dir": manifest.get("parser_result_dir"),
        "parse_run_id": manifest.get("parse_run_id"),
    }


def _missing_full_document_files(
    package_dir: Path,
    *,
    manifest: dict[str, Any],
    raw_path: Path,
    parser_results_root: Path,
) -> list[str]:
    missing = [rel for rel in REQUIRED_FULL_DOCUMENT_FILES if not (package_dir / rel).exists()]
    parser_result_dir = parser_results_root / us_sec_parser_task_id(manifest, raw_path)
    for rel in REQUIRED_PARSER_RESULT_FILES:
        if not (parser_result_dir / rel).exists():
            missing.append(f"parser_result:{rel}")
    quality = read_json(package_dir / "qa" / "quality_report.json")
    plan_manifest = {
        **manifest,
        "parser_result_dir": _repo_relative(parser_result_dir),
        "parser_result_task_id": parser_result_dir.name,
    }
    current_plan = build_wiki_ingestion_plan(
        package_dir=package_dir,
        manifest=plan_manifest,
        quality=quality,
        parser_result_dir=parser_result_dir,
        repo_root=REPO_ROOT,
    )
    if not current_plan.get("ready") and not missing:
        missing.append(f"wiki_ingestion_plan:status:{current_plan.get('status') or 'unknown'}")
        for warning in current_plan.get("warnings") or []:
            missing.append(f"wiki_ingestion_plan:{warning}")
    return missing


def _repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill US SEC Wiki full HTML document layer artifacts.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--forms", default="", help="Comma-separated SEC forms. Empty means all.")
    parser.add_argument("--tickers", default="", help="Comma-separated ticker filter. Empty means all.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-index", action="store_true")
    parser.add_argument("--parser-results-root", type=Path, default=DEFAULT_PARSER_RESULTS_ROOT)
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args()

    report = backfill_full_documents(
        args.output_root,
        forms=parse_csv_set(args.forms),
        tickers=parse_csv_set(args.tickers),
        limit=args.limit,
        dry_run=args.dry_run,
        force=args.force,
        no_index=args.no_index,
        parser_results_root=args.parser_results_root,
    )
    if args.report and not args.dry_run:
        report_path = args.report if args.report.is_absolute() else REPO_ROOT / args.report
        write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
