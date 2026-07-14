"""Atomic, owner-scoped storage for native capture batches."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class MeetingNativeCaptureStorageError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PersistedNativeCaptureBatch:
    storage_key: str
    sha256: str
    byte_size: int
    created: bool


class MeetingNativeCaptureStorage:
    def __init__(self, root: Path) -> None:
        requested_root = root.expanduser()
        if requested_root.is_symlink():
            raise MeetingNativeCaptureStorageError(
                "NATIVE_CAPTURE_STORAGE_PATH_INVALID",
                "native capture storage root cannot be a symlink",
            )
        try:
            self.root = requested_root.resolve()
        except (OSError, RuntimeError) as exc:
            raise MeetingNativeCaptureStorageError(
                "NATIVE_CAPTURE_STORAGE_PATH_INVALID",
                "native capture storage root cannot be resolved",
            ) from exc
        self._durable_directories: set[Path] = set()
        self._ensure_directory_chain_durable(self.root)
        try:
            os.chmod(self.root, 0o700)
        except OSError as exc:
            raise MeetingNativeCaptureStorageError(
                "NATIVE_CAPTURE_STORAGE_UNAVAILABLE",
                "native capture storage root permissions cannot be applied",
            ) from exc

    @staticmethod
    def _component(value: str) -> str:
        if not _ID.fullmatch(value):
            raise MeetingNativeCaptureStorageError(
                "NATIVE_CAPTURE_STORAGE_PATH_INVALID",
                "native capture storage identifier is invalid",
            )
        return value

    def _capture_root(self, owner_user_id: int, capture_id: str, *, create: bool) -> Path:
        owner = self._component(str(owner_user_id))
        capture = self._component(capture_id)
        owner_root = self.root / owner
        capture_root = owner_root / capture
        if owner_root.is_symlink() or capture_root.is_symlink():
            raise MeetingNativeCaptureStorageError(
                "NATIVE_CAPTURE_STORAGE_PATH_INVALID",
                "symlinked native capture paths are forbidden",
            )
        if create:
            self._ensure_directory_durable(owner_root)
            self._ensure_directory_durable(capture_root)
        resolved = capture_root.resolve()
        if self.root not in resolved.parents or owner_root.is_symlink() or capture_root.is_symlink():
            raise MeetingNativeCaptureStorageError(
                "NATIVE_CAPTURE_STORAGE_PATH_INVALID",
                "native capture path escaped its root",
            )
        return resolved

    def resolve_storage_key(self, storage_key: str) -> Path:
        candidate = Path(storage_key)
        if not storage_key or candidate.is_absolute() or ".." in candidate.parts:
            raise MeetingNativeCaptureStorageError(
                "NATIVE_CAPTURE_STORAGE_KEY_INVALID",
                "native capture storage key is invalid",
            )
        unresolved = self.root / candidate
        cursor = self.root
        for component in candidate.parts:
            cursor /= component
            if cursor.is_symlink():
                raise MeetingNativeCaptureStorageError(
                    "NATIVE_CAPTURE_STORAGE_KEY_INVALID",
                    "symlinked native capture storage keys are forbidden",
                )
        resolved = unresolved.resolve()
        if self.root not in resolved.parents:
            raise MeetingNativeCaptureStorageError(
                "NATIVE_CAPTURE_STORAGE_KEY_INVALID",
                "native capture storage key escaped its root",
            )
        return resolved

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _ensure_directory_durable(self, path: Path) -> None:
        try:
            path.mkdir(mode=0o700, exist_ok=True)
        except OSError as exc:
            raise MeetingNativeCaptureStorageError(
                "NATIVE_CAPTURE_STORAGE_UNAVAILABLE",
                "native capture storage directory cannot be created",
            ) from exc
        if path.is_symlink() or not path.is_dir():
            raise MeetingNativeCaptureStorageError(
                "NATIVE_CAPTURE_STORAGE_PATH_INVALID",
                "native capture storage directory is not a regular directory",
            )
        if path in self._durable_directories:
            return
        try:
            self._fsync_directory(path.parent)
        except OSError as exc:
            raise MeetingNativeCaptureStorageError(
                "NATIVE_CAPTURE_STORAGE_UNAVAILABLE",
                "native capture storage directory cannot be synchronized",
            ) from exc
        self._durable_directories.add(path)

    def _ensure_directory_chain_durable(self, path: Path) -> None:
        missing: list[Path] = []
        cursor = path
        while not cursor.exists():
            missing.append(cursor)
            parent = cursor.parent
            if parent == cursor:
                raise MeetingNativeCaptureStorageError(
                    "NATIVE_CAPTURE_STORAGE_UNAVAILABLE",
                    "native capture storage has no available ancestor",
                )
            cursor = parent
        if cursor.is_symlink() or not cursor.is_dir():
            raise MeetingNativeCaptureStorageError(
                "NATIVE_CAPTURE_STORAGE_PATH_INVALID",
                "native capture storage ancestor is not a regular directory",
            )
        if cursor.parent != cursor:
            # The nearest existing ancestor may be the residue of an earlier
            # mkdir whose parent fsync failed. Confirm it before extending the
            # chain so recovery cannot skip that failed durability boundary.
            self._ensure_directory_durable(cursor)
        for component in reversed(missing):
            self._ensure_directory_durable(component)
        # Existing roots are also confirmed once per storage instance. This
        # recovers a directory created by an earlier failed parent fsync.
        self._ensure_directory_durable(path)

    def _unlink_durable(self, path: Path, *, missing_ok: bool) -> bool:
        try:
            path.unlink()
        except FileNotFoundError:
            if missing_ok:
                return False
            raise MeetingNativeCaptureStorageError(
                "NATIVE_CAPTURE_STORAGE_UNAVAILABLE",
                "native capture file is unavailable for removal",
            ) from None
        except OSError as exc:
            raise MeetingNativeCaptureStorageError(
                "NATIVE_CAPTURE_STORAGE_UNAVAILABLE",
                "native capture file cannot be removed",
            ) from exc
        try:
            self._fsync_directory(path.parent)
        except OSError as exc:
            raise MeetingNativeCaptureStorageError(
                "NATIVE_CAPTURE_STORAGE_UNAVAILABLE",
                "native capture file removal cannot be synchronized",
            ) from exc
        return True

    async def persist_batch(
        self,
        owner_user_id: int,
        capture_id: str,
        stream_epoch: int,
        sequence: int,
        stream: AsyncIterator[bytes],
        *,
        expected_size: int,
        expected_sha256: str,
        max_bytes: int,
        min_free_bytes: int = 0,
    ) -> PersistedNativeCaptureBatch:
        if stream_epoch < 1 or sequence < 0 or expected_size < 1 or expected_size > max_bytes:
            raise MeetingNativeCaptureStorageError(
                "NATIVE_CAPTURE_BATCH_INVALID",
                "native capture batch coordinates or size are invalid",
            )
        capture_root = self._capture_root(owner_user_id, capture_id, create=True)
        try:
            free_bytes = int(shutil.disk_usage(capture_root).free)
        except OSError as exc:
            raise MeetingNativeCaptureStorageError(
                "NATIVE_CAPTURE_STORAGE_UNAVAILABLE",
                "native capture storage capacity cannot be inspected",
            ) from exc
        if free_bytes < min_free_bytes + expected_size:
            raise MeetingNativeCaptureStorageError(
                "NATIVE_CAPTURE_STORAGE_LOW_SPACE",
                "native capture storage free-space reserve would be crossed",
            )
        epoch_root = capture_root / str(stream_epoch)
        if epoch_root.is_symlink():
            raise MeetingNativeCaptureStorageError(
                "NATIVE_CAPTURE_STORAGE_PATH_INVALID",
                "symlinked native capture epoch paths are forbidden",
            )
        self._ensure_directory_durable(epoch_root)
        target = epoch_root / f"{sequence}.pcm"
        temporary = epoch_root / f".{sequence}.{uuid4().hex}.tmp"
        digest = hashlib.sha256()
        written = 0
        try:
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                async for block in stream:
                    if not block:
                        continue
                    written += len(block)
                    if written > expected_size or written > max_bytes:
                        raise MeetingNativeCaptureStorageError(
                            "NATIVE_CAPTURE_BATCH_TOO_LARGE",
                            "native capture batch exceeded its declared size",
                        )
                    output.write(block)
                    digest.update(block)
                output.flush()
                os.fsync(output.fileno())
            actual_sha256 = digest.hexdigest()
            if written != expected_size or actual_sha256 != expected_sha256:
                raise MeetingNativeCaptureStorageError(
                    "NATIVE_CAPTURE_BATCH_INTEGRITY_FAILED",
                    "native capture batch did not match its declared size or digest",
                )
            try:
                os.link(temporary, target)
                created = True
            except FileExistsError:
                if target.is_symlink() or not target.is_file():
                    raise MeetingNativeCaptureStorageError(
                        "NATIVE_CAPTURE_STORAGE_PATH_INVALID",
                        "native capture target is not a regular file",
                    ) from None
                if target.stat().st_size != written or self._file_sha256(target) != actual_sha256:
                    raise MeetingNativeCaptureStorageError(
                        "NATIVE_CAPTURE_BATCH_CONFLICT",
                        "native capture sequence already contains different audio",
                    ) from None
                created = False
            # The file contents were synced above. Persist the hard-link directory
            # entry before the API is allowed to ACK. This also makes a retry
            # recover safely after an earlier directory fsync failure.
            self._fsync_directory(epoch_root)
        except MeetingNativeCaptureStorageError:
            raise
        except OSError as exc:
            raise MeetingNativeCaptureStorageError(
                "NATIVE_CAPTURE_STORAGE_UNAVAILABLE",
                "native capture storage is unavailable",
            ) from exc
        finally:
            self._unlink_durable(temporary, missing_ok=True)
        storage_key = target.relative_to(self.root).as_posix()
        return PersistedNativeCaptureBatch(storage_key, expected_sha256, written, created)

    def remove_if_created(self, persisted: PersistedNativeCaptureBatch) -> None:
        if not persisted.created:
            return
        path = self.resolve_storage_key(persisted.storage_key)
        self._unlink_durable(path, missing_ok=True)

    def read_verified_batch(
        self,
        owner_user_id: int,
        capture_id: str,
        storage_key: str,
        *,
        expected_size: int,
        expected_sha256: str,
        max_bytes: int,
    ) -> bytes:
        if expected_size < 1 or expected_size > max_bytes:
            raise MeetingNativeCaptureStorageError(
                "NATIVE_CAPTURE_BATCH_TOO_LARGE",
                "native capture batch exceeds the finalization read bound",
            )
        capture_root = self._capture_root(owner_user_id, capture_id, create=False)
        path = self.resolve_storage_key(storage_key)
        if capture_root not in path.parents or not path.is_file():
            raise MeetingNativeCaptureStorageError(
                "NATIVE_CAPTURE_STORAGE_KEY_INVALID",
                "native capture batch does not belong to this capture",
            )
        digest = hashlib.sha256()
        payload = bytearray()
        try:
            with path.open("rb") as source:
                while True:
                    block = source.read(min(1024 * 1024, max_bytes + 1 - len(payload)))
                    if not block:
                        break
                    payload.extend(block)
                    digest.update(block)
                    if len(payload) > max_bytes:
                        raise MeetingNativeCaptureStorageError(
                            "NATIVE_CAPTURE_BATCH_TOO_LARGE",
                            "native capture batch exceeds the finalization read bound",
                        )
        except MeetingNativeCaptureStorageError:
            raise
        except OSError as exc:
            raise MeetingNativeCaptureStorageError(
                "NATIVE_CAPTURE_STORAGE_UNAVAILABLE",
                "native capture batch is unavailable",
            ) from exc
        if len(payload) != expected_size or digest.hexdigest() != expected_sha256:
            raise MeetingNativeCaptureStorageError(
                "NATIVE_CAPTURE_BATCH_INTEGRITY_FAILED",
                "native capture batch does not match its durable manifest",
            )
        return bytes(payload)
