#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import build_sec_wiki_index
from audit_sec_financial_recognition import audit_packages
from sec_evidence_lib import (
    apply_us_financial_review_policy_for_periods,
    compute_artifact_hashes,
    read_json,
    stable_parse_run_id,
    write_json,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "data" / "wiki" / "us"
DEFAULT_REPORT_PATH = DEFAULT_OUTPUT_ROOT / "_meta" / "financial_review_policy_backfill.json"
DEFAULT_AUDIT_PATH = DEFAULT_OUTPUT_ROOT / "_meta" / "financial_recognition_audit.json"


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
    packages: list[Path] = []
    for manifest_path in sorted((output_root / "companies").glob("*/reports/*/manifest.json")):
        manifest = read_json(manifest_path)
        if manifest.get("market") != "US":
            continue
        ticker = str(manifest.get("ticker") or "").upper()
        form = str(manifest.get("form") or "").upper()
        if tickers and ticker not in tickers:
            continue
        if forms and form not in forms:
            continue
        packages.append(manifest_path.parent)
        if limit and len(packages) >= limit:
            break
    return packages


def backfill_financial_review_policy(
    output_root: Path,
    *,
    forms: set[str] | None = None,
    tickers: set[str] | None = None,
    limit: int = 0,
    dry_run: bool = False,
    no_index: bool = False,
    no_audit: bool = False,
) -> dict[str, Any]:
    output_root = output_root.resolve()
    packages = discover_packages(output_root, forms=forms, tickers=tickers, limit=limit)
    items: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    downgraded_count = 0
    for package_dir in packages:
        try:
            item = _backfill_package(package_dir, dry_run=dry_run)
        except Exception as exc:
            item = {"status": "failed", "package_path": repo_relative(package_dir), "error": str(exc)}
        items.append(item)
        status_counts[str(item.get("status") or "unknown")] += 1
        downgraded_count += int(item.get("downgraded_check_count") or 0)

    index_summary = None
    audit_summary = None
    if not dry_run:
        if not no_index:
            index_summary = build_sec_wiki_index.build_wiki_index(output_root)
        if not no_audit:
            audit_summary = audit_packages(output_root, tickers=tickers, forms=forms, limit=limit)
            write_json(DEFAULT_AUDIT_PATH if output_root == DEFAULT_OUTPUT_ROOT.resolve() else output_root / "_meta" / "financial_recognition_audit.json", audit_summary)

    return {
        "schema_version": "sec_financial_review_policy_backfill_v1",
        "generated_at": now_iso(),
        "output_root": str(output_root),
        "dry_run": dry_run,
        "candidate_count": len(packages),
        "status_counts": dict(status_counts),
        "downgraded_check_count": downgraded_count,
        "items": items,
        "index": index_summary,
        "audit": _audit_summary(audit_summary) if audit_summary else None,
    }


def _backfill_package(package_dir: Path, *, dry_run: bool) -> dict[str, Any]:
    manifest_path = package_dir / "manifest.json"
    manifest = read_json(manifest_path)
    if manifest.get("market") != "US":
        return {"status": "skipped", "package_path": repo_relative(package_dir), "reason": "not_us_package"}

    checks_path = package_dir / "metrics" / "financial_checks.json"
    quality_path = package_dir / "qa" / "quality_report.json"
    metrics_path = package_dir / "metrics" / "normalized_metrics.json"
    financial_checks = read_json(checks_path)
    normalized_metrics = read_json(metrics_path)
    if not financial_checks or not normalized_metrics:
        return {
            "status": "failed",
            "package_path": repo_relative(package_dir),
            "reason": "missing_financial_checks_or_metrics",
        }

    balance_sheet_total_periods = _balance_sheet_total_periods(normalized_metrics)
    updated_checks = apply_us_financial_review_policy_for_periods(financial_checks, balance_sheet_total_periods)
    downgraded = int((updated_checks.get("review_policy") or {}).get("downgraded_check_count") or 0)
    if updated_checks == financial_checks:
        return {
            "status": "ready",
            "package_path": repo_relative(package_dir),
            "filing_id": manifest.get("filing_id"),
            "downgraded_check_count": 0,
        }
    if dry_run:
        return {
            "status": "would_update",
            "package_path": repo_relative(package_dir),
            "filing_id": manifest.get("filing_id"),
            "downgraded_check_count": downgraded,
        }

    quality = read_json(quality_path)
    quality["overall_status"] = updated_checks.get("overall_status") or quality.get("overall_status")
    quality["financial_review_policy"] = updated_checks.get("review_policy") or {}
    quality["rule_warnings"] = updated_checks.get("warnings") or quality.get("rule_warnings") or []
    parser_warnings = quality.get("parser_warnings") if isinstance(quality.get("parser_warnings"), list) else []
    quality["warnings"] = _dedupe([*parser_warnings, *quality["rule_warnings"]])

    manifest["quality_status"] = quality.get("overall_status") or manifest.get("quality_status")
    write_json(checks_path, updated_checks)
    write_json(quality_path, quality)
    manifest["artifact_hashes"] = compute_artifact_hashes(package_dir)
    manifest["parse_run_id"] = stable_parse_run_id(manifest, manifest["artifact_hashes"])
    write_json(manifest_path, manifest)
    return {
        "status": "updated",
        "package_path": repo_relative(package_dir),
        "filing_id": manifest.get("filing_id"),
        "downgraded_check_count": downgraded,
        "quality_status": manifest.get("quality_status"),
        "parse_run_id": manifest.get("parse_run_id"),
    }


def _balance_sheet_total_periods(normalized_metrics: dict[str, Any]) -> set[str]:
    periods: set[str] = set()
    for metric in normalized_metrics.get("metrics") or []:
        if not isinstance(metric, dict):
            continue
        if metric.get("canonical_name") in {"total_assets", "total_liabilities_and_equity"} and metric.get("period_key"):
            periods.add(str(metric["period_key"]))
    return periods


def _audit_summary(report: dict[str, Any] | None) -> dict[str, Any] | None:
    if not report:
        return None
    return {
        "package_count": report.get("package_count"),
        "status_counts": report.get("status_counts"),
        "warning_class_counts": report.get("warning_class_counts"),
        "bridge_gap_counts": report.get("bridge_gap_counts"),
    }


def _dedupe(items: list[Any]) -> list[Any]:
    seen: set[str] = set()
    out: list[Any] = []
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply US-only financial review policy calibration to existing SEC Wiki packages.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--forms", default="", help="Comma-separated SEC forms. Empty means all.")
    parser.add_argument("--tickers", default="", help="Comma-separated ticker filter. Empty means all.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-index", action="store_true")
    parser.add_argument("--no-audit", action="store_true")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    args = parser.parse_args()

    report = backfill_financial_review_policy(
        args.output_root,
        forms=parse_csv_set(args.forms),
        tickers=parse_csv_set(args.tickers),
        limit=args.limit,
        dry_run=args.dry_run,
        no_index=args.no_index,
        no_audit=args.no_audit,
    )
    if args.report and not args.dry_run:
        report_path = args.report if args.report.is_absolute() else REPO_ROOT / args.report
        write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
