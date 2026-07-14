import hashlib
from datetime import timedelta
from types import SimpleNamespace

import anyio
import pytest
from services.auth_service import User
from services.meeting_contracts import (
    MEETING_TABLES,
    MeetingCreateRequest,
    MeetingEvent,
    MeetingJob,
    MeetingJobKind,
    MeetingSpeakerTrack,
    MeetingVoiceprintConsent,
    MeetingVoiceprintMatch,
    MeetingVoiceProfile,
    SpeakerRenameRequest,
    StableSegmentInput,
    VoiceMatchDecision,
    VoiceprintEnrollmentRequest,
    VoiceProfileStatus,
    utcnow,
)
from services.meeting_repository import (
    MeetingIdempotencyConflict,
    MeetingInvalidOperation,
    MeetingRepository,
    MeetingVersionConflict,
)
from services.meeting_voiceprint_tombstone import (
    VoiceprintTombstoneIntegrityError,
    VoiceprintTombstoneLedger,
)
from sqlalchemy import func
from sqlalchemy.ext.asyncio import create_async_engine
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
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=[User.__table__, *[model.__table__ for model in MEETING_TABLES]],
            )
        )
    return engine


class _MemoryLedger:
    def __init__(self):
        self.entries = {}

    def append(self, *, owner_user_id, profile_id, deleted_at, reason):
        existing = self.entries.get(profile_id)
        if existing is not None and (existing.reason == "deleted" or existing.reason == reason):
            return existing
        entry = SimpleNamespace(
            owner_user_id=owner_user_id,
            profile_id=profile_id,
            deleted_at=deleted_at,
            reason=reason,
        )
        self.entries[profile_id] = entry
        return entry

    def latest(self):
        return dict(self.entries)


def _segment(track_key: str) -> StableSegmentInput:
    return StableSegmentInput(
        utterance_id=f"utterance-{track_key}",
        provider_segment_key=f"provider-{track_key}",
        start_ms=0,
        end_ms=2_000,
        raw_text="声纹测试语音",
        asr_final_text="声纹测试语音",
        asr_confidence=0.95,
        asr_provider="meeting-speech",
        asr_model="opaque-asr",
        asr_version="v1",
    )


async def _enrollment_fixture(repository: MeetingRepository, session: AsyncSession):
    meeting, _, _ = await repository.create_session(
        7,
        MeetingCreateRequest(title="声纹注册", voiceprint_enabled=True),
    )
    await repository.append_stable_segment(
        meeting.id,
        7,
        _segment("speaker-a"),
        speaker_track_key="speaker-a",
    )
    track = (
        await session.exec(
            select(MeetingSpeakerTrack).where(MeetingSpeakerTrack.meeting_id == meeting.id)
        )
    ).one()
    await repository.rename_speaker(
        meeting.id,
        track.id,
        7,
        SpeakerRenameRequest(display_name="张三", expected_version=track.version),
    )
    profile = await repository.create_voice_profile(7, "张三")
    enrollment = await repository.enroll_voiceprint(
        meeting.id,
        track.id,
        7,
        VoiceprintEnrollmentRequest(
            consent_accepted=True,
            policy_version="voiceprint-consent.v1",
            voice_profile_id=profile.id,
            source_track_id=track.id,
        ),
        idempotency_key=f"enroll-{meeting.id}",
    )
    return meeting, track, profile, enrollment


