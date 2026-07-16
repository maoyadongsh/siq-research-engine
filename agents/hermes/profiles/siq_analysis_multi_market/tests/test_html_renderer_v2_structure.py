import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "html_renderer_v2.py"
spec = importlib.util.spec_from_file_location("html_renderer_v2", MODULE_PATH)
renderer = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(renderer)


def test_render_html_report_preserves_core_structure_and_escaping():
    preflight = {
        "company_id": "600104<script>",
        "company_short_name": "上汽<集团>",
        "stock_code": "600104",
        "report_year": "2025",
        "task_id": "42",
    }
    snapshot = {
        "report_year": "2025",
        "metrics": {
            "operating_revenue": {"values": {"2024": 6140.74, "2025": 6461.52}, "unit": "亿元"},
            "net_profit_parent": {"values": {"2024": 16.66, "2025": 101.06}, "unit": "亿元"},
            "net_operating_cash_flow": {"values": {"2025": 343.07}, "unit": "亿元"},
            "gross_margin": {"values": {"2025": 10.09}, "unit": "%"},
            "total_assets": {"values": {"2025": 9602.07}, "unit": "亿元"},
            "total_liabilities": {"values": {"2025": 5989.31}, "unit": "亿元"},
        },
    }
    sections = [
        {
            "section_id": "executive_summary",
            "title": "一、执行摘要 <unsafe>",
            "narrative_blocks": [
                {
                    "role": "synthesis",
                    "title": "核心观点 <b>",
                    "items": ["【本地事实证据】收入增长来自公开年报。<script>alert(1)</script>"],
                }
            ],
            "evidence_ids": ["operating_revenue:2025:p7:t0"],
        },
        {
            "section_id": "profitability_and_cost",
            "title": "四、盈利与成本",
            "facts": ["归母净利润 101.06 亿元"],
            "evidence_ids": ["net_profit_parent:2025:p9:t1"],
        },
    ]

    html = renderer.render_html_report(
        preflight,
        snapshot,
        sections,
        {"overall_pass": True, "all_key_numbers_have_evidence": True, "review_queue": []},
    )

    assert html.startswith("<!DOCTYPE html>")
    assert "600104&lt;script&gt; 2025 财务诊断报告" in html
    assert "上汽&lt;集团&gt;" in html
    assert 'href="#section-executive_summary"' in html
    assert 'id="section-executive_summary"' in html
    assert "一、执行摘要 &lt;unsafe&gt;" in html
    assert "核心观点 &lt;b&gt;" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "本节证据 · 1 项" in html
    assert "operating_revenue:2025:p7:t0" in html
    assert "/api/pdf_page/42/7" in html
    assert "/api/source/42/table/0" in html
    assert 'id="revenue-profit-chart"' in html
    assert 'id="income-bridge-panel"' in html
    assert "结构验收通过" in html
    assert "证据完整" in html
