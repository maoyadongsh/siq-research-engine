from __future__ import annotations

from datetime import timedelta

import anyio
from services.auth_service import User
from services.meeting_ai_worker import MeetingAIWorker, MeetingAIWorkerConfig
from services.meeting_contracts import (
    MEETING_TABLES,
    ArtifactState,
    ArtifactType,
    AudioSource,
    MeetingArtifact,
    MeetingEvent,
    MeetingJob,
    MeetingJobKind,
    MeetingJobState,
    MeetingModelSetting,
    MeetingModelSnapshot,
    MeetingSegmentRevision,
    MeetingSession,
    MeetingSpeakerTrack,
    MeetingTranscriptSegment,
    ModelSelectionMode,
    utcnow,
)
from services.meeting_event_store import decode_json, encode_json
from services.meeting_finalization import (
    FINAL_ALIGNMENT_SCHEMA,
    FINAL_ASR_INDEPENDENT_PROTOCOL,
    FinalASRSegment,
    FinalizationAnalysis,
    MeetingFinalizationUnavailable,
)
from services.meeting_hermes_runner import (
    MeetingAITask,
    MeetingHermesRunner,
    MeetingHermesRunResult,
    MeetingHermesTarget,
    MeetingHermesTargetPool,
    MeetingHermesTargetUnavailable,
)
from services.meeting_speaker_recluster import (
    SpeakerMergeProposal,
    SpeakerReclusterPlan,
    SpeakerReclusterPolicy,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession


def _target(model_ref: str = "model:local:primary") -> MeetingHermesTarget:
    suffix = model_ref.rsplit(":", 1)[-1]
    return MeetingHermesTarget.from_mapping(
        {
            "model_ref": model_ref,
            "target_id": f"target:local:{suffix}",
            "provider": "custom:local",
            "model": f"local-{suffix}",
            "locality": "local",
            "runs_url": "http://127.0.0.1:18701/v1/runs",
            "api_key_env": "SIQ_TEST_MEETING_AI_KEY",
            "capabilities": ["text", "structured_json"],
        },
        allowed_gateway_hosts={"127.0.0.1"},
    )


def _cloud_target(model_ref: str = "model:cloud:kimi") -> MeetingHermesTarget:
    suffix = model_ref.rsplit(":", 1)[-1]
    return MeetingHermesTarget.from_mapping(
        {
            "model_ref": model_ref,
            "target_id": f"target:cloud:{suffix}",
            "provider": "kimi-for-coding",
            "model": "kimi-for-coding",
            "locality": "cloud",
            "runs_url": "http://127.0.0.1:18711/v1/runs",
            "api_key_env": "SIQ_TEST_MEETING_AI_KEY",
            "capabilities": ["text", "structured_json"],
        },
        allowed_gateway_hosts={"127.0.0.1"},
    )


def _minutes_output(segment_id: str, *, invalid_source: bool = False) -> dict:
    source = "outside-input" if invalid_source else segment_id
    return {
        "schema_version": "siq.meeting.final_minutes.v1",
        "overview": "会议摘要。",
        "agenda_topics": [],
        "chapters": [],
        "decisions": [{"text": "采用选定方案。", "source_segment_ids": [source]}],
        "open_questions": [],
        "risks": [],
        "action_items": [],
        "speaker_viewpoints": [],
    }


class FakeRunner(MeetingHermesRunner):
    def __init__(self, outputs=None, *, targets=None, failure=None):
        super().__init__(MeetingHermesTargetPool(targets or [_target()]))
        self.outputs = outputs or {}
        self.failure = failure
        self.calls = []

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        if self.failure is not None:
            raise self.failure
        task = kwargs["task"]
        output = self.outputs[task]
        if callable(output):
            output = output(kwargs)
        return MeetingHermesRunResult(
            run_id=f"run-{len(self.calls)}",
            task=task,
            snapshot=kwargs["snapshot"],
            output=output,
        )


class FakeFinalizationService:
    def __init__(self, analysis=None, failure=None):
        self.analysis = analysis
        self.failure = failure
        self.calls = []

    async def analyze(self, meeting_id, *, run_id=None):
        self.calls.append((meeting_id, run_id))
        if self.failure is not None:
            raise self.failure
        return self.analysis


class FakeSpeakerReclusterService:
    def __init__(self, plan: SpeakerReclusterPlan):
        self.plan_result = plan
        self.policy = SpeakerReclusterPolicy(
            version="speaker-recluster.validated.calibration.v1",
            final_diarizer_ref="diarizer-test-v1",
            auto_apply_validated=True,
            validation_artifact_sha256="b" * 64,
            operator_enabled=True,
        )
        self.calls = []

    async def plan(self, **kwargs):
        self.calls.append(kwargs)
        return self.plan_result


class MergeAllSpeakerReclusterService:
    def __init__(self):
        self.policy = SpeakerReclusterPolicy(
            version="speaker-recluster.validated.calibration.v1",
            final_diarizer_ref="diarizer-test-v1",
            auto_apply_validated=True,
            validation_artifact_sha256="c" * 64,
            operator_enabled=True,
        )
        self.calls = []

    async def plan(self, **kwargs):
        self.calls.append(kwargs)
        active_track_ids = sorted(
            {segment.speaker_track_id for segment in kwargs["segments"] if segment.speaker_track_id}
        )
        assert len(active_track_ids) == 2
        target_id, source_id = active_track_ids
        return SpeakerReclusterPlan(
            track_targets={source_id: target_id},
            embedded_track_count=2,
            selected_sample_count=4,
            encoder_ref="diarization-test-v1",
            final_diarizer_ref="diarizer-test-v1",
            policy_version="speaker-recluster.validated.calibration.v1",
            validation_artifact_sha256="c" * 64,
            automatic_enabled=True,
        )


class UnvalidatedMappingSpeakerReclusterService:
    def __init__(self):
        self.policy = SpeakerReclusterPolicy()

    async def plan(self, **kwargs):
        active_track_ids = sorted(
            {segment.speaker_track_id for segment in kwargs["segments"] if segment.speaker_track_id}
        )
        return SpeakerReclusterPlan(
            track_targets={active_track_ids[1]: active_track_ids[0]},
            policy_version="speaker-recluster.unvalidated.v1",
            automatic_enabled=True,
        )


async def _engine(path=None):
    if path is None:
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
    else:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{path}",
            connect_args={"timeout": 10},
        )
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=[User.__table__, *[model.__table__ for model in MEETING_TABLES]],
            )
        )
    return engine


def _factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _seed_meeting(
    session: AsyncSession,
    *,
    selection_mode: str = ModelSelectionMode.PINNED.value,
    model_ref: str | None = "model:local:primary",
    ai_enabled: bool = True,
    state: str = "live",
    voiceprint_enabled: bool = False,
) -> MeetingSession:
    meeting = MeetingSession(
        owner_user_id=7,
        title="AI worker test",
        state=state,
        ai_enabled=ai_enabled,
        selection_mode=selection_mode,
        requested_model_ref=model_ref,
        fallback_policy="disabled",
        postprocess_state="queued",
        voiceprint_enabled=voiceprint_enabled,
    )
    session.add(meeting)
    await session.flush()
    session.add(
        MeetingModelSetting(
            meeting_id=meeting.id,
            settings_version=1,
            selection_mode=selection_mode,
            requested_model_ref=model_ref,
            fallback_policy="disabled",
            changed_by="7",
        )
    )
    await session.commit()
    return meeting


async def _add_segment(
    session: AsyncSession,
    meeting: MeetingSession,
    ordinal: int,
    text: str,
    *,
    human_locked: bool = False,
) -> MeetingTranscriptSegment:
    segment = MeetingTranscriptSegment(
        meeting_id=meeting.id,
        ordinal=ordinal,
        utterance_id=f"utterance-{ordinal}",
        provider_segment_key=f"provider-{ordinal}",
        start_ms=(ordinal - 1) * 1000,
        end_ms=ordinal * 1000,
        raw_text=text,
        asr_final_text=text,
        asr_provider="meeting-speech",
        asr_model="asr-v1",
        asr_version="v1",
        human_locked=human_locked,
    )
    meeting.last_segment_ordinal = max(meeting.last_segment_ordinal, ordinal)
    session.add(segment)
    session.add(meeting)
    await session.flush()
    return segment


