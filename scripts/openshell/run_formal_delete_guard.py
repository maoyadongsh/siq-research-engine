#!/usr/bin/env python3
"""Run and export a four-transaction formal destructive-action guard suite."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
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
    destructive_action_guard as deletion_guard,
    formal_runtime_contract,
    probe_siq_analysis_sandbox as sandbox_probe,
    run_formal_filesystem_boundary as formal_filesystem,
    run_formal_host_rollback as host_rollback,
    siq_analysis_transaction as transaction,
)
from scripts.openshell.siq_analysis_lifecycle import (  # noqa: E402
    BROKER_IDENTITY_SECRET_FILES,
    FORWARD_HOST,
    FORWARD_PORT,
    GUARD_OUTCOME_NAME,
    HERMES_COMMIT,
    MAINTENANCE_LOCK_RELATIVE,
    PROFILE,
    LifecycleAdapter,
    LifecycleError,
    _host_receipt_sha256,
    _sha256_file,
)

SCHEMA_VERSION = "siq.openshell.formal-delete-guard-evidence.v2"
SUITE_SCHEMA_VERSION = "siq.openshell.formal-delete-guard-suite.v1"
CASE_SCHEMA_VERSION = "siq.openshell.formal-delete-guard-case-receipt.v1"
FINAL_RAW_SCHEMA_VERSION = "siq.openshell.formal-delete-guard-final-receipt.v1"
CLEANUP_STATE_SCHEMA_VERSION = "siq.openshell.formal-delete-guard-cleanup-state.v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_RELATIVE = Path("infra/openshell/schemas/formal-delete-guard-evidence.schema.json")
RUNNER_RELATIVE = Path("scripts/openshell/run_formal_delete_guard.py")
LIFECYCLE_RELATIVE = Path("scripts/openshell/siq_analysis_lifecycle.py")
TRANSACTION_RELATIVE = Path("scripts/openshell/siq_analysis_transaction.py")
GUARD_RELATIVE = Path("scripts/openshell/destructive_action_guard.py")
GUARD_WORKER_RELATIVE = Path("scripts/openshell/siq_analysis_guard_worker.py")
MOUNT_CONTRACT_RELATIVE = Path("scripts/openshell/formal_runtime_contract.py")
RAW_ROOT_RELATIVE = Path("var/openshell/proofs/formal-delete-guard")
LOCK_RELATIVE = Path("var/openshell/locks/formal-delete-guard.lock")
SNAPSHOT_ROOT_RELATIVE = Path("var/openshell/siq-analysis/deletion-snapshots")
ARTIFACT_ROOT_RELATIVE = Path("artifacts/openshell/v0.6")
MECHANISMS = ("shell_rm", "python_shutil", "node_fs")
NORMAL_MECHANISM = "normal_cleanup"
ALL_CASES = (*MECHANISMS, NORMAL_MECHANISM)
DELETE_FILE_COUNT = 501
RETAIN_FILE_COUNT = 500
NORMAL_FILE_COUNT = 3
SUITE_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,31}\Z")
RUN_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,47}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
MAX_JSON_BYTES = 16 * 1024 * 1024

SHELL_DELETE = 'exec /usr/bin/rm -f -- "$1"/delete-*.dat'
PYTHON_DELETE = "import shutil,sys; shutil.rmtree(sys.argv[1])"
NODE_DELETE = "const fs=require('fs'); fs.rmSync(process.argv[1],{recursive:true,force:false});"
NORMAL_DELETE = (
    "import json,os,pathlib,shutil,sys; p=pathlib.Path(sys.argv[1]); "
    "leaf=p.parent/'agent-created-leaf'; leaf.mkdir(); "
    "source=leaf/'source.txt'; source.write_text('created',encoding='ascii'); "
    "source.write_text('overwritten',encoding='ascii'); renamed=leaf/'renamed.txt'; os.replace(source,renamed); "
    "renamed.write_text(renamed.read_text(encoding='ascii')+':updated',encoding='ascii'); "
    "small=sorted(p.glob('delete-*.dat')); [f.unlink() for f in small]; "
    "nested=leaf/'nested'; nested.mkdir(); (nested/'child.txt').write_text('child',encoding='ascii'); "
    "shutil.rmtree(nested); renamed.unlink(); leaf.rmdir(); "
    "print(json.dumps({'ok':True,'deleted_file_count':len(small),'mkdir':True,'create':True,"
    "'write':True,'overwrite':True,'rename':True,'small_delete':True,'recursive_cleanup':True},sort_keys=True))"
)


class FormalDeleteGuardError(RuntimeError):
    """Stable, content-free failure for formal delete guard evidence."""

    def __init__(self, code: str) -> None:
        rendered = code if re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,95}", code) else "formal_delete_guard_failed"
        self.code = rendered
        super().__init__(rendered)


@dataclass(frozen=True)
class DeleteTerminalCapture:
    transaction_receipt_sha256: str
    transaction_generation: int
    terminal_action: str
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


def _private_directory(path: Path, *, create: bool, exclusive: bool = False) -> None:
    if create:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError as exc:
            if exclusive:
                raise FormalDeleteGuardError("delete_suite_conflict") from exc
    try:
        info = path.lstat()
    except OSError as exc:
        raise FormalDeleteGuardError("delete_suite_directory_invalid") from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise FormalDeleteGuardError("delete_suite_directory_invalid")


def _stable_file(path: Path, *, private: bool = False, max_bytes: int = MAX_JSON_BYTES) -> bytes:
    try:
        content = formal_runtime_contract.stable_regular_file(path, max_bytes=max_bytes)
        info = path.lstat()
    except (formal_runtime_contract.FormalRuntimeContractError, OSError) as exc:
        raise FormalDeleteGuardError("delete_evidence_source_invalid") from exc
    if private and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600):
        raise FormalDeleteGuardError("delete_evidence_source_invalid")
    return content


def _write_exclusive(path: Path, content: bytes) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise FormalDeleteGuardError("delete_evidence_write_failed")
            view = view[written:]
        os.fsync(descriptor)
    except FileExistsError as exc:
        raise FormalDeleteGuardError("delete_evidence_output_exists") from exc
    except OSError as exc:
        raise FormalDeleteGuardError("delete_evidence_write_failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _replace_private_json(path: Path, value: Mapping[str, Any]) -> None:
    content = json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True).encode("ascii") + b"\n"
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


@contextmanager
def _runner_lock(root: Path) -> Iterator[None]:
    path = root / LOCK_RELATIVE
    _private_directory(path.parent, create=False)
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
            raise FormalDeleteGuardError("delete_evidence_lock_invalid")
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FormalDeleteGuardError("delete_evidence_runner_busy") from exc
        yield
    except OSError as exc:
        raise FormalDeleteGuardError("delete_evidence_lock_invalid") from exc
    finally:
        if descriptor >= 0:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


@contextmanager
def _maintenance_lock(root: Path) -> Iterator[None]:
    """Hold the exact lifecycle maintenance lock around host-tree mutations."""

    path = root / MAINTENANCE_LOCK_RELATIVE
    _private_directory(path.parent, create=False)
    try:
        expected = path.lstat()
    except OSError as exc:
        raise FormalDeleteGuardError("delete_maintenance_lock_invalid") from exc
    if (
        not stat.S_ISREG(expected.st_mode)
        or expected.st_uid != os.geteuid()
        or stat.S_IMODE(expected.st_mode) & 0o077
        or expected.st_nlink != 1
    ):
        raise FormalDeleteGuardError("delete_maintenance_lock_invalid")

    inherited_raw = os.environ.get("SIQ_OPENSHELL_MAINTENANCE_FD", "")
    inherited = int(inherited_raw) if inherited_raw.isdigit() else -1
    if inherited >= 0:
        try:
            opened = os.fstat(inherited)
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino)
                or opened.st_uid != os.geteuid()
                or stat.S_IMODE(opened.st_mode) & 0o077
            ):
                raise FormalDeleteGuardError("delete_maintenance_lock_invalid")
            fcntl.flock(inherited, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FormalDeleteGuardError("delete_maintenance_lock_busy") from exc
        except OSError as exc:
            raise FormalDeleteGuardError("delete_maintenance_lock_invalid") from exc
        yield
        return

    descriptor = -1
    previous = os.environ.pop("SIQ_OPENSHELL_MAINTENANCE_FD", None)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino)
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) & 0o077
        ):
            raise FormalDeleteGuardError("delete_maintenance_lock_invalid")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FormalDeleteGuardError("delete_maintenance_lock_busy") from exc
        os.environ["SIQ_OPENSHELL_MAINTENANCE_FD"] = str(descriptor)
        yield
    except FormalDeleteGuardError:
        raise
    except OSError as exc:
        raise FormalDeleteGuardError("delete_maintenance_lock_invalid") from exc
    finally:
        os.environ.pop("SIQ_OPENSHELL_MAINTENANCE_FD", None)
        if previous is not None:
            os.environ["SIQ_OPENSHELL_MAINTENANCE_FD"] = previous
        if descriptor >= 0:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _suite_dir(root: Path, suite_id: str, *, create: bool = False) -> Path:
    if not SUITE_ID_RE.fullmatch(suite_id):
        raise FormalDeleteGuardError("delete_suite_id_invalid")
    parent = root / RAW_ROOT_RELATIVE
    _private_directory(parent.parent, create=False)
    _private_directory(parent, create=True)
    path = parent / suite_id
    if create:
        _private_directory(path, create=True, exclusive=True)
    else:
        _private_directory(path, create=False)
    return path


def _fixture_name(suite_id: str) -> str:
    return f".siq-openshell-delete-proof-{suite_id}"


def _fixture_content(mechanism: str, kind: str, index: int) -> bytes:
    return f"siq-openshell-delete-proof-v1:{mechanism}:{kind}:{index:04d}\n".encode("ascii")


def _fixture_relative_file(fixture_name: str, mechanism: str, kind: str, index: int) -> str:
    return f"{fixture_name}/{mechanism}/{kind}/delete-{index:04d}.dat" if kind == "delete" else (
        f"{fixture_name}/{mechanism}/{kind}/retain-{index:04d}.dat"
    )


def _hash_regular(path: Path) -> tuple[int, str, int]:
    try:
        byte_count, digest = deletion_guard._sha256_regular_file(path)
        info = path.lstat()
    except (deletion_guard.DestructiveActionGuardError, OSError) as exc:
        raise FormalDeleteGuardError("analysis_tree_file_invalid") from exc
    return byte_count, digest, stat.S_IMODE(info.st_mode)


def _tree_sha256(root: Path, *, excluded_name: str | None = None) -> str:
    try:
        root_info = root.lstat()
    except OSError as exc:
        raise FormalDeleteGuardError("analysis_tree_invalid") from exc
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise FormalDeleteGuardError("analysis_tree_invalid")
    records: list[tuple[str, str, int, int, str]] = []
    stack = [root]
    while stack:
        current = stack.pop()
        for child in sorted(current.iterdir(), key=lambda item: item.name, reverse=True):
            if child.parent == root and excluded_name is not None and child.name == excluded_name:
                continue
            relative = child.relative_to(root).as_posix()
            info = child.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise FormalDeleteGuardError("analysis_tree_symlink_forbidden")
            if stat.S_ISDIR(info.st_mode):
                records.append((relative, "directory", 0, stat.S_IMODE(info.st_mode), ""))
                stack.append(child)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                size, digest, mode = _hash_regular(child)
                records.append((relative, "file", size, mode, digest))
            else:
                raise FormalDeleteGuardError("analysis_tree_special_entry_forbidden")
    return _canonical_sha256(records)


def _fixture_expected_files(fixture_name: str, mechanism: str) -> dict[str, tuple[bytes, int]]:
    result: dict[str, tuple[bytes, int]] = {}
    delete_count = NORMAL_FILE_COUNT if mechanism == NORMAL_MECHANISM else DELETE_FILE_COUNT
    for index in range(delete_count):
        result[_fixture_relative_file(fixture_name, mechanism, "delete", index)] = (
            _fixture_content(mechanism, "delete", index),
            0o600,
        )
    if mechanism != NORMAL_MECHANISM:
        for index in range(RETAIN_FILE_COUNT):
            result[_fixture_relative_file(fixture_name, mechanism, "retain", index)] = (
                _fixture_content(mechanism, "retain", index),
                0o600,
            )
    return result


def _verify_fixture(
    analysis_root: Path,
    *,
    fixture_name: str,
    normal_deleted: bool,
) -> dict[str, Any]:
    fixture = analysis_root / fixture_name
    _private_directory(fixture, create=False)
    expected_files: dict[str, tuple[bytes, int]] = {}
    expected_directories = {fixture_name}
    for mechanism in ALL_CASES:
        expected_directories.add(f"{fixture_name}/{mechanism}")
        expected_directories.add(f"{fixture_name}/{mechanism}/delete")
        if mechanism != NORMAL_MECHANISM:
            expected_directories.add(f"{fixture_name}/{mechanism}/retain")
        for relative, value in _fixture_expected_files(fixture_name, mechanism).items():
            if normal_deleted and mechanism == NORMAL_MECHANISM:
                continue
            expected_files[relative] = value
    observed_files: set[str] = set()
    observed_directories: set[str] = set()
    stack = [fixture]
    while stack:
        current = stack.pop()
        observed_directories.add(current.relative_to(analysis_root).as_posix())
        for child in current.iterdir():
            info = child.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise FormalDeleteGuardError("delete_fixture_invalid")
            if stat.S_ISDIR(info.st_mode):
                if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
                    raise FormalDeleteGuardError("delete_fixture_invalid")
                stack.append(child)
                continue
            relative = child.relative_to(analysis_root).as_posix()
            expected = expected_files.get(relative)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or expected is None:
                raise FormalDeleteGuardError("delete_fixture_invalid")
            content = _stable_file(child, max_bytes=1024)
            if content != expected[0] or stat.S_IMODE(info.st_mode) != expected[1]:
                raise FormalDeleteGuardError("delete_fixture_invalid")
            observed_files.add(relative)
    if observed_files != set(expected_files) or observed_directories != expected_directories:
        raise FormalDeleteGuardError("delete_fixture_invalid")
    return {
        "file_count": len(observed_files),
        "tree_sha256": _canonical_sha256(
            [
                (relative, len(expected_files[relative][0]), _sha256(expected_files[relative][0]))
                for relative in sorted(observed_files)
            ]
        ),
    }


def _materialize_fixture(analysis_root: Path, *, fixture_name: str) -> dict[str, Any]:
    fixture = analysis_root / fixture_name
    _private_directory(fixture, create=True, exclusive=True)
    try:
        for mechanism in ALL_CASES:
            mechanism_dir = fixture / mechanism
            delete_dir = mechanism_dir / "delete"
            _private_directory(mechanism_dir, create=True, exclusive=True)
            _private_directory(delete_dir, create=True, exclusive=True)
            delete_count = NORMAL_FILE_COUNT if mechanism == NORMAL_MECHANISM else DELETE_FILE_COUNT
            for index in range(delete_count):
                name = f"delete-{index:04d}.dat"
                _write_exclusive(delete_dir / name, _fixture_content(mechanism, "delete", index))
            if mechanism != NORMAL_MECHANISM:
                retain_dir = mechanism_dir / "retain"
                _private_directory(retain_dir, create=True, exclusive=True)
                for index in range(RETAIN_FILE_COUNT):
                    name = f"retain-{index:04d}.dat"
                    _write_exclusive(retain_dir / name, _fixture_content(mechanism, "retain", index))
        return _verify_fixture(analysis_root, fixture_name=fixture_name, normal_deleted=False)
    except Exception:
        shutil.rmtree(fixture, ignore_errors=True)
        raise


def _read_suite(root: Path, suite_id: str) -> tuple[Path, dict[str, Any]]:
    suite_dir = _suite_dir(root, suite_id)
    path = suite_dir / "suite.json"
    content = _stable_file(path, private=True)
    try:
        value = json.loads(content)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise FormalDeleteGuardError("delete_suite_receipt_invalid") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != SUITE_SCHEMA_VERSION
        or value.get("suite_id") != suite_id
        or value.get("profile") != PROFILE
        or set(value.get("cases", {})) != set(ALL_CASES)
    ):
        raise FormalDeleteGuardError("delete_suite_receipt_invalid")
    return path, value


def prepare_suite(
    *,
    project_root: Path,
    suite_id: str,
    market: str,
    company: str,
) -> dict[str, Any]:
    root = project_root.expanduser().resolve(strict=True)
    if root != REPO_ROOT:
        raise FormalDeleteGuardError("project_root_invalid")
    with _runner_lock(root), _maintenance_lock(root):
        try:
            discovery = transaction.recover_discovery(root)
        except transaction.TransactionError as exc:
            raise FormalDeleteGuardError("delete_suite_transaction_state_invalid") from exc
        if discovery.transaction is not None or (root / transaction.ACTIVE_RELATIVE).exists():
            raise FormalDeleteGuardError("delete_suite_requires_no_active_transaction")
        adapter = LifecycleAdapter(project_root=root)
        spec = adapter.spec(profile=PROFILE, market=market, company=company, run_id=f"proof-{suite_id}")
        fixture_name = _fixture_name(suite_id)
        outside_tree = _tree_sha256(spec.analysis_root, excluded_name=fixture_name)
        suite_dir = _suite_dir(root, suite_id, create=True)
        try:
            fixture = _materialize_fixture(spec.analysis_root, fixture_name=fixture_name)
            if _tree_sha256(spec.analysis_root, excluded_name=fixture_name) != outside_tree:
                raise FormalDeleteGuardError("analysis_tree_changed_during_prepare")
            value = {
                "schema_version": SUITE_SCHEMA_VERSION,
                "created_at": _utc_now(),
                "suite_id": suite_id,
                "profile": PROFILE,
                "market": market,
                "company": company,
                "analysis_relative_path": spec.analysis_relative_path,
                "fixture_name": fixture_name,
                "outside_analysis_tree_sha256": outside_tree,
                "fixture_initial_file_count": fixture["file_count"],
                "fixture_initial_tree_sha256": fixture["tree_sha256"],
                "cases": {mechanism: {"status": "prepared", "run_id": "", "raw_receipt_sha256": ""} for mechanism in ALL_CASES},
            }
            _write_exclusive(
                suite_dir / "suite.json",
                json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True).encode("ascii") + b"\n",
            )
            return {
                "ok": True,
                "suite_id": suite_id,
                "fixture_file_count": fixture["file_count"],
                "outside_analysis_tree_sha256": outside_tree,
            }
        except Exception:
            shutil.rmtree(spec.analysis_root / fixture_name, ignore_errors=True)
            shutil.rmtree(suite_dir, ignore_errors=True)
            raise


def _source_sha256(root: Path) -> dict[str, str]:
    return {
        "lifecycle_sha256": _sha256(_stable_file(root / LIFECYCLE_RELATIVE)),
        "transaction_module_sha256": _sha256(_stable_file(root / TRANSACTION_RELATIVE)),
        "destructive_guard_sha256": _sha256(_stable_file(root / GUARD_RELATIVE)),
        "guard_worker_sha256": _sha256(_stable_file(root / GUARD_WORKER_RELATIVE)),
        "mount_contract_module_sha256": _sha256(_stable_file(root / MOUNT_CONTRACT_RELATIVE)),
        "runner_sha256": _sha256(_stable_file(root / RUNNER_RELATIVE)),
    }


def _case_command(context: sandbox_probe.ProbeContext, suite: Mapping[str, Any], mechanism: str) -> list[str]:
    sandbox_fixture = sandbox_probe.SANDBOX_ROOT / str(suite["analysis_relative_path"]) / str(
        suite["fixture_name"]
    )
    mechanism_root = sandbox_fixture / mechanism
    if mechanism == "shell_rm":
        return ["/bin/sh", "-c", SHELL_DELETE, "delete-proof", (mechanism_root / "delete").as_posix()]
    if mechanism == "python_shutil":
        return [sandbox_probe.SANDBOX_PYTHON, "-c", PYTHON_DELETE, (mechanism_root / "delete").as_posix()]
    if mechanism == "node_fs":
        return ["/usr/local/bin/node", "-e", NODE_DELETE, (mechanism_root / "delete").as_posix()]
    if mechanism == NORMAL_MECHANISM:
        return [sandbox_probe.SANDBOX_PYTHON, "-c", NORMAL_DELETE, (mechanism_root / "delete").as_posix()]
    raise FormalDeleteGuardError("delete_mechanism_invalid")


def _execute_case(
    context: sandbox_probe.ProbeContext,
    suite: Mapping[str, Any],
    mechanism: str,
    *,
    timeout: int,
) -> dict[str, Any]:
    command = _case_command(context, suite, mechanism)
    run_cli = context.project_root / "scripts/openshell/run_cli.sh"
    invocation = [
        str(run_cli),
        "sandbox",
        "exec",
        "--name",
        context.sandbox_name,
        "--timeout",
        str(timeout),
        "--no-tty",
        "--",
        *command,
    ]
    try:
        result = subprocess.run(
            invocation,
            cwd=context.project_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout + 10,
            check=False,
        )
    except subprocess.TimeoutExpired:
        if mechanism == NORMAL_MECHANISM:
            raise FormalDeleteGuardError("normal_cleanup_exec_timeout") from None
        return {"returncode_class": "timeout_or_fenced", "command_sha256": _canonical_sha256(command[:-1])}
    if mechanism == NORMAL_MECHANISM:
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise FormalDeleteGuardError("normal_cleanup_exec_invalid") from exc
        expected = {
            "ok": True,
            "deleted_file_count": NORMAL_FILE_COUNT,
            "mkdir": True,
            "create": True,
            "write": True,
            "overwrite": True,
            "rename": True,
            "small_delete": True,
            "recursive_cleanup": True,
        }
        if (
            result.returncode != 0
            or result.stderr.strip()
            or payload != expected
        ):
            raise FormalDeleteGuardError("normal_cleanup_exec_invalid")
        return {"returncode_class": "success", "command_sha256": _canonical_sha256(command[:-1])}
    return {
        "returncode_class": "success_before_fence" if result.returncode == 0 else "fenced",
        "command_sha256": _canonical_sha256(command[:-1]),
    }


def _wait_terminal(root: Path, run_id: str, *, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    last_phase = ""
    while time.monotonic() < deadline:
        try:
            record = transaction.load(root, f"tx-{run_id}")
            last_phase = str(record.get("phase") or "")
            if last_phase in transaction.TERMINAL_PHASES:
                return
        except transaction.TransactionError:
            pass
        time.sleep(0.2)
    del last_phase
    raise FormalDeleteGuardError("delete_guard_terminal_timeout")


def _terminal_capture(
    root: Path,
    run_id: str,
    *,
    expected_action: str,
    adapter: LifecycleAdapter | None = None,
) -> DeleteTerminalCapture:
    lifecycle = adapter or LifecycleAdapter(project_root=root)
    try:
        record = transaction.load(root, f"tx-{run_id}")
        spec, manifest = lifecycle._load_manifest(run_id)
        resources = record["resources"]
        if (
            record.get("phase") != "stopped"
            or record.get("terminal_action") != expected_action
            or manifest.get("phase") != "stopped"
            or resources["run_dir"].get("state") != "present"
            or any(resources[name].get("state") != "removed" for name in ("guard", "forward", "sandbox", "secrets"))
        ):
            raise FormalDeleteGuardError("delete_terminal_transaction_invalid")
        lifecycle._verify_transaction_receipts(record, spec, manifest)
        if (root / transaction.ACTIVE_RELATIVE).exists() or (root / transaction.ACTIVE_RELATIVE).is_symlink():
            raise FormalDeleteGuardError("delete_active_pointer_present")
        if [item for item in lifecycle._sandbox_inventory() if item.get("name") == spec.sandbox_name]:
            raise FormalDeleteGuardError("delete_sandbox_present")
        if lifecycle._docker_container_ids(spec.sandbox_name):
            raise FormalDeleteGuardError("delete_container_present")
        if not lifecycle.backend.port_listener_absent(FORWARD_HOST, FORWARD_PORT):
            raise FormalDeleteGuardError("delete_forward_port_present")
        for name in ("api.key", "run.nonce", *BROKER_IDENTITY_SECRET_FILES):
            path = spec.run_dir / name
            if path.exists() or path.is_symlink():
                raise FormalDeleteGuardError("delete_ephemeral_identity_present")
        for resource in ("guard", "forward"):
            process = lifecycle._read_process(spec, f"{resource}.process.json", resource)
            if lifecycle._process_receipt_sha(spec, resource, process) != resources[resource]["receipt_sha256"]:
                raise FormalDeleteGuardError("delete_process_receipt_mismatch")
            if lifecycle.backend.process_snapshot(process.pid, resource) is not None:
                raise FormalDeleteGuardError("delete_process_present")
        baseline = lifecycle._read_host_baseline(spec)
        if lifecycle._stable_host_receipt(after_stop=True) != baseline:
            raise FormalDeleteGuardError("delete_host_identity_changed")
        mount = formal_runtime_contract.normalized_mount_contract(
            project_root=root,
            mount_plan=root / str(manifest["mount_plan"]),
            analysis_root=spec.analysis_root,
            runtime_snapshot=root / str(manifest["runtime_snapshot"]),
        )
        image_id = str(manifest.get("image_id") or "")
        if (
            mount["raw_mount_plan_sha256"] != manifest.get("mount_plan_sha256")
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)
        ):
            raise FormalDeleteGuardError("delete_runtime_binding_invalid")
        return DeleteTerminalCapture(
            transaction_receipt_sha256=_canonical_sha256(record),
            transaction_generation=int(record["generation"]),
            terminal_action=expected_action,
            resource_receipts_sha256=_canonical_sha256(host_rollback._resource_projection(record)),
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
    except FormalDeleteGuardError:
        raise
    except (LifecycleError, transaction.TransactionError, formal_runtime_contract.FormalRuntimeContractError, OSError) as exc:
        raise FormalDeleteGuardError("delete_terminal_transaction_invalid") from exc


def _load_snapshot(root: Path, run_id: str, analysis_root: Path) -> deletion_guard.DeletionSnapshot:
    try:
        return deletion_guard._load_deletion_snapshot(
            project_root=root,
            analysis_root=analysis_root,
            snapshot_path=root / SNAPSHOT_ROOT_RELATIVE / run_id,
        )
    except deletion_guard.DestructiveActionGuardError as exc:
        raise FormalDeleteGuardError("delete_snapshot_invalid") from exc


def _validate_snapshot_fixture(
    snapshot: deletion_guard.DeletionSnapshot,
    *,
    fixture_name: str,
    mechanism: str,
) -> None:
    expected = _fixture_expected_files(fixture_name, mechanism)
    if mechanism == NORMAL_MECHANISM:
        expected = _fixture_expected_files(fixture_name, NORMAL_MECHANISM)
    for relative, (content, mode) in expected.items():
        item = snapshot.files.get(relative)
        if (
            item is None
            or item.byte_count != len(content)
            or item.sha256 != _sha256(content)
            or item.mode != mode
        ):
            raise FormalDeleteGuardError("delete_snapshot_fixture_missing")
    if mechanism in MECHANISMS and len(snapshot.files) < DELETE_FILE_COUNT + RETAIN_FILE_COUNT:
        raise FormalDeleteGuardError("delete_snapshot_threshold_baseline_invalid")


def _guard_outcome(
    root: Path,
    run_id: str,
    *,
    fixture_name: str,
    mechanism: str,
) -> tuple[dict[str, Any], str]:
    path = root / "var/openshell/siq-analysis/runs" / run_id / GUARD_OUTCOME_NAME
    content = _stable_file(path, private=True)
    try:
        value = json.loads(content)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise FormalDeleteGuardError("delete_guard_outcome_invalid") from exc
    expected_paths = {
        _fixture_relative_file(fixture_name, mechanism, "delete", index)
        for index in range(DELETE_FILE_COUNT)
    }
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "siq.openshell.deletion_guard_event.v1"
        or value.get("status") != "triggered"
        or value.get("profile") != PROFILE
        or value.get("run_id") != run_id
        or value.get("reason_code") != "deletion_count_gt_500"
        or value.get("observed_deleted_file_count") != DELETE_FILE_COUNT
        or value.get("restored_file_count") != DELETE_FILE_COUNT
        or not isinstance(value.get("baseline_file_count"), int)
        or value["baseline_file_count"] < 1001
        or set(value.get("deleted_paths", [])) != expected_paths
    ):
        raise FormalDeleteGuardError("delete_guard_outcome_invalid")
    return value, _sha256(content)


def _case_path(suite_dir: Path, mechanism: str) -> Path:
    return suite_dir / f"{mechanism}.raw.json"


def _update_suite_case(path: Path, suite: dict[str, Any], mechanism: str, run_id: str, digest: str) -> None:
    cases = suite.get("cases")
    if not isinstance(cases, dict) or cases.get(mechanism) != {
        "status": "prepared",
        "run_id": "",
        "raw_receipt_sha256": "",
    }:
        raise FormalDeleteGuardError("delete_case_already_captured")
    cases[mechanism] = {"status": "captured", "run_id": run_id, "raw_receipt_sha256": digest}
    _replace_private_json(path, suite)


def capture_case(
    *,
    project_root: Path,
    suite_id: str,
    mechanism: str,
    run_id: str,
    timeout: int = 120,
) -> Path:
    root = project_root.expanduser().resolve(strict=True)
    if (
        root != REPO_ROOT
        or mechanism not in ALL_CASES
        or not RUN_ID_RE.fullmatch(run_id)
        or not 30 <= timeout <= 600
    ):
        raise FormalDeleteGuardError("delete_capture_configuration_invalid")
    with _runner_lock(root):
        suite_path, suite = _read_suite(root, suite_id)
        suite_dir = suite_path.parent
        raw_path = _case_path(suite_dir, mechanism)
        if raw_path.exists() or raw_path.is_symlink():
            raise FormalDeleteGuardError("delete_case_already_captured")
        adapter = LifecycleAdapter(project_root=root)
        spec = adapter.spec(
            profile=PROFILE,
            market=str(suite["market"]),
            company=str(suite["company"]),
            run_id=run_id,
        )
        if spec.analysis_relative_path != suite["analysis_relative_path"]:
            raise FormalDeleteGuardError("delete_suite_analysis_binding_changed")
        normal_already = suite["cases"][NORMAL_MECHANISM]["status"] == "captured"
        fixture_before = _verify_fixture(
            spec.analysis_root,
            fixture_name=str(suite["fixture_name"]),
            normal_deleted=normal_already,
        )
        if _tree_sha256(spec.analysis_root, excluded_name=str(suite["fixture_name"])) != suite[
            "outside_analysis_tree_sha256"
        ]:
            raise FormalDeleteGuardError("analysis_tree_changed_before_case")
        before = formal_filesystem.capture_active_binding(project_root=root, run_id=run_id)
        if before.analysis_relative_path != suite["analysis_relative_path"]:
            raise FormalDeleteGuardError("delete_formal_transaction_binding_invalid")
        mounts = sandbox_probe._docker_inspect_mounts(before.context, timeout=min(timeout, 60))
        live_mount_counts = formal_runtime_contract.validate_runtime_mounts(
            context=before.context,
            mounts=mounts,
            validator=sandbox_probe.validate_container_mounts,
        )
        snapshot = _load_snapshot(root, run_id, spec.analysis_root)
        _validate_snapshot_fixture(snapshot, fixture_name=str(suite["fixture_name"]), mechanism=mechanism)
        snapshot_manifest_sha256 = _sha256(
            _stable_file(snapshot.path / "snapshot-manifest.json", private=True, max_bytes=16 * 1024 * 1024)
        )
        command = _execute_case(before.context, suite, mechanism, timeout=min(timeout, 120))

        if mechanism == NORMAL_MECHANISM:
            after = formal_filesystem.capture_active_binding(project_root=root, run_id=run_id)
            if before.binding != after.binding:
                raise FormalDeleteGuardError("normal_cleanup_transaction_changed")
            if any(
                (spec.run_dir / name).exists() or (spec.run_dir / name).is_symlink()
                for name in ("guard.trigger.json", GUARD_OUTCOME_NAME, "guard.cleanup.pending.json")
            ):
                raise FormalDeleteGuardError("normal_cleanup_guard_triggered")
            fixture_after = _verify_fixture(
                spec.analysis_root,
                fixture_name=str(suite["fixture_name"]),
                normal_deleted=True,
            )
            terminal: dict[str, Any] | None = None
            guard_outcome_sha256 = ""
            observed_deleted = NORMAL_FILE_COUNT
            restored = 0
        else:
            _wait_terminal(root, run_id, timeout=timeout)
            terminal_capture = _terminal_capture(root, run_id, expected_action="stop")
            terminal = asdict(terminal_capture)
            outcome, guard_outcome_sha256 = _guard_outcome(
                root,
                run_id,
                fixture_name=str(suite["fixture_name"]),
                mechanism=mechanism,
            )
            observed_deleted = int(outcome["observed_deleted_file_count"])
            restored = int(outcome["restored_file_count"])
            fixture_after = _verify_fixture(
                spec.analysis_root,
                fixture_name=str(suite["fixture_name"]),
                normal_deleted=normal_already,
            )
        if _tree_sha256(spec.analysis_root, excluded_name=str(suite["fixture_name"])) != suite[
            "outside_analysis_tree_sha256"
        ]:
            raise FormalDeleteGuardError("analysis_tree_changed_after_case")
        sources = _source_sha256(root)
        raw = {
            "schema_version": CASE_SCHEMA_VERSION,
            "generated_at": _utc_now(),
            "suite_id": suite_id,
            "profile": PROFILE,
            "mechanism": mechanism,
            "formal_business_run": True,
            "runtime_identifiers": {
                "transaction_id": before.transaction_id,
                "run_id": run_id,
                "sandbox_id": before.sandbox_id,
                "container_id": before.container_id,
            },
            "before": asdict(before.binding),
            "terminal": terminal,
            "live_mount_counts": live_mount_counts,
            "command": command,
            "fixture_before_sha256": fixture_before["tree_sha256"],
            "fixture_after_sha256": fixture_after["tree_sha256"],
            "outside_analysis_tree_sha256": suite["outside_analysis_tree_sha256"],
            "snapshot_manifest_sha256": snapshot_manifest_sha256,
            "snapshot_tree_sha256": snapshot.tree_sha256,
            "guard_outcome_sha256": guard_outcome_sha256,
            "observed_deleted_file_count": observed_deleted,
            "restored_file_count": restored,
            "source_sha256": sources,
            "credential_material_present": False,
        }
        content = json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True).encode("ascii") + b"\n"
        _write_exclusive(raw_path, content)
        digest = _sha256(content)
        _update_suite_case(suite_path, suite, mechanism, run_id, digest)
        return raw_path


def _read_case(root: Path, suite: Mapping[str, Any], suite_dir: Path, mechanism: str) -> tuple[dict[str, Any], bytes]:
    case_state = suite["cases"][mechanism]
    if not isinstance(case_state, dict) or case_state.get("status") != "captured":
        raise FormalDeleteGuardError("delete_suite_incomplete")
    path = _case_path(suite_dir, mechanism)
    content = _stable_file(path, private=True)
    if _sha256(content) != case_state.get("raw_receipt_sha256"):
        raise FormalDeleteGuardError("delete_case_receipt_digest_mismatch")
    try:
        value = json.loads(content)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise FormalDeleteGuardError("delete_case_receipt_invalid") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != CASE_SCHEMA_VERSION
        or value.get("suite_id") != suite.get("suite_id")
        or value.get("mechanism") != mechanism
        or value.get("profile") != PROFILE
        or value.get("formal_business_run") is not True
        or value.get("credential_material_present") is not False
        or value.get("outside_analysis_tree_sha256") != suite.get("outside_analysis_tree_sha256")
        or value.get("source_sha256") != _source_sha256(root)
    ):
        raise FormalDeleteGuardError("delete_case_receipt_invalid")
    return value, content


def _revalidate_case(
    root: Path,
    suite: Mapping[str, Any],
    case: Mapping[str, Any],
    mechanism: str,
    analysis_root: Path,
) -> dict[str, Any]:
    identifiers = case.get("runtime_identifiers")
    before = case.get("before")
    if not isinstance(identifiers, dict) or not isinstance(before, dict):
        raise FormalDeleteGuardError("delete_case_receipt_invalid")
    run_id = str(identifiers.get("run_id") or "")
    if run_id != suite["cases"][mechanism]["run_id"] or not RUN_ID_RE.fullmatch(run_id):
        raise FormalDeleteGuardError("delete_case_run_binding_invalid")
    snapshot = _load_snapshot(root, run_id, analysis_root)
    _validate_snapshot_fixture(snapshot, fixture_name=str(suite["fixture_name"]), mechanism=mechanism)
    manifest_digest = _sha256(
        _stable_file(snapshot.path / "snapshot-manifest.json", private=True, max_bytes=16 * 1024 * 1024)
    )
    if (
        manifest_digest != case.get("snapshot_manifest_sha256")
        or snapshot.tree_sha256 != case.get("snapshot_tree_sha256")
    ):
        raise FormalDeleteGuardError("delete_snapshot_receipt_changed")
    if mechanism == NORMAL_MECHANISM:
        terminal = _terminal_capture(root, run_id, expected_action="rollback_to_host")
        if (
            terminal.run_id_sha256 != before.get("run_id_sha256")
            or terminal.sandbox_id_sha256 != before.get("sandbox_id_sha256")
            or terminal.container_id_sha256 != before.get("container_id_sha256")
            or terminal.host_receipt_sha256 != before.get("host_receipt_sha256")
            or terminal.image_sha256 != before.get("image_sha256")
            or terminal.policy_sha256 != before.get("policy_sha256")
            or terminal.raw_mount_plan_sha256 != before.get("mount_plan_sha256")
            or terminal.mount_contract_sha256 != before.get("mount_contract_sha256")
        ):
            raise FormalDeleteGuardError("normal_cleanup_terminal_binding_invalid")
        return {
            "mechanism": "current_task_file_cleanup",
            "deleted_file_count": NORMAL_FILE_COUNT,
            "guard_triggered": False,
            "allowed": True,
            "sandbox_remained_healthy": True,
            "cleanup_succeeded": True,
            "unexpected_residual_file_count": 0,
            "mkdir_allowed": True,
            "create_allowed": True,
            "write_allowed": True,
            "overwrite_allowed": True,
            "rename_allowed": True,
            "small_delete_allowed": True,
            "recursive_cleanup_allowed": True,
            "run_id_sha256": terminal.run_id_sha256,
            "sandbox_id_sha256": terminal.sandbox_id_sha256,
            "transaction_receipt_sha256": terminal.transaction_receipt_sha256,
            "raw_case_receipt_sha256": "",
        }
    terminal = _terminal_capture(root, run_id, expected_action="stop")
    if asdict(terminal) != case.get("terminal"):
        raise FormalDeleteGuardError("delete_terminal_receipt_changed")
    outcome, outcome_sha = _guard_outcome(
        root,
        run_id,
        fixture_name=str(suite["fixture_name"]),
        mechanism=mechanism,
    )
    if outcome_sha != case.get("guard_outcome_sha256"):
        raise FormalDeleteGuardError("delete_guard_outcome_changed")
    return {
        "mechanism": mechanism,
        "triggered": True,
        "reason_code": "deletion_count_gt_500",
        "sandbox_terminated": True,
        "snapshot_restored": True,
        "observed_deleted_file_count": int(outcome["observed_deleted_file_count"]),
        "restored_file_count": int(outcome["restored_file_count"]),
        "residual_missing_file_count": 0,
        "run_id_sha256": terminal.run_id_sha256,
        "sandbox_id_sha256": terminal.sandbox_id_sha256,
        "transaction_receipt_sha256": terminal.transaction_receipt_sha256,
        "guard_event_sha256": outcome_sha,
        "snapshot_manifest_sha256": manifest_digest,
        "snapshot_tree_sha256": snapshot.tree_sha256,
        "raw_case_receipt_sha256": "",
    }


def _verify_snapshot_tree_for_removal(snapshot: deletion_guard.DeletionSnapshot) -> None:
    expected_files = {f"files/{relative}" for relative in snapshot.files}
    expected_files.add("snapshot-manifest.json")
    expected_directories = {"files"}
    for relative in snapshot.files:
        for parent in PurePosixPath(relative).parents:
            if parent != PurePosixPath("."):
                expected_directories.add(f"files/{parent.as_posix()}")
    observed_files: set[str] = set()
    observed_directories: set[str] = set()
    for path in snapshot.path.rglob("*"):
        relative = path.relative_to(snapshot.path).as_posix()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise FormalDeleteGuardError("delete_snapshot_cleanup_unsafe")
        if stat.S_ISDIR(info.st_mode):
            observed_directories.add(relative)
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            observed_files.add(relative)
        else:
            raise FormalDeleteGuardError("delete_snapshot_cleanup_unsafe")
    if observed_files != expected_files or observed_directories != expected_directories:
        raise FormalDeleteGuardError("delete_snapshot_cleanup_unsafe")
    for relative, expected in snapshot.files.items():
        try:
            byte_count, digest = deletion_guard._sha256_regular_file(snapshot.path / "files" / relative)
        except deletion_guard.DestructiveActionGuardError as exc:
            raise FormalDeleteGuardError("delete_snapshot_cleanup_unsafe") from exc
        if byte_count != expected.byte_count or digest != expected.sha256:
            raise FormalDeleteGuardError("delete_snapshot_cleanup_unsafe")


def _cleanup_state_path(suite_dir: Path) -> Path:
    return suite_dir / "cleanup-state.json"


def _cleanup_receipt() -> dict[str, bool]:
    return {
        "sandbox_deleted": True,
        "forward_port_released": True,
        "active_state_removed": True,
        "ephemeral_identity_removed": True,
        "transaction_finalized": True,
        "snapshot_artifacts_removed": True,
        "fixture_removed": True,
        "outside_analysis_tree_unchanged": True,
    }


def _validate_cleanup_state(
    root: Path,
    suite: Mapping[str, Any],
    state: Mapping[str, Any],
) -> dict[str, Any]:
    fixture = state.get("fixture")
    snapshots = state.get("snapshots")
    projection = state.get("final_projection")
    phase = state.get("phase")
    if (
        set(state)
        != {
            "schema_version",
            "suite_id",
            "phase",
            "generated_at",
            "outside_analysis_tree_sha256",
            "fixture",
            "snapshots",
            "final_projection",
            "source_sha256",
            "final_raw_sha256",
        }
        or state.get("schema_version") != CLEANUP_STATE_SCHEMA_VERSION
        or state.get("suite_id") != suite.get("suite_id")
        or phase not in {"intent", "cleaning", "cleaned", "terminal"}
        or state.get("outside_analysis_tree_sha256") != suite.get("outside_analysis_tree_sha256")
        or state.get("source_sha256") != _source_sha256(root)
        or not isinstance(fixture, dict)
        or set(fixture) != {"name", "status", "file_count", "tree_sha256"}
        or fixture.get("name") != suite.get("fixture_name")
        or fixture.get("status") not in {"pending", "removed"}
        or isinstance(fixture.get("file_count"), bool)
        or not isinstance(fixture.get("file_count"), int)
        or fixture["file_count"] < 1
        or not SHA256_RE.fullmatch(str(fixture.get("tree_sha256") or ""))
        or not isinstance(snapshots, list)
        or len(snapshots) != len(ALL_CASES)
        or not isinstance(projection, dict)
        or set(projection)
        != {
            "generated_at",
            "suite_id",
            "profile",
            "cases",
            "normal_cleanup",
            "case_receipt_sha256",
            "run_set_sha256",
            "transaction_receipt_set_sha256",
            "image_sha256",
            "policy_sha256",
            "mount_contract_sha256",
            "outside_analysis_tree_sha256",
            "source_sha256",
            "credential_material_present",
        }
        or projection.get("suite_id") != suite.get("suite_id")
        or projection.get("profile") != PROFILE
        or projection.get("generated_at") != state.get("generated_at")
        or projection.get("outside_analysis_tree_sha256") != suite.get("outside_analysis_tree_sha256")
        or projection.get("source_sha256") != state.get("source_sha256")
        or projection.get("credential_material_present") is not False
    ):
        raise FormalDeleteGuardError("delete_cleanup_state_invalid")
    run_ids: set[str] = set()
    for item in snapshots:
        if (
            not isinstance(item, dict)
            or set(item) != {"run_id", "status", "manifest_sha256", "tree_sha256"}
            or not RUN_ID_RE.fullmatch(str(item.get("run_id") or ""))
            or item.get("status") not in {"pending", "removed"}
            or not SHA256_RE.fullmatch(str(item.get("manifest_sha256") or ""))
            or not SHA256_RE.fullmatch(str(item.get("tree_sha256") or ""))
        ):
            raise FormalDeleteGuardError("delete_cleanup_state_invalid")
        run_ids.add(str(item["run_id"]))
    final_digest = state.get("final_raw_sha256")
    if (
        len(run_ids) != len(ALL_CASES)
        or (phase == "terminal" and not SHA256_RE.fullmatch(str(final_digest or "")))
        or (phase != "terminal" and final_digest != "")
        or (phase in {"cleaned", "terminal"} and (
            fixture.get("status") != "removed"
            or any(item.get("status") != "removed" for item in snapshots)
        ))
    ):
        raise FormalDeleteGuardError("delete_cleanup_state_invalid")
    return dict(state)


def _read_cleanup_state(
    root: Path,
    suite: Mapping[str, Any],
    suite_dir: Path,
) -> dict[str, Any] | None:
    path = _cleanup_state_path(suite_dir)
    if not path.exists() and not path.is_symlink():
        return None
    content = _stable_file(path, private=True)
    try:
        value = json.loads(content)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise FormalDeleteGuardError("delete_cleanup_state_invalid") from exc
    if not isinstance(value, dict):
        raise FormalDeleteGuardError("delete_cleanup_state_invalid")
    return _validate_cleanup_state(root, suite, value)


def _persist_cleanup_state(
    root: Path,
    suite: Mapping[str, Any],
    suite_dir: Path,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    validated = _validate_cleanup_state(root, suite, state)
    _replace_private_json(_cleanup_state_path(suite_dir), validated)
    return validated


def _verify_cleanup_absent(
    root: Path,
    suite: Mapping[str, Any],
    analysis_root: Path,
    state: Mapping[str, Any],
) -> None:
    fixture = analysis_root / str(suite["fixture_name"])
    if fixture.exists() or fixture.is_symlink():
        raise FormalDeleteGuardError("delete_final_cleanup_changed")
    for item in state["snapshots"]:
        snapshot_path = root / SNAPSHOT_ROOT_RELATIVE / str(item["run_id"])
        if snapshot_path.exists() or snapshot_path.is_symlink():
            raise FormalDeleteGuardError("delete_final_cleanup_changed")
    if _tree_sha256(analysis_root) != suite["outside_analysis_tree_sha256"]:
        raise FormalDeleteGuardError("analysis_tree_not_restored_after_suite")


def _resume_cleanup_sources(
    root: Path,
    suite: Mapping[str, Any],
    suite_dir: Path,
    analysis_root: Path,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    current = dict(state)
    current["fixture"] = dict(state["fixture"])
    current["snapshots"] = [dict(item) for item in state["snapshots"]]
    if current["phase"] == "terminal":
        _verify_cleanup_absent(root, suite, analysis_root, current)
        return current
    if current["phase"] == "intent":
        current["phase"] = "cleaning"
        current = _persist_cleanup_state(root, suite, suite_dir, current)

    snapshots = [dict(item) for item in current["snapshots"]]
    for index, item in enumerate(snapshots):
        path = root / SNAPSHOT_ROOT_RELATIVE / str(item["run_id"])
        if item["status"] == "removed":
            if path.exists() or path.is_symlink():
                raise FormalDeleteGuardError("delete_snapshot_cleanup_reappeared")
            continue
        if path.exists() or path.is_symlink():
            snapshot = _load_snapshot(root, str(item["run_id"]), analysis_root)
            _verify_snapshot_tree_for_removal(snapshot)
            manifest_sha256 = _sha256(
                _stable_file(path / "snapshot-manifest.json", private=True, max_bytes=16 * 1024 * 1024)
            )
            if manifest_sha256 != item["manifest_sha256"] or snapshot.tree_sha256 != item["tree_sha256"]:
                raise FormalDeleteGuardError("delete_snapshot_cleanup_changed")
            shutil.rmtree(path)
        if path.exists() or path.is_symlink():
            raise FormalDeleteGuardError("delete_suite_cleanup_failed")
        snapshots[index]["status"] = "removed"
        current["snapshots"] = snapshots
        current = _persist_cleanup_state(root, suite, suite_dir, current)
        snapshots = [dict(value) for value in current["snapshots"]]

    fixture_path = analysis_root / str(suite["fixture_name"])
    fixture_state = dict(current["fixture"])
    if fixture_state["status"] == "removed":
        if fixture_path.exists() or fixture_path.is_symlink():
            raise FormalDeleteGuardError("delete_fixture_cleanup_reappeared")
    else:
        if fixture_path.exists() or fixture_path.is_symlink():
            observed = _verify_fixture(
                analysis_root,
                fixture_name=str(suite["fixture_name"]),
                normal_deleted=True,
            )
            if (
                observed["file_count"] != fixture_state["file_count"]
                or observed["tree_sha256"] != fixture_state["tree_sha256"]
            ):
                raise FormalDeleteGuardError("delete_fixture_cleanup_changed")
            shutil.rmtree(fixture_path)
        if fixture_path.exists() or fixture_path.is_symlink():
            raise FormalDeleteGuardError("delete_suite_cleanup_failed")
        fixture_state["status"] = "removed"
        current["fixture"] = fixture_state
        current = _persist_cleanup_state(root, suite, suite_dir, current)

    _verify_cleanup_absent(root, suite, analysis_root, current)
    current["phase"] = "cleaned"
    return _persist_cleanup_state(root, suite, suite_dir, current)


def _final_raw_path(suite_dir: Path) -> Path:
    return suite_dir / "final.raw.json"


def _read_final_raw(root: Path, suite: Mapping[str, Any], suite_dir: Path) -> tuple[dict[str, Any], bytes] | None:
    path = _final_raw_path(suite_dir)
    if not path.exists() and not path.is_symlink():
        return None
    content = _stable_file(path, private=True)
    try:
        value = json.loads(content)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise FormalDeleteGuardError("delete_final_receipt_invalid") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != FINAL_RAW_SCHEMA_VERSION
        or value.get("suite_id") != suite.get("suite_id")
        or value.get("source_sha256") != _source_sha256(root)
        or value.get("outside_analysis_tree_sha256") != suite.get("outside_analysis_tree_sha256")
        or value.get("cleanup")
        != {
            "sandbox_deleted": True,
            "forward_port_released": True,
            "active_state_removed": True,
            "ephemeral_identity_removed": True,
            "transaction_finalized": True,
            "snapshot_artifacts_removed": True,
            "fixture_removed": True,
            "outside_analysis_tree_unchanged": True,
        }
    ):
        raise FormalDeleteGuardError("delete_final_receipt_invalid")
    return value, content


def _artifact_path(root: Path, value: Path, *, suffix: str) -> Path:
    relative = PurePosixPath(value.as_posix())
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise FormalDeleteGuardError("delete_artifact_path_invalid")
    candidate = root.joinpath(*relative.parts)
    if candidate.parent != root / ARTIFACT_ROOT_RELATIVE or not candidate.name.endswith(suffix):
        raise FormalDeleteGuardError("delete_artifact_path_invalid")
    _private_directory(candidate.parent, create=False)
    if candidate.exists() or candidate.is_symlink():
        raise FormalDeleteGuardError("delete_artifact_output_exists")
    return candidate


def _artifact_paths(root: Path, json_value: Path, markdown_value: Path) -> tuple[Path, Path]:
    json_path = _artifact_path(root, json_value, suffix=".sanitized.json")
    markdown_path = _artifact_path(root, markdown_value, suffix=".sanitized.md")
    if json_path.name.removesuffix(".sanitized.json") != markdown_path.name.removesuffix(".sanitized.md"):
        raise FormalDeleteGuardError("delete_artifact_pair_invalid")
    return json_path, markdown_path


def build_evidence(*, project_root: Path, final_raw: Mapping[str, Any], raw_receipt_sha256: str) -> dict[str, Any]:
    root = project_root.resolve(strict=True)
    sources = final_raw["source_sha256"]
    if sources != _source_sha256(root):
        raise FormalDeleteGuardError("delete_producer_changed_after_capture")
    schema_bytes = _stable_file(root / SCHEMA_RELATIVE)
    cases = final_raw["cases"]
    normal = final_raw["normal_cleanup"]
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": final_raw["generated_at"],
        "decision": "GO",
        "profile": PROFILE,
        "scope": "formal_business_sandbox",
        "formal_business_run": True,
        "business_inference_exercised": False,
        "threshold": {
            "absolute_deleted_file_count": deletion_guard.MAX_ABSOLUTE_DELETIONS,
            "trigger_operator": "greater_than",
            "monitoring": "recursive_filesystem_events",
        },
        "transactions": {
            "contract": "four_distinct_formal_transaction_v2_receipts",
            "transaction_count": 4,
            "run_set_sha256": final_raw["run_set_sha256"],
            "receipt_set_sha256": final_raw["transaction_receipt_set_sha256"],
        },
        "cases": cases,
        "normal_cleanup": normal,
        "cleanup": final_raw["cleanup"],
        "host_runtime_unchanged": True,
        "cutover_performed": False,
        "snapshot_integrity_verified": True,
        "analysis_tree_outside_fixture_unchanged": True,
        "provenance": {
            "hermes_commit": HERMES_COMMIT,
            "image_sha256": final_raw["image_sha256"],
            "policy_sha256": final_raw["policy_sha256"],
            "mount_contract_sha256": final_raw["mount_contract_sha256"],
            **sources,
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
        raise FormalDeleteGuardError("delete_evidence_schema_invalid") from exc
    cases = payload.get("cases")
    normal = payload.get("normal_cleanup")
    if not isinstance(cases, list) or not isinstance(normal, dict):
        raise FormalDeleteGuardError("delete_evidence_binding_invalid")
    run_ids = [case.get("run_id_sha256") for case in cases] + [normal.get("run_id_sha256")]
    transactions = [case.get("transaction_receipt_sha256") for case in cases] + [
        normal.get("transaction_receipt_sha256")
    ]
    if len(set(run_ids)) != 4 or len(set(transactions)) != 4:
        raise FormalDeleteGuardError("delete_evidence_transaction_reuse")
    if payload.get("transactions", {}).get("run_set_sha256") != _canonical_sha256(sorted(run_ids)):
        raise FormalDeleteGuardError("delete_evidence_binding_invalid")
    if payload.get("transactions", {}).get("receipt_set_sha256") != _canonical_sha256(sorted(transactions)):
        raise FormalDeleteGuardError("delete_evidence_binding_invalid")


def _markdown() -> bytes:
    return (
        "# Formal OpenShell Delete Guard Evidence\n\n"
        "- Decision: `GO`\n"
        "- Scope: `formal_business_sandbox`\n"
        "- Shell rm, Python shutil and Node fs paths: sandbox terminated after 501 baseline deletions\n"
        "- Snapshot restoration: complete with zero missing fixture files\n"
        "- Normal current-task cleanup: allowed while the sandbox remained healthy\n"
        "- Four distinct formal transactions: terminal and identity-clean\n"
        "- Synthetic fixture and per-run deletion snapshots: removed\n"
        "- Analysis tree outside the synthetic fixture: unchanged\n"
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
        raise FormalDeleteGuardError("delete_evidence_output_exists")
    staged: list[tuple[Path, Path]] = []
    installed: list[Path] = []
    try:
        for path, content in outputs:
            staged.append((path, _stage(path, content)))
        for path, temporary in staged:
            try:
                os.link(temporary, path, follow_symlinks=False)
            except FileExistsError as exc:
                raise FormalDeleteGuardError("delete_evidence_output_exists") from exc
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
                raise FormalDeleteGuardError("delete_evidence_output_invalid")
    except Exception:
        for path in installed:
            path.unlink(missing_ok=True)
        raise
    finally:
        for _, temporary in staged:
            temporary.unlink(missing_ok=True)


def _prepare_final_raw(root: Path, suite: Mapping[str, Any], suite_dir: Path) -> tuple[dict[str, Any], bytes]:
    adapter = LifecycleAdapter(project_root=root)
    proof_spec = adapter.spec(
        profile=PROFILE,
        market=str(suite["market"]),
        company=str(suite["company"]),
        run_id=f"proof-{suite['suite_id']}",
    )
    state = _read_cleanup_state(root, suite, suite_dir)
    existing_final_before_cleanup = _read_final_raw(root, suite, suite_dir)
    if state is None and existing_final_before_cleanup is not None:
        raise FormalDeleteGuardError("delete_cleanup_state_missing")
    if (
        state is not None
        and existing_final_before_cleanup is not None
        and state["phase"] not in {"cleaned", "terminal"}
    ):
        raise FormalDeleteGuardError("delete_final_receipt_out_of_order")
    if state is None:
        fixture = _verify_fixture(
            proof_spec.analysis_root,
            fixture_name=str(suite["fixture_name"]),
            normal_deleted=True,
        )
        if _tree_sha256(proof_spec.analysis_root, excluded_name=str(suite["fixture_name"])) != suite[
            "outside_analysis_tree_sha256"
        ]:
            raise FormalDeleteGuardError("analysis_tree_changed_before_publish")

        projected_cases: list[dict[str, Any]] = []
        normal_projection: dict[str, Any] | None = None
        case_receipt_digests: dict[str, str] = {}
        runtime_bindings: list[tuple[str, str, str]] = []
        run_ids: list[str] = []
        transaction_receipts: list[str] = []
        for mechanism in ALL_CASES:
            case, case_content = _read_case(root, suite, suite_dir, mechanism)
            projection = _revalidate_case(root, suite, case, mechanism, proof_spec.analysis_root)
            digest = _sha256(case_content)
            projection["raw_case_receipt_sha256"] = digest
            case_receipt_digests[mechanism] = digest
            run_id = str(case["runtime_identifiers"]["run_id"])
            run_ids.append(run_id)
            before = case["before"]
            runtime_bindings.append(
                (
                    str(before["image_sha256"]),
                    str(before["policy_sha256"]),
                    str(before["mount_contract_sha256"]),
                )
            )
            transaction_receipts.append(str(projection["transaction_receipt_sha256"]))
            if mechanism == NORMAL_MECHANISM:
                normal_projection = projection
            else:
                projected_cases.append(projection)
        if normal_projection is None or len(set(run_ids)) != 4 or len(set(transaction_receipts)) != 4:
            raise FormalDeleteGuardError("delete_suite_transaction_reuse")
        if len(set(runtime_bindings)) != 1:
            raise FormalDeleteGuardError("delete_suite_runtime_provenance_mismatch")
        image_sha256, policy_sha256, mount_contract_sha256 = runtime_bindings[0]
        snapshot_resources: list[dict[str, str]] = []
        for run_id in sorted(run_ids):
            snapshot = _load_snapshot(root, run_id, proof_spec.analysis_root)
            _verify_snapshot_tree_for_removal(snapshot)
            snapshot_resources.append(
                {
                    "run_id": run_id,
                    "status": "pending",
                    "manifest_sha256": _sha256(
                        _stable_file(
                            snapshot.path / "snapshot-manifest.json",
                            private=True,
                            max_bytes=16 * 1024 * 1024,
                        )
                    ),
                    "tree_sha256": snapshot.tree_sha256,
                }
            )
        sources = _source_sha256(root)
        generated_at = _utc_now()
        state = {
            "schema_version": CLEANUP_STATE_SCHEMA_VERSION,
            "suite_id": suite["suite_id"],
            "phase": "intent",
            "generated_at": generated_at,
            "outside_analysis_tree_sha256": suite["outside_analysis_tree_sha256"],
            "fixture": {
                "name": suite["fixture_name"],
                "status": "pending",
                "file_count": fixture["file_count"],
                "tree_sha256": fixture["tree_sha256"],
            },
            "snapshots": snapshot_resources,
            "final_projection": {
                "generated_at": generated_at,
                "suite_id": suite["suite_id"],
                "profile": PROFILE,
                "cases": sorted(projected_cases, key=lambda item: item["mechanism"]),
                "normal_cleanup": normal_projection,
                "case_receipt_sha256": case_receipt_digests,
                "run_set_sha256": _canonical_sha256(sorted(_sha256(value.encode("ascii")) for value in run_ids)),
                "transaction_receipt_set_sha256": _canonical_sha256(sorted(transaction_receipts)),
                "image_sha256": image_sha256,
                "policy_sha256": policy_sha256,
                "mount_contract_sha256": mount_contract_sha256,
                "outside_analysis_tree_sha256": suite["outside_analysis_tree_sha256"],
                "source_sha256": sources,
                "credential_material_present": False,
            },
            "source_sha256": sources,
            "final_raw_sha256": "",
        }
        state = _validate_cleanup_state(root, suite, state)
        provisional_final = {
            "schema_version": FINAL_RAW_SCHEMA_VERSION,
            **state["final_projection"],
            "cleanup": _cleanup_receipt(),
        }
        build_evidence(
            project_root=root,
            final_raw=provisional_final,
            raw_receipt_sha256="0" * 64,
        )
        _write_exclusive(
            _cleanup_state_path(suite_dir),
            json.dumps(state, ensure_ascii=True, indent=2, sort_keys=True).encode("ascii") + b"\n",
        )
    else:
        provisional_final = {
            "schema_version": FINAL_RAW_SCHEMA_VERSION,
            **state["final_projection"],
            "cleanup": _cleanup_receipt(),
        }
        build_evidence(
            project_root=root,
            final_raw=provisional_final,
            raw_receipt_sha256="0" * 64,
        )

    state = _resume_cleanup_sources(root, suite, suite_dir, proof_spec.analysis_root, state)
    projection = dict(state["final_projection"])
    final = {"schema_version": FINAL_RAW_SCHEMA_VERSION, **projection, "cleanup": _cleanup_receipt()}
    content = json.dumps(final, ensure_ascii=True, indent=2, sort_keys=True).encode("ascii") + b"\n"
    existing = _read_final_raw(root, suite, suite_dir)
    if state["phase"] == "terminal" and existing is None:
        raise FormalDeleteGuardError("delete_terminal_cleanup_receipt_missing")
    if existing is None:
        _write_exclusive(_final_raw_path(suite_dir), content)
    elif existing != (final, content):
        raise FormalDeleteGuardError("delete_final_receipt_changed")
    digest = _sha256(content)
    if state["phase"] == "terminal":
        if state["final_raw_sha256"] != digest:
            raise FormalDeleteGuardError("delete_terminal_cleanup_receipt_changed")
    else:
        state = dict(state)
        state["phase"] = "terminal"
        state["final_raw_sha256"] = digest
        _persist_cleanup_state(root, suite, suite_dir, state)
    return final, content


def publish_evidence(
    *,
    project_root: Path,
    suite_id: str,
    artifact_json: Path,
    artifact_markdown: Path,
) -> dict[str, Any]:
    root = project_root.expanduser().resolve(strict=True)
    if root != REPO_ROOT:
        raise FormalDeleteGuardError("project_root_invalid")
    json_path, markdown_path = _artifact_paths(root, artifact_json, artifact_markdown)
    with _runner_lock(root), _maintenance_lock(root):
        _suite_path, suite = _read_suite(root, suite_id)
        suite_dir = _suite_path.parent
        final_raw, raw_content = _prepare_final_raw(root, suite, suite_dir)
        evidence = build_evidence(project_root=root, final_raw=final_raw, raw_receipt_sha256=_sha256(raw_content))
        json_content = json.dumps(evidence, ensure_ascii=True, indent=2, sort_keys=True).encode("ascii") + b"\n"
        markdown_content = _markdown()
        findings = check_sanitized_artifacts.scan_content(json_path, json_content)
        findings.extend(check_sanitized_artifacts.scan_content(markdown_path, markdown_content))
        if findings:
            raise FormalDeleteGuardError("delete_evidence_sanitization_failed")
        _publish_exclusive(((json_path, json_content), (markdown_path, markdown_content)))
        if check_sanitized_artifacts.scan_paths([json_path, markdown_path]):
            json_path.unlink(missing_ok=True)
            markdown_path.unlink(missing_ok=True)
            raise FormalDeleteGuardError("delete_evidence_sanitization_failed")
        return evidence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--suite-id", required=True)
    prepare.add_argument("--market", required=True)
    prepare.add_argument("--company", required=True)
    capture = subparsers.add_parser("capture")
    capture.add_argument("--suite-id", required=True)
    capture.add_argument("--mechanism", required=True, choices=ALL_CASES)
    capture.add_argument("--run-id", required=True)
    capture.add_argument("--timeout", type=int, default=120)
    publish = subparsers.add_parser("publish")
    publish.add_argument("--suite-id", required=True)
    publish.add_argument("--artifact-json", type=Path, required=True)
    publish.add_argument("--artifact-markdown", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_suite(
                project_root=args.project_root,
                suite_id=args.suite_id,
                market=args.market,
                company=args.company,
            )
        elif args.command == "capture":
            path = capture_case(
                project_root=args.project_root,
                suite_id=args.suite_id,
                mechanism=args.mechanism,
                run_id=args.run_id,
                timeout=args.timeout,
            )
            result = {
                "ok": True,
                "decision": "captured",
                "mechanism": args.mechanism,
                "raw_receipt_sha256": _sha256(_stable_file(path, private=True)),
            }
        else:
            evidence = publish_evidence(
                project_root=args.project_root,
                suite_id=args.suite_id,
                artifact_json=args.artifact_json,
                artifact_markdown=args.artifact_markdown,
            )
            result = {"ok": True, "decision": evidence["decision"], "schema_version": evidence["schema_version"]}
    except (
        FormalDeleteGuardError,
        formal_filesystem.FormalFilesystemEvidenceError,
        formal_runtime_contract.FormalRuntimeContractError,
        deletion_guard.DestructiveActionGuardError,
        LifecycleError,
        sandbox_probe.ProbeError,
        transaction.TransactionError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
    ) as exc:
        code = getattr(exc, "code", "formal_delete_guard_failed")
        print(json.dumps({"ok": False, "decision": "NO_GO", "error_code": code}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
