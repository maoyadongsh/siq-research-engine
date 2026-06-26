from fastapi import APIRouter

from report_finder_service.models.schemas import HealthResponse, SourceCatalogResponse
from report_finder_service.services.orchestrator import ReportFinderOrchestrator

router = APIRouter()
orchestrator = ReportFinderOrchestrator()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="report-finder-service")


@router.get("/v1/sources", response_model=SourceCatalogResponse)
def list_sources() -> SourceCatalogResponse:
    return orchestrator.describe_sources()
