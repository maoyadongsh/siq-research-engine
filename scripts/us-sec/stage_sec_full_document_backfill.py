#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import backfill_sec_full_document
from sec_evidence_lib import compute_artifact_hashes, read_json, sha256_file, stable_id
from sec_wiki_ingestion_rules import PARSER_RESULT_MIRROR_MAP

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_ROOT = REPO_ROOT / "data" / "wiki" / "us"
PRODUCTION_PARSER_RESULTS_ROOT = REPO_ROOT / "data" / "parser-results" / "us-sec"
STAGING_PARSER_RESULTS_DIR = "_parser-results"
AUDIT_DIR = "_audit"
REPORT_PATH = f"{AUDIT_DIR}/report.json"
CHECKPOINT_DIR = f"{AUDIT_DIR}/checkpoints"
REPORT_SCHEMA_VERSION = "sec_full_document_staging_audit_v1"
CHECKPOINT_SCHEMA_VERSION = "sec_full_document_staging_checkpoint_v1"

HIDDEN_TAGS = {"script", "style", "noscript", "ix:header", "ix:hidden", "header"}
HIDDEN_ARIA_VALUES = {"1", "true"}
HIDDEN_MARKUP_PATTERN = re.compile(
    r"(?:display\s*:\s*none|visibility\s*:\s*hidden|aria-hidden|<ix:(?:header|hidden))",
    flags=re.IGNORECASE,
)
INVISIBLE_TEXT_PATTERN = re.compile(r"[\s\u200b\u200c\u200d\u2060\ufeff]+")
REQUIRED_PROTECTED_ARTIFACTS = {
    "raw/filing.htm",
    "sections.json",
    "tables/table_index.json",
    "xbrl/facts_raw.json",
    "metrics/normalized_metrics.json",
    "metrics/financial_checks.json",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_required_tickers(value: str | None) -> set[str]:
    tickers = {item.strip().upper() for item in str(value or "").split(",") if item.strip()}
    if not tickers:
        raise ValueError("--tickers is required and must contain at least one ticker")
    return tickers


def run_staging_audit(
    source_root: Path,
    staging_root: Path,
    *,
    tickers: set[str],
    resume: bool = False,
) -> dict[str, Any]:
    source_root, staging_root = _validate_roots(source_root, staging_root, resume=resume)
    normalized_tickers = {str(item).strip().upper() for item in tickers if str(item).strip()}
    if not normalized_tickers:
        raise ValueError("tickers must contain at least one ticker")

    packages = backfill_sec_full_document.discover_packages(source_root, tickers=normalized_tickers)
    records = [_package_record(source_root, package_dir) for package_dir in packages]
    found_tickers = {str(record["ticker"]) for record in records}
    missing_tickers = sorted(normalized_tickers - found_tickers)
    if missing_tickers:
        raise ValueError(f"No US SEC package found for required tickers: {', '.join(missing_tickers)}")
    implementation_hashes = _implementation_hashes()
    implementation_hashes_digest = _hashes_digest(implementation_hashes)

    staging_root.mkdir(parents=True, exist_ok=True)
    report_path = staging_root / REPORT_PATH
    existing_report = read_json(report_path)
    if resume and existing_report:
        previous_source = _resolve_manifest_path(existing_report.get("source_root"))
        if previous_source != source_root:
            raise ValueError(
                f"existing staging report belongs to a different source root: {existing_report.get('source_root')}"
            )
    parser_results_root = staging_root / STAGING_PARSER_RESULTS_DIR
    parser_results_root.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": now_iso(),
        "updated_at": now_iso(),
        "status": "running",
        "source_root": str(source_root),
        "staging_root": str(staging_root),
        "parser_results_root": str(parser_results_root),
        "tickers": sorted(normalized_tickers),
        "resume": resume,
        "candidate_count": len(records),
        "implementation_hashes": implementation_hashes,
        "implementation_hashes_digest": implementation_hashes_digest,
        "status_counts": {},
        "items": [],
    }
    _atomic_write_json(report_path, report)

    records_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        records_by_ticker.setdefault(str(record["ticker"]), []).append(record)

    for ticker in sorted(records_by_ticker):
        ticker_records = records_by_ticker[ticker]
        source_hashes_by_filing = {
            str(record["filing_id"]): compute_artifact_hashes(Path(record["source_package_dir"]))
            for record in ticker_records
        }
        if resume and all(
            _checkpoint_is_current(
                _checkpoint_path(staging_root, record),
                source_hashes_by_filing[str(record["filing_id"])],
                implementation_hashes_digest,
            )
            for record in ticker_records
        ):
            for record in ticker_records:
                checkpoint = read_json(_checkpoint_path(staging_root, record))
                item = {**checkpoint, "resumed": True}
                _upsert_report_item(report, item)
                _write_running_report(report_path, report)
            continue

        prepared: list[dict[str, Any]] = []
        for record in ticker_records:
            try:
                source_package_dir = Path(record["source_package_dir"])
                staging_package_dir = staging_root / str(record["relative_package_path"])
                _copy_company_metadata(source_package_dir.parents[1], staging_package_dir.parents[1])
                _copytree_replace(source_package_dir, staging_package_dir)
                prepared.append({**record, "staging_package_dir": str(staging_package_dir)})
            except Exception as exc:
                item = _failed_item(record, stage="copy", error=str(exc))
                _write_checkpoint_and_report(staging_root, report_path, report, record, item)

        if not prepared:
            continue

        try:
            backfill_report = backfill_sec_full_document.backfill_full_documents(
                staging_root,
                tickers={ticker},
                force=True,
                no_index=True,
                parser_results_root=parser_results_root,
            )
        except Exception as exc:
            for record in prepared:
                item = _failed_item(record, stage="backfill", error=str(exc))
                _write_checkpoint_and_report(staging_root, report_path, report, record, item)
            continue

        backfill_by_filing = {
            str(item.get("filing_id")): item
            for item in backfill_report.get("items") or []
            if isinstance(item, dict) and item.get("filing_id")
        }
        for record in prepared:
            filing_id = str(record["filing_id"])
            backfill_item = backfill_by_filing.get(filing_id)
            if not backfill_item or backfill_item.get("status") != "updated":
                item = _failed_item(
                    record,
                    stage="backfill",
                    error=str((backfill_item or {}).get("error") or (backfill_item or {}).get("reason") or "missing backfill result"),
                    backfill=backfill_item,
                )
            else:
                try:
                    item = audit_staged_package(
                        Path(record["source_package_dir"]),
                        Path(record["staging_package_dir"]),
                        parser_results_root=parser_results_root,
                    backfill_item=backfill_item,
                    source_hashes=source_hashes_by_filing[filing_id],
                    implementation_hashes_digest=implementation_hashes_digest,
                )
                except Exception as exc:
                    item = _failed_item(
                        record,
                        stage="audit",
                        error=str(exc),
                        backfill=backfill_item,
                    )
            _write_checkpoint_and_report(staging_root, report_path, report, record, item)

    counts = Counter(str(item.get("status") or "unknown") for item in report["items"])
    report["status_counts"] = dict(counts)
    report["status"] = (
        "pass"
        if len(report["items"]) == report["candidate_count"] and counts.get("pass", 0) == report["candidate_count"]
        else "fail"
    )
    report["updated_at"] = now_iso()
    _atomic_write_json(report_path, report)
    return report


