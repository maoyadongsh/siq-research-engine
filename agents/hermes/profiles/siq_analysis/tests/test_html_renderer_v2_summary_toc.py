import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "html_renderer_v2.py"
spec = importlib.util.spec_from_file_location("html_renderer_v2", MODULE_PATH)
renderer = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(renderer)


def test_report_summary_is_reader_facing_not_process_log():
    snapshot = {
        "report_year": "2025",
        "metrics": {
            "operating_revenue": {"values": {"2024": 6140.74, "2025": 6461.52}, "unit": "亿元"},
            "net_profit_parent": {"values": {"2024": 16.66, "2025": 101.06}, "unit": "亿元"},
            "deducted_parent_net_profit": {"values": {"2025": 74.23}, "unit": "亿元"},
            "net_operating_cash_flow": {"values": {"2025": 343.07}, "unit": "亿元"},
            "gross_margin": {"values": {"2025": 10.09}, "unit": "%"},
            "total_assets": {"values": {"2025": 9602.07}, "unit": "亿元"},
            "total_liabilities": {"values": {"2025": 5989.31}, "unit": "亿元"},
            "capital_expenditure": {"values": {"2025": 217.86}, "unit": "亿元"},
        },
    }
    sections = [
        {
            "section_id": "executive_summary",
            "title": "一、执行摘要",
            "narrative_blocks": [
                {
                    "role": "synthesis",
                    "items": [
                        "研究包补充判断：wiki_inventory file_count=291，metric_snapshot 与 evidence_package 可作为章节底稿来源。"
                    ],
                }
            ],
        }
    ]

    html = renderer.render_report_summary(
        {"company_short_name": "上汽集团", "report_year": "2025"},
        snapshot,
        sections,
        ["<span>ok</span>"],
    )

    assert "经营安全与盈利质量" in html
    assert "现金流含金量" in html
    assert "研究包" not in html
    assert "wiki_inventory" not in html
    assert "metric_snapshot" not in html
    assert "evidence_package" not in html


def test_navigation_outputs_real_section_links():
    sections = [
        {"section_id": "executive_summary", "title": "一、执行摘要"},
        {"section_id": "operating_quality", "title": "三、经营质量分析"},
    ]

    html = renderer.render_navigation(sections)

    assert 'href="#section-executive_summary"' in html
    assert 'href="#section-operating_quality"' in html
    assert 'aria-label="跳转到三、经营质量分析"' in html
    assert html.count("<a ") == 2


def test_renderer_static_assets_are_loaded_through_facade():
    assert ".section-toc" in renderer.CSS_STYLES
    assert ".income-bridge-panel" in renderer.CSS_STYLES
    assert "document.addEventListener('DOMContentLoaded'" in renderer.ECHARTS_SCRIPTS
    assert "echarts.init" in renderer.ECHARTS_SCRIPTS
