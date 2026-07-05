from datetime import date

import pytest

from market_report_finder_service.markets.jp.client import EdinetClient
from market_report_finder_service.models.schemas import CompanyEntity, Market, ReportFamily, ReportType


def _jp_company() -> CompanyEntity:
    return CompanyEntity(
        market=Market.jp,
        company_id="E02144",
        ticker="72030",
        company_name="トヨタ自動車株式会社",
        exchange="JPX",
    )


def test_edinet_resolves_sec_code_from_rows():
    client = EdinetClient()
    rows = [
        {"edinetCode": "E02144", "secCode": "72030", "filerName": "トヨタ自動車株式会社", "formCode": "030000"},
        {"edinetCode": "E01777", "secCode": "67580", "filerName": "ソニーグループ株式会社", "formCode": "030000"},
    ]

    candidates = client._company_candidates(rows, company_name=None, ticker="7203", company_id=None)

    assert candidates[0].market == Market.jp
    assert candidates[0].company_id == "E02144"
    assert candidates[0].ticker == "72030"
    assert candidates[0].confidence == 0.99


def test_edinet_resolves_common_japanese_company_without_api_scan():
    client = EdinetClient()

    resolved, candidates = client.resolve_company(company_name="铠侠", ticker="285A", company_id=None)

    assert resolved.market == Market.jp
    assert resolved.ticker == "285A0"
    assert resolved.company_name.startswith("キオクシア")
    assert resolved.match_reason == "offline_common_company"
    assert candidates == [resolved]


def test_edinet_builds_pdf_candidate():
    client = EdinetClient()
    row = {
        "docID": "S100TEST",
        "edinetCode": "E02144",
        "secCode": "72030",
        "filerName": "トヨタ自動車株式会社",
        "docDescription": "有価証券報告書－第121期(2024/04/01－2025/03/31)",
        "submitDateTime": "2025-06-24 15:00",
        "formCode": "030000",
        "periodEnd": "2025-03-31",
    }

    candidate = client._build_candidate(_jp_company(), row, ReportType.annual, ReportFamily.annual)

    assert candidate is not None
    assert candidate.market == Market.jp
    assert candidate.source_id == "edinet"
    assert candidate.source_domain == "api.edinet-fsa.go.jp"
    assert candidate.report_type == ReportType.annual
    assert candidate.report_end == date(2025, 3, 31)
    assert candidate.document_url == "https://api.edinet-fsa.go.jp/api/v2/documents/S100TEST?type=2"
    assert "Subscription-Key" not in candidate.document_url
    assert candidate.form == "yuho"


def test_edinet_matches_catalog_company_id_to_five_digit_sec_code():
    client = EdinetClient()
    company = CompanyEntity(
        market=Market.jp,
        company_id="JP:7974",
        ticker="7974",
        company_name="Nintendo Co., Ltd.",
        exchange="JPX",
    )
    row = {"edinetCode": "E02367", "secCode": "79740", "filerName": "任天堂株式会社", "formCode": "030000"}

    assert client._row_matches_company(row, company) is True


def test_edinet_score_accepts_listing_code_carried_in_company_id():
    client = EdinetClient()
    rows = [
        {"edinetCode": "E02367", "secCode": "79740", "filerName": "任天堂株式会社", "formCode": "030000"},
    ]

    candidates = client._company_candidates(rows, company_name=None, ticker=None, company_id="JP:7974")

    assert candidates[0].ticker == "79740"
    assert candidates[0].match_reason == "edinet_sec_code_from_company_id"


def test_edinet_uses_catalog_report_end_to_limit_year_scan():
    client = EdinetClient()
    company = CompanyEntity(
        market=Market.jp,
        company_id="JP:7751",
        ticker="7751",
        company_name="Canon Inc.",
        exchange="JPX",
        metadata={"catalog_report_end": "2025-12-31", "catalog_published_at": "2026-03-31"},
    )
    windows = client._company_filing_windows(company, allowed={ReportType.annual}, report_year=2025)

    assert windows == [
        (date(2026, 3, 17), date(2026, 4, 14)),
        (date(2026, 2, 14), date(2026, 5, 10)),
    ]


def test_edinet_ignores_late_auxiliary_published_at_when_limiting_year_scan():
    client = EdinetClient()
    company = CompanyEntity(
        market=Market.jp,
        company_id="JP:7203",
        ticker="7203",
        company_name="Toyota Motor Corporation",
        exchange="JPX",
        metadata={"catalog_report_end": "2025-03-31", "catalog_published_at": "2026-04-03"},
    )
    windows = client._company_filing_windows(company, allowed={ReportType.annual}, report_year=2025)

    assert windows == [(date(2025, 5, 15), date(2025, 8, 8))]


def test_edinet_infers_report_types():
    client = EdinetClient()

    assert client._infer_report_type({"formCode": "030000", "docDescription": "有価証券報告書"}) == (
        ReportType.annual,
        ReportFamily.annual,
    )
    assert client._infer_report_type({"formCode": "043000", "docDescription": "四半期報告書"}) == (
        ReportType.quarterly,
        ReportFamily.quarterly,
    )


def test_edinet_infers_report_end_from_last_title_date():
    client = EdinetClient()

    report_end = client._infer_report_end(
        "有価証券報告書－第85期(2024/04/01－2025/03/31)",
        ReportType.annual,
        date(2025, 6, 26),
    )

    assert report_end == date(2025, 3, 31)


def test_edinet_requires_api_key_for_remote_document_scan(monkeypatch):
    client = EdinetClient()
    monkeypatch.setattr("market_report_finder_service.markets.jp.client.settings.edinet_api_key", None)

    with pytest.raises(ValueError, match="EDINET_API_KEY is required"):
        client._document_rows(date(2025, 1, 1))
