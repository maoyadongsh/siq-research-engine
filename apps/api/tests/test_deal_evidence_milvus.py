from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

from services import deal_evidence, deal_evidence_milvus, deal_store


class _FakeField:
    def __init__(
        self,
        name: str,
        dtype_name: str,
        *,
        dim: int | None = None,
        is_primary: bool = False,
        auto_id: bool = False,
    ):
        self.name = name
        self.dtype = SimpleNamespace(name=dtype_name)
        self.params = {"dim": dim} if dim is not None else {}
        self.is_primary = is_primary
        self.auto_id = auto_id


class _FakeCollection:
    def __init__(self):
        self.schema = SimpleNamespace(
            fields=[
                _FakeField("id", "INT64", is_primary=True, auto_id=True),
                _FakeField("custom_vector", "FLOAT_VECTOR", dim=3),
                _FakeField("project_tag", "VARCHAR"),
                _FakeField("metadata", "JSON"),
            ]
        )
        self.rows = [
            {
                "id": 1,
                "project_tag": "DEAL-MILVUS-001",
                "metadata": {
                    "deal_id": "DEAL-MILVUS-001",
                    "evidence_id": "old-evidence",
                    "snapshot_hash": "0" * 64,
                },
            }
        ]
        self.next_id = 2
        self.events: list[object] = []

    def load(self):
        self.events.append("load")

    def query(self, *, expr, output_fields, limit):
        self.events.append(("query", expr, tuple(output_fields), limit))
        return [
            {"id": row["id"], "metadata": row["metadata"]}
            for row in self.rows
            if row["project_tag"] == "DEAL-MILVUS-001"
        ]

    def insert(self, columns):
        self.events.append("insert")
        vectors, project_tags, metadata_rows = columns
        for vector, project_tag, metadata in zip(
            vectors,
            project_tags,
            metadata_rows,
            strict=True,
        ):
            self.rows.append(
                {
                    "id": self.next_id,
                    "custom_vector": vector,
                    "project_tag": project_tag,
                    "metadata": metadata,
                }
            )
            self.next_id += 1

    def delete(self, *, expr):
        self.events.append(("delete", expr))
        ids = {int(value) for value in re.findall(r"\d+", expr)}
        self.rows = [row for row in self.rows if row["id"] not in ids]

    def flush(self):
        self.events.append("flush")


