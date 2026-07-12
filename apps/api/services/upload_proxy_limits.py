from __future__ import annotations

import asyncio
import hashlib
import math
import os
import tempfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, BinaryIO

import httpx
from fastapi import HTTPException

DEFAULT_CHUNK_BYTES = 1024 * 1024
DEFAULT_SPOOL_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_FILE_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_BATCH_BYTES = 200 * 1024 * 1024
DEFAULT_MAX_FILES = 5
DEFAULT_MAX_CONCURRENCY = 8
DEFAULT_QUEUE_TIMEOUT_SECONDS = 5.0


@dataclass
class BufferedUpload:
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    file: BinaryIO

    def close(self) -> None:
        self.file.close()


class UploadProxyConcurrencyLimiter:
    """Bound concurrent upload work before it consumes an upstream connection."""

    def __init__(self, *, max_concurrency: int, queue_timeout_seconds: float) -> None:
        self.max_concurrency = max(1, int(max_concurrency))
        self.queue_timeout_seconds = max(0.001, float(queue_timeout_seconds))
        self._semaphore = asyncio.Semaphore(self.max_concurrency)

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        acquired = False
        try:
            try:
                await asyncio.wait_for(
                    self._semaphore.acquire(),
                    timeout=self.queue_timeout_seconds,
                )
            except TimeoutError as exc:
                raise upload_proxy_busy_error(
                    limit=self.max_concurrency,
                    queue_timeout_seconds=self.queue_timeout_seconds,
                ) from exc
            acquired = True
            yield
        finally:
            if acquired:
                self._semaphore.release()


def env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(value, minimum)


def env_float(name: str, default: float, *, minimum: float = 0.001) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(value, minimum)


def upload_proxy_timeout(
    *,
    connect_env: str,
    write_env: str,
    read_env: str,
    pool_env: str,
    connect_default: float = 10.0,
    write_default: float = 60.0,
    read_default: float = 120.0,
    pool_default: float = 10.0,
) -> httpx.Timeout:
    return httpx.Timeout(
        connect=env_float(connect_env, connect_default),
        write=env_float(write_env, write_default),
        read=env_float(read_env, read_default),
        pool=env_float(pool_env, pool_default),
    )


def upload_too_large_error(
    *,
    filename: str,
    size_bytes: int,
    limit_bytes: int,
    scope: str,
) -> HTTPException:
    return HTTPException(
        status_code=413,
        detail={
            "error": "upload_too_large",
            "filename": filename,
            "size_bytes": size_bytes,
            "limit_bytes": limit_bytes,
            "scope": scope,
            "message": "上传文件超过大小限制，请减少文件大小或拆分批次。",
        },
    )


def too_many_upload_files_error(*, file_count: int, limit: int) -> HTTPException:
    return HTTPException(
        status_code=413,
        detail={
            "error": "too_many_upload_files",
            "file_count": file_count,
            "limit": limit,
            "scope": "batch",
            "message": "上传文件数量超过限制，请拆分批次。",
        },
    )


def upload_proxy_busy_error(
    *,
    limit: int,
    queue_timeout_seconds: float,
) -> HTTPException:
    retry_after = max(1, math.ceil(queue_timeout_seconds))
    return HTTPException(
        status_code=503,
        headers={"Retry-After": str(retry_after)},
        detail={
            "error": "upload_proxy_busy",
            "scope": "process",
            "limit": limit,
            "queue_timeout_seconds": queue_timeout_seconds,
            "message": "上传服务繁忙，请稍后重试。",
        },
    )


async def _read_upload_chunk(upload: Any, chunk_bytes: int) -> tuple[bytes, bool]:
    try:
        chunk = await upload.read(chunk_bytes)
    except TypeError:
        chunk = await upload.read()
        return chunk or b"", True
    return chunk or b"", False


async def buffer_upload_files(
    files: list[Any],
    *,
    max_files: int | None = None,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_batch_bytes: int = DEFAULT_MAX_BATCH_BYTES,
    chunk_bytes: int = DEFAULT_CHUNK_BYTES,
    spool_max_bytes: int = DEFAULT_SPOOL_MAX_BYTES,
    default_filename: str = "upload",
    default_content_type: str = "application/octet-stream",
    reject_empty: bool = False,
) -> list[BufferedUpload]:
    if max_files is not None and len(files) > max(0, int(max_files)):
        raise too_many_upload_files_error(file_count=len(files), limit=max(0, int(max_files)))

    buffered: list[BufferedUpload] = []
    batch_size = 0
    current_handle: BinaryIO | None = None
    try:
        for item in files:
            filename = str(getattr(item, "filename", "") or default_filename)
            content_type = str(getattr(item, "content_type", "") or default_content_type)
            handle = tempfile.SpooledTemporaryFile(max_size=spool_max_bytes, mode="w+b")
            current_handle = handle
            digest = hashlib.sha256()
            size = 0
            while True:
                chunk, single_read = await _read_upload_chunk(item, chunk_bytes)
                if not chunk:
                    break
                size += len(chunk)
                batch_size += len(chunk)
                if size > max_file_bytes:
                    handle.close()
                    raise upload_too_large_error(
                        filename=filename,
                        size_bytes=size,
                        limit_bytes=max_file_bytes,
                        scope="file",
                    )
                if batch_size > max_batch_bytes:
                    handle.close()
                    raise upload_too_large_error(
                        filename=filename,
                        size_bytes=batch_size,
                        limit_bytes=max_batch_bytes,
                        scope="batch",
                    )
                digest.update(chunk)
                handle.write(chunk)
                if single_read:
                    break
            if reject_empty and size == 0:
                raise HTTPException(status_code=400, detail="Uploaded file is empty")
            handle.seek(0)
            buffered.append(
                BufferedUpload(
                    filename=filename,
                    content_type=content_type,
                    size_bytes=size,
                    sha256=digest.hexdigest(),
                    file=handle,
                )
            )
            current_handle = None
        return buffered
    except BaseException:
        if current_handle is not None:
            current_handle.close()
        for upload in buffered:
            upload.close()
        raise


def close_buffered_uploads(files: list[BufferedUpload]) -> None:
    for item in files:
        item.close()


UPLOAD_PROXY_LIMITER = UploadProxyConcurrencyLimiter(
    max_concurrency=env_int("SIQ_UPLOAD_PROXY_MAX_CONCURRENCY", DEFAULT_MAX_CONCURRENCY),
    queue_timeout_seconds=env_float(
        "SIQ_UPLOAD_PROXY_QUEUE_TIMEOUT_SECONDS",
        DEFAULT_QUEUE_TIMEOUT_SECONDS,
    ),
)
