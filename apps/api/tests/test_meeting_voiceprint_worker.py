from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import anyio
import httpx
import pytest
from services.auth_service import User
from services.meeting_audio_store import MeetingAudioStore
from services.meeting_contracts import (
    MEETING_TABLES,
    MeetingAudioChunk,
    MeetingCreateRequest,
    MeetingEvent,
    MeetingJob,
    MeetingSpeakerTrack,
    MeetingTranscriptSegment,
    MeetingVoiceprintMatch,
    MeetingVoiceProfile,
    VoiceprintEnrollmentRequest,
)
from services.meeting_repository import MeetingRepository
from services.meeting_voiceprint_tombstone import VoiceprintTombstoneLedger
from services.meeting_voiceprint_worker import (
    ActiveVoiceTemplate,
    AudioChunkRef,
    ConsentSnapshot,
    DeleteResult,
    EmbeddingResult,
    EnrollmentCompletion,
    EnrollmentContext,
    HttpSpeakerEmbeddingClient,
    MatchContext,
    MatchJobContext,
    MatchRecord,
    MeetingAudioStoreReader,
    MeetingSnapshot,
    MeetingVoiceprintRepositoryAdapter,
    MeetingVoiceprintWorker,
    SpeakerTrackSnapshot,
    TrackSegment,
    VoiceprintJob,
    VoiceprintKeyring,
    VoiceprintMatchLevel,
    VoiceprintQualityPolicy,
    VoiceprintThresholdPolicy,
    VoiceprintWorkerError,
    VoiceprintWorkerSettings,
    VoiceProfileSnapshot,
    classify_voiceprint_match,
    select_non_overlapping_segments,
)
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

MEETING_ID = "11111111-1111-4111-8111-111111111111"
CONSENT_ID = "22222222-2222-4222-8222-222222222222"
PROFILE_ID = "33333333-3333-4333-8333-333333333333"
TRACK_ID = "44444444-4444-4444-8444-444444444444"
JOB_ID = "55555555-5555-4555-8555-555555555555"
MATCH_JOB_ID = "77777777-7777-4777-8777-777777777777"
ENCODER_REF = "iic/speech_eres2netv2_sv_zh-cn_16k-common"


def _settings(*, auto_match_enabled: bool = False) -> VoiceprintWorkerSettings:
    return VoiceprintWorkerSettings(
        worker_id="voice-worker-a",
        lease_seconds=300,
        encoder_name="funasr-eres2netv2",
        encoder_version=ENCODER_REF,
        expected_encoder_ref=ENCODER_REF,
        auto_match_enabled=auto_match_enabled,
    )


def _thresholds(*, validated: bool = False) -> VoiceprintThresholdPolicy:
    return VoiceprintThresholdPolicy(
        version="voiceprint-thresholds.zh-cn.v1",
        suggestion_min_score=0.70,
        suggestion_min_margin=0.10,
        auto_min_score=0.90,
        auto_min_margin=0.20,
        min_effective_duration_ms=6_000,
        auto_match_validated=validated,
    )


def _segments() -> tuple[TrackSegment, ...]:
    return tuple(
        TrackSegment(
            id=f"segment-{index}",
            meeting_id=MEETING_ID,
            speaker_track_id=TRACK_ID,
            start_ms=index * 2_000,
            end_ms=(index + 1) * 2_000,
            overlap=False,
            noise_level=0.05 + index * 0.01,
            asr_confidence=0.95 - index * 0.01,
        )
        for index in range(3)
    )


def _context(*, owner_user_id: int = 7) -> EnrollmentContext:
    return EnrollmentContext(
        job=VoiceprintJob(
            id=JOB_ID,
            meeting_id=MEETING_ID,
            job_kind="voiceprint_enroll",
            state="leased",
            lease_owner="voice-worker-a",
        ),
        meeting=MeetingSnapshot(
            id=MEETING_ID,
            owner_user_id=owner_user_id,
            voiceprint_enabled=True,
            state="stopped",
        ),
        profile=VoiceProfileSnapshot(
            id=PROFILE_ID,
            owner_user_id=owner_user_id,
            status="collecting",
        ),
        consent=ConsentSnapshot(
            id=CONSENT_ID,
            voice_profile_id=PROFILE_ID,
            actor_user_id=owner_user_id,
            purpose="future_meeting_speaker_identification",
            scope="user_private",
            policy_version="voiceprint-consent.v1",
            source_meeting_id=MEETING_ID,
            granted_at=datetime.now(timezone.utc),
        ),
        track=SpeakerTrackSnapshot(
            id=TRACK_ID,
            meeting_id=MEETING_ID,
            label_source="anonymous",
        ),
        chunks=(),
        segments=_segments(),
    )


def _match_context(*, owner_user_id: int = 7) -> MatchContext:
    enrollment = _context(owner_user_id=owner_user_id)
    return MatchContext(
        meeting=enrollment.meeting,
        track=enrollment.track,
        chunks=enrollment.chunks,
        segments=enrollment.segments,
    )


def _pcm(duration_ms: int, amplitude: int = 1_200) -> bytes:
    sample = int(amplitude).to_bytes(2, "little", signed=True)
    return sample * (duration_ms * 16)


class FakeAudioReader:
    def __init__(self) -> None:
        self.calls = []

    async def read_pcm_range(self, **kwargs) -> bytes:
        self.calls.append(kwargs)
        return _pcm(kwargs["end_ms"] - kwargs["start_ms"])


