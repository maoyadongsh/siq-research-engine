import json

import anyio
import pytest
from services.auth_service import User
from services.meeting_contracts import (
    MEETING_TABLES,
    CorrectionEditIntent,
    MeetingASRCorrectionEvent,
    MeetingCreateRequest,
    MeetingEvent,
    MeetingSegmentRevision,
    MeetingTermCandidate,
    ModelSelectionInput,
    SegmentCorrectionRequest,
    StableSegmentInput,
)
from services.meeting_repository import (
    MeetingRepository,
    MeetingResourceNotFound,
    MeetingVersionConflict,
)
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


def _segment(key: str = "provider-1", text: str = "欢迎张疆介绍项目") -> StableSegmentInput:
    return StableSegmentInput(
        utterance_id=f"utterance-{key}",
        provider_segment_key=key,
        start_ms=100,
        end_ms=1800,
        raw_text=text,
        asr_final_text=text,
        asr_confidence=0.9,
        asr_provider="meeting-speech",
        asr_model="opaque-asr",
        asr_version="v1",
    )


def test_repository_enforces_owner_and_create_idempotency():
    async def run():
        engine = await _database()
        async with AsyncSession(engine, expire_on_commit=False) as session:
            repository = MeetingRepository(session)
            request = MeetingCreateRequest(title="产品例会")
            created, replayed, event = await repository.create_session(
                7, request, idempotency_key="create-products-weekly"
            )
            assert replayed is False
            assert event.cursor == 1

            replay, replayed, replay_event = await repository.create_session(
                7, request, idempotency_key="create-products-weekly"
            )
            assert replay.id == created.id
            assert replayed is True
            assert replay_event is None

            with pytest.raises(MeetingResourceNotFound):
                await repository.get_session(created.id, 8)
        await engine.dispose()

    anyio.run(run)


def test_stable_segment_and_outbox_are_idempotent_and_monotonic():
    async def run():
        engine = await _database()
        async with AsyncSession(engine, expire_on_commit=False) as session:
            repository = MeetingRepository(session)
            meeting, _, _ = await repository.create_session(7, MeetingCreateRequest(title="研发会"))
            first, duplicate, event = await repository.append_stable_segment(
                meeting.id, 7, _segment()
            )
            assert first.ordinal == 1
            assert duplicate is False
            assert event.event_type == "transcript.segment.stable"
            assert event.cursor == 2
            stable_payload = json.loads(event.payload_json)
            assert stable_payload["segment"]["id"] == first.id
            assert stable_payload["segment"]["text_state"] == "stable"

            same, duplicate, duplicate_event = await repository.append_stable_segment(
                meeting.id, 7, _segment()
            )
            assert same.id == first.id
            assert duplicate is True
            assert duplicate_event is None

            second, _, second_event = await repository.append_stable_segment(
                meeting.id, 7, _segment("provider-2", "第二句")
            )
            assert second.ordinal == 2
            assert second_event.cursor == 3
            events = (
                await session.exec(
                    select(MeetingEvent)
                    .where(MeetingEvent.meeting_id == meeting.id)
                    .order_by(MeetingEvent.cursor)
                )
            ).all()
            assert [item.cursor for item in events] == [1, 2, 3]
        await engine.dispose()

    anyio.run(run)


