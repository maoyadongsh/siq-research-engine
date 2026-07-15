from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers.meeting_imports import router as import_router
from services.auth_dependencies import get_current_user
from services.auth_service import User, UserRole
from services.meeting_ai_worker import MeetingAIWorker, MeetingAIWorkerConfig
from services.meeting_audio_store import MeetingAudioStore
from services.meeting_contracts import (
    MEETING_TABLES,
    MeetingAudioChunk,
    MeetingJob,
    MeetingJobKind,
    MeetingJobState,
    MeetingSession,
    utcnow,
)
from services.meeting_database import get_meeting_async_session as get_async_session
from services.meeting_finalization import FINAL_ASR_INDEPENDENT_PROTOCOL, FinalASRSegment, FinalizationAnalysis
from services.meeting_hermes_runner import MeetingHermesRunner, MeetingHermesTargetPool
from services.meeting_import_config import MeetingImportSettings
from services.meeting_import_contracts import (
    MEETING_IMPORT_TABLES,
    MeetingImportCompleteRequest,
    MeetingImportCreateRequest,
    MeetingImportState,
    MeetingImportUpload,
)
from services.meeting_import_service import (
    MeetingImportConflict,
    MeetingImportInvalid,
    MeetingImportNotFound,
    MeetingImportQuotaExceeded,
    MeetingImportRepository,
)
from services.meeting_import_storage import MeetingImportStorage, MeetingImportStorageError
from services.meeting_import_worker import MeetingImportCancelled, MeetingImportWorker
from services.meeting_repository import MeetingRepository
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession


async def _database():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=[
                    User.__table__,
                    *[model.__table__ for model in MEETING_TABLES],
                    *[model.__table__ for model in MEETING_IMPORT_TABLES],
                ],
            )
        )
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _settings(tmp_path: Path, **overrides) -> MeetingImportSettings:
    values = {
        "enabled": True,
        "root": tmp_path / "imports",
        "max_file_bytes": 1024,
        "owner_quota_bytes": 2048,
        "max_active_per_owner": 3,
        "min_chunk_bytes": 1,
        "max_chunk_bytes": 16,
        "max_duration_seconds": 60,
        "upload_ttl_seconds": 3600,
        "lease_seconds": 30,
        "retry_base_seconds": 1,
        "ffprobe_timeout_seconds": 5,
        "ffmpeg_timeout_seconds": 5,
    }
    values.update(overrides)
    return MeetingImportSettings(**values)


def _request(filename: str, size: int, chunk_size: int = 4) -> MeetingImportCreateRequest:
    return MeetingImportCreateRequest(
        filename=filename,
        media_type="audio/wav",
        file_size=size,
        chunk_size=chunk_size,
        title="导入录音",
        ai_enabled=False,
        model_selection={"mode": "none", "model_ref": None, "fallback_policy": "disabled"},
    )


