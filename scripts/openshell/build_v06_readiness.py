#!/usr/bin/env python3
"""Build a fail-closed SIQ OpenShell V0.6 machine-readiness document.

The builder consumes existing evidence only. It never probes, starts, stops, or
repairs a service. By default the JSON document is printed to stdout and no
file is changed. Writing requires ``--output``; replacing any existing file
additionally requires ``--replace``. Byte-for-byte reproducibility requires an
explicit ``--generated-at`` and an unchanged evidence snapshot.
"""

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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.openshell import (  # noqa: E402
    check_sanitized_artifacts,
    check_tracked_state,
    check_v06_completion as completion,
)

SCHEMA_VERSION = completion.READINESS_SCHEMA
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE = Path("artifacts/openshell/v0.6/baseline.json")
DEFAULT_SERVICE = completion.DEFAULT_SERVICE
DEFAULT_PROVIDER_INVENTORY = Path("var/openshell/proofs/provider-inventory.json")
DEFAULT_BROKER_STATUS = Path("var/openshell/proofs/broker-status.json")
DEFAULT_PROVIDER_PROBE = Path("artifacts/openshell/v0.6/provider-independent-20260716/probe.sanitized.json")
DEFAULT_MEMORY = completion.DEFAULT_MEMORY_WRITE_EVIDENCE
DEFAULT_HOST_EGRESS = completion.DEFAULT_HOST_EGRESS_COMPONENT
DEFAULT_FORMAL_HOST_ROLLBACK = completion.DEFAULT_FORMAL_HOST_ROLLBACK
DEFAULT_FORMAL_DELETE_GUARD = completion.DEFAULT_FORMAL_DELETE_GUARD
DEFAULT_FORMAL_FILESYSTEM_BOUNDARY = completion.DEFAULT_FORMAL_FILESYSTEM_BOUNDARY
DEFAULT_FORMAL_EGRESS = completion.DEFAULT_FORMAL_EGRESS_SANDBOX
DEFAULT_FORMAL_AUDIT = completion.DEFAULT_FORMAL_STRUCTURED_AUDIT
DEFAULT_FORMAL_FALLBACK_DRILL = completion.DEFAULT_FORMAL_FALLBACK_DRILL

BASELINE_SCHEMA = "siq.openshell.baseline.v0.6"
PROVIDER_SCHEMA = "siq.openshell.provider_inventory.v1"
BROKER_SCHEMA = "siq.openshell.broker-lifecycle.v1"
PROVIDER_PROBE_SCHEMA = "siq.openshell.provider_independent_sanitized.v1"
GATEWAY = "siq-openshell-dev"
OPENSHELL_VERSION = "0.0.83"
REQUIRED_PROVIDERS = frozenset(
    {
        "siq-minimax-cn-pool",
        "siq-stepfun",
        "siq-kimi-coding",
        "siq-tavily-search",
    }
)
DEFERRED_PROVIDERS = frozenset({"siq-exa-search"})
SAFE_PROVIDER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
MAX_INPUT_BYTES = 8 * 1024 * 1024
FRESHNESS_SECONDS = {
    "service_preflight": 5 * 60,
    "provider_inventory": 15 * 60,
    "broker_status": 60,
}
FUTURE_TOLERANCE = timedelta(seconds=5)


class ReadinessBuildError(RuntimeError):
    """Stable failure code that never contains evidence content or local paths."""


class EvidenceMissing(ReadinessBuildError):
    """An expected evidence path does not currently exist."""


