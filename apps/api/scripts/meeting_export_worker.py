#!/usr/bin/env python3
"""Recover and process queued meeting export jobs."""

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
from services.meeting_export import MeetingExportService  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker  # noqa: E402
from sqlmodel.ext.asyncio.session import AsyncSession  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process durable SIQ meeting export jobs.")
    parser.add_argument(
        "--worker-id",
        default=os.getenv(
            "SIQ_MEETING_EXPORT_WORKER_ID",
            f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}",
        ),
    )
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


async def _run(arguments: argparse.Namespace) -> None:
    factory = async_sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(name, stop.set)
        except NotImplementedError:
            pass

    while not stop.is_set():
        async with factory() as session:
            service = MeetingExportService(session)
            job_id = await service.claim_next(arguments.worker_id)
            if job_id:
                await service.process_claimed(job_id, arguments.worker_id)
        if arguments.once:
            return
        if not job_id:
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.0)
            except TimeoutError:
                pass


def main() -> None:
    arguments = _arguments()
    logging.basicConfig(
        level=os.getenv("SIQ_MEETING_EXPORT_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    create_db_and_tables()
    asyncio.run(_run(arguments))


if __name__ == "__main__":
    main()
