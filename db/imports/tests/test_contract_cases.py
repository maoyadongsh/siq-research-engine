import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    backtest_dir = Path(__file__).resolve().parents[1] / "backtests"
    if str(backtest_dir) not in sys.path:
        sys.path.insert(0, str(backtest_dir))
    source = backtest_dir / "contract_cases.py"
    spec = importlib.util.spec_from_file_location("contract_cases_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_case_files(tmp_path):
    document_full = {
        "identity_scope": "synthetic_fixture",
        "task": {
            "task_id": "fixture-hk-contract-test",
            "filename": "SYNTHETIC_HK_CONTRACT_TEST.pdf",
        },
        "financial_data": {
            "market": "HK",
            "company_id": "HK:FIXTURE:CONTRACT_TEST",
            "ticker": "FIXTURE_CONTRACT_TEST",
            "report_id": "HK:FIXTURE:CONTRACT_TEST:2025-annual",
            "report_year": 2025,
            "reporting_currency": "HKD",
            "statements": [
                {
                    "statement_type": "income_statement",
                    "unit": "million",
                    "currency": "HKD",
                    "scale": "1e6",
                    "items": [
                        {
                            "canonical_name": "revenue",
                            "name": "Revenue",
                            "values": {"FY2025": "100.0"},
                            "raw_values": {"FY2025": "100"},
                            "sources": {"FY2025": {"table_index": 3, "quote_text": "Revenue was 100"}},
                        }
                    ],
                }
            ],
        },
        "content_list_enhanced": {
            "tables": [
                {
                    "table_index": 3,
                    "page_number": 9,
                    "bbox": [1, 2, 3, 4],
                }
            ]
        },
    }
    (tmp_path / "document_full.json").write_text(json.dumps(document_full), encoding="utf-8")
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps({"cases": []}), encoding="utf-8")
    case = {
        "case_id": "hk-revenue",
        "identity_scope": "synthetic_fixture",
        "market": "HK",
        "company_id": "HK:FIXTURE:CONTRACT_TEST",
        "period_key": "FY2025",
        "document_full_path": "document_full.json",
        "expected_identity": {
            "filing_id": "HK:FIXTURE:CONTRACT_TEST:2025-annual",
        },
        "assertions": [
            {
                "statement_type": "income_statement",
                "canonical_name": "revenue",
                "expected_value": "100",
                "raw_value": "100",
                "unit": "million",
                "currency": "HKD",
                "fact_currency": "HKD",
                "required_evidence": True,
                "evidence": {"page_number": 9, "table_index": 3},
            }
        ],
    }
    return cases_path, case


def test_check_case_validates_identity_value_unit_currency_and_evidence(tmp_path):
    module = _load_module()
    cases_path, case = _write_case_files(tmp_path)

    result = module.check_case(case, cases_path, read_json=lambda path: json.loads(path.read_text(encoding="utf-8")))

    assert result["passed"] is True
    assert result["fact_count"] == 1
    assert result["required_evidence_checked_count"] == 1
    assert result["required_evidence_passed_count"] == 1
    assert result["unit_currency_checked_count"] == 1
    assert result["unit_currency_passed_count"] == 1
    assert result["content_hash"]


def test_check_agent_case_falls_back_to_assertions(tmp_path):
    module = _load_module()
    cases_path, case = _write_case_files(tmp_path)

    result = module.check_agent_case(case, cases_path, read_json=lambda path: json.loads(path.read_text(encoding="utf-8")))

    assert result == {
        "case_id": "hk-revenue",
        "market": "HK",
        "passed": True,
        "checked": 1,
        "errors": [],
        "mode": "fixture_fact_lookup",
    }


def test_check_case_rejects_real_company_identity_in_eval_fixture(tmp_path):
    module = _load_module()
    cases_path, case = _write_case_files(tmp_path)
    document_path = tmp_path / "document_full.json"
    document_full = json.loads(document_path.read_text(encoding="utf-8"))
    document_full["financial_data"].update(
        {
            "company_id": "HK:00700",
            "ticker": "00700",
            "report_id": "HK:00700:2025-annual",
        }
    )
    document_path.write_text(json.dumps(document_full), encoding="utf-8")
    case["company_id"] = "HK:00700"
    case["expected_identity"]["filing_id"] = "HK:00700:2025-annual"

    result = module.check_case(
        case,
        cases_path,
        read_json=lambda path: json.loads(path.read_text(encoding="utf-8")),
    )

    assert result["passed"] is False
    assert any("synthetic fixture namespace" in error for error in result["errors"])
    assert any("ticker must start with 'FIXTURE_'" in error for error in result["errors"])


def test_contract_assertion_stats_counts_explainability_fields():
    module = _load_module()

    stats = module.contract_assertion_stats(
        [
            {
                "assertions": [
                    {
                        "canonical_name": "revenue",
                        "required_evidence": True,
                        "unit": "million",
                        "currency": "HKD",
                        "fact_currency": "HKD",
                    },
                    {"canonical_name": "custom_metric"},
                ]
            }
        ]
    )

    assert stats == {
        "assertion_count": 2,
        "common_core_assertion_count": 1,
        "required_evidence_assertion_count": 1,
        "unit_checked_assertion_count": 1,
        "currency_checked_assertion_count": 1,
        "fact_currency_checked_assertion_count": 1,
    }