class FakeEmbeddingClient:
    def __init__(self, vector=(1.0, 0.0), *, transaction_session=None) -> None:
        self.vector = vector
        self.transaction_session = transaction_session
        self.calls = []

    async def embed(self, pcm, *, authorization_id, purpose):
        if self.transaction_session is not None:
            assert self.transaction_session.in_transaction() is False
        self.calls.append((len(pcm), authorization_id, purpose))
        return EmbeddingResult(
            encoder_ref=ENCODER_REF,
            values=self.vector,
            duration_ms=len(pcm) // 32,
        )


class FakeRepository:
    def __init__(self, context: EnrollmentContext) -> None:
        self.context = context
        self.context_calls = 0
        self.revoke_on_revalidation = False
        self.completion: EnrollmentCompletion | None = None
        self.failures = []
        self.templates: list[ActiveVoiceTemplate] = []
        self.template_batches: list[list[ActiveVoiceTemplate]] | None = None
        self.template_calls = 0
        self.match_context = MatchContext(
            meeting=context.meeting,
            track=context.track,
            chunks=context.chunks,
            segments=context.segments,
        )
        self.match_records: list[MatchRecord] = []
        self.match_job_completions = []
        self.job_to_claim = context.job
        self.expected_kinds = {"voiceprint_enroll"}

    async def claim_job(self, worker_id, kinds, lease_seconds):
        assert worker_id == "voice-worker-a"
        assert kinds == self.expected_kinds
        assert lease_seconds == 300
        return self.job_to_claim

    async def voiceprint_enrollment_context(self, job_id, worker_id):
        assert (job_id, worker_id) == (JOB_ID, "voice-worker-a")
        self.context_calls += 1
        if self.revoke_on_revalidation and self.context_calls > 1:
            return replace(
                self.context,
                consent=replace(
                    self.context.consent,
                    revoked_at=datetime.now(timezone.utc),
                ),
            )
        return self.context

    async def complete_voiceprint_enrollment(self, completion):
        self.completion = completion

    async def fail_job(
        self,
        job_id,
        worker_id,
        public_error_code,
        *,
        retryable,
        internal_diagnostic,
    ):
        self.failures.append((job_id, worker_id, public_error_code, retryable, internal_diagnostic))

    async def voiceprint_match_context(self, meeting_id, track_id, owner_user_id):
        assert (meeting_id, track_id) == (MEETING_ID, TRACK_ID)
        return self.match_context

    async def voiceprint_match_job_context(self, job_id, worker_id):
        assert self.job_to_claim.id == job_id
        assert worker_id == "voice-worker-a"
        return MatchJobContext(job=self.job_to_claim, context=self.match_context)

    async def active_voiceprint_profiles(self, owner_user_id, encoder_name, encoder_version):
        if self.template_batches is not None:
            index = min(self.template_calls, len(self.template_batches) - 1)
            self.template_calls += 1
            return self.template_batches[index]
        return self.templates

    async def record_voiceprint_match(self, record):
        self.match_records.append(record)

    async def complete_voiceprint_match_job(
        self,
        job_id,
        worker_id,
        *,
        reason_code,
        effective_duration_ms,
        quality_grade,
    ):
        self.match_job_completions.append((job_id, worker_id, reason_code, effective_duration_ms, quality_grade))

    async def delete_voiceprint_profile(self, profile_id, owner_user_id):
        return DeleteResult(profile_id, owner_user_id, True, True, True)


def test_aes_gcm_round_trip_binds_owner_profile_and_encoder_aad():
    keyring = VoiceprintKeyring(active_key_id="key-2026-07", keys={"key-2026-07": b"k" * 32})
    envelope, key_id = keyring.encrypt(
        (3.0, 4.0),
        owner_user_id=7,
        profile_id=PROFILE_ID,
        encoder_name="funasr-eres2netv2",
        encoder_version=ENCODER_REF,
    )
    restored = keyring.decrypt(
        envelope,
        key_id=key_id,
        owner_user_id=7,
        profile_id=PROFILE_ID,
        encoder_name="funasr-eres2netv2",
        encoder_version=ENCODER_REF,
    )
    assert restored == pytest.approx((0.6, 0.8), abs=1e-6)
    assert json.loads(envelope)["algorithm"] == "AES-256-GCM+HKDF-SHA256"

    for changed in (
        {"owner_user_id": 8},
        {"profile_id": "66666666-6666-4666-8666-666666666666"},
        {"encoder_version": "another-version"},
    ):
        arguments = {
            "key_id": key_id,
            "owner_user_id": 7,
            "profile_id": PROFILE_ID,
            "encoder_name": "funasr-eres2netv2",
            "encoder_version": ENCODER_REF,
        }
        arguments.update(changed)
        with pytest.raises(VoiceprintWorkerError, match="authentication failed") as captured:
            keyring.decrypt(envelope, **arguments)
        assert captured.value.code == "VOICEPRINT_CIPHERTEXT_INVALID"

    payload = json.loads(envelope)
    first = payload["ciphertext"][0]
    payload["ciphertext"] = ("A" if first != "A" else "B") + payload["ciphertext"][1:]
    with pytest.raises(VoiceprintWorkerError) as captured:
        keyring.decrypt(
            json.dumps(payload),
            key_id=key_id,
            owner_user_id=7,
            profile_id=PROFILE_ID,
            encoder_name="funasr-eres2netv2",
            encoder_version=ENCODER_REF,
        )
    assert captured.value.code == "VOICEPRINT_CIPHERTEXT_INVALID"


