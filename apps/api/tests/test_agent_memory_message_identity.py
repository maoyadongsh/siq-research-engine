import json
from types import SimpleNamespace

import anyio
import database

from services import agent_memory_service as memory


class _Result:
    def first(self):
        return (42,)


class _AsyncSession:
    bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    def __init__(self):
        self.calls = []

    async def execute(self, statement, params):
        self.calls.append((str(statement), dict(params)))
        return _Result()

    async def commit(self):
        return None


def test_agent_memory_message_persists_immutable_research_identity_snapshot(monkeypatch):
    monkeypatch.setenv("SIQ_AGENT_MEMORY_ENABLED", "true")
    monkeypatch.setenv("SIQ_AGENT_MEMORY_WRITE_ENABLED", "true")
    context = memory.context_from_session_id(
        "user-42-assistant-a5c42649",
        research_identity={
            "market": "US_SEC",
            "company_id": "US:0000320193",
            "filing_id": "US:AAPL:2025-10-K",
            "parse_run_id": "parse-us-aapl",
        },
    )
    assert context is not None
    session = _AsyncSession()

    async def record():
        return await memory.record_message(session, context, role="user", content="Revenue?")

    message_id = anyio.run(record)

    assert message_id == 42
    message_sql, message_params = next(
        (sql, params) for sql, params in session.calls if "INSERT INTO agent_memory.messages" in sql
    )
    assert "research_identity_json" in message_sql
    assert json.loads(message_params["research_identity_json"]) == {
        "market": "US",
        "company_id": "US:0000320193",
        "filing_id": "US:AAPL:2025-10-K",
        "parse_run_id": "parse-us-aapl",
    }


def test_agent_memory_message_writes_null_for_missing_or_partial_identity(monkeypatch):
    monkeypatch.setenv("SIQ_AGENT_MEMORY_ENABLED", "true")
    monkeypatch.setenv("SIQ_AGENT_MEMORY_WRITE_ENABLED", "true")

    async def record(research_identity):
        context = memory.context_from_session_id(
            "user-42-assistant-a5c42649",
            research_identity=research_identity,
        )
        assert context is not None
        session = _AsyncSession()
        await memory.record_message(session, context, role="user", content="Revenue?")
        return next(
            params
            for sql, params in session.calls
            if "INSERT INTO agent_memory.messages" in sql
        )

    for research_identity in (
        None,
        {"market": "HK", "company_id": "HK:00700"},
    ):
        message_params = anyio.run(record, research_identity)
        assert message_params["research_identity_json"] is None


class _Connection:
    def __init__(self):
        self.statements = []

    def execute(self, statement):
        self.statements.append(str(statement))


class _Begin:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, *_args):
        return False


class _Engine:
    dialect = SimpleNamespace(name="postgresql")

    def __init__(self):
        self.connection = _Connection()

    def begin(self):
        return _Begin(self.connection)


def test_agent_memory_schema_migrates_existing_message_identity_column(monkeypatch):
    engine = _Engine()
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setenv("SIQ_AGENT_MEMORY_VECTOR_BACKEND", "milvus")
    monkeypatch.setenv("SIQ_AGENT_MEMORY_PGVECTOR_ENABLED", "false")

    database._ensure_agent_memory_schema()

    statements = engine.connection.statements
    create_messages = next(statement for statement in statements if "CREATE TABLE IF NOT EXISTS agent_memory.messages" in statement)
    assert "research_identity_json JSONB" in create_messages
    assert any(
        "ALTER TABLE agent_memory.messages ADD COLUMN IF NOT EXISTS research_identity_json JSONB" in statement
        for statement in statements
    )
