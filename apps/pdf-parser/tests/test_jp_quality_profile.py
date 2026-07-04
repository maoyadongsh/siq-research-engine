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