def _write_index_fixture(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-MILVUS-001",
        company_name="Milvus Robotics",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-MILVUS-001"
    rows = [
        {
            "schema_version": "siq_deal_evidence_item_v1",
            "deal_id": "DEAL-MILVUS-001",
            "evidence_id": "EVID-DEAL-MILVUS-001-000001",
            "document_id": "DOC-MILVUS-001",
            "source_id": "PM:DEAL-MILVUS-001:DOC-MILVUS-001",
            "quote": "Revenue grew 80 percent year over year.",
            "citation": "financial-model.md · L10-L12",
            "locator": "document.md:L10-L12",
            "dimension": "finance",
            "evidence_type": "verified",
            "source_path": "wiki/company/materials/finance/DOC-MILVUS-001.md",
            "wiki_path": "wiki/company/materials/finance/DOC-MILVUS-001.md",
            "wiki_sha256": "1" * 64,
            "parse_task_id": "task-milvus-001",
            "original_path": "data_room/raw/DOC-MILVUS-001.pdf",
            "original_sha256": "a" * 64,
            "parser_source_path": "parser_results/task-milvus-001/document.md",
            "parser_source_sha256": "b" * 64,
        },
        {
            "schema_version": "siq_deal_evidence_item_v1",
            "deal_id": "DEAL-MILVUS-001",
            "evidence_id": "EVID-DEAL-MILVUS-001-000002",
            "document_id": "DOC-MILVUS-002",
            "source_id": "PM:DEAL-MILVUS-001:DOC-MILVUS-002",
            "claim": "The company owns its core motion-control patents.",
            "citation": "legal-dd.md · L20-L24",
            "locator": "document.md:L20-L24",
            "dimension": "legal",
            "evidence_type": "verified",
            "source_path": "wiki/company/materials/legal/DOC-MILVUS-002.md",
            "wiki_path": "wiki/company/materials/legal/DOC-MILVUS-002.md",
            "wiki_sha256": "2" * 64,
            "parse_task_id": "task-milvus-002",
            "original_path": "data_room/raw/DOC-MILVUS-002.pdf",
            "original_sha256": "c" * 64,
            "parser_source_path": "parser_results/task-milvus-002/document.md",
            "parser_source_sha256": "d" * 64,
        },
    ]
    evidence_path = package_dir / "evidence" / "evidence_items.ndjson"
    evidence_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    deal_store.write_json(
        package_dir / "evidence" / "evidence_snapshot.json",
        {
            "schema_version": "siq_deal_evidence_snapshot_v1",
            "deal_id": "DEAL-MILVUS-001",
            "snapshot_hash": "a" * 64,
        },
    )
    return package_dir


def test_index_deal_evidence_replaces_old_rows_and_is_idempotent(tmp_path, monkeypatch):
    package_dir = _write_index_fixture(tmp_path)
    collection = _FakeCollection()
    monkeypatch.setattr(deal_evidence_milvus, "_open_collection", lambda: collection)
    embed_calls = []

    def fake_embed(texts, *, dimensions, timeout):
        embed_calls.append({"texts": list(texts), "dimensions": dimensions, "timeout": timeout})
        assert [row["id"] for row in collection.rows] == [1]
        assert "insert" not in collection.events
        return [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]

    monkeypatch.setattr(deal_evidence_milvus, "_embed_texts", fake_embed)

    first = deal_evidence_milvus.index_deal_evidence_milvus(
        "DEAL-MILVUS-001",
        created_by={"id": 7, "username": "analyst"},
        wiki_root=tmp_path,
    )

    assert first["status"] == "indexed"
    assert first["project_tag"] == "DEAL-MILVUS-001"
    assert first["physical_collection"] == "ic_collaboration_shared"
    assert first["vector_field"] == "custom_vector"
    assert first["vector_dimensions"] == 3
    assert first["counts"] == {"items": 2, "existing": 1, "inserted": 2, "deleted": 1}
    assert embed_calls[0]["dimensions"] == 3
    assert [row["id"] for row in collection.rows] == [2, 3]
    assert collection.events.index("insert") < next(
        index
        for index, event in enumerate(collection.events)
        if isinstance(event, tuple) and event[0] == "delete"
    )

    metadata_rows = [row["metadata"] for row in collection.rows]
    assert {metadata["evidence_id"] for metadata in metadata_rows} == {
        "EVID-DEAL-MILVUS-001-000001",
        "EVID-DEAL-MILVUS-001-000002",
    }
    for metadata in metadata_rows:
        assert metadata["domain"] == "primary_market"
        assert metadata["source_class"] == "project_evidence"
        assert metadata["project_fact"] is True
        assert metadata["deal_id"] == "DEAL-MILVUS-001"
        assert metadata["snapshot_hash"] == "a" * 64
        assert metadata["embedding_model"] == deal_evidence_milvus.DEFAULT_EMBEDDING_MODEL
        assert metadata["vector_field"] == "custom_vector"
        assert metadata["vector_dimensions"] == 3
        assert metadata["source_id"]
        assert metadata["document_id"]
        assert metadata["wiki_path"].startswith("wiki/company/materials/")
        assert len(metadata["wiki_sha256"]) == 64
        assert metadata["original_path"].startswith("data_room/raw/")
        assert len(metadata["original_sha256"]) == 64
        assert metadata["parser_source_path"].startswith("parser_results/")
        assert len(metadata["parser_source_sha256"]) == 64
        assert metadata["text"]
        assert "citation" in metadata

    persisted = deal_store.read_json(
        package_dir / "evidence" / "milvus_index_receipt.json",
        {},
    )
    assert persisted["receipt_id"] == first["receipt_id"]
    manifest = deal_store.read_json(package_dir / "manifest.json", {})
    assert manifest["evidence"]["last_milvus_index"]["status"] == "indexed"
    audit = deal_store.read_json(package_dir / "audit" / "audit_log.json", {})
    assert audit["events"][-1]["event_type"] == "deal_evidence_milvus_indexed"

    mutation_events = [
        event
        for event in collection.events
        if event == "insert" or isinstance(event, tuple) and event[0] == "delete"
    ]
    second = deal_evidence_milvus.index_deal_evidence_milvus(
        "DEAL-MILVUS-001",
        created_by={"id": 7, "username": "analyst"},
        wiki_root=tmp_path,
    )

    assert second["status"] == "unchanged"
    assert second["receipt_id"] == first["receipt_id"]
    assert second["counts"] == {"items": 2, "existing": 2, "inserted": 0, "deleted": 0}
    assert len(embed_calls) == 1
    assert [
        event
        for event in collection.events
        if event == "insert" or isinstance(event, tuple) and event[0] == "delete"
    ] == mutation_events


def test_embed_texts_uses_agent_memory_embedding_environment(monkeypatch):
    for name in (
        "SIQ_EMBEDDING_BASE_URL",
        "SIQ_EMBEDDING_MODEL",
        "SIQ_EMBEDDING_API_KEY",
        "EMBEDDING_BASE_URL",
        "EMBEDDING_MODEL",
        "EMBEDDING_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SIQ_AGENT_MEMORY_EMBEDDING_BASE_URL", "https://embedding.internal/v1")
    monkeypatch.setenv("SIQ_AGENT_MEMORY_EMBEDDING_MODEL", "shared-embedding-model")
    monkeypatch.setenv("SIQ_AGENT_MEMORY_EMBEDDING_API_KEY", "shared-embedding-key")
    request = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"index": 1, "embedding": [0.4, 0.5, 0.6]},
                    {"index": 0, "embedding": [0.1, 0.2, 0.3]},
                ]
            }

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, endpoint, **kwargs):
            request.update({"endpoint": endpoint, **kwargs})
            return FakeResponse()

    monkeypatch.setattr(deal_evidence_milvus.httpx, "Client", FakeClient)

    vectors = deal_evidence_milvus._embed_texts(
        ["first", "second"],
        dimensions=3,
        timeout=15,
    )

    assert vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    assert request["endpoint"] == "https://embedding.internal/v1/embeddings"
    assert request["headers"]["Authorization"] == "Bearer shared-embedding-key"
    assert request["json"] == {
        "model": "shared-embedding-model",
        "input": ["first", "second"],
    }


