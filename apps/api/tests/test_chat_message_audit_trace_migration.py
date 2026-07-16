import asyncio
from types import SimpleNamespace

import anyio
import database
import pytest
from models import ChatMessage
from services.agent_runtime_message_identity import decode_research_identity_snapshot
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from services import agent_chat_runtime as runtime, agent_runtime_attachments


def test_save_message_adds_nullable_audit_trace_column_to_legacy_sqlite(tmp_path, monkeypatch):
    async def run_case():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy-chat.db'}")
        try:
            async with engine.begin() as connection:
                await connection.execute(text("""
                    CREATE TABLE chatmessage (
                        id INTEGER PRIMARY KEY,
                        session_id VARCHAR NOT NULL,
                        role VARCHAR NOT NULL,
                        content VARCHAR NOT NULL,
                        attachments_json TEXT,
                        created_at DATETIME NOT NULL
                    )
                """))
                await connection.execute(text("""
                    INSERT INTO chatmessage (session_id, role, content, attachments_json, created_at)
                    VALUES ('legacy-session', 'assistant', '旧回答', NULL, CURRENT_TIMESTAMP)
                """))

            monkeypatch.setattr(runtime.agent_memory_service, "context_from_session_id", lambda *_args, **_kwargs: None)
            monkeypatch.setattr(runtime, "_CHAT_MESSAGE_ATTACHMENTS_COLUMN_READY", False)
            monkeypatch.setattr(agent_runtime_attachments, "_CHAT_MESSAGE_ATTACHMENTS_COLUMN_READY", False)
            async with AsyncSession(engine) as session:
                await runtime.save_message(
                    session,
                    "assistant",
                    "新回答",
                    "legacy-session",
                    audit_trace_id="aat_1234567890abcdef1234567890abcdef",
                    research_identity={
                        "market": "hk",
                        "company_id": "HK:01398",
                        "filing_id": "HK:01398:2025-annual",
                        "parse_run_id": "parse-hk-01398",
                    },
                )
                rows = (await session.exec(
                    select(ChatMessage).order_by(ChatMessage.id)
                )).all()
                columns = {
                    str(row[1])
                    for row in (await session.exec(text("PRAGMA table_info(chatmessage)"))).all()
                }
            return rows, columns
        finally:
            await engine.dispose()

    rows, columns = anyio.run(run_case)

    assert "audit_trace_id" in columns
    assert "research_identity_json" in columns
    assert [row.content for row in rows] == ["旧回答", "新回答"]
    assert rows[0].audit_trace_id is None
    assert rows[1].audit_trace_id == "aat_1234567890abcdef1234567890abcdef"
    assert rows[0].research_identity_json is None
    assert decode_research_identity_snapshot(rows[1].research_identity_json) == {
        "market": "HK",
        "company_id": "HK:01398",
        "filing_id": "HK:01398:2025-annual",
        "parse_run_id": "parse-hk-01398",
    }


def test_save_message_rejects_invalid_and_user_audit_trace_ids(tmp_path, monkeypatch):
    async def run_case():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'validation.db'}")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(ChatMessage.metadata.create_all)
            monkeypatch.setattr(runtime.agent_memory_service, "context_from_session_id", lambda *_args, **_kwargs: None)
            async with AsyncSession(engine) as session:
                await runtime.save_message(session, "assistant", "bad", "s", audit_trace_id="not-a-trace")
                await runtime.save_message(
                    session,
                    "user",
                    "question",
                    "s",
                    audit_trace_id="aat_1234567890abcdef1234567890abcdef",
                )
                return (await session.exec(select(ChatMessage).order_by(ChatMessage.id))).all()
        finally:
            await engine.dispose()

    rows = anyio.run(run_case)

    assert [row.audit_trace_id for row in rows] == [None, None]