def _worker(factory, runner, worker_id="worker-a", **overrides):
    config = MeetingAIWorkerConfig(
        lease_seconds=60,
        retry_delay_seconds=overrides.get("retry_delay_seconds", 0),
        poll_interval_seconds=0.01,
        correction_confidence=0.85,
    )
    return MeetingAIWorker(
        factory,
        runner,
        worker_id=worker_id,
        config=config,
        finalization_service=overrides.get("finalization_service"),
        job_kinds=overrides.get("job_kinds"),
        audio_sources=overrides.get("audio_sources"),
        speaker_recluster_service=overrides.get("speaker_recluster_service"),
    )


def test_auto_selection_prefers_configured_default_only_within_data_boundary(monkeypatch):
    local = _target()
    cloud = _cloud_target()
    runner = FakeRunner(targets=[local, cloud])
    worker = _worker(lambda: None, runner)
    monkeypatch.setenv("SIQ_MEETING_DEFAULT_MODEL_REF", cloud.model_ref)

    confirmed = MeetingModelSetting(
        meeting_id="meeting-1",
        settings_version=1,
        selection_mode=ModelSelectionMode.AUTO.value,
        fallback_policy="explicit_policy",
        cloud_data_boundary_confirmed_at=utcnow(),
        changed_by="7",
    )
    unconfirmed = confirmed.model_copy(update={"cloud_data_boundary_confirmed_at": None})

    assert worker._select_auto_target(confirmed).model_ref == cloud.model_ref
    assert worker._select_auto_target(unconfirmed).model_ref == local.model_ref


def test_sqlite_atomic_claim_allows_only_one_worker(tmp_path):
    async def scenario():
        engine = await _engine(tmp_path / "claim.db")
        factory = _factory(engine)
        async with factory() as session:
            meeting = await _seed_meeting(session)
            session.add(
                MeetingJob(
                    meeting_id=meeting.id,
                    job_kind="correction",
                    idempotency_key="atomic-claim",
                )
            )
            await session.commit()

        runner = FakeRunner()
        first, second = await asyncio_gather(
            _worker(factory, runner, "worker-a").claim_next(),
            _worker(factory, runner, "worker-b").claim_next(),
        )
        claimed = [value for value in (first, second) if value is not None]
        assert len(claimed) == 1
        assert claimed[0].attempt == 1
        assert claimed[0].state == MeetingJobState.LEASED.value
        await engine.dispose()

    anyio.run(scenario)


def test_final_minutes_claim_precedes_correction_in_compatible_all_lane():
    async def scenario():
        engine = await _engine()
        factory = _factory(engine)
        async with factory() as session:
            meeting = await _seed_meeting(session, state="stopped")
            session.add_all(
                [
                    MeetingJob(
                        meeting_id=meeting.id,
                        job_kind=MeetingJobKind.CORRECTION.value,
                        idempotency_key="priority-correction",
                    ),
                    MeetingJob(
                        meeting_id=meeting.id,
                        job_kind=MeetingJobKind.FINAL_MINUTES.value,
                        idempotency_key="priority-final-minutes",
                    ),
                ]
            )
            await session.commit()

        claimed = await _worker(factory, FakeRunner()).claim_next()
        assert claimed is not None
        assert claimed.job_kind == MeetingJobKind.FINAL_MINUTES.value
        await engine.dispose()

    anyio.run(scenario)


def test_final_minutes_waits_for_enabled_voiceprint_jobs_to_finish():
    async def scenario():
        engine = await _engine()
        factory = _factory(engine)
        async with factory() as session:
            meeting = await _seed_meeting(session, state="stopped", voiceprint_enabled=True)
            voiceprint = MeetingJob(
                meeting_id=meeting.id,
                job_kind=MeetingJobKind.VOICEPRINT_MATCH.value,
                idempotency_key="voiceprint-before-minutes",
                state=MeetingJobState.QUEUED.value,
            )
            minutes = MeetingJob(
                meeting_id=meeting.id,
                job_kind=MeetingJobKind.FINAL_MINUTES.value,
                idempotency_key="minutes-after-voiceprint",
                state=MeetingJobState.QUEUED.value,
            )
            session.add_all([voiceprint, minutes])
            await session.commit()

        worker = _worker(factory, FakeRunner())
        assert await worker.claim_next() is None
        async with factory() as session:
            stored = await session.get(MeetingJob, voiceprint.id)
            stored.state = MeetingJobState.SUCCEEDED.value
            session.add(stored)
            await session.commit()
        claimed = await worker.claim_next()
        assert claimed is not None
        assert claimed.id == minutes.id
        await engine.dispose()

    anyio.run(scenario)


def test_worker_lanes_claim_only_their_job_kinds():
    async def scenario():
        engine = await _engine()
        factory = _factory(engine)
        async with factory() as session:
            meeting = await _seed_meeting(session, state="stopped")
            session.add_all(
                [
                    MeetingJob(
                        meeting_id=meeting.id,
                        job_kind=MeetingJobKind.FINAL_TRANSCRIPT.value,
                        idempotency_key="lane-finalization",
                    ),
                    MeetingJob(
                        meeting_id=meeting.id,
                        job_kind=MeetingJobKind.FINAL_MINUTES.value,
                        idempotency_key="lane-minutes",
                    ),
                    MeetingJob(
                        meeting_id=meeting.id,
                        job_kind=MeetingJobKind.CORRECTION.value,
                        idempotency_key="lane-correction",
                    ),
                ]
            )
            await session.commit()

        finalization = await _worker(
            factory,
            FakeRunner(),
            "finalization-worker",
            job_kinds={
                MeetingJobKind.FINAL_TRANSCRIPT.value,
                MeetingJobKind.SPEAKER_RECLUSTER.value,
            },
        ).claim_next()
        minutes = await _worker(
            factory,
            FakeRunner(),
            "minutes-worker",
            job_kinds={
                MeetingJobKind.FINAL_MINUTES.value,
                MeetingJobKind.ROLLING_MINUTES.value,
            },
        ).claim_next()
        correction = await _worker(
            factory,
            FakeRunner(),
            "correction-worker",
            job_kinds={MeetingJobKind.CORRECTION.value},
        ).claim_next()

        assert finalization is not None and finalization.job_kind == MeetingJobKind.FINAL_TRANSCRIPT.value
        assert minutes is not None and minutes.job_kind == MeetingJobKind.FINAL_MINUTES.value
        assert correction is not None and correction.job_kind == MeetingJobKind.CORRECTION.value
        await engine.dispose()

    anyio.run(scenario)


async def asyncio_gather(*awaitables):
    import asyncio

    return await asyncio.gather(*awaitables)


def test_expired_running_lease_is_recovered():
    async def scenario():
        engine = await _engine()
        factory = _factory(engine)
        async with factory() as session:
            meeting = await _seed_meeting(session)
            session.add(
                MeetingJob(
                    meeting_id=meeting.id,
                    job_kind="correction",
                    idempotency_key="expired-running",
                    state=MeetingJobState.RUNNING.value,
                    attempt=1,
                    max_attempts=3,
                    lease_owner="dead-worker",
                    lease_until=utcnow() - timedelta(seconds=10),
                )
            )
            await session.commit()

        recovered = await _worker(factory, FakeRunner(), "replacement-worker").claim_next()
        assert recovered is not None
        assert recovered.state == MeetingJobState.LEASED.value
        assert recovered.lease_owner == "replacement-worker"
        assert recovered.attempt == 2
        await engine.dispose()

    anyio.run(scenario)


