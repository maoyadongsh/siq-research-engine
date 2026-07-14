import hashlib
import sqlite3
import wave
from datetime import timedelta
from pathlib import Path

import anyio
import pytest
from services.auth_service import User, UserRole
from services.meeting_audio_store import MeetingAudioStore
from services.meeting_config import MeetingSettings
from services.meeting_contracts import (
    MEETING_TABLES,
    AudioChunkState,
    MeetingAudioChunk,
    MeetingJob,
    MeetingSession,
    MeetingStreamTicket,
    utcnow,
)
from services.meeting_native_capture_config import MeetingNativeCaptureSettings
from services.meeting_native_capture_contracts import (
    MEETING_NATIVE_CAPTURE_TABLES,
    MeetingNativeCapture,
    MeetingNativeCaptureAudioLink,
    MeetingNativeCaptureBatch,
    MeetingNativeCaptureEpoch,
    MeetingNativeCaptureFinalization,
    NativeCaptureBatchMetadata,
    NativeCaptureBoundaryRequest,
    NativeCaptureCreateRequest,
    NativeCaptureGapRequest,
    NativeCaptureManifestEntry,
    native_capture_manifest_sha256,
)
from services.meeting_native_capture_service import (
    MeetingNativeCaptureConflict,
    MeetingNativeCaptureNotFound,
    MeetingNativeCaptureRepository,
)
from services.meeting_native_capture_storage import MeetingNativeCaptureStorage
from services.meeting_native_capture_worker import (
    MeetingNativeCaptureFinalizationWorker,
    NativeCaptureFinalizationConflict,
)
from services.meeting_repository import MeetingResourceNotFound
from services.meeting_stream_ticket import MeetingStreamTicketService
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=[
                    User.__table__,
                    *[model.__table__ for model in MEETING_TABLES],
                    *[model.__table__ for model in MEETING_NATIVE_CAPTURE_TABLES],
                ],
            )
        )
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _seed(factory):
    async with factory() as session:
        session.add_all(
            [
                User(
                    id=7,
                    username="native-worker-owner",
                    email="native-worker-owner@example.test",
                    hashed_password="x",
                    full_name="Native Worker Owner",
                    role=UserRole.ANALYST,
                    is_active=True,
                ),
                User(
                    id=8,
                    username="native-worker-other",
                    email="native-worker-other@example.test",
                    hashed_password="x",
                    full_name="Native Worker Other",
                    role=UserRole.ANALYST,
                    is_active=True,
                ),
            ]
        )
        meeting = MeetingSession(
            owner_user_id=7,
            title="Native worker",
            state="live",
            stream_epoch=1,
            ai_enabled=False,
            selection_mode="none",
        )
        other = MeetingSession(
            owner_user_id=8,
            title="Other native worker",
            state="live",
            stream_epoch=1,
            ai_enabled=False,
            selection_mode="none",
        )
        session.add_all([meeting, other])
        await session.commit()
        return meeting.id, other.id


def _settings(tmp_path: Path) -> MeetingNativeCaptureSettings:
    return MeetingNativeCaptureSettings(
        enabled=True,
        root=tmp_path / "native",
        token_ttl_seconds=300,
        max_batch_bytes=64_000,
        max_total_bytes=256_000,
        max_duration_seconds=60,
        max_active_per_owner=4,
        finalization_lease_seconds=30,
        finalization_retry_delay_seconds=0,
        finalization_poll_seconds=1,
        finalization_max_attempts=5,
    )


def _meeting_settings() -> MeetingSettings:
    return MeetingSettings(enabled=True, asr_enabled=False)


