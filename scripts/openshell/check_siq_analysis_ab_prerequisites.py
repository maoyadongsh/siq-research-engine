#!/usr/bin/env python3
"""Prerequisite gate for a real siq_analysis Host/OpenShell A/B run.

The checker performs one authenticated, read-only Host capability probe. It
never prints keys, prompts, datasets, capability bodies, or provider credential
values. Its private v3 report binds every input needed for fail-closed
revalidation immediately before the evaluator's first model request.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

try:
    from scripts.openshell import prepare_siq_analysis_ab_eval as ab_prepare, run_siq_analysis_ab_eval as ab_eval
except ModuleNotFoundError:  # direct execution from scripts/openshell
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.openshell import prepare_siq_analysis_ab_eval as ab_prepare, run_siq_analysis_ab_eval as ab_eval


SCHEMA_VERSION = "siq.openshell.siq-analysis-ab-prerequisites.v3"
LEGACY_SCHEMA_VERSIONS = frozenset(
    {
        "siq.openshell.siq-analysis-ab-prerequisites.v1",
        "siq.openshell.siq-analysis-ab-prerequisites.v2",
    }
)
PROVENANCE_SCHEMA_VERSION = ab_prepare.PROVENANCE_SCHEMA
REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILE = "siq_analysis"
HERMES_COMMIT = "ddb8d8fa842283ef651a6e4514f8f561f736c72e"
HOST_ANALYSIS_PORT = 18651
FORBIDDEN_ASSISTANT_PORT = 18642
DEFAULT_OPENSHELL_PORT = 28651
MAX_KEY_BYTES = 4096
SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
FORBIDDEN_EVALUATION_RE = re.compile(r"(?:synthetic|fixture|fake|test)", re.IGNORECASE)
SERVICE_CONTRACT = {
    "qwen_local": (8004, False),
    "gemma_local": (8006, False),
    "nemotron_local": (8007, False),
    "embedding": (8013, True),
    "postgres": (15432, True),
    "milvus": (19530, True),
    "siq_api": (18081, True),
    "hermes_host": (18651, True),
}
SERVICE_PROTOCOL_CONTRACT = {
    "qwen_local": ("openai_models_list_v1", "/v1/models"),
    "gemma_local": ("openai_models_list_v1", "/v1/models"),
    "nemotron_local": ("openai_models_list_v1", "/v1/models"),
    "embedding": ("openai_models_list_v1", "/v1/models"),
    "siq_api": ("status_ok_json_v1", "/health"),
    "hermes_host": ("status_ok_json_v1", "/health"),
}
BROKER_CONTRACT = {"egress": 18792, "data": 18793}
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
EVIDENCE_MAX_AGE_SECONDS = {
    "provider_inventory": 15 * 60,
    "service_report": 5 * 60,
    "broker_report": 60,
}
CLOCK_SKEW_SECONDS = 5
PREREQUISITE_MAX_BYTES = 1024 * 1024
OUTPUT_ROOT_RELATIVE = Path("var/openshell/eval")
OUTPUT_NAME = "prerequisites.json"
EVIDENCE_BINDING_FIELDS = {
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
REPORT_FIELDS = {
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
COMMON_PROVENANCE_FIELDS = (
    "hermes_commit",
    "profile_sha256",
    "model_route_sha256",
    "tools_sha256",
    "data_snapshot_sha256",
)


class PrerequisiteError(RuntimeError):
    """Stable configuration error without user or machine content."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalized_now(value: datetime | None) -> datetime:
    current = value or _utc_now()
    if current.tzinfo is None or current.utcoffset() is None:
        raise PrerequisiteError("prerequisite_clock_invalid")
    return current.astimezone(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: Any, *, code: str) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise PrerequisiteError(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PrerequisiteError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PrerequisiteError(code)
    return parsed.astimezone(timezone.utc)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError("duplicate_json_key")
        payload[key] = value
    return payload


def _safe_file(path: Path, *, mode: int | None = None) -> Path:
    if not path.is_absolute():
        path = Path.cwd() / path
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            info = current.lstat()
        except FileNotFoundError as exc:
            raise PrerequisiteError("input_file_missing") from exc
        if stat.S_ISLNK(info.st_mode):
            raise PrerequisiteError("input_file_symlink")
    try:
        info = path.stat()
    except OSError as exc:
        raise PrerequisiteError("input_file_unreadable") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise PrerequisiteError("input_file_regular_required")
    if info.st_uid != os.geteuid():
        raise PrerequisiteError("input_file_owner_invalid")
    if mode is not None and stat.S_IMODE(info.st_mode) != mode:
        raise PrerequisiteError("input_file_permissions_invalid")
    return path


def _read_limited(path: Path, max_bytes: int) -> bytes:
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
            raise PrerequisiteError("input_file_identity_changed")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, max_bytes - total + 1))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise PrerequisiteError("input_file_too_large")
        content = b"".join(chunks)
    except PrerequisiteError:
        raise
    except OSError as exc:
        raise PrerequisiteError("input_file_unreadable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return content


def _read_snapshot(path: Path, *, max_bytes: int) -> tuple[Path, bytes, os.stat_result]:
    """Read one owner-controlled file and bind bytes to its filesystem identity."""

    checked = _safe_file(path)
    descriptor = -1
    try:
        descriptor = os.open(checked, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.geteuid()
            or before.st_size <= 0
            or before.st_size > max_bytes
        ):
            raise PrerequisiteError("input_file_identity_changed")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) > max_bytes:
            raise PrerequisiteError("input_file_too_large")
        after = os.fstat(descriptor)
        identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in identity):
            raise PrerequisiteError("input_file_identity_changed")
        current = checked.stat()
        if any(getattr(after, field) != getattr(current, field) for field in identity):
            raise PrerequisiteError("input_file_identity_changed")
    except PrerequisiteError:
        raise
    except OSError as exc:
        raise PrerequisiteError("input_file_unreadable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return checked.resolve(strict=True), content, after


def _load_json_snapshot(
    path: Path,
    *,
    max_bytes: int,
    error_code: str,
) -> tuple[Path, Mapping[str, Any], bytes, os.stat_result]:
    checked, content, info = _read_snapshot(path, max_bytes=max_bytes)
    try:
        payload = json.loads(content, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PrerequisiteError(error_code) from exc
    if not isinstance(payload, dict):
        raise PrerequisiteError(error_code)
    return checked, payload, content, info


def _evidence_binding(
    path: Path,
    *,
    max_bytes: int,
    max_age_seconds: int,
    now: datetime,
    error_code: str,
) -> tuple[Mapping[str, Any], dict[str, Any]]:
    checked, payload, content, info = _load_json_snapshot(
        path,
        max_bytes=max_bytes,
        error_code=error_code,
    )
    generated_at = datetime.fromtimestamp(info.st_mtime_ns / 1_000_000_000, timezone.utc)
    expires_at = generated_at + timedelta(seconds=max_age_seconds)
    if generated_at > now + timedelta(seconds=CLOCK_SKEW_SECONDS):
        raise PrerequisiteError(f"{error_code}_timestamp_invalid")
    if now > expires_at:
        raise PrerequisiteError(f"{error_code}_stale")
    return payload, {
        "path": str(checked),
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": info.st_size,
        "device": info.st_dev,
        "inode": info.st_ino,
        "mode": stat.S_IMODE(info.st_mode),
        "mtime_ns": info.st_mtime_ns,
        "ctime_ns": info.st_ctime_ns,
        "generated_at": _format_timestamp(generated_at),
        "expires_at": _format_timestamp(expires_at),
    }


def _validate_url(value: str, *, role: str, forbidden_ports: set[int] = frozenset()) -> dict[str, Any]:
    try:
        normalized = ab_eval.normalize_runs_url(value)
        parsed = urlsplit(normalized)
        port = parsed.port
    except (ab_eval.EvaluationConfigurationError, AttributeError, ValueError) as exc:
        raise PrerequisiteError(f"{role}_url_invalid") from exc
    if port is None:
        raise PrerequisiteError(f"{role}_url_invalid")
    if port in forbidden_ports:
        raise PrerequisiteError(f"{role}_port_forbidden")
    return {"scheme": "http", "port": port, "path": "/v1/runs", "normalized": normalized}


def _key_fingerprint(path: Path) -> tuple[bytes, str]:
    checked = _safe_file(path, mode=0o600)
    content = _read_limited(checked, MAX_KEY_BYTES).strip()
    if (
        not 16 <= len(content) <= 1024
        or b"\x00" in content
        or any(byte <= 32 or byte > 126 for byte in content)
        or content.lower().startswith(b"bearer")
    ):
        raise PrerequisiteError("api_key_file_invalid")
    return content, hashlib.sha256(content).hexdigest()


def _load_provider_names(payload: Mapping[str, Any]) -> set[str]:
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != "siq.openshell.provider_inventory.v1"
        or payload.get("openshell_version") != "0.0.83"
        or payload.get("gateway") != "siq-openshell-dev"
        or not isinstance(payload.get("providers"), list)
    ):
        raise PrerequisiteError("provider_inventory_invalid")
    raw_names = payload["providers"]
    names: set[str] = set()
    for item in raw_names:
        name = item.get("name") if isinstance(item, dict) else None
        if (
            not isinstance(name, str)
            or not SAFE_ID_RE.fullmatch(name)
            or item.get("state") not in {"ready", "configured", "online"}
        ):
            raise PrerequisiteError("provider_inventory_invalid")
        names.add(name)
    return names


def _load_service_report(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_version") != "siq.openshell.service_preflight.v2":
        raise PrerequisiteError("service_report_invalid")
    services = payload.get("services")
    if not isinstance(services, list) or {item.get("service_id") for item in services if isinstance(item, dict)} != set(
        SERVICE_CONTRACT
    ):
        raise PrerequisiteError("service_report_invalid")
    for item in services:
        if not isinstance(item, dict):
            raise PrerequisiteError("service_report_invalid")
        service_id = item["service_id"]
        expected_port, required = SERVICE_CONTRACT[service_id]
        if (
            item.get("port") != expected_port
            or item.get("requirement") != ("required" if required else "optional")
            or item.get("blocking") is not required
            or not isinstance(item.get("reachable"), bool)
        ):
            raise PrerequisiteError("service_report_invalid")
        protocol = item.get("protocol_check")
        expected_protocol = SERVICE_PROTOCOL_CONTRACT.get(service_id)
        if not isinstance(protocol, dict):
            raise PrerequisiteError("service_report_invalid")
        if expected_protocol is None:
            if (
                protocol.get("contract") != "not_applicable"
                or protocol.get("checked") is not False
                or protocol.get("available") is not None
                or protocol.get("status") != "not_applicable"
                or protocol.get("method") != ""
                or protocol.get("path") != ""
            ):
                raise PrerequisiteError("service_report_invalid")
            service_available = item["reachable"] is True
        else:
            if (
                protocol.get("contract") != expected_protocol[0]
                or protocol.get("method") != "GET"
                or protocol.get("path") != expected_protocol[1]
                or protocol.get("checked") is not item["reachable"]
            ):
                raise PrerequisiteError("service_report_invalid")
            if item["reachable"] is True:
                if not isinstance(protocol.get("available"), bool):
                    raise PrerequisiteError("service_report_invalid")
                service_available = protocol["available"] is True
                expected_status = "pass" if service_available else ("no_go" if required else "warning")
                if protocol.get("status") != expected_status:
                    raise PrerequisiteError("service_report_invalid")
            else:
                service_available = False
                if protocol.get("available") is not False or protocol.get("status") != "not_run":
                    raise PrerequisiteError("service_report_invalid")
        expected_service_status = "pass" if service_available else ("no_go" if required else "warning")
        if item.get("status") != expected_service_status:
            raise PrerequisiteError("service_report_invalid")
        if required and not service_available:
            raise PrerequisiteError("service_preflight_not_go")
    checks = payload.get("security_checks")
    if not isinstance(checks, list) or {item.get("check_id") for item in checks if isinstance(item, dict)} != {
        "postgres_readonly_identity",
        "milvus_write_protection",
    }:
        raise PrerequisiteError("service_security_proof_missing")
    if any(
        not isinstance(item, dict)
        or item.get("status") != "pass"
        or item.get("proof_present") is not True
        or item.get("proof_source") not in {"cli", "proof_file"}
        for item in checks
    ):
        raise PrerequisiteError("service_security_proof_missing")
    scope = payload.get("probe_scope")
    if (
        not isinstance(scope, dict)
        or scope.get("read_only") is not True
        or scope.get("host_alias_kind") != "loopback"
        or scope.get("protocol") != "tcp_connect_plus_read_only_http_get"
        or scope.get("http_method") != "GET"
        or scope.get("request_body_sent") is not False
        or scope.get("redirects_followed") is not False
        or scope.get("response_body_recorded") is not False
    ):
        raise PrerequisiteError("service_probe_scope_invalid")
    summary = payload.get("summary")
    if (
        not isinstance(summary, dict)
        or summary.get("required_reachable") != summary.get("required_total")
        or summary.get("required_protocol_available") != summary.get("required_protocol_total")
        or summary.get("required_total") != 5
        or summary.get("required_protocol_total") != 3
    ):
        raise PrerequisiteError("service_report_invalid")
    return payload


def _load_broker_report(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != "siq.openshell.broker-lifecycle.v1"
        or payload.get("ok") is not True
        or payload.get("bridge") != {"network": "siq-openshell-dev", "alias": "host.openshell.internal"}
    ):
        raise PrerequisiteError("broker_report_invalid")
    brokers = payload.get("brokers")
    if not isinstance(brokers, dict) or set(brokers) != set(BROKER_CONTRACT):
        raise PrerequisiteError("broker_report_invalid")
    if any(
        not isinstance(item, dict)
        or item.get("state") != "running"
        or item.get("port") != BROKER_CONTRACT[name]
        or item.get("request_identity_required") is not True
        for name, item in brokers.items()
    ):
        raise PrerequisiteError("broker_report_not_running")
    return payload


def _validate_dataset(path: Path) -> dict[str, Any]:
    checked = _safe_file(path, mode=0o600)
    try:
        content = _read_limited(checked, ab_eval.MAX_DATASET_BYTES)
        payload = json.loads(content)
        digest = hashlib.sha256(content).hexdigest()
        dataset = ab_eval.parse_dataset(payload, sha256=digest)
    except ab_eval.EvaluationConfigurationError as exc:
        raise PrerequisiteError("dataset_schema_invalid") from exc
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise PrerequisiteError("dataset_invalid") from exc
    if not isinstance(payload, dict) or payload.get("profile") != PROFILE:
        raise PrerequisiteError("dataset_profile_invalid")
    normal_case_count = sum(not case.expectations.policy_denial_expected for case in dataset.cases)
    fallback_case_count = sum(case.expectations.fallback_expected is True for case in dataset.cases)
    fallback_telemetry_case_count = sum(case.expectations.fallback_expected is not None for case in dataset.cases)
    expected_metric_samples = {
        "citations": sum(len(case.expectations.citations) for case in dataset.cases) * dataset.repetitions,
        "numeric": sum(len(case.expectations.numeric) for case in dataset.cases) * dataset.repetitions,
        "hallucination": sum(case.expectations.abstention_required for case in dataset.cases)
        * dataset.repetitions,
        "evidence": sum(len(case.expectations.evidence_ids) for case in dataset.cases) * dataset.repetitions,
        "tools": sum(len(case.expectations.required_tools) for case in dataset.cases) * dataset.repetitions,
        "sections": sum(len(case.expectations.required_sections) for case in dataset.cases) * dataset.repetitions,
    }
    if (
        dataset.profile != PROFILE
        or dataset.repetitions < ab_eval.MIN_EVALUATION_REPETITIONS
        or len(dataset.cases) < ab_eval.MIN_EVALUATION_CASES
        or normal_case_count * dataset.repetitions < ab_eval.MIN_POLICY_NORMAL_SAMPLES
        or fallback_case_count != 0
        or fallback_telemetry_case_count != 0
        or any(value < ab_eval.MIN_PRIMARY_METRIC_SAMPLES for value in expected_metric_samples.values())
    ):
        raise PrerequisiteError("dataset_contract_invalid")
    return {
        "schema_version": ab_eval.DATASET_SCHEMA_VERSION,
        "sha256": digest,
        "case_count": len(dataset.cases),
        "repetitions": dataset.repetitions,
        "normal_case_count": normal_case_count,
        "fallback_case_count": fallback_case_count,
    }


def _load_provenance(
    path: Path,
    *,
    evaluation_id: str,
    dataset_sha256: str,
    host_runs_url: str,
    host_api_key_file: Path,
) -> dict[str, Any]:
    checked = _safe_file(path, mode=0o600)
    content = _read_limited(checked, 256 * 1024)
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrerequisiteError("ab_provenance_invalid") from exc
    if (
        not isinstance(payload, dict)
        or set(payload)
        != {
            "schema_version",
            "evaluation_id",
            "profile",
            "dataset_sha256",
            "arms",
            "runtime_attestation",
            "sources",
        }
        or payload.get("schema_version") != PROVENANCE_SCHEMA_VERSION
        or payload.get("evaluation_id") != evaluation_id
        or payload.get("profile") != PROFILE
        or payload.get("dataset_sha256") != dataset_sha256
    ):
        raise PrerequisiteError("ab_provenance_invalid")
    arms = payload.get("arms")
    if not isinstance(arms, dict) or set(arms) != {"host", "openshell"}:
        raise PrerequisiteError("ab_provenance_invalid")
    host = arms["host"]
    openshell = arms["openshell"]
    host_fields = {
        "runtime",
        *COMMON_PROVENANCE_FIELDS,
        "host_key_receipt_sha256",
        "host_runtime_receipt_sha256",
        "runtime_contract_sha256",
    }
    openshell_fields = {
        "runtime",
        *COMMON_PROVENANCE_FIELDS,
        "image_id",
        "policy_sha256",
        "mount_plan_sha256",
        "mount_contract_sha256",
        "runtime_config_sha256",
    }
    if (
        not isinstance(host, dict)
        or not isinstance(openshell, dict)
        or set(host) != host_fields
        or set(openshell) != openshell_fields
        or host.get("runtime") != "host"
        or openshell.get("runtime") != "openshell"
        or host.get("hermes_commit") != HERMES_COMMIT
        or openshell.get("hermes_commit") != HERMES_COMMIT
        or not isinstance(openshell.get("image_id"), str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", openshell["image_id"])
    ):
        raise PrerequisiteError("ab_provenance_invalid")
    for field in COMMON_PROVENANCE_FIELDS[1:]:
        if (
            not isinstance(host.get(field), str)
            or not SHA256_RE.fullmatch(host[field])
            or openshell.get(field) != host[field]
        ):
            raise PrerequisiteError("ab_provenance_arms_mismatch")
    for field in ("policy_sha256", "mount_plan_sha256", "mount_contract_sha256", "runtime_config_sha256"):
        if not isinstance(openshell.get(field), str) or not SHA256_RE.fullmatch(openshell[field]):
            raise PrerequisiteError("ab_provenance_invalid")
    attestation = payload.get("runtime_attestation")
    if (
        not isinstance(attestation, dict)
        or set(attestation)
        != {
            "context_sha256",
            "hermes_patch_sha256",
            "source_config_sha256",
            "compiled_config_sha256",
            "primary_provider",
            "primary_model",
            "fallback_route_sha256",
            "temperature_kind",
            "request_temperature",
            "host_runtime_metadata_v1",
            "host_candidate_source_match",
            "arms_match",
        }
        or attestation.get("arms_match") is not True
        or attestation.get("host_runtime_metadata_v1") is not True
        or attestation.get("host_candidate_source_match") is not True
        or attestation.get("compiled_config_sha256") != openshell.get("runtime_config_sha256")
        or any(
            not isinstance(attestation.get(field), str) or not SHA256_RE.fullmatch(attestation[field])
            for field in (
                "context_sha256",
                "hermes_patch_sha256",
                "source_config_sha256",
                "compiled_config_sha256",
                "fallback_route_sha256",
            )
        )
        or any(
            not isinstance(attestation.get(field), str)
            or not ab_eval.SAFE_RUNTIME_LABEL_RE.fullmatch(attestation[field])
            or "://" in attestation[field]
            for field in ("primary_provider", "primary_model")
        )
        or attestation.get("temperature_kind") not in {"explicit", "provider_default"}
        or isinstance(attestation.get("request_temperature"), bool)
        or not isinstance(attestation.get("request_temperature"), (int, float))
    ):
        raise PrerequisiteError("ab_provenance_invalid")
    sources = payload.get("sources")
    if not isinstance(sources, dict) or set(sources) != ab_prepare.PROVENANCE_SOURCE_NAMES:
        raise PrerequisiteError("ab_provenance_invalid")
    try:
        source_contents = {
            name: ab_prepare.recapture_source_binding(binding, maximum=16 * 1024 * 1024)
            for name, binding in sources.items()
        }
        ab_prepare._validate_candidate_source_manifest(
            source_contents["candidate_files_manifest"],
            context_sha256=attestation["context_sha256"],
            api_server_sha256=sources["candidate_api_server"]["sha256"],
            run_agent_sha256=sources["candidate_run_agent"]["sha256"],
        )
    except ab_prepare.PreparationError as exc:
        raise PrerequisiteError("ab_provenance_source_drift") from exc
    try:
        runtime_receipt = ab_prepare.verify_host_runtime_receipts(
            project_root=REPO_ROOT,
            host_runs_url=host_runs_url,
            host_api_key_file=host_api_key_file,
            host_key_receipt_path=Path(sources["host_key_receipt"]["path"]),
            host_runtime_receipt_path=Path(sources["host_runtime_receipt"]["path"]),
        )
    except (KeyError, TypeError, ab_prepare.PreparationError) as exc:
        code = str(exc) if isinstance(exc, ab_prepare.PreparationError) else "host_runtime_receipt_invalid"
        raise PrerequisiteError(code) from exc
    listener = runtime_receipt.get("listener")
    capabilities = runtime_receipt.get("capabilities")
    if (
        not isinstance(listener, dict)
        or not isinstance(capabilities, dict)
        or host.get("host_key_receipt_sha256") != sources["host_key_receipt"]["sha256"]
        or host.get("host_runtime_receipt_sha256") != sources["host_runtime_receipt"]["sha256"]
        or host.get("runtime_contract_sha256") != capabilities.get("document_sha256")
        or listener.get("api_server_sha256") != sources["candidate_api_server"]["sha256"]
        or listener.get("run_agent_sha256") != sources["candidate_run_agent"]["sha256"]
    ):
        raise PrerequisiteError("host_candidate_runtime_source_mismatch")
    return {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "sha256": hashlib.sha256(content).hexdigest(),
        "hermes_commit": HERMES_COMMIT,
        "host_runtime_verified": True,
        "host_runtime_receipt_sha256": sources["host_runtime_receipt"]["sha256"],
        "runtime_contract_sha256": capabilities["document_sha256"],
        "host_candidate_source_match": True,
        "arms_match": True,
    }


def build_report(
    *,
    host_runs_url: str,
    openshell_runs_url: str,
    host_api_key_file: Path,
    openshell_api_key_file: Path,
    dataset_file: Path,
    evaluation_id: str,
    provenance_report: Path,
    provider_inventory: Path,
    service_report: Path,
    broker_report: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    checked_at = _normalized_now(now)
    blockers: list[str] = []
    try:
        host = _validate_url(host_runs_url, role="host", forbidden_ports={FORBIDDEN_ASSISTANT_PORT})
        if host["port"] != HOST_ANALYSIS_PORT:
            blockers.append("host_analysis_port_must_be_18651")
    except PrerequisiteError as exc:
        host = {"valid": False}
        blockers.append(str(exc))
    try:
        openshell = _validate_url(
            openshell_runs_url,
            role="openshell",
            forbidden_ports={FORBIDDEN_ASSISTANT_PORT, HOST_ANALYSIS_PORT},
        )
        if openshell["port"] != DEFAULT_OPENSHELL_PORT:
            blockers.append("openshell_analysis_port_must_be_28651")
        openshell["expected_port"] = DEFAULT_OPENSHELL_PORT
    except PrerequisiteError as exc:
        openshell = {"valid": False}
        blockers.append(str(exc))
    if (
        host.get("valid", True)
        and openshell.get("valid", True)
        and host.get("normalized") == openshell.get("normalized")
    ):
        blockers.append("ab_endpoints_must_differ")

    key_fingerprints: dict[str, str] = {}
    host_key = b""
    try:
        host_key, host_digest = _key_fingerprint(host_api_key_file)
        open_key, open_digest = _key_fingerprint(openshell_api_key_file)
        if hmac.compare_digest(host_key, open_key):
            blockers.append("api_keys_must_differ")
        key_fingerprints = {"host": host_digest, "openshell": open_digest}
    except PrerequisiteError as exc:
        blockers.append(str(exc))

    if not SAFE_ID_RE.fullmatch(evaluation_id) or FORBIDDEN_EVALUATION_RE.search(evaluation_id):
        blockers.append("evaluation_id_invalid_or_non_live")
    dataset: dict[str, Any] = {}
    try:
        dataset = _validate_dataset(dataset_file)
    except PrerequisiteError as exc:
        blockers.append(str(exc))
    provenance: dict[str, Any] = {}
    if dataset and host_key and host.get("normalized") == ab_prepare.HOST_RUNS_URL:
        try:
            provenance = _load_provenance(
                provenance_report,
                evaluation_id=evaluation_id,
                dataset_sha256=str(dataset["sha256"]),
                host_runs_url=str(host["normalized"]),
                host_api_key_file=host_api_key_file,
            )
        except PrerequisiteError as exc:
            blockers.append(str(exc))
    else:
        blockers.append(
            "ab_provenance_dataset_unavailable" if not dataset else "ab_provenance_host_runtime_unavailable"
        )

    evidence: dict[str, dict[str, Any]] = {}
    providers: set[str] = set()
    try:
        provider_payload, provider_binding = _evidence_binding(
            provider_inventory,
            max_bytes=64 * 1024,
            max_age_seconds=EVIDENCE_MAX_AGE_SECONDS["provider_inventory"],
            now=checked_at,
            error_code="provider_inventory_invalid",
        )
        providers = _load_provider_names(provider_payload)
        evidence["provider_inventory"] = provider_binding
    except PrerequisiteError as exc:
        blockers.append(str(exc))
    required_providers = set(ab_eval.PROVIDERS) if hasattr(ab_eval, "PROVIDERS") else {
        "siq-minimax-cn-pool",
        "siq-stepfun",
        "siq-kimi-coding",
        "siq-tavily-search",
    }
    missing_providers = sorted(required_providers - providers)
    if missing_providers:
        blockers.append("required_providers_missing")

    try:
        service_payload, service_binding = _evidence_binding(
            service_report,
            max_bytes=512 * 1024,
            max_age_seconds=EVIDENCE_MAX_AGE_SECONDS["service_report"],
            now=checked_at,
            error_code="service_report_invalid",
        )
        service = _load_service_report(service_payload)
        evidence["service_report"] = service_binding
        if service.get("decision") != "GO" or service.get("passed") is not True:
            blockers.append("service_preflight_not_go")
    except PrerequisiteError as exc:
        service = {}
        blockers.append(str(exc))
    try:
        broker_payload, broker_binding = _evidence_binding(
            broker_report,
            max_bytes=512 * 1024,
            max_age_seconds=EVIDENCE_MAX_AGE_SECONDS["broker_report"],
            now=checked_at,
            error_code="broker_report_invalid",
        )
        _load_broker_report(broker_payload)
        evidence["broker_report"] = broker_binding
    except PrerequisiteError as exc:
        blockers.append(str(exc))

    expires_at = min(
        (
            _parse_timestamp(binding["expires_at"], code="evidence_expiry_invalid")
            for binding in evidence.values()
        ),
        default=checked_at,
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "decision": "GO" if not blockers else "NO_GO",
        "profile": PROFILE,
        "evaluation_id": evaluation_id if SAFE_ID_RE.fullmatch(evaluation_id) else None,
        "host": {**host, "analysis_port": HOST_ANALYSIS_PORT},
        "openshell": openshell,
        "dataset": dataset,
        "provenance": provenance,
        "evaluation_id_valid": SAFE_ID_RE.fullmatch(evaluation_id) is not None,
        "key_fingerprints": key_fingerprints,
        "evidence": evidence,
        "provider_count": len(providers),
        "missing_provider_count": len(missing_providers),
        "service_preflight_decision": service.get("decision"),
        "blockers": sorted(set(blockers)),
        "network_probe_performed": provenance.get("host_runtime_verified") is True,
        "cutover_performed": False,
        "generated_at": _format_timestamp(checked_at),
        "expires_at": _format_timestamp(expires_at),
    }


def _validate_binding_shape(binding: Any) -> Mapping[str, Any]:
    if not isinstance(binding, dict) or set(binding) != EVIDENCE_BINDING_FIELDS:
        raise PrerequisiteError("evidence_binding_schema_invalid")
    if (
        not isinstance(binding.get("path"), str)
        or not Path(binding["path"]).is_absolute()
        or not isinstance(binding.get("sha256"), str)
        or not SHA256_RE.fullmatch(binding["sha256"])
    ):
        raise PrerequisiteError("evidence_binding_schema_invalid")
    for field in ("size_bytes", "device", "inode", "mtime_ns", "ctime_ns"):
        value = binding.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise PrerequisiteError("evidence_binding_schema_invalid")
    mode = binding.get("mode")
    if not isinstance(mode, int) or isinstance(mode, bool) or not 0 < mode <= 0o7777:
        raise PrerequisiteError("evidence_binding_schema_invalid")
    generated_at = _parse_timestamp(binding.get("generated_at"), code="evidence_binding_timestamp_invalid")
    expires_at = _parse_timestamp(binding.get("expires_at"), code="evidence_binding_timestamp_invalid")
    if expires_at <= generated_at:
        raise PrerequisiteError("evidence_binding_timestamp_invalid")
    return binding


def _recapture_bound_evidence(
    name: str,
    binding: Mapping[str, Any],
    *,
    now: datetime,
) -> Mapping[str, Any]:
    maximums = {
        "provider_inventory": 64 * 1024,
        "service_report": 512 * 1024,
        "broker_report": 512 * 1024,
    }
    try:
        payload, observed = _evidence_binding(
            Path(binding["path"]),
            max_bytes=maximums[name],
            max_age_seconds=EVIDENCE_MAX_AGE_SECONDS[name],
            now=now,
            error_code=f"{name}_invalid",
        )
    except (KeyError, TypeError) as exc:
        raise PrerequisiteError("evidence_binding_schema_invalid") from exc
    if observed != binding:
        raise PrerequisiteError(f"{name}_binding_drift")
    return payload


def validate_report_for_evaluation(
    path: Path,
    *,
    evaluation_id: str,
    dataset_sha256: str,
    host_runs_url: str,
    openshell_runs_url: str,
    host_key_fingerprint: str,
    openshell_key_fingerprint: str,
    now: datetime | None = None,
) -> tuple[Mapping[str, Any], str]:
    """Revalidate a v3 GO report and the live Host before any model request."""

    checked_at = _normalized_now(now)
    checked = _safe_file(path, mode=0o600)
    _resolved, payload, content, info = _load_json_snapshot(
        checked,
        max_bytes=PREREQUISITE_MAX_BYTES,
        error_code="prerequisites_invalid",
    )
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise PrerequisiteError("prerequisites_permissions_invalid")
    if payload.get("schema_version") in LEGACY_SCHEMA_VERSIONS:
        raise PrerequisiteError("prerequisites_legacy_contract_forbidden")
    if set(payload) != REPORT_FIELDS or payload.get("schema_version") != SCHEMA_VERSION:
        raise PrerequisiteError("prerequisites_schema_invalid")
    if (
        payload.get("decision") != "GO"
        or payload.get("profile") != PROFILE
        or payload.get("evaluation_id") != evaluation_id
        or payload.get("evaluation_id_valid") is not True
        or payload.get("blockers") != []
        or payload.get("network_probe_performed") is not True
        or payload.get("cutover_performed") is not False
    ):
        raise PrerequisiteError("prerequisites_not_go")
    if not SAFE_ID_RE.fullmatch(evaluation_id) or FORBIDDEN_EVALUATION_RE.search(evaluation_id):
        raise PrerequisiteError("evaluation_id_invalid_or_non_live")
    generated_at = _parse_timestamp(payload.get("generated_at"), code="prerequisites_timestamp_invalid")
    expires_at = _parse_timestamp(payload.get("expires_at"), code="prerequisites_timestamp_invalid")
    if generated_at > checked_at + timedelta(seconds=CLOCK_SKEW_SECONDS) or checked_at > expires_at:
        raise PrerequisiteError("prerequisites_stale")

    try:
        normalized_host = ab_eval.normalize_runs_url(host_runs_url)
        normalized_openshell = ab_eval.normalize_runs_url(openshell_runs_url)
    except ab_eval.EvaluationConfigurationError as exc:
        raise PrerequisiteError("prerequisites_endpoint_invalid") from exc
    expected_host = {
        "scheme": "http",
        "port": HOST_ANALYSIS_PORT,
        "path": "/v1/runs",
        "normalized": normalized_host,
        "analysis_port": HOST_ANALYSIS_PORT,
    }
    expected_openshell = {
        "scheme": "http",
        "port": DEFAULT_OPENSHELL_PORT,
        "path": "/v1/runs",
        "normalized": normalized_openshell,
        "expected_port": DEFAULT_OPENSHELL_PORT,
    }
    if (
        urlsplit(normalized_host).port != HOST_ANALYSIS_PORT
        or urlsplit(normalized_openshell).port != DEFAULT_OPENSHELL_PORT
        or payload.get("host") != expected_host
        or payload.get("openshell") != expected_openshell
        or normalized_host == normalized_openshell
    ):
        raise PrerequisiteError("prerequisites_endpoint_drift")

    dataset = payload.get("dataset")
    if (
        not isinstance(dataset, dict)
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
        or dataset.get("sha256") != dataset_sha256
        or not isinstance(dataset.get("case_count"), int)
        or isinstance(dataset.get("case_count"), bool)
        or dataset["case_count"] < ab_eval.MIN_EVALUATION_CASES
        or not isinstance(dataset.get("repetitions"), int)
        or isinstance(dataset.get("repetitions"), bool)
        or dataset["repetitions"] < ab_eval.MIN_EVALUATION_REPETITIONS
    ):
        raise PrerequisiteError("prerequisites_dataset_drift")

    fingerprints = payload.get("key_fingerprints")
    if (
        not isinstance(fingerprints, dict)
        or set(fingerprints) != {"host", "openshell"}
        or fingerprints.get("host") != host_key_fingerprint
        or fingerprints.get("openshell") != openshell_key_fingerprint
        or not SHA256_RE.fullmatch(host_key_fingerprint)
        or not SHA256_RE.fullmatch(openshell_key_fingerprint)
        or hmac.compare_digest(host_key_fingerprint, openshell_key_fingerprint)
    ):
        raise PrerequisiteError("prerequisites_api_key_drift")

    provenance = payload.get("provenance")
    if (
        not isinstance(provenance, dict)
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
        or provenance.get("schema_version") != PROVENANCE_SCHEMA_VERSION
        or not isinstance(provenance.get("sha256"), str)
        or not SHA256_RE.fullmatch(provenance["sha256"])
        or provenance.get("hermes_commit") != HERMES_COMMIT
        or provenance.get("host_runtime_verified") is not True
        or provenance.get("host_candidate_source_match") is not True
        or not isinstance(provenance.get("host_runtime_receipt_sha256"), str)
        or not SHA256_RE.fullmatch(provenance["host_runtime_receipt_sha256"])
        or not isinstance(provenance.get("runtime_contract_sha256"), str)
        or not SHA256_RE.fullmatch(provenance["runtime_contract_sha256"])
        or provenance.get("arms_match") is not True
    ):
        raise PrerequisiteError("prerequisites_provenance_invalid")

    live_provenance = _load_provenance(
        checked.with_name("provenance.json"),
        evaluation_id=evaluation_id,
        dataset_sha256=dataset_sha256,
        host_runs_url=normalized_host,
        host_api_key_file=checked.with_name("host.key"),
    )
    if live_provenance != provenance:
        raise PrerequisiteError("prerequisites_provenance_drift")

    evidence = payload.get("evidence")
    if not isinstance(evidence, dict) or set(evidence) != set(EVIDENCE_MAX_AGE_SECONDS):
        raise PrerequisiteError("evidence_binding_schema_invalid")
    bindings = {name: _validate_binding_shape(evidence[name]) for name in EVIDENCE_MAX_AGE_SECONDS}
    expected_expiry = min(
        _parse_timestamp(binding["expires_at"], code="evidence_binding_timestamp_invalid")
        for binding in bindings.values()
    )
    if expires_at != expected_expiry:
        raise PrerequisiteError("prerequisites_expiry_drift")

    provider_payload = _recapture_bound_evidence("provider_inventory", bindings["provider_inventory"], now=checked_at)
    providers = _load_provider_names(provider_payload)
    required_providers = set(getattr(ab_eval, "PROVIDERS", ())) or {
        "siq-minimax-cn-pool",
        "siq-stepfun",
        "siq-kimi-coding",
        "siq-tavily-search",
    }
    if (
        required_providers - providers
        or payload.get("provider_count") != len(providers)
        or payload.get("missing_provider_count") != 0
    ):
        raise PrerequisiteError("prerequisites_provider_drift")

    service_payload = _recapture_bound_evidence("service_report", bindings["service_report"], now=checked_at)
    service = _load_service_report(service_payload)
    if (
        service.get("decision") != "GO"
        or service.get("passed") is not True
        or payload.get("service_preflight_decision") != "GO"
    ):
        raise PrerequisiteError("prerequisites_service_drift")
    broker_payload = _recapture_bound_evidence("broker_report", bindings["broker_report"], now=checked_at)
    _load_broker_report(broker_payload)
    return payload, hashlib.sha256(content).hexdigest()


def _canonical_report(report: Mapping[str, Any]) -> bytes:
    content = (json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("ascii")
    if not 0 < len(content) <= PREREQUISITE_MAX_BYTES:
        raise PrerequisiteError("prerequisites_output_size_invalid")
    return content


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _safe_output_parent(
    *,
    project_root: Path,
    evaluation_id: str,
    output: Path,
) -> tuple[Path, Path, os.stat_result]:
    if not SAFE_ID_RE.fullmatch(evaluation_id) or FORBIDDEN_EVALUATION_RE.search(evaluation_id):
        raise PrerequisiteError("evaluation_id_invalid_or_non_live")
    try:
        absolute_root = project_root.absolute()
        root = project_root.resolve(strict=True)
        root_info = project_root.lstat()
    except OSError as exc:
        raise PrerequisiteError("prerequisites_output_root_invalid") from exc
    if (
        root != absolute_root
        or stat.S_ISLNK(root_info.st_mode)
        or not stat.S_ISDIR(root_info.st_mode)
        or root_info.st_uid != os.geteuid()
        or stat.S_IMODE(root_info.st_mode) & 0o002
    ):
        raise PrerequisiteError("prerequisites_output_root_invalid")

    relative = OUTPUT_ROOT_RELATIVE / evaluation_id / OUTPUT_NAME
    expected = root / relative
    if output.is_absolute():
        candidate = output
    else:
        if output != relative:
            raise PrerequisiteError("prerequisites_output_path_invalid")
        candidate = root / output
    if candidate != expected:
        raise PrerequisiteError("prerequisites_output_path_invalid")

    current = root
    parent_info = root_info
    for index, component in enumerate(("var", "openshell", "eval", evaluation_id)):
        current /= component
        try:
            info = current.lstat()
        except OSError as exc:
            raise PrerequisiteError("prerequisites_output_parent_invalid") from exc
        mode = stat.S_IMODE(info.st_mode)
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or mode & 0o002
            or (index >= 1 and mode != 0o700)
        ):
            raise PrerequisiteError("prerequisites_output_parent_invalid")
        parent_info = info
    return expected, current, parent_info


def _validate_output_info(info: os.stat_result) -> None:
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise PrerequisiteError("prerequisites_output_file_invalid")


def _stat_at(directory_descriptor: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PrerequisiteError("prerequisites_output_file_invalid") from exc


def _read_output_at(directory_descriptor: int, name: str, expected: bytes) -> os.stat_result:
    info = _stat_at(directory_descriptor, name)
    if info is None:
        raise PrerequisiteError("prerequisites_output_file_invalid")
    _validate_output_info(info)
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_descriptor,
        )
        opened = os.fstat(descriptor)
        _validate_output_info(opened)
        if not _same_identity(info, opened):
            raise PrerequisiteError("prerequisites_output_file_changed")
        chunks: list[bytes] = []
        remaining = PREREQUISITE_MAX_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        if b"".join(chunks) != expected:
            raise PrerequisiteError("prerequisites_output_file_changed")
    except PrerequisiteError:
        raise
    except OSError as exc:
        raise PrerequisiteError("prerequisites_output_file_changed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return opened


def _unlink_if_identity(directory_descriptor: int, name: str, expected: os.stat_result | None) -> None:
    if expected is None:
        return
    try:
        current = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        if _same_identity(current, expected):
            os.unlink(name, dir_fd=directory_descriptor)
    except FileNotFoundError:
        return


def write_report(
    report: Mapping[str, Any],
    *,
    project_root: Path,
    evaluation_id: str,
    output: Path,
    replace: bool = False,
) -> Path:
    """Publish one canonical private report only at its evaluator-bound path."""

    content = _canonical_report(report)
    output_path, parent, expected_parent = _safe_output_parent(
        project_root=project_root,
        evaluation_id=evaluation_id,
        output=output,
    )
    directory_descriptor = -1
    temporary_descriptor = -1
    temporary_name = f".{OUTPUT_NAME}.{secrets.token_hex(16)}.tmp"
    backup_name = f".{OUTPUT_NAME}.{secrets.token_hex(16)}.rollback"
    temporary_exists = False
    backup_exists = False
    installed = False
    committed = False
    installed_info: os.stat_result | None = None
    existing: os.stat_result | None = None
    try:
        directory_descriptor = os.open(
            parent,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened_parent = os.fstat(directory_descriptor)
        if not _same_identity(expected_parent, opened_parent):
            raise PrerequisiteError("prerequisites_output_parent_changed")

        existing = _stat_at(directory_descriptor, OUTPUT_NAME)
        if existing is not None:
            _validate_output_info(existing)
            if not replace:
                raise PrerequisiteError("prerequisites_output_exists")

        temporary_descriptor = os.open(
            temporary_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_descriptor,
        )
        temporary_exists = True
        offset = 0
        while offset < len(content):
            written = os.write(temporary_descriptor, content[offset:])
            if written <= 0:
                raise PrerequisiteError("prerequisites_output_write_failed")
            offset += written
        os.fchmod(temporary_descriptor, 0o600)
        os.fsync(temporary_descriptor)
        staged = os.fstat(temporary_descriptor)
        _validate_output_info(staged)
        if staged.st_size != len(content):
            raise PrerequisiteError("prerequisites_output_write_failed")
        os.close(temporary_descriptor)
        temporary_descriptor = -1

        current_parent = parent.lstat()
        if not _same_identity(expected_parent, current_parent):
            raise PrerequisiteError("prerequisites_output_parent_changed")
        if replace:
            if existing is not None:
                os.link(
                    OUTPUT_NAME,
                    backup_name,
                    src_dir_fd=directory_descriptor,
                    dst_dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
                backup_exists = True
            os.replace(
                temporary_name,
                OUTPUT_NAME,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
            )
            temporary_exists = False
        else:
            try:
                os.link(
                    temporary_name,
                    OUTPUT_NAME,
                    src_dir_fd=directory_descriptor,
                    dst_dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except FileExistsError as exc:
                raise PrerequisiteError("prerequisites_output_exists") from exc
            os.unlink(temporary_name, dir_fd=directory_descriptor)
            temporary_exists = False
        installed = True
        installed_info = _read_output_at(directory_descriptor, OUTPUT_NAME, content)
        os.fsync(directory_descriptor)
        current_parent = parent.lstat()
        if not _same_identity(expected_parent, current_parent):
            raise PrerequisiteError("prerequisites_output_parent_changed")
        if backup_exists:
            os.unlink(backup_name, dir_fd=directory_descriptor)
            backup_exists = False
            os.fsync(directory_descriptor)
        committed = True
        return output_path
    except PrerequisiteError:
        raise
    except OSError as exc:
        raise PrerequisiteError("prerequisites_output_write_failed") from exc
    finally:
        if temporary_descriptor >= 0:
            os.close(temporary_descriptor)
        if directory_descriptor >= 0:
            if not committed and backup_exists:
                try:
                    if installed:
                        os.replace(
                            backup_name,
                            OUTPUT_NAME,
                            src_dir_fd=directory_descriptor,
                            dst_dir_fd=directory_descriptor,
                        )
                    else:
                        os.unlink(backup_name, dir_fd=directory_descriptor)
                    backup_exists = False
                except OSError:
                    pass
            elif not committed and installed and existing is None:
                try:
                    _unlink_if_identity(directory_descriptor, OUTPUT_NAME, installed_info)
                except OSError:
                    pass
            if temporary_exists:
                try:
                    os.unlink(temporary_name, dir_fd=directory_descriptor)
                except OSError:
                    pass
            try:
                os.fsync(directory_descriptor)
            except OSError:
                pass
            os.close(directory_descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-runs-url", required=True)
    parser.add_argument("--openshell-runs-url", required=True)
    parser.add_argument("--host-api-key-file", type=Path, required=True)
    parser.add_argument("--openshell-api-key-file", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--provider-inventory", type=Path, required=True)
    parser.add_argument("--service-report", type=Path, required=True)
    parser.add_argument("--broker-report", type=Path, required=True)
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--require-go", action="store_true", help="return 1 and do not write --output unless decision is GO")
    parser.add_argument(
        "--output",
        type=Path,
        help="write only var/openshell/eval/<evaluation-id>/prerequisites.json as an owner-only 0600 file",
    )
    parser.add_argument("--replace", action="store_true", help="atomically replace an existing valid --output file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.replace and args.output is None:
        print(json.dumps({"ok": False, "error_code": "prerequisites_output_required"}, sort_keys=True), file=sys.stderr)
        return 2
    try:
        report = build_report(
            host_runs_url=args.host_runs_url,
            openshell_runs_url=args.openshell_runs_url,
            host_api_key_file=args.host_api_key_file,
            openshell_api_key_file=args.openshell_api_key_file,
            dataset_file=args.dataset,
            evaluation_id=args.evaluation_id,
            provenance_report=args.provenance,
            provider_inventory=args.provider_inventory,
            service_report=args.service_report,
            broker_report=args.broker_report,
        )
    except PrerequisiteError as exc:
        print(json.dumps({"ok": False, "error_code": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    if args.require_go and report["decision"] != "GO":
        if args.json_output:
            print(json.dumps(report, ensure_ascii=True, sort_keys=True))
        else:
            print(f"{report['decision']} siq_analysis A/B prerequisites: blockers={len(report['blockers'])}")
        return 1
    if args.output is not None:
        try:
            write_report(
                report,
                project_root=REPO_ROOT,
                evaluation_id=args.evaluation_id,
                output=args.output,
                replace=args.replace,
            )
        except PrerequisiteError as exc:
            print(json.dumps({"ok": False, "error_code": str(exc)}, sort_keys=True), file=sys.stderr)
            return 2
    if args.json_output:
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    else:
        print(f"{report['decision']} siq_analysis A/B prerequisites: blockers={len(report['blockers'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
