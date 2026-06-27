from __future__ import annotations

from .contracts import financial_checks_contract, financial_data_contract
from .extraction import extract_artifact
from .load_plan import build_load_plan
from .models import ParsedArtifact, ProcessResult
from .validation import validate_extraction


def process_artifact(artifact: ParsedArtifact, *, include_load_plan: bool = True) -> ProcessResult:
    extraction = extract_artifact(artifact)
    validation = validate_extraction(extraction)
    load_plan = build_load_plan(extraction, validation) if include_load_plan else None
    return ProcessResult(extraction=extraction, validation=validation, load_plan=load_plan)


def process_contract(artifact: ParsedArtifact, *, include_load_plan: bool = True) -> dict[str, object]:
    result = process_artifact(artifact, include_load_plan=include_load_plan)
    return {
        "financial_data": financial_data_contract(result.extraction),
        "financial_checks": financial_checks_contract(result.validation),
        "load_plan": result.load_plan.model_dump(mode="json") if result.load_plan else None,
    }