def test_evidence_build_auto_indexes_only_when_enabled(tmp_path, monkeypatch):
    deal_store.create_deal_package(
        deal_id="DEAL-AUTO-MILVUS-001",
        company_name="Auto Index Robotics",
        wiki_root=tmp_path,
    )
    monkeypatch.setenv("SIQ_PRIMARY_MARKET_MILVUS_INDEX_ENABLED", "true")
    calls = []

    def fake_index(deal_id, **kwargs):
        calls.append({"deal_id": deal_id, **kwargs})
        snapshot_path = (
            tmp_path
            / "deals"
            / "DEAL-AUTO-MILVUS-001"
            / "evidence"
            / "evidence_snapshot.json"
        )
        assert snapshot_path.is_file()
        return {"status": "indexed", "receipt_id": "PMMILVUS-AUTO"}

    monkeypatch.setattr(deal_evidence_milvus, "index_deal_evidence_milvus", fake_index)

    result = deal_evidence.build_deal_evidence_package(
        "DEAL-AUTO-MILVUS-001",
        built_by={"id": 7, "username": "analyst"},
        wiki_root=tmp_path,
    )

    assert result["milvus_index"] == {"status": "indexed", "receipt_id": "PMMILVUS-AUTO"}
    assert calls == [
        {
            "deal_id": "DEAL-AUTO-MILVUS-001",
            "created_by": {"id": 7, "username": "analyst"},
            "wiki_root": tmp_path,
        }
    ]


