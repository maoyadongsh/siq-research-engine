from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _load_module():
    source = PROJECT_ROOT / "scripts" / "hermes" / "ingest_ic_profile_knowledge.py"
    spec = importlib.util.spec_from_file_location("ingest_ic_profile_knowledge_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _manifest_path() -> Path:
    return (
        PROJECT_ROOT
        / "agents"
        / "hermes"
        / "profiles"
        / "siq_ic_shared"
        / "knowledge"
        / "manifest.v1.json"
    )


def test_versioned_manifest_covers_seven_profiles_and_validates_assets():
    module = _load_module()

    manifest = module.load_and_validate_manifest(_manifest_path())

    assert manifest["schema_version"] == "siq_ic_profile_knowledge_manifest_v1"
    assert manifest["contract_version"] == "siq_ic_profile_knowledge_v1"
    assert manifest["source_class"] == "background_knowledge"
    assert manifest["manifest_digest"] == module.manifest_digest(manifest)
    assert {entry["profile_id"] for entry in manifest["profiles"]} == set(module.PROFILE_IDS)
    assert len({entry["physical_collection"] for entry in manifest["profiles"]}) == 7
    assert all(Path(entry["asset_path"]).is_file() for entry in manifest["profiles"])


def test_default_execution_is_dry_run_and_does_not_connect(monkeypatch, capsys):
    module = _load_module()
    monkeypatch.setattr(
        module,
        "open_collection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("dry-run must not connect to Milvus")),
    )
    monkeypatch.setattr(
        module,
        "embed_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("dry-run must not call embedding")),
    )

    exit_code = module.main(["--manifest", str(_manifest_path()), "--all"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["passed"] is True
    assert payload["write"] is False
    assert payload["dry_run"] is True
    assert payload["inserted"] == 0
    assert payload["planned_chunks"] >= 7
    assert len(payload["profiles"]) == 7


def test_deterministic_knowledge_ids_are_stable():
    module = _load_module()
    manifest = module.load_and_validate_manifest(_manifest_path())
    entries = manifest["profiles"]

    first = module.build_records(manifest, entries, embed_model="embedding-v1")
    second = module.build_records(manifest, entries, embed_model="embedding-v1")
    changed_model = module.build_records(manifest, entries, embed_model="embedding-v2")

    assert [record["knowledge_id"] for record in first] == [record["knowledge_id"] for record in second]
    assert [record["record_digest"] for record in first] == [record["record_digest"] for record in second]
    assert [record["knowledge_id"] for record in first] == [record["knowledge_id"] for record in changed_model]
    assert [record["record_digest"] for record in first] != [
        record["record_digest"] for record in changed_model
    ]


def test_record_metadata_keeps_background_source_and_full_text():
    module = _load_module()
    manifest = module.load_and_validate_manifest(_manifest_path())

    records = module.build_records(manifest, manifest["profiles"], embed_model="embedding-v1")

    assert {record["profile_id"] for record in records} == set(module.PROFILE_IDS)
    for record in records:
        metadata = record["metadata"]
        assert metadata["source_class"] == "background_knowledge"
        assert metadata["knowledge_type"] == "methodology"
        assert metadata["profile"] == record["profile_id"]
        assert metadata["profile_id"] == record["profile_id"]
        assert metadata["project_fact"] is False
        assert metadata["managed_by"] == module.MANAGED_BY
        assert metadata["text"] == record["text"]
        assert metadata["text_len"] == len(record["text"])
        assert metadata["title"] == record["title"]
        assert metadata["contract_version"] == manifest["contract_version"]
        assert metadata["manifest_digest"] == manifest["manifest_digest"]
        assert not Path(metadata["source_path"]).is_absolute()


def test_incompatible_collection_schema_fails_closed_before_embedding_or_write(monkeypatch, capsys):
    module = _load_module()

    class FakeField:
        def __init__(self, name, dtype_name, *, primary=False, auto_id=False, dim=None):
            self.name = name
            self.dtype = SimpleNamespace(name=dtype_name)
            self.is_primary = primary
            self.auto_id = auto_id
            self.params = {"dim": dim} if dim is not None else {}

    class FakeCollection:
        schema = SimpleNamespace(
            fields=[
                FakeField("id", "INT64", primary=True, auto_id=True),
                FakeField("vector", "FLOAT_VECTOR", dim=768),
                FakeField("project_tag", "VARCHAR"),
                FakeField("metadata", "JSON"),
            ]
        )
        indexes = [
            SimpleNamespace(
                field_name="vector",
                params={"metric_type": "L2", "index_type": "HNSW"},
            )
        ]

        def load(self):
            raise AssertionError("incompatible schema must fail before query")

        def insert(self, _rows):
            raise AssertionError("incompatible schema must not write")

    monkeypatch.setattr(module, "open_collection", lambda *_args, **_kwargs: FakeCollection())
    monkeypatch.setattr(
        module,
        "embed_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must fail before embedding")),
    )

    exit_code = module.main(
        [
            "--manifest",
            str(_manifest_path()),
            "--profile",
            "siq_ic_master_coordinator",
            "--write",
            "--embed-url",
            "https://embedding.example/v1",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["passed"] is False
    assert payload["error_type"] == "SchemaCompatibilityError"
    assert payload["inserted"] == 0
    assert payload["deleted"] == 0
    assert "embedding.example" not in json.dumps(payload)


def test_write_requires_explicit_embedding_endpoint_before_milvus_connect(monkeypatch, capsys):
    module = _load_module()
    for name in (
        "SIQ_IC_KNOWLEDGE_EMBEDDING_BASE_URL",
        "SIQ_EMBEDDING_BASE_URL",
        "EMBEDDING_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        module,
        "open_collection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must fail before Milvus connect")),
    )

    exit_code = module.main(
        [
            "--manifest",
            str(_manifest_path()),
            "--profile",
            "siq_ic_master_coordinator",
            "--write",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["passed"] is False
    assert payload["embedding_endpoint_configured"] is False
    assert payload["error_type"] == "EmbeddingConfigurationError"
    assert payload["inserted"] == 0


def test_reconcile_plan_is_idempotent_and_removes_duplicates():
    module = _load_module()
    record = {
        "knowledge_id": "ICKB-STABLE",
        "record_digest": "digest-v1",
    }
    existing = [
        {"id": 11, "metadata": {"knowledge_id": "ICKB-STABLE", "record_digest": "digest-v1"}},
        {"id": 12, "metadata": {"knowledge_id": "ICKB-STABLE", "record_digest": "digest-v1"}},
        {"id": 13, "metadata": {"knowledge_id": "ICKB-STALE", "record_digest": "old"}},
    ]

    plan = module.build_reconcile_plan([record], existing)

    assert plan["insert_records"] == []
    assert plan["skipped"] == 1
    assert plan["delete_ids"] == [12, 13]
