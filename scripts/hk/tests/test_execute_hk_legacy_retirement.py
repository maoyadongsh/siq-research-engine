from __future__ import annotations

import importlib.util
import json
import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "execute_hk_legacy_retirement.py"
SPEC = importlib.util.spec_from_file_location("execute_hk_legacy_retirement_under_test", SOURCE)
assert SPEC and SPEC.loader
retirement = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(retirement)


def _sha(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _documents(*, database_name: str = "siq_hk_staging_test"):
    canonical_filing = "HK:00001:canonical"
    canonical_parse = "HK:00001:canonical:hash"
    legacy_filing = "HK:00001:legacy-task"
    legacy_parse = "parse-legacy"
    document_hash = "1" * 64
    packages = []
    for index in range(50):
        company_id = f"HK:{index + 1:05d}"
        parse_run_id = canonical_parse if index == 0 else f"canonical-parse-{index:02d}"
        filing_id = canonical_filing if index == 0 else f"{company_id}:canonical"
        packages.append(
            {
                "company_id": company_id,
                "filing_id": filing_id,
                "parse_run_id": parse_run_id,
                "period_end": "2025-12-31",
                "report_family": "annual",
                "passed": True,
                "expected_row_count": 1,
                "observed_row_count": 1,
                "expected_rows_sha256": "a" * 64,
                "observed_rows_sha256": "a" * 64,
                "diff_counts": {},
            }
        )
    identity = {
        "schema_version": retirement.IDENTITY_SCHEMA_VERSION,
        "candidates": [
            {
                "company_id": "HK:00001",
                "ticker": "00001",
                "filing_id": canonical_filing,
                "parse_run_id": canonical_parse,
                "parser_task_id": "canonical-task",
                "document_full_sha256": "2" * 64,
                "period_end": "2025-12-31",
                "report_family": "annual",
                "migration_eligible": True,
                "migration_assessment": {
                    "blocking_reasons": [],
                    "evidence": {
                        "legacy_filing_id": legacy_filing,
                        "legacy_parse_run_ids": [legacy_parse],
                        "legacy_filing_task_id": "legacy-task",
                        "database_document_full_sha256": document_hash,
                        "legacy_filing_task_id_match": True,
                        "legacy_accession_missing": True,
                        "package_task_id_match": True,
                        "document_full_sha256_match": True,
                    },
                },
            }
        ],
    }
    operation = {
        "operation": "retire_exact_legacy_filing_cascade",
        "company_id": "HK:00001",
        "ticker": "00001",
        "period_end": "2025-12-31",
        "report_family": "annual",
        "legacy_filing_id": legacy_filing,
        "legacy_parse_run_id": legacy_parse,
        "legacy_task_id": "legacy-task",
        "legacy_document_full_sha256": document_hash,
        "canonical_filing_id": canonical_filing,
        "canonical_parse_run_id": canonical_parse,
        "canonical_accession_number": "canonical",
        "canonical_expected_agent_row_count": 1,
        "canonical_expected_rows_sha256": "a" * 64,
        "source_chain_checks": {
            "legacy_filing_task_id_match": True,
            "legacy_accession_missing": True,
            "package_task_id_match": True,
            "document_full_sha256_match": True,
        },
    }
    plan = {
        "schema_version": retirement.PLAN_SCHEMA_VERSION,
        "market": "HK",
        "read_only": True,
        "execution_authorized": False,
        "ready_for_controlled_staging_retirement": True,
        "blocking_reasons": [],
        "staging_database": {"database_name": database_name},
        "summary": {
            "operation_count": 1,
            "operations_sha256": retirement._json_sha256([operation]),
        },
        "operations": [operation],
    }
    parity = {
        "schema_version": retirement.PARITY_SCHEMA_VERSION,
        "passed": False,
        "database": {"database_name": database_name},
        "summary": {
            "package_count": 50,
            "passed_package_count": 50,
            "failed_package_count": 0,
            "currency_label_diff": 0,
            "canonical_package_parity_passed": True,
            "diff_counts": {"extra_agent_company_period_filing": 1},
        },
        "packages": packages,
        "global_agent_scope": {
            "extra_filing_count": 1,
            "unclassified_row_count": 0,
            "collisions": [
                {
                    "company_id": "HK:00001",
                    "period_end": "2025-12-31",
                    "report_family": "annual",
                    "extra_filing_ids": [legacy_filing],
                    "observed_parse_run_ids": [canonical_parse, legacy_parse],
                }
            ],
        },
        "legacy_retirement_plan": deepcopy(plan),
        "artifact_checksums": {},
    }
    return plan, identity, parity


def test_database_guard_rejects_production_and_nonstaging_names():
    for database_name, code in (
        ("siq_hk", "production_database_forbidden"),
        ("siq_hk_production", "production_database_forbidden"),
        ("siq_hk_copy", "staging_database_name_required"),
        ("siq-hk-staging", "expected_database_invalid"),
    ):
        with pytest.raises(retirement.RetirementBlocked, match=code):
            retirement.validate_staging_database_name(database_name)


def test_input_validation_uses_canonical_50_of_50_without_circular_global_pass_requirement(tmp_path):
    plan, identity, parity = _documents()
    identity_path = tmp_path / "identity.json"
    identity_path.write_text(json.dumps(identity), encoding="utf-8")
    identity_sha = retirement._sha256(identity_path)
    parity["artifact_checksums"] = {"identity.json": identity_sha}

    operations, failures, _ = retirement.validate_input_documents(
        plan=plan,
        identity=identity,
        parity=parity,
        expected_database="siq_hk_staging_test",
        identity_sha256=identity_sha,
    )

    assert failures == []
    assert len(operations) == 1
    assert operations[0]["_legacy_expected_task_id"] == "legacy-task"
    assert parity["passed"] is False


def test_input_validation_requires_extra_set_to_equal_plan_and_plan_to_be_ready(tmp_path):
    plan, identity, parity = _documents()
    identity_path = tmp_path / "identity.json"
    identity_path.write_text(json.dumps(identity), encoding="utf-8")
    identity_sha = retirement._sha256(identity_path)
    parity["artifact_checksums"] = {"identity.json": identity_sha}
    plan["ready_for_controlled_staging_retirement"] = False
    plan["blocking_reasons"] = ["fixture_pending"]
    parity["legacy_retirement_plan"] = deepcopy(plan)
    parity["global_agent_scope"]["collisions"].append(
        {"extra_filing_ids": ["HK:00700:2025-annual"]}
    )
    parity["global_agent_scope"]["extra_filing_count"] = 2

    _, failures, _ = retirement.validate_input_documents(
        plan=plan,
        identity=identity,
        parity=parity,
        expected_database="siq_hk_staging_test",
        identity_sha256=identity_sha,
    )

    assert "plan_not_ready" in failures
    assert "plan_has_blocking_reasons" in failures
    assert "parity_extra_set_not_equal_plan_legacy_set" in failures


def test_fixture_operation_requires_exact_catalog_signature(tmp_path):
    plan, identity, parity = _documents()
    candidate = identity["candidates"][0]
    candidate["filing_id"] = "HK:00700:canonical"
    candidate["parse_run_id"] = "HK:00700:canonical:hash"
    candidate["company_id"] = "HK:00700"
    candidate["ticker"] = "00700"
    candidate["migration_eligible"] = False
    candidate["migration_assessment"]["evidence"].update(
        {
            "legacy_filing_id": "HK:00700:2025-annual",
            "legacy_parse_run_ids": ["parse_ab6f710544effd640be32294"],
            "repo_benchmark_identity_migrated": True,
            "legacy_source_kind": "synthetic_eval_fixture",
        }
    )
    operation = plan["operations"][0]
    operation.update(
        {
            "operation": "retire_exact_legacy_fixture",
            "company_id": "HK:00700",
            "ticker": "00700",
            "legacy_filing_id": "HK:00700:2025-annual",
            "legacy_parse_run_id": "parse_ab6f710544effd640be32294",
            "legacy_task_id": "wrong-task",
            "legacy_document_full_sha256": "0" * 64,
            "fixture_catalog_key": "hk_row_period_document_full.json",
            "fixture_version": "legacy_real_identity_v1",
            "canonical_filing_id": candidate["filing_id"],
            "canonical_parse_run_id": candidate["parse_run_id"],
        }
    )
    package = parity["packages"][0]
    package.update(
        {
            "company_id": "HK:00700",
            "filing_id": candidate["filing_id"],
            "parse_run_id": candidate["parse_run_id"],
        }
    )
    plan["summary"]["operations_sha256"] = retirement._json_sha256(plan["operations"])
    parity["legacy_retirement_plan"] = deepcopy(plan)
    parity["global_agent_scope"]["collisions"][0]["extra_filing_ids"] = [operation["legacy_filing_id"]]
    identity_path = tmp_path / "identity.json"
    identity_path.write_text(json.dumps(identity), encoding="utf-8")
    identity_sha = retirement._sha256(identity_path)
    parity["artifact_checksums"] = {"identity.json": identity_sha}

    _, failures, _ = retirement.validate_input_documents(
        plan=plan,
        identity=identity,
        parity=parity,
        expected_database="siq_hk_staging_test",
        identity_sha256=identity_sha,
    )

    assert any(code.startswith("fixture_catalog_signature_mismatch") for code in failures)


def test_approval_is_hash_bound_and_expires():
    now = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)
    hashes = {
        "retirement_plan_sha256": "1" * 64,
        "identity_reconciliation_sha256": "2" * 64,
        "parity_report_sha256": "3" * 64,
    }
    approval = {
        "schema_version": retirement.APPROVAL_SCHEMA_VERSION,
        "approved": True,
        "authorized_action": "execute_exact_hk_legacy_retirement",
        "expected_database": "siq_hk_staging_test",
        "schema": retirement.SCHEMA,
        **hashes,
        "operations_sha256": "4" * 64,
        "fixture_catalog_sha256": "5" * 64,
        "backup_artifact_sha256": "6" * 64,
        "restore_rehearsal_sha256": "7" * 64,
        "approval_id": "approval-1",
        "approved_by": "release-owner",
        "approved_at": (now - timedelta(minutes=5)).isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "execution_nonce": "one-time-nonce",
    }

    result = retirement.validate_approval(
        approval,
        now=now,
        expected_database="siq_hk_staging_test",
        operations_sha256="4" * 64,
        input_hashes=hashes,
        fixture_catalog_sha256="5" * 64,
        fixture_operation_present=True,
    )
    assert result["approval_id"] == "approval-1"
    expired = {**approval, "expires_at": (now - timedelta(seconds=1)).isoformat()}
    with pytest.raises(retirement.RetirementBlocked, match="approval_not_current"):
        retirement.validate_approval(
            expired,
            now=now,
            expected_database="siq_hk_staging_test",
            operations_sha256="4" * 64,
            input_hashes=hashes,
            fixture_catalog_sha256="5" * 64,
            fixture_operation_present=True,
        )


