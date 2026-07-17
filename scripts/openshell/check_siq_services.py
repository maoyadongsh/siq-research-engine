#!/usr/bin/env python3
"""Read-only connectivity and security-proof preflight for SIQ OpenShell services."""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import os
import re
import socket
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.openshell import build_milvus_write_protection_proof as milvus_proof  # noqa: E402

SCHEMA_VERSION = "siq.openshell.service_preflight.v2"
PROOF_SCHEMA_VERSION = "siq.openshell.service_security_proofs.v1"
PROBE_SCOPE_PROTOCOL = "tcp_connect_plus_read_only_http_get"
DEFAULT_HOST_ALIAS = "127.0.0.1"
DEFAULT_TIMEOUT_SECONDS = 0.5
MIN_TIMEOUT_SECONDS = 0.05
MAX_TIMEOUT_SECONDS = 5.0
MAX_PROOF_FILE_BYTES = 16 * 1024
MAX_HTTP_RESPONSE_BYTES = 128 * 1024
EXIT_GO = 0
EXIT_NO_GO = 1
EXIT_CONFIGURATION_ERROR = 2

_DNS_ALIAS_RE = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
    re.IGNORECASE,
)


class PreflightConfigurationError(RuntimeError):
    """Raised with a stable, non-sensitive configuration error code."""


@dataclass(frozen=True)
class ServiceSpec:
    service_id: str
    label: str
    category: str
    port: int
    requirement: str
    blocker_code: str
    protocol_contract: str = ""
    protocol_path: str = ""
    protocol_blocker_code: str = ""

    @property
    def blocking(self) -> bool:
        return self.requirement == "required"


@dataclass(frozen=True)
class ProbeOutcome:
    reachable: bool
    error_code: str
    latency_ms: int


@dataclass(frozen=True)
class ProtocolOutcome:
    checked: bool
    available: bool
    error_code: str
    latency_ms: int
    http_status: int | None


SERVICE_SPECS = (
    ServiceSpec(
        "qwen_local",
        "Qwen local fallback",
        "local_model",
        8004,
        "optional",
        "qwen_local_unreachable",
        "openai_models_list_v1",
        "/v1/models",
        "qwen_local_protocol_unavailable",
    ),
    ServiceSpec(
        "gemma_local",
        "Gemma local fallback",
        "local_model",
        8006,
        "optional",
        "gemma_local_unreachable",
        "openai_models_list_v1",
        "/v1/models",
        "gemma_local_protocol_unavailable",
    ),
    ServiceSpec(
        "nemotron_local",
        "Nemotron image model",
        "local_model",
        8007,
        "optional",
        "nemotron_local_unreachable",
        "openai_models_list_v1",
        "/v1/models",
        "nemotron_local_protocol_unavailable",
    ),
    ServiceSpec(
        "embedding",
        "Embedding service",
        "embedding",
        8013,
        "required",
        "embedding_service_unreachable",
        "openai_models_list_v1",
        "/v1/models",
        "embedding_service_protocol_unavailable",
    ),
    ServiceSpec("postgres", "PostgreSQL market facts", "database", 15432, "required", "postgres_unreachable"),
    ServiceSpec("milvus", "Milvus knowledge store", "database", 19530, "required", "milvus_unreachable"),
    ServiceSpec(
        "siq_api",
        "SIQ API",
        "application",
        18081,
        "required",
        "siq_api_unreachable",
        "status_ok_json_v1",
        "/health",
        "siq_api_protocol_unavailable",
    ),
    ServiceSpec(
        "hermes_host",
        "Hermes host rollback runtime",
        "agent_runtime",
        18651,
        "required",
        "hermes_host_unreachable",
        "status_ok_json_v1",
        "/health",
        "hermes_host_protocol_unavailable",
    ),
)