async def _stream(payload: bytes):
    midpoint = max(1, len(payload) // 2)
    yield payload[:midpoint]
    yield payload[midpoint:]


async def _seed_user(factory, user_id: int = 7):
    async with factory() as session:
        session.add(
            User(
                id=user_id,
                username=f"import-user-{user_id}",
                email=f"import-user-{user_id}@example.test",
                hashed_password="x",
                full_name="Import User",
                role=UserRole.ANALYST,
                is_active=True,
            )
        )
        await session.commit()


def test_resumable_chunks_are_ordered_idempotent_owner_scoped_and_path_safe(tmp_path):
    async def scenario():
        engine, factory = await _database()
        await _seed_user(factory)
        settings = _settings(tmp_path)
        storage = MeetingImportStorage(settings.root)
        async with factory() as session:
            repository = MeetingImportRepository(session, storage, settings)
            created, replayed = await repository.create(
                7,
                _request("../../board.wav", 6),
                idempotency_key="upload-one",
            )
            assert replayed is False
            assert created.filename == "board.wav"
            upload_id = created.id

            with pytest.raises(MeetingImportNotFound):
                await repository.get_owned(upload_id, 8)
            with pytest.raises(MeetingImportConflict) as out_of_order:
                await repository.put_chunk(
                    upload_id,
                    7,
                    ordinal=1,
                    byte_offset=4,
                    sha256=hashlib.sha256(b"ef").hexdigest(),
                    content_length=2,
                    stream=_stream(b"ef"),
                )
            assert out_of_order.value.code == "MEETING_IMPORT_CHUNK_OUT_OF_ORDER"

            first_payload = b"abcd"
            first = await repository.put_chunk(
                upload_id,
                7,
                ordinal=0,
                byte_offset=0,
                sha256=hashlib.sha256(first_payload).hexdigest(),
                content_length=len(first_payload),
                stream=_stream(first_payload),
            )
            assert first.next_ordinal == 1
            replay = await repository.put_chunk(
                upload_id,
                7,
                ordinal=0,
                byte_offset=0,
                sha256=hashlib.sha256(first_payload).hexdigest(),
                content_length=len(first_payload),
                stream=_stream(first_payload),
            )
            assert replay.replayed is True
            with pytest.raises(MeetingImportConflict) as duplicate_conflict:
                await repository.put_chunk(
                    upload_id,
                    7,
                    ordinal=0,
                    byte_offset=0,
                    sha256=hashlib.sha256(b"wxyz").hexdigest(),
                    content_length=4,
                    stream=_stream(b"wxyz"),
                )
            assert duplicate_conflict.value.code == "MEETING_IMPORT_CHUNK_CONFLICT"

            second_payload = b"ef"
            await repository.put_chunk(
                upload_id,
                7,
                ordinal=1,
                byte_offset=4,
                sha256=hashlib.sha256(second_payload).hexdigest(),
                content_length=len(second_payload),
                stream=_stream(second_payload),
            )
            complete = await repository.complete(upload_id, 7, MeetingImportCompleteRequest())
            assert complete.state == MeetingImportState.QUEUED.value
            assert complete.upload_progress == 1

        assert not (tmp_path / "board.wav").exists()
        with pytest.raises(MeetingImportStorageError):
            storage.resolve_storage_key("../outside")
        outside = tmp_path / "outside"
        outside.mkdir()
        (settings.root / "9").symlink_to(outside, target_is_directory=True)
        with pytest.raises(MeetingImportStorageError):
            await storage.store_chunk(
                9,
                "upload-through-symlink",
                0,
                hashlib.sha256(b"x").hexdigest(),
                _stream(b"x"),
                expected_size=1,
            )
        assert not (outside / "upload-through-symlink").exists()
        await engine.dispose()

    anyio.run(scenario)


def test_import_router_hides_cross_owner_uploads(tmp_path, monkeypatch):
    engine, factory = anyio.run(_database)
    anyio.run(_seed_user, factory)
    settings = _settings(tmp_path)
    monkeypatch.setattr("routers.meeting_imports._settings", lambda: settings)
    active_user = {
        "value": User(
            id=7,
            username="route-import-7",
            email="route-import-7@example.test",
            hashed_password="x",
            full_name="Import Route",
            role=UserRole.ANALYST,
            is_active=True,
        )
    }

    async def current_user():
        return active_user["value"]

    async def session_dependency():
        async with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(import_router, prefix="/api")
    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_async_session] = session_dependency
    client = TestClient(app)
    created = client.post(
        "/api/meetings/v1/imports",
        headers={"Idempotency-Key": "route-import"},
        json=_request("route.wav", 4).model_dump(mode="json"),
    )
    assert created.status_code == 201
    upload_id = created.json()["id"]
    payload = b"data"
    uploaded = client.put(
        f"/api/meetings/v1/imports/{upload_id}/chunks/0",
        headers={
            "X-Chunk-Offset": "0",
            "X-Chunk-SHA256": hashlib.sha256(payload).hexdigest(),
            "Content-Type": "application/octet-stream",
        },
        content=payload,
    )
    assert uploaded.status_code == 200

    active_user["value"] = active_user["value"].model_copy(
        update={
            "id": 8,
            "username": "route-import-8",
            "email": "route-import-8@example.test",
        }
    )
    hidden = client.get(f"/api/meetings/v1/imports/{upload_id}")
    assert hidden.status_code == 404
    assert hidden.json()["detail"]["code"] == "MEETING_IMPORT_NOT_FOUND"
    anyio.run(engine.dispose)


class _FakeFinalization:
    async def analyze(self, meeting_id: str, *, run_id: str | None = None) -> FinalizationAnalysis:
        del meeting_id
        diarizer_ref = "diarizer-import-test-v1"
        return FinalizationAnalysis(
            mode="final_asr",
            chunk_count=1,
            total_audio_bytes=32_000,
            window_count=1,
            gaps=(),
            segments=(
                FinalASRSegment(
                    segment_token="import-final-1",
                    text="这是导入录音的最终文本",
                    start_ms=0,
                    end_ms=1000,
                    adapter="fake-final-asr",
                    speaker_track_key="speaker-a",
                    speaker_confidence=0.9,
                    word_timestamps=(),
                    degraded_reason=None,
                    window_index=0,
                    diarizer_ref=diarizer_ref,
                ),
            ),
            diarizer_ref=diarizer_ref,
            protocol_version=FINAL_ASR_INDEPENDENT_PROTOCOL,
            max_concurrency=2,
        )


