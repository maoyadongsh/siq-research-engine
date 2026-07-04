from datetime import date

from market_report_finder_service.markets.cn.client import CninfoClient
from market_report_finder_service.markets.cn.service import CnReportFinder
from market_report_finder_service.models.schemas import CompanyEntity, Market, ReportFamily, ReportTarget, ReportType
from market_report_finder_service.services.orchestrator import ReportFinderOrchestrator


def _cn_company() -> CompanyEntity:
    return CompanyEntity(
        market=Market.cn,
        company_id="600519",
        ticker="600519",
        company_name="贵州茅台",
        exchange="SSE",
        metadata={"org_id": "gssh0600519"},
    )


def test_cninfo_build_candidate_filters_summary_and_builds_pdf_url():
    client = CninfoClient()
    stock_entry = {
        "code": "600519",
        "orgId": "gssh0600519",
        "stock": "600519,gssh0600519",
        "name": "贵州茅台",
    }

    summary = client._build_candidate(
        _cn_company(),
        stock_entry,
        {
            "announcementTitle": "贵州茅台2025年年度报告摘要",
            "adjunctUrl": "finalpage/2026-04-17/1225114731.PDF",
            "announcementTime": 1776355200000,
            "announcementId": "1225114731",
            "orgId": "gssh0600519",
            "secCode": "600519",
            "secName": "贵州茅台",
            "adjunctType": "PDF",
        },
        ReportType.annual,
    )
    assert summary is None

    candidate = client._build_candidate(
        _cn_company(),
        stock_entry,
        {
            "announcementTitle": "贵州茅台2025年年度报告",
            "adjunctUrl": "finalpage/2026-04-17/1225114741.PDF",
            "announcementTime": 1776355200000,
            "announcementId": "1225114741",
            "orgId": "gssh0600519",
            "secCode": "600519",
            "secName": "贵州茅台",
            "adjunctType": "PDF",
        },
        ReportType.annual,
    )

    assert candidate is not None
    assert candidate.market == Market.cn
    assert candidate.company_id == "600519"
    assert candidate.report_family == ReportFamily.annual
    assert candidate.report_end == date(2025, 12, 31)
    assert candidate.document_url == "https://static.cninfo.com.cn/finalpage/2026-04-17/1225114741.PDF"
    assert "announcementId=1225114741" in candidate.landing_url


def test_cninfo_plate_mapping():
    client = CninfoClient()
    assert client._plate_for_company(_cn_company()) == "sh"


def test_cninfo_financial_scope_queries_all_formal_categories():
    client = CninfoClient()
    categories = client._categories_for_target(target=ReportTarget.financial_report, forms=[])
    assert (ReportType.annual, "category_ndbg_szsh") in categories
    assert (ReportType.semiannual, "category_bndbg_szsh") in categories
    assert (ReportType.q1, "category_yjdbg_szsh") in categories
    assert (ReportType.q3, "category_sjdbg_szsh") in categories


def test_cn_finder_maps_report_types_to_forms():
    finder = CnReportFinder()

    assert finder.forms_for_report_types(["annual", "semiannual", "q1", "q3"]) == [
        "annual",
        "semiannual",
        "q1",
        "q3",
    ]


def test_orchestrator_infers_cn_from_six_digit_ticker_and_cninfo_url():
    assert ReportFinderOrchestrator._infer_market(market=None, ticker="600519", company_id=None, cik=None) == Market.cn
    assert ReportFinderOrchestrator._infer_market(market=None, ticker=None, company_id=None, cik=None, company_name="茅台") == Market.cn
    assert (
        ReportFinderOrchestrator._infer_market_from_url_or_identifier(
            document_url="https://static.cninfo.com.cn/finalpage/2026-04-17/1225114741.PDF",
            ticker=None,
            company_id=None,
        )
        == Market.cn
    )


def test_orchestrator_infers_eu_from_new_curated_report_domain():
    assert (
        ReportFinderOrchestrator._infer_market_from_url_or_identifier(
            document_url="https://www.hsbc.com/-/files/hsbc/investors/hsbc-results/2025/annual/pdfs/hsbc-holdings-plc/260225-annual-report-and-accounts-2025.pdf",
            ticker=None,
            company_id=None,
        )
        == Market.eu
    )
