#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from hk_evidence_lib import (
    REPO_ROOT,
    _company_dir_name,
    _rel_to,
    _report_id,
    _write_company_wiki_indexes,
    compute_artifact_hashes,
    read_json,
    stable_id,
    stable_parse_run_id,
    validate_evidence_package,
    write_json,
)


DEFAULT_LEGACY_ROOT = REPO_ROOT / "data" / "wiki" / "hk_reports"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "data" / "wiki" / "hk"


def _manifest_paths(legacy_root: Path) -> list[Path]:
    return sorted(path for path in legacy_root.glob("*/*/*/manifest.json") if path.is_file())


def _copy_package(source: Path, target: Path, *, force: bool) -> None:
    if target.exists():
        if not force:
            return
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)


def _quality(package_dir: Path) -> dict[str, Any]:
    payload = read_json(package_dir / "qa" / "quality_report.json", {})
    return payload if isinstance(payload, dict) else {}


def _normalized_manifest(manifest: dict[str, Any], *, output_root: Path, company_dir: Path, package_dir: Path) -> dict[str, Any]:
    filing_key = manifest.get("accession_number") or str(manifest.get("filing_id") or "").rsplit(":", 1)[-1]
    report_id = manifest.get("report_id") or _report_id(manifest.get("fiscal_year"), str(manifest.get("report_type") or "annual"), str(filing_key or "unknown"))
    normalized = {
        **manifest,
        "market": "HK",
        "report_id": report_id,
        "stock_code": manifest.get("stock_code") or manifest.get("ticker"),
        "hkex_stock_code": manifest.get("hkex_stock_code") or manifest.get("ticker"),
        "exchange": manifest.get("exchange") or "HKEX",
        "company_wiki_id": company_dir.name,
        "company_wiki_path": _rel_to(output_root, company_dir),
        "wiki_company_path": _rel_to(output_root, company_dir),
        "wiki_report_path": _rel_to(output_root, package_dir),
    }
    artifact_hashes = compute_artifact_hashes(package_dir)
    normalized["artifact_hashes"] = artifact_hashes
    normalized["parse_run_id"] = normalized.get("parse_run_id") or stable_parse_run_id(normalized, artifact_hashes)
    if not normalized.get("filing_id"):
        normalized["filing_id"] = stable_id("HK", normalized.get("ticker"), filing_key)
    return normalized


def migrate_package(source_package: Path, output_root: Path, *, force: bool = False) -> Path:
    manifest = read_json(source_package / "manifest.json", {})
    if not isinstance(manifest, dict) or manifest.get("market") != "HK":
        raise ValueError(f"not a HK package: {source_package}")
    ticker = str(manifest.get("ticker") or manifest.get("stock_code") or source_package.parents[1].name).zfill(5)
    company_name = str(manifest.get("company_name") or ticker)
    filing_key = manifest.get("accession_number") or source_package.name.rsplit("_", 1)[-1]
    report_id = manifest.get("report_id") or _report_id(manifest.get("fiscal_year"), str(manifest.get("report_type") or "annual"), str(filing_key or "unknown"))
    company_dir = output_root / "companies" / _company_dir_name(ticker, company_name)
    target_package = company_dir / "reports" / str(report_id)
    _copy_package(source_package, target_package, force=force)
    normalized = _normalized_manifest(manifest, output_root=output_root, company_dir=company_dir, package_dir=target_package)
    write_json(target_package / "manifest.json", normalized)
    _write_company_wiki_indexes(output_root, company_dir, normalized, _quality(target_package))
    return target_package


def migrate_packages(legacy_root: Path, output_root: Path, *, force: bool = False, limit: int = 0) -> dict[str, Any]:
    legacy_root = legacy_root.resolve()
    output_root = output_root.resolve()
    manifest_paths = _manifest_paths(legacy_root)
    if limit:
        manifest_paths = manifest_paths[:limit]
    summary: dict[str, Any] = {
        "legacy_root": str(legacy_root),
        "output_root": str(output_root),
        "candidates": len(manifest_paths),
        "migrated": 0,
        "failed": 0,
        "validation_failed": 0,
        "items": [],
    }
    for manifest_path in manifest_paths:
        source_package = manifest_path.parent
        try:
            target_package = migrate_package(source_package, output_root, force=force)
            validation = validate_evidence_package(target_package)
            if not validation.ok:
                summary["validation_failed"] += 1
            summary["migrated"] += 1
            summary["items"].append(
                {
                    "status": "migrated" if validation.ok else "validation_failed",
                    "source": str(source_package),
                    "target": str(target_package),
                    "errors": validation.errors,
                }
            )
        except Exception as exc:
            summary["failed"] += 1
            summary["items"].append({"status": "failed", "source": str(source_package), "error": str(exc)})
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate legacy data/wiki/hk_reports packages into A-share-aligned HK company Wiki layout.")
    parser.add_argument("--legacy-root", type=Path, default=DEFAULT_LEGACY_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--report", type=Path, default=REPO_ROOT / "data" / "wiki" / "hk" / "_meta" / "migration_report.json")
    args = parser.parse_args()

    summary = migrate_packages(args.legacy_root, args.output_root, force=args.force, limit=args.limit)
    report_path = args.report if args.report.is_absolute() else REPO_ROOT / args.report
    write_json(report_path, summary)
    print(json.dumps({key: summary[key] for key in ("candidates", "migrated", "failed", "validation_failed")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
