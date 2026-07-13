import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "audit_hk_staging_postgres_parity.py"
SPEC = importlib.util.spec_from_file_location("audit_hk_staging_postgres_parity", SCRIPT)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gate
SPEC.loader.exec_module(gate)


def _write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _package(tmp_path: Path) -> Path:
    package = tmp_path / "companies" / "00700-TENCENT" / "reports" / "2025-annual"
    manifest = {
        "market": "HK",
        "company_id": "HK:00700",
        "company_name": "Tencent",
        "ticker": "00700",
        "filing_id": "HK:00700:12100024",
        "parse_run_id": "HK:00700:12100024:abc123",
        "accession_number": "12100024",
        "report_type": "annual",
        "period_end": "2025-12-31",
        "fiscal_year": 2025,
        "fiscal_period": "FY",
        "source_url": "https://www1.hkexnews.hk/report.pdf",
    }
    financial_data = {
        "statements": [
            {
                "statement_id": "income_statement",
                "statement_type": "income_statement",
                "statement_name": "Income statement",
                "items": [
                    {
                        "canonical_name": "revenue",
                        "local_name": "Revenue",
                        "value": "1000",
                        "raw_value": "1,000",
                        "unit": "CNY million",
                        "currency": "CNY",
                        "scale": "1000000",
                        "period_key": "2025-12-31",
                        "period_end": "2025-12-31",
                        "evidence": {
                            "table_index": 3,
                            "row_index": 5,
                            "column_index": 2,
                            "quote_text": "Revenue | 1,000",
                        },
                    }
                ],
            }
        ]
    }
    _write_json(package / "manifest.json", manifest)
    _write_json(package / "metrics" / "financial_data.json", financial_data)
    _write_json(package / "qa" / "source_map.json", {"entries": []})
    return package


def _exact_observed(package: Path) -> dict:
    expected = gate.load_package_expectation(package)
    return dict(expected["rows"][0])


def test_package_expectation_reuses_importer_shape_and_embedded_evidence(tmp_path):
    package = _package(tmp_path)

    result = gate.load_package_expectation(package)

    assert result["errors"] == []
    assert result["expected_row_count"] == 1
    row = result["rows"][0]
    assert row["company_ticker"] == "00700"
    assert row["filing_id"] == "HK:00700:12100024"
    assert row["raw_value"] == "1,000"
    assert row["evidence_id"] is None
    assert row["evidence_table_index"] == 3
    assert row["quote_text"] is None
    assert len(result["expected_rows_sha256"]) == 64


def test_package_expectation_matches_agent_view_evidence_coalesce_order(tmp_path):
    package = _package(tmp_path)
    financial_path = package / "metrics" / "financial_data.json"
    financial_data = json.loads(financial_path.read_text(encoding="utf-8"))
    item = financial_data["statements"][0]["items"][0]
    item["evidence_id"] = "evidence-1"
    item["evidence"]["table_index"] = 99
    _write_json(financial_path, financial_data)
    _write_json(
        package / "qa" / "source_map.json",
        {
            "entries": [
                {
                    "evidence_id": "evidence-1",
                    "page_number": 7,
                    "table_index": 3,
                    "quote_text": "stored citation",
                }
            ]
        },
    )

    row = gate.load_package_expectation(package)["rows"][0]

    assert row["evidence_id"] == "evidence-1"
    assert row["evidence_page_number"] == 7
    assert row["evidence_table_index"] == 3
    assert row["evidence_bbox"] == []
    assert row["quote_text"] == "stored citation"


def test_full_package_agent_view_parity_passes_only_for_exact_row_set(tmp_path):
    package = _package(tmp_path)
    observed = _exact_observed(package)

    report = gate.audit_staging_parity(tmp_path, [observed], database={"database_name": "siq_hk_staging"})

    assert report["passed"] is True
    assert report["read_only"] is True
    assert report["summary"]["package_count"] == 1
    assert report["summary"]["expected_row_count"] == 1
    assert report["summary"]["diff_counts"] == {}
    assert report["summary"]["canonical_package_parity_passed"] is True
    assert report["global_agent_scope"]["passed"] is True
    assert report["packages"][0]["expected_rows_sha256"] == report["packages"][0]["observed_rows_sha256"]


def test_parity_classifies_value_raw_unit_currency_period_and_evidence_diffs(tmp_path):
    package = _package(tmp_path)
    observed = _exact_observed(package)
    observed.update(
        {
            "value": "999",
            "raw_value": "999",
            "unit": "HKD million",
            "currency": "HKD",
            "period_end": "2024-12-31",
            "evidence_table_index": None,
        }
    )

    report = gate.audit_staging_parity(tmp_path, [observed])

    counts = report["summary"]["diff_counts"]
    assert report["passed"] is False
    assert counts["value_mismatch"] == 1
    assert counts["raw_value_diff"] == 1
    assert counts["unit_display_diff"] == 1
    assert counts["currency_label_diff"] == 1
    assert counts["period_diff"] == 1
    assert counts["evidence_diff"] == 1
    assert report["summary"]["currency_label_diff"] == 1


