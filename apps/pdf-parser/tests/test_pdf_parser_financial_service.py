import json
from pathlib import Path

import pytest

import pdf_parser_financial_service as financial


def test_financial_artifact_paths_use_result_dir(tmp_path):
    task = {"task_id": "task-fin"}

    def result_dir(value):
        return str(tmp_path / value["task_id"])

    assert financial.financial_data_path(task, result_dir) == str(tmp_path / "task-fin" / "financial_data.json")
    assert financial.financial_checks_path(task, result_dir) == str(tmp_path / "task-fin" / "financial_checks.json")


def test_read_financial_artifacts_and_current_round_trip(tmp_path):
    task = {"task_id": "task-fin"}
    result_dir = lambda value: str(tmp_path / value["task_id"])
    data_path = tmp_path / "task-fin" / "financial_data.json"
    checks_path = tmp_path / "task-fin" / "financial_checks.json"
    data_path.parent.mkdir(parents=True)
    data = {
        "schema_version": financial.FINANCIAL_DATA_SCHEMA_VERSION,
        "rule_version": financial.FINANCIAL_RULE_VERSION,
        "summary": {"statement_count": 1},
    }
    checks = {
        "schema_version": financial.FINANCIAL_CHECKS_SCHEMA_VERSION,
        "rule_version": financial.FINANCIAL_RULE_VERSION,
        "summary": {"pass": 1},
    }
    data_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    checks_path.write_text(json.dumps(checks, ensure_ascii=False), encoding="utf-8")

    loaded_data, loaded_checks = financial.read_financial_artifacts(task, result_dir)

    assert loaded_data == data
    assert loaded_checks == checks
    assert financial.financial_artifacts_are_current(loaded_data, loaded_checks)


@pytest.mark.parametrize("existing_artifact", ["financial_data.json", "financial_checks.json"])
def test_read_financial_artifacts_ignores_single_sided_artifact(tmp_path, existing_artifact):
    task = {"task_id": "task-fin"}
    result_dir = lambda value: str(tmp_path / value["task_id"])
    artifact_path = tmp_path / "task-fin" / existing_artifact
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text(json.dumps({"schema_version": 1}, ensure_ascii=False), encoding="utf-8")

    assert financial.read_financial_artifacts(task, result_dir) == (None, None)


@pytest.mark.parametrize(
    ("data_patch", "checks_patch"),
    [
        ({"schema_version": financial.FINANCIAL_DATA_SCHEMA_VERSION - 1}, {}),
        ({}, {"schema_version": financial.FINANCIAL_CHECKS_SCHEMA_VERSION - 1}),
        ({"rule_version": "stale-data-rules"}, {}),
        ({}, {"rule_version": "stale-check-rules"}),
    ],
)
def test_financial_artifacts_are_current_rejects_single_sided_schema_or_rule_mismatch(data_patch, checks_patch):
    data = {
        "schema_version": financial.FINANCIAL_DATA_SCHEMA_VERSION,
        "rule_version": financial.FINANCIAL_RULE_VERSION,
    }
    checks = {
        "schema_version": financial.FINANCIAL_CHECKS_SCHEMA_VERSION,
        "rule_version": financial.FINANCIAL_RULE_VERSION,
    }
    data.update(data_patch)
    checks.update(checks_patch)

    assert not financial.financial_artifacts_are_current(data, checks)


def test_ensure_financial_artifacts_writes_when_stale(tmp_path):
    task = {"task_id": "task-fin", "filename": "report.pdf"}
    result_dir = lambda value: str(tmp_path / value["task_id"])
    writes = {}

    def write_json(path, payload):
        writes[path] = payload

    def build_data(markdown, task_id=None, filename=None, llm_cache_dir=None):
        return {
            "schema_version": financial.FINANCIAL_DATA_SCHEMA_VERSION,
            "rule_version": financial.FINANCIAL_RULE_VERSION,
            "markdown": markdown,
            "task_id": task_id,
            "filename": filename,
            "llm_cache_dir": llm_cache_dir,
        }

    def build_checks(data):
        return {
            "schema_version": financial.FINANCIAL_CHECKS_SCHEMA_VERSION,
            "rule_version": financial.FINANCIAL_RULE_VERSION,
            "source_task_id": data["task_id"],
        }

    financial_data, financial_checks = financial.write_financial_artifacts(
        task,
        "markdown",
        result_dir=result_dir,
        write_json=write_json,
        financial_llm_cache_folder=str(tmp_path / "cache"),
        build_data=build_data,
        build_checks=build_checks,
    )

    assert financial_data["task_id"] == "task-fin"
    assert financial_data["filename"] == "report.pdf"
    assert financial_data["llm_cache_dir"].endswith("cache/task-fin")
    assert financial_checks["source_task_id"] == "task-fin"
    assert any(path.endswith("financial_data.json") for path in writes)
    assert any(path.endswith("financial_checks.json") for path in writes)


