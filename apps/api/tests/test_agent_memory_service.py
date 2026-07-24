import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import anyio

from services import agent_memory_service as memory


class _Dialect:
    name = "sqlite"


class _Bind:
    dialect = _Dialect()


class _Session:
    bind = _Bind()


def test_context_from_session_id_parses_user_and_normalizes_profile():
    context = memory.context_from_session_id(
        "user-42-assistant-123e4567-e89b-12d3-a456-426614174000"
    )

    assert context is not None
    assert context.user_id == 42
    assert context.profile == "siq_assistant"
    assert context.agent_group == "secondary_market"
    assert context.visibility == "user_private"


def test_context_from_session_id_accepts_canonical_short_session_suffix():
    context = memory.context_from_session_id("user-42-assistant-a5c42649")

    assert context is not None
    assert context.user_id == 42
    assert context.profile == "siq_assistant"
    assert context.agent_group == "secondary_market"


def test_context_from_session_id_marks_ic_profiles_project_shared():
    context = memory.context_from_session_id(
        "user-7-siq_ic_chairman-123e4567-e89b-12d3-a456-426614174000",
        deal_id="deal-alpha",
    )

    assert context is not None
    assert context.agent_group == "primary_market"
    assert context.visibility == "project_shared"


def test_context_from_session_id_rejects_unscoped_primary_market_memory():
    context = memory.context_from_session_id(
        "user-7-siq_ic_chairman-123e4567-e89b-12d3-a456-426614174000"
    )

    assert context is None


def test_context_from_session_id_preserves_normalized_research_identity():
    context = memory.context_from_session_id(
        "user-42-assistant-123e4567-e89b-12d3-a456-426614174000",
        research_identity={
            "market": "US_SEC",
            "company_id": "US:AAPL",
            "filing_id": "US:AAPL:2025-10-K",
            "parse_run_id": "parse-us-aapl",
        },
    )

    assert context is not None
    assert memory.context_research_identity(context) == {
        "market": "US",
        "company_id": "US:AAPL",
        "filing_id": "US:AAPL:2025-10-K",
        "parse_run_id": "parse-us-aapl",
    }
    assert memory.metadata_with_research_identity(context, {"source": "chat"}) == {
        "source": "chat",
        "research_identity": memory.context_research_identity(context),
    }


def test_memory_enabled_skips_sqlite_by_default(monkeypatch):
    monkeypatch.delenv("SIQ_AGENT_MEMORY_ALLOW_SQLITE", raising=False)
    monkeypatch.delenv("SIQ_AGENT_MEMORY_ENABLED", raising=False)

    assert memory.memory_enabled(_Session()) is False


def test_memory_enabled_can_be_disabled(monkeypatch):
    monkeypatch.setenv("SIQ_AGENT_MEMORY_ENABLED", "false")

    assert memory.memory_enabled(SimpleNamespace(bind=SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))) is False


def test_extract_explicit_memory_text_requires_clear_trigger():
    assert memory.extract_explicit_memory_text("请记住：我默认关注现金流和商誉") == "我默认关注现金流和商誉"
    assert memory.extract_explicit_memory_text("普通提问：分析一下上汽集团") is None


def test_classify_explicit_memory_type_detects_corrections_and_project_facts():
    assert (
        memory.classify_explicit_memory_type("请记住：你之前说错了，A 公司不是港股", agent_group="secondary_market")
        == "correction"
    )
    assert (
        memory.classify_explicit_memory_type("请记住：本项目红线是数据合规", agent_group="primary_market")
        == "project_fact"
    )
    assert (
        memory.classify_explicit_memory_type("请记住：我默认关注现金流", agent_group="secondary_market")
        == "user_preference"
    )


def test_build_memory_context_block_renders_traceable_items():
    block = memory.build_memory_context_block(
        [
            {
                "id": 10,
                "visibility": "user_private",
                "memory_type": "user_preference",
                "title": "偏好",
                "content": "默认关注现金流。",
                "source_type": "chat_message",
                "source_id": "5",
                "score": 0.88,
            }
        ]
    )

    assert block is not None
    assert block.startswith("<memory-context>")
    assert "source=chat_message:5" in block
    assert "默认关注现金流" in block


