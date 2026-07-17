#!/usr/bin/env python3
"""Strict public evidence contract for the independent siq_analysis fallback drill."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_VERSION = "siq.openshell.formal-fallback-drill-evidence.v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_RELATIVE = Path("infra/openshell/schemas/formal-fallback-drill-evidence.schema.json")
RUNNER_RELATIVE = Path("scripts/openshell/run_siq_analysis_fallback_drill.py")
VALIDATOR_RELATIVE = Path("scripts/openshell/formal_fallback_drill_evidence.py")
LIFECYCLE_RELATIVE = Path("scripts/openshell/siq_analysis_lifecycle.py")
EVALUATOR_RELATIVE = Path("scripts/openshell/run_siq_analysis_ab_eval.py")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class FallbackEvidenceError(RuntimeError):
    """Stable validation error that does not expose evidence content."""

    def __init__(self, code: str) -> None:
        rendered = code if re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,95}", code) else "fallback_evidence_invalid"
        self.code = rendered
        super().__init__(rendered)


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def source_sha256(root: Path, relative: Path) -> str:
    path = root / relative
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise FallbackEvidenceError("fallback_evidence_source_invalid") from exc
    if not content or len(content) > 16 * 1024 * 1024 or path.is_symlink() or not path.is_file():
        raise FallbackEvidenceError("fallback_evidence_source_invalid")
    return sha256_bytes(content)


def validate_evidence(payload: Mapping[str, Any], *, schema_bytes: bytes | None = None) -> None:
    try:
        schema = json.loads(schema_bytes) if schema_bytes is not None else json.loads(
            (REPO_ROOT / SCHEMA_RELATIVE).read_bytes()
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FallbackEvidenceError("fallback_evidence_schema_invalid") from exc
    if not isinstance(schema, dict):
        raise FallbackEvidenceError("fallback_evidence_schema_invalid")
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    )
    if errors:
        raise FallbackEvidenceError("fallback_evidence_contract_invalid")

    results = payload["results"]
    transaction = payload["transaction"]
    provenance = payload["provenance"]
    if (
        results["configured_provider"] != provenance["primary_provider"]
        or results["configured_model"] != provenance["primary_model"]
        or results["configured_provider"] in results["effective_providers"]
        or results["configured_model"] in results["effective_models"]
        or transaction["host_receipt_before_sha256"] != transaction["host_receipt_after_sha256"]
        or transaction["gateway_receipt_before_sha256"] != transaction["gateway_receipt_after_sha256"]
        or transaction["api_runtime_receipt_before_sha256"] != transaction["api_runtime_receipt_after_sha256"]
    ):
        raise FallbackEvidenceError("fallback_evidence_semantics_invalid")


def validate_bindings(
    payload: Mapping[str, Any],
    *,
    root: Path,
    normal_summary: Mapping[str, Any],
    normal_summary_sha256: str,
    prerequisites_sha256: str,
    provenance_report: Mapping[str, Any],
    provenance_sha256: str,
) -> None:
    validate_evidence(payload)
    arms = provenance_report.get("arms")
    attestation = provenance_report.get("runtime_attestation")
    openshell = arms.get("openshell") if isinstance(arms, dict) else None
    if (
        not isinstance(openshell, dict)
        or not isinstance(attestation, dict)
        or payload.get("evaluation_id") != normal_summary.get("evaluation_id")
        or payload.get("dataset_sha256") != normal_summary.get("dataset_sha256")
        or payload.get("dataset_sha256") != provenance_report.get("dataset_sha256")
        or payload.get("normal_summary_sha256") != normal_summary_sha256
        or payload.get("prerequisites_sha256") != prerequisites_sha256
        or payload.get("provenance_sha256") != provenance_sha256
        or payload["transaction"]["image_id"] != openshell.get("image_id")
        or payload["transaction"]["policy_sha256"] != openshell.get("policy_sha256")
        or payload["transaction"]["mount_contract_sha256"] != openshell.get("mount_contract_sha256")
        or payload["transaction"]["runtime_config_sha256"] != openshell.get("runtime_config_sha256")
        or payload["provenance"]["fallback_route_sha256"] != attestation.get("fallback_route_sha256")
        or payload["provenance"]["primary_provider"] != attestation.get("primary_provider")
        or payload["provenance"]["primary_model"] != attestation.get("primary_model")
    ):
        raise FallbackEvidenceError("fallback_evidence_binding_invalid")
    source_fields = {
        "evidence_schema_sha256": SCHEMA_RELATIVE,
        "runner_sha256": RUNNER_RELATIVE,
        "validator_sha256": VALIDATOR_RELATIVE,
        "lifecycle_sha256": LIFECYCLE_RELATIVE,
        "evaluator_sha256": EVALUATOR_RELATIVE,
    }
    if any(payload["provenance"].get(field) != source_sha256(root, relative) for field, relative in source_fields.items()):
        raise FallbackEvidenceError("fallback_evidence_source_drift")


def load_evidence(path: Path, *, maximum: int = 1024 * 1024) -> tuple[Mapping[str, Any], str]:
    try:
        content = path.read_bytes()
        payload = json.loads(content, object_pairs_hook=_reject_duplicate_keys)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise FallbackEvidenceError("fallback_evidence_file_invalid") from exc
    if not content or len(content) > maximum or not isinstance(payload, dict):
        raise FallbackEvidenceError("fallback_evidence_file_invalid")
    validate_evidence(payload)
    return payload, sha256_bytes(content)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result
