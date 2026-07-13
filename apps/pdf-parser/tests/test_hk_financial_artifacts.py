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
    rmb_statements = [statement for statement in data["statements"] if "rmb" in str(statement.get("unit") or "").lower()]
    assert rmb_statements
    assert {statement["currency"] for statement in rmb_statements} == {"CNY"}
    assert {
        item["currency"]
        for statement in rmb_statements
        for item in statement.get("items") or []
        if "rmb" in str(item.get("unit") or statement.get("unit") or "").lower()
    } == {"CNY"}
    assert checks["overall_status"] == "pass"
    assert checks["summary"]["fail"] == 0


def test_hk_financial_artifact_builder_uses_explicit_usd_presentation_for_hsbc():
    from hk_financial_artifacts import build_hk_financial_artifacts

    result_dir = Path("data/pdf-parser/results/24039b93-d3e3-4a29-a39f-7bea0b5b7d3a")
    if not result_dir.exists():
        pytest.skip("HSBC HK parser sample is not available in this checkout")
    document_full = json.loads((result_dir / "document_full.json").read_text(encoding="utf-8"))
    task = document_full["task"]
    markdown = (result_dir / "result_complete.md").read_text(encoding="utf-8")

    data, _ = build_hk_financial_artifacts(
        task,
        markdown,
        result_dir_path=str(result_dir),
        filename=task["filename"],
    )

    statements = {statement["statement_type"]: statement for statement in data["statements"]}
    assert {statement["currency"] for statement in statements.values()} == {"USD"}
    assert {statement["unit"] for statement in statements.values()} == {"million"}
    assert {statement["scale"] for statement in statements.values()} == {"1000000"}
    income_items = statements["income_statement"]["items"]
    assert any(
        item.get("canonical_name") == "net_interest_income"
        and item.get("period_key") == "2025-12-31"
        and item.get("value") == "34794"
        for item in income_items
    )


def test_hk_report_type_detects_non_annual_hkex_documents():
    from hk_financial_artifacts import _hk_report_type_from_content, _report_kind

    supplemental = {
        "markdown": {
            "content": "# SUPPLEMENTAL ANNOUNCEMENT TO THE ANNUAL REPORT FOR THE YEAR ENDED 31 DECEMBER 2025"
        }
    }
    notice = {
        "markdown": {
            "content": "Dear non-registered shareholder, Notice of Publication of Annual Report 2025 and Current Corporate Communications are available on the website."
        }
    }
    regulatory = {"markdown": {"content": "# OVERSEAS REGULATORY ANNOUNCEMENT\nThis announcement is issued pursuant to Rule 13.10B."}}

    assert _hk_report_type_from_content(supplemental) == "supplemental_announcement"
    assert _hk_report_type_from_content(notice) == "corporate_communication_notice"
    assert _hk_report_type_from_content(regulatory) == "overseas_regulatory_announcement"
    assert _report_kind("supplemental_announcement") == "supplemental_announcement"


def test_hk_markdown_tables_support_plain_bank_statement_headings():
    from hk_evidence_lib import _markdown_statement_tables

    result_dir = Path("data/pdf-parser/results/4c4f0281-34a2-4e0e-9ee2-e4b6bb6b2163")
    if not result_dir.exists():
        pytest.skip("BANK OF CHINA HK parser sample is not available in this checkout")

    tables = _markdown_statement_tables(result_dir)
    statement_types = {(table.raw or {}).get("statement_type") for table in tables}

    assert {"balance_sheet", "income_statement", "cash_flow_statement"}.issubset(statement_types)


def test_hk_markdown_tables_support_20f_operations_headings():
    from hk_evidence_lib import _markdown_statement_tables

    result_dir = Path("data/pdf-parser/results/0b8d4d2e-32f0-4ce7-909b-4c74456a1cbb")
    if not result_dir.exists():
        pytest.skip("NETEASE HK parser sample is not available in this checkout")

    tables = _markdown_statement_tables(result_dir)
    income_titles = [
        table.title
        for table in tables
        if (table.raw or {}).get("statement_type") == "income_statement"
    ]

    assert any("Operations" in title for title in income_titles)


def test_hk_markdown_tables_support_split_comprehensive_income_pages():
    from hk_evidence_lib import _markdown_statement_tables

    result_dir = Path("data/pdf-parser/results/362176b2-5a57-441d-9191-e060618a3a70")
    if not result_dir.exists():
        pytest.skip("LI AUTO HK parser sample is not available in this checkout")

    tables = _markdown_statement_tables(result_dir)
    income_tables = [
        table
        for table in tables
        if (table.raw or {}).get("statement_type") == "income_statement"
        and "COMPREHENSIVE INCOME" in str(table.title or "")
    ]

    assert income_tables


def test_hk_markdown_formal_window_can_start_before_auditor_report():
    from hk_evidence_lib import _markdown_statement_tables

    result_dir = Path("data/pdf-parser/results/aaba3271-6f9b-44b5-be92-ed926a6cb43d")
    if not result_dir.exists():
        pytest.skip("CRRC HK parser sample is not available in this checkout")

    tables = _markdown_statement_tables(result_dir)
    statement_types = {(table.raw or {}).get("statement_type") for table in tables}

    assert {"balance_sheet", "income_statement", "cash_flow_statement"}.issubset(statement_types)
