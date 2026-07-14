#!/usr/bin/env python3
"""Run the durable meeting deletion and opt-in audio-retention worker."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import socket
import sys
from pathlib import Path
from typing import Sequence
from uuid import uuid4

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from database import async_engine, create_db_and_tables  # noqa: E402
from services.meeting_retention import (  # noqa: E402
    MeetingDeletionLedger,
    MeetingRetentionSettings,
    MeetingRetentionWorker,
    MeetingStoragePurger,
)
from sqlalchemy.ext.asyncio import async_sessionmaker  # noqa: E402
from sqlmodel.ext.asyncio.session import AsyncSession  # noqa: E402


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--worker-id",
        default=os.getenv(
            "SIQ_MEETING_DELETE_WORKER_ID",
            f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}",
        ),
        help="Unique durable-job lease owner identity.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process at most one eligible delete job, optionally run one retention scan, and exit.",
    )
    return parser.parse_args(argv)


async def _run(arguments: argparse.Namespace, settings: MeetingRetentionSettings) -> dict[str, object]:
    factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    worker = MeetingRetentionWorker(
        factory,
        ledger=MeetingDeletionLedger.from_env(),
        purger=MeetingStoragePurger(),
        worker_id=arguments.worker_id,
        settings=settings,
    )
    if arguments.once:
        await worker.initialize()
        claimed = await worker.run_once()
        expired_audio_count = await worker.scan_expired_audio()
        return {
            "schema_version": "siq.meeting.retention_worker.v1",
            "status": "completed",
            "delete_job_claimed": claimed,
            "expired_audio_count": expired_audio_count,
        }

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
        except NotImplementedError:
            pass
    await worker.run_until_stopped(stop)
    return {
        "schema_version": "siq.meeting.retention_worker.v1",
        "status": "stopped",
    }


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _arguments(argv)
    settings = MeetingRetentionSettings.from_env()
    if not settings.worker_enabled:
        print(
            json.dumps(
                {
                    "schema_version": "siq.meeting.retention_worker.v1",
                    "status": "disabled",
                    "error_code": "MEETING_DELETE_WORKER_DISABLED",
                },
                sort_keys=True,
            )
        )
        return 2
    try:
        create_db_and_tables()
        report = asyncio.run(_run(arguments, settings))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "schema_version": "siq.meeting.retention_worker.v1",
                    "status": "failed",
                    "error_code": getattr(exc, "code", type(exc).__name__),
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
