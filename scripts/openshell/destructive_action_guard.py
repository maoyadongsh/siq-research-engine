#!/usr/bin/env python3
"""Protect one task-scoped SIQ analysis bind root from mass deletion."""

from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import json
import os
import re
import select
import shutil
import stat
import struct
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

from scripts.openshell.security_audit import (
    SecurityRunContext,
    append_record,
    build_record,
    project_target,
)

SCHEMA_VERSION = "siq.openshell.deletion_snapshot.v1"
PROFILE = "siq_analysis"
REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_ROOT_RELATIVE = Path("var/openshell/siq-analysis/deletion-snapshots")
AUDIT_TARGET_SCOPE = "task_analysis"

WIKI_RELATIVE = Path("data/wiki")
COMPANY_ROOTS = (
    WIKI_RELATIVE / "companies",
    WIKI_RELATIVE / "eu/companies",
    WIKI_RELATIVE / "hk/companies",
    WIKI_RELATIVE / "jp/companies",
    WIKI_RELATIVE / "kr/companies",
    WIKI_RELATIVE / "us/companies",
)

MAX_ABSOLUTE_DELETIONS = 500
MIN_RATIO_DELETIONS = 20
RATIO_NUMERATOR = 1
RATIO_DENOMINATOR = 2

RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
EPHEMERAL_DIRECTORY_NAMES = {
    ".cache",
    ".work",
    "__pycache__",
    "cache",
    "temp",
    "tmp",
}
FORBIDDEN_EXACT_NAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    "auth.json",
    "auth.lock",
    "credentials.json",
    "id_ed25519",
    "id_rsa",
    "secrets.json",
    "token.json",
}
FORBIDDEN_DIRECTORY_NAMES = {".aws", ".ssh", "credentials", "secrets"}
FORBIDDEN_SUFFIXES = (".crt", ".key", ".p12", ".pem", ".pfx")
SENSITIVE_NAME_RE = re.compile(
    r"(?:^|[._-])(?:api[_-]?key|access[_-]?token|auth|credential|credentials|password|passwd|private[_-]?key|secret|secrets|token|tokens)(?:$|[._-])",
    re.IGNORECASE,
)
PRIVATE_KEY_MARKER_RE = re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
BEARER_VALUE_RE = re.compile(rb"(?i:authorization)\s*:\s*(?i:bearer)\s+[^\s<]{12,}")
CREDENTIAL_URL_RE = re.compile(rb"[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s]+@", re.IGNORECASE)
SENSITIVE_ASSIGNMENT_RE = re.compile(
    rb"(?i)(?:api[_-]?key|access[_-]?token|authorization|cookie|password|passwd|secret|token)"
    rb"[A-Za-z0-9_-]*\s*[:=]\s*[^\s,}\]]{8,}"
)

# Linux inotify constants from <sys/inotify.h>.
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_DELETE_SELF = 0x00000400
IN_MOVE_SELF = 0x00000800
IN_Q_OVERFLOW = 0x00004000
IN_IGNORED = 0x00008000
IN_ONLYDIR = 0x01000000
IN_DONT_FOLLOW = 0x02000000
IN_ISDIR = 0x40000000
INOTIFY_WATCH_MASK = (
    IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE | IN_DELETE | IN_DELETE_SELF | IN_MOVE_SELF | IN_ONLYDIR | IN_DONT_FOLLOW
)
INOTIFY_EVENT = struct.Struct("iIII")


class DestructiveActionGuardError(RuntimeError):
    """Raised when the guard cannot maintain its fixed safety contract."""


class SandboxTerminator(ABC):
    """Fixed injection boundary for stopping the one guarded sandbox."""

    @abstractmethod
    def terminate(self, *, sandbox_id: str, reason_code: str) -> None:
        """Synchronously stop and fence the named sandbox, or raise."""


@dataclass(frozen=True)
class SnapshotFile:
    relative_path: str
    byte_count: int
    sha256: str
    mode: int
    source_mtime_ns: int


@dataclass(frozen=True)
class SnapshotDirectory:
    relative_path: str
    mode: int


@dataclass(frozen=True)
class DeletionSnapshot:
    path: Path
    analysis_relative_path: str
    root_mode: int
    files: Mapping[str, SnapshotFile]
    directories: Mapping[str, SnapshotDirectory]
    tree_sha256: str


@dataclass(frozen=True)
class InotifyEvent:
    watch_descriptor: int
    mask: int
    cookie: int
    name: str


@dataclass(frozen=True)
class GuardResult:
    triggered: bool
    reason_code: str
    baseline_file_count: int
    observed_deleted_file_count: int
    restored_file_count: int
    snapshot_path: Path
    audit_path: Path | None
    deleted_paths: tuple[str, ...] = ()


