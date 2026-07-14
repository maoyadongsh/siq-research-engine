"""Server-owned staging storage for resumable meeting imports."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path
from typing import AsyncIterator, Iterable
from uuid import uuid4

from services.meeting_import_contracts import MeetingImportChunk

_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class MeetingImportStorageError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class MeetingImportStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)

    def _upload_root(self, owner_user_id: int, upload_id: str, *, create: bool = False) -> Path:
        owner = str(owner_user_id)
        if not _ID.fullmatch(owner) or not _ID.fullmatch(upload_id):
            raise MeetingImportStorageError("MEETING_IMPORT_PATH_INVALID", "upload identifier is invalid")
        owner_root = self.root / owner
        upload_root = owner_root / upload_id
        if create:
            if owner_root.is_symlink():
                raise MeetingImportStorageError("MEETING_IMPORT_PATH_INVALID", "symlinked owner paths are forbidden")
            owner_root.mkdir(mode=0o700, exist_ok=True)
            if upload_root.is_symlink():
                raise MeetingImportStorageError("MEETING_IMPORT_PATH_INVALID", "symlinked upload paths are forbidden")
            upload_root.mkdir(mode=0o700, exist_ok=True)
        resolved = upload_root.resolve()
        if self.root not in resolved.parents:
            raise MeetingImportStorageError("MEETING_IMPORT_PATH_INVALID", "upload path escaped its root")
        if upload_root.is_symlink() or owner_root.is_symlink():
            raise MeetingImportStorageError("MEETING_IMPORT_PATH_INVALID", "symlinked upload paths are forbidden")
        return resolved

    def resolve_storage_key(self, storage_key: str) -> Path:
        candidate = Path(storage_key)
        if not storage_key or candidate.is_absolute() or ".." in candidate.parts:
            raise MeetingImportStorageError("MEETING_IMPORT_STORAGE_KEY_INVALID", "storage key is invalid")
        resolved = (self.root / candidate).resolve()
        if self.root not in resolved.parents or resolved.is_symlink():
            raise MeetingImportStorageError("MEETING_IMPORT_STORAGE_KEY_INVALID", "storage key escaped its root")
        return resolved

    async def store_chunk(
        self,
        owner_user_id: int,
        upload_id: str,
        ordinal: int,
        expected_sha256: str,
        stream: AsyncIterator[bytes],
        *,
        expected_size: int,
    ) -> tuple[str, int, str, bool]:
        digest_header = expected_sha256.lower()
        if ordinal < 0 or not _SHA256.fullmatch(digest_header) or expected_size <= 0:
            raise MeetingImportStorageError("MEETING_IMPORT_CHUNK_INVALID", "chunk metadata is invalid")
        upload_root = self._upload_root(owner_user_id, upload_id, create=True)
        chunks_root = upload_root / "chunks"
        if chunks_root.is_symlink():
            raise MeetingImportStorageError("MEETING_IMPORT_PATH_INVALID", "symlinked chunk paths are forbidden")
        chunks_root.mkdir(mode=0o700, exist_ok=True)
        target = chunks_root / f"{ordinal:08d}-{digest_header}.part"
        storage_key = target.relative_to(self.root).as_posix()
        if target.exists():
            actual_size, actual_hash = self._file_stats(target)
            if actual_size == expected_size and actual_hash == digest_header:
                return storage_key, actual_size, actual_hash, False
            raise MeetingImportStorageError("MEETING_IMPORT_CHUNK_CONFLICT", "stored chunk differs")

        temporary = chunks_root / f".{ordinal:08d}.{uuid4().hex}.tmp"
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600)
        digest = hashlib.sha256()
        written = 0
        try:
            with os.fdopen(descriptor, "wb") as output:
                async for block in stream:
                    if not block:
                        continue
                    written += len(block)
                    if written > expected_size:
                        raise MeetingImportStorageError(
                            "MEETING_IMPORT_CHUNK_TOO_LARGE", "chunk exceeds its declared length"
                        )
                    output.write(block)
                    digest.update(block)
                output.flush()
                os.fsync(output.fileno())
            actual_hash = digest.hexdigest()
            if written != expected_size:
                raise MeetingImportStorageError(
                    "MEETING_IMPORT_CHUNK_SIZE_MISMATCH", "chunk length differs from its manifest"
                )
            if actual_hash != digest_header:
                raise MeetingImportStorageError(
                    "MEETING_IMPORT_CHUNK_HASH_MISMATCH", "chunk hash verification failed"
                )
            os.replace(temporary, target)
            os.chmod(target, 0o600)
            return storage_key, written, actual_hash, True
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def assemble_source(
        self,
        owner_user_id: int,
        upload_id: str,
        chunks: Iterable[MeetingImportChunk],
        *,
        extension: str,
        expected_size: int,
        expected_sha256: str | None,
    ) -> tuple[Path, str]:
        upload_root = self._upload_root(owner_user_id, upload_id, create=True)
        target = upload_root / f"source.{extension}"
        temporary = upload_root / f".source.{uuid4().hex}.tmp"
        digest = hashlib.sha256()
        written = 0
        try:
            with temporary.open("xb") as output:
                os.chmod(temporary, 0o600)
                for chunk in sorted(chunks, key=lambda item: item.ordinal):
                    source = self.resolve_storage_key(chunk.storage_key)
                    size, chunk_hash = self._file_stats(source)
                    if size != chunk.byte_size or chunk_hash != chunk.sha256:
                        raise MeetingImportStorageError(
                            "MEETING_IMPORT_CHUNK_INTEGRITY_FAILED", "a staged chunk failed verification"
                        )
                    with source.open("rb") as handle:
                        for block in iter(lambda: handle.read(1024 * 1024), b""):
                            output.write(block)
                            digest.update(block)
                            written += len(block)
                output.flush()
                os.fsync(output.fileno())
            actual_hash = digest.hexdigest()
            if written != expected_size:
                raise MeetingImportStorageError(
                    "MEETING_IMPORT_FILE_SIZE_MISMATCH", "assembled recording size differs from manifest"
                )
            if expected_sha256 and actual_hash != expected_sha256.lower():
                raise MeetingImportStorageError(
                    "MEETING_IMPORT_FILE_HASH_MISMATCH", "assembled recording hash verification failed"
                )
            os.replace(temporary, target)
            return target, actual_hash
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def normalized_path(self, owner_user_id: int, upload_id: str) -> Path:
        return self._upload_root(owner_user_id, upload_id, create=True) / "normalized.pcm"

    def remove_key(self, storage_key: str) -> None:
        self.resolve_storage_key(storage_key).unlink(missing_ok=True)

    def purge_upload(self, owner_user_id: int, upload_id: str) -> None:
        path = self._upload_root(owner_user_id, upload_id)
        if path.exists():
            shutil.rmtree(path)

    @staticmethod
    def _file_stats(path: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return path.stat().st_size, digest.hexdigest()
