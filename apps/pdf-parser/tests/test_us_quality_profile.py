import sys
import types


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


def test_us_quality_report_uses_sec_profile_tables():
    markdown = """
    # Form 10-K
    # Item 1. Business
    # Item 1A. Risk Factors
    # Item 7. Management's Discussion and Analysis
    # Item 8. Financial Statements and Supplementary Data
    # Report of Independent Registered Public Accounting Firm
    # Consolidated Statements of Operations
    <table><tr><td>Year ended January 26</td><td>2025</td><td>2024</td></tr>
    <tr><td>Revenue</td><td>130,497</td><td>60,922</td></tr>
    <tr><td>Operating income</td><td>81,453</td><td>32,972</td></tr>
    <tr><td>Net income</td><td>72,880</td><td>29,760</td></tr>
    <tr><td>Diluted earnings per share</td><td>2.94</td><td>1.19</td></tr></table>
    # Consolidated Balance Sheets
    <table><tr><td>January 26</td><td>2025</td><td>2024</td></tr>
    <tr><td>Total assets</td><td>111,601</td><td>65,728</td></tr>
    <tr><td>Total liabilities</td><td>32,274</td><td>22,750</td></tr>
    <tr><td>Total stockholders' equity</td><td>79,327</td><td>42,978</td></tr></table>
    # Consolidated Statements of Cash Flows
    <table><tr><td>Year ended January 26</td><td>2025</td><td>2024</td></tr>
    <tr><td>Net cash provided by operating activities</td><td>64,089</td><td>28,090</td></tr>
    <tr><td>Net cash used in investing activities</td><td>(20,421)</td><td>(10,566)</td></tr>
    <tr><td>Net cash used in financing activities</td><td>(43,005)</td><td>(13,633)</td></tr>
    <tr><td>Cash and cash equivalents at end of period</td><td>8,589</td><td>7,280</td></tr></table>
    """
    task = {
        "task_id": "us-quality",
        "filename": "NVIDIA-CORP_US_NVDA_2025-01-26_10-K_2025-02-26_sec.pdf",
        "submit_config": {"market": "US"},
    }

    report = app._build_quality_report(markdown, task)

    candidate_names = [item["name"] for item in report["core_financial_table_candidates"]]
    assert report["market_profile"] == "US"
    assert report["accounting_standard"] == "US GAAP / SEC"
    assert "Consolidated Statements of Operations" in candidate_names
    assert "Consolidated Balance Sheets" in candidate_names
    assert "Consolidated Statements of Cash Flows" in candidate_names
    assert "资产负债表" not in candidate_names
    assert "Financial Statements" in report["found_sections"]
