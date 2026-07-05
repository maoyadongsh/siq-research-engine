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


def test_eu_quality_report_uses_europe_profile_tables_and_sections():
    markdown = """
    # Strategic Report
    # Governance
    # Financial Statements
    # Consolidated income statement
    <table><tr><td>Year ended 31 December</td><td>Notes</td><td>2025 £m</td><td>2024 £m</td></tr>
    <tr><td>Revenue</td><td>2.1</td><td>9,081</td><td>8,579</td></tr>
    <tr><td>Operating profit</td><td></td><td>2,127</td><td>1,463</td></tr>
    <tr><td>Profit before tax</td><td></td><td>1,969</td><td>1,258</td></tr>
    <tr><td>Income tax expense</td><td></td><td>(463)</td><td>(337)</td></tr>
    <tr><td>Profit for the year</td><td></td><td>1,506</td><td>921</td></tr></table>
    # Consolidated balance sheet
    <table><tr><td>At 31 December</td><td>Notes</td><td>2025£m</td><td>2024£m</td></tr>
    <tr><td>Assets</td><td></td><td></td><td></td></tr>
    <tr><td>Total assets</td><td></td><td>796,704</td><td>732,819</td></tr>
    <tr><td>Liabilities</td><td></td><td></td><td></td></tr>
    <tr><td>Total liabilities</td><td></td><td>774,536</td><td>707,666</td></tr>
    <tr><td>Net assets</td><td></td><td>22,168</td><td>25,153</td></tr>
    <tr><td>Equity</td><td></td><td></td><td></td></tr>
    <tr><td>Total equity</td><td></td><td>22,168</td><td>25,153</td></tr></table>
    # Consolidated cash flow statement
    <table><tr><td>Year ended 31 December</td><td>Notes</td><td>2025£m</td><td>2024£m</td></tr>
    <tr><td>Operating activities</td><td></td><td></td><td></td></tr>
    <tr><td>Net cash flows from operating activities</td><td></td><td>3,622</td><td>3,396</td></tr>
    <tr><td>Investing activities</td><td></td><td></td><td></td></tr>
    <tr><td>Net cash flows used in investing activities</td><td></td><td>(2,046)</td><td>(1,279)</td></tr>
    <tr><td>Financing activities</td><td></td><td></td><td></td></tr>
    <tr><td>Net cash flows used in financing activities</td><td></td><td>(1,061)</td><td>(2,164)</td></tr>
    <tr><td>Cash and cash equivalents at 31 December</td><td></td><td>3,949</td><td>3,475</td></tr></table>
    """
    task = {
        "task_id": "eu-quality",
        "filename": "London-Stock-Exchange-Group-plc_EU_LSEG_2025-12-31_annual.pdf",
        "submit_config": {"market": "EU"},
    }

    report = app._build_quality_report(markdown, task)

    candidate_names = [item["name"] for item in report["core_financial_table_candidates"]]
    assert report["market_profile"] == "EU"
    assert report["accounting_standard"] == "IFRS / EU local GAAP"
    assert "Consolidated Income Statement" in candidate_names
    assert "Consolidated Statement of Financial Position" in candidate_names
    assert "Consolidated Statement of Cash Flows" in candidate_names
    assert "资产负债表" not in candidate_names
    assert "重要提示" not in report["missing_sections"]
    assert "Financial Statements" in report["found_sections"]
