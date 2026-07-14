#!/usr/bin/env python3
"""Fail-closed executor for exact HK staging legacy-filing retirement.

Dry-run is the default.  Real deletion additionally requires an unexpired,
hash-bound approval document and an explicit operations digest confirmation.
This command must never target the production ``siq_hk`` database.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
HK_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(HK_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(HK_SCRIPTS_DIR))

from audit_hk_staging_postgres_parity import (  # noqa: E402
    POSTGRES_SELECT_FIELDS,
    _rows_digest,
)

SCHEMA = "pdf2md_hk"
PLAN_SCHEMA_VERSION = "hk_legacy_retirement_plan_v1"
IDENTITY_SCHEMA_VERSION = "hk_identity_reconciliation_v1"
PARITY_SCHEMA_VERSION = "hk_staging_postgres_agent_view_parity_v1"
APPROVAL_SCHEMA_VERSION = "hk_legacy_retirement_approval_v1"
AUDIT_SCHEMA_VERSION = "hk_legacy_retirement_execution_audit_v1"
PRODUCTION_DATABASES = {"siq_hk"}
DATABASE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_OPERATION_TYPES = {
    "retire_exact_legacy_filing_cascade",
    "retire_exact_legacy_fixture",
}
FIXTURE_CATALOG_PATH = REPO_ROOT / "scripts" / "maintenance" / "audit_market_postgres_fixture_contamination.py"

# Every current base relation in pdf2md_hk that carries filing_id or
# parse_run_id.  Runtime catalog enumeration must match this set exactly.
EXPECTED_IDENTITY_TABLES = frozenset(
    {
        "artifacts",
        "content_blocks",
        "evidence_citations",
        "filing_sections",
        "filings",
        "financial_all_metrics_wide",
        "financial_balance_sheet_items",
        "financial_cash_flow_statement_items",
        "financial_checks",
        "financial_facts",
        "financial_income_statement_items",
        "financial_items_enriched",
        "financial_key_metrics",
        "financial_note_links",
        "financial_statement_items",
        "financial_statements",
        "footnotes",
        "operating_metric_facts",
        "parse_runs",
        "parser_artifacts",
        "pdf_pages",
        "pdf_tables",
        "quality_reports",
        "raw_payload_refs",
        "retrieval_chunks",
        "table_quality_signals",
        "table_relations",
        "toc_entries",
    }
)
EXPLICIT_DELETE_TABLES = frozenset(
    {
        "financial_balance_sheet_items",
        "financial_cash_flow_statement_items",
        "financial_income_statement_items",
        "financial_key_metrics",
    }
)


class RetirementBlocked(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RetirementBlocked(f"invalid_json:{path.name}") from exc
    if not isinstance(value, dict):
        raise RetirementBlocked(f"json_object_required:{path.name}")
    return value


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _parse_time(value: Any, *, field: str) -> datetime:
    text = _text(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RetirementBlocked(f"approval_invalid_{field}") from exc
    if parsed.tzinfo is None:
        raise RetirementBlocked(f"approval_invalid_{field}")
    return parsed.astimezone(UTC)


def _report_family(value: Any) -> str:
    text = _text(value).lower().replace("-", "_").replace(" ", "_")
    if any(token in text for token in ("annual", "\u5e74\u62a5", "\u5e74\u5ea6")):
        return "annual"
    if any(token in text for token in ("interim", "half", "\u4e2d\u62a5", "\u534a\u5e74")):
        return "interim"
    return text


def validate_staging_database_name(database_name: str) -> None:
    normalized = _text(database_name)
    lowered = normalized.lower()
    if not DATABASE_NAME_RE.fullmatch(normalized):
        raise RetirementBlocked("expected_database_invalid")
    if lowered in PRODUCTION_DATABASES or "prod" in lowered or "production" in lowered:
        raise RetirementBlocked("production_database_forbidden")
    if "stage" not in lowered and "staging" not in lowered:
        raise RetirementBlocked("staging_database_name_required")


def _literal_assignment(path: Path, assignment_name: str) -> Any:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(target, ast.Name) and target.id == assignment_name for target in targets):
            return ast.literal_eval(node.value)
    raise RetirementBlocked(f"fixture_catalog_assignment_missing:{assignment_name}")


def _hk_fixture_catalog() -> tuple[dict[str, dict[str, Any]], str]:
    catalog = _literal_assignment(FIXTURE_CATALOG_PATH, "LEGACY_REAL_IDENTITY_FIXTURES")
    if not isinstance(catalog, dict):
        raise RetirementBlocked("fixture_catalog_invalid")
    selected = {
        str(key): dict(value)
        for key, value in catalog.items()
        if isinstance(value, dict) and _text(value.get("market")).upper() == "HK"
    }
    return selected, _sha256(FIXTURE_CATALOG_PATH)


def _candidate_by_canonical_filing(identity: dict[str, Any]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for candidate in identity.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        filing_id = _text(candidate.get("filing_id"))
        if not filing_id or filing_id in output:
            raise RetirementBlocked("identity_candidates_not_unique")
        output[filing_id] = candidate
    return output


def _parity_packages(parity: dict[str, Any]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for package in parity.get("packages") or []:
        if not isinstance(package, dict):
            raise RetirementBlocked("parity_package_invalid")
        parse_run_id = _text(package.get("parse_run_id"))
        if not parse_run_id or parse_run_id in output:
            raise RetirementBlocked("parity_parse_runs_not_unique")
        output[parse_run_id] = package
    return output


def _plan_extra_filing_ids(parity: dict[str, Any]) -> set[str]:
    scope = parity.get("global_agent_scope") if isinstance(parity.get("global_agent_scope"), dict) else {}
    extras: list[str] = []
    for collision in scope.get("collisions") or []:
        if isinstance(collision, dict):
            extras.extend(_text(value) for value in collision.get("extra_filing_ids") or [])
    if any(not value for value in extras) or len(extras) != len(set(extras)):
        raise RetirementBlocked("parity_extra_filing_set_invalid")
    if int(scope.get("extra_filing_count") or 0) != len(extras):
        raise RetirementBlocked("parity_extra_filing_count_mismatch")
    if int(scope.get("unclassified_row_count") or 0) != 0:
        raise RetirementBlocked("parity_has_unclassified_rows")
    return set(extras)


def _canonical_parity_failures(parity: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    summary = parity.get("summary") if isinstance(parity.get("summary"), dict) else {}
    package_count = int(summary.get("package_count") or 0)
    passed_count = int(summary.get("passed_package_count") or 0)
    failed_count = int(summary.get("failed_package_count") or 0)
    if package_count != 50 or passed_count != 50 or failed_count != 0:
        failures.append("canonical_package_parity_not_50_of_50")
    if summary.get("canonical_package_parity_passed") is not True:
        failures.append("canonical_package_parity_not_passed")
    if int(summary.get("currency_label_diff") or 0) != 0:
        failures.append("canonical_currency_diff_nonzero")
    diff_counts = summary.get("diff_counts") if isinstance(summary.get("diff_counts"), dict) else {}
    unexpected = {
        str(code): int(count or 0)
        for code, count in diff_counts.items()
        if str(code) != "extra_agent_company_period_filing" and int(count or 0) != 0
    }
    if unexpected:
        failures.append("canonical_diff_nonzero")
    packages = parity.get("packages") or []
    if len(packages) != package_count:
        failures.append("canonical_package_list_count_mismatch")
    for package in packages:
        if not isinstance(package, dict):
            failures.append("canonical_package_invalid")
            continue
        if package.get("passed") is not True or package.get("diff_counts"):
            failures.append(f"canonical_package_failed:{_text(package.get('parse_run_id'))}")
        if int(package.get("expected_row_count") or 0) <= 0:
            failures.append(f"canonical_package_empty:{_text(package.get('parse_run_id'))}")
        if package.get("expected_rows_sha256") != package.get("observed_rows_sha256"):
            failures.append(f"canonical_package_digest_mismatch:{_text(package.get('parse_run_id'))}")
    return sorted(set(failures))


def validate_input_documents(
    *,
    plan: dict[str, Any],
    identity: dict[str, Any],
    parity: dict[str, Any],
    expected_database: str,
    identity_sha256: str,
) -> tuple[list[dict[str, Any]], list[str], str]:
    failures: list[str] = []
    try:
        validate_staging_database_name(expected_database)
    except RetirementBlocked as exc:
        failures.append(exc.code)
    if plan.get("schema_version") != PLAN_SCHEMA_VERSION:
        failures.append("plan_schema_invalid")
    if identity.get("schema_version") != IDENTITY_SCHEMA_VERSION:
        failures.append("identity_schema_invalid")
    if parity.get("schema_version") != PARITY_SCHEMA_VERSION:
        failures.append("parity_schema_invalid")
    if plan.get("market") != "HK":
        failures.append("plan_market_not_hk")
    if plan.get("read_only") is not True or plan.get("execution_authorized") is not False:
        failures.append("plan_authority_contract_invalid")
    if plan.get("ready_for_controlled_staging_retirement") is not True:
        failures.append("plan_not_ready")
    if plan.get("blocking_reasons"):
        failures.append("plan_has_blocking_reasons")
    plan_database = plan.get("staging_database") if isinstance(plan.get("staging_database"), dict) else {}
    parity_database = parity.get("database") if isinstance(parity.get("database"), dict) else {}
    if _text(plan_database.get("database_name")) != expected_database:
        failures.append("plan_expected_database_mismatch")
    if _text(parity_database.get("database_name")) != expected_database:
        failures.append("parity_expected_database_mismatch")

    embedded = parity.get("legacy_retirement_plan")
    if not isinstance(embedded, dict) or _json_sha256(embedded) != _json_sha256(plan):
        failures.append("plan_not_bound_to_parity_report")
    artifact_checksums = parity.get("artifact_checksums") if isinstance(parity.get("artifact_checksums"), dict) else {}
    if identity_sha256 not in {_text(value).lower() for value in artifact_checksums.values()}:
        failures.append("identity_not_bound_to_parity_report")
    failures.extend(_canonical_parity_failures(parity))

    operations = plan.get("operations") if isinstance(plan.get("operations"), list) else []
    operation_digest = _json_sha256(operations)
    plan_summary = plan.get("summary") if isinstance(plan.get("summary"), dict) else {}
    if int(plan_summary.get("operation_count") or 0) != len(operations):
        failures.append("plan_operation_count_mismatch")
    if _text(plan_summary.get("operations_sha256")).lower() != operation_digest:
        failures.append("plan_operations_digest_mismatch")
    if not operations:
        failures.append("plan_has_no_operations")

    legacy_ids = [_text(operation.get("legacy_filing_id")) for operation in operations if isinstance(operation, dict)]
    canonical_ids = [_text(operation.get("canonical_filing_id")) for operation in operations if isinstance(operation, dict)]
    if len(legacy_ids) != len(operations) or any(not value for value in legacy_ids):
        failures.append("plan_legacy_identity_missing")
    if len(canonical_ids) != len(operations) or any(not value for value in canonical_ids):
        failures.append("plan_canonical_identity_missing")
    if len(set(legacy_ids)) != len(legacy_ids):
        failures.append("plan_legacy_identity_duplicate")
    if len(set(canonical_ids)) != len(canonical_ids):
        failures.append("plan_canonical_identity_duplicate")
    if set(legacy_ids) & set(canonical_ids):
        failures.append("plan_legacy_canonical_overlap")
    try:
        if _plan_extra_filing_ids(parity) != set(legacy_ids):
            failures.append("parity_extra_set_not_equal_plan_legacy_set")
    except RetirementBlocked as exc:
        failures.append(exc.code)

    candidates = _candidate_by_canonical_filing(identity)
    packages = _parity_packages(parity)
    fixture_catalog, fixture_catalog_sha256 = _hk_fixture_catalog()
    enriched: list[dict[str, Any]] = []
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            failures.append(f"operation_invalid:{index}")
            continue
        operation_type = _text(operation.get("operation"))
        if operation_type not in ALLOWED_OPERATION_TYPES:
            failures.append(f"operation_type_forbidden:{index}")
            continue
        canonical_filing_id = _text(operation.get("canonical_filing_id"))
        canonical_parse_run_id = _text(operation.get("canonical_parse_run_id"))
        legacy_filing_id = _text(operation.get("legacy_filing_id"))
        legacy_parse_run_id = _text(operation.get("legacy_parse_run_id"))
        candidate = candidates.get(canonical_filing_id)
        package = packages.get(canonical_parse_run_id)
        if not candidate:
            failures.append(f"identity_candidate_missing:{canonical_filing_id}")
            continue
        if not package or package.get("passed") is not True:
            failures.append(f"canonical_parity_missing:{canonical_parse_run_id}")
            continue
        for field in ("company_id", "period_end", "report_family"):
            expected = _report_family(candidate.get(field)) if field == "report_family" else _text(candidate.get(field))
            observed = _report_family(operation.get(field)) if field == "report_family" else _text(operation.get(field))
            if expected != observed:
                failures.append(f"operation_candidate_{field}_mismatch:{canonical_filing_id}")
        if _text(candidate.get("parse_run_id")) != canonical_parse_run_id:
            failures.append(f"operation_candidate_parse_run_mismatch:{canonical_filing_id}")
        if _text(package.get("filing_id")) != canonical_filing_id:
            failures.append(f"operation_package_filing_mismatch:{canonical_filing_id}")
        if int(operation.get("canonical_expected_agent_row_count") or 0) != int(package.get("expected_row_count") or 0):
            failures.append(f"operation_canonical_row_count_mismatch:{canonical_filing_id}")
        if _text(operation.get("canonical_expected_rows_sha256")) != _text(package.get("expected_rows_sha256")):
            failures.append(f"operation_canonical_digest_mismatch:{canonical_filing_id}")

        migration = candidate.get("migration_assessment") if isinstance(candidate.get("migration_assessment"), dict) else {}
        evidence = migration.get("evidence") if isinstance(migration.get("evidence"), dict) else {}
        expected_task_id = ""
        expected_document_sha256 = ""
        if operation_type == "retire_exact_legacy_fixture":
            catalog_key = _text(operation.get("fixture_catalog_key"))
            catalog_entry = fixture_catalog.get(catalog_key)
            if not catalog_entry:
                failures.append(f"fixture_catalog_entry_missing:{canonical_filing_id}")
                continue
            expected_signature = {
                "legacy_filing_id": catalog_entry.get("filing_id"),
                "legacy_parse_run_id": catalog_entry.get("parse_run_id"),
                "legacy_task_id": catalog_entry.get("task_id"),
                "legacy_document_full_sha256": catalog_entry.get("document_full_sha256"),
                "fixture_version": catalog_entry.get("fixture_version"),
            }
            if any(_text(operation.get(key)) != _text(value) for key, value in expected_signature.items()):
                failures.append(f"fixture_catalog_signature_mismatch:{canonical_filing_id}")
            if evidence.get("repo_benchmark_identity_migrated") is not True or evidence.get("legacy_source_kind") != "synthetic_eval_fixture":
                failures.append(f"fixture_identity_migration_not_verified:{canonical_filing_id}")
            if _text(evidence.get("legacy_filing_id")) != legacy_filing_id or legacy_parse_run_id not in {
                _text(value) for value in evidence.get("legacy_parse_run_ids") or []
            }:
                failures.append(f"fixture_identity_report_mismatch:{canonical_filing_id}")
            expected_task_id = _text(catalog_entry.get("task_id"))
            expected_document_sha256 = _text(catalog_entry.get("document_full_sha256")).lower()
        else:
            required_checks = (
                "legacy_filing_task_id_match",
                "legacy_accession_missing",
                "package_task_id_match",
                "document_full_sha256_match",
            )
            if candidate.get("migration_eligible") is not True or migration.get("blocking_reasons"):
                failures.append(f"legacy_migration_not_eligible:{canonical_filing_id}")
            if not all(evidence.get(field) is True for field in required_checks):
                failures.append(f"legacy_source_chain_not_verified:{canonical_filing_id}")
            if _text(evidence.get("legacy_filing_id")) != legacy_filing_id:
                failures.append(f"legacy_filing_identity_mismatch:{canonical_filing_id}")
            if [_text(value) for value in evidence.get("legacy_parse_run_ids") or []] != [legacy_parse_run_id]:
                failures.append(f"legacy_parse_identity_mismatch:{canonical_filing_id}")
            expected_task_id = _text(evidence.get("legacy_filing_task_id"))
            expected_document_sha256 = _text(evidence.get("database_document_full_sha256")).lower()
            if operation.get("legacy_task_id") is not None and _text(operation.get("legacy_task_id")) != expected_task_id:
                failures.append(f"legacy_task_plan_mismatch:{canonical_filing_id}")
            if operation.get("legacy_document_full_sha256") is not None and _text(
                operation.get("legacy_document_full_sha256")
            ).lower() != expected_document_sha256:
                failures.append(f"legacy_document_hash_plan_mismatch:{canonical_filing_id}")
        if not expected_task_id or not SHA256_RE.fullmatch(expected_document_sha256):
            failures.append(f"legacy_task_or_hash_missing:{canonical_filing_id}")
        enriched.append(
            {
                **operation,
                "_legacy_expected_task_id": expected_task_id,
                "_legacy_expected_document_full_sha256": expected_document_sha256,
                "_canonical_expected_task_id": _text(candidate.get("parser_task_id")),
                "_canonical_expected_document_full_sha256": _text(candidate.get("document_full_sha256")).lower(),
            }
        )
    return enriched, sorted(set(failures)), fixture_catalog_sha256


def validate_approval(
    approval: dict[str, Any],
    *,
    now: datetime,
    expected_database: str,
    operations_sha256: str,
    input_hashes: dict[str, str],
    fixture_catalog_sha256: str,
    fixture_operation_present: bool,
) -> dict[str, Any]:
    if approval.get("schema_version") != APPROVAL_SCHEMA_VERSION:
        raise RetirementBlocked("approval_schema_invalid")
    if approval.get("approved") is not True or approval.get("authorized_action") != "execute_exact_hk_legacy_retirement":
        raise RetirementBlocked("approval_action_not_authorized")
    if _text(approval.get("expected_database")) != expected_database or approval.get("schema") != SCHEMA:
        raise RetirementBlocked("approval_database_binding_mismatch")
    for field, expected in (
        ("retirement_plan_sha256", input_hashes["retirement_plan_sha256"]),
        ("identity_reconciliation_sha256", input_hashes["identity_reconciliation_sha256"]),
        ("parity_report_sha256", input_hashes["parity_report_sha256"]),
        ("operations_sha256", operations_sha256),
    ):
        if _text(approval.get(field)).lower() != expected:
            raise RetirementBlocked(f"approval_{field}_mismatch")
    if fixture_operation_present and _text(approval.get("fixture_catalog_sha256")).lower() != fixture_catalog_sha256:
        raise RetirementBlocked("approval_fixture_catalog_sha256_mismatch")
    for field in ("backup_artifact_sha256", "restore_rehearsal_sha256"):
        if not SHA256_RE.fullmatch(_text(approval.get(field)).lower()):
            raise RetirementBlocked(f"approval_{field}_missing")
    approved_at = _parse_time(approval.get("approved_at"), field="approved_at")
    expires_at = _parse_time(approval.get("expires_at"), field="expires_at")
    if approved_at > now or expires_at <= now:
        raise RetirementBlocked("approval_not_current")
    if expires_at <= approved_at or expires_at - approved_at > timedelta(hours=24):
        raise RetirementBlocked("approval_expiry_window_invalid")
    if not _text(approval.get("approval_id")) or not _text(approval.get("approved_by")) or not _text(
        approval.get("execution_nonce")
    ):
        raise RetirementBlocked("approval_identity_missing")
    return {
        "approval_id": _text(approval.get("approval_id")),
        "approved_by": _text(approval.get("approved_by")),
        "approved_at": approved_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "execution_nonce_sha256": hashlib.sha256(_text(approval.get("execution_nonce")).encode("utf-8")).hexdigest(),
        "backup_artifact_sha256": _text(approval.get("backup_artifact_sha256")).lower(),
        "restore_rehearsal_sha256": _text(approval.get("restore_rehearsal_sha256")).lower(),
    }


def _database_identity(conn: Any) -> dict[str, str]:
    row = conn.execute(
        "select current_database() as database_name, current_setting('transaction_read_only') as transaction_read_only"
    ).fetchone()
    return dict(row)


def _schema_inventory(conn: Any) -> dict[str, Any]:
    rows = conn.execute(
        """
        select c.table_name, array_agg(c.column_name order by c.ordinal_position)
            filter (where c.column_name in ('filing_id', 'parse_run_id')) as identity_columns
        from information_schema.columns c
        join information_schema.tables t
          on t.table_schema = c.table_schema and t.table_name = c.table_name
        where c.table_schema = %s and t.table_type = 'BASE TABLE'
        group by c.table_name
        having bool_or(c.column_name in ('filing_id', 'parse_run_id'))
        order by c.table_name
        """,
        (SCHEMA,),
    ).fetchall()
    columns = {row["table_name"]: list(row["identity_columns"] or []) for row in rows}
    if set(columns) != EXPECTED_IDENTITY_TABLES:
        raise RetirementBlocked("identity_table_inventory_mismatch")

    foreign_keys = [
        dict(row)
        for row in conn.execute(
            """
            select con.conname as constraint_name,
                   child_ns.nspname as child_schema,
                   child.relname as child_table,
                   parent_ns.nspname as parent_schema,
                   parent.relname as parent_table,
                   case con.confdeltype
                     when 'c' then 'CASCADE' when 'n' then 'SET NULL'
                     when 'd' then 'SET DEFAULT' when 'r' then 'RESTRICT'
                     else 'NO ACTION' end as delete_action,
                   pg_get_constraintdef(con.oid) as definition
            from pg_constraint con
            join pg_class child on child.oid = con.conrelid
            join pg_namespace child_ns on child_ns.oid = child.relnamespace
            join pg_class parent on parent.oid = con.confrelid
            join pg_namespace parent_ns on parent_ns.oid = parent.relnamespace
            where con.contype = 'f' and parent_ns.nspname = %s
            order by child_ns.nspname, child.relname, con.conname
            """,
            (SCHEMA,),
        ).fetchall()
        if row["parent_table"] in EXPECTED_IDENTITY_TABLES
    ]
    unexpected_inbound = [
        row
        for row in foreign_keys
        if row["child_schema"] != SCHEMA or row["child_table"] not in EXPECTED_IDENTITY_TABLES
    ]
    if unexpected_inbound:
        raise RetirementBlocked("unexpected_inbound_foreign_key")
    cascade_children = {
        row["child_table"]
        for row in foreign_keys
        if row["parent_table"] in {"filings", "parse_runs"} and row["delete_action"] == "CASCADE"
    }
    expected_cascade_children = EXPECTED_IDENTITY_TABLES - EXPLICIT_DELETE_TABLES - {"filings"}
    if not expected_cascade_children <= cascade_children:
        raise RetirementBlocked("cascade_coverage_incomplete")
    actual_explicit = {
        table
        for table in EXPECTED_IDENTITY_TABLES - {"filings", "parse_runs"}
        if table not in cascade_children
    }
    if actual_explicit != EXPLICIT_DELETE_TABLES:
        raise RetirementBlocked("explicit_delete_table_inventory_mismatch")
    return {
        "identity_columns": columns,
        "foreign_keys": foreign_keys,
        "explicit_delete_tables": sorted(actual_explicit),
    }


def _document_identity_rows(conn: Any, filing_ids: list[str], *, lock: bool) -> dict[str, list[dict[str, Any]]]:
    suffix = " for update of f, pr" if lock else ""
    rows = conn.execute(
        f"""
        select f.filing_id, f.company_id, f.ticker, f.accession_number,
               f.period_end, f.report_type,
               pr.parse_run_id,
               coalesce(pr.artifact_hashes ->> 'document_full.json',
                        pr.artifact_hashes ->> 'parser/document_full.json') as document_full_sha256,
               coalesce(pr.raw #>> '{{task,task_id}}', pr.raw ->> 'task_id') as task_id
        from {SCHEMA}.filings f
        join {SCHEMA}.parse_runs pr on pr.filing_id = f.filing_id
        where f.filing_id = any(%s)
        order by f.filing_id, pr.parse_run_id{suffix}
        """,
        (filing_ids,),
    ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {filing_id: [] for filing_id in filing_ids}
    for row in rows:
        grouped.setdefault(row["filing_id"], []).append(dict(row))
    return grouped


def _assert_document_identity(row: dict[str, Any], operation: dict[str, Any], *, canonical: bool) -> None:
    prefix = "canonical" if canonical else "legacy"
    expected_filing = _text(operation[f"{prefix}_filing_id"])
    expected_parse = _text(operation[f"{prefix}_parse_run_id"])
    if row["filing_id"] != expected_filing or row["parse_run_id"] != expected_parse:
        raise RetirementBlocked(f"{prefix}_database_identity_mismatch:{expected_filing}")
    if _text(row.get("company_id")) != _text(operation.get("company_id")):
        raise RetirementBlocked(f"{prefix}_database_company_mismatch:{expected_filing}")
    if _text(row.get("period_end"))[:10] != _text(operation.get("period_end"))[:10]:
        raise RetirementBlocked(f"{prefix}_database_period_mismatch:{expected_filing}")
    if _report_family(row.get("report_type")) != _report_family(operation.get("report_family")):
        raise RetirementBlocked(f"{prefix}_database_report_family_mismatch:{expected_filing}")
    expected_task = operation[f"_{prefix}_expected_task_id"]
    expected_hash = operation[f"_{prefix}_expected_document_full_sha256"]
    if _text(row.get("task_id")) != expected_task:
        raise RetirementBlocked(f"{prefix}_database_task_mismatch:{expected_filing}")
    if _text(row.get("document_full_sha256")).lower() != expected_hash:
        raise RetirementBlocked(f"{prefix}_database_document_hash_mismatch:{expected_filing}")
    if canonical:
        if _text(row.get("accession_number")) != _text(operation.get("canonical_accession_number")):
            raise RetirementBlocked(f"canonical_database_accession_mismatch:{expected_filing}")
    elif _text(row.get("accession_number")):
        raise RetirementBlocked(f"legacy_accession_not_empty:{expected_filing}")


def _canonical_snapshot(conn: Any, parity: dict[str, Any]) -> dict[str, dict[str, Any]]:
    selected = ", ".join(POSTGRES_SELECT_FIELDS)
    snapshot: dict[str, dict[str, Any]] = {}
    for package in parity.get("packages") or []:
        parse_run_id = _text(package.get("parse_run_id"))
        rows = [
            dict(row)
            for row in conn.execute(
                f"select {selected} from {SCHEMA}.v_agent_financial_facts "
                "where parse_run_id = %s order by item_uid",
                (parse_run_id,),
            ).fetchall()
        ]
        observed = {"row_count": len(rows), "rows_sha256": _rows_digest(rows)}
        if observed["row_count"] != int(package.get("expected_row_count") or 0):
            raise RetirementBlocked(f"live_canonical_row_count_mismatch:{parse_run_id}")
        if observed["rows_sha256"] != _text(package.get("expected_rows_sha256")):
            raise RetirementBlocked(f"live_canonical_digest_mismatch:{parse_run_id}")
        snapshot[parse_run_id] = observed
    return snapshot


def _live_extra_identity_set(conn: Any, parity: dict[str, Any]) -> set[tuple[str, str]]:
    expected_scopes: dict[tuple[str, str, str], set[str]] = {}
    company_ids: set[str] = set()
    for package in parity.get("packages") or []:
        scope = (
            _text(package.get("company_id")),
            _text(package.get("period_end"))[:10],
            _report_family(package.get("report_family")),
        )
        expected_scopes.setdefault(scope, set()).add(_text(package.get("filing_id")))
        company_ids.add(scope[0])
    rows = conn.execute(
        f"""
        select distinct company_id, filing_period_end, report_type, filing_id, parse_run_id
        from {SCHEMA}.v_agent_financial_facts
        where company_id = any(%s)
        """,
        (sorted(company_ids),),
    ).fetchall()
    observed: dict[tuple[str, str, str], set[tuple[str, str]]] = {}
    for row in rows:
        scope = (
            _text(row.get("company_id")),
            _text(row.get("filing_period_end"))[:10],
            _report_family(row.get("report_type")),
        )
        if scope in expected_scopes:
            observed.setdefault(scope, set()).add((_text(row.get("filing_id")), _text(row.get("parse_run_id"))))
    extras: set[tuple[str, str]] = set()
    for scope, expected_filings in expected_scopes.items():
        observed_pairs = observed.get(scope, set())
        observed_filings = {filing_id for filing_id, _ in observed_pairs}
        if not expected_filings <= observed_filings:
            raise RetirementBlocked("live_canonical_scope_missing")
        extras.update(pair for pair in observed_pairs if pair[0] not in expected_filings)
    return extras


def _tree_counts(
    conn: Any,
    inventory: dict[str, Any],
    *,
    filing_id: str,
    parse_run_id: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table, columns in inventory["identity_columns"].items():
        if table == "filings":
            where, params = "filing_id = %s", (filing_id,)
        elif "filing_id" in columns and "parse_run_id" in columns:
            mismatch = conn.execute(
                f"select count(*) as count from {SCHEMA}.{table} "
                "where (filing_id = %s or parse_run_id = %s) "
                "and (filing_id is distinct from %s or parse_run_id is distinct from %s)",
                (filing_id, parse_run_id, filing_id, parse_run_id),
            ).fetchone()["count"]
            if int(mismatch) != 0:
                raise RetirementBlocked(f"cross_identity_rows_present:{table}:{filing_id}")
            where, params = "filing_id = %s and parse_run_id = %s", (filing_id, parse_run_id)
        elif "filing_id" in columns:
            where, params = "filing_id = %s", (filing_id,)
        else:
            where, params = "parse_run_id = %s", (parse_run_id,)
        counts[table] = int(
            conn.execute(f"select count(*) as count from {SCHEMA}.{table} where {where}", params).fetchone()["count"]
        )
    if counts.get("filings") != 1 or counts.get("parse_runs") != 1:
        raise RetirementBlocked(f"legacy_tree_root_count_invalid:{filing_id}")
    return counts


def _delete_operation(conn: Any, operation: dict[str, Any], inventory: dict[str, Any]) -> dict[str, int]:
    filing_id = _text(operation.get("legacy_filing_id"))
    parse_run_id = _text(operation.get("legacy_parse_run_id"))
    deleted: dict[str, int] = {}
    for table in sorted(EXPLICIT_DELETE_TABLES):
        cursor = conn.execute(
            f"delete from {SCHEMA}.{table} where filing_id = %s and parse_run_id = %s",
            (filing_id, parse_run_id),
        )
        deleted[table] = int(cursor.rowcount or 0)
    cursor = conn.execute(
        f"delete from {SCHEMA}.filings "
        "where filing_id = %s and company_id = %s and period_end = %s returning filing_id",
        (filing_id, operation.get("company_id"), operation.get("period_end")),
    )
    deleted_row = cursor.fetchone()
    duplicate_row = cursor.fetchone()
    if deleted_row is None or duplicate_row is not None:
        raise RetirementBlocked(f"legacy_filing_delete_count_invalid:{filing_id}")
    deleted["filings"] = 1
    remaining = _tree_counts_allow_absent(
        conn,
        inventory,
        filing_id=filing_id,
        parse_run_id=parse_run_id,
    )
    if any(remaining.values()):
        raise RetirementBlocked(f"legacy_tree_rows_remain:{filing_id}")
    return deleted


def _tree_counts_allow_absent(
    conn: Any,
    inventory: dict[str, Any],
    *,
    filing_id: str,
    parse_run_id: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table, columns in inventory["identity_columns"].items():
        predicates: list[str] = []
        params: list[str] = []
        if "filing_id" in columns:
            predicates.append("filing_id = %s")
            params.append(filing_id)
        if "parse_run_id" in columns:
            predicates.append("parse_run_id = %s")
            params.append(parse_run_id)
        counts[table] = int(
            conn.execute(
                f"select count(*) as count from {SCHEMA}.{table} where " + " or ".join(predicates),
                tuple(params),
            ).fetchone()["count"]
        )
    return counts


def _connect_default() -> Any:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise RetirementBlocked("psycopg_required") from exc
    return psycopg.connect("", row_factory=dict_row)


def run_retirement(
    *,
    retirement_plan_path: Path,
    identity_reconciliation_path: Path,
    parity_report_path: Path,
    expected_database: str,
    execute: bool = False,
    approval_path: Path | None = None,
    confirm_operations_sha256: str | None = None,
    connect: Callable[[], Any] | None = None,
    now: datetime | None = None,
    after_delete_hook: Callable[[Any, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    input_hashes = {
        "retirement_plan_sha256": _sha256(retirement_plan_path),
        "identity_reconciliation_sha256": _sha256(identity_reconciliation_path),
        "parity_report_sha256": _sha256(parity_report_path),
    }
    audit: dict[str, Any] = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "generated_at": timestamp.isoformat(),
        "task_id": "T10",
        "market": "HK",
        "mode": "execute" if execute else "dry_run",
        "dry_run": not execute,
        "execution_attempted": False,
        "execution_committed": False,
        "result": "fail",
        "expected_database": expected_database,
        "schema": SCHEMA,
        "input_bindings": input_hashes,
        "failures": [],
        "operations": [],
    }
    try:
        plan = _read_json(retirement_plan_path)
        identity = _read_json(identity_reconciliation_path)
        parity = _read_json(parity_report_path)
        operations, failures, fixture_catalog_sha256 = validate_input_documents(
            plan=plan,
            identity=identity,
            parity=parity,
            expected_database=expected_database,
            identity_sha256=input_hashes["identity_reconciliation_sha256"],
        )
        audit["input_bindings"]["fixture_catalog_sha256"] = fixture_catalog_sha256
        operation_digest = _json_sha256(plan.get("operations") or [])
        audit["operations_sha256"] = operation_digest
        audit["operation_count"] = len(operations)
        if failures:
            raise RetirementBlocked(";".join(failures))

        approval_audit: dict[str, Any] | None = None
        if execute:
            if approval_path is None:
                raise RetirementBlocked("approval_required_for_execute")
            if _text(confirm_operations_sha256).lower() != operation_digest:
                raise RetirementBlocked("operations_digest_confirmation_mismatch")
            approval = _read_json(approval_path)
            approval_audit = validate_approval(
                approval,
                now=timestamp,
                expected_database=expected_database,
                operations_sha256=operation_digest,
                input_hashes=input_hashes,
                fixture_catalog_sha256=fixture_catalog_sha256,
                fixture_operation_present=any(
                    operation.get("operation") == "retire_exact_legacy_fixture" for operation in operations
                ),
            )
        audit["approval"] = approval_audit or {"required": False, "reason": "dry_run"}

        connector = connect or _connect_default
        conn = connector()
        try:
            with conn.transaction():
                conn.execute(
                    "set transaction isolation level serializable " + ("read write" if execute else "read only")
                )
                if execute:
                    audit["execution_attempted"] = True
                    conn.execute("set local lock_timeout = '5s'")
                    conn.execute("set local statement_timeout = '120s'")
                    conn.execute("select pg_advisory_xact_lock(hashtextextended('siq_hk_legacy_retirement', 0))")
                database = _database_identity(conn)
                audit["database"] = database
                validate_staging_database_name(database["database_name"])
                if database["database_name"] != expected_database:
                    raise RetirementBlocked("connected_database_mismatch")
                if execute and database["transaction_read_only"] != "off":
                    raise RetirementBlocked("execute_transaction_not_read_write")
                if not execute and database["transaction_read_only"] != "on":
                    raise RetirementBlocked("dry_run_transaction_not_read_only")

                inventory = _schema_inventory(conn)
                audit["schema_inventory"] = inventory
                all_filing_ids = sorted(
                    {_text(operation["legacy_filing_id"]) for operation in operations}
                    | {_text(operation["canonical_filing_id"]) for operation in operations}
                )
                identities = _document_identity_rows(conn, all_filing_ids, lock=execute)
                for operation in operations:
                    legacy_rows = identities.get(_text(operation["legacy_filing_id"]), [])
                    canonical_rows = identities.get(_text(operation["canonical_filing_id"]), [])
                    if len(legacy_rows) != 1:
                        raise RetirementBlocked(f"legacy_database_row_count_invalid:{operation['legacy_filing_id']}")
                    if len(canonical_rows) != 1:
                        raise RetirementBlocked(f"canonical_database_row_count_invalid:{operation['canonical_filing_id']}")
                    _assert_document_identity(legacy_rows[0], operation, canonical=False)
                    _assert_document_identity(canonical_rows[0], operation, canonical=True)

                expected_extra_pairs = {
                    (_text(operation["legacy_filing_id"]), _text(operation["legacy_parse_run_id"]))
                    for operation in operations
                }
                if _live_extra_identity_set(conn, parity) != expected_extra_pairs:
                    raise RetirementBlocked("live_extra_identity_set_not_equal_plan")
                canonical_before = _canonical_snapshot(conn, parity)
                audit["canonical_before_sha256"] = _json_sha256(canonical_before)

                for operation in operations:
                    before_counts = _tree_counts(
                        conn,
                        inventory,
                        filing_id=_text(operation["legacy_filing_id"]),
                        parse_run_id=_text(operation["legacy_parse_run_id"]),
                    )
                    operation_audit = {
                        "operation": operation["operation"],
                        "legacy_filing_id": operation["legacy_filing_id"],
                        "legacy_parse_run_id": operation["legacy_parse_run_id"],
                        "canonical_filing_id": operation["canonical_filing_id"],
                        "canonical_parse_run_id": operation["canonical_parse_run_id"],
                        "legacy_task_id": operation["_legacy_expected_task_id"],
                        "legacy_document_full_sha256": operation[
                            "_legacy_expected_document_full_sha256"
                        ],
                        "before_counts": before_counts,
                        "delete_counts": {},
                        "status": "validated",
                    }
                    audit["operations"].append(operation_audit)
                    if execute:
                        operation_audit["delete_counts"] = _delete_operation(conn, operation, inventory)
                        operation_audit["status"] = "deleted_pending_commit"
                        if after_delete_hook is not None:
                            after_delete_hook(conn, operation)

                if execute:
                    if _live_extra_identity_set(conn, parity):
                        raise RetirementBlocked("legacy_extras_remain_after_delete")
                    canonical_after = _canonical_snapshot(conn, parity)
                    audit["canonical_after_sha256"] = _json_sha256(canonical_after)
                    if canonical_after != canonical_before:
                        raise RetirementBlocked("canonical_snapshot_changed")
            audit["execution_committed"] = execute
            if execute:
                for operation_audit in audit["operations"]:
                    operation_audit["status"] = "deleted_committed"
        finally:
            conn.close()
        audit["result"] = "pass"
        audit["safe_to_execute"] = not execute
    except RetirementBlocked as exc:
        audit["failures"].extend(
            {"code": code}
            for code in sorted(set(part for part in exc.code.split(";") if part))
        )
        for operation in audit["operations"]:
            if operation.get("status") == "deleted_pending_commit":
                operation["status"] = "rolled_back"
    except Exception as exc:  # pragma: no cover - defensive fail-closed path
        audit["failures"].append({"code": f"transaction_failed:{type(exc).__name__}"})
        for operation in audit["operations"]:
            if operation.get("status") == "deleted_pending_commit":
                operation["status"] = "rolled_back"
    return audit


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Controlled exact HK staging legacy-filing retirement")
    parser.add_argument("--retirement-plan", type=Path, required=True)
    parser.add_argument("--identity-reconciliation", type=Path, required=True)
    parser.add_argument("--parity-report", type=Path, required=True)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", help="Delete exact approved legacy trees; default is dry-run")
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--confirm-operations-sha256")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    preflight = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "execute" if args.execute else "dry_run",
        "result": "preflight_started",
        "execution_committed": False,
    }
    _write_json(args.json_output, preflight)
    audit = run_retirement(
        retirement_plan_path=args.retirement_plan,
        identity_reconciliation_path=args.identity_reconciliation,
        parity_report_path=args.parity_report,
        expected_database=args.expected_database,
        execute=args.execute,
        approval_path=args.approval,
        confirm_operations_sha256=args.confirm_operations_sha256,
    )
    _write_json(args.json_output, audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2, default=str))
    return 0 if audit["result"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