def audit_staged_package(
    source_package_dir: Path,
    staging_package_dir: Path,
    *,
    parser_results_root: Path,
    backfill_item: dict[str, Any] | None = None,
    source_hashes: dict[str, str] | None = None,
    implementation_hashes_digest: str | None = None,
) -> dict[str, Any]:
    source_package_dir = source_package_dir.resolve()
    staging_package_dir = staging_package_dir.resolve()
    source_manifest = read_json(source_package_dir / "manifest.json")
    staging_manifest = read_json(staging_package_dir / "manifest.json")
    old_document = read_json(source_package_dir / "parser" / "document_full.json")
    new_document = read_json(staging_package_dir / "parser" / "document_full.json")
    source_map = read_json(staging_package_dir / "qa" / "source_map.json")
    quality = read_json(staging_package_dir / "qa" / "quality_report.json")
    checks: list[dict[str, Any]] = []

    old_hidden_dom_ids = _hidden_dom_node_ids(old_document)
    new_hidden_dom_ids = _hidden_dom_node_ids(new_document)
    old_dom_nodes = {
        str(node.get("dom_node_id")): node
        for node in _object_list(old_document.get("dom_nodes"))
        if node.get("dom_node_id")
    }
    new_dom_nodes = {
        str(node.get("dom_node_id")): node
        for node in _object_list(new_document.get("dom_nodes"))
        if node.get("dom_node_id")
    }
    old_blocks = _object_list(old_document.get("blocks"))
    new_blocks = _object_list(new_document.get("blocks"))
    old_hidden_block_ids = [
        str(block.get("block_id"))
        for block in old_blocks
        if block.get("block_id") and str(block.get("dom_node_id") or "") in old_hidden_dom_ids
    ]
    old_hidden_invisible_block_ids = [
        str(block.get("block_id"))
        for block in old_blocks
        if block.get("block_id") and _block_is_hidden_invisible_text(block, old_dom_nodes)
    ]
    excluded_old_block_ids = set(old_hidden_block_ids) | set(old_hidden_invisible_block_ids)
    old_visible_block_ids = [
        str(block.get("block_id"))
        for block in old_blocks
        if block.get("block_id") and str(block.get("block_id")) not in excluded_old_block_ids
    ]
    new_hidden_block_ids = [
        str(block.get("block_id"))
        for block in new_blocks
        if block.get("block_id") and str(block.get("dom_node_id") or "") in new_hidden_dom_ids
    ]
    new_hidden_invisible_block_ids = [
        str(block.get("block_id"))
        for block in new_blocks
        if block.get("block_id") and _block_is_hidden_invisible_text(block, new_dom_nodes)
    ]
    new_block_ids = [str(block.get("block_id")) for block in new_blocks if block.get("block_id")]
    _add_check(
        checks,
        "hidden_ancestor_blocks_removed",
        not new_hidden_block_ids and not new_hidden_invisible_block_ids,
        old_hidden_block_count=len(old_hidden_block_ids),
        old_hidden_invisible_block_count=len(old_hidden_invisible_block_ids),
        new_hidden_block_count=len(new_hidden_block_ids),
        new_hidden_invisible_block_count=len(new_hidden_invisible_block_ids),
        new_hidden_block_ids=new_hidden_block_ids[:20],
        new_hidden_invisible_block_ids=new_hidden_invisible_block_ids[:20],
    )
    _add_check(
        checks,
        "visible_block_ids_preserved",
        old_visible_block_ids == new_block_ids,
        old_visible_block_count=len(old_visible_block_ids),
        new_block_count=len(new_block_ids),
        first_mismatches=_sequence_mismatches(old_visible_block_ids, new_block_ids),
    )

    old_fact_ids = {str(fact.get("fact_id")) for fact in _object_list(old_document.get("facts")) if fact.get("fact_id")}
    new_fact_ids = {str(fact.get("fact_id")) for fact in _object_list(new_document.get("facts")) if fact.get("fact_id")}
    _add_check(
        checks,
        "fact_ids_preserved",
        old_fact_ids == new_fact_ids,
        old_fact_count=len(old_fact_ids),
        new_fact_count=len(new_fact_ids),
        missing_fact_ids=sorted(old_fact_ids - new_fact_ids)[:20],
        added_fact_ids=sorted(new_fact_ids - old_fact_ids)[:20],
    )
    hidden_relation_sources = [
        str(relation.get("source_id"))
        for relation in _object_list(new_document.get("relations"))
        if relation.get("relation_type") == "block_contains_fact"
        and str(relation.get("source_id") or "") in excluded_old_block_ids
    ]
    hidden_fact_block_ids = [
        str(fact.get("block_id"))
        for fact in _object_list(new_document.get("facts"))
        if str(fact.get("block_id") or "") in excluded_old_block_ids
    ]
    old_hidden_fact_ids = {
        str(fact.get("fact_id"))
        for fact in _object_list(old_document.get("facts"))
        if fact.get("fact_id") and str(fact.get("dom_node_id") or "") in old_hidden_dom_ids
    }
    linked_hidden_fact_ids = [
        str(relation.get("target_id"))
        for relation in _object_list(new_document.get("relations"))
        if relation.get("relation_type") == "block_contains_fact"
        and str(relation.get("target_id") or "") in old_hidden_fact_ids
    ]
    assigned_hidden_fact_ids = [
        str(fact.get("fact_id"))
        for fact in _object_list(new_document.get("facts"))
        if str(fact.get("fact_id") or "") in old_hidden_fact_ids and fact.get("block_id")
    ]
    _add_check(
        checks,
        "hidden_block_fact_relations_removed",
        not hidden_relation_sources
        and not hidden_fact_block_ids
        and not linked_hidden_fact_ids
        and not assigned_hidden_fact_ids,
        old_hidden_block_count=len(old_hidden_block_ids),
        old_hidden_invisible_block_count=len(old_hidden_invisible_block_ids),
        old_hidden_fact_count=len(old_hidden_fact_ids),
        remaining_relation_sources=hidden_relation_sources[:20],
        remaining_fact_block_ids=hidden_fact_block_ids[:20],
        linked_hidden_fact_ids=linked_hidden_fact_ids[:20],
        assigned_hidden_fact_ids=assigned_hidden_fact_ids[:20],
    )

    entries = source_map.get("entries") if isinstance(source_map.get("entries"), list) else []
    entry_count = len(entries)
    resolvable_count = _integer(quality.get("resolvable_source_map_entry_count"))
    unresolvable_count = _integer(quality.get("unresolvable_source_map_entry_count"))
    ratio = round(resolvable_count / entry_count, 6) if entry_count else None
    summary_source_map = ((quality.get("summary") or {}).get("source_map") if isinstance(quality.get("summary"), dict) else {}) or {}
    source_map_counts_match = (
        all(
            key in quality
            for key in (
                "source_map_entry_count",
                "resolvable_source_map_entry_count",
                "unresolvable_source_map_entry_count",
                "evidence_resolvability_ratio",
            )
        )
        and all(
            key in summary_source_map
            for key in (
                "source_map_entry_count",
                "resolvable_source_map_entry_count",
                "unresolvable_source_map_entry_count",
                "evidence_resolvability_ratio",
            )
        )
        and _integer(quality.get("source_map_entry_count")) == entry_count
        and resolvable_count + unresolvable_count == entry_count
        and quality.get("evidence_resolvability_ratio") == ratio
        and _integer(summary_source_map.get("source_map_entry_count")) == entry_count
        and _integer(summary_source_map.get("resolvable_source_map_entry_count")) == resolvable_count
        and _integer(summary_source_map.get("unresolvable_source_map_entry_count")) == unresolvable_count
        and summary_source_map.get("evidence_resolvability_ratio") == ratio
    )
    _add_check(
        checks,
        "source_map_quality_counts_match",
        source_map_counts_match,
        entry_count=entry_count,
        resolvable_count=resolvable_count,
        unresolvable_count=unresolvable_count,
        expected_ratio=ratio,
        quality_ratio=quality.get("evidence_resolvability_ratio"),
    )
    block_source_map_count = sum(
        1 for entry in entries if isinstance(entry, dict) and entry.get("source_type") == "sec_html_block"
    )
    full_quality = quality.get("full_document") if isinstance(quality.get("full_document"), dict) else {}
    document_quality = new_document.get("quality") if isinstance(new_document.get("quality"), dict) else {}
    block_counts_match = (
        all(key in full_quality for key in ("block_count", "block_source_map_count"))
        and all(key in document_quality for key in ("block_count", "block_source_map_count"))
        and block_source_map_count == len(new_blocks)
        and _integer(full_quality.get("block_count")) == len(new_blocks)
        and _integer(full_quality.get("block_source_map_count")) == len(new_blocks)
        and _integer(document_quality.get("block_count")) == len(new_blocks)
        and _integer(document_quality.get("block_source_map_count")) == len(new_blocks)
    )
    _add_check(
        checks,
        "full_document_block_counts_match",
        block_counts_match,
        document_block_count=len(new_blocks),
        block_source_map_count=block_source_map_count,
        quality_block_count=full_quality.get("block_count"),
        quality_block_source_map_count=full_quality.get("block_source_map_count"),
    )

    mirror_checks, canonical_parser_dir, manifest_parser_dir_matches = _parser_mirror_checks(
        staging_package_dir,
        staging_manifest,
        parser_results_root.resolve(),
    )
    _add_check(
        checks,
        "parser_mirrors_match",
        manifest_parser_dir_matches and bool(mirror_checks) and all(item["matched"] for item in mirror_checks),
        canonical_parser_dir=str(canonical_parser_dir),
        manifest_parser_result_dir=staging_manifest.get("parser_result_dir"),
        manifest_parser_dir_matches=manifest_parser_dir_matches,
        files=mirror_checks,
    )

    protected_before = _protected_artifact_hashes(source_package_dir)
    protected_after = _protected_artifact_hashes(staging_package_dir)
    protected_changes = _hash_changes(protected_before, protected_after)
    missing_protected = sorted(REQUIRED_PROTECTED_ARTIFACTS - set(protected_before))
    _add_check(
        checks,
        "protected_artifacts_unchanged",
        not missing_protected and not protected_changes,
        protected_artifact_count=len(protected_before),
        missing_required=missing_protected,
        changes=protected_changes,
    )

    before_hashes = source_hashes or compute_artifact_hashes(source_package_dir)
    after_hashes = compute_artifact_hashes(staging_package_dir)
    passed = all(check["passed"] for check in checks)
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "generated_at": now_iso(),
        "status": "pass" if passed else "fail",
        "ticker": staging_manifest.get("ticker") or source_manifest.get("ticker"),
        "filing_id": staging_manifest.get("filing_id") or source_manifest.get("filing_id"),
        "source_package_path": str(source_package_dir),
        "staging_package_path": str(staging_package_dir),
        "canonical_parser_dir": str(canonical_parser_dir),
        "backfill": backfill_item or {},
        "checks": checks,
        "failed_checks": [check["name"] for check in checks if not check["passed"]],
        "changed_hashes": _hash_changes(before_hashes, after_hashes),
        "source_artifact_hashes_digest": _hashes_digest(before_hashes),
        "staging_artifact_hashes_digest": _hashes_digest(after_hashes),
        "implementation_hashes_digest": implementation_hashes_digest or _hashes_digest(_implementation_hashes()),
        "parse_run_id": {
            "before": source_manifest.get("parse_run_id"),
            "after": staging_manifest.get("parse_run_id"),
            "changed": source_manifest.get("parse_run_id") != staging_manifest.get("parse_run_id"),
        },
    }


