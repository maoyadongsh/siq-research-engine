#!/usr/bin/env python3
"""Execute and export one receipt-bound formal OpenShell rollback to host."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
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
    run_formal_filesystem_boundary as formal_filesystem,
    siq_analysis_transaction as transaction,
)
from scripts.openshell.siq_analysis_lifecycle import (  # noqa: E402
    BROKER_IDENTITY_SECRET_FILES,
    FORWARD_HOST,
    FORWARD_PORT,
    HERMES_COMMIT,
    HOST_HERMES_PORT,
    PROFILE,
    LifecycleAdapter,
    LifecycleError,
    _host_receipt_sha256,
    _sha256_file,
)

SCHEMA_VERSION = "siq.openshell.formal-host-rollback-evidence.v2"
RAW_SCHEMA_VERSION = "siq.openshell.formal-host-rollback-raw-receipt.v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_RELATIVE = Path("infra/openshell/schemas/formal-host-rollback-evidence.schema.json")
RUNNER_RELATIVE = Path("scripts/openshell/run_formal_host_rollback.py")
LIFECYCLE_RELATIVE = Path("scripts/openshell/siq_analysis_lifecycle.py")
TRANSACTION_RELATIVE = Path("scripts/openshell/siq_analysis_transaction.py")
MOUNT_CONTRACT_RELATIVE = Path("scripts/openshell/formal_runtime_contract.py")
ROLLBACK_WRAPPER_RELATIVE = Path("scripts/openshell/rollback_to_host.sh")
RAW_ROOT_RELATIVE = Path("var/openshell/proofs/formal-host-rollback")
LOCK_RELATIVE = Path("var/openshell/locks/formal-host-rollback.lock")
ARTIFACT_ROOT_RELATIVE = Path("artifacts/openshell/v0.6")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
RUN_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,47}\Z")
MAX_RECEIPT_BYTES = 8 * 1024 * 1024


class FormalHostRollbackError(RuntimeError):
    """Stable, content-free failure for formal rollback evidence."""

    def __init__(self, code: str) -> None:
        rendered = code if re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,95}", code) else "formal_host_rollback_failed"
        self.code = rendered
        super().__init__(rendered)


@dataclass(frozen=True)
class TerminalCapture:
    transaction_receipt_sha256: str
    transaction_generation: int
    resource_receipts_sha256: str
    manifest_sha256: str
    host_receipt_sha256: str
    run_id_sha256: str
    sandbox_id_sha256: str
    container_id_sha256: str
    image_sha256: str
    policy_sha256: str
    raw_mount_plan_sha256: str
    mount_contract_sha256: str


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return formal_runtime_contract.canonical_sha256(value)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _private_directory(path: Path, *, create: bool) -> None:
    if create:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
    try:
        info = path.lstat()
    except OSError as exc:
        raise FormalHostRollbackError("rollback_evidence_directory_invalid") from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise FormalHostRollbackError("rollback_evidence_directory_invalid")


def _stable_file(path: Path, *, private: bool = False, max_bytes: int = MAX_RECEIPT_BYTES) -> bytes:
    try:
        content = formal_runtime_contract.stable_regular_file(path, max_bytes=max_bytes)
        info = path.lstat()
    except (formal_runtime_contract.FormalRuntimeContractError, OSError) as exc:
        raise FormalHostRollbackError("rollback_evidence_source_invalid") from exc
    if private and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600):
        raise FormalHostRollbackError("rollback_evidence_source_invalid")
    return content


def _raw_path(root: Path, run_id: str) -> Path:
    if not RUN_ID_RE.fullmatch(run_id):
        raise FormalHostRollbackError("run_id_invalid")
    parent = root / RAW_ROOT_RELATIVE
    _private_directory(parent.parent, create=False)
    _private_directory(parent, create=True)
    return parent / f"{run_id}.raw.json"


def _artifact_path(root: Path, value: Path, *, suffix: str) -> Path:
    relative = PurePosixPath(value.as_posix())
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise FormalHostRollbackError("rollback_artifact_path_invalid")
    candidate = root.joinpath(*relative.parts)
    if candidate.parent != root / ARTIFACT_ROOT_RELATIVE or not candidate.name.endswith(suffix):
        raise FormalHostRollbackError("rollback_artifact_path_invalid")
    _private_directory(candidate.parent, create=False)
    if candidate.exists() or candidate.is_symlink():
        raise FormalHostRollbackError("rollback_artifact_output_exists")
    return candidate


def _artifact_paths(root: Path, json_value: Path, markdown_value: Path) -> tuple[Path, Path]:
    json_path = _artifact_path(root, json_value, suffix=".sanitized.json")
    markdown_path = _artifact_path(root, markdown_value, suffix=".sanitized.md")
    if json_path.name.removesuffix(".sanitized.json") != markdown_path.name.removesuffix(".sanitized.md"):
        raise FormalHostRollbackError("rollback_artifact_pair_invalid")
    return json_path, markdown_path


@contextmanager
def _runner_lock(root: Path) -> Iterator[None]:
    path = root / LOCK_RELATIVE
    _private_directory(path.parent, create=False)
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
            raise FormalHostRollbackError("rollback_evidence_lock_invalid")
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FormalHostRollbackError("rollback_evidence_runner_busy") from exc
        yield
    except OSError as exc:
        raise FormalHostRollbackError("rollback_evidence_lock_invalid") from exc
    finally:
        if descriptor >= 0:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _resource_projection(record: Mapping[str, Any]) -> dict[str, Any]:
    resources = record.get("resources")
    if not isinstance(resources, dict) or set(resources) != set(transaction.FORMAL_RESOURCES):
        raise FormalHostRollbackError("rollback_transaction_invalid")
    return {
        name: {
            "kind": item.get("kind"),
            "disposition": item.get("disposition"),
            "state": item.get("state"),
            "generation": item.get("generation"),
            "intent_sha256": item.get("intent_sha256"),
            "receipt_sha256": item.get("receipt_sha256"),
        }
        for name, item in sorted(resources.items())
        if isinstance(item, dict)
    }


def _terminal_capture(root: Path, run_id: str, *, adapter: LifecycleAdapter | None = None) -> TerminalCapture:
    lifecycle = adapter or LifecycleAdapter(project_root=root)
    try:
        record = transaction.load(root, f"tx-{run_id}")
        spec, manifest = lifecycle._load_manifest(run_id)
        resources = record["resources"]
        if (
            record.get("phase") != "stopped"
            or record.get("terminal_action") != "rollback_to_host"
            or record.get("error_code") != ""
            or manifest.get("phase") != "stopped"
            or manifest.get("error_code") != ""
            or resources["run_dir"].get("state") != "present"
            or any(resources[name].get("state") != "removed" for name in ("guard", "forward", "sandbox", "secrets"))
        ):
            raise FormalHostRollbackError("rollback_terminal_transaction_invalid")
        lifecycle._verify_transaction_receipts(record, spec, manifest)
        if (root / transaction.ACTIVE_RELATIVE).exists() or (root / transaction.ACTIVE_RELATIVE).is_symlink():
            raise FormalHostRollbackError("rollback_active_pointer_present")
        if [item for item in lifecycle._sandbox_inventory() if item.get("name") == spec.sandbox_name]:
            raise FormalHostRollbackError("rollback_sandbox_present")
        if lifecycle._docker_container_ids(spec.sandbox_name):
            raise FormalHostRollbackError("rollback_container_present")
        if not lifecycle.backend.port_listener_absent(FORWARD_HOST, FORWARD_PORT):
            raise FormalHostRollbackError("rollback_forward_port_present")
        for name in ("api.key", "run.nonce", *BROKER_IDENTITY_SECRET_FILES):
            path = spec.run_dir / name
            if path.exists() or path.is_symlink():
                raise FormalHostRollbackError("rollback_ephemeral_identity_present")
        for resource in ("guard", "forward"):
            process = lifecycle._read_process(spec, f"{resource}.process.json", resource)
            if lifecycle._process_receipt_sha(spec, resource, process) != resources[resource]["receipt_sha256"]:
                raise FormalHostRollbackError("rollback_process_receipt_mismatch")
            if lifecycle.backend.process_snapshot(process.pid, resource) is not None:
                raise FormalHostRollbackError("rollback_process_present")
        if lifecycle._sandbox_receipt_sha(spec, manifest) != resources["sandbox"]["receipt_sha256"]:
            raise FormalHostRollbackError("rollback_sandbox_receipt_mismatch")
        baseline = lifecycle._read_host_baseline(spec)
        current = lifecycle._stable_host_receipt(after_stop=True)
        if current != baseline:
            raise FormalHostRollbackError("rollback_host_identity_changed")
        mount = formal_runtime_contract.normalized_mount_contract(
            project_root=root,
            mount_plan=root / str(manifest["mount_plan"]),
            analysis_root=spec.analysis_root,
            runtime_snapshot=root / str(manifest["runtime_snapshot"]),
        )
        if mount["raw_mount_plan_sha256"] != manifest.get("mount_plan_sha256"):
            raise FormalHostRollbackError("rollback_mount_binding_invalid")
        image_id = str(manifest.get("image_id") or "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise FormalHostRollbackError("rollback_image_binding_invalid")
        return TerminalCapture(
            transaction_receipt_sha256=_canonical_sha256(record),
            transaction_generation=int(record["generation"]),
            resource_receipts_sha256=_canonical_sha256(_resource_projection(record)),
            manifest_sha256=_sha256_file(spec.run_dir / "run.json"),
            host_receipt_sha256=_host_receipt_sha256(baseline),
            run_id_sha256=_sha256(run_id.encode("ascii")),
            sandbox_id_sha256=_sha256(str(manifest["sandbox_id"]).encode("ascii")),
            container_id_sha256=_sha256(str(manifest["container_id"]).encode("ascii")),
            image_sha256=image_id.removeprefix("sha256:"),
            policy_sha256=str(manifest["policy_sha256"]),
            raw_mount_plan_sha256=str(mount["raw_mount_plan_sha256"]),
            mount_contract_sha256=str(mount["mount_contract_sha256"]),
        )
    except FormalHostRollbackError:
        raise
    except (LifecycleError, transaction.TransactionError, formal_runtime_contract.FormalRuntimeContractError, OSError) as exc:
        raise FormalHostRollbackError("rollback_terminal_transaction_invalid") from exc


def _validate_lifecycle_result(value: Any, *, run_id: str, host_receipt_sha256: str) -> dict[str, Any]:
    required = {
        "ok",
        "profile",
        "run_id",
        "status",
        "runtime",
        "host_runs_url",
        "host_runtime_unchanged",
        "host_receipt_sha256",
        "publisher",
    }
    publisher = value.get("publisher") if isinstance(value, dict) else None
    publisher_valid = (
        isinstance(publisher, dict)
        and (
            publisher == {"status": "published"}
            or publisher
            == {
                "status": "published",
                "audit": "deferred",
                "error_code": "publisher_audit_deferred",
            }
        )
    )
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value.get("ok") is not True
        or value.get("profile") != PROFILE
        or value.get("run_id") != run_id
        or value.get("status") != "stopped"
        or value.get("runtime") != "host"
        or value.get("host_runs_url") != f"http://{FORWARD_HOST}:{HOST_HERMES_PORT}/v1/runs"
        or value.get("host_runtime_unchanged") is not True
        or value.get("host_receipt_sha256") != host_receipt_sha256
        or not publisher_valid
    ):
        raise FormalHostRollbackError("rollback_lifecycle_result_invalid")
    return dict(value)


def capture_rollback(*, project_root: Path, run_id: str, timeout: int = 180) -> Path:
    root = project_root.expanduser().resolve(strict=True)
    if root != REPO_ROOT or not 30 <= timeout <= 600:
        raise FormalHostRollbackError("rollback_capture_configuration_invalid")
    raw_path = _raw_path(root, run_id)
    if raw_path.exists() or raw_path.is_symlink():
        raise FormalHostRollbackError("rollback_raw_receipt_exists")
    wrapper = root / ROLLBACK_WRAPPER_RELATIVE
    if not wrapper.is_file() or wrapper.is_symlink() or not os.access(wrapper, os.X_OK):
        raise FormalHostRollbackError("rollback_wrapper_invalid")

    with _runner_lock(root):
        before = formal_filesystem.capture_active_binding(project_root=root, run_id=run_id)
        mounts = sandbox_probe._docker_inspect_mounts(before.context, timeout=min(timeout, 60))
        live_mount_counts = formal_runtime_contract.validate_runtime_mounts(
            context=before.context,
            mounts=mounts,
            validator=sandbox_probe.validate_container_mounts,
        )
        adapter = LifecycleAdapter(project_root=root)
        baseline = adapter._read_host_baseline(adapter._load_manifest(run_id)[0])
        current = adapter._stable_host_receipt()
        if current != baseline or before.binding.host_receipt_sha256 != _host_receipt_sha256(baseline):
            raise FormalHostRollbackError("rollback_host_identity_changed_before_action")
        result = subprocess.run(
            [str(wrapper), "--profile", PROFILE, "--run-id", run_id],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0 or result.stderr.strip():
            raise FormalHostRollbackError("rollback_lifecycle_action_failed")
        try:
            lifecycle_result = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise FormalHostRollbackError("rollback_lifecycle_result_invalid") from exc
        terminal = _terminal_capture(root, run_id)
        lifecycle_result = _validate_lifecycle_result(
            lifecycle_result,
            run_id=run_id,
            host_receipt_sha256=terminal.host_receipt_sha256,
        )
        if (
            before.binding.run_id_sha256 != terminal.run_id_sha256
            or before.binding.sandbox_id_sha256 != terminal.sandbox_id_sha256
            or before.binding.image_sha256 != terminal.image_sha256
            or before.binding.policy_sha256 != terminal.policy_sha256
            or before.binding.mount_plan_sha256 != terminal.raw_mount_plan_sha256
            or before.binding.mount_contract_sha256 != terminal.mount_contract_sha256
        ):
            raise FormalHostRollbackError("rollback_runtime_binding_changed")
        source_sha256 = {
            "lifecycle_sha256": _sha256(_stable_file(root / LIFECYCLE_RELATIVE)),
            "transaction_module_sha256": _sha256(_stable_file(root / TRANSACTION_RELATIVE)),
            "mount_contract_module_sha256": _sha256(_stable_file(root / MOUNT_CONTRACT_RELATIVE)),
            "runner_sha256": _sha256(_stable_file(root / RUNNER_RELATIVE)),
            "wrapper_sha256": _sha256(_stable_file(wrapper)),
        }
        raw = {
            "schema_version": RAW_SCHEMA_VERSION,
            "generated_at": _utc_now(),
            "profile": PROFILE,
            "scope": "formal_business_sandbox",
            "formal_business_run": True,
            "runtime_identifiers": {
                "transaction_id": before.transaction_id,
                "run_id": run_id,
                "sandbox_id": before.sandbox_id,
                "container_id": before.container_id,
                "analysis_relative_path": before.analysis_relative_path,
            },
            "before": asdict(before.binding),
            "live_mount_counts": live_mount_counts,
            "lifecycle_result": lifecycle_result,
            "terminal": asdict(terminal),
            "source_sha256": source_sha256,
            "credential_material_present": False,
        }
        content = json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True).encode("ascii") + b"\n"
        _publish_exclusive(((raw_path, content),))
        return raw_path


def _read_raw(root: Path, raw_path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        expected = raw_path.resolve(strict=True)
    except OSError as exc:
        raise FormalHostRollbackError("rollback_raw_receipt_invalid") from exc
    if expected.parent != root / RAW_ROOT_RELATIVE or expected.name != raw_path.name:
        raise FormalHostRollbackError("rollback_raw_receipt_invalid")
    content = _stable_file(expected, private=True)
    try:
        value = json.loads(content)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise FormalHostRollbackError("rollback_raw_receipt_invalid") from exc
    if not isinstance(value, dict) or value.get("schema_version") != RAW_SCHEMA_VERSION:
        raise FormalHostRollbackError("rollback_raw_receipt_invalid")
    identifiers = value.get("runtime_identifiers")
    before = value.get("before")
    terminal = value.get("terminal")
    sources = value.get("source_sha256")
    run_id = identifiers.get("run_id") if isinstance(identifiers, dict) else None
    if (
        set(value)
        != {
            "schema_version",
            "generated_at",
            "profile",
            "scope",
            "formal_business_run",
            "runtime_identifiers",
            "before",
            "live_mount_counts",
            "lifecycle_result",
            "terminal",
            "source_sha256",
            "credential_material_present",
        }
        or value.get("profile") != PROFILE
        or value.get("scope") != "formal_business_sandbox"
        or value.get("formal_business_run") is not True
        or value.get("credential_material_present") is not False
        or not isinstance(run_id, str)
        or not RUN_ID_RE.fullmatch(run_id)
        or expected != _raw_path(root, run_id)
        or not isinstance(before, dict)
        or set(before) != set(formal_filesystem.ActiveBinding.__dataclass_fields__)
        or not isinstance(terminal, dict)
        or set(terminal) != set(TerminalCapture.__dataclass_fields__)
        or not isinstance(sources, dict)
        or set(sources)
        != {
            "lifecycle_sha256",
            "transaction_module_sha256",
            "mount_contract_module_sha256",
            "runner_sha256",
            "wrapper_sha256",
        }
        or any(not SHA256_RE.fullmatch(str(item)) for item in before.values() if isinstance(item, str))
        or terminal.get("terminal_action", "rollback_to_host") != "rollback_to_host"
        or any(
            not SHA256_RE.fullmatch(str(item))
            for key, item in terminal.items()
            if isinstance(item, str) and key != "terminal_action"
        )
        or any(not SHA256_RE.fullmatch(str(item)) for item in sources.values())
    ):
        raise FormalHostRollbackError("rollback_raw_receipt_invalid")
    _validate_lifecycle_result(
        value.get("lifecycle_result"),
        run_id=run_id,
        host_receipt_sha256=str(terminal["host_receipt_sha256"]),
    )
    return value, content


def build_evidence(*, project_root: Path, raw: Mapping[str, Any], raw_receipt_sha256: str) -> dict[str, Any]:
    root = project_root.resolve(strict=True)
    identifiers = raw["runtime_identifiers"]
    run_id = str(identifiers["run_id"])
    before = raw["before"]
    recorded_terminal = raw["terminal"]
    lifecycle_result = _validate_lifecycle_result(
        raw.get("lifecycle_result"),
        run_id=run_id,
        host_receipt_sha256=str(recorded_terminal["host_receipt_sha256"]),
    )
    terminal = _terminal_capture(root, run_id)
    if asdict(terminal) != recorded_terminal:
        raise FormalHostRollbackError("rollback_terminal_receipt_changed")
    sources = raw["source_sha256"]
    current_sources = {
        "lifecycle_sha256": _sha256(_stable_file(root / LIFECYCLE_RELATIVE)),
        "transaction_module_sha256": _sha256(_stable_file(root / TRANSACTION_RELATIVE)),
        "mount_contract_module_sha256": _sha256(_stable_file(root / MOUNT_CONTRACT_RELATIVE)),
        "runner_sha256": _sha256(_stable_file(root / RUNNER_RELATIVE)),
        "wrapper_sha256": _sha256(_stable_file(root / ROLLBACK_WRAPPER_RELATIVE)),
    }
    if sources != current_sources:
        raise FormalHostRollbackError("rollback_producer_changed_after_capture")
    schema_bytes = _stable_file(root / SCHEMA_RELATIVE)
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": raw["generated_at"],
        "decision": "GO",
        "profile": PROFILE,
        "scope": "formal_business_sandbox",
        "formal_business_run": True,
        "action": "rollback_to_host",
        "status": "stopped",
        "runtime": "host",
        "host_runtime_unchanged": True,
        "cutover_performed": False,
        "transaction": {
            "contract": "formal_transaction_v2_terminal_receipt",
            "phase": "stopped",
            "terminal_action": "rollback_to_host",
            "generation": terminal.transaction_generation,
            "before_receipt_sha256": before["transaction_receipt_sha256"],
            "terminal_receipt_sha256": terminal.transaction_receipt_sha256,
            "resource_receipts_sha256": terminal.resource_receipts_sha256,
            "run_id_sha256": terminal.run_id_sha256,
            "sandbox_id_sha256": terminal.sandbox_id_sha256,
            "container_id_sha256": terminal.container_id_sha256,
        },
        "host_identity": {
            "contract": "exact_receipt_before_and_after",
            "baseline_receipt_sha256": terminal.host_receipt_sha256,
            "before_receipt_sha256": before["host_receipt_sha256"],
            "after_receipt_sha256": terminal.host_receipt_sha256,
        },
        "cleanup": {
            "sandbox_deleted": True,
            "forward_port_released": True,
            "active_state_removed": True,
            "ephemeral_identity_removed": True,
            "transaction_finalized": True,
            "publisher_index_published": True,
            "publisher_receipt_sha256": _canonical_sha256(lifecycle_result),
        },
        "provenance": {
            "hermes_commit": HERMES_COMMIT,
            "image_sha256": terminal.image_sha256,
            "policy_sha256": terminal.policy_sha256,
            "raw_mount_plan_sha256": terminal.raw_mount_plan_sha256,
            "mount_contract_sha256": terminal.mount_contract_sha256,
            **current_sources,
            "evidence_schema_sha256": _sha256(schema_bytes),
            "raw_receipt_sha256": raw_receipt_sha256,
        },
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
        raise FormalHostRollbackError("rollback_evidence_schema_invalid") from exc
    transaction_value = payload.get("transaction")
    identity = payload.get("host_identity")
    provenance = payload.get("provenance")
    if (
        not isinstance(transaction_value, dict)
        or transaction_value.get("before_receipt_sha256") == transaction_value.get("terminal_receipt_sha256")
        or not isinstance(identity, dict)
        or len(
            {
                identity.get("baseline_receipt_sha256"),
                identity.get("before_receipt_sha256"),
                identity.get("after_receipt_sha256"),
            }
        )
        != 1
        or not isinstance(provenance, dict)
        or provenance.get("policy_sha256") is None
        or provenance.get("raw_mount_plan_sha256") == provenance.get("mount_contract_sha256")
    ):
        raise FormalHostRollbackError("rollback_evidence_binding_invalid")


def _markdown() -> bytes:
    return (
        "# Formal OpenShell Host Rollback Evidence\n\n"
        "- Decision: `GO`\n"
        "- Scope: `formal_business_sandbox`\n"
        "- Action: `rollback_to_host`\n"
        "- Exact host receipt before and after: unchanged\n"
        "- Formal transaction: terminal with rollback action\n"
        "- Sandbox, forward listener, active pointer and ephemeral identity: removed\n"
        "- Traffic cutover: not performed\n\n"
        "Only stable outcomes and SHA-256 projections are published. Runtime identifiers, paths, content and credentials are excluded.\n"
    ).encode("ascii")


def _stage(path: Path, content: bytes) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
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
    if any(path.exists() or path.is_symlink() for path, _ in outputs):
        raise FormalHostRollbackError("rollback_evidence_output_exists")
    staged: list[tuple[Path, Path]] = []
    installed: list[Path] = []
    try:
        for path, content in outputs:
            staged.append((path, _stage(path, content)))
        for path, temporary in staged:
            try:
                os.link(temporary, path, follow_symlinks=False)
            except FileExistsError as exc:
                raise FormalHostRollbackError("rollback_evidence_output_exists") from exc
            installed.append(path)
            temporary.unlink()
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        for path in installed:
            info = path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise FormalHostRollbackError("rollback_evidence_output_invalid")
    except Exception:
        for path in installed:
            path.unlink(missing_ok=True)
        raise
    finally:
        for _, temporary in staged:
            temporary.unlink(missing_ok=True)


def publish_evidence(
    *,
    project_root: Path,
    raw_receipt: Path,
    artifact_json: Path,
    artifact_markdown: Path,
) -> dict[str, Any]:
    root = project_root.expanduser().resolve(strict=True)
    if root != REPO_ROOT:
        raise FormalHostRollbackError("project_root_invalid")
    json_path, markdown_path = _artifact_paths(root, artifact_json, artifact_markdown)
    with _runner_lock(root):
        raw, raw_content = _read_raw(root, raw_receipt if raw_receipt.is_absolute() else root / raw_receipt)
        evidence = build_evidence(project_root=root, raw=raw, raw_receipt_sha256=_sha256(raw_content))
        json_content = json.dumps(evidence, ensure_ascii=True, indent=2, sort_keys=True).encode("ascii") + b"\n"
        markdown_content = _markdown()
        findings = check_sanitized_artifacts.scan_content(json_path, json_content)
        findings.extend(check_sanitized_artifacts.scan_content(markdown_path, markdown_content))
        if findings:
            raise FormalHostRollbackError("rollback_evidence_sanitization_failed")
        _publish_exclusive(((json_path, json_content), (markdown_path, markdown_content)))
        if check_sanitized_artifacts.scan_paths([json_path, markdown_path]):
            json_path.unlink(missing_ok=True)
            markdown_path.unlink(missing_ok=True)
            raise FormalHostRollbackError("rollback_evidence_sanitization_failed")
        return evidence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture = subparsers.add_parser("capture")
    capture.add_argument("--run-id", required=True)
    capture.add_argument("--timeout", type=int, default=180)
    publish = subparsers.add_parser("publish")
    publish.add_argument("--raw-receipt", type=Path, required=True)
    publish.add_argument("--artifact-json", type=Path, required=True)
    publish.add_argument("--artifact-markdown", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "capture":
            raw_path = capture_rollback(project_root=args.project_root, run_id=args.run_id, timeout=args.timeout)
            result = {
                "ok": True,
                "decision": "captured",
                "raw_receipt_sha256": _sha256(_stable_file(raw_path, private=True)),
            }
        else:
            evidence = publish_evidence(
                project_root=args.project_root,
                raw_receipt=args.raw_receipt,
                artifact_json=args.artifact_json,
                artifact_markdown=args.artifact_markdown,
            )
            result = {"ok": True, "decision": evidence["decision"], "schema_version": evidence["schema_version"]}
    except (
        FormalHostRollbackError,
        formal_filesystem.FormalFilesystemEvidenceError,
        formal_runtime_contract.FormalRuntimeContractError,
        LifecycleError,
        sandbox_probe.ProbeError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
    ) as exc:
        code = getattr(exc, "code", "formal_host_rollback_failed")
        print(json.dumps({"ok": False, "decision": "NO_GO", "error_code": code}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