def test_startup_migration_adds_audit_trace_column_without_rebuilding_legacy_table(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'startup-legacy.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(text("""
                CREATE TABLE chatmessage (
                    id INTEGER PRIMARY KEY,
                    session_id VARCHAR NOT NULL,
                    role VARCHAR NOT NULL,
                    content VARCHAR NOT NULL,
                    attachments_json TEXT,
                    created_at DATETIME NOT NULL
                )
            """))
            connection.execute(text("""
                INSERT INTO chatmessage (session_id, role, content, attachments_json, created_at)
                VALUES ('legacy-session', 'assistant', '保留旧回答', NULL, CURRENT_TIMESTAMP)
            """))
        monkeypatch.setattr(database, "engine", engine)

        database._ensure_chat_message_columns()

        columns = {column["name"] for column in inspect(engine).get_columns("chatmessage")}
        assert "audit_trace_id" in columns
        assert "research_identity_json" in columns
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT content, research_identity_json FROM chatmessage")
            ).one()
            assert row == ("保留旧回答", None)
    finally:
        engine.dispose()


def test_runtime_migration_uses_idempotent_postgresql_identity_column_ddl(monkeypatch):
    class FakeAsyncSession:
        def __init__(self):
            self.statements = []
            self.commits = 0
            self.rollbacks = 0

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        async def exec(self, statement):
            self.statements.append(str(statement))
            if "information_schema.columns" in str(statement):
                return SimpleNamespace(all=lambda: [])
            return SimpleNamespace(all=lambda: [])

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    async def run_case():
        session = FakeAsyncSession()
        monkeypatch.setattr(agent_runtime_attachments, "_CHAT_MESSAGE_ATTACHMENTS_COLUMN_READY", False)
        await agent_runtime_attachments._ensure_chatmessage_attachments_column(session)
        return session

    session = anyio.run(run_case)

    assert (
        "ALTER TABLE chatmessage ADD COLUMN IF NOT EXISTS research_identity_json TEXT"
        in session.statements
    )
    assert session.commits == 1
    assert session.rollbacks == 0


def test_runtime_migration_skips_postgresql_ddl_when_columns_exist(monkeypatch):
    columns = [("attachments_json",), ("audit_trace_id",), ("research_identity_json",)]

    class FakeAsyncSession:
        def __init__(self):
            self.statements = []
            self.commits = 0

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        async def exec(self, statement):
            value = str(statement)
            self.statements.append(value)
            return SimpleNamespace(all=lambda: columns if "information_schema.columns" in value else [])

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            raise AssertionError("rollback should not be called")

    async def run_case():
        session = FakeAsyncSession()
        monkeypatch.setattr(agent_runtime_attachments, "_CHAT_MESSAGE_ATTACHMENTS_COLUMN_READY", False)
        monkeypatch.setattr(agent_runtime_attachments, "_CHAT_MESSAGE_ATTACHMENTS_COLUMN_LOCK", None)
        monkeypatch.setattr(agent_runtime_attachments, "_CHAT_MESSAGE_ATTACHMENTS_COLUMN_LOCK_LOOP", None)
        await agent_runtime_attachments._ensure_chatmessage_attachments_column(session)
        return session

    session = anyio.run(run_case)

    assert len(session.statements) == 1
    assert "information_schema.columns" in session.statements[0]
    assert not any(statement.startswith("ALTER TABLE") for statement in session.statements)
    assert session.commits == 0


def test_runtime_wrapper_does_not_reset_attachment_owner_readiness(monkeypatch):
    class NoDatabaseSession:
        def get_bind(self):
            raise AssertionError("ready owner must not touch the database")

    async def run_case():
        monkeypatch.setattr(runtime, "_CHAT_MESSAGE_ATTACHMENTS_COLUMN_READY", False)
        monkeypatch.setattr(agent_runtime_attachments, "_CHAT_MESSAGE_ATTACHMENTS_COLUMN_READY", True)
        await runtime._ensure_chatmessage_attachments_column(NoDatabaseSession())

    anyio.run(run_case)

    assert runtime._CHAT_MESSAGE_ATTACHMENTS_COLUMN_READY is True
    assert agent_runtime_attachments._CHAT_MESSAGE_ATTACHMENTS_COLUMN_READY is True


@pytest.mark.parametrize("dialect", ["sqlite", "postgresql"])
def test_runtime_migration_is_single_flight_for_concurrent_sessions(dialect, monkeypatch):
    state = SimpleNamespace(ddl=[], probes=0, commits=0, rollbacks=0)

    class FakeAsyncSession:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name=dialect))

        async def exec(self, statement):
            value = str(statement)
            await anyio.sleep(0)
            if value.startswith("PRAGMA"):
                state.probes += 1
                return SimpleNamespace(all=lambda: [])
            if "information_schema.columns" in value:
                state.probes += 1
                return SimpleNamespace(all=lambda: [])
            if value.startswith("ALTER TABLE"):
                state.ddl.append(value)
            return SimpleNamespace(all=lambda: [])

        async def commit(self):
            state.commits += 1

        async def rollback(self):
            state.rollbacks += 1

    async def run_case():
        monkeypatch.setattr(agent_runtime_attachments, "_CHAT_MESSAGE_ATTACHMENTS_COLUMN_READY", False)
        monkeypatch.setattr(agent_runtime_attachments, "_CHAT_MESSAGE_ATTACHMENTS_COLUMN_LOCK", None)
        monkeypatch.setattr(agent_runtime_attachments, "_CHAT_MESSAGE_ATTACHMENTS_COLUMN_LOCK_LOOP", None)
        await asyncio.gather(
            *(agent_runtime_attachments._ensure_chatmessage_attachments_column(FakeAsyncSession()) for _ in range(7))
        )

    anyio.run(run_case)

    assert state.probes == 1
    assert len(state.ddl) == 3
    assert len(set(state.ddl)) == 3
    assert state.commits == 1
    assert state.rollbacks == 0
    assert agent_runtime_attachments._CHAT_MESSAGE_ATTACHMENTS_COLUMN_READY is True


@pytest.mark.parametrize("dialect", ["sqlite", "postgresql"])
def test_runtime_migration_can_retry_after_failure(dialect, monkeypatch):
    state = SimpleNamespace(fail_next_ddl=True, ddl=0, commits=0, rollbacks=0)

    class FakeAsyncSession:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name=dialect))

        async def exec(self, statement):
            value = str(statement)
            if value.startswith("PRAGMA") or "information_schema.columns" in value:
                return SimpleNamespace(all=lambda: [])
            if value.startswith("ALTER TABLE"):
                state.ddl += 1
                if state.fail_next_ddl:
                    state.fail_next_ddl = False
                    raise RuntimeError("simulated ddl failure")
            return SimpleNamespace(all=lambda: [])

        async def commit(self):
            state.commits += 1

        async def rollback(self):
            state.rollbacks += 1

    async def run_case():
        monkeypatch.setattr(agent_runtime_attachments, "_CHAT_MESSAGE_ATTACHMENTS_COLUMN_READY", False)
        monkeypatch.setattr(agent_runtime_attachments, "_CHAT_MESSAGE_ATTACHMENTS_COLUMN_LOCK", None)
        monkeypatch.setattr(agent_runtime_attachments, "_CHAT_MESSAGE_ATTACHMENTS_COLUMN_LOCK_LOOP", None)
        with pytest.raises(RuntimeError, match="simulated ddl failure"):
            await agent_runtime_attachments._ensure_chatmessage_attachments_column(FakeAsyncSession())
        assert agent_runtime_attachments._CHAT_MESSAGE_ATTACHMENTS_COLUMN_READY is False
        assert not agent_runtime_attachments._CHAT_MESSAGE_ATTACHMENTS_COLUMN_LOCK.locked()
        await agent_runtime_attachments._ensure_chatmessage_attachments_column(FakeAsyncSession())

    anyio.run(run_case)

    assert state.ddl == 4
    assert state.commits == 1
    assert state.rollbacks == 1
    assert agent_runtime_attachments._CHAT_MESSAGE_ATTACHMENTS_COLUMN_READY is True


def test_startup_migration_adds_query_indexes_to_legacy_tables(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'startup-indexes.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(text("""
                CREATE TABLE chatmessage (
                    id INTEGER PRIMARY KEY,
                    session_id VARCHAR NOT NULL,
                    role VARCHAR NOT NULL,
                    content VARCHAR NOT NULL,
                    created_at DATETIME NOT NULL
                )
            """))
            connection.execute(text("""
                CREATE TABLE usage_events (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    event_type VARCHAR(40) NOT NULL,
                    event_date VARCHAR(10) NOT NULL,
                    count INTEGER NOT NULL
                )
            """))
        monkeypatch.setattr(database, "engine", engine)

        database._ensure_app_indexes()
        database._ensure_app_indexes()

        inspector = inspect(engine)
        chat_indexes = {item["name"]: item["column_names"] for item in inspector.get_indexes("chatmessage")}
        usage_indexes = {item["name"]: item["column_names"] for item in inspector.get_indexes("usage_events")}
        assert chat_indexes["idx_chatmessage_session_created_at"] == ["session_id", "created_at"]
        assert usage_indexes["idx_usage_events_user_type_date"] == [
            "user_id",
            "event_type",
            "event_date",
        ]
    finally:
        engine.dispose()
