#!/usr/bin/env python3
"""Build and validate the short-lived SIQ Milvus sandbox boundary proof."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.openshell import bridge_endpoint, probe_milvus_sandbox_boundary as sandbox_probe  # noqa: E402
from scripts.openshell.siq_analysis_lifecycle import (  # noqa: E402
    LIFECYCLE_LABEL,
    MANIFEST_FIELDS,
    NONCE_RE,
    PROFILE,
    SCHEMA_VERSION as LIFECYCLE_SCHEMA_VERSION,
    LifecycleAdapter,
    LifecycleError,
    _minimal_child_environment,
)

SCHEMA_VERSION = "siq.openshell.milvus-write-protection-proof.v1"
GATEWAY = "siq-openshell-dev"
PROOF_TTL_SECONDS = 3_600
MAX_CLOCK_SKEW_SECONDS = 30
MAX_FILE_BYTES = 1024 * 1024
REPO_ROOT = Path(__file__).resolve().parents[2]
RUNS_RELATIVE = Path("var/openshell/siq-analysis/runs")
RAW_RECEIPT_RELATIVE = Path("var/openshell/proofs/milvus-sandbox-receipt.json")
PROOF_RELATIVE = Path("var/openshell/proofs/milvus-write-protection.json")
SANITIZED_JSON_RELATIVE = Path("artifacts/openshell/v0.6/milvus-write-protection.sanitized.json")
SANITIZED_MD_RELATIVE = Path("artifacts/openshell/v0.6/milvus-write-protection.sanitized.md")
POLICY_CONTRACT_FILES = (
    Path("infra/openshell/policies/base.yaml"),
    Path("infra/openshell/policies/profiles/siq-analysis.yaml"),
    Path("scripts/openshell/build_policy.py"),
)
DATA_BROKER_SOURCE = Path("scripts/openshell/read_only_data_broker.py")
SANDBOX_PROBE_SOURCE = Path("scripts/openshell/probe_milvus_sandbox_boundary.py")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
RUN_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,47}\Z")
EXPECTED_CHECKS = {
    "active_policy_bound_to_verified_sandbox": True,
    "broker_describe_allowed": True,
    "broker_get_allowed": True,
    "broker_mutation_routes_absent": True,
    "broker_query_allowed": True,
    "broker_search_allowed": True,
    "business_rows_modified": 0,
    "sandbox_direct_milvus_denied": True,
}
SANDBOX_PROBE_ERROR_CODES = frozenset(
    {
        "broker_contract_stale",
        "broker_connection_refused",
        "broker_connection_timed_out",
        "broker_describe_contract_invalid",
        "broker_describe_failed",
        "broker_get_contract_invalid",
        "broker_get_failed",
        "broker_mutation_route_exposed",
        "broker_name_resolution_failed",
        "broker_policy_denied",
        "broker_query_empty",
        "broker_query_failed",
        "broker_query_primary_key_invalid",
        "broker_response_invalid",
        "broker_response_too_large",
        "broker_route_unreachable",
        "broker_search_contract_invalid",
        "broker_search_failed",
        "broker_unreachable",
        "direct_milvus_allowed",
        "direct_milvus_probe_failed",
        "sandbox_binding_invalid",
    }
)


class MilvusProofError(RuntimeError):
    """Stable proof failure without runtime identifiers, data, or credentials."""


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")


def _safe_regular(path: Path, *, private: bool, max_bytes: int = MAX_FILE_BYTES) -> bytes:
    try:
        info = path.lstat()
    except OSError as exc:
        raise MilvusProofError("proof_input_missing") from exc
    expected_mode = 0o600 if private else None
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or info.st_size > max_bytes
        or (expected_mode is not None and stat.S_IMODE(info.st_mode) != expected_mode)
    ):
        raise MilvusProofError("proof_input_unsafe")
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        current = os.fstat(descriptor)
        if (current.st_dev, current.st_ino, current.st_size) != (info.st_dev, info.st_ino, info.st_size):
            raise MilvusProofError("proof_input_changed")
        content = os.read(descriptor, max_bytes + 1)
    except OSError as exc:
        raise MilvusProofError("proof_input_unreadable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(content) > max_bytes:
        raise MilvusProofError("proof_input_too_large")
    return content


def _read_json(path: Path, *, private: bool) -> dict[str, Any]:
    try:
        value = json.loads(_safe_regular(path, private=private))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MilvusProofError("proof_input_invalid") from exc
    if not isinstance(value, dict):
        raise MilvusProofError("proof_input_invalid")
    return value


def policy_contract_sha256(project_root: Path) -> str:
    digest = hashlib.sha256()
    for relative in POLICY_CONTRACT_FILES:
        content = _safe_regular(project_root / relative, private=False)
        digest.update(relative.as_posix().encode("ascii") + b"\0" + content + b"\0")
    return digest.hexdigest()


def data_broker_sha256(project_root: Path) -> str:
    return _sha256(_safe_regular(project_root / DATA_BROKER_SOURCE, private=False))


def sandbox_probe_sha256(project_root: Path) -> str:
    return _sha256(_safe_regular(project_root / SANDBOX_PROBE_SOURCE, private=False))


def bridge_binding() -> tuple[bridge_endpoint.BridgeEndpoint, str]:
    try:
        endpoint = bridge_endpoint.discover_bridge_endpoint()
        endpoint.validate()
    except bridge_endpoint.BridgeEndpointError as exc:
        raise MilvusProofError("bridge_binding_unavailable") from exc
    value = {
        "network_name": endpoint.network_name,
        "network_id": endpoint.network_id,
        "subnet": endpoint.subnet,
        "gateway_ip": endpoint.gateway_ip,
        "host_alias": endpoint.host_alias,
    }
    return endpoint, _sha256(_canonical(value))


def _validate_policy(policy: Mapping[str, Any]) -> None:
    network = policy.get("network_policies")
    allowed_rule_sets = (
        {"siq_data_broker"},
        {"siq_data_broker", "siq_egress_guard", "siq_internal_services"},
    )
    if not isinstance(network, dict) or set(network) not in allowed_rule_sets:
        raise MilvusProofError("active_policy_network_contract_invalid")
    endpoints: dict[str, set[tuple[str, int]]] = {}
    for name, rule in network.items():
        if not isinstance(rule, dict) or not isinstance(rule.get("endpoints"), list):
            raise MilvusProofError("active_policy_network_contract_invalid")
        values: set[tuple[str, int]] = set()
        for item in rule["endpoints"]:
            if not isinstance(item, dict) or not isinstance(item.get("host"), str):
                raise MilvusProofError("active_policy_network_contract_invalid")
            port = item.get("port")
            if isinstance(port, bool) or not isinstance(port, int):
                raise MilvusProofError("active_policy_network_contract_invalid")
            values.add((item["host"], port))
        endpoints[name] = values
    if endpoints["siq_data_broker"] != {("host.openshell.internal", 18_793)}:
        raise MilvusProofError("active_policy_data_broker_invalid")
    all_ports = {port for values in endpoints.values() for _host, port in values}
    if all_ports & {5_432, 15_432, 19_530}:
        raise MilvusProofError("active_policy_direct_database_exposed")


def _validate_receipt(
    receipt: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    policy_sha256: str,
    now: int,
) -> None:
    expected_keys = {
        "broker_schema_version",
        "business_rows_modified",
        "captured_at_unix",
        "container_id",
        "direct_milvus",
        "milvus_catalog_sha256",
        "mutation_routes_denied",
        "passed",
        "policy_sha256",
        "profile",
        "read_operations",
        "run_id",
        "sandbox_id",
        "schema_version",
    }
    captured = receipt.get("captured_at_unix")
    direct = receipt.get("direct_milvus")
    if (
        set(receipt) != expected_keys
        or receipt.get("schema_version") != sandbox_probe.SCHEMA_VERSION
        or receipt.get("broker_schema_version") != sandbox_probe.BROKER_SCHEMA_VERSION
        or receipt.get("passed") is not True
        or receipt.get("profile") != PROFILE
        or receipt.get("run_id") != manifest.get("run_id")
        or receipt.get("sandbox_id") != manifest.get("sandbox_id")
        or receipt.get("container_id") != manifest.get("container_id")
        or receipt.get("policy_sha256") != policy_sha256
        or isinstance(captured, bool)
        or not isinstance(captured, int)
        or captured > now + MAX_CLOCK_SKEW_SECONDS
        or now - captured > PROOF_TTL_SECONDS
        or receipt.get("read_operations") != list(sandbox_probe.READ_OPERATIONS)
        or receipt.get("mutation_routes_denied") != list(sandbox_probe.MUTATION_ROUTES)
        or receipt.get("business_rows_modified") != 0
        or not SHA256_RE.fullmatch(str(receipt.get("milvus_catalog_sha256") or ""))
        or not isinstance(direct, dict)
        or direct.get("port") != 19_530
        or direct.get("result") != "denied"
        or direct.get("reason_class") not in {"connect_denied", "name_resolution_denied"}
    ):
        raise MilvusProofError("sandbox_receipt_invalid")


def build_proof(
    *,
    project_root: Path,
    manifest: Mapping[str, Any],
    policy: Mapping[str, Any],
    policy_bytes: bytes,
    receipt: Mapping[str, Any],
    bridge_sha256: str,
    now: int,
) -> dict[str, Any]:
    policy_sha256 = _sha256(policy_bytes)
    _validate_policy(policy)
    _validate_receipt(receipt, manifest=manifest, policy_sha256=policy_sha256, now=now)
    captured = int(receipt["captured_at_unix"])
    return {
        "schema_version": SCHEMA_VERSION,
        "decision": "GO",
        "passed": True,
        "captured_at_unix": captured,
        "valid_until_unix": captured + PROOF_TTL_SECONDS,
        "profile": PROFILE,
        "gateway": GATEWAY,
        "environment_binding": {
            "bridge_sha256": bridge_sha256,
            "data_broker_sha256": data_broker_sha256(project_root),
            "milvus_catalog_sha256": receipt["milvus_catalog_sha256"],
            "milvus_database": "default",
            "milvus_port": 19_530,
            "policy_contract_sha256": policy_contract_sha256(project_root),
            "sandbox_probe_sha256": sandbox_probe_sha256(project_root),
        },
        "sandbox_binding": {
            "active_policy_sha256": policy_sha256,
            "container_id_sha256": _sha256(str(manifest["container_id"]).encode("ascii")),
            "receipt_sha256": _sha256(_canonical(receipt)),
            "run_id_sha256": _sha256(str(manifest["run_id"]).encode("ascii")),
            "sandbox_id_sha256": _sha256(str(manifest["sandbox_id"]).encode("ascii")),
        },
        "checks": dict(EXPECTED_CHECKS),
    }


def validate_consumable_proof(
    path: Path,
    *,
    project_root: Path = REPO_ROOT,
    now: int | None = None,
    bridge_sha256: str | None = None,
) -> dict[str, Any]:
    proof = _read_json(path, private=True)
    expected_keys = {
        "captured_at_unix",
        "checks",
        "decision",
        "environment_binding",
        "gateway",
        "passed",
        "profile",
        "sandbox_binding",
        "schema_version",
        "valid_until_unix",
    }
    environment = proof.get("environment_binding")
    sandbox = proof.get("sandbox_binding")
    captured = proof.get("captured_at_unix")
    valid_until = proof.get("valid_until_unix")
    current = int(time.time()) if now is None else now
    actual_bridge_sha256 = bridge_sha256 or bridge_binding()[1]
    if (
        set(proof) != expected_keys
        or proof.get("schema_version") != SCHEMA_VERSION
        or proof.get("decision") != "GO"
        or proof.get("passed") is not True
        or proof.get("profile") != PROFILE
        or proof.get("gateway") != GATEWAY
        or isinstance(captured, bool)
        or not isinstance(captured, int)
        or isinstance(valid_until, bool)
        or not isinstance(valid_until, int)
        or valid_until - captured != PROOF_TTL_SECONDS
        or captured > current + MAX_CLOCK_SKEW_SECONDS
        or current >= valid_until
        or proof.get("checks") != EXPECTED_CHECKS
        or not isinstance(environment, dict)
        or set(environment)
        != {
            "bridge_sha256",
            "data_broker_sha256",
            "milvus_catalog_sha256",
            "milvus_database",
            "milvus_port",
            "policy_contract_sha256",
            "sandbox_probe_sha256",
        }
        or environment.get("bridge_sha256") != actual_bridge_sha256
        or environment.get("data_broker_sha256") != data_broker_sha256(project_root)
        or environment.get("policy_contract_sha256") != policy_contract_sha256(project_root)
        or environment.get("sandbox_probe_sha256") != sandbox_probe_sha256(project_root)
        or environment.get("milvus_database") != "default"
        or environment.get("milvus_port") != 19_530
        or not SHA256_RE.fullmatch(str(environment.get("milvus_catalog_sha256") or ""))
        or not isinstance(sandbox, dict)
        or set(sandbox)
        != {
            "active_policy_sha256",
            "container_id_sha256",
            "receipt_sha256",
            "run_id_sha256",
            "sandbox_id_sha256",
        }
        or any(not SHA256_RE.fullmatch(str(value or "")) for value in sandbox.values())
    ):
        raise MilvusProofError("milvus_proof_invalid")
    return proof


def _atomic_write(path: Path, content: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700 if mode == 0o600 else 0o755)
    if path.exists():
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise MilvusProofError("proof_output_unsafe")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise MilvusProofError("proof_output_failed") from exc
    finally:
        temporary.unlink(missing_ok=True)


def write_proof_outputs(
    *,
    project_root: Path,
    receipt: Mapping[str, Any],
    proof: Mapping[str, Any],
) -> None:
    _atomic_write(project_root / RAW_RECEIPT_RELATIVE, _canonical(receipt) + b"\n", mode=0o600)
    serialized = json.dumps(proof, ensure_ascii=True, indent=2, sort_keys=True).encode("ascii") + b"\n"
    _atomic_write(project_root / PROOF_RELATIVE, serialized, mode=0o600)
    _atomic_write(project_root / SANITIZED_JSON_RELATIVE, serialized, mode=0o644)
    markdown = (
        "# Milvus Sandbox Write Protection\n\n"
        "- Decision: `GO`\n"
        "- Direct sandbox access to `19530`: `denied`\n"
        "- Broker reads: `Search / Query / Get / Describe`\n"
        "- Broker mutation routes: `absent`\n"
        "- Business rows modified by proof: `0`\n"
        f"- Valid for: `{PROOF_TTL_SECONDS} seconds`\n\n"
        "The proof is bound to the active policy, verified sandbox/container, broker source, "
        "OpenShell bridge and a read-only Milvus schema observation.\n"
    ).encode("ascii")
    _atomic_write(project_root / SANITIZED_MD_RELATIVE, markdown, mode=0o644)


def _run_command(command: Sequence[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        list(command),
        cwd=REPO_ROOT,
        env=_minimal_child_environment(REPO_ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )
    if len(process.stdout.encode(errors="replace")) > MAX_FILE_BYTES:
        raise MilvusProofError("sandbox_probe_output_too_large")
    return process


def _collect_receipt(
    *,
    project_root: Path,
    manifest: Mapping[str, Any],
    policy_sha256: str,
    timeout: int,
) -> dict[str, Any]:
    _safe_regular(project_root / SANDBOX_PROBE_SOURCE, private=False)
    command = [
        str(project_root / "scripts/openshell/run_cli.sh"),
        "sandbox",
        "exec",
        "--name",
        str(manifest["sandbox_name"]),
        "--timeout",
        str(timeout),
        "--no-tty",
        "--",
        "/opt/siq/hermes/venv/bin/python",
        "-I",
        "-B",
        "/opt/siq/probe_milvus_sandbox_boundary.py",
        "--run-id",
        str(manifest["run_id"]),
        "--sandbox-id",
        str(manifest["sandbox_id"]),
        "--container-id",
        str(manifest["container_id"]),
        "--policy-sha256",
        policy_sha256,
    ]
    try:
        result = _run_command(command, timeout=timeout + 5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MilvusProofError("sandbox_probe_failed") from exc
    try:
        value = json.loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MilvusProofError("sandbox_probe_response_invalid") from exc
    if result.returncode != 0:
        error_code = value.get("error_code") if isinstance(value, dict) else None
        if (
            isinstance(value, dict)
            and set(value) == {"error_code", "ok"}
            and value.get("ok") is False
            and error_code in SANDBOX_PROBE_ERROR_CODES
        ):
            raise MilvusProofError(f"sandbox_probe_failed.{error_code}")
        raise MilvusProofError("sandbox_probe_failed")
    if result.stderr.strip() or not isinstance(value, dict) or value.get("ok") is not True:
        raise MilvusProofError("sandbox_probe_failed")
    value.pop("ok", None)
    return value


def collect_and_build(*, project_root: Path, run_id: str, timeout: int) -> dict[str, Any]:
    if project_root.resolve(strict=True) != REPO_ROOT or not RUN_ID_RE.fullmatch(run_id):
        raise MilvusProofError("proof_arguments_invalid")
    run_dir = project_root / RUNS_RELATIVE / run_id
    manifest = _read_json(run_dir / "run.json", private=True)
    if (
        set(manifest) != MANIFEST_FIELDS
        or manifest.get("schema_version") != LIFECYCLE_SCHEMA_VERSION
        or manifest.get("phase") != "running"
        or manifest.get("profile") != PROFILE
        or manifest.get("run_id") != run_id
    ):
        raise MilvusProofError("formal_sandbox_manifest_invalid")
    nonce = _safe_regular(run_dir / "run.nonce", private=True, max_bytes=128).decode("ascii").strip()
    if not NONCE_RE.fullmatch(nonce):
        raise MilvusProofError("formal_sandbox_nonce_invalid")
    policy_path = project_root / str(manifest["policy"])
    policy_bytes = _safe_regular(policy_path, private=True)
    try:
        policy = json.loads(policy_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MilvusProofError("active_policy_invalid") from exc
    if not isinstance(policy, dict):
        raise MilvusProofError("active_policy_invalid")
    policy_sha256 = _sha256(policy_bytes)
    if manifest.get("policy_sha256") != policy_sha256:
        raise MilvusProofError("active_policy_digest_mismatch")
    _, bridge_sha256 = bridge_binding()
    _validate_policy(policy)

    try:
        identity = LifecycleAdapter(project_root=project_root).verify_sandbox_identity(
            sandbox_name=str(manifest["sandbox_name"]),
            run_id=run_id,
            nonce=nonce,
            expected_sandbox_id=str(manifest["sandbox_id"]),
            expected_container_id=str(manifest["container_id"]),
            lifecycle_label=LIFECYCLE_LABEL,
        )
    except LifecycleError as exc:
        raise MilvusProofError("formal_sandbox_identity_invalid") from exc
    if identity.sandbox_id != manifest["sandbox_id"] or identity.container_id != manifest["container_id"]:
        raise MilvusProofError("formal_sandbox_identity_invalid")

    receipt = _collect_receipt(
        project_root=project_root,
        manifest=manifest,
        policy_sha256=policy_sha256,
        timeout=timeout,
    )
    now = int(time.time())
    proof = build_proof(
        project_root=project_root,
        manifest=manifest,
        policy=policy,
        policy_bytes=policy_bytes,
        receipt=receipt,
        bridge_sha256=bridge_sha256,
        now=now,
    )
    write_proof_outputs(project_root=project_root, receipt=receipt, proof=proof)
    return proof


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--timeout", type=int, default=30)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not 10 <= args.timeout <= 60:
            raise MilvusProofError("proof_timeout_invalid")
        proof = collect_and_build(project_root=args.project_root, run_id=args.run_id, timeout=args.timeout)
    except (MilvusProofError, OSError, ValueError) as exc:
        code = str(exc) if isinstance(exc, MilvusProofError) else "milvus_proof_failed"
        print(json.dumps({"ok": False, "decision": "NO_GO", "error_code": code}, sort_keys=True))
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "decision": proof["decision"],
                "schema_version": proof["schema_version"],
                "proof": PROOF_RELATIVE.as_posix(),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
