from __future__ import annotations

from fastapi import FastAPI

from . import __version__
from .contracts import financial_checks_contract, financial_data_contract
from .extraction import extract_artifact
from .industry_profiles import list_industry_profiles
from .load_plan import build_load_plan
from .markets.cn.routes import router as cn_market_router
from .markets import list_market_modules
from .models import ExtractionResult, ParsedArtifact, ProcessRequest
from .operating_metrics import list_operating_metric_rules
from .pipeline import process_artifact, process_contract
from .registry import list_profiles
from .rules import HK_LABEL_RULES, JP_CONCEPT_RULES, JP_LABEL_RULES, KR_CONCEPT_RULES, KR_LABEL_RULES, US_CONCEPT_RULES
from .storage import list_storage_profiles
from .validation import validate_extraction


app = FastAPI(
    title="Market Report Rules Service",
    version=__version__,
    description="Market-isolated extraction, validation, provenance, and load-plan rules service.",
)
app.include_router(cn_market_router)


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
    return process_contract(payload.artifact, include_load_plan=payload.build_load_plan)


@app.post("/load-plan")
def load_plan(payload: ExtractionResult) -> dict[str, object]:
    validation = validate_extraction(payload)
    return build_load_plan(payload, validation).model_dump(mode="json")