def test_tombstone_permanently_blocks_template_key_derivation(tmp_path):
    ledger = VoiceprintTombstoneLedger(
        path=tmp_path / "security" / "voiceprint-tombstones.jsonl",
        hmac_key=b"h" * 32,
        backend_data_root=tmp_path / "database-backup",
    )
    keyring = VoiceprintKeyring(
        active_key_id="key-2026-07",
        keys={"key-2026-07": b"k" * 32},
        tombstones=ledger,
    )
    envelope, key_id = keyring.encrypt(
        (3.0, 4.0),
        owner_user_id=7,
        profile_id=PROFILE_ID,
        encoder_name="funasr-eres2netv2",
        encoder_version=ENCODER_REF,
    )
    ledger.append(
        owner_user_id=7,
        profile_id=PROFILE_ID,
        deleted_at=datetime.now(timezone.utc),
        reason="deleted",
    )

    with pytest.raises(VoiceprintWorkerError) as captured:
        keyring.decrypt(
            envelope,
            key_id=key_id,
            owner_user_id=7,
            profile_id=PROFILE_ID,
            encoder_name="funasr-eres2netv2",
            encoder_version=ENCODER_REF,
        )
    assert captured.value.code == "VOICEPRINT_TEMPLATE_DESTROYED"


def test_http_embedding_client_enforces_internal_protocol_and_no_persistence():
    async def scenario():
        async def handler(request: httpx.Request) -> httpx.Response:
            body = await request.aread()
            assert request.headers["x-siq-service-token"] == "internal-token"
            assert request.headers["x-siq-voiceprint-consent"] == CONSENT_ID
            assert request.headers["x-siq-voiceprint-purpose"] == "enrollment"
            assert body == _pcm(1_000)
            return httpx.Response(
                200,
                json={
                    "schema_version": "siq.meeting.speaker_embedding.v1",
                    "encoder_ref": ENCODER_REF,
                    "dimension": 2,
                    "embedding": [3.0, 4.0],
                    "duration_ms": 1_000,
                    "purpose": "enrollment",
                    "persisted": False,
                },
            )

        raw_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = HttpSpeakerEmbeddingClient(
            endpoint="https://speech.internal/v1/speaker/embedding",
            service_token="internal-token",
            expected_encoder_ref=ENCODER_REF,
            client=raw_client,
        )
        result = await client.embed(_pcm(1_000), authorization_id=CONSENT_ID, purpose="enrollment")
        assert result.values == pytest.approx((0.6, 0.8))
        await raw_client.aclose()

    anyio.run(scenario)


def test_sample_selection_filters_overlap_noise_confidence_and_prefers_quality():
    policy = VoiceprintQualityPolicy(min_sample_count=2, min_effective_duration_ms=2_000)
    segments = (
        TrackSegment("good", MEETING_ID, TRACK_ID, 0, 2_000, False, 0.05, 0.98),
        TrackSegment("overlapping", MEETING_ID, TRACK_ID, 500, 2_500, False, 0.01, 0.99),
        TrackSegment("noisy", MEETING_ID, TRACK_ID, 3_000, 5_000, False, 0.90, 0.99),
        TrackSegment("overlap-flag", MEETING_ID, TRACK_ID, 5_000, 7_000, True, 0.01, 0.99),
        TrackSegment("low-confidence", MEETING_ID, TRACK_ID, 7_000, 9_000, False, 0.01, 0.10),
        TrackSegment("second", MEETING_ID, TRACK_ID, 9_000, 11_000, False, 0.10, 0.90),
    )
    selected = select_non_overlapping_segments(segments, meeting_id=MEETING_ID, policy=policy)
    assert [item.id for item in selected] == ["overlapping", "second"]


def test_enrollment_revalidates_consent_and_persists_only_encrypted_aggregate():
    async def scenario():
        repository = FakeRepository(_context())
        audio = FakeAudioReader()
        embedding = FakeEmbeddingClient()
        keyring = VoiceprintKeyring(active_key_id="active", keys={"active": b"a" * 32})
        worker = MeetingVoiceprintWorker(
            repository=repository,
            audio_reader=audio,
            embedding_client=embedding,
            keyring=keyring,
            settings=_settings(),
        )
        result = await worker.run_once()
        assert result.status == "succeeded"
        assert repository.context_calls == 2
        assert len(embedding.calls) == 3
        assert repository.completion is not None
        completion = repository.completion
        assert completion.sample_count == 3
        assert completion.effective_duration_ms == 6_000
        assert completion.quality_summary["quality_grade"] == "good"
        assert "embedding" not in completion.quality_summary
        assert "1.0" not in completion.encrypted_embedding
        restored = keyring.decrypt(
            completion.encrypted_embedding,
            key_id=completion.key_id,
            owner_user_id=7,
            profile_id=PROFILE_ID,
            encoder_name=_settings().encoder_name,
            encoder_version=_settings().encoder_version,
        )
        assert restored == pytest.approx((1.0, 0.0))

    anyio.run(scenario)


