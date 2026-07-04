from fastapi import APIRouter, Query

from market_report_finder_service.models.schemas import (
    DirectReportDownloadRequest,
    DirectReportDownloadResponse,
    BatchDownloadRequest,
    BatchDownloadResponse,
    LatestReportRequest,
    LatestReportResponse,
    RecentReportsRequest,
    RecentReportsResponse,
    ReportAssistRequest,
    ReportAssistResponse,
    SelectiveDownloadRequest,
    SelectiveDownloadResponse,
    SingleDownloadRequest,
    Market,
)
from market_report_finder_service.services.orchestrator import ReportFinderOrchestrator

router = APIRouter()
orchestrator = ReportFinderOrchestrator()


@router.post("/v1/reports/latest", response_model=LatestReportResponse)
def latest_report(req: LatestReportRequest) -> LatestReportResponse:
    return orchestrator.find_latest_report(
        market=req.market,
        company_name=req.company_name,
        ticker=req.ticker,
        company_id=req.company_id,
        cik=req.cik,
        target=req.target,
        forms=req.forms,
        include_amendments=req.include_amendments,
        include_earnings=req.include_earnings,
    )


@router.post("/v1/reports/recent", response_model=RecentReportsResponse)
def recent_reports(req: RecentReportsRequest) -> RecentReportsResponse:
    return orchestrator.list_recent_reports(
        market=req.market,
        company_name=req.company_name,
        ticker=req.ticker,
        company_id=req.company_id,
        cik=req.cik,
        target=req.target,
        report_year=req.report_year,
        forms=req.forms,
        include_amendments=req.include_amendments,
        include_earnings=req.include_earnings,
        limit=req.limit,
    )


@router.post("/v1/reports/assist", response_model=ReportAssistResponse)
def assist_reports(req: ReportAssistRequest) -> ReportAssistResponse:
    return orchestrator.assist_reports(req)


@router.get("/v1/reports/curated-annuals")
def curated_annual_reports(
    market: Market = Query(...),
    report_year: int | None = Query(default=None, ge=1900, le=2100),
    limit: int = Query(default=10, ge=1, le=50),
    country: str | None = Query(default=None, max_length=16),
):
    return orchestrator.curated_annual_reports(market=market, report_year=report_year, limit=limit, country=country)


@router.post("/v1/reports/select-download", response_model=SelectiveDownloadResponse)
def download_selected_reports(req: SelectiveDownloadRequest) -> SelectiveDownloadResponse:
    return orchestrator.download_selected(
        market=req.market,
        company_name=req.company_name,
        ticker=req.ticker,
        company_id=req.company_id,
        cik=req.cik,
        report_types=req.report_types,
        forms=req.forms,
        reports=req.reports,
        report_year=req.report_year,
        include_amendments=req.include_amendments,
        include_earnings=req.include_earnings,
    )


@router.post("/v1/reports/batch-download", response_model=BatchDownloadResponse)
def batch_download_reports(req: BatchDownloadRequest) -> BatchDownloadResponse:
    return orchestrator.download_batch(
        items=req.items,
        default_company_name=req.default_company_name,
        market=req.market,
    )


@router.post("/v1/reports/download")
def download_single_report(req: SingleDownloadRequest):
    return orchestrator.download_single(
        market=req.market,
        company_name=req.company_name,
        ticker=req.ticker,
        document_url=req.document_url,
        title=req.title,
    )


@router.post("/v1/reports/direct-download", response_model=DirectReportDownloadResponse)
def direct_download_report(req: DirectReportDownloadRequest) -> DirectReportDownloadResponse:
    return orchestrator.download_direct(req)