def test_incremental_scheduler_debounces_and_uses_idempotent_watermarks():
    async def scenario():
        engine = await _engine()
        factory = _factory(engine)
        async with factory() as session:
            meeting = await _seed_meeting(session)
            segments = [
                await _add_segment(session, meeting, 1, "第一句"),
                await _add_segment(session, meeting, 2, "第二句"),
                await _add_segment(session, meeting, 3, "第三句"),
            ]
            for segment in segments:
                segment.created_at = utcnow() - timedelta(seconds=60)
                session.add(segment)
            await session.commit()

        worker = _worker(factory, FakeRunner())
        assert await worker.schedule_incremental_jobs() == 2
        assert await worker.schedule_incremental_jobs() == 0
        async with factory() as session:
            jobs = list((await session.exec(select(MeetingJob))).all())
            assert {value.job_kind for value in jobs} == {
                MeetingJobKind.CORRECTION.value,
                MeetingJobKind.ROLLING_MINUTES.value,
            }
            correction = next(value for value in jobs if value.job_kind == MeetingJobKind.CORRECTION.value)
            assert ":correction:range:1-3:" in correction.idempotency_key
            assert correction.input_watermark == 3
            assert all(value.settings_version == 1 for value in jobs)
        await engine.dispose()

    anyio.run(scenario)


def test_incremental_scheduler_respects_minutes_and_correction_lanes():
    async def scenario():
        engine = await _engine()
        factory = _factory(engine)
        async with factory() as session:
            meeting = await _seed_meeting(session)
            for ordinal in range(1, 4):
                segment = await _add_segment(session, meeting, ordinal, f"分 lane 句子 {ordinal}")
                segment.created_at = utcnow() - timedelta(seconds=60)
                session.add(segment)
            await session.commit()

        minutes_worker = _worker(
            factory,
            FakeRunner(),
            "minutes-worker",
            job_kinds={
                MeetingJobKind.FINAL_MINUTES.value,
                MeetingJobKind.ROLLING_MINUTES.value,
            },
        )
        correction_worker = _worker(
            factory,
            FakeRunner(),
            "correction-worker",
            job_kinds={MeetingJobKind.CORRECTION.value},
        )

        assert await minutes_worker.schedule_incremental_jobs() == 1
        async with factory() as session:
            first_jobs = list((await session.exec(select(MeetingJob))).all())
            assert {job.job_kind for job in first_jobs} == {MeetingJobKind.ROLLING_MINUTES.value}

        assert await correction_worker.schedule_incremental_jobs() == 1
        async with factory() as session:
            jobs = list((await session.exec(select(MeetingJob))).all())
            assert {job.job_kind for job in jobs} == {
                MeetingJobKind.CORRECTION.value,
                MeetingJobKind.ROLLING_MINUTES.value,
            }
        await engine.dispose()

    anyio.run(scenario)


def test_incremental_scheduler_batches_imported_recording_corrections():
    async def scenario():
        engine = await _engine()
        factory = _factory(engine)
        async with factory() as session:
            meeting = await _seed_meeting(session, state="stopped")
            meeting.audio_source = AudioSource.IMPORT.value
            session.add(meeting)
            for ordinal in range(1, 61):
                segment = await _add_segment(session, meeting, ordinal, f"导入逐字稿 {ordinal}")
                segment.created_at = utcnow() - timedelta(seconds=60)
                session.add(segment)
            await session.commit()

        worker = _worker(factory, FakeRunner())
        assert await worker.schedule_incremental_jobs() == 1
        async with factory() as session:
            correction = (
                await session.exec(select(MeetingJob).where(MeetingJob.job_kind == MeetingJobKind.CORRECTION.value))
            ).one()
            assert ":correction:range:1-50:" in correction.idempotency_key
            assert correction.input_watermark == 50
        await engine.dispose()

    anyio.run(scenario)


def test_correction_applies_minimal_patch_and_protects_human_and_entities():
    async def scenario():
        engine = await _engine()
        factory = _factory(engine)
        async with factory() as session:
            meeting = await _seed_meeting(session)
            normal = await _add_segment(session, meeting, 1, "耐莫创平台")
            locked = await _add_segment(session, meeting, 2, "人工锁定原文", human_locked=True)
            amount = await _add_segment(session, meeting, 3, "预算为100万元")
            wrong_base = await _add_segment(session, meeting, 4, "基线文本")
            wrong_original = await _add_segment(session, meeting, 5, "真实原文")
            session.add(
                MeetingJob(
                    meeting_id=meeting.id,
                    job_kind="correction",
                    idempotency_key="correction-1",
                    input_watermark=5,
                )
            )
            await session.commit()

        output = {
            "schema_version": "siq.meeting.correction.v1",
            "patches": [
                {
                    "segment_id": normal.id,
                    "base_revision": 0,
                    "original": "耐莫创平台",
                    "replacement": "Nemotron平台",
                    "reason_code": "term_correction",
                    "confidence": 0.96,
                },
                {
                    "segment_id": locked.id,
                    "base_revision": 0,
                    "original": "人工锁定原文",
                    "replacement": "试图覆盖人工文本",
                    "reason_code": "grammar_minimal",
                    "confidence": 0.99,
                },
                {
                    "segment_id": amount.id,
                    "base_revision": 0,
                    "original": "预算为100万元",
                    "replacement": "预算为200万元",
                    "reason_code": "itn",
                    "confidence": 0.99,
                },
                {
                    "segment_id": wrong_base.id,
                    "base_revision": 1,
                    "original": "基线文本",
                    "replacement": "修改文本",
                    "reason_code": "grammar_minimal",
                    "confidence": 0.99,
                },
                {
                    "segment_id": wrong_original.id,
                    "base_revision": 0,
                    "original": "伪造原文",
                    "replacement": "修改文本",
                    "reason_code": "grammar_minimal",
                    "confidence": 0.99,
                },
            ],
            "review_flags": [],
        }
        runner = FakeRunner({MeetingAITask.CORRECTION: output})
        assert await _worker(factory, runner).run_once() is True

        async with factory() as session:
            revisions = list((await session.exec(select(MeetingSegmentRevision))).all())
            assert len(revisions) == 1
            assert revisions[0].segment_id == normal.id
            assert revisions[0].text == "Nemotron平台"
            assert revisions[0].model_snapshot_id is not None
            job = (await session.exec(select(MeetingJob))).one()
            assert job.state == MeetingJobState.SUCCEEDED.value
            snapshot = await session.get(MeetingModelSnapshot, job.model_snapshot_id)
            assert snapshot.model_ref == "model:local:primary"
            review = (
                await session.exec(
                    select(MeetingEvent).where(MeetingEvent.event_type == "transcript.correction.review_required")
                )
            ).one()
            reasons = {item["reason"] for item in decode_json(review.payload_json, {})["rejected_patches"]}
            assert reasons == {
                "human_locked",
                "critical_entity_changed",
                "base_revision_mismatch",
                "original_mismatch",
            }
            corrected = (
                await session.exec(
                    select(MeetingEvent).where(MeetingEvent.event_type == "transcript.segment.corrected")
                )
            ).one()
            corrected_payload = decode_json(corrected.payload_json, {})
            assert corrected_payload["diff"]["operations"]
            assert corrected_payload["text"] == "Nemotron平台"
            assert corrected_payload["segment"]["display_text"] == "Nemotron平台"
            assert corrected_payload["segment"]["revision_no"] == 1
            assert corrected_payload["segment"]["human_locked"] is False
        await engine.dispose()

    anyio.run(scenario)