def test_consent_revoked_during_embedding_prevents_template_publication():
    async def scenario():
        repository = FakeRepository(_context())
        repository.revoke_on_revalidation = True
        embedding = FakeEmbeddingClient()
        worker = MeetingVoiceprintWorker(
            repository=repository,
            audio_reader=FakeAudioReader(),
            embedding_client=embedding,
            keyring=VoiceprintKeyring(active_key_id="active", keys={"active": b"a" * 32}),
            settings=_settings(),
        )
        result = await worker.run_once()
        assert result.status == "failed"
        assert result.public_error_code == "VOICEPRINT_CONSENT_INVALID"
        assert repository.completion is None
        assert len(embedding.calls) == 3
        assert repository.failures[0][2] == "VOICEPRINT_CONSENT_INVALID"

    anyio.run(scenario)


def test_real_repository_audio_store_and_worker_complete_enrollment_atomically(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'voiceprint.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SQLModel.metadata.create_all(
                    sync_connection,
                    tables=[User.__table__, *[model.__table__ for model in MEETING_TABLES]],
                )
            )

        audio_store = MeetingAudioStore(tmp_path / "audio")
        tombstones = VoiceprintTombstoneLedger(
            path=tmp_path / "security" / "voiceprint-tombstones.jsonl",
            hmac_key=b"h" * 32,
            backend_data_root=tmp_path / "database-backup",
        )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            repository = MeetingRepository(session, voiceprint_tombstones=tombstones)
            meeting, _, _ = await repository.create_session(
                7,
                MeetingCreateRequest(title="声纹注册集成测试", voiceprint_enabled=True),
            )
            profile = await repository.create_voice_profile(7, "本人")
            track = MeetingSpeakerTrack(
                meeting_id=meeting.id,
                track_key="speaker-1",
                anonymous_label="发言人 1",
            )
            session.add(track)
            await session.flush()
            for index in range(3):
                start_ms = index * 2_000
                pcm = _pcm(2_000)
                persisted = audio_store.persist_chunk(7, meeting.id, 1, index, pcm)
                session.add(
                    MeetingAudioChunk(
                        meeting_id=meeting.id,
                        stream_epoch=1,
                        sequence=index,
                        start_ms=start_ms,
                        duration_ms=2_000,
                        storage_key=persisted.storage_key,
                        sha256=persisted.sha256,
                        byte_size=persisted.byte_size,
                        state="verified",
                    )
                )
                session.add(
                    MeetingTranscriptSegment(
                        meeting_id=meeting.id,
                        ordinal=index + 1,
                        utterance_id=f"utterance-{index}",
                        provider_segment_key=f"provider-{index}",
                        start_ms=start_ms,
                        end_ms=start_ms + 2_000,
                        speaker_track_id=track.id,
                        raw_text=f"清晰语音 {index}",
                        asr_final_text=f"清晰语音 {index}",
                        asr_confidence=0.95,
                        asr_provider="meeting-speech",
                        asr_model="funasr",
                        asr_version="v1",
                        overlap=False,
                        noise_level=0.05,
                    )
                )
            await session.commit()
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
                idempotency_key="voiceprint-enroll-integration",
            )
            keyring = VoiceprintKeyring(
                active_key_id="active",
                keys={"active": b"a" * 32},
                tombstones=tombstones,
            )
            worker = MeetingVoiceprintWorker(
                repository=MeetingVoiceprintRepositoryAdapter(repository),
                audio_reader=MeetingAudioStoreReader(audio_store),
                embedding_client=FakeEmbeddingClient(transaction_session=session),
                keyring=keyring,
                settings=_settings(),
                thresholds=_thresholds(),
            )
            result = await worker.run_once()
            assert result.status == "succeeded"
            stored_profile = await session.get(MeetingVoiceProfile, profile.id)
            stored_job = await session.get(MeetingJob, enrollment.job_id)
            assert stored_profile is not None
            assert stored_job is not None
            assert stored_profile.status == "active"
            assert stored_profile.sample_count == 3
            assert stored_profile.effective_duration_ms == 6_000
            assert stored_profile.encrypted_embedding
            assert stored_profile.key_id == "active"
            assert stored_job.state == "succeeded"
            restored = keyring.decrypt(
                stored_profile.encrypted_embedding,
                key_id=stored_profile.key_id,
                owner_user_id=7,
                profile_id=profile.id,
                encoder_name=_settings().encoder_name,
                encoder_version=_settings().encoder_version,
            )
            assert restored == pytest.approx((1.0, 0.0))
            events = (
                await session.exec(
                    select(MeetingEvent).where(
                        MeetingEvent.meeting_id == meeting.id,
                        MeetingEvent.event_type == "voiceprint.profile.activated",
                    )
                )
            ).all()
            assert len(events) == 1

            target_meeting, _, _ = await repository.create_session(
                7,
                MeetingCreateRequest(title="跨会议声纹匹配", voiceprint_enabled=True),
            )
            target_track = MeetingSpeakerTrack(
                meeting_id=target_meeting.id,
                track_key="speaker-new-meeting",
                anonymous_label="发言人 1",
            )
            session.add(target_track)
            await session.flush()
            for index in range(3):
                start_ms = index * 2_000
                pcm = _pcm(2_000)
                persisted = audio_store.persist_chunk(7, target_meeting.id, 1, index, pcm)
                session.add(
                    MeetingAudioChunk(
                        meeting_id=target_meeting.id,
                        stream_epoch=1,
                        sequence=index,
                        start_ms=start_ms,
                        duration_ms=2_000,
                        storage_key=persisted.storage_key,
                        sha256=persisted.sha256,
                        byte_size=persisted.byte_size,
                        state="verified",
                    )
                )
                session.add(
                    MeetingTranscriptSegment(
                        meeting_id=target_meeting.id,
                        ordinal=index + 1,
                        utterance_id=f"target-utterance-{index}",
                        provider_segment_key=f"target-provider-{index}",
                        start_ms=start_ms,
                        end_ms=start_ms + 2_000,
                        speaker_track_id=target_track.id,
                        raw_text=f"另一场会议的清晰语音 {index}",
                        asr_final_text=f"另一场会议的清晰语音 {index}",
                        asr_confidence=0.95,
                        asr_provider="meeting-speech",
                        asr_model="funasr",
                        asr_version="v1",
                        overlap=False,
                        noise_level=0.05,
                    )
                )
            await session.commit()
            await repository.transition_session(target_meeting.id, 7, "start")
            await repository.transition_session(target_meeting.id, 7, "stop")
            await repository.transition_session(target_meeting.id, 7, "mark_stopped")
            match_result = await worker.run_once()
            assert match_result.status == "succeeded"
            matches = (
                await session.exec(
                    select(MeetingVoiceprintMatch).where(MeetingVoiceprintMatch.meeting_id == target_meeting.id)
                )
            ).all()
            assert len(matches) == 1
            assert matches[0].decision == "suggested"
            assert matches[0].top1_score == pytest.approx(1.0)
            match_job = await session.get(MeetingJob, match_result.job_id)
            assert match_job is not None
            assert match_job.job_kind == "voiceprint_match"
            assert match_job.state == "succeeded"

            second_enrollment = await repository.enroll_voiceprint(
                meeting.id,
                track.id,
                7,
                VoiceprintEnrollmentRequest(
                    consent_accepted=True,
                    policy_version="voiceprint-consent.v1",
                    voice_profile_id=profile.id,
                    source_track_id=track.id,
                ),
                idempotency_key="voiceprint-enroll-consent-race",
            )
            encoding_started = anyio.Event()
            allow_encoding_to_finish = anyio.Event()

            class BlockingEmbeddingClient(FakeEmbeddingClient):
                async def embed(self, pcm, *, authorization_id, purpose):
                    assert session.in_transaction() is False
                    if not self.calls:
                        encoding_started.set()
                        await allow_encoding_to_finish.wait()
                    return await super().embed(
                        pcm,
                        authorization_id=authorization_id,
                        purpose=purpose,
                    )

            racing_worker = MeetingVoiceprintWorker(
                repository=MeetingVoiceprintRepositoryAdapter(repository),
                audio_reader=MeetingAudioStoreReader(audio_store),
                embedding_client=BlockingEmbeddingClient(transaction_session=session),
                keyring=keyring,
                settings=_settings(),
            )
            race_result = []

            async def run_racing_worker():
                race_result.append(await racing_worker.run_once())

            async with anyio.create_task_group() as task_group:
                task_group.start_soon(run_racing_worker)
                await encoding_started.wait()
                async with AsyncSession(engine, expire_on_commit=False) as revoke_session:
                    await MeetingRepository(
                        revoke_session,
                        voiceprint_tombstones=tombstones,
                    ).revoke_voiceprint_consent(profile.id, 7)
                allow_encoding_to_finish.set()
            assert race_result[0].status == "failed"
            assert race_result[0].public_error_code == "VOICEPRINT_CONSENT_INVALID"
            raced_job = await session.get(MeetingJob, second_enrollment.job_id)
            assert raced_job is not None
            assert raced_job.state == "failed"
            await session.refresh(stored_profile)
            assert stored_profile.status == "revoked"

            deleted = await worker.delete_profile(profile_id=profile.id, owner_user_id=7)
            assert deleted.ciphertext_cleared is True
            assert deleted.key_id_cleared is True
            await session.refresh(stored_profile)
            assert stored_profile.status == "deleted"
            assert stored_profile.encrypted_embedding is None
            assert stored_profile.key_id is None
            assert (
                await repository.active_voiceprint_profiles(
                    7,
                    _settings().encoder_name,
                    _settings().encoder_version,
                )
                == []
            )
        await engine.dispose()

    anyio.run(scenario)