def test_enrollment_links_source_track_and_revoke_clears_embedding():
    async def run():
        engine = await _database()
        async with AsyncSession(engine, expire_on_commit=False) as session:
            repository = MeetingRepository(session, voiceprint_tombstones=_MemoryLedger())
            meeting, track, profile, enrollment = await _enrollment_fixture(repository, session)
            claimed = await repository.claim_job(
                "voice-worker",
                {MeetingJobKind.VOICEPRINT_ENROLL.value},
                300,
            )
            assert claimed is not None and claimed.id == enrollment.job_id
            await repository.complete_voiceprint_enrollment(
                claimed.id,
                "voice-worker",
                encoder_name="speaker-encoder",
                encoder_version="v1",
                encrypted_embedding="ciphertext",
                key_id="active-key",
                sample_count=3,
                effective_duration_ms=6_000,
                quality_summary={"grade": "good"},
            )

            await session.refresh(track)
            await session.refresh(profile)
            assert track.voice_profile_id == profile.id
            assert track.display_name == "张三"
            assert track.label_source == "manual"
            assert profile.status == "active"
            assert profile.encrypted_embedding == "ciphertext"
            event_types = list(
                (
                    await session.exec(
                        select(MeetingEvent.event_type)
                        .where(MeetingEvent.meeting_id == meeting.id)
                        .order_by(MeetingEvent.cursor)
                    )
                ).all()
            )
            assert event_types.count("speaker.label.changed") == 2
            assert "voiceprint.profile.activated" in event_types

            second_consent = MeetingVoiceprintConsent(
                voice_profile_id=profile.id,
                actor_user_id=7,
                subject_label=profile.display_name,
                policy_version="voiceprint-consent.v2",
                source_meeting_id=meeting.id,
            )
            session.add(second_consent)
            await session.commit()
            revoked = await repository.revoke_voiceprint_consent(profile.id, 7)
            assert revoked.status == "revoked"
            assert revoked.encrypted_embedding is None
            assert revoked.key_id is None
            consent = (
                await session.exec(
                    select(MeetingVoiceprintConsent).where(
                        MeetingVoiceprintConsent.id == enrollment.consent_id
                    )
                )
            ).one()
            assert consent.revoked_at is not None
            await session.refresh(second_consent)
            assert second_consent.revoked_at is not None
            assert await repository.active_voiceprint_profiles(7, "speaker-encoder", "v1") == []
        await engine.dispose()

    anyio.run(run)


def test_paused_profile_cannot_be_reactivated_by_stale_enrollment_completion():
    async def run():
        engine = await _database()
        async with AsyncSession(engine, expire_on_commit=False) as session:
            repository = MeetingRepository(session, voiceprint_tombstones=_MemoryLedger())
            _, _, profile, enrollment = await _enrollment_fixture(repository, session)
            profile.status = VoiceProfileStatus.ACTIVE.value
            session.add(profile)
            await session.commit()
            claimed = await repository.claim_job(
                "voice-worker",
                {MeetingJobKind.VOICEPRINT_ENROLL.value},
                300,
            )
            assert claimed is not None and claimed.id == enrollment.job_id
            await repository.set_voice_profile_status(profile.id, 7, VoiceProfileStatus.PAUSED)

            with pytest.raises(MeetingInvalidOperation):
                await repository.complete_voiceprint_enrollment(
                    claimed.id,
                    "voice-worker",
                    encoder_name="speaker-encoder",
                    encoder_version="v1",
                    encrypted_embedding="ciphertext",
                    key_id="active-key",
                    sample_count=3,
                    effective_duration_ms=6_000,
                    quality_summary={"grade": "good"},
                )
            await session.refresh(profile)
            assert profile.status == "paused"
            assert profile.encrypted_embedding is None
        await engine.dispose()

    anyio.run(run)