def test_final_minutes_use_latest_revision_and_version_artifacts():
    async def scenario():
        engine = await _engine()
        factory = _factory(engine)
        async with factory() as session:
            meeting = await _seed_meeting(session, state="stopped")
            segment = await _add_segment(session, meeting, 1, "初始识别")
            session.add(
                MeetingSegmentRevision(
                    segment_id=segment.id,
                    revision_no=1,
                    revision_type="manual",
                    text="人工确认后的文本",
                    base_revision_no=0,
                    created_by="7",
                )
            )
            old = MeetingArtifact(
                meeting_id=meeting.id,
                artifact_type=ArtifactType.FINAL_MINUTES.value,
                version=1,
                state=ArtifactState.READY.value,
                input_from_ordinal=1,
                input_to_ordinal=1,
                transcript_revision=0,
                content_json=encode_json({"overview": "Existing English minutes."}),
            )
            session.add(old)
            session.add(
                MeetingJob(
                    meeting_id=meeting.id,
                    job_kind="final_minutes",
                    idempotency_key="min-v2",
                    input_watermark=1,
                )
            )
            await session.commit()

        runner = FakeRunner(
            {MeetingAITask.FINAL_MINUTES: lambda call: _minutes_output(call["segments"][0]["segment_id"])}
        )
        await _worker(factory, runner).run_once()
        assert runner.calls[0]["segments"][0]["text"] == "人工确认后的文本"
        assert runner.calls[0]["segments"][0]["revision"] == 1
        assert runner.calls[0]["language"] == "zh-CN"

        async with factory() as session:
            artifacts = list((await session.exec(select(MeetingArtifact).order_by(MeetingArtifact.version))).all())
            assert [value.version for value in artifacts] == [1, 2]
            assert artifacts[0].state == ArtifactState.STALE.value
            assert decode_json(artifacts[0].content_json, {})["overview"] == "Existing English minutes."
            assert artifacts[1].state == ArtifactState.READY.value
            assert artifacts[1].supersedes_id == artifacts[0].id
            assert artifacts[1].transcript_revision == 1
            assert artifacts[1].model_snapshot_id is not None
            stored_meeting = await session.get(MeetingSession, meeting.id)
            assert stored_meeting.postprocess_state == "succeeded"
        await engine.dispose()

    anyio.run(scenario)


def test_rolling_minutes_are_temporary_and_evidence_bound():
    async def scenario():
        engine = await _engine()
        factory = _factory(engine)
        async with factory() as session:
            meeting = await _seed_meeting(session)
            segment = await _add_segment(session, meeting, 1, "滚动纪要证据")
            session.add(
                MeetingJob(
                    meeting_id=meeting.id,
                    job_kind="rolling_minutes",
                    idempotency_key="rolling-1",
                    input_watermark=1,
                )
            )
            await session.commit()

        output = _minutes_output(segment.id)
        output["schema_version"] = "siq.meeting.rolling_minutes.v1"
        output["temporary"] = True
        runner = FakeRunner({MeetingAITask.ROLLING_MINUTES: output})
        await _worker(factory, runner).run_once()

        async with factory() as session:
            artifact = (await session.exec(select(MeetingArtifact))).one()
            event = (
                await session.exec(select(MeetingEvent).where(MeetingEvent.event_type == "minutes.rolling.updated"))
            ).one()
            assert artifact.artifact_type == ArtifactType.ROLLING_MINUTES.value
            assert artifact.state == ArtifactState.READY.value
            assert decode_json(artifact.content_json, {})["temporary"] is True
            assert decode_json(event.payload_json, {})["temporary"] is True
        await engine.dispose()

    anyio.run(scenario)


def test_minutes_reject_unknown_evidence_without_persisting_ready_artifact():
    async def scenario():
        engine = await _engine()
        factory = _factory(engine)
        async with factory() as session:
            meeting = await _seed_meeting(session, state="stopped")
            segment = await _add_segment(session, meeting, 1, "有证据的文本")
            session.add(
                MeetingJob(
                    meeting_id=meeting.id,
                    job_kind="final_minutes",
                    idempotency_key="invalid-evidence",
                    input_watermark=1,
                )
            )
            await session.commit()

        runner = FakeRunner({MeetingAITask.FINAL_MINUTES: _minutes_output(segment.id, invalid_source=True)})
        await _worker(factory, runner).run_once()
        async with factory() as session:
            job = (await session.exec(select(MeetingJob))).one()
            meeting = await session.get(MeetingSession, job.meeting_id)
            artifacts = list((await session.exec(select(MeetingArtifact))).all())
            assert job.state == MeetingJobState.FAILED.value
            assert job.public_error_code == "MEETING_AI_OUTPUT_INVALID"
            assert meeting.state == "stopped"
            assert artifacts == []
        await engine.dispose()

    anyio.run(scenario)


def test_target_failure_retries_only_ai_job_and_never_changes_meeting_state():
    async def scenario():
        engine = await _engine()
        factory = _factory(engine)
        async with factory() as session:
            meeting = await _seed_meeting(session, state="live")
            await _add_segment(session, meeting, 1, "字幕继续可用")
            session.add(
                MeetingJob(
                    meeting_id=meeting.id,
                    job_kind="correction",
                    idempotency_key="target-down",
                    input_watermark=1,
                    max_attempts=2,
                )
            )
            await session.commit()

        runner = FakeRunner(
            {MeetingAITask.CORRECTION: {}},
            failure=MeetingHermesTargetUnavailable("target is down"),
        )
        worker = _worker(factory, runner, retry_delay_seconds=0)
        await worker.run_once()
        async with factory() as session:
            job = (await session.exec(select(MeetingJob))).one()
            assert job.state == MeetingJobState.RETRY_WAIT.value
            job.lease_until = utcnow() - timedelta(seconds=1)
            session.add(job)
            await session.commit()
        await worker.run_once()

        async with factory() as session:
            job = (await session.exec(select(MeetingJob))).one()
            meeting = await session.get(MeetingSession, job.meeting_id)
            segment = (await session.exec(select(MeetingTranscriptSegment))).one()
            assert job.state == MeetingJobState.FAILED.value
            assert job.attempt == 2
            assert job.public_error_code == "MODEL_TARGET_UNAVAILABLE"
            assert meeting.state == "live"
            assert segment.asr_final_text == "字幕继续可用"
        await engine.dispose()

    anyio.run(scenario)


def test_pinned_model_never_falls_back_and_none_never_calls_runner():
    async def scenario():
        engine = await _engine()
        factory = _factory(engine)
        async with factory() as session:
            pinned = await _seed_meeting(session, model_ref="model:missing:pinned", state="stopped")
            await _add_segment(session, pinned, 1, "固定模型")
            session.add(
                MeetingJob(
                    meeting_id=pinned.id,
                    job_kind="correction",
                    idempotency_key="pinned-no-fallback",
                    input_watermark=1,
                    max_attempts=1,
                )
            )
            disabled = await _seed_meeting(
                session,
                selection_mode=ModelSelectionMode.NONE.value,
                model_ref=None,
                ai_enabled=False,
                state="stopped",
            )
            session.add(
                MeetingJob(
                    meeting_id=disabled.id,
                    job_kind="correction",
                    idempotency_key="ai-none",
                    max_attempts=1,
                )
            )
            await session.commit()

        runner = FakeRunner({MeetingAITask.CORRECTION: {}})
        worker = _worker(factory, runner)
        await worker.run_once()
        await worker.run_once()
        async with factory() as session:
            jobs = {value.idempotency_key: value for value in (await session.exec(select(MeetingJob))).all()}
            assert jobs["pinned-no-fallback"].state == MeetingJobState.FAILED.value
            assert jobs["pinned-no-fallback"].public_error_code == "MODEL_TARGET_UNAVAILABLE"
            assert jobs["pinned-no-fallback"].model_snapshot_id is None
            assert jobs["ai-none"].state == MeetingJobState.SUCCEEDED.value
            assert jobs["ai-none"].model_snapshot_id is None
            assert runner.calls == []
        await engine.dispose()

    anyio.run(scenario)


