#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from sec_evidence_lib import (
    REPO_ROOT,
    _write_company_wiki_indexes,
    company_wiki_dir_name,
    compute_artifact_hashes,
    read_json,
    repo_relative,
    stable_parse_run_id,
    us_report_id,
    write_json,
)


DEFAULT_LEGACY_ROOT = REPO_ROOT / "data" / "wiki" / "us_sec"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "data" / "wiki" / "us"


def _manifest_paths(legacy_root: Path) -> list[Path]:
    if not legacy_root.exists():
        return []
    return sorted(path for path in legacy_root.glob("*/*/*/manifest.json") if path.is_file())


def _copy_package(source: Path, target: Path, *, force: bool) -> None:
    if target.exists():
        if not force:
            return
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)


def _quality(package_dir: Path) -> dict[str, Any]:
    payload = read_json(package_dir / "qa" / "quality_report.json")
    return payload if isinstance(payload, dict) else {}


def _target_paths(manifest: dict[str, Any], output_root: Path) -> tuple[Path, Path, str, str]:
    ticker = manifest.get("ticker") or "UNKNOWN"
    company_name = manifest.get("company_name") or ticker
    accession = manifest.get("accession_number") or str(manifest.get("filing_id") or "").rsplit(":", 1)[-1]
    report_id = manifest.get("report_id") or us_report_id(manifest.get("fiscal_year"), manifest.get("form"), accession)
    company_wiki_id = manifest.get("company_wiki_id") or company_wiki_dir_name(ticker, company_name)
    company_dir = output_root / "companies" / company_wiki_id
    return company_dir, company_dir / "reports" / str(report_id), company_wiki_id, str(report_id)


def _normalized_manifest(manifest: dict[str, Any], *, output_root: Path, company_dir: Path, package_dir: Path, company_wiki_id: str, report_id: str) -> dict[str, Any]:
    normalized = {
        **manifest,
        "market": "US",
        "country": manifest.get("country") or "US",
        "report_id": report_id,
        "company_wiki_id": company_wiki_id,
        "company_wiki_path": repo_relative(company_dir),
        "wiki_report_path": repo_relative(package_dir),
    }
    artifact_hashes = compute_artifact_hashes(package_dir)
    normalized["artifact_hashes"] = artifact_hashes
    normalized["parse_run_id"] = normalized.get("parse_run_id") or stable_parse_run_id(normalized, artifact_hashes)
    return normalized


def migrate_package(source_package: Path, output_root: Path, *, force: bool = False) -> Path:
    manifest = read_json(source_package / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("market") != "US":
        raise ValueError(f"not a US package: {source_package}")
    company_dir, target_package, company_wiki_id, report_id = _target_paths(manifest, output_root)
    _copy_package(source_package, target_package, force=force)
    normalized = _normalized_manifest(
        manifest,
        output_root=output_root,
        company_dir=company_dir,
        package_dir=target_package,
        company_wiki_id=company_wiki_id,
        report_id=report_id,
    )
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
        "items": [],
    }
    for manifest_path in manifest_paths:
        source_package = manifest_path.parent
        try:
            target_package = migrate_package(source_package, output_root, force=force)
            summary["migrated"] += 1
            summary["items"].append({"status": "migrated", "source": str(source_package), "target": str(target_package)})
        except Exception as exc:
            summary["failed"] += 1
            summary["items"].append({"status": "failed", "source": str(source_package), "error": str(exc)})
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate legacy data/wiki/us_sec packages into A-share-aligned US company Wiki layout.")
    parser.add_argument("--legacy-root", type=Path, default=DEFAULT_LEGACY_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--report", type=Path, default=DEFAULT_OUTPUT_ROOT / "_meta" / "migration_report.json")
    args = parser.parse_args()

    summary = migrate_packages(args.legacy_root, args.output_root, force=args.force, limit=args.limit)
    report_path = args.report if args.report.is_absolute() else REPO_ROOT / args.report
    write_json(report_path, summary)
    print(json.dumps({key: summary[key] for key in ("candidates", "migrated", "failed")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