@dataclass(frozen=True)
class Snapshot:
    path: Path
    relative_path: str
    content: bytes
    sha256: str
    info: os.stat_result
    payload: Mapping[str, Any] | None = None


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("ascii")


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ReadinessBuildError("generated_at_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReadinessBuildError("generated_at_invalid")
    return parsed.astimezone(timezone.utc)


def _normalized_generated_at(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc).replace(microsecond=0)
    if isinstance(value, str):
        return _parse_timestamp(value).replace(microsecond=0)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ReadinessBuildError("generated_at_invalid")
    return value.astimezone(timezone.utc).replace(microsecond=0)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReadinessBuildError("evidence_json_duplicate_key")
        result[key] = value
    return result


def _rooted_path(root: Path, requested: Path, *, missing_ok: bool = False) -> tuple[Path, str]:
    if "\x00" in os.fspath(requested) or "\\" in os.fspath(requested):
        raise ReadinessBuildError("evidence_path_invalid")
    candidate = requested.expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = Path(os.path.abspath(candidate))
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ReadinessBuildError("evidence_path_outside_project") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ReadinessBuildError("evidence_path_invalid")
    current = root
    for index, component in enumerate(relative.parts):
        current /= component
        try:
            info = current.lstat()
        except FileNotFoundError as exc:
            if missing_ok and index == len(relative.parts) - 1:
                raise EvidenceMissing("evidence_missing") from exc
            raise EvidenceMissing("evidence_missing") from exc
        except OSError as exc:
            raise ReadinessBuildError("evidence_path_unreadable") from exc
        if stat.S_ISLNK(info.st_mode):
            raise ReadinessBuildError("evidence_symlink_not_allowed")
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise ReadinessBuildError("evidence_parent_invalid")
    return candidate, relative.as_posix()


def _read_snapshot(
    root: Path,
    requested: Path,
    *,
    max_bytes: int = MAX_INPUT_BYTES,
    allow_local_path_finding: bool = False,
    parse_json: bool = True,
) -> Snapshot:
    path, relative = _rooted_path(root, requested, missing_ok=True)
    try:
        initial = path.lstat()
    except FileNotFoundError as exc:
        raise EvidenceMissing("evidence_missing") from exc
    if (
        not stat.S_ISREG(initial.st_mode)
        or initial.st_uid != os.geteuid()
        or initial.st_nlink != 1
        or initial.st_size <= 0
        or initial.st_size > max_bytes
        or stat.S_IMODE(initial.st_mode) & 0o002
    ):
        raise ReadinessBuildError("evidence_file_unsafe")

    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(opened, field) != getattr(initial, field) for field in identity):
            raise ReadinessBuildError("evidence_changed_during_read")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        final = os.fstat(descriptor)
        if any(getattr(final, field) != getattr(opened, field) for field in identity):
            raise ReadinessBuildError("evidence_changed_during_read")
    except ReadinessBuildError:
        raise
    except OSError as exc:
        raise ReadinessBuildError("evidence_file_unreadable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    content = b"".join(chunks)
    if len(content) > max_bytes:
        raise ReadinessBuildError("evidence_file_too_large")
    findings = check_sanitized_artifacts.scan_content(path, content, max_file_bytes=max_bytes)
    if any(not allow_local_path_finding or finding.code != "local_absolute_path" for finding in findings):
        raise ReadinessBuildError("evidence_not_sanitized")

    payload: Mapping[str, Any] | None = None
    if parse_json:
        try:
            parsed = json.loads(content.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
        except ReadinessBuildError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReadinessBuildError("evidence_json_invalid") from exc
        if not isinstance(parsed, dict):
            raise ReadinessBuildError("evidence_json_object_required")
        payload = parsed
    return Snapshot(
        path=path,
        relative_path=relative,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        info=final,
        payload=payload,
    )


def _sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _validate_baseline(payload: Mapping[str, Any]) -> dict[str, str] | None:
    sanitization = payload.get("sanitization")
    hermes = payload.get("hermes")
    openshell = payload.get("openshell")
    host = payload.get("host")
    if (
        payload.get("schema_version") != BASELINE_SCHEMA
        or sanitization
        != {
            "status": "passed",
            "contains_secret_values": False,
            "contains_user_content": False,
            "contains_raw_logs": False,
        }
        or not isinstance(hermes, dict)
        or hermes.get("upgrade_status") != "frozen"
        or not isinstance(hermes.get("version"), str)
        or not COMMIT_RE.fullmatch(str(hermes.get("source_commit") or ""))
        or not isinstance(openshell, dict)
        or openshell.get("candidate_reviewed") != OPENSHELL_VERSION
        or openshell.get("planned_gateway") != GATEWAY
        or not isinstance(host, dict)
        or not isinstance(host.get("node"), str)
    ):
        return None
    supervisor = openshell.get("installed_supervisor")
    if not isinstance(supervisor, dict) or supervisor.get("version") != OPENSHELL_VERSION:
        return None
    return {
        "openshell": OPENSHELL_VERSION,
        "hermes": str(hermes["version"]),
        "hermes_commit": str(hermes["source_commit"]),
        "node": str(host["node"]),
    }


def _validate_provider_inventory(payload: Mapping[str, Any]) -> list[str] | None:
    if set(payload) != {"schema_version", "openshell_version", "gateway", "providers"} or (
        payload.get("schema_version") != PROVIDER_SCHEMA
        or payload.get("openshell_version") != OPENSHELL_VERSION
        or payload.get("gateway") != GATEWAY
    ):
        return None
    providers = payload.get("providers")
    if not isinstance(providers, list):
        return None
    names: list[str] = []
    for item in providers:
        if (
            not isinstance(item, dict)
            or set(item) != {"name", "state"}
            or item.get("state") != "configured"
            or not isinstance(item.get("name"), str)
            or SAFE_PROVIDER_RE.fullmatch(item["name"]) is None
        ):
            return None
        names.append(item["name"])
    if names != sorted(names) or len(names) != len(set(names)):
        return None
    return names


def _validate_broker_status(payload: Mapping[str, Any]) -> bool:
    if set(payload) != {"schema_version", "ok", "action", "bridge", "brokers"}:
        return False
    if (
        payload.get("schema_version") != BROKER_SCHEMA
        or payload.get("ok") is not True
        or payload.get("action") != "status"
        or payload.get("bridge") != {"network": GATEWAY, "alias": "host.openshell.internal"}
    ):
        return False
    brokers = payload.get("brokers")
    if not isinstance(brokers, dict) or set(brokers) != {"egress", "data"}:
        return False
    for name, port in {"egress": 18792, "data": 18793}.items():
        if brokers.get(name) != {
            "port": port,
            "state": "running",
            "request_identity_required": True,
        }:
            return False
    return True


def _historical_timestamp(value: Any, generated_at: datetime) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = _parse_timestamp(value)
    except ReadinessBuildError:
        return False
    return parsed <= generated_at


def _validate_provider_probe(payload: Mapping[str, Any], *, generated_at: datetime) -> bool:
    required_checks = {
        "code_read_only",
        "configuration_read_only",
        "control_credentials_read_only",
        "memory_path_writable",
        "prompt_read_only",
        "runtime_session_writable",
        "runtime_state_directory_writable",
        "sensitive_paths_hidden",
        "source_data_read_only",
        "task_analysis_writable",
        "unknown_file_upload_denied",
        "workflow_read_only",
    }
    checks = payload.get("checks")
    cleanup = payload.get("cleanup")
    process = payload.get("process_hardening")
    provenance = payload.get("provenance")
    runtime = payload.get("runtime_isolation")
    container = payload.get("container_hardening")
    if (
        payload.get("schema_version") != PROVIDER_PROBE_SCHEMA
        or payload.get("result") != "PASS"
        or payload.get("profile") != "siq_analysis"
        or payload.get("mode") != "provider-independent"
        or payload.get("formal_business_sandbox") is not False
        or payload.get("provider_calls") != 0
        or payload.get("quality_validated") is not False
        or payload.get("readiness_effect") != "none"
        or not _historical_timestamp(payload.get("generated_at"), generated_at)
        or payload.get("limitations")
        != [
            "provider_independent_deny_all_scope",
            "no_hermes_business_process",
            "no_provider_calls",
            "not_quality_evidence",
            "readiness_effect_none",
        ]
        or not isinstance(checks, dict)
        or not isinstance(checks.get("check_count"), int)
        or isinstance(checks.get("check_count"), bool)
        or checks["check_count"] < 44
        or any(checks.get(key) is not True for key in required_checks)
        or cleanup
        != {
            "host_runtime_unchanged": True,
            "runtime_snapshot_removed": True,
            "sandbox_deleted": True,
            "sandbox_inventory_empty": True,
            "sentinels_removed": True,
        }
        or payload.get("mounts")
        != {
            "business_mount_count": 7,
            "control_mount_count": 5,
            "strict_bind_contract": "7_plus_5",
            "total_mount_count": 12,
        }
        or payload.get("network_isolation")
        != {
            "cloud_metadata": "denied",
            "egress_broker": "denied",
            "internal_model": "denied",
            "public_https": "denied",
        }
        or runtime
        != {
            "api_listener_count": 0,
            "provider_material_count": 0,
            "hermes_process_count": 0,
            "provider_call_capable_processes": 0,
            "provider_env_count": 0,
        }
        or container
        != {
            "bootstrap_capability_profile_exact": True,
            "device_request_count": 0,
            "host_device_count": 0,
            "privileged": False,
            "supervisor_bootstrap_user": "0",
        }
        or not isinstance(process, dict)
        or process.get("uid") != 1000
        or process.get("gid") != 1000
        or any(value is not True for key, value in process.items() if key not in {"uid", "gid"})
        or not isinstance(provenance, dict)
        or set(provenance)
        != {
            "image_id",
            "image_ref",
            "mount_plan_sha256",
            "policy_sha256",
            "raw_receipt_sha256",
            "task_policy_summary_sha256",
        }
        or not isinstance(provenance.get("image_id"), str)
        or not provenance["image_id"].startswith("sha256:")
        or not _sha256(provenance["image_id"][7:])
        or not isinstance(provenance.get("image_ref"), str)
        or not provenance["image_ref"]
        or any(
            not _sha256(provenance.get(key))
            for key in (
                "mount_plan_sha256",
                "policy_sha256",
                "raw_receipt_sha256",
                "task_policy_summary_sha256",
            )
        )
    ):
        return False
    return True


def _fresh(snapshot: Snapshot, generated_at: datetime, lifetime_seconds: int) -> bool:
    modified = datetime.fromtimestamp(snapshot.info.st_mtime, timezone.utc)
    return modified <= generated_at + FUTURE_TOLERANCE and generated_at - modified <= timedelta(
        seconds=lifetime_seconds
    )


def _binding(verification: dict[str, Any], name: str, snapshot: Snapshot) -> None:
    verification[f"{name}_evidence"] = snapshot.relative_path
    digest_key = {
        "formal_ab": "formal_ab_summary_sha256",
        "memory_write": "memory_write_evidence_sha256",
    }.get(name, f"{name}_sha256")
    verification[digest_key] = snapshot.sha256


def _safe_error_suffix(value: Any) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,95}", value):
        return None
    return value.replace(".", "_").replace("-", "_")


def _service_root_blockers(payload: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    raw = payload.get("blockers")
    if not isinstance(raw, list):
        return result
    for item in raw:
        if not isinstance(item, dict):
            continue
        suffix = _safe_error_suffix(item.get("error_code"))
        if suffix is not None:
            result.append(f"service_{suffix}")
    return sorted(set(result))


def _git(repo_root: Path, *arguments: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), *arguments],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReadinessBuildError("git_index_read_failed") from exc
    if completed.returncode != 0 or completed.stderr.strip():
        raise ReadinessBuildError("git_index_read_failed")
    return completed.stdout


def _normalized_runbook_paths() -> tuple[str, ...]:
    raw = getattr(completion, "REQUIRED_RUNBOOKS", ())
    paths: list[str] = []
    if not isinstance(raw, (tuple, list, frozenset, set)) or not raw:
        raise ReadinessBuildError("required_runbooks_contract_missing")
    for item in raw:
        value = item.as_posix() if isinstance(item, Path) else item
        if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
            raise ReadinessBuildError("required_runbooks_contract_invalid")
        pure = PurePosixPath(value)
        if (
            pure.is_absolute()
            or pure.as_posix() != value
            or any(part in {"", ".", ".."} for part in pure.parts)
            or not value.startswith("docs/runbooks/openshell/")
        ):
            raise ReadinessBuildError("required_runbooks_contract_invalid")
        paths.append(value)
    if len(paths) != len(set(paths)):
        raise ReadinessBuildError("required_runbooks_contract_invalid")
    return tuple(sorted(paths))


def git_index_runbook_bundle_sha256(repo_root: Path) -> str:
    """Hash the exact regular-file runbook blobs currently in the Git index."""

    # Prefer the completion gate's implementation when it exposes one, so the
    # producer and consumer cannot silently diverge on bundle framing.
    for helper_name in (
        "git_index_runbook_bundle_sha256",
        "_git_index_runbook_bundle_sha256",
        "_runbook_index_digest",
        "_runbook_bundle_sha256",
    ):
        helper = getattr(completion, helper_name, None)
        if callable(helper):
            try:
                value = helper(repo_root)
            except Exception as exc:
                raise ReadinessBuildError("runbook_index_bundle_invalid") from exc
            if not _sha256(value):
                raise ReadinessBuildError("runbook_index_bundle_invalid")
            return str(value)

    records: list[bytes] = []
    for path in _normalized_runbook_paths():
        index = _git(repo_root, "ls-files", "--stage", "--", path).decode("ascii").splitlines()
        if len(index) != 1:
            raise ReadinessBuildError("runbook_not_staged")
        fields = index[0].split(maxsplit=3)
        if len(fields) != 4 or fields[0] != "100644" or fields[2] != "0" or fields[3] != path:
            raise ReadinessBuildError("runbook_index_entry_invalid")
        blob = _git(repo_root, "show", f":{path}")
        records.append(path.encode("utf-8") + b"\0" + hashlib.sha256(blob).hexdigest().encode("ascii") + b"\n")
    return hashlib.sha256(b"".join(records)).hexdigest()


def _repository_scan(root: Path) -> bool:
    try:
        return not check_tracked_state.scan_tracked_state(root, require_allowlist=True)
    except Exception:
        return False


def _git_index_matches_snapshot(root: Path, snapshot: Snapshot) -> bool:
    try:
        raw = _git(root, "ls-files", "--stage", "-z", "--", snapshot.relative_path)
        rows = [row for row in raw.split(b"\0") if row]
        if len(rows) != 1:
            return False
        metadata, indexed_path = rows[0].split(b"\t", 1)
        mode, _object_id, stage = metadata.decode("ascii").split(" ", 2)
        if mode != "100644" or stage != "0" or indexed_path.decode("utf-8") != snapshot.relative_path:
            return False
        return _git(root, "show", f":{snapshot.relative_path}") == snapshot.content
    except (ReadinessBuildError, UnicodeDecodeError, ValueError):
        return False


def _published_evidence_path(snapshot: Snapshot) -> bool:
    return snapshot.relative_path.startswith("artifacts/openshell/")


def _capture(
    root: Path,
    path: Path | None,
    *,
    label: str,
    blockers: set[str],
    verification: dict[str, Any],
    allow_local_path_finding: bool = False,
    parse_json: bool = True,
) -> Snapshot | None:
    if path is None:
        blockers.add(f"{label}_evidence_missing")
        return None
    try:
        snapshot = _read_snapshot(
            root,
            path,
            allow_local_path_finding=allow_local_path_finding,
            parse_json=parse_json,
        )
    except EvidenceMissing:
        blockers.add(f"{label}_evidence_missing")
        return None
    except ReadinessBuildError:
        blockers.add(f"{label}_evidence_invalid")
        return None
    _binding(verification, label, snapshot)
    return snapshot


def _same_formal_runtime_provenance(
    formal_filesystem: Mapping[str, Any],
    formal_host: Mapping[str, Any],
    formal_delete: Mapping[str, Any],
    formal_egress: Mapping[str, Any],
) -> bool:
    filesystem = formal_filesystem["provenance"]
    filesystem_mount = formal_filesystem["mount_contract"]
    host = formal_host["provenance"]
    delete = formal_delete["provenance"]
    egress = formal_egress["provenance"]
    policy_values = {
        filesystem.get("policy_sha256"),
        host.get("policy_sha256"),
        delete.get("policy_sha256"),
        egress.get("policy_sha256"),
    }
    image_values = {
        filesystem.get("image_sha256"),
        host.get("image_sha256"),
        delete.get("image_sha256"),
        egress.get("image_sha256"),
    }
    mount_values = {
        filesystem_mount.get("mount_contract_sha256"),
        host.get("mount_contract_sha256"),
        delete.get("mount_contract_sha256"),
        egress.get("mount_contract_sha256"),
    }
    return (
        len(policy_values) == 1
        and len(image_values) == 1
        and len(mount_values) == 1
        and all(_sha256(next(iter(values))) for values in (policy_values, image_values, mount_values))
    )


def build_readiness(
    *,
    project_root: Path = REPO_ROOT,
    generated_at: datetime | str | None = None,
    baseline_path: Path = DEFAULT_BASELINE,
    service_path: Path = DEFAULT_SERVICE,
    provider_inventory_path: Path = DEFAULT_PROVIDER_INVENTORY,
    broker_status_path: Path = DEFAULT_BROKER_STATUS,
    provider_probe_path: Path | None = DEFAULT_PROVIDER_PROBE,
    memory_path: Path | None = DEFAULT_MEMORY,
    host_egress_path: Path | None = DEFAULT_HOST_EGRESS,
    formal_host_rollback_path: Path | None = DEFAULT_FORMAL_HOST_ROLLBACK,
    formal_delete_guard_path: Path | None = DEFAULT_FORMAL_DELETE_GUARD,
    formal_filesystem_boundary_path: Path | None = DEFAULT_FORMAL_FILESYSTEM_BOUNDARY,
    formal_egress_path: Path | None = DEFAULT_FORMAL_EGRESS,
    formal_audit_path: Path | None = DEFAULT_FORMAL_AUDIT,
    ab_summary_path: Path | None = None,
    ab_prerequisites_path: Path | None = None,
    formal_fallback_drill_path: Path | None = DEFAULT_FORMAL_FALLBACK_DRILL,
) -> dict[str, Any]:
    root = project_root.expanduser().resolve(strict=True)
    generated = _normalized_generated_at(generated_at)
    generated_text = _format_timestamp(generated)
    blockers: set[str] = set()
    verification: dict[str, Any] = {}

    baseline = _capture(root, baseline_path, label="baseline", blockers=blockers, verification=verification)
    versions = _validate_baseline(baseline.payload) if baseline and baseline.payload else None
    if versions is None:
        if baseline is not None:
            blockers.add("baseline_contract_invalid")
        versions = {
            "openshell": "unknown",
            "hermes": "unknown",
            "hermes_commit": "unknown",
            "node": "unknown",
        }

    service = _capture(root, service_path, label="service_preflight", blockers=blockers, verification=verification)
    service_valid = False
    service_go = False
    service_payload: Mapping[str, Any] = service.payload if service and service.payload else {}
    if service is not None:
        service_valid, service_go = completion._validate_service_report(service_payload)
        if not service_valid:
            blockers.add("service_preflight_contract_invalid")
        else:
            if not service_go:
                blockers.update(_service_root_blockers(service_payload))
                blockers.add("service_preflight_no_go")
            if not _fresh(service, generated, FRESHNESS_SECONDS["service_preflight"]):
                blockers.add("service_preflight_stale")

    provider = _capture(
        root,
        provider_inventory_path,
        label="provider_inventory",
        blockers=blockers,
        verification=verification,
    )
    provider_names = (
        _validate_provider_inventory(provider.payload)
        if provider is not None and provider.payload is not None
        else None
    )
    provider_inventory_valid = provider_names is not None
    if provider_names is None:
        if provider is not None:
            blockers.add("provider_inventory_contract_invalid")
        provider_names = []
    elif not _fresh(provider, generated, FRESHNESS_SECONDS["provider_inventory"]):
        blockers.add("provider_inventory_stale")
    missing_providers = sorted(REQUIRED_PROVIDERS - set(provider_names))
    if provider_inventory_valid:
        blockers.update(f"provider_missing_{name}" for name in missing_providers)

    broker = _capture(root, broker_status_path, label="broker_status", blockers=blockers, verification=verification)
    broker_valid = bool(broker and broker.payload and _validate_broker_status(broker.payload))
    if broker is not None and not broker_valid:
        blockers.add("broker_status_contract_invalid")
    elif broker is not None and not _fresh(broker, generated, FRESHNESS_SECONDS["broker_status"]):
        blockers.add("broker_status_stale")

    provider_probe = _capture(
        root,
        provider_probe_path,
        label="provider_independent_probe",
        blockers=blockers,
        verification=verification,
    )
    provider_probe_valid = bool(
        provider_probe
        and provider_probe.payload
        and _validate_provider_probe(provider_probe.payload, generated_at=generated)
    )
    if provider_probe is not None and not provider_probe_valid:
        blockers.add("provider_independent_probe_invalid")

    memory = _capture(root, memory_path, label="memory_write", blockers=blockers, verification=verification)
    memory_valid = False
    if memory is not None:
        try:
            validated_memory = completion._memory_write_evidence(
                root,
                memory.path,
                readiness_generated_at=generated_text,
            )
        except Exception:
            validated_memory = None
        memory_valid = bool(
            validated_memory is not None
            and validated_memory[0] == memory.payload
            and validated_memory[2] == memory.sha256
        )
    if memory is not None and not memory_valid:
        blockers.add("memory_write_evidence_invalid")

    host_egress = _capture(
        root, host_egress_path, label="host_egress_component", blockers=blockers, verification=verification
    )
    host_egress_valid = bool(
        host_egress and host_egress.payload and completion._validate_host_egress_component(host_egress.payload)
    )
    if host_egress is not None and not host_egress_valid:
        blockers.add("host_egress_component_invalid")

    formal_host = _capture(
        root,
        formal_host_rollback_path,
        label="formal_host_rollback",
        blockers=blockers,
        verification=verification,
    )
    formal_host_valid = bool(
        formal_host
        and formal_host.payload
        and completion._validate_formal_host_rollback(
            formal_host.payload,
            root=root,
            readiness_generated_at=generated_text,
        )
    )
    if formal_host is not None and not formal_host_valid:
        blockers.add("formal_host_rollback_invalid")

    formal_delete = _capture(
        root,
        formal_delete_guard_path,
        label="formal_delete_guard",
        blockers=blockers,
        verification=verification,
    )
    formal_delete_valid = bool(
        formal_delete
        and formal_delete.payload
        and completion._validate_formal_delete_guard(
            formal_delete.payload,
            root=root,
            readiness_generated_at=generated_text,
        )
    )
    if formal_delete is not None and not formal_delete_valid:
        blockers.add("formal_delete_guard_invalid")

    formal_filesystem = _capture(
        root,
        formal_filesystem_boundary_path,
        label="formal_filesystem_boundary",
        blockers=blockers,
        verification=verification,
    )
    formal_filesystem_valid = bool(
        formal_filesystem
        and formal_filesystem.payload
        and completion._validate_formal_filesystem_boundary(
            formal_filesystem.payload,
            root=root,
            readiness_generated_at=generated_text,
        )
    )
    if formal_filesystem is not None and not formal_filesystem_valid:
        blockers.add("formal_filesystem_boundary_invalid")

    formal_egress = _capture(
        root,
        formal_egress_path,
        label="formal_egress_sandbox",
        blockers=blockers,
        verification=verification,
    )
    formal_egress_valid = bool(
        host_egress_valid
        and host_egress
        and host_egress.payload
        and formal_egress
        and formal_egress.payload
        and completion._validate_formal_egress_sandbox(
            formal_egress.payload,
            readiness_generated_at=generated_text,
            host_component=host_egress.payload,
            host_component_path=host_egress.path,
            host_component_digest=host_egress.sha256,
            root=root,
        )
    )
    if formal_egress is not None and not formal_egress_valid:
        blockers.add("formal_egress_sandbox_invalid")

    formal_audit = _capture(
        root,
        formal_audit_path,
        label="formal_structured_audit",
        blockers=blockers,
        verification=verification,
    )
    formal_audit_valid = bool(
        formal_egress_valid
        and formal_egress
        and formal_egress.payload
        and formal_audit
        and formal_audit.payload
        and completion._validate_formal_structured_audit(
            formal_audit.payload,
            root=root,
            readiness_generated_at=generated_text,
            formal_egress=formal_egress.payload,
        )
    )
    if formal_audit is not None and not formal_audit_valid:
        blockers.add("formal_structured_audit_invalid")

    formal_provenance_bound = bool(
        formal_filesystem_valid
        and formal_filesystem
        and formal_filesystem.payload
        and formal_host_valid
        and formal_host
        and formal_host.payload
        and formal_delete_valid
        and formal_delete
        and formal_delete.payload
        and formal_egress_valid
        and formal_egress
        and formal_egress.payload
        and _same_formal_runtime_provenance(
            formal_filesystem.payload,
            formal_host.payload,
            formal_delete.payload,
            formal_egress.payload,
        )
    )
    if (
        all((formal_filesystem_valid, formal_host_valid, formal_delete_valid, formal_egress_valid))
        and not formal_provenance_bound
    ):
        blockers.add("formal_runtime_provenance_mismatch")

    ab_summary = _capture(root, ab_summary_path, label="formal_ab", blockers=blockers, verification=verification)
    ab_valid = bool(ab_summary and ab_summary.payload and completion._validate_ab_summary(ab_summary.payload))
    if ab_summary is not None and not ab_valid:
        blockers.add("formal_ab_summary_invalid")
    ab_prerequisites = _capture(
        root,
        ab_prerequisites_path,
        label="formal_ab_prerequisites",
        blockers=blockers,
        verification=verification,
        allow_local_path_finding=True,
    )
    prerequisites_valid = False
    if ab_valid and ab_summary and ab_summary.payload and ab_prerequisites:
        try:
            validated_prerequisites = completion._ab_prerequisites(
                root,
                ab_prerequisites.path,
                summary=ab_summary.payload,
                readiness_generated_at=generated_text,
            )
        except Exception:
            validated_prerequisites = None
        prerequisites_valid = bool(
            validated_prerequisites
            and validated_prerequisites[0] == ab_prerequisites.payload
            and validated_prerequisites[2] == ab_prerequisites.sha256
        )
    if ab_prerequisites is not None and not prerequisites_valid:
        blockers.add("formal_ab_prerequisites_invalid")
    quality_valid = bool(
        ab_valid
        and prerequisites_valid
        and ab_summary
        and ab_summary.payload
        and isinstance(ab_summary.payload.get("quality_gate"), dict)
        and ab_summary.payload["quality_gate"].get("passed") is True
        and ab_summary.payload["quality_gate"].get("failure_reasons") == []
    )
    if ab_valid and prerequisites_valid and not quality_valid:
        blockers.add("formal_ab_quality_not_passed")
    formal_fallback = _capture(
        root,
        formal_fallback_drill_path,
        label="formal_fallback_drill",
        blockers=blockers,
        verification=verification,
    )
    formal_fallback_valid = bool(
        quality_valid
        and ab_summary
        and ab_summary.payload
        and ab_prerequisites
        and ab_prerequisites.payload
        and formal_fallback
        and formal_fallback.payload
        and completion._validate_formal_fallback_drill(
            formal_fallback.payload,
            root=root,
            readiness_generated_at=generated_text,
            summary=ab_summary.payload,
            summary_sha256=ab_summary.sha256,
            prerequisites=ab_prerequisites.payload,
            prerequisites_sha256=ab_prerequisites.sha256,
        )
        and formal_filesystem
        and formal_filesystem.payload
        and formal_host
        and formal_host.payload
        and formal_delete
        and formal_delete.payload
        and formal_egress
        and formal_egress.payload
        and completion._formal_fallback_runtime_provenance_matches(
            formal_fallback.payload,
            formal_filesystem.payload,
            formal_host.payload,
            formal_delete.payload,
            formal_egress.payload,
        )
    )
    if formal_fallback is not None and not formal_fallback_valid:
        blockers.add("formal_fallback_drill_invalid")
    api_output_contract_valid = bool(
        formal_host_valid
        and formal_host
        and formal_host.payload
        and formal_host.payload.get("cleanup", {}).get("publisher_index_published") is True
        and ab_valid
        and prerequisites_valid
        and quality_valid
    )

    published_evidence = {
        "baseline": baseline,
        "service_preflight": service,
        "provider_independent_probe": provider_probe,
        "memory_write": memory,
        "host_egress_component": host_egress,
        "formal_host_rollback": formal_host,
        "formal_delete_guard": formal_delete,
        "formal_filesystem_boundary": formal_filesystem,
        "formal_egress_sandbox": formal_egress,
        "formal_structured_audit": formal_audit,
        "formal_ab": ab_summary,
        "formal_fallback_drill": formal_fallback,
    }
    published_evidence_index_bound = all(
        snapshot is not None and _published_evidence_path(snapshot) and _git_index_matches_snapshot(root, snapshot)
        for snapshot in published_evidence.values()
    )
    verification["published_evidence_index_scan"] = "passed" if published_evidence_index_bound else "failed"
    if not published_evidence_index_bound:
        blockers.add("published_evidence_index_scan_failed")

    docs_digest = ""
    try:
        docs_digest = git_index_runbook_bundle_sha256(root)
    except ReadinessBuildError:
        blockers.add("openshell_runbook_index_bundle_missing")
    verification["openshell_docs_sha256"] = docs_digest

    tracked_state_passed = _repository_scan(root)
    verification["tracked_state_scan"] = "passed" if tracked_state_passed else "failed"
    if not tracked_state_passed:
        blockers.add("tracked_state_scan_failed")

    service_by_id: dict[str, Mapping[str, Any]] = {}
    if service_valid:
        service_by_id = {
            item["service_id"]: item
            for item in service_payload.get("services", [])
            if isinstance(item, dict) and isinstance(item.get("service_id"), str)
        }
    model_mapping = {8004: "qwen_local", 8006: "gemma_local", 8007: "nemotron_local", 8013: "embedding"}
    model_reachability: dict[str, str] = {}
    for port, service_id in model_mapping.items():
        item = service_by_id.get(service_id, {})
        protocol = item.get("protocol_check") if isinstance(item, dict) else None
        online = bool(
            item.get("reachable") is True and isinstance(protocol, dict) and protocol.get("available") is True
        )
        model_reachability[str(port)] = "online" if online else "offline"
        if not online and port not in {8004, 8006}:
            blockers.add(f"internal_model_{port}_offline")

    security_checks = (
        {item.get("check_id"): item for item in service_payload.get("security_checks", []) if isinstance(item, dict)}
        if service_valid
        else {}
    )
    postgres_readonly = bool(security_checks.get("postgres_readonly_identity", {}).get("status") == "pass")
    milvus_protected = bool(security_checks.get("milvus_write_protection", {}).get("status") == "pass")

    runtime_go_inputs = all(
        (
            versions["openshell"] == OPENSHELL_VERSION,
            service_valid,
            service_go,
            provider_inventory_valid,
            not missing_providers,
            broker_valid,
            provider_probe_valid,
            memory_valid,
            host_egress_valid,
            formal_host_valid,
            formal_delete_valid,
            formal_filesystem_valid,
            formal_egress_valid,
            formal_audit_valid,
            formal_provenance_bound,
            ab_valid,
            prerequisites_valid,
            quality_valid,
            formal_fallback_valid,
            published_evidence_index_bound,
            bool(docs_digest),
            tracked_state_passed,
            all(model_reachability[str(port)] == "online" for port in (8007, 8013)),
        )
    )

    filesystem_immutable = (
        formal_filesystem.payload.get("immutable_write_denials", {})
        if formal_filesystem_valid and formal_filesystem and formal_filesystem.payload
        else {}
    )
    filesystem_writes = (
        formal_filesystem.payload.get("allowed_writes", {})
        if formal_filesystem_valid and formal_filesystem and formal_filesystem.payload
        else {}
    )
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_text,
        "decision": "GO" if runtime_go_inputs and not blockers else "NO_GO",
        "decision_scope": "formal_hermes_traffic_cutover",
        "default_runtime": "host",
        "versions": versions,
        "runtime_state": {
            "project_gateway": "healthy" if provider_inventory_valid else "unverified",
            "host_brokers": "healthy" if broker_valid else "unverified",
            "formal_image_smoke": "passed" if provider_probe_valid else "unverified",
            "provider_independent_probe": "passed" if provider_probe_valid else "unverified",
            "service_preflight": "GO" if service_valid and service_go else "NO_GO",
            "broker_preflight": "passed" if broker_valid else "failed",
            "formal_business_sandbox_created": (formal_filesystem_valid and formal_egress_valid and formal_audit_valid),
            "formal_ab_completed": ab_valid and prerequisites_valid,
            "quality_validated": quality_valid,
            "formal_fallback_drill": "passed" if formal_fallback_valid else "unverified",
        },
        "network_contract": {
            "broker_ports": [18792, 18793],
            "internal_model_ports": [8004, 8006, 8007, 8013],
            "internal_model_reachability": model_reachability,
        },
        "providers": {
            "configured": provider_names,
            "required_missing": missing_providers,
        },
        "data_boundary": {
            "postgres_readonly_verified": postgres_readonly,
            "milvus_mutation_route_exposed": not milvus_protected,
            "milvus_sandbox_write_proof": milvus_protected,
            "host_memory_write_verified": memory_valid,
        },
        "security_controls": {
            "project_code_readonly": bool(
                filesystem_immutable.get("code_read_only") is True and formal_provenance_bound
            ),
            "agent_control_files_readonly": bool(
                filesystem_immutable.get("configuration_read_only") is True
                and filesystem_immutable.get("prompt_read_only") is True
                and filesystem_immutable.get("workflow_read_only") is True
                and formal_provenance_bound
            ),
            "finalized_ingested_paths_readonly": bool(
                filesystem_immutable.get("source_data_read_only") is True and formal_provenance_bound
            ),
            "task_analysis_path_writable": bool(
                filesystem_writes.get("analysis_bind_read_write") is True and formal_provenance_bound
            ),
            "runtime_session_and_memory_paths_writable": bool(
                filesystem_writes.get("runtime_session_bind_read_write") is True
                and filesystem_writes.get("runtime_memory_bind_read_write") is True
                and memory_valid
                and formal_provenance_bound
            ),
            "unknown_file_upload_blocked": formal_egress_valid and formal_audit_valid,
            "high_risk_delete_guard": formal_delete_valid and formal_provenance_bound,
        },
        "lifecycle_safety": {
            "host_rollback_identity": ("exact_receipt_before_and_after" if formal_host_valid else "unverified"),
            "service_preflight": "evidence_bound",
            "broker_preflight": "evidence_bound",
            "formal_runtime_provenance": "cross_bound" if formal_provenance_bound else "unverified",
        },
        "contracts": {
            "api_and_output_paths_unchanged": api_output_contract_valid,
        },
        "verification": verification,
        "blockers": sorted(blockers),
    }

    result["verification"]["sanitized_artifact_scan"] = "passed"
    provisional = _canonical_json(result)
    if check_sanitized_artifacts.scan_content(Path("readiness.json"), provisional):
        result["verification"]["sanitized_artifact_scan"] = "failed"
        result["blockers"] = sorted({*result["blockers"], "readiness_output_not_sanitized"})
        result["decision"] = "NO_GO"
    if result["blockers"]:
        result["decision"] = "NO_GO"
    return result


