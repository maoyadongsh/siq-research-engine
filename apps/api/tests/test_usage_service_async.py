import anyio
import pytest
from services.usage_service import (
    AGENT_QUESTION_EVENT,
    DOCUMENT_PARSE_EVENT,
    ensure_within_quota_async,
    get_usage_count_async,
    record_usage_async,
    usage_response_payload_async,
)
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession


async def _with_usage_db(tmp_path, callback):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'usage-service.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with AsyncSession(engine) as session:
            await callback(session)
    finally:
        await engine.dispose()


def test_async_usage_helpers_record_and_sum_counts(tmp_path):
    statements = []

    async def run_case(session: AsyncSession):
        first = await record_usage_async(
            session,
            user_id=7,
            event_type=DOCUMENT_PARSE_EVENT,
            count=2,
            source="document_parser",
            metadata_json='{"task_id":"task-1"}',
        )
        assert first.id is not None
        assert first.created_at.tzinfo is None

        second = await record_usage_async(
            session,
            user_id=7,
            event_type=DOCUMENT_PARSE_EVENT,
            count=3,
            source="chat_attachment",
        )

        assert second.source == "chat_attachment"
        assert await get_usage_count_async(session, 7, DOCUMENT_PARSE_EVENT) == 5
        assert await get_usage_count_async(session, 7, DOCUMENT_PARSE_EVENT, day_key="2099-01-01") == 0
        assert await get_usage_count_async(session, 8, DOCUMENT_PARSE_EVENT) == 0

    async def run_with_sql_capture():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'usage-sum.db'}")
        event.listen(
            engine.sync_engine,
            "before_cursor_execute",
            lambda _conn, _cursor, statement, _parameters, _context, _executemany: statements.append(statement),
        )
        try:
            async with engine.begin() as connection:
                await connection.run_sync(SQLModel.metadata.create_all)
            async with AsyncSession(engine) as session:
                await run_case(session)
        finally:
            await engine.dispose()

    anyio.run(run_with_sql_capture)

    aggregate_queries = [
        statement.lower()
        for statement in statements
        if "from usage_events" in statement.lower() and "sum(" in statement.lower()
    ]
    assert len(aggregate_queries) == 3
    assert all("sum(usage_events.count)" in statement for statement in aggregate_queries)
    assert all("usage_events.id" not in statement for statement in aggregate_queries)


def test_async_quota_helper_matches_sync_quota_contract(tmp_path):
    async def run_case(session: AsyncSession):
        await record_usage_async(
            session,
            user_id=9,
            event_type=AGENT_QUESTION_EVENT,
            count=19,
            source="assistant",
        )

        assert await ensure_within_quota_async(
            session,
            user_id=9,
            user_role="analyst",
            event_type=AGENT_QUESTION_EVENT,
            increment=1,
        ) == (19, 20)
        with pytest.raises(ValueError, match="daily_quota_exceeded:agent_question:20:19"):
            await ensure_within_quota_async(
                session,
                user_id=9,
                user_role="analyst",
                event_type=AGENT_QUESTION_EVENT,
                increment=2,
            )
        assert await ensure_within_quota_async(
            session,
            user_id=9,
            user_role="admin",
            event_type=AGENT_QUESTION_EVENT,
            increment=100,
        ) == (19, None)

    anyio.run(_with_usage_db, tmp_path, run_case)


def test_async_usage_response_payload_matches_sync_contract(tmp_path):
    async def run_case(session: AsyncSession):
        await record_usage_async(
            session,
            user_id=11,
            event_type=DOCUMENT_PARSE_EVENT,
            count=2,
            source="document_parser",
        )

        payload = await usage_response_payload_async(
            session,
            user_id=11,
            user_role="analyst",
            event_type=DOCUMENT_PARSE_EVENT,
        )

        assert payload["eventType"] == DOCUMENT_PARSE_EVENT
        assert payload["used"] == 2
        assert payload["limit"] == 5
        assert payload["remaining"] == 3
        assert payload["resetAt"]

    anyio.run(_with_usage_db, tmp_path, run_case)
