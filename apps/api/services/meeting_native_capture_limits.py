"""Process-level admission bound for native-capture request bodies."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import AsyncIterator


class MeetingNativeCaptureIngressBusy(RuntimeError):
    code = "NATIVE_CAPTURE_INGRESS_BUSY"


class MeetingNativeCaptureIngressLimiter:
    def __init__(self, max_concurrency: int, queue_timeout_seconds: int) -> None:
        if max_concurrency < 1 or queue_timeout_seconds < 1:
            raise ValueError("native capture ingress limits must be positive")
        self.max_concurrency = max_concurrency
        self.queue_timeout_seconds = queue_timeout_seconds
        self._semaphore = asyncio.Semaphore(max_concurrency)

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(),
                timeout=self.queue_timeout_seconds,
            )
        except TimeoutError as exc:
            raise MeetingNativeCaptureIngressBusy(
                "native capture ingest concurrency is saturated"
            ) from exc
        try:
            yield
        finally:
            self._semaphore.release()


@lru_cache(maxsize=16)
def native_capture_ingress_limiter(
    max_concurrency: int,
    queue_timeout_seconds: int,
) -> MeetingNativeCaptureIngressLimiter:
    return MeetingNativeCaptureIngressLimiter(max_concurrency, queue_timeout_seconds)