def write_readiness(
    *,
    project_root: Path,
    output: Path,
    readiness: Mapping[str, Any],
    replace: bool = False,
) -> tuple[Path, str]:
    root = project_root.expanduser().resolve(strict=True)
    candidate = output.expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = Path(os.path.abspath(candidate))
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ReadinessBuildError("output_path_outside_project") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ReadinessBuildError("output_path_invalid")

    current = root
    for component in relative.parts[:-1]:
        current /= component
        try:
            info = current.lstat()
        except FileNotFoundError:
            try:
                current.mkdir(mode=0o755)
                info = current.lstat()
            except OSError as exc:
                raise ReadinessBuildError("output_parent_create_failed") from exc
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o002
        ):
            raise ReadinessBuildError("output_parent_unsafe")
    try:
        existing = candidate.lstat()
    except FileNotFoundError:
        existing = None
    if existing is not None:
        if not replace:
            raise ReadinessBuildError("output_exists_replace_required")
        if (
            stat.S_ISLNK(existing.st_mode)
            or not stat.S_ISREG(existing.st_mode)
            or existing.st_uid != os.geteuid()
            or existing.st_nlink != 1
        ):
            raise ReadinessBuildError("output_file_unsafe")
    content = _canonical_json(readiness)
    if check_sanitized_artifacts.scan_content(candidate, content):
        raise ReadinessBuildError("output_not_sanitized")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{candidate.name}.", dir=candidate.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, candidate)
        directory_fd = os.open(candidate.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        raise ReadinessBuildError("output_write_failed") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return candidate, hashlib.sha256(content).hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--generated-at", help="explicit ISO-8601 readiness timestamp")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--service-report", type=Path, default=DEFAULT_SERVICE)
    parser.add_argument("--provider-inventory", type=Path, default=DEFAULT_PROVIDER_INVENTORY)
    parser.add_argument("--broker-status", type=Path, default=DEFAULT_BROKER_STATUS)
    parser.add_argument("--provider-independent-probe", type=Path, default=DEFAULT_PROVIDER_PROBE)
    parser.add_argument("--memory-write-evidence", type=Path, default=DEFAULT_MEMORY)
    parser.add_argument("--host-egress-component", type=Path, default=DEFAULT_HOST_EGRESS)
    parser.add_argument("--formal-host-rollback-evidence", type=Path, default=DEFAULT_FORMAL_HOST_ROLLBACK)
    parser.add_argument("--formal-delete-guard-evidence", type=Path, default=DEFAULT_FORMAL_DELETE_GUARD)
    parser.add_argument(
        "--formal-filesystem-boundary-evidence",
        type=Path,
        default=DEFAULT_FORMAL_FILESYSTEM_BOUNDARY,
    )
    parser.add_argument("--formal-egress-sandbox-evidence", type=Path, default=DEFAULT_FORMAL_EGRESS)
    parser.add_argument("--formal-structured-audit-evidence", type=Path, default=DEFAULT_FORMAL_AUDIT)
    parser.add_argument("--ab-summary", type=Path)
    parser.add_argument("--ab-prerequisites", type=Path)
    parser.add_argument("--formal-fallback-drill-evidence", type=Path, default=DEFAULT_FORMAL_FALLBACK_DRILL)
    parser.add_argument("--output", type=Path, help="opt-in project-local output path")
    parser.add_argument("--replace", action="store_true", help="permit replacing the explicit output path")
    parser.add_argument("--require-go", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.replace and args.output is None:
        print(json.dumps({"ok": False, "error_code": "replace_requires_output"}, sort_keys=True), file=sys.stderr)
        return 2
    if args.output is not None and args.generated_at is None:
        print(
            json.dumps({"ok": False, "error_code": "output_requires_generated_at"}, sort_keys=True),
            file=sys.stderr,
        )
        return 2
    try:
        readiness = build_readiness(
            project_root=args.project_root,
            generated_at=args.generated_at,
            baseline_path=args.baseline,
            service_path=args.service_report,
            provider_inventory_path=args.provider_inventory,
            broker_status_path=args.broker_status,
            provider_probe_path=args.provider_independent_probe,
            memory_path=args.memory_write_evidence,
            host_egress_path=args.host_egress_component,
            formal_host_rollback_path=args.formal_host_rollback_evidence,
            formal_delete_guard_path=args.formal_delete_guard_evidence,
            formal_filesystem_boundary_path=args.formal_filesystem_boundary_evidence,
            formal_egress_path=args.formal_egress_sandbox_evidence,
            formal_audit_path=args.formal_structured_audit_evidence,
            ab_summary_path=args.ab_summary,
            ab_prerequisites_path=args.ab_prerequisites,
            formal_fallback_drill_path=args.formal_fallback_drill_evidence,
        )
        if args.output is None:
            sys.stdout.buffer.write(_canonical_json(readiness))
        else:
            destination, digest = write_readiness(
                project_root=args.project_root,
                output=args.output,
                readiness=readiness,
                replace=args.replace,
            )
            root = args.project_root.expanduser().resolve(strict=True)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "decision": readiness["decision"],
                        "output": destination.relative_to(root).as_posix(),
                        "sha256": digest,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                )
            )
    except (OSError, ReadinessBuildError, ValueError) as exc:
        code = str(exc) if isinstance(exc, ReadinessBuildError) else "readiness_build_failed"
        print(json.dumps({"ok": False, "error_code": code}, sort_keys=True), file=sys.stderr)
        return 2
    if args.require_go and readiness["decision"] != "GO":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
