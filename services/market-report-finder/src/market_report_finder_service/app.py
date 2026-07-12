import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from market_report_finder_service import __version__
from market_report_finder_service.api.routes import company_router, health_router, reports_router, sources_router
from market_report_finder_service.core.config import settings

PROTECTED_DEPLOYMENT_PROFILES = frozenset({"production", "prod", "docker"})


def validate_internal_service_auth() -> None:
    profile = settings.deployment_profile.strip().lower()
    if profile in PROTECTED_DEPLOYMENT_PROFILES and _configured_internal_service_token() is None:
        raise RuntimeError(f"SIQ_MARKET_REPORT_FINDER_TOKEN must be set when SIQ_DEPLOYMENT_PROFILE={profile}.")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    validate_internal_service_auth()
    yield


app = FastAPI(
    title="Market Report Finder Service",
    version=__version__,
    description="Standalone backend for resolving companies and downloading official market filings.",
    lifespan=lifespan,
)

app.include_router(health_router)
app.include_router(sources_router)
app.include_router(company_router)
app.include_router(reports_router)


@app.middleware("http")
async def require_internal_service_token(request: Request, call_next):
    if _requires_internal_service_token(request.url.path):
        expected_token = _configured_internal_service_token()
        if expected_token is not None:
            actual_token = _request_internal_service_token(request)
            if actual_token is None or not secrets.compare_digest(actual_token, expected_token):
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)


@app.get("/")
def index():
    return {
        "service": "market-report-finder-service",
        "version": __version__,
        "docs": "/docs",
        "health": "/health",
    }


def _requires_internal_service_token(path: str) -> bool:
    return path == "/v1" or path.startswith("/v1/")


def _configured_internal_service_token() -> str | None:
    token = settings.internal_service_token
    if token is None:
        return None
    token = token.strip()
    return token or None


def _request_internal_service_token(request: Request) -> str | None:
    token = request.headers.get("x-siq-service-token")
    return token.strip() if token and token.strip() else None
