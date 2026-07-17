#!/usr/bin/env python3
"""Compile a task-scoped OpenShell policy without contacting a gateway."""

from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import ipaddress
import json
import os
import re
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.openshell import bridge_endpoint  # noqa: E402
from scripts.openshell.runtime_state_lifecycle_smoke import is_passed_lifecycle_result  # noqa: E402

PROJECT_TOKEN = "${SIQ_PROJECT_ROOT}"
REGISTRY_SCHEMA = "siq.immutable_paths.v1"
PROFILE_SCHEMA = "siq.openshell.profile_policy.v1"
MAX_FILESYSTEM_PATHS = 256
MARKETS = {"us", "hk", "jp", "kr", "eu"}
ALLOWED_NON_PROJECT_WRITE_PATHS = {
    PurePosixPath("/dev/null"),
    PurePosixPath("/sandbox"),
    PurePosixPath("/tmp"),
}
REQUIRED_ETC_READ_ONLY_PATHS = {
    PurePosixPath(path)
    for path in (
        "/etc/alternatives",
        "/etc/ca-certificates",
        "/etc/ca-certificates.conf",
        "/etc/debian_version",
        "/etc/fonts",
        "/etc/gai.conf",
        "/etc/group",
        "/etc/host.conf",
        "/etc/hostname",
        "/etc/hosts",
        "/etc/inputrc",
        "/etc/ld.so.cache",
        "/etc/ld.so.conf",
        "/etc/ld.so.conf.d",
        "/etc/localtime",
        "/etc/netconfig",
        "/etc/networks",
        "/etc/nsswitch.conf",
        "/etc/os-release",
        "/etc/passwd",
        "/etc/profile",
        "/etc/protocols",
        "/etc/resolv.conf",
        "/etc/services",
        "/etc/ssl",
        "/etc/terminfo",
    )
}
OPENSHELL_CONTROL_ETC_PATH = PurePosixPath("/etc/openshell")
REQUIRED_BASE_READ_ONLY_PATHS = {
    PurePosixPath("/lib"),
    PurePosixPath("/home/sandbox/.bashrc"),
    PurePosixPath("/home/sandbox/.profile"),
    PurePosixPath("/opt/hermes-agent"),
    PurePosixPath("/opt/siq"),
    PurePosixPath("/proc"),
    PurePosixPath("/usr"),
} | REQUIRED_ETC_READ_ONLY_PATHS
REQUIRED_RUNTIME_FILES: set[str] = set()
CANDIDATE_STATE_RELATIVE = Path("var/openshell/siq-analysis/current-image.json")
CANDIDATE_SMOKE_RELATIVE = Path("var/openshell/siq-analysis/current-image.smoke.json")
CANDIDATE_SMOKE_SCRIPT_RELATIVE = Path("scripts/openshell/smoke_siq_analysis_image.sh")
CANDIDATE_SMOKE_CHECKS = [
    "network_none",
    "non_root_user",
    "hermes_version_exact",
    "credential_absence",
    "runtime_state_writable",
    "runtime_metadata_materialized",
    "api_key_required",
    "hermes_auth_placeholder_persistence",
    "healthcheck",
    "runtime_lifecycle_two_rounds",
    "runtime_lifecycle_directory_bind",
]
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
IMAGE_REF_RE = re.compile(r"siq/hermes-openshell-siq-analysis:[0-9a-f]{24}\Z")
IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
ALLOWED_PROJECT_WRITE_ROOTS = {
    "data/hermes/home/profiles/siq_analysis/cache",
    "data/hermes/home/profiles/siq_analysis/checkpoints",
    "data/hermes/home/profiles/siq_analysis/cron",
    "data/hermes/home/profiles/siq_analysis/logs",
    "data/hermes/home/profiles/siq_analysis/memories",
    "data/hermes/home/profiles/siq_analysis/sessions",
    "data/hermes/home/profiles/siq_analysis/workspace",
}