def test_parser_defaults_to_dry_run_and_requires_audit_output(tmp_path):
    args = retirement.build_parser().parse_args(
        [
            "--retirement-plan",
            str(tmp_path / "plan.json"),
            "--identity-reconciliation",
            str(tmp_path / "identity.json"),
            "--parity-report",
            str(tmp_path / "parity.json"),
            "--expected-database",
            "siq_hk_staging_test",
            "--json-output",
            str(tmp_path / "audit.json"),
        ]
    )
    assert args.execute is False
    assert args.approval is None


@pytest.mark.skipif(
    not os.getenv("SIQ_TEST_HK_RETIREMENT_POSTGRES_URL"),
    reason="SIQ_TEST_HK_RETIREMENT_POSTGRES_URL is not configured",
)
def test_real_postgres_dry_run_execute_and_failure_rollback(tmp_path):
    import psycopg
    from psycopg.rows import dict_row

    url = os.environ["SIQ_TEST_HK_RETIREMENT_POSTGRES_URL"]

    def connect():
        return psycopg.connect(url, row_factory=dict_row)

    with connect() as conn:
        database_name = conn.execute("select current_database()").fetchone()["current_database"]
        assert "staging" in database_name.lower() and "test" in database_name.lower()

    def reset_database():
        with connect() as conn:
            conn.execute("drop schema if exists pdf2md_hk cascade")
            conn.execute((retirement.REPO_ROOT / "db" / "ddl" / "020_create_pdf2md_hk_schema.sql").read_text())
            for index in range(50):
                company_id = f"HK:{index + 1:05d}"
                ticker = f"{index + 1:05d}"
                filing_id = "HK:00001:canonical" if index == 0 else f"{company_id}:canonical"
                parse_run_id = "HK:00001:canonical:hash" if index == 0 else f"canonical-parse-{index:02d}"
                task_id = "canonical-task" if index == 0 else f"canonical-task-{index:02d}"
                document_hash = "2" * 64 if index == 0 else _sha(parse_run_id)
                conn.execute(
                    "insert into pdf2md_hk.companies (company_id,ticker,company_name) values (%s,%s,%s)",
                    (company_id, ticker, company_id),
                )
                conn.execute(
                    "insert into pdf2md_hk.filings "
                    "(filing_id,company_id,ticker,report_type,period_end,accession_number) "
                    "values (%s,%s,%s,'annual','2025-12-31',%s)",
                    (filing_id, company_id, ticker, filing_id.rsplit(":", 1)[-1]),
                )
                conn.execute(
                    "insert into pdf2md_hk.parse_runs "
                    "(parse_run_id,filing_id,parser_version,rules_version,wiki_package_path,status,completed_at,artifact_hashes,raw) "
                    "values (%s,%s,'test','test','test','completed',now(),%s,%s)",
                    (parse_run_id, filing_id, json.dumps({"document_full.json": document_hash}), json.dumps({"task": {"task_id": task_id}})),
                )
                conn.execute(
                    "insert into pdf2md_hk.financial_statement_items "
                    "(item_uid,filing_id,parse_run_id,company_id,ticker,period_key,canonical_name,value) "
                    "values (%s,%s,%s,%s,%s,'2025-12-31','revenue',%s)",
                    (f"item-{index:02d}", filing_id, parse_run_id, company_id, ticker, index + 1),
                )
            conn.execute(
                "insert into pdf2md_hk.filings "
                "(filing_id,company_id,ticker,report_type,period_end,accession_number) "
                "values ('HK:00001:legacy-task','HK:00001','00001','annual','2025-12-31',null)"
            )
            conn.execute(
                "insert into pdf2md_hk.parse_runs "
                "(parse_run_id,filing_id,parser_version,rules_version,wiki_package_path,status,completed_at,artifact_hashes,raw) "
                "values ('parse-legacy','HK:00001:legacy-task','test','test','test','completed',now(),%s,%s)",
                (json.dumps({"document_full.json": "1" * 64}), json.dumps({"task": {"task_id": "legacy-task"}})),
            )
            conn.execute(
                "insert into pdf2md_hk.financial_statement_items "
                "(item_uid,filing_id,parse_run_id,company_id,ticker,period_key,canonical_name,value) "
                "values ('legacy-item','HK:00001:legacy-task','parse-legacy','HK:00001','00001','2025-12-31','revenue',0)"
            )
            conn.commit()

    def write_documents():
        plan, identity, parity = _documents(database_name=database_name)
        with connect() as conn:
            for package in parity["packages"]:
                rows = [
                    dict(row)
                    for row in conn.execute(
                        "select " + ", ".join(retirement.POSTGRES_SELECT_FIELDS) + " "
                        "from pdf2md_hk.v_agent_financial_facts where parse_run_id=%s order by item_uid",
                        (package["parse_run_id"],),
                    ).fetchall()
                ]
                digest = retirement._rows_digest(rows)
                package.update(
                    {
                        "expected_row_count": len(rows),
                        "observed_row_count": len(rows),
                        "expected_rows_sha256": digest,
                        "observed_rows_sha256": digest,
                    }
                )
        operation = plan["operations"][0]
        first_package = parity["packages"][0]
        operation["canonical_expected_agent_row_count"] = first_package["expected_row_count"]
        operation["canonical_expected_rows_sha256"] = first_package["expected_rows_sha256"]
        plan["summary"]["operations_sha256"] = retirement._json_sha256(plan["operations"])
        parity["legacy_retirement_plan"] = deepcopy(plan)
        identity_path = tmp_path / "identity.json"
        plan_path = tmp_path / "plan.json"
        parity_path = tmp_path / "parity.json"
        identity_path.write_text(json.dumps(identity), encoding="utf-8")
        parity["artifact_checksums"] = {"identity.json": retirement._sha256(identity_path)}
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        parity_path.write_text(json.dumps(parity), encoding="utf-8")
        approval = {
            "schema_version": retirement.APPROVAL_SCHEMA_VERSION,
            "approved": True,
            "authorized_action": "execute_exact_hk_legacy_retirement",
            "expected_database": database_name,
            "schema": retirement.SCHEMA,
            "retirement_plan_sha256": retirement._sha256(plan_path),
            "identity_reconciliation_sha256": retirement._sha256(identity_path),
            "parity_report_sha256": retirement._sha256(parity_path),
            "operations_sha256": plan["summary"]["operations_sha256"],
            "backup_artifact_sha256": "6" * 64,
            "restore_rehearsal_sha256": "7" * 64,
            "approval_id": "test-approval",
            "approved_by": "test-release-owner",
            "approved_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "execution_nonce": "test-only-nonce",
        }
        approval_path = tmp_path / "approval.json"
        approval_path.write_text(json.dumps(approval), encoding="utf-8")
        return plan_path, identity_path, parity_path, approval_path, plan

    reset_database()
    plan_path, identity_path, parity_path, approval_path, plan = write_documents()
    dry_run = retirement.run_retirement(
        retirement_plan_path=plan_path,
        identity_reconciliation_path=identity_path,
        parity_report_path=parity_path,
        expected_database=database_name,
        connect=connect,
    )
    assert dry_run["result"] == "pass"
    assert dry_run["execution_committed"] is False
    with connect() as conn:
        assert conn.execute("select count(*) from pdf2md_hk.filings where filing_id='HK:00001:legacy-task'").fetchone()[0] == 1

    def fail_after_delete(_conn, _operation):
        raise RuntimeError("injected failure")

    rolled_back = retirement.run_retirement(
        retirement_plan_path=plan_path,
        identity_reconciliation_path=identity_path,
        parity_report_path=parity_path,
        expected_database=database_name,
        execute=True,
        approval_path=approval_path,
        confirm_operations_sha256=plan["summary"]["operations_sha256"],
        connect=connect,
        after_delete_hook=fail_after_delete,
    )
    assert rolled_back["result"] == "fail"
    assert rolled_back["execution_committed"] is False
    assert rolled_back["operations"][0]["status"] == "rolled_back"
    with connect() as conn:
        assert conn.execute("select count(*) from pdf2md_hk.filings where filing_id='HK:00001:legacy-task'").fetchone()[0] == 1

    executed = retirement.run_retirement(
        retirement_plan_path=plan_path,
        identity_reconciliation_path=identity_path,
        parity_report_path=parity_path,
        expected_database=database_name,
        execute=True,
        approval_path=approval_path,
        confirm_operations_sha256=plan["summary"]["operations_sha256"],
        connect=connect,
    )
    assert executed["result"] == "pass"
    assert executed["execution_committed"] is True
    with connect() as conn:
        assert conn.execute("select count(*) from pdf2md_hk.filings where filing_id='HK:00001:legacy-task'").fetchone()[0] == 0
        assert conn.execute("select count(*) from pdf2md_hk.filings where filing_id='HK:00001:canonical'").fetchone()[0] == 1