def test_match_classification_requires_score_margin_duration_and_validated_auto_gate():
    policy = _thresholds(validated=True)
    low_margin = classify_voiceprint_match(
        [(PROFILE_ID, 0.95), ("other", 0.90)],
        effective_duration_ms=6_000,
        quality_grade="good",
        thresholds=policy,
        auto_match_enabled=True,
    )
    assert low_margin.level == VoiceprintMatchLevel.UNKNOWN
    assert low_margin.reason_code == "VOICEPRINT_THRESHOLD_NOT_MET"

    default_suggestion = classify_voiceprint_match(
        [(PROFILE_ID, 0.95), ("other", 0.70)],
        effective_duration_ms=6_000,
        quality_grade="good",
        thresholds=policy,
        auto_match_enabled=False,
    )
    assert default_suggestion.level == VoiceprintMatchLevel.SUGGESTION

    validated_auto = classify_voiceprint_match(
        [(PROFILE_ID, 0.95), ("other", 0.70)],
        effective_duration_ms=6_000,
        quality_grade="good",
        thresholds=policy,
        auto_match_enabled=True,
    )
    assert validated_auto.level == VoiceprintMatchLevel.AUTO_MATCH


def test_run_once_consumes_durable_match_job_and_records_suggestion_with_lease_identity():
    async def scenario():
        repository = FakeRepository(_context())
        repository.job_to_claim = VoiceprintJob(
            id=MATCH_JOB_ID,
            meeting_id=MEETING_ID,
            job_kind="voiceprint_match",
            state="leased",
            lease_owner="voice-worker-a",
        )
        repository.expected_kinds = {"voiceprint_enroll", "voiceprint_match"}
        keyring = VoiceprintKeyring(active_key_id="active", keys={"active": b"a" * 32})
        encrypted, key_id = keyring.encrypt(
            (1.0, 0.0),
            owner_user_id=7,
            profile_id=PROFILE_ID,
            encoder_name=_settings().encoder_name,
            encoder_version=_settings().encoder_version,
        )
        repository.templates = [
            ActiveVoiceTemplate(
                profile=VoiceProfileSnapshot(
                    id=PROFILE_ID,
                    owner_user_id=7,
                    status="active",
                    encoder_name=_settings().encoder_name,
                    encoder_version=_settings().encoder_version,
                    encrypted_embedding=encrypted,
                    key_id=key_id,
                    consent_active=True,
                ),
                consent=_context().consent,
            )
        ]
        worker = MeetingVoiceprintWorker(
            repository=repository,
            audio_reader=FakeAudioReader(),
            embedding_client=FakeEmbeddingClient(),
            keyring=keyring,
            settings=_settings(),
            thresholds=_thresholds(),
        )
        result = await worker.run_once()
        assert result.status == "succeeded"
        assert result.job_id == MATCH_JOB_ID
        assert not repository.match_job_completions
        assert len(repository.match_records) == 1
        record = repository.match_records[0]
        assert record.decision == "suggested"
        assert record.job_id == MATCH_JOB_ID
        assert record.worker_id == "voice-worker-a"

    anyio.run(scenario)


