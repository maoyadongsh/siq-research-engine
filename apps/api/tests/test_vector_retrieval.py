from __future__ import annotations

from types import SimpleNamespace

import pymilvus

from services import vector_retrieval


class _FakeField:
    def __init__(self, name: str, dtype_name: str):
        self.name = name
        self.dtype = SimpleNamespace(name=dtype_name)


class _FakeIndex:
    def __init__(self, field_name: str, metric_type: str):
        self.field_name = field_name
        self.params = {
            "metric_type": metric_type,
            "index_type": "HNSW",
            "params": {"M": 32, "efConstruction": 256},
        }


class _FakeCollection:
    def __init__(self, name: str, metric_type: str):
        self.name = name
        self.schema = SimpleNamespace(fields=[
            _FakeField("id", "INT64"),
            _FakeField("vector", "FLOAT_VECTOR"),
            _FakeField("project_tag", "VARCHAR"),
            _FakeField("metadata", "JSON"),
        ])
        self.indexes = [_FakeIndex("vector", metric_type)]
        self.search_calls = []

    def load(self):
        return None

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        return [[]]


def _clear_milvus_search_env(monkeypatch):
    for name in (
        "SIQ_MILVUS_VECTOR_FIELD",
        "SIQ_MILVUS_OUTPUT_FIELDS",
        "SIQ_MILVUS_METRIC_TYPE",
    ):
        monkeypatch.delenv(name, raising=False)


def test_milvus_search_uses_each_collection_schema_and_index(monkeypatch):
    _clear_milvus_search_env(monkeypatch)
    collections = {
        "ic_collaboration_shared": _FakeCollection("ic_collaboration_shared", "L2"),
        "ic_legal_scanner": _FakeCollection("ic_legal_scanner", "IP"),
    }
    monkeypatch.setattr(pymilvus.connections, "connect", lambda **_kwargs: None)
    monkeypatch.setattr(pymilvus.utility, "has_collection", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        pymilvus,
        "Collection",
        lambda name, **_kwargs: collections[name],
    )

    vector_retrieval._search_milvus_collection("siq_deal_shared", [0.1, 0.2], top_k=3)
    vector_retrieval._search_milvus_collection("siq_ic_legal_scanner", [0.1, 0.2], top_k=3)

    shared_call = collections["ic_collaboration_shared"].search_calls[0]
    legal_call = collections["ic_legal_scanner"].search_calls[0]
    assert shared_call["anns_field"] == "vector"
    assert legal_call["anns_field"] == "vector"
    assert shared_call["param"] == {"metric_type": "L2", "params": {"ef": 128}}
    assert legal_call["param"] == {"metric_type": "IP", "params": {"ef": 128}}
    assert shared_call["output_fields"] == ["metadata", "project_tag"]
    assert legal_call["output_fields"] == ["metadata", "project_tag"]


def test_collection_search_config_filters_invalid_configured_fields(monkeypatch):
    _clear_milvus_search_env(monkeypatch)
    monkeypatch.setenv("SIQ_MILVUS_VECTOR_FIELD", "embedding")
    monkeypatch.setenv("SIQ_MILVUS_OUTPUT_FIELDS", "text,metadata,evidence_id,project_tag")
    monkeypatch.setenv("SIQ_MILVUS_METRIC_TYPE", "COSINE")
    collection = _FakeCollection("ic_chairman", "L2")

    config = vector_retrieval._collection_search_config(collection)

    assert config == {
        "vector_field": "vector",
        "output_fields": ["metadata", "project_tag"],
        "metric_type": "L2",
        "search_params": {"ef": 128},
    }