def test_enrollment_idempotency_key_is_bound_to_profile_track_and_policy():
    async def run():
        engine = await _database()
        async with AsyncSession(engine, expire_on_commit=False) as session:
            repository = MeetingRepository(session)
            meeting, track, profile, enrollment = await _enrollment_fixture(repository, session)
            replay = await repository.enroll_voiceprint(
                meeting.id,
                track.id,
                7,
                VoiceprintEnrollmentRequest(
                    consent_accepted=True,
                    policy_version="voiceprint-consent.v1",
                    voice_profile_id=profile.id,
                    source_track_id=track.id,
                ),
                idempotency_key=f"enroll-{meeting.id}",
            )
            assert replay.job_id == enrollment.job_id
            assert replay.consent_id == enrollment.consent_id

            other_profile = await repository.create_voice_profile(7, "李四")
            with pytest.raises(MeetingIdempotencyConflict):
                await repository.enroll_voiceprint(
                    meeting.id,
                    track.id,
                    7,
                    VoiceprintEnrollmentRequest(
                        consent_accepted=True,
                        policy_version="voiceprint-consent.v1",
                        voice_profile_id=other_profile.id,
                        source_track_id=track.id,
                    ),
                    idempotency_key=f"enroll-{meeting.id}",
                )
            await repository.append_stable_segment(
                meeting.id,
                7,
                _segment("speaker-other"),
                speaker_track_key="speaker-other",
            )
            other_track = (
                await session.exec(
                    select(MeetingSpeakerTrack).where(
                        MeetingSpeakerTrack.meeting_id == meeting.id,
                        MeetingSpeakerTrack.track_key == "speaker-other",
                    )
                )
            ).one()
            with pytest.raises(MeetingIdempotencyConflict):
                await repository.enroll_voiceprint(
                    meeting.id,
                    other_track.id,
                    7,
                    VoiceprintEnrollmentRequest(
                        consent_accepted=True,
                        policy_version="voiceprint-consent.v1",
                        voice_profile_id=profile.id,
                        source_track_id=other_track.id,
                    ),
                    idempotency_key=f"enroll-{meeting.id}",
                )
            with pytest.raises(MeetingIdempotencyConflict):
                await repository.enroll_voiceprint(
                    meeting.id,
                    track.id,
                    7,
                    VoiceprintEnrollmentRequest(
                        consent_accepted=True,
                        policy_version="voiceprint-consent.v2",
                        voice_profile_id=profile.id,
                        source_track_id=track.id,
                    ),
                    idempotency_key=f"enroll-{meeting.id}",
                )
        await engine.dispose()

    anyio.run(run)


def test_mark_stopped_queues_durable_match_job_and_retry_wait_has_backoff():
    async def run():
        engine = await _database()
        async with AsyncSession(engine, expire_on_commit=False) as session:
            repository = MeetingRepository(session)
            meeting, _, _ = await repository.create_session(
                7,
                MeetingCreateRequest(title="声纹匹配", voiceprint_enabled=True),
            )
            await repository.append_stable_segment(
                meeting.id,
                7,
                _segment("speaker-b"),
                speaker_track_key="speaker-b",
            )
            await repository.transition_session(meeting.id, 7, "start")
            await repository.transition_session(meeting.id, 7, "stop")
            await repository.transition_session(meeting.id, 7, "mark_stopped")

            jobs = list(
                (
                    await session.exec(
                        select(MeetingJob).where(
                            MeetingJob.meeting_id == meeting.id,
                            MeetingJob.job_kind == MeetingJobKind.VOICEPRINT_MATCH.value,
                        )
                    )
                ).all()
            )
            assert len(jobs) == 1
            job = await repository.claim_job(
                "voice-worker",
                {MeetingJobKind.VOICEPRINT_MATCH.value},
                300,
            )
            assert job is not None
            context = await repository.voiceprint_match_job_context(job.id, "voice-worker")
            assert context["meeting"].owner_user_id == 7
            assert context["track"].meeting_id == meeting.id

            failed = await repository.fail_job(
                job.id,
                "voice-worker",
                public_error_code="VOICEPRINT_ENCODER_UNAVAILABLE",
                retryable=True,
            )
            assert failed.state == "retry_wait"
            assert (
                await repository.claim_job(
                    "another-worker",
                    {MeetingJobKind.VOICEPRINT_MATCH.value},
                    300,
                    retry_delay_seconds=30,
                )
                is None
            )
            failed.updated_at = utcnow() - timedelta(seconds=31)
            session.add(failed)
            await session.commit()
            retried = await repository.claim_job(
                "another-worker",
                {MeetingJobKind.VOICEPRINT_MATCH.value},
                300,
                retry_delay_seconds=30,
            )
            assert retried is not None and retried.attempt == 2
            completed = await repository.complete_voiceprint_match_job(
                retried.id,
                "another-worker",
                reason_code="VOICEPRINT_NO_ELIGIBLE_TEMPLATE",
                effective_duration_ms=2_000,
                quality_grade="good",
            )
            assert completed.state == "succeeded"
        await engine.dispose()

    anyio.run(run)


