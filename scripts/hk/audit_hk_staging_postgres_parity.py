#!/usr/bin/env python3
"""Audit HK staging packages against PostgreSQL Agent View rows.

The command is deliberately read-only.  It reuses the HK evidence-package
importer's row builder so the expected rows follow the same transformation as
the staging import.  An optional identity reconciliation report can be turned
into an exact legacy-retirement plan, but this module never executes it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
IMPORTS_DIR = REPO_ROOT / "db" / "imports"
MAINTENANCE_DIR = REPO_ROOT / "scripts" / "maintenance"
for import_dir in (IMPORTS_DIR, MAINTENANCE_DIR):
    if str(import_dir) not in sys.path:
        sys.path.insert(0, str(import_dir))

from evidence_metadata import attach_evidence_metadata  # noqa: E402
from import_hk_evidence_package_to_postgres import (  # noqa: E402
    build_company_record,
    build_statement_item_rows,
)

SCHEMA = "pdf2md_hk"
VIEW = "v_agent_financial_facts"
PARITY_SCHEMA_VERSION = "hk_staging_postgres_agent_view_parity_v1"
RETIREMENT_SCHEMA_VERSION = "hk_legacy_retirement_plan_v1"

IDENTITY_FIELDS = (
    "company_id",
    "company_ticker",
    "filing_id",
    "accession_number",
    "parse_run_id",
)
METADATA_FIELDS = (
    "statement_id",
    "statement_type",
    "statement_name",
    "canonical_name",
    "item_name",
)
VALUE_FIELDS = ("value", "raw_value", "unit", "currency", "scale")
PERIOD_FIELDS = ("period_key", "period_start", "period_end")
EVIDENCE_FIELDS = (
    "evidence_id",
    "evidence_page_number",
    "evidence_table_index",
    "evidence_row_index",
    "evidence_column_index",
    "evidence_bbox",
    "quote_text",
    "source_url",
)
COMPARISON_FIELDS = IDENTITY_FIELDS + METADATA_FIELDS + VALUE_FIELDS + PERIOD_FIELDS + EVIDENCE_FIELDS
SELECT_FIELDS = ("item_uid",) + COMPARISON_FIELDS
GLOBAL_SCOPE_FIELDS = ("filing_period_end", "report_type")
POSTGRES_SELECT_FIELDS = SELECT_FIELDS + GLOBAL_SCOPE_FIELDS
NUMERIC_FIELDS = {"value", "scale"}
INTEGER_FIELDS = {
    "evidence_page_number",
    "evidence_table_index",
    "evidence_row_index",
    "evidence_column_index",
}
DATE_FIELDS = {"period_start", "period_end"}
SAFE_RECONCILIATION_STATUSES = {
    "exact_match",
    "safe_metadata_backfill",
    "safe_new_filing",
    "safe_new_parse_run",
}


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return "<external>"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _report_family(value: Any) -> str:
    text = _text(value).lower().replace("-", "_").replace(" ", "_")
    if any(token in text for token in ("annual", "年报", "年度")):
        return "annual"
    if any(token in text for token in ("interim", "half", "中报", "半年")):
        return "interim"
    return text


def _json_value(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _date_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value)[:10]


def _normalized_field(field: str, value: Any) -> Any:
    if field in NUMERIC_FIELDS:
        if value in (None, ""):
            return None
        numeric = _decimal(value)
        return str(numeric.normalize()) if numeric is not None else {"invalid_numeric": str(value)}
    if field in INTEGER_FIELDS:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return str(value)
    if field in DATE_FIELDS:
        return _date_text(value)
    if field == "evidence_bbox":
        return _json_value(value or [])
    if value is None:
        return None
    return str(value)


def _rows_digest(rows: Iterable[dict[str, Any]]) -> str:
    normalized = [
        {
            "item_uid": _text(row.get("item_uid")),
            **{field: _normalized_field(field, row.get(field)) for field in COMPARISON_FIELDS},
        }
        for row in rows
    ]
    normalized.sort(key=lambda row: row["item_uid"])
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def discover_package_dirs(staging_wiki_root: Path) -> list[Path]:
    packages: list[Path] = []
    for manifest_path in sorted(staging_wiki_root.resolve().rglob("manifest.json")):
        manifest = read_json(manifest_path, {})
        if isinstance(manifest, dict) and _text(manifest.get("market")).upper() == "HK":
            packages.append(manifest_path.parent)
    return packages


def _known_sources(source_map: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(item.get("evidence_id")): item
        for item in source_map.get("entries") or []
        if isinstance(item, dict) and _text(item.get("evidence_id"))
    }


def _evidence_value(evidence: dict[str, Any] | None, field: str, fallback: Any) -> Any:
    value = evidence.get(field) if evidence else None
    return value if value is not None else fallback


def load_package_expectation(package_dir: Path) -> dict[str, Any]:
    manifest = read_json(package_dir / "manifest.json", {})
    financial_data = read_json(package_dir / "metrics" / "financial_data.json", {})
    source_map = read_json(package_dir / "qa" / "source_map.json", {})
    errors: list[str] = []
    if not isinstance(manifest, dict):
        manifest = {}
        errors.append("manifest_invalid")
    if _text(manifest.get("market")).upper() != "HK":
        errors.append("manifest_market_not_hk")
    for field in ("company_id", "filing_id", "parse_run_id", "accession_number", "period_end"):
        if not _text(manifest.get(field)):
            errors.append(f"manifest_missing_{field}")
    if not isinstance(financial_data, dict):
        financial_data = {}
        errors.append("financial_data_invalid")
    if not isinstance(source_map, dict):
        source_map = {}
        errors.append("source_map_invalid")

    parse_run_id = _text(manifest.get("parse_run_id"))
    company = build_company_record(manifest)
    sources = _known_sources(source_map)
    rows: list[dict[str, Any]] = []
    for item in build_statement_item_rows(manifest, financial_data, source_map, parse_run_id):
        evidence_id = _text(item.get("evidence_id"))
        evidence = sources.get(evidence_id) if evidence_id else None
        stored_evidence_id = evidence_id if evidence is not None else None
        rows.append(
            {
                "item_uid": item.get("item_uid"),
                "company_id": item.get("company_id"),
                "company_ticker": company.get("ticker"),
                "filing_id": item.get("filing_id"),
                "accession_number": manifest.get("accession_number"),
                "parse_run_id": item.get("parse_run_id"),
                "statement_id": item.get("statement_id"),
                "statement_type": item.get("statement_type"),
                "statement_name": item.get("statement_name"),
                "canonical_name": item.get("canonical_name"),
                "item_name": item.get("item_name"),
                "value": item.get("value"),
                "raw_value": item.get("raw_value"),
                "unit": item.get("unit"),
                "currency": item.get("currency"),
                "scale": item.get("scale"),
                "period_key": item.get("period_key"),
                "period_start": item.get("period_start"),
                "period_end": item.get("period_end"),
                "evidence_id": stored_evidence_id,
                "evidence_page_number": _evidence_value(evidence, "page_number", item.get("source_page_number")),
                "evidence_table_index": _evidence_value(evidence, "table_index", item.get("source_table_index")),
                "evidence_row_index": _evidence_value(evidence, "row_index", item.get("source_row_index")),
                "evidence_column_index": _evidence_value(evidence, "column_index", item.get("source_column_index")),
                # evidence_citations stores an empty JSON array when its bbox is absent,
                # and the Agent View coalesces that value before the statement-item bbox.
                "evidence_bbox": (evidence.get("bbox") or evidence.get("source_bbox") or []) if evidence else (item.get("source_bbox") or []),
                "quote_text": evidence.get("quote_text") if evidence else None,
                "source_url": (evidence.get("source_url") if evidence else None) or manifest.get("source_url"),
            }
        )
    if not rows:
        errors.append("financial_data_has_no_statement_rows")
    item_counts = Counter(_text(row.get("item_uid")) for row in rows)
    duplicate_item_uids = sorted(item_uid for item_uid, count in item_counts.items() if not item_uid or count > 1)
    if duplicate_item_uids:
        errors.append("duplicate_expected_item_uid")
    return {
        "package_path": _portable_path(package_dir),
        "company_id": _text(manifest.get("company_id")),
        "ticker": _text(manifest.get("ticker") or manifest.get("stock_code")),
        "filing_id": _text(manifest.get("filing_id")),
        "parse_run_id": parse_run_id,
        "accession_number": _text(manifest.get("accession_number")),
        "period_end": _date_text(manifest.get("period_end")),
        "report_family": _report_family(manifest.get("report_type") or manifest.get("form")),
        "errors": sorted(set(errors)),
        "duplicate_item_uids": duplicate_item_uids,
        "rows": rows,
        "expected_row_count": len(rows),
        "expected_rows_sha256": _rows_digest(rows),
    }


def _diff_code(field: str) -> str:
    if field in IDENTITY_FIELDS:
        return "identity_diff"
    if field == "value":
        return "value_mismatch"
    if field == "raw_value":
        return "raw_value_diff"
    if field in {"unit", "scale"}:
        return "unit_display_diff"
    if field == "currency":
        return "currency_label_diff"
    if field in PERIOD_FIELDS:
        return "period_diff"
    if field in EVIDENCE_FIELDS:
        return "evidence_diff"
    return "metadata_diff"


def compare_rows(expected: dict[str, Any], observed: dict[str, Any]) -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []
    for field in COMPARISON_FIELDS:
        expected_value = _normalized_field(field, expected.get(field))
        observed_value = _normalized_field(field, observed.get(field))
        if expected_value == observed_value:
            continue
        diffs.append(
            {
                "code": _diff_code(field),
                "field": field,
                "expected": expected_value,
                "observed": observed_value,
            }
        )
    return diffs


def _group_rows(rows: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_text(row.get("item_uid"))].append(row)
    return grouped


def check_package_parity(
    package: dict[str, Any],
    observed_rows: list[dict[str, Any]],
    *,
    max_diff_examples: int = 50,
) -> dict[str, Any]:
    expected_rows = package.get("rows") or []
    parse_run_id = package.get("parse_run_id")
    scoped_observed = [row for row in observed_rows if _text(row.get("parse_run_id")) == parse_run_id]
    expected_by_uid = _group_rows(expected_rows)
    observed_by_uid = _group_rows(scoped_observed)
    expected_ids = set(expected_by_uid)
    observed_ids = set(observed_by_uid)
    missing_ids = sorted(expected_ids - observed_ids)
    extra_ids = sorted(observed_ids - expected_ids)
    duplicate_expected = sorted(item_uid for item_uid, rows in expected_by_uid.items() if not item_uid or len(rows) != 1)
    duplicate_observed = sorted(item_uid for item_uid, rows in observed_by_uid.items() if not item_uid or len(rows) != 1)
    diff_counts: Counter[str] = Counter()
    diff_examples: list[dict[str, Any]] = []

    for code, item_ids in (
        ("missing_agent_row", missing_ids),
        ("extra_agent_row", extra_ids),
        ("duplicate_expected_item_uid", duplicate_expected),
        ("duplicate_agent_item_uid", duplicate_observed),
    ):
        if item_ids:
            diff_counts[code] += len(item_ids)
        for item_uid in item_ids:
            if len(diff_examples) < max_diff_examples:
                diff_examples.append({"code": code, "item_uid": item_uid})

    for item_uid in sorted(expected_ids & observed_ids):
        if len(expected_by_uid[item_uid]) != 1 or len(observed_by_uid[item_uid]) != 1:
            continue
        for diff in compare_rows(expected_by_uid[item_uid][0], observed_by_uid[item_uid][0]):
            diff_counts[diff["code"]] += 1
            if len(diff_examples) < max_diff_examples:
                diff_examples.append({"item_uid": item_uid, **diff})

    for error in package.get("errors") or []:
        diff_counts[str(error)] += 1
    passed = bool(expected_rows) and not diff_counts
    return {
        "package_path": package.get("package_path"),
        "company_id": package.get("company_id"),
        "ticker": package.get("ticker"),
        "filing_id": package.get("filing_id"),
        "parse_run_id": parse_run_id,
        "accession_number": package.get("accession_number"),
        "period_end": package.get("period_end"),
        "report_family": package.get("report_family"),
        "passed": passed,
        "expected_row_count": len(expected_rows),
        "observed_row_count": len(scoped_observed),
        "matched_item_uid_count": len(expected_ids & observed_ids),
        "expected_rows_sha256": package.get("expected_rows_sha256"),
        "observed_rows_sha256": _rows_digest(scoped_observed),
        "diff_counts": dict(sorted(diff_counts.items())),
        "diff_examples": diff_examples,
    }


def _package_scope(package: dict[str, Any]) -> tuple[str, str | None, str]:
    return (
        _text(package.get("company_id")),
        _date_text(package.get("period_end")),
        _report_family(package.get("report_family")),
    )


def audit_global_agent_scopes(
    packages: list[dict[str, Any]],
    observed_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    expected_by_scope: dict[tuple[str, str | None, str], set[str]] = defaultdict(set)
    package_by_parse_run: dict[str, dict[str, Any]] = {}
    for package in packages:
        expected_by_scope[_package_scope(package)].add(_text(package.get("filing_id")))
        parse_run_id = _text(package.get("parse_run_id"))
        if parse_run_id:
            package_by_parse_run[parse_run_id] = package

    expected_company_ids = {scope[0] for scope in expected_by_scope if scope[0]}
    observed_by_scope: dict[tuple[str, str | None, str], list[dict[str, Any]]] = defaultdict(list)
    unclassified_rows: list[dict[str, Any]] = []
    for row in observed_rows:
        company_id = _text(row.get("company_id"))
        if company_id not in expected_company_ids:
            continue
        package = package_by_parse_run.get(_text(row.get("parse_run_id")))
        filing_period_end = _date_text(row.get("filing_period_end"))
        family = _report_family(row.get("report_type") or row.get("report_family"))
        if package:
            filing_period_end = filing_period_end or _date_text(package.get("period_end"))
            family = family or _report_family(package.get("report_family"))
        if not filing_period_end or not family:
            unclassified_rows.append(
                {
                    "company_id": company_id,
                    "filing_id": _text(row.get("filing_id")),
                    "parse_run_id": _text(row.get("parse_run_id")),
                    "item_uid": _text(row.get("item_uid")),
                }
            )
            continue
        scope = (company_id, filing_period_end, family)
        if scope in expected_by_scope:
            observed_by_scope[scope].append(row)

    collisions: list[dict[str, Any]] = []
    for scope, expected_filing_ids in sorted(
        expected_by_scope.items(), key=lambda item: tuple(str(value or "") for value in item[0])
    ):
        rows = observed_by_scope.get(scope, [])
        observed_filing_ids = sorted({_text(row.get("filing_id")) for row in rows if _text(row.get("filing_id"))})
        extra_filing_ids = sorted(set(observed_filing_ids) - expected_filing_ids)
        if not extra_filing_ids:
            continue
        collisions.append(
            {
                "company_id": scope[0],
                "period_end": scope[1],
                "report_family": scope[2],
                "expected_filing_ids": sorted(expected_filing_ids),
                "observed_filing_ids": observed_filing_ids,
                "extra_filing_ids": extra_filing_ids,
                "observed_parse_run_ids": sorted(
                    {_text(row.get("parse_run_id")) for row in rows if _text(row.get("parse_run_id"))}
                ),
                "observed_row_count": len(rows),
            }
        )

    return {
        "passed": not collisions and not unclassified_rows,
        "checked_scope_count": len(expected_by_scope),
        "collision_count": len(collisions),
        "extra_filing_count": sum(len(row["extra_filing_ids"]) for row in collisions),
        "unclassified_row_count": len(unclassified_rows),
        "collisions": collisions,
        "unclassified_rows": unclassified_rows,
    }


def audit_staging_parity(
    staging_wiki_root: Path,
    observed_rows: list[dict[str, Any]],
    *,
    database: dict[str, Any] | None = None,
    max_diff_examples: int = 50,
) -> dict[str, Any]:
    packages = [load_package_expectation(path) for path in discover_package_dirs(staging_wiki_root)]
    package_results = [
        check_package_parity(package, observed_rows, max_diff_examples=max_diff_examples)
        for package in packages
    ]
    diff_counts: Counter[str] = Counter()
    for result in package_results:
        diff_counts.update(result.get("diff_counts") or {})

    filing_counts = Counter(package.get("filing_id") for package in packages if package.get("filing_id"))
    parse_run_counts = Counter(package.get("parse_run_id") for package in packages if package.get("parse_run_id"))
    period_filings: dict[tuple[str, str | None, str], set[str]] = defaultdict(set)
    for package in packages:
        key = (package.get("company_id"), package.get("period_end"), package.get("report_family"))
        period_filings[key].add(package.get("filing_id"))
    duplicate_filing_ids = sorted(key for key, count in filing_counts.items() if count > 1)
    duplicate_parse_run_ids = sorted(key for key, count in parse_run_counts.items() if count > 1)
    duplicate_periods = [
        {"company_id": key[0], "period_end": key[1], "report_family": key[2], "filing_ids": sorted(filing_ids)}
        for key, filing_ids in sorted(period_filings.items(), key=lambda item: tuple(str(value or "") for value in item[0]))
        if len(filing_ids) > 1
    ]
    if duplicate_filing_ids:
        diff_counts["duplicate_package_filing_id"] += len(duplicate_filing_ids)
    if duplicate_parse_run_ids:
        diff_counts["duplicate_package_parse_run_id"] += len(duplicate_parse_run_ids)
    if duplicate_periods:
        diff_counts["duplicate_package_company_period"] += len(duplicate_periods)

    global_agent_scope = audit_global_agent_scopes(packages, observed_rows)
    if global_agent_scope["extra_filing_count"]:
        diff_counts["extra_agent_company_period_filing"] += global_agent_scope["extra_filing_count"]
    if global_agent_scope["unclassified_row_count"]:
        diff_counts["unclassified_agent_scope_row"] += global_agent_scope["unclassified_row_count"]
    passed_count = sum(1 for result in package_results if result.get("passed"))
    passed = bool(package_results) and passed_count == len(package_results) and not diff_counts
    return {
        "schema_version": PARITY_SCHEMA_VERSION,
        "market": "HK",
        "read_only": True,
        "passed": passed,
        "database": database or {},
        "staging_wiki_root": _portable_path(staging_wiki_root),
        "summary": {
            "package_count": len(package_results),
            "passed_package_count": passed_count,
            "failed_package_count": len(package_results) - passed_count,
            "expected_row_count": sum(result["expected_row_count"] for result in package_results),
            "observed_row_count": sum(result["observed_row_count"] for result in package_results),
            "currency_label_diff": diff_counts.get("currency_label_diff", 0),
            "diff_counts": dict(sorted(diff_counts.items())),
            "duplicate_package_filing_ids": duplicate_filing_ids,
            "duplicate_package_parse_run_ids": duplicate_parse_run_ids,
            "duplicate_package_periods": duplicate_periods,
            "canonical_package_parity_passed": bool(package_results) and passed_count == len(package_results),
        },
        "global_agent_scope": global_agent_scope,
        "packages": package_results,
    }


def load_agent_view_export(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = read_json(path, {})
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        raise SystemExit("Agent View export must be a JSON object with a rows array")
    rows = [row for row in payload["rows"] if isinstance(row, dict)]
    database = payload.get("database") if isinstance(payload.get("database"), dict) else {}
    return rows, database


def read_agent_view_from_postgres(
    company_ids: list[str],
    expected_database: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
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
                    "select current_database() as database_name, current_setting('transaction_read_only') as transaction_read_only"
                ).fetchone()
            )
            if identity["database_name"] != expected_database:
                raise SystemExit(
                    f"Connected database {identity['database_name']!r} does not match --expected-database {expected_database!r}"
                )
            if identity.get("transaction_read_only") != "on":
                raise SystemExit("PostgreSQL parity connection is not read-only")
            columns = {
                row["column_name"]
                for row in conn.execute(
                    "select column_name from information_schema.columns where table_schema = %s and table_name = %s",
                    (SCHEMA, VIEW),
                ).fetchall()
            }
            missing_columns = sorted(set(POSTGRES_SELECT_FIELDS) - columns)
            if missing_columns:
                raise SystemExit(f"{SCHEMA}.{VIEW} missing required columns: {', '.join(missing_columns)}")
            selected = ", ".join(POSTGRES_SELECT_FIELDS)
            rows = conn.execute(
                f"select {selected} from {SCHEMA}.{VIEW} where company_id = any(%s) "
                "order by company_id, filing_period_end, filing_id, parse_run_id, item_uid",
                (company_ids,),
            ).fetchall()
            conn.rollback()
    except SystemExit:
        raise
    except Exception as exc:
        raise SystemExit(f"PostgreSQL Agent View audit failed: {type(exc).__name__}") from None
    return [dict(row) for row in rows], identity


def build_legacy_retirement_plan(
    parity_report: dict[str, Any],
    reconciliation_report: dict[str, Any],
) -> dict[str, Any]:
    blockers: list[str] = []
    if parity_report.get("schema_version") != PARITY_SCHEMA_VERSION:
        blockers.append("invalid_parity_report_schema")
    if not parity_report.get("passed"):
        blockers.append("staging_parity_not_passed")
    summary = parity_report.get("summary") if isinstance(parity_report.get("summary"), dict) else {}
    if int(summary.get("package_count") or 0) <= 0:
        blockers.append("staging_parity_has_no_packages")
    if int(summary.get("currency_label_diff") or 0) != 0:
        blockers.append("currency_label_diff_nonzero")
    if reconciliation_report.get("schema_version") != "hk_identity_reconciliation_v1":
        blockers.append("invalid_identity_reconciliation_schema")
    staging_database = parity_report.get("database") if isinstance(parity_report.get("database"), dict) else {}
    if not _text(staging_database.get("database_name")):
        blockers.append("staging_database_identity_missing")

    parity_by_parse_run = {
        _text(row.get("parse_run_id")): row
        for row in parity_report.get("packages") or []
        if isinstance(row, dict) and _text(row.get("parse_run_id"))
    }
    operations: list[dict[str, Any]] = []
    candidates = reconciliation_report.get("candidates") or []
    if not candidates:
        blockers.append("identity_reconciliation_has_no_candidates")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            blockers.append("invalid_reconciliation_candidate")
            continue
        status = _text(candidate.get("status"))
        if status in SAFE_RECONCILIATION_STATUSES:
            if candidate.get("errors") or candidate.get("conflicts"):
                blockers.append(f"safe_candidate_has_conflicts:{_text(candidate.get('filing_id')) or '<missing>'}")
            continue
        if status != "legacy_period_collision" or not candidate.get("migration_eligible"):
            blockers.append(f"unresolved_identity_candidate:{_text(candidate.get('filing_id')) or '<missing>'}")
            continue
        migration = candidate.get("migration_assessment") if isinstance(candidate.get("migration_assessment"), dict) else {}
        evidence = migration.get("evidence") if isinstance(migration.get("evidence"), dict) else {}
        source_chain_fields = (
            "legacy_filing_task_id_match",
            "legacy_accession_missing",
            "package_task_id_match",
            "document_full_sha256_match",
        )
        source_chain_checks = {key: evidence.get(key) is True for key in source_chain_fields}
        legacy_parse_run_ids = [_text(value) for value in evidence.get("legacy_parse_run_ids") or [] if _text(value)]
        canonical_parse_run_id = _text(candidate.get("parse_run_id"))
        package_parity = parity_by_parse_run.get(canonical_parse_run_id)
        if not package_parity or not package_parity.get("passed"):
            blockers.append(f"canonical_parse_run_parity_missing:{canonical_parse_run_id or '<missing>'}")
        legacy_filing_id = _text(evidence.get("legacy_filing_id"))
        canonical_filing_id = _text(candidate.get("filing_id"))
        if not legacy_filing_id or len(legacy_parse_run_ids) != 1:
            blockers.append(f"legacy_identity_not_exact:{canonical_filing_id or '<missing>'}")
            continue
        if legacy_filing_id == canonical_filing_id:
            blockers.append(f"legacy_equals_canonical:{canonical_filing_id}")
            continue
        if not all(source_chain_checks.values()) or migration.get("blocking_reasons"):
            blockers.append(f"legacy_source_chain_not_verified:{canonical_filing_id or '<missing>'}")
            continue
        operations.append(
            {
                "operation": "retire_exact_legacy_filing_cascade",
                "company_id": candidate.get("company_id"),
                "ticker": candidate.get("ticker"),
                "period_end": candidate.get("period_end"),
                "report_family": candidate.get("report_family"),
                "legacy_filing_id": legacy_filing_id,
                "legacy_parse_run_id": legacy_parse_run_ids[0],
                "canonical_filing_id": canonical_filing_id,
                "canonical_parse_run_id": canonical_parse_run_id,
                "canonical_accession_number": candidate.get("accession_number"),
                "canonical_expected_agent_row_count": (package_parity or {}).get("expected_row_count"),
                "canonical_expected_rows_sha256": (package_parity or {}).get("expected_rows_sha256"),
                "source_chain_checks": source_chain_checks,
            }
        )

    legacy_ids = [row["legacy_filing_id"] for row in operations]
    canonical_ids = [row["canonical_filing_id"] for row in operations]
    if len(set(legacy_ids)) != len(legacy_ids):
        blockers.append("duplicate_legacy_filing_operation")
    if len(set(canonical_ids)) != len(canonical_ids):
        blockers.append("duplicate_canonical_filing_operation")
    operations.sort(key=lambda row: (str(row.get("ticker")), row["legacy_filing_id"]))
    operations_sha256 = hashlib.sha256(
        json.dumps(operations, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    blockers = sorted(set(blockers))
    return {
        "schema_version": RETIREMENT_SCHEMA_VERSION,
        "market": "HK",
        "read_only": True,
        "execution_authorized": False,
        "ready_for_controlled_staging_retirement": not blockers,
        "blocking_reasons": blockers,
        "staging_database": staging_database,
        "summary": {
            "operation_count": len(operations),
            "retirement_required": bool(operations),
            "operations_sha256": operations_sha256,
        },
        "required_execution_controls": [
            "verified Wiki and PostgreSQL snapshot identifiers",
            "verified restore/rollback rehearsal evidence",
            "staging database identity assertion",
            "serializable transaction and exact filing/parse-run row locks",
            "foreign-key cascade inventory with no unexpected dependants",
            "post-retirement canonical Agent View row-count and parity rerun",
        ],
        "operations": operations,
        "note": "This report is an exact review plan only; it cannot delete PostgreSQL rows.",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only HK staging package/PostgreSQL Agent View parity audit")
    parser.add_argument("--staging-wiki-root", type=Path, required=True)
    database = parser.add_mutually_exclusive_group(required=True)
    database.add_argument(
        "--database-env",
        action="store_true",
        help="Read PostgreSQL through libpq PG* environment variables; credentials never enter argv.",
    )
    database.add_argument("--agent-view-export", type=Path, help="Offline JSON object containing a rows array")
    parser.add_argument("--expected-database", help="Required with --database-env; must exactly match current_database()")
    parser.add_argument("--identity-reconciliation", type=Path, help="Optional reconciliation report used to generate a read-only retirement plan")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--max-diff-examples", type=int, default=50)
    return parser


def main(argv: list[str] | None = None) -> int:
    started_at = time.monotonic()
    args = build_parser().parse_args(argv)
    if args.max_diff_examples < 0:
        raise SystemExit("--max-diff-examples must be non-negative")
    package_dirs = discover_package_dirs(args.staging_wiki_root)
    company_ids = [_text(read_json(path / "manifest.json", {}).get("company_id")) for path in package_dirs]
    company_ids = sorted({value for value in company_ids if value})
    if args.database_env:
        if not args.expected_database:
            raise SystemExit("--expected-database is required with --database-env")
        observed_rows, database = read_agent_view_from_postgres(company_ids, args.expected_database)
    else:
        observed_rows, database = load_agent_view_export(args.agent_view_export)
    report = audit_staging_parity(
        args.staging_wiki_root,
        observed_rows,
        database=database,
        max_diff_examples=args.max_diff_examples,
    )
    if args.identity_reconciliation:
        reconciliation = read_json(args.identity_reconciliation, {})
        report["legacy_retirement_plan"] = build_legacy_retirement_plan(report, reconciliation)
        report["passed"] = bool(
            report["passed"] and report["legacy_retirement_plan"]["ready_for_controlled_staging_retirement"]
        )
    artifacts = [Path(__file__).resolve()]
    artifacts.extend(sorted(args.staging_wiki_root.resolve().rglob("manifest.json")))
    artifacts.extend(sorted(args.staging_wiki_root.resolve().rglob("metrics/financial_data.json")))
    if args.identity_reconciliation:
        artifacts.append(args.identity_reconciliation.resolve())
    if args.agent_view_export:
        artifacts.append(args.agent_view_export.resolve())
    failures: list[dict[str, Any]] = []
    failed_packages = int(report["summary"].get("failed_package_count") or 0)
    if failed_packages:
        failures.append({"code": "canonical_package_parity_failed", "count": failed_packages})
    failures.extend(
        {"code": str(code), "count": int(count)}
        for code, count in sorted((report["summary"].get("diff_counts") or {}).items())
        if count
    )
    retirement_plan = report.get("legacy_retirement_plan") or {}
    if retirement_plan.get("blocking_reasons"):
        failures.append(
            {
                "code": "legacy_retirement_plan_blocked",
                "count": len(retirement_plan["blocking_reasons"]),
            }
        )
    report = attach_evidence_metadata(
        report,
        repo_root=REPO_ROOT,
        task_id="T10",
        environment_profile=(
            "local-hk-staging-postgres-read-only"
            if args.database_env
            else "local-hk-offline-agent-view-read-only"
        ),
        command=(
            "python scripts/hk/audit_hk_staging_postgres_parity.py "
            "--staging-wiki-root <configured-path> "
            + (
                "--database-env --expected-database <configured-name> "
                if args.database_env
                else "--agent-view-export <configured-path> "
            )
            + (
                "--identity-reconciliation <configured-path> "
                if args.identity_reconciliation
                else ""
            )
            + "--json-output <artifact.json>"
        ),
        result="pass" if report["passed"] else "fail",
        failures=failures,
        started_at=started_at,
        artifacts=artifacts,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    if args.json_output:
        write_json(args.json_output, report)
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
