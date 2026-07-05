from __future__ import annotations

import json
from pathlib import Path

import pytest


def _sample_task_and_markdown(result_dir: Path) -> tuple[dict, str]:
    document_full = json.loads((result_dir / "document_full.json").read_text(encoding="utf-8"))
    return document_full["task"], (result_dir / "result.md").read_text(encoding="utf-8")


def test_jp_financial_artifact_builder_recovers_cash_flow_used_in_variant():
    from jp_financial_artifacts import build_jp_financial_artifacts

    result_dir = Path("data/pdf-parser/results/928fabbe-7bb2-4f1c-829a-fc8f95e8086d")
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


def test_kr_financial_artifact_builder_recovers_insurance_cash_flow_labels():
    from kr_financial_artifacts import build_kr_financial_artifacts

    result_dir = Path("data/pdf-parser/results/d23776c9-16bb-439a-b171-7e374f1c5c80")
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