def test_durable_match_job_completes_unknown_without_creating_misleading_match():
    async def scenario():
        repository = FakeRepository(_context())
        repository.match_context = replace(
            repository.match_context,
            segments=repository.match_context.segments[:2],
        )
        repository.job_to_claim = VoiceprintJob(
            id=MATCH_JOB_ID,
            meeting_id=MEETING_ID,
            job_kind="voiceprint_match",
            state="leased",
            lease_owner="voice-worker-a",
        )
        repository.expected_kinds = {"voiceprint_enroll", "voiceprint_match"}
        worker = MeetingVoiceprintWorker(
            repository=repository,
            audio_reader=FakeAudioReader(),
            embedding_client=FakeEmbeddingClient(),
            keyring=VoiceprintKeyring(active_key_id="active", keys={"active": b"a" * 32}),
            settings=_settings(),
            thresholds=_thresholds(),
        )
        result = await worker.run_once()
        assert result.status == "succeeded"
        assert repository.match_records == []
        assert repository.match_job_completions == [
            (
                MATCH_JOB_ID,
                "voice-worker-a",
                "VOICEPRINT_DURATION_OR_QUALITY_INSUFFICIENT",
                0,
                "insufficient",
            )
        ]

    anyio.run(scenario)


def test_matching_rejects_cross_user_context_before_audio_or_model_access():
    async def scenario():
        repository = FakeRepository(_context())
        repository.match_context = _match_context(owner_user_id=8)
        audio = FakeAudioReader()
        embedding = FakeEmbeddingClient()
        worker = MeetingVoiceprintWorker(
            repository=repository,
            audio_reader=audio,
            embedding_client=embedding,
            keyring=VoiceprintKeyring(active_key_id="active", keys={"active": b"a" * 32}),
            settings=_settings(),
            thresholds=_thresholds(),
        )
        with pytest.raises(VoiceprintWorkerError) as captured:
            await worker.match_track(meeting_id=MEETING_ID, track_id=TRACK_ID, owner_user_id=7)
        assert captured.value.code == "VOICEPRINT_MATCH_NOT_AUTHORIZED"
        assert not audio.calls
        assert not embedding.calls

    anyio.run(scenario)


def test_matching_keeps_borderline_noise_and_confidence_samples_anonymous():
    async def scenario():
        repository = FakeRepository(_context())
        repository.match_context = replace(
            repository.match_context,
            segments=tuple(
                replace(segment, noise_level=0.30, asr_confidence=0.60) for segment in repository.match_context.segments
            ),
        )
        embedding = FakeEmbeddingClient()
        worker = MeetingVoiceprintWorker(
            repository=repository,
            audio_reader=FakeAudioReader(),
            embedding_client=embedding,
            keyring=VoiceprintKeyring(active_key_id="active", keys={"active": b"a" * 32}),
            settings=_settings(),
            thresholds=_thresholds(),
        )
        outcome = await worker.match_track(
            meeting_id=MEETING_ID,
            track_id=TRACK_ID,
            owner_user_id=7,
        )
        assert outcome.level == VoiceprintMatchLevel.UNKNOWN
        assert outcome.reason_code == "VOICEPRINT_DURATION_OR_QUALITY_INSUFFICIENT"
        assert outcome.quality_grade == "insufficient"
        assert embedding.calls == []

    anyio.run(scenario)