class PolicyCompileError(RuntimeError):
    pass


@dataclass(frozen=True)
class CompiledPolicy:
    policy: dict[str, Any]
    summary: dict[str, Any]
    content: bytes
    summary_content: bytes
    digest: str


def _json_yaml(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise PolicyCompileError(f"symlink input is not allowed: {path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PolicyCompileError(f"invalid JSON-compatible YAML: {path.name}") from exc
    if not isinstance(payload, dict):
        raise PolicyCompileError(f"policy input must be an object: {path.name}")
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_registry_digest(registry_path: Path, digest_path: Path, project_root: Path) -> str:
    actual = _sha256(registry_path)
    try:
        fields = digest_path.read_text(encoding="ascii").strip().split()
    except (OSError, UnicodeDecodeError) as exc:
        raise PolicyCompileError("immutable registry digest sidecar is missing or unreadable") from exc
    expected_relative = registry_path.relative_to(project_root.resolve(strict=True)).as_posix()
    if len(fields) != 2 or fields[0] != actual or fields[1] != expected_relative:
        raise PolicyCompileError("immutable registry digest sidecar does not match the registry")
    return actual


def _absolute_path(value: Any, *, project_root: PurePosixPath) -> PurePosixPath:
    text = str(value or "").strip().replace(PROJECT_TOKEN, str(project_root))
    path = PurePosixPath(text)
    if not path.is_absolute() or ".." in path.parts:
        raise PolicyCompileError("filesystem policy paths must be absolute and traversal-free")
    return path


def _relative_to(path: PurePosixPath, root: PurePosixPath) -> PurePosixPath | None:
    try:
        return path.relative_to(root)
    except ValueError:
        return None


def _is_ancestor(parent: PurePosixPath, child: PurePosixPath) -> bool:
    return parent == child or parent in child.parents


def _dynamic_kind(path: PurePosixPath, project_root: PurePosixPath) -> str | None:
    relative = _relative_to(path, project_root)
    if relative is None:
        return None
    parts = relative.parts
    if len(parts) >= 5 and parts[:3] == ("data", "wiki", "companies"):
        return parts[4]
    if len(parts) >= 6 and parts[0:2] == ("data", "wiki") and parts[2] in MARKETS and parts[3] == "companies":
        return parts[5]
    return None


def _validate_registry(
    registry: Mapping[str, Any],
    project_root: PurePosixPath,
    *,
    verify_sources: bool = False,
) -> list[PurePosixPath]:
    if registry.get("schema_version") != REGISTRY_SCHEMA:
        raise PolicyCompileError("immutable registry schema is unsupported")
    if registry.get("project_root") != "${SIQ_PROJECT_ROOT}":
        raise PolicyCompileError("immutable registry project root label is invalid")
    source_digest = str(registry.get("source_digest") or "")
    summary = registry.get("summary")
    if len(source_digest) != 64 or any(char not in "0123456789abcdef" for char in source_digest.lower()):
        raise PolicyCompileError("immutable registry source digest is invalid")
    if not isinstance(summary, dict) or not isinstance(summary.get("entry_count"), int):
        raise PolicyCompileError("immutable registry summary is missing")
    summary_by_kind = summary.get("by_kind")
    skipped_by_reason = summary.get("skipped_by_reason")
    if not isinstance(summary_by_kind, dict) or not isinstance(skipped_by_reason, dict):
        raise PolicyCompileError("immutable registry summary counters are missing")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in [*summary_by_kind.values(), *skipped_by_reason.values()]
    ):
        raise PolicyCompileError("immutable registry summary counters are invalid")
    entries = registry.get("entries")
    if not isinstance(entries, list):
        raise PolicyCompileError("immutable registry entries must be a list")
    if summary["entry_count"] != len(entries) or not entries:
        raise PolicyCompileError("immutable registry must contain a non-empty, consistent entry set")
    paths: list[PurePosixPath] = []
    source_paths: list[tuple[Path, str]] = []
    actual_by_kind: dict[str, int] = {}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("recursive") is not True:
            raise PolicyCompileError("immutable registry entry is malformed")
        raw_path = str(entry.get("path") or "")
        relative = PurePosixPath(raw_path)
        raw_source = str(entry.get("source_manifest") or "")
        source = PurePosixPath(raw_source)
        digest = str(entry.get("manifest_sha256") or "")
        finalization_digest = str(entry.get("finalization_sha256") or "")
        identity = entry.get("identity")
        kind = entry.get("kind")
        owner = entry.get("owner")
        if relative.is_absolute() or ".." in relative.parts or not raw_path:
            raise PolicyCompileError("immutable registry path must be repository-relative")
        if source.is_absolute() or ".." in source.parts or not raw_source or source.parent != relative:
            raise PolicyCompileError("immutable registry source manifest must belong to its entry")
        if (
            len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest.lower())
            or len(finalization_digest) != 64
            or any(char not in "0123456789abcdef" for char in finalization_digest.lower())
            or not isinstance(identity, dict)
            or kind not in {"finalized_report", "deal_evidence_snapshot"}
            or owner not in {"ingestion", "deal_evidence"}
            or (kind == "finalized_report" and owner != "ingestion")
            or (kind == "deal_evidence_snapshot" and owner != "deal_evidence")
            or not identity
            or any(not str(key).strip() or not str(value).strip() for key, value in identity.items())
        ):
            raise PolicyCompileError("immutable registry digest is invalid")
        if kind == "finalized_report" and not {"market", "company_id", "report_id"}.issubset(identity):
            raise PolicyCompileError("finalized report identity is incomplete")
        if kind == "deal_evidence_snapshot" and not {"deal_id", "snapshot_id"}.issubset(identity):
            raise PolicyCompileError("deal snapshot identity is incomplete")
        actual_by_kind[kind] = actual_by_kind.get(kind, 0) + 1
        paths.append(project_root / relative)
        source_paths.append((Path(str(project_root / source)), digest))
    if len(set(paths)) != len(paths) or summary_by_kind != actual_by_kind:
        raise PolicyCompileError("immutable registry summary does not match entries")
    if verify_sources:
        for source_path, expected_digest in source_paths:
            if source_path.is_symlink() or not source_path.is_file():
                raise PolicyCompileError("immutable registry source manifest is missing or not regular")
            if _sha256(source_path) != expected_digest:
                raise PolicyCompileError("immutable registry source manifest digest is stale")
    return sorted(set(paths), key=str)


