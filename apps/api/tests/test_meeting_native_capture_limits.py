from __future__ import annotations

import asyncio

import anyio
import pytest
from services.meeting_native_capture_limits import (
    MeetingNativeCaptureIngressBusy,
    MeetingNativeCaptureIngressLimiter,
)


def test_native_capture_ingress_limiter_fails_closed_when_slot_is_saturated():
    async def scenario() -> None:
        limiter = MeetingNativeCaptureIngressLimiter(1, 1)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def hold() -> None:
            async with limiter.slot():
                entered.set()
                await release.wait()

        holder = asyncio.create_task(hold())
        await entered.wait()
        with pytest.raises(MeetingNativeCaptureIngressBusy):
            async with limiter.slot():
                raise AssertionError("saturated limiter unexpectedly admitted a request")
        release.set()
        await holder
        async with limiter.slot():
            pass

    anyio.run(scenario)
