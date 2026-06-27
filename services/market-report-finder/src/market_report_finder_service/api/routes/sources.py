from fastapi import APIRouter

from market_report_finder_service.models.schemas import SourceCatalogResponse
from market_report_finder_service.services.orchestrator import ReportFinderOrchestrator

router = APIRouter()
orchestrator = ReportFinderOrchestrator()


@router.get("/v1/sources", response_model=SourceCatalogResponse)
def sources() -> SourceCatalogResponse:
    return orchestrator.describe_sources()
