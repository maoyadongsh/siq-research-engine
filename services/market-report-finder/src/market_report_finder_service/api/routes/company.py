from fastapi import APIRouter

from market_report_finder_service.models.schemas import LegacyResolveCompanyRequest, Market, ResolveCompanyRequest, ResolveCompanyResponse
from market_report_finder_service.services.orchestrator import ReportFinderOrchestrator

router = APIRouter()
orchestrator = ReportFinderOrchestrator()


@router.post("/v1/company/resolve", response_model=ResolveCompanyResponse)
def resolve_company(req: ResolveCompanyRequest) -> ResolveCompanyResponse:
    return orchestrator.resolve_company(
        market=req.market,
        company_name=req.company_name,
        ticker=req.ticker,
        company_id=req.company_id,
        cik=req.cik,
    )


@router.post("/v1/resolve", response_model=ResolveCompanyResponse)
def legacy_resolve_company(req: LegacyResolveCompanyRequest) -> ResolveCompanyResponse:
    return orchestrator.resolve_company(
        market=_market_from_exchange_hint(req.exchange_hint),
        company_name=req.company_name,
        ticker=req.ticker,
    )


def _market_from_exchange_hint(exchange_hint: str | None) -> Market | None:
    if not exchange_hint:
        return None
    normalized = exchange_hint.strip().upper()
    if normalized in {"CN", "SSE", "SH", "SS", "SZSE", "SZ", "BSE", "BJ"}:
        return Market.cn
    if normalized in {"HK", "HKG", "HKEX"}:
        return Market.hk
    if normalized in {"KR", "KOR", "KRX"}:
        return Market.kr
    if normalized in {"JP", "JPN", "JPX", "TSE", "TYO"}:
        return Market.jp
    if normalized in {"US", "NASDAQ", "NYSE", "AMEX"}:
        return Market.us
    return None
