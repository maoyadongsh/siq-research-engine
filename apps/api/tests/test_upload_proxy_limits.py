import asyncio
import hashlib

import pytest
import services.upload_proxy_limits as upload_proxy_limits
from fastapi import HTTPException
from services.upload_proxy_limits import (
    UploadProxyConcurrencyLimiter,
    buffer_upload_files,
    close_buffered_uploads,
)


def test_buffer_upload_files_reads_in_chunks_and_rolls_large_content_to_disk():
    class StreamingUpload:
        filename = "large.pdf"
        content_type = "application/pdf"

        def __init__(self) -> None:
            self.remaining = 32 * 1024
            self.read_sizes: list[int] = []

        async def read(self, size: int) -> bytes:
            self.read_sizes.append(size)
            count = min(size, self.remaining)
            self.remaining -= count
            return b"x" * count

    async def run_case():
        upload = StreamingUpload()
        buffered = await buffer_upload_files(
            [upload],
            max_file_bytes=64 * 1024,
            max_batch_bytes=64 * 1024,
            chunk_bytes=1024,
            spool_max_bytes=2048,
        )
        try:
            item = buffered[0]
            assert item.size_bytes == 32 * 1024
            assert item.sha256 == hashlib.sha256(b"x" * (32 * 1024)).hexdigest()
            assert item.file.name is not None
            assert set(upload.read_sizes) == {1024}
            assert len(upload.read_sizes) == 33
        finally:
            close_buffered_uploads(buffered)

    asyncio.run(run_case())


def test_buffer_upload_files_rejects_too_many_files_before_reading():
    class UnreadUpload:
        filename = "report.pdf"
        content_type = "application/pdf"

        async def read(self, _size: int) -> bytes:
            raise AssertionError("file count must be validated before reading uploads")

    async def run_case():
        with pytest.raises(HTTPException) as exc:
            await buffer_upload_files([UnreadUpload() for _ in range(6)], max_files=5)
        assert exc.value.status_code == 413
        assert exc.value.detail["error"] == "too_many_upload_files"
        assert exc.value.detail["file_count"] == 6
        assert exc.value.detail["limit"] == 5

    asyncio.run(run_case())


def test_buffer_upload_files_rejects_empty_file_and_closes_spools(monkeypatch):
    opened_files = []
    real_spooled_file = upload_proxy_limits.tempfile.SpooledTemporaryFile

    def tracked_spooled_file(*args, **kwargs):
        handle = real_spooled_file(*args, **kwargs)
        opened_files.append(handle)
        return handle

    class Upload:
        content_type = "application/pdf"

        def __init__(self, filename: str, content: bytes) -> None:
            self.filename = filename
            self.content = content
            self.consumed = False

        async def read(self, _size: int) -> bytes:
            if self.consumed:
                return b""
            self.consumed = True
            return self.content

    monkeypatch.setattr(upload_proxy_limits.tempfile, "SpooledTemporaryFile", tracked_spooled_file)

    async def run_case():
        with pytest.raises(HTTPException) as exc:
            await buffer_upload_files(
                [Upload("first.pdf", b"first"), Upload("empty.pdf", b"")],
                reject_empty=True,
            )
        assert exc.value.status_code == 400
        assert exc.value.detail == "Uploaded file is empty"

    asyncio.run(run_case())
    assert len(opened_files) == 2
    assert all(handle.closed for handle in opened_files)


def test_slow_upstream_holds_capacity_and_excess_request_fails_with_retry_contract():
    async def run_case():
        limiter = UploadProxyConcurrencyLimiter(max_concurrency=1, queue_timeout_seconds=0.01)
        upstream_started = asyncio.Event()
        release_upstream = asyncio.Event()

        async def slow_upstream():
            async with limiter.slot():
                upstream_started.set()
                await release_upstream.wait()

        first = asyncio.create_task(slow_upstream())
        await upstream_started.wait()
        try:
            with pytest.raises(HTTPException) as exc:
                async with limiter.slot():
                    raise AssertionError("excess request must not enter the upstream stage")
            assert exc.value.status_code == 503
            assert exc.value.detail["error"] == "upload_proxy_busy"
            assert exc.value.detail["limit"] == 1
            assert exc.value.headers == {"Retry-After": "1"}
        finally:
            release_upstream.set()
            await first

    asyncio.run(run_case())


def test_client_cancellation_releases_active_capacity():
    async def run_case():
        limiter = UploadProxyConcurrencyLimiter(max_concurrency=1, queue_timeout_seconds=0.05)
        upstream_started = asyncio.Event()

        async def cancelled_upstream():
            async with limiter.slot():
                upstream_started.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(cancelled_upstream())
        await upstream_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        async with limiter.slot():
            pass

    asyncio.run(run_case())


def test_client_cancellation_while_queued_does_not_consume_capacity():
    async def run_case():
        limiter = UploadProxyConcurrencyLimiter(max_concurrency=1, queue_timeout_seconds=1.0)
        holder_started = asyncio.Event()
        release_holder = asyncio.Event()

        async def holder():
            async with limiter.slot():
                holder_started.set()
                await release_holder.wait()

        first = asyncio.create_task(holder())
        await holder_started.wait()
        queued_entered = False

        async def queued_request():
            nonlocal queued_entered
            async with limiter.slot():
                queued_entered = True

        queued = asyncio.create_task(queued_request())
        await asyncio.sleep(0)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        assert queued_entered is False
        release_holder.set()
        await first

        async with limiter.slot():
            pass

    asyncio.run(run_case())


def test_client_cancellation_during_chunk_read_closes_current_spool(monkeypatch):
    opened_files = []
    real_spooled_file = upload_proxy_limits.tempfile.SpooledTemporaryFile

    def tracked_spooled_file(*args, **kwargs):
        handle = real_spooled_file(*args, **kwargs)
        opened_files.append(handle)
        return handle

    class PausedUpload:
        filename = "cancelled.pdf"
        content_type = "application/pdf"

        def __init__(self) -> None:
            self.read_started = asyncio.Event()

        async def read(self, _size: int) -> bytes:
            self.read_started.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(upload_proxy_limits.tempfile, "SpooledTemporaryFile", tracked_spooled_file)

    async def run_case():
        upload = PausedUpload()
        task = asyncio.create_task(buffer_upload_files([upload]))
        await upload.read_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_case())

    assert len(opened_files) == 1
    assert opened_files[0].closed is True
