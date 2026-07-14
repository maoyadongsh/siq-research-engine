from __future__ import annotations

import base64
import json
import os
import sqlite3
from datetime import timedelta
from pathlib import Path

import anyio
import pytest
from services.auth_service import User, UserRole
from services.meeting_contracts import (
    MEETING_TABLES,
    LexiconScope,
    MeetingArtifact,
    MeetingASRCorrectionEvent,
    MeetingAudioChunk,
    MeetingEvent,
    MeetingIdempotencyRecord,
    MeetingJob,
    MeetingJobKind,
    MeetingJobState,
    MeetingLexiconEntry,
    MeetingLexiconVersion,
    MeetingModelSetting,
    MeetingModelSnapshot,
    MeetingSegmentRevision,
    MeetingSession,
    MeetingSpeakerTrack,
    MeetingStreamLease,
    MeetingStreamTicket,
    MeetingTermCandidate,
    MeetingTermCandidateSource,
    MeetingTranscriptSegment,
    MeetingVoiceprintConsent,
    MeetingVoiceprintMatch,
    MeetingVoiceProfile,
    utcnow,
)
from services.meeting_import_contracts import (
    MEETING_IMPORT_TABLES,
    MeetingImportChunk,
    MeetingImportUpload,
)
from services.meeting_import_storage import MeetingImportStorage
from services.meeting_native_capture_contracts import (
    MEETING_NATIVE_CAPTURE_TABLES,
    MeetingNativeCapture,
    MeetingNativeCaptureAudioLink,
    MeetingNativeCaptureBatch,
    MeetingNativeCaptureEpoch,
    MeetingNativeCaptureFinalization,
    MeetingNativeCaptureGap,
    MeetingNativeCaptureManifestEntry,
    MeetingNativeCaptureToken,
)
from services.meeting_repository import MeetingRepository
from services.meeting_retention import (
    MeetingDeletionLedger,
    MeetingDeletionLedgerError,
    MeetingRetentionError,
    MeetingRetentionSettings,
    MeetingRetentionWorker,
    MeetingStoragePurger,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession


def _key() -> bytes:
    return bytes(range(32))


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=[
                    User.__table__,
                    *[model.__table__ for model in MEETING_TABLES],
                    *[model.__table__ for model in MEETING_IMPORT_TABLES],
                    *[model.__table__ for model in MEETING_NATIVE_CAPTURE_TABLES],
                ],
            )
        )
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _ledger(tmp_path: Path) -> MeetingDeletionLedger:
    return MeetingDeletionLedger(
        path=tmp_path / "external-security" / "meeting-deletions.jsonl",
        hmac_key=_key(),
        backend_data_root=tmp_path / "backend",
    )


def _purger(tmp_path: Path) -> MeetingStoragePurger:
    return MeetingStoragePurger(
        audio_root=tmp_path / "backend" / "meeting-audio",
        export_root=tmp_path / "backend" / "meeting-exports",
        native_capture_root=tmp_path / "backend" / "meeting-native-captures",
    )


def _settings(*, retention: bool = False) -> MeetingRetentionSettings:
    return MeetingRetentionSettings(
        worker_enabled=True,
        retention_scan_enabled=retention,
        audio_retention_days=90,
        scan_batch_size=50,
        lease_seconds=30,
        retry_delay_seconds=1,
        poll_interval_seconds=0.01,
        scan_interval_seconds=60,
    )


async def _seed_user(session: AsyncSession, user_id: int = 7) -> None:
    session.add(
        User(
            id=user_id,
            username=f"retention-user-{user_id}",
            email=f"retention-user-{user_id}@example.test",
            hashed_password="x",
            full_name="Retention User",
            role=UserRole.ANALYST,
            is_active=True,
        )
    )
    await session.flush()


