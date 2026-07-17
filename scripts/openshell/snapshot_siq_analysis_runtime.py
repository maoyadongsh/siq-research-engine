#!/usr/bin/env python3
"""Create a credential-free, isolated snapshot of the live siq_analysis runtime."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlsplit

import yaml

SCHEMA_VERSION = "siq.openshell.siq_analysis_runtime_snapshot.v3"
PROFILE = "siq_analysis"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROJECT_ROOT = REPO_ROOT

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.openshell import build_siq_analysis_runtime_config  # noqa: E402

SOURCE_RELATIVE = Path("data/hermes/home/profiles/siq_analysis")
SNAPSHOT_ROOT_RELATIVE = Path("var/openshell/siq-analysis/runtime-snapshots")
MANIFEST_NAME = "snapshot-manifest.json"
SQLITE_DATABASES = ("state.db", "response_store.db")
SQLITE_SIDECARS = tuple(f"{database}{suffix}" for database in SQLITE_DATABASES for suffix in ("-wal", "-shm"))
RUNTIME_STATE_DIRECTORY = "runtime-state"
RUNTIME_DIRECTORIES = ("sessions", "checkpoints", "cron", "memories")
FRESH_SNAPSHOT_MODE = "fresh"
ALLOWED_TOP_LEVEL = {
    "config.yaml",
    RUNTIME_STATE_DIRECTORY,
    *RUNTIME_DIRECTORIES,
    MANIFEST_NAME,
}
INLINE_SECRET_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "password",
    "private_key",
    "secret",
    "token",
}
SAFE_SECRET_REFERENCE_KEYS = {"api_key_env", "key_env", "token_env"}
FORBIDDEN_EXACT_NAMES = {
    ".env",
    "auth.json",
    "auth.lock",
    "gateway.lock",
    "gateway.pid",
    "gateway_state.json",
    "processes.json",
}
FORBIDDEN_SUFFIXES = (
    "-shm",
    "-wal",
    ".crt",
    ".key",
    ".lock",
    ".p12",
    ".pem",
    ".pfx",
    ".pid",
    ".shm",
    ".wal",
)
SENSITIVE_NAME_RE = re.compile(r"(?:^|[._-])(?:credential|credentials|tls|token|tokens)(?:$|[._-])")
SNAPSHOT_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
PRIVATE_KEY_MARKER_RE = re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
BEARER_VALUE_RE = re.compile(rb"(?i:authorization)\s*:\s*(?i:bearer)\s+\S+")


class RuntimeSnapshotError(RuntimeError):
    pass


def _absolute_normalized(path: Path, *, label: str) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        raise RuntimeSnapshotError(f"{label} must be absolute")
    if ".." in expanded.parts:
        raise RuntimeSnapshotError(f"{label} must not contain '..'")
    return Path(os.path.normpath(os.fspath(expanded)))


def _assert_no_symlink_components(path: Path, *, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            break
        if stat.S_ISLNK(mode):
            raise RuntimeSnapshotError(f"{label} uses a symlink: {current}")


def _require_directory(path: Path, *, label: str) -> None:
    _assert_no_symlink_components(path, label=label)
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise RuntimeSnapshotError(f"{label} does not exist: {path}") from exc
    if not stat.S_ISDIR(mode):
        raise RuntimeSnapshotError(f"{label} must be a non-symlink directory: {path}")


def _mkdir_chain(root: Path, relative: Path) -> Path:
    current = root
    for part in relative.parts:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            continue
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise RuntimeSnapshotError(f"snapshot root component is unsafe: {current}")
    return current


def _is_forbidden_name(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered in FORBIDDEN_EXACT_NAMES
        or lowered.startswith(".env.")
        or lowered.endswith(FORBIDDEN_SUFFIXES)
        or SENSITIVE_NAME_RE.search(lowered) is not None
    )


def _require_regular_source(path: Path, *, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise RuntimeSnapshotError(f"required {label} is missing: {path.name}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RuntimeSnapshotError(f"{label} must be a regular, non-symlink file: {path.name}")
    return info


def _read_stable_file(path: Path, *, label: str) -> bytes:
    expected = _require_regular_source(path, label=label)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
            raise RuntimeSnapshotError(f"{label} changed while it was opened: {path.name}")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        finished = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (finished.st_size, finished.st_mtime_ns, finished.st_ino) != (
        opened.st_size,
        opened.st_mtime_ns,
        opened.st_ino,
    ):
        raise RuntimeSnapshotError(f"{label} changed while it was copied: {path.name}")
    return b"".join(chunks)


def _write_new_file(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sha256_file(path: Path) -> tuple[int, str]:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    byte_count = 0
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeSnapshotError(f"snapshot output is not a regular file: {path.name}")
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
            byte_count += len(chunk)
    finally:
        os.close(descriptor)
    return byte_count, digest.hexdigest()


def _sensitive_config_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in SAFE_SECRET_REFERENCE_KEYS or lowered.endswith("_env") or lowered == "redact_secrets":
        return False
    return lowered in INLINE_SECRET_KEYS or any(lowered.endswith(f"_{item}") for item in INLINE_SECRET_KEYS)


def _validate_config_value(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            child_path = (*path, key)
            if _sensitive_config_key(key) and child not in (None, "", [], {}):
                raise RuntimeSnapshotError(f"runtime config contains an inline secret at {'.'.join(child_path)}")
            _validate_config_value(child, child_path)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate_config_value(child, (*path, str(index)))
        return
    if isinstance(value, str) and "://" in value:
        parsed = urlsplit(value)
        if parsed.username or parsed.password:
            raise RuntimeSnapshotError(f"runtime config URL contains credentials at {'.'.join(path)}")


def _copy_config(
    source: Path,
    destination: Path,
    *,
    project_root: Path,
    compile_config: bool,
) -> dict[str, Any]:
    content = _read_stable_file(source, label="runtime config")
    if PRIVATE_KEY_MARKER_RE.search(content) or BEARER_VALUE_RE.search(content):
        raise RuntimeSnapshotError("runtime config contains credential material")
    try:
        payload = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise RuntimeSnapshotError("runtime config is not valid YAML") from exc
    if not isinstance(payload, dict):
        raise RuntimeSnapshotError("runtime config must contain a mapping")
    _validate_config_value(payload)
    source_sha256 = hashlib.sha256(content).hexdigest()
    output = content
    compiled_sha256: str | None = None
    compiler_schema_version: str | None = None
    if compile_config:
        try:
            output, _summary_content, summary = build_siq_analysis_runtime_config.compile_runtime_config(
                payload,
                source_sha256=source_sha256,
                project_root=project_root.as_posix(),
            )
        except (build_siq_analysis_runtime_config.RuntimeConfigError, OSError, ValueError) as exc:
            raise RuntimeSnapshotError("compiled runtime config is invalid") from exc
        compiled_sha256 = str(summary.get("output_sha256") or "")
        compiler_schema_version = str(summary.get("schema_version") or "")
        if (
            not re.fullmatch(r"[0-9a-f]{64}", compiled_sha256)
            or compiler_schema_version != build_siq_analysis_runtime_config.SCHEMA_VERSION
            or hashlib.sha256(output).hexdigest() != compiled_sha256
        ):
            raise RuntimeSnapshotError("compiled runtime config digest is invalid")
    _write_new_file(destination, output)
    return {
        "present": True,
        "file_count": 1,
        "byte_count": len(output),
        "tree_sha256": hashlib.sha256(output).hexdigest(),
        "source_sha256": source_sha256,
        "compiled": compile_config,
        "compiled_sha256": compiled_sha256,
        "compiler_schema_version": compiler_schema_version,
    }


def _copy_regular_file(source: Path, destination: Path) -> tuple[int, str]:
    expected = _require_regular_source(source, label="runtime file")
    source_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    source_fd = os.open(source, source_flags)
    destination_fd = -1
    digest = hashlib.sha256()
    byte_count = 0
    try:
        opened = os.fstat(source_fd)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
            raise RuntimeSnapshotError(f"runtime file changed while it was opened: {source.name}")
        destination_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        while chunk := os.read(source_fd, 1024 * 1024):
            digest.update(chunk)
            byte_count += len(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                view = view[written:]
        os.fsync(destination_fd)
        finished = os.fstat(source_fd)
        if (finished.st_size, finished.st_mtime_ns, finished.st_ino) != (
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ino,
        ):
            raise RuntimeSnapshotError(f"runtime file changed while it was copied: {source.name}")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    finally:
        os.close(source_fd)
        if destination_fd >= 0:
            os.close(destination_fd)
    return byte_count, digest.hexdigest()


def _tree_digest(records: list[tuple[str, str, int, str]]) -> str:
    digest = hashlib.sha256()
    for relative, kind, byte_count, content_sha256 in sorted(records):
        digest.update(f"{kind}\0{relative}\0{byte_count}\0{content_sha256}\n".encode())
    return digest.hexdigest()


def _copy_runtime_directory(source: Path, destination: Path) -> tuple[dict[str, Any], int]:
    try:
        source_info = source.lstat()
    except FileNotFoundError:
        destination.mkdir(mode=0o700)
        records = [(source.name, "directory", 0, "")]
        return {
            "present": True,
            "source_present": False,
            "materialized_empty": True,
            "file_count": 0,
            "directory_count": 1,
            "byte_count": 0,
            "tree_sha256": _tree_digest(records),
        }, 0
    if stat.S_ISLNK(source_info.st_mode) or not stat.S_ISDIR(source_info.st_mode):
        raise RuntimeSnapshotError(f"allowed runtime entry must be a non-symlink directory: {source.name}")

    records: list[tuple[str, str, int, str]] = []
    skipped = 0

    def copy_one(source_path: Path, destination_path: Path, relative: Path) -> None:
        nonlocal skipped
        info = source_path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise RuntimeSnapshotError(f"allowed runtime tree contains a symlink: {source.name}/{relative}")
        if _is_forbidden_name(source_path.name):
            skipped += 1
            return
        if stat.S_ISDIR(info.st_mode):
            destination_path.mkdir(mode=0o700)
            records.append((relative.as_posix(), "directory", 0, ""))
            for child in sorted(source_path.iterdir(), key=lambda item: item.name):
                copy_one(child, destination_path / child.name, relative / child.name)
            return
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeSnapshotError(f"runtime tree contains a non-regular entry: {source.name}/{relative}")
        byte_count, content_sha256 = _copy_regular_file(source_path, destination_path)
        records.append((relative.as_posix(), "file", byte_count, content_sha256))

    copy_one(source, destination, Path(source.name))
    file_records = [record for record in records if record[1] == "file"]
    directory_records = [record for record in records if record[1] == "directory"]
    return {
        "present": True,
        "source_present": True,
        "materialized_empty": False,
        "file_count": len(file_records),
        "directory_count": len(directory_records),
        "byte_count": sum(record[2] for record in file_records),
        "tree_sha256": _tree_digest(records),
    }, skipped


def _materialize_fresh_runtime_directory(name: str, destination: Path) -> dict[str, Any]:
    destination.mkdir(mode=0o700)
    records = [(name, "directory", 0, "")]
    return {
        "present": True,
        "source_copied": False,
        "materialized_empty": True,
        "file_count": 0,
        "directory_count": 1,
        "byte_count": 0,
        "tree_sha256": _tree_digest(records),
    }


def _backup_sqlite(source: Path, destination: Path) -> dict[str, Any]:
    source_info = _require_regular_source(source, label="SQLite database")
    source_uri = f"file:{quote(source.as_posix(), safe='/')}?mode=ro"
    source_connection: sqlite3.Connection | None = None
    destination_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(source_uri, uri=True, timeout=30.0)
        source_connection.execute("PRAGMA query_only = ON")
        destination_connection = sqlite3.connect(destination, timeout=30.0)
        source_connection.backup(destination_connection, pages=256, sleep=0.01)
        destination_connection.commit()
        journal_mode = destination_connection.execute("PRAGMA journal_mode = DELETE").fetchone()
        integrity = destination_connection.execute("PRAGMA integrity_check").fetchone()
        destination_connection.commit()
    except sqlite3.DatabaseError as exc:
        raise RuntimeSnapshotError(f"SQLite backup failed for {source.name}: {exc}") from exc
    finally:
        if destination_connection is not None:
            destination_connection.close()
        if source_connection is not None:
            source_connection.close()

    current_info = source.lstat()
    if stat.S_ISLNK(current_info.st_mode) or (current_info.st_dev, current_info.st_ino) != (
        source_info.st_dev,
        source_info.st_ino,
    ):
        raise RuntimeSnapshotError(f"SQLite source changed identity during backup: {source.name}")
    if not journal_mode or str(journal_mode[0]).lower() != "delete":
        raise RuntimeSnapshotError(f"isolated SQLite journal mode is not DELETE: {source.name}")
    if not integrity or integrity[0] != "ok":
        raise RuntimeSnapshotError(f"isolated SQLite integrity check failed: {source.name}")
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(f"{destination}{suffix}").exists():
            raise RuntimeSnapshotError(f"SQLite backup retained a sidecar: {destination.name}{suffix}")
    os.chmod(destination, 0o600)
    byte_count, content_sha256 = _sha256_file(destination)
    return {
        "name": source.name,
        "byte_count": byte_count,
        "sha256": content_sha256,
        "integrity_check": "ok",
        "journal_mode": "delete",
        "backup_method": "python_sqlite3_connection_backup",
    }


def _write_manifest(destination: Path, manifest: Mapping[str, Any]) -> None:
    content = (json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode()
    _write_new_file(destination / MANIFEST_NAME, content)


def _audit_snapshot_tree(root: Path) -> None:
    for path in root.rglob("*"):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise RuntimeSnapshotError(f"snapshot contains a symlink: {path.relative_to(root)}")
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise RuntimeSnapshotError(f"snapshot contains a non-regular entry: {path.relative_to(root)}")
        if path.parent == root and path.name not in ALLOWED_TOP_LEVEL:
            raise RuntimeSnapshotError(f"snapshot contains a non-allowlisted entry: {path.name}")
        materialized_sidecar = path.parent == root / RUNTIME_STATE_DIRECTORY and path.name in SQLITE_SIDECARS
        if path.name != MANIFEST_NAME and not materialized_sidecar and _is_forbidden_name(path.name):
            raise RuntimeSnapshotError("snapshot contains a forbidden runtime artifact")
        if materialized_sidecar and (not stat.S_ISREG(info.st_mode) or info.st_size != 0):
            raise RuntimeSnapshotError("snapshot SQLite sidecar must be an empty regular file")


def _cleanup_staging(path: Path | None, snapshot_root: Path) -> None:
    if (
        path is not None
        and path.parent == snapshot_root
        and path.name.startswith(".snapshot-staging-")
        and path.exists()
    ):
        shutil.rmtree(path)


def snapshot_runtime(
    *,
    project_root: Path,
    destination: Path,
    source: Path | None = None,
    compile_config: bool = False,
    fresh: bool = False,
) -> dict[str, Any]:
    project_root = _absolute_normalized(project_root, label="project root")
    destination = _absolute_normalized(destination, label="snapshot destination")
    _require_directory(project_root, label="project root")
    if project_root in {Path("/"), Path("/home"), Path("/tmp"), Path("/var"), Path.home()}:
        raise RuntimeSnapshotError("project root is a dangerous target root")

    expected_source = project_root / SOURCE_RELATIVE
    source = _absolute_normalized(source or expected_source, label="runtime source")
    if source != expected_source:
        raise RuntimeSnapshotError("runtime source must be the current project siq_analysis profile")
    _require_directory(source, label="runtime source")

    snapshot_root = project_root / SNAPSHOT_ROOT_RELATIVE
    if destination.parent != snapshot_root or not SNAPSHOT_NAME_RE.fullmatch(destination.name):
        raise RuntimeSnapshotError("snapshot destination must be a named child of the managed snapshot root")
    if destination == snapshot_root or destination == project_root:
        raise RuntimeSnapshotError("snapshot destination is a dangerous target root")

    snapshot_root = _mkdir_chain(project_root, SNAPSHOT_ROOT_RELATIVE)
    _assert_no_symlink_components(destination, label="snapshot destination")
    if destination.exists() or destination.is_symlink():
        raise RuntimeSnapshotError("snapshot destination already exists")

    lock_path = snapshot_root / ".snapshot-operation.lock"
    try:
        lock_fd = os.open(
            lock_path,
            os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError as exc:
        raise RuntimeSnapshotError("snapshot operation lock path is unsafe") from exc
    staging: Path | None = None
    try:
        lock_info = os.fstat(lock_fd)
        if not stat.S_ISREG(lock_info.st_mode):
            raise RuntimeSnapshotError("snapshot operation lock must be a regular file")
        os.fchmod(lock_fd, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if destination.exists() or destination.is_symlink():
            raise RuntimeSnapshotError("snapshot destination already exists")
        staging = Path(tempfile.mkdtemp(prefix=".snapshot-staging-", dir=snapshot_root))
        os.chmod(staging, 0o700)

        config_summary = _copy_config(
            source / "config.yaml",
            staging / "config.yaml",
            project_root=project_root,
            compile_config=compile_config,
        )
        runtime_state = staging / RUNTIME_STATE_DIRECTORY
        runtime_state.mkdir(mode=0o700)
        runtime_entries: dict[str, Any] = {}
        if fresh:
            database_summaries: list[dict[str, Any]] = []
            sidecar_summaries: list[dict[str, Any]] = []
            skipped_forbidden = 0
            for name in RUNTIME_DIRECTORIES:
                runtime_entries[name] = _materialize_fresh_runtime_directory(name, staging / name)
        else:
            database_summaries = [
                _backup_sqlite(source / database, runtime_state / database) for database in SQLITE_DATABASES
            ]
            sidecar_summaries = []
            for sidecar in SQLITE_SIDECARS:
                _write_new_file(runtime_state / sidecar, b"")
                sidecar_summaries.append(
                    {
                        "name": sidecar,
                        "byte_count": 0,
                        "sha256": hashlib.sha256(b"").hexdigest(),
                        "materialization": "empty_not_copied_from_host",
                    }
                )
            skipped_forbidden = 0
            for name in RUNTIME_DIRECTORIES:
                summary, skipped = _copy_runtime_directory(source / name, staging / name)
                runtime_entries[name] = summary
                skipped_forbidden += skipped

        database_bytes = sum(int(item["byte_count"]) for item in database_summaries)
        runtime_bytes = sum(int(item["byte_count"]) for item in runtime_entries.values())
        copy_policy = {
            "allowlist_only": True,
            "config_files": ["config.yaml"],
            "runtime_state_directory": RUNTIME_STATE_DIRECTORY,
            "sqlite_databases": [] if fresh else list(SQLITE_DATABASES),
            "sqlite_sidecars": [] if fresh else list(SQLITE_SIDECARS),
            "runtime_directories": list(RUNTIME_DIRECTORIES),
        }
        safeguards = {
            "source_opened_read_only": True,
            "runtime_config_compiled": compile_config,
            "sqlite_backup_api": not fresh,
            "credentials_copied": False,
            "tls_material_copied": False,
            "host_process_state_copied": False,
            "sqlite_sidecars_copied": False,
            "sqlite_sidecars_materialized_empty": not fresh,
            "symlinks_allowed": False,
        }
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "profile": PROFILE,
            "snapshot_kind": "isolated_runtime",
            "source_scope": (
                "current_project_siq_analysis_config_only" if fresh else "current_project_siq_analysis_runtime"
            ),
            "copy_policy": copy_policy,
            "safeguards": safeguards,
            "inventory": {
                "config": config_summary,
                "databases": database_summaries,
                "sqlite_sidecars": sidecar_summaries,
                "runtime_entries": runtime_entries,
                "skipped_forbidden_artifact_count": skipped_forbidden,
                "total_file_bytes": int(config_summary["byte_count"]) + database_bytes + runtime_bytes,
            },
        }
        if fresh:
            manifest["snapshot_mode"] = FRESH_SNAPSHOT_MODE
            manifest["host_runtime_records_copied"] = False
            safeguards["host_runtime_records_copied"] = False
        _write_manifest(staging, manifest)
        _audit_snapshot_tree(staging)
        os.rename(staging, destination)
        return manifest
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)
        _cleanup_staging(staging, snapshot_root)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--compile-config",
        action="store_true",
        help="Compile the current profile config for the sandbox before snapshotting it.",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Create empty runtime state without copying host sessions, memories, or databases.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        manifest = snapshot_runtime(
            project_root=args.project_root,
            source=args.source,
            destination=args.output,
            compile_config=args.compile_config,
            fresh=args.fresh,
        )
        config_summary = manifest["inventory"]["config"]
        result = {
            "schema_version": manifest["schema_version"],
            "profile": PROFILE,
            "snapshot": str(_absolute_normalized(args.output, label="snapshot destination")),
            "manifest": MANIFEST_NAME,
            "runtime_config_sha256": config_summary.get("compiled_sha256"),
            "source_config_sha256": config_summary.get("source_sha256"),
        }
        if manifest.get("snapshot_mode") == FRESH_SNAPSHOT_MODE:
            result["snapshot_mode"] = FRESH_SNAPSHOT_MODE
            result["host_runtime_records_copied"] = False
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0
    except (OSError, RuntimeSnapshotError) as exc:
        print(f"siq_analysis runtime snapshot failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