def test_embedding_failure_preserves_existing_rows_and_writes_failed_receipt(tmp_path, monkeypatch):
    package_dir = _write_index_fixture(tmp_path)
    collection = _FakeCollection()
    monkeypatch.setattr(deal_evidence_milvus, "_open_collection", lambda: collection)
    monkeypatch.setattr(
        deal_evidence_milvus,
        "_embed_texts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("embedding unavailable")),
    )

    with pytest.raises(deal_evidence_milvus.DealEvidenceMilvusIndexError) as exc_info:
        deal_evidence_milvus.index_deal_evidence_milvus(
            "DEAL-MILVUS-001",
            wiki_root=tmp_path,
        )

    assert [row["id"] for row in collection.rows] == [1]
    assert "insert" not in collection.events
    receipt = exc_info.value.receipt
    assert receipt is not None
    assert receipt["status"] == "failed"
    assert "embedding unavailable" in receipt["error"]
    assert deal_store.read_json(
        package_dir / "evidence" / "milvus_index_receipt.json",
        {},
    )["status"] == "failed"


def test_empty_snapshot_removes_previous_deal_rows_without_embedding(tmp_path, monkeypatch):
    package_dir = _write_index_fixture(tmp_path)
    (package_dir / "evidence" / "evidence_items.ndjson").write_text("", encoding="utf-8")
    collection = _FakeCollection()
    collection.rows.append({
        "id": 99,
        "project_tag": "ingest_0430_2033",
        "metadata": {"document_id": "DOC-LEGACYLEGACY"},
    })
    monkeypatch.setattr(deal_evidence_milvus, "_open_collection", lambda: collection)
    monkeypatch.setattr(
        deal_evidence_milvus,
        "_embed_texts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("embedding must be skipped")),
    )

    receipt = deal_evidence_milvus.index_deal_evidence_milvus(
        "DEAL-MILVUS-001",
        wiki_root=tmp_path,
    )

    assert receipt["status"] == "indexed"
    assert receipt["counts"] == {"items": 0, "existing": 1, "inserted": 0, "deleted": 1}
    assert [row["id"] for row in collection.rows] == [99]
    assert collection.rows[0]["project_tag"] == "ingest_0430_2033"


def test_remove_document_rows_keeps_other_deal_materials(tmp_path, monkeypatch):
    package_dir = _write_index_fixture(tmp_path)
    collection = _FakeCollection()
    collection.rows = [
        {
            "id": 1,
            "project_tag": "DEAL-MILVUS-001",
            "metadata": {"document_id": "DOC-AAAAAAAAAAAA"},
        },
        {
            "id": 2,
            "project_tag": "DEAL-MILVUS-001",
            "metadata": {"document_id": "DOC-BBBBBBBBBBBB"},
        },
    ]
    monkeypatch.setattr(deal_evidence_milvus, "_open_collection", lambda: collection)

    result = deal_evidence_milvus.remove_deal_document_rows(
        "DEAL-MILVUS-001",
        "DOC-AAAAAAAAAAAA",
        deleted_by={"id": 7},
        wiki_root=tmp_path,
    )

    assert result["status"] == "cleaned"
    assert result["deleted"] == 1
    assert [row["id"] for row in collection.rows] == [2]
    audit = deal_store.read_json(package_dir / "audit" / "audit_log.json", {})
    assert audit["events"][-1]["event_type"] == "deal_document_milvus_rows_removed"