async def _seed_complete_deleted_meeting(session: AsyncSession):
    await _seed_user(session)
    meeting = MeetingSession(
        owner_user_id=7,
        title="高度敏感的并购会议",
        language="zh-CN",
        state="deleted",
        postprocess_state="processing",
        voiceprint_enabled=True,
        ai_enabled=True,
        selection_mode="auto",
        requested_model_ref="private-model-ref",
        settings_version=3,
        version=5,
        stream_epoch=2,
        last_audio_sequence=4,
        last_segment_ordinal=1,
        active_lexicon_version=1,
        started_at=utcnow() - timedelta(hours=1),
        stopped_at=utcnow(),
    )
    session.add(meeting)
    await session.flush()

    profile = MeetingVoiceProfile(
        owner_user_id=7,
        display_name="张三",
        status="active",
        encoder_name="eres2net",
        encoder_version="v1",
        encrypted_embedding="ciphertext-that-must-remain",
        key_id="key-v1",
        sample_count=2,
        effective_duration_ms=8_000,
    )
    session.add(profile)
    await session.flush()
    consent = MeetingVoiceprintConsent(
        voice_profile_id=profile.id,
        actor_user_id=7,
        subject_label="张三",
        policy_version="voice-consent-v1",
        source_meeting_id=meeting.id,
    )
    session.add(consent)

    speaker = MeetingSpeakerTrack(
        meeting_id=meeting.id,
        track_key="SPK0",
        anonymous_label="发言人 1",
        display_name="张三",
        label_source="manual",
        voice_profile_id=profile.id,
    )
    session.add(speaker)
    await session.flush()
    segment = MeetingTranscriptSegment(
        meeting_id=meeting.id,
        ordinal=1,
        utterance_id="utterance-1",
        provider_segment_key="provider-1",
        start_ms=0,
        end_ms=1_000,
        speaker_track_id=speaker.id,
        raw_text="敏感原始逐字稿",
        asr_final_text="敏感识别逐字稿",
        normalized_text="敏感修订逐字稿",
        asr_provider="funasr",
        asr_model="paraformer",
        asr_version="v1",
    )
    session.add(segment)
    await session.flush()
    revision = MeetingSegmentRevision(
        segment_id=segment.id,
        revision_no=1,
        revision_type="manual",
        text="人工订正敏感正文",
        base_revision_no=0,
        created_by="7",
    )
    session.add(revision)
    correction = MeetingASRCorrectionEvent(
        owner_user_id=7,
        meeting_id=meeting.id,
        segment_id=segment.id,
        speaker_track_id=speaker.id,
        voice_profile_id=profile.id,
        base_revision_no=0,
        result_revision_no=1,
        original_text="耐莫创",
        corrected_text="Nemotron",
        diff_ops_json="[]",
        edit_intent="term_correction",
        error_class="proper_noun",
        contribute_to_accuracy=True,
        asr_provider="funasr",
        asr_model="paraformer",
        asr_version="v1",
        audio_start_ms=0,
        audio_end_ms=1_000,
        idempotency_key="correction-1",
        created_by="7",
    )
    session.add(correction)
    await session.flush()
    candidate = MeetingTermCandidate(
        owner_user_id=7,
        canonical_term="Nemotron",
        misrecognition="耐莫创",
        status="confirmed",
    )
    session.add(candidate)
    await session.flush()
    session.add(
        MeetingTermCandidateSource(
            candidate_id=candidate.id,
            correction_event_id=correction.id,
            meeting_id=meeting.id,
        )
    )
    current_lexicon = MeetingLexiconEntry(
        owner_user_id=7,
        canonical_term="仅本场机密词",
        scope=LexiconScope.CURRENT_MEETING.value,
        meeting_id=meeting.id,
        created_by="7",
    )
    future_lexicon = MeetingLexiconEntry(
        owner_user_id=7,
        canonical_term="Nemotron",
        scope=LexiconScope.USER_FUTURE_MEETINGS.value,
        meeting_id=meeting.id,
        speaker_voice_profile_id=profile.id,
        source_candidate_id=candidate.id,
        created_by="7",
    )
    lexicon_version = MeetingLexiconVersion(
        owner_user_id=7,
        version=1,
        entries_hash="a" * 64,
        entry_count=1,
        entries_json='[{"canonical_term":"Nemotron"}]',
        change_reason="user_confirmed",
        created_by="7",
    )
    session.add(current_lexicon)
    session.add(future_lexicon)
    session.add(lexicon_version)

    session.add(
        MeetingVoiceprintMatch(
            meeting_id=meeting.id,
            speaker_track_id=speaker.id,
            voice_profile_id=profile.id,
            encoder_version="v1",
            threshold_version="v1",
            top1_score=0.9,
            top1_top2_margin=0.2,
            effective_duration_ms=8_000,
            quality_grade="good",
        )
    )
    audio_chunk = MeetingAudioChunk(
        meeting_id=meeting.id,
        stream_epoch=2,
        sequence=4,
        start_ms=0,
        duration_ms=1_000,
        storage_key=f"7/{meeting.id}/chunks/2/4.pcm",
        sha256="b" * 64,
        byte_size=32_000,
    )
    session.add(audio_chunk)
    session.add(
        MeetingStreamLease(
            meeting_id=meeting.id,
            stream_epoch=2,
            connection_id="connection-1",
            owner_user_id=7,
            lease_until=utcnow() + timedelta(minutes=5),
        )
    )
    session.add(
        MeetingStreamTicket(
            token_hash="c" * 64,
            meeting_id=meeting.id,
            owner_user_id=7,
            stream_epoch=2,
            origin="http://127.0.0.1",
            expires_at=utcnow() + timedelta(minutes=5),
        )
    )
    model_setting = MeetingModelSetting(
        meeting_id=meeting.id,
        settings_version=3,
        selection_mode="auto",
        changed_by="7",
    )
    snapshot = MeetingModelSnapshot(
        meeting_id=meeting.id,
        model_ref="private-model-ref",
        selection_mode="auto",
        resolved_provider="local",
        resolved_model="private-model",
        provider_locality="local",
        hermes_target="private-target",
        meeting_profile_version="v1",
        prompt_version="v1",
        settings_version=3,
    )
    session.add(model_setting)
    session.add(snapshot)
    await session.flush()
    artifact = MeetingArtifact(
        meeting_id=meeting.id,
        artifact_type="export",
        version=1,
        state="ready",
        content_json='{"storage_key":"private.docx"}',
        content_text="敏感会议纪要",
        input_to_ordinal=1,
        model_snapshot_id=snapshot.id,
    )
    session.add(artifact)
    await session.flush()
    delete_job = MeetingJob(
        meeting_id=meeting.id,
        job_kind=MeetingJobKind.DELETE.value,
        idempotency_key=f"{meeting.id}:delete:v1",
        settings_version=3,
    )
    other_job = MeetingJob(
        meeting_id=meeting.id,
        job_kind=MeetingJobKind.FINAL_MINUTES.value,
        idempotency_key=f"{meeting.id}:minutes:v1",
        model_snapshot_id=snapshot.id,
        input_json='{"transcript":"sensitive"}',
    )
    session.add(delete_job)
    session.add(other_job)
    await session.flush()
    native_capture = MeetingNativeCapture(
        meeting_id=meeting.id,
        owner_user_id=7,
        device_installation_hash="1" * 64,
        create_idempotency_key="native-retention-create",
        create_request_hash="2" * 64,
        state="active",
        current_epoch=2,
        max_total_bytes=4_000_000,
        max_duration_samples=64_000,
        total_bytes=32_000,
        total_samples=16_000,
    )
    session.add(native_capture)
    await session.flush()
    native_epoch = MeetingNativeCaptureEpoch(capture_id=native_capture.id, stream_epoch=2)
    native_token = MeetingNativeCaptureToken(
        token_hash="3" * 64,
        capture_id=native_capture.id,
        meeting_id=meeting.id,
        owner_user_id=7,
        scopes_json='["batch:write"]',
        expires_at=utcnow() + timedelta(days=1),
    )
    native_batch = MeetingNativeCaptureBatch(
        capture_id=native_capture.id,
        meeting_id=meeting.id,
        owner_user_id=7,
        stream_epoch=2,
        sequence=0,
        first_sample=0,
        sample_count=16_000,
        end_sample=16_000,
        captured_monotonic_ns=1_000_000_000,
        encoding="pcm_s16le",
        sample_rate=16_000,
        channels=1,
        byte_size=32_000,
        sha256="4" * 64,
        storage_key=f"7/{native_capture.id}/2/0.pcm",
        manifest_revision=1,
        idempotency_key="native-retention-batch",
    )
    session.add_all([native_epoch, native_token, native_batch])
    await session.flush()
    session.add_all(
        [
            MeetingNativeCaptureManifestEntry(
                capture_id=native_capture.id,
                meeting_id=meeting.id,
                owner_user_id=7,
                stream_epoch=2,
                sequence=0,
                first_sample=0,
                sample_count=16_000,
                end_sample=16_000,
                captured_monotonic_ns=1_000_000_000,
                encoding="pcm_s16le",
                sample_rate=16_000,
                channels=1,
                byte_size=32_000,
                sha256=native_batch.sha256,
                manifest_revision=2,
            ),
            MeetingNativeCaptureGap(
                capture_id=native_capture.id,
                meeting_id=meeting.id,
                owner_user_id=7,
                stream_epoch=2,
                from_sequence=1,
                to_sequence=1,
                start_sample=16_000,
                end_sample=32_000,
                reason="system_interruption",
                manifest_revision=2,
                idempotency_key="native-retention-gap",
                request_hash="5" * 64,
            ),
            MeetingNativeCaptureFinalization(
                capture_id=native_capture.id,
                meeting_id=meeting.id,
                owner_user_id=7,
                state="processing",
                attempt=1,
                max_attempts=5,
                lease_owner="native-worker",
                lease_until=utcnow() + timedelta(minutes=5),
                final_transcript_job_id=other_job.id,
            ),
            MeetingNativeCaptureAudioLink(
                capture_id=native_capture.id,
                batch_id=native_batch.id,
                meeting_id=meeting.id,
                stream_epoch=2,
                sequence=0,
                audio_chunk_id=audio_chunk.id,
                source_sha256=native_batch.sha256,
            ),
        ]
    )
    session.add(
        MeetingEvent(
            meeting_id=meeting.id,
            cursor=1,
            event_type="transcript.stable",
            payload_json='{"text":"sensitive"}',
        )
    )
    session.add(
        MeetingIdempotencyRecord(
            owner_user_id=7,
            idempotency_key="export-request",
            operation=f"exports.create:{meeting.id}",
            request_hash="d" * 64,
            resource_id=artifact.id,
            response_status=202,
            response_json='{"sensitive":"value"}',
        )
    )
    imported_upload = MeetingImportUpload(
        owner_user_id=7,
        meeting_id=meeting.id,
        idempotency_key="sensitive-import",
        request_hash="e" * 64,
        original_filename="sensitive-board-recording.wav",
        extension="wav",
        expected_size=4,
        chunk_size=4,
        total_chunks=1,
        received_size=4,
        received_chunks=1,
        title="sensitive import title",
        state="postprocess_queued",
        step="finalizing",
        expires_at=utcnow() + timedelta(days=1),
    )
    session.add(imported_upload)
    await session.flush()
    session.add(
        MeetingImportChunk(
            upload_id=imported_upload.id,
            ordinal=0,
            byte_offset=0,
            byte_size=4,
            sha256="f" * 64,
            storage_key=f"7/{imported_upload.id}/chunks/00000000-{'f' * 64}.part",
        )
    )
    await session.commit()
    return {
        "meeting": meeting,
        "delete_job": delete_job,
        "future_lexicon": future_lexicon,
        "current_lexicon": current_lexicon,
        "lexicon_version": lexicon_version,
        "profile": profile,
        "consent": consent,
        "segment": segment,
        "revision": revision,
        "import_upload": imported_upload,
        "native_capture": native_capture,
        "native_token": native_token,
    }