def test_matching_excludes_paused_revoked_and_other_owner_templates():
    async def scenario():
        repository = FakeRepository(_context())
        keyring = VoiceprintKeyring(active_key_id="active", keys={"active": b"a" * 32})

        def template(profile_id, *, owner=7, status="active", revoked=False, vector=(1.0, 0.0)):
            encrypted, key_id = keyring.encrypt(
                vector,
                owner_user_id=owner,
                profile_id=profile_id,
                encoder_name=_settings().encoder_name,
                encoder_version=_settings().encoder_version,
            )
            return ActiveVoiceTemplate(
                profile=VoiceProfileSnapshot(
                    id=profile_id,
                    owner_user_id=owner,
                    status=status,
                    encoder_name=_settings().encoder_name,
                    encoder_version=_settings().encoder_version,
                    encrypted_embedding=encrypted,
                    key_id=key_id,
                    consent_active=not revoked,
                ),
                consent=ConsentSnapshot(
                    id=f"consent-{profile_id}",
                    voice_profile_id=profile_id,
                    actor_user_id=owner,
                    purpose="future_meeting_speaker_identification",
                    scope="user_private",
                    policy_version="v1",
                    source_meeting_id=MEETING_ID,
                    granted_at=datetime.now(timezone.utc),
                    revoked_at=datetime.now(timezone.utc) if revoked else None,
                ),
            )

        repository.templates = [
            template(PROFILE_ID),
            template("paused-profile", status="paused"),
            template("revoked-profile", revoked=True),
            template("other-owner-profile", owner=8),
            template("valid-second", vector=(0.5, 0.8660254)),
        ]
        worker = MeetingVoiceprintWorker(
            repository=repository,
            audio_reader=FakeAudioReader(),
            embedding_client=FakeEmbeddingClient(),
            keyring=keyring,
            settings=_settings(),
            thresholds=_thresholds(),
        )
        outcome = await worker.match_track(
            meeting_id=MEETING_ID,
            track_id=TRACK_ID,
            owner_user_id=7,
        )
        assert outcome.level == VoiceprintMatchLevel.SUGGESTION
        assert outcome.voice_profile_id == PROFILE_ID
        assert len(repository.match_records) == 1
        assert repository.match_records[0].decision == "suggested"

    anyio.run(scenario)


def test_matching_never_uses_partial_candidate_set_when_active_template_authentication_fails():
    async def scenario():
        repository = FakeRepository(_context())
        keyring = VoiceprintKeyring(active_key_id="active", keys={"active": b"a" * 32})

        def active_template(profile_id, envelope):
            return ActiveVoiceTemplate(
                profile=VoiceProfileSnapshot(
                    id=profile_id,
                    owner_user_id=7,
                    status="active",
                    encoder_name=_settings().encoder_name,
                    encoder_version=_settings().encoder_version,
                    encrypted_embedding=envelope,
                    key_id="active",
                    consent_active=True,
                ),
                consent=replace(_context().consent, voice_profile_id=profile_id),
            )

        valid, _ = keyring.encrypt(
            (1.0, 0.0),
            owner_user_id=7,
            profile_id=PROFILE_ID,
            encoder_name=_settings().encoder_name,
            encoder_version=_settings().encoder_version,
        )
        corrupted, _ = keyring.encrypt(
            (0.9, 0.1),
            owner_user_id=7,
            profile_id="corrupted-profile",
            encoder_name=_settings().encoder_name,
            encoder_version=_settings().encoder_version,
        )
        payload = json.loads(corrupted)
        payload["ciphertext"] = ("A" if payload["ciphertext"][0] != "A" else "B") + payload["ciphertext"][1:]
        repository.templates = [
            active_template(PROFILE_ID, valid),
            active_template("corrupted-profile", json.dumps(payload)),
        ]
        worker = MeetingVoiceprintWorker(
            repository=repository,
            audio_reader=FakeAudioReader(),
            embedding_client=FakeEmbeddingClient(),
            keyring=keyring,
            settings=_settings(),
            thresholds=_thresholds(),
        )
        outcome = await worker.match_track(
            meeting_id=MEETING_ID,
            track_id=TRACK_ID,
            owner_user_id=7,
        )
        assert outcome.level == VoiceprintMatchLevel.UNKNOWN
        assert outcome.reason_code == "VOICEPRINT_TEMPLATE_SET_INCOMPLETE"
        assert repository.match_records == []

    anyio.run(scenario)


