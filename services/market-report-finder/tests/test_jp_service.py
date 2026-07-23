from market_report_finder_service.markets.jp.service import JpReportFinder
from market_report_finder_service.markets.jp.tdnet import TdnetClient
from market_report_finder_service.models.schemas import CompanyEntity, Market, ReportFamily, ReportTarget, ReportType


def test_jp_finder_resolves_but_does_not_use_integrated_report_as_annual_fallback(monkeypatch):
    finder = JpReportFinder()
    monkeypatch.setattr(finder.client, "list_filings", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("EDINET_API_KEY is required for Japanese market report search")))
    monkeypatch.setattr(finder.tdnet, "list_filings", lambda *args, **kwargs: [])

    company, candidates = finder.resolve_company(ticker="7203")
    reports = finder.list_filings(
        company,
        target=ReportTarget.annual_report,
        forms=["yuho"],
        include_amendments=False,
        include_earnings=False,
        report_year=2025,
    )

    assert candidates[0].company_name == "Toyota Motor Corporation"
    assert reports == []


def test_jp_integrated_report_requires_explicit_form(monkeypatch):
    finder = JpReportFinder()
    edinet_calls = []

    def edinet_list(*args, **kwargs):
        edinet_calls.append((args, kwargs))
        raise AssertionError("integrated-report requests must not query EDINET")

    monkeypatch.setattr(finder.client, "list_filings", edinet_list)
    monkeypatch.setattr(finder.tdnet, "list_filings", lambda *args, **kwargs: [])

    company, _ = finder.resolve_company(ticker="7203")
    reports = finder.list_filings(
        company,
        target=ReportTarget.annual_report,
        forms=["integrated-report"],
        include_amendments=False,
        include_earnings=False,
        report_year=2025,
    )

    assert reports[0].source_id == "issuer_annual_report"
    assert reports[0].form == "integrated-report"
    assert reports[0].document_url.endswith("2025_001_integrated_en.pdf")
    assert reports[0].metadata["is_primary_financial_report"] is False
    assert edinet_calls == []


def test_jp_statutory_catalog_mirror_is_allowed_without_edinet_key(monkeypatch):
    finder = JpReportFinder()
    monkeypatch.setattr(finder.client, "list_filings", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("EDINET_API_KEY is required for Japanese market report search")))
    monkeypatch.setattr(finder.tdnet, "list_filings", lambda *args, **kwargs: [])

    company, _ = finder.resolve_company(ticker="4502")
    reports = finder.list_filings(
        company,
        target=ReportTarget.annual_report,
        forms=["yuho"],
        include_amendments=False,
        include_earnings=False,
        report_year=2025,
    )

    assert reports[0].source_id == "issuer_annual_report"
    assert reports[0].form == "yuho"
    assert reports[0].metadata["jp_report_role"] == "statutory_annual_securities_report"
    assert reports[0].metadata["is_primary_financial_report"] is True


def test_jp_annual_search_skips_tdnet_when_edinet_found_statutory_report(monkeypatch):
    finder = JpReportFinder()
    company, _ = finder.resolve_company(ticker="7203")
    candidate = finder.client._build_candidate(
        company,
        {
            "docID": "S100TOYOTA",
            "edinetCode": "E02144",
            "secCode": "72030",
            "filerName": "トヨタ自動車株式会社",
            "docDescription": "有価証券報告書－第121期(2024/04/01－2025/03/31)",
            "submitDateTime": "2025-06-24 15:00",
            "formCode": "030000",
            "periodEnd": "2025-03-31",
        },
        ReportType.annual,
        ReportFamily.annual,
    )
    assert candidate is not None
    monkeypatch.setattr(finder.client, "list_filings", lambda *args, **kwargs: [candidate])
    monkeypatch.setattr(
        finder.tdnet,
        "list_filings",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("TDnet should not be scanned")),
    )

    reports = finder.list_filings(
        company,
        target=ReportTarget.annual_report,
        forms=["yuho"],
        include_amendments=False,
        include_earnings=False,
        report_year=2025,
    )

    assert [report.accession_number for report in reports] == ["S100TOYOTA"]


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


def test_jp_curated_annual_reports_only_include_statutory_primary_reports_by_default():
    finder = JpReportFinder()

    reports = finder.curated_annual_reports(report_year=2025, limit=30)

    assert len(reports) == 1
    assert reports[0].ticker == "4502"
    assert reports[0].form == "yuho"
    assert reports[0].metadata["is_primary_financial_report"] is True
    assert all(report.market == Market.jp for report in reports)
    assert all(report.source_id == "issuer_annual_report" for report in reports)
    assert all(report.file_format == "pdf" for report in reports)


def test_jp_auxiliary_catalog_still_covers_30_mainstream_companies_when_requested():
    reports = JpReportFinder().catalog.sample_filings(report_year=2025, limit=30, include_auxiliary=True)

    company_keys = {(report.company_id, report.ticker, report.company_name) for report in reports}
    assert len(reports) == 30
    assert len(company_keys) == 30
    assert sum(1 for report in reports if report.metadata["is_primary_financial_report"]) == 1


def test_jp_finder_resolves_chinese_alias_to_catalog_company():
    finder = JpReportFinder()

    company, candidates = finder.resolve_company(company_name="优衣库")

    assert company.ticker == "9983"
    assert company.company_id == "JP:9983"
    assert candidates[0].company_name == "Fast Retailing Co., Ltd."


def test_jp_tdnet_annual_target_excludes_earnings_summaries(monkeypatch):
    client = TdnetClient()
    company = CompanyEntity(
        market=Market.jp,
        company_id="JP:7203",
        ticker="7203",
        company_name="Toyota Motor Corporation",
        exchange="JPX",
    )
    rows = [
        {
            "time": "15:00",
            "code": "7203",
            "company_name": "Toyota Motor Corporation",
            "title": "2025年3月期 決算短信",
            "pdf_href": "summary.pdf",
            "published_at": "2026-05-10",
            "list_page": "I_list_001_20260510.html",
        },
        {
            "time": "15:00",
            "code": "7203",
            "company_name": "Toyota Motor Corporation",
            "title": "Annual Report 2025",
            "pdf_href": "annual.pdf",
            "published_at": "2026-06-20",
            "list_page": "I_list_001_20260620.html",
        },
    ]
    monkeypatch.setattr(client, "_scan_window", lambda report_year=None: rows)

    reports = client.list_filings(company, target=ReportTarget.annual_report, forms=[], report_year=2025)

    assert [report.report_type for report in reports] == [ReportType.annual]
    assert reports[0].document_url.endswith("annual.pdf")