def _write_controlled_files(purger: MeetingStoragePurger, owner_id: int, meeting_id: str) -> None:
    audio = purger.audio_root / str(owner_id) / meeting_id / "chunks" / "1" / "0.pcm"
    export = purger.export_root / str(owner_id) / meeting_id / "exports" / "minutes.docx"
    audio.parent.mkdir(parents=True)
    export.parent.mkdir(parents=True)
    audio.write_bytes(b"private-audio")
    export.write_bytes(b"private-export")


def _write_native_capture_file(purger: MeetingStoragePurger, owner_id: int, capture_id: str) -> Path:
    path = purger.native_capture_root / str(owner_id) / capture_id / "2" / "0.pcm"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"private-native-audio")
    return path


def test_deletion_worker_scrubs_meeting_but_preserves_future_lexicon_and_voiceprint(tmp_path):
    async def scenario():
        engine, factory = await _database(tmp_path / "retention.db")
        async with factory() as session:
            seeded = await _seed_complete_deleted_meeting(session)
        meeting = seeded["meeting"]
        purger = _purger(tmp_path)
        _write_controlled_files(purger, 7, meeting.id)
        native_file = _write_native_capture_file(purger, 7, seeded["native_capture"].id)
        import_storage = MeetingImportStorage(tmp_path / "imports")
        import_chunk = import_storage.root / "7" / seeded["import_upload"].id / "chunks" / f"00000000-{'f' * 64}.part"
        import_chunk.parent.mkdir(parents=True)
        import_chunk.write_bytes(b"RIFF")
        ledger = _ledger(tmp_path)
        worker = MeetingRetentionWorker(
            factory,
            ledger=ledger,
            purger=purger,
            worker_id="retention-test",
            settings=_settings(),
            import_storage=import_storage,
        )

        assert await worker.run_once() is True
        assert purger.has_meeting_storage(7, meeting.id) is False
        assert not native_file.exists()
        assert not import_chunk.parent.parent.exists()
        tombstones = ledger.load()
        assert len(tombstones) == 1
        assert tombstones[0].meeting_id == meeting.id
        assert stat_mode(ledger.path) == 0o600

        async with factory() as session:
            stored = await session.get(MeetingSession, meeting.id)
            assert stored is not None
            assert stored.title == "[deleted]"
            assert stored.language == "und"
            assert stored.state == "deleted"
            assert stored.requested_model_ref is None
            assert stored.started_at is None
            assert stored.stopped_at is None

            jobs = list((await session.exec(select(MeetingJob))).all())
            assert len(jobs) == 1
            assert jobs[0].id == seeded["delete_job"].id
            assert jobs[0].state == MeetingJobState.SUCCEEDED.value
            assert jobs[0].input_json == "{}"
            events = list((await session.exec(select(MeetingEvent))).all())
            assert len(events) == 1
            assert events[0].event_type == "session.deleted"
            assert "敏感" not in events[0].payload_json

            assert await session.get(MeetingTranscriptSegment, seeded["segment"].id) is None
            assert await session.get(MeetingSegmentRevision, seeded["revision"].id) is None
            assert await session.get(MeetingLexiconEntry, seeded["current_lexicon"].id) is None
            future = await session.get(MeetingLexiconEntry, seeded["future_lexicon"].id)
            assert future is not None
            assert future.meeting_id is None
            assert future.source_candidate_id is None
            assert await session.get(MeetingLexiconVersion, seeded["lexicon_version"].id) is not None
            profile = await session.get(MeetingVoiceProfile, seeded["profile"].id)
            consent = await session.get(MeetingVoiceprintConsent, seeded["consent"].id)
            assert profile is not None
            assert profile.encrypted_embedding == "ciphertext-that-must-remain"
            assert consent is not None and consent.revoked_at is None
            assert list((await session.exec(select(MeetingVoiceprintMatch))).all()) == []
            assert list((await session.exec(select(MeetingAudioChunk))).all()) == []
            assert list((await session.exec(select(MeetingArtifact))).all()) == []
            assert list((await session.exec(select(MeetingIdempotencyRecord))).all()) == []
            assert await session.get(MeetingImportUpload, seeded["import_upload"].id) is None
            assert list((await session.exec(select(MeetingImportChunk))).all()) == []
            for model in MEETING_NATIVE_CAPTURE_TABLES:
                assert list((await session.exec(select(model))).all()) == []

        report = await worker.reconcile_tombstones(apply=False)
        assert report.passed is True
        assert report.residual_session_count == 0
        assert report.residual_storage_count == 0
        await engine.dispose()

    anyio.run(scenario)


