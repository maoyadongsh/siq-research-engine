#!/usr/bin/env python3
"""Read-only HK canonical identity reconciliation gate.

The gate compares canonical identities planned by the HK Wiki builder (or written
to a staging Wiki) with the current ``siq_hk`` filing/parse-run inventory.  It
never mutates Wiki packages or PostgreSQL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
WIKI_BUILDER_DIR = REPO_ROOT / "scripts" / "wiki" / "market_wikiset"
MAINTENANCE_DIR = REPO_ROOT / "scripts" / "maintenance"
for import_dir in (WIKI_BUILDER_DIR, MAINTENANCE_DIR):
    if str(import_dir) not in sys.path:
        sys.path.insert(0, str(import_dir))

from evidence_metadata import attach_evidence_metadata  # noqa: E402

SAFE_STATUSES = {"exact_match", "safe_metadata_backfill", "safe_new_filing", "safe_new_parse_run"}
BLOCKING_STATUSES = {"invalid_candidate", "identity_conflict", "legacy_period_collision"}
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _portable_report_value(value: Any) -> Any:
    """Remove host-local paths from release evidence without changing gate logic."""
    if isinstance(value, dict):
        return {key: _portable_report_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_portable_report_value(item) for item in value]
    if isinstance(value, str) and value.startswith("/"):
        return _portable_path(value)
    return value


def _portable_path(value: Any) -> str:
    text = normalize_text(value)
    if not text:
        return ""
    path = Path(text)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return "<external>"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as infile:
        for chunk in iter(lambda: infile.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_ticker(value: Any) -> str:
    text = str(value or "").strip()
    return text.zfill(5) if text.isdigit() else text


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_period(value: Any) -> str:
    return normalize_text(value)[:10]


def report_family(value: Any) -> str:
    text = normalize_text(value).lower().replace("-", "_").replace(" ", "_")
    if any(token in text for token in ("annual", "年报", "年度")):
        return "annual"
    if any(token in text for token in ("interim", "half", "中报", "半年")):
        return "interim"
    return text


def _candidate(
    *,
    company_id: Any,
    ticker: Any,
    filing_id: Any,
    parse_run_id: Any,
    accession_number: Any,
    period_end: Any,
    report_type: Any,
    source_url: Any,
    source_sha256: Any,
    package_path: Any,
    parser_task_id: Any = None,
    document_full_sha256: Any = None,
) -> dict[str, Any]:
    ticker_text = normalize_ticker(ticker)
    return {
        "company_id": normalize_text(company_id) or (f"HK:{ticker_text}" if ticker_text else ""),
        "ticker": ticker_text,
        "filing_id": normalize_text(filing_id),
        "parse_run_id": normalize_text(parse_run_id),
        "accession_number": normalize_text(accession_number),
        "period_end": normalize_period(period_end),
        "report_type": normalize_text(report_type),
        "report_family": report_family(report_type),
        "source_url": normalize_text(source_url),
        "source_sha256": normalize_text(source_sha256).lower(),
        "package_path": _portable_path(package_path),
        "parser_task_id": normalize_text(parser_task_id),
        "document_full_sha256": normalize_text(document_full_sha256).lower(),
    }


def candidates_from_builder(results_dir: Path, downloads_root: Path) -> list[dict[str, Any]]:
    from ingest_hk_pdf_wiki import build_plan

    rows, _ = build_plan(results_dir.resolve(), downloads_root=downloads_root.resolve())
    candidates: list[dict[str, Any]] = []
    for row in rows:
        identity = row.get("canonical_identity") if isinstance(row.get("canonical_identity"), dict) else {}
        sidecar = row.get("sidecar") if isinstance(row.get("sidecar"), dict) else {}
        document_full_path = Path(row["result_dir"]) / "document_full.json"
        candidates.append(
            _candidate(
                company_id=f"HK:{row.get('ticker')}",
                ticker=row.get("ticker"),
                filing_id=identity.get("filing_id"),
                parse_run_id=identity.get("parse_run_id"),
                accession_number=sidecar.get("accession_number"),
                period_end=row.get("period_end"),
                report_type=row.get("report_type") or row.get("report_kind"),
                source_url=identity.get("source_url"),
                source_sha256=identity.get("source_sha256"),
                package_path=row.get("result_dir"),
                parser_task_id=row.get("task_id"),
                document_full_sha256=sha256_file(document_full_path) if document_full_path.is_file() else None,
            )
        )
    return candidates


def candidates_from_staging(wiki_root: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for manifest_path in sorted(wiki_root.resolve().rglob("manifest.json")):
        manifest = read_json(manifest_path, {})
        if not isinstance(manifest, dict) or normalize_text(manifest.get("market")).upper() != "HK":
            continue
        report = read_json(manifest_path.parent / "report.json", {})
        report_source = report.get("source") if isinstance(report, dict) and isinstance(report.get("source"), dict) else {}
        source_manifest = manifest.get("source_manifest") if isinstance(manifest.get("source_manifest"), dict) else {}
        artifact_hashes = manifest.get("artifact_hashes") if isinstance(manifest.get("artifact_hashes"), dict) else {}
        accession = normalize_text(manifest.get("accession_number"))
        filing_id = normalize_text(manifest.get("filing_id"))
        if not accession and filing_id.startswith("HK:"):
            accession = filing_id.rsplit(":", 1)[-1]
        candidates.append(
            _candidate(
                company_id=manifest.get("company_id"),
                ticker=manifest.get("ticker") or manifest.get("stock_code"),
                filing_id=filing_id,
                parse_run_id=manifest.get("parse_run_id"),
                accession_number=accession,
                period_end=manifest.get("period_end"),
                report_type=manifest.get("report_type") or manifest.get("form"),
                source_url=manifest.get("source_url") or report_source.get("source_url"),
                source_sha256=source_manifest.get("content_sha256") or report_source.get("source_sha256"),
                package_path=manifest_path.parent,
                parser_task_id=manifest.get("task_id") or report_source.get("task_id"),
                document_full_sha256=artifact_hashes.get("parser/document_full.json"),
            )
        )
    return candidates


def database_inventory_from_json(path: Path) -> dict[str, list[dict[str, Any]]]:
    payload = read_json(path, {})
    return {
        "filings": [row for row in payload.get("filings") or [] if isinstance(row, dict)],
        "parse_runs": [row for row in payload.get("parse_runs") or [] if isinstance(row, dict)],
    }


def database_inventory_from_postgres(expected_database: str) -> dict[str, list[dict[str, Any]]]:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise SystemExit("psycopg is required for --database-env") from exc

    try:
        with psycopg.connect("", row_factory=dict_row) as conn:
            conn.execute("set transaction read only")
            identity = dict(
                conn.execute(
                    "select current_database() as database_name, "
                    "current_setting('transaction_read_only') as transaction_read_only"
                ).fetchone()
            )
            if identity.get("database_name") != expected_database:
                raise SystemExit(
                    f"Connected database {identity.get('database_name')!r} does not match "
                    f"--expected-database {expected_database!r}"
                )
            if identity.get("transaction_read_only") != "on":
                raise SystemExit("PostgreSQL identity reconciliation connection is not read-only")
            filings = conn.execute(
                """
                select filing_id, company_id, ticker, stock_code, accession_number,
                       report_type, period_end, fiscal_year, source_url, quality_status
                from pdf2md_hk.filings
                """
            ).fetchall()
            parse_runs = conn.execute(
                """
                select parse_run_id, filing_id, status, completed_at, wiki_package_path,
                       artifact_hashes
                from pdf2md_hk.parse_runs
                """
            ).fetchall()
            conn.rollback()
    except SystemExit:
        raise
    except Exception as exc:
        raise SystemExit(f"PostgreSQL identity reconciliation audit failed: {type(exc).__name__}") from None
    return {"filings": [dict(row) for row in filings], "parse_runs": [dict(row) for row in parse_runs]}


def _candidate_errors(candidate: dict[str, Any]) -> list[str]:
    errors = []
    for field in ("company_id", "ticker", "filing_id", "parse_run_id", "accession_number", "period_end", "report_family"):
        if not normalize_text(candidate.get(field)):
            errors.append(f"missing_{field}")
    ticker = normalize_ticker(candidate.get("ticker"))
    accession = normalize_text(candidate.get("accession_number"))
    if ticker and normalize_text(candidate.get("company_id")) != f"HK:{ticker}":
        errors.append("company_id_ticker_mismatch")
    if ticker and accession and normalize_text(candidate.get("filing_id")) != f"HK:{ticker}:{accession}":
        errors.append("noncanonical_filing_id")
    source_sha = normalize_text(candidate.get("source_sha256"))
    if not SHA256_RE.fullmatch(source_sha):
        errors.append("missing_or_invalid_source_sha256")
    if ticker and accession and source_sha:
        expected_parse = f"HK:{ticker}:{accession}:{source_sha[:16]}"
        if normalize_text(candidate.get("parse_run_id")) != expected_parse:
            errors.append("noncanonical_parse_run_id")
    if not normalize_text(candidate.get("source_url")):
        errors.append("missing_source_url")
    return errors


def _normalized_filing(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "filing_id": normalize_text(row.get("filing_id")),
        "company_id": normalize_text(row.get("company_id")),
        "ticker": normalize_ticker(row.get("ticker") or row.get("stock_code")),
        "accession_number": normalize_text(row.get("accession_number")),
        "period_end": normalize_period(row.get("period_end")),
        "report_family": report_family(row.get("report_type")),
    }


def _task_id_from_package_path(value: Any) -> str:
    text = normalize_text(value)
    if not text:
        return ""
    path = Path(text)
    local_path = _repo_local_path(path)
    if local_path is not None and local_path.is_file() and local_path.suffix.lower() == ".json":
        payload = read_json(local_path, {})
        task = payload.get("task") if isinstance(payload, dict) and isinstance(payload.get("task"), dict) else {}
        task_id = normalize_text(task.get("task_id"))
        if task_id:
            return task_id
    if path.name == "document_full.json":
        return path.parent.name
    return path.name


def _repo_local_path(path: Path) -> Path | None:
    candidate = path if path.is_absolute() else REPO_ROOT / path
    try:
        resolved = candidate.resolve()
        resolved.relative_to(REPO_ROOT.resolve())
    except (OSError, ValueError):
        return None
    return resolved


def _package_file_evidence(value: Any, database_hashes: list[str]) -> dict[str, Any]:
    text = normalize_text(value)
    path = Path(text) if text else Path()
    local_path = _repo_local_path(path) if text else None
    task_id = _task_id_from_package_path(value)
    evidence = {
        "path": _portable_path(value),
        "task_id": task_id,
        "repo_file_verified": False,
        "file_sha256": "",
        "database_hash_match": False,
        "synthetic_eval_fixture": False,
        "repo_benchmark_identity_migrated": False,
    }
    if local_path is None or not local_path.is_file():
        return evidence
    payload = read_json(local_path, {})
    file_hash = sha256_file(local_path)
    relative_path = local_path.relative_to(REPO_ROOT.resolve())
    database_hash_match = database_hashes == [file_hash]
    synthetic_marker = bool(
        isinstance(payload, dict)
        and (
            payload.get("identity_scope") == "synthetic_fixture"
            or task_id.startswith("fixture-")
        )
    )
    financial_data = payload.get("financial_data") if isinstance(payload, dict) else None
    financial_data = financial_data if isinstance(financial_data, dict) else {}
    synthetic_identity_namespace = ":FIXTURE:" in normalize_text(
        financial_data.get("company_id")
    ).upper()
    synthetic_eval_fixture = bool(
        relative_path.parts
        and relative_path.parts[0] == "eval_datasets"
        and synthetic_marker
    )
    evidence.update(
        {
            "repo_file_verified": True,
            "file_sha256": file_hash,
            "database_hash_match": database_hash_match,
            "synthetic_eval_fixture": synthetic_eval_fixture,
            "repo_benchmark_identity_migrated": bool(
                synthetic_eval_fixture
                and synthetic_identity_namespace
                and database_hashes
                and not database_hash_match
            ),
        }
    )
    return evidence


def _document_full_hash(parse_run: dict[str, Any]) -> str:
    hashes = parse_run.get("artifact_hashes") if isinstance(parse_run.get("artifact_hashes"), dict) else {}
    return normalize_text(hashes.get("document_full.json") or hashes.get("parser/document_full.json")).lower()


def assess_legacy_migration(
    candidate: dict[str, Any],
    legacy_filings: list[dict[str, Any]],
    parse_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    blockers: list[str] = []
    if len(legacy_filings) != 1:
        blockers.append("legacy_filing_count_not_one")
    legacy = legacy_filings[0] if len(legacy_filings) == 1 else {}
    legacy_filing_id = normalize_text(legacy.get("filing_id"))
    legacy_task_id = legacy_filing_id.rsplit(":", 1)[-1] if legacy_filing_id else ""
    candidate_task_id = normalize_text(candidate.get("parser_task_id"))
    filing_runs = [row for row in parse_runs if normalize_text(row.get("filing_id")) == legacy_filing_id]
    if len(filing_runs) != 1:
        blockers.append("legacy_parse_run_count_not_one")
    package_task_ids = sorted({_task_id_from_package_path(row.get("wiki_package_path")) for row in filing_runs if row.get("wiki_package_path")})
    database_hashes = sorted({_document_full_hash(row) for row in filing_runs if _document_full_hash(row)})
    package_file_evidence = [
        _package_file_evidence(row.get("wiki_package_path"), database_hashes)
        for row in filing_runs
        if row.get("wiki_package_path")
    ]
    synthetic_eval_fixture = bool(
        len(filing_runs) == 1
        and len(package_file_evidence) == 1
        and package_file_evidence[0]["synthetic_eval_fixture"]
    )
    repo_benchmark_identity_migrated = bool(
        synthetic_eval_fixture
        and package_file_evidence[0]["repo_benchmark_identity_migrated"]
    )
    candidate_hash = normalize_text(candidate.get("document_full_sha256")).lower()

    evidence = {
        "legacy_filing_id": legacy_filing_id,
        "legacy_filing_task_id": legacy_task_id,
        "candidate_parser_task_id": candidate_task_id,
        "legacy_filing_task_id_match": bool(candidate_task_id and legacy_task_id == candidate_task_id),
        "legacy_accession_missing": not normalize_text(legacy.get("accession_number")),
        "legacy_parse_run_count": len(filing_runs),
        "legacy_parse_run_ids": sorted(normalize_text(row.get("parse_run_id")) for row in filing_runs),
        "package_task_ids": package_task_ids,
        "package_task_id_match": bool(candidate_task_id and package_task_ids == [candidate_task_id]),
        "candidate_document_full_sha256": candidate_hash,
        "database_document_full_sha256": database_hashes[0] if len(database_hashes) == 1 else "",
        "document_full_sha256_match": bool(candidate_hash and database_hashes == [candidate_hash]),
        "legacy_source_kind": "synthetic_eval_fixture" if synthetic_eval_fixture else "parser_or_wiki_artifact",
        "repo_benchmark_identity_migrated": repo_benchmark_identity_migrated,
        "package_file_evidence": package_file_evidence,
    }
    required_evidence = [
        ("legacy_accession_missing", "legacy_accession_already_bound"),
        ("document_full_sha256_match", "document_full_sha256_mismatch"),
    ]
    if synthetic_eval_fixture:
        blockers.append("legacy_source_is_synthetic_eval_fixture")
    else:
        required_evidence.extend(
            [
                ("legacy_filing_task_id_match", "legacy_filing_task_id_mismatch"),
                ("package_task_id_match", "legacy_package_task_id_mismatch"),
            ]
        )
    for field, reason in required_evidence:
        if not evidence[field]:
            blockers.append(reason)
    blockers = sorted(set(blockers))
    eligible = not blockers
    return {
        "migration_eligible": eligible,
        "migration_state": (
            "repo_benchmark_identity_migrated_database_legacy_row_pending"
            if repo_benchmark_identity_migrated
            else "assessment_only_not_migrated"
        ),
        "evidence": evidence,
        "blocking_reasons": blockers,
        "recommended_action": (
            "exact_legacy_fixture_audit_then_controlled_database_retirement"
            if repo_benchmark_identity_migrated
            else "migrate_benchmark_identity_then_stage_fixture_retirement"
            if synthetic_eval_fixture
            else (
                "controlled_staging_rebuild_then_retire_exact_legacy_filing"
                if eligible
                else "manual_identity_review_before_any_import"
            )
        ),
    }


def reconcile_identities(
    candidates: Iterable[dict[str, Any]],
    database_inventory: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    candidate_rows = list(candidates)
    filings = [_normalized_filing(row) for row in database_inventory.get("filings") or []]
    parse_runs = [dict(row) for row in database_inventory.get("parse_runs") or []]
    filings_by_id = {row["filing_id"]: row for row in filings if row["filing_id"]}
    parse_runs_by_id = {normalize_text(row.get("parse_run_id")): row for row in parse_runs if row.get("parse_run_id")}
    results: list[dict[str, Any]] = []
    filing_counts = Counter(normalize_text(row.get("filing_id")) for row in candidate_rows if row.get("filing_id"))
    parse_run_counts = Counter(normalize_text(row.get("parse_run_id")) for row in candidate_rows if row.get("parse_run_id"))
    period_filings: dict[tuple[str, str, str], set[str]] = {}
    for row in candidate_rows:
        period_key = (
            normalize_text(row.get("company_id")),
            normalize_period(row.get("period_end")),
            normalize_text(row.get("report_family")),
        )
        period_filings.setdefault(period_key, set()).add(normalize_text(row.get("filing_id")))

    for candidate in candidate_rows:
        migration: dict[str, Any] | None = None
        errors = _candidate_errors(candidate)
        filing_id = normalize_text(candidate.get("filing_id"))
        parse_run_id = normalize_text(candidate.get("parse_run_id"))
        period_key = (
            normalize_text(candidate.get("company_id")),
            normalize_period(candidate.get("period_end")),
            normalize_text(candidate.get("report_family")),
        )
        if filing_counts.get(filing_id, 0) > 1:
            errors.append("duplicate_candidate_filing_id")
        if parse_run_counts.get(parse_run_id, 0) > 1:
            errors.append("duplicate_candidate_parse_run_id")
        if len(period_filings.get(period_key, set())) > 1:
            errors.append("candidate_period_collision")
        conflicts: list[dict[str, Any]] = []
        status = "invalid_candidate" if errors else "safe_new_filing"
        existing_filing = filings_by_id.get(filing_id)
        existing_parse = parse_runs_by_id.get(parse_run_id)
        period_matches = [
            row
            for row in filings
            if row.get("company_id") == candidate.get("company_id")
            and row.get("period_end") == candidate.get("period_end")
            and row.get("report_family") == candidate.get("report_family")
        ]
        other_period_matches = [row for row in period_matches if row.get("filing_id") != filing_id]

        if not errors and existing_filing:
            metadata_backfill = False
            mismatches = []
            for field in ("company_id", "ticker", "accession_number", "period_end", "report_family"):
                existing_value = normalize_text(existing_filing.get(field))
                candidate_value = normalize_text(candidate.get(field))
                if existing_value == candidate_value:
                    continue
                if field == "accession_number" and not existing_value and filing_id == f"HK:{candidate.get('ticker')}:{candidate_value}":
                    metadata_backfill = True
                    continue
                mismatches.append(field)
            if mismatches:
                status = "identity_conflict"
                conflicts.append({"kind": "filing_id_mismatch", "fields": mismatches, "existing_filing_id": filing_id})
            elif existing_parse and normalize_text(existing_parse.get("filing_id")) != filing_id:
                status = "identity_conflict"
                conflicts.append({"kind": "parse_run_filing_mismatch", "existing_filing_id": existing_parse.get("filing_id")})
            else:
                if metadata_backfill:
                    status = "safe_metadata_backfill"
                else:
                    status = "exact_match" if existing_parse else "safe_new_parse_run"
        elif not errors:
            accession_matches = [
                row
                for row in filings
                if row.get("accession_number") == candidate.get("accession_number")
                and row.get("accession_number")
            ]
            if accession_matches:
                status = "identity_conflict"
                conflicts.append(
                    {
                        "kind": "accession_bound_to_different_filing",
                        "existing_filing_ids": sorted({row["filing_id"] for row in accession_matches}),
                    }
                )
            elif existing_parse:
                status = "identity_conflict"
                conflicts.append({"kind": "parse_run_id_collision", "existing_filing_id": existing_parse.get("filing_id")})

        if not errors and status != "identity_conflict" and other_period_matches:
            status = "legacy_period_collision"
            migration = assess_legacy_migration(candidate, other_period_matches, parse_runs)
            conflicts.append(
                {
                    "kind": "same_company_period_different_filing",
                    "existing_filing_ids": sorted({row["filing_id"] for row in other_period_matches}),
                    "existing_accessions": sorted(
                        {row["accession_number"] for row in other_period_matches if row["accession_number"]}
                    ),
                }
            )

        results.append(
            {
                **candidate,
                "status": status,
                "errors": errors,
                "conflicts": conflicts,
                "migration_eligible": migration["migration_eligible"] if migration else False,
                "migration_assessment": migration,
            }
        )

    status_counts = Counter(row["status"] for row in results)
    blocking_count = sum(status_counts.get(status, 0) for status in BLOCKING_STATUSES)
    return {
        "schema_version": "hk_identity_reconciliation_v1",
        "market": "HK",
        "read_only": True,
        "passed": bool(results) and blocking_count == 0,
        "ready_for_staging_import": bool(results) and blocking_count == 0,
        "summary": {
            "candidate_count": len(results),
            "database_filing_count": len(filings),
            "database_parse_run_count": len(parse_runs),
            "blocking_count": blocking_count,
            "migration_eligible_count": sum(1 for row in results if row["migration_eligible"]),
            "migration_ineligible_collision_count": sum(
                1
                for row in results
                if row["status"] == "legacy_period_collision" and not row["migration_eligible"]
            ),
            "synthetic_eval_fixture_collision_count": sum(
                1
                for row in results
                if row["status"] == "legacy_period_collision"
                and ((row.get("migration_assessment") or {}).get("evidence") or {}).get("legacy_source_kind")
                == "synthetic_eval_fixture"
            ),
            "repo_benchmark_identity_migrated_collision_count": sum(
                1
                for row in results
                if row["status"] == "legacy_period_collision"
                and ((row.get("migration_assessment") or {}).get("evidence") or {}).get(
                    "repo_benchmark_identity_migrated"
                )
                is True
            ),
            "status_counts": dict(sorted(status_counts.items())),
        },
        "required_importer": "db/imports/import_hk_evidence_package_to_postgres.py",
        "notes": [
            "Do not use document_full importer for this rebuild: it derives a different parse_run_id contract.",
            "legacy_period_collision requires an explicit identity migration/retirement decision before import.",
            "migration_eligible is an assessment result only; it never means the filing has been migrated.",
            "synthetic_eval_fixture collisions require benchmark identity migration before any staging retirement.",
            "When repo_benchmark_identity_migrated is true, only the exact legacy database row remains pending; the repository fixture must not be migrated again.",
        ],
        "candidates": results,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only HK identity reconciliation/staging gate")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--results-dir", type=Path, help="HK parser results; canonical identities are resolved via HKEX sidecars")
    source.add_argument("--staging-wiki-root", type=Path, help="Staging Wiki root containing HK report manifests")
    parser.add_argument("--downloads-root", type=Path, help="HK report downloads root; required with --results-dir")
    database = parser.add_mutually_exclusive_group(required=True)
    database.add_argument(
        "--database-env",
        action="store_true",
        help="Use libpq PG* environment variables so credentials do not appear in process arguments.",
    )
    database.add_argument("--database-inventory", type=Path, help="Read-only JSON snapshot with filings and parse_runs arrays")
    parser.add_argument(
        "--expected-database",
        help="Required with --database-env; must exactly match current_database().",
    )
    parser.add_argument("--json-output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    started_at = time.monotonic()
    args = build_parser().parse_args(argv)
    if args.database_env and not args.expected_database:
        raise SystemExit("--expected-database is required with --database-env")
    if args.results_dir:
        if not args.downloads_root:
            raise SystemExit("--downloads-root is required with --results-dir")
        candidates = candidates_from_builder(args.results_dir, args.downloads_root)
    else:
        candidates = candidates_from_staging(args.staging_wiki_root)
    inventory = (
        database_inventory_from_json(args.database_inventory)
        if args.database_inventory
        else database_inventory_from_postgres(args.expected_database)
    )
    report = reconcile_identities(candidates, inventory)
    artifacts = [Path(__file__).resolve()]
    if args.staging_wiki_root:
        artifacts.extend(sorted(args.staging_wiki_root.resolve().rglob("manifest.json")))
    if args.database_inventory:
        artifacts.append(args.database_inventory.resolve())
    blocking_count = int(report["summary"]["blocking_count"])
    failures = (
        [{"code": "identity_reconciliation_blocked", "count": blocking_count}]
        if blocking_count
        else []
    )
    source_mode = "--staging-wiki-root <configured-path>" if args.staging_wiki_root else (
        "--results-dir <configured-path> --downloads-root <configured-path>"
    )
    database_mode = (
        "--database-env --expected-database <configured-name>"
        if args.database_env
        else "--database-inventory <configured-path>"
    )
    report = attach_evidence_metadata(
        report,
        repo_root=REPO_ROOT,
        task_id="T10",
        environment_profile=(
            "local-hk-staging-postgres-read-only"
            if args.database_env
            else "local-hk-offline-inventory-read-only"
        ),
        command=(
            "python scripts/hk/audit_hk_identity_reconciliation.py "
            f"{source_mode} {database_mode} --json-output <artifact.json>"
        ),
        result="pass" if report["passed"] else "fail",
        failures=failures,
        started_at=started_at,
        artifacts=artifacts,
    )
    report = _portable_report_value(report)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    if args.json_output:
        write_json(args.json_output, report)
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
