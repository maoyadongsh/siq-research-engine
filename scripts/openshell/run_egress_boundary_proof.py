#!/usr/bin/env python3
"""Run and publish a strict, sanitized host egress broker boundary proof."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import secrets
import stat
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.openshell import (  # noqa: E402
    bridge_endpoint,
    broker_lifecycle,
    broker_request_identity,
    check_sanitized_artifacts,
    egress_decision,
    egress_guard,
    security_audit,
)

SCHEMA_VERSION = "siq.openshell.egress-boundary-proof.v1"
GATEWAY = "siq-openshell-dev"
PROFILE = "siq_analysis"
SCOPE = "host_egress_broker"
PROOF_TTL_SECONDS = 3_600
IDENTITY_TTL_SECONDS = 600
MAX_HTTP_BYTES = 1024 * 1024
MAX_AUDIT_FILES = 4
MAX_AUDIT_FILE_BYTES = 8 * 1024 * 1024
REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_RELATIVE = Path("infra/openshell/schemas/egress-boundary-proof.schema.json")
ALLOWLIST_RELATIVE = Path("infra/openshell/egress/allowlist.json")
IDENTITY_KEY_RELATIVE = Path("var/openshell/secrets/broker-request-identity.key")
SANITIZED_JSON_RELATIVE = Path("artifacts/openshell/v0.6/egress-boundary.sanitized.json")
SANITIZED_MD_RELATIVE = Path("artifacts/openshell/v0.6/egress-boundary.sanitized.md")
SOURCE_FILES = {
    "audit_contract_sha256": Path("scripts/openshell/security_audit.py"),
    "broker_lifecycle_sha256": Path("scripts/openshell/broker_lifecycle.py"),
    "broker_identity_contract_sha256": Path("scripts/openshell/broker_request_identity.py"),
    "egress_decision_sha256": Path("scripts/openshell/egress_decision.py"),
    "egress_guard_sha256": Path("scripts/openshell/egress_guard.py"),
    "mihomo_runtime_config_sha256": Path("infra/openshell/egress/mihomo-runtime.json"),
    "proof_runner_sha256": Path("scripts/openshell/run_egress_boundary_proof.py"),
    "siq_fetch_sha256": Path("scripts/openshell/siq_fetch.py"),
    "toolchain_manifest_sha256": Path("var/openshell/manifests/toolchain.sanitized.json"),
}
EXPECTED_NOT_CLAIMED = [
    "formal_business_sandbox",
    "direct_transfer_client_execution",
    "provider_route_availability",
    "restart_persistence",
    "semantic_dlp",
]


class EgressBoundaryProofError(RuntimeError):
    """Stable proof failure that never includes request, target, or credential data."""


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    envelope: Mapping[str, Any]
    decision: str
    rule_id: str
    outer_status: int
    upstream_required: bool


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    decision: str
    rule_id: str
    outer_http_status: int
    upstream_http_status: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "decision": self.decision,
            "outer_http_status": self.outer_http_status,
            "rule_id": self.rule_id,
            "upstream_http_status": self.upstream_http_status,
        }


def _case_specs() -> tuple[CaseSpec, ...]:
    public = "https://example.com/"
    upload = "https://example.com/upload"
    return (
        CaseSpec("public_get", {"method": "GET", "url": public}, "allow", "unknown_safe_read", 200, True),
        CaseSpec("public_head", {"method": "HEAD", "url": public}, "allow", "unknown_safe_read", 200, True),
        CaseSpec(
            "unknown_small_json",
            {"method": "POST", "url": public, "json_body": {"probe": "egress-boundary"}},
            "audit_only",
            "unknown_json_post_audit",
            200,
            True,
        ),
        CaseSpec(
            "unknown_multipart",
            {
                "method": "POST",
                "url": upload,
                "json_body": {"probe": "egress-boundary"},
                "headers": {"Content-Type": "multipart/form-data"},
            },
            "deny",
            "broker_multipart_denied",
            403,
            False,
        ),
        CaseSpec(
            "unknown_octet_stream",
            {
                "method": "POST",
                "url": upload,
                "json_body": {"probe": "egress-boundary"},
                "headers": {"Content-Type": "application/octet-stream"},
            },
            "deny",
            "broker_octet_stream_denied",
            403,
            False,
        ),
        CaseSpec(
            "unknown_put",
            {"method": "PUT", "url": upload, "json_body": {"probe": "egress-boundary"}},
            "deny",
            "broker_method_denied",
            403,
            False,
        ),
        CaseSpec(
            "cloud_metadata",
            {"method": "GET", "url": "http://169.254.169.254/latest/meta-data/"},
            "deny",
            "ssrf_non_public_ip",
            403,
            False,
        ),
    )


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _project_root(path: Path) -> Path:
    try:
        root = path.expanduser().resolve(strict=True)
        info = root.stat()
    except OSError as exc:
        raise EgressBoundaryProofError("project_root_invalid") from exc
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise EgressBoundaryProofError("project_root_invalid")
    return root


def _safe_regular(path: Path, *, private: bool = False, max_bytes: int = MAX_HTTP_BYTES) -> bytes:
    try:
        initial = path.lstat()
    except OSError as exc:
        raise EgressBoundaryProofError("proof_input_missing") from exc
    expected_mode = 0o600 if private else None
    if (
        stat.S_ISLNK(initial.st_mode)
        or not stat.S_ISREG(initial.st_mode)
        or initial.st_uid != os.geteuid()
        or initial.st_nlink != 1
        or initial.st_size > max_bytes
        or (expected_mode is not None and stat.S_IMODE(initial.st_mode) != expected_mode)
    ):
        raise EgressBoundaryProofError("proof_input_unsafe")
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (
            initial.st_dev,
            initial.st_ino,
            initial.st_size,
        ):
            raise EgressBoundaryProofError("proof_input_changed")
        content = bytearray()
        while len(content) <= max_bytes:
            chunk = os.read(descriptor, min(64 * 1024, max_bytes + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
    except OSError as exc:
        raise EgressBoundaryProofError("proof_input_unreadable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(content) > max_bytes:
        raise EgressBoundaryProofError("proof_input_too_large")
    return bytes(content)


def _source_digests(root: Path) -> dict[str, str]:
    try:
        allowlist = egress_decision.load_allowlist(root / ALLOWLIST_RELATIVE)
    except egress_decision.EgressConfigurationError as exc:
        raise EgressBoundaryProofError("allowlist_contract_invalid") from exc
    try:
        runtime_source_digest = egress_guard.runtime_source_bundle_sha256()
    except egress_guard.BrokerError as exc:
        raise EgressBoundaryProofError("runtime_source_binding_invalid") from exc
    result = {
        "allowlist_sha256": _sha256(_safe_regular(root / ALLOWLIST_RELATIVE)),
        "allowlist_contract_sha256": egress_guard.allowlist_contract_sha256(allowlist),
        "evidence_schema_sha256": _sha256(_safe_regular(root / SCHEMA_RELATIVE)),
        "runtime_source_bundle_sha256": runtime_source_digest,
    }
    for label, relative in SOURCE_FILES.items():
        result[label] = _sha256(_safe_regular(root / relative))
    return result


def _http_json(
    endpoint: bridge_endpoint.BridgeEndpoint,
    *,
    method: str,
    path: str,
    payload: Mapping[str, Any] | None = None,
    identity: str | None = None,
) -> tuple[int, dict[str, Any]]:
    body = None if payload is None else _canonical(payload)
    headers = {"Host": endpoint.host_alias, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))
    if identity is not None:
        headers[broker_request_identity.HEADER_NAME] = identity
    connection = http.client.HTTPConnection(endpoint.gateway_ip, 18_792, timeout=20)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        content = response.read(MAX_HTTP_BYTES + 1)
        status = response.status
    except (OSError, http.client.HTTPException) as exc:
        raise EgressBoundaryProofError("egress_broker_unreachable") from exc
    finally:
        connection.close()
    if len(content) > MAX_HTTP_BYTES:
        raise EgressBoundaryProofError("egress_broker_response_too_large")
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EgressBoundaryProofError("egress_broker_response_invalid") from exc
    if not isinstance(value, dict):
        raise EgressBoundaryProofError("egress_broker_response_invalid")
    return status, value


def _assert_strict_brokers(
    root: Path,
    *,
    expected_allowlist_contract_sha256: str,
    expected_source_bundle_sha256: str,
) -> bridge_endpoint.BridgeEndpoint:
    lifecycle = broker_lifecycle.BrokerLifecycle(project_root=root, require_request_identity=True)
    try:
        status, ok = lifecycle.status()
        endpoint = bridge_endpoint.discover_bridge_endpoint()
        endpoint.validate()
    except (broker_lifecycle.LifecycleError, bridge_endpoint.BridgeEndpointError) as exc:
        raise EgressBoundaryProofError("strict_brokers_unavailable") from exc
    brokers = status.get("brokers") if isinstance(status, dict) else None
    egress = brokers.get("egress") if isinstance(brokers, dict) else None
    if (
        not ok
        or not isinstance(egress, dict)
        or egress.get("state") != "running"
        or egress.get("request_identity_required") is not True
        or egress.get("port") != 18_792
    ):
        raise EgressBoundaryProofError("strict_brokers_unavailable")
    outer, health = _http_json(endpoint, method="GET", path="/health")
    if (
        outer != 200
        or set(health)
        != {
            "ok",
            "service",
            "dns_resolver_mode",
            "allowlist_contract_sha256",
            "source_bundle_sha256",
        }
        or health.get("ok") is not True
        or health.get("service") != "siq-egress-guard"
        or health.get("dns_resolver_mode") != "mihomo_fake_ip_verified"
        or health.get("allowlist_contract_sha256") != expected_allowlist_contract_sha256
        or health.get("source_bundle_sha256") != expected_source_bundle_sha256
    ):
        raise EgressBoundaryProofError("egress_resolver_contract_invalid")
    return endpoint


def _run_case(endpoint: bridge_endpoint.BridgeEndpoint, identity: str, spec: CaseSpec) -> CaseResult:
    outer_status, response = _http_json(
        endpoint,
        method="POST",
        path="/v1/request",
        payload=spec.envelope,
        identity=identity,
    )
    egress = response.get("egress")
    if not isinstance(egress, dict):
        raise EgressBoundaryProofError("egress_case_contract_invalid")
    rule_id = egress.get("rule_id")
    decision = egress.get("decision")
    if outer_status != spec.outer_status or rule_id != spec.rule_id or decision != spec.decision:
        raise EgressBoundaryProofError("egress_case_outcome_invalid")
    if spec.upstream_required:
        upstream = response.get("status")
        if outer_status != 200 or response.get("ok") is not True:
            raise EgressBoundaryProofError("egress_case_outcome_invalid")
        if isinstance(upstream, bool) or not isinstance(upstream, int) or not 100 <= upstream <= 599:
            raise EgressBoundaryProofError("egress_case_outcome_invalid")
    else:
        upstream = None
        if response.get("ok") is not False or response.get("error_code") != spec.rule_id:
            raise EgressBoundaryProofError("egress_case_outcome_invalid")
    return CaseResult(
        case_id=spec.case_id,
        decision=spec.decision,
        rule_id=spec.rule_id,
        outer_http_status=outer_status,
        upstream_http_status=upstream,
    )


def _audit_files(root: Path) -> list[Path]:
    audit_root = root / security_audit.AUDIT_RELATIVE_ROOT
    try:
        info = audit_root.lstat()
    except OSError as exc:
        raise EgressBoundaryProofError("audit_evidence_missing") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise EgressBoundaryProofError("audit_evidence_unsafe")
    candidates = sorted(audit_root.glob("*.jsonl"), key=lambda item: item.name, reverse=True)
    return candidates[:MAX_AUDIT_FILES]


def _bound_audit_records(
    root: Path,
    *,
    run_id: str,
    sandbox_id: str,
    policy_digest: str,
    results: Sequence[CaseResult],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for path in _audit_files(root):
        content = _safe_regular(path, private=True, max_bytes=MAX_AUDIT_FILE_BYTES)
        for line in content.splitlines():
            if not line or len(line) > security_audit.MAX_RECORD_BYTES:
                raise EgressBoundaryProofError("audit_evidence_invalid")
            try:
                record = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise EgressBoundaryProofError("audit_evidence_invalid") from exc
            if not isinstance(record, dict):
                raise EgressBoundaryProofError("audit_evidence_invalid")
            if record.get("siq_run_id") != run_id:
                continue
            try:
                if security_audit.serialize_record(record).rstrip(b"\n") != line:
                    raise EgressBoundaryProofError("audit_evidence_noncanonical")
            except security_audit.SecurityAuditError as exc:
                raise EgressBoundaryProofError("audit_evidence_invalid") from exc
            if (
                record.get("profile") != PROFILE
                or record.get("sandbox_id") != sandbox_id
                or record.get("policy_digest") != policy_digest
                or record.get("operation_class") != "network.request"
            ):
                raise EgressBoundaryProofError("audit_evidence_binding_invalid")
            selected.append(record)
    expected = {(item.rule_id, item.decision) for item in results}
    observed: dict[tuple[str, str], int] = {}
    for record in selected:
        target = record.get("target")
        scope = target.get("scope") if isinstance(target, dict) else None
        if not isinstance(scope, str) or not scope.startswith("egress."):
            raise EgressBoundaryProofError("audit_evidence_invalid")
        rule_id = scope.removeprefix("egress.")
        pair = (rule_id, str(record.get("decision") or ""))
        observed[pair] = observed.get(pair, 0) + 1
        if pair not in expected and pair != ("mihomo_fake_ip_compat_resolved", "audit_only"):
            raise EgressBoundaryProofError("audit_evidence_unexpected_record")
    if any(observed.get(item, 0) < 1 for item in expected):
        raise EgressBoundaryProofError("audit_evidence_incomplete")
    if observed.get(("mihomo_fake_ip_compat_resolved", "audit_only"), 0) < 6:
        raise EgressBoundaryProofError("audit_resolver_evidence_incomplete")
    if not 7 <= len(selected) <= 32:
        raise EgressBoundaryProofError("audit_evidence_count_invalid")
    return sorted(
        selected,
        key=lambda item: (
            str(item.get("timestamp") or ""),
            str((item.get("target") or {}).get("scope") or ""),
            str(item.get("decision") or ""),
        ),
    )


def _assert_identity_boundary(
    endpoint: bridge_endpoint.BridgeEndpoint,
    *,
    key: bytes,
    captured_at: int,
    run_nonce_digest: str,
    policy_digest: str,
) -> dict[str, bool]:
    envelope = {"method": "HEAD", "url": "https://example.com/"}
    missing_status, missing = _http_json(
        endpoint,
        method="POST",
        path="/v1/request",
        payload=envelope,
    )
    try:
        wrong_identity = broker_request_identity.sign_identity(
            key,
            audience=broker_request_identity.DATA_AUDIENCE,
            profile=PROFILE,
            run_id="egress-proof-auth",
            sandbox_id="host-egress-proof",
            session_id="egress-boundary-proof",
            policy_digest=policy_digest,
            run_nonce_digest=run_nonce_digest,
            now=captured_at,
            ttl_seconds=IDENTITY_TTL_SECONDS,
        )
    except broker_request_identity.IdentityError as exc:
        raise EgressBoundaryProofError("broker_identity_unavailable") from exc
    wrong_status, wrong = _http_json(
        endpoint,
        method="POST",
        path="/v1/request",
        payload=envelope,
        identity=wrong_identity,
    )
    checks = {
        "missing_identity_denied": missing_status == 401
        and missing == {"ok": False, "error_code": "broker_identity_required"},
        "wrong_audience_denied": wrong_status == 403
        and wrong == {"ok": False, "error_code": "broker_identity_invalid"},
    }
    if not all(checks.values()):
        raise EgressBoundaryProofError("broker_identity_boundary_invalid")
    return checks


def _checks(results: Sequence[CaseResult], identity_checks: Mapping[str, bool]) -> dict[str, Any]:
    by_id = {item.case_id: item for item in results}
    if set(by_id) != {item.case_id for item in _case_specs()} or len(by_id) != len(results):
        raise EgressBoundaryProofError("egress_case_set_invalid")
    if identity_checks != {"missing_identity_denied": True, "wrong_audience_denied": True}:
        raise EgressBoundaryProofError("broker_identity_boundary_invalid")
    return {
        "public_get_allowed": by_id["public_get"].decision == "allow",
        "public_head_allowed": by_id["public_head"].decision == "allow",
        **dict(identity_checks),
        "unknown_small_json_audit_only": by_id["unknown_small_json"].decision == "audit_only",
        "unknown_multipart_denied": by_id["unknown_multipart"].decision == "deny",
        "unknown_octet_stream_denied": by_id["unknown_octet_stream"].decision == "deny",
        "unknown_put_denied": by_id["unknown_put"].decision == "deny",
        "cloud_metadata_denied": by_id["cloud_metadata"].decision == "deny",
        "audit_records_bound": True,
        "target_values_stored": False,
        "request_payloads_stored": False,
        "response_payloads_stored": False,
        "runtime_credentials_stored": False,
    }


def build_evidence(
    *,
    captured_at: int,
    run_id: str,
    source_digests: Mapping[str, str],
    results: Sequence[CaseResult],
    audit_records: Sequence[Mapping[str, Any]],
    identity_checks: Mapping[str, bool],
) -> dict[str, Any]:
    expected_digest_keys = {
        "allowlist_sha256",
        "allowlist_contract_sha256",
        "evidence_schema_sha256",
        "runtime_source_bundle_sha256",
        *SOURCE_FILES,
    }
    if set(source_digests) != expected_digest_keys or any(
        not isinstance(value, str) or len(value) != 64 for value in source_digests.values()
    ):
        raise EgressBoundaryProofError("source_binding_invalid")
    checks = _checks(results, identity_checks)
    if not all(value is True for key, value in checks.items() if not key.endswith("_stored")):
        raise EgressBoundaryProofError("egress_checks_failed")
    if any(checks[key] is not False for key in checks if key.endswith("_stored")):
        raise EgressBoundaryProofError("sanitization_contract_invalid")
    serialized_audit = _canonical(list(audit_records))
    value = {
        "schema_version": SCHEMA_VERSION,
        "decision": "GO",
        "passed": True,
        "captured_at_unix": captured_at,
        "valid_until_unix": captured_at + PROOF_TTL_SECONDS,
        "scope": SCOPE,
        "formal_business_run": False,
        "formal_business_sandbox_evidence": False,
        "readiness_effect": "none",
        "eligible_for_completion": False,
        "gateway": GATEWAY,
        "environment_binding": {
            **dict(source_digests),
            "request_identity_required": True,
            "resolver_audit_rule": "mihomo_fake_ip_compat_resolved",
            "resolver_mode": "mihomo_fake_ip_verified",
        },
        "audit_binding": {
            "audit_record_count": len(audit_records),
            "audit_records_sha256": _sha256(serialized_audit),
            "run_id_sha256": _sha256(run_id.encode("ascii")),
        },
        "checks": checks,
        "cases": [item.as_dict() for item in results],
        "not_claimed": EXPECTED_NOT_CLAIMED,
    }
    _validate_evidence(value)
    return value


def _validate_evidence(value: Mapping[str, Any]) -> None:
    expected_keys = {
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
    if set(value) != expected_keys:
        raise EgressBoundaryProofError("evidence_fields_invalid")
    captured = value.get("captured_at_unix")
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("decision") != "GO"
        or value.get("passed") is not True
        or isinstance(captured, bool)
        or not isinstance(captured, int)
        or captured <= 0
        or value.get("valid_until_unix") != captured + PROOF_TTL_SECONDS
        or value.get("scope") != SCOPE
        or value.get("formal_business_run") is not False
        or value.get("formal_business_sandbox_evidence") is not False
        or value.get("readiness_effect") != "none"
        or value.get("eligible_for_completion") is not False
        or value.get("gateway") != GATEWAY
        or value.get("not_claimed") != EXPECTED_NOT_CLAIMED
    ):
        raise EgressBoundaryProofError("evidence_contract_invalid")
    cases = value.get("cases")
    checks = value.get("checks")
    environment = value.get("environment_binding")
    audit = value.get("audit_binding")
    if (
        not isinstance(cases, list)
        or len(cases) != 7
        or not isinstance(checks, dict)
        or not isinstance(environment, dict)
        or environment.get("request_identity_required") is not True
        or environment.get("resolver_audit_rule") != "mihomo_fake_ip_compat_resolved"
        or environment.get("resolver_mode") != "mihomo_fake_ip_verified"
        or not isinstance(audit, dict)
        or isinstance(audit.get("audit_record_count"), bool)
        or not isinstance(audit.get("audit_record_count"), int)
        or not 7 <= audit["audit_record_count"] <= 32
    ):
        raise EgressBoundaryProofError("evidence_contract_invalid")


def _markdown(value: Mapping[str, Any]) -> bytes:
    lines = [
        "# SIQ OpenShell Egress Boundary Proof",
        "",
        f"- Decision: `{value['decision']}`",
        f"- Scope: `{value['scope']}`",
        "- Formal business run: `false`",
        "- Formal business sandbox evidence: `false`",
        "- Readiness effect: `none`",
        "- Eligible for completion: `false`",
        f"- Resolver mode: `{value['environment_binding']['resolver_mode']}`",
        f"- Bound audit records: `{value['audit_binding']['audit_record_count']}`",
        "- Raw targets and payload material published: `false`",
        "",
        "| Case | Decision | Rule | Broker HTTP | Upstream HTTP |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    for case in value["cases"]:
        upstream = "n/a" if case["upstream_http_status"] is None else str(case["upstream_http_status"])
        lines.append(
            f"| `{case['case_id']}` | `{case['decision']}` | `{case['rule_id']}` | "
            f"{case['outer_http_status']} | {upstream} |"
        )
    lines.extend(
        [
            "",
            "This proof does not claim formal business sandbox coverage, direct transfer-client execution, or semantic DLP.",
            "",
        ]
    )
    return "\n".join(lines).encode("ascii")


def _atomic_write(path: Path, content: bytes, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise EgressBoundaryProofError("evidence_output_unsafe")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, mode)
        written = 0
        while written < len(content):
            written += os.write(descriptor, content[written:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary_path, path)
        os.chmod(path, mode)
    except OSError as exc:
        raise EgressBoundaryProofError("evidence_output_failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _write_outputs(root: Path, value: Mapping[str, Any]) -> tuple[Path, Path]:
    serialized = _canonical(value) + b"\n"
    markdown = _markdown(value)
    json_path = root / SANITIZED_JSON_RELATIVE
    markdown_path = root / SANITIZED_MD_RELATIVE
    for path, content in ((json_path, serialized), (markdown_path, markdown)):
        findings = check_sanitized_artifacts.scan_content(path, content)
        if findings:
            raise EgressBoundaryProofError("sanitized_evidence_rejected")
    _atomic_write(json_path, serialized)
    _atomic_write(markdown_path, markdown)
    return json_path, markdown_path


def run_proof(project_root: Path = REPO_ROOT, *, now: int | None = None) -> dict[str, Any]:
    root = _project_root(project_root)
    captured_at = int(time.time()) if now is None else now
    if isinstance(captured_at, bool) or not isinstance(captured_at, int) or captured_at <= 0:
        raise EgressBoundaryProofError("proof_time_invalid")
    source_digests = _source_digests(root)
    endpoint = _assert_strict_brokers(
        root,
        expected_allowlist_contract_sha256=source_digests["allowlist_contract_sha256"],
        expected_source_bundle_sha256=source_digests["runtime_source_bundle_sha256"],
    )
    run_id = f"egress-proof-{secrets.token_hex(8)}"
    sandbox_id = "host-egress-proof"
    run_nonce_digest = _sha256(secrets.token_bytes(32))
    try:
        key = broker_request_identity.read_key_file(root / IDENTITY_KEY_RELATIVE)
        identity_checks = _assert_identity_boundary(
            endpoint,
            key=key,
            captured_at=captured_at,
            run_nonce_digest=run_nonce_digest,
            policy_digest=source_digests["allowlist_sha256"],
        )
        identity = broker_request_identity.sign_identity(
            key,
            audience=broker_request_identity.EGRESS_AUDIENCE,
            profile=PROFILE,
            run_id=run_id,
            sandbox_id=sandbox_id,
            session_id="egress-boundary-proof",
            policy_digest=source_digests["allowlist_sha256"],
            run_nonce_digest=run_nonce_digest,
            now=captured_at,
            ttl_seconds=IDENTITY_TTL_SECONDS,
        )
    except broker_request_identity.IdentityError as exc:
        raise EgressBoundaryProofError("broker_identity_unavailable") from exc
    results = [_run_case(endpoint, identity, spec) for spec in _case_specs()]
    audit_records = _bound_audit_records(
        root,
        run_id=run_id,
        sandbox_id=sandbox_id,
        policy_digest=source_digests["allowlist_sha256"],
        results=results,
    )
    value = build_evidence(
        captured_at=captured_at,
        run_id=run_id,
        source_digests=source_digests,
        results=results,
        audit_records=audit_records,
        identity_checks=identity_checks,
    )
    _write_outputs(root, value)
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        value = run_proof(args.project_root)
    except EgressBoundaryProofError as exc:
        print(
            json.dumps(
                {"schema_version": SCHEMA_VERSION, "decision": "NO_GO", "error_code": str(exc)},
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "decision": value["decision"],
                "scope": value["scope"],
                "case_count": len(value["cases"]),
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