def _validate_roots(source_root: Path, staging_root: Path, *, resume: bool) -> tuple[Path, Path]:
    source = source_root.resolve()
    staging = staging_root.resolve()
    production = DEFAULT_SOURCE_ROOT.resolve()
    production_parser_results = PRODUCTION_PARSER_RESULTS_ROOT.resolve()
    if not source.is_dir() or not (source / "companies").is_dir():
        raise ValueError(f"source root is not a US Wiki root: {source}")
    if staging == source or _is_within(staging, source) or _is_within(source, staging):
        raise ValueError("staging root must not equal, contain, or be inside source root")
    if staging == production or _is_within(staging, production):
        raise ValueError(f"staging root must never publish into production US Wiki root: {production}")
    if (
        staging == production_parser_results
        or _is_within(staging, production_parser_results)
        or _is_within(production_parser_results, staging)
    ):
        raise ValueError(f"staging root must not overlap production parser results: {production_parser_results}")
    if staging.exists() and not resume:
        raise FileExistsError(f"staging root already exists; pass --resume explicitly: {staging}")
    return source, staging


def _package_record(source_root: Path, package_dir: Path) -> dict[str, Any]:
    manifest = read_json(package_dir / "manifest.json")
    relative = package_dir.resolve().relative_to(source_root.resolve())
    return {
        "ticker": str(manifest.get("ticker") or "").upper(),
        "filing_id": str(manifest.get("filing_id") or relative),
        "report_id": str(manifest.get("report_id") or package_dir.name),
        "source_package_dir": str(package_dir.resolve()),
        "relative_package_path": str(relative),
    }