def test_revoked_profile_cannot_be_confirmed_and_rejected_candidate_is_suppressed():
    async def run():
        engine = await _database()
        async with AsyncSession(engine, expire_on_commit=False) as session:
            repository = MeetingRepository(session, voiceprint_tombstones=_MemoryLedger())
            meeting, track, profile, enrollment = await _enrollment_fixture(repository, session)
            profile.status = VoiceProfileStatus.ACTIVE.value
            profile.encoder_name = "speaker-encoder"
            profile.encoder_version = "v1"
            profile.encrypted_embedding = "ciphertext"
            profile.key_id = "key"
            session.add(profile)
            match = MeetingVoiceprintMatch(
                meeting_id=meeting.id,
                speaker_track_id=track.id,
                voice_profile_id=profile.id,
                encoder_version="v1",
                threshold_version="threshold-v1",
                top1_score=0.9,
                top1_top2_margin=0.2,
                effective_duration_ms=6_000,
                quality_grade="good",
            )
            session.add(match)
            await session.commit()
            meeting_id = meeting.id
            track_id = track.id
            profile_id = profile.id
            match_id = match.id
            await repository.revoke_voiceprint_consent(profile_id, 7)
            with pytest.raises(MeetingInvalidOperation):
                await repository.decide_voice_match(
                    meeting_id,
                    match_id,
                    7,
                    VoiceMatchDecision.CONFIRMED,
                )

            # A rejection for another eligible profile is scoped to this track
            # and must suppress repeat prompts for that same candidate.
            await session.refresh(track)
            track.display_name = None
            track.label_source = "anonymous"
            track.voice_profile_id = None
            candidate_profile = await repository.create_voice_profile(7, "李四")
            candidate_profile.status = VoiceProfileStatus.ACTIVE.value
            candidate_profile.encoder_name = "speaker-encoder"
            candidate_profile.encoder_version = "v1"
            candidate_profile.encrypted_embedding = "candidate-ciphertext"
            candidate_profile.key_id = "candidate-key"
            consent = MeetingVoiceprintConsent(
                voice_profile_id=candidate_profile.id,
                actor_user_id=7,
                subject_label=candidate_profile.display_name,
                policy_version="voiceprint-consent.v2",
                source_meeting_id=meeting_id,
            )
            rejected = MeetingVoiceprintMatch(
                meeting_id=meeting_id,
                speaker_track_id=track_id,
                voice_profile_id=candidate_profile.id,
                encoder_version="v1",
                threshold_version="threshold-v1",
                top1_score=0.92,
                top1_top2_margin=0.25,
                effective_duration_ms=6_000,
                quality_grade="good",
                decision=VoiceMatchDecision.REJECTED.value,
            )
            session.add(candidate_profile)
            session.add(consent)
            session.add(rejected)
            session.add(track)
            await session.commit()
            returned, event = await repository.record_voiceprint_match(
                meeting_id,
                7,
                speaker_track_id=track_id,
                voice_profile_id=candidate_profile.id,
                encoder_version="v1",
                threshold_version="threshold-v1",
                top1_score=0.92,
                top1_top2_margin=0.25,
                effective_duration_ms=6_000,
                quality_grade="good",
            )
            assert returned.id == rejected.id
            assert event.event_type == "voiceprint.match.suppressed"
            matches = list(
                (
                    await session.exec(
                        select(MeetingVoiceprintMatch).where(
                            MeetingVoiceprintMatch.meeting_id == meeting_id,
                            MeetingVoiceprintMatch.speaker_track_id == track_id,
                            MeetingVoiceprintMatch.voice_profile_id == candidate_profile.id,
                        )
                    )
                ).all()
            )
            assert len(matches) == 1
            assert enrollment.consent_id != consent.id
            assert profile_id != candidate_profile.id
        await engine.dispose()

    anyio.run(run)


