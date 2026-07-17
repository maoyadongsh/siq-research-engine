#!/usr/bin/env python3
"""Exercise SIQ runtime-state create/delete/rebuild semantics without Hermes."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sqlite3
import stat
import sys
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "siq.openshell.runtime_state_lifecycle_smoke.v1"
SQLITE_DATABASES = ("state.db", "response_store.db")
SQLITE_SIDECARS = tuple(f"{database}{suffix}" for database in SQLITE_DATABASES for suffix in ("-wal", "-shm"))
RUNTIME_METADATA = ("gateway.pid", "gateway.lock", "gateway_state.json", "processes.json")
LIFECYCLE_FILES = (*SQLITE_SIDECARS, *RUNTIME_METADATA)
FORMAL_SANDBOX_REASON_CODES = ("formal_runtime_directory_bind_requires_live_sandbox_evidence",)
RESOLVED_DESIGN_BLOCKERS = (
    "sqlite_sidecars_are_not_file_bind_mounts",
    "gateway_metadata_parent_allows_atomic_replace",
    "hermes_control_home_remains_outside_runtime_state_mount",
)
WAL_MAGIC = {b"\x37\x7f\x06\x82", b"\x37\x7f\x06\x83"}


def is_passed_lifecycle_result(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    formal = value.get("formal_sandbox_evidence")
    rounds = value.get("rounds")
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("status") != "passed"
        or value.get("scope") != "candidate_image_directory_bind_without_openshell_policy_or_gateway"
        or value.get("readiness_effect") != "none"
        or value.get("gateway_started") is not False
        or value.get("provider_contacted") is not False
        or value.get("rounds_completed") != 2
        or value.get("final_cleanup") is not True
        or not isinstance(formal, Mapping)
        or formal.get("status") != "pending_live_validation"
        or formal.get("reason_codes") != list(FORMAL_SANDBOX_REASON_CODES)
        or formal.get("resolved_design_blockers") != list(RESOLVED_DESIGN_BLOCKERS)
        or not isinstance(rounds, list)
        or len(rounds) != 2
    ):
        return False
    expected_metadata = {
        "gateway.pid": "atomic_create",
        "gateway.lock": "exclusive_create_and_flock",
        "gateway_state.json": "atomic_create_and_replace",
        "processes.json": "atomic_create_and_replace",
    }
    expected_sqlite = {
        "integrity_check": "ok",
        "journal_mode": "wal",
        "shm_nonempty": True,
        "wal_header_valid": True,
        "wal_nonempty": True,
    }
    for generation, item in enumerate(rounds, start=1):
        if (
            not isinstance(item, Mapping)
            or isinstance(item.get("generation"), bool)
            or not isinstance(item.get("generation"), int)
            or item.get("generation") != generation
            or item.get("created") != list(LIFECYCLE_FILES)
            or item.get("deleted") != list(LIFECYCLE_FILES)
            or item.get("metadata") != expected_metadata
        ):
            return False
        sqlite_evidence = item.get("sqlite")
        if not isinstance(sqlite_evidence, Mapping) or set(sqlite_evidence) != set(SQLITE_DATABASES):
            return False
        if any(sqlite_evidence.get(name) != expected_sqlite for name in SQLITE_DATABASES):
            return False
    return True


class RuntimeStateSmokeError(RuntimeError):
    """A stable, secret-free lifecycle-smoke failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _assert_no_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            raise RuntimeStateSmokeError("runtime_smoke_root_missing") from None
        if stat.S_ISLNK(mode):
            raise RuntimeStateSmokeError("runtime_smoke_root_uses_symlink")