def _copy_company_metadata(source_company_dir: Path, staging_company_dir: Path) -> None:
    staging_company_dir.mkdir(parents=True, exist_ok=True)
    for source in sorted(source_company_dir.iterdir()):
        if source.name == "reports":
            continue
        target = staging_company_dir / source.name
        if target.exists():
            continue
        if source.is_dir():
            _copytree_replace(source, target)
        elif source.is_file():
            _copy_file_atomic(source, target)
    (staging_company_dir / "reports").mkdir(exist_ok=True)


def _copytree_replace(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.copy-{uuid.uuid4().hex}"
    backup = target.parent / f".{target.name}.previous-{uuid.uuid4().hex}"
    moved_existing = False
    try:
        shutil.copytree(source, temporary)
        if target.exists():
            os.replace(target, backup)
            moved_existing = True
        os.replace(temporary, target)
    except Exception:
        if moved_existing and backup.exists() and not target.exists():
            os.replace(backup, target)
        raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
        if backup.exists() and target.exists():
            shutil.rmtree(backup)


def _copy_file_atomic(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_checkpoint_and_report(
    staging_root: Path,
    report_path: Path,
    report: dict[str, Any],
    record: dict[str, Any],
    item: dict[str, Any],
) -> None:
    _atomic_write_json(_checkpoint_path(staging_root, record), item)
    _upsert_report_item(report, item)
    _write_running_report(report_path, report)


def _write_running_report(report_path: Path, report: dict[str, Any]) -> None:
    report["updated_at"] = now_iso()
    report["status_counts"] = dict(Counter(str(item.get("status") or "unknown") for item in report["items"]))
    _atomic_write_json(report_path, report)


def _upsert_report_item(report: dict[str, Any], item: dict[str, Any]) -> None:
    filing_id = str(item.get("filing_id") or "")
    items = [candidate for candidate in report.get("items") or [] if str(candidate.get("filing_id") or "") != filing_id]
    items.append(item)
    report["items"] = sorted(items, key=lambda candidate: (str(candidate.get("ticker") or ""), str(candidate.get("filing_id") or "")))


def _checkpoint_path(staging_root: Path, record: dict[str, Any]) -> Path:
    ticker = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(record.get("ticker") or "UNKNOWN"))
    report_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(record.get("report_id") or "report"))
    suffix = stable_id(record.get("filing_id"))[:12]
    return staging_root / CHECKPOINT_DIR / f"{ticker}-{report_id}-{suffix}.json"


