#!/usr/bin/env python3
"""Durable transaction state for one formal SIQ analysis lifecycle run."""

from __future__ import annotations

import copy
import ctypes
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator, Mapping

JOURNAL_SCHEMA = "siq.openshell.siq_analysis_transaction.v2"
ACTIVE_SCHEMA = "siq.openshell.siq_analysis_active_transaction.v2"
LEGACY_JOURNAL_SCHEMA = "siq.openshell.siq_analysis_transaction.v1"
LEGACY_ACTIVE_SCHEMA = "siq.openshell.siq_analysis_active_run.v1"
PROFILE = "siq_analysis"
NAMESPACE = "siq-openshell-dev"
STATE_RELATIVE = Path("var/openshell/siq-analysis")
TRANSACTIONS_RELATIVE = STATE_RELATIVE / "transactions"
ACTIVE_RELATIVE = STATE_RELATIVE / "active-run.json"
LOCK_RELATIVE = STATE_RELATIVE / ".transaction.lock"
MAX_STATE_BYTES = 1024 * 1024
AT_EMPTY_PATH = 0x1000
RENAME_NOREPLACE = 0x1
RENAME_EXCHANGE = 0x2
RETIRED_DIRECTORY = ".retired"
RETIRED_NAME_RE = re.compile(r"retired-[0-9a-f]{32}\.state\Z")
_LIBC = ctypes.CDLL(None, use_errno=True)

IDENTIFIER_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
RUN_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,47}\Z")
RESOURCE_RE = re.compile(r"[a-z][a-z0-9_]{0,47}\Z")
ERROR_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,95}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
TEMPORARY_RE = re.compile(r"\.[a-z0-9][a-z0-9_.-]{0,127}\.json\.[0-9a-f]{16}\.tmp\Z")

MARKETS = frozenset({"cn", "eu", "hk", "jp", "kr", "us"})
FORMAL_RESOURCES: Mapping[str, str] = MappingProxyType(
    {
        "run_dir": "directory",
        "guard": "process",
        "secrets": "secrets",
        "sandbox": "sandbox",
        "forward": "process",
    }
)
RESOURCE_DISPOSITIONS: Mapping[str, str] = MappingProxyType(
    {
        "run_dir": "retain",
        "guard": "remove",
        "secrets": "remove",
        "sandbox": "remove",
        "forward": "remove",
    }
)

JOURNAL_FIELDS = {
    "schema_version",
    "transaction_id",
    "phase",
    "generation",
    "intent",
    "resources",
    "created_at",
    "updated_at",
    "error_code",
    "terminal_action",
}
INTENT_FIELDS = {
    "profile",
    "run_id",
    "market",
    "company",
    "run_dir",
    "sandbox_name",
    "namespace",
}
RESOURCE_FIELDS = {
    "kind",
    "disposition",
    "state",
    "generation",
    "intent_sha256",
    "receipt_sha256",
    "updated_at",
}
ACTIVE_FIELDS = {"schema_version", "transaction_id", "run_id", "journal", "created_at"}

PHASE_TRANSITIONS = {
    "intent": frozenset({"starting"}),
    "starting": frozenset({"running", "rollback_pending"}),
    "running": frozenset({"stopping"}),
    "stopping": frozenset({"stopped"}),
    "rollback_pending": frozenset({"rolled_back"}),
    "stopped": frozenset(),
    "rolled_back": frozenset(),
}
TERMINAL_PHASES = frozenset({"stopped", "rolled_back"})
TERMINAL_ACTIONS = frozenset({"stop", "rollback_to_host", "failed_start"})
RESOURCE_TRANSITIONS = {
    "pending": frozenset({"removing"}),
    "present": frozenset({"removing"}),
    "removing": frozenset({"removed"}),
    "removed": frozenset(),
}


class TransactionError(RuntimeError):
    """Stable, value-free transaction failure."""

    def __init__(self, code: str) -> None:
        self.code = code if ERROR_RE.fullmatch(code) else "transaction_error"
        super().__init__(self.code)


@dataclass(frozen=True)
class RecoveryDiscovery:
    transaction: dict[str, Any] | None
    has_active_pointer: bool
    orphaned: bool
    terminal_pending_finalize: bool


@dataclass(frozen=True)
class _LockedState:
    state_root: Path
    transactions: Path
    state_descriptor: int
    transactions_descriptor: int


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _path_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise TransactionError("state_io_failed") from exc
    return True


def _path_exists_at(path: Path, parent_descriptor: int | None = None) -> bool:
    if parent_descriptor is None:
        return _path_exists(path)
    try:
        os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise TransactionError("state_io_failed") from exc
    return True


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise TransactionError("directory_fsync_failed") from exc


def _open_directory_anchor(path: Path) -> int:
    try:
        expected = path.lstat()
        if stat.S_ISLNK(expected.st_mode) or not stat.S_ISDIR(expected.st_mode):
            raise TransactionError("state_directory_invalid")
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
            os.close(descriptor)
            raise TransactionError("state_directory_changed")
        return descriptor
    except TransactionError:
        raise
    except OSError as exc:
        raise TransactionError("state_directory_invalid") from exc


def _fsync_directory_descriptor(descriptor: int) -> None:
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise TransactionError("directory_fsync_failed") from exc


def _project_root(project_root: Path) -> Path:
    try:
        absolute = project_root.absolute()
        resolved = project_root.resolve(strict=True)
        info = project_root.lstat()
    except OSError as exc:
        raise TransactionError("project_root_invalid") from exc
    if resolved != absolute or stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise TransactionError("project_root_invalid")
    return resolved


def _require_owned_directory(path: Path, *, private: bool) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise TransactionError("state_directory_invalid") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise TransactionError("state_directory_invalid")
    if private and stat.S_IMODE(info.st_mode) != 0o700:
        raise TransactionError("state_directory_permissions")


def _ensure_directory(path: Path, *, parent: Path, private: bool = True) -> None:
    if not _path_exists(path):
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise TransactionError("state_directory_create_failed") from exc
        else:
            _fsync_directory(parent)
    _require_owned_directory(path, private=private)


def _ensure_layout(root: Path) -> tuple[Path, Path]:
    var = root / "var"
    _ensure_directory(var, parent=root, private=False)
    openshell = var / "openshell"
    _ensure_directory(openshell, parent=var)
    state_root = root / STATE_RELATIVE
    _ensure_directory(state_root, parent=openshell)
    transactions = root / TRANSACTIONS_RELATIVE
    _ensure_directory(transactions, parent=state_root)
    _ensure_directory(state_root / RETIRED_DIRECTORY, parent=state_root)
    _ensure_directory(transactions / RETIRED_DIRECTORY, parent=transactions)
    return state_root, transactions


def _require_private_file(path: Path, *, parent_descriptor: int | None = None) -> os.stat_result:
    try:
        info = (
            path.lstat()
            if parent_descriptor is None
            else os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        )
    except FileNotFoundError as exc:
        raise TransactionError("state_file_missing") from exc
    except OSError as exc:
        raise TransactionError("state_io_failed") from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_size <= 0
        or info.st_size > MAX_STATE_BYTES
    ):
        raise TransactionError("state_file_unsafe")
    return info


def _parent_anchor(path: Path, parent_descriptor: int | None) -> int:
    if parent_descriptor is None:
        return _open_directory_anchor(path.parent)
    try:
        duplicate = os.dup(parent_descriptor)
        info = os.fstat(duplicate)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            os.close(duplicate)
            raise TransactionError("state_directory_invalid")
        return duplicate
    except TransactionError:
        raise
    except OSError as exc:
        raise TransactionError("state_directory_invalid") from exc


