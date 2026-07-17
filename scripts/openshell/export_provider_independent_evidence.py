#!/usr/bin/env python3
"""Export one verified provider-independent probe receipt without private identity."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from scripts.openshell import check_sanitized_artifacts
    from scripts.openshell import probe_siq_analysis_sandbox as probe
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.openshell import check_sanitized_artifacts
    from scripts.openshell import probe_siq_analysis_sandbox as probe

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "siq.openshell.provider_independent_sanitized.v1"
RAW_SCHEMA_VERSION = "siq.openshell.provider_independent_security_probe.v1"
PROBE_ID_RE = re.compile(r"probe-[0-9a-f]{12}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
LIMITATIONS = [
    "provider_independent_deny_all_scope",
    "no_hermes_business_process",
    "no_provider_calls",
    "not_quality_evidence",
    "readiness_effect_none",
]


class ProviderEvidenceError(RuntimeError):
    """Stable error that never includes private receipt values."""


def _stable_file(path: Path, *, maximum: int = 2 * 1024 * 1024) -> bytes:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum or path.is_symlink():
        raise ProviderEvidenceError("provider_probe_file_invalid")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        content = os.read(descriptor, maximum + 1)
        finished = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if len(content) > maximum or (opened.st_dev, opened.st_ino, opened.st_size) != (
        finished.st_dev,
        finished.st_ino,
        finished.st_size,
    ):
        raise ProviderEvidenceError("provider_probe_file_changed")
    return content


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _load_receipt(root: Path, probe_id: str) -> tuple[dict[str, Any], bytes, Path]:
    if not PROBE_ID_RE.fullmatch(probe_id):
        raise ProviderEvidenceError("provider_probe_id_invalid")
    run_dir = root / "var/openshell/siq-analysis/security-probes" / probe_id
    raw_path = run_dir / "probe.json"
    try:
        payload = json.loads(_stable_file(raw_path).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderEvidenceError("provider_probe_receipt_invalid") from exc
    content = _stable_file(raw_path)
    if not isinstance(payload, dict):
        raise ProviderEvidenceError("provider_probe_receipt_invalid")
    return payload, content, run_dir


def _validate_raw(root: Path, probe_id: str, raw: Mapping[str, Any], run_dir: Path) -> None:
    checks = raw.get("checks")
    mounts = raw.get("mounts")
    network = raw.get("network_isolation")
    process = raw.get("process_hardening")
    container = raw.get("container_hardening")
    runtime = raw.get("runtime_isolation")
    if (
        raw.get("schema_version") != RAW_SCHEMA_VERSION
        or raw.get("phase") != "passed"
        or raw.get("mode") != "provider-independent"
        or raw.get("profile") != "siq_analysis"
        or raw.get("probe_id") != probe_id
        or raw.get("provider_calls") != 0
        or raw.get("provider_calls_observed") is not True
        or raw.get("providers") != []
        or raw.get("formal_business_sandbox") is not False
        or raw.get("host_runtime_unchanged") is not True
        or raw.get("quality_validated") is not False
        or raw.get("readiness_effect") != "none"
        or not isinstance(checks, list)
        or len(checks) < 44
        or len(checks) != len(set(checks))
        or not set((*probe.FILESYSTEM_IMMUTABLE_DENIALS, *probe.FILESYSTEM_SENSITIVE_DENIALS)).issubset(checks)
        or mounts != {"business_mount_count": 7, "control_mount_count": 5, "total_mount_count": 12}
        or not isinstance(network, dict)
        or set(network) != {"cloud_metadata", "egress_broker", "internal_model", "public_https"}
        or any(not isinstance(value, dict) or value.get("result") != "denied" for value in network.values())
        or not isinstance(process, dict)
        or process.get("uid") != 1000
        or process.get("gid") != 1000
        or any(value is not True for key, value in process.items() if key not in {"schema_version", "uid", "gid"})
        or not isinstance(container, dict)
        or container.get("privileged") is not False
        or container.get("host_device_count") != 0
        or container.get("device_request_count") != 0
        or container.get("cap_add_profile") != "openshell_v0.0.83_bootstrap_exact"
        or container.get("supervisor_user") != "0"
        or not isinstance(runtime, dict)
        or any(runtime.get(key) != 0 for key in (
            "api_listener_count",
            "auth_material_count",
            "hermes_process_count",
            "provider_call_capable_processes",
            "provider_env_count",
        ))
        or raw.get("cleanup_error_code") != ""
    ):
        raise ProviderEvidenceError("provider_probe_contract_failed")
    for check in ("probe_sentinels_removed", "verified_probe_sandbox_deleted", "runtime_snapshot_removed"):
        if check not in checks:
            raise ProviderEvidenceError("provider_probe_cleanup_unverified")
    if (root / str(raw.get("runtime_snapshot", "invalid"))).exists():
        raise ProviderEvidenceError("provider_probe_runtime_snapshot_present")
    summary = run_dir / "task-policy.summary.json"
    policy = run_dir / "task-policy.yaml"
    if not SHA256_RE.fullmatch(str(raw.get("mount_plan_sha256", ""))):
        raise ProviderEvidenceError("provider_probe_provenance_invalid")
    if _sha256(_stable_file(policy)) != raw.get("policy_sha256") or not _stable_file(summary):
        raise ProviderEvidenceError("provider_probe_provenance_invalid")


def build_projection(root: Path, probe_id: str) -> dict[str, Any]:
    raw, content, run_dir = _load_receipt(root, probe_id)
    _validate_raw(root, probe_id, raw, run_dir)
    checks = set(raw["checks"])
    process = raw["process_hardening"]
    container = raw["container_hardening"]
    runtime = raw["runtime_isolation"]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.fromtimestamp((run_dir / "probe.json").stat().st_mtime, timezone.utc).isoformat(),
        "result": "PASS",
        "profile": "siq_analysis",
        "mode": "provider-independent",
        "formal_business_sandbox": False,
        "provider_calls": 0,
        "quality_validated": False,
        "readiness_effect": "none",
        "limitations": list(LIMITATIONS),
        "checks": {
            "check_count": len(checks),
            "source_data_read_only": "source_data_read_only" in checks,
            "code_read_only": "code_read_only" in checks,
            "configuration_read_only": "configuration_read_only" in checks,
            "prompt_read_only": "prompt_read_only" in checks,
            "workflow_read_only": "workflow_read_only" in checks,
            "control_credentials_read_only": "control_credentials_read_only" in checks,
            "sensitive_paths_hidden": "sensitive_paths_hidden" in checks,
            "task_analysis_writable": "analysis_bind_read_write" in checks,
            "runtime_state_directory_writable": "runtime_state_directory_bind_read_write" in checks,
            "runtime_session_writable": "runtime_session_bind_read_write" in checks,
            "memory_path_writable": "memory_path_read_write" in checks,
            "unknown_file_upload_denied": "unknown_curl_upload_denied" in checks,
        },
        "mounts": {**raw["mounts"], "strict_bind_contract": "7_plus_5"},
        "network_isolation": {key: "denied" for key in sorted(raw["network_isolation"])},
        "process_hardening": {
            key: value for key, value in process.items() if key != "schema_version"
        },
        "container_hardening": {
            "bootstrap_capability_profile_exact": container["cap_add_profile"] == "openshell_v0.0.83_bootstrap_exact",
            "device_request_count": container["device_request_count"],
            "host_device_count": container["host_device_count"],
            "privileged": container["privileged"],
            "supervisor_bootstrap_user": container["supervisor_user"],
        },
        "runtime_isolation": {
            "api_listener_count": runtime["api_listener_count"],
            "provider_material_count": runtime["auth_material_count"],
            "hermes_process_count": runtime["hermes_process_count"],
            "provider_call_capable_processes": runtime["provider_call_capable_processes"],
            "provider_env_count": runtime["provider_env_count"],
        },
        "cleanup": {
            "host_runtime_unchanged": True,
            "runtime_snapshot_removed": True,
            "sandbox_deleted": True,
            "sandbox_inventory_empty": True,
            "sentinels_removed": True,
        },
        "provenance": {
            "image_id": raw["image_id"],
            "image_ref": raw["image_ref"],
            "mount_plan_sha256": raw["mount_plan_sha256"],
            "policy_sha256": raw["policy_sha256"],
            "raw_receipt_sha256": _sha256(content),
            "task_policy_summary_sha256": _sha256(_stable_file(run_dir / "task-policy.summary.json")),
        },
    }


def _markdown(payload: Mapping[str, Any]) -> bytes:
    return (
        "# Provider-Independent OpenShell Probe\n\n"
        "- Result: `PASS`\n"
        "- Scope: deny-all, no Hermes business process or provider call\n"
        "- Mount contract: 7 business + 5 read-only control mounts\n"
        "- OpenShell control credentials: runtime-readable, not writable\n"
        "- Other sensitive paths: hidden\n"
        "- Sandbox, sentinels and runtime snapshot: removed\n\n"
        "Runtime identities, paths, credentials and business content are excluded.\n"
    ).encode("ascii")


def _write(path: Path, content: bytes, *, replace: bool) -> None:
    if path.exists() and not replace:
        raise ProviderEvidenceError("provider_evidence_exists")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def export(*, root: Path, probe_id: str, output_json: Path, output_markdown: Path, replace: bool) -> None:
    resolved = root.resolve(strict=True)
    if resolved != REPO_ROOT:
        raise ProviderEvidenceError("project_root_invalid")
    projection = build_projection(resolved, probe_id)
    json_content = json.dumps(projection, ensure_ascii=True, indent=2, sort_keys=True).encode("ascii") + b"\n"
    markdown_content = _markdown(projection)
    if check_sanitized_artifacts.scan_content(output_json, json_content) or check_sanitized_artifacts.scan_content(
        output_markdown, markdown_content
    ):
        raise ProviderEvidenceError("provider_evidence_not_sanitized")
    _write(output_json, json_content, replace=replace)
    _write(output_markdown, markdown_content, replace=replace)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--probe-id", required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)
    try:
        export(
            root=args.project_root,
            probe_id=args.probe_id,
            output_json=args.output_json,
            output_markdown=args.output_markdown,
            replace=args.replace,
        )
    except (OSError, ValueError, ProviderEvidenceError) as exc:
        code = str(exc) if isinstance(exc, ProviderEvidenceError) else "provider_evidence_failed"
        print(json.dumps({"ok": False, "error_code": code}, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, "schema_version": SCHEMA_VERSION}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