def _checkpoint_is_current(
    path: Path,
    source_hashes: dict[str, str],
    implementation_hashes_digest: str,
) -> bool:
    checkpoint = read_json(path)
    staging_package = Path(str(checkpoint.get("staging_package_path") or ""))
    canonical_parser_dir = Path(str(checkpoint.get("canonical_parser_dir") or ""))
    return bool(
        checkpoint.get("status") == "pass"
        and checkpoint.get("source_artifact_hashes_digest") == _hashes_digest(source_hashes)
        and checkpoint.get("implementation_hashes_digest") == implementation_hashes_digest
        and staging_package.is_dir()
        and (staging_package / "manifest.json").is_file()
        and canonical_parser_dir.is_dir()
        and all((canonical_parser_dir / parser_rel).is_file() for parser_rel in PARSER_RESULT_MIRROR_MAP)
    )


def _failed_item(
    record: dict[str, Any],
    *,
    stage: str,
    error: str,
    backfill: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "generated_at": now_iso(),
        "status": "fail",
        "ticker": record.get("ticker"),
        "filing_id": record.get("filing_id"),
        "source_package_path": record.get("source_package_dir"),
        "staging_package_path": record.get("staging_package_dir"),
        "backfill": backfill or {},
        "checks": [],
        "failed_checks": [stage],
        "error": error,
    }