def _absolute_normalized(path: Path, *, label: str) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute() or ".." in expanded.parts:
        raise DestructiveActionGuardError(f"{label} must be absolute and normalized")
    return Path(os.path.normpath(os.fspath(expanded)))


def _assert_no_symlink_components(path: Path, *, label: str) -> None:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            break
        if stat.S_ISLNK(mode):
            raise DestructiveActionGuardError(f"{label} contains a symlink component")


def _require_directory(path: Path, *, label: str) -> os.stat_result:
    _assert_no_symlink_components(path, label=label)
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise DestructiveActionGuardError(f"{label} does not exist") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise DestructiveActionGuardError(f"{label} must be a non-symlink directory")
    return info


def _validate_analysis_root(project_root: Path, analysis_root: Path) -> str:
    project_root = _absolute_normalized(project_root, label="project root")
    analysis_root = _absolute_normalized(analysis_root, label="analysis root")
    _require_directory(project_root, label="project root")
    if project_root.resolve(strict=True) != project_root:
        raise DestructiveActionGuardError("project root must not resolve through a symlink")
    _require_directory(analysis_root, label="analysis root")
    if analysis_root.resolve(strict=True) != analysis_root:
        raise DestructiveActionGuardError("analysis root must not resolve through a symlink")

    matched: Path | None = None
    for company_root_relative in COMPANY_ROOTS:
        company_root = project_root / company_root_relative
        try:
            candidate = analysis_root.relative_to(company_root)
        except ValueError:
            continue
        matched = candidate
        break
    if matched is None or len(matched.parts) != 2 or matched.parts[1] != "analysis":
        raise DestructiveActionGuardError("analysis root must be one company's direct analysis bind root")
    company = matched.parts[0]
    if (
        company in {"", ".", ".."}
        or len(company) > 128
        or not company[0].isalnum()
        or any(not (character.isalnum() or character in "-_.()（）") for character in company)
    ):
        raise DestructiveActionGuardError("analysis company directory name is unsafe")
    return analysis_root.relative_to(project_root).as_posix()


def _mkdir_checked(path: Path, *, private: bool) -> None:
    try:
        path.mkdir(mode=0o700 if private else 0o755)
    except FileExistsError:
        pass
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise DestructiveActionGuardError("managed state path is not a directory")
    if info.st_uid != os.geteuid():
        raise DestructiveActionGuardError("managed state path has an unexpected owner")
    if private and info.st_mode & 0o077:
        raise DestructiveActionGuardError("managed state path is not private")


def _managed_snapshot_root(project_root: Path) -> Path:
    current = project_root
    for index, component in enumerate(SNAPSHOT_ROOT_RELATIVE.parts):
        current /= component
        _mkdir_checked(current, private=index >= 1)
    if current.resolve(strict=True) != project_root / SNAPSHOT_ROOT_RELATIVE:
        raise DestructiveActionGuardError("managed snapshot root escapes the project")
    return current


def _is_forbidden_entry(path: Path, *, is_directory: bool) -> bool:
    lowered = path.name.lower()
    if lowered in FORBIDDEN_EXACT_NAMES or lowered.startswith(".env."):
        return True
    if lowered.endswith(FORBIDDEN_SUFFIXES) or SENSITIVE_NAME_RE.search(lowered):
        return True
    return is_directory and lowered in FORBIDDEN_DIRECTORY_NAMES


