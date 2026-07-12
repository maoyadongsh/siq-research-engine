from __future__ import annotations

import hmac
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from . import __version__
from .contracts import financial_checks_contract, financial_data_contract
from .extraction import extract_artifact
from .industry_profiles import list_industry_profiles
from .load_plan import build_load_plan
from .markets import list_market_modules
from .markets.cn.routes import router as cn_market_router
from .models import ExtractionResult, ParsedArtifact, ProcessRequest
from .operating_metrics import list_operating_metric_rules
from .pipeline import process_contract
from .registry import list_profiles
from .rules import HK_LABEL_RULES, JP_CONCEPT_RULES, JP_LABEL_RULES, KR_CONCEPT_RULES, KR_LABEL_RULES, US_CONCEPT_RULES
from .storage import list_storage_profiles
from .validation import validate_extraction

SERVICE_TOKEN_ENV = "SIQ_MARKET_REPORT_RULES_TOKEN"
SERVICE_TOKEN_HEADER = "X-SIQ-Service-Token"
DEPLOYMENT_PROFILE_ENV = "SIQ_DEPLOYMENT_PROFILE"
PROTECTED_DEPLOYMENT_PROFILES = frozenset({"production", "prod", "docker"})


def _configured_service_token() -> str:
    return os.environ.get(SERVICE_TOKEN_ENV, "").strip()


def validate_internal_service_auth() -> None:
    profile = os.environ.get(DEPLOYMENT_PROFILE_ENV, "local").strip().lower()
    if profile in PROTECTED_DEPLOYMENT_PROFILES and not _configured_service_token():
        raise RuntimeError(f"{SERVICE_TOKEN_ENV} must be set when {DEPLOYMENT_PROFILE_ENV}={profile}.")


def require_service_token(request: Request) -> None:
    service_token = _configured_service_token()
    if not service_token:
        return
    provided_token = request.headers.get(SERVICE_TOKEN_HEADER, "").strip()
    if not hmac.compare_digest(provided_token, service_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    validate_internal_service_auth()
    yield


app = FastAPI(
    title="Market Report Rules Service",
    version=__version__,
    description="Market-isolated extraction, validation, provenance, and load-plan rules service.",
    lifespan=lifespan,
)
app.include_router(cn_market_router)


@app.middleware("http")
async def require_internal_service_token(request: Request, call_next) -> Response:
    if request.url.path == "/healthz":
        return await call_next(request)
    try:
        require_service_token(request)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)


@app.get("/healthz")
def healthz() -> dict[str, object]:
    return {
        "status": "ok",
        "version": __version__,
        "markets": [module.to_dict() for module in list_market_modules()],
        "profiles": [profile.model_dump(mode="json") for profile in list_profiles()],
        "storage_profiles": list_storage_profiles(),
    }


@app.get("/profiles")
def profiles() -> dict[str, object]:
    return {
        "markets": [module.to_dict() for module in list_market_modules()],
        "rule_profiles": [profile.model_dump(mode="json") for profile in list_profiles()],
        "storage_profiles": list_storage_profiles(),
        "industry_profiles": list_industry_profiles(),
    }


@app.get("/markets")
def markets() -> dict[str, object]:
    return {"markets": [module.to_dict() for module in list_market_modules()]}


@app.get("/rules")
def rules() -> dict[str, object]:
    market_modules = {module.market.value: module for module in list_market_modules()}
    return {
        "markets": {
            market: {
                "rule_count": module.rule_count,
                "parser_boundary": module.parser_boundary,
                "profile_id": module.rule_profile.profile_id,
            }
            for market, module in market_modules.items()
        },
        "cn_financial_rule_count": market_modules["CN"].rule_count,
        "us_financial_rule_count": len(US_CONCEPT_RULES),
        "hk_financial_rule_count": len(HK_LABEL_RULES),
        "jp_financial_rule_count": len(JP_CONCEPT_RULES) + len(JP_LABEL_RULES),
        "kr_financial_rule_count": len(KR_CONCEPT_RULES) + len(KR_LABEL_RULES),
        "operating_metric_rules": list_operating_metric_rules(),
    }


@app.post("/extract")
def extract(payload: ParsedArtifact) -> dict[str, object]:
    result = extract_artifact(payload)
    return financial_data_contract(result)


@app.post("/validate")
def validate(payload: ExtractionResult) -> dict[str, object]:
    result = validate_extraction(payload)
    return financial_checks_contract(result)


@app.post("/process")
def process(payload: ProcessRequest) -> dict[str, object]:
    return process_contract(
        payload.artifact,
        include_load_plan=payload.build_load_plan,
        package_dir=payload.package_dir,
    )


@app.post("/load-plan")
def load_plan(payload: ExtractionResult) -> dict[str, object]:
    validation = validate_extraction(payload)
    return build_load_plan(payload, validation).model_dump(mode="json")
