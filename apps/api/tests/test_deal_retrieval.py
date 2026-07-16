import json

from services import deal_retrieval, deal_store, external_research_clients, rerank_provider, vector_retrieval


def _write_ndjson(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _write_snapshot(package_dir, deal_id, snapshot_hash="a" * 64):
    path = package_dir / "evidence" / "evidence_snapshot.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"deal_id": deal_id, "snapshot_hash": snapshot_hash}),
        encoding="utf-8",
    )


def _clear_optional_retrieval_env(monkeypatch):
    for name in (
        "SIQ_VECTOR_RETRIEVAL_ENABLED",
        "SIQ_EMBEDDING_BASE_URL",
        "SIQ_AGENT_MEMORY_EMBEDDING_BASE_URL",
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
    _write_snapshot(package_dir, "DEAL-RETRIEVAL-001")
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
    _write_snapshot(package_dir, "DEAL-RETRIEVAL-001")

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
    assert result["evidence_hits"][0]["source_class"] == "project_evidence"
    assert result["evidence_hits"][0]["retrieval_score"] > 0
    assert result["hybrid_hits"][0]["evidence_id"] == "EVID-DEAL-RETRIEVAL-001-000002"
    assert result["hybrid_hit_count"] == 1
    assert [item["query_type"] for item in result["dynamic_queries"]] == ["base", "role_focus", "evidence_gap"]
    assert result["vector_retrieval"]["status"] == "skipped"
    assert result["vector_retrieval"]["reason"] == "vector_retrieval_disabled"
    assert result["rerank"]["status"] == "skipped"
    assert result["rerank"]["reason"] == "rerank_endpoint_not_configured"
    assert result["milvus_used"] is False
    assert result["reranker_used"] is False
    assert result["external_research"]["enabled"] is False
    assert result["external_research"]["providers"][0]["reason"] == "external_research_disabled"


def test_deal_retrieval_rejects_unbound_local_evidence_without_snapshot(tmp_path, monkeypatch):
    _clear_optional_retrieval_env(monkeypatch)
    deal_store.create_deal_package(
        deal_id="DEAL-RETRIEVAL-NO-SNAPSHOT",
        company_name="Deleted Evidence Robotics",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-RETRIEVAL-NO-SNAPSHOT"
    _write_ndjson(
        package_dir / "evidence" / "evidence_items.ndjson",
        [{
            "evidence_id": "EVID-DEAL-RETRIEVAL-NO-SNAPSHOT-000001",
            "document_id": "DOC-AAAAAAAAAAAA",
            "dimension": "legal",
            "claim": "Deleted contract must not remain retrievable.",
            "role_hints": ["siq_ic_legal_scanner"],
        }],
    )

    result = deal_retrieval.retrieve_for_agent(
        "DEAL-RETRIEVAL-NO-SNAPSHOT",
        "siq_ic_legal_scanner",
        wiki_root=tmp_path,
    )

    assert result["matched_evidence_count"] == 0
    assert result["evidence_hits"] == []
    assert "evidence_snapshot_missing_local_evidence_rejected: 1" in result["gaps"]


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


def test_vector_retrieval_explicit_false_overrides_enabled_environment(monkeypatch):
    _clear_optional_retrieval_env(monkeypatch)
    monkeypatch.setenv("SIQ_VECTOR_RETRIEVAL_ENABLED", "true")
    monkeypatch.setenv("SIQ_EMBEDDING_BASE_URL", "https://embedding.example")

    result = vector_retrieval.retrieve_vector_hits(
        query="宇树科技 机器人",
        profile_id="siq_ic_finance_auditor",
        enabled=False,
        collections=["siq_deal_shared"],
    )

    assert result["enabled"] is False
    assert result["configured"] is True
    assert result["status"] == "skipped"
    assert result["reason"] == "vector_retrieval_disabled"
    assert result["hits"] == []


def test_vector_retrieval_fairly_merges_shared_and_private_collections(monkeypatch):
    _clear_optional_retrieval_env(monkeypatch)
    monkeypatch.setenv("SIQ_EMBEDDING_BASE_URL", "https://embedding.example")
    monkeypatch.setattr(vector_retrieval, "find_spec", lambda _name: object())
    monkeypatch.setattr(vector_retrieval, "_embed_query", lambda *_args, **_kwargs: [0.1, 0.2])

    def fake_search(collection_name, _embedding, *, top_k, expr=None):
        if collection_name == "siq_deal_shared":
            return [
                {
                    "source_id": f"{collection_name}-{index}",
                    "collection": collection_name,
                    "project_tag": "DEAL-MERGE-001",
                    "text": f"{collection_name} knowledge {index}",
                }
                for index in range(top_k)
            ]
        if expr:
            return []
        return [
            {
                "source_id": f"{collection_name}-{index}",
                "collection": collection_name,
                "text": f"{collection_name} knowledge {index}",
            }
            for index in range(top_k)
        ]

    monkeypatch.setattr(vector_retrieval, "_search_milvus_collection", fake_search)

    result = vector_retrieval.retrieve_vector_hits(
        query="机器人估值方法",
        profile_id="siq_ic_finance_auditor",
        enabled=True,
        allowed_project_tag="DEAL-MERGE-001",
        top_k=4,
    )

    assert result["status"] == "completed"
    assert result["milvus_used"] is True
    assert result["collection_hit_counts"] == {
        "siq_deal_shared": 4,
        "siq_ic_finance_auditor": 4,
    }
    assert result["physical_collections"] == {
        "siq_deal_shared": "ic_collaboration_shared",
        "siq_ic_finance_auditor": "ic_finance_auditor",
    }
    assert [item["collection"] for item in result["hits"]] == [
        "siq_deal_shared",
        "siq_ic_finance_auditor",
        "siq_deal_shared",
        "siq_ic_finance_auditor",
    ]


def test_deal_retrieval_separates_project_evidence_from_profile_background(tmp_path, monkeypatch):
    _clear_optional_retrieval_env(monkeypatch)
    deal_store.create_deal_package(
        deal_id="DEAL-RETRIEVAL-KB-001",
        company_name="Knowledge Robotics",
        industry="Robotics",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-RETRIEVAL-KB-001"
    _write_snapshot(package_dir, "DEAL-RETRIEVAL-KB-001")
    _write_ndjson(
        package_dir / "evidence" / "evidence_items.ndjson",
        [{
            "evidence_id": "EVID-DEAL-RETRIEVAL-KB-001-000001",
            "dimension": "finance",
            "evidence_type": "verified",
            "claim": "Issuer revenue is RMB 100m.",
        }],
    )
    monkeypatch.setattr(
        vector_retrieval,
        "retrieve_vector_hits",
        lambda **_kwargs: {
            "schema_version": "siq_vector_retrieval_result_v1",
            "status": "completed",
            "collections": ["siq_deal_shared", "siq_ic_finance_auditor"],
            "physical_collections": {
                "siq_deal_shared": "ic_collaboration_shared",
                "siq_ic_finance_auditor": "ic_finance_auditor",
            },
            "hits": [
                {
                    "source_id": "shared-1",
                    "collection": "siq_deal_shared",
                    "project_tag": "DEAL-RETRIEVAL-KB-001",
                    "text": "Issuer evidence",
                    "metadata": {
                        "domain": "primary_market",
                        "source_class": "project_evidence",
                        "project_fact": True,
                        "deal_id": "DEAL-RETRIEVAL-KB-001",
                        "project_tag": "DEAL-RETRIEVAL-KB-001",
                        "snapshot_hash": "a" * 64,
                    },
                },
                {
                    "source_id": "private-domain-1",
                    "collection": "siq_ic_finance_auditor",
                    "text": "Receivable aging domain guidance",
                    "metadata": {"source_path": "finance-domain.md"},
                },
            ],
            "hit_count": 2,
            "methodology_hits": [
                {
                    "source_id": "private-method-1",
                    "collection": "siq_ic_finance_auditor",
                    "text": "Revenue quality review methodology",
                    "project_tag": vector_retrieval.DEFAULT_MANAGED_KNOWLEDGE_PROJECT_TAG,
                    "metadata": {
                        "content_hash": "method-1",
                        "knowledge_type": "methodology",
                        "managed_by": vector_retrieval.MANAGED_KNOWLEDGE_WRITER,
                    },
                }
            ],
        },
    )

    result = deal_retrieval.retrieve_for_agent(
        "DEAL-RETRIEVAL-KB-001",
        "siq_ic_finance_auditor",
        include_vector=True,
        wiki_root=tmp_path,
    )

    assert result["background_knowledge_hit_count"] == 2
    assert result["background_knowledge_hits"][0]["source_class"] == "background_knowledge"
    assert result["methodology_hit_count"] == 1
    assert result["domain_background_hit_count"] == 1
    assert result["background_selection"]["methodology_selected"] == 1
    assert result["shared_vector_hits"][0]["source_class"] == "project_evidence"
    assert {item["source_class"] for item in result["hybrid_hits"]} == {
        "project_evidence",
        "background_knowledge",
    }


def test_deal_retrieval_passes_deal_tag_and_filters_cross_market_vector_hits(tmp_path, monkeypatch):
    _clear_optional_retrieval_env(monkeypatch)
    deal_store.create_deal_package(
        deal_id="DEAL-SCOPED-KB-001",
        company_name="Scoped Robotics",
        industry="Robotics",
        wiki_root=tmp_path,
    )
    _write_snapshot(tmp_path / "deals" / "DEAL-SCOPED-KB-001", "DEAL-SCOPED-KB-001")
    captured = {}

    def fake_retrieve_vector_hits(**kwargs):
        captured.update(kwargs)
        return {
            "schema_version": "siq_vector_retrieval_result_v1",
            "status": "completed",
            "collections": ["siq_deal_shared", "siq_ic_finance_auditor"],
            "collection_hit_counts": {
                "siq_deal_shared": 3,
                "siq_ic_finance_auditor": 3,
            },
            "hits": [
                {
                    "source_id": "shared-current",
                    "collection": "siq_deal_shared",
                    "project_tag": "DEAL-SCOPED-KB-001",
                    "text": "Current deal evidence",
                    "metadata": {
                        "domain": "primary_market",
                        "source_class": "project_evidence",
                        "project_fact": True,
                        "deal_id": "DEAL-SCOPED-KB-001",
                        "project_tag": "DEAL-SCOPED-KB-001",
                        "snapshot_hash": "a" * 64,
                    },
                },
                {
                    "source_id": "shared-stale",
                    "collection": "siq_deal_shared",
                    "project_tag": "DEAL-SCOPED-KB-001",
                    "text": "Stale same-deal evidence",
                    "metadata": {
                        "domain": "primary_market",
                        "source_class": "project_evidence",
                        "project_fact": True,
                        "deal_id": "DEAL-SCOPED-KB-001",
                        "project_tag": "DEAL-SCOPED-KB-001",
                        "snapshot_hash": "b" * 64,
                    },
                },
                {
                    "source_id": "shared-other",
                    "collection": "siq_deal_shared",
                    "project_tag": "DEAL-OTHER-999",
                    "text": "Other deal evidence",
                },
                {
                    "source_id": "private-safe",
                    "collection": "siq_ic_finance_auditor",
                    "text": "Primary-market valuation method",
                    "metadata": {"source_path": "knowledge/valuation.md"},
                },
                {
                    "source_id": "private-company-wiki",
                    "collection": "siq_ic_finance_auditor",
                    "text": "Secondary company report",
                    "metadata": {
                        "source_path": "/home/maoyd/siq-research-engine/data/wiki/companies/600001-Test/report.md"
                    },
                },
                {
                    "source_id": "private-secondary-group",
                    "collection": "siq_ic_finance_auditor",
                    "text": "Secondary agent memory",
                    "metadata": {"agent_group": "secondary_market"},
                },
                {
                    "source_id": "secondary-collection",
                    "collection": "siq_analysis",
                    "text": "Secondary-market analysis collection",
                },
            ],
            "hit_count": 7,
            "methodology_hits": [
                {
                    "source_id": "method-safe",
                    "collection": "siq_ic_finance_auditor",
                    "text": "Managed IC methodology",
                    "metadata": {"content_hash": "method-safe"},
                },
                {
                    "source_id": "method-secondary",
                    "collection": "siq_ic_finance_auditor",
                    "text": "Wrong-scope methodology",
                    "metadata": {
                        "content_hash": "method-secondary",
                        "market_scope": "secondary_market",
                    },
                },
            ],
            "methodology_hit_count": 2,
        }

    monkeypatch.setattr(vector_retrieval, "retrieve_vector_hits", fake_retrieve_vector_hits)

    result = deal_retrieval.retrieve_for_agent(
        "DEAL-SCOPED-KB-001",
        "siq_ic_finance_auditor",
        include_vector=True,
        vector_collections=[
            "siq_deal_shared",
            "siq_ic_finance_auditor",
            "siq_analysis",
            "ic_collaboration_shared",
        ],
        wiki_root=tmp_path,
    )

    assert captured["allowed_project_tag"] == "DEAL-SCOPED-KB-001"
    assert captured["collections"] == ["siq_deal_shared", "siq_ic_finance_auditor"]
    assert [item["source_id"] for item in result["shared_vector_hits"]] == ["shared-current"]
    assert [item["source_id"] for item in result["domain_background_hits"]] == ["private-safe"]
    assert [item["source_id"] for item in result["methodology_hits"]] == ["method-safe"]
    exposed = json.dumps(result, ensure_ascii=False)
    assert "shared-other" not in exposed
    assert "shared-stale" not in exposed
    assert "private-company-wiki" not in exposed
    assert "private-secondary-group" not in exposed
    assert "method-secondary" not in exposed
    assert "secondary-collection" not in exposed
    assert result["vector_retrieval"]["collection_hit_counts"] == {
        "siq_deal_shared": 1,
        "siq_ic_finance_auditor": 1,
    }
    assert result["vector_retrieval"]["primary_market_filter"] == {
        "deal_id": "DEAL-SCOPED-KB-001",
        "evidence_snapshot_hash": "a" * 64,
        "cross_deal_shared_hits_rejected": 1,
        "stale_or_unbound_shared_hits_rejected": 1,
        "secondary_market_private_hits_rejected": 3,
        "disallowed_collection_hits_rejected": 1,
    }
    assert any(
        gap.startswith("disallowed_primary_market_vector_collections:")
        for gap in result["gaps"]
    )


def test_primary_retrieval_ignores_global_secondary_collection_list(tmp_path, monkeypatch):
    _clear_optional_retrieval_env(monkeypatch)
    monkeypatch.setenv("SIQ_MILVUS_COLLECTIONS", "siq_analysis,shared")
    deal_store.create_deal_package(
        deal_id="DEAL-EXPLICIT-COLLECTIONS-001",
        company_name="Scoped Robotics",
        wiki_root=tmp_path,
    )
    captured = {}

    def fake_retrieve_vector_hits(**kwargs):
        captured.update(kwargs)
        return {
            "status": "completed",
            "collections": kwargs["collections"],
            "physical_collections": kwargs["required_physical_collections"],
            "hits": [],
            "methodology_hits": [],
        }

    monkeypatch.setattr(vector_retrieval, "retrieve_vector_hits", fake_retrieve_vector_hits)

    deal_retrieval.retrieve_for_agent(
        "DEAL-EXPLICIT-COLLECTIONS-001",
        "siq_ic_finance_auditor",
        include_vector=True,
        wiki_root=tmp_path,
    )

    assert captured["collections"] == ["siq_deal_shared", "siq_ic_finance_auditor"]
    assert captured["required_physical_collections"] == {
        "siq_deal_shared": "ic_collaboration_shared",
        "siq_ic_finance_auditor": "ic_finance_auditor",
    }


def test_private_background_selection_prioritizes_methodology_and_dedupes_domain_sources():
    methodology = [
        {
            "source_id": f"method-{index}",
            "metadata": {"content_hash": f"hash-{index}"},
        }
        for index in range(1, 4)
    ]
    domain = [
        {
            "source_id": "hainan-1",
            "title": "海南法规",
            "metadata": {"source_path": "hainan.md"},
        },
        {
            "source_id": "hainan-duplicate",
            "title": "海南法规重复片段",
            "metadata": {"source_path": "hainan.md"},
        },
        {
            "source_id": "jiangsu-patent",
            "title": "江苏省专利促进条例",
            "metadata": {"source_path": "jiangsu-patent.md"},
        },
    ]

    selected, stats = deal_retrieval._select_private_background_hits(
        methodology_hits=methodology,
        domain_hits=domain,
        limit=10,
    )

    assert [item["source_id"] for item in selected] == [
        "method-1",
        "method-2",
        "hainan-1",
        "jiangsu-patent",
    ]
    assert [item["knowledge_lane"] for item in selected] == [
        "methodology",
        "methodology",
        "domain_background",
        "domain_background",
    ]
    assert stats["methodology_selected"] == 2
    assert stats["domain_selected"] == 2
    assert stats["domain_duplicates_dropped"] == 1


def test_shared_vector_filter_fails_closed_without_current_snapshot():
    payload, stats = deal_retrieval._filter_primary_market_vector_payload(
        {
            "hits": [
                {
                    "source_id": "shared-unbound",
                    "collection": "siq_deal_shared",
                    "project_tag": "DEAL-NO-SNAPSHOT-001",
                    "metadata": {
                        "domain": "primary_market",
                        "source_class": "project_evidence",
                        "project_fact": True,
                        "deal_id": "DEAL-NO-SNAPSHOT-001",
                        "snapshot_hash": "a" * 64,
                    },
                }
            ]
        },
        profile_id="siq_ic_finance_auditor",
        deal_id="DEAL-NO-SNAPSHOT-001",
        snapshot_hash="",
    )

    assert payload["hits"] == []
    assert stats["stale_or_unbound_shared_hits_rejected"] == 1


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


def test_rerank_provider_explicit_false_overrides_enabled_environment(monkeypatch):
    _clear_optional_retrieval_env(monkeypatch)
    monkeypatch.setenv("SIQ_RERANK_ENABLED", "true")
    monkeypatch.setenv("SIQ_RERANK_BASE_URL", "https://rerank.example")
    calls = []

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, **kwargs):
            calls.append({"url": url, **kwargs})
            raise AssertionError("rerank should not be called when explicitly disabled")

    monkeypatch.setattr(rerank_provider.httpx, "Client", FakeClient)

    result = rerank_provider.rerank_candidates(
        query="收入 毛利",
        candidates=[{"source_id": "A", "quote_preview": "估值偏高。"}],
        enabled=False,
        top_n=1,
    )

    assert result["enabled"] is False
    assert result["configured"] is True
    assert result["status"] == "skipped"
    assert result["reason"] == "rerank_disabled"
    assert result["results"] == [{"source_id": "A", "quote_preview": "估值偏高。"}]
    assert calls == []


def test_rerank_provider_skips_empty_candidates_without_http(monkeypatch):
    _clear_optional_retrieval_env(monkeypatch)
    monkeypatch.setenv("SIQ_RERANK_BASE_URL", "https://rerank.example")
    calls = []

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, **kwargs):
            calls.append({"url": url, **kwargs})
            raise AssertionError("rerank should not be called without candidates")

    monkeypatch.setattr(rerank_provider.httpx, "Client", FakeClient)

    result = rerank_provider.rerank_candidates(
        query="收入 毛利",
        candidates=[],
        enabled=True,
    )

    assert result["enabled"] is True
    assert result["configured"] is True
    assert result["status"] == "skipped"
    assert result["reason"] == "no_candidates"
    assert result["results"] == []
    assert calls == []


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
