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


def test_milvus_search_reconnects_once_after_transient_failure(monkeypatch):
    _clear_milvus_search_env(monkeypatch)
    collection = _FakeCollection("ic_legal_scanner", "IP")
    attempts = []
    disconnects = []

    def flaky_search(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise OSError("stale Milvus channel")
        return [[]]

    collection.search = flaky_search
    monkeypatch.setattr(pymilvus.connections, "connect", lambda **_kwargs: None)
    monkeypatch.setattr(pymilvus.connections, "disconnect", lambda alias: disconnects.append(alias))
    monkeypatch.setattr(pymilvus.utility, "has_collection", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(pymilvus, "Collection", lambda _name, **_kwargs: collection)

    assert vector_retrieval._search_milvus_collection(
        "siq_ic_legal_scanner",
        [0.1, 0.2],
        top_k=3,
    ) == []
    assert len(attempts) == 2
    assert disconnects == ["siq_deal_retrieval"]


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


def test_hybrid_rank_fuses_dense_bm25_and_rrf_without_new_runtime_dependency():
    hits = [
        {"source_id": "dense-general", "text": "企业基本情况和一般尽调事项"},
        {"source_id": "dense-finance", "text": "收入现金流与估值模型"},
        {"source_id": "exact-legal", "text": "股权权属 重大合同 诉讼 知识产权 数据合规 交割条件"},
        {"source_id": "other", "text": "行业技术路线和竞争格局"},
    ]

    ranked = vector_retrieval._hybrid_rank_hits(
        "请核验股权权属、诉讼和数据合规",
        hits,
        limit=4,
    )

    by_id = {item["source_id"]: item for item in ranked}
    assert ranked[0]["source_id"] == "exact-legal"
    assert by_id["exact-legal"]["bm25_score"] > by_id["dense-general"]["bm25_score"]
    assert by_id["exact-legal"]["dense_rank"] == 3
    assert by_id["exact-legal"]["lexical_rank"] == 1
    assert all(item["rrf_score"] == item["hybrid_score"] for item in ranked)


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
        if collection_name == vector_retrieval.SHARED_DEAL_COLLECTION:
            return []
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
        allowed_project_tag="DEAL-LEGAL-001",
        top_k=4,
    )

    assert result["status"] == "completed"
    assert result["methodology_hit_count"] == 1
    assert result["methodology_hits"][0]["source_id"] == "managed-method-1"
    method_call = next(
        item
        for item in calls
        if item["collection"] == "siq_ic_legal_scanner" and item["expr"]
    )
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


def test_embed_query_accepts_agent_memory_embedding_environment(monkeypatch):
    monkeypatch.delenv("SIQ_EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("SIQ_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("SIQ_EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.setenv("SIQ_AGENT_MEMORY_EMBEDDING_BASE_URL", "https://memory-embedding.example/v1")
    monkeypatch.setenv("SIQ_AGENT_MEMORY_EMBEDDING_MODEL", "repository-embedding-model")
    monkeypatch.setenv("SIQ_AGENT_MEMORY_EMBEDDING_API_KEY", "memory-embedding-key")
    request = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"embedding": [0.3, 0.4]}]}

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

    assert embedding == [0.3, 0.4]
    assert request["endpoint"] == "https://memory-embedding.example/v1/embeddings"
    assert request["headers"]["Authorization"] == "Bearer memory-embedding-key"
    assert request["json"]["model"] == "repository-embedding-model"


