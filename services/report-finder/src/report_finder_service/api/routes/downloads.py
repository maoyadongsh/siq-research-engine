from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import FileResponse

from report_finder_service.core.config import settings
from report_finder_service.models.schemas import (
    BatchDownloadRequest,
    BatchDownloadResponse,
    DirectReportDownloadRequest,
    DirectReportDownloadResponse,
    LatestReportDownloadRequest,
    LatestReportDownloadResponse,
    SingleDownloadRequest,
)
from report_finder_service.services.orchestrator import ReportFinderOrchestrator

router = APIRouter()
orchestrator = ReportFinderOrchestrator()


@router.post("/v1/reports/latest/download")
def download_latest_report(req: LatestReportDownloadRequest):
    result = orchestrator.find_latest_report_and_download(
        company_name=req.company_name,
        ticker=req.ticker,
        exchange_hint=req.exchange_hint,
        target=req.target,
    )
    downloaded = result.downloaded_file
    return FileResponse(
        path=downloaded.saved_path,
        media_type=downloaded.content_type or "application/pdf",
        filename=downloaded.file_name,
        headers={
            "X-Company-Name": quote(result.resolved.canonical_name),
            "X-Ticker": result.resolved.ticker,
            "X-Report-End": str(result.selected.report_end),
            "X-Published-At": str(result.selected.published_at),
            "X-Report-Title": quote(result.selected.title),
            "X-Document-Url": result.selected.document_url,
            "X-Cache-Hit": str(downloaded.cache_hit).lower(),
        },
    )


@router.post("/v1/reports/download")
def download_single_report(req: SingleDownloadRequest):
    downloaded = orchestrator.download_single(
        company_name=req.company_name,
        document_url=req.document_url,
        title=req.title,
        sub_dir=req.company_name,
    )
    return FileResponse(
        path=downloaded.saved_path,
        media_type=downloaded.content_type or "application/pdf",
        filename=downloaded.file_name,
        headers={
            "X-Company-Name": quote(req.company_name),
            "X-Document-Url": req.document_url,
            "X-Cache-Hit": str(downloaded.cache_hit).lower(),
        },
    )


@router.post("/v1/reports/batch-download", response_model=BatchDownloadResponse)
def download_batch_reports(req: BatchDownloadRequest) -> BatchDownloadResponse:
    """已知URL批量下载，按公司名分子目录存放到 downloads/"""
    return orchestrator.download_batch(
        items=req.items,
        default_company_name=req.default_company_name,
    )


@router.post("/v1/reports/direct-download")
def direct_download_report(req: DirectReportDownloadRequest):
    result = orchestrator.download_direct_official_report(
        company_name=req.company_name,
        document_url=req.document_url,
        landing_url=req.landing_url,
        source_name=req.source_name,
        report_type=req.report_type,
        report_end=req.report_end,
        published_at=req.published_at,
    )
    downloaded = result.downloaded_file
    return FileResponse(
        path=downloaded.saved_path,
        media_type=downloaded.content_type or "application/pdf",
        filename=downloaded.file_name,
        headers={
            "X-Company-Name": quote(result.company_name),
            "X-Report-Type": result.report_type.value if hasattr(result.report_type, "value") else str(result.report_type),
            "X-Report-End": str(result.report_end),
            "X-Published-At": str(result.published_at),
            "X-Document-Url": result.document_url,
        },
    )
