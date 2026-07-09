from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


RULES_SCHEMA_VERSION = "sec_wiki_ingestion_rules_v1"
PLAN_SCHEMA_VERSION = "sec_wiki_ingestion_plan_v1"
WIKI_INGESTION_PLAN_PATH = "qa/wiki_ingestion_plan.json"

CANONICAL_PARSER_ARTIFACTS = {
    "raw_html": "raw/filing.htm",
    "document_full": "document_full.json",
    "report_complete": "report_complete.md",
    "content_list_enhanced": "content_list_enhanced.json",
    "table_relations": "table_relations.json",
    "quality_report": "quality_report.json",
    "manifest": "manifest.json",
}

PARSER_RESULT_MIRROR_MAP = {
    "document_full.json": "parser/document_full.json",
    "report_complete.md": "parser/report_complete.md",
    "content_list_enhanced.json": "parser/content_list_enhanced.json",
    "table_relations.json": "parser/table_relations.json",
}

FULL_DOCUMENT_WIKI_FILES = {
    "document_full": "parser/document_full.json",
    "report_complete": "parser/report_complete.md",
    "content_list_enhanced": "parser/content_list_enhanced.json",
    "table_relations": "parser/table_relations.json",
    "wiki_report_complete": "sections/report_complete.md",
}

MANIFEST_ARTIFACT_PATHS = {
    **FULL_DOCUMENT_WIKI_FILES,
    "wiki_ingestion_plan": WIKI_INGESTION_PLAN_PATH,
}

REQUIRED_PARSER_RESULT_FILES = tuple(CANONICAL_PARSER_ARTIFACTS.values())
REQUIRED_WIKI_READY_FILES = tuple(FULL_DOCUMENT_WIKI_FILES.values())


def rules_manifest() -> dict[str, Any]:
    return {
        "schema_version": RULES_SCHEMA_VERSION,
        "market": "US",
        "source_of_truth": "canonical_parser_result",
        "parser_result_root_default": "data/parser-results/us-sec",
        "wiki_package_root_default": "data/wiki/us",
        "parser_artifacts": CANONICAL_PARSER_ARTIFACTS,
        "wiki_mirror_artifacts": FULL_DOCUMENT_WIKI_FILES,
        "quality_gate": {
            "ready": [
                "all_required_parser_result_files_exist",
                "all_required_wiki_mirror_files_exist",
                "parser_and_wiki_mirror_hashes_match",
                "package_raw_html_matches_parser_raw_html",
            ],
            "scope": "file-level ingestion readiness only; PostgreSQL and Milvus are out of scope",
        },
        "policies": {
            "raw_html_authority": "raw/filing.htm is canonical source input; parser raw copy must hash-match it",
            "wiki_role": "Wiki packages archive, index, and expose parser artifacts; they do not regenerate full-document semantics during indexing",
            "markdown_role": "parser/report_complete.md is the canonical US HTML reading markdown; sections/report_complete.md is the Wiki reading copy",
        },
    }