def test_final_transcript_derives_recluster_then_one_idempotent_final_minutes_job():
    async def scenario():
        engine = await _engine()
        factory = _factory(engine)
        async with factory() as session:
            meeting = await _seed_meeting(session, state="stopped")
            await _add_segment(session, meeting, 1, "最终逐字稿")
            session.add_all(
                [
                    MeetingJob(
                        meeting_id=meeting.id,
                        job_kind="final_transcript",
                        idempotency_key="final-transcript-a",
                        input_watermark=1,
                    ),
                    MeetingJob(
                        meeting_id=meeting.id,
                        job_kind="final_transcript",
                        idempotency_key="final-transcript-b",
                        input_watermark=1,
                    ),
                ]
            )
            await session.commit()

        worker = _worker(factory, FakeRunner())
        await worker.run_once()
        await worker.run_once()
        await worker.run_once()
        async with factory() as session:
            jobs = list((await session.exec(select(MeetingJob))).all())
            final_transcript = [value for value in jobs if value.job_kind == "final_transcript"]
            speaker_recluster = [value for value in jobs if value.job_kind == "speaker_recluster"]
            final_minutes = [value for value in jobs if value.job_kind == "final_minutes"]
            assert len(final_transcript) == 2
            assert all(value.state == MeetingJobState.SUCCEEDED.value for value in final_transcript)
            assert len(speaker_recluster) == 1
            assert speaker_recluster[0].state == MeetingJobState.SUCCEEDED.value
            assert len(final_minutes) == 1
            assert final_minutes[0].state == MeetingJobState.QUEUED.value
        await engine.dispose()

    anyio.run(scenario)


def test_final_asr_creates_aligned_revisions_and_recluster_preserves_human_locks():
    async def scenario():
        engine = await _engine()
        factory = _factory(engine)
        async with factory() as session:
            meeting = await _seed_meeting(session, state="stopped")
            first_track = MeetingSpeakerTrack(
                meeting_id=meeting.id,
                track_key="live-0",
                anonymous_label="发言人 1",
            )
            second_track = MeetingSpeakerTrack(
                meeting_id=meeting.id,
                track_key="live-1",
                anonymous_label="发言人 2",
            )
            manual_track = MeetingSpeakerTrack(
                meeting_id=meeting.id,
                track_key="live-manual",
                anonymous_label="发言人 3",
                display_name="李总",
                label_source="manual",
            )
            session.add_all([first_track, second_track, manual_track])
            await session.flush()
            first = await _add_segment(session, meeting, 1, "旧文本一")
            second = await _add_segment(session, meeting, 2, "旧文本二")
            human = await _add_segment(session, meeting, 3, "人工确认文本", human_locked=True)
            first.speaker_track_id = first_track.id
            second.speaker_track_id = second_track.id
            human.speaker_track_id = manual_track.id
            session.add_all([first, second, human])
            session.add(
                MeetingSegmentRevision(
                    segment_id=human.id,
                    revision_no=1,
                    revision_type="manual",
                    text="人工确认文本",
                    base_revision_no=0,
                    created_by="7",
                )
            )
            job = MeetingJob(
                meeting_id=meeting.id,
                job_kind="final_transcript",
                idempotency_key="final-asr-aligned",
                input_watermark=3,
            )
            session.add(job)
            await session.commit()

        analysis = FinalizationAnalysis(
            mode="final_asr",
            chunk_count=6,
            total_audio_bytes=96_000,
            window_count=2,
            gaps=(),
            segments=(
                FinalASRSegment(
                    "f1",
                    "最终文本一",
                    0,
                    1_000,
                    "fake",
                    "final-speaker-0",
                    0.91,
                    (),
                    None,
                    0,
                    "diarizer-test-v1",
                ),
                FinalASRSegment(
                    "f2",
                    "最终文本二",
                    1_000,
                    2_000,
                    "fake",
                    "final-speaker-0",
                    0.92,
                    (),
                    None,
                    0,
                    "diarizer-test-v1",
                ),
                FinalASRSegment(
                    "f3",
                    "模型试图覆盖人工文本",
                    2_000,
                    3_000,
                    "fake",
                    "final-speaker-1",
                    0.93,
                    (),
                    None,
                    1,
                    "diarizer-test-v1",
                ),
            ),
            diarizer_ref="diarizer-test-v1",
            protocol_version=FINAL_ASR_INDEPENDENT_PROTOCOL,
            window_overlap_ms=500,
            max_concurrency=2,
        )
        finalization = FakeFinalizationService(analysis)
        worker = _worker(
            factory,
            FakeRunner(),
            finalization_service=finalization,
        )
        assert await worker.run_once() is True

        async with factory() as session:
            revisions = list(
                (
                    await session.exec(
                        select(MeetingSegmentRevision).order_by(
                            MeetingSegmentRevision.segment_id,
                            MeetingSegmentRevision.revision_no,
                        )
                    )
                ).all()
            )
            assert sum(value.revision_type == "final_asr_review" for value in revisions) == 2
            human_revisions = [value for value in revisions if value.segment_id == human.id]
            assert len(human_revisions) == 1
            assert human_revisions[0].text == "人工确认文本"
            alignment = (
                await session.exec(
                    select(MeetingArtifact).where(MeetingArtifact.artifact_type == "final_transcript_alignment")
                )
            ).one()
            alignment_payload = decode_json(alignment.content_json, {})
            assert alignment_payload["diarizer_ref"] == "diarizer-test-v1"
            assert alignment_payload["manifest"]["protocol_version"] == FINAL_ASR_INDEPENDENT_PROTOCOL
            assert alignment_payload["manifest"]["window_overlap_ms"] == 500
            assert alignment_payload["manifest"]["max_concurrency"] == 2
            assert alignment_payload["manifest"]["boundary_trimmed_segment_count"] == 0
            assert alignment_payload["revised_segment_count"] == 2
            assert alignment_payload["human_protected_segment_count"] == 1
            assert "embedding" not in alignment.content_json
        assert finalization.calls == [(meeting.id, job.id)]

        assert await worker.run_once() is True
        async with factory() as session:
            stored_first = await session.get(MeetingTranscriptSegment, first.id)
            stored_second = await session.get(MeetingTranscriptSegment, second.id)
            stored_human = await session.get(MeetingTranscriptSegment, human.id)
            stored_manual_track = await session.get(MeetingSpeakerTrack, manual_track.id)
            assert stored_first.speaker_track_id == stored_second.speaker_track_id
            assert stored_human.speaker_track_id == manual_track.id
            assert stored_manual_track.display_name == "李总"
            assert stored_manual_track.label_source == "manual"
            events = list((await session.exec(select(MeetingEvent))).all())
            assert any(value.event_type == "speaker.track.merged" for value in events)
            assert any(value.event_type == "speaker.recluster.completed" for value in events)
            jobs = list((await session.exec(select(MeetingJob))).all())
            assert sum(value.job_kind == "speaker_recluster" for value in jobs) == 1
            assert sum(value.job_kind == "final_minutes" for value in jobs) == 1
        await engine.dispose()

    anyio.run(scenario)