def test_matching_reloads_candidate_set_and_withholds_stale_suggestion_when_margin_changes():
    async def scenario():
        repository = FakeRepository(_context())
        keyring = VoiceprintKeyring(active_key_id="active", keys={"active": b"a" * 32})

        def template(profile_id, vector):
            envelope, key_id = keyring.encrypt(
                vector,
                owner_user_id=7,
                profile_id=profile_id,
                encoder_name=_settings().encoder_name,
                encoder_version=_settings().encoder_version,
            )
            return ActiveVoiceTemplate(
                profile=VoiceProfileSnapshot(
                    id=profile_id,
                    owner_user_id=7,
                    status="active",
                    encoder_name=_settings().encoder_name,
                    encoder_version=_settings().encoder_version,
                    encrypted_embedding=envelope,
                    key_id=key_id,
                    consent_active=True,
                ),
                consent=replace(_context().consent, voice_profile_id=profile_id),
            )

        winner = template(PROFILE_ID, (1.0, 0.0))
        repository.template_batches = [
            [winner, template("distant-profile", (0.5, 0.8660254))],
            [winner, template("new-close-profile", (0.95, 0.3122499))],
        ]
        worker = MeetingVoiceprintWorker(
            repository=repository,
            audio_reader=FakeAudioReader(),
            embedding_client=FakeEmbeddingClient(),
            keyring=keyring,
            settings=_settings(),
            thresholds=_thresholds(),
        )
        outcome = await worker.match_track(
            meeting_id=MEETING_ID,
            track_id=TRACK_ID,
            owner_user_id=7,
        )
        assert repository.template_calls == 2
        assert outcome.level == VoiceprintMatchLevel.UNKNOWN
        assert outcome.reason_code == "VOICEPRINT_THRESHOLD_NOT_MET"
        assert repository.match_records == []

    anyio.run(scenario)


def test_repository_adapter_delete_clears_ciphertext_and_keeps_owner_boundary():
    class RawRepositoryError(RuntimeError):
        code = "MEETING_RESOURCE_NOT_FOUND"

    class RawRepository:
        async def set_voice_profile_status(self, profile_id, owner_user_id, target):
            if owner_user_id != 7:
                raise RawRepositoryError("not found")
            return (
                SimpleNamespace(
                    id=profile_id,
                    owner_user_id=owner_user_id,
                    encrypted_embedding=None,
                    key_id=None,
                ),
                False,
            )

    async def scenario():
        adapter = MeetingVoiceprintRepositoryAdapter(RawRepository())
        deleted = await adapter.delete_voiceprint_profile(PROFILE_ID, 7)
        assert deleted == DeleteResult(PROFILE_ID, 7, True, True, True)
        with pytest.raises(VoiceprintWorkerError) as captured:
            await adapter.delete_voiceprint_profile(PROFILE_ID, 8)
        assert captured.value.code == "VOICEPRINT_DELETE_NOT_AUTHORIZED"

    anyio.run(scenario)


def test_audio_chunk_reference_retains_store_coordinates():
    value = AudioChunkRef(
        id="chunk-1",
        meeting_id=MEETING_ID,
        stream_epoch=2,
        sequence=9,
        start_ms=0,
        duration_ms=1_000,
        storage_key="7/meeting/chunks/2/9.pcm",
        sha256="a" * 64,
        byte_size=32_000,
        codec="pcm_s16le",
        sample_rate=16_000,
        channels=1,
        state="verified",
    )
    assert (value.stream_epoch, value.sequence) == (2, 9)


def test_audio_reader_rejects_manifest_key_from_another_owner_before_storage_access():
    class Store:
        called = False

        def read_pcm_range(self, *args):
            self.called = True
            return b""

    async def scenario():
        store = Store()
        reader = MeetingAudioStoreReader(store)
        for storage_key in (
            f"8/{MEETING_ID}/chunks/1/0.pcm",
            f"7/{MEETING_ID}/../other-meeting/chunks/1/0.pcm",
        ):
            chunk = AudioChunkRef(
                id="chunk-cross-owner",
                meeting_id=MEETING_ID,
                stream_epoch=1,
                sequence=0,
                start_ms=0,
                duration_ms=1_000,
                storage_key=storage_key,
                sha256="a" * 64,
                byte_size=32_000,
                codec="pcm_s16le",
                sample_rate=16_000,
                channels=1,
                state="verified",
            )
            with pytest.raises(VoiceprintWorkerError) as captured:
                await reader.read_pcm_range(
                    owner_user_id=7,
                    meeting_id=MEETING_ID,
                    chunks=[chunk],
                    start_ms=0,
                    end_ms=1_000,
                    max_bytes=32_000,
                )
            assert captured.value.code == "VOICEPRINT_AUDIO_NOT_AUTHORIZED"
        assert store.called is False

    anyio.run(scenario)


def test_audio_reader_rejects_file_content_that_no_longer_matches_manifest(tmp_path):
    async def scenario():
        store = MeetingAudioStore(tmp_path / "audio-integrity")
        persisted = store.persist_chunk(7, MEETING_ID, 1, 0, _pcm(1_000))
        path = store.resolve_storage_key(persisted.storage_key)
        path.write_bytes(_pcm(1_000, amplitude=2_000))
        chunk = AudioChunkRef(
            id="chunk-tampered",
            meeting_id=MEETING_ID,
            stream_epoch=1,
            sequence=0,
            start_ms=0,
            duration_ms=1_000,
            storage_key=persisted.storage_key,
            sha256=persisted.sha256,
            byte_size=persisted.byte_size,
            codec="pcm_s16le",
            sample_rate=16_000,
            channels=1,
            state="verified",
        )
        with pytest.raises(VoiceprintWorkerError) as captured:
            await MeetingAudioStoreReader(store).read_pcm_range(
                owner_user_id=7,
                meeting_id=MEETING_ID,
                chunks=[chunk],
                start_ms=0,
                end_ms=1_000,
                max_bytes=32_000,
            )
        assert captured.value.code == "VOICEPRINT_AUDIO_INTEGRITY_FAILED"

    anyio.run(scenario)
