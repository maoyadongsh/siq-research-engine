#!/usr/bin/env python3
"""Evaluate the SIQ OpenShell V0.6 completion definition from explicit evidence.

This is a read-only release gate.  It never infers a live business result from
unit tests or the provider-independent probe, and it never starts/stops a
service.  Without ``--require-go`` a NO_GO report is an expected diagnostic
result and exits successfully.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts.openshell import (
        build_memory_write_evidence as memory_write_evidence,
        check_sanitized_artifacts,
        check_siq_analysis_ab_prerequisites as ab_prerequisites,
        check_tracked_state,
        formal_business_route_evidence,
        formal_fallback_drill_evidence,
        prepare_siq_analysis_ab_eval as ab_prepare,
        run_formal_delete_guard as formal_delete_evidence,
        run_formal_filesystem_boundary as formal_filesystem_evidence,
        run_formal_host_rollback as formal_host_rollback_evidence,
        run_siq_analysis_ab_eval as ab_eval,
    )
except ModuleNotFoundError:  # direct execution from scripts/openshell
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.openshell import (
        build_memory_write_evidence as memory_write_evidence,
        check_sanitized_artifacts,
        check_siq_analysis_ab_prerequisites as ab_prerequisites,
        check_tracked_state,
        formal_business_route_evidence,
        formal_fallback_drill_evidence,
        prepare_siq_analysis_ab_eval as ab_prepare,
        run_formal_delete_guard as formal_delete_evidence,
        run_formal_filesystem_boundary as formal_filesystem_evidence,
        run_formal_host_rollback as formal_host_rollback_evidence,
        run_siq_analysis_ab_eval as ab_eval,
    )


SCHEMA_VERSION = "siq.openshell.v0.6-completion.v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_READINESS = Path("artifacts/openshell/v0.6/readiness.json")
DEFAULT_SERVICE = Path("artifacts/openshell/v0.6/service-preflight.sanitized.json")
DEFAULT_FORMAL_HOST_ROLLBACK = Path("artifacts/openshell/v0.6/formal-host-rollback.sanitized.json")
DEFAULT_FORMAL_DELETE_GUARD = Path("artifacts/openshell/v0.6/formal-delete-guard.sanitized.json")
DEFAULT_FORMAL_FILESYSTEM_BOUNDARY = Path("artifacts/openshell/v0.6/formal-filesystem-boundary.sanitized.json")
DEFAULT_HOST_EGRESS_COMPONENT = Path("artifacts/openshell/v0.6/egress-boundary.sanitized.json")
DEFAULT_FORMAL_EGRESS_SANDBOX = Path("artifacts/openshell/v0.6/formal-egress-sandbox.sanitized.json")
DEFAULT_FORMAL_STRUCTURED_AUDIT = Path("artifacts/openshell/v0.6/formal-structured-audit.sanitized.json")
DEFAULT_FORMAL_FALLBACK_DRILL = Path("artifacts/openshell/v0.6/formal-fallback-drill.sanitized.json")
DEFAULT_FORMAL_BUSINESS_ROUTE = Path("artifacts/openshell/v0.6/formal-business-route.sanitized.json")
DEFAULT_MEMORY_WRITE_EVIDENCE = Path("artifacts/openshell/v0.6/memory-write-evidence.sanitized.json")
DEFAULT_AB_ROOT = Path("var/openshell/eval")
REQUIRED_EVIDENCE = (
    Path("artifacts/openshell/v0.6/baseline.json"),
    Path("artifacts/openshell/v0.6/readiness.json"),
    Path("artifacts/openshell/v0.6/immutable-registry.sanitized.json"),
    Path("artifacts/openshell/v0.6/service-preflight.sanitized.json"),
)
REQUIRED_RUNBOOKS = (
    Path("docs/runbooks/hermes-upgrade-freeze.md"),
    Path("docs/runbooks/openshell/README.md"),
    Path("docs/runbooks/openshell/broker-request-identity.md"),
    Path("docs/runbooks/openshell/egress-boundary-proof.md"),
    Path("docs/runbooks/openshell/formal-filesystem-boundary.md"),
    Path("docs/runbooks/openshell/formal-host-rollback.md"),
    Path("docs/runbooks/openshell/formal-delete-guard.md"),
    Path("docs/runbooks/openshell/git-publication-policy.md"),
    Path("docs/runbooks/openshell/memory-write-boundary.md"),
    Path("docs/runbooks/openshell/mihomo-fake-ip-egress.md"),
    Path("docs/runbooks/openshell/milvus-write-protection-proof.md"),
    Path("docs/runbooks/openshell/review-record-template.md"),
    Path("docs/runbooks/openshell/service-protocol-preflight.md"),
    Path("docs/runbooks/openshell/siq-analysis-lifecycle.md"),
    Path("docs/runbooks/openshell/siq-analysis-runtime-lifecycle-smoke.md"),
    Path("docs/runbooks/openshell/siq-analysis-wide-pilot.md"),
)
READINESS_SCHEMA = "siq.openshell.readiness.v1"
SERVICE_SCHEMA = "siq.openshell.service_preflight.v2"
SERVICE_PROTOCOL = "tcp_connect_plus_read_only_http_get"
AB_SUMMARY_SCHEMA = ab_eval.SUMMARY_SCHEMA_VERSION
AB_PREREQUISITE_SCHEMA = ab_prerequisites.SCHEMA_VERSION
AB_PROVENANCE_SCHEMA = ab_prepare.PROVENANCE_SCHEMA
FORMAL_HOST_ROLLBACK_SCHEMA = formal_host_rollback_evidence.SCHEMA_VERSION
FORMAL_DELETE_GUARD_SCHEMA = formal_delete_evidence.SCHEMA_VERSION
FORMAL_FILESYSTEM_BOUNDARY_SCHEMA = formal_filesystem_evidence.SCHEMA_VERSION
HOST_EGRESS_COMPONENT_SCHEMA = "siq.openshell.egress-boundary-proof.v1"
FORMAL_EGRESS_SANDBOX_SCHEMA = "siq.openshell.formal-egress-sandbox-evidence.v1"
FORMAL_STRUCTURED_AUDIT_SCHEMA = "siq.openshell.formal-structured-audit-evidence.v1"
FORMAL_EGRESS_SCHEMA_RELATIVE = Path("infra/openshell/schemas/formal-egress-sandbox-evidence.schema.json")
FORMAL_AUDIT_SCHEMA_RELATIVE = Path("infra/openshell/schemas/formal-structured-audit-evidence.schema.json")
FORMAL_EGRESS_EXPORTER_RELATIVE = Path("scripts/openshell/run_formal_egress_audit.py")
EGRESS_GUARD_RELATIVE = Path("scripts/openshell/egress_guard.py")
REQUEST_IDENTITY_RELATIVE = Path("scripts/openshell/broker_request_identity.py")
AUDIT_CONTRACT_RELATIVE = Path("scripts/openshell/security_audit.py")
AUDIT_AGGREGATOR_RELATIVE = Path("scripts/openshell/aggregate_security_audit.py")
ARCHITECTURE_REVIEW_SCHEMA = "siq.openshell.architecture-security-review.v1"
HERMES_COMMIT = "ddb8d8fa842283ef651a6e4514f8f561f736c72e"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
MAX_EVIDENCE_AGE = timedelta(hours=24)
FUTURE_TIMESTAMP_TOLERANCE = timedelta(minutes=5)
MAX_RUNBOOK_BYTES = 2 * 1024 * 1024
REVIEW_CHECKLIST_FIELDS = frozenset(
    {
        "immutable_paths_protected",
        "normal_writes_preserved",
        "unknown_uploads_denied",
        "delete_guard_recovery_verified",
        "database_boundaries_verified",
        "host_rollback_verified",
        "quality_ab_approved",
        "git_publication_safe",
    }
)
REVIEW_EVIDENCE_FIELDS = frozenset(
    {
        "readiness_sha256",
        "service_preflight_sha256",
        "formal_ab_summary_sha256",
        "formal_fallback_drill_sha256",
        "formal_host_rollback_sha256",
        "formal_delete_guard_sha256",
        "formal_egress_sandbox_sha256",
        "formal_structured_audit_sha256",
    }
)

SERVICE_SPECS = {
    "qwen_local": (8004, "optional"),
    "gemma_local": (8006, "optional"),
    "nemotron_local": (8007, "optional"),
    "embedding": (8013, "required"),
    "postgres": (15432, "required"),
    "milvus": (19530, "required"),
    "siq_api": (18081, "required"),
    "hermes_host": (18651, "required"),
}
SERVICE_PROTOCOL_SPECS = {
    "qwen_local": ("openai_models_list_v1", "/v1/models"),
    "gemma_local": ("openai_models_list_v1", "/v1/models"),
    "nemotron_local": ("openai_models_list_v1", "/v1/models"),
    "embedding": ("openai_models_list_v1", "/v1/models"),
    "siq_api": ("status_ok_json_v1", "/health"),
    "hermes_host": ("status_ok_json_v1", "/health"),
}
SERVICE_CONNECTIVITY_BLOCKERS = {
    "qwen_local": "qwen_local_unreachable",
    "gemma_local": "gemma_local_unreachable",
    "nemotron_local": "nemotron_local_unreachable",
    "embedding": "embedding_service_unreachable",
    "postgres": "postgres_unreachable",
    "milvus": "milvus_unreachable",
    "siq_api": "siq_api_unreachable",
    "hermes_host": "hermes_host_unreachable",
}
SERVICE_PROTOCOL_BLOCKERS = {
    "qwen_local": "qwen_local_protocol_unavailable",
    "gemma_local": "gemma_local_protocol_unavailable",
    "nemotron_local": "nemotron_local_protocol_unavailable",
    "embedding": "embedding_service_protocol_unavailable",
    "siq_api": "siq_api_protocol_unavailable",
    "hermes_host": "hermes_host_protocol_unavailable",
}
SERVICE_SECURITY_CHECKS = {"postgres_readonly_identity", "milvus_write_protection"}
AB_ARM_RATE_FIELDS = (
    "task_success_rate",
    "answer_citation_rate",
    "numeric_accuracy",
    "hallucination_block_rate",
    "evidence_coverage",
    "tool_success_rate",
    "tool_error_rate",
    "tool_retry_rate",
    "tool_recovery_rate",
    "tool_unrecovered_failure_rate",
    "fallback_success_rate",
    "fallback_telemetry_coverage",
    "report_completeness",
    "timeout_rate",
    "policy_false_positive_rate",
)
AB_ARM_COUNT_FIELDS = (
    "fallback_expected_execution_count",
    "fallback_telemetry_expected_count",
    "contract_failure_count",
    "unexpected_fallback_count",
)
AB_ARM_LATENCY_FIELDS = (
    "ttft_sample_count",
    "ttft_p50",
    "ttft_p95",
    "total_sample_count",
    "total_p50",
    "total_p95",
)
AB_COMPARISON_METRICS = (
    "task_success_rate",
    "answer_citation_rate",
    "numeric_accuracy",
    "hallucination_block_rate",
    "evidence_coverage",
    "tool_success_rate",
    "tool_error_rate",
    "tool_retry_rate",
    "tool_recovery_rate",
    "tool_unrecovered_failure_rate",
    "fallback_success_rate",
    "fallback_telemetry_coverage",
    "report_completeness",
    "timeout_rate",
    "policy_false_positive_rate",
)
FORMAL_DELETE_MECHANISMS = frozenset({"shell_rm", "python_shutil", "node_fs"})
FORMAL_SANITIZATION_FIELDS = frozenset(
    {
        "contains_api_keys",
        "contains_headers",
        "contains_prompt_or_input",
        "contains_raw_output",
        "contains_local_paths",
        "exporter_ready",
    }
)
FORMAL_TRANSACTION_FIELDS = frozenset(
    {
        "transaction_receipt_sha256",
        "run_id_sha256",
        "sandbox_id_sha256",
        "session_id_sha256",
        "policy_sha256",
        "audit_records_sha256",
    }
)
HOST_EGRESS_CASES = {
    "public_get": ("allow", "unknown_safe_read"),
    "public_head": ("allow", "unknown_safe_read"),
    "unknown_small_json": ("audit_only", "unknown_json_post_audit"),
    "unknown_multipart": ("deny", "broker_multipart_denied"),
    "unknown_octet_stream": ("deny", "broker_octet_stream_denied"),
    "unknown_put": ("deny", "broker_method_denied"),
    "cloud_metadata": ("deny", "ssrf_non_public_ip"),
}
FORMAL_EGRESS_CASES = {
    "public_get": ("allow", "egress_broker", "unknown_safe_read"),
    "public_head": ("allow", "egress_broker", "unknown_safe_read"),
    "unknown_small_json": ("audit_only", "egress_broker", "unknown_json_post_audit"),
    "unknown_multipart": ("deny", "egress_guard", "broker_multipart_denied"),
    "unknown_octet_stream": ("deny", "egress_guard", "broker_octet_stream_denied"),
    "unknown_put": ("deny", "egress_guard", "broker_method_denied"),
    "oversized_unknown_body": ("deny", "egress_guard", "json_body_too_large"),
    "presigned_object_upload": ("deny", "egress_guard", "approved_destination_rule_mismatch"),
    "curl_upload": ("deny", "sandbox_network_policy", "direct_egress_denied"),
    "scp": ("deny", "sandbox_network_policy", "direct_egress_denied"),
    "sftp": ("deny", "sandbox_network_policy", "direct_egress_denied"),
    "rsync": ("deny", "sandbox_network_policy", "direct_egress_denied"),
    "rclone": ("deny", "sandbox_network_policy", "direct_egress_denied"),
    "cloud_metadata": ("deny", "egress_guard", "ssrf_non_public_ip"),
    "direct_public_tcp": ("deny", "sandbox_network_policy", "direct_egress_denied"),
    "direct_public_udp": ("deny", "sandbox_network_policy", "direct_egress_denied"),
    "direct_public_websocket": ("deny", "sandbox_network_policy", "direct_egress_denied"),
}
FORMAL_AUDIT_OPERATION_CLASSES = frozenset(
    {
        "database.query",
        "filesystem.delete",
        "filesystem.write",
        "immutable.write",
        "network.request",
        "publisher.index",
        "runtime.route",
        "sandbox.lifecycle",
        "service.preflight",
    }
)
FORMAL_AUDIT_CONTENT_ABSENCE_FIELDS = frozenset(
    {
        "contains_api_keys",
        "contains_headers",
        "contains_prompt_or_input",
        "contains_raw_output",
        "contains_local_paths",
        "contains_request_or_response_content",
        "contains_sql_or_vector_payload",
        "contains_target_values",
        "contains_unprojected_session_id",
        "exporter_ready",
    }
)
AB_PREREQUISITE_EVIDENCE_NAMES = frozenset({"provider_inventory", "service_report", "broker_report"})
AB_PREREQUISITE_EVIDENCE_FIELDS = frozenset(
    {
        "path",
        "sha256",
        "size_bytes",
        "device",
        "inode",
        "mode",
        "mtime_ns",
        "ctime_ns",
        "generated_at",
        "expires_at",
    }
)


class CompletionConfigurationError(RuntimeError):
    """Stable configuration error without source content or machine paths."""


def _rooted(root: Path, value: Path) -> Path:
    candidate = value if value.is_absolute() else root / value
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise CompletionConfigurationError("evidence_path_missing") from exc
    if root.resolve(strict=True) not in (resolved, *resolved.parents):
        raise CompletionConfigurationError("evidence_path_outside_project")
    current = Path(resolved.anchor)
    for component in resolved.parts[1:]:
        current /= component
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                raise CompletionConfigurationError("evidence_path_symlink")
        except FileNotFoundError as exc:
            raise CompletionConfigurationError("evidence_path_missing") from exc
    if not resolved.is_file():
        raise CompletionConfigurationError("evidence_path_regular_file_required")
    return resolved


def _read_evidence(path: Path, *, max_bytes: int = 8 * 1024 * 1024) -> tuple[bytes, str]:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.geteuid()
            or info.st_size > max_bytes
        ):
            raise CompletionConfigurationError("evidence_file_identity_invalid")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, max_bytes - total + 1))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise CompletionConfigurationError("evidence_file_too_large")
        content = b"".join(chunks)
    except CompletionConfigurationError:
        raise
    except OSError as exc:
        raise CompletionConfigurationError("evidence_file_unreadable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return content, hashlib.sha256(content).hexdigest()


def _json_with_digest(
    path: Path,
    *,
    max_bytes: int = 8 * 1024 * 1024,
) -> tuple[Mapping[str, Any], str]:
    content, digest = _read_evidence(path, max_bytes=max_bytes)
    try:
        payload = json.loads(content)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CompletionConfigurationError("evidence_json_invalid") from exc
    if not isinstance(payload, dict):
        raise CompletionConfigurationError("evidence_json_object_required")
    return payload, digest


def _json(path: Path, *, max_bytes: int = 8 * 1024 * 1024) -> Mapping[str, Any]:
    return _json_with_digest(path, max_bytes=max_bytes)[0]


def _is_int(value: Any, *, minimum: int = 0, maximum: int | None = None) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= minimum
        and (maximum is None or value <= maximum)
    )


def _is_number(value: Any, *, minimum: float | None = None, maximum: float | None = None) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return False
    number = float(value)
    return (minimum is None or number >= minimum) and (maximum is None or number <= maximum)


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _matches_current_source(root: Path, value: Any, relative: Path) -> bool:
    if not _is_sha256(value):
        return False
    try:
        _, digest = _read_evidence(_rooted(root, relative), max_bytes=8 * 1024 * 1024)
    except CompletionConfigurationError:
        return False
    return value == digest


def _is_safe_id(value: Any) -> bool:
    return isinstance(value, str) and SAFE_ID_RE.fullmatch(value) is not None


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _is_fresh_timestamp(value: Any, *, now: datetime | None = None) -> bool:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return False
    current = now or datetime.now(timezone.utc)
    return current - MAX_EVIDENCE_AGE <= parsed <= current + FUTURE_TIMESTAMP_TOLERANCE


def _is_historical_evidence_timestamp(value: Any, *, readiness_generated_at: Any) -> bool:
    """Accept durable historical evidence, but never evidence created after readiness."""

    generated_at = _parse_timestamp(value)
    readiness_at = _parse_timestamp(readiness_generated_at)
    return generated_at is not None and readiness_at is not None and generated_at <= readiness_at


def _validate_formal_sanitization(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != FORMAL_SANITIZATION_FIELDS:
        return False
    return (
        all(
            value.get(key) is False
            for key in (
                "contains_api_keys",
                "contains_headers",
                "contains_prompt_or_input",
                "contains_raw_output",
                "contains_local_paths",
            )
        )
        and value.get("exporter_ready") is True
    )


def _validate_formal_filesystem_boundary(
    payload: Mapping[str, Any],
    *,
    root: Path,
    readiness_generated_at: Any,
) -> bool:
    """Validate formal filesystem evidence against the currently tracked producer."""

    if payload.get("schema_version") != FORMAL_FILESYSTEM_BOUNDARY_SCHEMA or not _is_historical_evidence_timestamp(
        payload.get("generated_at"),
        readiness_generated_at=readiness_generated_at,
    ):
        return False
    try:
        schema_path = _rooted(root, formal_filesystem_evidence.SCHEMA_RELATIVE)
        schema_bytes, schema_sha256 = _read_evidence(schema_path, max_bytes=1024 * 1024)
        formal_filesystem_evidence.validate_evidence(payload, schema_bytes=schema_bytes)

        source_bindings = {
            "probe_module_sha256": formal_filesystem_evidence.PROBE_MODULE_RELATIVE,
            "lifecycle_sha256": formal_filesystem_evidence.LIFECYCLE_RELATIVE,
            "transaction_module_sha256": formal_filesystem_evidence.TRANSACTION_RELATIVE,
            "mount_contract_module_sha256": formal_filesystem_evidence.MOUNT_CONTRACT_RELATIVE,
            "runner_sha256": formal_filesystem_evidence.RUNNER_RELATIVE,
        }
        current_source_sha256 = {
            key: _read_evidence(_rooted(root, relative), max_bytes=8 * 1024 * 1024)[1]
            for key, relative in source_bindings.items()
        }
    except (
        CompletionConfigurationError,
        formal_filesystem_evidence.FormalFilesystemEvidenceError,
        OSError,
        ValueError,
    ):
        return False

    provenance = payload.get("provenance")
    transaction_value = payload.get("transaction")
    mount_contract = payload.get("mount_contract")
    if not isinstance(provenance, dict) or not isinstance(transaction_value, dict):
        return False
    if not isinstance(mount_contract, dict):
        return False
    expected_probe_sha256 = hashlib.sha256(
        formal_filesystem_evidence.sandbox_probe.FILESYSTEM_PROBE.encode("utf-8")
    ).hexdigest()
    return (
        provenance.get("evidence_schema_sha256") == schema_sha256
        and provenance.get("filesystem_probe_sha256") == expected_probe_sha256
        and all(provenance.get(key) == digest for key, digest in current_source_sha256.items())
        and provenance.get("policy_sha256") == transaction_value.get("policy_sha256")
        and _is_sha256(mount_contract.get("mount_plan_sha256"))
    )


def _validate_formal_transaction(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == FORMAL_TRANSACTION_FIELDS
        and all(_is_sha256(value.get(key)) for key in FORMAL_TRANSACTION_FIELDS)
    )


def _formal_runtime_provenance_matches(
    filesystem: Mapping[str, Any],
    host_rollback: Mapping[str, Any],
    delete_guard: Mapping[str, Any],
    egress: Mapping[str, Any],
) -> bool:
    filesystem_provenance = filesystem.get("provenance")
    filesystem_mount = filesystem.get("mount_contract")
    host_provenance = host_rollback.get("provenance")
    delete_provenance = delete_guard.get("provenance")
    egress_provenance = egress.get("provenance")
    if not all(
        isinstance(value, dict)
        for value in (
            filesystem_provenance,
            filesystem_mount,
            host_provenance,
            delete_provenance,
            egress_provenance,
        )
    ):
        return False
    policy_values = {
        filesystem_provenance.get("policy_sha256"),
        host_provenance.get("policy_sha256"),
        delete_provenance.get("policy_sha256"),
        egress_provenance.get("policy_sha256"),
    }
    image_values = {
        filesystem_provenance.get("image_sha256"),
        host_provenance.get("image_sha256"),
        delete_provenance.get("image_sha256"),
        egress_provenance.get("image_sha256"),
    }
    mount_values = {
        filesystem_mount.get("mount_contract_sha256"),
        host_provenance.get("mount_contract_sha256"),
        delete_provenance.get("mount_contract_sha256"),
        egress_provenance.get("mount_contract_sha256"),
    }
    return all(
        len(values) == 1 and _is_sha256(next(iter(values))) for values in (policy_values, image_values, mount_values)
    )


def _validate_formal_fallback_drill(
    payload: Mapping[str, Any],
    *,
    root: Path,
    readiness_generated_at: Any,
    summary: Mapping[str, Any],
    summary_sha256: str,
    prerequisites: Mapping[str, Any],
    prerequisites_sha256: str,
) -> bool:
    if not _is_historical_evidence_timestamp(
        payload.get("generated_at"),
        readiness_generated_at=readiness_generated_at,
    ):
        return False
    try:
        schema_bytes, schema_sha256 = _read_evidence(
            _rooted(root, formal_fallback_drill_evidence.SCHEMA_RELATIVE),
            max_bytes=1024 * 1024,
        )
        formal_fallback_drill_evidence.validate_evidence(payload, schema_bytes=schema_bytes)
    except (
        CompletionConfigurationError,
        formal_fallback_drill_evidence.FallbackEvidenceError,
        OSError,
        ValueError,
    ):
        return False
    provenance = payload.get("provenance")
    prerequisite_provenance = prerequisites.get("provenance")
    if (
        not isinstance(provenance, dict)
        or not isinstance(prerequisite_provenance, dict)
        or provenance.get("evidence_schema_sha256") != schema_sha256
        or payload.get("evaluation_id") != summary.get("evaluation_id")
        or payload.get("dataset_sha256") != summary.get("dataset_sha256")
        or payload.get("normal_summary_sha256") != summary_sha256
        or payload.get("prerequisites_sha256") != prerequisites_sha256
        or payload.get("provenance_sha256") != prerequisite_provenance.get("sha256")
    ):
        return False
    bindings = {
        "runner_sha256": formal_fallback_drill_evidence.RUNNER_RELATIVE,
        "validator_sha256": formal_fallback_drill_evidence.VALIDATOR_RELATIVE,
        "lifecycle_sha256": formal_fallback_drill_evidence.LIFECYCLE_RELATIVE,
        "evaluator_sha256": formal_fallback_drill_evidence.EVALUATOR_RELATIVE,
    }
    return all(_matches_current_source(root, provenance.get(key), relative) for key, relative in bindings.items())


def _formal_fallback_runtime_provenance_matches(
    fallback: Mapping[str, Any],
    filesystem: Mapping[str, Any],
    host_rollback: Mapping[str, Any],
    delete_guard: Mapping[str, Any],
    egress: Mapping[str, Any],
) -> bool:
    if not _formal_runtime_provenance_matches(filesystem, host_rollback, delete_guard, egress):
        return False
    transaction = fallback.get("transaction")
    filesystem_provenance = filesystem.get("provenance")
    filesystem_mount = filesystem.get("mount_contract")
    egress_provenance = egress.get("provenance")
    if not all(
        isinstance(value, dict)
        for value in (transaction, filesystem_provenance, filesystem_mount, egress_provenance)
    ):
        return False
    return (
        transaction.get("image_id") == f"sha256:{filesystem_provenance.get('image_sha256')}"
        and transaction.get("policy_sha256") == filesystem_provenance.get("policy_sha256")
        and transaction.get("mount_contract_sha256") == filesystem_mount.get("mount_contract_sha256")
        and transaction.get("runtime_config_sha256") == filesystem_provenance.get("runtime_config_sha256")
        and transaction.get("runtime_config_sha256") == egress_provenance.get("runtime_config_sha256")
    )


def _formal_business_runtime_provenance_matches(
    business_route: Mapping[str, Any],
    filesystem: Mapping[str, Any],
    host_rollback: Mapping[str, Any],
    delete_guard: Mapping[str, Any],
    egress: Mapping[str, Any],
) -> bool:
    if not _formal_runtime_provenance_matches(filesystem, host_rollback, delete_guard, egress):
        return False
    transaction = business_route.get("transaction")
    filesystem_provenance = filesystem.get("provenance")
    filesystem_mount = filesystem.get("mount_contract")
    egress_provenance = egress.get("provenance")
    if not all(
        isinstance(value, dict)
        for value in (transaction, filesystem_provenance, filesystem_mount, egress_provenance)
    ):
        return False
    return (
        transaction.get("image_id") == f"sha256:{filesystem_provenance.get('image_sha256')}"
        and transaction.get("policy_sha256") == filesystem_provenance.get("policy_sha256")
        and transaction.get("mount_contract_sha256") == filesystem_mount.get("mount_contract_sha256")
        and transaction.get("runtime_config_sha256") == filesystem_provenance.get("runtime_config_sha256")
        and transaction.get("runtime_config_sha256") == egress_provenance.get("runtime_config_sha256")
    )


def _validate_formal_business_route(
    payload: Mapping[str, Any],
    *,
    root: Path,
    readiness_generated_at: Any,
    summary: Mapping[str, Any],
    summary_sha256: str,
    raw_sha256: str,
    prerequisites_sha256: str,
    provenance_report: Mapping[str, Any],
    provenance_sha256: str,
) -> bool:
    if not _is_historical_evidence_timestamp(
        payload.get("generated_at"),
        readiness_generated_at=readiness_generated_at,
    ):
        return False
    try:
        schema_bytes, schema_sha256 = _read_evidence(
            _rooted(root, formal_business_route_evidence.SCHEMA_RELATIVE),
            max_bytes=1024 * 1024,
        )
        formal_business_route_evidence.validate_evidence(payload, schema_bytes=schema_bytes)
        formal_business_route_evidence.validate_bindings(
            payload,
            root=root,
            summary=summary,
            summary_sha256=summary_sha256,
            raw_sha256=raw_sha256,
            prerequisites_sha256=prerequisites_sha256,
            provenance_report=provenance_report,
            provenance_sha256=provenance_sha256,
        )
    except (
        CompletionConfigurationError,
        formal_business_route_evidence.BusinessRouteEvidenceError,
        OSError,
        ValueError,
    ):
        return False
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("evidence_schema_sha256") != schema_sha256:
        return False
    bindings = {
        "producer_sha256": formal_business_route_evidence.PRODUCER_RELATIVE,
        "validator_sha256": formal_business_route_evidence.VALIDATOR_RELATIVE,
        "evaluator_sha256": formal_business_route_evidence.EVALUATOR_RELATIVE,
        "preparer_sha256": formal_business_route_evidence.PREPARER_RELATIVE,
        "lifecycle_sha256": formal_business_route_evidence.LIFECYCLE_RELATIVE,
        "runtime_contract_sha256": formal_business_route_evidence.RUNTIME_CONTRACT_RELATIVE,
    }
    return all(_matches_current_source(root, provenance.get(key), relative) for key, relative in bindings.items())


def _validate_host_egress_component(payload: Mapping[str, Any]) -> bool:
    expected_fields = {
        "schema_version",
        "decision",
        "passed",
        "captured_at_unix",
        "valid_until_unix",
        "scope",
        "formal_business_run",
        "formal_business_sandbox_evidence",
        "readiness_effect",
        "eligible_for_completion",
        "gateway",
        "environment_binding",
        "audit_binding",
        "checks",
        "cases",
        "not_claimed",
    }
    captured_at = payload.get("captured_at_unix")
    valid_until = payload.get("valid_until_unix")
    if (
        set(payload) != expected_fields
        or payload.get("schema_version") != HOST_EGRESS_COMPONENT_SCHEMA
        or payload.get("decision") != "GO"
        or payload.get("passed") is not True
        or payload.get("scope") != "host_egress_broker"
        or payload.get("formal_business_run") is not False
        or payload.get("formal_business_sandbox_evidence") is not False
        or payload.get("readiness_effect") != "none"
        or payload.get("eligible_for_completion") is not False
        or payload.get("gateway") != "siq-openshell-dev"
        or not _is_int(captured_at, minimum=1)
        or not _is_int(valid_until, minimum=int(captured_at) + 1 if _is_int(captured_at, minimum=1) else 2)
        or int(valid_until) - int(captured_at) > 6 * 60 * 60
    ):
        return False

    environment = payload.get("environment_binding")
    environment_digest_fields = {
        "allowlist_sha256",
        "allowlist_contract_sha256",
        "audit_contract_sha256",
        "broker_lifecycle_sha256",
        "broker_identity_contract_sha256",
        "egress_decision_sha256",
        "egress_guard_sha256",
        "evidence_schema_sha256",
        "mihomo_runtime_config_sha256",
        "proof_runner_sha256",
        "runtime_source_bundle_sha256",
        "siq_fetch_sha256",
        "toolchain_manifest_sha256",
    }
    if (
        not isinstance(environment, dict)
        or set(environment)
        != {
            *environment_digest_fields,
            "request_identity_required",
            "resolver_audit_rule",
            "resolver_mode",
        }
        or any(not _is_sha256(environment.get(key)) for key in environment_digest_fields)
        or environment.get("request_identity_required") is not True
        or environment.get("resolver_audit_rule") != "mihomo_fake_ip_compat_resolved"
        or environment.get("resolver_mode") != "mihomo_fake_ip_verified"
    ):
        return False

    audit = payload.get("audit_binding")
    if (
        not isinstance(audit, dict)
        or set(audit) != {"audit_record_count", "audit_records_sha256", "run_id_sha256"}
        or not _is_int(audit.get("audit_record_count"), minimum=7, maximum=32)
        or not _is_sha256(audit.get("audit_records_sha256"))
        or not _is_sha256(audit.get("run_id_sha256"))
    ):
        return False

    checks = payload.get("checks")
    true_checks = {
        "public_get_allowed",
        "public_head_allowed",
        "missing_identity_denied",
        "wrong_audience_denied",
        "unknown_small_json_audit_only",
        "unknown_multipart_denied",
        "unknown_octet_stream_denied",
        "unknown_put_denied",
        "cloud_metadata_denied",
        "audit_records_bound",
    }
    false_checks = {
        "target_values_stored",
        "request_payloads_stored",
        "response_payloads_stored",
        "runtime_credentials_stored",
    }
    if (
        not isinstance(checks, dict)
        or set(checks) != true_checks | false_checks
        or any(checks.get(key) is not True for key in true_checks)
        or any(checks.get(key) is not False for key in false_checks)
    ):
        return False

    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) != len(HOST_EGRESS_CASES):
        return False
    seen_cases: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != {
            "case_id",
            "decision",
            "outer_http_status",
            "rule_id",
            "upstream_http_status",
        }:
            return False
        case_id = case.get("case_id")
        if case_id not in HOST_EGRESS_CASES or case_id in seen_cases:
            return False
        seen_cases.add(str(case_id))
        expected_decision, expected_rule = HOST_EGRESS_CASES[str(case_id)]
        outer_status = case.get("outer_http_status")
        upstream_status = case.get("upstream_http_status")
        if case.get("decision") != expected_decision or case.get("rule_id") != expected_rule:
            return False
        if expected_decision == "deny":
            if outer_status != 403 or upstream_status is not None:
                return False
        elif outer_status != 200 or not _is_int(upstream_status, minimum=100, maximum=599):
            return False
    return seen_cases == set(HOST_EGRESS_CASES) and payload.get("not_claimed") == [
        "formal_business_sandbox",
        "direct_transfer_client_execution",
        "provider_route_availability",
        "restart_persistence",
        "semantic_dlp",
    ]


def _validate_formal_egress_sandbox(
    payload: Mapping[str, Any],
    *,
    readiness_generated_at: Any,
    host_component: Mapping[str, Any],
    host_component_path: Path,
    host_component_digest: str,
    root: Path,
) -> bool:
    expected_fields = {
        "schema_version",
        "generated_at",
        "decision",
        "profile",
        "scope",
        "formal_business_run",
        "eligible_for_completion",
        "host_runtime_unchanged",
        "cutover_performed",
        "transaction",
        "host_egress_component",
        "sandbox_network_enforcement",
        "direct_denial_contract",
        "transfer_clients_tested",
        "cases",
        "provenance",
        "sanitization",
    }
    generated_at = _parse_timestamp(payload.get("generated_at"))
    if (
        set(payload) != expected_fields
        or payload.get("schema_version") != FORMAL_EGRESS_SANDBOX_SCHEMA
        or payload.get("decision") != "GO"
        or payload.get("profile") != "siq_analysis"
        or payload.get("scope") != "formal_business_sandbox"
        or payload.get("formal_business_run") is not True
        or payload.get("eligible_for_completion") is not True
        or payload.get("host_runtime_unchanged") is not True
        or payload.get("cutover_performed") is not False
        or generated_at is None
        or not _is_historical_evidence_timestamp(
            payload.get("generated_at"),
            readiness_generated_at=readiness_generated_at,
        )
        or not _validate_formal_transaction(payload.get("transaction"))
    ):
        return False

    component = payload.get("host_egress_component")
    if (
        not isinstance(component, dict)
        or set(component)
        != {
            "path",
            "sha256",
            "schema_version",
            "scope",
            "decision",
            "passed",
            "eligible_for_completion",
            "captured_at_unix",
            "valid_until_unix",
        }
        or component.get("path") != _relative_evidence_path(root, host_component_path)
        or component.get("sha256") != host_component_digest
        or component.get("schema_version") != HOST_EGRESS_COMPONENT_SCHEMA
        or component.get("scope") != "host_egress_broker"
        or component.get("decision") != "GO"
        or component.get("passed") is not True
        or component.get("eligible_for_completion") is not False
        or component.get("captured_at_unix") != host_component.get("captured_at_unix")
        or component.get("valid_until_unix") != host_component.get("valid_until_unix")
    ):
        return False
    generated_unix = int(generated_at.timestamp())
    if not int(component["captured_at_unix"]) <= generated_unix <= int(component["valid_until_unix"]):
        return False

    enforcement = payload.get("sandbox_network_enforcement")
    if not isinstance(enforcement, dict) or enforcement != {
        "egress_mode": "broker_and_approved_providers_only",
        "direct_public_tcp_denied": True,
        "direct_public_udp_denied": True,
        "direct_public_websocket_denied": True,
        "cloud_metadata_denied": True,
        "broker_request_identity_required": True,
        "unknown_raw_socket_route_present": False,
    }:
        return False
    if payload.get("direct_denial_contract") != {
        "receiver_binding": "verified_bridge_gateway_ephemeral_tcp_udp",
        "controlled_endpoint_permission_present": False,
        "connection_observed": False,
        "client_exit_status_only_accepted": False,
        "protocol_or_auth_failure_accepted": False,
    }:
        return False
    if payload.get("transfer_clients_tested") != ["curl_upload", "rclone", "rsync", "scp", "sftp"]:
        return False

    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) != len(FORMAL_EGRESS_CASES):
        return False
    seen_cases: set[str] = set()
    event_digests: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != {
            "case_id",
            "decision",
            "enforcement_layer",
            "reason_code",
            "audit_record_sha256",
        }:
            return False
        case_id = case.get("case_id")
        event_digest = case.get("audit_record_sha256")
        if case_id not in FORMAL_EGRESS_CASES or case_id in seen_cases or not _is_sha256(event_digest):
            return False
        if (
            tuple(case.get(key) for key in ("decision", "enforcement_layer", "reason_code"))
            != FORMAL_EGRESS_CASES[str(case_id)]
        ):
            return False
        seen_cases.add(str(case_id))
        event_digests.add(str(event_digest))
    if (
        seen_cases != set(FORMAL_EGRESS_CASES)
        or [case.get("case_id") for case in cases] != list(FORMAL_EGRESS_CASES)
        or len(event_digests) != len(FORMAL_EGRESS_CASES)
    ):
        return False

    transaction = payload["transaction"]
    provenance = payload.get("provenance")
    provenance_digest_fields = {
        "image_sha256",
        "policy_sha256",
        "mount_contract_sha256",
        "runtime_config_sha256",
        "egress_guard_sha256",
        "request_identity_contract_sha256",
        "evidence_schema_sha256",
        "exporter_sha256",
        "transaction_receipt_sha256",
    }
    if (
        not isinstance(provenance, dict)
        or set(provenance) != {"hermes_commit", *provenance_digest_fields}
        or provenance.get("hermes_commit") != HERMES_COMMIT
        or any(not _is_sha256(provenance.get(key)) for key in provenance_digest_fields)
        or provenance.get("policy_sha256") != transaction.get("policy_sha256")
        or provenance.get("transaction_receipt_sha256") != transaction.get("transaction_receipt_sha256")
        or not _matches_current_source(root, provenance.get("egress_guard_sha256"), EGRESS_GUARD_RELATIVE)
        or not _matches_current_source(
            root,
            provenance.get("request_identity_contract_sha256"),
            REQUEST_IDENTITY_RELATIVE,
        )
        or not _matches_current_source(
            root,
            provenance.get("evidence_schema_sha256"),
            FORMAL_EGRESS_SCHEMA_RELATIVE,
        )
        or not _matches_current_source(
            root,
            provenance.get("exporter_sha256"),
            FORMAL_EGRESS_EXPORTER_RELATIVE,
        )
    ):
        return False
    return _validate_formal_sanitization(payload.get("sanitization"))


def _validate_formal_audit_content_absence(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != FORMAL_AUDIT_CONTENT_ABSENCE_FIELDS:
        return False
    return (
        all(value.get(key) is False for key in FORMAL_AUDIT_CONTENT_ABSENCE_FIELDS - {"exporter_ready"})
        and value.get("exporter_ready") is True
    )


def _validate_formal_structured_audit(
    payload: Mapping[str, Any],
    *,
    root: Path,
    readiness_generated_at: Any,
    formal_egress: Mapping[str, Any],
) -> bool:
    expected_fields = {
        "schema_version",
        "generated_at",
        "decision",
        "profile",
        "scope",
        "formal_business_run",
        "eligible_for_completion",
        "host_runtime_unchanged",
        "cutover_performed",
        "transaction",
        "source_contract",
        "identity_coverage",
        "decision_counts",
        "operation_counts",
        "event_classification",
        "security_case_event_sha256",
        "metrics",
        "provenance",
        "content_absence",
    }
    generated_at = _parse_timestamp(payload.get("generated_at"))
    egress_generated_at = _parse_timestamp(formal_egress.get("generated_at"))
    if (
        set(payload) != expected_fields
        or payload.get("schema_version") != FORMAL_STRUCTURED_AUDIT_SCHEMA
        or payload.get("decision") != "GO"
        or payload.get("profile") != "siq_analysis"
        or payload.get("scope") != "formal_business_sandbox"
        or payload.get("formal_business_run") is not True
        or payload.get("eligible_for_completion") is not True
        or payload.get("host_runtime_unchanged") is not True
        or payload.get("cutover_performed") is not False
        or generated_at is None
        or egress_generated_at is None
        or generated_at < egress_generated_at
        or not _is_historical_evidence_timestamp(
            payload.get("generated_at"),
            readiness_generated_at=readiness_generated_at,
        )
        or not _validate_formal_transaction(payload.get("transaction"))
        or payload.get("transaction") != formal_egress.get("transaction")
    ):
        return False

    transaction = payload["transaction"]
    source = payload.get("source_contract")
    if (
        not isinstance(source, dict)
        or set(source)
        != {
            "record_schema_version",
            "aggregate_schema_version",
            "source_file_count",
            "record_count",
            "audit_records_sha256",
            "source_set_sha256",
            "chronological_order_verified",
            "strict_schema_validated",
            "transaction_filtered",
            "single_transaction",
            "single_policy",
        }
        or source.get("record_schema_version") != "siq.openshell.audit.v1"
        or source.get("aggregate_schema_version") != "siq.openshell.audit-summary.v1"
        or not _is_int(source.get("source_file_count"), minimum=1, maximum=256)
        or source.get("record_count") != len(FORMAL_EGRESS_CASES) + 3
        or source.get("audit_records_sha256") != transaction.get("audit_records_sha256")
        or not _is_sha256(source.get("source_set_sha256"))
        or any(
            source.get(key) is not True
            for key in (
                "chronological_order_verified",
                "strict_schema_validated",
                "transaction_filtered",
                "single_transaction",
                "single_policy",
            )
        )
    ):
        return False

    identity = payload.get("identity_coverage")
    identity_fields = {
        "profile_present",
        "sandbox_identity_projected",
        "siq_run_identity_projected",
        "session_identity_projected",
        "operation_class_present",
        "target_projected",
        "decision_present",
        "policy_digest_present",
        "error_code_present",
        "duration_present",
    }
    if (
        not isinstance(identity, dict)
        or set(identity) != identity_fields
        or any(identity.get(key) is not True for key in identity_fields)
    ):
        return False

    record_count = int(source["record_count"])
    decisions = payload.get("decision_counts")
    if (
        not isinstance(decisions, dict)
        or set(decisions) != {"allow", "deny", "audit_only"}
        or any(not _is_int(decisions.get(key)) for key in decisions)
        or sum(decisions.values()) != record_count
        or decisions != {"allow": 5, "deny": 14, "audit_only": 1}
    ):
        return False

    operations = payload.get("operation_counts")
    if (
        not isinstance(operations, dict)
        or set(operations) != FORMAL_AUDIT_OPERATION_CLASSES
        or any(not _is_int(operations.get(key)) for key in operations)
        or sum(operations.values()) != record_count
        or operations.get("network.request") != len(FORMAL_EGRESS_CASES)
        or operations.get("runtime.route") != 0
        or operations.get("sandbox.lifecycle") != 2
        or operations.get("service.preflight") != 1
        or any(
            operations.get(key) != 0
            for key in FORMAL_AUDIT_OPERATION_CLASSES
            - {"network.request", "runtime.route", "sandbox.lifecycle", "service.preflight"}
        )
    ):
        return False

    classification = payload.get("event_classification")
    if (
        not isinstance(classification, dict)
        or classification
        != {
            "formal_runner_observation_count": 3,
            "security_probe_event_count": len(FORMAL_EGRESS_CASES),
            "unclassified_count": 0,
        }
        or classification["formal_runner_observation_count"] + classification["security_probe_event_count"]
        != record_count
    ):
        return False

    event_digests = payload.get("security_case_event_sha256")
    egress_cases = formal_egress.get("cases")
    expected_event_digests = (
        {
            str(case.get("audit_record_sha256"))
            for case in egress_cases
            if isinstance(case, dict) and _is_sha256(case.get("audit_record_sha256"))
        }
        if isinstance(egress_cases, list)
        else set()
    )
    if (
        not isinstance(event_digests, list)
        or len(event_digests) != len(FORMAL_EGRESS_CASES)
        or any(not _is_sha256(item) for item in event_digests)
        or len(set(event_digests)) != len(event_digests)
        or set(event_digests) != expected_event_digests
        or event_digests != [str(case.get("audit_record_sha256")) for case in egress_cases]
    ):
        return False

    metrics = payload.get("metrics")
    if not isinstance(metrics, dict) or set(metrics) != {
        "policy_deny_count",
        "audit_only_count",
        "external_upload_blocks",
        "immutable_write_blocks",
        "sandbox_start_failures",
        "gateway_overhead_ms",
    }:
        return False
    overhead = metrics.get("gateway_overhead_ms")
    if (
        metrics.get("policy_deny_count") != decisions["deny"]
        or metrics.get("audit_only_count") != decisions["audit_only"]
        or not _is_int(metrics.get("external_upload_blocks"), minimum=10)
        or not _is_int(metrics.get("immutable_write_blocks"))
        or metrics.get("sandbox_start_failures") != 0
        or not isinstance(overhead, dict)
        or set(overhead) != {"sample_count", "p50", "p95"}
        or overhead != {"sample_count": 0, "p50": None, "p95": None}
    ):
        return False

    provenance = payload.get("provenance")
    provenance_digest_fields = {
        "audit_contract_sha256",
        "aggregator_sha256",
        "evidence_schema_sha256",
        "exporter_sha256",
        "source_file_set_sha256",
        "transaction_receipt_sha256",
    }
    if (
        not isinstance(provenance, dict)
        or set(provenance) != provenance_digest_fields
        or any(not _is_sha256(provenance.get(key)) for key in provenance_digest_fields)
        or provenance.get("source_file_set_sha256") != source.get("source_set_sha256")
        or provenance.get("transaction_receipt_sha256") != transaction.get("transaction_receipt_sha256")
        or not _matches_current_source(root, provenance.get("audit_contract_sha256"), AUDIT_CONTRACT_RELATIVE)
        or not _matches_current_source(root, provenance.get("aggregator_sha256"), AUDIT_AGGREGATOR_RELATIVE)
        or not _matches_current_source(
            root,
            provenance.get("evidence_schema_sha256"),
            FORMAL_AUDIT_SCHEMA_RELATIVE,
        )
        or not _matches_current_source(
            root,
            provenance.get("exporter_sha256"),
            FORMAL_EGRESS_EXPORTER_RELATIVE,
        )
    ):
        return False
    return _validate_formal_audit_content_absence(payload.get("content_absence"))


def _validate_formal_host_rollback(
    payload: Mapping[str, Any],
    *,
    readiness_generated_at: Any,
    root: Path = REPO_ROOT,
) -> bool:
    if not _is_historical_evidence_timestamp(
        payload.get("generated_at"),
        readiness_generated_at=readiness_generated_at,
    ):
        return False
    try:
        schema_bytes, schema_sha256 = _read_evidence(
            _rooted(root, formal_host_rollback_evidence.SCHEMA_RELATIVE),
            max_bytes=1024 * 1024,
        )
        formal_host_rollback_evidence.validate_evidence(payload, schema_bytes=schema_bytes)
    except (
        CompletionConfigurationError,
        formal_host_rollback_evidence.FormalHostRollbackError,
        OSError,
        ValueError,
    ):
        return False
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("evidence_schema_sha256") != schema_sha256:
        return False
    bindings = {
        "lifecycle_sha256": formal_host_rollback_evidence.LIFECYCLE_RELATIVE,
        "transaction_module_sha256": formal_host_rollback_evidence.TRANSACTION_RELATIVE,
        "mount_contract_module_sha256": formal_host_rollback_evidence.MOUNT_CONTRACT_RELATIVE,
        "runner_sha256": formal_host_rollback_evidence.RUNNER_RELATIVE,
        "wrapper_sha256": formal_host_rollback_evidence.ROLLBACK_WRAPPER_RELATIVE,
    }
    return all(_matches_current_source(root, provenance.get(key), relative) for key, relative in bindings.items())


def _validate_formal_delete_guard(
    payload: Mapping[str, Any],
    *,
    readiness_generated_at: Any,
    root: Path = REPO_ROOT,
) -> bool:
    if not _is_historical_evidence_timestamp(
        payload.get("generated_at"),
        readiness_generated_at=readiness_generated_at,
    ):
        return False
    try:
        schema_bytes, schema_sha256 = _read_evidence(
            _rooted(root, formal_delete_evidence.SCHEMA_RELATIVE),
            max_bytes=1024 * 1024,
        )
        formal_delete_evidence.validate_evidence(payload, schema_bytes=schema_bytes)
    except (
        CompletionConfigurationError,
        formal_delete_evidence.FormalDeleteGuardError,
        OSError,
        ValueError,
    ):
        return False
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("evidence_schema_sha256") != schema_sha256:
        return False
    bindings = {
        "lifecycle_sha256": formal_delete_evidence.LIFECYCLE_RELATIVE,
        "transaction_module_sha256": formal_delete_evidence.TRANSACTION_RELATIVE,
        "destructive_guard_sha256": formal_delete_evidence.GUARD_RELATIVE,
        "guard_worker_sha256": formal_delete_evidence.GUARD_WORKER_RELATIVE,
        "mount_contract_module_sha256": formal_delete_evidence.MOUNT_CONTRACT_RELATIVE,
        "runner_sha256": formal_delete_evidence.RUNNER_RELATIVE,
    }
    return all(_matches_current_source(root, provenance.get(key), relative) for key, relative in bindings.items())


def _relative_evidence_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise CompletionConfigurationError("evidence_path_outside_project") from exc


def _git_index_evidence_matches(root: Path, path: Path, digest: str) -> bool:
    """Require one public evidence file to equal its stage-zero Git blob."""

    try:
        relative = _relative_evidence_path(root, path)
        if not relative.startswith("artifacts/openshell/") or not _is_sha256(digest):
            return False
        indexed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--stage", "-z", "--", relative],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
        ).stdout
        rows = [row for row in indexed.split(b"\0") if row]
        if len(rows) != 1:
            return False
        metadata, indexed_path = rows[0].split(b"\t", 1)
        mode, _object_id, stage = metadata.decode("ascii").split(" ", 2)
        if mode != "100644" or stage != "0" or indexed_path.decode("utf-8") != relative:
            return False
        blob = subprocess.run(
            ["git", "-C", str(root), "show", f":{relative}"],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
        ).stdout
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        UnicodeDecodeError,
        ValueError,
    ):
        return False
    return hashlib.sha256(blob).hexdigest() == digest


def _validate_service_report(payload: Mapping[str, Any]) -> tuple[bool, bool]:
    """Validate the complete sanitized service contract, returning (valid, go)."""

    if payload.get("schema_version") != SERVICE_SCHEMA:
        return False, False
    scope = payload.get("probe_scope")
    services = payload.get("services")
    checks = payload.get("security_checks")
    blockers = payload.get("blockers")
    summary = payload.get("summary")
    if (
        not isinstance(scope, dict)
        or scope.get("protocol") != SERVICE_PROTOCOL
        or scope.get("read_only") is not True
        or scope.get("host_alias_kind") != "loopback"
        or scope.get("http_method") != "GET"
        or scope.get("request_body_sent") is not False
        or scope.get("redirects_followed") is not False
        or scope.get("response_body_recorded") is not False
        or not isinstance(services, list)
        or not isinstance(checks, list)
        or not isinstance(blockers, list)
        or not isinstance(summary, dict)
    ):
        return False, False

    by_service: dict[str, Mapping[str, Any]] = {}
    for item in services:
        if not isinstance(item, dict) or not isinstance(item.get("service_id"), str):
            return False, False
        service_id = item["service_id"]
        if service_id in by_service:
            return False, False
        by_service[service_id] = item
    if set(by_service) != set(SERVICE_SPECS) or len(services) != len(SERVICE_SPECS):
        return False, False

    expected_blocker_codes: set[str] = set()
    required_reachable = 0
    optional_reachable = 0
    warning_count = 0
    for service_id, (port, requirement) in SERVICE_SPECS.items():
        item = by_service[service_id]
        reachable = item.get("reachable")
        status = item.get("status")
        error_code = item.get("error_code")
        if not isinstance(reachable, bool) or status not in {"pass", "warning", "no_go"}:
            return False, False
        if not isinstance(error_code, str) or (status == "pass" and error_code):
            return False, False
        if status != "pass" and not error_code:
            return False, False
        if item.get("port") != port or item.get("requirement") != requirement:
            return False, False
        if "blocking" in item and item.get("blocking") is not (requirement == "required"):
            return False, False
        protocol = item.get("protocol_check")
        expected_protocol = SERVICE_PROTOCOL_SPECS.get(service_id)
        if not isinstance(protocol, dict):
            return False, False
        protocol_available = True
        if expected_protocol is None:
            if (
                protocol.get("contract") != "not_applicable"
                or protocol.get("checked") is not False
                or protocol.get("available") is not None
                or protocol.get("status") != "not_applicable"
                or protocol.get("method") != ""
                or protocol.get("path") != ""
            ):
                return False, False
        else:
            if (
                protocol.get("contract") != expected_protocol[0]
                or protocol.get("method") != "GET"
                or protocol.get("path") != expected_protocol[1]
                or protocol.get("checked") is not reachable
            ):
                return False, False
            if reachable:
                protocol_available = protocol.get("available") is True
                expected_protocol_status = (
                    "pass" if protocol_available else ("no_go" if requirement == "required" else "warning")
                )
                if (
                    not isinstance(protocol.get("available"), bool)
                    or protocol.get("status") != expected_protocol_status
                    or (protocol_available and protocol.get("error_code", "") != "")
                    or (not protocol_available and not isinstance(protocol.get("error_code"), str))
                    or (not protocol_available and not protocol.get("error_code"))
                ):
                    return False, False
            else:
                protocol_available = False
                if (
                    protocol.get("available") is not False
                    or protocol.get("status") != "not_run"
                    or protocol.get("error_code") != "transport_unreachable"
                ):
                    return False, False
        service_available = reachable and protocol_available
        expected_status = "pass" if service_available else ("no_go" if requirement == "required" else "warning")
        if status != expected_status:
            return False, False
        if requirement == "required":
            required_reachable += int(reachable)
            if status == "no_go":
                expected_blocker_codes.add(
                    SERVICE_CONNECTIVITY_BLOCKERS[service_id]
                    if not reachable
                    else SERVICE_PROTOCOL_BLOCKERS[service_id]
                )
        else:
            optional_reachable += int(reachable)
            if status == "warning":
                warning_count += 1

    by_check: dict[str, Mapping[str, Any]] = {}
    for item in checks:
        if not isinstance(item, dict) or not isinstance(item.get("check_id"), str):
            return False, False
        check_id = item["check_id"]
        if check_id in by_check:
            return False, False
        by_check[check_id] = item
    if set(by_check) != SERVICE_SECURITY_CHECKS or len(checks) != len(SERVICE_SECURITY_CHECKS):
        return False, False
    security_passed = 0
    for check_id in sorted(SERVICE_SECURITY_CHECKS):
        item = by_check[check_id]
        status = item.get("status")
        proof_present = item.get("proof_present")
        proof_source = item.get("proof_source")
        error_code = item.get("error_code", "")
        if status not in {"pass", "no_go"} or not isinstance(proof_present, bool):
            return False, False
        if not isinstance(proof_source, str) or not isinstance(error_code, str):
            return False, False
        if status == "pass":
            if proof_present is not True or proof_source != "proof_file" or error_code:
                return False, False
            security_passed += 1
        else:
            if proof_present is True or not error_code:
                return False, False
            expected_blocker_codes.add(error_code)

    actual_blocker_codes: set[str] = set()
    for item in blockers:
        if not isinstance(item, dict) or not isinstance(item.get("error_code"), str) or not item["error_code"]:
            return False, False
        actual_blocker_codes.add(item["error_code"])
    if actual_blocker_codes != expected_blocker_codes or len(blockers) != len(actual_blocker_codes):
        return False, False

    expected_summary = {
        "required_total": sum(requirement == "required" for _, requirement in SERVICE_SPECS.values()),
        "required_reachable": required_reachable,
        "optional_total": sum(requirement == "optional" for _, requirement in SERVICE_SPECS.values()),
        "optional_reachable": optional_reachable,
        "required_protocol_total": sum(
            service_id in SERVICE_PROTOCOL_SPECS
            for service_id, (_, requirement) in SERVICE_SPECS.items()
            if requirement == "required"
        ),
        "required_protocol_available": sum(
            by_service[service_id]["protocol_check"].get("available") is True
            for service_id, (_, requirement) in SERVICE_SPECS.items()
            if requirement == "required" and service_id in SERVICE_PROTOCOL_SPECS
        ),
        "optional_protocol_total": sum(
            service_id in SERVICE_PROTOCOL_SPECS
            for service_id, (_, requirement) in SERVICE_SPECS.items()
            if requirement == "optional"
        ),
        "optional_protocol_available": sum(
            by_service[service_id]["protocol_check"].get("available") is True
            for service_id, (_, requirement) in SERVICE_SPECS.items()
            if requirement == "optional" and service_id in SERVICE_PROTOCOL_SPECS
        ),
        "security_proofs_required": len(SERVICE_SECURITY_CHECKS),
        "security_proofs_present": security_passed,
        "blocking_count": len(expected_blocker_codes),
        "warning_count": warning_count,
    }
    if any(summary.get(key) != value for key, value in expected_summary.items()):
        return False, False
    expected_decision = "GO" if not expected_blocker_codes else "NO_GO"
    if payload.get("decision") != expected_decision:
        return False, False
    if "passed" in payload and payload.get("passed") is not (expected_decision == "GO"):
        return False, False
    return True, expected_decision == "GO"


def _validate_ab_arm(arm: Any, *, expected_execution_count: int) -> bool:
    if not isinstance(arm, dict):
        return False
    expected_fields = {
        "execution_count",
        *AB_ARM_RATE_FIELDS,
        *AB_ARM_COUNT_FIELDS,
        "sample_counts",
        "tool_runtime",
        "runtime_telemetry",
        "latency_ms",
    }
    if set(arm) != expected_fields or arm.get("execution_count") != expected_execution_count:
        return False
    if not _is_int(arm.get("execution_count"), minimum=1):
        return False
    # The normal A/B is intentionally free of fault injection. Fallback is
    # proven by the separately bound formal drill, while all normal-path
    # quality and policy false-positive metrics retain non-null denominators.
    for field in (
        item for item in AB_ARM_RATE_FIELDS if item not in {"fallback_success_rate", "fallback_telemetry_coverage"}
    ):
        if not _is_number(arm.get(field), minimum=0, maximum=1):
            return False
    if arm.get("fallback_success_rate") is not None or arm.get("fallback_telemetry_coverage") is not None:
        return False
    for field in AB_ARM_COUNT_FIELDS:
        if not _is_int(arm.get(field), minimum=0, maximum=expected_execution_count):
            return False
    if arm["fallback_expected_execution_count"] != 0 or arm["fallback_telemetry_expected_count"] != 0:
        return False
    sample_counts = arm.get("sample_counts")
    expected_sample_fields = {
        "answer_citation_rate",
        "numeric_accuracy",
        "hallucination_block_rate",
        "evidence_coverage",
        "tool_success_rate",
        "report_completeness",
        "policy_false_positive_rate",
    }
    if not isinstance(sample_counts, dict) or set(sample_counts) != expected_sample_fields:
        return False
    if any(not _is_int(value, minimum=0, maximum=expected_execution_count * 100) for value in sample_counts.values()):
        return False
    tool_runtime = arm.get("tool_runtime")
    tool_runtime_fields = {
        "attempt_count",
        "success_count",
        "failure_count",
        "retry_count",
        "failed_tool_state_count",
        "recovered_tool_state_count",
        "unrecovered_tool_state_count",
    }
    if not isinstance(tool_runtime, dict) or set(tool_runtime) != tool_runtime_fields:
        return False
    maximum_tool_events = expected_execution_count * ab_eval.MAX_SSE_EVENTS
    if any(
        not _is_int(tool_runtime.get(field), minimum=0, maximum=maximum_tool_events)
        for field in tool_runtime_fields
    ):
        return False
    attempts = int(tool_runtime["attempt_count"])
    failures = int(tool_runtime["failure_count"])
    retries = int(tool_runtime["retry_count"])
    failed_states = int(tool_runtime["failed_tool_state_count"])
    recovered_states = int(tool_runtime["recovered_tool_state_count"])
    unrecovered_states = int(tool_runtime["unrecovered_tool_state_count"])
    if (
        attempts != int(tool_runtime["success_count"]) + failures
        or retries > attempts
        or failed_states != recovered_states + unrecovered_states
        or failed_states > attempts
    ):
        return False
    expected_tool_rates = {
        "tool_error_rate": round(failures / attempts, 6) if attempts else 0.0,
        "tool_retry_rate": round(retries / attempts, 6) if attempts else 0.0,
        "tool_recovery_rate": round(recovered_states / failed_states, 6) if failed_states else 1.0,
        "tool_unrecovered_failure_rate": round(unrecovered_states / failed_states, 6) if failed_states else 0.0,
    }
    if any(arm.get(field) != value for field, value in expected_tool_rates.items()):
        return False
    runtime = arm.get("runtime_telemetry")
    runtime_fields = {
        "expected_primary_provider",
        "expected_primary_model",
        "telemetry_count",
        "requested_model_match_count",
        "configured_route_match_count",
        "effective_route_match_count",
        "fallback_inactive_count",
        "configured_routes",
        "effective_routes",
    }
    if not isinstance(runtime, dict) or set(runtime) != runtime_fields:
        return False
    provider = runtime.get("expected_primary_provider")
    model = runtime.get("expected_primary_model")
    if any(
        not isinstance(value, str) or not ab_eval.SAFE_RUNTIME_LABEL_RE.fullmatch(value) or "://" in value
        for value in (provider, model)
    ):
        return False
    if any(
        runtime.get(field) != expected_execution_count
        for field in ("telemetry_count", "requested_model_match_count", "configured_route_match_count")
    ):
        return False
    if any(
        not _is_int(runtime.get(field), minimum=0, maximum=expected_execution_count)
        for field in ("effective_route_match_count", "fallback_inactive_count")
    ):
        return False
    configured = runtime.get("configured_routes")
    if configured != [{"provider": provider, "model": model, "count": expected_execution_count}]:
        return False
    effective = runtime.get("effective_routes")
    if not isinstance(effective, list) or not effective:
        return False
    observed_routes: set[tuple[str, str]] = set()
    effective_total = 0
    primary_effective = 0
    for route in effective:
        if not isinstance(route, dict) or set(route) != {"provider", "model", "count"}:
            return False
        route_provider = route.get("provider")
        route_model = route.get("model")
        route_count = route.get("count")
        if (
            not isinstance(route_provider, str)
            or not ab_eval.SAFE_RUNTIME_LABEL_RE.fullmatch(route_provider)
            or not isinstance(route_model, str)
            or not ab_eval.SAFE_RUNTIME_LABEL_RE.fullmatch(route_model)
            or not _is_int(route_count, minimum=1, maximum=expected_execution_count)
            or (route_provider, route_model) in observed_routes
        ):
            return False
        observed_routes.add((route_provider, route_model))
        effective_total += int(route_count)
        if (route_provider, route_model) == (provider, model):
            primary_effective += int(route_count)
    if (
        effective_total != expected_execution_count
        or primary_effective != runtime["effective_route_match_count"]
        or primary_effective != runtime["fallback_inactive_count"]
    ):
        return False
    latency = arm.get("latency_ms")
    if not isinstance(latency, dict) or set(latency) != set(AB_ARM_LATENCY_FIELDS):
        return False
    if not _is_int(latency.get("ttft_sample_count"), minimum=0, maximum=expected_execution_count):
        return False
    if (
        not _is_int(latency.get("total_sample_count"), minimum=1)
        or latency["total_sample_count"] != expected_execution_count
    ):
        return False
    for prefix in ("ttft", "total"):
        sample_count = latency[f"{prefix}_sample_count"]
        p50 = latency[f"{prefix}_p50"]
        p95 = latency[f"{prefix}_p95"]
        if sample_count == 0:
            if p50 is not None or p95 is not None:
                return False
        elif not _is_number(p50, minimum=0) or not _is_number(p95, minimum=0) or float(p50) > float(p95):
            return False
    return True


def _validate_ab_summary(payload: Mapping[str, Any]) -> bool:
    expected_fields = {
        "schema_version",
        "evaluation_id",
        "prerequisites_path",
        "prerequisites_sha256",
        "dataset_sha256",
        "dataset_schema_version",
        "profile",
        "model",
        "temperature",
        "case_count",
        "repetitions",
        "execution_count",
        "interleaving",
        "arms",
        "comparison",
        "quality_gate",
        "sanitization",
    }
    if set(payload) != expected_fields:
        return False
    if (
        payload.get("schema_version") != AB_SUMMARY_SCHEMA
        or payload.get("dataset_schema_version") != ab_eval.DATASET_SCHEMA_VERSION
        or payload.get("profile") != "siq_analysis"
        or not _is_safe_id(payload.get("evaluation_id"))
        or payload.get("prerequisites_path") != f"var/openshell/eval/{payload.get('evaluation_id')}/prerequisites.json"
        or not _is_sha256(payload.get("prerequisites_sha256"))
        or not _is_sha256(payload.get("dataset_sha256"))
        or not isinstance(payload.get("model"), str)
        or not payload["model"]
        or "://" in payload["model"]
        or not _is_number(payload.get("temperature"), minimum=0, maximum=2)
        or not _is_int(payload.get("case_count"), minimum=ab_eval.MIN_EVALUATION_CASES, maximum=500)
        or not _is_int(
            payload.get("repetitions"),
            minimum=ab_eval.MIN_EVALUATION_REPETITIONS,
            maximum=10,
        )
        or payload.get("interleaving") != "alternating_case_and_repetition"
    ):
        return False
    case_count = int(payload["case_count"])
    repetitions = int(payload["repetitions"])
    expected_total = case_count * repetitions * 2
    expected_arm = case_count * repetitions
    if payload.get("execution_count") != expected_total or not _is_int(payload.get("execution_count"), minimum=1):
        return False
    arms = payload.get("arms")
    if not isinstance(arms, dict) or set(arms) != {"host", "openshell"}:
        return False
    if not all(_validate_ab_arm(arms[name], expected_execution_count=expected_arm) for name in ("host", "openshell")):
        return False

    comparison = payload.get("comparison")
    if not isinstance(comparison, dict) or set(comparison) != {"metric_deltas", "total_p95_ratio"}:
        return False
    deltas = comparison.get("metric_deltas")
    if not isinstance(deltas, dict) or set(deltas) != set(AB_COMPARISON_METRICS):
        return False
    fallback_deltas = {"fallback_success_rate", "fallback_telemetry_coverage"}
    if any(deltas.get(field) is not None for field in fallback_deltas) or any(
        not _is_number(deltas.get(field)) for field in set(AB_COMPARISON_METRICS) - fallback_deltas
    ):
        return False
    if not _is_number(comparison.get("total_p95_ratio"), minimum=0):
        return False

    quality = payload.get("quality_gate")
    if not isinstance(quality, dict) or set(quality) != {
        "passed",
        "failure_reasons",
        "cutover_performed",
        "recommendation",
    }:
        return False
    reasons = quality.get("failure_reasons")
    if (
        not isinstance(quality.get("passed"), bool)
        or not isinstance(reasons, list)
        or any(
            not isinstance(reason, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,95}\Z", reason)
            for reason in reasons
        )
        or len(set(reasons)) != len(reasons)
        or quality.get("cutover_performed") is not False
        or quality.get("recommendation") != "manual_review_only_no_automatic_cutover"
    ):
        return False

    sanitization = payload.get("sanitization")
    if not isinstance(sanitization, dict) or set(sanitization) != {
        "contains_api_keys",
        "contains_headers",
        "contains_prompt_or_input",
        "contains_raw_output",
        "t8_exporter_ready",
    }:
        return False
    if (
        any(
            sanitization.get(key) is not False
            for key in ("contains_api_keys", "contains_headers", "contains_prompt_or_input", "contains_raw_output")
        )
        or sanitization.get("t8_exporter_ready") is not True
    ):
        return False

    try:
        expected_comparison, expected_reasons = ab_eval.quality_comparison(
            arms["host"],
            arms["openshell"],
            case_count=case_count,
            repetitions=repetitions,
            require_fallback=False,
        )
    except (KeyError, TypeError, ValueError):
        return False
    if (
        comparison != expected_comparison
        or reasons != expected_reasons
        or quality["passed"] is not (not expected_reasons)
    ):
        return False
    return True


def _validate_readiness_document(payload: Mapping[str, Any]) -> bool:
    if payload.get("schema_version") != READINESS_SCHEMA:
        return False
    if (
        payload.get("decision") not in {"GO", "NO_GO"}
        or payload.get("decision_scope") != "formal_hermes_traffic_cutover"
    ):
        return False
    if payload.get("default_runtime") != "host" or not _is_fresh_timestamp(payload.get("generated_at")):
        return False
    blockers = payload.get("blockers")
    if not isinstance(blockers, list) or any(not isinstance(item, str) or not item for item in blockers):
        return False
    if (payload["decision"] == "GO") != (len(blockers) == 0):
        return False
    for key in (
        "runtime_state",
        "network_contract",
        "providers",
        "data_boundary",
        "security_controls",
        "lifecycle_safety",
        "verification",
        "contracts",
    ):
        if not isinstance(payload.get(key), dict):
            return False
    return True


def _readiness_runtime_go(payload: Mapping[str, Any], *, service_go: bool) -> bool:
    if not _validate_readiness_document(payload) or payload.get("decision") != "GO" or not service_go:
        return False
    runtime = payload["runtime_state"]
    if any(
        runtime.get(key) != value
        for key, value in {
            "project_gateway": "healthy",
            "host_brokers": "healthy",
            "formal_image_smoke": "passed",
            "provider_independent_probe": "passed",
            "broker_preflight": "passed",
            "formal_business_sandbox_created": True,
            "formal_ab_completed": True,
            "quality_validated": True,
            "formal_fallback_drill": "passed",
        }.items()
    ):
        return False
    if runtime.get("service_preflight") not in {"GO", "passed"}:
        return False
    providers = payload["providers"]
    required_providers = {
        "siq-minimax-cn-pool",
        "siq-stepfun",
        "siq-kimi-coding",
        "siq-tavily-search",
    }
    if (
        not isinstance(providers.get("configured"), list)
        or not required_providers.issubset(set(providers["configured"]))
        or providers.get("required_missing") != []
    ):
        return False
    network = payload["network_contract"]
    reachability = network.get("internal_model_reachability")
    if not isinstance(reachability, dict) or any(
        reachability.get(str(port)) != "online" for port in (8007, 8013)
    ):
        return False
    data_boundary = payload["data_boundary"]
    if (
        data_boundary.get("postgres_readonly_verified") is not True
        or data_boundary.get("milvus_sandbox_write_proof") is not True
    ):
        return False
    controls = payload["security_controls"]
    if any(
        controls.get(key) is not True
        for key in (
            "project_code_readonly",
            "agent_control_files_readonly",
            "finalized_ingested_paths_readonly",
            "task_analysis_path_writable",
            "runtime_session_and_memory_paths_writable",
            "unknown_file_upload_blocked",
            "high_risk_delete_guard",
        )
    ):
        return False
    contracts = payload["contracts"]
    if contracts.get("api_and_output_paths_unchanged") is not True:
        return False
    lifecycle = payload["lifecycle_safety"]
    if lifecycle.get("host_rollback_identity") != "exact_receipt_before_and_after":
        return False
    verification = payload["verification"]
    return (
        verification.get("sanitized_artifact_scan") == "passed"
        and verification.get("tracked_state_scan") == "passed"
        and verification.get("published_evidence_index_scan") == "passed"
    )


def _evidence_binding(
    readiness: Mapping[str, Any],
    *,
    verification_key: str,
    digest_key: str,
    path: Path,
    root: Path,
    digest: str,
) -> bool:
    verification = readiness.get("verification")
    if not isinstance(verification, dict):
        return False
    return (
        verification.get(verification_key) == _relative_evidence_path(root, path)
        and verification.get(digest_key) == digest
    )


def _check(check_id: str, passed: bool, error_code: str) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "status": "pass" if passed else "no_go",
        "error_code": "" if passed else error_code,
    }


def _formal_evidence(
    root: Path,
    explicit: Path | None,
) -> tuple[Mapping[str, Any], Path, str] | None:
    if explicit is None:
        return None
    try:
        path = _rooted(root, explicit)
    except CompletionConfigurationError as exc:
        if str(exc) == "evidence_path_missing":
            return None
        raise
    if check_sanitized_artifacts.scan_paths([path]):
        return None
    payload, digest = _json_with_digest(path, max_bytes=1024 * 1024)
    return payload, path, digest


def _memory_write_evidence(
    root: Path,
    explicit: Path | None,
    *,
    readiness_generated_at: Any,
) -> tuple[Mapping[str, Any], Path, str] | None:
    """Validate durable memory evidence against current contracts and readiness."""

    if explicit is None:
        return None
    try:
        path = _rooted(root, explicit)
    except CompletionConfigurationError as exc:
        if str(exc) == "evidence_path_missing":
            return None
        raise
    try:
        initial, _initial_digest = _json_with_digest(path, max_bytes=1024 * 1024)
        window = initial.get("evidence_window")
        completed_at = window.get("completed_at_unix") if isinstance(window, dict) else None
        if not _is_int(completed_at, minimum=1):
            return None
        default_path = (root / DEFAULT_MEMORY_WRITE_EVIDENCE).resolve(strict=False)
        if path == default_path:
            validated = memory_write_evidence.validate_consumable_evidence(
                path,
                project_root=root,
                # Memory evidence is durable. Its age is bounded by readiness
                # below, not by wall-clock time at each later completion check.
                now=int(completed_at),
            )
        else:
            relative = path.relative_to(root)
            evidence_bytes = memory_write_evidence._safe_read(root, relative, private=True)
            boundary_bytes = memory_write_evidence._safe_read(
                root,
                memory_write_evidence.BOUNDARY_RELATIVE,
                private=False,
            )
            schema_bytes = memory_write_evidence._safe_read(
                root,
                memory_write_evidence.EVIDENCE_SCHEMA_RELATIVE,
                private=False,
            )
            memory_write_evidence._validate_boundary(memory_write_evidence._parse_json(boundary_bytes))
            memory_write_evidence._validate_schema_contract(memory_write_evidence._parse_json(schema_bytes))
            validated = memory_write_evidence._parse_json(evidence_bytes)
            memory_write_evidence._validate_evidence_payload(
                validated,
                boundary_sha256=hashlib.sha256(boundary_bytes).hexdigest(),
                schema_sha256=hashlib.sha256(schema_bytes).hexdigest(),
                now=int(completed_at),
            )
            if check_sanitized_artifacts.scan_content(path, evidence_bytes):
                return None
        payload, digest = _json_with_digest(path, max_bytes=1024 * 1024)
    except (
        CompletionConfigurationError,
        memory_write_evidence.MemoryEvidenceError,
        OSError,
        ValueError,
    ):
        return None
    readiness_at = _parse_timestamp(readiness_generated_at)
    try:
        completed_datetime = datetime.fromtimestamp(int(completed_at), timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None
    if payload != initial or payload != validated or readiness_at is None or completed_datetime > readiness_at:
        return None
    return payload, path, digest


def _inferred_ab_path(summary: Mapping[str, Any] | None, filename: str) -> Path | None:
    if summary is None or not isinstance(summary.get("evaluation_id"), str):
        return None
    evaluation_id = str(summary["evaluation_id"])
    if SAFE_ID_RE.fullmatch(evaluation_id) is None:
        return None
    return DEFAULT_AB_ROOT / evaluation_id / filename


def _ab_summary(root: Path, explicit: Path | None) -> tuple[Mapping[str, Any], Path, str] | None:
    if explicit is None:
        return None
    path = _rooted(root, explicit)
    findings = check_sanitized_artifacts.scan_paths([path])
    if findings:
        raise CompletionConfigurationError("ab_summary_not_sanitized")
    payload, digest = _json_with_digest(path, max_bytes=8 * 1024 * 1024)
    if payload.get("schema_version") != AB_SUMMARY_SCHEMA:
        raise CompletionConfigurationError("ab_summary_schema_invalid")
    return payload, path, digest


def _ab_raw_results(
    root: Path,
    explicit: Path | None,
    *,
    summary: Mapping[str, Any] | None,
) -> tuple[Mapping[str, Any], Path, str] | None:
    selected = explicit if explicit is not None else _inferred_ab_path(summary, "raw-results.json")
    if selected is None or summary is None:
        return None
    try:
        path = _rooted(root, selected)
        payload, digest = _json_with_digest(path, max_bytes=64 * 1024 * 1024)
    except CompletionConfigurationError:
        return None
    if (
        payload.get("schema_version") != ab_eval.RAW_SCHEMA_VERSION
        or payload.get("evaluation_id") != summary.get("evaluation_id")
        or payload.get("dataset_sha256") != summary.get("dataset_sha256")
        or payload.get("prerequisites_path") != summary.get("prerequisites_path")
        or payload.get("prerequisites_sha256") != summary.get("prerequisites_sha256")
        or payload.get("cutover_performed") is not False
    ):
        return None
    return payload, path, digest


def _validate_ab_prerequisite_evidence(
    value: Any,
    *,
    report_generated_at: datetime,
    report_expires_at: datetime,
) -> bool:
    if not isinstance(value, dict) or set(value) != AB_PREREQUISITE_EVIDENCE_NAMES:
        return False
    maximum_sizes = {
        "provider_inventory": 64 * 1024,
        "service_report": 512 * 1024,
        "broker_report": 512 * 1024,
    }
    lifetimes = {
        "provider_inventory": timedelta(minutes=15),
        "service_report": timedelta(minutes=5),
        "broker_report": timedelta(minutes=1),
    }
    paths: set[str] = set()
    expiries: list[datetime] = []
    for name in AB_PREREQUISITE_EVIDENCE_NAMES:
        binding = value.get(name)
        if not isinstance(binding, dict) or set(binding) != AB_PREREQUISITE_EVIDENCE_FIELDS:
            return False
        source_path = binding.get("path")
        if (
            not isinstance(source_path, str)
            or not source_path
            or len(source_path) > 4096
            or "\x00" in source_path
            or not Path(source_path).is_absolute()
            or not _is_sha256(binding.get("sha256"))
            or not _is_int(binding.get("size_bytes"), minimum=1, maximum=maximum_sizes[name])
            or not _is_int(binding.get("device"), minimum=1)
            or not _is_int(binding.get("inode"), minimum=1)
            or not _is_int(binding.get("mode"), minimum=1, maximum=0o7777)
            or not _is_int(binding.get("mtime_ns"), minimum=1)
            or not _is_int(binding.get("ctime_ns"), minimum=1)
        ):
            return False
        generated_at = _parse_timestamp(binding.get("generated_at"))
        expires_at = _parse_timestamp(binding.get("expires_at"))
        if (
            generated_at is None
            or expires_at is None
            or expires_at - generated_at != lifetimes[name]
            or generated_at > report_generated_at + timedelta(seconds=5)
        ):
            return False
        paths.add(source_path)
        expiries.append(expires_at)
    return (
        len(paths) == len(AB_PREREQUISITE_EVIDENCE_NAMES)
        and report_expires_at == min(expiries)
        and report_generated_at < report_expires_at
    )


def _ab_prerequisites(
    root: Path,
    explicit: Path | None,
    *,
    summary: Mapping[str, Any] | None,
    readiness_generated_at: Any,
) -> tuple[Mapping[str, Any], Path, str] | None:
    if explicit is None or summary is None:
        return None
    path = _rooted(root, explicit)
    # The private v3 prerequisite receipt necessarily binds machine-local source
    # paths. Permit only that scanner finding; credentials and business content
    # remain forbidden. The public A/B summary remains fully sanitized.
    findings = check_sanitized_artifacts.scan_paths([path])
    if any(finding.code != "local_absolute_path" for finding in findings):
        raise CompletionConfigurationError("ab_prerequisites_not_sanitized")
    payload, digest = _json_with_digest(path, max_bytes=1024 * 1024)
    if (
        summary.get("prerequisites_path") != _relative_evidence_path(root, path)
        or summary.get("prerequisites_sha256") != digest
    ):
        return None
    host = payload.get("host")
    openshell = payload.get("openshell")
    dataset = payload.get("dataset")
    provenance = payload.get("provenance")
    fingerprints = payload.get("key_fingerprints")
    evidence = payload.get("evidence")
    summary_case_count = summary.get("case_count")
    if not _is_int(summary_case_count, minimum=ab_eval.MIN_EVALUATION_CASES, maximum=500):
        return None
    generated_at = _parse_timestamp(payload.get("generated_at"))
    expires_at = _parse_timestamp(payload.get("expires_at"))
    readiness_at = _parse_timestamp(readiness_generated_at)
    expected_fields = {
        "schema_version",
        "decision",
        "profile",
        "evaluation_id",
        "host",
        "openshell",
        "dataset",
        "provenance",
        "evaluation_id_valid",
        "key_fingerprints",
        "evidence",
        "provider_count",
        "missing_provider_count",
        "service_preflight_decision",
        "blockers",
        "network_probe_performed",
        "cutover_performed",
        "generated_at",
        "expires_at",
    }
    if (
        set(payload) != expected_fields
        or payload.get("schema_version") != AB_PREREQUISITE_SCHEMA
        or payload.get("decision") != "GO"
        or payload.get("profile") != "siq_analysis"
        or payload.get("evaluation_id") != summary.get("evaluation_id")
        or payload.get("evaluation_id_valid") is not True
        or payload.get("blockers") != []
        or payload.get("network_probe_performed") is not True
        or payload.get("cutover_performed") is not False
        or payload.get("missing_provider_count") != 0
        or not _is_int(payload.get("provider_count"), minimum=len(ab_eval.PROVIDERS))
        or payload.get("service_preflight_decision") != "GO"
        or generated_at is None
        or expires_at is None
        or readiness_at is None
        or generated_at > readiness_at
        or expires_at <= generated_at
        or not _validate_ab_prerequisite_evidence(
            evidence,
            report_generated_at=generated_at,
            report_expires_at=expires_at,
        )
        or not isinstance(host, dict)
        or set(host) != {"scheme", "port", "path", "normalized", "analysis_port"}
        or host.get("scheme") != "http"
        or host.get("port") != 18651
        or host.get("analysis_port") != 18651
        or host.get("path") != "/v1/runs"
        or not isinstance(host.get("normalized"), str)
        or not isinstance(openshell, dict)
        or set(openshell) != {"scheme", "port", "path", "normalized", "expected_port"}
        or openshell.get("scheme") != "http"
        or openshell.get("port") != 28651
        or openshell.get("expected_port") != 28651
        or openshell.get("path") != "/v1/runs"
        or not isinstance(openshell.get("normalized"), str)
        or not isinstance(dataset, dict)
        or set(dataset)
        != {
            "schema_version",
            "sha256",
            "case_count",
            "repetitions",
            "normal_case_count",
            "fallback_case_count",
        }
        or dataset.get("schema_version") != ab_eval.DATASET_SCHEMA_VERSION
        or dataset.get("sha256") != summary.get("dataset_sha256")
        or dataset.get("case_count") != summary.get("case_count")
        or dataset.get("repetitions") != summary.get("repetitions")
        or not _is_int(
            dataset.get("normal_case_count"),
            minimum=1,
            maximum=int(summary_case_count),
        )
        or int(dataset.get("normal_case_count", 0)) * int(dataset.get("repetitions", 0))
        < ab_eval.MIN_POLICY_NORMAL_SAMPLES
        or dataset.get("fallback_case_count") != 0
        or not isinstance(provenance, dict)
        or set(provenance)
        != {
            "schema_version",
            "sha256",
            "hermes_commit",
            "host_runtime_verified",
            "host_runtime_receipt_sha256",
            "runtime_contract_sha256",
            "host_candidate_source_match",
            "arms_match",
        }
        or provenance.get("schema_version") != AB_PROVENANCE_SCHEMA
        or provenance.get("hermes_commit") != HERMES_COMMIT
        or provenance.get("host_runtime_verified") is not True
        or provenance.get("host_candidate_source_match") is not True
        or not _is_sha256(provenance.get("host_runtime_receipt_sha256"))
        or not _is_sha256(provenance.get("runtime_contract_sha256"))
        or provenance.get("arms_match") is not True
        or not _is_sha256(provenance.get("sha256"))
        or not isinstance(fingerprints, dict)
        or set(fingerprints) != {"host", "openshell"}
        or not all(_is_sha256(value) for value in fingerprints.values())
        or fingerprints.get("host") == fingerprints.get("openshell")
    ):
        return None
    return payload, path, digest


def _ab_provenance(
    root: Path,
    explicit: Path | None,
    *,
    summary: Mapping[str, Any] | None,
    prerequisites: Mapping[str, Any] | None,
) -> tuple[Mapping[str, Any], Path, str] | None:
    selected = explicit if explicit is not None else _inferred_ab_path(summary, "provenance.json")
    if selected is None or summary is None or prerequisites is None:
        return None
    try:
        path = _rooted(root, selected)
        payload, digest = _json_with_digest(path, max_bytes=1024 * 1024)
    except CompletionConfigurationError:
        return None
    prerequisite_provenance = prerequisites.get("provenance")
    if (
        payload.get("schema_version") != AB_PROVENANCE_SCHEMA
        or payload.get("profile") != "siq_analysis"
        or payload.get("evaluation_id") != summary.get("evaluation_id")
        or payload.get("dataset_sha256") != summary.get("dataset_sha256")
        or not isinstance(payload.get("arms"), dict)
        or not isinstance(payload.get("runtime_attestation"), dict)
        or not isinstance(prerequisite_provenance, dict)
        or prerequisite_provenance.get("sha256") != digest
    ):
        return None
    return payload, path, digest


def _runbook_index_digest(root: Path) -> str | None:
    """Bind the exact stage-zero Git blobs for every required runbook."""

    records: list[tuple[str, str]] = []
    for relative in REQUIRED_RUNBOOKS:
        relative_text = relative.as_posix()
        try:
            indexed = subprocess.run(
                ["git", "-C", str(root), "ls-files", "--stage", "-z", "--", relative_text],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ).stdout
        except (OSError, subprocess.CalledProcessError):
            return None
        rows = [row for row in indexed.split(b"\0") if row]
        if len(rows) != 1:
            return None
        try:
            metadata, indexed_path = rows[0].split(b"\t", 1)
            mode, _object_id, stage = metadata.decode("ascii").split(" ", 2)
            decoded_path = indexed_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            return None
        if mode != "100644" or stage != "0" or decoded_path != relative_text:
            return None
        try:
            blob = subprocess.run(
                ["git", "-C", str(root), "show", f":{relative_text}"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ).stdout
            blob.decode("utf-8")
        except (OSError, UnicodeDecodeError, subprocess.CalledProcessError):
            return None
        if not blob or len(blob) > MAX_RUNBOOK_BYTES or b"\x00" in blob:
            return None
        records.append((relative_text, hashlib.sha256(blob).hexdigest()))

    digest = hashlib.sha256()
    for relative_text, blob_sha256 in sorted(records):
        digest.update(relative_text.encode("utf-8"))
        digest.update(b"\0")
        digest.update(blob_sha256.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _review_text(value: Any, *, maximum: int = 128) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip()
    if not normalized or len(normalized) > maximum or "\x00" in normalized:
        return False
    lowered = normalized.casefold()
    return not any(marker in lowered for marker in ("<fill", "<填写", "placeholder", "pending", "tbd"))


def _review_approved(
    root: Path,
    path_value: Path | None,
    *,
    expected_evidence: Mapping[str, str],
) -> bool:
    if path_value is None or set(expected_evidence) != REVIEW_EVIDENCE_FIELDS:
        return False
    try:
        path = _rooted(root, path_value)
        if path.suffix != ".json" or check_sanitized_artifacts.scan_paths([path]):
            return False
        payload, _digest = _json_with_digest(path, max_bytes=256 * 1024)
    except (CompletionConfigurationError, OSError, UnicodeError, ValueError):
        return False
    reviewer = payload.get("reviewer")
    scope = payload.get("scope")
    evidence = payload.get("evidence")
    checklist = payload.get("checklist")
    reviewed_at = _parse_timestamp(payload.get("reviewed_at"))
    if (
        set(payload)
        != {
            "schema_version",
            "review_id",
            "decision",
            "reviewed_at",
            "reviewer",
            "scope",
            "evidence",
            "checklist",
            "cutover_performed",
        }
        or payload.get("schema_version") != ARCHITECTURE_REVIEW_SCHEMA
        or payload.get("decision") != "approved"
        or payload.get("cutover_performed") is not False
        or not _review_text(payload.get("review_id"), maximum=96)
        or reviewed_at is None
        or reviewed_at > datetime.now(timezone.utc) + FUTURE_TIMESTAMP_TOLERANCE
        or not isinstance(reviewer, dict)
        or set(reviewer) != {"name", "role", "organization"}
        or not all(_review_text(reviewer.get(field)) for field in reviewer)
        or not isinstance(scope, dict)
        or set(scope) != {"profile", "openshell_version", "hermes_commit"}
        or scope.get("profile") != "siq_analysis"
        or scope.get("openshell_version") != "0.0.83"
        or scope.get("hermes_commit") != HERMES_COMMIT
        or not isinstance(evidence, dict)
        or set(evidence) != REVIEW_EVIDENCE_FIELDS
        or evidence != dict(expected_evidence)
        or not all(_is_sha256(value) for value in evidence.values())
        or not isinstance(checklist, dict)
        or set(checklist) != REVIEW_CHECKLIST_FIELDS
        or any(checklist.get(field) is not True for field in REVIEW_CHECKLIST_FIELDS)
    ):
        return False
    return True


def build_report(
    *,
    project_root: Path = REPO_ROOT,
    readiness_path: Path = DEFAULT_READINESS,
    service_path: Path = DEFAULT_SERVICE,
    formal_host_rollback_path: Path | None = DEFAULT_FORMAL_HOST_ROLLBACK,
    formal_delete_guard_path: Path | None = DEFAULT_FORMAL_DELETE_GUARD,
    formal_filesystem_boundary_path: Path | None = DEFAULT_FORMAL_FILESYSTEM_BOUNDARY,
    host_egress_component_path: Path | None = DEFAULT_HOST_EGRESS_COMPONENT,
    formal_egress_sandbox_path: Path | None = DEFAULT_FORMAL_EGRESS_SANDBOX,
    formal_structured_audit_path: Path | None = DEFAULT_FORMAL_STRUCTURED_AUDIT,
    memory_write_evidence_path: Path | None = DEFAULT_MEMORY_WRITE_EVIDENCE,
    ab_summary_path: Path | None = None,
    ab_raw_results_path: Path | None = None,
    ab_prerequisites_path: Path | None = None,
    formal_fallback_drill_path: Path | None = DEFAULT_FORMAL_FALLBACK_DRILL,
    ab_provenance_path: Path | None = None,
    review_record_path: Path | None = None,
) -> dict[str, Any]:
    root = project_root.resolve(strict=True)
    readiness_file = _rooted(root, readiness_path)
    service_file = _rooted(root, service_path)
    readiness, readiness_digest = _json_with_digest(readiness_file)
    service, service_digest = _json_with_digest(service_file)
    host_rollback_evidence = _formal_evidence(root, formal_host_rollback_path)
    delete_guard_evidence = _formal_evidence(root, formal_delete_guard_path)
    filesystem_boundary_evidence = _formal_evidence(root, formal_filesystem_boundary_path)
    host_egress_component = _formal_evidence(root, host_egress_component_path)
    formal_egress_evidence = _formal_evidence(root, formal_egress_sandbox_path)
    formal_audit_evidence = _formal_evidence(root, formal_structured_audit_path)
    memory_evidence = _memory_write_evidence(
        root,
        memory_write_evidence_path,
        readiness_generated_at=readiness.get("generated_at"),
    )
    summary_evidence = _ab_summary(root, ab_summary_path)
    summary = summary_evidence[0] if summary_evidence is not None else None
    raw_results_evidence = _ab_raw_results(root, ab_raw_results_path, summary=summary)
    prerequisites_evidence = _ab_prerequisites(
        root,
        ab_prerequisites_path,
        summary=summary,
        readiness_generated_at=readiness.get("generated_at"),
    )
    provenance_evidence = _ab_provenance(
        root,
        ab_provenance_path,
        summary=summary,
        prerequisites=prerequisites_evidence[0] if prerequisites_evidence is not None else None,
    )
    fallback_drill_evidence = _formal_evidence(root, formal_fallback_drill_path)
    required_public_evidence: list[tuple[Path, str]] = []
    for relative in REQUIRED_EVIDENCE:
        path = _rooted(root, relative)
        if path == readiness_file:
            digest = readiness_digest
        elif path == service_file:
            digest = service_digest
        else:
            _content, digest = _read_evidence(path)
        required_public_evidence.append((path, digest))
    optional_public_evidence = (
        host_rollback_evidence,
        delete_guard_evidence,
        filesystem_boundary_evidence,
        host_egress_component,
        formal_egress_evidence,
        formal_audit_evidence,
        memory_evidence,
        summary_evidence,
        fallback_drill_evidence,
    )
    published_evidence_index_bound = bool(
        all(item is not None for item in optional_public_evidence)
        and all(_git_index_evidence_matches(root, path, digest) for path, digest in required_public_evidence)
        and all(
            _git_index_evidence_matches(root, item[1], item[2]) for item in optional_public_evidence if item is not None
        )
    )
    checks: list[dict[str, Any]] = []
    controls = readiness.get("security_controls") if isinstance(readiness.get("security_controls"), dict) else {}
    lifecycle = readiness.get("lifecycle_safety") if isinstance(readiness.get("lifecycle_safety"), dict) else {}
    verification = readiness.get("verification") if isinstance(readiness.get("verification"), dict) else {}
    providers = readiness.get("providers") if isinstance(readiness.get("providers"), dict) else {}
    network = readiness.get("network_contract") if isinstance(readiness.get("network_contract"), dict) else {}
    reachability = network.get("internal_model_reachability")
    runbook_digest = _runbook_index_digest(root)
    readiness_valid = _validate_readiness_document(readiness)
    service_valid, service_go = _validate_service_report(service)
    service_bound = _evidence_binding(
        readiness,
        verification_key="service_preflight_evidence",
        digest_key="service_preflight_sha256",
        path=service_file,
        root=root,
        digest=service_digest,
    )
    runtime_service_state = (
        readiness.get("runtime_state", {}).get("service_preflight")
        if isinstance(readiness.get("runtime_state"), dict)
        else None
    )
    service_decision_consistent = (service_go and runtime_service_state in {"GO", "passed"}) or (
        service_valid
        and not service_go
        and isinstance(runtime_service_state, str)
        and runtime_service_state.startswith("NO_GO")
    )
    readiness_runtime_go = (
        _readiness_runtime_go(readiness, service_go=service_go)
        and service_valid
        and service_bound
        and service_decision_consistent
        and published_evidence_index_bound
    )
    memory_evidence_bound = bool(
        memory_evidence is not None
        and _evidence_binding(
            readiness,
            verification_key="memory_write_evidence",
            digest_key="memory_write_evidence_sha256",
            path=memory_evidence[1],
            root=root,
            digest=memory_evidence[2],
        )
    )
    host_rollback_valid = bool(
        host_rollback_evidence is not None
        and _validate_formal_host_rollback(
            host_rollback_evidence[0],
            root=root,
            readiness_generated_at=readiness.get("generated_at"),
        )
    )
    host_rollback_bound = bool(
        host_rollback_evidence is not None
        and _evidence_binding(
            readiness,
            verification_key="formal_host_rollback_evidence",
            digest_key="formal_host_rollback_sha256",
            path=host_rollback_evidence[1],
            root=root,
            digest=host_rollback_evidence[2],
        )
    )
    host_rollback_passed = bool(
        readiness_runtime_go
        and host_rollback_valid
        and host_rollback_bound
        and lifecycle.get("host_rollback_identity") == "exact_receipt_before_and_after"
    )
    delete_guard_valid = bool(
        delete_guard_evidence is not None
        and _validate_formal_delete_guard(
            delete_guard_evidence[0],
            root=root,
            readiness_generated_at=readiness.get("generated_at"),
        )
    )
    delete_guard_bound = bool(
        delete_guard_evidence is not None
        and _evidence_binding(
            readiness,
            verification_key="formal_delete_guard_evidence",
            digest_key="formal_delete_guard_sha256",
            path=delete_guard_evidence[1],
            root=root,
            digest=delete_guard_evidence[2],
        )
    )
    delete_guard_passed = bool(
        readiness_runtime_go
        and delete_guard_valid
        and delete_guard_bound
        and controls.get("high_risk_delete_guard") is True
    )
    filesystem_boundary_valid = bool(
        filesystem_boundary_evidence is not None
        and _validate_formal_filesystem_boundary(
            filesystem_boundary_evidence[0],
            root=root,
            readiness_generated_at=readiness.get("generated_at"),
        )
    )
    filesystem_boundary_bound = bool(
        filesystem_boundary_evidence is not None
        and _evidence_binding(
            readiness,
            verification_key="formal_filesystem_boundary_evidence",
            digest_key="formal_filesystem_boundary_sha256",
            path=filesystem_boundary_evidence[1],
            root=root,
            digest=filesystem_boundary_evidence[2],
        )
    )
    host_egress_component_valid = bool(
        host_egress_component is not None and _validate_host_egress_component(host_egress_component[0])
    )
    host_egress_component_bound = bool(
        host_egress_component is not None
        and _evidence_binding(
            readiness,
            verification_key="host_egress_component_evidence",
            digest_key="host_egress_component_sha256",
            path=host_egress_component[1],
            root=root,
            digest=host_egress_component[2],
        )
    )
    formal_egress_valid = bool(
        host_egress_component_valid
        and host_egress_component is not None
        and formal_egress_evidence is not None
        and _validate_formal_egress_sandbox(
            formal_egress_evidence[0],
            readiness_generated_at=readiness.get("generated_at"),
            host_component=host_egress_component[0],
            host_component_path=host_egress_component[1],
            host_component_digest=host_egress_component[2],
            root=root,
        )
    )
    formal_runtime_provenance_bound = bool(
        filesystem_boundary_valid
        and filesystem_boundary_evidence is not None
        and host_rollback_valid
        and host_rollback_evidence is not None
        and delete_guard_valid
        and delete_guard_evidence is not None
        and formal_egress_valid
        and formal_egress_evidence is not None
        and _formal_runtime_provenance_matches(
            filesystem_boundary_evidence[0],
            host_rollback_evidence[0],
            delete_guard_evidence[0],
            formal_egress_evidence[0],
        )
    )
    formal_egress_bound = bool(
        formal_egress_evidence is not None
        and _evidence_binding(
            readiness,
            verification_key="formal_egress_sandbox_evidence",
            digest_key="formal_egress_sandbox_sha256",
            path=formal_egress_evidence[1],
            root=root,
            digest=formal_egress_evidence[2],
        )
    )
    formal_audit_valid = bool(
        formal_egress_valid
        and formal_egress_evidence is not None
        and formal_audit_evidence is not None
        and _validate_formal_structured_audit(
            formal_audit_evidence[0],
            root=root,
            readiness_generated_at=readiness.get("generated_at"),
            formal_egress=formal_egress_evidence[0],
        )
    )
    formal_audit_bound = bool(
        formal_audit_evidence is not None
        and _evidence_binding(
            readiness,
            verification_key="formal_structured_audit_evidence",
            digest_key="formal_structured_audit_sha256",
            path=formal_audit_evidence[1],
            root=root,
            digest=formal_audit_evidence[2],
        )
    )
    formal_egress_passed = bool(
        readiness_runtime_go
        and formal_runtime_provenance_bound
        and controls.get("unknown_file_upload_blocked") is True
        and host_egress_component_valid
        and host_egress_component_bound
        and formal_egress_valid
        and formal_egress_bound
        and formal_audit_valid
        and formal_audit_bound
    )
    ab_valid = bool(summary is not None and _validate_ab_summary(summary))
    ab_bound = False
    prerequisites_bound = False
    if summary_evidence is not None:
        ab_bound = _evidence_binding(
            readiness,
            verification_key="formal_ab_evidence",
            digest_key="formal_ab_summary_sha256",
            path=summary_evidence[1],
            root=root,
            digest=summary_evidence[2],
        )
    if prerequisites_evidence is not None:
        prerequisites_bound = _evidence_binding(
            readiness,
            verification_key="formal_ab_prerequisites_evidence",
            digest_key="formal_ab_prerequisites_sha256",
            path=prerequisites_evidence[1],
            root=root,
            digest=prerequisites_evidence[2],
        )
    fallback_drill_valid = bool(
        summary_evidence is not None
        and summary is not None
        and prerequisites_evidence is not None
        and fallback_drill_evidence is not None
        and _validate_formal_fallback_drill(
            fallback_drill_evidence[0],
            root=root,
            readiness_generated_at=readiness.get("generated_at"),
            summary=summary,
            summary_sha256=summary_evidence[2],
            prerequisites=prerequisites_evidence[0],
            prerequisites_sha256=prerequisites_evidence[2],
        )
    )
    fallback_drill_bound = bool(
        fallback_drill_evidence is not None
        and _evidence_binding(
            readiness,
            verification_key="formal_fallback_drill_evidence",
            digest_key="formal_fallback_drill_sha256",
            path=fallback_drill_evidence[1],
            root=root,
            digest=fallback_drill_evidence[2],
        )
    )
    fallback_runtime_provenance_bound = bool(
        fallback_drill_valid
        and fallback_drill_evidence is not None
        and filesystem_boundary_evidence is not None
        and host_rollback_evidence is not None
        and delete_guard_evidence is not None
        and formal_egress_evidence is not None
        and _formal_fallback_runtime_provenance_matches(
            fallback_drill_evidence[0],
            filesystem_boundary_evidence[0],
            host_rollback_evidence[0],
            delete_guard_evidence[0],
            formal_egress_evidence[0],
        )
    )
    ab_passed = bool(
        ab_valid
        and ab_bound
        and prerequisites_evidence is not None
        and prerequisites_bound
        and readiness_runtime_go
        and summary is not None
        and summary["quality_gate"]["passed"] is True
        and summary["quality_gate"]["failure_reasons"] == []
        and fallback_drill_valid
        and fallback_drill_bound
        and fallback_runtime_provenance_bound
    )
    checks.append(_check("real_host_openshell_ab", ab_passed, "formal_ab_missing_or_failed"))
    contracts = readiness.get("contracts") if isinstance(readiness.get("contracts"), dict) else {}
    checks.append(
        _check(
            "api_and_output_paths_unchanged",
            readiness_runtime_go and contracts.get("api_and_output_paths_unchanged") is True,
            "api_output_contract_evidence_missing",
        )
    )
    checks.append(
        _check(
            "immutable_write_denials",
            readiness_runtime_go
            and filesystem_boundary_valid
            and filesystem_boundary_bound
            and formal_runtime_provenance_bound
            and all(
                controls.get(key) is True
                for key in (
                    "project_code_readonly",
                    "agent_control_files_readonly",
                    "finalized_ingested_paths_readonly",
                )
            ),
            "immutable_write_evidence_missing",
        )
    )
    checks.append(
        _check(
            "normal_analysis_and_memory_writes",
            readiness_runtime_go
            and filesystem_boundary_valid
            and filesystem_boundary_bound
            and formal_runtime_provenance_bound
            and controls.get("task_analysis_path_writable") is True
            and controls.get("runtime_session_and_memory_paths_writable") is True
            and memory_evidence is not None
            and memory_evidence_bound,
            "normal_write_evidence_missing",
        )
    )
    checks.append(
        _check(
            "services_models_search_fallback",
            readiness_runtime_go
            and service_valid
            and service_go
            and service_bound
            and providers.get("required_missing") == []
            and isinstance(reachability, dict)
            and all(reachability.get(str(port)) == "online" for port in (8007, 8013)),
            "service_or_provider_preflight_no_go",
        )
    )
    checks.append(
        _check(
            "unknown_file_upload_denied",
            formal_egress_passed,
            "upload_guard_missing",
        )
    )
    checks.append(_check("quality_gate", ab_passed, "quality_ab_missing_or_failed"))
    checks.append(
        _check(
            "formal_host_rollback",
            host_rollback_passed and formal_runtime_provenance_bound,
            "formal_host_rollback_missing",
        )
    )
    docs_ok = (
        all((_rooted(root, path).is_file() for path in REQUIRED_EVIDENCE))
        and runbook_digest is not None
        and verification.get("openshell_docs_sha256") == runbook_digest
        and readiness_valid
        and service_valid
        and service_bound
        and service_decision_consistent
        and verification.get("sanitized_artifact_scan") == "passed"
        and formal_audit_valid
        and formal_audit_bound
    )
    checks.append(_check("docs_and_audit_complete", docs_ok, "docs_or_audit_evidence_missing"))
    review_evidence = {
        "readiness_sha256": readiness_digest,
        "service_preflight_sha256": service_digest,
        "formal_ab_summary_sha256": summary_evidence[2] if summary_evidence is not None else "",
        "formal_fallback_drill_sha256": (
            fallback_drill_evidence[2] if fallback_drill_evidence is not None else ""
        ),
        "formal_host_rollback_sha256": host_rollback_evidence[2] if host_rollback_evidence is not None else "",
        "formal_delete_guard_sha256": delete_guard_evidence[2] if delete_guard_evidence is not None else "",
        "formal_egress_sandbox_sha256": formal_egress_evidence[2] if formal_egress_evidence is not None else "",
        "formal_structured_audit_sha256": formal_audit_evidence[2] if formal_audit_evidence is not None else "",
    }
    checks.append(
        _check(
            "human_architecture_security_review",
            _review_approved(root, review_record_path, expected_evidence=review_evidence),
            "human_review_missing",
        )
    )
    reproducible = (
        all((_rooted(root, path).is_file() for path in REQUIRED_EVIDENCE))
        and readiness_runtime_go
        and service_valid
        and service_bound
        and ab_valid
        and ab_bound
        and prerequisites_evidence is not None
        and prerequisites_bound
        and fallback_drill_valid
        and fallback_drill_bound
        and fallback_runtime_provenance_bound
        and host_rollback_valid
        and host_rollback_bound
        and delete_guard_valid
        and delete_guard_bound
        and filesystem_boundary_valid
        and filesystem_boundary_bound
        and formal_runtime_provenance_bound
        and host_egress_component_valid
        and host_egress_component_bound
        and formal_egress_valid
        and formal_egress_bound
        and formal_audit_valid
        and formal_audit_bound
        and memory_evidence is not None
        and memory_evidence_bound
        and published_evidence_index_bound
        and verification.get("tracked_state_scan") == "passed"
    )
    checks.append(_check("reproducible_sanitized_evidence", reproducible, "reproducible_evidence_missing"))
    try:
        tracked_findings = check_tracked_state.scan_tracked_state(root, require_allowlist=True)
    except Exception:
        tracked_findings = [object()]
    checks.append(_check("tracked_state_secret_scan", not tracked_findings, "tracked_state_scan_missing_or_failed"))
    checks.append(
        _check(
            "formal_delete_guard_evidence",
            delete_guard_passed and formal_runtime_provenance_bound,
            "formal_delete_guard_evidence_missing",
        )
    )
    blockers = sorted(item["error_code"] for item in checks if item["status"] != "pass")
    return {
        "schema_version": SCHEMA_VERSION,
        "decision": "GO" if not blockers else "NO_GO",
        "default_runtime": readiness.get("default_runtime"),
        "checks": checks,
        "passed_count": len(checks) - len(blockers),
        "total_count": len(checks),
        "blockers": blockers,
        "cutover_performed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--readiness", type=Path, default=DEFAULT_READINESS)
    parser.add_argument("--service-report", type=Path, default=DEFAULT_SERVICE)
    parser.add_argument("--formal-host-rollback-evidence", type=Path, default=DEFAULT_FORMAL_HOST_ROLLBACK)
    parser.add_argument("--formal-delete-guard-evidence", type=Path, default=DEFAULT_FORMAL_DELETE_GUARD)
    parser.add_argument(
        "--formal-filesystem-boundary-evidence",
        type=Path,
        default=DEFAULT_FORMAL_FILESYSTEM_BOUNDARY,
    )
    parser.add_argument("--host-egress-component", type=Path, default=DEFAULT_HOST_EGRESS_COMPONENT)
    parser.add_argument("--formal-egress-sandbox-evidence", type=Path, default=DEFAULT_FORMAL_EGRESS_SANDBOX)
    parser.add_argument("--formal-structured-audit-evidence", type=Path, default=DEFAULT_FORMAL_STRUCTURED_AUDIT)
    parser.add_argument("--memory-write-evidence", type=Path, default=DEFAULT_MEMORY_WRITE_EVIDENCE)
    parser.add_argument("--ab-summary", type=Path)
    parser.add_argument("--ab-prerequisites", type=Path)
    parser.add_argument("--formal-fallback-drill-evidence", type=Path, default=DEFAULT_FORMAL_FALLBACK_DRILL)
    parser.add_argument("--review-record", type=Path)
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--require-go", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = build_report(
            project_root=args.project_root,
            readiness_path=args.readiness,
            service_path=args.service_report,
            formal_host_rollback_path=args.formal_host_rollback_evidence,
            formal_delete_guard_path=args.formal_delete_guard_evidence,
            formal_filesystem_boundary_path=args.formal_filesystem_boundary_evidence,
            host_egress_component_path=args.host_egress_component,
            formal_egress_sandbox_path=args.formal_egress_sandbox_evidence,
            formal_structured_audit_path=args.formal_structured_audit_evidence,
            memory_write_evidence_path=args.memory_write_evidence,
            ab_summary_path=args.ab_summary,
            ab_prerequisites_path=args.ab_prerequisites,
            formal_fallback_drill_path=args.formal_fallback_drill_evidence,
            review_record_path=args.review_record,
        )
    except CompletionConfigurationError as exc:
        print(json.dumps({"ok": False, "error_code": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    if args.json_output:
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    else:
        print(f"{report['decision']} SIQ OpenShell V0.6 completion: {report['passed_count']}/{report['total_count']}")
    if args.require_go and report["decision"] != "GO":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
