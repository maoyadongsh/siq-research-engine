from __future__ import annotations

import json
from pathlib import Path

import pytest

import pdf_parser_financial_service as financial


def test_write_financial_artifacts_dispatches_hk_market_to_hk_builder(tmp_path, monkeypatch):
    task = {"task_id": "hk-task", "filename": "LINK-REIT_HK_00823_2025-12-31_annual_hkex.pdf"}
    result_dir = lambda value: str(tmp_path / value["task_id"])
    writes = {}
    calls = []

    def write_json(path, payload):
        writes[Path(path).name] = payload

    def fake_hk_builder(task, markdown, *, result_dir_path, filename=None):
        calls.append({"task": task, "markdown": markdown, "result_dir_path": result_dir_path, "filename": filename})
        return (
            {"schema_version": financial.FINANCIAL_DATA_SCHEMA_VERSION, "rule_version": financial.FINANCIAL_RULE_VERSION, "market": "HK"},
            {"schema_version": financial.FINANCIAL_CHECKS_SCHEMA_VERSION, "rule_version": financial.FINANCIAL_RULE_VERSION, "market": "HK", "overall_status": "pass"},
        )

    monkeypatch.setattr(financial, "build_hk_financial_artifacts", fake_hk_builder)

    data, checks = financial.write_financial_artifacts(
        task,
        "markdown",
        result_dir=result_dir,
        write_json=write_json,
        financial_llm_cache_folder=str(tmp_path / "cache"),
    )

    assert data["market"] == "HK"
    assert checks["market"] == "HK"
    assert calls[0]["filename"] == task["filename"]
    assert Path(calls[0]["result_dir_path"]).name == "hk-task"
    assert writes["financial_data.json"]["market"] == "HK"
    assert writes["financial_checks.json"]["overall_status"] == "pass"


def test_ensure_financial_artifacts_rewrites_current_non_hk_files_for_hk(tmp_path, monkeypatch):
    task = {"task_id": "hk-task", "filename": "LINK-REIT_HK_00823_2025-12-31_annual_hkex.pdf"}
    result_dir = lambda value: str(tmp_path / value["task_id"])
    data_path = tmp_path / "hk-task" / "financial_data.json"
    checks_path = tmp_path / "hk-task" / "financial_checks.json"
    data_path.parent.mkdir(parents=True)
    data_path.write_text(
        json.dumps({"schema_version": financial.FINANCIAL_DATA_SCHEMA_VERSION, "rule_version": financial.FINANCIAL_RULE_VERSION}),
        encoding="utf-8",
    )
    checks_path.write_text(
        json.dumps({"schema_version": financial.FINANCIAL_CHECKS_SCHEMA_VERSION, "rule_version": financial.FINANCIAL_RULE_VERSION}),
        encoding="utf-8",
    )
    calls = []

    def fake_write_financial_artifacts(task, markdown, *, result_dir, write_json, financial_llm_cache_folder, file_name=None):
        calls.append((task, markdown, file_name))
        return (
            {"schema_version": financial.FINANCIAL_DATA_SCHEMA_VERSION, "rule_version": financial.FINANCIAL_RULE_VERSION, "market": "HK"},
            {"schema_version": financial.FINANCIAL_CHECKS_SCHEMA_VERSION, "rule_version": financial.FINANCIAL_RULE_VERSION, "market": "HK"},
        )

    monkeypatch.setattr(financial, "write_financial_artifacts", fake_write_financial_artifacts)

    data, checks = financial.ensure_financial_artifacts(
        task,
        "markdown",
        result_dir=result_dir,
        write_json=lambda path, payload: None,
        financial_llm_cache_folder=str(tmp_path / "cache"),
    )

    assert data["market"] == "HK"
    assert checks["market"] == "HK"
    assert calls == [(task, "markdown", task["filename"])]


def test_hk_financial_artifact_builder_extracts_link_reit_sample():
    from hk_financial_artifacts import build_hk_financial_artifacts

    result_dir = Path("data/pdf-parser/results/50090c9f-a424-4d73-b28c-96fa60dd99ff")
    if not result_dir.exists():
        pytest.skip("LINK REIT HK parser sample is not available in this checkout")
    document_full = json.loads((result_dir / "document_full.json").read_text(encoding="utf-8"))
    task = document_full["task"]
    markdown = (result_dir / "result.md").read_text(encoding="utf-8")

    data, checks = build_hk_financial_artifacts(
        task,
        markdown,
        result_dir_path=str(result_dir),
        filename=task["filename"],
    )

    assert data["market"] == "HK"
    assert data["accounting_standard"] in {"HKFRS", "IFRS"}
    assert data["industry_profile"] in {"real_estate", "general"}
    assert len(data["statements"]) >= 2
    assert len(data.get("key_metrics") or []) + len(data.get("operating_metrics") or []) >= 1
    assert checks["market"] == "HK"
    assert checks["overall_status"] != "skipped"
    assert checks["summary"]["total"] >= 1


def test_hk_financial_artifact_builder_uses_markdown_formal_window_for_tencent():
    from hk_financial_artifacts import build_hk_financial_artifacts

    result_dir = Path("data/pdf-parser/results/9aecfb55-5069-47b1-8383-47cb118b0b16")
    if not result_dir.exists():
        pytest.skip("TENCENT HK parser sample is not available in this checkout")
    document_full = json.loads((result_dir / "document_full.json").read_text(encoding="utf-8"))
    task = document_full["task"]
    markdown = (result_dir / "result.md").read_text(encoding="utf-8")

    data, checks = build_hk_financial_artifacts(
        task,
        markdown,
        result_dir_path=str(result_dir),
        filename=task["filename"],
    )

    statement_types = {statement["statement_type"] for statement in data["statements"]}
    assert {"balance_sheet", "income_statement", "cash_flow_statement"}.issubset(statement_types)
    assert checks["overall_status"] == "pass"
    assert checks["summary"]["fail"] == 0
