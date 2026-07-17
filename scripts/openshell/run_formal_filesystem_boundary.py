#!/usr/bin/env python3
"""Attach to one running formal sandbox and publish filesystem boundary evidence."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.openshell import (  # noqa: E402
    check_sanitized_artifacts,
    formal_runtime_contract,
    probe_siq_analysis_sandbox as sandbox_probe,
    siq_analysis_transaction as transaction,
)
from scripts.openshell.siq_analysis_lifecycle import (  # noqa: E402
    GUARD_CLEANUP_PENDING_NAME,
    GUARD_OUTCOME_NAME,
    GUARD_TRIGGER_NAME,
    HERMES_COMMIT,
    NONCE_RE,
    PROFILE,
    LifecycleAdapter,
    LifecycleError,
    _assert_no_symlink_chain,
    _host_receipt_sha256,
    _read_json,
    _sha256_file,
)

SCHEMA_VERSION = "siq.openshell.formal-filesystem-boundary-evidence.v1"
RAW_SCHEMA_VERSION = "siq.openshell.formal-filesystem-boundary-raw-receipt.v1"
PROBE_SCHEMA_VERSION = "siq.openshell.formal_filesystem_boundary_probe.v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_RELATIVE = Path("infra/openshell/schemas/formal-filesystem-boundary-evidence.schema.json")
ARTIFACT_ROOT_RELATIVE = Path("artifacts/openshell/v0.6")
RAW_ROOT_RELATIVE = Path("var/openshell/proofs/formal-filesystem-boundary")
LOCK_RELATIVE = Path("var/openshell/locks/formal-filesystem-boundary.lock")
PROBE_MODULE_RELATIVE = Path("scripts/openshell/probe_siq_analysis_sandbox.py")
LIFECYCLE_RELATIVE = Path("scripts/openshell/siq_analysis_lifecycle.py")
TRANSACTION_RELATIVE = Path("scripts/openshell/siq_analysis_transaction.py")
MOUNT_CONTRACT_RELATIVE = Path("scripts/openshell/formal_runtime_contract.py")
RUNNER_RELATIVE = Path("scripts/openshell/run_formal_filesystem_boundary.py")
RUN_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,47}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
MAX_SOURCE_BYTES = 8 * 1024 * 1024
NOT_CLAIMED = [
    "business_inference_quality",
    "api_output_contract",
    "provider_route_availability",
    "host_rollback",
    "destructive_delete_threshold",
]


class FormalFilesystemEvidenceError(RuntimeError):
    """Stable, value-free failure for formal filesystem evidence generation."""

    def __init__(self, code: str) -> None:
        rendered = code if re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,95}", code) else "formal_filesystem_failed"
        self.code = rendered
        super().__init__(rendered)


@dataclass(frozen=True)
class ActiveBinding:
    transaction_receipt_sha256: str
    transaction_generation: int
    manifest_sha256: str
    sandbox_binding_sha256: str
    host_receipt_sha256: str
    run_id_sha256: str
    sandbox_id_sha256: str
    container_id_sha256: str
    session_id_sha256: str
    resource_receipts_sha256: str
    image_sha256: str
    policy_sha256: str
    mount_plan_sha256: str
    mount_contract_sha256: str
    runtime_config_sha256: str


@dataclass(frozen=True)
class ActiveCapture:
    context: sandbox_probe.ProbeContext
    binding: ActiveBinding
    transaction_id: str
    run_id: str
    sandbox_id: str
    container_id: str
    analysis_relative_path: str


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return _sha256(json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii"))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _stable_file(path: Path, *, max_bytes: int = MAX_SOURCE_BYTES) -> bytes:
    try:
        expected = path.lstat()
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise FormalFilesystemEvidenceError("evidence_source_invalid") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_uid != os.geteuid()
            or opened.st_size > max_bytes
            or (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino)
        ):
            raise FormalFilesystemEvidenceError("evidence_source_invalid")
        content = bytearray()
        while chunk := os.read(descriptor, min(64 * 1024, max_bytes + 1 - len(content))):
            content.extend(chunk)
            if len(content) > max_bytes:
                raise FormalFilesystemEvidenceError("evidence_source_invalid")
        finished = os.fstat(descriptor)
        final = path.lstat()
        identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        if identity != (finished.st_dev, finished.st_ino, finished.st_size, finished.st_mtime_ns) or identity != (
            final.st_dev,
            final.st_ino,
            final.st_size,
            final.st_mtime_ns,
        ):
            raise FormalFilesystemEvidenceError("evidence_source_changed")
        return bytes(content)
    except OSError as exc:
        raise FormalFilesystemEvidenceError("evidence_source_invalid") from exc
    finally:
        os.close(descriptor)


def _private_directory(path: Path, *, create: bool) -> None:
    if create:
        path.mkdir(mode=0o700, parents=False, exist_ok=True)
    try:
        info = path.lstat()
    except OSError as exc:
        raise FormalFilesystemEvidenceError("evidence_directory_invalid") from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise FormalFilesystemEvidenceError("evidence_directory_invalid")


def _artifact_path(root: Path, value: Path, *, suffix: str) -> Path:
    rendered = PurePosixPath(value.as_posix())
    if rendered.is_absolute() or any(part in {"", ".", ".."} for part in rendered.parts):
        raise FormalFilesystemEvidenceError("artifact_path_invalid")
    candidate = root.joinpath(*rendered.parts)
    artifact_root = root / ARTIFACT_ROOT_RELATIVE
    if candidate.parent != artifact_root or not candidate.name.endswith(suffix):
        raise FormalFilesystemEvidenceError("artifact_path_invalid")
    try:
        _assert_no_symlink_chain(root, artifact_root)
    except LifecycleError as exc:
        raise FormalFilesystemEvidenceError("artifact_path_invalid") from exc
    _private_directory(artifact_root, create=False)
    if candidate.exists() or candidate.is_symlink():
        raise FormalFilesystemEvidenceError("artifact_output_exists")
    return candidate


def _artifact_paths(root: Path, json_value: Path, markdown_value: Path) -> tuple[Path, Path]:
    json_path = _artifact_path(root, json_value, suffix=".sanitized.json")
    markdown_path = _artifact_path(root, markdown_value, suffix=".sanitized.md")
    json_stem = json_path.name.removesuffix(".sanitized.json")
    markdown_stem = markdown_path.name.removesuffix(".sanitized.md")
    if not json_stem or json_stem != markdown_stem:
        raise FormalFilesystemEvidenceError("artifact_pair_invalid")
    return json_path, markdown_path


def _ensure_raw_parent(root: Path) -> Path:
    parent = root / RAW_ROOT_RELATIVE
    base = parent.parent
    try:
        _assert_no_symlink_chain(root, base)
    except LifecycleError as exc:
        raise FormalFilesystemEvidenceError("evidence_directory_invalid") from exc
    _private_directory(base, create=False)
    if not parent.exists() and not parent.is_symlink():
        _private_directory(parent, create=True)
    else:
        _private_directory(parent, create=False)
    return parent


@contextmanager
def _runner_lock(root: Path) -> Iterator[None]:
    path = root / LOCK_RELATIVE
    _private_directory(path.parent, create=False)
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError as exc:
        raise FormalFilesystemEvidenceError("evidence_lock_invalid") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
            raise FormalFilesystemEvidenceError("evidence_lock_invalid")
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FormalFilesystemEvidenceError("evidence_runner_busy") from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _validate_running_record(record: Mapping[str, Any], *, run_id: str) -> None:
    resources = record.get("resources")
    intent = record.get("intent")
    if (
        record.get("schema_version") != transaction.JOURNAL_SCHEMA
        or record.get("phase") != "running"
        or record.get("terminal_action") != ""
        or not isinstance(intent, dict)
        or intent.get("profile") != PROFILE
        or intent.get("run_id") != run_id
        or not isinstance(resources, dict)
        or set(resources) != set(transaction.FORMAL_RESOURCES)
    ):
        raise FormalFilesystemEvidenceError("formal_active_transaction_required")
    for name, kind in transaction.FORMAL_RESOURCES.items():
        item = resources.get(name)
        if (
            not isinstance(item, dict)
            or item.get("kind") != kind
            or item.get("state") != "present"
            or not SHA256_RE.fullmatch(str(item.get("intent_sha256") or ""))
            or not SHA256_RE.fullmatch(str(item.get("receipt_sha256") or ""))
        ):
            raise FormalFilesystemEvidenceError("formal_transaction_receipt_incomplete")


def _guard_trigger_absent(run_dir: Path) -> None:
    for name in (GUARD_TRIGGER_NAME, GUARD_OUTCOME_NAME, GUARD_CLEANUP_PENDING_NAME):
        path = run_dir / name
        if path.exists() or path.is_symlink():
            raise FormalFilesystemEvidenceError("guard_event_present")


def capture_active_binding(
    *,
    project_root: Path,
    run_id: str,
    adapter: LifecycleAdapter | None = None,
) -> ActiveCapture:
    if not RUN_ID_RE.fullmatch(run_id):
        raise FormalFilesystemEvidenceError("run_id_invalid")
    root = project_root.expanduser().resolve(strict=True)
    if root != REPO_ROOT:
        raise FormalFilesystemEvidenceError("project_root_invalid")
    lifecycle = adapter or LifecycleAdapter(project_root=root)
    try:
        context = sandbox_probe.load_context(root, run_id)
        record = lifecycle._transaction_for_run(run_id)
        spec, manifest = lifecycle._load_manifest(run_id)
        _validate_running_record(record, run_id=run_id)
        if manifest.get("phase") != "running":
            raise FormalFilesystemEvidenceError("formal_running_manifest_required")
        if (
            context.run_dir != spec.run_dir
            or context.sandbox_id != manifest.get("sandbox_id")
            or context.container_id != manifest.get("container_id")
            or context.manifest != manifest
        ):
            raise FormalFilesystemEvidenceError("formal_context_binding_mismatch")
        lifecycle._verify_transaction_receipts(record, spec, manifest)
        status = lifecycle.status(profile=PROFILE, run_id=run_id)
        if status != {
            "ok": True,
            "profile": PROFILE,
            "run_id": run_id,
            "status": "running",
            "guard": True,
            "forward": True,
            "sandbox": True,
            "health": True,
        }:
            raise FormalFilesystemEvidenceError("formal_sandbox_not_healthy")
        nonce = lifecycle._read_secret(spec, "run.nonce", NONCE_RE, str(manifest["run_nonce_sha256"]))
        identity = lifecycle.verify_sandbox_identity(
            sandbox_name=spec.sandbox_name,
            run_id=run_id,
            nonce=nonce,
            expected_sandbox_id=str(manifest["sandbox_id"]),
            expected_container_id=str(manifest["container_id"]),
        )
        if identity.sandbox_id != context.sandbox_id or identity.container_id != context.container_id:
            raise FormalFilesystemEvidenceError("formal_sandbox_identity_mismatch")
        baseline = lifecycle._read_host_baseline(spec)
        if lifecycle._stable_host_receipt() != baseline:
            raise FormalFilesystemEvidenceError("host_runtime_identity_changed")
        _guard_trigger_absent(spec.run_dir)

        runtime_snapshot = root / str(manifest["runtime_snapshot"])
        runtime_manifest = _read_json(runtime_snapshot / "snapshot-manifest.json", root=root)
        inventory = runtime_manifest.get("inventory")
        config = inventory.get("config") if isinstance(inventory, dict) else None
        runtime_config_sha256 = config.get("compiled_sha256") if isinstance(config, dict) else None
        if not isinstance(runtime_config_sha256, str) or not SHA256_RE.fullmatch(runtime_config_sha256):
            raise FormalFilesystemEvidenceError("runtime_config_binding_invalid")
        lifecycle._validate_compiled_runtime_snapshot(runtime_snapshot, expected_sha256=runtime_config_sha256)

        mount_contract = formal_runtime_contract.normalized_mount_contract(
            project_root=root,
            mount_plan=context.mount_plan,
            analysis_root=context.analysis_path,
            runtime_snapshot=context.runtime_snapshot,
        )
        if mount_contract["raw_mount_plan_sha256"] != manifest.get("mount_plan_sha256"):
            raise FormalFilesystemEvidenceError("mount_contract_binding_invalid")

        image_id = str(manifest.get("image_id") or "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise FormalFilesystemEvidenceError("image_binding_invalid")
        resource_receipts = {
            name: {
                "kind": item["kind"],
                "disposition": item["disposition"],
                "state": item["state"],
                "generation": item["generation"],
                "intent_sha256": item["intent_sha256"],
                "receipt_sha256": item["receipt_sha256"],
            }
            for name, item in sorted(record["resources"].items())
        }
        sandbox_binding = {
            "profile": PROFILE,
            "run_id": run_id,
            "sandbox_id": context.sandbox_id,
            "container_id": context.container_id,
            "image_id": image_id,
            "policy_sha256": manifest["policy_sha256"],
            "mount_plan_sha256": manifest["mount_plan_sha256"],
            "run_nonce_sha256": manifest["run_nonce_sha256"],
        }
        binding = ActiveBinding(
            transaction_receipt_sha256=_canonical_sha256(record),
            transaction_generation=int(record["generation"]),
            manifest_sha256=_sha256_file(spec.run_dir / "run.json"),
            sandbox_binding_sha256=_canonical_sha256(sandbox_binding),
            host_receipt_sha256=_host_receipt_sha256(baseline),
            run_id_sha256=_sha256(run_id.encode("ascii")),
            sandbox_id_sha256=_sha256(context.sandbox_id.encode("ascii")),
            container_id_sha256=_sha256(context.container_id.encode("ascii")),
            session_id_sha256=_sha256(run_id.encode("ascii")),
            resource_receipts_sha256=_canonical_sha256(resource_receipts),
            image_sha256=image_id.removeprefix("sha256:"),
            policy_sha256=str(manifest["policy_sha256"]),
            mount_plan_sha256=str(manifest["mount_plan_sha256"]),
            mount_contract_sha256=str(mount_contract["mount_contract_sha256"]),
            runtime_config_sha256=runtime_config_sha256,
        )
        return ActiveCapture(
            context=context,
            binding=binding,
            transaction_id=str(record["transaction_id"]),
            run_id=run_id,
            sandbox_id=context.sandbox_id,
            container_id=context.container_id,
            analysis_relative_path=str(manifest["analysis_relative_path"]),
        )
    except FormalFilesystemEvidenceError:
        raise
    except (LifecycleError, sandbox_probe.ProbeError, transaction.TransactionError, OSError, ValueError) as exc:
        raise FormalFilesystemEvidenceError("formal_active_transaction_required") from exc


def _validate_probe_result(payload: Mapping[str, Any], *, run_id: str) -> None:
    expected_fields = {
        "schema_version",
        "ok",
        "profile",
        "run_id",
        "checks",
        "mounts",
        "immutable_write_denials",
        "sensitive_read_denials",
        "allowed_writes",
        "host_visibility_receipts",
        "filesystem_response_sha256",
        "filesystem_probe_sha256",
        "cleanup_succeeded",
        "residual_host_sentinel_count",
    }
    expected_checks = [
        *sandbox_probe.FILESYSTEM_IDENTITY_CHECKS,
        *sandbox_probe.FILESYSTEM_IMMUTABLE_DENIALS,
        *sandbox_probe.FILESYSTEM_SENSITIVE_DENIALS,
        "analysis_bind_read_write",
        "runtime_state_directory_bind_read_write",
        "runtime_session_bind_read_write",
        "runtime_memory_bind_read_write",
        "tmp_scratch_write",
        "probe_sentinels_removed",
    ]
    receipts = payload.get("host_visibility_receipts")
    if (
        set(payload) != expected_fields
        or payload.get("schema_version") != PROBE_SCHEMA_VERSION
        or payload.get("ok") is not True
        or payload.get("profile") != PROFILE
        or payload.get("run_id") != run_id
        or payload.get("checks") != expected_checks
        or payload.get("mounts") != {"business_mount_count": 7, "control_mount_count": 5, "total_mount_count": 12}
        or payload.get("immutable_write_denials") != {key: True for key in sandbox_probe.FILESYSTEM_IMMUTABLE_DENIALS}
        or payload.get("sensitive_read_denials") != {key: True for key in sandbox_probe.FILESYSTEM_SENSITIVE_DENIALS}
        or payload.get("allowed_writes") != {key: True for key in sandbox_probe.FILESYSTEM_ALLOWED_WRITES}
        or not isinstance(receipts, dict)
        or set(receipts) != {"analysis", "runtime_state", "runtime_session", "runtime_memory"}
        or any(not isinstance(value, str) or not SHA256_RE.fullmatch(value) for value in receipts.values())
        or not SHA256_RE.fullmatch(str(payload.get("filesystem_response_sha256") or ""))
        or payload.get("filesystem_probe_sha256") != _sha256(sandbox_probe.FILESYSTEM_PROBE.encode("utf-8"))
        or payload.get("cleanup_succeeded") is not True
        or payload.get("residual_host_sentinel_count") != 0
    ):
        raise FormalFilesystemEvidenceError("filesystem_probe_receipt_invalid")


def _raw_receipt(
    *,
    generated_at: str,
    before: ActiveCapture,
    after: ActiveCapture,
    probe_result: Mapping[str, Any],
    artifact_json: Path,
    artifact_markdown: Path,
    root: Path,
) -> dict[str, Any]:
    return {
        "schema_version": RAW_SCHEMA_VERSION,
        "generated_at": generated_at,
        "decision": "GO",
        "profile": PROFILE,
        "scope": "formal_running_filesystem_boundary",
        "runtime_identifiers": {
            "transaction_id": before.transaction_id,
            "run_id": before.run_id,
            "sandbox_id": before.sandbox_id,
            "container_id": before.container_id,
            "analysis_relative_path": before.analysis_relative_path,
        },
        "before": asdict(before.binding),
        "after": asdict(after.binding),
        "probe": dict(probe_result),
        "artifact_targets": {
            "json": artifact_json.relative_to(root).as_posix(),
            "markdown": artifact_markdown.relative_to(root).as_posix(),
        },
        "credential_material_present": False,
    }


def build_evidence(
    *,
    project_root: Path,
    generated_at: str,
    before: ActiveCapture,
    after: ActiveCapture,
    probe_result: Mapping[str, Any],
    raw_receipt_sha256: str,
) -> dict[str, Any]:
    if before.binding != after.binding or (
        before.transaction_id,
        before.run_id,
        before.sandbox_id,
        before.container_id,
        before.analysis_relative_path,
    ) != (
        after.transaction_id,
        after.run_id,
        after.sandbox_id,
        after.container_id,
        after.analysis_relative_path,
    ):
        raise FormalFilesystemEvidenceError("formal_binding_changed_during_probe")
    _validate_probe_result(probe_result, run_id=before.run_id)
    root = project_root.resolve(strict=True)
    schema_bytes = _stable_file(root / SCHEMA_RELATIVE)
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "decision": "GO",
        "profile": PROFILE,
        "scope": "formal_running_filesystem_boundary",
        "formal_business_sandbox": True,
        "business_inference_exercised": False,
        "component_acceptance": True,
        "overall_readiness_effect": "component_evidence_only",
        "host_runtime_unchanged": True,
        "traffic_cutover_performed": False,
        "transaction": {
            "contract": "active_transaction_v2_exact_receipts",
            "generation": before.binding.transaction_generation,
            "before_receipt_sha256": before.binding.transaction_receipt_sha256,
            "after_receipt_sha256": after.binding.transaction_receipt_sha256,
            "run_id_sha256": before.binding.run_id_sha256,
            "sandbox_id_sha256": before.binding.sandbox_id_sha256,
            "container_id_sha256": before.binding.container_id_sha256,
            "session_id_sha256": before.binding.session_id_sha256,
            "policy_sha256": before.binding.policy_sha256,
            "resource_receipts_sha256": before.binding.resource_receipts_sha256,
        },
        "stability": {
            "active_transaction_before": True,
            "active_transaction_after": True,
            "transaction_receipt_unchanged": True,
            "manifest_receipt_unchanged": True,
            "sandbox_binding_unchanged": True,
            "host_receipt_unchanged": True,
            "policy_receipt_unchanged": True,
            "sandbox_healthy_after": True,
            "guard_trigger_absent": True,
        },
        "mount_contract": {
            **dict(probe_result["mounts"]),
            "mount_plan_sha256": before.binding.mount_plan_sha256,
            "mount_contract_sha256": before.binding.mount_contract_sha256,
        },
        "immutable_write_denials": dict(probe_result["immutable_write_denials"]),
        "sensitive_read_denials": dict(probe_result["sensitive_read_denials"]),
        "allowed_writes": dict(probe_result["allowed_writes"]),
        "cleanup": {
            "sentinel_cleanup_attempted": True,
            "sentinel_cleanup_succeeded": True,
            "residual_host_sentinel_count": 0,
            "sandbox_remained_running": True,
        },
        "provenance": {
            "hermes_commit": HERMES_COMMIT,
            "image_sha256": before.binding.image_sha256,
            "policy_sha256": before.binding.policy_sha256,
            "mount_contract_sha256": before.binding.mount_contract_sha256,
            "runtime_config_sha256": before.binding.runtime_config_sha256,
            "filesystem_probe_sha256": str(probe_result["filesystem_probe_sha256"]),
            "probe_module_sha256": _sha256(_stable_file(root / PROBE_MODULE_RELATIVE)),
            "lifecycle_sha256": _sha256(_stable_file(root / LIFECYCLE_RELATIVE)),
            "transaction_module_sha256": _sha256(_stable_file(root / TRANSACTION_RELATIVE)),
            "mount_contract_module_sha256": _sha256(_stable_file(root / MOUNT_CONTRACT_RELATIVE)),
            "runner_sha256": _sha256(_stable_file(root / RUNNER_RELATIVE)),
            "evidence_schema_sha256": _sha256(schema_bytes),
            "raw_receipt_sha256": raw_receipt_sha256,
        },
        "not_claimed": list(NOT_CLAIMED),
        "sanitization": {
            "contains_api_keys": False,
            "contains_headers": False,
            "contains_prompt_or_input": False,
            "contains_raw_output": False,
            "contains_local_paths": False,
            "contains_runtime_identifiers": False,
            "exporter_ready": True,
        },
    }
    validate_evidence(evidence, schema_bytes=schema_bytes)
    return evidence


def validate_evidence(payload: Mapping[str, Any], *, schema_bytes: bytes | None = None) -> None:
    content = schema_bytes or _stable_file(REPO_ROOT / SCHEMA_RELATIVE)
    try:
        schema = json.loads(content)
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(dict(payload))
    except Exception as exc:
        raise FormalFilesystemEvidenceError("formal_filesystem_schema_validation_failed") from exc
    transaction_value = payload.get("transaction")
    provenance = payload.get("provenance")
    if (
        not isinstance(transaction_value, dict)
        or transaction_value.get("before_receipt_sha256") != transaction_value.get("after_receipt_sha256")
        or not isinstance(provenance, dict)
        or provenance.get("policy_sha256") != transaction_value.get("policy_sha256")
        or not isinstance(payload.get("mount_contract"), dict)
        or provenance.get("mount_contract_sha256") != payload["mount_contract"].get("mount_contract_sha256")
    ):
        raise FormalFilesystemEvidenceError("formal_filesystem_binding_invalid")


def _markdown() -> bytes:
    return (
        "# Formal OpenShell Filesystem Boundary Evidence\n\n"
        "- Decision: `GO`\n"
        "- Scope: `formal_running_filesystem_boundary`\n"
        "- Immutable source, code, configuration, Prompt and workflow writes: denied\n"
        "- Analysis, runtime state, session, memory and temporary writes: allowed\n"
        "- Formal transaction, manifest, sandbox, policy and host identity: unchanged\n"
        "- Probe sentinels: removed with zero host residuals\n"
        "- Business inference, API output, provider routes, rollback and destructive thresholds: not claimed\n\n"
        "Only stable outcomes and SHA-256 projections are published. Runtime identifiers, paths, content and credentials are excluded.\n"
    ).encode("ascii")


def _stage(path: Path, content: bytes) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        return temporary
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


def _publish_exclusive(outputs: Sequence[tuple[Path, bytes]]) -> None:
    for path, _ in outputs:
        if path.exists() or path.is_symlink():
            raise FormalFilesystemEvidenceError("evidence_output_exists")
    staged: list[tuple[Path, Path]] = []
    installed: list[Path] = []
    try:
        for path, content in outputs:
            staged.append((path, _stage(path, content)))
        for path, temporary in staged:
            try:
                os.link(temporary, path, follow_symlinks=False)
            except FileExistsError as exc:
                raise FormalFilesystemEvidenceError("evidence_output_exists") from exc
            installed.append(path)
            temporary.unlink()
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        for path in installed:
            info = path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise FormalFilesystemEvidenceError("evidence_output_invalid")
    except Exception:
        for path in installed:
            path.unlink(missing_ok=True)
        raise
    finally:
        for _, temporary in staged:
            temporary.unlink(missing_ok=True)


def run_and_publish(
    *,
    project_root: Path,
    run_id: str,
    artifact_json: Path,
    artifact_markdown: Path,
    timeout: int,
) -> tuple[dict[str, Any], Path]:
    root = project_root.expanduser().resolve(strict=True)
    if root != REPO_ROOT:
        raise FormalFilesystemEvidenceError("project_root_invalid")
    if not 5 <= timeout <= 60:
        raise FormalFilesystemEvidenceError("timeout_invalid")
    json_path, markdown_path = _artifact_paths(root, artifact_json, artifact_markdown)
    with _runner_lock(root):
        before = capture_active_binding(project_root=root, run_id=run_id)
        probe_result = sandbox_probe.run_filesystem_boundary_probe(before.context, timeout=timeout)
        after = capture_active_binding(project_root=root, run_id=run_id)
        generated_at = _utc_now()
        raw = _raw_receipt(
            generated_at=generated_at,
            before=before,
            after=after,
            probe_result=probe_result,
            artifact_json=json_path,
            artifact_markdown=markdown_path,
            root=root,
        )
        raw_content = json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True).encode("ascii") + b"\n"
        evidence = build_evidence(
            project_root=root,
            generated_at=generated_at,
            before=before,
            after=after,
            probe_result=probe_result,
            raw_receipt_sha256=_sha256(raw_content),
        )
        json_content = json.dumps(evidence, ensure_ascii=True, indent=2, sort_keys=True).encode("ascii") + b"\n"
        markdown_content = _markdown()
        findings = check_sanitized_artifacts.scan_content(json_path, json_content)
        findings.extend(check_sanitized_artifacts.scan_content(markdown_path, markdown_content))
        if findings:
            raise FormalFilesystemEvidenceError("formal_filesystem_sanitization_failed")
        raw_parent = _ensure_raw_parent(root)
        raw_path = raw_parent / f"{run_id}.raw.json"
        _publish_exclusive(
            (
                (raw_path, raw_content),
                (json_path, json_content),
                (markdown_path, markdown_content),
            )
        )
        if check_sanitized_artifacts.scan_paths([json_path, markdown_path]):
            for path in (raw_path, json_path, markdown_path):
                path.unlink(missing_ok=True)
            raise FormalFilesystemEvidenceError("formal_filesystem_sanitization_failed")
        return evidence, raw_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--artifact-json", type=Path, required=True)
    parser.add_argument("--artifact-markdown", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=sandbox_probe.DEFAULT_EXEC_TIMEOUT_SECONDS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        evidence, raw_path = run_and_publish(
            project_root=args.project_root,
            run_id=args.run_id,
            artifact_json=args.artifact_json,
            artifact_markdown=args.artifact_markdown,
            timeout=args.timeout,
        )
    except (FormalFilesystemEvidenceError, LifecycleError, sandbox_probe.ProbeError, OSError, ValueError) as exc:
        code = (
            exc.code
            if isinstance(exc, (FormalFilesystemEvidenceError, LifecycleError, sandbox_probe.ProbeError))
            else "formal_filesystem_failed"
        )
        print(json.dumps({"ok": False, "decision": "NO_GO", "error_code": code}, sort_keys=True))
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "decision": evidence["decision"],
                "schema_version": evidence["schema_version"],
                "raw_receipt_sha256": evidence["provenance"]["raw_receipt_sha256"],
                "raw_receipt_mode": f"{stat.S_IMODE(raw_path.stat().st_mode):04o}",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