def test_recluster_splits_anonymous_track_using_mapping_event():
    async def scenario():
        engine = await _engine()
        factory = _factory(engine)
        async with factory() as session:
            meeting = await _seed_meeting(
                session, state="stopped", ai_enabled=False, selection_mode="none", model_ref=None
            )
            track = MeetingSpeakerTrack(
                meeting_id=meeting.id,
                track_key="live-shared",
                anonymous_label="发言人 1",
            )
            session.add(track)
            await session.flush()
            first = await _add_segment(session, meeting, 1, "甲")
            second = await _add_segment(session, meeting, 2, "乙")
            first.speaker_track_id = track.id
            second.speaker_track_id = track.id
            session.add_all([first, second])
            alignment = MeetingArtifact(
                meeting_id=meeting.id,
                artifact_type="final_transcript_alignment",
                version=1,
                state="ready",
                content_json=encode_json(
                    {
                        "schema_version": FINAL_ALIGNMENT_SCHEMA,
                        "alignments": [
                            {"stable_segment_id": first.id, "speaker_track_key": "speaker-a"},
                            {"stable_segment_id": second.id, "speaker_track_key": "speaker-b"},
                        ],
                    }
                ),
                input_from_ordinal=1,
                input_to_ordinal=2,
            )
            session.add(alignment)
            await session.flush()
            session.add(
                MeetingJob(
                    meeting_id=meeting.id,
                    job_kind="speaker_recluster",
                    idempotency_key="speaker-split",
                    input_watermark=2,
                    input_json=encode_json({"alignment_artifact_id": alignment.id}),
                )
            )
            await session.commit()

        worker = _worker(factory, FakeRunner())
        assert await worker.run_once() is True
        async with factory() as session:
            stored_first = await session.get(MeetingTranscriptSegment, first.id)
            stored_second = await session.get(MeetingTranscriptSegment, second.id)
            assert stored_first.speaker_track_id != stored_second.speaker_track_id
            split = (
                await session.exec(select(MeetingEvent).where(MeetingEvent.event_type == "speaker.track.split"))
            ).one()
            payload = decode_json(split.payload_json, {})
            assert payload["automatic"] is True
            assert payload["source_track_id"] == track.id
            assert set(payload["segment_ids_by_target"]) == {
                stored_first.speaker_track_id,
                stored_second.speaker_track_id,
            }
            stored_meeting = await session.get(MeetingSession, meeting.id)
            assert stored_meeting.postprocess_state == "succeeded"
        await engine.dispose()

    anyio.run(scenario)


def test_global_recluster_applies_mapping_and_requeues_voiceprint_for_active_target():
    async def scenario():
        engine = await _engine()
        factory = _factory(engine)
        async with factory() as session:
            meeting = await _seed_meeting(
                session,
                state="stopped",
                ai_enabled=False,
                selection_mode="none",
                model_ref=None,
                voiceprint_enabled=True,
            )
            target = MeetingSpeakerTrack(
                meeting_id=meeting.id,
                track_key="epoch-1:speaker-0",
                anonymous_label="发言人 1",
            )
            source = MeetingSpeakerTrack(
                meeting_id=meeting.id,
                track_key="epoch-2:speaker-0",
                anonymous_label="发言人 2",
            )
            session.add_all([target, source])
            await session.flush()
            first = await _add_segment(session, meeting, 1, "甲方发言")
            second = await _add_segment(session, meeting, 2, "同一人继续")
            first.speaker_track_id = target.id
            second.speaker_track_id = source.id
            session.add_all([first, second])
            alignment = MeetingArtifact(
                meeting_id=meeting.id,
                artifact_type="final_transcript_alignment",
                version=1,
                state="ready",
                content_json=encode_json(
                    {
                        "schema_version": FINAL_ALIGNMENT_SCHEMA,
                        "diarizer_ref": "diarizer-test-v1",
                        "alignments": [
                            {"stable_segment_id": first.id, "speaker_track_key": "final-speaker-a"},
                            {"stable_segment_id": second.id, "speaker_track_key": "final-speaker-b"},
                        ],
                    }
                ),
                input_from_ordinal=1,
                input_to_ordinal=2,
            )
            session.add(alignment)
            await session.flush()
            old_voiceprint_job = MeetingJob(
                meeting_id=meeting.id,
                job_kind=MeetingJobKind.VOICEPRINT_MATCH.value,
                idempotency_key="old-voiceprint-source",
                input_watermark=2,
                state=MeetingJobState.QUEUED.value,
            )
            recluster_job = MeetingJob(
                meeting_id=meeting.id,
                job_kind=MeetingJobKind.SPEAKER_RECLUSTER.value,
                idempotency_key="global-recluster",
                input_watermark=2,
                input_json=encode_json({"alignment_artifact_id": alignment.id}),
            )
            session.add_all([old_voiceprint_job, recluster_job])
            await session.commit()

        recluster = FakeSpeakerReclusterService(
            SpeakerReclusterPlan(
                track_targets={source.id: target.id},
                proposals=(
                    SpeakerMergeProposal(
                        source_track_ids=(source.id,),
                        target_track_id=target.id,
                        score=0.94,
                        auto_apply=True,
                        reason_code="AUTO_MERGE",
                    ),
                ),
                embedded_track_count=2,
                selected_sample_count=4,
                skipped_sample_count=1,
                encoder_ref="diarization-test-v1",
                final_diarizer_ref="diarizer-test-v1",
                policy_version="speaker-recluster.validated.calibration.v1",
                validation_artifact_sha256="b" * 64,
                automatic_enabled=True,
            )
        )
        worker = _worker(factory, FakeRunner(), speaker_recluster_service=recluster)
        assert await worker.run_once() is True
        assert len(recluster.calls) == 1
        assert recluster.calls[0]["meeting"].id == meeting.id

        async with factory() as session:
            stored_first = await session.get(MeetingTranscriptSegment, first.id)
            stored_second = await session.get(MeetingTranscriptSegment, second.id)
            assert stored_first.speaker_track_id == target.id
            assert stored_second.speaker_track_id == target.id

            artifact = (
                await session.exec(
                    select(MeetingArtifact).where(MeetingArtifact.artifact_type == "speaker_recluster")
                )
            ).one()
            payload = decode_json(artifact.content_json, {})
            assert payload["global_embedding_recluster"] == {
                "policy_version": "speaker-recluster.validated.calibration.v1",
                "final_diarizer_ref": "diarizer-test-v1",
                "observed_final_diarizer_ref": "diarizer-test-v1",
                "validation_artifact_sha256": "b" * 64,
                "automatic_enabled": True,
                "encoder_ref": "diarization-test-v1",
                "embedded_track_count": 2,
                "selected_sample_count": 4,
                "skipped_sample_count": 1,
                "degraded_reason": None,
                "proposals": [
                    {
                        "source_track_ids": [source.id],
                        "target_track_id": target.id,
                        "score": 0.94,
                        "auto_apply": True,
                        "reason_code": "AUTO_MERGE",
                    }
                ],
            }
            jobs = list((await session.exec(select(MeetingJob))).all())
            old = next(value for value in jobs if value.id == old_voiceprint_job.id)
            assert old.state == MeetingJobState.CANCELLED.value
            assert old.public_error_code == "SPEAKER_RECLUSTER_SUPERSEDED"
            queued = [
                value
                for value in jobs
                if value.job_kind == MeetingJobKind.VOICEPRINT_MATCH.value
                and value.state == MeetingJobState.QUEUED.value
            ]
            assert len(queued) == 1
            assert decode_json(queued[0].input_json, {})["speaker_track_id"] == target.id
            events = list((await session.exec(select(MeetingEvent))).all())
            assert any(value.event_type == "speaker.track.merged" for value in events)
            assert any(value.event_type == "voiceprint.match.queued" for value in events)
        await engine.dispose()

    anyio.run(scenario)


