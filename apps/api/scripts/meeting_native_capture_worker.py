#!/usr/bin/env python3
"""Package sealed native captures and hand off final transcript jobs."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import socket
import sys
from pathlib import Path
from uuid import uuid4

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from database import async_engine, create_db_and_tables  # noqa: E402
from services.meeting_audio_store import MeetingAudioStore  # noqa: E402
from services.meeting_native_capture_config import MeetingNativeCaptureSettings  # noqa: E402
from services.meeting_native_capture_storage import MeetingNativeCaptureStorage  # noqa: E402
from services.meeting_native_capture_worker import (  # noqa: E402
    MeetingNativeCaptureFinalizationWorker,
)
from sqlalchemy.ext.asyncio import async_sessionmaker  # noqa: E402
from sqlmodel.ext.asyncio.session import AsyncSession  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Package durable SIQ iOS native meeting captures.")
    parser.add_argument(
        "--worker-id",
        default=os.getenv(
            "SIQ_MEETING_NATIVE_FINALIZATION_WORKER_ID",
            f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}",
        ),
    )
    parser.add_argument("--once", action="store_true", help="Process at most one recovery or packaging job.")
    parser.add_argument(
        "--retry-capture",
        metavar="CAPTURE_ID",
        help="Reset one failed/retry-wait finalization after its storage issue is repaired.",
    )
    return parser.parse_args()


async def _run(arguments: argparse.Namespace) -> None:
    settings = MeetingNativeCaptureSettings.from_env()
    if not settings.operational:
        raise RuntimeError("iOS native capture is disabled or invalid: " + ", ".join(settings.errors))
    factory = async_sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    worker = MeetingNativeCaptureFinalizationWorker(
        factory,
        MeetingNativeCaptureStorage(settings.root),
        MeetingAudioStore(),
        settings,
        worker_id=arguments.worker_id,
    )
    if arguments.retry_capture:
        if not await worker.retry_capture(arguments.retry_capture):
            raise RuntimeError("native capture finalization was not retryable or was not found")
        return
    if arguments.once:
        await worker.run_once()
        return
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(name, stop.set)
        except NotImplementedError:
            pass
    await worker.run_forever(stop)


def main() -> None:
    arguments = _arguments()
    logging.basicConfig(
        level=os.getenv("SIQ_MEETING_NATIVE_FINALIZATION_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    create_db_and_tables()
    asyncio.run(_run(arguments))


if __name__ == "__main__":
    main()
