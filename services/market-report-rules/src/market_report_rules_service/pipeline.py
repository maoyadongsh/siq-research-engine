from __future__ import annotations

from .contracts import financial_checks_contract, financial_data_contract
from .extraction import extract_artifact
from .load_plan import build_load_plan
from .models import ParsedArtifact, ProcessResult
from .quality_gate_adapter import apply_package_quality_gates
from .validation import validate_extraction


def process_artifact(
    artifact: ParsedArtifact,
    *,
    include_load_plan: bool = True,
    package_dir: str | None = None,
) -> ProcessResult:
    extraction = extract_artifact(artifact)
    validation = validate_extraction(extraction)
    validation = apply_package_quality_gates(validation, package_dir=package_dir)
    load_plan = build_load_plan(extraction, validation) if include_load_plan else None
    return ProcessResult(extraction=extraction, validation=validation, load_plan=load_plan)


def process_contract(
    artifact: ParsedArtifact,
    *,
    include_load_plan: bool = True,
    package_dir: str | None = None,
) -> dict[str, object]:
    result = process_artifact(artifact, include_load_plan=include_load_plan, package_dir=package_dir)
    return {
        "financial_data": financial_data_contract(result.extraction),
        "financial_checks": financial_checks_contract(result.validation),
        "load_plan": result.load_plan.model_dump(mode="json") if result.load_plan else None,
    }