def test_global_recluster_drops_mapping_when_alignment_diarizer_does_not_match_policy():
    async def scenario():
        engine = await _engine()
        factory = _factory(engine)
        async with factory() as session:
            meeting = await _seed_meeting(
                session,
                state="stopped",
                ai_enabled=False,
                selection_mode="none",
                model_ref=None,
            )
            target = MeetingSpeakerTrack(
                meeting_id=meeting.id,
                track_key="runtime-a",
                anonymous_label="发言人 1",
            )
            source = MeetingSpeakerTrack(
                meeting_id=meeting.id,
                track_key="runtime-b",
                anonymous_label="发言人 2",
            )
            session.add_all([target, source])
            await session.flush()
            first = await _add_segment(session, meeting, 1, "甲方发言")
            second = await _add_segment(session, meeting, 2, "乙方发言")
            first.speaker_track_id = target.id
            second.speaker_track_id = source.id
            session.add_all([first, second])
            alignment = MeetingArtifact(
                meeting_id=meeting.id,
                artifact_type="final_transcript_alignment",
                version=1,
                state="ready",
                content_json=encode_json(
                    {
                        "schema_version": FINAL_ALIGNMENT_SCHEMA,
                        "diarizer_ref": "different-runtime-diarizer-v1",
                        "alignments": [
                            {"stable_segment_id": first.id, "speaker_track_key": "final-a"},
                            {"stable_segment_id": second.id, "speaker_track_key": "final-b"},
                        ],
                    }
                ),
                input_from_ordinal=1,
                input_to_ordinal=2,
            )
            session.add(alignment)
            await session.flush()
            session.add(
                MeetingJob(
                    meeting_id=meeting.id,
                    job_kind=MeetingJobKind.SPEAKER_RECLUSTER.value,
                    idempotency_key="runtime-diarizer-mismatch",
                    input_watermark=2,
                    input_json=encode_json({"alignment_artifact_id": alignment.id}),
                )
            )
            await session.commit()

        recluster = FakeSpeakerReclusterService(
            SpeakerReclusterPlan(
                track_targets={source.id: target.id},
                final_diarizer_ref="diarizer-test-v1",
                policy_version="speaker-recluster.validated.calibration.v1",
                validation_artifact_sha256="b" * 64,
                automatic_enabled=True,
            )
        )
        worker = _worker(factory, FakeRunner(), speaker_recluster_service=recluster)
        assert await worker.run_once() is True

        async with factory() as session:
            stored_first = await session.get(MeetingTranscriptSegment, first.id)
            stored_second = await session.get(MeetingTranscriptSegment, second.id)
            assert stored_first.speaker_track_id != stored_second.speaker_track_id
            artifact = (
                await session.exec(
                    select(MeetingArtifact).where(MeetingArtifact.artifact_type == "speaker_recluster")
                )
            ).one()
            payload = decode_json(artifact.content_json, {})["global_embedding_recluster"]
            assert payload["observed_final_diarizer_ref"] == "different-runtime-diarizer-v1"
            assert payload["automatic_enabled"] is False
            assert payload["degraded_reason"] == "SPEAKER_RECLUSTER_FINAL_DIARIZER_MISMATCH"
        await engine.dispose()

    anyio.run(scenario)


def test_import_recluster_embeds_provisional_final_tracks_when_live_tracks_are_missing():
    async def scenario():
        engine = await _engine()
        factory = _factory(engine)
        async with factory() as session:
            meeting = await _seed_meeting(
                session,
                state="stopped",
                ai_enabled=False,
                selection_mode="none",
                model_ref=None,
            )
            first = await _add_segment(session, meeting, 1, "导入片段一")
            second = await _add_segment(session, meeting, 2, "导入片段二")
            alignment = MeetingArtifact(
                meeting_id=meeting.id,
                artifact_type="final_transcript_alignment",
                version=1,
                state="ready",
                content_json=encode_json(
                    {
                        "schema_version": FINAL_ALIGNMENT_SCHEMA,
                        "diarizer_ref": "diarizer-test-v1",
                        "alignments": [
                            {"stable_segment_id": first.id, "speaker_track_key": "final-a"},
                            {"stable_segment_id": second.id, "speaker_track_key": "final-b"},
                        ],
                    }
                ),
                input_from_ordinal=1,
                input_to_ordinal=2,
            )
            session.add(alignment)
            await session.flush()
            session.add(
                MeetingJob(
                    meeting_id=meeting.id,
                    job_kind=MeetingJobKind.SPEAKER_RECLUSTER.value,
                    idempotency_key="import-global-recluster",
                    input_watermark=2,
                    input_json=encode_json({"alignment_artifact_id": alignment.id}),
                )
            )
            await session.commit()

        recluster = MergeAllSpeakerReclusterService()
        worker = _worker(factory, FakeRunner(), speaker_recluster_service=recluster)
        assert await worker.run_once() is True
        assert len(recluster.calls) == 1
        assert len(recluster.calls[0]["tracks"]) == 2
        assert all(segment.speaker_track_id for segment in recluster.calls[0]["segments"])

        async with factory() as session:
            stored_first = await session.get(MeetingTranscriptSegment, first.id)
            stored_second = await session.get(MeetingTranscriptSegment, second.id)
            assert stored_first.speaker_track_id == stored_second.speaker_track_id
            tracks = list((await session.exec(select(MeetingSpeakerTrack))).all())
            assert len(tracks) == 1
            artifact = (
                await session.exec(
                    select(MeetingArtifact).where(MeetingArtifact.artifact_type == "speaker_recluster")
                )
            ).one()
            payload = decode_json(artifact.content_json, {})
            assert payload["global_embedding_recluster"]["embedded_track_count"] == 2
        await engine.dispose()

    anyio.run(scenario)


def test_pending_recluster_does_not_undo_a_prior_manual_track_merge():
    async def scenario():
        engine = await _engine()
        factory = _factory(engine)
        async with factory() as session:
            meeting = await _seed_meeting(
                session,
                state="stopped",
                ai_enabled=False,
                selection_mode="none",
                model_ref=None,
            )
            target = MeetingSpeakerTrack(
                meeting_id=meeting.id,
                track_key="manual-target",
                anonymous_label="发言人 1",
            )
            source = MeetingSpeakerTrack(
                meeting_id=meeting.id,
                track_key="manual-source",
                anonymous_label="发言人 2",
            )
            session.add_all([target, source])
            await session.flush()
            first = await _add_segment(session, meeting, 1, "手工合并一")
            second = await _add_segment(session, meeting, 2, "手工合并二")
            first.speaker_track_id = target.id
            second.speaker_track_id = target.id
            session.add_all([first, second])
            session.add(
                MeetingEvent(
                    meeting_id=meeting.id,
                    cursor=1,
                    event_type="speaker.track.merged",
                    payload_json=encode_json(
                        {
                            "schema_version": "siq.meeting.speaker_mapping.v1",
                            "operation": "merge",
                            "automatic": False,
                            "source_track_ids": [source.id],
                            "target_track_id": target.id,
                            "segment_ids": [second.id],
                            "changed_by": "7",
                        }
                    ),
                )
            )
            alignment = MeetingArtifact(
                meeting_id=meeting.id,
                artifact_type="final_transcript_alignment",
                version=1,
                state="ready",
                content_json=encode_json(
                    {
                        "schema_version": FINAL_ALIGNMENT_SCHEMA,
                        "alignments": [
                            {"stable_segment_id": first.id, "speaker_track_key": "final-a"},
                            {"stable_segment_id": second.id, "speaker_track_key": "final-b"},
                        ],
                    }
                ),
                input_from_ordinal=1,
                input_to_ordinal=2,
            )
            session.add(alignment)
            await session.flush()
            session.add(
                MeetingJob(
                    meeting_id=meeting.id,
                    job_kind=MeetingJobKind.SPEAKER_RECLUSTER.value,
                    idempotency_key="manual-merge-recluster",
                    input_watermark=2,
                    input_json=encode_json({"alignment_artifact_id": alignment.id}),
                )
            )
            await session.commit()

        recluster = FakeSpeakerReclusterService(SpeakerReclusterPlan())
        worker = _worker(factory, FakeRunner(), speaker_recluster_service=recluster)
        assert await worker.run_once() is True
        async with factory() as session:
            stored_first = await session.get(MeetingTranscriptSegment, first.id)
            stored_second = await session.get(MeetingTranscriptSegment, second.id)
            assert stored_first.speaker_track_id == target.id
            assert stored_second.speaker_track_id == target.id
        await engine.dispose()

    anyio.run(scenario)


