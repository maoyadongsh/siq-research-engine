from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "audit_hk_identity_reconciliation.py"
SPEC = importlib.util.spec_from_file_location("audit_hk_identity_reconciliation", SCRIPT)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gate
SPEC.loader.exec_module(gate)


SHA = "a" * 64


def candidate(**overrides):
    payload = {
        "company_id": "HK:00700",
        "ticker": "00700",
        "filing_id": "HK:00700:12100024",
        "parse_run_id": f"HK:00700:12100024:{SHA[:16]}",
        "accession_number": "12100024",
        "period_end": "2025-12-31",
        "report_type": "annual_report",
        "report_family": "annual",
        "source_url": "https://www1.hkexnews.hk/filing.pdf",
        "source_sha256": SHA,
        "package_path": "staging/00700/2025-annual",
        "parser_task_id": "task-00700",
        "document_full_sha256": "c" * 64,
    }
    payload.update(overrides)
    return payload


def test_reconciliation_allows_new_canonical_filing():
    report = gate.reconcile_identities([candidate()], {"filings": [], "parse_runs": []})

    assert report["passed"] is True
    assert report["candidates"][0]["status"] == "safe_new_filing"


def test_reconciliation_blocks_legacy_same_company_period_filing():
    inventory = {
        "filings": [
            {
                "filing_id": "legacy-hk-00700-2025",
                "company_id": "HK:00700",
                "ticker": "00700",
                "accession_number": None,
                "period_end": "2025-12-31",
                "report_type": "annual_report",
            }
        ],
        "parse_runs": [],
    }

    report = gate.reconcile_identities([candidate()], inventory)

    assert report["passed"] is False
    assert report["candidates"][0]["status"] == "legacy_period_collision"
    assert report["summary"]["blocking_count"] == 1
    assert report["candidates"][0]["migration_eligible"] is False


def test_legacy_collision_is_migration_eligible_only_with_matching_source_chain():
    task_id = "50090c9f-a424-4d73-b28c-96fa60dd99ff"
    document_hash = "c" * 64
    filing_id = f"HK:00700:{task_id}"
    inventory = {
        "filings": [
            {
                "filing_id": filing_id,
                "company_id": "HK:00700",
                "ticker": "00700",
                "accession_number": None,
                "period_end": "2025-12-31",
                "report_type": "annual_report",
            }
        ],
        "parse_runs": [
            {
                "parse_run_id": "parse-legacy",
                "filing_id": filing_id,
                "wiki_package_path": f"/external/results/{task_id}/document_full.json",
                "artifact_hashes": {"document_full.json": document_hash},
            }
        ],
    }

    report = gate.reconcile_identities(
        [candidate(parser_task_id=task_id, document_full_sha256=document_hash)],
        inventory,
    )

    result = report["candidates"][0]
    assert result["status"] == "legacy_period_collision"
    assert result["migration_eligible"] is True
    assert result["migration_assessment"]["migration_state"] == "assessment_only_not_migrated"
    assert result["migration_assessment"]["blocking_reasons"] == []
    assert report["passed"] is False


def test_legacy_collision_hash_mismatch_remains_ineligible():
    task_id = "50090c9f-a424-4d73-b28c-96fa60dd99ff"
    filing_id = f"HK:00700:{task_id}"
    inventory = {
        "filings": [
            {
                "filing_id": filing_id,
                "company_id": "HK:00700",
                "ticker": "00700",
                "accession_number": None,
                "period_end": "2025-12-31",
                "report_type": "annual_report",
            }
        ],
        "parse_runs": [
            {
                "parse_run_id": "parse-legacy",
                "filing_id": filing_id,
                "wiki_package_path": f"/external/results/{task_id}/document_full.json",
                "artifact_hashes": {"document_full.json": "d" * 64},
            }
        ],
    }

    result = gate.reconcile_identities(
        [candidate(parser_task_id=task_id, document_full_sha256="c" * 64)],
        inventory,
    )["candidates"][0]

    assert result["migration_eligible"] is False
    assert "document_full_sha256_mismatch" in result["migration_assessment"]["blocking_reasons"]


