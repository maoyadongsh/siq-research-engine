from __future__ import annotations

from types import SimpleNamespace

import pytest

from services import agent_memory_milvus


class _FakeDataType:
    VARCHAR = "VARCHAR"
    FLOAT_VECTOR = "FLOAT_VECTOR"
    INT64 = "INT64"


class _FakeSchema:
    def __init__(self):
        self.fields: list[dict[str, object]] = []

    def add_field(self, **kwargs):
        self.fields.append(kwargs)


class _FakeIndexParams:
    def __init__(self):
        self.indexes: list[dict[str, object]] = []

    def add_index(self, **kwargs):
        self.indexes.append(kwargs)


class _FakeMilvusClientFactory:
    @staticmethod
    def create_schema(auto_id=False, enable_dynamic_field=False):
        return _FakeSchema()

    @staticmethod
    def prepare_index_params():
        return _FakeIndexParams()


class _FakeMilvusClient:
    def __init__(self, *, existing_fields: set[str]):
        self.existing_fields = existing_fields
        self.dropped: list[str] = []
        self.created: list[dict[str, object]] = []
        self.loaded: list[str] = []

    def has_collection(self, name: str) -> bool:
        return True

    def describe_collection(self, name: str):
        return {"fields": [{"name": field} for field in sorted(self.existing_fields)]}

    def drop_collection(self, name: str) -> None:
        self.dropped.append(name)

    def create_collection(self, **kwargs) -> None:
        self.created.append(kwargs)

    def load_collection(self, name: str) -> None:
        self.loaded.append(name)


@pytest.fixture
def fake_pymilvus(monkeypatch):
    monkeypatch.setitem(
        __import__("sys").modules,
        "pymilvus",
        SimpleNamespace(DataType=_FakeDataType, MilvusClient=_FakeMilvusClientFactory),
    )


def test_agent_memory_schema_mismatch_refuses_drop_by_default(monkeypatch, fake_pymilvus):
    client = _FakeMilvusClient(existing_fields={"id", agent_memory_milvus.VECTOR_FIELD})
    monkeypatch.setattr(agent_memory_milvus, "_client", lambda: client)
    monkeypatch.setenv("SIQ_AGENT_MEMORY_MILVUS_COLLECTION", "siq_agent_memory_active")
    monkeypatch.delenv("SIQ_AGENT_MEMORY_MILVUS_RECREATE_ON_SCHEMA_MISMATCH", raising=False)
    monkeypatch.delenv("SIQ_AGENT_MEMORY_MILVUS_ALLOW_DESTRUCTIVE_SCHEMA_RECREATE", raising=False)

    with pytest.raises(RuntimeError, match="refusing to drop"):
        agent_memory_milvus.ensure_collection()

    assert client.dropped == []
    assert client.created == []


def test_agent_memory_schema_mismatch_requires_destructive_recreate_opt_in(monkeypatch, fake_pymilvus):
    client = _FakeMilvusClient(existing_fields={"id", agent_memory_milvus.VECTOR_FIELD})
    monkeypatch.setattr(agent_memory_milvus, "_client", lambda: client)
    monkeypatch.setenv("SIQ_AGENT_MEMORY_MILVUS_COLLECTION", "siq_agent_memory_active")
    monkeypatch.setenv("SIQ_AGENT_MEMORY_MILVUS_RECREATE_ON_SCHEMA_MISMATCH", "true")
    monkeypatch.delenv("SIQ_AGENT_MEMORY_MILVUS_ALLOW_DESTRUCTIVE_SCHEMA_RECREATE", raising=False)

    with pytest.raises(RuntimeError, match="refusing to drop"):
        agent_memory_milvus.ensure_collection()

    assert client.dropped == []
    assert client.created == []


def test_agent_memory_v1_collection_without_identity_fields_fails_closed(monkeypatch, fake_pymilvus):
    v1_fields = agent_memory_milvus.REQUIRED_FIELDS - set(agent_memory_milvus.RESEARCH_IDENTITY_FIELDS)
    client = _FakeMilvusClient(existing_fields=v1_fields)
    monkeypatch.setattr(agent_memory_milvus, "_client", lambda: client)
    monkeypatch.delenv("SIQ_AGENT_MEMORY_MILVUS_RECREATE_ON_SCHEMA_MISMATCH", raising=False)
    monkeypatch.delenv("SIQ_AGENT_MEMORY_MILVUS_ALLOW_DESTRUCTIVE_SCHEMA_RECREATE", raising=False)

    with pytest.raises(RuntimeError, match="research_company_id"):
        agent_memory_milvus.ensure_collection()

    assert client.dropped == []
    assert client.created == []


