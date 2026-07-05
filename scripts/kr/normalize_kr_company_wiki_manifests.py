#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from kr_pdf_wiki_lib import REPO_ROOT, _repo_relative, _write_json, compute_artifact_hashes, stable_parse_run_id


DEFAULT_ROOT = REPO_ROOT / "data" / "wiki" / "kr"


def _manifest_paths(root: Path) -> list[Path]:
    return sorted(path for path in root.glob("companies/*/reports/*/manifest.json") if path.is_file())


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def normalize_manifest(manifest_path: Path, *, dry_run: bool = False) -> bool:
    package_dir = manifest_path.parent
    company_dir = package_dir.parents[1]
    manifest = _read_json(manifest_path)
    if str(manifest.get("market") or "").upper() != "KR":
        return False

    updated = dict(manifest)
    updated["company_wiki_id"] = updated.get("company_wiki_id") or company_dir.name
    updated["company_wiki_path"] = _repo_relative(company_dir)
    updated["wiki_report_path"] = _repo_relative(package_dir)
    updated["report_id"] = updated.get("report_id") or package_dir.name
    updated["filing_id"] = updated.get("filing_id") or updated["report_id"]
    artifact_hashes = compute_artifact_hashes(package_dir)
    updated["artifact_hashes"] = artifact_hashes
    updated["parse_run_id"] = updated.get("parse_run_id") or stable_parse_run_id(updated, artifact_hashes)

    if updated == manifest:
        return False
    if not dry_run:
        _write_json(manifest_path, updated)
    return True


def normalize_manifests(root: Path, *, dry_run: bool = False) -> dict[str, int]:
    paths = _manifest_paths(root)
    changed = 0
    for manifest_path in paths:
        if normalize_manifest(manifest_path, dry_run=dry_run):
            changed += 1
    return {"candidates": len(paths), "changed": changed}


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize KR company Wiki package manifests to the shared company-level path contract.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    summary = normalize_manifests(args.root, dry_run=args.dry_run)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
