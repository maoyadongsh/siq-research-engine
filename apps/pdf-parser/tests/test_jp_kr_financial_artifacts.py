from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

PARSER_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(PARSER_DIR) not in sys.path:
    sys.path.insert(0, str(PARSER_DIR))


def _sample_task_and_markdown(result_dir: Path) -> tuple[dict, str]:
    document_full = json.loads((result_dir / "document_full.json").read_text(encoding="utf-8"))
    return document_full["task"], (result_dir / "result.md").read_text(encoding="utf-8")


def _result_dir(task_id: str) -> Path:
    return REPO_ROOT / "data" / "pdf-parser" / "results" / task_id


def test_jp_financial_artifact_builder_recovers_cash_flow_used_in_variant():
    from jp_financial_artifacts import build_jp_financial_artifacts

    result_dir = _result_dir("928fabbe-7bb2-4f1c-829a-fc8f95e8086d")
    if not result_dir.exists():
        pytest.skip("Subaru JP parser sample is not available in this checkout")
    task, markdown = _sample_task_and_markdown(result_dir)

    data, checks = build_jp_financial_artifacts(
        task,
        markdown,
        result_dir_path=str(result_dir),
        filename=task["filename"],
    )

    statement_types = {statement["statement_type"] for statement in data["statements"]}
    assert {"balance_sheet", "income_statement", "cash_flow_statement"}.issubset(statement_types)
    assert checks["overall_status"] == "pass"
    assert checks["summary"]["fail"] == 0


def test_jp_financial_artifact_builder_recovers_formal_statements_from_edinet_pdf_tables():
    from jp_financial_artifacts import build_jp_financial_artifacts

    result_dir = _result_dir("d258580c-6611-4193-bc35-7c276ef4aa34")
    if not result_dir.exists():
        pytest.skip("Mitsubishi Estate JP parser sample is not available in this checkout")
    document_full = json.loads((result_dir / "document_full.json").read_text(encoding="utf-8"))
    task = document_full["task"]
    markdown = document_full.get("markdown", {}).get("content", "")

    data, checks = build_jp_financial_artifacts(
        task,
        markdown,
        result_dir_path=str(result_dir),
        filename=task["filename"],
    )

    statement_types = {statement["statement_type"] for statement in data["statements"]}
    assert {"balance_sheet", "income_statement", "cash_flow_statement"}.issubset(statement_types)
    assert checks["summary"]["fail"] == 0
    assert all(
        check["status"] == "pass"
        for check in checks["checks"]
        if check["rule_id"].startswith("required.statement.")
    )
    revenue_periods = {
        item["period_key"]
        for statement in data["statements"]
        if statement["statement_type"] == "income_statement"
        for item in statement["items"]
        if item["canonical_name"] == "operating_revenue"
    }
    assert "2025-03-31" in revenue_periods


def test_jp_financial_artifact_builder_inherits_units_across_split_balance_sheet_pages():
    from jp_financial_artifacts import build_jp_financial_artifacts

    result_dir = _result_dir("dc9cc8b3-0bd1-474b-8967-3de1486f9c3f")
    if not result_dir.exists():
        pytest.skip("KDDI JP parser sample is not available in this checkout")
    document_full = json.loads((result_dir / "document_full.json").read_text(encoding="utf-8"))
    task = document_full["task"]
    markdown = document_full.get("markdown", {}).get("content", "")

    data, checks = build_jp_financial_artifacts(
        task,
        markdown,
        result_dir_path=str(result_dir),
        filename=task["filename"],
    )

    assert checks["overall_status"] == "pass"
    assert checks["summary"]["fail"] == 0
    split_page_total_assets = [
        item
        for statement in data["statements"]
        if statement["statement_type"] == "balance_sheet"
        for item in statement["items"]
        if item["canonical_name"] == "total_assets" and item["evidence"]["table_index"] == 65
    ]
    assert split_page_total_assets
    assert {item["scale"] for item in split_page_total_assets} == {"1000000"}


def test_kr_financial_artifact_builder_recovers_insurance_cash_flow_labels():
    from kr_financial_artifacts import build_kr_financial_artifacts

    result_dir = _result_dir("d23776c9-16bb-439a-b171-7e374f1c5c80")
    if not result_dir.exists():
        pytest.skip("Samsung Life KR parser sample is not available in this checkout")
    task, markdown = _sample_task_and_markdown(result_dir)

    data, checks = build_kr_financial_artifacts(
        task,
        markdown,
        result_dir_path=str(result_dir),
        filename=task["filename"],
    )

    statement_types = {statement["statement_type"] for statement in data["statements"]}
    assert {"balance_sheet", "income_statement", "cash_flow_statement"}.issubset(statement_types)
    assert data["ticker"] == "032830"
    assert checks["overall_status"] == "pass"
    assert checks["summary"]["fail"] == 0
