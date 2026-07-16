import json
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services import deal_store, hermes_client, primary_market_meeting_readiness


def _write_ndjson(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _write_finance_receipt_and_report(package_dir: Path) -> None:
    evidence_id = "EVID-DEAL-MEET-READY-001-000002"
    deal_store.write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v1",
            "deal_id": "DEAL-MEET-READY-001",
            "agents": {
                "siq_ic_finance_auditor": {
                    "receipt_id": "startup-siq_ic_finance_auditor-R1-001",
                    "agent_id": "siq_ic_finance_auditor",
                    "round_name": "R1",
                    "query": "Ready Robotics 财务",
                    "project_tag": "DEAL-MEET-READY-001",
                    "shared_hits": 1,
                    "private_hits": 3,
                    "retrieval_collections": ["siq_deal_shared", "siq_ic_finance_auditor"],
                    "physical_collections": {
                        "siq_deal_shared": "ic_collaboration_shared",
                        "siq_ic_finance_auditor": "ic_finance_auditor",
                    },
                    "retrieval_status": "ready",
                    "shared_ready": True,
                    "private_ready": True,
                    "rerank_ready": True,
                    "retrieval_strategy": {"mode": "dense_bm25_rrf"},
                    "collection_candidate_counts": {
                        "siq_deal_shared": 12,
                        "siq_ic_finance_auditor": 20,
                    },
                    "rerank": {
                        "status": "completed",
                        "candidate_count": 16,
                        "result_count": 8,
                    },
                    "gate": {"allowed_to_speak": True, "blocking_reasons": []},
                    "workspace_rules_read": ["SOUL.md", "AGENTS.md"],
                    "gaps": [],
                    "evidence_hits": [{"evidence_id": evidence_id}],
                    "created_at": "2026-07-06T10:20:00+08:00",
                },
            },
        },
    )
    deal_store.write_json(
        package_dir / "phases" / "r1_reports.json",
        {
            "siq_ic_finance_auditor": {
                "agent_id": "siq_ic_finance_auditor",
                "round_name": "R1",
                "score": 82,
                "recommendation": "support",
                "verified": [{"claim": "收入已核验", "evidence_id": evidence_id}],
                "assumed": [],
                "open_questions": [],
                "risk_flags": [],
                "key_points": ["现金流质量尚可"],
                "evidence_stats": {"shared": 1, "private": 0, "total": 1},
                "startup_receipt_id": "startup-siq_ic_finance_auditor-R1-001",
                "artifact_path": "discussion/01_R1_finance_auditor_report.md",
                "created_at": "2026-07-06T10:30:00+08:00",
                "evidence_ids": [evidence_id],
            },
        },
    )
    _write_ndjson(
        package_dir / "evidence" / "evidence_items.ndjson",
        [
            {
                "evidence_id": evidence_id,
                "evidence_type": "verified",
                "dimension": "finance",
                "claim": "finance",
            }
        ],
    )
    (package_dir / "discussion" / "01_R1_finance_auditor_report.md").write_text(
        "\n".join(
            [
                "# R1 Finance",
                "## 检索结果摘要",
                "### 共享底稿证据",
                "### 私有知识库证据",
                "### 信息缺口清单",
                "### 检索后观点",
            ]
        ),
        encoding="utf-8",
    )


