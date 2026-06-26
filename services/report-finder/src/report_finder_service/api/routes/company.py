from fastapi import APIRouter

from report_finder_service.models.schemas import ResolveCompanyRequest, ResolveCompanyResponse
from report_finder_service.services.orchestrator import ReportFinderOrchestrator

router = APIRouter()
orchestrator = ReportFinderOrchestrator()


@router.post("/v1/resolve", response_model=ResolveCompanyResponse)
def resolve_company(req: ResolveCompanyRequest) -> ResolveCompanyResponse:
    return orchestrator.resolve_company(
        company_name=req.company_name,
        ticker=req.ticker,
        exchange_hint=req.exchange_hint,
    )
