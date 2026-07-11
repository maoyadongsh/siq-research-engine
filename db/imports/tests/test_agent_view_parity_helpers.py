import importlib.util
import sys
from pathlib import Path


def _load_module():
    backtest_dir = Path(__file__).resolve().parents[1] / "backtests"
    if str(backtest_dir) not in sys.path:
        sys.path.insert(0, str(backtest_dir))
    source = backtest_dir / "agent_view_parity_helpers.py"
    spec = importlib.util.spec_from_file_location("agent_view_parity_helpers_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_agent_view_row_diffs_classify_value_unit_currency_and_evidence():
    module = _load_module()

    assert [diff["code"] for diff in module.agent_view_row_diffs({"raw_value": "100"}, {"value": "100"})] == []
    assert module.agent_view_row_diffs({"unit": "HKD million"}, {"unit": "million"})[0]["code"] == "unit_display_diff"
    assert module.agent_view_row_diffs({"currency": "HKD"}, {"currency": "CNY"})[0]["code"] == "currency_label_diff"
    assert module.agent_view_row_diffs({"value": "101"}, {"value": "100"})[0]["code"] == "value_mismatch"
    assert module.agent_view_row_diffs({"value": "101"}, {"value": "100", "tolerance_ratio": "0.02"}) == []
    assert module.agent_view_row_diffs({"value": "100"}, {"value": "100", "required_evidence": True})[0]["code"] == "evidence_missing"
    assert module.agent_view_row_diffs(
        {"value": "100", "evidence_page_number": 9},
        {"value": "100", "required_evidence": True},
    ) == []


def test_diff_code_counts_can_separate_error_and_warning_sources():
    module = _load_module()
    results = [
        {
            "passed": False,
            "questions": [
                {"passed": False, "diff_codes": ["value_mismatch", "currency_label_diff"]},
                {"passed": True, "diff_codes": ["unit_display_diff"]},
            ],
        },
        {
            "passed": True,
            "questions": [
                {"passed": True, "diff_codes": ["period_alias_diff"]},
            ],
        },
    ]

    assert module.diff_code_counts(results) == {
        "currency_label_diff": 1,
        "period_alias_diff": 1,
        "unit_display_diff": 1,
        "value_mismatch": 1,
    }
    assert module.diff_code_counts(results, source="errors") == {
        "currency_label_diff": 1,
        "value_mismatch": 1,
    }
    assert module.diff_code_counts(results, source="warnings") == {"period_alias_diff": 1}


def test_generated_wiki_postgres_questions_prefer_core_reviewable_facts():
    module = _load_module()
    facts = [
        module.NormalizedFact(
            statement_type="income_statement",
            period_key="FY2025",
            canonical_name="misc_metric",
            value="1",
            raw_value="1",
        ),
        module.NormalizedFact(
            statement_type="income_statement",
            period_key="FY2025",
            canonical_name="revenue",
            value="100",
            raw_value="100",
            evidence={"page_number": 5},
        ),
        module.NormalizedFact(
            statement_type="income_statement",
            period_key="FY2025",
            canonical_name="revenue",
            value="100",
            raw_value="100",
            evidence={"page_number": 6},
        ),
    ]

    questions = module.generated_wiki_postgres_questions(facts, {"case_id": "case-hk"}, limit=5)

    assert [question["expected_fact"].get("canonical_name") for question in questions] == ["revenue", "misc_metric"]
    assert questions[0]["wiki_fact"] is facts[1]
    assert questions[0]["question_id"] == "case-hk:wiki_pg_parity:1"
