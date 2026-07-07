from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


DERIVED_PLAN_FILES = frozenset({"metrics/load_plan.json"})

PACKAGE_FILE_PATHS = {
    "manifest": "manifest.json",
    "quality_report": "qa/quality_report.json",
    "source_map": "qa/source_map.json",
    "financial_data": "metrics/financial_data.json",
    "financial_checks": "metrics/financial_checks.json",
    "load_plan": "metrics/load_plan.json",
    "normalized_metrics": "metrics/normalized_metrics.json",
    "table_index": "tables/table_index.json",
    "report_complete": "sections/report_complete.md",
    "document_full": "parser/document_full.json",
    "content_list_enhanced": "parser/content_list_enhanced.json",
    "table_relations": "parser/table_relations.json",
    "footnotes": "qa/footnotes.json",
    "toc": "qa/toc.json",
    "financial_note_links": "qa/financial_note_links.json",
    "table_quality_signals": "qa/table_quality_signals.json",
}


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stable_id(*parts: Any) -> str:
    return hashlib.sha256("\x1f".join("" if item is None else str(item) for item in parts).encode("utf-8")).hexdigest()


def compute_artifact_hashes(package_dir: Path, *, include_manifest: bool = False) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(package_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(package_dir))
        if not include_manifest and rel == "manifest.json":
            continue
        if rel in DERIVED_PLAN_FILES:
            continue
        hashes[rel] = sha256_file(path)
    return hashes


def stable_parse_run_id(manifest: dict[str, Any], artifact_hashes: dict[str, str] | None = None) -> str:
    hashes = artifact_hashes if artifact_hashes is not None else manifest.get("artifact_hashes") or {}
    return stable_id(
        manifest.get("filing_id"),
        manifest.get("parser_version"),
        manifest.get("rules_version"),
        json.dumps(hashes, sort_keys=True, ensure_ascii=False),
    )


def market_package_paths(package_dir: Path) -> dict[str, str]:
    return {
        key: rel
        for key, rel in PACKAGE_FILE_PATHS.items()
        if (package_dir / rel).exists()
    }