def _required_runtime_paths(profile_fs: Mapping[str, Any], project_root: PurePosixPath) -> list[PurePosixPath]:
    raw_paths = profile_fs.get("required_files")
    if not isinstance(raw_paths, list) or len(raw_paths) != len(REQUIRED_RUNTIME_FILES):
        raise PolicyCompileError("profile must declare every required Hermes runtime file")
    required = {_absolute_path(path, project_root=project_root) for path in raw_paths}
    expected = {project_root / relative for relative in REQUIRED_RUNTIME_FILES}
    if required != expected:
        raise PolicyCompileError("profile required Hermes runtime files are not the fixed allowlist")
    return sorted(required, key=str)


def _validate_required_runtime_files(profile: Mapping[str, Any], project_root: Path) -> list[Path]:
    profile_fs = profile.get("filesystem_policy")
    if not isinstance(profile_fs, dict):
        raise PolicyCompileError("filesystem policy input is malformed")
    root = project_root.resolve(strict=True)
    required = _required_runtime_paths(profile_fs, PurePosixPath(root.as_posix()))
    validated: list[Path] = []
    for raw_path in required:
        candidate = Path(str(raw_path))
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise PolicyCompileError("required Hermes runtime file is outside the project root") from exc
        current = candidate
        while current != root:
            if current.exists() and current.is_symlink():
                raise PolicyCompileError(f"required Hermes runtime file uses a symlink: {candidate}")
            current = current.parent
        if not candidate.exists() or candidate.is_symlink() or not candidate.is_file():
            raise PolicyCompileError(f"required Hermes runtime file is missing or not regular: {candidate}")
        validated.append(candidate)
    return validated


