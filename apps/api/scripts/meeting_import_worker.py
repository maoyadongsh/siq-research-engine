#!/usr/bin/env python3
"""Run resumable meeting ingestion and optional import-only post-processing."""

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
from services.meeting_ai_worker import MeetingAIWorker, MeetingAIWorkerConfig  # noqa: E402
from services.meeting_audio_store import MeetingAudioStore  # noqa: E402
from services.meeting_contracts import AudioSource, MeetingJobKind  # noqa: E402
from services.meeting_hermes_runner import MeetingHermesRunner, MeetingHermesTargetPool  # noqa: E402
from services.meeting_import_config import MeetingImportSettings  # noqa: E402
from services.meeting_import_storage import MeetingImportStorage  # noqa: E402
from services.meeting_import_worker import MeetingImportWorker  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker  # noqa: E402
from sqlmodel.ext.asyncio.session import AsyncSession  # noqa: E402

WORKER_MODES = ("all", "ingest", "postprocess")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process durable SIQ meeting recording imports.")
    parser.add_argument(
        "--worker-id",
        default=os.getenv(
            "SIQ_MEETING_IMPORT_WORKER_ID",
            f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}",
        ),
    )
    parser.add_argument(
        "--mode",
        choices=WORKER_MODES,
        default=os.getenv("SIQ_MEETING_IMPORT_WORKER_MODE", "all"),
        help=(
            "Run import ingestion, import-only final-ASR post-processing, or both. "
            "The default 'all' preserves the legacy behavior."
        ),
    )
    parser.add_argument("--once", action="store_true", help="Process at most one selected job.")
    return parser.parse_args()


async def _run(arguments: argparse.Namespace) -> None:
    settings = MeetingImportSettings.from_env()
    if not settings.operational:
        raise RuntimeError(f"meeting import is disabled or invalid: {', '.join(settings.errors)}")
    factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    ingest = None
    if arguments.mode in {"all", "ingest"}:
        ingest = MeetingImportWorker(
            factory,
            MeetingImportStorage(settings.root),
            MeetingAudioStore(),
            settings,
            worker_id=arguments.worker_id,
        )
    postprocess = None
    if arguments.mode in {"all", "postprocess"}:
        postprocess = MeetingAIWorker(
            factory,
            MeetingHermesRunner(MeetingHermesTargetPool([])),
            worker_id=f"{arguments.worker_id}:postprocess",
            config=MeetingAIWorkerConfig.from_env(),
            job_kinds={
                MeetingJobKind.FINAL_TRANSCRIPT.value,
                MeetingJobKind.SPEAKER_RECLUSTER.value,
            },
            audio_sources={AudioSource.IMPORT.value},
        )
    if arguments.once:
        if ingest is not None and await ingest.run_once():
            return
        if postprocess is not None:
            await postprocess.run_once()
        return

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(name, stop.set)
        except NotImplementedError:
            pass
    poll_seconds = float(os.getenv("SIQ_MEETING_IMPORT_POLL_SECONDS", "1"))
    if poll_seconds <= 0:
        raise ValueError("SIQ_MEETING_IMPORT_POLL_SECONDS must be positive")
    while not stop.is_set():
        worked = await ingest.run_once() if ingest is not None else False
        if postprocess is not None:
            worked = await postprocess.run_once() or worked
        if worked:
            continue
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
        except TimeoutError:
            pass


def main() -> None:
    arguments = _arguments()
    logging.basicConfig(
        level=os.getenv("SIQ_MEETING_IMPORT_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    create_db_and_tables()
    asyncio.run(_run(arguments))


if __name__ == "__main__":
    main()