def test_import_final_asr_materializes_stable_segments_then_reuses_speaker_pipeline(tmp_path):
    del tmp_path

    async def scenario():
        engine, factory = await _database()
        await _seed_user(factory)
        async with factory() as session:
            microphone_meeting = MeetingSession(
                owner_user_id=7,
                title="unrelated-live-meeting",
                state="stopped",
                postprocess_state="queued",
                audio_source="microphone",
                ai_enabled=False,
                selection_mode="none",
            )
            session.add(microphone_meeting)
            await session.flush()
            microphone_job = MeetingJob(
                meeting_id=microphone_meeting.id,
                job_kind=MeetingJobKind.FINAL_TRANSCRIPT.value,
                idempotency_key=f"{microphone_meeting.id}:finalize:v1",
                input_watermark=0,
                settings_version=1,
            )
            session.add(microphone_job)
            meeting = MeetingSession(
                owner_user_id=7,
                title="import-final",
                state="stopped",
                postprocess_state="queued",
                audio_source="import",
                ai_enabled=False,
                selection_mode="none",
            )
            session.add(meeting)
            await session.flush()
            session.add(
                MeetingJob(
                    meeting_id=meeting.id,
                    job_kind=MeetingJobKind.FINAL_TRANSCRIPT.value,
                    idempotency_key=f"{meeting.id}:finalize:v1",
                    input_watermark=0,
                    settings_version=1,
                )
            )
            await session.commit()
            meeting_id = meeting.id
            microphone_job_id = microphone_job.id

        worker = MeetingAIWorker(
            factory,
            MeetingHermesRunner(pool=MeetingHermesTargetPool([])),
            worker_id="import-postprocess",
            config=MeetingAIWorkerConfig(
                lease_seconds=30,
                retry_delay_seconds=1,
                poll_interval_seconds=0.01,
                correction_confidence=0.85,
                correction_debounce_seconds=20,
                rolling_debounce_seconds=45,
                correction_window_segments=5,
                rolling_min_new_segments=3,
            ),
            finalization_service=_FakeFinalization(),  # type: ignore[arg-type]
            job_kinds={MeetingJobKind.FINAL_TRANSCRIPT.value, MeetingJobKind.SPEAKER_RECLUSTER.value},
            audio_sources={"import"},
        )
        assert await worker.run_once() is True
        assert await worker.run_once() is True

        from services.meeting_contracts import MeetingSpeakerTrack, MeetingTranscriptSegment

        async with factory() as session:
            meeting = await session.get(MeetingSession, meeting_id)
            assert meeting is not None
            assert meeting.last_segment_ordinal == 1
            assert meeting.postprocess_state == "succeeded"
            segment = (
                await session.exec(
                    select(MeetingTranscriptSegment).where(MeetingTranscriptSegment.meeting_id == meeting_id)
                )
            ).one()
            assert segment.raw_text == "这是导入录音的最终文本"
            assert segment.asr_final_text == segment.raw_text
            assert segment.speaker_track_id is not None
            track = await session.get(MeetingSpeakerTrack, segment.speaker_track_id)
            assert track is not None
            assert track.anonymous_label == "发言人 1"
            unrelated = await session.get(MeetingJob, microphone_job_id)
            assert unrelated is not None
            assert unrelated.state == "queued"
        await engine.dispose()

    anyio.run(scenario)