def _validate_root(value: Path) -> Path:
    expanded = value.expanduser()
    if not expanded.is_absolute() or ".." in expanded.parts:
        raise RuntimeStateSmokeError("runtime_smoke_root_not_absolute")
    root = Path(os.path.normpath(os.fspath(expanded)))
    if root in {Path("/"), Path("/home"), Path("/tmp"), Path("/var"), Path.home()}:
        raise RuntimeStateSmokeError("runtime_smoke_root_dangerous")
    _assert_no_symlink_components(root)
    try:
        info = root.lstat()
    except FileNotFoundError:
        raise RuntimeStateSmokeError("runtime_smoke_root_missing") from None
    if not stat.S_ISDIR(info.st_mode):
        raise RuntimeStateSmokeError("runtime_smoke_root_not_directory")
    if not os.access(root, os.W_OK | os.X_OK):
        raise RuntimeStateSmokeError("runtime_smoke_root_not_writable")
    for variable in ("HERMES_HOME", "SIQ_PROJECT_ROOT"):
        protected = os.environ.get(variable, "").strip()
        if protected:
            protected_path = Path(os.path.normpath(protected))
            if root == protected_path or root in protected_path.parents or protected_path in root.parents:
                raise RuntimeStateSmokeError("runtime_smoke_root_overlaps_protected_path")
    if any(root.iterdir()):
        raise RuntimeStateSmokeError("runtime_smoke_root_not_empty")
    return root


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise RuntimeStateSmokeError("runtime_metadata_write_failed")
        view = view[written:]


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.parent / f".siq-smoke-{path.name}.{os.getpid()}.tmp"
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        _write_all(descriptor, _json_bytes(payload))
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
    except OSError as exc:
        raise RuntimeStateSmokeError("runtime_metadata_atomic_replace_failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _write_gateway_lock(path: Path, payload: dict[str, Any]) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _write_all(descriptor, _json_bytes(payload))
        os.fsync(descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError as exc:
        raise RuntimeStateSmokeError("runtime_gateway_lock_create_failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _metadata_payload(name: str, generation: int, phase: str) -> dict[str, Any]:
    base: dict[str, Any] = {
        "generation": generation,
        "kind": "siq-runtime-lifecycle-smoke",
        "phase": phase,
        "pid": os.getpid(),
    }
    if name == "gateway_state.json":
        base["gateway_state"] = phase
    elif name == "processes.json":
        base["processes"] = []
    return base


def _create_runtime_metadata(root: Path, generation: int) -> dict[str, str]:
    _atomic_json_write(root / "gateway.pid", _metadata_payload("gateway.pid", generation, "running"))
    _write_gateway_lock(root / "gateway.lock", _metadata_payload("gateway.lock", generation, "running"))
    for name in ("gateway_state.json", "processes.json"):
        path = root / name
        _atomic_json_write(path, _metadata_payload(name, generation, "starting"))
        _atomic_json_write(path, _metadata_payload(name, generation, "running"))
    return {
        "gateway.pid": "atomic_create",
        "gateway.lock": "exclusive_create_and_flock",
        "gateway_state.json": "atomic_create_and_replace",
        "processes.json": "atomic_create_and_replace",
    }


def _validate_metadata(root: Path, generation: int) -> None:
    for name in RUNTIME_METADATA:
        path = root / name
        try:
            info = path.lstat()
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
            raise RuntimeStateSmokeError("runtime_metadata_validation_failed") from exc
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_mode & 0o077
            or not isinstance(payload, dict)
            or isinstance(payload.get("generation"), bool)
            or not isinstance(payload.get("generation"), int)
            or payload.get("generation") != generation
            or payload.get("phase") != "running"
        ):
            raise RuntimeStateSmokeError("runtime_metadata_validation_failed")


def _materialize_empty_sidecars(root: Path) -> None:
    for name in SQLITE_SIDECARS:
        try:
            descriptor = os.open(
                root / name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            os.close(descriptor)
        except OSError as exc:
            raise RuntimeStateSmokeError("sqlite_sidecar_preallocation_failed") from exc


def _initialize_databases(root: Path) -> None:
    for name in SQLITE_DATABASES:
        try:
            with sqlite3.connect(root / name, timeout=10.0) as connection:
                connection.execute("CREATE TABLE lifecycle_probe (generation INTEGER NOT NULL, marker TEXT NOT NULL)")
                connection.commit()
                mode = connection.execute("PRAGMA journal_mode = DELETE").fetchone()
                integrity = connection.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.DatabaseError as exc:
            raise RuntimeStateSmokeError("sqlite_database_initialize_failed") from exc
        if not mode or str(mode[0]).lower() != "delete" or not integrity or integrity[0] != "ok":
            raise RuntimeStateSmokeError("sqlite_database_initialize_failed")


def _open_wal_round(root: Path, generation: int) -> tuple[list[sqlite3.Connection], dict[str, dict[str, Any]]]:
    connections: list[sqlite3.Connection] = []
    evidence: dict[str, dict[str, Any]] = {}
    try:
        for name in SQLITE_DATABASES:
            connection = sqlite3.connect(root / name, timeout=10.0)
            connections.append(connection)
            mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()
            connection.execute("PRAGMA wal_autocheckpoint = 0")
            connection.execute(
                "INSERT INTO lifecycle_probe (generation, marker) VALUES (?, ?)",
                (generation, f"round-{generation}"),
            )
            connection.commit()
            row = connection.execute(
                "SELECT generation, marker FROM lifecycle_probe ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            wal = Path(f"{root / name}-wal")
            shm = Path(f"{root / name}-shm")
            if (
                not mode
                or str(mode[0]).lower() != "wal"
                or row != (generation, f"round-{generation}")
                or not integrity
                or integrity[0] != "ok"
                or not wal.is_file()
                or wal.stat().st_size <= 32
                or wal.read_bytes()[:4] not in WAL_MAGIC
                or not shm.is_file()
                or shm.stat().st_size <= 0
            ):
                raise RuntimeStateSmokeError("sqlite_wal_validation_failed")
            evidence[name] = {
                "integrity_check": "ok",
                "journal_mode": "wal",
                "shm_nonempty": True,
                "wal_header_valid": True,
                "wal_nonempty": True,
            }
    except sqlite3.DatabaseError as exc:
        for connection in reversed(connections):
            try:
                connection.close()
            except sqlite3.DatabaseError:
                pass
        raise RuntimeStateSmokeError("sqlite_wal_create_failed") from exc
    except OSError as exc:
        for connection in reversed(connections):
            try:
                connection.close()
            except sqlite3.DatabaseError:
                pass
        raise RuntimeStateSmokeError("sqlite_wal_validation_failed") from exc
    except BaseException:
        for connection in reversed(connections):
            try:
                connection.close()
            except sqlite3.DatabaseError:
                pass
        raise
    return connections, evidence


def _close_connections(connections: list[sqlite3.Connection]) -> None:
    first_error: sqlite3.DatabaseError | None = None
    for connection in reversed(connections):
        try:
            connection.close()
        except sqlite3.DatabaseError as exc:
            first_error = first_error or exc
    if first_error is not None:
        raise RuntimeStateSmokeError("sqlite_connection_close_failed") from first_error


def _delete_lifecycle_files(root: Path) -> None:
    for name in LIFECYCLE_FILES:
        try:
            (root / name).unlink(missing_ok=True)
        except OSError as exc:
            if name in SQLITE_SIDECARS:
                raise RuntimeStateSmokeError("sqlite_sidecar_delete_failed") from exc
            raise RuntimeStateSmokeError("runtime_metadata_delete_failed") from exc
    if any((root / name).exists() for name in LIFECYCLE_FILES):
        raise RuntimeStateSmokeError("runtime_lifecycle_delete_incomplete")


def _cleanup_owned_files(root: Path) -> None:
    journals = tuple(f"{database}-journal" for database in SQLITE_DATABASES)
    for name in (*SQLITE_DATABASES, *LIFECYCLE_FILES, *journals):
        try:
            (root / name).unlink(missing_ok=True)
        except OSError:
            pass
    try:
        temporary_files = tuple(root.glob(".siq-smoke-*.tmp"))
    except OSError:
        temporary_files = ()
    for path in temporary_files:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _run_round(root: Path, generation: int) -> dict[str, Any]:
    _materialize_empty_sidecars(root)
    connections: list[sqlite3.Connection] = []
    try:
        connections, sqlite_evidence = _open_wal_round(root, generation)
        metadata_evidence = _create_runtime_metadata(root, generation)
        _validate_metadata(root, generation)
    finally:
        _close_connections(connections)
    _delete_lifecycle_files(root)
    return {
        "created": list(LIFECYCLE_FILES),
        "deleted": list(LIFECYCLE_FILES),
        "generation": generation,
        "metadata": metadata_evidence,
        "sqlite": sqlite_evidence,
    }


def run_lifecycle_smoke(runtime_root: Path) -> dict[str, Any]:
    root = _validate_root(runtime_root)
    try:
        _initialize_databases(root)
        rounds = [_run_round(root, generation) for generation in (1, 2)]
        for name in SQLITE_DATABASES:
            try:
                (root / name).unlink()
            except OSError as exc:
                raise RuntimeStateSmokeError("sqlite_database_cleanup_failed") from exc
        if any(root.iterdir()):
            raise RuntimeStateSmokeError("runtime_smoke_final_cleanup_failed")
    except BaseException:
        _cleanup_owned_files(root)
        raise
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "passed",
        "scope": "candidate_image_directory_bind_without_openshell_policy_or_gateway",
        "readiness_effect": "none",
        "gateway_started": False,
        "provider_contacted": False,
        "rounds_completed": 2,
        "rounds": rounds,
        "final_cleanup": True,
        "formal_sandbox_evidence": {
            "status": "pending_live_validation",
            "reason_codes": list(FORMAL_SANDBOX_REASON_CODES),
            "resolved_design_blockers": list(RESOLVED_DESIGN_BLOCKERS),
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run_lifecycle_smoke(args.runtime_root)
    except RuntimeStateSmokeError as exc:
        result = {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "error_code": exc.code,
            "readiness_effect": "none",
        }
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 2
    except (OSError, sqlite3.DatabaseError):
        result = {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "error_code": "runtime_lifecycle_unclassified_io_failed",
            "readiness_effect": "none",
        }
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