def test_delete_transition_revokes_native_capture_before_async_scrub(tmp_path):
    async def scenario():
        engine, factory = await _database(tmp_path / "native-revoke.db")
        async with factory() as session:
            await _seed_user(session)
            meeting = MeetingSession(owner_user_id=7, title="native delete", state="live", stream_epoch=1)
            session.add(meeting)
            await session.flush()
            capture = MeetingNativeCapture(
                meeting_id=meeting.id,
                owner_user_id=7,
                device_installation_hash="6" * 64,
                create_idempotency_key="native-delete-create",
                create_request_hash="7" * 64,
                state="active",
                max_total_bytes=64_000,
                max_duration_samples=32_000,
            )
            session.add(capture)
            await session.flush()
            token = MeetingNativeCaptureToken(
                token_hash="8" * 64,
                capture_id=capture.id,
                meeting_id=meeting.id,
                owner_user_id=7,
                scopes_json='["batch:write"]',
                expires_at=utcnow() + timedelta(days=1),
            )
            finalization = MeetingNativeCaptureFinalization(
                capture_id=capture.id,
                meeting_id=meeting.id,
                owner_user_id=7,
                state="processing",
                lease_owner="native-worker",
                lease_until=utcnow() + timedelta(minutes=5),
            )
            session.add_all([token, finalization])
            await session.commit()

            deleted, idempotent, job = await MeetingRepository(session).request_delete(meeting.id, 7)
            assert deleted.state == "deleted"
            assert idempotent is False
            assert job is not None
            await session.refresh(capture)
            await session.refresh(token)
            await session.refresh(finalization)
            assert capture.state == "revoked"
            assert capture.revoked_at is not None
            assert token.revoked_at is not None
            assert finalization.state == "failed"
            assert finalization.public_error_code == "MEETING_DELETED"
            assert finalization.lease_owner is None
        await engine.dispose()

    anyio.run(scenario)


def test_expired_final_attempt_is_reclaimed_after_crash(tmp_path):
    async def scenario():
        engine, factory = await _database(tmp_path / "crash.db")
        async with factory() as session:
            await _seed_user(session)
            meeting = MeetingSession(owner_user_id=7, title="crash", state="deleted")
            session.add(meeting)
            await session.flush()
            job = MeetingJob(
                meeting_id=meeting.id,
                job_kind=MeetingJobKind.DELETE.value,
                idempotency_key=f"{meeting.id}:delete:v1",
                state=MeetingJobState.RUNNING.value,
                attempt=3,
                max_attempts=3,
                lease_owner="dead-worker",
                lease_until=utcnow() - timedelta(minutes=1),
            )
            session.add(job)
            await session.commit()

        ledger = _ledger(tmp_path)
        ledger.append(
            owner_user_id=7,
            meeting_id=meeting.id,
            delete_job_id=job.id,
            deleted_at=utcnow(),
        )
        purger = _purger(tmp_path)
        _write_controlled_files(purger, 7, meeting.id)
        worker = MeetingRetentionWorker(
            factory,
            ledger=ledger,
            purger=purger,
            worker_id="recovery-worker",
            settings=_settings(),
        )
        assert await worker.run_once() is True
        async with factory() as session:
            recovered = await session.get(MeetingJob, job.id)
            assert recovered is not None
            assert recovered.state == MeetingJobState.SUCCEEDED.value
            assert recovered.attempt == 4
        assert len(ledger.load()) == 1
        assert purger.has_meeting_storage(7, meeting.id) is False
        await engine.dispose()

    anyio.run(scenario)