def test_ensure_financial_artifacts_rewrites_when_checks_schema_is_stale(tmp_path, monkeypatch):
    task = {"task_id": "task-fin", "filename": "report.pdf"}
    result_dir = lambda value: str(tmp_path / value["task_id"])
    data_path = tmp_path / "task-fin" / "financial_data.json"
    checks_path = tmp_path / "task-fin" / "financial_checks.json"
    data_path.parent.mkdir(parents=True)
    data_path.write_text(
        json.dumps(
            {
                "schema_version": financial.FINANCIAL_DATA_SCHEMA_VERSION,
                "rule_version": financial.FINANCIAL_RULE_VERSION,
                "sentinel": "old-data",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    checks_path.write_text(
        json.dumps(
            {
                "schema_version": financial.FINANCIAL_CHECKS_SCHEMA_VERSION - 1,
                "rule_version": financial.FINANCIAL_RULE_VERSION,
                "sentinel": "old-checks",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    writes = []

    def write_json(path, payload):
        writes.append((path, payload))

    def fake_write_financial_artifacts(
        task,
        markdown,
        *,
        result_dir,
        write_json,
        financial_llm_cache_folder,
        file_name=None,
    ):
        writes.append(
            {
                "task": task,
                "markdown": markdown,
                "result_dir": result_dir,
                "write_json": write_json,
                "financial_llm_cache_folder": financial_llm_cache_folder,
                "file_name": file_name,
            }
        )
        return (
            {"schema_version": financial.FINANCIAL_DATA_SCHEMA_VERSION, "sentinel": "new-data"},
            {"schema_version": financial.FINANCIAL_CHECKS_SCHEMA_VERSION, "sentinel": "new-checks"},
        )

    monkeypatch.setattr(financial, "write_financial_artifacts", fake_write_financial_artifacts)

    financial_data, financial_checks = financial.ensure_financial_artifacts(
        task,
        "markdown",
        result_dir=result_dir,
        write_json=write_json,
        financial_llm_cache_folder=str(tmp_path / "cache"),
    )

    assert financial_data["sentinel"] == "new-data"
    assert financial_checks["sentinel"] == "new-checks"
    assert len(writes) == 1
    assert writes[0]["task"] == task
    assert writes[0]["markdown"] == "markdown"


def test_ensure_financial_artifacts_reuses_current_files(tmp_path):
    task = {"task_id": "task-fin", "filename": "report.pdf"}
    result_dir = lambda value: str(tmp_path / value["task_id"])
    data_path = tmp_path / "task-fin" / "financial_data.json"
    checks_path = tmp_path / "task-fin" / "financial_checks.json"
    data_path.parent.mkdir(parents=True)
    data = {
        "schema_version": financial.FINANCIAL_DATA_SCHEMA_VERSION,
        "rule_version": financial.FINANCIAL_RULE_VERSION,
        "sentinel": "data",
    }
    checks = {
        "schema_version": financial.FINANCIAL_CHECKS_SCHEMA_VERSION,
        "rule_version": financial.FINANCIAL_RULE_VERSION,
        "sentinel": "checks",
    }
    data_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    checks_path.write_text(json.dumps(checks, ensure_ascii=False), encoding="utf-8")

    called = []

    def write_json(path, payload):
        called.append((path, payload))

    loaded_data, loaded_checks = financial.ensure_financial_artifacts(
        task,
        "markdown",
        result_dir=result_dir,
        write_json=write_json,
        financial_llm_cache_folder=str(tmp_path / "cache"),
    )

    assert loaded_data == data
    assert loaded_checks == checks
    assert called == []


def test_ensure_financial_artifacts_rewrites_current_jp_files_without_market_profile(tmp_path, monkeypatch):
    task = {"task_id": "jp-task", "filename": "Keyence-Corporation_JP_6861_2025.pdf", "submit_config": {"market": "JP"}}
    result_dir = lambda value: str(tmp_path / value["task_id"])
    data_path = tmp_path / "jp-task" / "financial_data.json"
    checks_path = tmp_path / "jp-task" / "financial_checks.json"
    data_path.parent.mkdir(parents=True)
    data_path.write_text(
        json.dumps(
            {
                "schema_version": financial.FINANCIAL_DATA_SCHEMA_VERSION,
                "rule_version": financial.FINANCIAL_RULE_VERSION,
                "sentinel": "old-a-share-data",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    checks_path.write_text(
        json.dumps(
            {
                "schema_version": financial.FINANCIAL_CHECKS_SCHEMA_VERSION,
                "rule_version": financial.FINANCIAL_RULE_VERSION,
                "sentinel": "old-a-share-checks",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls = []

    def write_json(path, payload):
        calls.append(("write_json", path, payload))

    def fake_write_financial_artifacts(
        task,
        markdown,
        *,
        result_dir,
        write_json,
        financial_llm_cache_folder,
        file_name=None,
    ):
        calls.append(("rewrite", task, markdown, file_name))
        return (
            {"schema_version": financial.FINANCIAL_DATA_SCHEMA_VERSION, "rule_version": financial.FINANCIAL_RULE_VERSION, "market": "JP"},
            {"schema_version": financial.FINANCIAL_CHECKS_SCHEMA_VERSION, "rule_version": financial.FINANCIAL_RULE_VERSION, "market": "JP"},
        )

    monkeypatch.setattr(financial, "write_financial_artifacts", fake_write_financial_artifacts)

    financial_data, financial_checks = financial.ensure_financial_artifacts(
        task,
        "markdown",
        result_dir=result_dir,
        write_json=write_json,
        financial_llm_cache_folder=str(tmp_path / "cache"),
    )

    assert financial_data["market"] == "JP"
    assert financial_checks["market"] == "JP"
    assert calls == [("rewrite", task, "markdown", "Keyence-Corporation_JP_6861_2025.pdf")]


def test_financial_artifacts_match_market_rejects_old_non_cn_market_payloads():
    stale_data = {
        "schema_version": financial.FINANCIAL_DATA_SCHEMA_VERSION,
        "rule_version": financial.FINANCIAL_RULE_VERSION,
        "summary": {"statement_count": 0},
    }
    stale_checks = {
        "schema_version": financial.FINANCIAL_CHECKS_SCHEMA_VERSION,
        "rule_version": financial.FINANCIAL_RULE_VERSION,
        "summary": {"pass": 0},
    }

    for market in ("HK", "JP", "KR", "EU", "US"):
        assert not financial.financial_artifacts_match_market(market, stale_data, stale_checks)

    assert financial.financial_artifacts_match_market("CN", stale_data, stale_checks)


def test_detect_market_recognizes_us_pdf_fallback_names():
    assert financial.detect_market({"submit_config": {"market": "US"}}, "anything.pdf") == "US"
    assert financial.detect_market({}, "NVIDIA_US_NVDA_2025-10-K_sec.pdf") == "US"


def test_us_sec_schema_v1_artifacts_are_current_for_us_sec_chain():
    data = {"schema_version": 1, "rule_version": "us_sec_rules_v1", "market": "US"}
    checks = {"schema_version": 1, "rule_version": "us_sec_rules_v1", "market": "US"}

    assert financial.financial_artifacts_are_current(data, checks)


def test_write_financial_artifacts_dispatches_hk_market_to_hk_builder(tmp_path, monkeypatch):
    task = {"task_id": "hk-task", "filename": "LINK-REIT_HK_00823_2025-12-31_年报_2026-06-11_hkex.pdf"}
    result_dir = lambda value: str(tmp_path / value["task_id"])
    writes = {}
    calls = []

    def write_json(path, payload):
        writes[Path(path).name] = payload

    def fake_hk_builder(task, markdown, *, result_dir_path, filename=None):
        calls.append({"task": task, "markdown": markdown, "result_dir_path": result_dir_path, "filename": filename})
        return (
            {"schema_version": financial.FINANCIAL_DATA_SCHEMA_VERSION, "rule_version": financial.FINANCIAL_RULE_VERSION, "market": "HK", "statements": [{"statement_type": "balance_sheet"}]},
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


def test_write_financial_artifacts_keeps_non_hk_on_legacy_builder(tmp_path, monkeypatch):
    task = {"task_id": "cn-task", "filename": "600000_CN_2025.pdf"}
    result_dir = lambda value: str(tmp_path / value["task_id"])
    hk_calls = []
    writes = {}

    def write_json(path, payload):
        writes[Path(path).name] = payload

    def fake_hk_builder(*args, **kwargs):
        hk_calls.append((args, kwargs))
        raise AssertionError("HK builder should not be called for non-HK tasks")

    monkeypatch.setattr(financial, "build_hk_financial_artifacts", fake_hk_builder)

    data, checks = financial.write_financial_artifacts(
        task,
        "markdown",
        result_dir=result_dir,
        write_json=write_json,
        financial_llm_cache_folder=str(tmp_path / "cache"),
        build_data=lambda markdown, **kwargs: {"schema_version": financial.FINANCIAL_DATA_SCHEMA_VERSION, "rule_version": financial.FINANCIAL_RULE_VERSION, "market": "CN", "task_id": kwargs["task_id"]},
        build_checks=lambda payload: {"schema_version": financial.FINANCIAL_CHECKS_SCHEMA_VERSION, "rule_version": financial.FINANCIAL_RULE_VERSION, "source_market": payload["market"]},
    )

    assert hk_calls == []
    assert data["market"] == "CN"
    assert checks["source_market"] == "CN"
    assert writes["financial_data.json"]["task_id"] == "cn-task"


def test_write_financial_artifacts_dispatches_kr_market_to_kr_builder(tmp_path, monkeypatch):
    task = {"task_id": "kr-task", "filename": "Samsung-Electronics-Co.,-Ltd_KR_005930_2025.pdf"}
    result_dir = lambda value: str(tmp_path / value["task_id"])
    writes = {}
    calls = []

    def write_json(path, payload):
        writes[Path(path).name] = payload

    def fake_kr_builder(task, markdown, *, result_dir_path, filename=None):
        calls.append({"task": task, "markdown": markdown, "result_dir_path": result_dir_path, "filename": filename})
        return {
            "schema_version": financial.FINANCIAL_DATA_SCHEMA_VERSION,
            "rule_version": financial.FINANCIAL_RULE_VERSION,
            "profile_rule_version": financial.KR_FINANCIAL_PROFILE_VERSION,
            "market": "KR",
            "task_id": task["task_id"],
            "filename": filename,
            "report_kind": "kr_business_report",
            "statements": [{"statement_type": "balance_sheet"}],
            "warnings": [],
        }, {
            "schema_version": financial.FINANCIAL_CHECKS_SCHEMA_VERSION,
            "rule_version": financial.FINANCIAL_RULE_VERSION,
            "profile_rule_version": financial.KR_FINANCIAL_PROFILE_VERSION,
            "market": "KR",
            "overall_status": "pass",
            "warnings": [],
        }

    monkeypatch.setattr(financial, "build_kr_financial_artifacts", fake_kr_builder)

    data, checks = financial.write_financial_artifacts(
        task,
        "markdown",
        result_dir=result_dir,
        write_json=write_json,
        financial_llm_cache_folder=str(tmp_path / "cache"),
        build_data=lambda markdown, **kwargs: (_ for _ in ()).throw(AssertionError("legacy builder should not be called")),
    )

    assert data["market"] == "KR"
    assert checks["market"] == "KR"
    assert checks["overall_status"] == "pass"
    assert calls[0]["filename"] == task["filename"]
    assert Path(calls[0]["result_dir_path"]).name == "kr-task"
    assert writes["financial_data.json"]["market"] == "KR"
    assert writes["financial_checks.json"]["market"] == "KR"


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
    assert data["industry_profile"] in {"real_estate", "reit", "general"}
    assert len(data["statements"]) >= 2
    assert len(data.get("key_metrics") or []) + len(data.get("operating_metrics") or []) >= 1
    assert checks["market"] == "HK"
    assert checks["overall_status"] != "skipped"
    assert checks["summary"]["total"] >= 1


def test_write_financial_artifacts_dispatches_eu_market_to_eu_checks(tmp_path):
    task = {
        "task_id": "eu-task",
        "filename": "London-Stock-Exchange-Group-plc_EU_LSEG_2025-12-31_annual.pdf",
    }
    result_dir = lambda value: str(tmp_path / value["task_id"])
    writes = {}
    build_kwargs = {}

    def write_json(path, payload):
        writes[Path(path).name] = payload

    def fake_build_data(markdown, **kwargs):
        build_kwargs.update(kwargs)
        return {
            "schema_version": financial.FINANCIAL_DATA_SCHEMA_VERSION,
            "rule_version": financial.FINANCIAL_RULE_VERSION,
            "market": kwargs["market"],
            "task_id": kwargs["task_id"],
            "filename": kwargs["filename"],
            "report_kind": "eu_annual_report",
            "statements": [],
            "warnings": [],
            "summary": {"statement_count": 0, "key_metric_count": 0, "scopes": []},
        }

    data, checks = financial.write_financial_artifacts(
        task,
        "Annual Report and Accounts\nFinancial Statements",
        result_dir=result_dir,
        write_json=write_json,
        financial_llm_cache_folder=str(tmp_path / "cache"),
        build_data=fake_build_data,
    )

    assert financial.detect_market(task, task["filename"]) == "EU"
    assert build_kwargs["market"] == "EU"
    assert data["market"] == "EU"
    assert checks["market"] == "EU"
    assert checks["overall_status"] == "fail"
    assert checks["summary"]["fail"] == 3
    assert {
        item["statement_type"]
        for item in checks["checks"]
        if item.get("status") == "fail"
    } == {"balance_sheet", "income_statement", "cash_flow_statement"}
    assert writes["financial_data.json"]["market"] == "EU"
    assert writes["financial_checks.json"]["market"] == "EU"
    assert not any("未提取到合并资产负债表" in item for item in checks["warnings"])


def test_write_financial_artifacts_uses_us_fallback_checks_without_a_share_warnings(tmp_path):
    task = {"task_id": "us-task", "filename": "NVIDIA-CORP_US_NVDA_2025-01-26_10-K_sec.pdf"}
    result_dir = lambda value: str(tmp_path / value["task_id"])
    writes = {}
    build_kwargs = {}

    def write_json(path, payload):
        writes[Path(path).name] = payload

    def fake_build_data(markdown, **kwargs):
        build_kwargs.update(kwargs)
        return {
            "schema_version": financial.FINANCIAL_DATA_SCHEMA_VERSION,
            "rule_version": financial.FINANCIAL_RULE_VERSION,
            "market": kwargs["market"],
            "task_id": kwargs["task_id"],
            "filename": kwargs["filename"],
            "report_kind": "us_10k",
            "statements": [],
            "warnings": [],
            "summary": {"statement_count": 0, "key_metric_count": 0, "scopes": []},
        }

    data, checks = financial.write_financial_artifacts(
        task,
        "Form 10-K\nItem 8. Financial Statements",
        result_dir=result_dir,
        write_json=write_json,
        financial_llm_cache_folder=str(tmp_path / "cache"),
        build_data=fake_build_data,
    )

    assert build_kwargs["market"] == "US"
    assert data["market"] == "US"
    assert checks["market"] == "US"
    assert checks["market_profile"] == "US"
    joined_warnings = "\n".join(checks.get("warnings") or [])
    assert "合并资产负债表" not in joined_warnings
    assert "合并利润表" not in joined_warnings
    assert "SEC HTML/iXBRL" in joined_warnings
    assert writes["financial_data.json"]["market"] == "US"
    assert writes["financial_checks.json"]["market"] == "US"


def test_write_financial_artifacts_uses_jp_profile_without_a_share_missing_warnings(tmp_path):
    task = {"task_id": "jp-task", "filename": "Keyence-Corporation_JP_6861_2025.pdf", "submit_config": {"market": "JP"}}
    result_dir = lambda value: str(tmp_path / value["task_id"])
    writes = {}

    def write_json(path, payload):
        writes[Path(path).name] = payload

    markdown = """
    <table><tr><td>FINANCIAL HIGHLIGHTS</td><td>2025</td></tr><tr><td>Net sales</td><td>1059145</td></tr></table>
    <table><tr><td>ASSETS</td><td>2025</td></tr><tr><td>Total assets</td><td>3000000</td></tr><tr><td>LIABILITIES AND NET ASSETS</td><td>3000000</td></tr></table>
    """

    data, checks = financial.write_financial_artifacts(
        task,
        markdown,
        result_dir=result_dir,
        write_json=write_json,
        financial_llm_cache_folder=str(tmp_path / "cache"),
    )

    assert data["market"] == "JP"
    assert checks["market"] == "JP"
    joined_warnings = "\n".join(checks.get("warnings") or [])
    assert "未提取到合并资产负债表" not in joined_warnings
    assert "未提取到合并利润表" not in joined_warnings
    assert "未提取到合并现金流量表" not in joined_warnings
    assert "JP" in joined_warnings
    assert writes["financial_data.json"]["market"] == "JP"