def build_wiki_ingestion_plan(
    *,
    package_dir: Path,
    manifest: dict[str, Any],
    quality: dict[str, Any] | None = None,
    parser_result_dir: Path | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    package_dir = package_dir.resolve()
    parser_result_dir = parser_result_dir or _resolve_manifest_path(manifest.get("parser_result_dir"), repo_root=repo_root)
    parser_artifacts = _artifact_statuses(parser_result_dir, CANONICAL_PARSER_ARTIFACTS) if parser_result_dir else {}
    wiki_artifacts = _artifact_statuses(package_dir, FULL_DOCUMENT_WIKI_FILES)
    mirror_checks = _mirror_checks(parser_result_dir, package_dir) if parser_result_dir else []
    raw_check = _raw_html_check(package_dir, manifest, parser_result_dir)
    missing_parser = [
        rel
        for rel in REQUIRED_PARSER_RESULT_FILES
        if not parser_result_dir or not (parser_result_dir / rel).exists()
    ]
    missing_wiki = [rel for rel in REQUIRED_WIKI_READY_FILES if not (package_dir / rel).exists()]
    mirror_mismatches = [item for item in mirror_checks if item.get("status") == "mismatch"]
    raw_mismatch = raw_check.get("status") == "mismatch"
    warnings = _warnings(missing_parser, missing_wiki, mirror_mismatches, raw_check)
    if not missing_parser and not missing_wiki and not mirror_mismatches and not raw_mismatch:
        status = "ready"
    elif len(missing_wiki) == len(REQUIRED_WIKI_READY_FILES) and len(missing_parser) == len(REQUIRED_PARSER_RESULT_FILES):
        status = "missing"
    else:
        status = "partial"
    full_quality = (quality or {}).get("full_document") if isinstance((quality or {}).get("full_document"), dict) else {}
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "rules": rules_manifest(),
        "market": "US",
        "filing_id": manifest.get("filing_id"),
        "report_id": manifest.get("report_id"),
        "ticker": manifest.get("ticker"),
        "form": manifest.get("form"),
        "accession_number": manifest.get("accession_number"),
        "parser_result_dir": _path_value(parser_result_dir),
        "parser_result_task_id": manifest.get("parser_result_task_id") or (parser_result_dir.name if parser_result_dir else None),
        "package_dir": _path_value(package_dir),
        "status": status,
        "ready": status == "ready",
        "missing_parser_artifacts": missing_parser,
        "missing_wiki_artifacts": missing_wiki,
        "mirror_checks": mirror_checks,
        "raw_html_check": raw_check,
        "parser_artifacts": parser_artifacts,
        "wiki_artifacts": wiki_artifacts,
        "full_document_quality": {
            "dom_node_count": full_quality.get("dom_node_count"),
            "block_count": full_quality.get("block_count"),
            "markdown_chars": full_quality.get("markdown_chars"),
            "table_relation_count": full_quality.get("table_relation_count"),
            "block_source_map_count": full_quality.get("block_source_map_count"),
            "fact_linkage_ratio": full_quality.get("fact_linkage_ratio"),
            "table_linkage_ratio": full_quality.get("table_linkage_ratio"),
        },
        "warnings": warnings,
        "summary": {
            "status": status,
            "ready": status == "ready",
            "parser_artifact_count": len(parser_artifacts),
            "wiki_artifact_count": len(wiki_artifacts),
            "missing_parser_artifact_count": len(missing_parser),
            "missing_wiki_artifact_count": len(missing_wiki),
            "mirror_mismatch_count": len(mirror_mismatches),
            "raw_html_status": raw_check.get("status"),
        },
    }


