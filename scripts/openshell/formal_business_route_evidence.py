#!/usr/bin/env python3
"""Strict validator and current-source bindings for normal formal business-route evidence."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker


SCHEMA_VERSION = "siq.openshell.formal-business-route-evidence.v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_RELATIVE = Path("infra/openshell/schemas/formal-business-route-evidence.schema.json")
PRODUCER_RELATIVE = Path("scripts/openshell/build_formal_business_route_receipt.py")
VALIDATOR_RELATIVE = Path("scripts/openshell/formal_business_route_evidence.py")
EVALUATOR_RELATIVE = Path("scripts/openshell/run_siq_analysis_ab_eval.py")
PREPARER_RELATIVE = Path("scripts/openshell/prepare_siq_analysis_ab_eval.py")
LIFECYCLE_RELATIVE = Path("scripts/openshell/siq_analysis_lifecycle.py")
RUNTIME_CONTRACT_RELATIVE = Path("scripts/openshell/formal_runtime_contract.py")


class BusinessRouteEvidenceError(RuntimeError):
    def __init__(self, code: str) -> None:
        rendered = code if re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,95}", code) else "business_route_invalid"
        self.code = rendered
        super().__init__(rendered)


def source_sha256(root: Path, relative: Path) -> str:
    try:
        path = root / relative
        content = path.read_bytes()
    except OSError as exc:
        raise BusinessRouteEvidenceError("business_route_source_invalid") from exc
    if not content or len(content) > 16 * 1024 * 1024 or path.is_symlink() or not path.is_file():
        raise BusinessRouteEvidenceError("business_route_source_invalid")
    return hashlib.sha256(content).hexdigest()


def validate_evidence(payload: Mapping[str, Any], *, schema_bytes: bytes | None = None) -> None:
    try:
        schema = json.loads(schema_bytes) if schema_bytes is not None else json.loads(
            (REPO_ROOT / SCHEMA_RELATIVE).read_bytes()
        )
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(dict(payload))
    except Exception as exc:
        raise BusinessRouteEvidenceError("business_route_contract_invalid") from exc
    runtime = payload["routes"]["model_runtime"]
    routes = payload["routes"]
    if (
        runtime["execution_count"] != runtime["terminal_completed_count"]
        or runtime["execution_count"] != runtime["task_success_count"]
        or runtime["configured_provider"] != runtime["effective_provider"]
        or runtime["configured_model"] != runtime["effective_model"]
        or routes["analysis_crud"]["case_id"] != "workflow_analysis_roundtrip"
        or routes["session_continuity"]["case_id"] != "workflow_session_continuity"
    ):
        raise BusinessRouteEvidenceError("business_route_semantics_invalid")


def validate_bindings(
    payload: Mapping[str, Any],
    *,
    root: Path,
    summary: Mapping[str, Any],
    summary_sha256: str,
    raw_sha256: str,
    prerequisites_sha256: str,
    provenance_report: Mapping[str, Any],
    provenance_sha256: str,
) -> None:
    validate_evidence(payload)
    arms = provenance_report.get("arms")
    openshell = arms.get("openshell") if isinstance(arms, dict) else None
    attestation = provenance_report.get("runtime_attestation")
    runtime = payload["routes"]["model_runtime"]
    if (
        not isinstance(openshell, dict)
        or not isinstance(attestation, dict)
        or payload.get("evaluation_id") != summary.get("evaluation_id")
        or payload.get("dataset_sha256") != summary.get("dataset_sha256")
        or payload.get("dataset_sha256") != provenance_report.get("dataset_sha256")
        or payload.get("normal_summary_sha256") != summary_sha256
        or payload.get("normal_raw_results_sha256") != raw_sha256
        or payload.get("prerequisites_sha256") != prerequisites_sha256
        or payload.get("provenance_sha256") != provenance_sha256
        or payload["transaction"]["image_id"] != openshell.get("image_id")
        or payload["transaction"]["policy_sha256"] != openshell.get("policy_sha256")
        or payload["transaction"]["mount_plan_sha256"] != openshell.get("mount_plan_sha256")
        or payload["transaction"]["mount_contract_sha256"] != openshell.get("mount_contract_sha256")
        or payload["transaction"]["runtime_config_sha256"] != openshell.get("runtime_config_sha256")
        or runtime["configured_provider"] != attestation.get("primary_provider")
        or runtime["configured_model"] != attestation.get("primary_model")
    ):
        raise BusinessRouteEvidenceError("business_route_binding_invalid")
    sources = {
        "evidence_schema_sha256": SCHEMA_RELATIVE,
        "producer_sha256": PRODUCER_RELATIVE,
        "validator_sha256": VALIDATOR_RELATIVE,
        "evaluator_sha256": EVALUATOR_RELATIVE,
        "preparer_sha256": PREPARER_RELATIVE,
        "lifecycle_sha256": LIFECYCLE_RELATIVE,
        "runtime_contract_sha256": RUNTIME_CONTRACT_RELATIVE,
    }
    if any(payload["provenance"].get(field) != source_sha256(root, path) for field, path in sources.items()):
        raise BusinessRouteEvidenceError("business_route_source_drift")
