import types
import sys


class _DummyFlask:
    def __init__(self, *args, **kwargs):
        self.config = {}

    def route(self, *args, **kwargs):
        def decorator(func):
            return func
        return decorator

    def before_request(self, func=None):
        def decorator(func):
            return func
        return decorator if func is None else func

    def errorhandler(self, *args, **kwargs):
        def decorator(func):
            return func
        return decorator


sys.modules.setdefault(
    "flask",
    types.SimpleNamespace(
        Flask=_DummyFlask,
        jsonify=lambda *args, **kwargs: None,
        make_response=lambda value: types.SimpleNamespace(value=value, set_cookie=lambda *args, **kwargs: None),
        render_template=lambda *args, **kwargs: "",
        request=types.SimpleNamespace(args={}, files={}, form={}, headers={}, cookies={}, get_json=lambda silent=True: {}),
        send_file=lambda *args, **kwargs: None,
    ),
)

import app


def test_jp_quality_report_uses_japan_profile_tables_and_sections():
    markdown = """
# Integrated Report
<table><tr><td>FINANCIAL HIGHLIGHTS</td><td>2025</td><td>2024</td></tr><tr><td>Net sales</td><td>1,059,145</td><td>967,288</td></tr><tr><td>Operating profit</td><td>549,775</td><td>495,014</td></tr></table>
<table><tr><td>YEAR ENDED MARCH 20, 2025</td><td>2025</td><td>2024</td></tr><tr><td>ASSETS</td><td></td><td></td></tr><tr><td>CURRENT ASSETS</td><td>451,715</td><td>406,065</td></tr><tr><td>TOTAL ASSETS</td><td>3,000,000</td><td>2,800,000</td></tr><tr><td>LIABILITIES AND NET ASSETS</td><td>3,000,000</td><td>2,800,000</td></tr></table>
<table><tr><td>NET SALES</td><td>1,059,145</td><td>967,288</td></tr><tr><td>Operating income</td><td>549,775</td><td>495,014</td></tr><tr><td>Net income</td><td>398,656</td><td>369,642</td></tr></table>
<table><tr><td>CASH FLOWS FROM OPERATING ACTIVITIES</td><td>2025</td></tr><tr><td>Net cash provided by operating activities</td><td>400,000</td></tr></table>
    """
    task = {"task_id": "jp-quality", "filename": "Keyence-Corporation_JP_6861_2025.pdf", "submit_config": {"market": "JP"}}

    report = app._build_quality_report(markdown, task)

    candidate_names = [item["name"] for item in report["core_financial_table_candidates"]]
    assert "Financial Highlights" in candidate_names
    assert "Consolidated Statement of Financial Position" in candidate_names
    assert "资产负债表" not in candidate_names
    assert "重要提示" not in report["missing_sections"]
    assert report["report_kind"] == "jp_integrated_report"
    assert "Financial Highlights" in report["found_financial_tables"]


def test_jp_quality_report_locates_local_annual_highlights_table():
    markdown = """
# 有価証券報告書
第一部【企業情報】
## 1【主要な経営指標等の推移】
<table><tr><td>（1）連結経営指標等</td><td>第117期</td><td>第118期</td><td>第119期</td><td>第120期</td><td>第121期</td></tr><tr><td>決算期</td><td>2021年3月</td><td>2022年3月</td><td>2023年3月</td><td>2024年3月</td><td>2025年3月</td></tr><tr><td>営業収益 (百万円)</td><td>27,214,594</td><td>31,379,507</td><td>37,154,298</td><td>45,095,325</td><td>48,036,704</td></tr><tr><td>税引前利益 (百万円)</td><td>2,932,354</td><td>3,990,532</td><td>3,668,733</td><td>6,965,085</td><td>6,414,590</td></tr><tr><td>親会社の所有者に帰属する当期利益 (百万円)</td><td>2,245,261</td><td>2,850,110</td><td>2,451,318</td><td>4,944,933</td><td>4,765,029</td></tr><tr><td>総資産額 (百万円)</td><td>62,267,140</td><td>67,688,771</td><td>74,303,180</td><td>90,114,261</td><td>95,512,321</td></tr></table>
## 経理の状況
<table><tr><td>①【連結財政状態計算書】</td><td>2025年3月31日</td></tr><tr><td>資産合計</td><td>95,512,321</td></tr><tr><td>負債合計</td><td>60,000,000</td></tr></table>
    """
    task = {"task_id": "jp-quality-local", "filename": "Toyota-Motor-Corporation_JP_7203_2025.pdf", "submit_config": {"market": "JP"}}

    report = app._build_quality_report(markdown, task)

    by_name = {item["name"]: item for item in report["core_financial_table_candidates"]}
    assert by_name["Financial Highlights"]["status"] == "found"
    assert by_name["Financial Highlights"]["table_index"] == 1
    assert "Financial Highlights" in report["found_financial_tables"]