def summarize_wiki_ingestion_plan(plan: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(plan, dict) or not plan:
        return {
            "status": "missing",
            "ready": False,
            "missing_parser_artifact_count": len(REQUIRED_PARSER_RESULT_FILES),
            "missing_wiki_artifact_count": len(REQUIRED_WIKI_READY_FILES),
            "mirror_mismatch_count": 0,
            "raw_html_status": "unknown",
        }
    summary = plan.get("summary") if isinstance(plan.get("summary"), dict) else {}
    return {
        "status": plan.get("status") or summary.get("status") or "unknown",
        "ready": bool(plan.get("ready")),
        "missing_parser_artifact_count": summary.get("missing_parser_artifact_count"),
        "missing_wiki_artifact_count": summary.get("missing_wiki_artifact_count"),
        "mirror_mismatch_count": summary.get("mirror_mismatch_count"),
        "raw_html_status": summary.get("raw_html_status"),
        "parser_result_dir": plan.get("parser_result_dir"),
        "parser_result_task_id": plan.get("parser_result_task_id"),
        "warnings": plan.get("warnings") if isinstance(plan.get("warnings"), list) else [],
    }


def _artifact_statuses(root: Path, artifacts: dict[str, str]) -> dict[str, dict[str, Any]]:
    return {key: _file_status(root / rel, rel) for key, rel in artifacts.items()}


def _file_status(path: Path, rel: str) -> dict[str, Any]:
    exists = path.exists()
    return {
        "path": rel,
        "exists": exists,
        "bytes": path.stat().st_size if exists and path.is_file() else 0,
        "sha256": _sha256_file(path) if exists and path.is_file() else None,
    }


def _mirror_checks(parser_result_dir: Path | None, package_dir: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for parser_rel, wiki_rel in PARSER_RESULT_MIRROR_MAP.items():
        parser_path = parser_result_dir / parser_rel if parser_result_dir else None
        wiki_path = package_dir / wiki_rel
        parser_hash = _sha256_file(parser_path) if parser_path and parser_path.exists() else None
        wiki_hash = _sha256_file(wiki_path) if wiki_path.exists() else None
        if not parser_hash or not wiki_hash:
            status = "missing"
        elif parser_hash == wiki_hash:
            status = "ok"
        else:
            status = "mismatch"
        checks.append(
            {
                "parser_path": parser_rel,
                "wiki_path": wiki_rel,
                "status": status,
                "parser_sha256": parser_hash,
                "wiki_sha256": wiki_hash,
            }
        )
    return checks


def _raw_html_check(package_dir: Path, manifest: dict[str, Any], parser_result_dir: Path | None) -> dict[str, Any]:
    package_raw_rel = str(manifest.get("local_source_path") or "raw/filing.htm")
    package_raw = package_dir / package_raw_rel
    parser_raw = parser_result_dir / CANONICAL_PARSER_ARTIFACTS["raw_html"] if parser_result_dir else None
    package_hash = _sha256_file(package_raw) if package_raw.exists() else None
    parser_hash = _sha256_file(parser_raw) if parser_raw and parser_raw.exists() else None
    if not package_hash or not parser_hash:
        status = "missing"
    elif package_hash == parser_hash:
        status = "ok"
    else:
        status = "mismatch"
    document_full_raw_sha256 = _document_full_raw_sha256(parser_result_dir)
    if status == "ok" and document_full_raw_sha256 and document_full_raw_sha256 != package_hash:
        status = "mismatch"
    return {
        "package_raw_path": package_raw_rel,
        "parser_raw_path": CANONICAL_PARSER_ARTIFACTS["raw_html"],
        "status": status,
        "package_raw_sha256": package_hash,
        "parser_raw_sha256": parser_hash,
        "document_full_raw_sha256": document_full_raw_sha256,
    }


def _document_full_raw_sha256(parser_result_dir: Path | None) -> str | None:
    if not parser_result_dir:
        return None
    payload = _read_json(parser_result_dir / CANONICAL_PARSER_ARTIFACTS["document_full"])
    source = payload.get("source") if isinstance(payload, dict) else {}
    value = source.get("raw_sha256") if isinstance(source, dict) else None
    return str(value) if value else None


def _warnings(
    missing_parser: list[str],
    missing_wiki: list[str],
    mirror_mismatches: list[dict[str, Any]],
    raw_check: dict[str, Any],
) -> list[str]:
    warnings: list[str] = []
    if missing_parser:
        warnings.append(f"missing parser artifacts: {', '.join(missing_parser)}")
    if missing_wiki:
        warnings.append(f"missing wiki mirror artifacts: {', '.join(missing_wiki)}")
    if mirror_mismatches:
        warnings.append(
            "parser/wiki mirror hash mismatches: "
            + ", ".join(str(item.get("wiki_path") or item.get("parser_path")) for item in mirror_mismatches)
        )
    if raw_check.get("status") == "mismatch":
        warnings.append("package raw HTML does not match parser raw HTML or document_full raw sha256")
    return warnings


def _resolve_manifest_path(value: Any, *, repo_root: Path | None) -> Path | None:
    if not value:
        return None
    path = Path(str(value))
    if path.is_absolute():
        return path
    if repo_root:
        return repo_root / path
    return path


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _sha256_file(path: Path | None) -> str | None:
    if not path or not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_value(path: Path | None) -> str | None:
    return str(path) if path else None
