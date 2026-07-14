from datetime import timedelta

import anyio
import pytest
from services.auth_service import User
from services.meeting_contracts import (
    MEETING_TABLES,
    ArtifactState,
    ArtifactType,
    MeetingArtifact,
    MeetingCreateRequest,
    MeetingModelSnapshot,
    MeetingSpeakerTrack,
    MeetingTranscriptSegment,
    SpeakerRenameRequest,
    utcnow,
)
from services.meeting_hermes_runner import FinalMinutesResult
from services.meeting_repository import MeetingInvalidOperation, MeetingRepository
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


def test_final_minutes_schema_keeps_keywords_in_the_same_evidence_contract():
    value = FinalMinutesResult.model_validate(
        {
            "schema_version": "siq.meeting.final_minutes.v1",
            "overview": "会议摘要",
            "keywords": [
                {
                    "text": "客户留存率",
                    "source_segment_ids": ["segment-1"],
                }
            ],
        }
    )

    assert value.keywords[0].text == "客户留存率"
    assert value.keywords[0].source_segment_ids == ["segment-1"]


def _snapshot(meeting_id: str, model: str, locality: str, *, seconds: int):
    return MeetingModelSnapshot(
        meeting_id=meeting_id,
        model_ref=f"meeting:test:{model}",
        selection_mode="pinned",
        resolved_provider="test-provider",
        resolved_model=model,
        provider_locality=locality,
        hermes_target="test-target",
        meeting_profile_version="meeting.v1",
        prompt_version="minutes.v1",
        settings_version=1,
        resolved_at=utcnow() + timedelta(seconds=seconds),
    )


def test_session_list_returns_bounded_speaker_and_latest_model_summary():
    async def scenario():
        engine = await _database()
        async with AsyncSession(engine, expire_on_commit=False) as session:
            repository = MeetingRepository(session)
            meeting, _, _ = await repository.create_session(7, MeetingCreateRequest(title="产品周会"))
            first_speaker = MeetingSpeakerTrack(
                meeting_id=meeting.id,
                track_key="speaker-1",
                anonymous_label="发言人 1",
            )
            second_speaker = MeetingSpeakerTrack(
                meeting_id=meeting.id,
                track_key="speaker-2",
                anonymous_label="发言人 2",
            )
            session.add_all(
                [
                    first_speaker,
                    second_speaker,
                    MeetingTranscriptSegment(
                        meeting_id=meeting.id,
                        ordinal=1,
                        utterance_id="utterance-1",
                        provider_segment_key="provider-1",
                        start_ms=0,
                        end_ms=1_000,
                        speaker_track_id=first_speaker.id,
                        raw_text="第一位发言",
                        asr_final_text="第一位发言",
                        asr_provider="test-asr",
                        asr_model="test-model",
                        asr_version="v1",
                    ),
                    MeetingTranscriptSegment(
                        meeting_id=meeting.id,
                        ordinal=2,
                        utterance_id="utterance-2",
                        provider_segment_key="provider-2",
                        start_ms=1_000,
                        end_ms=2_000,
                        speaker_track_id=second_speaker.id,
                        raw_text="第二位发言",
                        asr_final_text="第二位发言",
                        asr_provider="test-asr",
                        asr_model="test-model",
                        asr_version="v1",
                    ),
                    _snapshot(meeting.id, "older-model", "cloud", seconds=1),
                    _snapshot(meeting.id, "latest-model", "local", seconds=2),
                ]
            )
            await session.commit()

            values, total = await repository.list_sessions(7)

            assert total == 1
            assert len(values) == 1
            assert values[0].session.id == meeting.id
            assert values[0].speaker_count == 2
            assert values[0].model_label == "latest-model"
            assert values[0].model_locality == "local"
        await engine.dispose()

    anyio.run(scenario)


def test_speaker_rename_stales_only_minutes_artifacts_in_the_same_transaction():
    async def scenario():
        engine = await _database()
        async with AsyncSession(engine, expire_on_commit=False) as session:
            repository = MeetingRepository(session)
            meeting, _, _ = await repository.create_session(7, MeetingCreateRequest(title="评审会"))
            speaker = MeetingSpeakerTrack(
                meeting_id=meeting.id,
                track_key="speaker-1",
                anonymous_label="发言人 1",
            )
            session.add(speaker)
            session.add_all(
                [
                    MeetingArtifact(
                        meeting_id=meeting.id,
                        artifact_type=ArtifactType.ROLLING_MINUTES.value,
                        version=1,
                        state=ArtifactState.READY.value,
                    ),
                    MeetingArtifact(
                        meeting_id=meeting.id,
                        artifact_type=ArtifactType.FINAL_MINUTES.value,
                        version=1,
                        state=ArtifactState.READY.value,
                    ),
                    MeetingArtifact(
                        meeting_id=meeting.id,
                        artifact_type=ArtifactType.FINAL_TRANSCRIPT_ALIGNMENT.value,
                        version=1,
                        state=ArtifactState.READY.value,
                    ),
                ]
            )
            await session.commit()

            await repository.rename_speaker(
                meeting.id,
                speaker.id,
                7,
                SpeakerRenameRequest(display_name="张明", expected_version=1),
            )
            artifacts = list(
                (
                    await session.exec(
                        select(MeetingArtifact).where(MeetingArtifact.meeting_id == meeting.id)
                    )
                ).all()
            )
            states = {artifact.artifact_type: artifact.state for artifact in artifacts}

            assert states[ArtifactType.ROLLING_MINUTES.value] == ArtifactState.STALE.value
            assert states[ArtifactType.FINAL_MINUTES.value] == ArtifactState.STALE.value
            assert states[ArtifactType.FINAL_TRANSCRIPT_ALIGNMENT.value] == ArtifactState.READY.value
        await engine.dispose()

    anyio.run(scenario)


def test_regeneration_creates_a_new_minutes_version_and_rejects_other_artifacts():
    async def scenario():
        engine = await _database()
        async with AsyncSession(engine, expire_on_commit=False) as session:
            repository = MeetingRepository(session)
            meeting, _, _ = await repository.create_session(7, MeetingCreateRequest(title="会后复盘"))
            previous = MeetingArtifact(
                meeting_id=meeting.id,
                artifact_type=ArtifactType.FINAL_MINUTES.value,
                version=3,
                state=ArtifactState.READY.value,
                transcript_revision=4,
            )
            alignment = MeetingArtifact(
                meeting_id=meeting.id,
                artifact_type=ArtifactType.FINAL_TRANSCRIPT_ALIGNMENT.value,
                version=1,
                state=ArtifactState.READY.value,
            )
            session.add_all([previous, alignment])
            await session.commit()

            created, job = await repository.regenerate_artifact(
                meeting.id,
                previous.id,
                7,
                expected_settings_version=1,
            )

            assert previous.state == ArtifactState.STALE.value
            assert created.version == 4
            assert created.state == ArtifactState.GENERATING.value
            assert created.supersedes_id == previous.id
            assert job.job_kind == "final_minutes"
            with pytest.raises(MeetingInvalidOperation):
                await repository.regenerate_artifact(
                    meeting.id,
                    alignment.id,
                    7,
                    expected_settings_version=1,
                )
        await engine.dispose()

    anyio.run(scenario)