def test_agent_memory_schema_preflight_returns_versioned_migration_report(monkeypatch):
    v1_fields = agent_memory_milvus.REQUIRED_FIELDS - set(agent_memory_milvus.RESEARCH_IDENTITY_FIELDS)
    client = _FakeMilvusClient(existing_fields=v1_fields)
    monkeypatch.delenv("SIQ_AGENT_MEMORY_MILVUS_RECREATE_ON_SCHEMA_MISMATCH", raising=False)
    monkeypatch.delenv("SIQ_AGENT_MEMORY_MILVUS_ALLOW_DESTRUCTIVE_SCHEMA_RECREATE", raising=False)

    report = agent_memory_milvus.collection_schema_preflight(client=client, name="siq_agent_memory_v1")

    assert report["schema_version"] == "siq_agent_memory_milvus_v2"
    assert report["collection_name"] == "siq_agent_memory_v1"
    assert report["compatible"] is False
    assert report["migration_required"] is True
    assert report["migration_action"] == "create_versioned_collection_and_reindex"
    assert set(report["missing_fields"]) == set(agent_memory_milvus.RESEARCH_IDENTITY_FIELDS)
    assert report["destructive_recreate_enabled"] is False
    assert client.dropped == []


def test_agent_memory_milvus_acl_expr_pins_complete_research_identity():
    identity = {
        "market": "HK",
        "company_id": "HK:00700",
        "filing_id": "HK:00700:2025-annual",
        "parse_run_id": "parse-hk-00700",
    }

    expr = agent_memory_milvus.acl_expr(
        tenant_id="tenant-a",
        user_id=7,
        profile="siq_assistant",
        research_identity=identity,
    )

    assert 'research_market == "HK"' in expr
    assert 'research_company_id == "HK:00700"' in expr
    assert 'research_filing_id == "HK:00700:2025-annual"' in expr
    assert 'research_parse_run_id == "parse-hk-00700"' in expr
    with pytest.raises(ValueError, match="complete ResearchIdentity"):
        agent_memory_milvus.acl_expr(
            tenant_id="tenant-a",
            user_id=7,
            research_identity={"market": "HK"},
        )


def test_agent_memory_milvus_unscoped_acl_excludes_research_scoped_records():
    expr = agent_memory_milvus.acl_expr(
        tenant_id="tenant-a",
        user_id=7,
        profile="siq_assistant",
    )

    for field in agent_memory_milvus.RESEARCH_IDENTITY_FIELDS:
        assert f'{field} == ""' in expr


def test_agent_memory_milvus_project_shared_requires_same_agent_group():
    expr = agent_memory_milvus.acl_expr(
        tenant_id="tenant-a",
        user_id=7,
        deal_id="deal-a",
        profile="siq_assistant",
        agent_group="secondary_market",
    )

    assert 'visibility == "system_shared"' in expr
    assert 'agent_group == "secondary_market"' in expr
    assert 'profile == "siq_assistant"' in expr
    assert 'profile == "shared"' in expr
    assert (
        '(visibility == "project_shared" and agent_group == "secondary_market" '
        'and (deal_id == "deal-a"))'
    ) in expr
    assert 'agent_group == "primary_market"' not in expr


def test_agent_memory_milvus_project_shared_fails_closed_without_agent_group():
    expr = agent_memory_milvus.acl_expr(
        tenant_id="tenant-a",
        user_id=7,
        deal_id="deal-a",
        profile="siq_assistant",
    )

    assert 'visibility == "system_shared"' not in expr
    assert 'visibility == "project_shared"' not in expr