def test_import_limits_and_cancel_remove_temporary_chunks(tmp_path):
    async def scenario():
        engine, factory = await _database()
        await _seed_user(factory)
        settings = _settings(tmp_path, max_file_bytes=5, owner_quota_bytes=5)
        storage = MeetingImportStorage(settings.root)
        async with factory() as session:
            repository = MeetingImportRepository(session, storage, settings)
            with pytest.raises(MeetingImportQuotaExceeded) as oversized:
                await repository.create(7, _request("large.wav", 6), idempotency_key="too-large")
            assert oversized.value.code == "MEETING_IMPORT_FILE_TOO_LARGE"
            with pytest.raises(MeetingImportInvalid) as unsupported:
                await repository.create(7, _request("notes.txt", 4), idempotency_key="wrong-format")
            assert unsupported.value.code == "MEETING_IMPORT_FORMAT_UNSUPPORTED"

            retained, _ = await repository.create(
                7,
                _request("retained.wav", 4),
                idempotency_key="retained-cancel",
            )
            retained_row = await session.get(MeetingImportUpload, retained.id)
            assert retained_row is not None
            retained_row.state = MeetingImportState.CANCELLED.value
            session.add(retained_row)
            await session.commit()
            with pytest.raises(MeetingImportQuotaExceeded) as retained_quota:
                await repository.create(7, _request("blocked.wav", 2), idempotency_key="blocked")
            assert retained_quota.value.code == "MEETING_IMPORT_OWNER_QUOTA_EXCEEDED"
            retained_row.staging_purged_at = utcnow()
            session.add(retained_row)
            await session.commit()

            created, _ = await repository.create(7, _request("small.wav", 4), idempotency_key="cancel-me")
            payload = b"data"
            await repository.put_chunk(
                created.id,
                7,
                ordinal=0,
                byte_offset=0,
                sha256=hashlib.sha256(payload).hexdigest(),
                content_length=4,
                stream=_stream(payload),
            )
            upload_root = settings.root / "7" / created.id
            assert upload_root.exists()
            cancelled = await repository.cancel(created.id, 7)
            assert cancelled.state == MeetingImportState.CANCELLED.value
            assert not upload_root.exists()
        await engine.dispose()

    anyio.run(scenario)


def test_deleted_meeting_hides_stale_import_postprocess_failure(tmp_path):
    async def scenario():
        engine, factory = await _database()
        await _seed_user(factory)
        settings = _settings(tmp_path)
        storage = MeetingImportStorage(settings.root)
        async with factory() as session:
            repository = MeetingImportRepository(session, storage, settings)
            created, _ = await repository.create(7, _request("deleted.wav", 4), idempotency_key="deleted")
            meeting = MeetingSession(
                owner_user_id=7,
                title="deleted import",
                state="deleted",
                postprocess_state="queued",
                audio_source="import",
                ai_enabled=False,
                selection_mode="none",
            )
            session.add(meeting)
            await session.flush()
            upload = await session.get(MeetingImportUpload, created.id)
            assert upload is not None
            upload.meeting_id = meeting.id
            upload.state = MeetingImportState.POSTPROCESS_QUEUED.value
            session.add(upload)
            session.add(
                MeetingJob(
                    meeting_id=meeting.id,
                    job_kind=MeetingJobKind.FINAL_TRANSCRIPT.value,
                    idempotency_key=f"{meeting.id}:failed-final",
                    state=MeetingJobState.FAILED.value,
                    public_error_code="MEETING_AI_OUTPUT_INVALID",
                )
            )
            await session.commit()

            projected = await repository.status(upload)
            assert projected.state == MeetingImportState.CANCELLED.value
            assert projected.step == "cancelled"
            assert projected.public_error_code is None
            assert projected.retryable is False

        await engine.dispose()

    anyio.run(scenario)


def test_worker_cancel_race_queues_standard_meeting_deletion(tmp_path, monkeypatch):
    async def scenario():
        engine, factory = await _database()
        await _seed_user(factory)
        settings = _settings(tmp_path)
        storage = MeetingImportStorage(settings.root)
        audio_store = MeetingAudioStore(tmp_path / "meeting-audio")
        async with factory() as session:
            repository = MeetingImportRepository(session, storage, settings)
            created, _ = await repository.create(7, _request("race.wav", 4), idempotency_key="race")
            payload = b"RIFF"
            await repository.put_chunk(
                created.id,
                7,
                ordinal=0,
                byte_offset=0,
                sha256=hashlib.sha256(payload).hexdigest(),
                content_length=4,
                stream=_stream(payload),
            )
            await repository.complete(created.id, 7, MeetingImportCompleteRequest())

        worker = _FakeMediaWorker(factory, storage, audio_store, settings, worker_id="race-worker")
        claimed = await worker.claim_next()
        assert claimed is not None and claimed.id == created.id
        original_create = MeetingRepository.create_session

        async def create_then_cancel(repository, *args, **kwargs):
            result = await original_create(repository, *args, **kwargs)
            async with factory() as cancel_session:
                await MeetingImportRepository(cancel_session, storage, settings).cancel(created.id, 7)
            return result

        monkeypatch.setattr(MeetingRepository, "create_session", create_then_cancel)
        with pytest.raises(MeetingImportCancelled):
            await worker._ensure_meeting(created.id)

        async with factory() as session:
            upload = await session.get(MeetingImportUpload, created.id)
            assert upload is not None and upload.meeting_id is not None
            meeting = await session.get(MeetingSession, upload.meeting_id)
            assert meeting is not None and meeting.state == "deleted"
            delete_job = (
                await session.exec(
                    select(MeetingJob).where(
                        MeetingJob.meeting_id == meeting.id,
                        MeetingJob.job_kind == MeetingJobKind.DELETE.value,
                    )
                )
            ).one()
            assert delete_job.state == MeetingJobState.QUEUED.value
        await engine.dispose()

    anyio.run(scenario)


