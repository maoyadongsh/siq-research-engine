#!/usr/bin/env python3
"""Snapshot host paths that an observe-only OpenShell sandbox must not change."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "var/openshell/registry/immutable-paths.json"
PROFILE_SOURCE = ROOT / "agents/hermes/profiles/siq_analysis"
HOST_PROFILE = ROOT / "data/hermes/home/profiles/siq_analysis"
SCHEMA_VERSION = "siq.openshell.observe_host_invariants.v1"


class InvariantSnapshotError(RuntimeError):
    pass


def _profile_path_is_runtime_only(relative: Path) -> bool:
    if any(part in {".git", "__pycache__", ".pytest_cache"} for part in relative.parts):
        return True
    name = relative.name
    if name == ".env" or name.startswith(".env.") or name in {"auth.json", "FILES.sha256"}:
        return True
    if name.endswith(".pyc"):
        return True
    if relative.parts and relative.parts[0] in {"cache", "logs", "sessions", "workspace"}:
        return True
    return bool(relative.parts and relative.parts[0].startswith(("state.db", "response_store.db")))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise InvariantSnapshotError(f"not_regular_file:{path.relative_to(ROOT)}")
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _profile_records() -> list[tuple[str, int, str]]:
    if PROFILE_SOURCE.is_symlink() or not PROFILE_SOURCE.is_dir():
        raise InvariantSnapshotError("profile_source_missing")
    if HOST_PROFILE.is_symlink() or not HOST_PROFILE.is_dir():
        raise InvariantSnapshotError("host_profile_missing")
    records: list[tuple[str, int, str]] = []
    for source in sorted(PROFILE_SOURCE.rglob("*")):
        relative = source.relative_to(PROFILE_SOURCE)
        if _profile_path_is_runtime_only(relative):
            continue
        if source.is_symlink():
            raise InvariantSnapshotError(f"profile_source_symlink:{source.relative_to(ROOT)}")
        if not source.is_file():
            continue
        host_path = HOST_PROFILE / relative
        if host_path.is_symlink() or not host_path.is_file():
            raise InvariantSnapshotError(f"host_profile_static_file_missing:{relative}")
        info = host_path.stat()
        records.append((relative.as_posix(), info.st_size, _sha256_file(host_path)))
    return records


def _safe_registry_path(raw_path: Any) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        raise InvariantSnapshotError("immutable_registry_path_invalid")
    relative = Path(raw_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise InvariantSnapshotError("immutable_registry_path_invalid")
    path = ROOT / relative
    try:
        path.relative_to(ROOT)
    except ValueError as exc:
        raise InvariantSnapshotError("immutable_registry_path_escape") from exc
    if path.is_symlink() or not path.is_dir():
        raise InvariantSnapshotError(f"immutable_path_missing:{raw_path}")
    return path


def _metadata_record(path: Path) -> tuple[str, str, int, int, int, int]:
    info = path.lstat()
    relative = path.relative_to(ROOT).as_posix()
    if stat.S_ISDIR(info.st_mode):
        kind = "directory"
    elif stat.S_ISREG(info.st_mode):
        kind = "file"
    elif stat.S_ISLNK(info.st_mode):
        kind = "symlink"
    else:
        kind = "other"
    return (
        relative,
        kind,
        stat.S_IMODE(info.st_mode),
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _walk_metadata(root: Path) -> Iterable[tuple[str, str, int, int, int, int]]:
    yield _metadata_record(root)
    for current, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
        directory_names.sort()
        file_names.sort()
        current_path = Path(current)
        for name in directory_names:
            yield _metadata_record(current_path / name)
        for name in file_names:
            yield _metadata_record(current_path / name)


def snapshot() -> dict[str, Any]:
    if REGISTRY.is_symlink() or not REGISTRY.is_file():
        raise InvariantSnapshotError("immutable_registry_missing")
    try:
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvariantSnapshotError("immutable_registry_invalid") from exc
    entries = registry.get("entries") if isinstance(registry, dict) else None
    if not isinstance(entries, list) or not entries:
        raise InvariantSnapshotError("immutable_registry_entries_missing")

    profile_records = _profile_records()
    profile_digest = hashlib.sha256()
    for record in profile_records:
        profile_digest.update(json.dumps(record, ensure_ascii=True, separators=(",", ":")).encode("ascii"))
        profile_digest.update(b"\n")

    immutable_records: dict[str, tuple[str, str, int, int, int, int]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("recursive") is not True:
            raise InvariantSnapshotError("immutable_registry_entry_invalid")
        for record in _walk_metadata(_safe_registry_path(entry.get("path"))):
            immutable_records[record[0]] = record
    immutable_digest = hashlib.sha256()
    for record in sorted(immutable_records.values()):
        immutable_digest.update(json.dumps(record, ensure_ascii=True, separators=(",", ":")).encode("ascii"))
        immutable_digest.update(b"\n")

    return {
        "schema_version": SCHEMA_VERSION,
        "profile": "siq_analysis",
        "profile_static_file_count": len(profile_records),
        "profile_static_content_sha256": profile_digest.hexdigest(),
        "immutable_registry_sha256": _sha256_file(REGISTRY),
        "immutable_entry_count": len(entries),
        "immutable_path_record_count": len(immutable_records),
        "immutable_metadata_sha256": immutable_digest.hexdigest(),
    }


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise InvariantSnapshotError("output_symlink")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        Path(temporary).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        _write_atomic(args.output, snapshot())
        return 0
    except (OSError, InvariantSnapshotError) as exc:
        print(f"observe host invariant snapshot failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