def test_unvalidated_global_mapping_is_ignored_even_if_service_returns_targets():
    async def scenario():
        engine = await _engine()
        factory = _factory(engine)
        async with factory() as session:
            meeting = await _seed_meeting(
                session,
                state="stopped",
                ai_enabled=False,
                selection_mode="none",
                model_ref=None,
            )
            first_track = MeetingSpeakerTrack(
                meeting_id=meeting.id,
                track_key="unvalidated-a",
                anonymous_label="发言人 1",
            )
            second_track = MeetingSpeakerTrack(
                meeting_id=meeting.id,
                track_key="unvalidated-b",
                anonymous_label="发言人 2",
            )
            session.add_all([first_track, second_track])
            await session.flush()
            first = await _add_segment(session, meeting, 1, "未校准一")
            second = await _add_segment(session, meeting, 2, "未校准二")
            first.speaker_track_id = first_track.id
            second.speaker_track_id = second_track.id
            session.add_all([first, second])
            alignment = MeetingArtifact(
                meeting_id=meeting.id,
                artifact_type="final_transcript_alignment",
                version=1,
                state="ready",
                content_json=encode_json(
                    {
                        "schema_version": FINAL_ALIGNMENT_SCHEMA,
                        "alignments": [
                            {"stable_segment_id": first.id, "speaker_track_key": None},
                            {"stable_segment_id": second.id, "speaker_track_key": None},
                        ],
                    }
                ),
                input_from_ordinal=1,
                input_to_ordinal=2,
            )
            session.add(alignment)
            await session.flush()
            session.add(
                MeetingJob(
                    meeting_id=meeting.id,
                    job_kind=MeetingJobKind.SPEAKER_RECLUSTER.value,
                    idempotency_key="unvalidated-global-map",
                    input_watermark=2,
                    input_json=encode_json({"alignment_artifact_id": alignment.id}),
                )
            )
            await session.commit()

        worker = _worker(
            factory,
            FakeRunner(),
            speaker_recluster_service=UnvalidatedMappingSpeakerReclusterService(),
        )
        assert await worker.run_once() is True
        async with factory() as session:
            stored_first = await session.get(MeetingTranscriptSegment, first.id)
            stored_second = await session.get(MeetingTranscriptSegment, second.id)
            assert stored_first.speaker_track_id != stored_second.speaker_track_id
            artifact = (
                await session.exec(
                    select(MeetingArtifact).where(MeetingArtifact.artifact_type == "speaker_recluster")
                )
            ).one()
            payload = decode_json(artifact.content_json, {})
            assert (
                payload["global_embedding_recluster"]["degraded_reason"]
                == "SPEAKER_RECLUSTER_POLICY_GATE_INVALID"
            )
        await engine.dispose()

    anyio.run(scenario)


def test_final_key_does_not_auto_merge_anonymous_track_into_manual_identity():
    async def scenario():
        engine = await _engine()
        factory = _factory(engine)
        async with factory() as session:
            meeting = await _seed_meeting(
                session,
                state="stopped",
                ai_enabled=False,
                selection_mode="none",
                model_ref=None,
            )
            manual = MeetingSpeakerTrack(
                meeting_id=meeting.id,
                track_key="manual-identity",
                anonymous_label="发言人 1",
                display_name="李总",
                label_source="manual",
            )
            anonymous = MeetingSpeakerTrack(
                meeting_id=meeting.id,
                track_key="anonymous-other",
                anonymous_label="发言人 2",
            )
            session.add_all([manual, anonymous])
            await session.flush()
            first = await _add_segment(session, meeting, 1, "具名发言")
            second = await _add_segment(session, meeting, 2, "匿名发言")
            first.speaker_track_id = manual.id
            second.speaker_track_id = anonymous.id
            session.add_all([first, second])
            alignment = MeetingArtifact(
                meeting_id=meeting.id,
                artifact_type="final_transcript_alignment",
                version=1,
                state="ready",
                content_json=encode_json(
                    {
                        "schema_version": FINAL_ALIGNMENT_SCHEMA,
                        "alignments": [
                            {"stable_segment_id": first.id, "speaker_track_key": "final-shared"},
                            {"stable_segment_id": second.id, "speaker_track_key": "final-shared"},
                        ],
                    }
                ),
                input_from_ordinal=1,
                input_to_ordinal=2,
            )
            session.add(alignment)
            await session.flush()
            session.add(
                MeetingJob(
                    meeting_id=meeting.id,
                    job_kind=MeetingJobKind.SPEAKER_RECLUSTER.value,
                    idempotency_key="protected-final-key",
                    input_watermark=2,
                    input_json=encode_json({"alignment_artifact_id": alignment.id}),
                )
            )
            await session.commit()

        worker = _worker(
            factory,
            FakeRunner(),
            speaker_recluster_service=FakeSpeakerReclusterService(SpeakerReclusterPlan()),
        )
        assert await worker.run_once() is True
        async with factory() as session:
            stored_first = await session.get(MeetingTranscriptSegment, first.id)
            stored_second = await session.get(MeetingTranscriptSegment, second.id)
            assert stored_first.speaker_track_id == manual.id
            assert stored_second.speaker_track_id == anonymous.id
            artifact = (
                await session.exec(
                    select(MeetingArtifact).where(MeetingArtifact.artifact_type == "speaker_recluster")
                )
            ).one()
            payload = decode_json(artifact.content_json, {})
            assert payload["review_clusters"][0]["reason_code"] == "PROTECTED_TRACK_MERGE_REQUIRES_REVIEW"
        await engine.dispose()

    anyio.run(scenario)


def test_final_asr_failure_retries_without_changing_capture_or_transcript():
    async def scenario():
        engine = await _engine()
        factory = _factory(engine)
        async with factory() as session:
            meeting = await _seed_meeting(session, state="stopped")
            segment = await _add_segment(session, meeting, 1, "稳定文本仍可用")
            session.add(
                MeetingJob(
                    meeting_id=meeting.id,
                    job_kind="final_transcript",
                    idempotency_key="final-asr-down",
                    input_watermark=1,
                    max_attempts=2,
                )
            )
            await session.commit()

        worker = _worker(
            factory,
            FakeRunner(),
            finalization_service=FakeFinalizationService(
                failure=MeetingFinalizationUnavailable("speech service unavailable")
            ),
        )
        assert await worker.run_once() is True
        async with factory() as session:
            job = (await session.exec(select(MeetingJob))).one()
            stored_meeting = await session.get(MeetingSession, meeting.id)
            stored_segment = await session.get(MeetingTranscriptSegment, segment.id)
            assert job.state == MeetingJobState.RETRY_WAIT.value
            assert job.public_error_code == "MEETING_FINAL_ASR_UNAVAILABLE"
            assert stored_meeting.state == "stopped"
            assert stored_segment.asr_final_text == "稳定文本仍可用"
            assert list((await session.exec(select(MeetingArtifact))).all()) == []
        await engine.dispose()

    anyio.run(scenario)


def test_expired_final_attempt_is_reclaimed_after_worker_crash():
    async def scenario():
        engine = await _engine()
        factory = _factory(engine)
        async with factory() as session:
            meeting = await _seed_meeting(session, state="stopped")
            await _add_segment(session, meeting, 1, "崩溃恢复")
            job = MeetingJob(
                meeting_id=meeting.id,
                job_kind="final_transcript",
                idempotency_key="final-crash-recovery",
                input_watermark=1,
                state=MeetingJobState.RUNNING.value,
                attempt=3,
                max_attempts=3,
                lease_owner="dead-worker",
                lease_until=utcnow() - timedelta(seconds=1),
            )
            session.add(job)
            await session.commit()

        worker = _worker(factory, FakeRunner(), worker_id="recovery-worker")
        assert await worker.run_once() is True
        async with factory() as session:
            recovered = await session.get(MeetingJob, job.id)
            assert recovered.state == MeetingJobState.SUCCEEDED.value
            assert recovered.attempt == 4
            speaker_jobs = list(
                (await session.exec(select(MeetingJob).where(MeetingJob.job_kind == "speaker_recluster"))).all()
            )
            assert len(speaker_jobs) == 1
        await engine.dispose()

    anyio.run(scenario)
