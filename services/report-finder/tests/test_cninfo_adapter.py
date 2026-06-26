from report_finder_service.adapters.cninfo import CninfoAdapter
from report_finder_service.models.schemas import ReportTarget, ReportType
from report_finder_service.services.company_resolver import CompanyResolver


def test_cninfo_build_candidate_filters_summary_and_builds_pdf_url():
    adapter = CninfoAdapter()
    company = CompanyResolver().resolve("茅台")
    stock_entry = {
        "code": "600519",
        "orgId": "gssh0600519",
        "stock": "600519,gssh0600519",
        "name": "贵州茅台",
    }

    summary = adapter._build_candidate(
        company,
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

    candidate = adapter._build_candidate(
        company,
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
    assert candidate.report_end.isoformat() == "2025-12-31"
    assert candidate.document_url == "https://static.cninfo.com.cn/finalpage/2026-04-17/1225114741.PDF"
    assert "announcementId=1225114741" in candidate.landing_url


def test_cninfo_plate_mapping():
    adapter = CninfoAdapter()
    assert adapter._plate_for_company(CompanyResolver().resolve("茅台")) == "sh"


def test_cninfo_financial_scope_queries_all_formal_categories():
    adapter = CninfoAdapter()
    categories = adapter._categories_for_target(ReportTarget.financial_report)
    assert (ReportType.annual, "category_ndbg_szsh") in categories
    assert (ReportType.semiannual, "category_bndbg_szsh") in categories
    assert (ReportType.q1, "category_yjdbg_szsh") in categories
    assert (ReportType.q3, "category_sjdbg_szsh") in categories
