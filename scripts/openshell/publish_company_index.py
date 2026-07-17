#!/usr/bin/env python3
"""Atomically rebuild one known SIQ company index from the trusted host."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "siq.openshell.company_index_publish.v1"
MARKET_ROOTS = {
    "cn": Path("data/wiki/companies"),
    "eu": Path("data/wiki/eu/companies"),
    "hk": Path("data/wiki/hk/companies"),
    "jp": Path("data/wiki/jp/companies"),
    "kr": Path("data/wiki/kr/companies"),
    "us": Path("data/wiki/us/companies"),
}
SOURCE_MODULE = Path("agents/hermes/profiles/shared/scripts/update_company_index.py")
# The publisher executes this builder only after the sandbox is gone. Pinning
# the reviewed bytes prevents a writable working tree from swapping in a new
# builder between lifecycle review and the host-side write.
SOURCE_SHA256 = "fe21e8c5ce49399ed8a2faf75e486a6069cdea7031c8228cc7db8fdc6287f974"
LOCK_ROOT = Path("var/openshell/publisher/company-index-locks")
COMPANY_ID_RE = re.compile(r"[^/\\\x00\r\n]{1,160}\Z")
REQUIRED_INDEX_FIELDS = {
    "schema_version",
    "company_id",
    "stock_code",
    "company_short_name",
    "industry",
    "generated_at",
    "data",
    "analysis",
    "factcheck",
    "tracking",
    "legal",
}
SCANNED_INPUTS = (
    Path("company.json"),
    Path("metrics"),
    Path("evidence"),
    Path("analysis"),
    Path("factcheck"),
    Path("tracking"),
    Path("legal"),
)


class CompanyIndexPublishError(RuntimeError):
    pass


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _same_file_snapshot(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        _same_inode(left, right)
        and stat.S_IFMT(left.st_mode) == stat.S_IFMT(right.st_mode)
        and left.st_nlink == right.st_nlink
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and left.st_ctime_ns == right.st_ctime_ns
    )


def _normalize_root(path: Path) -> Path:
    if path.is_symlink():
        raise CompanyIndexPublishError("project_root_is_symlink")
    try:
        root = path.resolve(strict=True)
    except OSError as exc:
        raise CompanyIndexPublishError("project_root_invalid") from exc
    if not root.is_dir():
        raise CompanyIndexPublishError("project_root_invalid")
    return root


def _open_directory_anchor(path: Path, *, error_code: str) -> int:
    descriptor = -1
    try:
        expected = path.lstat()
        if stat.S_ISLNK(expected.st_mode) or not stat.S_ISDIR(expected.st_mode):
            raise CompanyIndexPublishError(error_code)
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode) or not _same_inode(expected, opened):
            raise CompanyIndexPublishError(error_code)
        return descriptor
    except CompanyIndexPublishError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise CompanyIndexPublishError(error_code) from exc


def _open_directory_at(parent_descriptor: int, name: str, *, error_code: str) -> int:
    descriptor = -1
    try:
        expected = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if stat.S_ISLNK(expected.st_mode) or not stat.S_ISDIR(expected.st_mode):
            raise CompanyIndexPublishError(error_code)
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode) or not _same_inode(expected, opened):
            raise CompanyIndexPublishError(error_code)
        return descriptor
    except CompanyIndexPublishError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise CompanyIndexPublishError(error_code) from exc


def _open_directory_chain(parent_descriptor: int, parts: tuple[str, ...], *, error_code: str) -> int:
    current = os.dup(parent_descriptor)
    try:
        for part in parts:
            child = _open_directory_at(current, part, error_code=error_code)
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _assert_named_directory(path: Path, descriptor: int, *, error_code: str) -> None:
    try:
        named = path.lstat()
        opened = os.fstat(descriptor)
    except OSError as exc:
        raise CompanyIndexPublishError(error_code) from exc
    if (
        stat.S_ISLNK(named.st_mode)
        or not stat.S_ISDIR(named.st_mode)
        or not stat.S_ISDIR(opened.st_mode)
        or not _same_inode(named, opened)
    ):
        raise CompanyIndexPublishError(error_code)


def _assert_no_symlink_components(path: Path, *, root: Path) -> None:
    path.relative_to(root)
    current = root
    for part in path.relative_to(root).parts:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError as exc:
            raise CompanyIndexPublishError("company_directory_missing") from exc
        if stat.S_ISLNK(mode):
            raise CompanyIndexPublishError("company_path_uses_symlink")


def resolve_company_directory(*, project_root: Path, market: str, company_id: str) -> Path:
    root = _normalize_root(project_root)
    normalized_market = market.strip().lower()
    if normalized_market not in MARKET_ROOTS:
        raise CompanyIndexPublishError("market_not_allowed")
    if company_id in {".", ".."} or not COMPANY_ID_RE.fullmatch(company_id) or company_id.strip() != company_id:
        raise CompanyIndexPublishError("company_id_invalid")
    company = root / MARKET_ROOTS[normalized_market] / company_id
    _assert_no_symlink_components(company, root=root)
    if not company.is_dir():
        raise CompanyIndexPublishError("company_directory_missing")
    return company


def _input_snapshot_record(relative: Path, info: os.stat_result) -> tuple[object, ...]:
    return (
        relative.as_posix(),
        stat.S_IFMT(info.st_mode),
        stat.S_IMODE(info.st_mode),
        info.st_dev,
        info.st_ino,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _validate_input_info(info: os.stat_result) -> None:
    if stat.S_ISLNK(info.st_mode):
        raise CompanyIndexPublishError("publisher_input_uses_symlink")
    if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
        raise CompanyIndexPublishError("publisher_input_hardlink")
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise CompanyIndexPublishError("publisher_input_is_special_file")


def _scan_for_unsafe_inputs(company: Path) -> tuple[tuple[object, ...], ...]:
    snapshot: list[tuple[object, ...]] = []
    try:
        for relative in SCANNED_INPUTS:
            candidate = company / relative
            try:
                info = candidate.lstat()
            except FileNotFoundError:
                snapshot.append((relative.as_posix(), "missing"))
                continue
            _validate_input_info(info)
            snapshot.append(_input_snapshot_record(relative, info))
            if stat.S_ISREG(info.st_mode):
                continue
            for parent, directory_names, file_names in os.walk(candidate, followlinks=False):
                directory_names.sort()
                file_names.sort()
                parent_path = Path(parent)
                for name in (*directory_names, *file_names):
                    item = parent_path / name
                    item_info = item.lstat()
                    _validate_input_info(item_info)
                    snapshot.append(_input_snapshot_record(item.relative_to(company), item_info))
    except CompanyIndexPublishError:
        raise
    except OSError as exc:
        raise CompanyIndexPublishError("publisher_input_changed") from exc
    return tuple(snapshot)


def _read_company_metadata(company_descriptor: int) -> dict[str, Any]:
    name = "company.json"
    descriptor = -1
    try:
        try:
            expected = os.stat(name, dir_fd=company_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return {}
        if not stat.S_ISREG(expected.st_mode) or expected.st_nlink != 1:
            raise CompanyIndexPublishError("company_metadata_invalid")
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=company_descriptor,
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or not _same_file_snapshot(expected, opened):
            raise CompanyIndexPublishError("company_metadata_invalid")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        finished = os.fstat(descriptor)
        named = os.stat(name, dir_fd=company_descriptor, follow_symlinks=False)
        if not _same_file_snapshot(opened, finished) or not _same_file_snapshot(opened, named):
            raise CompanyIndexPublishError("publisher_input_changed")
        value = json.loads(b"".join(chunks).decode("utf-8"))
    except CompanyIndexPublishError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CompanyIndexPublishError("company_metadata_invalid") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(value, dict):
        raise CompanyIndexPublishError("company_metadata_invalid")
    return value


def _load_index_module(project_root: Path) -> ModuleType:
    source = project_root / SOURCE_MODULE
    try:
        relative_parts = source.relative_to(project_root).parts
    except ValueError as exc:
        raise CompanyIndexPublishError("publisher_source_invalid") from exc
    current = project_root
    try:
        root_info = current.lstat()
    except OSError as exc:
        raise CompanyIndexPublishError("publisher_source_invalid") from exc
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or root_info.st_uid != os.geteuid()
        or stat.S_IMODE(root_info.st_mode) & 0o002
    ):
        raise CompanyIndexPublishError("publisher_source_parent_unsafe")
    for part in relative_parts:
        current /= part
        try:
            info = current.lstat()
        except OSError as exc:
            raise CompanyIndexPublishError("publisher_source_invalid") from exc
        if stat.S_ISLNK(info.st_mode):
            raise CompanyIndexPublishError("publisher_source_parent_unsafe")
        if current != source:
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o002
            ):
                raise CompanyIndexPublishError("publisher_source_parent_unsafe")
        elif (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise CompanyIndexPublishError("publisher_source_unsafe")
    descriptor = -1
    try:
        descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        expected = source.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_uid != os.geteuid()
            or (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino)
        ):
            raise CompanyIndexPublishError("publisher_source_unsafe")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        source_bytes = b"".join(chunks)
    except CompanyIndexPublishError:
        raise
    except OSError as exc:
        raise CompanyIndexPublishError("publisher_source_invalid") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if hashlib.sha256(source_bytes).hexdigest() != SOURCE_SHA256:
        raise CompanyIndexPublishError("publisher_source_digest_mismatch")
    module = ModuleType("siq_trusted_company_index_builder")
    module.__file__ = SOURCE_MODULE.as_posix()
    try:
        compiled = compile(source_bytes, SOURCE_MODULE.as_posix(), "exec", dont_inherit=True)
        exec(compiled, module.__dict__)
    except Exception as exc:
        raise CompanyIndexPublishError("publisher_source_invalid") from exc
    if not callable(getattr(module, "build_index", None)):
        raise CompanyIndexPublishError("publisher_builder_missing")
    return module


def _validate_payload(payload: Any, *, expected_identity: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != REQUIRED_INDEX_FIELDS:
        raise CompanyIndexPublishError("index_schema_invalid")
    if payload.get("schema_version") != 1:
        raise CompanyIndexPublishError("index_schema_invalid")
    if not isinstance(payload.get("company_id"), str) or not payload["company_id"].strip():
        raise CompanyIndexPublishError("index_schema_invalid")
    if not isinstance(payload.get("stock_code"), str) or not payload["stock_code"].strip():
        raise CompanyIndexPublishError("index_schema_invalid")
    for key in ("data", "analysis", "factcheck", "tracking", "legal"):
        if not isinstance(payload.get(key), Mapping):
            raise CompanyIndexPublishError("index_schema_invalid")
    if expected_identity:
        for key in ("company_id", "stock_code"):
            expected = expected_identity.get(key)
            if expected is not None and payload.get(key) != expected:
                raise CompanyIndexPublishError("index_identity_mismatch")
    return payload


def _safe_lock_root(project_root_descriptor: int) -> int:
    current = os.dup(project_root_descriptor)
    for index, part in enumerate(LOCK_ROOT.parts):
        child = -1
        created = False
        try:
            try:
                os.mkdir(part, mode=0o755 if index == 0 else 0o700, dir_fd=current)
                created = True
            except FileExistsError:
                pass
            child = _open_directory_at(current, part, error_code="publisher_lock_root_unsafe")
            info = os.fstat(child)
            if info.st_uid != os.geteuid() or (index > 0 and stat.S_IMODE(info.st_mode) & 0o077):
                raise CompanyIndexPublishError("publisher_lock_root_unsafe")
            if created:
                os.fsync(current)
            os.close(current)
            current = child
            child = -1
        except BaseException:
            if child >= 0:
                os.close(child)
            os.close(current)
            raise
    return current


def _assert_publisher_lock(lock_root_descriptor: int, lock_name: str, descriptor: int) -> None:
    try:
        opened = os.fstat(descriptor)
        named = os.stat(lock_name, dir_fd=lock_root_descriptor, follow_symlinks=False)
    except OSError as exc:
        raise CompanyIndexPublishError("publisher_lock_file_unsafe") from exc
    if (
        stat.S_ISLNK(named.st_mode)
        or not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(named.st_mode)
        or not _same_inode(opened, named)
        or opened.st_uid != os.geteuid()
        or named.st_uid != os.geteuid()
        or opened.st_nlink != 1
        or named.st_nlink != 1
        or stat.S_IMODE(opened.st_mode) != 0o600
        or stat.S_IMODE(named.st_mode) != 0o600
    ):
        raise CompanyIndexPublishError("publisher_lock_file_unsafe")


def _acquire_publisher_lock(lock_root_descriptor: int, lock_name: str) -> int:
    flags = (
        os.O_RDWR
        | os.O_APPEND
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    created = False
    try:
        try:
            descriptor = os.open(lock_name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=lock_root_descriptor)
            created = True
        except FileExistsError:
            descriptor = os.open(lock_name, flags, dir_fd=lock_root_descriptor)
        opened = os.fstat(descriptor)
        named = os.stat(lock_name, dir_fd=lock_root_descriptor, follow_symlinks=False)
        if (
            stat.S_ISLNK(named.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or not _same_inode(opened, named)
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != 1
            or named.st_nlink != 1
        ):
            raise CompanyIndexPublishError("publisher_lock_file_unsafe")
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        if created:
            os.fsync(lock_root_descriptor)
        _assert_publisher_lock(lock_root_descriptor, lock_name, descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        _assert_publisher_lock(lock_root_descriptor, lock_name, descriptor)
        return descriptor
    except CompanyIndexPublishError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise CompanyIndexPublishError("publisher_lock_file_unsafe") from exc


def _rebase_builder_paths(value: Any, *, anchor: Path, company: Path) -> Any:
    if isinstance(value, dict):
        return {key: _rebase_builder_paths(item, anchor=anchor, company=company) for key, item in value.items()}
    if isinstance(value, list):
        return [_rebase_builder_paths(item, anchor=anchor, company=company) for item in value]
    if isinstance(value, tuple):
        return tuple(_rebase_builder_paths(item, anchor=anchor, company=company) for item in value)
    if isinstance(value, str):
        source = str(anchor)
        if value == source:
            return str(company)
        if value.startswith(f"{source}{os.sep}"):
            return f"{company}{value[len(source):]}"
    return value


def _validate_existing_output(company_descriptor: int) -> None:
    try:
        info = os.stat("_index.json", dir_fd=company_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise CompanyIndexPublishError("publisher_output_unsafe") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise CompanyIndexPublishError("publisher_output_unsafe")


def _atomic_write_index(company_descriptor: int, payload: Mapping[str, Any]) -> None:
    content = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    descriptor = -1
    temporary_name = ""
    try:
        for _ in range(32):
            candidate = f"._index.{secrets.token_hex(12)}.tmp"
            try:
                descriptor = os.open(
                    candidate,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=company_descriptor,
                )
            except FileExistsError:
                continue
            temporary_name = candidate
            break
        if descriptor < 0:
            raise CompanyIndexPublishError("publisher_output_write_failed")
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise CompanyIndexPublishError("publisher_output_write_failed")
            view = view[written:]
        os.fchmod(descriptor, 0o644)
        os.fsync(descriptor)
        temporary_info = os.fstat(descriptor)
        named_temporary = os.stat(temporary_name, dir_fd=company_descriptor, follow_symlinks=False)
        if (
            not stat.S_ISREG(temporary_info.st_mode)
            or temporary_info.st_uid != os.geteuid()
            or temporary_info.st_nlink != 1
            or stat.S_IMODE(temporary_info.st_mode) != 0o644
            or not _same_inode(temporary_info, named_temporary)
        ):
            raise CompanyIndexPublishError("publisher_output_write_failed")
        _validate_existing_output(company_descriptor)
        os.replace(
            temporary_name,
            "_index.json",
            src_dir_fd=company_descriptor,
            dst_dir_fd=company_descriptor,
        )
        temporary_name = ""
        published = os.stat("_index.json", dir_fd=company_descriptor, follow_symlinks=False)
        if (
            not stat.S_ISREG(published.st_mode)
            or published.st_nlink != 1
            or not _same_inode(temporary_info, published)
            or stat.S_IMODE(published.st_mode) != 0o644
        ):
            raise CompanyIndexPublishError("publisher_output_write_failed")
        os.fsync(company_descriptor)
    except CompanyIndexPublishError:
        raise
    except OSError as exc:
        raise CompanyIndexPublishError("publisher_output_write_failed") from exc
    finally:
        try:
            if temporary_name:
                try:
                    named = os.stat(temporary_name, dir_fd=company_descriptor, follow_symlinks=False)
                    if descriptor >= 0 and _same_inode(os.fstat(descriptor), named):
                        os.unlink(temporary_name, dir_fd=company_descriptor)
                except FileNotFoundError:
                    pass
        finally:
            if descriptor >= 0:
                os.close(descriptor)


def publish_company_index(
    *,
    project_root: Path,
    market: str,
    company_id: str,
    builder: Callable[[Path], Any] | None = None,
) -> dict[str, Any]:
    root = _normalize_root(project_root)
    normalized_market = market.strip().lower()
    company = resolve_company_directory(project_root=root, market=normalized_market, company_id=company_id)
    identity = f"{normalized_market}:{company_id}"
    projection = hashlib.sha256(identity.encode()).hexdigest()[:24]
    lock_name = f"{projection}.lock"
    root_descriptor = _open_directory_anchor(root, error_code="project_root_changed")
    lock_root_descriptor = -1
    lock_descriptor = -1
    company_descriptor = -1
    payload: dict[str, Any] | None = None
    try:
        lock_root_descriptor = _safe_lock_root(root_descriptor)
        lock_root_path = root / LOCK_ROOT
        _assert_named_directory(root, root_descriptor, error_code="project_root_changed")
        _assert_named_directory(
            lock_root_path,
            lock_root_descriptor,
            error_code="publisher_lock_root_changed",
        )
        lock_descriptor = _acquire_publisher_lock(lock_root_descriptor, lock_name)
        try:
            company_relative = MARKET_ROOTS[normalized_market] / company_id
            company_descriptor = _open_directory_chain(
                root_descriptor,
                company_relative.parts,
                error_code="company_directory_changed",
            )
            _assert_named_directory(company, company_descriptor, error_code="company_directory_changed")
            anchor = Path(f"/proc/self/fd/{company_descriptor}")
            if not anchor.is_dir():
                raise CompanyIndexPublishError("publisher_directory_anchor_unavailable")
            input_snapshot = _scan_for_unsafe_inputs(anchor)
            selected_builder = builder or _load_index_module(root).build_index
            company_metadata = _read_company_metadata(company_descriptor)
            built_payload = selected_builder(anchor)
            _assert_named_directory(root, root_descriptor, error_code="project_root_changed")
            _assert_named_directory(company, company_descriptor, error_code="company_directory_changed")
            if _scan_for_unsafe_inputs(anchor) != input_snapshot:
                raise CompanyIndexPublishError("publisher_input_changed")
            payload = _validate_payload(
                _rebase_builder_paths(built_payload, anchor=anchor, company=company),
                expected_identity=company_metadata,
            )
            _assert_named_directory(
                lock_root_path,
                lock_root_descriptor,
                error_code="publisher_lock_root_changed",
            )
            _assert_publisher_lock(lock_root_descriptor, lock_name, lock_descriptor)
            _assert_named_directory(company, company_descriptor, error_code="company_directory_changed")
            _atomic_write_index(company_descriptor, payload)
            _assert_named_directory(root, root_descriptor, error_code="project_root_changed")
            _assert_named_directory(company, company_descriptor, error_code="company_directory_changed")
            _assert_publisher_lock(lock_root_descriptor, lock_name, lock_descriptor)
        finally:
            if company_descriptor >= 0:
                os.close(company_descriptor)
                company_descriptor = -1
    finally:
        if lock_descriptor >= 0:
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            finally:
                os.close(lock_descriptor)
        if lock_root_descriptor >= 0:
            os.close(lock_root_descriptor)
        os.close(root_descriptor)
    if payload is None:
        raise CompanyIndexPublishError("publisher_output_write_failed")
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "market": normalized_market,
        "company_projection": projection,
        "index_schema_version": payload["schema_version"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--market", choices=sorted(MARKET_ROOTS), required=True)
    parser.add_argument("--company-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = publish_company_index(
            project_root=args.project_root,
            market=args.market,
            company_id=args.company_id,
        )
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0
    except (OSError, ValueError, CompanyIndexPublishError) as exc:
        print(
            json.dumps({"schema_version": SCHEMA_VERSION, "ok": False, "error_code": str(exc)}, sort_keys=True),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