def test_restore_reconcile_reports_then_removes_database_and_file_residuals(tmp_path):
    async def scenario():
        engine, factory = await _database(tmp_path / "restore.db")
        async with factory() as session:
            await _seed_user(session)
            meeting = MeetingSession(owner_user_id=7, title="restored secret", state="stopped")
            session.add(meeting)
            await session.flush()
            session.add(
                MeetingTranscriptSegment(
                    meeting_id=meeting.id,
                    ordinal=1,
                    utterance_id="u1",
                    provider_segment_key="p1",
                    start_ms=0,
                    end_ms=500,
                    raw_text="restored secret",
                    asr_final_text="restored secret",
                    asr_provider="funasr",
                    asr_model="paraformer",
                    asr_version="v1",
                )
            )
            await session.commit()

        ledger = _ledger(tmp_path)
        from uuid import uuid4

        ledger.append(
            owner_user_id=7,
            meeting_id=meeting.id,
            delete_job_id=str(uuid4()),
            deleted_at=utcnow(),
            reason="restore_replay",
        )
        purger = _purger(tmp_path)
        _write_controlled_files(purger, 7, meeting.id)
        worker = MeetingRetentionWorker(
            factory,
            ledger=ledger,
            purger=purger,
            worker_id="restore-reconcile",
            settings=_settings(),
        )

        before = await worker.reconcile_tombstones(apply=False)
        assert before.passed is False
        assert before.residual_session_count == 1
        assert before.residual_storage_count == 1
        applied = await worker.reconcile_tombstones(apply=True)
        assert applied.passed is True
        assert applied.scrubbed_session_count == 1
        verified = await worker.reconcile_tombstones(apply=False)
        assert verified.passed is True
        await engine.dispose()

    anyio.run(scenario)


def test_audio_retention_is_default_off_and_never_deletes_transcript(monkeypatch, tmp_path):
    async def scenario():
        for name in (
            "SIQ_MEETING_DELETE_WORKER_ENABLED",
            "SIQ_MEETING_RETENTION_SCAN_ENABLED",
            "SIQ_MEETING_AUDIO_RETENTION_DAYS",
        ):
            monkeypatch.delenv(name, raising=False)
        defaults = MeetingRetentionSettings.from_env()
        assert defaults.worker_enabled is False
        assert defaults.retention_scan_enabled is False
        assert defaults.audio_retention_days == 90

        engine, factory = await _database(tmp_path / "audio-retention.db")
        async with factory() as session:
            await _seed_user(session)
            meeting = MeetingSession(
                owner_user_id=7,
                title="retained transcript",
                state="stopped",
                started_at=utcnow() - timedelta(days=101),
                stopped_at=utcnow() - timedelta(days=100),
                last_audio_sequence=0,
                last_segment_ordinal=1,
            )
            session.add(meeting)
            await session.flush()
            segment = MeetingTranscriptSegment(
                meeting_id=meeting.id,
                ordinal=1,
                utterance_id="u1",
                provider_segment_key="p1",
                start_ms=0,
                end_ms=1_000,
                raw_text="transcript remains",
                asr_final_text="transcript remains",
                asr_provider="funasr",
                asr_model="paraformer",
                asr_version="v1",
            )
            chunk = MeetingAudioChunk(
                meeting_id=meeting.id,
                stream_epoch=1,
                sequence=0,
                start_ms=0,
                duration_ms=1_000,
                storage_key=f"7/{meeting.id}/chunks/1/0.pcm",
                sha256="a" * 64,
                byte_size=32_000,
            )
            session.add(segment)
            session.add(chunk)
            await session.flush()
            capture = MeetingNativeCapture(
                meeting_id=meeting.id,
                owner_user_id=7,
                device_installation_hash="9" * 64,
                create_idempotency_key="native-retention-expiry",
                create_request_hash="a" * 64,
                state="sealed",
                max_total_bytes=64_000,
                max_duration_samples=16_000,
                total_bytes=32_000,
                total_samples=16_000,
                sealed_through_sample=16_000,
                seal_manifest_revision=2,
                seal_manifest_sha256="b" * 64,
                ingest_complete=True,
                server_playback_state="ready",
            )
            session.add(capture)
            await session.flush()
            native_batch = MeetingNativeCaptureBatch(
                capture_id=capture.id,
                meeting_id=meeting.id,
                owner_user_id=7,
                stream_epoch=1,
                sequence=0,
                first_sample=0,
                sample_count=16_000,
                end_sample=16_000,
                captured_monotonic_ns=1_000_000_000,
                encoding="pcm_s16le",
                sample_rate=16_000,
                channels=1,
                byte_size=32_000,
                sha256="c" * 64,
                storage_key=f"7/{capture.id}/1/0.pcm",
                manifest_revision=1,
                idempotency_key="native-retention-expiry-batch",
            )
            session.add_all(
                [
                    MeetingNativeCaptureEpoch(
                        capture_id=capture.id,
                        stream_epoch=1,
                        state="sealed",
                        last_sequence=0,
                        recorded_through_sample=16_000,
                        manifest_revision=2,
                    ),
                    native_batch,
                    MeetingNativeCaptureManifestEntry(
                        capture_id=capture.id,
                        meeting_id=meeting.id,
                        owner_user_id=7,
                        stream_epoch=1,
                        sequence=0,
                        first_sample=0,
                        sample_count=16_000,
                        end_sample=16_000,
                        captured_monotonic_ns=1_000_000_000,
                        encoding="pcm_s16le",
                        sample_rate=16_000,
                        channels=1,
                        byte_size=32_000,
                        sha256=native_batch.sha256,
                        manifest_revision=2,
                    ),
                    MeetingNativeCaptureToken(
                        token_hash="d" * 64,
                        capture_id=capture.id,
                        meeting_id=meeting.id,
                        owner_user_id=7,
                        scopes_json='["batch:write"]',
                        expires_at=utcnow() + timedelta(days=1),
                    ),
                    MeetingNativeCaptureFinalization(
                        capture_id=capture.id,
                        meeting_id=meeting.id,
                        owner_user_id=7,
                        state="processing",
                        lease_owner="native-retention-worker",
                        lease_until=utcnow() + timedelta(minutes=5),
                    ),
                ]
            )
            await session.flush()
            session.add(
                MeetingNativeCaptureAudioLink(
                    capture_id=capture.id,
                    batch_id=native_batch.id,
                    meeting_id=meeting.id,
                    stream_epoch=1,
                    sequence=0,
                    audio_chunk_id=chunk.id,
                    source_sha256=native_batch.sha256,
                )
            )
            await session.commit()

        observed_before_purge: dict[str, object] = {}

        class ObservingPurger(MeetingStoragePurger):
            def purge_native_captures(self, owner_user_id, capture_ids):
                with sqlite3.connect(tmp_path / "audio-retention.db") as connection:
                    observed_before_purge["capture"] = connection.execute(
                        "SELECT state, server_playback_state FROM meeting_native_captures WHERE id = ?",
                        (capture.id,),
                    ).fetchone()
                    observed_before_purge["finalization"] = connection.execute(
                        "SELECT state, lease_owner, public_error_code "
                        "FROM meeting_native_capture_finalizations WHERE capture_id = ?",
                        (capture.id,),
                    ).fetchone()
                    observed_before_purge["token_revoked"] = connection.execute(
                        "SELECT revoked_at IS NOT NULL FROM meeting_native_capture_tokens WHERE capture_id = ?",
                        (capture.id,),
                    ).fetchone()
                return super().purge_native_captures(owner_user_id, capture_ids)

        purger = ObservingPurger(
            audio_root=tmp_path / "backend" / "meeting-audio",
            export_root=tmp_path / "backend" / "meeting-exports",
            native_capture_root=tmp_path / "backend" / "meeting-native-captures",
        )
        _write_controlled_files(purger, 7, meeting.id)
        native_file = _write_native_capture_file(purger, 7, capture.id)
        disabled = MeetingRetentionWorker(
            factory,
            ledger=_ledger(tmp_path),
            purger=purger,
            settings=defaults,
        )
        assert await disabled.scan_expired_audio() == 0
        assert purger.has_meeting_storage(7, meeting.id) is True

        enabled = MeetingRetentionWorker(
            factory,
            ledger=_ledger(tmp_path),
            purger=purger,
            settings=_settings(retention=True),
        )
        assert await enabled.scan_expired_audio() == 1
        assert not native_file.exists()
        assert observed_before_purge == {
            "capture": ("revoked", "not_ready"),
            "finalization": ("failed", None, "MEETING_AUDIO_RETENTION_EXPIRED"),
            "token_revoked": (1,),
        }
        async with factory() as session:
            assert await session.get(MeetingAudioChunk, chunk.id) is None
            assert await session.get(MeetingTranscriptSegment, segment.id) is not None
            stored_capture = await session.get(MeetingNativeCapture, capture.id)
            assert stored_capture is not None and stored_capture.state == "revoked"
            assert stored_capture.total_bytes == stored_capture.total_samples == 0
            assert list((await session.exec(select(MeetingNativeCaptureBatch))).all()) == []
            assert list((await session.exec(select(MeetingNativeCaptureToken))).all()) == []
            assert list((await session.exec(select(MeetingNativeCaptureAudioLink))).all()) == []
            assert list((await session.exec(select(MeetingNativeCaptureManifestEntry))).all())
            finalization = (await session.exec(select(MeetingNativeCaptureFinalization))).one()
            assert finalization.state == "failed"
            assert finalization.lease_owner is None
            assert finalization.wav_sha256 is None
        await engine.dispose()

    anyio.run(scenario)