def test_memory_recency_weight_decays_old_memory_and_full_recall_bypasses(monkeypatch):
    monkeypatch.setenv("SIQ_AGENT_MEMORY_TIME_DECAY_HALF_LIFE_DAYS", "30")
    old_time = datetime.now(timezone.utc) - timedelta(days=30)

    decayed = memory.memory_recency_weight(old_time, source_type="chat_message", query="普通问题")
    bypassed = memory.memory_recency_weight(old_time, source_type="chat_message", query="请全量检索所有记忆")
    profile_file = memory.memory_recency_weight(old_time, source_type="profile_file", query="普通问题")

    assert 0.49 <= decayed <= 0.51
    assert bypassed == 1.0
    assert profile_file == 1.0


def test_memory_search_builds_exact_identity_filters_for_milvus_and_pgvector(monkeypatch):
    identity = {
        "market": "HK",
        "company_id": "HK:00700",
        "filing_id": "HK:00700:2025-annual",
        "parse_run_id": "parse-hk-00700",
    }
    context = memory.context_from_session_id(
        "user-42-assistant-123e4567-e89b-12d3-a456-426614174000",
        deal_id="deal-a",
        research_identity=identity,
    )
    assert context is not None

    class EmptyResult:
        def mappings(self):
            return self

        def all(self):
            return []

    class FakeSession:
        bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def __init__(self):
            self.calls = []

        async def execute(self, statement, params):
            self.calls.append((str(statement), dict(params)))
            return EmptyResult()

    async def fake_embed(_query):
        return [0.1, 0.2]

    monkeypatch.setattr(memory, "memory_retrieval_enabled", lambda _session: True)
    monkeypatch.setattr(memory, "_embed_text", fake_embed)
    monkeypatch.setattr(memory, "milvus_enabled", lambda _session: True)
    monkeypatch.setattr(memory, "pgvector_enabled", lambda _session: False)
    milvus_exprs = []
    monkeypatch.setattr(
        memory.agent_memory_milvus,
        "search_records",
        lambda **kwargs: milvus_exprs.append(kwargs["expr"]) or [],
    )
    async def search(session):
        return await memory.search_memory_items(session, context, query="腾讯收入")

    milvus_session = FakeSession()
    anyio.run(search, milvus_session)

    assert len(milvus_exprs) == 1
    assert 'agent_group == "secondary_market"' in milvus_exprs[0]
    assert 'research_company_id == "HK:00700"' in milvus_exprs[0]
    assert 'research_parse_run_id == "parse-hk-00700"' in milvus_exprs[0]

    monkeypatch.setattr(memory, "milvus_enabled", lambda _session: False)
    monkeypatch.setattr(memory, "pgvector_enabled", lambda _session: True)
    pg_session = FakeSession()
    anyio.run(search, pg_session)

    assert len(pg_session.calls) == 2
    for sql, params in pg_session.calls:
        assert "mi.metadata_json->'research_identity'->>'company_id' = :research_company_id" in sql
        assert "mi.metadata_json->'research_identity'->>'parse_run_id' = :research_parse_run_id" in sql
        assert "mi.agent_group = :agent_group" in sql
        assert params["agent_group"] == "secondary_market"
        assert params["research_company_id"] == "HK:00700"
        assert params["research_parse_run_id"] == "parse-hk-00700"


def test_memory_search_fails_closed_for_partial_identity_before_backend_calls(monkeypatch):
    context = memory.context_from_session_id(
        "user-42-assistant-123e4567-e89b-12d3-a456-426614174000",
        research_identity={"market": "HK", "company_id": "HK:00700"},
    )
    assert context is not None
    calls = []
    monkeypatch.setattr(memory, "memory_retrieval_enabled", lambda _session: True)
    monkeypatch.setattr(memory, "_embed_text", lambda _query: calls.append("embed"))

    async def search():
        return await memory.search_memory_items(object(), context, query="腾讯收入")

    result = anyio.run(search)

    assert result == []
    assert calls == []


def test_unscoped_memory_sql_excludes_all_research_scoped_records():
    sql = memory._memory_identity_sql(None)

    for field in memory.RESEARCH_IDENTITY_FIELDS:
        assert f"metadata_json->'research_identity'->>'{field}', '') = ''" in sql


