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
    assert candidate.report_type == ReportType.annual
    assert candidate.report_end == date(2025, 3, 31)
    assert candidate.document_url.endswith("/S100TEST?type=2")
    assert "Subscription-Key" not in candidate.document_url


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


def test_edinet_requires_api_key_for_remote_document_scan(monkeypatch):
    client = EdinetClient()
    monkeypatch.setattr("market_report_finder_service.markets.jp.client.settings.edinet_api_key", None)

    with pytest.raises(ValueError, match="EDINET_API_KEY is required"):
        client._document_rows(date(2025, 1, 1))