def test_audio_retention_expires_native_only_batches_without_canonical_chunks(monkeypatch, tmp_path):
    async def scenario():
        engine, factory = await _database(tmp_path / "native-only-retention.db")
        async with factory() as session:
            await _seed_user(session)
            meeting = MeetingSession(
                owner_user_id=7,
                title="native-only retained transcript",
                state="stopped",
                started_at=utcnow() - timedelta(days=101),
                stopped_at=utcnow() - timedelta(days=100),
                last_segment_ordinal=1,
            )
            session.add(meeting)
            await session.flush()
            segment = MeetingTranscriptSegment(
                meeting_id=meeting.id,
                ordinal=1,
                utterance_id="native-only-u1",
                provider_segment_key="native-only-p1",
                start_ms=0,
                end_ms=1_000,
                raw_text="native-only transcript remains",
                asr_final_text="native-only transcript remains",
                asr_provider="funasr",
                asr_model="paraformer",
                asr_version="v1",
            )
            capture = MeetingNativeCapture(
                meeting_id=meeting.id,
                owner_user_id=7,
                device_installation_hash="8" * 64,
                create_idempotency_key="native-only-retention",
                create_request_hash="7" * 64,
                state="sealed",
                current_epoch=2,
                max_total_bytes=64_000,
                max_duration_samples=16_000,
                total_bytes=32_000,
                total_samples=16_000,
                sealed_through_sample=16_000,
                seal_manifest_revision=2,
                seal_manifest_sha256="6" * 64,
                ingest_complete=True,
                server_playback_state="packaging",
            )
            session.add_all([segment, capture])
            await session.flush()
            batch = MeetingNativeCaptureBatch(
                capture_id=capture.id,
                meeting_id=meeting.id,
                owner_user_id=7,
                stream_epoch=2,
                sequence=0,
                first_sample=0,
                sample_count=16_000,
                end_sample=16_000,
                captured_monotonic_ns=1_000_000_000,
                encoding="pcm_s16le",
                sample_rate=16_000,
                channels=1,
                byte_size=32_000,
                sha256="5" * 64,
                storage_key=f"7/{capture.id}/2/0.pcm",
                manifest_revision=1,
                idempotency_key="native-only-retention-batch",
            )
            session.add_all(
                [
                    MeetingNativeCaptureEpoch(
                        capture_id=capture.id,
                        stream_epoch=2,
                        state="sealed",
                        last_sequence=0,
                        recorded_through_sample=16_000,
                        manifest_revision=2,
                        manifest_sha256=capture.seal_manifest_sha256,
                    ),
                    batch,
                    MeetingNativeCaptureManifestEntry(
                        capture_id=capture.id,
                        meeting_id=meeting.id,
                        owner_user_id=7,
                        stream_epoch=2,
                        sequence=0,
                        first_sample=0,
                        sample_count=16_000,
                        end_sample=16_000,
                        captured_monotonic_ns=1_000_000_000,
                        encoding="pcm_s16le",
                        sample_rate=16_000,
                        channels=1,
                        byte_size=32_000,
                        sha256=batch.sha256,
                        manifest_revision=2,
                    ),
                    MeetingNativeCaptureFinalization(
                        capture_id=capture.id,
                        meeting_id=meeting.id,
                        owner_user_id=7,
                        state="processing",
                        attempt=1,
                        lease_owner="native-only-worker",
                        lease_until=utcnow() + timedelta(minutes=5),
                    ),
                ]
            )
            await session.commit()

        purger = _purger(tmp_path)
        native_file = _write_native_capture_file(purger, 7, capture.id)
        worker = MeetingRetentionWorker(
            factory,
            ledger=_ledger(tmp_path),
            purger=purger,
            settings=_settings(retention=True),
        )
        original_fence = worker._native_audio_is_quiesced

        async def unconfirmed_fence(*_args, **_kwargs):
            return False

        monkeypatch.setattr(worker, "_native_audio_is_quiesced", unconfirmed_fence)
        assert await worker.scan_expired_audio() == 0
        assert native_file.exists()
        async with factory() as session:
            assert len((await session.exec(select(MeetingNativeCaptureBatch))).all()) == 1

        monkeypatch.setattr(worker, "_native_audio_is_quiesced", original_fence)
        assert await worker.scan_expired_audio() == 1
        assert not native_file.exists()
        async with factory() as session:
            assert await session.get(MeetingTranscriptSegment, segment.id) is not None
            assert not (await session.exec(select(MeetingAudioChunk))).all()
            assert not (await session.exec(select(MeetingNativeCaptureBatch))).all()
            assert len((await session.exec(select(MeetingNativeCaptureManifestEntry))).all()) == 1
            stored_capture = await session.get(MeetingNativeCapture, capture.id)
            assert stored_capture is not None and stored_capture.state == "revoked"
            assert stored_capture.total_bytes == stored_capture.total_samples == 0
            finalization = (await session.exec(select(MeetingNativeCaptureFinalization))).one()
            assert finalization.state == "failed"
            assert finalization.lease_owner is None
            assert finalization.public_error_code == "MEETING_AUDIO_RETENTION_EXPIRED"
        await engine.dispose()

    anyio.run(scenario)


