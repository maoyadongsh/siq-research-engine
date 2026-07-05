import json

from services import deal_retrieval
from services import deal_store
from services import external_research_clients
from services import rerank_provider
from services import vector_retrieval


def _write_ndjson(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _clear_optional_retrieval_env(monkeypatch):
    for name in (
        "SIQ_VECTOR_RETRIEVAL_ENABLED",
        "SIQ_EMBEDDING_BASE_URL",
        "EMBEDDING_BASE_URL",
        "SIQ_RERANK_ENABLED",
        "SIQ_RERANK_BASE_URL",
        "RERANK_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_deal_retrieval_ranks_role_evidence_and_builds_dynamic_queries(tmp_path, monkeypatch):
    _clear_optional_retrieval_env(monkeypatch)
    deal_store.create_deal_package(
        deal_id="DEAL-RETRIEVAL-001",
        company_name="宇树科技",
        industry="机器人",
        stage="Pre-IPO",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-RETRIEVAL-001"
    _write_ndjson(
        package_dir / "evidence" / "evidence_items.ndjson",
        [
            {
                "evidence_id": "EVID-DEAL-RETRIEVAL-001-000001",
                "dimension": "legal",
                "evidence_type": "verified",
                "claim": "公司拥有多项专利和软件著作权。",
                "role_hints": ["siq_ic_legal_scanner"],
            },
            {
                "evidence_id": "EVID-DEAL-RETRIEVAL-001-000002",
                "dimension": "finance",
                "evidence_type": "verified",
                "claim": "Revenue reached RMB 100m and gross margin improved.",
                "role_hints": ["siq_ic_finance_auditor"],
            },
        ],
    )

    result = deal_retrieval.retrieve_for_agent(
        "DEAL-RETRIEVAL-001",
        "ic_finance_auditor",
        query="收入 毛利率 估值",
        wiki_root=tmp_path,
    )

    assert result["schema_version"] == "siq_deal_retrieval_result_v1"
    assert result["agent_id"] == "siq_ic_finance_auditor"
    assert result["retrieval_mode"] == "local_dynamic_evidence_package_v1"
    assert result["matched_evidence_count"] == 1
    assert result["evidence_hits"][0]["evidence_id"] == "EVID-DEAL-RETRIEVAL-001-000002"
    assert result["evidence_hits"][0]["retrieval_score"] > 0
    assert result["hybrid_hits"][0]["evidence_id"] == "EVID-DEAL-RETRIEVAL-001-000002"
    assert result["hybrid_hit_count"] == 1
    assert [item["query_type"] for item in result["dynamic_queries"]] == ["base", "role_focus", "evidence_gap"]
    assert result["vector_retrieval"]["status"] == "skipped"
    assert result["vector_retrieval"]["reason"] == "vector_retrieval_disabled"
    assert result["rerank"]["status"] == "skipped"
    assert result["rerank"]["reason"] == "rerank_disabled"
    assert result["milvus_used"] is False
    assert result["reranker_used"] is False
    assert result["external_research"]["enabled"] is False
    assert result["external_research"]["providers"][0]["reason"] == "external_research_disabled"


def test_vector_retrieval_enabled_without_embedding_endpoint_skips_safely(monkeypatch):
    _clear_optional_retrieval_env(monkeypatch)

    result = vector_retrieval.retrieve_vector_hits(
        query="宇树科技 机器人",
        profile_id="siq_ic_finance_auditor",
        enabled=True,
        collections=["siq_deal_shared"],
    )

    assert result["schema_version"] == "siq_vector_retrieval_result_v1"
    assert result["enabled"] is True
    assert result["configured"] is False
    assert result["status"] == "skipped"
    assert result["reason"] == "embedding_endpoint_not_configured"
    assert result["hits"] == []


def test_rerank_provider_normalizes_openai_compatible_response(monkeypatch):
    _clear_optional_retrieval_env(monkeypatch)
    monkeypatch.setenv("SIQ_RERANK_BASE_URL", "https://rerank.example")
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"index": 1, "relevance_score": 0.92},
                    {"index": 0, "relevance_score": 0.31},
                ]
            }

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, **kwargs):
            calls.append({"url": url, **kwargs})
            return FakeResponse()

    monkeypatch.setattr(rerank_provider.httpx, "Client", FakeClient)

    result = rerank_provider.rerank_candidates(
        query="收入 毛利",
        candidates=[
            {"source_id": "A", "quote_preview": "估值偏高，需要关注退出。"},
            {"source_id": "B", "quote_preview": "收入增长，毛利率改善。"},
        ],
        enabled=True,
        top_n=2,
    )

    assert result["schema_version"] == "siq_rerank_result_v1"
    assert result["status"] == "completed"
    assert [item["source_id"] for item in result["results"]] == ["B", "A"]
    assert result["results"][0]["rerank_score"] == 0.92
    assert calls[0]["url"] == "https://rerank.example/v1/rerank"
    assert "https://rerank.example" not in json.dumps(result, ensure_ascii=False)


def test_external_exa_search_normalizes_results_without_leaking_key(monkeypatch):
    monkeypatch.setenv("SIQ_EXA_API_KEY", "test-exa-key")
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "title": "宇树科技融资新闻",
                        "url": "https://example.com/yushu",
                        "text": "宇树科技完成新一轮融资。",
                        "publishedDate": "2026-01-02",
                        "score": 0.91,
                    }
                ]
            }

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, **kwargs):
            calls.append({"url": url, **kwargs})
            return FakeResponse()

    monkeypatch.setattr(external_research_clients.httpx, "Client", FakeClient)

    result = external_research_clients.run_external_research(
        query="宇树科技 融资",
        providers=["exa"],
        max_results=3,
        enabled=True,
    )

    assert result["schema_version"] == "siq_external_research_v1"
    assert result["enabled"] is True
    assert result["providers"][0]["status"] == "completed"
    assert result["results"][0]["provider"] == "exa"
    assert result["results"][0]["url"] == "https://example.com/yushu"
    assert calls[0]["headers"]["Authorization"] == "Bearer test-exa-key"
    assert "test-exa-key" not in json.dumps(result, ensure_ascii=False)