_PROOF_KEYS = ("postgres_readonly_identity", "milvus_write_protection")
_PROOF_SOURCES = {"cli", "milvus_proof_file", "proof_file", "none"}
_PROBE_ERROR_CODES = {
    "",
    "connection_failed",
    "connection_refused",
    "connection_timeout",
    "name_resolution_failed",
    "probe_contract_invalid",
    "probe_failed",
}
_PROTOCOL_ERROR_CODES = {
    "",
    "http_connection_failed",
    "http_connection_refused",
    "http_name_resolution_failed",
    "http_response_too_large",
    "http_status_unexpected",
    "http_timeout",
    "protocol_probe_contract_invalid",
    "protocol_probe_failed",
    "response_content_type_invalid",
    "response_contract_invalid",
    "response_not_json",
    "transport_unreachable",
}


def validate_host_alias(value: str) -> str:
    candidate = str(value or "").strip().lower()
    if not candidate or any(character.isspace() for character in candidate):
        raise PreflightConfigurationError("host_alias_invalid")
    try:
        ipaddress.ip_address(candidate)
    except ValueError as exc:
        if not _DNS_ALIAS_RE.fullmatch(candidate):
            raise PreflightConfigurationError("host_alias_invalid") from exc
    return candidate


def validate_timeout(value: float | str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise PreflightConfigurationError("timeout_invalid") from exc
    if not MIN_TIMEOUT_SECONDS <= parsed <= MAX_TIMEOUT_SECONDS:
        raise PreflightConfigurationError("timeout_out_of_range")
    return parsed


def _proof_file_payload(path: Path) -> dict[str, bool]:
    if path.is_symlink() or not path.is_file():
        raise PreflightConfigurationError("proof_file_invalid")
    try:
        if path.stat().st_size > MAX_PROOF_FILE_BYTES:
            raise PreflightConfigurationError("proof_file_invalid")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreflightConfigurationError("proof_file_invalid") from exc
    expected_keys = {"schema_version", *_PROOF_KEYS}
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise PreflightConfigurationError("proof_file_invalid")
    if payload.get("schema_version") != PROOF_SCHEMA_VERSION:
        raise PreflightConfigurationError("proof_file_invalid")
    if any(not isinstance(payload.get(key), bool) for key in _PROOF_KEYS):
        raise PreflightConfigurationError("proof_file_invalid")
    return {key: payload[key] for key in _PROOF_KEYS}


def resolve_security_proofs(
    *,
    proof_file: Path | None,
    postgres_cli_proof: bool,
    milvus_cli_proof: bool,
    milvus_proof_file: Path | None = None,
    project_root: Path = REPO_ROOT,
    milvus_validator: Callable[[Path], Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    file_proofs = _proof_file_payload(proof_file) if proof_file is not None else {}
    postgres_file_value = bool(file_proofs.get("postgres_readonly_identity"))
    postgres_source = "cli" if postgres_cli_proof else ("proof_file" if postgres_file_value else "none")
    resolved: dict[str, dict[str, Any]] = {
        "postgres_readonly_identity": {
            "proven": bool(postgres_cli_proof or postgres_file_value),
            "source": postgres_source,
        }
    }

    # A legacy boolean or command-line assertion cannot prove the composite Milvus
    # boundary. It must be bound to a real sandbox/container receipt and current
    # policy/broker/bridge inputs by the dedicated short-lived proof.
    if milvus_cli_proof:
        raise PreflightConfigurationError("milvus_cli_proof_unsupported")
    if milvus_proof_file is None:
        resolved["milvus_write_protection"] = {"proven": False, "source": "none"}
        return resolved
    try:
        if milvus_validator is None:
            validated = milvus_proof.validate_consumable_proof(
                milvus_proof_file,
                project_root=project_root,
            )
        else:
            validated = milvus_validator(milvus_proof_file)
    except milvus_proof.MilvusProofError as exc:
        raise PreflightConfigurationError("milvus_proof_invalid") from exc
    if validated.get("schema_version") != milvus_proof.SCHEMA_VERSION or validated.get("passed") is not True:
        raise PreflightConfigurationError("milvus_proof_invalid")
    resolved["milvus_write_protection"] = {
        "proven": True,
        "source": "milvus_proof_file",
        "schema_version": milvus_proof.SCHEMA_VERSION,
    }
    return resolved


def tcp_probe(host: str, port: int, timeout_seconds: float) -> ProbeOutcome:
    started = time.monotonic()
    error_code = ""
    reachable = False
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            reachable = True
    except (TimeoutError, socket.timeout):
        error_code = "connection_timeout"
    except ConnectionRefusedError:
        error_code = "connection_refused"
    except socket.gaierror:
        error_code = "name_resolution_failed"
    except OSError:
        error_code = "connection_failed"
    latency_ms = max(0, int((time.monotonic() - started) * 1000))
    return ProbeOutcome(reachable=reachable, error_code=error_code, latency_ms=latency_ms)


def _json_content_type(value: str | None) -> bool:
    media_type = str(value or "").split(";", 1)[0].strip().lower()
    return media_type == "application/json" or media_type.endswith("+json")


def _valid_protocol_payload(contract: str, payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if contract == "status_ok_json_v1":
        return payload.get("status") == "ok"
    if contract == "openai_models_list_v1":
        models = payload.get("data")
        return (
            payload.get("object") == "list"
            and isinstance(models, list)
            and bool(models)
            and all(
                isinstance(model, dict)
                and isinstance(model.get("id"), str)
                and bool(model["id"].strip())
                for model in models
            )
        )
    return False


def http_protocol_probe(host: str, spec: ServiceSpec, timeout_seconds: float) -> ProtocolOutcome:
    """Issue one fixed GET without redirects, credentials, query text, or request body."""

    started = time.monotonic()
    connection: http.client.HTTPConnection | None = None
    try:
        connection = http.client.HTTPConnection(host, spec.port, timeout=timeout_seconds)
        connection.request(
            "GET",
            spec.protocol_path,
            headers={"Accept": "application/json", "Connection": "close", "User-Agent": "siq-service-preflight/2"},
        )
        response = connection.getresponse()
        status = response.status
        content_type = response.getheader("Content-Type")
        body = response.read(MAX_HTTP_RESPONSE_BYTES + 1)
    except (TimeoutError, socket.timeout):
        return ProtocolOutcome(True, False, "http_timeout", _elapsed_ms(started), None)
    except ConnectionRefusedError:
        return ProtocolOutcome(True, False, "http_connection_refused", _elapsed_ms(started), None)
    except socket.gaierror:
        return ProtocolOutcome(True, False, "http_name_resolution_failed", _elapsed_ms(started), None)
    except (OSError, http.client.HTTPException):
        return ProtocolOutcome(True, False, "http_connection_failed", _elapsed_ms(started), None)
    finally:
        if connection is not None:
            connection.close()

    latency_ms = _elapsed_ms(started)
    if status != 200:
        return ProtocolOutcome(True, False, "http_status_unexpected", latency_ms, status)
    if len(body) > MAX_HTTP_RESPONSE_BYTES:
        return ProtocolOutcome(True, False, "http_response_too_large", latency_ms, status)
    if not _json_content_type(content_type):
        return ProtocolOutcome(True, False, "response_content_type_invalid", latency_ms, status)
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ProtocolOutcome(True, False, "response_not_json", latency_ms, status)
    if not _valid_protocol_payload(spec.protocol_contract, payload):
        return ProtocolOutcome(True, False, "response_contract_invalid", latency_ms, status)
    return ProtocolOutcome(True, True, "", latency_ms, status)


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _safe_probe(
    probe: Callable[[str, int, float], ProbeOutcome],
    host_alias: str,
    spec: ServiceSpec,
    timeout_seconds: float,
) -> ProbeOutcome:
    try:
        outcome = probe(host_alias, spec.port, timeout_seconds)
    except Exception:
        return ProbeOutcome(reachable=False, error_code="probe_failed", latency_ms=0)
    if not isinstance(outcome, ProbeOutcome):
        return ProbeOutcome(reachable=False, error_code="probe_contract_invalid", latency_ms=0)
    if outcome.error_code not in _PROBE_ERROR_CODES:
        return ProbeOutcome(reachable=False, error_code="probe_contract_invalid", latency_ms=0)
    if outcome.reachable and outcome.error_code:
        return ProbeOutcome(reachable=False, error_code="probe_contract_invalid", latency_ms=0)
    if not outcome.reachable and not outcome.error_code:
        return ProbeOutcome(reachable=False, error_code="probe_contract_invalid", latency_ms=0)
    if isinstance(outcome.latency_ms, bool) or not isinstance(outcome.latency_ms, int) or outcome.latency_ms < 0:
        return ProbeOutcome(reachable=False, error_code="probe_contract_invalid", latency_ms=0)
    return outcome


def _probe_services(
    host_alias: str,
    timeout_seconds: float,
    probe: Callable[[str, int, float], ProbeOutcome],
) -> dict[str, ProbeOutcome]:
    outcomes: dict[str, ProbeOutcome] = {}
    with ThreadPoolExecutor(max_workers=len(SERVICE_SPECS), thread_name_prefix="siq-service-preflight") as executor:
        futures = {
            executor.submit(_safe_probe, probe, host_alias, spec, timeout_seconds): spec.service_id
            for spec in SERVICE_SPECS
        }
        for future in as_completed(futures):
            outcomes[futures[future]] = future.result()
    return outcomes


def _safe_protocol_probe(
    probe: Callable[[str, ServiceSpec, float], ProtocolOutcome],
    host_alias: str,
    spec: ServiceSpec,
    timeout_seconds: float,
    transport: ProbeOutcome,
) -> ProtocolOutcome:
    if not spec.protocol_contract:
        return ProtocolOutcome(False, True, "", 0, None)
    if not transport.reachable:
        return ProtocolOutcome(False, False, "transport_unreachable", 0, None)
    try:
        outcome = probe(host_alias, spec, timeout_seconds)
    except Exception:
        return ProtocolOutcome(True, False, "protocol_probe_failed", 0, None)
    if not isinstance(outcome, ProtocolOutcome):
        return ProtocolOutcome(True, False, "protocol_probe_contract_invalid", 0, None)
    if (
        outcome.error_code not in _PROTOCOL_ERROR_CODES
        or outcome.checked is not True
        or not isinstance(outcome.available, bool)
        or (outcome.available and outcome.error_code)
        or (not outcome.available and not outcome.error_code)
        or isinstance(outcome.latency_ms, bool)
        or not isinstance(outcome.latency_ms, int)
        or outcome.latency_ms < 0
        or (
            outcome.http_status is not None
            and (
                isinstance(outcome.http_status, bool)
                or not isinstance(outcome.http_status, int)
                or not 100 <= outcome.http_status <= 599
            )
        )
    ):
        return ProtocolOutcome(True, False, "protocol_probe_contract_invalid", 0, None)
    if outcome.available and outcome.http_status != 200:
        return ProtocolOutcome(True, False, "protocol_probe_contract_invalid", 0, None)
    return outcome


def _probe_protocols(
    host_alias: str,
    timeout_seconds: float,
    transports: Mapping[str, ProbeOutcome],
    probe: Callable[[str, ServiceSpec, float], ProtocolOutcome],
) -> dict[str, ProtocolOutcome]:
    outcomes: dict[str, ProtocolOutcome] = {}
    with ThreadPoolExecutor(max_workers=len(SERVICE_SPECS), thread_name_prefix="siq-protocol-preflight") as executor:
        futures = {
            executor.submit(
                _safe_protocol_probe,
                probe,
                host_alias,
                spec,
                timeout_seconds,
                transports[spec.service_id],
            ): spec.service_id
            for spec in SERVICE_SPECS
        }
        for future in as_completed(futures):
            outcomes[futures[future]] = future.result()
    return outcomes


def _service_report(spec: ServiceSpec, outcome: ProbeOutcome, protocol: ProtocolOutcome) -> dict[str, Any]:
    service_available = outcome.reachable and (not spec.protocol_contract or protocol.available)
    if service_available:
        status = "pass"
    elif spec.blocking:
        status = "no_go"
    else:
        status = "warning"
    error_code = (
        outcome.error_code
        if not outcome.reachable
        else (spec.protocol_blocker_code if spec.protocol_contract and not protocol.available else "")
    )
    protocol_status = "not_applicable"
    if spec.protocol_contract:
        if not protocol.checked:
            protocol_status = "not_run"
        elif protocol.available:
            protocol_status = "pass"
        else:
            protocol_status = "no_go" if spec.blocking else "warning"
    return {
        "service_id": spec.service_id,
        "label": spec.label,
        "category": spec.category,
        "port": spec.port,
        "probe": "tcp_connect+http_get" if spec.protocol_contract else "tcp_connect",
        "requirement": spec.requirement,
        "blocking": spec.blocking,
        "reachable": outcome.reachable,
        "status": status,
        "error_code": error_code,
        "latency_ms": outcome.latency_ms,
        "protocol_check": {
            "contract": spec.protocol_contract or "not_applicable",
            "method": "GET" if spec.protocol_contract else "",
            "path": spec.protocol_path,
            "checked": protocol.checked,
            "available": protocol.available if spec.protocol_contract else None,
            "status": protocol_status,
            "error_code": protocol.error_code,
            "latency_ms": protocol.latency_ms if spec.protocol_contract else None,
            "http_status": protocol.http_status,
        },
    }


def _security_checks(proofs: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    definitions = (
        (
            "postgres_readonly_identity",
            "PostgreSQL sandbox identity is read-only",
            "postgres_readonly_identity_unproven",
        ),
        (
            "milvus_write_protection",
            "Milvus anonymous or sandbox mutation is blocked",
            "milvus_anonymous_write_not_excluded",
        ),
    )
    checks: list[dict[str, Any]] = []
    for check_id, label, blocker_code in definitions:
        proof = proofs.get(check_id) or {}
        raw_source = str(proof.get("source") or "none")
        internal_source = raw_source if raw_source in _PROOF_SOURCES else "none"
        allowed_sources = (
            {"milvus_proof_file"}
            if check_id == "milvus_write_protection"
            else {"cli", "proof_file"}
        )
        proven = proof.get("proven") is True and internal_source in allowed_sources
        proof_source = "proof_file" if internal_source == "milvus_proof_file" else internal_source
        checks.append(
            {
                "check_id": check_id,
                "label": label,
                "requirement": "required",
                "blocking": True,
                "status": "pass" if proven else "no_go",
                "proof_present": proven,
                "proof_source": proof_source,
                "proof_schema_version": str(proof.get("schema_version") or ""),
                "error_code": "" if proven else blocker_code,
            }
        )
    return checks


def build_report(
    *,
    host_alias: str,
    timeout_seconds: float,
    proofs: Mapping[str, Mapping[str, Any]],
    probe: Callable[[str, int, float], ProbeOutcome] | None = None,
    protocol_probe: Callable[[str, ServiceSpec, float], ProtocolOutcome] | None = None,
) -> dict[str, Any]:
    validated_host = validate_host_alias(host_alias)
    validated_timeout = validate_timeout(timeout_seconds)
    outcomes = _probe_services(validated_host, validated_timeout, probe or tcp_probe)
    protocol_outcomes = _probe_protocols(
        validated_host,
        validated_timeout,
        outcomes,
        protocol_probe or http_protocol_probe,
    )
    services = [
        _service_report(spec, outcomes[spec.service_id], protocol_outcomes[spec.service_id])
        for spec in SERVICE_SPECS
    ]
    security_checks = _security_checks(proofs)

    blockers = [
        {
            "check_id": f"service:{service['service_id']}",
            "kind": "service_connectivity" if not service["reachable"] else "service_protocol",
            "error_code": (
                next(spec.blocker_code for spec in SERVICE_SPECS if spec.service_id == service["service_id"])
                if not service["reachable"]
                else next(
                    spec.protocol_blocker_code
                    for spec in SERVICE_SPECS
                    if spec.service_id == service["service_id"]
                )
            ),
            "port": service["port"],
        }
        for service in services
        if service["status"] == "no_go"
    ]
    blockers.extend(
        {
            "check_id": check["check_id"],
            "kind": "security_proof",
            "error_code": check["error_code"],
        }
        for check in security_checks
        if check["status"] == "no_go"
    )
    warnings = [
        {
            "check_id": f"service:{service['service_id']}",
            "kind": "optional_service_connectivity" if not service["reachable"] else "optional_service_protocol",
            "error_code": (
                next(spec.blocker_code for spec in SERVICE_SPECS if spec.service_id == service["service_id"])
                if not service["reachable"]
                else next(
                    spec.protocol_blocker_code
                    for spec in SERVICE_SPECS
                    if spec.service_id == service["service_id"]
                )
            ),
            "port": service["port"],
        }
        for service in services
        if service["status"] == "warning"
    ]
    required_services = [service for service in services if service["requirement"] == "required"]
    optional_services = [service for service in services if service["requirement"] == "optional"]
    required_protocols = [service for service in required_services if service["protocol_check"]["contract"] != "not_applicable"]
    optional_protocols = [service for service in optional_services if service["protocol_check"]["contract"] != "not_applicable"]
    passed = not blockers
    return {
        "schema_version": SCHEMA_VERSION,
        "decision": "GO" if passed else "NO_GO",
        "passed": passed,
        "probe_scope": {
            "host_alias_configured": True,
            "host_alias_kind": "loopback" if validated_host in {"127.0.0.1", "::1"} else "configured",
            "timeout_ms": int(validated_timeout * 1000),
            "protocol": PROBE_SCOPE_PROTOCOL,
            "read_only": True,
            "http_method": "GET",
            "request_body_sent": False,
            "redirects_followed": False,
            "response_body_recorded": False,
        },
        "services": services,
        "security_checks": security_checks,
        "blockers": blockers,
        "warnings": warnings,
        "summary": {
            "required_total": len(required_services),
            "required_reachable": sum(service["reachable"] is True for service in required_services),
            "optional_total": len(optional_services),
            "optional_reachable": sum(service["reachable"] is True for service in optional_services),
            "required_protocol_total": len(required_protocols),
            "required_protocol_available": sum(
                service["protocol_check"]["available"] is True for service in required_protocols
            ),
            "optional_protocol_total": len(optional_protocols),
            "optional_protocol_available": sum(
                service["protocol_check"]["available"] is True for service in optional_protocols
            ),
            "security_proofs_required": len(security_checks),
            "security_proofs_present": sum(check["proof_present"] is True for check in security_checks),
            "blocking_count": len(blockers),
            "warning_count": len(warnings),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Exit codes: 0=GO, 1=NO_GO, 2=invalid preflight configuration.",
    )
    parser.add_argument(
        "--host-alias",
        default=DEFAULT_HOST_ALIAS,
        help="Validated DNS/IP alias used for every fixed service port; the alias is never emitted.",
    )
    parser.add_argument(
        "--timeout",
        default=str(DEFAULT_TIMEOUT_SECONDS),
        dest="timeout_seconds",
        help=f"Per-service TCP timeout in seconds ({MIN_TIMEOUT_SECONDS}..{MAX_TIMEOUT_SECONDS}).",
    )
    parser.add_argument(
        "--proof-file",
        type=Path,
        help="Strict secret-free JSON proof record for both database security checks.",
    )
    parser.add_argument(
        "--postgres-readonly-proof",
        action="store_true",
        help="Assert that direct sandbox PostgreSQL DML/DDL negative tests passed.",
    )
    parser.add_argument(
        "--milvus-write-protection-proof",
        action="store_true",
        help="Deprecated and rejected; use --milvus-proof-file with a verified sandbox receipt.",
    )
    parser.add_argument(
        "--milvus-proof-file",
        type=Path,
        help="Short-lived proof binding sandbox/container, policy, broker, bridge and Milvus read contract.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the direct v2 JSON report to this path; no output is written unless explicitly requested.",
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        help="Write the matching sanitized Markdown summary to this path.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Allow replacing existing regular output files atomically.",
    )
    parser.add_argument("--json", action="store_true", dest="json_output", help="Emit the structured report.")
    return parser


def _safe_output_path(path: Path) -> Path:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    current = Path(candidate.anchor)
    for component in candidate.parent.parts[1:]:
        current /= component
        if current.is_symlink() or not current.is_dir():
            raise PreflightConfigurationError("output_path_invalid")
    if candidate.exists() and (candidate.is_symlink() or not candidate.is_file()):
        raise PreflightConfigurationError("output_path_invalid")
    return candidate


def _atomic_write(path: Path, content: bytes, *, replace: bool) -> None:
    checked = _safe_output_path(path)
    if checked.exists() and not replace:
        raise PreflightConfigurationError("output_exists")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{checked.name}.", dir=checked.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        if replace:
            os.replace(temporary, checked)
        else:
            try:
                os.link(temporary, checked, follow_symlinks=False)
            except FileExistsError as exc:
                raise PreflightConfigurationError("output_exists") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _markdown_report(report: Mapping[str, Any]) -> str:
    scope = report.get("probe_scope") if isinstance(report.get("probe_scope"), dict) else {}
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# SIQ Service Preflight",
        "",
        f"- Schema: `{report.get('schema_version', 'unknown')}`",
        f"- Decision: `{report.get('decision', 'unknown')}`",
        f"- Probe: `{scope.get('protocol', 'unknown')}`",
        f"- Required transport reachable: `{summary.get('required_reachable', 0)} / {summary.get('required_total', 0)}`",
        f"- Required protocol available: `{summary.get('required_protocol_available', 0)} / {summary.get('required_protocol_total', 0)}`",
        f"- Security proofs present: `{summary.get('security_proofs_present', 0)} / {summary.get('security_proofs_required', 0)}`",
        f"- Blocking checks: `{summary.get('blocking_count', 0)}`",
        "",
        "| Port | Service | Transport | Protocol | Error |",
        "|---:|---|---|---|---|",
    ]
    services = report.get("services") if isinstance(report.get("services"), list) else []
    for item in services:
        if not isinstance(item, dict):
            continue
        protocol = item.get("protocol_check") if isinstance(item.get("protocol_check"), dict) else {}
        lines.append(
            f"| {item.get('port', '')} | {item.get('label', item.get('service_id', ''))} | "
            f"{'pass' if item.get('reachable') is True else 'no_go'} | "
            f"{protocol.get('status', 'not_applicable')} | `{item.get('error_code', '')}` |"
        )
    lines.append("")
    lines.append("This is a read-only pre-cutover gate; it does not start or stop services or models.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        proofs = resolve_security_proofs(
            proof_file=args.proof_file,
            postgres_cli_proof=args.postgres_readonly_proof,
            milvus_cli_proof=args.milvus_write_protection_proof,
            milvus_proof_file=args.milvus_proof_file,
        )
        report = build_report(
            host_alias=args.host_alias,
            timeout_seconds=args.timeout_seconds,
            proofs=proofs,
        )
        if args.output is not None:
            _atomic_write(
                args.output,
                (json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("utf-8"),
                replace=args.replace,
            )
        if args.markdown_output is not None:
            _atomic_write(
                args.markdown_output,
                _markdown_report(report).encode("utf-8"),
                replace=args.replace,
            )
    except PreflightConfigurationError as exc:
        print(f"SIQ service preflight configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIGURATION_ERROR

    if args.json_output:
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    else:
        summary = report["summary"]
        print(
            f"{report['decision']} SIQ OpenShell service preflight: "
            f"required={summary['required_reachable']}/{summary['required_total']} "
            f"blockers={summary['blocking_count']} warnings={summary['warning_count']}"
        )
    return EXIT_GO if report["passed"] else EXIT_NO_GO


if __name__ == "__main__":
    sys.exit(main())