def _read_json(path: Path, *, parent_descriptor: int | None = None) -> dict[str, Any]:
    expected = _require_private_file(path, parent_descriptor=parent_descriptor)
    parent_descriptor = _parent_anchor(path, parent_descriptor)
    try:
        descriptor = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        try:
            opened = os.fstat(descriptor)
            if not _same_snapshot(expected, opened):
                raise TransactionError("state_file_changed")
            content = b""
            while chunk := os.read(descriptor, min(64 * 1024, MAX_STATE_BYTES + 1 - len(content))):
                content += chunk
                if len(content) > MAX_STATE_BYTES:
                    raise TransactionError("state_file_unsafe")
            finished = os.fstat(descriptor)
            if not _same_snapshot(opened, finished):
                raise TransactionError("state_file_changed")
        finally:
            os.close(descriptor)
    except TransactionError:
        raise
    except OSError as exc:
        raise TransactionError("state_io_failed") from exc
    finally:
        os.close(parent_descriptor)
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransactionError("state_json_invalid") from exc
    if not isinstance(value, dict):
        raise TransactionError("state_json_invalid")
    return value


def _serialize(value: Mapping[str, Any]) -> bytes:
    try:
        return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TransactionError("state_json_invalid") from exc


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        try:
            written = os.write(descriptor, view)
        except OSError as exc:
            raise TransactionError("state_write_failed") from exc
        if written <= 0:
            raise TransactionError("state_write_failed")
        view = view[written:]
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise TransactionError("state_fsync_failed") from exc


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _same_snapshot(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        _same_inode(left, right)
        and left.st_mode == right.st_mode
        and left.st_uid == right.st_uid
        and left.st_nlink == right.st_nlink
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and left.st_ctime_ns == right.st_ctime_ns
    )


def _same_material(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        _same_inode(left, right)
        and left.st_mode == right.st_mode
        and left.st_uid == right.st_uid
        and left.st_nlink == right.st_nlink
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
    )


def _safe_state_stat(
    info: os.stat_result,
    *,
    link_counts: frozenset[int],
    allow_empty: bool = False,
) -> bool:
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_uid == os.geteuid()
        and stat.S_IMODE(info.st_mode) == 0o600
        and info.st_nlink in link_counts
        and (0 <= info.st_size <= MAX_STATE_BYTES if allow_empty else 0 < info.st_size <= MAX_STATE_BYTES)
    )


def _stage_json(
    parent_descriptor: int,
    target_name: str,
    value: Mapping[str, Any],
) -> tuple[str, int, os.stat_result, bytes]:
    temporary_name = f".{target_name}.{secrets.token_hex(8)}.tmp"
    content = _serialize(value)
    if not content or len(content) > MAX_STATE_BYTES:
        raise TransactionError("state_file_unsafe")
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(temporary_name, flags, 0o600, dir_fd=parent_descriptor)
    except OSError as exc:
        raise TransactionError("state_write_failed") from exc
    try:
        _write_all(descriptor, content)
        opened = os.fstat(descriptor)
        named = os.stat(temporary_name, dir_fd=parent_descriptor, follow_symlinks=False)
        if not _same_snapshot(opened, named) or not _safe_state_stat(opened, link_counts=frozenset({1})):
            raise TransactionError("temporary_state_changed")
        return temporary_name, descriptor, opened, content
    except BaseException:
        try:
            opened = os.fstat(descriptor)
        except OSError:
            opened = None
        os.close(descriptor)
        try:
            named = os.stat(temporary_name, dir_fd=parent_descriptor, follow_symlinks=False)
            if (
                opened is not None
                and _same_inode(opened, named)
                and stat.S_ISREG(named.st_mode)
                and named.st_uid == os.geteuid()
            ):
                _quarantine_path(
                    parent_descriptor,
                    temporary_name,
                    named,
                    error_code="state_unlink_failed",
                )
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise TransactionError("state_unlink_failed") from exc
        raise


def _verify_descriptor_content(descriptor: int, expected: bytes) -> None:
    try:
        info = os.fstat(descriptor)
        if info.st_size != len(expected):
            raise TransactionError("temporary_state_changed")
        offset = 0
        while offset < len(expected):
            chunk = os.pread(descriptor, min(64 * 1024, len(expected) - offset), offset)
            if not chunk or chunk != expected[offset : offset + len(chunk)]:
                raise TransactionError("temporary_state_changed")
            offset += len(chunk)
        if os.pread(descriptor, 1, len(expected)):
            raise TransactionError("temporary_state_changed")
    except TransactionError:
        raise
    except OSError as exc:
        raise TransactionError("temporary_state_changed") from exc


def _verify_installed_state(
    descriptor: int,
    parent_descriptor: int,
    target_name: str,
    staged: os.stat_result,
    content: bytes,
    *,
    link_counts: frozenset[int],
) -> None:
    opened = os.fstat(descriptor)
    named = os.stat(target_name, dir_fd=parent_descriptor, follow_symlinks=False)
    if (
        not _same_inode(staged, opened)
        or not _same_inode(opened, named)
        or not _safe_state_stat(opened, link_counts=link_counts)
        or not _safe_state_stat(named, link_counts=link_counts)
    ):
        raise TransactionError("state_file_changed")
    _verify_descriptor_content(descriptor, content)


def _descriptor_sha256(descriptor: int) -> bytes:
    digest = hashlib.sha256()
    offset = 0
    try:
        while chunk := os.pread(descriptor, 64 * 1024, offset):
            digest.update(chunk)
            offset += len(chunk)
            if offset > MAX_STATE_BYTES:
                raise TransactionError("state_file_unsafe")
    except TransactionError:
        raise
    except OSError as exc:
        raise TransactionError("state_io_failed") from exc
    return digest.digest()


