#!/usr/bin/env python3
"""Run consent-bound meeting voiceprint enrollment jobs."""

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
from services.meeting_config import MeetingSettings  # noqa: E402
from services.meeting_repository import MeetingRepository  # noqa: E402
from services.meeting_voiceprint_worker import (  # noqa: E402
    HttpSpeakerEmbeddingClient,
    MeetingAudioStoreReader,
    MeetingVoiceprintRepositoryAdapter,
    MeetingVoiceprintWorker,
    VoiceprintKeyring,
    VoiceprintThresholdPolicy,
    VoiceprintWorkerSettings,
)
from sqlmodel.ext.asyncio.session import AsyncSession  # noqa: E402

logger = logging.getLogger("siq.meeting.voiceprint_worker")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process durable SIQ meeting voiceprint jobs.")
    parser.add_argument(
        "--worker-id",
        default=os.getenv(
            "SIQ_MEETING_VOICEPRINT_WORKER_ID",
            f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}",
        ),
        help="Unique lease owner identity.",
    )
    parser.add_argument("--once", action="store_true", help="Process at most one job and exit.")
    return parser.parse_args()


def _poll_seconds() -> float:
    try:
        value = float(os.getenv("SIQ_MEETING_VOICEPRINT_POLL_SECONDS", "1"))
    except ValueError as exc:
        raise ValueError("SIQ_MEETING_VOICEPRINT_POLL_SECONDS must be numeric") from exc
    if value < 0.1 or value > 60:
        raise ValueError("SIQ_MEETING_VOICEPRINT_POLL_SECONDS must be between 0.1 and 60")
    return value


async def _run(arguments: argparse.Namespace) -> None:
    meeting_settings = MeetingSettings.from_env()
    if not meeting_settings.operational or not meeting_settings.voiceprint_enabled:
        raise RuntimeError("meeting voiceprint worker is disabled or misconfigured")

    worker_settings = VoiceprintWorkerSettings.from_env(worker_id=arguments.worker_id)
    endpoint = os.getenv(
        "SIQ_MEETING_SPEAKER_EMBEDDING_URL",
        "http://127.0.0.1:8901/v1/speaker/embedding",
    ).strip()
    service_token = os.getenv("SIQ_MEETING_SPEECH_INTERNAL_SERVICE_TOKEN", "").strip()
    keyring = VoiceprintKeyring.from_env()
    if keyring.tombstones is None:
        raise RuntimeError("voiceprint tombstone ledger is required")
    embedding_client = HttpSpeakerEmbeddingClient(
        endpoint=endpoint,
        service_token=service_token,
        expected_encoder_ref=worker_settings.expected_encoder_ref,
    )
    threshold_json = os.getenv("SIQ_MEETING_VOICEPRINT_THRESHOLDS_JSON", "").strip()
    if not threshold_json:
        raise RuntimeError("SIQ_MEETING_VOICEPRINT_THRESHOLDS_JSON is required")
    thresholds = VoiceprintThresholdPolicy.from_json(threshold_json)
    audio_reader = MeetingAudioStoreReader(MeetingAudioStore())
    poll_seconds = _poll_seconds()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(name, stop.set)
        except NotImplementedError:
            pass

    tombstone_count = await asyncio.to_thread(keyring.tombstones.initialize)
    async with AsyncSession(async_engine, expire_on_commit=False) as session:
        reconciliation = await MeetingRepository(
            session,
            voiceprint_tombstones=keyring.tombstones,
        ).reconcile_voiceprint_tombstones()
    if reconciliation["remaining"]:
        raise RuntimeError("voiceprint tombstone reconciliation is incomplete")
    logger.info(
        "voiceprint tombstone reconciliation ledger_entries=%s seen=%s purged=%s remaining=%s",
        tombstone_count,
        reconciliation["seen"],
        reconciliation["purged"],
        reconciliation["remaining"],
    )

    try:
        while not stop.is_set():
            async with AsyncSession(async_engine, expire_on_commit=False) as session:
                worker = MeetingVoiceprintWorker(
                    repository=MeetingVoiceprintRepositoryAdapter(
                        MeetingRepository(
                            session,
                            voiceprint_tombstones=keyring.tombstones,
                        )
                    ),
                    audio_reader=audio_reader,
                    embedding_client=embedding_client,
                    keyring=keyring,
                    settings=worker_settings,
                    thresholds=thresholds,
                )
                result = await worker.run_once()
            logger.info(
                "voiceprint worker result status=%s job_id=%s error_code=%s",
                result.status,
                result.job_id,
                result.public_error_code,
            )
            if arguments.once:
                return
            if result.status != "succeeded":
                try:
                    await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
                except TimeoutError:
                    pass
    finally:
        await embedding_client.aclose()


def main() -> None:
    arguments = _arguments()
    logging.basicConfig(
        level=os.getenv("SIQ_MEETING_VOICEPRINT_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    meeting_settings = MeetingSettings.from_env()
    if not meeting_settings.operational or not meeting_settings.voiceprint_enabled:
        raise SystemExit("meeting voiceprint worker is disabled or misconfigured")
    create_db_and_tables()
    asyncio.run(_run(arguments))


if __name__ == "__main__":
    main()