def _private_json_attestation(path: Path, *, project_root: Path) -> dict[str, Any]:
    safe = _safe_input(project_root, path)
    info = safe.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
        or info.st_size > 64 * 1024
    ):
        raise PolicyCompileError("candidate runtime attestation file is unsafe")
    return _json_yaml(safe)


def _validate_candidate_runtime_attestation(project_root: Path) -> str:
    candidate_path = project_root / CANDIDATE_STATE_RELATIVE
    smoke_path = project_root / CANDIDATE_SMOKE_RELATIVE
    smoke_script = _safe_input(project_root, project_root / CANDIDATE_SMOKE_SCRIPT_RELATIVE)
    candidate = _private_json_attestation(candidate_path, project_root=project_root)
    smoke = _private_json_attestation(smoke_path, project_root=project_root)
    image_ref = candidate.get("image_ref")
    image_id = candidate.get("image_id")
    if (
        candidate.get("schema_version") != "siq.openshell.candidate_image.v1"
        or candidate.get("architecture") != "arm64"
        or candidate.get("user") != "sandbox:sandbox"
        or not isinstance(image_ref, str)
        or not IMAGE_REF_RE.fullmatch(image_ref)
        or not isinstance(image_id, str)
        or not IMAGE_ID_RE.fullmatch(image_id)
        or not SHA256_RE.fullmatch(str(candidate.get("context_sha256") or ""))
        or not SHA256_RE.fullmatch(str(candidate.get("runtime_config_sha256") or ""))
    ):
        raise PolicyCompileError("candidate runtime image state is invalid")
    if (
        smoke.get("schema_version") != "siq.openshell.candidate_image_smoke.v1"
        or smoke.get("status") != "passed"
        or smoke.get("profile") != "siq_analysis"
        or smoke.get("image_ref") != image_ref
        or smoke.get("image_id") != image_id
        or smoke.get("candidate_state_sha256") != _sha256(candidate_path)
        or smoke.get("smoke_script_sha256") != _sha256(smoke_script)
        or smoke.get("checks") != CANDIDATE_SMOKE_CHECKS
        or smoke.get("readiness_effect") != "none"
        or not is_passed_lifecycle_result(smoke.get("runtime_lifecycle"))
    ):
        raise PolicyCompileError("candidate runtime file attestation is missing or stale")
    return _sha256(smoke_path)


def _validate_write_target_aliases(policy: Mapping[str, Any], project_root: Path) -> None:
    """Reject symlinked write targets before a policy can reach a sandbox."""
    filesystem = policy.get("filesystem_policy")
    if not isinstance(filesystem, dict):
        raise PolicyCompileError("compiled filesystem policy is malformed")
    root = project_root.resolve(strict=True)
    for raw_path in filesystem.get("read_write", []):
        candidate = Path(str(raw_path))
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        current = candidate
        while current != root:
            if current.exists() and current.is_symlink():
                raise PolicyCompileError(f"read-write path uses a symlink: {candidate}")
            current = current.parent
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise PolicyCompileError(f"read-write path escapes the project root: {candidate}") from exc


