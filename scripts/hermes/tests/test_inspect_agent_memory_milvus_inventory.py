from __future__ import annotations

import json
from pathlib import Path

import pytest


def load_module():
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "inspect_agent_memory_milvus_inventory.py"
    spec = importlib.util.spec_from_file_location("inspect_agent_memory_milvus_inventory", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.mutations = []

    def has_collection(self, name):
        return True

    def describe_collection(self, name):
        field_names = {
            "id",
            "vector",
            "tenant_id",
            "visibility",
            "owner_user_id",
            "profile",
            "agent_group",
            "deal_id",
            "project_id",
            "memory_type",
            "source_kind",
            "source_id",
            "source_path",
            "content_hash",
            "title",
            "content",
            "metadata_json",
            "updated_at_ts",
        }
        return {
            "fields": [
                {
                    "name": field,
                    **({"data_type": "VARCHAR", "is_primary": True} if field == "id" else {}),
                    **({"type": 101, "params": {"dim": 1024}} if field == "vector" else {}),
                }
                for field in sorted(field_names)
            ]
        }

    def get_collection_stats(self, collection_name):
        return {"row_count": len(self.rows)}

    def query(self, **kwargs):
        assert "content" not in kwargs["output_fields"]
        assert "vector" not in kwargs["output_fields"]
        return list(self.rows)

    def list_indexes(self, collection_name):
        return ["vector"]

    def describe_index(self, collection_name, index_name):
        return {"index_type": "HNSW", "metric_type": "COSINE"}

    def list_aliases(self, collection_name):
        return {"aliases": [], "collection_name": collection_name, "db_name": "default"}

    def describe_alias(self, alias):
        raise KeyError(alias)

    def __getattr__(self, name):
        if name in {"upsert", "delete", "drop_collection", "create_collection", "flush", "alter_alias"}:
            return lambda *args, **kwargs: self.mutations.append(name)
        raise AttributeError(name)


class FakeIterator:
    def __init__(self, rows):
        self.batches = [rows[index : index + 4096] for index in range(0, len(rows), 4096)]
        self.closed = False

    def next(self):
        return self.batches.pop(0) if self.batches else []

    def close(self):
        self.closed = True


class IteratorClient(FakeClient):
    def query(self, **kwargs):
        raise AssertionError("large inventories must use query_iterator")

    def query_iterator(self, **kwargs):
        assert kwargs["batch_size"] <= 4096
        assert kwargs["limit"] == len(self.rows)
        self.iterator = FakeIterator(self.rows)
        return self.iterator


def profile_rows(count=3):
    return [
        {
            "id": f"profile_file:{index}",
            "content_hash": f"hash-{index}",
            "source_kind": "profile_file",
            "memory_type": "profile_file",
            "visibility": "system_shared",
            "metadata_json": json.dumps({"schema_version": "siq_agent_profile_chunk_v1"}),
        }
        for index in range(count)
    ]


def test_profile_contract_produces_observed_unscoped_snapshot():
    module = load_module()
    client = FakeClient(profile_rows())

    report = module.collect_inventory(client, "siq_agent_memory")

    assert report["provenance"]["contract_match"] is True
    assert report["identity"] == {
        "observation_status": "observed",
        "observation_reason": "all records match the structured profile_file/system_shared seed contract",
        "research_scoped_count": 0,
        "complete_count": 0,
        "partial_count": 0,
        "unscoped_count": 3,
        "missing_by_field": {"market": 0, "company_id": 0, "filing_id": 0, "parse_run_id": 0},
    }
    assert report["collection"]["vector_dimension"] == 1024
    assert report["collection"]["metric_type"] == "COSINE"
    assert report["collection"]["index_type"] == "HNSW"
    assert client.mutations == []


def test_unexpected_row_keeps_identity_unknown_and_fail_closed():
    module = load_module()
    rows = profile_rows(2)
    rows.append(
        {
            "id": "memory:unexpected",
            "content_hash": "hash-unexpected",
            "source_kind": "memory_item",
            "memory_type": "note",
            "visibility": "user_private",
            "metadata_json": "{}",
        }
    )

    report = module.collect_inventory(FakeClient(rows), "siq_agent_memory")

    assert report["provenance"]["contract_match"] is False
    assert report["identity"]["observation_status"] == "unavailable"
    assert report["identity"]["unscoped_count"] is None


def test_large_inventory_uses_iterator_beyond_milvus_query_window():
    module = load_module()
    client = IteratorClient(profile_rows(16385))

    report = module.collect_inventory(client, "siq_agent_memory")

    assert report["collection"]["entity_count"] == 16385
    assert report["provenance"]["contract_match"] is True
    assert client.iterator.closed is True


def test_missing_stats_without_iterator_fails_closed_instead_of_reporting_one_row():
    module = load_module()

    class NoStatsClient(FakeClient):
        def get_collection_stats(self, collection_name):
            raise RuntimeError("stats unavailable")

    with pytest.raises(RuntimeError, match="stats are unavailable"):
        module.collect_inventory(NoStatsClient(profile_rows(3)), "siq_agent_memory")


def test_main_require_contract_returns_failure_without_mutation(tmp_path, monkeypatch):
    module = load_module()
    client = FakeClient(profile_rows())
    output = tmp_path / "inventory.json"
    monkeypatch.setattr(module.agent_memory_milvus, "_client", lambda: client)

    assert module.main(["--output", str(output), "--require-profile-contract"], client=client) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["writes_performed"] is False
    assert client.mutations == []


def test_main_require_contract_rejects_unexpected_rows(tmp_path):
    module = load_module()
    output = tmp_path / "inventory.json"
    client = FakeClient(profile_rows(1) + [{
        "id": "memory:unexpected",
        "content_hash": "hash-unexpected",
        "source_kind": "memory_item",
        "memory_type": "note",
        "visibility": "user_private",
        "metadata_json": "{}",
    }])

    assert module.main(["--output", str(output), "--require-profile-contract"], client=client) == 1
    assert client.mutations == []


def test_alias_inventory_resolves_physical_collection_with_describe_alias():
    module = load_module()

    class AliasClient(FakeClient):
        def list_aliases(self, collection_name):
            return {"aliases": ["siq_agent_memory_active"], "collection_name": "siq_agent_memory_active"}

        def describe_alias(self, alias):
            assert alias == "siq_agent_memory_active"
            return {"alias_name": alias, "collection_name": "siq_agent_memory__v2"}

    aliases, error = module._aliases(AliasClient(profile_rows()), "siq_agent_memory")

    assert error is None
    assert aliases == [{"name": "siq_agent_memory_active", "collection": "siq_agent_memory__v2"}]


def test_requested_alias_probe_resolves_alias_after_source_switch():
    module = load_module()

    class SwitchedAliasClient(FakeClient):
        def list_aliases(self, collection_name):
            return {"aliases": [], "collection_name": collection_name}

        def describe_alias(self, alias):
            return {"alias_name": alias, "collection_name": "siq_agent_memory__v2"}

    aliases, error = module._aliases(
        SwitchedAliasClient(profile_rows()),
        "siq_agent_memory",
        ["siq_agent_memory_active"],
    )

    assert error is None
    assert aliases == [{"name": "siq_agent_memory_active", "collection": "siq_agent_memory__v2"}]