def test_agent_memory_milvus_private_scope_excludes_system_and_project_records():
    expr = agent_memory_milvus.acl_expr(
        tenant_id="tenant-a",
        user_id=7,
        deal_id="deal-a",
        profile="siq_assistant",
        agent_group="secondary_market",
        visibility_scope="user_private",
    )

    assert 'visibility == "user_private"' in expr
    assert 'owner_user_id == "7"' in expr
    assert 'visibility == "system_shared"' not in expr
    assert 'visibility == "project_shared"' not in expr


def test_agent_memory_milvus_primary_system_memory_allows_only_role_and_ic_shared():
    expr = agent_memory_milvus.acl_expr(
        tenant_id="tenant-a",
        user_id=7,
        deal_id="deal-a",
        profile="siq_ic_chairman",
        agent_group="primary_market",
    )

    assert 'agent_group == "primary_market"' in expr
    assert 'profile == "siq_ic_chairman"' in expr
    assert 'profile == "siq_ic_shared"' in expr
    assert 'agent_group == "secondary_market"' not in expr
    assert 'profile == "shared"' not in expr


def test_agent_memory_milvus_requires_deal_and_project_to_match_together():
    expr = agent_memory_milvus.acl_expr(
        tenant_id="tenant-a",
        user_id=7,
        deal_id="deal-a",
        project_id="project-a",
        profile="siq_ic_chairman",
        agent_group="primary_market",
    )

    assert '(deal_id == "deal-a" and project_id == "project-a")' in expr
    assert 'deal_id == "deal-a" or project_id == "project-a"' not in expr


def test_agent_memory_milvus_payload_carries_identity_scalar_fields():
    payload = agent_memory_milvus._record_payload(
        agent_memory_milvus.AgentMemoryVectorRecord(
            id="memory:1",
            vector=[0.1, 0.2],
            research_market="JP",
            research_company_id="JP:7203",
            research_filing_id="JP:7203:2025-annual",
            research_parse_run_id="parse-jp-7203",
        )
    )

    assert payload["research_market"] == "JP"
    assert payload["research_company_id"] == "JP:7203"
    assert payload["research_filing_id"] == "JP:7203:2025-annual"
    assert payload["research_parse_run_id"] == "parse-jp-7203"


def test_agent_memory_schema_mismatch_recreate_is_explicitly_destructive(monkeypatch, fake_pymilvus):
    client = _FakeMilvusClient(existing_fields={"id", agent_memory_milvus.VECTOR_FIELD})
    monkeypatch.setattr(agent_memory_milvus, "_client", lambda: client)
    monkeypatch.setenv("SIQ_AGENT_MEMORY_MILVUS_COLLECTION", "siq_agent_memory_active")
    monkeypatch.setenv("SIQ_AGENT_MEMORY_MILVUS_RECREATE_ON_SCHEMA_MISMATCH", "true")
    monkeypatch.setenv("SIQ_AGENT_MEMORY_MILVUS_ALLOW_DESTRUCTIVE_SCHEMA_RECREATE", "true")

    result = agent_memory_milvus.ensure_collection()

    assert result is client
    assert client.dropped == ["siq_agent_memory_active"]
    assert client.created
    assert client.created[0]["collection_name"] == "siq_agent_memory_active"
    assert client.loaded == ["siq_agent_memory_active"]


@pytest.mark.parametrize(
    "collection",
    ["siq_agent_memory", "siq_agent_memory__v2", "siq_documents", "ic_master_coordinator"],
)
def test_runtime_memory_collection_rejects_non_allowlisted_physical_or_knowledge_names(
    monkeypatch,
    collection,
):
    monkeypatch.setenv("SIQ_AGENT_MEMORY_MILVUS_COLLECTION", collection)

    with pytest.raises(RuntimeError, match="not allowlisted"):
        agent_memory_milvus.collection_name()


def test_create_versioned_collection_rejects_invalid_vector_dimensions(monkeypatch, fake_pymilvus):
    client = _FakeMilvusClient(existing_fields=set())

    with pytest.raises(ValueError, match="vector dimension"):
        agent_memory_milvus.create_versioned_collection(
            client=client,
            name="target",
            dimension=0,
            require_absent=False,
        )

    with pytest.raises(ValueError, match="vector dimension"):
        agent_memory_milvus.create_versioned_collection(
            client=client,
            name="target",
            dimension=16385,
            require_absent=False,
        )
