from datetime import date

from market_report_finder_service.models.schemas import CompanyEntity, Market, ReportTarget, ReportType
from market_report_finder_service.markets.us.client import SecClient


def test_company_candidates_from_ticker_payload_exact_ticker():
    client = SecClient()
    payload = {
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
    }

    candidates = client._company_candidates_from_ticker_payload(payload, ticker="aapl")

    assert candidates[0].ticker == "AAPL"
    assert candidates[0].cik_padded == "0000320193"
    assert candidates[0].confidence == 0.99


def test_company_candidates_from_ticker_payload_supports_chinese_us_alias():
    client = SecClient()
    payload = {
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "1": {"cik_str": 1018724, "ticker": "AMZN", "title": "Amazon.com, Inc."},
    }

    candidates = client._company_candidates_from_ticker_payload(payload, company_name="苹果")

    assert candidates[0].ticker == "AAPL"
    assert candidates[0].company_name == "Apple Inc."
    assert candidates[0].aliases == ["苹果"]


def test_company_candidates_from_ticker_payload_supports_nvidia_chinese_alias():
    client = SecClient()
    payload = {
        "0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
        "1": {"cik_str": 1018724, "ticker": "AMZN", "title": "Amazon.com, Inc."},
    }

    candidates = client._company_candidates_from_ticker_payload(payload, company_name="英伟达")

    assert candidates[0].ticker == "NVDA"
    assert candidates[0].cik == "1045810"
    assert candidates[0].confidence == 1.0
    assert candidates[0].match_reason == "foreign_alias_cik"


def test_company_candidates_from_ticker_payload_normalizes_sec_hyphen_ticker():
    client = SecClient()
    payload = {
        "0": {"cik_str": 1067983, "ticker": "BRK-B", "title": "BERKSHIRE HATHAWAY INC"},
    }

    candidates = client._company_candidates_from_ticker_payload(payload, ticker="BRK.B")

    assert candidates[0].ticker == "BRK-B"
    assert candidates[0].confidence == 0.99


def test_build_candidates_from_recent_payload_maps_sec_forms():
    client = SecClient()
    company = CompanyEntity(
        market=Market.us,
        company_id="320193",
        cik="320193",
        cik_padded="0000320193",
        ticker="AAPL",
        company_name="Apple Inc.",
    )
    recent = {
        "form": ["10-K", "10-Q", "8-K", "20-F", "6-K"],
        "accessionNumber": [
            "0000320193-25-000079",
            "0000320193-25-000050",
            "0000320193-25-000040",
            "0001104659-25-010000",
            "0001104659-25-020000",
        ],
        "filingDate": ["2025-10-31", "2025-08-01", "2025-07-01", "2025-04-20", "2025-05-20"],
        "reportDate": ["2025-09-27", "2025-06-28", "", "2024-12-31", "2025-03-31"],
        "primaryDocument": ["aapl-20250927.htm", "aapl-20250628.htm", "aapl-8k.htm", "issuer-20f.htm", "issuer-6k.htm"],
        "primaryDocDescription": ["10-K", "10-Q", "8-K", "Annual report", "Quarterly results"],
        "isInlineXBRL": [1, 1, 1, 1, 0],
    }

    candidates = client._build_candidates_from_recent_payload(company, "Apple Inc.", recent)

    assert [candidate.report_type for candidate in candidates] == [
        ReportType.form_10k,
        ReportType.form_10q,
        ReportType.form_20f,
        ReportType.form_6k,
    ]
    assert candidates[0].market == Market.us
    assert candidates[0].company_id == "320193"
    assert candidates[0].report_end == date(2025, 9, 27)
    assert candidates[0].document_url.endswith("/aapl-20250927.htm")
    assert candidates[0].landing_url.endswith("/0000320193-25-000079-index.html")


def test_allowed_forms_for_targets():
    client = SecClient()

    assert client._allowed_forms(target=ReportTarget.annual_report, forms=[]) == {"10-K", "20-F"}
    assert client._allowed_forms(target=ReportTarget.quarterly_report, forms=[]) == {"10-Q", "6-K"}
    assert client._allowed_forms(target=ReportTarget.financial_report, forms=["10-K"]) == {"10-K"}