def test_auto_match_fails_closed_when_candidate_set_changes_after_scoring():
    async def run():
        engine = await _database()
        async with AsyncSession(engine, expire_on_commit=False) as session:
            repository = MeetingRepository(session)
            source_meeting, _, profile, _ = await _enrollment_fixture(repository, session)
            profile.status = VoiceProfileStatus.ACTIVE.value
            profile.encoder_name = "speaker-encoder"
            profile.encoder_version = "v1"
            profile.encrypted_embedding = "ciphertext-a"
            profile.key_id = "key-a"
            session.add(profile)
            await session.commit()
            candidates = await repository.active_voiceprint_profiles(7, "speaker-encoder", "v1")
            stale_fingerprint = candidates[0]["candidate_set_fingerprint"]

            target_meeting, _, _ = await repository.create_session(
                7,
                MeetingCreateRequest(title="自动声纹", voiceprint_enabled=True),
            )
            await repository.append_stable_segment(
                target_meeting.id,
                7,
                _segment("target-speaker"),
                speaker_track_key="target-speaker",
            )
            target_track = (
                await session.exec(
                    select(MeetingSpeakerTrack).where(
                        MeetingSpeakerTrack.meeting_id == target_meeting.id
                    )
                )
            ).one()

            with pytest.raises(MeetingInvalidOperation):
                await repository.record_voiceprint_match(
                    target_meeting.id,
                    7,
                    speaker_track_id=target_track.id,
                    voice_profile_id=profile.id,
                    encoder_name="speaker-encoder",
                    encoder_version="v1",
                    threshold_version="threshold-v1",
                    top1_score=0.99,
                    top1_top2_margin=0.3,
                    effective_duration_ms=6_000,
                    quality_grade="good",
                    decision=VoiceMatchDecision.AUTO_APPLIED,
                    validated_auto_match_gate=True,
                )

            second = await repository.create_voice_profile(7, "李四")
            second.status = VoiceProfileStatus.ACTIVE.value
            second.encoder_name = "speaker-encoder"
            second.encoder_version = "v1"
            second.encrypted_embedding = "ciphertext-b"
            second.key_id = "key-b"
            second_consent = MeetingVoiceprintConsent(
                voice_profile_id=second.id,
                actor_user_id=7,
                subject_label=second.display_name,
                policy_version="voiceprint-consent.v1",
                source_meeting_id=source_meeting.id,
            )
            session.add(second)
            session.add(second_consent)
            await session.commit()

            evidence = {
                "expected_profile_updated_at": profile.updated_at,
                "expected_key_id": profile.key_id,
                "expected_encrypted_embedding_sha256": hashlib.sha256(
                    profile.encrypted_embedding.encode("utf-8")
                ).hexdigest(),
            }
            with pytest.raises(MeetingVersionConflict):
                await repository.record_voiceprint_match(
                    target_meeting.id,
                    7,
                    speaker_track_id=target_track.id,
                    voice_profile_id=profile.id,
                    encoder_name="speaker-encoder",
                    encoder_version="v1",
                    threshold_version="threshold-v1",
                    top1_score=0.99,
                    top1_top2_margin=0.3,
                    effective_duration_ms=6_000,
                    quality_grade="good",
                    decision=VoiceMatchDecision.AUTO_APPLIED,
                    validated_auto_match_gate=True,
                    expected_candidate_set_fingerprint=stale_fingerprint,
                    **evidence,
                )

            fresh = await repository.active_voiceprint_profiles(7, "speaker-encoder", "v1")
            match, _ = await repository.record_voiceprint_match(
                target_meeting.id,
                7,
                speaker_track_id=target_track.id,
                voice_profile_id=profile.id,
                encoder_name="speaker-encoder",
                encoder_version="v1",
                threshold_version="threshold-v1",
                top1_score=0.99,
                top1_top2_margin=0.3,
                effective_duration_ms=6_000,
                quality_grade="good",
                decision=VoiceMatchDecision.AUTO_APPLIED,
                validated_auto_match_gate=True,
                expected_candidate_set_fingerprint=fresh[0]["candidate_set_fingerprint"],
                **evidence,
            )
            assert match.decision == "auto_applied"
            await session.refresh(target_track)
            assert target_track.voice_profile_id == profile.id
            assert target_track.label_source == "voiceprint_auto"
        await engine.dispose()

    anyio.run(run)


