from datetime import date

from market_report_finder_service.models.schemas import CompanyEntity, Market, ReportFamily, ReportTarget, ReportType
from market_report_finder_service.markets.hk.client import HkexClient


def _hk_company() -> CompanyEntity:
    return CompanyEntity(
        market=Market.hk,
        company_id="00700",
        ticker="00700",
        company_name="TENCENT",
        exchange="HKEX",
        hkex_stock_id="806",
    )


def test_hkex_resolves_ticker_from_rows():
    client = HkexClient()
    rows = [
        {"i": "7609", "c": "00700", "s": "15418", "n": "TENCENT", "_status": "active"},
        {"i": "1000015694", "c": "09988", "s": "281", "n": "BABA-W"},
    ]

    candidates = client._company_candidates_from_rows(rows, ticker="HK:700")

    assert candidates[0].market == Market.hk
    assert candidates[0].ticker == "00700"
    assert candidates[0].hkex_stock_id == "7609"
    assert candidates[0].metadata["legacy_stock_id"] == "15418"


def test_hkex_resolves_common_chinese_alias_from_rows():
    client = HkexClient()
    rows = [
        {"i": "198419", "c": "03690", "s": "11639", "n": "MEITUAN-W", "_status": "active"},
        {"i": "7609", "c": "00700", "s": "15418", "n": "TENCENT", "_status": "active"},
    ]

    candidates = client._company_candidates_from_rows(rows, company_name="美团")

    assert candidates[0].ticker == "03690"
    assert candidates[0].company_name == "MEITUAN-W"
    assert candidates[0].hkex_stock_id == "198419"


def test_hkex_resolves_official_traditional_chinese_catalog_alias():
    client = HkexClient()
    rows = [
        {
            "i": "1",
            "c": "00001",
            "s": "3829",
            "n": "CKH HOLDINGS",
            "_localized_names": ["長和"],
            "_status": "active",
        }
    ]

    candidates = client._company_candidates_from_rows(rows, company_name="長和")

    assert candidates[0].ticker == "00001"
    assert candidates[0].company_name == "CKH HOLDINGS"
    assert "長和" in candidates[0].aliases
    assert candidates[0].metadata["localized_names"] == ["長和"]


def test_hkex_builds_annual_interim_and_quarterly_candidates():
    client = HkexClient()
    payload = {
        "result": """[
          {
            "NEWS_ID":"1",
            "SHORT_TEXT":"Financial Statements/ESG Information - [Annual Report]",
            "LONG_TEXT":"Financial Statements/ESG Information - [Annual Report]",
            "STOCK_NAME":"TENCENT",
            "TITLE":"2025 Annual Report",
            "FILE_TYPE":"PDF",
            "DATE_TIME":"16/04/2026 18:42",
            "FILE_LINK":"/listedco/listconews/sehk/2026/0416/2026041601346.pdf"
          },
          {
            "NEWS_ID":"2",
            "SHORT_TEXT":"Financial Statements/ESG Information - [Interim/Half-Year Report]",
            "LONG_TEXT":"Financial Statements/ESG Information - [Interim/Half-Year Report]",
            "STOCK_NAME":"TENCENT",
            "TITLE":"2025 Interim Report",
            "FILE_TYPE":"PDF",
            "DATE_TIME":"20/09/2025 18:00",
            "FILE_LINK":"/listedco/listconews/sehk/2025/0920/2025092000002.pdf"
          },
          {
            "NEWS_ID":"3",
            "SHORT_TEXT":"Announcements and Notices - [Quarterly Results]",
            "LONG_TEXT":"Announcements and Notices - [Quarterly Results]",
            "STOCK_NAME":"TENCENT",
            "TITLE":"ANNOUNCEMENT OF THE RESULTS FOR THE THREE MONTHS ENDED 31 MARCH 2025",
            "FILE_TYPE":"PDF",
            "DATE_TIME":"14/05/2025 18:00",
            "FILE_LINK":"/listedco/listconews/sehk/2025/0514/2025051400003.pdf"
          }
        ]"""
    }

    candidates = client._build_candidates(_hk_company(), payload)

    assert [candidate.report_type for candidate in candidates] == [
        ReportType.annual,
        ReportType.semiannual,
        ReportType.quarterly,
    ]
    assert candidates[0].report_end == date(2025, 12, 31)
    assert candidates[1].report_family == ReportFamily.semiannual
    assert candidates[2].report_end == date(2025, 3, 31)


def test_hkex_ignores_notice_letters_that_mention_annual_report():
    client = HkexClient()
    payload = {
        "result": """[
          {
            "NEWS_ID":"11888001",
            "SHORT_TEXT":"Circulars - [Other]",
            "LONG_TEXT":"Circulars - [Other]",
            "STOCK_NAME":"SHK PPT",
            "TITLE":"Notification Letter and Request Form to Non-registered Shareholder - Notice of Publication of 2024/25 Annual Report",
            "FILE_TYPE":"PDF",
            "DATE_TIME":"08/10/2025 17:04",
            "FILE_LINK":"/listedco/listconews/sehk/2025/1008/2025100800801.pdf"
          },
          {
            "NEWS_ID":"11872506",
            "SHORT_TEXT":"Financial Statements/ESG Information - [Annual Report]",
            "LONG_TEXT":"Financial Statements/ESG Information - [Annual Report]",
            "STOCK_NAME":"SHK PPT",
            "TITLE":"2024/25 Annual Report",
            "FILE_TYPE":"PDF",
            "DATE_TIME":"08/10/2025 16:52",
            "FILE_LINK":"/listedco/listconews/sehk/2025/1008/2025100800798.pdf"
          }
        ]"""
    }

    candidates = client._build_candidates(_hk_company(), payload)

    assert len(candidates) == 1
    assert candidates[0].accession_number == "11872506"
    assert candidates[0].report_type == ReportType.annual
    assert candidates[0].report_end == date(2025, 6, 30)


def test_hkex_parses_report_end_from_ended_phrase():
    assert HkexClient._parse_report_end_from_title(
        "ANNOUNCEMENT OF THE RESULTS FOR THE THREE AND NINE MONTHS ENDED 30 SEPTEMBER 2025"
    ) == date(2025, 9, 30)


def test_hkex_allowed_types_by_target():
    client = HkexClient()

    assert client._allowed_types(target=ReportTarget.annual_report, forms=[], include_earnings=False) == {ReportType.annual}
    assert client._allowed_types(target=ReportTarget.semiannual_report, forms=[], include_earnings=False) == {ReportType.semiannual}
    assert client._allowed_types(target=ReportTarget.quarterly_report, forms=[], include_earnings=False) == {ReportType.quarterly}
    assert ReportType.earnings in client._allowed_types(target=ReportTarget.financial_report, forms=[], include_earnings=True)