def test_vector_retrieval_fails_closed_on_milvus_exception(monkeypatch):
    monkeypatch.setenv("SIQ_EMBEDDING_BASE_URL", "https://embedding.example")
    monkeypatch.setattr(vector_retrieval, "find_spec", lambda _name: object())
    monkeypatch.setattr(vector_retrieval, "_embed_query", lambda *_args, **_kwargs: [0.1, 0.2])

    class FakeMilvusException(Exception):
        pass

    def fake_search(collection_name, _embedding, *, top_k, expr=None):
        if collection_name == "siq_ic_legal_scanner":
            raise FakeMilvusException("metric type mismatch")
        return [{"collection": collection_name, "text": "must not leak as a partial result"}]

    monkeypatch.setattr(vector_retrieval, "_search_milvus_collection", fake_search)

    result = vector_retrieval.retrieve_vector_hits(
        query="A-share IPO legal review",
        profile_id="siq_ic_legal_scanner",
        enabled=True,
        top_k=4,
    )

    assert result["status"] == "error"
    assert result["reason"] == "vector_retrieval_failed"
    assert result["milvus_used"] is False
    assert result["failure_stage"] == "collection_search"
    assert result["failed_collection"] == "siq_ic_legal_scanner"
    assert result["failed_physical_collection"] == "ic_legal_scanner"
    assert result["error_type"] == "FakeMilvusException"
    assert result["hits"] == []
    assert result["hit_count"] == 0


def test_vector_retrieval_has_independent_managed_methodology_lane(monkeypatch):
    monkeypatch.setenv("SIQ_EMBEDDING_BASE_URL", "https://embedding.example")
    monkeypatch.setattr(vector_retrieval, "find_spec", lambda _name: object())
    monkeypatch.setattr(vector_retrieval, "_embed_query", lambda *_args, **_kwargs: [0.1, 0.2])
    calls = []

    def fake_search(collection_name, _embedding, *, top_k, expr=None):
        calls.append({"collection": collection_name, "top_k": top_k, "expr": expr})
        if expr:
            tag = vector_retrieval.DEFAULT_MANAGED_KNOWLEDGE_PROJECT_TAG
            return [
                {
                    "source_id": "managed-method-1",
                    "collection": collection_name,
                    "project_tag": tag,
                    "metadata": {
                        "schema_version": vector_retrieval.MANAGED_KNOWLEDGE_SCHEMA,
                        "knowledge_type": "methodology",
                        "managed_by": vector_retrieval.MANAGED_KNOWLEDGE_WRITER,
                        "profile_id": "siq_ic_legal_scanner",
                        "project_tag": tag,
                        "project_fact": False,
                    },
                },
                {
                    "source_id": "wrong-profile",
                    "collection": collection_name,
                    "project_tag": tag,
                    "metadata": {
                        "schema_version": vector_retrieval.MANAGED_KNOWLEDGE_SCHEMA,
                        "knowledge_type": "methodology",
                        "managed_by": vector_retrieval.MANAGED_KNOWLEDGE_WRITER,
                        "profile_id": "siq_ic_finance_auditor",
                        "project_tag": tag,
                        "project_fact": False,
                    },
                },
            ]
        return [
            {
                "source_id": f"{collection_name}-domain",
                "collection": collection_name,
                "text": "domain result",
            }
        ]

    monkeypatch.setattr(vector_retrieval, "_search_milvus_collection", fake_search)

    result = vector_retrieval.retrieve_vector_hits(
        query="shared project query",
        private_query="patent lawsuit compliance methodology",
        profile_id="siq_ic_legal_scanner",
        enabled=True,
        top_k=4,
    )

    assert result["status"] == "completed"
    assert result["methodology_hit_count"] == 1
    assert result["methodology_hits"][0]["source_id"] == "managed-method-1"
    method_call = next(item for item in calls if item["expr"])
    assert method_call["collection"] == "siq_ic_legal_scanner"
    assert vector_retrieval.DEFAULT_MANAGED_KNOWLEDGE_PROJECT_TAG in method_call["expr"]


def test_embed_query_uses_repository_embedding_model_default(monkeypatch):
    monkeypatch.setenv("SIQ_EMBEDDING_BASE_URL", "https://embedding.example/v1")
    monkeypatch.delenv("SIQ_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    request = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"embedding": [0.1, 0.2]}]}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, endpoint, **kwargs):
            request.update({"endpoint": endpoint, **kwargs})
            return FakeResponse()

    monkeypatch.setattr(vector_retrieval.httpx, "Client", FakeClient)

    embedding = vector_retrieval._embed_query("IC diligence", timeout=5)

    assert embedding == [0.1, 0.2]
    assert request["endpoint"] == "https://embedding.example/v1/embeddings"
    assert request["json"]["model"] == "Qwen3-VL-Embedding-2B"