def test_tombstone_reconcile_prevents_revoked_or_deleted_template_restore(tmp_path):
    async def run():
        engine = await _database()
        ledger = VoiceprintTombstoneLedger(
            path=tmp_path / "runtime" / "voiceprint-tombstones.jsonl",
            hmac_key=b"t" * 32,
            backend_data_root=tmp_path / "backend-data",
        )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            repository = MeetingRepository(session, voiceprint_tombstones=ledger)
            meeting, _, profile, enrollment = await _enrollment_fixture(repository, session)
            profile.status = VoiceProfileStatus.ACTIVE.value
            profile.encoder_name = "speaker-encoder"
            profile.encoder_version = "v1"
            profile.encrypted_embedding = "ciphertext"
            profile.key_id = "key"
            extra_consent = MeetingVoiceprintConsent(
                voice_profile_id=profile.id,
                actor_user_id=7,
                subject_label=profile.display_name,
                policy_version="voiceprint-consent.v2",
                source_meeting_id=meeting.id,
            )
            session.add(profile)
            session.add(extra_consent)
            await session.commit()

            profile_id = profile.id
            enrollment_consent_id = enrollment.consent_id
            await repository.revoke_voiceprint_consent(profile_id, 7)
            assert ledger.latest()[profile_id].reason == "revoked"
            consents = list(
                (
                    await session.exec(
                        select(MeetingVoiceprintConsent).where(
                            MeetingVoiceprintConsent.voice_profile_id == profile_id
                        )
                    )
                ).all()
            )
            assert len(consents) == 2
            assert all(consent.revoked_at is not None for consent in consents)
            consent_ids = {consent.id for consent in consents}

            # Simulate restoring an old database backup while retaining the
            # external security ledger.
            await session.refresh(profile)
            profile.status = VoiceProfileStatus.ACTIVE.value
            profile.encrypted_embedding = "restored-ciphertext"
            profile.key_id = "restored-key"
            for consent in consents:
                consent.revoked_at = None
                session.add(consent)
            session.add(profile)
            await session.commit()
            report = await repository.reconcile_voiceprint_tombstones(owner_user_id=7)
            assert report == {"seen": 1, "purged": 1, "remaining": 0}
            await session.refresh(profile)
            assert profile.status == "revoked"
            assert profile.encrypted_embedding is None
            assert profile.key_id is None
            assert await repository.active_voiceprint_profiles(7, "speaker-encoder", "v1") == []

            await repository.set_voice_profile_status(profile_id, 7, VoiceProfileStatus.DELETED)
            assert ledger.latest()[profile_id].reason == "deleted"
            await session.refresh(profile)
            profile.status = VoiceProfileStatus.ACTIVE.value
            profile.deleted_at = None
            profile.encrypted_embedding = "restored-again"
            profile.key_id = "restored-key"
            session.add(profile)
            await session.commit()
            await repository.reconcile_voiceprint_tombstones(owner_user_id=7)
            await session.refresh(profile)
            assert profile.status == "deleted"
            assert profile.deleted_at is not None
            assert profile.encrypted_embedding is None
            assert enrollment_consent_id in consent_ids
        await engine.dispose()

    anyio.run(run)


def test_tombstone_append_failure_blocks_profile_delete():
    class FailingLedger:
        def latest(self):
            return {}

        def append(self, **_kwargs):
            raise VoiceprintTombstoneIntegrityError("ledger unavailable")

    async def run():
        engine = await _database()
        async with AsyncSession(engine, expire_on_commit=False) as session:
            repository = MeetingRepository(session, voiceprint_tombstones=FailingLedger())
            profile = await repository.create_voice_profile(7, "张三")
            profile.status = VoiceProfileStatus.ACTIVE.value
            profile.encrypted_embedding = "ciphertext"
            profile.key_id = "key"
            session.add(profile)
            await session.commit()
            profile_id = profile.id
            profile_type = type(profile)

            with pytest.raises(MeetingInvalidOperation):
                await repository.set_voice_profile_status(
                    profile_id,
                    7,
                    VoiceProfileStatus.DELETED,
                )
            stored = await session.get(profile_type, profile_id)
            assert stored is not None
            assert stored.status == "active"
            assert stored.encrypted_embedding == "ciphertext"
            assert stored.key_id == "key"
        await engine.dispose()

    anyio.run(run)