class _FakeMediaWorker(MeetingImportWorker):
    async def _probe(self, source: Path, extension: str) -> tuple[int, str]:
        assert source.stat().st_size == 4
        assert extension == "wav"
        return 1000, "wav"

    async def _transcode(self, source: Path, target: Path) -> None:
        del source
        target.write_bytes(bytes(32_000))


def test_worker_recovers_durably_into_an_ordinary_meeting_and_queues_final_asr(tmp_path):
    async def scenario():
        engine, factory = await _database()
        await _seed_user(factory)
        settings = _settings(tmp_path)
        storage = MeetingImportStorage(settings.root)
        audio_store = MeetingAudioStore(tmp_path / "meeting-audio")
        async with factory() as session:
            repository = MeetingImportRepository(session, storage, settings)
            created, _ = await repository.create(7, _request("meeting.wav", 4), idempotency_key="worker-one")
            payload = b"RIFF"
            await repository.put_chunk(
                created.id,
                7,
                ordinal=0,
                byte_offset=0,
                sha256=hashlib.sha256(payload).hexdigest(),
                content_length=4,
                stream=_stream(payload),
            )
            await repository.complete(created.id, 7, MeetingImportCompleteRequest())

        worker = _FakeMediaWorker(factory, storage, audio_store, settings, worker_id="test-worker")
        assert await worker.run_once() is True

        async with factory() as session:
            upload = await session.get(MeetingImportUpload, created.id)
            assert upload is not None
            assert upload.state == MeetingImportState.POSTPROCESS_QUEUED.value
            assert upload.meeting_id is not None
            assert upload.staging_purged_at is not None
            meeting = await session.get(MeetingSession, upload.meeting_id)
            assert meeting is not None
            assert meeting.audio_source == "import"
            assert meeting.state == "stopped"
            assert meeting.last_audio_sequence == 0
            packed = audio_store.ready_packed_audio_path(7, meeting.id)
            assert packed is not None
            assert packed.stat().st_size == 32_044
            chunks = list(
                (await session.exec(select(MeetingAudioChunk).where(MeetingAudioChunk.meeting_id == meeting.id))).all()
            )
            assert len(chunks) == 1
            assert chunks[0].byte_size == 32_000
            job = (
                await session.exec(
                    select(MeetingJob).where(
                        MeetingJob.meeting_id == meeting.id,
                        MeetingJob.job_kind == MeetingJobKind.FINAL_TRANSCRIPT.value,
                    )
                )
            ).one()
            assert job.input_watermark == 0
            assert not (settings.root / "7" / upload.id).exists()

        # If a process dies after the meeting is committed but before staging
        # cleanup is durably recorded, a later idle iteration finishes it.
        orphan = settings.root / "7" / created.id / "chunks" / "orphan.part"
        orphan.parent.mkdir(parents=True)
        orphan.write_bytes(b"orphan")
        async with factory() as session:
            upload = await session.get(MeetingImportUpload, created.id)
            assert upload is not None
            upload.staging_purged_at = None
            session.add(upload)
            await session.commit()
        assert await worker.cleanup_once() is True
        assert not orphan.parent.parent.exists()
        async with factory() as session:
            upload = await session.get(MeetingImportUpload, created.id)
            assert upload is not None
            assert upload.staging_purged_at is not None

        # A crashed worker on its last configured attempt remains claimable.
        async with factory() as session:
            recovery = MeetingImportUpload(
                owner_user_id=7,
                idempotency_key="recover",
                request_hash="a" * 64,
                original_filename="recover.wav",
                extension="wav",
                expected_size=4,
                chunk_size=4,
                total_chunks=1,
                title="recover",
                expires_at=utcnow() + timedelta(hours=1),
                state=MeetingImportState.PROCESSING.value,
                step="verifying",
                attempt=3,
                max_attempts=3,
                lease_owner="dead-worker",
                lease_until=utcnow() - timedelta(seconds=1),
            )
            session.add(recovery)
            await session.commit()
            recovery_id = recovery.id
        claimed = await worker.claim_next()
        assert claimed is not None
        assert claimed.id == recovery_id
        assert claimed.lease_owner == "test-worker"
        await engine.dispose()

    anyio.run(scenario)
