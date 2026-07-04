#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from jp_evidence_lib import (
    REPO_ROOT,
    _write_company_index,
    company_wiki_report_paths,
    compute_artifact_hashes,
    read_json,
    stable_id,
    stable_parse_run_id,
    validate_evidence_package,
    write_json,
)


DEFAULT_LEGACY_ROOT = REPO_ROOT / "data" / "wiki" / "jp_reports"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "data" / "wiki"


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


def _normalized_manifest(manifest: dict[str, Any], *, output_root: Path, package_dir: Path) -> tuple[dict[str, Any], Any]:
    paths = company_wiki_report_paths(output_root, manifest)
    normalized = {
        **manifest,
        "market": "JP",
        "report_id": paths.report_id,
        "company_wiki_id": paths.company_id,
        "company_wiki_path": paths.company_wiki_path,
        "wiki_report_path": paths.wiki_report_path,
        "currency": manifest.get("currency") or "JPY",
    }
    if not normalized.get("filing_id"):
        normalized["filing_id"] = stable_id("JP", normalized.get("ticker"), normalized.get("doc_id") or paths.report_id)
    artifact_hashes = compute_artifact_hashes(package_dir)
    normalized["artifact_hashes"] = artifact_hashes
    normalized["parse_run_id"] = normalized.get("parse_run_id") or stable_parse_run_id(normalized, artifact_hashes)
    return normalized, paths


def migrate_package(source_package: Path, output_root: Path, *, force: bool = False) -> Path:
    manifest = read_json(source_package / "manifest.json", {})
    if not isinstance(manifest, dict) or manifest.get("market") != "JP":
        raise ValueError(f"not a JP package: {source_package}")
    paths = company_wiki_report_paths(output_root, manifest)
    target_package = paths.report_dir
    _copy_package(source_package, target_package, force=force)
    normalized, paths = _normalized_manifest(manifest, output_root=output_root, package_dir=target_package)
    write_json(target_package / "manifest.json", normalized)
    _write_company_index(paths, normalized)
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
    parser = argparse.ArgumentParser(description="Migrate legacy data/wiki/jp_reports packages into A-share-aligned JP company Wiki layout.")
    parser.add_argument("--legacy-root", type=Path, default=DEFAULT_LEGACY_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--report", type=Path, default=REPO_ROOT / "data" / "wiki" / "jp" / "_meta" / "migration_report.json")
    args = parser.parse_args()

    summary = migrate_packages(args.legacy_root, args.output_root, force=args.force, limit=args.limit)
    report_path = args.report if args.report.is_absolute() else REPO_ROOT / args.report
    write_json(report_path, summary)
    print(json.dumps({key: summary[key] for key in ("candidates", "migrated", "failed", "validation_failed")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
