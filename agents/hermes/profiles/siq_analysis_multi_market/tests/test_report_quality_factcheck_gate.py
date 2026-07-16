import importlib.util
import json
from pathlib import Path


PROFILE_DIR = Path(__file__).resolve().parents[1]


def _load_validate_report_quality():
    path = PROFILE_DIR / "scripts" / "validate_report_quality.py"
    spec = importlib.util.spec_from_file_location("validate_report_quality", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


validate_report_quality = _load_validate_report_quality()


def _write_minimal_report(tmp_path: Path, verdict: str | None) -> Path:
    prefix = tmp_path / "600000-test-2025-analysis"
    report_data = {
        "template_id": "siq_analysis_report_v1.1",
        "report_year": 2025,
        "factcheck": ({"verdict": verdict} if verdict is not None else {}),
        "quality_report": {},
        "sections": [],
    }
    prefix.with_suffix(".json").write_text(json.dumps(report_data, ensure_ascii=False), encoding="utf-8")
    prefix.with_suffix(".md").write_text("", encoding="utf-8")
    prefix.with_suffix(".html").write_text("", encoding="utf-8")
    return prefix


def test_publication_gate_allows_approved_factcheck():
    gate = validate_report_quality.publication_gate([], [], {"verdict": "approve"})

    assert gate["contract_pass"] is True
    assert gate["publish_ready"] is True
    assert gate["pass_with_review"] is False
    assert gate["publication_status"] == "publish_ready"


def test_publication_gate_keeps_request_changes_in_review():
    gate = validate_report_quality.publication_gate([], ["factcheck_verdict_request_changes"], {"verdict": "request_changes"})

    assert gate["contract_pass"] is True
    assert gate["publish_ready"] is False
    assert gate["pass_with_review"] is True
    assert gate["publication_status"] == "pass_with_review"


def test_publication_gate_blocks_factcheck_block(tmp_path):
    prefix = _write_minimal_report(tmp_path, "block")
    result = validate_report_quality.validate(prefix)

    assert result["ok"] is False
    assert result["contract_pass"] is False
    assert result["publish_ready"] is False
    assert result["publication_status"] == "blocked"
    assert "factcheck_verdict_block" in result["failures"]


def test_publication_gate_treats_missing_factcheck_as_review(tmp_path):
    prefix = _write_minimal_report(tmp_path, None)
    result = validate_report_quality.validate(prefix)

    assert result["factcheck"]["verdict"] == "missing"
    assert result["publish_ready"] is False
    assert "factcheck_verdict_missing" in result["warnings"]


def test_visible_negative_ordinary_expense_mentions_flags_reader_facing_costs():
    text = "营业收入为 4564.52亿元，营业成本为 -3359.90亿元，销售费用：-428.91亿元。"

    assert validate_report_quality.visible_negative_ordinary_expense_mentions(text) == ["营业成本", "销售费用"]


def test_visible_negative_ordinary_expense_mentions_ignores_waterfall_deltas():
    text = "| 营业成本 | -3359.90 | cost |\n费用桥中 delta 为负数表示流出。"

    assert validate_report_quality.visible_negative_ordinary_expense_mentions(text) == []