def _hidden_dom_node_ids(document: dict[str, Any]) -> set[str]:
    hidden: set[str] = set()
    nodes = sorted(_object_list(document.get("dom_nodes")), key=lambda item: _integer(item.get("source_order")))
    for node in nodes:
        node_id = str(node.get("dom_node_id") or "")
        parent_id = str(node.get("parent_id") or "")
        attrs = node.get("attrs") if isinstance(node.get("attrs"), dict) else {}
        if node_id and (parent_id in hidden or _node_is_directly_hidden(str(node.get("tag") or ""), attrs)):
            hidden.add(node_id)
    return hidden


def _node_is_directly_hidden(tag: str, attrs: dict[str, Any]) -> bool:
    normalized_attrs = {str(key).lower(): value for key, value in attrs.items()}
    if tag.lower() in HIDDEN_TAGS or "hidden" in normalized_attrs:
        return True
    if str(normalized_attrs.get("aria-hidden") or "").strip().lower() in HIDDEN_ARIA_VALUES:
        return True
    style = str(normalized_attrs.get("style") or "").lower()
    declarations = {}
    for declaration in style.split(";"):
        if ":" not in declaration:
            continue
        key, value = declaration.split(":", 1)
        declarations[key.strip()] = re.sub(r"\s*!important\s*$", "", value.strip())
    return (
        declarations.get("display") == "none"
        or declarations.get("visibility") == "hidden"
    )


