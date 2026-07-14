#!/usr/bin/env python3
"""Run the meeting AI worker as an independent process."""

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
from services.meeting_ai_worker import (  # noqa: E402
    MeetingAIWorker,
    MeetingAIWorkerConfig,
)
from services.meeting_contracts import MeetingJobKind  # noqa: E402
from services.meeting_hermes_runner import MeetingHermesRunner, MeetingHermesTargetPool  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker  # noqa: E402
from sqlmodel.ext.asyncio.session import AsyncSession  # noqa: E402

WORKER_LANES: dict[str, set[str] | None] = {
    "all": None,
    "finalization": {
        MeetingJobKind.FINAL_TRANSCRIPT.value,
        MeetingJobKind.SPEAKER_RECLUSTER.value,
    },
    "minutes": {
        MeetingJobKind.FINAL_MINUTES.value,
        MeetingJobKind.ROLLING_MINUTES.value,
    },
    "correction": {MeetingJobKind.CORRECTION.value},
}


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process durable SIQ meeting correction and minutes jobs.")
    parser.add_argument(
        "--worker-id",
        default=os.getenv(
            "SIQ_MEETING_AI_WORKER_ID",
            f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}",
        ),
        help="Unique lease owner identity (defaults to host, pid, and random suffix).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process at most one eligible job and exit.",
    )
    parser.add_argument(
        "--lane",
        choices=tuple(WORKER_LANES),
        default=os.getenv("SIQ_MEETING_AI_WORKER_LANE", "all"),
        help=("Restrict claims to one workload lane. The default 'all' preserves the legacy single-worker behavior."),
    )
    return parser.parse_args()


async def _run(arguments: argparse.Namespace) -> None:
    factory = async_sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    job_kinds = WORKER_LANES[arguments.lane]
    runner = (
        MeetingHermesRunner(MeetingHermesTargetPool([])) if arguments.lane == "finalization" else MeetingHermesRunner()
    )
    worker = MeetingAIWorker(
        factory,
        runner,
        worker_id=arguments.worker_id,
        config=MeetingAIWorkerConfig.from_env(),
        job_kinds=job_kinds,
    )
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
        level=os.getenv("SIQ_MEETING_AI_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    create_db_and_tables()
    asyncio.run(_run(arguments))


if __name__ == "__main__":
    main()