def _link_descriptor_exclusive(descriptor: int, parent_descriptor: int, target_name: str) -> None:
    result = _LIBC.linkat(
        descriptor,
        ctypes.c_char_p(b""),
        parent_descriptor,
        ctypes.c_char_p(os.fsencode(target_name)),
        AT_EMPTY_PATH,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    raise OSError(error_number, os.strerror(error_number))


def _rename_exchange(parent_descriptor: int, left_name: str, right_name: str) -> None:
    result = _LIBC.renameat2(
        parent_descriptor,
        ctypes.c_char_p(os.fsencode(left_name)),
        parent_descriptor,
        ctypes.c_char_p(os.fsencode(right_name)),
        RENAME_EXCHANGE,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    raise OSError(error_number, os.strerror(error_number))


def _rename_noreplace(
    source_parent_descriptor: int,
    source_name: str,
    destination_parent_descriptor: int,
    destination_name: str,
) -> None:
    result = _LIBC.renameat2(
        source_parent_descriptor,
        ctypes.c_char_p(os.fsencode(source_name)),
        destination_parent_descriptor,
        ctypes.c_char_p(os.fsencode(destination_name)),
        RENAME_NOREPLACE,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    raise OSError(error_number, os.strerror(error_number))


def _open_retired_directory(parent_descriptor: int) -> int:
    try:
        descriptor = os.open(
            RETIRED_DIRECTORY,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            os.close(descriptor)
            raise TransactionError("state_directory_invalid")
        return descriptor
    except TransactionError:
        raise
    except OSError as exc:
        raise TransactionError("state_directory_invalid") from exc


def _quarantine_path(
    parent_descriptor: int,
    source_name: str,
    expected: os.stat_result,
    *,
    error_code: str,
    expected_digest: bytes | None = None,
) -> None:
    """Remove a private name without deleting an unverified inode.

    The source is atomically moved into a private retired directory. The
    retired copy is intentionally retained; deleting by pathname would reopen
    the same-UID rename race this helper is designed to close.
    """

    source_descriptor = -1
    retired_descriptor = -1
    try:
        try:
            source_descriptor = os.open(
                source_name,
                os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_descriptor,
            )
        except OSError as exc:
            raise TransactionError(error_code) from exc
        opened = os.fstat(source_descriptor)
        if (
            not _same_inode(expected, opened)
            or opened.st_mode != expected.st_mode
            or opened.st_uid != expected.st_uid
            or opened.st_size != expected.st_size
            or opened.st_mtime_ns != expected.st_mtime_ns
            or not _safe_state_stat(
                opened,
                link_counts=frozenset({1, 2, 3}),
                allow_empty=expected.st_size == 0,
            )
        ):
            raise TransactionError(error_code)
        digest = _descriptor_sha256(source_descriptor)
        if expected_digest is not None and digest != expected_digest:
            raise TransactionError(error_code)
        retired_descriptor = _open_retired_directory(parent_descriptor)
        retired_name = ""
        for _ in range(8):
            candidate = f"retired-{secrets.token_hex(16)}.state"
            try:
                _rename_noreplace(
                    parent_descriptor,
                    source_name,
                    retired_descriptor,
                    candidate,
                )
            except FileExistsError:
                continue
            retired_name = candidate
            break
        if not retired_name:
            raise TransactionError(error_code)
        moved_descriptor = -1
        try:
            moved_descriptor = os.open(
                retired_name,
                os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=retired_descriptor,
            )
            moved = os.fstat(moved_descriptor)
            if (
                not _same_inode(opened, moved)
                or moved.st_mode != opened.st_mode
                or moved.st_uid != opened.st_uid
                or moved.st_size != opened.st_size
                or moved.st_mtime_ns != opened.st_mtime_ns
                or not _safe_state_stat(
                    moved,
                    link_counts=frozenset({1, 2, 3}),
                    allow_empty=expected.st_size == 0,
                )
                or digest != _descriptor_sha256(moved_descriptor)
            ):
                raise TransactionError(error_code)
            _fsync_directory_descriptor(parent_descriptor)
            _fsync_directory_descriptor(retired_descriptor)
        finally:
            if moved_descriptor >= 0:
                os.close(moved_descriptor)
    finally:
        if retired_descriptor >= 0:
            os.close(retired_descriptor)
        if source_descriptor >= 0:
            os.close(source_descriptor)


def _unlink_staged(
    parent_descriptor: int,
    temporary_name: str,
    expected: os.stat_result,
) -> None:
    try:
        named = os.stat(temporary_name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (
            not _same_inode(expected, named)
            or not stat.S_ISREG(named.st_mode)
            or named.st_uid != os.geteuid()
            or named.st_nlink not in {1, 2}
            or stat.S_IMODE(named.st_mode) != 0o600
            or named.st_size > MAX_STATE_BYTES
        ):
            raise TransactionError("recovery_artifact_invalid")
        descriptor = os.open(
            temporary_name,
            os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        try:
            opened = os.fstat(descriptor)
            if not _same_inode(named, opened):
                raise TransactionError("recovery_artifact_invalid")
        finally:
            os.close(descriptor)
        os.unlink(temporary_name, dir_fd=parent_descriptor)
        _fsync_directory_descriptor(parent_descriptor)
    except FileNotFoundError:
        return
    except TransactionError:
        raise
    except OSError as exc:
        raise TransactionError("state_unlink_failed") from exc


def _write_exclusive_json(
    path: Path,
    value: Mapping[str, Any],
    *,
    conflict_code: str,
    parent_descriptor: int | None = None,
) -> None:
    parent_descriptor = _parent_anchor(path, parent_descriptor)
    temporary_name = ""
    descriptor = -1
    staged: os.stat_result | None = None
    content = b""
    installed = False
    verified = False
    try:
        temporary_name, descriptor, staged, content = _stage_json(parent_descriptor, path.name, value)
        _verify_descriptor_content(descriptor, content)
        try:
            _link_descriptor_exclusive(descriptor, parent_descriptor, path.name)
        except FileExistsError as exc:
            raise TransactionError(conflict_code) from exc
        except OSError as exc:
            raise TransactionError("state_install_failed") from exc
        installed = True
        _verify_installed_state(
            descriptor,
            parent_descriptor,
            path.name,
            staged,
            content,
            link_counts=frozenset({2}),
        )
        _unlink_staged(parent_descriptor, temporary_name, staged)
        temporary_name = ""
        _verify_installed_state(
            descriptor,
            parent_descriptor,
            path.name,
            staged,
            content,
            link_counts=frozenset({1}),
        )
        verified = True
    except TransactionError:
        raise
    except OSError as exc:
        raise TransactionError("state_install_failed") from exc
    finally:
        try:
            if installed and not verified and descriptor >= 0:
                try:
                    final = os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
                    opened = os.fstat(descriptor)
                    if _same_inode(opened, final):
                        _quarantine_path(
                            parent_descriptor,
                            path.name,
                            opened,
                            error_code="state_unlink_failed",
                        )
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise TransactionError("state_unlink_failed") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                if temporary_name and staged is not None:
                    _unlink_staged(parent_descriptor, temporary_name, staged)
            finally:
                os.close(parent_descriptor)


def _replace_json(
    path: Path,
    value: Mapping[str, Any],
    *,
    parent_descriptor: int | None = None,
) -> None:
    expected = _require_private_file(path, parent_descriptor=parent_descriptor)
    _replace_json_matching(path, value, expected, parent_descriptor=parent_descriptor)


def _replace_json_matching(
    path: Path,
    value: Mapping[str, Any],
    expected: os.stat_result,
    *,
    parent_descriptor: int | None = None,
) -> None:
    parent_descriptor = _parent_anchor(path, parent_descriptor)
    temporary_name = ""
    exchange_name = ""
    descriptor = -1
    target_descriptor = -1
    staged: os.stat_result | None = None
    target_opened: os.stat_result | None = None
    content = b""
    target_digest = b""
    exchanged = False
    exchange_verified = False
    try:
        current = os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        if not _same_snapshot(expected, current):
            raise TransactionError("state_file_changed")
        target_descriptor = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        target_opened = os.fstat(target_descriptor)
        if not _same_snapshot(expected, target_opened):
            raise TransactionError("state_file_changed")
        target_digest = _descriptor_sha256(target_descriptor)
        temporary_name, descriptor, staged, content = _stage_json(parent_descriptor, path.name, value)
        _verify_descriptor_content(descriptor, content)
        exchange_name = f".{path.name}.{secrets.token_hex(8)}.tmp"
        _link_descriptor_exclusive(descriptor, parent_descriptor, exchange_name)
        current = os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        exchange_source = os.stat(exchange_name, dir_fd=parent_descriptor, follow_symlinks=False)
        if not _same_snapshot(expected, current) or not _same_inode(staged, exchange_source):
            raise TransactionError("state_file_changed")
        try:
            _rename_exchange(parent_descriptor, exchange_name, path.name)
        except OSError as exc:
            raise TransactionError("state_replace_failed") from exc
        exchanged = True
        final = os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        displaced = os.stat(exchange_name, dir_fd=parent_descriptor, follow_symlinks=False)
        target_finished = os.fstat(target_descriptor)
        if (
            staged is None
            or target_opened is None
            or not _same_inode(staged, final)
            or not _same_inode(target_opened, displaced)
            or not _safe_state_stat(final, link_counts=frozenset({1, 2}))
            or not _safe_state_stat(
                displaced,
                link_counts=frozenset({1}),
                allow_empty=target_opened.st_size == 0,
            )
            or not _same_material(target_opened, target_finished)
            or target_digest != _descriptor_sha256(target_descriptor)
        ):
            raise TransactionError("state_file_changed")
        _verify_installed_state(
            descriptor,
            parent_descriptor,
            path.name,
            staged,
            content,
            link_counts=frozenset({2}),
        )
        _unlink_staged(parent_descriptor, temporary_name, staged)
        temporary_name = ""
        _verify_installed_state(
            descriptor,
            parent_descriptor,
            path.name,
            staged,
            content,
            link_counts=frozenset({1}),
        )
        _quarantine_path(
            parent_descriptor,
            exchange_name,
            target_opened,
            error_code="state_file_changed",
            expected_digest=target_digest,
        )
        exchange_name = ""
        exchange_verified = True
    except TransactionError:
        raise
    except OSError as exc:
        raise TransactionError("state_replace_failed") from exc
    finally:
        try:
            if exchanged and not exchange_verified and exchange_name:
                try:
                    final = os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
                    displaced = os.stat(exchange_name, dir_fd=parent_descriptor, follow_symlinks=False)
                    target_identity_matches = target_opened is not None and _same_inode(target_opened, displaced)
                    staged_identity_matches = staged is not None and _same_inode(staged, final)
                    if target_identity_matches and staged_identity_matches:
                        _rename_exchange(parent_descriptor, exchange_name, path.name)
                        exchanged = False
                        _fsync_directory_descriptor(parent_descriptor)
                    else:
                        raise TransactionError("state_replace_rollback_failed")
                except OSError as exc:
                    raise TransactionError("state_replace_rollback_failed") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if target_descriptor >= 0:
                os.close(target_descriptor)
            try:
                if temporary_name and staged is not None:
                    _unlink_staged(parent_descriptor, temporary_name, staged)
                if exchange_name and staged is not None and not exchanged:
                    _quarantine_path(
                        parent_descriptor,
                        exchange_name,
                        staged,
                        error_code="recovery_artifact_invalid",
                        expected_digest=hashlib.sha256(content).digest(),
                    )
            finally:
                os.close(parent_descriptor)


def _unlink_private(path: Path, *, parent_descriptor: int | None = None) -> None:
    expected = _require_private_file(path, parent_descriptor=parent_descriptor)
    parent_descriptor = _parent_anchor(path, parent_descriptor)
    try:
        current = os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        if not _same_snapshot(expected, current):
            raise TransactionError("state_file_changed")
        _quarantine_path(
            parent_descriptor,
            path.name,
            expected,
            error_code="state_unlink_failed",
        )
    except TransactionError:
        raise
    except OSError as exc:
        raise TransactionError("state_unlink_failed") from exc
    finally:
        os.close(parent_descriptor)


def _unlink_stale_temporary(path: Path, *, parent_descriptor: int | None = None) -> None:
    try:
        info = (
            path.lstat()
            if parent_descriptor is None
            else os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        )
    except OSError as exc:
        raise TransactionError("recovery_artifact_invalid") from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink not in {1, 2}
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_size > MAX_STATE_BYTES
    ):
        raise TransactionError("recovery_artifact_invalid")
    try:
        parent_descriptor = _parent_anchor(path, parent_descriptor)
        try:
            current = os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
            if not _same_snapshot(info, current):
                raise TransactionError("recovery_artifact_invalid")
            _unlink_staged(parent_descriptor, path.name, info)
        finally:
            os.close(parent_descriptor)
    except TransactionError:
        raise
    except OSError as exc:
        raise TransactionError("state_unlink_failed") from exc


@contextmanager
def _state_lock(root: Path) -> Iterator[_LockedState]:
    state_root, transactions = _ensure_layout(root)
    state_descriptor = _open_directory_anchor(state_root)
    state_info = os.fstat(state_descriptor)
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    created = False
    descriptor = -1
    transactions_descriptor = -1
    try:
        # The directory inode is the actual mutex. Replacing the visible lock
        # filename therefore cannot create a second independent flock.
        fcntl.flock(state_descriptor, fcntl.LOCK_EX)
        named_state = (root / STATE_RELATIVE).lstat()
        if not _same_inode(state_info, named_state):
            raise TransactionError("state_directory_changed")
        transactions_descriptor = _open_directory_anchor(transactions)
        transactions_info = os.fstat(transactions_descriptor)
        try:
            descriptor = os.open(
                LOCK_RELATIVE.name,
                flags | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=state_descriptor,
            )
            created = True
        except FileExistsError:
            descriptor = os.open(LOCK_RELATIVE.name, flags, dir_fd=state_descriptor)
        info = os.fstat(descriptor)
        named_lock = os.stat(LOCK_RELATIVE.name, dir_fd=state_descriptor, follow_symlinks=False)
        if (
            not _same_inode(info, named_lock)
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise TransactionError("transaction_lock_invalid")
        if created:
            os.fsync(descriptor)
            _fsync_directory_descriptor(state_descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        named_lock = os.stat(LOCK_RELATIVE.name, dir_fd=state_descriptor, follow_symlinks=False)
        named_transactions = transactions.lstat()
        if not _same_inode(info, named_lock) or not _same_inode(transactions_info, named_transactions):
            raise TransactionError("transaction_lock_changed")
        _clean_stale_temporaries(state_root, parent_descriptor=state_descriptor)
        _clean_stale_temporaries(
            transactions,
            parent_descriptor=transactions_descriptor,
        )
    except TransactionError:
        if descriptor >= 0:
            os.close(descriptor)
        if transactions_descriptor >= 0:
            os.close(transactions_descriptor)
        os.close(state_descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        if transactions_descriptor >= 0:
            os.close(transactions_descriptor)
        os.close(state_descriptor)
        code = "transaction_lock_invalid" if descriptor < 0 else "transaction_lock_failed"
        raise TransactionError(code) from exc
    try:
        yield _LockedState(
            state_root=state_root,
            transactions=transactions,
            state_descriptor=state_descriptor,
            transactions_descriptor=transactions_descriptor,
        )
        try:
            named_state = (root / STATE_RELATIVE).lstat()
            named_lock = os.stat(LOCK_RELATIVE.name, dir_fd=state_descriptor, follow_symlinks=False)
            named_transactions = transactions.lstat()
        except OSError as exc:
            raise TransactionError("transaction_lock_changed") from exc
        if (
            not _same_inode(state_info, named_state)
            or not _same_inode(info, named_lock)
            or not _same_inode(transactions_info, named_transactions)
        ):
            raise TransactionError("transaction_lock_changed")
    finally:
        os.close(descriptor)
        os.close(transactions_descriptor)
        os.close(state_descriptor)


def _validate_identifier(value: Any, *, code: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        raise TransactionError(code)
    return value


def _validate_run_id(value: Any, *, code: str) -> str:
    if not isinstance(value, str) or not RUN_ID_RE.fullmatch(value):
        raise TransactionError(code)
    return value


def _validate_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not TIMESTAMP_RE.fullmatch(value):
        raise TransactionError("transaction_timestamp_invalid")
    return value


def _validate_error_code(value: Any) -> str:
    if not isinstance(value, str) or (value and not ERROR_RE.fullmatch(value)):
        raise TransactionError("transaction_error_code_invalid")
    return value


def _validate_terminal_action(value: Any) -> str:
    if not isinstance(value, str) or (value and value not in TERMINAL_ACTIONS):
        raise TransactionError("terminal_action_invalid")
    return value


def _validate_sha256(value: Any, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str) or (not value and not allow_empty) or (value and not SHA256_RE.fullmatch(value)):
        raise TransactionError("resource_digest_invalid")
    return value


def _validate_intent(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != INTENT_FIELDS:
        raise TransactionError("transaction_intent_invalid")
    intent = {key: value.get(key) for key in INTENT_FIELDS}
    if not all(isinstance(item, str) for item in intent.values()):
        raise TransactionError("transaction_intent_invalid")
    run_id = _validate_run_id(intent["run_id"], code="run_id_invalid")
    company = intent["company"]
    if (
        intent["profile"] != PROFILE
        or intent["namespace"] != NAMESPACE
        or intent["market"] not in MARKETS
        or not company
        or len(company) > 128
        or company in {".", ".."}
        or any(character in "/\\\x00" or ord(character) < 32 for character in company)
        or intent["run_dir"] != (Path("var/openshell/siq-analysis/runs") / run_id).as_posix()
        or intent["sandbox_name"] != f"siq-analysis-{run_id}"
    ):
        raise TransactionError("transaction_intent_invalid")
    return intent


def _validate_resource_name(value: Any) -> str:
    if not isinstance(value, str) or not RESOURCE_RE.fullmatch(value):
        raise TransactionError("resource_name_invalid")
    return value


def _validate_journal(value: Any, *, expected_transaction_id: str | None = None) -> dict[str, Any]:
    if isinstance(value, dict) and value.get("schema_version") == LEGACY_JOURNAL_SCHEMA:
        raise TransactionError("legacy_state_migration_required")
    if not isinstance(value, dict) or set(value) != JOURNAL_FIELDS:
        raise TransactionError("transaction_journal_invalid")
    transaction_id = _validate_identifier(value.get("transaction_id"), code="transaction_id_invalid")
    if expected_transaction_id is not None and transaction_id != expected_transaction_id:
        raise TransactionError("transaction_journal_invalid")
    phase = value.get("phase")
    if value.get("schema_version") != JOURNAL_SCHEMA or not isinstance(phase, str) or phase not in PHASE_TRANSITIONS:
        raise TransactionError("transaction_journal_invalid")
    generation = value.get("generation")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation <= 0:
        raise TransactionError("transaction_generation_invalid")
    intent = _validate_intent(value.get("intent"))
    resources = value.get("resources")
    if not isinstance(resources, dict) or set(resources) != set(FORMAL_RESOURCES):
        raise TransactionError("transaction_resources_invalid")
    validated_resources: dict[str, dict[str, Any]] = {}
    for raw_name, raw_receipt in resources.items():
        name = _validate_resource_name(raw_name)
        if not isinstance(raw_receipt, dict) or set(raw_receipt) != RESOURCE_FIELDS:
            raise TransactionError("transaction_resources_invalid")
        state = raw_receipt.get("state")
        kind = raw_receipt.get("kind")
        disposition = raw_receipt.get("disposition")
        resource_generation = raw_receipt.get("generation")
        intent_sha256 = _validate_sha256(raw_receipt.get("intent_sha256"))
        receipt_sha256 = _validate_sha256(raw_receipt.get("receipt_sha256"))
        if (
            kind != FORMAL_RESOURCES[name]
            or disposition != RESOURCE_DISPOSITIONS[name]
            or not isinstance(state, str)
            or state not in RESOURCE_TRANSITIONS
            or isinstance(resource_generation, bool)
            or not isinstance(resource_generation, int)
            or resource_generation <= 0
            or resource_generation > generation
        ):
            raise TransactionError("transaction_resources_invalid")
        if (
            (state == "pending" and receipt_sha256)
            or (state == "present" and (not intent_sha256 or not receipt_sha256))
            or (receipt_sha256 and not intent_sha256)
            or (disposition == "retain" and state == "removed" and receipt_sha256)
        ):
            raise TransactionError("transaction_resources_invalid")
        validated_resources[name] = {
            "kind": kind,
            "disposition": disposition,
            "state": state,
            "generation": resource_generation,
            "intent_sha256": intent_sha256,
            "receipt_sha256": receipt_sha256,
            "updated_at": _validate_timestamp(raw_receipt.get("updated_at")),
        }
    terminal_action = _validate_terminal_action(value.get("terminal_action"))
    allowed_actions = {
        "intent": frozenset({""}),
        "starting": frozenset({"", "failed_start"}),
        "running": frozenset({"", "stop", "rollback_to_host"}),
        "stopping": frozenset({"stop", "rollback_to_host"}),
        "stopped": frozenset({"stop", "rollback_to_host"}),
        "rollback_pending": frozenset({"failed_start"}),
        "rolled_back": frozenset({"failed_start"}),
    }
    if terminal_action not in allowed_actions[phase]:
        raise TransactionError("terminal_action_invalid")
    semantic_record = {"resources": validated_resources}
    if phase == "running" and not _resources_present(semantic_record):
        raise TransactionError("transaction_resources_invalid")
    if phase in TERMINAL_PHASES and not _resources_terminal(semantic_record):
        raise TransactionError("transaction_resources_invalid")
    return {
        "schema_version": JOURNAL_SCHEMA,
        "transaction_id": transaction_id,
        "phase": phase,
        "generation": generation,
        "intent": intent,
        "resources": validated_resources,
        "created_at": _validate_timestamp(value.get("created_at")),
        "updated_at": _validate_timestamp(value.get("updated_at")),
        "error_code": _validate_error_code(value.get("error_code")),
        "terminal_action": terminal_action,
    }


def _validate_active(value: Any) -> dict[str, str]:
    if isinstance(value, dict) and value.get("schema_version") == LEGACY_ACTIVE_SCHEMA:
        raise TransactionError("legacy_state_migration_required")
    if isinstance(value, dict) and value.get("schema_version") not in {None, ACTIVE_SCHEMA}:
        raise TransactionError("active_pointer_schema_unsupported")
    if not isinstance(value, dict) or set(value) != ACTIVE_FIELDS or value.get("schema_version") != ACTIVE_SCHEMA:
        raise TransactionError("active_pointer_invalid")
    transaction_id = _validate_identifier(value.get("transaction_id"), code="active_pointer_invalid")
    run_id = _validate_run_id(value.get("run_id"), code="active_pointer_invalid")
    expected_journal = (TRANSACTIONS_RELATIVE / f"{transaction_id}.json").as_posix()
    if value.get("journal") != expected_journal:
        raise TransactionError("active_pointer_invalid")
    try:
        created_at = _validate_timestamp(value.get("created_at"))
    except TransactionError as exc:
        raise TransactionError("active_pointer_invalid") from exc
    return {
        "schema_version": ACTIVE_SCHEMA,
        "transaction_id": transaction_id,
        "run_id": run_id,
        "journal": expected_journal,
        "created_at": created_at,
    }


def _journal_path(root: Path, transaction_id: str) -> Path:
    return root / TRANSACTIONS_RELATIVE / f"{transaction_id}.json"


def _load_journal(
    root: Path,
    transaction_id: str,
    *,
    transaction_descriptor: int | None = None,
) -> dict[str, Any]:
    _validate_identifier(transaction_id, code="transaction_id_invalid")
    return _validate_journal(
        _read_json(_journal_path(root, transaction_id), parent_descriptor=transaction_descriptor),
        expected_transaction_id=transaction_id,
    )


def _load_active(root: Path, *, state_descriptor: int | None = None) -> dict[str, str] | None:
    path = root / ACTIVE_RELATIVE
    if not _path_exists_at(path, state_descriptor):
        return None
    return _validate_active(_read_json(path, parent_descriptor=state_descriptor))


def _active_pointer_for(journal: Mapping[str, Any]) -> dict[str, str]:
    transaction_id = journal["transaction_id"]
    return {
        "schema_version": ACTIVE_SCHEMA,
        "transaction_id": transaction_id,
        "run_id": journal["intent"]["run_id"],
        "journal": (TRANSACTIONS_RELATIVE / f"{transaction_id}.json").as_posix(),
        "created_at": journal["created_at"],
    }


def _active_matches_journal(active: Mapping[str, str], journal: Mapping[str, Any]) -> bool:
    return active == _active_pointer_for(journal)


def _resources_present(journal: Mapping[str, Any]) -> bool:
    return all(
        receipt["state"] == "present" and bool(receipt["intent_sha256"]) and bool(receipt["receipt_sha256"])
        for receipt in journal["resources"].values()
    )


def _resources_terminal(journal: Mapping[str, Any]) -> bool:
    return all(
        (receipt["disposition"] == "remove" and receipt["state"] == "removed")
        or (
            receipt["disposition"] == "retain"
            and receipt["state"] == "present"
            and bool(receipt["intent_sha256"])
            and bool(receipt["receipt_sha256"])
        )
        or (receipt["disposition"] == "retain" and receipt["state"] == "removed" and not receipt["receipt_sha256"])
        for receipt in journal["resources"].values()
    )


def create(
    project_root: Path,
    *,
    transaction_id: str,
    intent: Mapping[str, str],
    resources: Mapping[str, str],
) -> dict[str, Any]:
    """Create and durably activate one new transaction."""

    root = _project_root(project_root)
    transaction_id = _validate_identifier(transaction_id, code="transaction_id_invalid")
    try:
        validated_intent = _validate_intent(dict(intent))
    except (TypeError, ValueError) as exc:
        raise TransactionError("transaction_intent_invalid") from exc
    if not isinstance(resources, Mapping):
        raise TransactionError("transaction_resources_invalid")
    try:
        validated_resource_kinds = dict(resources)
    except (TypeError, ValueError) as exc:
        raise TransactionError("transaction_resources_invalid") from exc
    if validated_resource_kinds != dict(FORMAL_RESOURCES):
        raise TransactionError("transaction_resources_invalid")
    now = _utc_now()
    journal = {
        "schema_version": JOURNAL_SCHEMA,
        "transaction_id": transaction_id,
        "phase": "intent",
        "generation": 1,
        "intent": validated_intent,
        "resources": {
            name: {
                "kind": kind,
                "disposition": RESOURCE_DISPOSITIONS[name],
                "state": "pending",
                "generation": 1,
                "intent_sha256": "",
                "receipt_sha256": "",
                "updated_at": now,
            }
            for name, kind in FORMAL_RESOURCES.items()
        },
        "created_at": now,
        "updated_at": now,
        "error_code": "",
        "terminal_action": "",
    }
    pointer = _active_pointer_for(journal)
    with _state_lock(root) as locked:
        if _load_active(root, state_descriptor=locked.state_descriptor) is not None:
            raise TransactionError("active_run_conflict")
        journals = _scan_journals(
            root,
            locked.transactions,
            transaction_descriptor=locked.transactions_descriptor,
        )
        if any(record["phase"] not in TERMINAL_PHASES for record in journals.values()):
            raise TransactionError("recovery_required")
        journal_path = _journal_path(root, transaction_id)
        if _path_exists_at(journal_path, locked.transactions_descriptor):
            raise TransactionError("transaction_conflict")
        _write_exclusive_json(
            journal_path,
            journal,
            conflict_code="transaction_conflict",
            parent_descriptor=locked.transactions_descriptor,
        )
        active_path = root / ACTIVE_RELATIVE
        try:
            _write_exclusive_json(
                active_path,
                pointer,
                conflict_code="active_run_conflict",
                parent_descriptor=locked.state_descriptor,
            )
        except TransactionError as exc:
            if exc.code == "active_run_conflict" or not _path_exists_at(active_path, locked.state_descriptor):
                _unlink_private(
                    journal_path,
                    parent_descriptor=locked.transactions_descriptor,
                )
            raise
        return _load_journal(
            root,
            transaction_id,
            transaction_descriptor=locked.transactions_descriptor,
        )


def load(project_root: Path, transaction_id: str) -> dict[str, Any]:
    """Load and strictly validate one transaction journal."""

    root = _project_root(project_root)
    with _state_lock(root) as locked:
        return _load_journal(
            root,
            transaction_id,
            transaction_descriptor=locked.transactions_descriptor,
        )


def transition(
    project_root: Path,
    transaction_id: str,
    *,
    expected_generation: int,
    phase: str,
    error_code: str | None = None,
    terminal_action: str | None = None,
) -> dict[str, Any]:
    """Advance a transaction through the fixed phase graph."""

    root = _project_root(project_root)
    if not isinstance(phase, str) or phase not in PHASE_TRANSITIONS:
        raise TransactionError("phase_transition_invalid")
    if isinstance(expected_generation, bool) or not isinstance(expected_generation, int) or expected_generation <= 0:
        raise TransactionError("transaction_generation_conflict")
    if error_code is not None:
        _validate_error_code(error_code)
    if terminal_action is not None:
        _validate_terminal_action(terminal_action)
        if not terminal_action:
            raise TransactionError("terminal_action_invalid")
    with _state_lock(root) as locked:
        journal = _load_journal(
            root,
            transaction_id,
            transaction_descriptor=locked.transactions_descriptor,
        )
        if expected_generation != journal["generation"]:
            raise TransactionError("transaction_generation_conflict")
        if phase not in PHASE_TRANSITIONS[journal["phase"]]:
            raise TransactionError("phase_transition_invalid")
        updated = copy.deepcopy(journal)
        if terminal_action is not None:
            if journal["terminal_action"] or phase not in {"stopping", "rollback_pending"}:
                raise TransactionError("terminal_action_invalid")
            updated["terminal_action"] = terminal_action
        if phase == "running":
            if journal["terminal_action"] or not _resources_present(journal):
                raise TransactionError("transaction_resources_incomplete")
        elif phase == "stopping":
            if updated["terminal_action"] not in {"stop", "rollback_to_host"}:
                raise TransactionError("terminal_action_required")
        elif phase == "rollback_pending":
            if updated["terminal_action"] != "failed_start":
                raise TransactionError("terminal_action_required")
        elif phase in TERMINAL_PHASES and not _resources_terminal(journal):
            raise TransactionError("transaction_resources_incomplete")
        updated["generation"] += 1
        updated["phase"] = phase
        updated["updated_at"] = _utc_now()
        if error_code is not None:
            updated["error_code"] = error_code
        _replace_json(
            _journal_path(root, transaction_id),
            updated,
            parent_descriptor=locked.transactions_descriptor,
        )
        return _load_journal(
            root,
            transaction_id,
            transaction_descriptor=locked.transactions_descriptor,
        )


def set_terminal_action(
    project_root: Path,
    transaction_id: str,
    *,
    expected_generation: int,
    action: str,
) -> dict[str, Any]:
    """Record the one terminal operation before cleanup begins."""

    root = _project_root(project_root)
    action = _validate_terminal_action(action)
    if not action:
        raise TransactionError("terminal_action_invalid")
    if isinstance(expected_generation, bool) or not isinstance(expected_generation, int) or expected_generation <= 0:
        raise TransactionError("transaction_generation_conflict")
    with _state_lock(root) as locked:
        journal = _load_journal(
            root,
            transaction_id,
            transaction_descriptor=locked.transactions_descriptor,
        )
        if expected_generation != journal["generation"]:
            raise TransactionError("transaction_generation_conflict")
        allowed = {
            "starting": frozenset({"failed_start"}),
            "running": frozenset({"stop", "rollback_to_host"}),
        }
        if journal["terminal_action"] or action not in allowed.get(journal["phase"], frozenset()):
            raise TransactionError("terminal_action_invalid")
        updated = copy.deepcopy(journal)
        updated["generation"] += 1
        updated["terminal_action"] = action
        updated["updated_at"] = _utc_now()
        _replace_json(
            _journal_path(root, transaction_id),
            updated,
            parent_descriptor=locked.transactions_descriptor,
        )
        return _load_journal(
            root,
            transaction_id,
            transaction_descriptor=locked.transactions_descriptor,
        )


def bind_resource_intent(
    project_root: Path,
    transaction_id: str,
    *,
    expected_generation: int,
    resource: str,
    intent_sha256: str,
) -> dict[str, Any]:
    """Bind a pending resource to the exact acquisition intent."""

    root = _project_root(project_root)
    resource = _validate_resource_name(resource)
    intent_sha256 = _validate_sha256(intent_sha256, allow_empty=False)
    if isinstance(expected_generation, bool) or not isinstance(expected_generation, int) or expected_generation <= 0:
        raise TransactionError("transaction_generation_conflict")
    with _state_lock(root) as locked:
        journal = _load_journal(
            root,
            transaction_id,
            transaction_descriptor=locked.transactions_descriptor,
        )
        if expected_generation != journal["generation"]:
            raise TransactionError("transaction_generation_conflict")
        receipt = journal["resources"].get(resource)
        if (
            journal["phase"] != "starting"
            or receipt is None
            or receipt["state"] != "pending"
            or receipt["intent_sha256"]
            or receipt["receipt_sha256"]
        ):
            raise TransactionError("resource_transition_invalid")
        updated = copy.deepcopy(journal)
        updated["generation"] += 1
        updated["updated_at"] = _utc_now()
        updated_receipt = updated["resources"][resource]
        updated_receipt["intent_sha256"] = intent_sha256
        updated_receipt["generation"] = updated["generation"]
        updated_receipt["updated_at"] = updated["updated_at"]
        _replace_json(
            _journal_path(root, transaction_id),
            updated,
            parent_descriptor=locked.transactions_descriptor,
        )
        return _load_journal(
            root,
            transaction_id,
            transaction_descriptor=locked.transactions_descriptor,
        )


def commit_resource_present(
    project_root: Path,
    transaction_id: str,
    *,
    expected_generation: int,
    resource: str,
    receipt_sha256: str,
) -> dict[str, Any]:
    """Commit a durable receipt proving that a bound resource exists."""

    root = _project_root(project_root)
    resource = _validate_resource_name(resource)
    receipt_sha256 = _validate_sha256(receipt_sha256, allow_empty=False)
    if isinstance(expected_generation, bool) or not isinstance(expected_generation, int) or expected_generation <= 0:
        raise TransactionError("transaction_generation_conflict")
    with _state_lock(root) as locked:
        journal = _load_journal(
            root,
            transaction_id,
            transaction_descriptor=locked.transactions_descriptor,
        )
        if expected_generation != journal["generation"]:
            raise TransactionError("transaction_generation_conflict")
        receipt = journal["resources"].get(resource)
        if (
            journal["phase"] != "starting"
            or receipt is None
            or receipt["state"] != "pending"
            or not receipt["intent_sha256"]
            or receipt["receipt_sha256"]
        ):
            raise TransactionError("resource_transition_invalid")
        updated = copy.deepcopy(journal)
        updated["generation"] += 1
        updated["updated_at"] = _utc_now()
        updated_receipt = updated["resources"][resource]
        updated_receipt["state"] = "present"
        updated_receipt["receipt_sha256"] = receipt_sha256
        updated_receipt["generation"] = updated["generation"]
        updated_receipt["updated_at"] = updated["updated_at"]
        _replace_json(
            _journal_path(root, transaction_id),
            updated,
            parent_descriptor=locked.transactions_descriptor,
        )
        return _load_journal(
            root,
            transaction_id,
            transaction_descriptor=locked.transactions_descriptor,
        )


def update_resource(
    project_root: Path,
    transaction_id: str,
    *,
    expected_generation: int,
    resource: str,
    state: str,
) -> dict[str, Any]:
    """Advance one resource receipt and the journal generation atomically."""

    root = _project_root(project_root)
    resource = _validate_resource_name(resource)
    if not isinstance(state, str) or state not in RESOURCE_TRANSITIONS:
        raise TransactionError("resource_transition_invalid")
    if isinstance(expected_generation, bool) or not isinstance(expected_generation, int) or expected_generation <= 0:
        raise TransactionError("transaction_generation_conflict")
    with _state_lock(root) as locked:
        journal = _load_journal(
            root,
            transaction_id,
            transaction_descriptor=locked.transactions_descriptor,
        )
        if expected_generation != journal["generation"]:
            raise TransactionError("transaction_generation_conflict")
        if journal["phase"] not in {"stopping", "rollback_pending"} or resource not in journal["resources"]:
            raise TransactionError("resource_transition_invalid")
        if journal["resources"][resource]["disposition"] != "remove":
            if not (
                journal["phase"] == "rollback_pending"
                and journal["resources"][resource]["disposition"] == "retain"
                and journal["resources"][resource]["state"] in {"pending", "removing"}
            ):
                raise TransactionError("resource_transition_invalid")
        current = journal["resources"][resource]["state"]
        if state not in RESOURCE_TRANSITIONS[current]:
            raise TransactionError("resource_transition_invalid")
        updated = copy.deepcopy(journal)
        updated["generation"] += 1
        updated["updated_at"] = _utc_now()
        updated_receipt = updated["resources"][resource]
        updated_receipt["state"] = state
        updated_receipt["generation"] = updated["generation"]
        updated_receipt["updated_at"] = updated["updated_at"]
        _replace_json(
            _journal_path(root, transaction_id),
            updated,
            parent_descriptor=locked.transactions_descriptor,
        )
        return _load_journal(
            root,
            transaction_id,
            transaction_descriptor=locked.transactions_descriptor,
        )


def finalize(project_root: Path, transaction_id: str) -> dict[str, Any]:
    """Remove the active pointer only after a terminal journal is durable."""

    root = _project_root(project_root)
    with _state_lock(root) as locked:
        journal = _load_journal(
            root,
            transaction_id,
            transaction_descriptor=locked.transactions_descriptor,
        )
        if journal["phase"] not in TERMINAL_PHASES:
            raise TransactionError("transaction_not_terminal")
        if not _resources_terminal(journal):
            raise TransactionError("transaction_resources_incomplete")
        active = _load_active(root, state_descriptor=locked.state_descriptor)
        if active is None:
            return journal
        if not _active_matches_journal(active, journal):
            raise TransactionError("active_pointer_mismatch")
        _unlink_private(
            root / ACTIVE_RELATIVE,
            parent_descriptor=locked.state_descriptor,
        )
        return _load_journal(
            root,
            transaction_id,
            transaction_descriptor=locked.transactions_descriptor,
        )


def _clean_stale_temporaries(
    transactions: Path,
    *,
    parent_descriptor: int | None = None,
) -> None:
    try:
        names = (
            os.listdir(parent_descriptor)
            if parent_descriptor is not None
            else [entry.name for entry in transactions.iterdir()]
        )
    except OSError as exc:
        raise TransactionError("recovery_discovery_failed") from exc
    for name in names:
        if TEMPORARY_RE.fullmatch(name):
            _unlink_stale_temporary(
                transactions / name,
                parent_descriptor=parent_descriptor,
            )


def _scan_journals(
    root: Path,
    transactions: Path,
    *,
    transaction_descriptor: int | None = None,
) -> dict[str, dict[str, Any]]:
    journals: dict[str, dict[str, Any]] = {}
    try:
        names = (
            os.listdir(transaction_descriptor)
            if transaction_descriptor is not None
            else [entry.name for entry in transactions.iterdir()]
        )
    except OSError as exc:
        raise TransactionError("recovery_discovery_failed") from exc
    for name in sorted(names):
        if name == RETIRED_DIRECTORY:
            if transaction_descriptor is None:
                _require_owned_directory(transactions / name, private=True)
            else:
                retired_descriptor = _open_retired_directory(transaction_descriptor)
                os.close(retired_descriptor)
            continue
        if name.startswith("."):
            raise TransactionError("recovery_artifact_invalid")
        entry = Path(name)
        if entry.suffix != ".json" or not IDENTIFIER_RE.fullmatch(entry.stem):
            raise TransactionError("recovery_artifact_invalid")
        journals[entry.stem] = _load_journal(
            root,
            entry.stem,
            transaction_descriptor=transaction_descriptor,
        )
    return journals


def _one_nonterminal(journals: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    nonterminal = [record for record in journals.values() if record["phase"] not in TERMINAL_PHASES]
    if len(nonterminal) != 1:
        raise TransactionError("recovery_state_conflict")
    return nonterminal[0]


def claim_orphan(project_root: Path, transaction_id: str) -> dict[str, Any]:
    """Exclusively reinstall the active pointer for the sole orphan journal."""

    root = _project_root(project_root)
    transaction_id = _validate_identifier(transaction_id, code="transaction_id_invalid")
    with _state_lock(root) as locked:
        if _load_active(root, state_descriptor=locked.state_descriptor) is not None:
            raise TransactionError("active_run_conflict")
        journal = _one_nonterminal(
            _scan_journals(
                root,
                locked.transactions,
                transaction_descriptor=locked.transactions_descriptor,
            )
        )
        if journal["transaction_id"] != transaction_id:
            raise TransactionError("recovery_state_conflict")
        _write_exclusive_json(
            root / ACTIVE_RELATIVE,
            _active_pointer_for(journal),
            conflict_code="active_run_conflict",
            parent_descriptor=locked.state_descriptor,
        )
        return _load_journal(
            root,
            transaction_id,
            transaction_descriptor=locked.transactions_descriptor,
        )


def _require_repairable_active(
    path: Path,
    *,
    parent_descriptor: int | None = None,
) -> os.stat_result:
    try:
        info = (
            path.lstat()
            if parent_descriptor is None
            else os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        )
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise TransactionError("active_pointer_unsafe") from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_size > MAX_STATE_BYTES
    ):
        raise TransactionError("active_pointer_unsafe")
    return info


def repair_active_from_journal(project_root: Path) -> dict[str, Any]:
    """Repair only a private corrupt active pointer from one valid orphan journal."""

    root = _project_root(project_root)
    with _state_lock(root) as locked:
        journal = _one_nonterminal(
            _scan_journals(
                root,
                locked.transactions,
                transaction_descriptor=locked.transactions_descriptor,
            )
        )
        active_path = root / ACTIVE_RELATIVE
        pointer = _active_pointer_for(journal)
        try:
            expected = _require_repairable_active(
                active_path,
                parent_descriptor=locked.state_descriptor,
            )
        except FileNotFoundError:
            _write_exclusive_json(
                active_path,
                pointer,
                conflict_code="active_run_conflict",
                parent_descriptor=locked.state_descriptor,
            )
            return _load_journal(
                root,
                journal["transaction_id"],
                transaction_descriptor=locked.transactions_descriptor,
            )

        active: dict[str, str] | None = None
        try:
            active = _validate_active(
                _read_json(
                    active_path,
                    parent_descriptor=locked.state_descriptor,
                )
            )
        except TransactionError as exc:
            if exc.code == "legacy_state_migration_required":
                raise
            repairable_codes = {
                "active_pointer_invalid",
                "run_id_invalid",
                "state_file_unsafe",
                "state_json_invalid",
                "transaction_timestamp_invalid",
            }
            if exc.code not in repairable_codes:
                raise
        if active is not None:
            if not _active_matches_journal(active, journal):
                raise TransactionError("recovery_state_conflict")
            return journal

        _replace_json_matching(
            active_path,
            pointer,
            expected,
            parent_descriptor=locked.state_descriptor,
        )
        return _load_journal(
            root,
            journal["transaction_id"],
            transaction_descriptor=locked.transactions_descriptor,
        )


def recover_discovery(project_root: Path) -> RecoveryDiscovery:
    """Discover the one transaction requiring recovery without changing its journal."""

    root = _project_root(project_root)
    with _state_lock(root) as locked:
        active = _load_active(root, state_descriptor=locked.state_descriptor)
        journals = _scan_journals(
            root,
            locked.transactions,
            transaction_descriptor=locked.transactions_descriptor,
        )

        nonterminal = [record for record in journals.values() if record["phase"] not in TERMINAL_PHASES]
        if active is not None:
            record = journals.get(active["transaction_id"])
            if record is None or not _active_matches_journal(active, record):
                raise TransactionError("recovery_state_conflict")
            other_nonterminal = [item for item in nonterminal if item["transaction_id"] != active["transaction_id"]]
            if other_nonterminal:
                raise TransactionError("recovery_state_conflict")
            return RecoveryDiscovery(
                transaction=record,
                has_active_pointer=True,
                orphaned=False,
                terminal_pending_finalize=record["phase"] in TERMINAL_PHASES,
            )
        if len(nonterminal) > 1:
            raise TransactionError("recovery_state_conflict")
        if nonterminal:
            return RecoveryDiscovery(
                transaction=nonterminal[0],
                has_active_pointer=False,
                orphaned=True,
                terminal_pending_finalize=False,
            )
        return RecoveryDiscovery(
            transaction=None,
            has_active_pointer=False,
            orphaned=False,
            terminal_pending_finalize=False,
        )


__all__ = [
    "ACTIVE_RELATIVE",
    "ACTIVE_SCHEMA",
    "FORMAL_RESOURCES",
    "JOURNAL_SCHEMA",
    "PHASE_TRANSITIONS",
    "RESOURCE_DISPOSITIONS",
    "RESOURCE_TRANSITIONS",
    "RecoveryDiscovery",
    "TERMINAL_PHASES",
    "TransactionError",
    "bind_resource_intent",
    "claim_orphan",
    "commit_resource_present",
    "create",
    "finalize",
    "load",
    "recover_discovery",
    "repair_active_from_journal",
    "set_terminal_action",
    "transition",
    "update_resource",
]
