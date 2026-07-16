import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from routers import deals

from services import deal_contracts, deal_store, ic_agent_runtime, ic_startup_retrieval

DEAL_ID = "DEAL-PMM-IC-SNAPSHOT-001"
SOURCE_ID = "PM:DEAL-PMM-IC-SNAPSHOT-001:DOC-0123456789ABCDEF:PRUN-20260713-ABCDEF123456"


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_evidence(path: Path) -> None:
    rows = [
        {
            "evidence_id": f"EVID-{DEAL_ID}-{index:06d}",
            "deal_id": DEAL_ID,
            "source_id": SOURCE_ID,
            "evidence_type": "verified",
            "dimension": dimension,
            "claim": f"{dimension} evidence",
            "role_hints": ["siq_ic_finance_auditor"] if dimension == "finance" else [],
        }
        for index, dimension in enumerate(("business", "finance", "legal", "risk"), start=1)
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _snapshot(hash_value: str = "a" * 64) -> dict:
    return {
        "schema_version": "siq_deal_evidence_snapshot_v1",
        "deal_id": DEAL_ID,
        "snapshot_hash": hash_value,
        "source_ids": [SOURCE_ID],
        "active_sources": [
            {
                "source_id": SOURCE_ID,
                "document_id": "DOC-0123456789ABCDEF",
                "parse_run_id": "PRUN-20260713-ABCDEF123456",
                "status": "ready_with_restrictions",
                "capabilities": {
                    "text_evidence": "ready",
                    "source_page_trace": "ready",
                    "financial_facts": "blocked",
                    "semantic_index": "pending",
                },
            }
        ],
    }


def test_receipt_and_task_payload_bind_current_primary_market_snapshot(tmp_path: Path, monkeypatch):
    for name in (
        "SIQ_VECTOR_RETRIEVAL_ENABLED",
        "SIQ_EMBEDDING_BASE_URL",
        "EMBEDDING_BASE_URL",
        "SIQ_RERANK_ENABLED",
        "SIQ_RERANK_BASE_URL",
        "RERANK_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    deal_store.create_deal_package(
        deal_id=DEAL_ID,
        company_name="Snapshot Issuer",
        industry="Semiconductors",
        stage="Pre-IPO",
        wiki_root=tmp_path,
    )
    package = tmp_path / "deals" / DEAL_ID
    _write_evidence(package / "evidence" / "evidence_items.ndjson")
    _write_json(package / "evidence" / "evidence_snapshot.json", _snapshot())

    receipt = ic_startup_retrieval.generate_startup_retrieval_receipt(
        DEAL_ID,
        "siq_ic_finance_auditor",
        wiki_root=tmp_path,
    )

    assert receipt["schema_version"] == "siq_ic_startup_receipt_v2"
    assert receipt["deal_id"] == DEAL_ID
    assert receipt["phase"] == "R1A"
    assert receipt["queries"]
    assert receipt["retrieval_collections"] == ["siq_deal_shared", "siq_ic_finance_auditor"]
    assert receipt["private_collection"] == "siq_ic_finance_auditor"
    assert receipt["private_hits"] == 0
    assert receipt["retrieval_status"] == "blocked"
    assert receipt["gate"]["allowed_to_speak"] is False
    assert receipt["gate"]["blocking_reasons"] == [
        "embedding_endpoint_not_configured",
        "rerank_endpoint_not_configured",
    ]
    assert receipt["source_ids"] == [SOURCE_ID]
    assert receipt["evidence_snapshot_hash"] == "a" * 64
    assert receipt["capability_restrictions"][SOURCE_ID] == ["financial_facts", "semantic_index"]
    assert receipt["research_identities"][0]["domain"] == "primary_market"
    assert "primary_market_financial_facts_restricted" in receipt["gaps"]
    checks = {item["id"]: item for item in deal_contracts.run_deal_preflight(DEAL_ID, wiki_root=tmp_path)["checks"]}
    assert checks["retrieval.evidence_snapshot"]["status"] == "pass"

    task = ic_agent_runtime.build_ic_agent_task_dry_run(
        DEAL_ID,
        "siq_ic_finance_auditor",
        wiki_root=tmp_path,
    )
    assert task["payload"]["source_ids"] == [SOURCE_ID]
    assert task["payload"]["evidence_snapshot_hash"] == "a" * 64
    assert task["payload"]["research_identities"][0]["filing_id"].startswith("PROSPECTUS:")


def test_preflight_blocks_receipt_after_snapshot_changes(tmp_path: Path, monkeypatch):
    for name in ("SIQ_VECTOR_RETRIEVAL_ENABLED", "SIQ_RERANK_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    deal_store.create_deal_package(
        deal_id=DEAL_ID,
        company_name="Snapshot Issuer",
        wiki_root=tmp_path,
    )
    package = tmp_path / "deals" / DEAL_ID
    _write_evidence(package / "evidence" / "evidence_items.ndjson")
    _write_json(package / "evidence" / "evidence_snapshot.json", _snapshot())
    ic_startup_retrieval.generate_startup_retrieval_receipt(
        DEAL_ID,
        "siq_ic_strategist",
        wiki_root=tmp_path,
    )

    _write_json(package / "evidence" / "evidence_snapshot.json", _snapshot("b" * 64))

    preflight = deal_contracts.run_deal_preflight(DEAL_ID, wiki_root=tmp_path)
    snapshot_check = next(item for item in preflight["checks"] if item["id"] == "retrieval.evidence_snapshot")
    assert snapshot_check["status"] == "fail"
    task = ic_agent_runtime.build_ic_agent_task_dry_run(
        DEAL_ID,
        "siq_ic_strategist",
        wiki_root=tmp_path,
    )
    assert "preflight_fail:retrieval.evidence_snapshot" in task["blocking_reasons"]
    loaded = ic_startup_retrieval.read_startup_retrieval_receipt(
        DEAL_ID,
        "siq_ic_strategist",
        wiki_root=tmp_path,
    )
    assert loaded["receipt"]["readiness_status"] == "stale"
    assert loaded["receipt"]["current_evidence_snapshot_hash"] == "b" * 64
    assert loaded["receipt"]["gate"]["allowed_to_speak"] is False
    assert "evidence_snapshot_changed" in loaded["receipt"]["gate"]["blocking_reasons"]


def test_receipt_freshness_detects_active_source_set_change():
    receipt = {
        "evidence_snapshot_hash": "a" * 64,
        "source_ids": [SOURCE_ID],
    }

    freshness = ic_startup_retrieval.evaluate_startup_receipt_freshness(
        receipt,
        {
            "evidence_snapshot_hash": "a" * 64,
            "source_ids": [SOURCE_ID, "PM:OTHER"],
        },
    )

    assert freshness["status"] == "stale"
    assert freshness["reasons"] == ["active_source_set_changed"]


def test_legacy_ic_receipt_key_is_read_through_canonical_profile(tmp_path: Path):
    deal_store.create_deal_package(
        deal_id=DEAL_ID,
        company_name="Legacy Receipt Issuer",
        wiki_root=tmp_path,
    )
    package = tmp_path / "deals" / DEAL_ID
    _write_json(
        package / "phases" / "startup_receipts.json",
        {
            "agents": {
                "ic_finance_auditor": {
                    "receipt_id": "legacy-finance-receipt",
                    "round_name": "R1",
                    "source_ids": [],
                    "evidence_snapshot_hash": None,
                }
            }
        },
    )

    loaded = ic_startup_retrieval.read_startup_retrieval_receipt(
        DEAL_ID,
        "siq_ic_finance_auditor",
        wiki_root=tmp_path,
    )

    assert loaded["receipt"]["receipt_id"] == "legacy-finance-receipt"


def test_workflow_write_action_rejects_expected_snapshot_mismatch(monkeypatch):
    monkeypatch.setattr(
        deals.ic_startup_retrieval,
        "current_evidence_identity",
        lambda _deal_id: {"evidence_snapshot_hash": "b" * 64},
    )

    with pytest.raises(HTTPException) as exc_info:
        deals._require_expected_evidence_snapshot(DEAL_ID, "a" * 64)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "evidence_snapshot_mismatch"
    assert exc_info.value.detail["current_evidence_snapshot_hash"] == "b" * 64


def test_private_milvus_hit_becomes_stable_background_reference():
    hit = {
        "source_id": "VEC-siq_ic_finance_auditor-001",
        "collection": "siq_ic_finance_auditor",
        "title": "Revenue quality methodology",
        "text": "Compare receivable growth with revenue growth and operating cash conversion.",
    }

    first = ic_startup_retrieval._background_reference(
        hit,
        profile_id="siq_ic_finance_auditor",
        physical_collection="ic_finance_auditor",
    )
    second = ic_startup_retrieval._background_reference(
        hit,
        profile_id="siq_ic_finance_auditor",
        physical_collection="ic_finance_auditor",
    )

    assert first == second
    assert first["ref_id"].startswith("KBREF-")
    assert first["source_class"] == "background_knowledge"
    assert first["collection"] == "siq_ic_finance_auditor"
    assert first["physical_collection"] == "ic_finance_auditor"
    assert first["usage"] == "background"
    assert not first["ref_id"].startswith("EVID-")


def test_managed_methodology_hit_becomes_methodology_reference():
    reference = ic_startup_retrieval._background_reference(
        {
            "source_id": "VEC-siq_ic_legal_scanner-method-001",
            "collection": "siq_ic_legal_scanner",
            "knowledge_lane": "methodology",
            "title": "Legal review methodology",
            "text": "Review ownership, permits, contracts, disputes and closing conditions.",
            "metadata": {"knowledge_type": "methodology"},
        },
        profile_id="siq_ic_legal_scanner",
        physical_collection="ic_legal_scanner",
    )

    assert reference["usage"] == "methodology"
    assert reference["source_class"] == "background_knowledge"
    assert reference["ref_id"].startswith("KBREF-")


def _retrieval_result(*, methodology: bool, domain: bool) -> dict:
    profile_id = "siq_ic_legal_scanner"
    method_hit = {
        "source_id": "managed-method-1",
        "collection": profile_id,
        "knowledge_lane": "methodology",
        "title": "Legal review methodology",
        "text": "Review ownership, permits, contracts, disputes and closing conditions.",
        "metadata": {"knowledge_type": "methodology"},
        "source_class": "background_knowledge",
    }
    domain_hit = {
        "source_id": "domain-law-1",
        "collection": profile_id,
        "knowledge_lane": "domain_background",
        "title": "Patent law guidance",
        "text": "Review chain of title and pending ownership disputes.",
        "metadata": {"source_path": "patent-law.md"},
        "source_class": "background_knowledge",
    }
    methodology_hits = [method_hit] if methodology else []
    domain_hits = [domain_hit] if domain else []
    background_hits = [*methodology_hits, *domain_hits]
    return {
        "dimensions": ["legal"],
        "evidence_hits": [
            {
                "evidence_id": f"EVID-{DEAL_ID}-000003",
                "source_class": "project_evidence",
            }
        ],
        "hybrid_hits": [],
        "shared_vector_hits": [
            {
                "source_id": "shared-project-1",
                "collection": "siq_deal_shared",
                "project_tag": DEAL_ID,
                "metadata": {
                    "domain": "primary_market",
                    "source_class": "project_evidence",
                    "project_fact": True,
                    "deal_id": DEAL_ID,
                    "project_tag": DEAL_ID,
                    "snapshot_hash": "a" * 64,
                },
            }
        ],
        "background_knowledge_hits": background_hits,
        "methodology_hits": methodology_hits,
        "domain_background_hits": domain_hits,
        "background_selection": {
            "methodology_selected": len(methodology_hits),
            "domain_selected": len(domain_hits),
        },
        "vector_retrieval": {
            "status": "completed",
            "milvus_used": True,
            "collections": ["siq_deal_shared", profile_id],
            "physical_collections": {
                "siq_deal_shared": "ic_collaboration_shared",
                profile_id: "ic_legal_scanner",
            },
            "shared_filter_applied": True,
            "shared_project_tag": DEAL_ID,
        },
        "matched_evidence_count": 1,
        "milvus_used": True,
        "rerank": {
            "status": "completed",
            "reason": None,
            "candidate_count": len(background_hits) + 1,
            "result_count": len(background_hits) + 1,
        },
        "reranker_used": True,
        "gaps": [],
        "dynamic_queries": [],
    }


def test_startup_receipt_blocks_domain_only_private_kb(tmp_path: Path, monkeypatch):
    deal_store.create_deal_package(
        deal_id=DEAL_ID,
        company_name="Snapshot Issuer",
        wiki_root=tmp_path,
    )
    package = tmp_path / "deals" / DEAL_ID
    _write_evidence(package / "evidence" / "evidence_items.ndjson")
    _write_json(package / "evidence" / "evidence_snapshot.json", _snapshot())
    monkeypatch.setattr(
        ic_startup_retrieval.deal_retrieval,
        "retrieve_for_agent",
        lambda *_args, **_kwargs: _retrieval_result(methodology=False, domain=True),
    )

    receipt = ic_startup_retrieval.generate_startup_retrieval_receipt(
        DEAL_ID,
        "siq_ic_legal_scanner",
        wiki_root=tmp_path,
    )

    assert receipt["retrieval_status"] == "blocked"
    assert receipt["gate"]["blocking_reasons"] == ["private_methodology_missing"]
    assert receipt["methodology_refs"] == []


def test_startup_receipt_accepts_methodology_only_and_types_refs(tmp_path: Path, monkeypatch):
    deal_store.create_deal_package(
        deal_id=DEAL_ID,
        company_name="Snapshot Issuer",
        wiki_root=tmp_path,
    )
    package = tmp_path / "deals" / DEAL_ID
    _write_evidence(package / "evidence" / "evidence_items.ndjson")
    _write_json(package / "evidence" / "evidence_snapshot.json", _snapshot())
    monkeypatch.setattr(
        ic_startup_retrieval.deal_retrieval,
        "retrieve_for_agent",
        lambda *_args, **_kwargs: _retrieval_result(methodology=True, domain=False),
    )

    receipt = ic_startup_retrieval.generate_startup_retrieval_receipt(
        DEAL_ID,
        "siq_ic_legal_scanner",
        wiki_root=tmp_path,
    )

    assert receipt["retrieval_status"] == "ready"
    assert receipt["gate"]["allowed_to_speak"] is True
    assert "private_domain_corpus_empty" in receipt["gaps"]
    assert receipt["methodology_hit_count"] == 1
    assert receipt["domain_background_hit_count"] == 0
    assert receipt["methodology_refs"][0]["usage"] == "methodology"
    assert receipt["background_knowledge_refs"][0]["usage"] == "methodology"


def test_startup_receipt_separates_empty_shared_content_from_collection_connectivity(
    tmp_path: Path,
    monkeypatch,
):
    deal_store.create_deal_package(
        deal_id=DEAL_ID,
        company_name="Snapshot Issuer",
        wiki_root=tmp_path,
    )
    retrieval = _retrieval_result(methodology=True, domain=False)
    retrieval["evidence_hits"] = []
    retrieval["shared_vector_hits"] = []
    retrieval["matched_evidence_count"] = 0
    retrieval["vector_retrieval"]["collection_candidate_counts"] = {
        "siq_deal_shared": 0,
        "siq_ic_legal_scanner": 4,
    }
    monkeypatch.setattr(
        ic_startup_retrieval.deal_retrieval,
        "retrieve_for_agent",
        lambda *_args, **_kwargs: retrieval,
    )

    receipt = ic_startup_retrieval.generate_startup_retrieval_receipt(
        DEAL_ID,
        "siq_ic_legal_scanner",
        wiki_root=tmp_path,
    )

    assert receipt["shared_connected"] is True
    assert receipt["private_connected"] is True
    assert receipt["collections_connected"] is True
    assert receipt["dual_kb_connected"] is True
    assert receipt["connection_status"] == "connected"
    assert receipt["connection_errors"] == []
    assert receipt["connection_checked_at"] == receipt["created_at"]
    assert receipt["chat_retrieval_ready"] is True
    assert receipt["chat_retrieval_status"] == "ready"
    assert receipt["shared_ready"] is False
    assert receipt["retrieval_status"] == "blocked"
    assert receipt["gate"]["allowed_to_speak"] is False
    assert receipt["gate"]["blocking_reasons"] == ["deal_scoped_shared_kb_empty"]
    assert "deal_scoped_shared_kb_empty" in receipt["gaps"]
    assert "deal_scoped_shared_kb_unavailable" not in receipt["gaps"]
    assert receipt["collection_connections"]["shared"]["connected"] is True
    assert receipt["collection_connections"]["shared"]["hit_count"] == 0
    assert receipt["shared_vector_hit_count"] == 0
    assert receipt["local_evidence_hit_count"] == 0
    assert receipt["private_selected_hit_count"] == 1