def _is_ephemeral(relative_path: Path) -> bool:
    return any(component.lower() in EPHEMERAL_DIRECTORY_NAMES for component in relative_path.parts)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_exclusive(path: Path, content: bytes, *, mode: int = 0o600) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        mode,
    )
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise DestructiveActionGuardError("short snapshot write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _private_snapshot_parent(files_root: Path, relative_parent: Path) -> Path:
    current = files_root
    for component in relative_parent.parts:
        current /= component
        _mkdir_checked(current, private=True)
    return current


def _scan_credential_markers(content: bytes) -> bool:
    return bool(
        PRIVATE_KEY_MARKER_RE.search(content)
        or BEARER_VALUE_RE.search(content)
        or CREDENTIAL_URL_RE.search(content)
        or SENSITIVE_ASSIGNMENT_RE.search(content)
    )


def _copy_stable_source(source: Path, destination: Path) -> SnapshotFile:
    expected = source.lstat()
    if not stat.S_ISREG(expected.st_mode) or expected.st_nlink != 1 or expected.st_mode & (stat.S_ISUID | stat.S_ISGID):
        raise DestructiveActionGuardError("snapshot source must be a single-link regular file")
    source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    destination_fd = -1
    digest = hashlib.sha256()
    byte_count = 0
    overlap = b""
    try:
        opened = os.fstat(source_fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino)
        ):
            raise DestructiveActionGuardError("snapshot source changed while opening")
        destination_fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        while chunk := os.read(source_fd, 1024 * 1024):
            if _scan_credential_markers(overlap + chunk):
                raise DestructiveActionGuardError("credential material is forbidden in deletion snapshots")
            overlap = (overlap + chunk)[-512:]
            digest.update(chunk)
            byte_count += len(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                if written <= 0:
                    raise DestructiveActionGuardError("short snapshot write")
                view = view[written:]
        os.fsync(destination_fd)
        finished = os.fstat(source_fd)
        if (
            finished.st_size,
            finished.st_mtime_ns,
            finished.st_ino,
            finished.st_nlink,
        ) != (
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ino,
            opened.st_nlink,
        ):
            raise DestructiveActionGuardError("snapshot source changed while copying")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    finally:
        os.close(source_fd)
        if destination_fd >= 0:
            os.close(destination_fd)
    return SnapshotFile(
        relative_path="",
        byte_count=byte_count,
        sha256=digest.hexdigest(),
        mode=stat.S_IMODE(expected.st_mode),
        source_mtime_ns=expected.st_mtime_ns,
    )


def _tree_sha256(files: Mapping[str, SnapshotFile], directories: Mapping[str, SnapshotDirectory]) -> str:
    digest = hashlib.sha256()
    for relative, directory in sorted(directories.items()):
        digest.update(f"d\0{relative}\0{directory.mode:o}\n".encode())
    for relative, file in sorted(files.items()):
        digest.update(f"f\0{relative}\0{file.byte_count}\0{file.sha256}\0{file.mode:o}\n".encode())
    return digest.hexdigest()


def _create_snapshot(
    *,
    project_root: Path,
    analysis_root: Path,
    analysis_relative_path: str,
    run_id: str,
) -> DeletionSnapshot:
    snapshot_root = _managed_snapshot_root(project_root)
    final_path = snapshot_root / run_id
    if final_path.exists() or final_path.is_symlink():
        raise DestructiveActionGuardError("deletion snapshot run id already exists")
    staging = Path(tempfile.mkdtemp(prefix=f".{run_id}.staging-", dir=snapshot_root))
    staging.chmod(0o700)
    files_root = staging / "files"
    files_root.mkdir(mode=0o700)
    files: dict[str, SnapshotFile] = {}
    directories: dict[str, SnapshotDirectory] = {}
    root_info = analysis_root.lstat()
    stack: list[tuple[Path, Path, bool]] = [(analysis_root, Path(), True)]
    try:
        while stack:
            current, current_relative, persistent_parent = stack.pop()
            for child in sorted(current.iterdir(), key=lambda item: item.name, reverse=True):
                relative = current_relative / child.name
                info = child.lstat()
                if stat.S_ISLNK(info.st_mode):
                    raise DestructiveActionGuardError("analysis root contains a symlink")
                is_directory = stat.S_ISDIR(info.st_mode)
                if _is_forbidden_entry(child, is_directory=is_directory):
                    raise DestructiveActionGuardError("analysis root contains a credential file")
                persistent = persistent_parent and not _is_ephemeral(relative)
                if is_directory:
                    if persistent:
                        directories[relative.as_posix()] = SnapshotDirectory(
                            relative_path=relative.as_posix(),
                            mode=stat.S_IMODE(info.st_mode),
                        )
                    stack.append((child, relative, persistent))
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise DestructiveActionGuardError("analysis root contains a special file")
                if info.st_nlink != 1:
                    raise DestructiveActionGuardError("analysis root contains a hard-linked file")
                if info.st_mode & (stat.S_ISUID | stat.S_ISGID):
                    raise DestructiveActionGuardError("analysis root contains a set-ID file")
                if not persistent:
                    continue
                destination_parent = _private_snapshot_parent(files_root, relative.parent)
                destination = destination_parent / relative.name
                copied = _copy_stable_source(child, destination)
                files[relative.as_posix()] = SnapshotFile(
                    relative_path=relative.as_posix(),
                    byte_count=copied.byte_count,
                    sha256=copied.sha256,
                    mode=copied.mode,
                    source_mtime_ns=copied.source_mtime_ns,
                )

        tree_sha256 = _tree_sha256(files, directories)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "profile": PROFILE,
            "snapshot_kind": "task_analysis_deletion_recovery",
            "analysis_relative_path": analysis_relative_path,
            "root_mode": stat.S_IMODE(root_info.st_mode),
            "baseline_file_count": len(files),
            "tree_sha256": tree_sha256,
            "safeguards": {
                "credentials_copied": False,
                "symlinks_allowed": False,
                "hardlinks_allowed": False,
                "special_files_allowed": False,
                "ephemeral_directories_copied": False,
            },
            "directories": [
                {"path": item.relative_path, "mode": item.mode}
                for item in sorted(directories.values(), key=lambda value: value.relative_path)
            ],
            "files": [
                {
                    "path": item.relative_path,
                    "byte_count": item.byte_count,
                    "sha256": item.sha256,
                    "mode": item.mode,
                    "source_mtime_ns": item.source_mtime_ns,
                }
                for item in sorted(files.values(), key=lambda value: value.relative_path)
            ],
        }
        manifest_content = (json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode()
        _write_exclusive(staging / "snapshot-manifest.json", manifest_content)
        _fsync_directory(files_root)
        _fsync_directory(staging)
        if final_path.exists() or final_path.is_symlink():
            raise DestructiveActionGuardError("deletion snapshot run id raced with another writer")
        os.rename(staging, final_path)
        _fsync_directory(snapshot_root)
        return DeletionSnapshot(
            path=final_path,
            analysis_relative_path=analysis_relative_path,
            root_mode=stat.S_IMODE(root_info.st_mode),
            files=files,
            directories=directories,
            tree_sha256=tree_sha256,
        )
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


class _RecursiveInotify:
    def __init__(self, root: Path) -> None:
        if os.name != "posix" or not hasattr(os, "O_NONBLOCK"):
            raise DestructiveActionGuardError("Linux inotify is required")
        libc = ctypes.CDLL(None, use_errno=True)
        self._inotify_add_watch = libc.inotify_add_watch
        self._inotify_add_watch.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32)
        self._inotify_add_watch.restype = ctypes.c_int
        self._inotify_init1 = libc.inotify_init1
        self._inotify_init1.argtypes = (ctypes.c_int,)
        self._inotify_init1.restype = ctypes.c_int
        descriptor = self._inotify_init1(os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
        if descriptor < 0:
            error = ctypes.get_errno()
            raise DestructiveActionGuardError(f"inotify initialization failed: errno_{error}")
        self.fd = descriptor
        self.root = root
        self.by_watch: dict[int, Path] = {}
        self._poll = select.poll()
        self._poll.register(self.fd, select.POLLIN | select.POLLERR | select.POLLHUP)

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1
            self.by_watch.clear()

    def _add_directory(self, path: Path, relative: Path, *, required: bool) -> None:
        try:
            info = path.lstat()
        except FileNotFoundError as exc:
            if required:
                raise DestructiveActionGuardError("analysis directory disappeared while watching") from exc
            return
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            if required:
                raise DestructiveActionGuardError("inotify target is not a safe directory")
            return
        watch = self._inotify_add_watch(self.fd, os.fsencode(path), INOTIFY_WATCH_MASK)
        if watch < 0:
            error = ctypes.get_errno()
            if not required and error in {errno.ENOENT, errno.ENOTDIR}:
                return
            raise DestructiveActionGuardError(f"inotify watch failed: errno_{error}")
        self.by_watch[watch] = relative

    def add_tree(self, path: Path, relative: Path = Path(), *, required: bool = True) -> None:
        stack = [(path, relative)]
        while stack:
            current, current_relative = stack.pop()
            self._add_directory(current, current_relative, required=required)
            try:
                children = list(current.iterdir())
            except (FileNotFoundError, NotADirectoryError) as exc:
                if required:
                    raise DestructiveActionGuardError("analysis tree changed while adding watches") from exc
                continue
            for child in children:
                try:
                    mode = child.lstat().st_mode
                except FileNotFoundError:
                    continue
                if stat.S_ISDIR(mode) and not stat.S_ISLNK(mode):
                    stack.append((child, current_relative / child.name))

    def read(self, timeout_seconds: float | None) -> list[InotifyEvent]:
        if self.fd < 0:
            raise DestructiveActionGuardError("inotify watcher is closed")
        timeout_ms = -1 if timeout_seconds is None else max(0, int(timeout_seconds * 1000))
        ready = self._poll.poll(timeout_ms)
        if not ready:
            return []
        if any(mask & (select.POLLERR | select.POLLHUP) for _, mask in ready):
            raise DestructiveActionGuardError("inotify descriptor failed")
        events: list[InotifyEvent] = []
        while True:
            try:
                content = os.read(self.fd, 1024 * 1024)
            except BlockingIOError:
                break
            if not content:
                break
            offset = 0
            while offset + INOTIFY_EVENT.size <= len(content):
                watch, mask, cookie, name_length = INOTIFY_EVENT.unpack_from(content, offset)
                offset += INOTIFY_EVENT.size
                end = offset + name_length
                if end > len(content):
                    raise DestructiveActionGuardError("truncated inotify event")
                raw_name = content[offset:end].split(b"\0", 1)[0]
                offset = end
                events.append(
                    InotifyEvent(
                        watch_descriptor=watch,
                        mask=mask,
                        cookie=cookie,
                        name=os.fsdecode(raw_name),
                    )
                )
        return events


def _threshold_reason(*, deleted_count: int, baseline_count: int) -> str | None:
    if deleted_count > MAX_ABSOLUTE_DELETIONS:
        return "deletion_count_gt_500"
    if (
        deleted_count >= MIN_RATIO_DELETIONS
        and baseline_count > 0
        and deleted_count * RATIO_DENOMINATOR >= baseline_count * RATIO_NUMERATOR
    ):
        return "deletion_ratio_threshold"
    return None


def _sha256_regular_file(path: Path) -> tuple[int, str]:
    expected = path.lstat()
    if not stat.S_ISREG(expected.st_mode) or expected.st_nlink != 1:
        raise DestructiveActionGuardError("recovery target must be a single-link regular file")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    byte_count = 0
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino)
        ):
            raise DestructiveActionGuardError("recovery file changed while opening")
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
            byte_count += len(chunk)
    finally:
        os.close(descriptor)
    return byte_count, digest.hexdigest()


