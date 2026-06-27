from market_report_finder_service.markets.jp.service import JpReportFinder
from market_report_finder_service.models.schemas import CompanyEntity, Market, ReportTarget


def test_jp_finder_resolves_and_lists_curated_issuer_report_without_edinet_key(monkeypatch):
    finder = JpReportFinder()
    monkeypatch.setattr(finder.client, "list_filings", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("EDINET_API_KEY is required for Japanese market report search")))
    monkeypatch.setattr(finder.tdnet, "list_filings", lambda *args, **kwargs: [])

    company, candidates = finder.resolve_company(ticker="7203")
    reports = finder.list_filings(
        company,
        target=ReportTarget.annual_report,
        forms=["annual"],
        include_amendments=False,
        include_earnings=False,
        report_year=2025,
    )

    assert candidates[0].company_name == "Toyota Motor Corporation"
    assert reports[0].source_id == "issuer_annual_report"
    assert reports[0].document_url.endswith("2025_001_integrated_en.pdf")
    assert reports[0].file_format == "pdf"


def test_jp_finder_does_not_fail_when_edinet_key_missing_and_tdnet_has_no_recent_match(monkeypatch):
    finder = JpReportFinder()
    company = CompanyEntity(
        market=Market.jp,
        company_id="6302",
        ticker="63020",
        company_name="住友重機械工業株式会社",
        exchange="JPX",
    )
    monkeypatch.setattr(finder.client, "list_filings", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("EDINET_API_KEY is required for Japanese market report search")))
    monkeypatch.setattr(finder.tdnet, "list_filings", lambda *args, **kwargs: [])

    result = finder.list_filings(
        company,
        target=ReportTarget.annual_report,
        forms=[],
        include_amendments=False,
        include_earnings=False,
        report_year=2025,
    )

    assert result == []