def test_destructive_status_replay_recleans_restored_template_and_all_consents():
    async def run():
        engine = await _database()
        async with AsyncSession(engine, expire_on_commit=False) as session:
            repository = MeetingRepository(session, voiceprint_tombstones=_MemoryLedger())
            meeting, _, _ = await repository.create_session(
                7,
                MeetingCreateRequest(title="删除恢复审计", voiceprint_enabled=True),
            )
            profile = await repository.create_voice_profile(7, "张三")
            profile.status = VoiceProfileStatus.ACTIVE.value
            profile.encrypted_embedding = "ciphertext"
            profile.key_id = "key"
            consent = MeetingVoiceprintConsent(
                voice_profile_id=profile.id,
                actor_user_id=999,
                subject_label=profile.display_name,
                policy_version="voiceprint-consent.v1",
                source_meeting_id=meeting.id,
            )
            session.add(profile)
            session.add(consent)
            await session.commit()
            profile_id = profile.id
            consent_id = consent.id

            revoked, replayed = await repository.set_voice_profile_status(
                profile_id,
                7,
                VoiceProfileStatus.REVOKED,
            )
            assert replayed is False
            assert revoked.encrypted_embedding is None
            stored_consent = await session.get(MeetingVoiceprintConsent, consent_id)
            assert stored_consent is not None and stored_consent.revoked_at is not None

            revoked.encrypted_embedding = "restored"
            revoked.key_id = "restored-key"
            stored_consent.revoked_at = None
            session.add(revoked)
            session.add(stored_consent)
            await session.commit()
            replay, replayed = await repository.set_voice_profile_status(
                profile_id,
                7,
                VoiceProfileStatus.REVOKED,
            )
            assert replayed is True
            assert replay.encrypted_embedding is None
            assert replay.key_id is None
            await session.refresh(stored_consent)
            assert stored_consent.revoked_at is not None

            deleted, _ = await repository.set_voice_profile_status(
                profile_id,
                7,
                VoiceProfileStatus.DELETED,
            )
            deleted.encrypted_embedding = "restored-after-delete"
            deleted.key_id = "restored-key"
            stored_consent.revoked_at = None
            session.add(deleted)
            session.add(stored_consent)
            await session.commit()
            replay, replayed = await repository.set_voice_profile_status(
                profile_id,
                7,
                VoiceProfileStatus.DELETED,
            )
            assert replayed is True
            assert replay.status == "deleted"
            assert replay.encrypted_embedding is None
            assert replay.key_id is None
            await session.refresh(stored_consent)
            assert stored_consent.revoked_at is not None
        await engine.dispose()

    anyio.run(run)


def test_tombstone_reconcile_batches_above_database_bind_limit():
    async def run():
        engine = await _database()
        ledger = _MemoryLedger()
        async with AsyncSession(engine, expire_on_commit=False) as session:
            meeting, _, _ = await MeetingRepository(session).create_session(
                7,
                MeetingCreateRequest(title="批量恢复", voiceprint_enabled=True),
            )
            profiles = [
                MeetingVoiceProfile(
                    owner_user_id=7,
                    display_name=f"profile-{index}",
                    status=VoiceProfileStatus.ACTIVE.value,
                    encoder_name="speaker-encoder",
                    encoder_version="v1",
                    encrypted_embedding=f"ciphertext-{index}",
                    key_id="key",
                )
                for index in range(501)
            ]
            session.add_all(profiles)
            await session.flush()
            consents = [
                MeetingVoiceprintConsent(
                    voice_profile_id=profile.id,
                    actor_user_id=7,
                    subject_label=profile.display_name,
                    policy_version="voiceprint-consent.v1",
                    source_meeting_id=meeting.id,
                )
                for profile in profiles
            ]
            session.add_all(consents)
            await session.commit()
            for profile in profiles:
                ledger.append(
                    owner_user_id=7,
                    profile_id=profile.id,
                    deleted_at=utcnow(),
                    reason="revoked",
                )
            repository = MeetingRepository(session, voiceprint_tombstones=ledger)
            report = await repository.reconcile_voiceprint_tombstones(owner_user_id=7)
            assert report == {"seen": 501, "purged": 501, "remaining": 0}
            active_consents = (
                await session.exec(
                    select(func.count())
                    .select_from(MeetingVoiceprintConsent)
                    .where(MeetingVoiceprintConsent.revoked_at.is_(None))
                )
            ).one()
            assert active_consents == 0
        await engine.dispose()

    anyio.run(run)