def _block_is_hidden_invisible_text(
    block: dict[str, Any],
    dom_nodes: dict[str, dict[str, Any]],
) -> bool:
    text = str(block.get("text") or "")
    if INVISIBLE_TEXT_PATTERN.fullmatch(text) is None:
        return False
    node = dom_nodes.get(str(block.get("dom_node_id") or ""), {})
    return bool(HIDDEN_MARKUP_PATTERN.search(str(node.get("html_snippet") or "")))


def _parser_mirror_checks(
    staging_package_dir: Path,
    manifest: dict[str, Any],
    parser_results_root: Path,
) -> tuple[list[dict[str, Any]], Path, bool]:
    task_id = str(manifest.get("parser_result_task_id") or "")
    canonical = (parser_results_root / task_id).resolve()
    manifest_parser_dir = _resolve_manifest_path(manifest.get("parser_result_dir"))
    manifest_parser_dir_matches = manifest_parser_dir == canonical
    if not task_id or not _is_within(canonical, parser_results_root.resolve()):
        return [], canonical, False
    checks = []
    for parser_rel, package_rel in PARSER_RESULT_MIRROR_MAP.items():
        parser_path = canonical / parser_rel
        package_path = staging_package_dir / package_rel
        parser_hash = sha256_file(parser_path) if parser_path.is_file() else None
        package_hash = sha256_file(package_path) if package_path.is_file() else None
        checks.append(
            {
                "parser_path": parser_rel,
                "package_path": package_rel,
                "parser_sha256": parser_hash,
                "package_sha256": package_hash,
                "matched": bool(parser_hash and parser_hash == package_hash),
            }
        )
    return checks, canonical, manifest_parser_dir_matches


def _resolve_manifest_path(value: Any) -> Path | None:
    if not value:
        return None
    path = Path(str(value))
    return path.resolve() if path.is_absolute() else (REPO_ROOT / path).resolve()


def _protected_artifact_hashes(package_dir: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(package_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(package_dir)
        if _is_protected_artifact(relative):
            hashes[str(relative)] = sha256_file(path)
    return hashes


def _is_protected_artifact(relative: Path) -> bool:
    parts = relative.parts
    if not parts:
        return False
    if len(parts) == 1 and relative.name != "manifest.json":
        return True
    if parts[0] in {"raw", "tables", "xbrl", "metrics"}:
        return True
    if parts[0] == "qa":
        return relative.name not in {"quality_report.json", "source_map.json", "wiki_ingestion_plan.json"}
    return parts[0] == "sections" and relative.name != "report_complete.md"


def _hash_changes(before: dict[str, str], after: dict[str, str]) -> dict[str, dict[str, str | None]]:
    return {
        key: {"before": before.get(key), "after": after.get(key)}
        for key in sorted(set(before) | set(after))
        if before.get(key) != after.get(key)
    }


def _hashes_digest(hashes: dict[str, str]) -> str:
    payload = json.dumps(hashes, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _implementation_hashes() -> dict[str, str]:
    files = (
        Path(__file__).resolve(),
        Path(backfill_sec_full_document.__file__).resolve(),
        Path(__file__).with_name("sec_evidence_lib.py").resolve(),
        Path(__file__).with_name("sec_html_document.py").resolve(),
    )
    return {path.name: sha256_file(path) for path in files}


def _sequence_mismatches(before: list[str], after: list[str], *, limit: int = 20) -> list[dict[str, Any]]:
    mismatches = []
    for index in range(max(len(before), len(after))):
        left = before[index] if index < len(before) else None
        right = after[index] if index < len(after) else None
        if left != right:
            mismatches.append({"index": index, "before": left, "after": right})
        if len(mismatches) >= limit:
            break
    return mismatches


def _object_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _integer(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _add_check(checks: list[dict[str, Any]], name: str, passed: bool, **details: Any) -> None:
    checks.append({"name": name, "passed": bool(passed), "details": details})


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return path != parent
    except ValueError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage and audit US SEC full-document backfills without publishing production data.")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--tickers", required=True, help="Required comma-separated ticker list.")
    parser.add_argument("--resume", action="store_true", help="Explicitly resume an existing staging root.")
    args = parser.parse_args()

    report = run_staging_audit(
        args.source_root,
        args.staging_root,
        tickers=parse_required_tickers(args.tickers),
        resume=args.resume,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