def _restore_file_atomic(source: Path, destination: Path, expected: SnapshotFile) -> None:
    source_size, source_sha256 = _sha256_regular_file(source)
    if source_size != expected.byte_count or source_sha256 != expected.sha256:
        raise DestructiveActionGuardError("deletion snapshot content failed integrity validation")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.restore-", dir=destination.parent)
    temporary = Path(temporary_name)
    source_fd = -1
    digest = hashlib.sha256()
    byte_count = 0
    try:
        os.fchmod(descriptor, expected.mode & 0o777)
        source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        while chunk := os.read(source_fd, 1024 * 1024):
            digest.update(chunk)
            byte_count += len(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise DestructiveActionGuardError("short recovery write")
                view = view[written:]
        os.fsync(descriptor)
        if byte_count != expected.byte_count or digest.hexdigest() != expected.sha256:
            raise DestructiveActionGuardError("staged recovery content failed integrity validation")
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
    finally:
        if source_fd >= 0:
            os.close(source_fd)
        os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _snapshot_relative(value: Any, *, code: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise DestructiveActionGuardError(code)
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise DestructiveActionGuardError(code)
    return relative.as_posix()


def _load_deletion_snapshot(
    *,
    project_root: Path,
    analysis_root: Path,
    snapshot_path: Path,
) -> DeletionSnapshot:
    """Load and verify a previously published snapshot for crash recovery."""

    project_root = _absolute_normalized(project_root, label="project root")
    analysis_root = _absolute_normalized(analysis_root, label="analysis root")
    analysis_relative = _validate_analysis_root(project_root, analysis_root)
    snapshot_root = _managed_snapshot_root(project_root)
    snapshot_path = _absolute_normalized(snapshot_path, label="snapshot path")
    try:
        snapshot_path.relative_to(snapshot_root)
    except ValueError as exc:
        raise DestructiveActionGuardError("snapshot_path_outside_managed_root") from exc
    _assert_no_symlink_components(snapshot_path, label="snapshot path")
    manifest_path = snapshot_path / "snapshot-manifest.json"
    try:
        info = manifest_path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > 16 * 1024 * 1024
        ):
            raise DestructiveActionGuardError("snapshot_manifest_unsafe")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except DestructiveActionGuardError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DestructiveActionGuardError("snapshot_manifest_invalid") from exc
    if not isinstance(manifest, dict):
        raise DestructiveActionGuardError("snapshot_manifest_invalid")
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("profile") != PROFILE
        or manifest.get("snapshot_kind") != "task_analysis_deletion_recovery"
        or manifest.get("analysis_relative_path") != analysis_relative
        or manifest.get("safeguards")
        != {
            "credentials_copied": False,
            "symlinks_allowed": False,
            "hardlinks_allowed": False,
            "special_files_allowed": False,
            "ephemeral_directories_copied": False,
        }
    ):
        raise DestructiveActionGuardError("snapshot_manifest_identity_invalid")
    root_mode = manifest.get("root_mode")
    tree_sha = manifest.get("tree_sha256")
    if isinstance(root_mode, bool) or not isinstance(root_mode, int) or not 0 <= root_mode <= 0o777:
        raise DestructiveActionGuardError("snapshot_manifest_invalid")
    if not isinstance(tree_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", tree_sha):
        raise DestructiveActionGuardError("snapshot_manifest_invalid")
    files: dict[str, SnapshotFile] = {}
    raw_files = manifest.get("files")
    raw_directories = manifest.get("directories")
    if not isinstance(raw_files, list) or not isinstance(raw_directories, list):
        raise DestructiveActionGuardError("snapshot_manifest_invalid")
    directories: dict[str, SnapshotDirectory] = {}
    for item in raw_directories:
        if not isinstance(item, dict) or set(item) != {"path", "mode"}:
            raise DestructiveActionGuardError("snapshot_manifest_invalid")
        relative = _snapshot_relative(item.get("path"), code="snapshot_manifest_invalid")
        mode = item.get("mode")
        if relative in directories or isinstance(mode, bool) or not isinstance(mode, int) or not 0 <= mode <= 0o777:
            raise DestructiveActionGuardError("snapshot_manifest_invalid")
        directories[relative] = SnapshotDirectory(relative_path=relative, mode=mode)
    for item in raw_files:
        if not isinstance(item, dict) or set(item) != {"path", "byte_count", "sha256", "mode", "source_mtime_ns"}:
            raise DestructiveActionGuardError("snapshot_manifest_invalid")
        relative = _snapshot_relative(item.get("path"), code="snapshot_manifest_invalid")
        byte_count = item.get("byte_count")
        mode = item.get("mode")
        source_mtime_ns = item.get("source_mtime_ns")
        sha256 = item.get("sha256")
        if (
            relative in files
            or isinstance(byte_count, bool)
            or not isinstance(byte_count, int)
            or byte_count < 0
            or isinstance(mode, bool)
            or not isinstance(mode, int)
            or not 0 <= mode <= 0o777
            or isinstance(source_mtime_ns, bool)
            or not isinstance(source_mtime_ns, int)
            or source_mtime_ns < 0
            or not isinstance(sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", sha256)
        ):
            raise DestructiveActionGuardError("snapshot_manifest_invalid")
        source = snapshot_path / "files" / relative
        _assert_no_symlink_components(source, label="snapshot file")
        try:
            source_info = source.lstat()
        except OSError as exc:
            raise DestructiveActionGuardError("snapshot_file_missing") from exc
        if (
            not stat.S_ISREG(source_info.st_mode)
            or source_info.st_uid != os.geteuid()
            or source_info.st_nlink != 1
            or stat.S_IMODE(source_info.st_mode) != 0o600
            or source_info.st_size != byte_count
        ):
            raise DestructiveActionGuardError("snapshot_file_unsafe")
        files[relative] = SnapshotFile(
            relative_path=relative,
            byte_count=byte_count,
            sha256=sha256,
            mode=mode,
            source_mtime_ns=source_mtime_ns,
        )
    if _tree_sha256(files, directories) != tree_sha:
        raise DestructiveActionGuardError("snapshot_tree_digest_mismatch")
    return DeletionSnapshot(
        path=snapshot_path,
        analysis_relative_path=analysis_relative,
        root_mode=root_mode,
        files=files,
        directories=directories,
        tree_sha256=tree_sha,
    )


def restore_deletion_snapshot(
    *,
    project_root: Path,
    analysis_root: Path,
    snapshot_path: Path,
    observed_paths: Sequence[str] | None = None,
) -> int:
    """Restore deleted baseline files after a worker or terminator crash."""

    snapshot = _load_deletion_snapshot(
        project_root=project_root,
        analysis_root=analysis_root,
        snapshot_path=snapshot_path,
    )
    guard = object.__new__(DestructiveActionGuard)
    guard.project_root = _absolute_normalized(project_root, label="project root")
    guard.analysis_root = _absolute_normalized(analysis_root, label="analysis root")
    guard.analysis_relative_path = snapshot.analysis_relative_path
    guard.snapshot = snapshot
    guard._watcher = None
    guard._lock_fd = -1
    guard._observed_deleted = set(snapshot.files)
    guard._triggered = True
    if observed_paths is not None:
        normalized: set[str] = set()
        for value in observed_paths:
            normalized.add(_snapshot_relative(value, code="guard_observed_paths_invalid"))
        if not normalized.issubset(snapshot.files):
            raise DestructiveActionGuardError("guard_observed_paths_invalid")
        guard._observed_deleted = normalized
    guard._acquire_lock()
    try:
        return guard._restore()
    finally:
        guard.close()


class DestructiveActionGuard:
    """Snapshot and monitor exactly one company's task analysis bind root."""

    def __init__(
        self,
        *,
        project_root: Path,
        analysis_root: Path,
        audit_context: SecurityRunContext,
        terminator: SandboxTerminator,
        before_terminate: Callable[[str, tuple[str, ...]], None] | None = None,
    ) -> None:
        if not isinstance(terminator, SandboxTerminator):
            raise DestructiveActionGuardError("terminator must implement SandboxTerminator")
        audit_context.validate()
        if audit_context.profile != PROFILE:
            raise DestructiveActionGuardError("deletion guard only supports siq_analysis")
        if not RUN_ID_RE.fullmatch(audit_context.run_id):
            raise DestructiveActionGuardError("run id is unsafe for the fixed snapshot path")
        self.project_root = _absolute_normalized(project_root, label="project root")
        self.analysis_root = _absolute_normalized(analysis_root, label="analysis root")
        self.analysis_relative_path = _validate_analysis_root(self.project_root, self.analysis_root)
        self.audit_context = audit_context
        self.terminator = terminator
        self.before_terminate = before_terminate
        self.snapshot: DeletionSnapshot | None = None
        self._watcher: _RecursiveInotify | None = None
        self._lock_fd = -1
        self._observed_deleted: set[str] = set()
        self._triggered = False

    def __enter__(self) -> DestructiveActionGuard:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._watcher is not None:
            self._watcher.close()
            self._watcher = None
        if self._lock_fd >= 0:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(self._lock_fd)
                self._lock_fd = -1

    def _acquire_lock(self) -> None:
        snapshot_root = _managed_snapshot_root(self.project_root)
        lock_root = snapshot_root / ".locks"
        _mkdir_checked(lock_root, private=True)
        scope = hashlib.sha256(self.analysis_relative_path.encode()).hexdigest()[:24]
        lock_path = lock_root / f"{scope}.lock"
        descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid():
                raise DestructiveActionGuardError("deletion guard lock is unsafe")
            os.fchmod(descriptor, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise DestructiveActionGuardError("another deletion guard already owns this analysis root") from exc
        except BaseException:
            os.close(descriptor)
            raise
        self._lock_fd = descriptor

    def prepare(self) -> DeletionSnapshot:
        if self.snapshot is not None or self._watcher is not None:
            raise DestructiveActionGuardError("deletion guard is already prepared")
        self._acquire_lock()
        try:
            self.snapshot = _create_snapshot(
                project_root=self.project_root,
                analysis_root=self.analysis_root,
                analysis_relative_path=self.analysis_relative_path,
                run_id=self.audit_context.run_id,
            )
            self._watcher = _RecursiveInotify(self.analysis_root)
            self._watcher.add_tree(self.analysis_root, required=True)
            for relative, expected in self.snapshot.files.items():
                current = self.analysis_root / relative
                info = current.lstat()
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or info.st_size != expected.byte_count
                    or info.st_mtime_ns != expected.source_mtime_ns
                ):
                    raise DestructiveActionGuardError("analysis root changed during deletion guard preparation")
            return self.snapshot
        except BaseException:
            self.close()
            raise

    def _mark_baseline_prefix(self, relative: str) -> None:
        if self.snapshot is None:
            raise DestructiveActionGuardError("deletion guard is not prepared")
        prefix = relative.rstrip("/")
        for candidate in self.snapshot.files:
            if not prefix or candidate == prefix or candidate.startswith(f"{prefix}/"):
                self._observed_deleted.add(candidate)

    def _process_events(self, events: Sequence[InotifyEvent]) -> str | None:
        if self.snapshot is None or self._watcher is None:
            raise DestructiveActionGuardError("deletion guard is not prepared")
        for event in events:
            if event.mask & IN_Q_OVERFLOW:
                return "inotify_queue_overflow"
            watched_relative = self._watcher.by_watch.get(event.watch_descriptor)
            if watched_relative is None:
                continue
            event_relative = watched_relative / event.name if event.name else watched_relative
            event_key = event_relative.as_posix() if event_relative.parts else ""

            if event.mask & (IN_CREATE | IN_MOVED_TO) and event.mask & IN_ISDIR:
                self._watcher.add_tree(
                    self.analysis_root / event_relative,
                    event_relative,
                    required=False,
                )
            if event.mask & (IN_DELETE | IN_MOVED_FROM):
                self._mark_baseline_prefix(event_key)
            if event.mask & (IN_DELETE_SELF | IN_MOVE_SELF):
                if not watched_relative.parts:
                    return "analysis_root_self_deleted"
                self._mark_baseline_prefix(watched_relative.as_posix())
            if event.mask & IN_IGNORED:
                self._watcher.by_watch.pop(event.watch_descriptor, None)

            reason = _threshold_reason(
                deleted_count=len(self._observed_deleted),
                baseline_count=len(self.snapshot.files),
            )
            if reason is not None:
                return reason
        return None

    def _ensure_restore_directories(self) -> None:
        if self.snapshot is None:
            raise DestructiveActionGuardError("deletion guard is not prepared")
        parent = self.analysis_root.parent
        _require_directory(parent, label="analysis company parent")
        try:
            root_info = self.analysis_root.lstat()
        except FileNotFoundError:
            self.analysis_root.mkdir(mode=self.snapshot.root_mode & 0o777)
            _fsync_directory(parent)
        else:
            if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
                raise DestructiveActionGuardError("analysis root recovery target is unsafe")

        for relative, directory in sorted(self.snapshot.directories.items(), key=lambda item: len(Path(item[0]).parts)):
            target = self.analysis_root / relative
            try:
                info = target.lstat()
            except FileNotFoundError:
                target.mkdir(mode=directory.mode & 0o777)
                _fsync_directory(target.parent)
                continue
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise DestructiveActionGuardError("recovery directory target is unsafe")

    def _restore(self) -> int:
        if self.snapshot is None:
            raise DestructiveActionGuardError("deletion guard is not prepared")
        self._ensure_restore_directories()
        restored = 0
        for relative, expected in sorted(self.snapshot.files.items()):
            target = self.analysis_root / relative
            restore = False
            try:
                info = target.lstat()
            except FileNotFoundError:
                restore = True
            else:
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise DestructiveActionGuardError("recovery file target is unsafe")
                if relative in self._observed_deleted:
                    size, digest = _sha256_regular_file(target)
                    restore = (
                        size != expected.byte_count
                        or digest != expected.sha256
                        or stat.S_IMODE(info.st_mode) != expected.mode
                    )
            if not restore:
                continue
            source = self.snapshot.path / "files" / relative
            _restore_file_atomic(source, target, expected)
            restored += 1
        return restored

    def _write_audit(self, *, reason_code: str, started: float) -> Path:
        duration_ms = min(86_400_000, max(0, int((time.monotonic() - started) * 1000)))
        record = build_record(
            context=self.audit_context,
            operation_class="filesystem.delete",
            target=project_target(
                kind="path",
                scope=AUDIT_TARGET_SCOPE,
                value=self.analysis_relative_path,
            ),
            decision="deny",
            error_code=reason_code,
            duration_ms=duration_ms,
        )
        return append_record(project_root=self.project_root, record=record)

    def _respond_to_trigger(self, reason_code: str) -> GuardResult:
        if self.snapshot is None:
            raise DestructiveActionGuardError("deletion guard is not prepared")
        if self._triggered:
            raise DestructiveActionGuardError("deletion guard has already triggered")
        self._triggered = True
        started = time.monotonic()
        # Persist the trigger intent before touching the sandbox.  The
        # lifecycle can recover from a worker crash between fencing and
        # writing the final outcome only when this hook is durable.
        trigger_intent_failed = False
        if self.before_terminate is not None:
            try:
                self.before_terminate(reason_code, tuple(sorted(self._observed_deleted)))
            except BaseException:
                trigger_intent_failed = True
                try:
                    self._write_audit(reason_code="guard_trigger_intent_failed", started=started)
                except BaseException:
                    pass
        try:
            self.terminator.terminate(
                sandbox_id=self.audit_context.sandbox_id,
                reason_code=reason_code,
            )
        except BaseException as exc:
            try:
                self._write_audit(reason_code="sandbox_termination_failed", started=started)
            except BaseException:
                pass
            raise DestructiveActionGuardError("sandbox termination failed") from exc
        try:
            restored = self._restore()
        except BaseException as exc:
            try:
                self._write_audit(reason_code="deletion_recovery_failed", started=started)
            except BaseException:
                pass
            raise DestructiveActionGuardError("deletion recovery failed") from exc
        audit_path = self._write_audit(reason_code=reason_code, started=started)
        if trigger_intent_failed:
            raise DestructiveActionGuardError("guard trigger intent failed")
        return GuardResult(
            triggered=True,
            reason_code=reason_code,
            baseline_file_count=len(self.snapshot.files),
            observed_deleted_file_count=len(self._observed_deleted),
            restored_file_count=restored,
            snapshot_path=self.snapshot.path,
            audit_path=audit_path,
            deleted_paths=tuple(sorted(self._observed_deleted)),
        )

    def monitor(self, *, timeout_seconds: float | None = None) -> GuardResult:
        if self.snapshot is None or self._watcher is None:
            raise DestructiveActionGuardError("deletion guard is not prepared")
        if timeout_seconds is not None and timeout_seconds < 0:
            raise DestructiveActionGuardError("monitor timeout must be non-negative")
        deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
        while True:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            try:
                events = self._watcher.read(remaining)
            except DestructiveActionGuardError:
                return self._respond_to_trigger("inotify_monitor_failure")
            if not events:
                return GuardResult(
                    triggered=False,
                    reason_code="",
                    baseline_file_count=len(self.snapshot.files),
                    observed_deleted_file_count=len(self._observed_deleted),
                    restored_file_count=0,
                    snapshot_path=self.snapshot.path,
                    audit_path=None,
                )
            reason = self._process_events(events)
            if reason is not None:
                return self._respond_to_trigger(reason)
            if deadline is not None and time.monotonic() >= deadline:
                return GuardResult(
                    triggered=False,
                    reason_code="",
                    baseline_file_count=len(self.snapshot.files),
                    observed_deleted_file_count=len(self._observed_deleted),
                    restored_file_count=0,
                    snapshot_path=self.snapshot.path,
                    audit_path=None,
                )


__all__ = [
    "DestructiveActionGuard",
    "DestructiveActionGuardError",
    "GuardResult",
    "InotifyEvent",
    "SandboxTerminator",
    "restore_deletion_snapshot",
]
