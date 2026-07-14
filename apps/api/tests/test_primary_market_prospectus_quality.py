import json
from pathlib import Path

from services import primary_market_prospectus_quality as quality


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _valid_markdown() -> str:
    headings = "\n".join(
        [
            "# 重大事项提示",
            "# 风险因素",
            "# 发行人基本情况与股权结构",
            "# 业务与技术",
            "# 行业与竞争格局及市场地位",
            "# 公司治理、独立性与关联交易",
            "# 财务会计信息与管理层分析",
            "# 募集资金运用",
            "# 投资者保护、重要合同与诉讼",
        ]
    )
    return headings + "\n" + ("招股说明书正文。" * 180)


def test_quality_ready_when_text_trace_and_financial_checks_pass(tmp_path: Path):
    (tmp_path / "document.md").write_text(_valid_markdown(), encoding="utf-8")
    _write_json(tmp_path / "content_list.json", [{"page": 1, "text": "正文"}, {"page": 2, "text": "正文"}])
    _write_json(tmp_path / "financial_checks.json", {"overall_status": "pass"})
    _write_json(tmp_path / "financial_data.json", {"statements": [{"period": "2025"}]})

    report = quality.evaluate_prospectus_quality(tmp_path, generated_at="2026-07-13T00:00:00Z")

    assert report["status"] == "ready"
    assert report["capabilities"] == {
        "text_evidence": "ready",
        "source_page_trace": "ready",
        "financial_facts": "ready",
        "semantic_index": "pending",
    }
    assert report["section_coverage"]["found_count"] == 9


def test_quality_allows_text_with_financial_restriction(tmp_path: Path):
    (tmp_path / "result.md").write_text(_valid_markdown(), encoding="utf-8")
    _write_json(tmp_path / "content_list_enhanced.json", {"blocks": [{"page_idx": 0, "text": "正文"}]})
    _write_json(tmp_path / "financial_checks.json", {"overall_status": "fail"})

    report = quality.evaluate_prospectus_quality(tmp_path)

    assert report["status"] == "ready_with_restrictions"
    assert report["capabilities"]["text_evidence"] == "ready"
    assert report["capabilities"]["financial_facts"] == "blocked"


def test_quality_blocks_when_canonical_text_or_trace_is_missing(tmp_path: Path):
    (tmp_path / "document.md").write_text("too short", encoding="utf-8")
    _write_json(tmp_path / "financial_checks.json", {"overall_status": "pass"})
    _write_json(tmp_path / "financial_data.json", {})

    report = quality.evaluate_prospectus_quality(tmp_path)

    assert report["status"] == "blocked"
    assert report["capabilities"]["text_evidence"] == "blocked"
    assert report["capabilities"]["source_page_trace"] == "blocked"
    assert "canonical_markdown_missing_or_too_short" in report["blockers"]