def _materialize_network(base_network: Any, bridge_gateway_ip: str) -> tuple[dict[str, Any], str]:
    try:
        gateway = ipaddress.ip_address(bridge_gateway_ip)
    except ValueError as exc:
        raise PolicyCompileError("bridge gateway IP is invalid") from exc
    if (
        not isinstance(gateway, ipaddress.IPv4Address)
        or not any(gateway in network for network in bridge_endpoint.RFC1918_NETWORKS)
        or gateway.is_loopback
        or gateway.is_link_local
        or gateway.is_multicast
        or gateway.is_reserved
        or gateway.is_unspecified
    ):
        raise PolicyCompileError("bridge gateway IP must be one RFC1918 IPv4 address")
    if not isinstance(base_network, dict):
        raise PolicyCompileError("network policy must be an object")
    network = copy.deepcopy(base_network)
    gateway_cidr = f"{gateway.compressed}/32"
    for rule in network.values():
        if not isinstance(rule, dict) or not isinstance(rule.get("endpoints"), list):
            raise PolicyCompileError("network policy is malformed")
        for endpoint in rule["endpoints"]:
            if not isinstance(endpoint, dict) or "allowed_ips" in endpoint:
                raise PolicyCompileError("base network endpoints cannot predeclare allowed IP ranges")
            endpoint["allowed_ips"] = [gateway_cidr]
    return network, gateway_cidr


def _validate_network(policy: Mapping[str, Any], *, expected_gateway_cidr: str) -> None:
    network = policy.get("network_policies")
    required_rules = {"siq_egress_guard", "siq_data_broker", "siq_internal_services"}
    if not isinstance(network, dict) or set(network) != required_rules:
        raise PolicyCompileError("network policy must contain only the fixed SIQ broker and service rules")
    for name, rule in network.items():
        if (
            not isinstance(rule, dict)
            or not isinstance(rule.get("endpoints"), list)
            or not isinstance(rule.get("binaries"), list)
        ):
            raise PolicyCompileError(f"network policy is malformed: {name}")
        for endpoint in rule["endpoints"]:
            if (
                not isinstance(endpoint, dict)
                or set(endpoint) != {"host", "port", "allowed_ips"}
                or not str(endpoint.get("host") or "").strip()
                or endpoint.get("allowed_ips") != [expected_gateway_cidr]
            ):
                raise PolicyCompileError(f"network endpoint is malformed: {name}")
            port = endpoint.get("port")
            if not isinstance(port, int) or not 1 <= port <= 65535:
                raise PolicyCompileError(f"network endpoint port is invalid: {name}")
        for binary in rule["binaries"]:
            if not isinstance(binary, dict) or not str(binary.get("path") or "").startswith("/"):
                raise PolicyCompileError(f"network binary is malformed: {name}")
    endpoints = {
        name: {(str(item["host"]), int(item["port"])) for item in rule["endpoints"]} for name, rule in network.items()
    }
    if endpoints["siq_egress_guard"] != {("host.openshell.internal", 18792)}:
        raise PolicyCompileError("egress guard endpoint must use the fixed OpenShell host route")
    if endpoints["siq_data_broker"] != {("host.openshell.internal", 18793)}:
        raise PolicyCompileError("data broker endpoint must use the fixed OpenShell host route")
    if endpoints["siq_internal_services"] != {
        ("host.openshell.internal", 8004),
        ("host.openshell.internal", 8006),
        ("host.openshell.internal", 8007),
        ("host.openshell.internal", 8013),
    }:
        raise PolicyCompileError("internal service endpoints differ from the fixed model routes")
    all_ports = {port for rules in endpoints.values() for _, port in rules}
    if all_ports & {5432, 15432, 19530}:
        raise PolicyCompileError("sandbox database connections must use the read-only data broker")