def test_embedding_endpoint_preserves_full_embeddings_path(monkeypatch):
    monkeypatch.setenv(
        "SIQ_AGENT_MEMORY_EMBEDDING_BASE_URL",
        "https://memory-embedding.example/v1/embeddings",
    )
    monkeypatch.delenv("SIQ_EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)

    assert vector_retrieval._embedding_endpoint() == (
        "https://memory-embedding.example/v1/embeddings"
    )


def test_vector_retrieval_scopes_shared_collection_and_filters_wrong_tags(monkeypatch):
    monkeypatch.setenv("SIQ_EMBEDDING_BASE_URL", "https://embedding.example")
    monkeypatch.setattr(vector_retrieval, "find_spec", lambda _name: object())
    monkeypatch.setattr(vector_retrieval, "_embed_query", lambda *_args, **_kwargs: [0.1, 0.2])
    calls = []

    def fake_search(collection_name, _embedding, *, top_k, expr=None):
        calls.append({"collection": collection_name, "top_k": top_k, "expr": expr})
        if collection_name != vector_retrieval.SHARED_DEAL_COLLECTION:
            return []
        return [
            {
                "source_id": "same-deal",
                "collection": collection_name,
                "project_tag": "DEAL-SCOPE-001",
            },
            {
                "source_id": "other-deal",
                "collection": collection_name,
                "project_tag": "DEAL-SCOPE-999",
            },
            {"source_id": "missing-tag", "collection": collection_name},
        ]

    monkeypatch.setattr(vector_retrieval, "_search_milvus_collection", fake_search)

    result = vector_retrieval.retrieve_vector_hits(
        query="issuer diligence",
        profile_id="siq_ic_finance_auditor",
        enabled=True,
        collections=[vector_retrieval.SHARED_DEAL_COLLECTION],
        allowed_project_tag="DEAL-SCOPE-001",
        top_k=5,
    )

    assert calls == [{
        "collection": vector_retrieval.SHARED_DEAL_COLLECTION,
        "top_k": 20,
        "expr": 'project_tag == "DEAL-SCOPE-001"',
    }]
    assert [item["source_id"] for item in result["hits"]] == ["same-deal"]
    assert result["collection_hit_counts"][vector_retrieval.SHARED_DEAL_COLLECTION] == 1
    assert result["shared_project_tag"] == "DEAL-SCOPE-001"
    assert result["shared_filter_applied"] is True
    assert result["shared_hits_rejected"] == 2
    assert result["collection_candidate_counts"][vector_retrieval.SHARED_DEAL_COLLECTION] == 1
    assert result["retrieval_strategy"]["mode"] == "dense_bm25_rrf"
    assert result["retrieval_strategy"]["candidate_top_k"] == 20
    assert result["retrieval_strategy"]["rrf"]["k"] == 40


def test_vector_retrieval_does_not_query_shared_collection_without_allowed_tag(monkeypatch):
    monkeypatch.setenv("SIQ_EMBEDDING_BASE_URL", "https://embedding.example")
    monkeypatch.setattr(vector_retrieval, "find_spec", lambda _name: object())
    monkeypatch.setattr(vector_retrieval, "_embed_query", lambda *_args, **_kwargs: [0.1, 0.2])
    calls = []
    monkeypatch.setattr(
        vector_retrieval,
        "_search_milvus_collection",
        lambda *args, **kwargs: calls.append((args, kwargs)) or [{"source_id": "must-not-leak"}],
    )

    result = vector_retrieval.retrieve_vector_hits(
        query="issuer diligence",
        profile_id="siq_ic_finance_auditor",
        enabled=True,
        collections=[vector_retrieval.SHARED_DEAL_COLLECTION],
    )

    assert calls == []
    assert result["status"] == "completed"
    assert result["hits"] == []
    assert result["shared_filter_applied"] is False


def test_primary_collection_alias_mismatch_fails_before_embedding_or_milvus(monkeypatch):
    monkeypatch.setenv("SIQ_EMBEDDING_BASE_URL", "https://embedding.example")
    monkeypatch.setenv(
        "SIQ_MILVUS_COLLECTION_ALIAS_SIQ_DEAL_SHARED",
        "secondary_financial_reports",
    )
    calls = []
    monkeypatch.setattr(
        vector_retrieval,
        "_embed_query",
        lambda *_args, **_kwargs: calls.append("embed") or [0.1],
    )
    monkeypatch.setattr(
        vector_retrieval,
        "_search_milvus_collection",
        lambda *_args, **_kwargs: calls.append("milvus") or [],
    )

    result = vector_retrieval.retrieve_vector_hits(
        query="issuer diligence",
        profile_id="siq_ic_finance_auditor",
        enabled=True,
        collections=["siq_deal_shared", "siq_ic_finance_auditor"],
        required_physical_collections=vector_retrieval.primary_market_physical_collections(
            "siq_ic_finance_auditor"
        ),
        allowed_project_tag="DEAL-SCOPE-001",
    )

    assert result["status"] == "error"
    assert result["reason"] == "collection_alias_scope_violation"
    assert result["binding_mismatches"]["siq_deal_shared"] == {
        "expected": "ic_collaboration_shared",
        "actual": "secondary_financial_reports",
    }
    assert calls == []


def test_vector_retrieval_rejects_unsafe_project_tag_before_milvus_query(monkeypatch):
    monkeypatch.setenv("SIQ_EMBEDDING_BASE_URL", "https://embedding.example")
    monkeypatch.setattr(vector_retrieval, "find_spec", lambda _name: object())
    calls = []
    monkeypatch.setattr(
        vector_retrieval,
        "_search_milvus_collection",
        lambda *args, **kwargs: calls.append((args, kwargs)) or [],
    )

    result = vector_retrieval.retrieve_vector_hits(
        query="issuer diligence",
        profile_id="siq_ic_finance_auditor",
        enabled=True,
        collections=[vector_retrieval.SHARED_DEAL_COLLECTION],
        allowed_project_tag='DEAL-001" or project_tag != "',
    )

    assert calls == []
    assert result["status"] == "error"
    assert result["failure_stage"] == "project_tag_validation"
    assert result["error"] == "allowed_project_tag_invalid"