def test_parity_fails_closed_for_missing_extra_and_duplicate_agent_rows(tmp_path):
    package = _package(tmp_path)
    expected = gate.load_package_expectation(package)
    observed = _exact_observed(package)
    duplicate = dict(observed)
    extra = dict(observed, item_uid="extra-item")

    duplicate_report = gate.check_package_parity(expected, [observed, duplicate, extra])
    missing_report = gate.check_package_parity(expected, [])

    assert duplicate_report["passed"] is False
    assert duplicate_report["diff_counts"] == {
        "duplicate_agent_item_uid": 1,
        "extra_agent_row": 1,
    }
    assert missing_report["diff_counts"] == {"missing_agent_row": 1}


def test_global_scope_detects_legacy_filing_without_corrupting_expected_parity(tmp_path):
    package = _package(tmp_path)
    expected = _exact_observed(package)
    legacy = {
        **expected,
        "item_uid": "legacy-item",
        "filing_id": "HK:00700:2025-annual",
        "parse_run_id": "parse-legacy-00700",
        "filing_period_end": "2025-12-31",
        "report_type": "annual_report",
    }

    report = gate.audit_staging_parity(tmp_path, [expected, legacy])

    assert report["passed"] is False
    assert report["summary"]["canonical_package_parity_passed"] is True
    assert report["packages"][0]["passed"] is True
    assert report["summary"]["diff_counts"] == {"extra_agent_company_period_filing": 1}
    scope = report["global_agent_scope"]
    assert scope["passed"] is False
    assert scope["extra_filing_count"] == 1
    assert scope["collisions"] == [
        {
            "company_id": "HK:00700",
            "period_end": "2025-12-31",
            "report_family": "annual",
            "expected_filing_ids": ["HK:00700:12100024"],
            "observed_filing_ids": ["HK:00700:12100024", "HK:00700:2025-annual"],
            "extra_filing_ids": ["HK:00700:2025-annual"],
            "observed_parse_run_ids": ["HK:00700:12100024:abc123", "parse-legacy-00700"],
            "observed_row_count": 2,
        }
    ]


def test_global_scope_ignores_same_company_rows_from_other_periods(tmp_path):
    package = _package(tmp_path)
    expected = _exact_observed(package)
    historical = {
        **expected,
        "item_uid": "historical-item",
        "filing_id": "HK:00700:historical",
        "parse_run_id": "parse-historical",
        "filing_period_end": "2024-12-31",
        "report_type": "annual_report",
    }

    report = gate.audit_staging_parity(tmp_path, [expected, historical])

    assert report["passed"] is True
    assert report["global_agent_scope"]["collisions"] == []


def _reconciliation_report():
    return {
        "schema_version": "hk_identity_reconciliation_v1",
        "candidates": [
            {
                "company_id": "HK:00700",
                "ticker": "00700",
                "filing_id": "HK:00700:12100024",
                "parse_run_id": "HK:00700:12100024:abc123",
                "accession_number": "12100024",
                "period_end": "2025-12-31",
                "report_family": "annual",
                "status": "legacy_period_collision",
                "migration_eligible": True,
                "migration_assessment": {
                    "evidence": {
                        "legacy_filing_id": "HK:00700:legacy-task",
                        "legacy_parse_run_ids": ["parse-legacy"],
                        "legacy_filing_task_id_match": True,
                        "legacy_accession_missing": True,
                        "package_task_id_match": True,
                        "document_full_sha256_match": True,
                    }
                },
            }
        ],
    }


def test_retirement_plan_binds_exact_legacy_and_canonical_identities(tmp_path):
    package = _package(tmp_path)
    parity = gate.audit_staging_parity(
        tmp_path,
        [_exact_observed(package)],
        database={"database_name": "siq_hk_staging", "transaction_read_only": "on"},
    )

    plan = gate.build_legacy_retirement_plan(parity, _reconciliation_report())

    assert plan["ready_for_controlled_staging_retirement"] is True
    assert plan["execution_authorized"] is False
    assert plan["summary"]["operation_count"] == 1
    assert len(plan["summary"]["operations_sha256"]) == 64
    operation = plan["operations"][0]
    assert operation["legacy_filing_id"] == "HK:00700:legacy-task"
    assert operation["legacy_parse_run_id"] == "parse-legacy"
    assert operation["canonical_filing_id"] == "HK:00700:12100024"
    assert operation["canonical_expected_agent_row_count"] == 1


def test_retirement_plan_fails_closed_when_parity_or_legacy_identity_is_not_exact(tmp_path):
    _package(tmp_path)
    failed_parity = gate.audit_staging_parity(tmp_path, [])
    reconciliation = _reconciliation_report()
    reconciliation["candidates"][0]["migration_assessment"]["evidence"]["legacy_parse_run_ids"] = [
        "parse-a",
        "parse-b",
    ]

    plan = gate.build_legacy_retirement_plan(failed_parity, reconciliation)

    assert plan["ready_for_controlled_staging_retirement"] is False
    assert "staging_parity_not_passed" in plan["blocking_reasons"]
    assert "legacy_identity_not_exact:HK:00700:12100024" in plan["blocking_reasons"]
    assert plan["operations"] == []


def test_database_env_requires_expected_database(tmp_path):
    _package(tmp_path)

    try:
        gate.main(["--staging-wiki-root", str(tmp_path), "--database-env"])
    except SystemExit as exc:
        assert "--expected-database is required" in str(exc)
    else:
        raise AssertionError("expected database identity guard")