def test_build_meeting_readiness_aggregates_contract_receipt_report_and_quality(tmp_path, monkeypatch):
    monkeypatch.setattr(hermes_client, "_is_tcp_port_open", lambda *args, **kwargs: False)
    monkeypatch.setattr(primary_market_meeting_readiness, "_tcp_port_open", lambda *args, **kwargs: False)
    deal_store.create_deal_package(
        deal_id="DEAL-MEET-READY-001",
        company_name="Ready Robotics",
        industry="机器人",
        stage="Pre-IPO",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-MEET-READY-001"
    _write_finance_receipt_and_report(package_dir)

    readiness = primary_market_meeting_readiness.build_meeting_readiness(
        "DEAL-MEET-READY-001",
        wiki_root=tmp_path,
    )

    assert readiness["schema_version"] == "siq_primary_market_meeting_readiness_v1"
    assert readiness["deal_id"] == "DEAL-MEET-READY-001"
    assert len(readiness["profiles"]) == 7
    assert "/home/" not in json.dumps(readiness, ensure_ascii=False)

    by_profile = {item["profile_id"]: item for item in readiness["profiles"]}
    finance = by_profile["siq_ic_finance_auditor"]
    assert finance["runtime"]["health"] == "configured"
    assert finance["runtime"]["port"] == 18664
    assert finance["contract"]["startup_retrieval_required"] is True
    assert any(
        "historical financials" in responsibility
        for responsibility in finance["contract"]["responsibilities"]
    )
    assert finance["startup_receipt"]["present"] is True
    assert finance["startup_receipt"]["receipt_id"] == "startup-siq_ic_finance_auditor-R1-001"
    assert finance["startup_receipt"]["shared_hits"] == 1
    assert finance["startup_receipt"]["shared_collection"] == "ic_collaboration_shared"
    assert finance["startup_receipt"]["private_collection"] == "ic_finance_auditor"
    assert finance["startup_receipt"]["rerank_ready"] is True
    assert finance["startup_receipt"]["rerank_status"] == "completed"
    assert finance["startup_receipt"]["rerank_candidate_count"] == 16
    assert finance["startup_receipt"]["retrieval_strategy"]["mode"] == "dense_bm25_rrf"
    assert finance["startup_receipt"]["dual_kb_connected"] is False
    assert finance["service_ready_for_chat"] is False
    assert "shared_collection_unavailable" in finance["chat_blocking_reasons"]
    assert finance["r1_report"]["present"] is True
    assert finance["r1_report"]["score"] == 82
    assert "startup_receipt_missing" not in finance["quality"]["blocking_reasons"]

    sector = by_profile["siq_ic_sector_expert"]
    assert sector["startup_receipt"]["present"] is False
    assert sector["r1_report"]["present"] is False
    assert "startup_receipt_missing" in sector["quality"]["blocking_reasons"]
    assert "r1_report_missing" in sector["quality"]["warnings"]
    assert sector["quality"]["ready_for_formal_task"] is False

    master = by_profile["siq_ic_master_coordinator"]
    assert master["startup_receipt"]["required"] is True
    assert master["startup_receipt"]["skipped"] is False
    assert master["r1_report"]["required"] is False
    assert "startup_receipt_missing" in master["quality"]["blocking_reasons"]

    assert readiness["summary"]["profiles"] == 7
    assert readiness["summary"]["receipt_present"] == 1
    assert readiness["summary"]["receipt_required"] == 7
    assert readiness["summary"]["r1_reports_present"] == 1
    assert "siq_ic_sector_expert" in readiness["summary"]["blocking_profiles"]
    assert "siq_ic_master_coordinator" in readiness["summary"]["blocking_profiles"]


def test_build_meeting_readiness_without_runtime_skips_hermes_probe(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-MEET-READY-002",
        company_name="Quiet Robotics",
        wiki_root=tmp_path,
    )

    readiness = primary_market_meeting_readiness.build_meeting_readiness(
        "DEAL-MEET-READY-002",
        wiki_root=tmp_path,
        include_runtime=False,
    )

    assert readiness["profiles"][0]["runtime"]["health"] == "not_checked"
    assert readiness["summary"]["runtime_running"] == 0


def test_build_meeting_readiness_keeps_chat_service_ready_when_shared_deal_content_is_empty(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(primary_market_meeting_readiness, "_tcp_port_open", lambda *args, **kwargs: True)
    deal_store.create_deal_package(
        deal_id="DEAL-MEET-READY-001",
        company_name="Ready Robotics",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-MEET-READY-001"
    _write_finance_receipt_and_report(package_dir)
    receipts = deal_store.read_json(package_dir / "phases" / "startup_receipts.json", {})
    finance = receipts["agents"]["siq_ic_finance_auditor"]
    finance.update({
        "shared_hits": 0,
        "shared_connected": True,
        "private_connected": True,
        "collections_connected": True,
        "chat_retrieval_ready": True,
        "chat_retrieval_status": "ready",
        "shared_ready": False,
        "retrieval_status": "blocked",
        "evidence_snapshot_hash": None,
        "gate": {
            "allowed_to_speak": False,
            "blocking_reasons": ["deal_scoped_shared_kb_empty"],
        },
    })
    deal_store.write_json(package_dir / "phases" / "startup_receipts.json", receipts)

    readiness = primary_market_meeting_readiness.build_meeting_readiness(
        "DEAL-MEET-READY-001",
        wiki_root=tmp_path,
    )

    by_profile = {item["profile_id"]: item for item in readiness["profiles"]}
    finance_readiness = by_profile["siq_ic_finance_auditor"]
    assert finance_readiness["startup_receipt"]["shared_connected"] is True
    assert finance_readiness["startup_receipt"]["private_connected"] is True
    assert finance_readiness["service_ready_for_chat"] is True
    assert finance_readiness["chat_blocking_reasons"] == []
    assert finance_readiness["ready_for_formal_task"] is False
    assert "deal_scoped_shared_kb_empty" in finance_readiness["content_warnings"]
    assert "evidence_snapshot_unavailable" in finance_readiness["content_warnings"]
    assert readiness["summary"]["service_ready_for_chat"] == 1
    assert readiness["summary"]["formal_task_ready"] == 0


def test_build_meeting_readiness_missing_deal_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        primary_market_meeting_readiness.build_meeting_readiness(
            "DEAL-NOT-FOUND",
            wiki_root=tmp_path,
            include_runtime=False,
        )


def test_readiness_surfaces_persisted_stale_receipt_without_freshness_field(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-MEET-STALE-001",
        company_name="Stale Robotics",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-MEET-STALE-001"
    deal_store.write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "agents": {
                "siq_ic_legal_scanner": {
                    "receipt_id": "startup-siq_ic_legal_scanner-R1-001",
                    "agent_id": "siq_ic_legal_scanner",
                    "readiness_status": "stale",
                    "stale_reason": "evidence_snapshot_changed",
                    "gate": {
                        "allowed_to_speak": False,
                        "blocking_reasons": ["evidence_snapshot_changed"],
                    },
                }
            }
        },
    )

    receipt = primary_market_meeting_readiness._read_receipt(
        "DEAL-MEET-STALE-001",
        "siq_ic_legal_scanner",
        wiki_root=tmp_path,
    )

    assert receipt["stale"] is True
    assert receipt["blocking_reasons"] == ["evidence_snapshot_changed"]
