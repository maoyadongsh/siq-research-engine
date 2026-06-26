from fastapi import APIRouter

from report_finder_service.models.schemas import (
    LatestReportRequest,
    LatestReportResponse,
    RecentReportsRequest,
    RecentReportsResponse,
    SelectiveDownloadRequest,
    SelectiveDownloadResponse,
)
from report_finder_service.services.orchestrator import ReportFinderOrchestrator

router = APIRouter()
orchestrator = ReportFinderOrchestrator()


@router.post("/v1/reports/latest", response_model=LatestReportResponse)
def latest_report(req: LatestReportRequest) -> LatestReportResponse:
    return orchestrator.find_latest_report(
        company_name=req.company_name,
        ticker=req.ticker,
        exchange_hint=req.exchange_hint,
        target=req.target,
    )


@router.post("/v1/reports/recent", response_model=RecentReportsResponse)
def recent_reports(req: RecentReportsRequest) -> RecentReportsResponse:
    return orchestrator.list_recent_reports(
        company_name=req.company_name,
        ticker=req.ticker,
        exchange_hint=req.exchange_hint,
        target=req.target,
        report_year=req.report_year,
        include_earnings=req.include_earnings,
        limit=req.limit,
    )


@router.post("/v1/reports/select-download", response_model=SelectiveDownloadResponse)
def download_selected_reports(req: SelectiveDownloadRequest) -> SelectiveDownloadResponse:
    """
    一站式：传入公司名 + 报告类型 → 自动查询 → 筛选 → 下载到 downloads/
    返回JSON确认哪些文件已下载到本地
    示例 report_types: ["annual", "semiannual"]  # 年报 + 半年报
    """
    return orchestrator.download_selected(
        company_name=req.company_name,
        ticker=req.ticker,
        exchange_hint=req.exchange_hint,
        report_types=req.report_types,
        reports=req.reports,
        report_year=req.report_year,
    )