def test_audio_retention_expires_orphan_seal_when_meeting_never_reaches_stopped(tmp_path):
    async def scenario():
        engine, factory = await _database(tmp_path / "orphan-native-retention.db")
        old = utcnow() - timedelta(days=100)
        async with factory() as session:
            await _seed_user(session)
            meeting = MeetingSession(
                owner_user_id=7,
                title="stuck native stop",
                state="stopping",
                started_at=utcnow() - timedelta(days=101),
                stopped_at=None,
                last_segment_ordinal=1,
            )
            session.add(meeting)
            await session.flush()
            segment = MeetingTranscriptSegment(
                meeting_id=meeting.id,
                ordinal=1,
                utterance_id="orphan-native-u1",
                provider_segment_key="orphan-native-p1",
                start_ms=0,
                end_ms=1_000,
                raw_text="orphan transcript remains",
                asr_final_text="orphan transcript remains",
                asr_provider="funasr",
                asr_model="paraformer",
                asr_version="v1",
            )
            session.add(segment)

            async def add_capture(key: str, state: str, *, sealed_at=None):
                capture = MeetingNativeCapture(
                    meeting_id=meeting.id,
                    owner_user_id=7,
                    device_installation_hash=key * 64,
                    create_idempotency_key=f"orphan-{key}",
                    create_request_hash=key * 64,
                    state=state,
                    current_epoch=2,
                    max_total_bytes=64_000,
                    max_duration_samples=16_000,
                    total_bytes=32_000,
                    total_samples=16_000,
                    sealed_through_sample=16_000 if sealed_at else None,
                    seal_manifest_revision=2 if sealed_at else None,
                    seal_manifest_sha256=key * 64 if sealed_at else None,
                    sealed_at=sealed_at,
                    updated_at=old,
                    server_playback_state="pending_upload",
                )
                session.add(capture)
                await session.flush()
                batch = MeetingNativeCaptureBatch(
                    capture_id=capture.id,
                    meeting_id=meeting.id,
                    owner_user_id=7,
                    stream_epoch=2,
                    sequence=0,
                    first_sample=0,
                    sample_count=16_000,
                    end_sample=16_000,
                    captured_monotonic_ns=1_000_000_000,
                    encoding="pcm_s16le",
                    sample_rate=16_000,
                    channels=1,
                    byte_size=32_000,
                    sha256=key * 64,
                    storage_key=f"7/{capture.id}/2/0.pcm",
                    manifest_revision=1,
                    idempotency_key=f"orphan-{key}-batch",
                )
                session.add(batch)
                await session.flush()
                return capture, batch

            orphan, orphan_batch = await add_capture("a", "sealed", sealed_at=old)
            active, _ = await add_capture("b", "active")
            uploading, _ = await add_capture("c", "sealed", sealed_at=old)
            finalizing, _ = await add_capture("e", "sealed", sealed_at=old)
            session.add_all(
                [
                    MeetingNativeCaptureManifestEntry(
                        capture_id=orphan.id,
                        meeting_id=meeting.id,
                        owner_user_id=7,
                        stream_epoch=2,
                        sequence=0,
                        first_sample=0,
                        sample_count=16_000,
                        end_sample=16_000,
                        captured_monotonic_ns=1_000_000_000,
                        encoding="pcm_s16le",
                        sample_rate=16_000,
                        channels=1,
                        byte_size=32_000,
                        sha256=orphan_batch.sha256,
                        manifest_revision=2,
                    ),
                    MeetingNativeCaptureFinalization(
                        capture_id=orphan.id,
                        meeting_id=meeting.id,
                        owner_user_id=7,
                        state="pending_upload",
                    ),
                    MeetingNativeCaptureToken(
                        token_hash="d" * 64,
                        capture_id=uploading.id,
                        meeting_id=meeting.id,
                        owner_user_id=7,
                        scopes_json='["batch:write"]',
                        expires_at=utcnow() + timedelta(days=1),
                    ),
                    MeetingNativeCaptureFinalization(
                        capture_id=finalizing.id,
                        meeting_id=meeting.id,
                        owner_user_id=7,
                        state="processing",
                        attempt=1,
                        lease_owner="active-finalizer",
                        lease_until=utcnow() + timedelta(minutes=5),
                    ),
                ]
            )
            await session.commit()

        purger = _purger(tmp_path)
        orphan_file = _write_native_capture_file(purger, 7, orphan.id)
        active_file = _write_native_capture_file(purger, 7, active.id)
        uploading_file = _write_native_capture_file(purger, 7, uploading.id)
        finalizing_file = _write_native_capture_file(purger, 7, finalizing.id)
        worker = MeetingRetentionWorker(
            factory,
            ledger=_ledger(tmp_path),
            purger=purger,
            settings=_settings(retention=True),
        )
        assert await worker.scan_expired_audio() == 1
        assert not orphan_file.exists()
        assert active_file.exists()
        assert uploading_file.exists()
        assert finalizing_file.exists()
        async with factory() as session:
            stored_meeting = await session.get(MeetingSession, meeting.id)
            assert stored_meeting is not None and stored_meeting.state == "stopping"
            assert stored_meeting.stopped_at is None
            assert await session.get(MeetingTranscriptSegment, segment.id) is not None
            stored_orphan = await session.get(MeetingNativeCapture, orphan.id)
            stored_active = await session.get(MeetingNativeCapture, active.id)
            stored_uploading = await session.get(MeetingNativeCapture, uploading.id)
            stored_finalizing = await session.get(MeetingNativeCapture, finalizing.id)
            assert stored_orphan is not None and stored_orphan.state == "revoked"
            assert stored_orphan.total_bytes == stored_orphan.total_samples == 0
            assert stored_active is not None and stored_active.state == "active"
            assert stored_active.total_bytes == 32_000
            assert stored_uploading is not None and stored_uploading.state == "sealed"
            assert stored_finalizing is not None and stored_finalizing.state == "sealed"
            remaining_batches = list((await session.exec(select(MeetingNativeCaptureBatch))).all())
            assert {value.capture_id for value in remaining_batches} == {
                active.id,
                uploading.id,
                finalizing.id,
            }
            assert len((await session.exec(select(MeetingNativeCaptureManifestEntry))).all()) == 1
        await engine.dispose()

    anyio.run(scenario)