def compile_policy(
    *,
    base: Mapping[str, Any],
    profile: Mapping[str, Any],
    registry: Mapping[str, Any],
    project_root: str,
    bridge_gateway_ip: str,
    writable_paths: Iterable[str],
    source_digests: Mapping[str, str] | None = None,
) -> CompiledPolicy:
    root = PurePosixPath(project_root)
    if not root.is_absolute() or ".." in root.parts:
        raise PolicyCompileError("project root must be an absolute normalized path")
    if base.get("version") != 1:
        raise PolicyCompileError("OpenShell policy version must be 1")
    if profile.get("schema_version") != PROFILE_SCHEMA or profile.get("profile") != "siq_analysis":
        raise PolicyCompileError("only the siq_analysis profile policy is supported in V0.6")

    base_fs = base.get("filesystem_policy")
    profile_fs = profile.get("filesystem_policy")
    if not isinstance(base_fs, dict) or not isinstance(profile_fs, dict):
        raise PolicyCompileError("filesystem policy input is malformed")
    if base_fs.get("include_workdir") is not False:
        raise PolicyCompileError("include_workdir must remain false")
    landlock = base.get("landlock")
    if not isinstance(landlock, dict) or landlock.get("compatibility") != "hard_requirement":
        raise PolicyCompileError("Landlock hard_requirement is required")
    process = base.get("process")
    if (
        not isinstance(process, dict)
        or str(process.get("run_as_user")) in {"0", "root"}
        or str(process.get("run_as_group")) in {"0", "root"}
    ):
        raise PolicyCompileError("sandbox process must be non-root")

    read_only = {_absolute_path(path, project_root=root) for path in base_fs.get("read_only", [])}
    if root not in read_only:
        raise PolicyCompileError("project root must be read-only")
    if not REQUIRED_BASE_READ_ONLY_PATHS.issubset(read_only):
        raise PolicyCompileError("base policy must keep system libraries and proc read-only")
    if any(
        _is_ancestor(path, OPENSHELL_CONTROL_ETC_PATH) or _is_ancestor(OPENSHELL_CONTROL_ETC_PATH, path)
        for path in read_only
    ):
        raise PolicyCompileError("base policy must not expose OpenShell control credentials under /etc")
    base_read_write = {_absolute_path(path, project_root=root) for path in base_fs.get("read_write", [])}
    if base_read_write != ALLOWED_NON_PROJECT_WRITE_PATHS:
        raise PolicyCompileError("base policy has an unsupported non-project write path")
    read_write = {
        *base_read_write,
        *{_absolute_path(path, project_root=root) for path in profile_fs.get("read_write", [])},
    }
    if profile_fs.get("dynamic_write_kinds") != ["analysis"]:
        raise PolicyCompileError("siq_analysis dynamic output class is fixed to analysis")
    allowed_kinds = {"analysis"}
    required_runtime_paths = _required_runtime_paths(profile_fs, root)
    if not set(required_runtime_paths).issubset(read_write):
        raise PolicyCompileError("required Hermes runtime files must be writable")
    dynamic_paths = [
        _absolute_path(
            path if str(path).startswith("/") else f"{root}/{str(path).lstrip('/')}",
            project_root=root,
        )
        for path in writable_paths
    ]
    if profile_fs.get("require_task_write_path") is True and not dynamic_paths:
        raise PolicyCompileError("siq_analysis requires at least one task-scoped write path")
    for path in dynamic_paths:
        kind = _dynamic_kind(path, root)
        if kind not in allowed_kinds:
            raise PolicyCompileError("task write path is outside the profile's allowed output class")
        read_write.add(path)

    for path in sorted(read_write, key=str):
        relative = _relative_to(path, root)
        if relative is None:
            if path not in ALLOWED_NON_PROJECT_WRITE_PATHS:
                raise PolicyCompileError("non-project write path is outside the fixed runtime allowlist")
            continue
        relative_text = relative.as_posix()
        if relative_text == "var/openshell" or relative_text.startswith("var/openshell/") or relative_text == "var":
            raise PolicyCompileError("OpenShell control state and its parent var root cannot be writable")
        if (
            relative_text not in ALLOWED_PROJECT_WRITE_ROOTS
            and not any(relative_text.startswith(f"{allowed}/") for allowed in ALLOWED_PROJECT_WRITE_ROOTS)
            and path not in dynamic_paths
        ):
            raise PolicyCompileError("static project write path is outside the controlled work surface")

    immutable_paths = _validate_registry(registry, root)
    for immutable in immutable_paths:
        if not any(_is_ancestor(read_path, immutable) for read_path in read_only):
            raise PolicyCompileError("immutable path is not covered by a read-only rule")

    intentional_overrides: list[dict[str, str]] = []
    for write_path in sorted(read_write, key=str):
        if write_path == PurePosixPath("/"):
            raise PolicyCompileError("filesystem root cannot be writable")
        for read_path in sorted(read_only, key=str):
            if write_path == read_path or _is_ancestor(write_path, read_path):
                raise PolicyCompileError("read-write path would weaken an equal or nested read-only rule")
            if _is_ancestor(read_path, write_path):
                intentional_overrides.append({"read_only_parent": str(read_path), "read_write_child": str(write_path)})
        for immutable in immutable_paths:
            if _is_ancestor(write_path, immutable) or _is_ancestor(immutable, write_path):
                raise PolicyCompileError("read-write path overlaps an immutable registry entry")

    path_count = len(read_only) + len(read_write)
    if path_count > MAX_FILESYSTEM_PATHS:
        raise PolicyCompileError(f"filesystem policy exceeds the {MAX_FILESYSTEM_PATHS}-path OpenShell limit")

    network, gateway_cidr = _materialize_network(base.get("network_policies"), bridge_gateway_ip)
    policy = {
        "version": 1,
        "filesystem_policy": {
            "include_workdir": False,
            "read_only": [str(path) for path in sorted(read_only, key=str)],
            "read_write": [str(path) for path in sorted(read_write, key=str)],
        },
        "landlock": dict(landlock),
        "process": dict(process),
        "network_policies": network,
    }
    _validate_network(policy, expected_gateway_cidr=gateway_cidr)
    summary = {
        "schema_version": "siq.openshell.policy_summary.v1",
        "profile": "siq_analysis",
        "project_root": PROJECT_TOKEN,
        "immutable_entry_count": len(immutable_paths),
        "task_scoped_write_count": len(dynamic_paths),
        "filesystem_path_count": path_count,
        "filesystem_path_limit": MAX_FILESYSTEM_PATHS,
        "network_gateway_cidr": gateway_cidr,
        "intentional_read_only_parent_overrides": intentional_overrides,
        "source_digests": dict(sorted((source_digests or {}).items())),
        "required_external_controls": list(profile.get("required_external_controls") or []),
        "required_runtime_files": [str(path.relative_to(root)) for path in required_runtime_paths],
        "native_capability_gaps": [
            "content-type and generic request-body upload classification",
            "batch deletion counting and automatic restore",
            "PostgreSQL and Milvus operation-level read-only enforcement",
            "dynamic filesystem policy updates without sandbox recreation",
        ],
    }
    content = (json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    summary_content = (json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return CompiledPolicy(
        policy=policy,
        summary=summary,
        content=content,
        summary_content=summary_content,
        digest=hashlib.sha256(content).hexdigest(),
    )


def _safe_output(project_root: Path, path: Path) -> Path:
    if ".." in path.parts:
        raise PolicyCompileError("output path is outside the project root")
    root = project_root.resolve(strict=True)
    candidate = (path if path.is_absolute() else root / path).absolute()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PolicyCompileError("output path is outside the project root") from exc
    current = candidate
    while current != root:
        if current.exists() and current.is_symlink():
            raise PolicyCompileError("symlink output path is not allowed")
        current = current.parent
    target = candidate.resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise PolicyCompileError("output path is outside the project root") from exc
    return target


def _safe_input(project_root: Path, path: Path) -> Path:
    root = project_root.resolve(strict=True)
    candidate = (path if path.is_absolute() else root / path).absolute()
    if ".." in candidate.parts:
        raise PolicyCompileError("policy input is outside the project root")
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PolicyCompileError("policy input is outside the project root") from exc
    current = candidate
    while current != root:
        if current.exists() and current.is_symlink():
            raise PolicyCompileError("symlink policy input is not allowed")
        current = current.parent
    target = candidate.resolve(strict=True)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise PolicyCompileError("policy input is outside the project root") from exc
    return target


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--base", type=Path, default=Path("infra/openshell/policies/base.yaml"))
    parser.add_argument("--profile", type=Path, default=Path("infra/openshell/policies/profiles/siq-analysis.yaml"))
    parser.add_argument("--registry", type=Path, default=Path("var/openshell/registry/immutable-paths.json"))
    parser.add_argument("--registry-digest", type=Path)
    parser.add_argument("--output", type=Path, default=Path("var/openshell/policies/siq-analysis.yaml"))
    parser.add_argument("--summary-output", type=Path, default=Path("var/openshell/policies/siq-analysis.summary.json"))
    parser.add_argument("--writable-path", action="append", default=[])
    parser.add_argument(
        "--runtime-file-source",
        choices=("host", "candidate-image"),
        default="host",
        help="Validate runtime files on the host or through the hash-bound candidate image smoke proof.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--diff", action="store_true")
    return parser


def _under(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    project_root = args.project_root.resolve()
    try:
        base_path = _safe_input(project_root, _under(project_root, args.base))
        profile_path = _safe_input(project_root, _under(project_root, args.profile))
        registry_path = _safe_input(project_root, _under(project_root, args.registry))
        registry_digest_path = _safe_input(
            project_root,
            _under(project_root, args.registry_digest or args.registry.with_suffix(".sha256")),
        )
        output_path = _safe_output(project_root, args.output)
        summary_path = _safe_output(project_root, args.summary_output)
        if output_path == summary_path:
            raise PolicyCompileError("policy and summary outputs must be different files")
        base = _json_yaml(base_path)
        profile = _json_yaml(profile_path)
        registry = _json_yaml(registry_path)
        runtime_attestation_digest = ""
        if args.runtime_file_source == "host":
            _validate_required_runtime_files(profile, project_root)
        else:
            runtime_attestation_digest = _validate_candidate_runtime_attestation(project_root)
        _validate_registry(registry, PurePosixPath(project_root.as_posix()), verify_sources=True)
        source_digests = {
            "base": _sha256(base_path),
            "profile": _sha256(profile_path),
            "registry": _verify_registry_digest(registry_path, registry_digest_path, project_root),
        }
        bridge = bridge_endpoint.discover_bridge_endpoint()
        source_digests["bridge_endpoint"] = hashlib.sha256(
            json.dumps(bridge.as_dict(), ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
        ).hexdigest()
        if runtime_attestation_digest:
            source_digests["runtime_attestation"] = runtime_attestation_digest
        compiled = compile_policy(
            base=base,
            profile=profile,
            registry=registry,
            project_root=project_root.as_posix(),
            bridge_gateway_ip=bridge.gateway_ip,
            writable_paths=args.writable_path,
            source_digests=source_digests,
        )
        _validate_write_target_aliases(compiled.policy, project_root)
        if args.dry_run:
            sys.stdout.buffer.write(compiled.content)
            return 0
        existing = output_path.read_bytes() if output_path.is_file() and not output_path.is_symlink() else b""
        existing_summary = (
            summary_path.read_bytes() if summary_path.is_file() and not summary_path.is_symlink() else b""
        )
        differs = existing != compiled.content or existing_summary != compiled.summary_content
        if differs and args.diff:
            sys.stdout.writelines(
                difflib.unified_diff(
                    existing.decode("utf-8", errors="replace").splitlines(keepends=True),
                    compiled.content.decode("utf-8").splitlines(keepends=True),
                    fromfile="siq-analysis.current",
                    tofile="siq-analysis.generated",
                )
            )
        if args.check:
            return 1 if differs else 0
        _atomic_write(output_path, compiled.content)
        _atomic_write(summary_path, compiled.summary_content)
        print(
            f"OpenShell policy: profile=siq_analysis paths={compiled.summary['filesystem_path_count']} "
            f"sha256={compiled.digest}"
        )
        return 0
    except (OSError, PolicyCompileError, bridge_endpoint.BridgeEndpointError) as exc:
        print(f"OpenShell policy error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