def test_synthetic_eval_fixture_collision_is_proven_but_never_auto_migrated(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    fixture_path = tmp_path / "eval_datasets" / "market_document_full_postgres" / "examples" / "hk.json"
    fixture_path.parent.mkdir(parents=True)
    fixture_path.write_text(
        json.dumps(
            {
                "identity_scope": "synthetic_fixture",
                "task": {
                    "task_id": "fixture-hk-row-period-v2",
                    "filename": "SYNTHETIC_HK_ROW_PERIOD_2025-12-31_annual.pdf",
                },
                "financial_data": {
                    "company_id": "HK:FIXTURE:ROW_PERIOD",
                    "period_end": "2025-12-31",
                },
            }
        ),
        encoding="utf-8",
    )
    fixture_hash = gate.sha256_file(fixture_path)
    legacy_filing_id = "HK:00700:2025-annual"
    inventory = {
        "filings": [
            {
                "filing_id": legacy_filing_id,
                "company_id": "HK:00700",
                "ticker": "00700",
                "accession_number": None,
                "period_end": "2025-12-31",
                "report_type": "annual",
            }
        ],
        "parse_runs": [
            {
                "parse_run_id": "parse-fixture",
                "filing_id": legacy_filing_id,
                "wiki_package_path": str(fixture_path),
                "artifact_hashes": {"document_full.json": "d" * 64},
            }
        ],
    }

    report = gate.reconcile_identities([candidate()], inventory)
    result = report["candidates"][0]

    assessment = result["migration_assessment"]
    assert result["migration_eligible"] is False
    assert assessment["evidence"]["legacy_source_kind"] == "synthetic_eval_fixture"
    assert assessment["evidence"]["package_task_ids"] == ["fixture-hk-row-period-v2"]
    assert assessment["evidence"]["package_file_evidence"] == [
        {
            "path": "eval_datasets/market_document_full_postgres/examples/hk.json",
            "task_id": "fixture-hk-row-period-v2",
            "repo_file_verified": True,
            "file_sha256": fixture_hash,
            "database_hash_match": False,
            "synthetic_eval_fixture": True,
            "repo_benchmark_identity_migrated": True,
        }
    ]
    assert assessment["evidence"]["repo_benchmark_identity_migrated"] is True
    assert assessment["blocking_reasons"] == [
        "document_full_sha256_mismatch",
        "legacy_source_is_synthetic_eval_fixture",
    ]
    assert assessment["migration_state"] == "repo_benchmark_identity_migrated_database_legacy_row_pending"
    assert assessment["recommended_action"] == "exact_legacy_fixture_audit_then_controlled_database_retirement"
    assert report["summary"]["synthetic_eval_fixture_collision_count"] == 1
    assert report["summary"]["repo_benchmark_identity_migrated_collision_count"] == 1


def test_reconciliation_allows_new_parse_run_for_exact_filing():
    inventory = {
        "filings": [
            {
                "filing_id": "HK:00700:12100024",
                "company_id": "HK:00700",
                "ticker": "00700",
                "accession_number": "12100024",
                "period_end": "2025-12-31",
                "report_type": "annual_report",
            }
        ],
        "parse_runs": [],
    }

    report = gate.reconcile_identities([candidate()], inventory)

    assert report["passed"] is True
    assert report["candidates"][0]["status"] == "safe_new_parse_run"


def test_reconciliation_allows_accession_backfill_when_canonical_filing_already_matches():
    inventory = {
        "filings": [
            {
                "filing_id": "HK:00700:12100024",
                "company_id": "HK:00700",
                "ticker": "00700",
                "accession_number": None,
                "period_end": "2025-12-31",
                "report_type": "annual_report",
            }
        ],
        "parse_runs": [],
    }

    report = gate.reconcile_identities([candidate()], inventory)

    assert report["passed"] is True
    assert report["candidates"][0]["status"] == "safe_metadata_backfill"


def test_reconciliation_blocks_legacy_period_filing_even_when_canonical_filing_exists():
    legacy_filing_id = "HK:00700:legacy-task"
    inventory = {
        "filings": [
            {
                "filing_id": "HK:00700:12100024",
                "company_id": "HK:00700",
                "ticker": "00700",
                "accession_number": "12100024",
                "period_end": "2025-12-31",
                "report_type": "annual_report",
            },
            {
                "filing_id": legacy_filing_id,
                "company_id": "HK:00700",
                "ticker": "00700",
                "accession_number": None,
                "period_end": "2025-12-31",
                "report_type": "annual_report",
            },
        ],
        "parse_runs": [
            {
                "parse_run_id": "legacy-run",
                "filing_id": legacy_filing_id,
                "wiki_package_path": "/missing/legacy-task/document_full.json",
                "artifact_hashes": {},
            }
        ],
    }

    report = gate.reconcile_identities([candidate()], inventory)

    result = report["candidates"][0]
    assert report["passed"] is False
    assert result["status"] == "legacy_period_collision"
    assert result["migration_eligible"] is False
    assert result["conflicts"] == [
        {
            "kind": "same_company_period_different_filing",
            "existing_filing_ids": [legacy_filing_id],
            "existing_accessions": [],
        }
    ]
    assert "document_full_sha256_mismatch" in result["migration_assessment"]["blocking_reasons"]


def test_reconciliation_blocks_parse_run_bound_to_other_filing():
    inventory = {
        "filings": [],
        "parse_runs": [{"parse_run_id": candidate()["parse_run_id"], "filing_id": "HK:00005:other"}],
    }

    report = gate.reconcile_identities([candidate()], inventory)

    assert report["passed"] is False
    assert report["candidates"][0]["status"] == "identity_conflict"


def test_reconciliation_rejects_noncanonical_or_untraceable_candidate():
    bad = candidate(filing_id="legacy", source_url="", source_sha256="short")

    report = gate.reconcile_identities([bad], {"filings": [], "parse_runs": []})

    assert report["passed"] is False
    assert report["candidates"][0]["status"] == "invalid_candidate"
    assert "noncanonical_filing_id" in report["candidates"][0]["errors"]
    assert "missing_source_url" in report["candidates"][0]["errors"]


def test_reconciliation_blocks_duplicates_inside_staging_inventory():
    duplicate = candidate(package_path="staging/copy")

    report = gate.reconcile_identities([candidate(), duplicate], {"filings": [], "parse_runs": []})

    assert report["passed"] is False
    assert report["summary"]["blocking_count"] == 2
    assert all("duplicate_candidate_parse_run_id" in row["errors"] for row in report["candidates"])


def test_reconciliation_blocks_two_accessions_for_same_staging_period():
    other_sha = "b" * 64
    other = candidate(
        filing_id="HK:00700:12100025",
        accession_number="12100025",
        parse_run_id=f"HK:00700:12100025:{other_sha[:16]}",
        source_sha256=other_sha,
    )

    report = gate.reconcile_identities([candidate(), other], {"filings": [], "parse_runs": []})

    assert report["passed"] is False
    assert all("candidate_period_collision" in row["errors"] for row in report["candidates"])


def test_staging_manifest_preserves_canonical_sidecar_identity(tmp_path: Path):
    report_dir = tmp_path / "companies" / "00700-TENCENT" / "reports" / "2025-annual"
    report_dir.mkdir(parents=True)
    (report_dir / "manifest.json").write_text(
        json.dumps(
            {
                "market": "HK",
                "company_id": "HK:00700",
                "ticker": "00700",
                "filing_id": "HK:00700:12100024",
                "parse_run_id": f"HK:00700:12100024:{SHA[:16]}",
                "accession_number": "12100024",
                "period_end": "2025-12-31",
                "report_type": "annual_report",
                "source_url": "https://www1.hkexnews.hk/filing.pdf",
                "source_manifest": {"content_sha256": SHA},
            }
        ),
        encoding="utf-8",
    )

    records = gate.candidates_from_staging(tmp_path)

    expected = candidate(
        package_path="<external>",
        parser_task_id="",
        document_full_sha256="",
    )
    assert records == [expected]


def test_portable_report_paths_are_relative_or_redacted(tmp_path: Path):
    inside = gate.REPO_ROOT / "data" / "pdf-parser" / "results" / "task-1"
    payload = {"inside": str(inside), "outside": str(tmp_path), "source_url": "https://example.com/report.pdf"}

    observed = gate._portable_report_value(payload)

    assert observed == {
        "inside": "data/pdf-parser/results/task-1",
        "outside": "<external>",
        "source_url": "https://example.com/report.pdf",
    }


def test_cli_rejects_database_url_argv_credentials():
    parser = gate.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--results-dir", "results", "--database-url", "postgresql://user:secret@db/siq_hk"])


def test_database_env_requires_expected_database(tmp_path: Path):
    with pytest.raises(SystemExit, match="--expected-database is required"):
        gate.main(["--staging-wiki-root", str(tmp_path), "--database-env"])


class _Cursor:
    def __init__(self, *, one=None, many=None):
        self._one = one
        self._many = many or []

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._many


class _Connection:
    def __init__(self, database_name: str):
        self.database_name = database_name
        self.queries = []
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, query):
        self.queries.append(query)
        if "current_database()" in query:
            return _Cursor(one={"database_name": self.database_name, "transaction_read_only": "on"})
        return _Cursor(many=[])

    def rollback(self):
        self.rolled_back = True


def _install_fake_psycopg(monkeypatch, connection: _Connection):
    psycopg = types.ModuleType("psycopg")
    psycopg.connect = lambda *_args, **_kwargs: connection
    rows = types.ModuleType("psycopg.rows")
    rows.dict_row = object()
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.rows", rows)


def test_postgres_inventory_asserts_exact_database_before_reading_inventory(monkeypatch):
    connection = _Connection("siq_hk")
    _install_fake_psycopg(monkeypatch, connection)

    with pytest.raises(SystemExit, match="does not match --expected-database"):
        gate.database_inventory_from_postgres("siq_hk_staging")

    assert connection.queries[0] == "set transaction read only"
    assert len(connection.queries) == 2


def test_postgres_inventory_runs_in_read_only_transaction(monkeypatch):
    connection = _Connection("siq_hk_staging")
    _install_fake_psycopg(monkeypatch, connection)

    inventory = gate.database_inventory_from_postgres("siq_hk_staging")

    assert inventory == {"filings": [], "parse_runs": []}
    assert connection.queries[0] == "set transaction read only"
    assert connection.rolled_back is True