def test_manual_correction_writes_revision_feedback_and_only_eligible_candidate():
    async def run():
        engine = await _database()
        async with AsyncSession(engine, expire_on_commit=False) as session:
            repository = MeetingRepository(session)
            meeting, _, _ = await repository.create_session(7, MeetingCreateRequest(title="投研会"))
            segment, _, _ = await repository.append_stable_segment(meeting.id, 7, _segment())
            result = await repository.correct_segment(
                meeting.id,
                segment.id,
                7,
                SegmentCorrectionRequest(
                    text="欢迎张江介绍项目",
                    expected_revision=0,
                    edit_intent=CorrectionEditIntent.ASR_ERROR,
                    contribute_to_accuracy=True,
                    candidate_terms=[
                        {
                            "canonical_term": "张江",
                            "misrecognition": "张疆",
                            "promote_now": True,
                        }
                    ],
                ),
                idempotency_key="correct-segment-1",
                correction_learning_enabled=True,
            )
            assert result.segment.display_text == "欢迎张江介绍项目"
            assert result.segment.human_locked is True
            assert result.revision.revision_no == 1
            assert result.feedback.error_class == "entity"
            assert result.feedback.contribute_to_accuracy is True
            assert len(result.candidate_ids) == 1

            revisions = (await session.exec(select(MeetingSegmentRevision))).all()
            feedback = (await session.exec(select(MeetingASRCorrectionEvent))).all()
            candidates = (await session.exec(select(MeetingTermCandidate))).all()
            assert len(revisions) == len(feedback) == len(candidates) == 1
            event_types = list(
                (
                    await session.exec(
                        select(MeetingEvent.event_type)
                        .where(MeetingEvent.meeting_id == meeting.id)
                        .order_by(MeetingEvent.cursor)
                    )
                ).all()
            )
            assert "transcript.segment.human_edited" in event_types
            assert "asr.feedback.recorded" in event_types
            assert "lexicon.candidate.created" in event_types

            second, _, _ = await repository.append_stable_segment(
                meeting.id, 7, _segment("provider-2", "今天开会")
            )
            content_edit = await repository.correct_segment(
                meeting.id,
                second.id,
                7,
                SegmentCorrectionRequest(
                    text="今天召开项目讨论会",
                    expected_revision=0,
                    edit_intent=CorrectionEditIntent.CONTENT_EDIT,
                    contribute_to_accuracy=True,
                    candidate_terms=[{"canonical_term": "项目讨论会", "misrecognition": "开会"}],
                ),
                correction_learning_enabled=True,
            )
            assert content_edit.feedback.contribute_to_accuracy is False
            assert content_edit.candidate_ids == []
            assert len((await session.exec(select(MeetingTermCandidate))).all()) == 1
            await repository.confirm_term_candidate(
                candidates[0].id,
                7,
                correction_learning_enabled=True,
            )
            version_event = (
                await session.exec(
                    select(MeetingEvent)
                    .where(
                        MeetingEvent.meeting_id == meeting.id,
                        MeetingEvent.event_type == "lexicon.version.activated",
                    )
                    .order_by(MeetingEvent.cursor.desc())
                )
            ).first()
            assert version_event is not None
            assert json.loads(version_event.payload_json)["version"] == meeting.active_lexicon_version
        await engine.dispose()

    anyio.run(run)


def test_model_settings_use_optimistic_lock_and_effective_boundary():
    async def run():
        engine = await _database()
        async with AsyncSession(engine, expire_on_commit=False) as session:
            repository = MeetingRepository(session)
            meeting, _, _ = await repository.create_session(7, MeetingCreateRequest(title="模型会"))
            meeting, setting, _ = await repository.update_model_selection(
                meeting.id,
                7,
                ModelSelectionInput(mode="auto", fallback_policy="local_only"),
                expected_settings_version=1,
            )
            assert meeting.settings_version == 2
            assert setting.settings_version == 2
            assert setting.effective_after_segment_ordinal == 0
            model_event = (
                await session.exec(
                    select(MeetingEvent).where(
                        MeetingEvent.meeting_id == meeting.id,
                        MeetingEvent.event_type == "model.selection.changed",
                    )
                )
            ).first()
            assert model_event is not None

            with pytest.raises(MeetingVersionConflict) as error:
                await repository.update_model_selection(
                    meeting.id,
                    7,
                    ModelSelectionInput(mode="none"),
                    expected_settings_version=1,
                )
            assert error.value.current["settings_version"] == 2
        await engine.dispose()

    anyio.run(run)