async def _stream(payload: bytes):
    yield payload[: len(payload) // 2]
    yield payload[len(payload) // 2 :]


def _metadata(payload: bytes, *, first: int, count: int, sequence: int):
    return NativeCaptureBatchMetadata(
        first_sample=first,
        sample_count=count,
        captured_monotonic_ns=1_000_000_000 + first * 62_500,
        encoding="pcm_s16le",
        sample_rate=16_000,
        channels=1,
        sha256=hashlib.sha256(payload).hexdigest(),
        manifest_revision=1,
        idempotency_key=f"worker-batch-{sequence}",
        content_length=len(payload),
    )


def _boundary(final_sequence: int, samples: int, entries: list[NativeCaptureManifestEntry]):
    return NativeCaptureBoundaryRequest(
        expected_epoch=1,
        final_sequence=final_sequence,
        recorded_through_sample=samples,
        manifest_revision=2,
        manifest_sha256=native_capture_manifest_sha256(
            expected_epoch=1,
            final_sequence=final_sequence,
            recorded_through_sample=samples,
            manifest_revision=2,
            entries=entries,
        ),
        manifest_entries=entries,
    )


async def _create_capture(factory, settings, meeting_id: str, key: str):
    async with factory() as session:
        repository = MeetingNativeCaptureRepository(
            session,
            MeetingNativeCaptureStorage(settings.root),
            settings,
            meeting_settings=_meeting_settings(),
        )
        return await repository.create(
            meeting_id,
            7,
            NativeCaptureCreateRequest(device_installation_id=f"device-installation-{key}"),
            idempotency_key=key,
        )


async def _put(factory, settings, meeting_id, capture_id, token, sequence, payload, first):
    async with factory() as session:
        return await MeetingNativeCaptureRepository(
            session,
            MeetingNativeCaptureStorage(settings.root),
            settings,
            meeting_settings=_meeting_settings(),
        ).put_batch(
            meeting_id,
            capture_id,
            token,
            1,
            sequence,
            _metadata(payload, first=first, count=len(payload) // 2, sequence=sequence),
            _stream(payload),
        )


async def _seal(factory, settings, meeting_id, capture_id, token, payloads):
    entries: list[NativeCaptureManifestEntry] = []
    first_sample = 0
    for sequence, payload in enumerate(payloads):
        sample_count = len(payload) // 2
        entries.append(
            NativeCaptureManifestEntry(
                sequence=sequence,
                first_sample=first_sample,
                sample_count=sample_count,
                captured_monotonic_ns=1_000_000_000 + first_sample * 62_500,
                sha256=hashlib.sha256(payload).hexdigest(),
            )
        )
        first_sample += sample_count
    async with factory() as session:
        return await MeetingNativeCaptureRepository(
            session,
            MeetingNativeCaptureStorage(settings.root),
            settings,
            meeting_settings=_meeting_settings(),
        ).seal(meeting_id, capture_id, token, _boundary(len(entries) - 1, first_sample, entries))


def _worker(factory, settings, audio_root: Path, worker_id: str = "native-worker"):
    return MeetingNativeCaptureFinalizationWorker(
        factory,
        MeetingNativeCaptureStorage(settings.root),
        MeetingAudioStore(audio_root),
        settings,
        worker_id=worker_id,
    )


def test_native_worker_reuses_canonical_audio_and_queues_final_transcript_after_wav(tmp_path):
    async def scenario():
        engine, factory = await _database(tmp_path / "native-worker.db")
        meeting_id, _ = await _seed(factory)
        settings = _settings(tmp_path)
        audio_root = tmp_path / "audio"
        created = await _create_capture(factory, settings, meeting_id, "worker-complete")
        payload = b"\x15\x00" * 1_600
        await _put(factory, settings, meeting_id, created.capture.id, created.capture_token, 0, payload, 0)
        sealed = await _seal(
            factory,
            settings,
            meeting_id,
            created.capture.id,
            created.capture_token,
            [payload],
        )
        assert sealed.checkpoint.finalization_checkpoint.packaging_state == "queued"

        audio_store = MeetingAudioStore(audio_root)
        persisted = audio_store.persist_chunk(7, meeting_id, 1, 99, payload)
        async with factory() as session:
            existing = MeetingAudioChunk(
                meeting_id=meeting_id,
                stream_epoch=1,
                sequence=99,
                start_ms=0,
                duration_ms=100,
                storage_key=persisted.storage_key,
                sha256=persisted.sha256,
                byte_size=persisted.byte_size,
                state=AudioChunkState.VERIFIED.value,
            )
            session.add(existing)
            await session.commit()

        worker = _worker(factory, settings, audio_root)
        assert await worker.run_once() is True
        assert await worker.run_once() is False
        async with factory() as session:
            finalization = (await session.exec(select(MeetingNativeCaptureFinalization))).one()
            capture = await session.get(MeetingNativeCapture, created.capture.id)
            chunks = list((await session.exec(select(MeetingAudioChunk))).all())
            links = list((await session.exec(select(MeetingNativeCaptureAudioLink))).all())
            jobs = list((await session.exec(select(MeetingJob))).all())
            meeting = await session.get(MeetingSession, meeting_id)
            assert finalization.state == "ready"
            assert finalization.wav_byte_size and finalization.wav_byte_size >= 44
            assert finalization.wav_sha256
            assert finalization.final_transcript_job_id == jobs[0].id
            assert capture.server_playback_state == "ready"
            assert len(chunks) == len(links) == len(jobs) == 1
            assert links[0].audio_chunk_id == existing.id
            assert links[0].source_sha256 == hashlib.sha256(payload).hexdigest()
            assert meeting.state == "stopped"
            assert meeting.postprocess_state == "queued"
            assert jobs[0].idempotency_key == f"{meeting_id}:finalize:v1"

            checkpoint = await MeetingNativeCaptureRepository(
                session,
                MeetingNativeCaptureStorage(settings.root),
                settings,
                meeting_settings=_meeting_settings(),
            ).checkpoint(meeting_id, created.capture.id, created.capture_token)
            assert checkpoint.finalization_checkpoint.packaging_state == "ready"
            assert checkpoint.finalization_checkpoint.server_playback_state == "ready"
            assert checkpoint.finalization_checkpoint.wav_sha256 == finalization.wav_sha256

            with pytest.raises(MeetingResourceNotFound):
                await MeetingStreamTicketService(session, _meeting_settings()).issue_playback(
                    meeting_id,
                    8,
                    origin="https://siq.example",
                )
            raw_ticket, _, _ = await MeetingStreamTicketService(
                session,
                _meeting_settings(),
            ).issue_playback(meeting_id, 7, origin="https://siq.example")
            assert raw_ticket
            assert len((await session.exec(select(MeetingStreamTicket))).all()) == 1
        wav = audio_store.ready_packed_audio_path(7, meeting_id)
        assert wav is not None
        with wave.open(str(wav), "rb") as source:
            assert source.getnframes() == 1_600
        await engine.dispose()

    anyio.run(scenario)


def test_missing_batch_stays_pending_upload_until_backlog_is_durable(tmp_path):
    async def scenario():
        engine, factory = await _database(tmp_path / "native-pending.db")
        meeting_id, _ = await _seed(factory)
        settings = _settings(tmp_path)
        created = await _create_capture(factory, settings, meeting_id, "worker-pending")
        first = b"\x21\x00" * 1_600
        second = b"\x22\x00" * 1_600
        await _put(factory, settings, meeting_id, created.capture.id, created.capture_token, 0, first, 0)
        sealed = await _seal(
            factory,
            settings,
            meeting_id,
            created.capture.id,
            created.capture_token,
            [first, second],
        )
        assert sealed.capture.server_playback_state == "pending_upload"
        assert sealed.checkpoint.ingest_checkpoint.missing_sample_ranges == [{"start": 1_600, "end": 3_200}]
        worker = _worker(factory, settings, tmp_path / "audio-pending")
        assert await worker.run_once() is False
        assert MeetingAudioStore(tmp_path / "audio-pending").ready_packed_audio_path(7, meeting_id) is None
        async with factory() as session:
            assert not (await session.exec(select(MeetingJob))).all()
            finalization = (await session.exec(select(MeetingNativeCaptureFinalization))).one()
            assert finalization.state == "pending_upload"

        response = await _put(
            factory,
            settings,
            meeting_id,
            created.capture.id,
            created.capture_token,
            1,
            second,
            1_600,
        )
        assert response.checkpoint.finalization_checkpoint.packaging_state == "queued"
        assert await worker.run_once() is True
        async with factory() as session:
            finalization = (await session.exec(select(MeetingNativeCaptureFinalization))).one()
            assert finalization.state == "ready"
            assert len((await session.exec(select(MeetingAudioChunk))).all()) == 2
        await engine.dispose()

    anyio.run(scenario)


def test_explicit_gap_is_owner_bound_and_packs_silence_without_accepting_late_audio(tmp_path):
    async def scenario():
        engine, factory = await _database(tmp_path / "native-gap.db")
        meeting_id, other_meeting_id = await _seed(factory)
        settings = _settings(tmp_path)
        created = await _create_capture(factory, settings, meeting_id, "worker-gap")
        first = b"\x31\x00" * 1_600
        await _put(factory, settings, meeting_id, created.capture.id, created.capture_token, 0, first, 0)
        await _seal(
            factory,
            settings,
            meeting_id,
            created.capture.id,
            created.capture_token,
            [first, b"\x00\x00" * 1_600],
        )
        request = NativeCaptureGapRequest(
            stream_epoch=1,
            from_sequence=1,
            to_sequence=1,
            start_sample=1_600,
            end_sample=3_200,
            reason="file_corrupt",
            manifest_revision=2,
        )
        async with factory() as session:
            repository = MeetingNativeCaptureRepository(
                session,
                MeetingNativeCaptureStorage(settings.root),
                settings,
                meeting_settings=_meeting_settings(),
            )
            with pytest.raises(MeetingNativeCaptureNotFound):
                await repository.record_gap(
                    other_meeting_id,
                    created.capture.id,
                    7,
                    request,
                    idempotency_key="gap-owner-bound",
                )
            with pytest.raises(MeetingNativeCaptureConflict) as mismatched_gap:
                await repository.record_gap(
                    meeting_id,
                    created.capture.id,
                    7,
                    request.model_copy(update={"start_sample": 0, "end_sample": 1_600}),
                    idempotency_key="gap-wrong-manifest-range",
                )
            assert mismatched_gap.value.code == "NATIVE_CAPTURE_GAP_MANIFEST_CONFLICT"
            recorded = await repository.record_gap(
                meeting_id,
                created.capture.id,
                7,
                request,
                idempotency_key="gap-owner-bound",
            )
            replayed = await repository.record_gap(
                meeting_id,
                created.capture.id,
                7,
                request,
                idempotency_key="gap-owner-bound",
            )
            assert recorded.checkpoint.ingest_checkpoint.ingest_complete is True
            assert recorded.checkpoint.finalization_checkpoint.has_unrecoverable_gaps is True
            assert replayed.replayed is True
        with pytest.raises(MeetingNativeCaptureConflict) as conflict:
            await _put(
                factory,
                settings,
                meeting_id,
                created.capture.id,
                created.capture_token,
                1,
                b"\x32\x00" * 1_600,
                1_600,
            )
        assert conflict.value.code == "NATIVE_CAPTURE_GAP_CONFLICT"

        audio_root = tmp_path / "audio-gap"
        assert await _worker(factory, settings, audio_root).run_once() is True
        wav = MeetingAudioStore(audio_root).ready_packed_audio_path(7, meeting_id)
        assert wav is not None
        with wave.open(str(wav), "rb") as source:
            assert source.getnframes() == 3_200
            payload = source.readframes(3_200)
        assert payload[:3_200] == first
        assert payload[3_200:] == bytes(3_200)
        await engine.dispose()

    anyio.run(scenario)


def test_worker_rejects_tampered_epoch_manifest_digest(tmp_path):
    async def scenario():
        engine, factory = await _database(tmp_path / "native-manifest-tamper.db")
        meeting_id, _ = await _seed(factory)
        settings = _settings(tmp_path)
        created = await _create_capture(factory, settings, meeting_id, "worker-manifest-tamper")
        payload = b"\x21\x00" * 1_600
        await _put(factory, settings, meeting_id, created.capture.id, created.capture_token, 0, payload, 0)
        await _seal(factory, settings, meeting_id, created.capture.id, created.capture_token, [payload])
        async with factory() as session:
            epoch = (await session.exec(select(MeetingNativeCaptureEpoch))).one()
            epoch.manifest_sha256 = "0" * 64
            session.add(epoch)
            await session.commit()

        assert await _worker(factory, settings, tmp_path / "audio-tampered-manifest").run_once() is True
        async with factory() as session:
            finalization = (await session.exec(select(MeetingNativeCaptureFinalization))).one()
            assert finalization.state == "failed"
            assert finalization.public_error_code == "NATIVE_CAPTURE_FINALIZATION_PROVENANCE_CONFLICT"
        await engine.dispose()

    anyio.run(scenario)


def test_worker_rejects_non_contiguous_epoch_chain():
    first_digest = native_capture_manifest_sha256(
        expected_epoch=1,
        final_sequence=-1,
        recorded_through_sample=0,
        manifest_revision=1,
        entries=[],
    )
    third_digest = native_capture_manifest_sha256(
        expected_epoch=3,
        final_sequence=-1,
        recorded_through_sample=0,
        manifest_revision=1,
        entries=[],
    )
    capture = MeetingNativeCapture(
        id="capture-non-contiguous",
        meeting_id="meeting-non-contiguous",
        owner_user_id=7,
        device_installation_hash="0" * 64,
        create_idempotency_key="create-non-contiguous",
        create_request_hash="1" * 64,
        state="sealed",
        current_epoch=3,
        max_total_bytes=1,
        max_duration_samples=1,
        sealed_through_sample=0,
        seal_manifest_revision=1,
        seal_manifest_sha256=third_digest,
    )
    finalization = MeetingNativeCaptureFinalization(
        capture_id=capture.id,
        meeting_id=capture.meeting_id,
        owner_user_id=capture.owner_user_id,
    )
    epochs = [
        MeetingNativeCaptureEpoch(
            capture_id=capture.id,
            stream_epoch=1,
            state="rolled_over",
            last_sequence=-1,
            recorded_through_sample=0,
            manifest_revision=1,
            manifest_sha256=first_digest,
        ),
        MeetingNativeCaptureEpoch(
            capture_id=capture.id,
            stream_epoch=3,
            state="sealed",
            last_sequence=-1,
            recorded_through_sample=0,
            manifest_revision=1,
            manifest_sha256=third_digest,
            rollover_from_epoch=2,
        ),
    ]

    with pytest.raises(NativeCaptureFinalizationConflict):
        MeetingNativeCaptureFinalizationWorker._validate_frozen_manifest(
            finalization,
            capture,
            epochs,
            [],
            [],
            [],
        )


def test_worker_accepts_contiguous_epoch_chain_from_current_meeting_epoch():
    fifth_digest = native_capture_manifest_sha256(
        expected_epoch=5,
        final_sequence=-1,
        recorded_through_sample=0,
        manifest_revision=1,
        entries=[],
    )
    sixth_digest = native_capture_manifest_sha256(
        expected_epoch=6,
        final_sequence=-1,
        recorded_through_sample=0,
        manifest_revision=1,
        entries=[],
    )
    capture = MeetingNativeCapture(
        id="capture-current-epoch",
        meeting_id="meeting-current-epoch",
        owner_user_id=7,
        device_installation_hash="2" * 64,
        create_idempotency_key="create-current-epoch",
        create_request_hash="3" * 64,
        state="sealed",
        current_epoch=6,
        max_total_bytes=1,
        max_duration_samples=1,
        sealed_through_sample=0,
        seal_manifest_revision=1,
        seal_manifest_sha256=sixth_digest,
    )
    finalization = MeetingNativeCaptureFinalization(
        capture_id=capture.id,
        meeting_id=capture.meeting_id,
        owner_user_id=capture.owner_user_id,
    )
    epochs = [
        MeetingNativeCaptureEpoch(
            capture_id=capture.id,
            stream_epoch=5,
            state="rolled_over",
            last_sequence=-1,
            recorded_through_sample=0,
            manifest_revision=1,
            manifest_sha256=fifth_digest,
        ),
        MeetingNativeCaptureEpoch(
            capture_id=capture.id,
            stream_epoch=6,
            state="sealed",
            last_sequence=-1,
            recorded_through_sample=0,
            manifest_revision=1,
            manifest_sha256=sixth_digest,
            rollover_from_epoch=5,
        ),
    ]

    MeetingNativeCaptureFinalizationWorker._validate_frozen_manifest(
        finalization,
        capture,
        epochs,
        [],
        [],
        [],
    )


def test_worker_retries_symlink_and_hash_tamper_then_recovers_idempotently(tmp_path):
    async def scenario():
        engine, factory = await _database(tmp_path / "native-retry.db")
        meeting_id, _ = await _seed(factory)
        settings = _settings(tmp_path)
        created = await _create_capture(factory, settings, meeting_id, "worker-retry")
        payload = b"\x41\x00" * 1_600
        await _put(factory, settings, meeting_id, created.capture.id, created.capture_token, 0, payload, 0)
        await _seal(
            factory,
            settings,
            meeting_id,
            created.capture.id,
            created.capture_token,
            [payload],
        )
        async with factory() as session:
            batch = (await session.exec(select(MeetingNativeCaptureBatch))).one()
        storage = MeetingNativeCaptureStorage(settings.root)
        source = storage.resolve_storage_key(batch.storage_key)
        outside = tmp_path / "outside-native.pcm"
        outside.write_bytes(payload)
        source.unlink()
        source.symlink_to(outside)
        worker = _worker(factory, settings, tmp_path / "audio-retry")
        assert await worker.run_once() is True
        async with factory() as session:
            finalization = (await session.exec(select(MeetingNativeCaptureFinalization))).one()
            assert finalization.state == "retry_wait"
            assert finalization.public_error_code == "NATIVE_CAPTURE_STORAGE_KEY_INVALID"
            assert not (await session.exec(select(MeetingJob))).all()

        source.unlink()
        source.write_bytes(b"\x42\x00" * 1_600)
        assert await worker.run_once() is True
        async with factory() as session:
            finalization = (await session.exec(select(MeetingNativeCaptureFinalization))).one()
            assert finalization.state == "retry_wait"
            assert finalization.public_error_code == "NATIVE_CAPTURE_BATCH_INTEGRITY_FAILED"

        source.write_bytes(payload)
        assert await worker.run_once() is True
        assert await worker.run_once() is False
        async with factory() as session:
            finalization = (await session.exec(select(MeetingNativeCaptureFinalization))).one()
            assert finalization.state == "ready"
            assert finalization.attempt == 3
            assert len((await session.exec(select(MeetingAudioChunk))).all()) == 1
            assert len((await session.exec(select(MeetingNativeCaptureAudioLink))).all()) == 1
            assert len((await session.exec(select(MeetingJob))).all()) == 1
        await engine.dispose()

    anyio.run(scenario)


def test_expired_processing_lease_is_reclaimed_even_on_last_attempt(tmp_path):
    async def scenario():
        engine, factory = await _database(tmp_path / "native-crash.db")
        meeting_id, _ = await _seed(factory)
        settings = _settings(tmp_path)
        created = await _create_capture(factory, settings, meeting_id, "worker-crash")
        payload = b"\x51\x00" * 1_600
        await _put(factory, settings, meeting_id, created.capture.id, created.capture_token, 0, payload, 0)
        await _seal(
            factory,
            settings,
            meeting_id,
            created.capture.id,
            created.capture_token,
            [payload],
        )
        async with factory() as session:
            finalization = (await session.exec(select(MeetingNativeCaptureFinalization))).one()
            finalization.state = "processing"
            finalization.attempt = finalization.max_attempts
            finalization.lease_owner = "dead-worker"
            finalization.lease_until = utcnow() - timedelta(seconds=1)
            session.add(finalization)
            await session.commit()
        worker = _worker(factory, settings, tmp_path / "audio-crash", "replacement-worker")
        claimed = await worker.claim_next()
        assert claimed is not None
        assert claimed.attempt == claimed.max_attempts + 1
        await worker._process_with_heartbeat(claimed.id, claimed.attempt)
        async with factory() as session:
            finalization = (await session.exec(select(MeetingNativeCaptureFinalization))).one()
            assert finalization.state == "ready"
            assert finalization.final_transcript_job_id is not None
        await engine.dispose()

    anyio.run(scenario)


def test_reclaimed_attempt_fences_stale_worker_with_same_worker_id(tmp_path):
    async def scenario():
        engine, factory = await _database(tmp_path / "native-attempt-fence.db")
        meeting_id, _ = await _seed(factory)
        settings = _settings(tmp_path)
        created = await _create_capture(factory, settings, meeting_id, "worker-attempt-fence")
        payload = b"\x61\x00" * 1_600
        await _put(factory, settings, meeting_id, created.capture.id, created.capture_token, 0, payload, 0)
        await _seal(
            factory,
            settings,
            meeting_id,
            created.capture.id,
            created.capture_token,
            [payload],
        )
        stale_worker = _worker(factory, settings, tmp_path / "audio-attempt-fence", "shared-worker")
        stale_claim = await stale_worker.claim_next()
        assert stale_claim is not None
        async with factory() as session:
            finalization = await session.get(MeetingNativeCaptureFinalization, stale_claim.id)
            assert finalization is not None
            finalization.lease_until = utcnow() - timedelta(seconds=1)
            session.add(finalization)
            await session.commit()

        replacement = _worker(factory, settings, tmp_path / "audio-attempt-fence", "shared-worker")
        current_claim = await replacement.claim_next()
        assert current_claim is not None
        assert current_claim.attempt == stale_claim.attempt + 1

        await stale_worker._process_with_heartbeat(stale_claim.id, stale_claim.attempt)
        async with factory() as session:
            finalization = await session.get(MeetingNativeCaptureFinalization, stale_claim.id)
            assert finalization is not None
            assert finalization.state == "processing"
            assert finalization.attempt == current_claim.attempt
            assert finalization.lease_owner == "shared-worker"
            assert not (await session.exec(select(MeetingAudioChunk))).all()
            assert not (await session.exec(select(MeetingNativeCaptureAudioLink))).all()
        assert MeetingAudioStore(tmp_path / "audio-attempt-fence").ready_packed_audio_path(7, meeting_id) is None

        await replacement._process_with_heartbeat(current_claim.id, current_claim.attempt)
        async with factory() as session:
            finalization = await session.get(MeetingNativeCaptureFinalization, current_claim.id)
            assert finalization is not None and finalization.state == "ready"
        await engine.dispose()

    anyio.run(scenario)


def test_worker_removes_its_wav_when_retention_fence_wins_after_pack(monkeypatch, tmp_path):
    async def scenario():
        database_path = tmp_path / "native-pack-fence.db"
        engine, factory = await _database(database_path)
        meeting_id, _ = await _seed(factory)
        settings = _settings(tmp_path)
        created = await _create_capture(factory, settings, meeting_id, "worker-pack-fence")
        payload = b"\x71\x00" * 1_600
        await _put(factory, settings, meeting_id, created.capture.id, created.capture_token, 0, payload, 0)
        await _seal(
            factory,
            settings,
            meeting_id,
            created.capture.id,
            created.capture_token,
            [payload],
        )
        worker = _worker(factory, settings, tmp_path / "audio-pack-fence")
        claim = await worker.claim_next()
        assert claim is not None
        original_pack = worker.audio_store.pack_wav

        def pack_then_revoke(*args, **kwargs):
            path = original_pack(*args, **kwargs)
            with sqlite3.connect(database_path) as connection:
                connection.execute(
                    "UPDATE meeting_native_capture_finalizations "
                    "SET state = 'failed', lease_owner = NULL, lease_until = NULL, "
                    "public_error_code = 'MEETING_AUDIO_RETENTION_EXPIRED' WHERE id = ?",
                    (claim.id,),
                )
                connection.execute(
                    "UPDATE meeting_native_captures "
                    "SET state = 'revoked', server_playback_state = 'not_ready' WHERE id = ?",
                    (created.capture.id,),
                )
            return path

        monkeypatch.setattr(worker.audio_store, "pack_wav", pack_then_revoke)
        await worker._process_with_heartbeat(claim.id, claim.attempt)

        assert MeetingAudioStore(tmp_path / "audio-pack-fence").ready_packed_audio_path(7, meeting_id) is None
        async with factory() as session:
            finalization = await session.get(MeetingNativeCaptureFinalization, claim.id)
            capture = await session.get(MeetingNativeCapture, created.capture.id)
            assert finalization is not None and finalization.state == "failed"
            assert finalization.lease_owner is None
            assert capture is not None and capture.state == "revoked"
            assert not (await session.exec(select(MeetingJob))).all()
        await engine.dispose()

    anyio.run(scenario)