def test_memory_acl_isolates_private_project_and_system_memory_by_market_and_profile():
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE memory_items (
            id TEXT PRIMARY KEY,
            visibility TEXT NOT NULL,
            owner_user_id INTEGER,
            profile TEXT NOT NULL,
            agent_group TEXT NOT NULL,
            deal_id TEXT,
            project_id TEXT
        )
        """
    )
    connection.executemany(
        "INSERT INTO memory_items VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("primary-project", "project_shared", 1, "siq_ic_finance_auditor", "primary_market", "deal-a", None),
            ("secondary-project", "project_shared", 1, "siq_assistant", "secondary_market", "deal-a", None),
            ("primary-system-role", "system_shared", None, "siq_ic_chairman", "primary_market", None, None),
            ("primary-system-other-role", "system_shared", None, "siq_ic_finance_auditor", "primary_market", None, None),
            ("primary-system-shared", "system_shared", None, "siq_ic_shared", "primary_market", None, None),
            ("secondary-system-role", "system_shared", None, "siq_assistant", "secondary_market", None, None),
            ("secondary-system-shared", "system_shared", None, "shared", "shared", None, None),
            ("private-primary", "user_private", 7, "siq_ic_chairman", "primary_market", None, None),
            ("private-secondary", "user_private", 7, "siq_assistant", "secondary_market", None, None),
            ("private-other-user", "user_private", 8, "siq_assistant", "secondary_market", None, None),
        ],
    )

    query = "SELECT id FROM memory_items mi WHERE TRUE " + memory._memory_acl_sql() + " ORDER BY id"
    base_params = {"user_id": 7, "deal_id": "deal-a", "project_id": None}
    secondary_ids = [
        row[0]
        for row in connection.execute(
            query,
            {
                **base_params,
                "agent_group": "secondary_market",
                "profile": "siq_assistant",
                "system_shared_profile": "shared",
            },
        )
    ]
    primary_ids = [
        row[0]
        for row in connection.execute(
            query,
            {
                **base_params,
                "agent_group": "primary_market",
                "profile": "siq_ic_chairman",
                "system_shared_profile": "siq_ic_shared",
            },
        )
    ]

    assert secondary_ids == [
        "private-secondary",
        "secondary-project",
        "secondary-system-role",
        "secondary-system-shared",
    ]
    assert primary_ids == [
        "primary-project",
        "primary-system-role",
        "primary-system-shared",
        "private-primary",
    ]
    connection.close()


def test_memory_acl_casts_nullable_scope_parameters_for_asyncpg():
    sql = memory._memory_acl_sql()

    assert "CAST(:deal_id AS TEXT) IS NOT NULL" in sql
    assert "mi.deal_id = CAST(:deal_id AS TEXT)" in sql
    assert "CAST(:project_id AS TEXT) IS NOT NULL" in sql
    assert "mi.project_id = CAST(:project_id AS TEXT)" in sql


def test_private_memory_acl_excludes_system_and_project_memory():
    sql = memory._memory_acl_sql("user_private")

    assert "mi.visibility = 'user_private'" in sql
    assert "mi.owner_user_id = :user_id" in sql
    assert "mi.profile = :profile" in sql
    assert "system_shared" not in sql
    assert "project_shared" not in sql


def test_memory_acl_requires_deal_and_project_to_match_when_both_are_bound():
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE memory_items (
            id TEXT PRIMARY KEY,
            visibility TEXT NOT NULL,
            owner_user_id INTEGER,
            profile TEXT NOT NULL,
            agent_group TEXT NOT NULL,
            deal_id TEXT,
            project_id TEXT
        )
        """
    )
    connection.executemany(
        "INSERT INTO memory_items VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("exact", "project_shared", None, "siq_ic_chairman", "primary_market", "deal-a", "project-a"),
            ("same-deal-wrong-project", "project_shared", None, "siq_ic_chairman", "primary_market", "deal-a", "project-b"),
            ("same-project-wrong-deal", "project_shared", None, "siq_ic_chairman", "primary_market", "deal-b", "project-a"),
        ],
    )
    query = "SELECT id FROM memory_items mi WHERE TRUE " + memory._memory_acl_sql()
    rows = connection.execute(
        query,
        {
            "user_id": 7,
            "deal_id": "deal-a",
            "project_id": "project-a",
            "agent_group": "primary_market",
            "profile": "siq_ic_chairman",
            "system_shared_profile": "siq_ic_shared",
        },
    ).fetchall()

    assert rows == [("exact",)]
    connection.close()
