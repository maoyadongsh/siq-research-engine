import anyio
import pytest
from routers import meeting_stream
from services.auth_service import User
from services.meeting_contracts import (
    MEETING_TABLES,
    LexiconEntryCreateRequest,
    MeetingCreateRequest,
    MeetingLexiconVersion,
    MeetingTranscriptSegment,
)
from services.meeting_repository import MeetingRepository, MeetingResourceNotFound
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


def test_meeting_scoped_lexicon_snapshots_never_cross_meeting_or_owner(monkeypatch):
    async def run():
        engine = await _database()
        monkeypatch.setattr(meeting_stream, "async_engine", engine)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            repository = MeetingRepository(session)
            meeting_a, _, _ = await repository.create_session(
                7,
                MeetingCreateRequest(title="会议 A"),
            )
            meeting_b, _, _ = await repository.create_session(
                7,
                MeetingCreateRequest(title="会议 B"),
            )
            meeting_other, _, _ = await repository.create_session(
                8,
                MeetingCreateRequest(title="其他用户会议"),
            )

            _, owner_version = await repository.create_lexicon_entry(
                7,
                LexiconEntryCreateRequest(
                    canonical_term="Nemotron",
                    scope="user_future_meetings",
                ),
            )
            _, meeting_a_version = await repository.create_lexicon_entry(
                7,
                LexiconEntryCreateRequest(
                    canonical_term="海光信息",
                    scope="current_meeting",
                    meeting_id=meeting_a.id,
                ),
            )
            _, meeting_b_version = await repository.create_lexicon_entry(
                7,
                LexiconEntryCreateRequest(
                    canonical_term="寒武纪",
                    scope="current_meeting",
                    meeting_id=meeting_b.id,
                ),
            )

            assert owner_version.meeting_id is None
            assert meeting_a_version.meeting_id == meeting_a.id
            assert meeting_b_version.meeting_id == meeting_b.id

        hotwords_a, version_a = await meeting_stream._load_hotwords(7, meeting_a.id)
        hotwords_b, version_b = await meeting_stream._load_hotwords(7, meeting_b.id)
        hotwords_other, version_other = await meeting_stream._load_hotwords(8, meeting_other.id)

        assert hotwords_a == ["Nemotron", "海光信息"]
        assert hotwords_b == ["Nemotron", "寒武纪"]
        assert hotwords_other == []
        assert version_a == meeting_a_version.version
        assert version_b == meeting_b_version.version
        assert version_other is not None
        with pytest.raises(MeetingResourceNotFound):
            await meeting_stream._load_hotwords(8, meeting_a.id)

        await meeting_stream._persist_final(
            {
                "schema_version": "siq.meeting.speech.event.v1",
                "trace_id": None,
                "payload": {
                    "segment_token": "lexicon-provenance-a",
                    "text": "海光信息",
                    "start_ms": 0,
                    "end_ms": 800,
                    "adapter": "test-asr",
                    "word_timestamps": [],
                },
            },
            meeting_id=meeting_a.id,
            owner_user_id=7,
            stream_epoch=1,
            hotword_version=version_a,
        )

        async with AsyncSession(engine) as session:
            segment = (
                await session.exec(
                    select(MeetingTranscriptSegment).where(
                        MeetingTranscriptSegment.meeting_id == meeting_a.id
                    )
                )
            ).one()
            assert segment.hotword_version == meeting_a_version.version
            versions = list((await session.exec(select(MeetingLexiconVersion))).all())
            snapshots = {
                value.meeting_id: value
                for value in versions
                if value.is_active and value.owner_user_id == 7
            }
            assert snapshots[meeting_a.id].version == meeting_a_version.version
            assert snapshots[meeting_b.id].version == meeting_b_version.version
            assert snapshots[None].version == owner_version.version
        await engine.dispose()

    anyio.run(run)
