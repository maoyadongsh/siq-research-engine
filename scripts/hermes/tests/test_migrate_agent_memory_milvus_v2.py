from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def load_module():
    script_root = Path(__file__).resolve().parents[1]
    if str(script_root) not in sys.path:
        sys.path.insert(0, str(script_root))
    path = script_root / "migrate_agent_memory_milvus_v2.py"
    spec = importlib.util.spec_from_file_location("migrate_agent_memory_milvus_v2", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def source_row(index: int) -> dict:
    return {
        "id": f"profile_file:{index}",
        "vector": [float(index), 0.5],
        "tenant_id": "default",
        "visibility": "system_shared",
        "owner_user_id": "",
        "profile": "siq_assistant",
        "agent_group": "secondary_market",
        "deal_id": "",
        "project_id": "",
        "memory_type": "profile_file",
        "source_kind": "profile_file",
        "source_id": f"profile_file:{index}",
        "source_path": "agents/hermes/profiles/siq_assistant/README.md",
        "content_hash": f"hash-{index}",
        "title": f"title-{index}",
        "content": f"content-{index}",
        "metadata_json": json.dumps({"schema_version": "siq_agent_profile_chunk_v1"}),
        "updated_at_ts": index,
    }


class FakeClient:
    def __init__(self, rows):
        self.collections = {"siq_agent_memory": list(rows)}
        self.fields = {
            "siq_agent_memory": {
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
        }
        self.aliases = {}
        self.calls = []

    def has_collection(self, name):
        return name in self.collections

    def describe_collection(self, name):
        fields = []
        for field in sorted(self.fields[name]):
            item = {"name": field}
            if field == "id":
                item["is_primary"] = True
            if field == "vector":
                item["params"] = {"dim": 1024}
            fields.append(item)
        return {"fields": fields}

    def get_collection_stats(self, collection_name):
        return {"row_count": len(self.collections[collection_name])}

    def query(self, collection_name, output_fields, **kwargs):
        return [{field: row.get(field) for field in output_fields} for row in self.collections[collection_name]]

    def list_indexes(self, collection_name):
        return {"index_names": ["vector"]}

    def describe_index(self, collection_name, index_name):
        return {"index_type": "HNSW", "metric_type": "COSINE"}

    def list_aliases(self, collection_name):
        return {
            "aliases": [alias for alias, target in self.aliases.items() if target == collection_name],
            "collection_name": collection_name,
        }

    def create_alias(self, collection_name, alias):
        self.calls.append("create_alias")
        self.aliases[alias] = collection_name

    def alter_alias(self, collection_name, alias):
        self.calls.append("alter_alias")
        self.aliases[alias] = collection_name

    def upsert(self, collection_name, data):
        self.calls.append("upsert")
        self.collections[collection_name].extend(dict(row) for row in data)

    def flush(self, collection_name):
        self.calls.append("flush")


def make_snapshot(module, client):
    return module.inventory.collect_inventory(client, "siq_agent_memory")


def install_create_stub(module, monkeypatch, client):
    def create(*, client, name, **kwargs):
        client.calls.append("create_collection")
        client.collections[name] = []
        client.fields[name] = set(module.agent_memory_milvus.REQUIRED_FIELDS)

    monkeypatch.setattr(module.agent_memory_milvus, "create_versioned_collection", create)


def test_dry_run_never_contacts_milvus():
    module = load_module()
    client = FakeClient([source_row(1), source_row(2)])
    snapshot = make_snapshot(module, client)
    before = list(client.calls)

    report = module.execute_migration(
        snapshot,
        source="siq_agent_memory",
        target="siq_agent_memory__v2",
        alias="siq_agent_memory_active",
        apply=False,
        switch_alias=False,
        resume_existing_target=False,
        batch_size=2,
        client=lambda: (_ for _ in ()).throw(AssertionError("must not contact Milvus")),
    )

    assert report["passed"] is True
    assert report["dry_run"] is True
    assert report["writes_performed"] == []
    assert client.calls == before


def test_apply_bootstraps_source_alias_copies_and_verifies_without_switch(monkeypatch):
    module = load_module()
    client = FakeClient([source_row(1), source_row(2)])
    snapshot = make_snapshot(module, client)
    install_create_stub(module, monkeypatch, client)

    report = module.execute_migration(
        snapshot,
        source="siq_agent_memory",
        target="siq_agent_memory__v2",
        alias="siq_agent_memory_active",
        apply=True,
        switch_alias=False,
        resume_existing_target=False,
        batch_size=1,
        client=client,
    )

    assert report["passed"] is True
    assert report["target_verification"]["passed"] is True
    assert report["copied_records"] == 2
    assert report["alias_target_after"] == "siq_agent_memory"
    assert client.aliases["siq_agent_memory_active"] == "siq_agent_memory"
    assert len(client.collections["siq_agent_memory"]) == 2
    assert all(row["research_market"] == "" for row in client.collections["siq_agent_memory__v2"])
    assert "alter_alias" not in client.calls


def test_switch_alias_occurs_only_after_verified_target(monkeypatch):
    module = load_module()
    client = FakeClient([source_row(1)])
    snapshot = make_snapshot(module, client)
    install_create_stub(module, monkeypatch, client)

    report = module.execute_migration(
        snapshot,
        source="siq_agent_memory",
        target="siq_agent_memory__v2",
        alias="siq_agent_memory_active",
        apply=True,
        switch_alias=True,
        resume_existing_target=False,
        batch_size=10,
        client=client,
    )

    assert report["target_verification"]["passed"] is True
    assert report["alias_target_after"] == "siq_agent_memory__v2"
    assert client.calls.index("alter_alias") > client.calls.index("flush")


def test_resume_rejects_unverified_existing_target_and_keeps_source_alias(monkeypatch):
    module = load_module()
    client = FakeClient([source_row(1)])
    snapshot = make_snapshot(module, client)
    client.aliases["siq_agent_memory_active"] = "siq_agent_memory"
    client.collections["siq_agent_memory__v2"] = []
    client.fields["siq_agent_memory__v2"] = set(module.agent_memory_milvus.REQUIRED_FIELDS)

    try:
        module.execute_migration(
            snapshot,
            source="siq_agent_memory",
            target="siq_agent_memory__v2",
            alias="siq_agent_memory_active",
            apply=True,
            switch_alias=True,
            resume_existing_target=True,
            batch_size=10,
            client=client,
        )
    except RuntimeError as exc:
        assert "target verification failed" in str(exc)
        assert exc.migration_report["passed"] is False
        assert exc.migration_report["writes_performed"] == []
    else:
        raise AssertionError("expected target verification failure")

    assert client.aliases["siq_agent_memory_active"] == "siq_agent_memory"
    assert "alter_alias" not in client.calls


def test_rollback_alias_only_restores_source_after_verified_target():
    module = load_module()
    client = FakeClient([source_row(1)])
    snapshot = make_snapshot(module, client)
    client.collections["siq_agent_memory__v2"] = [source_row(1)]
    client.fields["siq_agent_memory__v2"] = set(module.agent_memory_milvus.REQUIRED_FIELDS)
    client.aliases["siq_agent_memory_active"] = "siq_agent_memory__v2"

    report = module.execute_migration(
        snapshot,
        source="siq_agent_memory",
        target="siq_agent_memory__v2",
        alias="siq_agent_memory_active",
        apply=True,
        switch_alias=False,
        resume_existing_target=False,
        rollback=True,
        batch_size=10,
        client=client,
    )

    assert report["passed"] is True
    assert report["writes_performed"] == ["rollback_alias_to_source"]
    assert report["alias_target_after"] == "siq_agent_memory"
    assert client.aliases["siq_agent_memory_active"] == "siq_agent_memory"
    assert "upsert" not in client.calls
    assert "drop_collection" not in client.calls


def test_resume_rejects_wrong_target_vector_contract_before_alias_switch():
    module = load_module()

    class WrongDimensionClient(FakeClient):
        def describe_collection(self, name):
            description = super().describe_collection(name)
            if name == "siq_agent_memory__v2":
                for field in description["fields"]:
                    if field["name"] == "vector":
                        field["params"] = {"dim": 2048}
            return description

    client = WrongDimensionClient([source_row(1)])
    snapshot = make_snapshot(module, client)
    target_row = source_row(1)
    target_row.update({field: "" for field in module.agent_memory_milvus.RESEARCH_IDENTITY_FIELDS})
    client.collections["siq_agent_memory__v2"] = [target_row]
    client.fields["siq_agent_memory__v2"] = set(module.agent_memory_milvus.REQUIRED_FIELDS)
    client.aliases["siq_agent_memory_active"] = "siq_agent_memory"

    try:
        module.execute_migration(
            snapshot,
            source="siq_agent_memory",
            target="siq_agent_memory__v2",
            alias="siq_agent_memory_active",
            apply=True,
            switch_alias=True,
            resume_existing_target=True,
            batch_size=10,
            client=client,
        )
    except RuntimeError as exc:
        assert exc.migration_report["target_verification"]["checks"]["vector_dimension_matches"] is False
    else:
        raise AssertionError("expected target vector verification failure")

    assert client.aliases["siq_agent_memory_active"] == "siq_agent_memory"
    assert "alter_alias" not in client.calls


def test_failed_alias_switch_verification_automatically_restores_source(monkeypatch):
    module = load_module()

    class OneShotHiddenTargetAliasClient(FakeClient):
        hide_target_alias_once = True

        def list_aliases(self, collection_name):
            aliases = super().list_aliases(collection_name)
            if (
                collection_name == "siq_agent_memory__v2"
                and self.aliases.get("siq_agent_memory_active") == collection_name
                and self.hide_target_alias_once
            ):
                self.hide_target_alias_once = False
                return {"aliases": [], "collection_name": collection_name}
            return aliases

    client = OneShotHiddenTargetAliasClient([source_row(1)])
    snapshot = make_snapshot(module, client)
    client.aliases["siq_agent_memory_active"] = "siq_agent_memory"
    install_create_stub(module, monkeypatch, client)

    try:
        module.execute_migration(
            snapshot,
            source="siq_agent_memory",
            target="siq_agent_memory__v2",
            alias="siq_agent_memory_active",
            apply=True,
            switch_alias=True,
            resume_existing_target=False,
            batch_size=10,
            client=client,
        )
    except RuntimeError as exc:
        report = exc.migration_report
        assert "switch_alias_to_v2" in report["writes_performed"]
        assert "rollback_alias_after_switch_failure" in report["writes_performed"]
        assert report["alias_target_after_rollback"] == "siq_agent_memory"
    else:
        raise AssertionError("expected alias switch verification failure")

    assert client.aliases["siq_agent_memory_active"] == "siq_agent_memory"


def test_rollback_refuses_when_alias_is_not_on_v2_target():
    module = load_module()
    client = FakeClient([source_row(1)])
    snapshot = make_snapshot(module, client)
    client.collections["siq_agent_memory__v2"] = []
    client.fields["siq_agent_memory__v2"] = set(module.agent_memory_milvus.REQUIRED_FIELDS)
    client.aliases["siq_agent_memory_active"] = "siq_agent_memory"

    try:
        module.execute_migration(
            snapshot,
            source="siq_agent_memory",
            target="siq_agent_memory__v2",
            alias="siq_agent_memory_active",
            apply=True,
            switch_alias=False,
            resume_existing_target=False,
            rollback=True,
            batch_size=10,
            client=client,
        )
    except RuntimeError as exc:
        assert "alias does not point to the target" in str(exc)
    else:
        raise AssertionError("expected rollback precondition failure")

    assert client.aliases["siq_agent_memory_active"] == "siq_agent_memory"
    assert "alter_alias" not in client.calls


def test_existing_target_rejection_does_not_bootstrap_source_alias(monkeypatch):
    module = load_module()
    client = FakeClient([source_row(1)])
    snapshot = make_snapshot(module, client)
    client.collections["siq_agent_memory__v2"] = []
    client.fields["siq_agent_memory__v2"] = set(module.agent_memory_milvus.REQUIRED_FIELDS)
    install_create_stub(module, monkeypatch, client)

    try:
        module.execute_migration(
            snapshot,
            source="siq_agent_memory",
            target="siq_agent_memory__v2",
            alias="siq_agent_memory_active",
            apply=True,
            switch_alias=False,
            resume_existing_target=False,
            batch_size=10,
            client=client,
        )
    except RuntimeError as exc:
        assert "target collection already exists" in str(exc)
    else:
        raise AssertionError("expected existing target rejection")

    assert client.aliases == {}
    assert "create_alias" not in client.calls


def test_alias_target_uses_describe_alias_when_list_is_ambiguous():
    module = load_module()
    client = FakeClient([source_row(1)])
    client.collections["siq_agent_memory__v2"] = []
    client.aliases["siq_agent_memory_active"] = "siq_agent_memory__v2"

    def describe_alias(alias):
        return {"alias_name": alias, "collection_name": "siq_agent_memory__v2"}

    client.describe_alias = describe_alias

    assert module.alias_target(client, "siq_agent_memory_active", ["siq_agent_memory", "siq_agent_memory__v2"]) == "siq_agent_memory__v2"