def test_ledger_is_authenticated_external_and_idempotent(tmp_path):
    from uuid import uuid4

    backend = tmp_path / "backend"
    with pytest.raises(MeetingDeletionLedgerError) as inside:
        MeetingDeletionLedger(
            path=backend / "meeting-deletions.jsonl",
            hmac_key=_key(),
            backend_data_root=backend,
        )
    assert inside.value.code == "DELETE_LEDGER_CONFIGURATION_INVALID"

    ledger = _ledger(tmp_path)
    meeting_id = str(uuid4())
    job_id = str(uuid4())
    first = ledger.append(
        owner_user_id=7,
        meeting_id=meeting_id,
        delete_job_id=job_id,
        deleted_at=utcnow(),
    )
    replay = ledger.append(
        owner_user_id=7,
        meeting_id=meeting_id,
        delete_job_id=job_id,
        deleted_at=utcnow(),
    )
    assert replay == first
    assert len(ledger.load()) == 1
    assert stat_mode(ledger.path.parent) == 0o700
    assert stat_mode(ledger.path) == 0o600

    content = ledger.path.read_text(encoding="ascii")
    ledger.path.write_text(content.replace('"owner_user_id":7', '"owner_user_id":8'), encoding="ascii")
    os.chmod(ledger.path, 0o600)
    with pytest.raises(MeetingDeletionLedgerError) as tampered:
        ledger.load()
    assert tampered.value.code == "DELETE_LEDGER_INTEGRITY_FAILED"


def test_storage_purger_never_follows_owner_symlink(tmp_path):
    from uuid import uuid4

    purger = _purger(tmp_path)
    meeting_id = str(uuid4())
    outside = tmp_path / "outside" / meeting_id
    outside.mkdir(parents=True)
    secret = outside / "must-remain.pcm"
    secret.write_bytes(b"secret")
    (purger.audio_root / "7").symlink_to(tmp_path / "outside", target_is_directory=True)

    with pytest.raises(MeetingRetentionError) as unsafe:
        purger.purge_meeting(7, meeting_id)
    assert unsafe.value.code == "DELETE_STORAGE_PATH_INVALID"
    assert secret.read_bytes() == b"secret"
    assert purger.has_meeting_storage(7, meeting_id) is True


def test_worker_cli_is_fail_closed_when_disabled(monkeypatch, capsys):
    from scripts.meeting_retention_worker import main

    monkeypatch.setenv("SIQ_MEETING_DELETE_WORKER_ENABLED", "0")
    assert main(["--once"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "disabled"
    assert payload["error_code"] == "MEETING_DELETE_WORKER_DISABLED"


def test_reconcile_cli_can_require_external_ledger(monkeypatch, capsys, tmp_path):
    from scripts.reconcile_meeting_deletion_tombstones import main

    monkeypatch.setenv(
        "SIQ_MEETING_DELETION_TOMBSTONE_HMAC_KEY",
        base64.urlsafe_b64encode(_key()).decode("ascii").rstrip("="),
    )
    monkeypatch.setenv(
        "SIQ_MEETING_DELETION_TOMBSTONE_PATH",
        str(tmp_path / "missing-external-ledger.jsonl"),
    )
    monkeypatch.setenv("SIQ_BACKEND_DATA_ROOT", str(tmp_path / "backend"))
    assert main(["--require-ledger-file"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
    assert payload["error_code"] == "DELETE_LEDGER_REQUIRED"


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777
