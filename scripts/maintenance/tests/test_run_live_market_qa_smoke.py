from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

SOURCE = Path(__file__).resolve().parents[1] / "run_live_market_qa_smoke.py"
SPEC = importlib.util.spec_from_file_location("run_live_market_qa_smoke_under_test", SOURCE)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def row(statement_type: str, *, located: bool = True) -> dict:
    return {
        "statement_type": statement_type,
        "task_id": "task-1" if located else None,
        "pdf_page": 1 if located else None,
    }


def result(rows: list[dict], *, identity: bool = True) -> dict:
    return {
        "market": "HK",
        "company_id": "HK:00001" if identity else None,
        "report_id": "2025-annual",
        "filing_id": "HK:00001:2025-annual" if identity else None,
        "parse_run_id": "run-1" if identity else None,
        "validation": {"status": "pass"},
        "rows": rows,
    }


PACKAGE_ROWS = [
    row("balance_sheet"),
    row("cash_flow_statement"),
    row("income_statement"),
]
CASE = {"metric_question": "Company revenue", "package_question": "Company 财务表现"}


def test_metric_filter_does_not_fail_complete_three_statement_package():
    runtime = SimpleNamespace(
        _three_statement_core_result=lambda question, context=None: (
            result([row("income_statement")]) if question == CASE["metric_question"] else result(PACKAGE_ROWS)
        ),
        _wiki_fulltext_fallback_result=lambda question, context=None: None,
    )

    observed = MODULE.evaluate_case(runtime, "HK", CASE)

    assert observed["passed"] is True
    assert observed["metric_evidence_pass"] is True
    assert observed["metric_evidence_source"] == "structured"
    assert observed["metric_statement_types"] == ["income_statement"]
    assert observed["three_statement_package_pass"] is True
    assert set(observed["package_statement_types"]) == MODULE.EXPECTED_STATEMENTS


def test_located_fulltext_fallback_can_satisfy_metric_evidence_only():
    fallback = {
        "report_id": "2025-annual",
        "rows": [{"task_id": "task-1", "md_line": 10, "source_type": "wiki_report_fulltext"}],
    }
    runtime = SimpleNamespace(
        _three_statement_core_result=lambda question, context=None: (
            None if question == CASE["metric_question"] else result(PACKAGE_ROWS)
        ),
        _wiki_fulltext_fallback_result=lambda question, context=None: fallback,
    )

    observed = MODULE.evaluate_case(runtime, "HK", CASE)

    assert observed["passed"] is True
    assert observed["metric_evidence_pass"] is True
    assert observed["metric_evidence_source"] == "fulltext"
    assert observed["three_statement_package_pass"] is True


def test_fulltext_fallback_does_not_hide_incomplete_package():
    fallback = {
        "report_id": "2025-annual",
        "rows": [{"task_id": "task-1", "md_line": 10}],
    }
    runtime = SimpleNamespace(
        _three_statement_core_result=lambda question, context=None: (
            None
            if question == CASE["metric_question"]
            else result([row("balance_sheet"), row("income_statement")])
        ),
        _wiki_fulltext_fallback_result=lambda question, context=None: fallback,
    )

    observed = MODULE.evaluate_case(runtime, "HK", CASE)

    assert observed["metric_evidence_pass"] is True
    assert observed["three_statement_package_pass"] is False
    assert observed["passed"] is False
    assert "incomplete_three_statement_coverage" in observed["errors"]


def test_unlocated_fulltext_fallback_is_not_evidence_pass():
    fallback = {"report_id": "2025-annual", "rows": [{"snippet": "revenue"}]}
    runtime = SimpleNamespace(
        _three_statement_core_result=lambda question, context=None: (
            None if question == CASE["metric_question"] else result(PACKAGE_ROWS)
        ),
        _wiki_fulltext_fallback_result=lambda question, context=None: fallback,
    )

    observed = MODULE.evaluate_case(runtime, "HK", CASE)

    assert observed["metric_evidence_pass"] is False
    assert observed["passed"] is False
    assert "incomplete_fulltext_evidence" in observed["errors"]


def test_non_cn_package_requires_complete_research_identity():
    runtime = SimpleNamespace(
        _three_statement_core_result=lambda question, context=None: (
            result([row("income_statement")])
            if question == CASE["metric_question"]
            else result(PACKAGE_ROWS, identity=False)
        ),
        _wiki_fulltext_fallback_result=lambda question, context=None: None,
    )

    observed = MODULE.evaluate_case(runtime, "HK", CASE)

    assert observed["metric_evidence_pass"] is True
    assert observed["three_statement_package_pass"] is False
    assert observed["passed"] is False
    assert "incomplete_package_company_report_identity" in observed["errors"]
    assert "incomplete_package_research_identity" in observed["errors"]
