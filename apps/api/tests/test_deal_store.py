import asyncio
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from routers import deals  # noqa: E402
from services.ic_openclaw_importer import import_openclaw_project  # noqa: E402

from services import (  # noqa: E402
    deal_agents,
    deal_audit,
    deal_contracts,
    deal_decision,
    deal_disputes,
    deal_documents,
    deal_evidence,
    deal_manifest,
    deal_phase_artifacts,
    deal_reports,
    deal_status,
    deal_store,
    ic_agent_runtime,
    ic_policy,
    ic_startup_retrieval,
    ic_workflow,
)


def test_append_audit_event_serializes_concurrent_read_modify_write(tmp_path):
    deal_id = "DEAL-CONCURRENT-AUDIT"
    deal_store.create_deal_package(deal_id=deal_id, company_name="Concurrent", wiki_root=tmp_path)

    def append(index: int) -> None:
        deal_store.append_audit_event(
            deal_id,
            {"event_id": f"event-{index}"},
            wiki_root=tmp_path,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(append, range(40)))

    package_dir = deal_store.safe_deal_dir(deal_id, wiki_root=tmp_path)
    for relative in ("phases/audit_log.json", "audit/audit_log.json"):
        payload = json.loads((package_dir / relative).read_text(encoding="utf-8"))
        assert {item["event_id"] for item in payload["events"]} == {f"event-{index}" for index in range(40)}


def test_update_json_serializes_concurrent_counter_updates(tmp_path):
    path = tmp_path / "counter.json"
    deal_store.write_json(path, {"count": 0})

    def increment(_: int) -> None:
        deal_store.update_json(path, lambda payload: {"count": payload["count"] + 1})

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(increment, range(40)))
    assert deal_store.read_json(path) == {"count": 40}


def test_ic_r1_concurrent_agent_updates_preserve_reports_and_submissions(tmp_path):
    deal_id = "DEAL-CONCURRENT-R1"
    deal_store.create_deal_package(deal_id=deal_id, company_name="Concurrent R1", wiki_root=tmp_path)
    package_dir = deal_store.safe_deal_dir(deal_id, wiki_root=tmp_path)

    def submit(profile_id: str) -> None:
        ic_agent_runtime._merge_r1_report(package_dir, profile_id, {"agent_id": profile_id})
        ic_agent_runtime._advance_workflow_for_r1_report(package_dir, profile_id)

    with ThreadPoolExecutor(max_workers=len(ic_policy.R1_AGENT_SEQUENCE)) as executor:
        list(executor.map(submit, ic_policy.R1_AGENT_SEQUENCE))

    reports = deal_store.read_json(package_dir / "phases" / "r1_reports.json")
    workflow = deal_store.read_json(package_dir / "phases" / "workflow_state.json")
    assert set(reports) == set(ic_policy.R1_AGENT_SEQUENCE)
    assert set(workflow["phases"]["R1"]["submitted_agents"]) == set(ic_policy.R1_AGENT_SEQUENCE)
    assert workflow["phases"]["R1"]["status"] == "completed"


def test_update_json_uses_optimistic_updated_at_and_preserves_stale_state(tmp_path):
    path = tmp_path / "state.json"
    deal_store.write_json(path, {"updated_at": "v1", "items": []})

    deal_store.update_json(
        path,
        lambda payload: {**payload, "updated_at": "v2", "items": ["fresh"]},
        expected_updated_at="v1",
    )
    with pytest.raises(deal_store.JsonRevisionConflictError, match="expected updated_at='v1'"):
        deal_store.update_json(
            path,
            lambda payload: {**payload, "updated_at": "v3", "items": ["stale"]},
            expected_updated_at="v1",
        )
    assert deal_store.read_json(path) == {"updated_at": "v2", "items": ["fresh"]}


def test_write_json_failure_does_not_corrupt_existing_document(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    deal_store.write_json(path, {"status": "old"})

    def fail_replace(source, target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(deal_store.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        deal_store.write_json(path, {"status": "new"})
    assert deal_store.read_json(path) == {"status": "old"}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_ndjson(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _verified_evidence_rows() -> list[dict]:
    return [
        {
            "evidence_id": "EVID-DEAL-YUSHU-2026-001-000001",
            "evidence_type": "verified",
            "dimension": "business",
            "claim": "business",
        },
        {
            "evidence_id": "EVID-DEAL-YUSHU-2026-001-000002",
            "evidence_type": "verified",
            "dimension": "finance",
            "claim": "finance",
        },
        {
            "evidence_id": "EVID-DEAL-YUSHU-2026-001-000003",
            "evidence_type": "verified",
            "dimension": "legal",
            "claim": "legal",
        },
        {
            "evidence_id": "EVID-DEAL-YUSHU-2026-001-000004",
            "evidence_type": "verified",
            "dimension": "risk",
            "claim": "risk",
        },
    ]


def _write_verified_evidence_gate_pass(package_dir: Path) -> None:
    _write_ndjson(package_dir / "evidence" / "evidence_items.ndjson", _verified_evidence_rows())
    _write_json(
        package_dir / "evidence" / "evidence_snapshot.json",
        {
            "schema_version": "siq_deal_evidence_snapshot_v1",
            "deal_id": package_dir.name,
            "snapshot_hash": "a" * 64,
            "source_ids": [],
            "active_sources": [],
        },
    )


def _write_formal_confirmation_gate_pass(package_dir: Path) -> None:
    decision_path = package_dir / "phases" / "r4_decision.json"
    decision = deal_store.read_json(decision_path, {}) or {}
    decision.update(
        {
            "generation_mode": "model",
            "report_id": "ICRPT-TEST-CONFIRMATION-0001",
            "revision": 1,
            "workflow_run_id": "ICRUN-TEST-CONFIRMATION-0001",
            "evidence_snapshot_hash": "a" * 64,
        }
    )
    _write_json(decision_path, decision)
    _write_json(
        package_dir / "decision" / "report_quality.json",
        {
            "schema_version": "siq_ic_report_quality_v1",
            "report_id": decision["report_id"],
            "report_revision": decision["revision"],
            "evidence_snapshot_hash": decision["evidence_snapshot_hash"],
            "allowed_for_human_confirmation": True,
            "blocking_reasons": [],
        },
    )
    _write_json(
        package_dir / "decision" / "factcheck.json",
        {
            "schema_version": "siq_ic_report_factcheck_v1",
            "report_id": decision["report_id"],
            "report_revision": decision["revision"],
            "evidence_snapshot_hash": decision["evidence_snapshot_hash"],
            "status": "pass",
        },
    )
    _write_json(
        package_dir / "phases" / "ic_workflow_runs.json",
        {
            "schema_version": "siq_ic_workflow_runs_v1",
            "active_workflow_run_id": decision["workflow_run_id"],
            "runs": [
                {
                    "schema_version": "siq_ic_workflow_run_v1",
                    "workflow_run_id": decision["workflow_run_id"],
                    "deal_id": package_dir.name,
                    "status": "active",
                    "evidence_snapshot_hash": decision["evidence_snapshot_hash"],
                }
            ],
        },
    )


def _receipt_payload(agent_id: str, evidence_id: str = "EVID-DEAL-YUSHU-2026-001-000001") -> dict:
    kbref_id = f"KBREF-{agent_id.removeprefix('siq_ic_').replace('_', '-').upper()}-0001"
    return {
        "schema_version": "siq_ic_startup_receipt_v2",
        "receipt_id": f"startup-{agent_id}-R1-001",
        "agent_id": agent_id,
        "round_name": "R1",
        "phase": "R1A",
        "evidence_snapshot_hash": "a" * 64,
        "source_ids": [],
        "query": "宇树科技",
        "project_tag": "DEAL-YUSHU-2026-001",
        "shared_hits": 1,
        "private_hits": 1,
        "milvus_used": True,
        "retrieval_status": "completed",
        "shared_collection": "siq_deal_shared",
        "shared_collections": ["siq_deal_shared"],
        "private_collection": agent_id,
        "private_collections": [agent_id],
        "retrieval_collections": ["siq_deal_shared", agent_id],
        "workspace_rules_read": ["SOUL.md", "AGENTS.md"],
        "gaps": [],
        "evidence_hits": [{"evidence_id": evidence_id}],
        "project_evidence_hits": [{"evidence_id": evidence_id}],
        "background_knowledge_hits": [{"id": f"KB-{agent_id}", "collection": agent_id, "title": "role methodology"}],
        "background_knowledge_refs": [
            {
                "ref_id": kbref_id,
                "collection": agent_id,
                "locator": f"KB-{agent_id}",
                "title": "role methodology",
                "usage": "background",
            }
        ],
        "gate": {"allowed_to_speak": True, "blocking_reasons": []},
        "created_at": "2026-07-03T10:20:00+08:00",
    }


def _r1_model_output(
    agent_id: str,
    *,
    evidence_id: str = "EVID-DEAL-YUSHU-2026-001-000001",
    score: int = 80,
) -> dict:
    role_fields = {
        "siq_ic_strategist": (
            "policy_assessment",
            "cycle_position",
            "capital_flow_signals",
            "strategic_fit",
            "scenario_matrix",
            "exit_window",
        ),
        "siq_ic_sector_expert": (
            "market_sizing",
            "competitor_matrix",
            "technology_routes",
            "value_chain",
            "market_share_evidence",
            "industry_lifecycle",
        ),
    }
    suffix = agent_id.removeprefix("siq_ic_").replace("_", "-").upper()
    claim_id = f"CLM-{suffix}-R1-001"
    kbref_id = f"KBREF-{suffix}-0001"
    return {
        "recommendation": "conditional_support",
        "score": score,
        "confidence": "medium",
        "claims": [
            {
                "claim_id": claim_id,
                "topic": "role_assessment",
                "conclusion": "The cited project Evidence conditionally supports the assessment.",
                "status": "verified",
                "evidence_ids": [evidence_id],
                "counter_evidence_ids": [],
                "calculation_trace_ids": [],
                "background_knowledge_ref_ids": [kbref_id],
                "methodology_ref_ids": [],
                "confidence": "medium",
                "decision_impact": "material",
                "period": "2026",
                "currency": None,
                "unit": None,
            }
        ],
        "scorecard": [
            {
                "dimension": "role_assessment",
                "score": score,
                "weight": 1,
                "rationale": "The score is supported by the cited project Evidence.",
                "claim_ids": [claim_id],
                "evidence_ids": [evidence_id],
                "confidence": "medium",
            }
        ],
        "red_flags": [],
        "open_questions": ["Validate remaining diligence assumptions."],
        "required_followups": ["Refresh project Evidence before final IC."],
        "executive_summary": "The role assessment conditionally supports the project.",
        "methodology": ["role-specific evidence review"],
        "limitations": ["Further diligence remains required."],
        **{field: {"result": "reviewed"} for field in role_fields[agent_id]},
    }


def _write_minimum_complete_deal_contract(package_dir: Path) -> None:
    report_agents = [
        "siq_ic_strategist",
        "siq_ic_sector_expert",
        "siq_ic_finance_auditor",
        "siq_ic_legal_scanner",
        "siq_ic_risk_controller",
        "siq_ic_chairman",
    ]
    evidence_by_agent = {
        "siq_ic_strategist": "EVID-DEAL-YUSHU-2026-001-000001",
        "siq_ic_sector_expert": "EVID-DEAL-YUSHU-2026-001-000001",
        "siq_ic_finance_auditor": "EVID-DEAL-YUSHU-2026-001-000002",
        "siq_ic_legal_scanner": "EVID-DEAL-YUSHU-2026-001-000003",
        "siq_ic_risk_controller": "EVID-DEAL-YUSHU-2026-001-000004",
        "siq_ic_chairman": "EVID-DEAL-YUSHU-2026-001-000001",
    }
    _write_json(
        package_dir / "phases" / "r1_reports.json",
        {
            agent_id: {
                "agent_id": agent_id,
                "round_name": "R1",
                "score": 80,
                "recommendation": "SUPPORT",
                "verified": [{"claim": "verified", "evidence_id": evidence_by_agent[agent_id]}],
                "assumed": [],
                "open_questions": [],
                "startup_receipt_id": f"startup-{agent_id}-R1-001",
                "key_points": ["evidence-backed view"],
                "risk_flags": [],
                "evidence_stats": {"shared": 1, "private": 0, "total": 1},
                "artifact_path": f"discussion/01_R1_{agent_id.removeprefix('siq_ic_')}_report.md",
                "created_at": "2026-07-03T10:30:00+08:00",
                "evidence_ids": [evidence_by_agent[agent_id]],
            }
            for agent_id in report_agents
        },
    )
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "agents": {agent_id: _receipt_payload(agent_id, evidence_by_agent[agent_id]) for agent_id in report_agents},
        },
    )
    _write_ndjson(
        package_dir / "evidence" / "evidence_items.ndjson",
        [
            {
                "evidence_id": "EVID-DEAL-YUSHU-2026-001-000001",
                "evidence_type": "verified",
                "dimension": "business",
                "claim": "business",
            },
            {
                "evidence_id": "EVID-DEAL-YUSHU-2026-001-000002",
                "evidence_type": "verified",
                "dimension": "finance",
                "claim": "finance",
            },
            {
                "evidence_id": "EVID-DEAL-YUSHU-2026-001-000003",
                "evidence_type": "verified",
                "dimension": "legal",
                "claim": "legal",
            },
            {
                "evidence_id": "EVID-DEAL-YUSHU-2026-001-000004",
                "evidence_type": "verified",
                "dimension": "risk",
                "claim": "risk",
            },
        ],
    )
    for agent_id in report_agents:
        (package_dir / "discussion" / f"01_R1_{agent_id.removeprefix('siq_ic_')}_report.md").write_text(
            "\n".join(
                [
                    "# R1",
                    "## 检索结果摘要",
                    "### 共享底稿证据",
                    "### 私有知识库证据",
                    "### 信息缺口清单",
                    "### 检索后观点",
                ]
            ),
            encoding="utf-8",
        )
    _write_json(
        package_dir / "phases" / "r4_decision.json",
        {
            "schema_version": "siq_ic_r4_decision_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "decision": "pass",
            "final_score": 78.55,
            "weighted_agent_score": 84.2,
            "chairman_dimension_score": 78.55,
            "chairman_qualitative_decision": "建议投资但需保护条款",
            "conditions": ["设置 IPO 时间表触发的回购保护"],
            "monitoring_metrics": ["核心客户续约"],
            "human_confirmation": {"status": "pending", "confirmed_by": None, "confirmed_at": None},
            "artifact_paths": {
                "markdown": "decision/IC_DECISION_REPORT.md",
                "html": "decision/IC_DECISION_REPORT.html",
            },
        },
    )
    (package_dir / "decision" / "IC_DECISION_REPORT.md").write_text("# IC Decision\n\n建议投资。", encoding="utf-8")


def test_create_and_read_deal_package(tmp_path):
    summary = deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="杭州宇树科技股份有限公司",
        industry="机器人",
        stage="Pre-IPO",
        wiki_root=tmp_path,
    )

    assert summary["deal_id"] == "DEAL-YUSHU-2026-001"
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    assert (package_dir / "project_meta.json").is_file()
    assert (package_dir / "manifest.json").is_file()
    assert (package_dir / "phases" / "workflow_state.json").is_file()

    detail = deal_store.read_deal_detail("DEAL-YUSHU-2026-001", wiki_root=tmp_path)
    assert detail["project_meta"]["company_name"] == "杭州宇树科技股份有限公司"
    assert detail["workflow"]["current_phase"] == "R0"
    assert deal_store.list_deals(wiki_root=tmp_path)[0]["stage"] == "Pre-IPO"


def test_deal_reports_index_and_read_detail(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="杭州宇树科技股份有限公司",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_json(
        package_dir / "phases" / "r1_reports.json",
        {
            "siq_ic_strategist": {
                "score": 82,
                "recommendation": "SUPPORT",
                "source_root": "/tmp/hidden",
                "created_by": {"id": 7, "username": "analyst", "email": "hide@example.test"},
            }
        },
    )
    (package_dir / "discussion" / "01_R1_strategist_report.md").write_text("# R1\n\n战略窗口明确。", encoding="utf-8")
    _write_ndjson(
        package_dir / "evidence" / "evidence_items.ndjson",
        [{"evidence_id": "EVID-001", "source_root": "/tmp/hidden"}],
    )

    index = deal_reports.list_deal_reports("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    report_paths = {item["path"] for item in index["reports"]}
    assert index["schema_version"] == "siq_deal_reports_index_v1"
    assert "phases/r1_reports.json" in report_paths
    assert "discussion/01_R1_strategist_report.md" in report_paths
    assert "evidence/evidence_items.ndjson" in report_paths
    assert index["counts"]["reports"] >= 4
    assert any(item["path"] == "decision/IC_DECISION_REPORT.md" for item in index["missing_expected"])

    detail = deal_reports.read_deal_report(
        "DEAL-YUSHU-2026-001",
        "phases/r1_reports.json",
        wiki_root=tmp_path,
    )
    assert detail["schema_version"] == "siq_deal_report_detail_v1"
    assert detail["report"]["format"] == "json"
    assert "/tmp/hidden" not in detail["content"]
    assert "hide@example.test" not in detail["content"]
    assert detail["json"]["siq_ic_strategist"]["created_by"] == {"id": 7, "username": "analyst"}

    ndjson = deal_reports.read_deal_report(
        "DEAL-YUSHU-2026-001",
        "evidence/evidence_items.ndjson",
        wiki_root=tmp_path,
    )
    assert ndjson["rows_preview"] == [{"evidence_id": "EVID-001"}]


def test_deal_disputes_summary_tracks_resolution_artifacts_and_redaction(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_json(
        package_dir / "phases" / "r1_5_disputes.json",
        {
            "schema_version": "siq_ic_disputes_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "source_root": "/tmp/secret",
            "disputes": [
                {
                    "dispute_id": "DISP-001",
                    "topic": "估值是否支撑 Pre-IPO 定价",
                    "dimension": "finance",
                    "severity": "medium",
                    "positions": [
                        {"agent_id": "ic_finance_auditor", "evidence_ids": ["EVID-001", "EVID-001"]},
                        {"agent_id": "siq_ic_risk_controller", "evidence_id": "EVID-002"},
                    ],
                    "chairman_ruling": {
                        "agent_id": "siq_ic_chairman",
                        "decision": "resolved_with_conditions",
                        "required_followups": ["补充 IPO 估值区间敏感性分析"],
                        "created_by": {"id": 7, "username": "chair", "email": "hide@example.test"},
                    },
                    "resolved": True,
                },
                {
                    "dispute_id": "DISP-002",
                    "topic": "核心客户集中度是否可接受",
                    "dimension": "risk",
                    "severity": "high",
                    "positions": [
                        {"profile_id": "ic_legal_scanner", "evidence_ids": ["EVID-003"]},
                    ],
                    "resolved": False,
                },
            ],
        },
    )
    (package_dir / deal_disputes.DISPUTES_MARKDOWN_PATH).write_text("# R1.5\n", encoding="utf-8")

    summary = deal_disputes.summarize_deal_disputes("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    assert summary["schema_version"] == "siq_deal_r1_5_disputes_summary_v1"
    assert summary["status"] == "warn"
    assert summary["counts"] == {
        "disputes": 2,
        "resolved": 1,
        "unresolved": 1,
        "positions": 3,
        "rulings": 1,
        "high_severity": 1,
        "artifacts": 2,
    }
    assert summary["artifacts"]["json"]["available"] is True
    assert summary["artifacts"]["markdown"]["available"] is True
    by_id = {item["dispute_id"]: item for item in summary["disputes"]}
    assert by_id["DISP-001"]["agent_ids"] == ["siq_ic_finance_auditor", "siq_ic_risk_controller"]
    assert by_id["DISP-001"]["evidence_ids"] == ["EVID-001", "EVID-002"]
    assert by_id["DISP-001"]["required_followups"] == ["补充 IPO 估值区间敏感性分析"]
    assert "dispute_unresolved:DISP-002" in summary["warnings"]
    payload_text = json.dumps(summary, ensure_ascii=False)
    assert "/tmp/secret" not in payload_text
    assert "hide@example.test" not in payload_text


def test_deal_disputes_summary_marks_missing_without_artifacts(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )

    summary = deal_disputes.summarize_deal_disputes("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    assert summary["schema_version"] == "siq_deal_r1_5_disputes_summary_v1"
    assert summary["status"] == "missing"
    assert summary["counts"]["disputes"] == 0
    assert summary["counts"]["artifacts"] == 0
    assert summary["artifacts"]["json"]["available"] is False
    assert summary["artifacts"]["markdown"]["available"] is False
    assert summary["warnings"] == ["disputes_json_missing"]


def test_deal_disputes_summary_preserves_generation_warnings_and_unknown_agents(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_json(
        package_dir / "phases" / "r1_5_disputes.json",
        {
            "schema_version": "siq_ic_disputes_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "warnings": ["r1_reports_incomplete:siq_ic_sector_expert"],
            "disputes": [
                {
                    "dispute_id": "DISP-001",
                    "topic": "Unknown legacy reviewer note",
                    "dimension": "legacy",
                    "severity": "low",
                    "positions": [{"agent_id": "legacy_external_reviewer", "evidence_ids": ["EVID-001"]}],
                    "chairman_ruling": {"decision": "accepted"},
                    "resolved": True,
                }
            ],
        },
    )
    (package_dir / deal_disputes.DISPUTES_MARKDOWN_PATH).write_text("# R1.5\n", encoding="utf-8")

    summary = deal_disputes.summarize_deal_disputes("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    assert summary["status"] == "warn"
    assert "r1_reports_incomplete:siq_ic_sector_expert" in summary["warnings"]
    assert summary["disputes"][0]["agent_ids"] == ["legacy_external_reviewer"]


def test_identify_deal_disputes_dry_run_and_write(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_json(
        package_dir / "phases" / "r1_reports.json",
        {
            "siq_ic_strategist": {
                "agent_id": "siq_ic_strategist",
                "round_name": "R1",
                "score": 91,
                "recommendation": "support",
                "summary": "Strong strategic fit.",
                "evidence_ids": ["EVID-001"],
            },
            "siq_ic_finance_auditor": {
                "agent_id": "siq_ic_finance_auditor",
                "round_name": "R1",
                "score": 55,
                "recommendation": "reject",
                "summary": "Financial evidence is insufficient.",
                "open_questions": ["Validate gross margin bridge"],
                "risk_flags": ["cash runway"],
                "evidence_ids": ["EVID-002"],
            },
        },
    )

    dry_run = deal_disputes.identify_deal_disputes(
        "DEAL-YUSHU-2026-001",
        dry_run=True,
        wiki_root=tmp_path,
    )

    assert dry_run["schema_version"] == "siq_deal_r1_5_disputes_identification_v1"
    assert dry_run["dry_run"] is True
    assert dry_run["would_write"] is False
    assert dry_run["dispute_count"] == 3
    assert not (package_dir / "phases" / "r1_5_disputes.json").is_file()
    topics = {item["topic"] for item in dry_run["payload"]["disputes"]}
    assert "R1 recommendation divergence" in topics
    assert "R1 unresolved diligence gaps" in topics

    result = deal_disputes.identify_deal_disputes(
        "DEAL-YUSHU-2026-001",
        dry_run=False,
        created_by={"id": 7, "username": "ic-admin"},
        wiki_root=tmp_path,
    )

    assert result["dry_run"] is False
    assert result["would_write"] is True
    assert result["written"] is True
    assert (package_dir / "phases" / "r1_5_disputes.json").is_file()
    assert (package_dir / deal_disputes.DISPUTES_MARKDOWN_PATH).is_file()
    workflow = json.loads((package_dir / "phases" / "workflow_state.json").read_text(encoding="utf-8"))
    assert workflow["current_phase"] == "R1.5"
    assert workflow["status"] == "r1_5_disputes_identified"
    audit = json.loads((package_dir / "audit" / "audit_log.json").read_text(encoding="utf-8"))
    assert audit["events"][-1]["event_type"] == "deal_r1_5_disputes_identified"
    assert result["summary"]["counts"]["disputes"] == 3
    assert result["summary"]["counts"]["unresolved"] == 3

    deal_disputes.rule_deal_dispute(
        "DEAL-YUSHU-2026-001",
        "DISP-DEAL-YUSHU-2026-001-001",
        decision="resolved_with_conditions",
        rationale="Preserve this ruling across re-identification.",
        resolved=True,
        dry_run=False,
        created_by={"id": 7, "username": "chair"},
        wiki_root=tmp_path,
    )

    preserved_preview = deal_disputes.identify_deal_disputes(
        "DEAL-YUSHU-2026-001",
        dry_run=True,
        wiki_root=tmp_path,
    )
    assert preserved_preview["preserve_rulings"] is True
    assert preserved_preview["preserved_ruling_count"] == 1
    preview_by_id = {item["dispute_id"]: item for item in preserved_preview["payload"]["disputes"]}
    assert preview_by_id["DISP-DEAL-YUSHU-2026-001-001"]["resolved"] is True
    assert preview_by_id["DISP-DEAL-YUSHU-2026-001-001"]["chairman_ruling"]["decision"] == "resolved_with_conditions"

    destructive_preview = deal_disputes.identify_deal_disputes(
        "DEAL-YUSHU-2026-001",
        dry_run=True,
        preserve_rulings=False,
        wiki_root=tmp_path,
    )
    destructive_by_id = {item["dispute_id"]: item for item in destructive_preview["payload"]["disputes"]}
    assert destructive_preview["preserved_ruling_count"] == 0
    assert "chairman_ruling" not in destructive_by_id["DISP-DEAL-YUSHU-2026-001-001"]

    rerun = deal_disputes.identify_deal_disputes(
        "DEAL-YUSHU-2026-001",
        dry_run=False,
        wiki_root=tmp_path,
    )
    assert rerun["preserved_ruling_count"] == 1
    assert rerun["summary"]["counts"]["rulings"] == 1
    assert rerun["summary"]["counts"]["unresolved"] == 2
    persisted = json.loads((package_dir / "phases" / "r1_5_disputes.json").read_text(encoding="utf-8"))
    persisted_by_id = {item["dispute_id"]: item for item in persisted["disputes"]}
    assert persisted_by_id["DISP-DEAL-YUSHU-2026-001-001"]["chairman_ruling"]["decision"] == "resolved_with_conditions"


def test_dispute_detector_preserves_structured_material_gaps(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-MATERIAL-GAPS-001",
        company_name="Material gaps",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-MATERIAL-GAPS-001"
    blocking_question = {
        "question": "Complete the customer concentration reconciliation.",
        "blocking": True,
    }
    critical_flag = {
        "severity": "critical",
        "type": "diligence_blocker",
        "description": "Customer identities are not verified.",
    }
    _write_json(
        package_dir / "phases" / "r1_reports.json",
        {
            "siq_ic_strategist": {
                "agent_id": "siq_ic_strategist",
                "score": 70,
                "recommendation": "review",
                "open_questions": [blocking_question],
                "red_flags": [critical_flag],
            }
        },
    )

    result = deal_disputes.identify_deal_disputes(
        "DEAL-MATERIAL-GAPS-001",
        dry_run=True,
        wiki_root=tmp_path,
    )

    gap = next(item for item in result["payload"]["disputes"] if item["dimension"] == "evidence_sufficiency")
    assert gap["severity"] == "high"
    assert gap["positions"][0]["open_questions"] == [blocking_question]
    assert gap["positions"][0]["red_flags"] == [critical_flag]
    assert gap["detection_rules"] == ["blocking_questions_or_material_red_flags_present"]


def test_dispute_detector_does_not_promote_nonblocking_questions(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-NONBLOCKING-GAPS-001",
        company_name="Nonblocking gaps",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-NONBLOCKING-GAPS-001"
    _write_json(
        package_dir / "phases" / "r1_reports.json",
        {
            "siq_ic_strategist": {
                "agent_id": "siq_ic_strategist",
                "score": 70,
                "recommendation": "review",
                "open_questions": [{"question": "Refresh the peer set before R4.", "blocking": False}],
                "red_flags": [{"severity": "low", "description": "Peer multiples can move before pricing."}],
            }
        },
    )

    result = deal_disputes.identify_deal_disputes(
        "DEAL-NONBLOCKING-GAPS-001",
        dry_run=True,
        wiki_root=tmp_path,
    )

    assert not any(item["dimension"] == "evidence_sufficiency" for item in result["payload"]["disputes"])


def test_identify_deal_disputes_missing_r1_reports_blocks_instead_of_clear(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"

    result = deal_disputes.identify_deal_disputes(
        "DEAL-YUSHU-2026-001",
        dry_run=False,
        wiki_root=tmp_path,
    )

    assert result["dispute_count"] == 0
    assert result["warnings"] == ["r1_reports_missing"]
    workflow = json.loads((package_dir / "phases" / "workflow_state.json").read_text(encoding="utf-8"))
    assert workflow["current_phase"] == "R1.5"
    assert workflow["status"] == "r1_5_blocked"
    assert workflow["phases"]["R1.5"]["status"] == "blocked"
    assert workflow["phases"]["R1.5"]["warnings"] == ["r1_reports_missing"]
    assert "completed_at" not in workflow["phases"]["R1.5"]
    assert result["summary"]["status"] == "warn"
    assert "r1_reports_missing" in result["summary"]["warnings"]
    status = deal_status.summarize_deal_status("DEAL-YUSHU-2026-001", wiki_root=tmp_path)
    by_component = {item["id"]: item for item in status["components"]}
    assert by_component["r1_5_disputes"]["blocking"] is True
    assert "r1_reports_missing" in by_component["r1_5_disputes"]["warnings"]


def test_rule_deal_dispute_dry_run_write_and_completion(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_json(
        package_dir / "phases" / "r1_5_disputes.json",
        {
            "schema_version": "siq_ic_disputes_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "disputes": [
                {
                    "dispute_id": "DISP-001",
                    "topic": "Recommendation divergence",
                    "dimension": "committee_alignment",
                    "severity": "high",
                    "positions": [{"agent_id": "siq_ic_strategist", "evidence_ids": ["EVID-001"]}],
                    "resolved": False,
                },
                {
                    "dispute_id": "DISP-002",
                    "topic": "Evidence gap",
                    "dimension": "evidence_sufficiency",
                    "severity": "medium",
                    "positions": [{"agent_id": "siq_ic_finance_auditor", "evidence_ids": ["EVID-002"]}],
                    "resolved": False,
                },
            ],
        },
    )
    (package_dir / deal_disputes.DISPUTES_MARKDOWN_PATH).write_text("# before\n", encoding="utf-8")
    json_before = (package_dir / "phases" / "r1_5_disputes.json").read_text(encoding="utf-8")
    audit_before = (package_dir / "audit" / "audit_log.json").read_text(encoding="utf-8")

    dry_run = deal_disputes.rule_deal_dispute(
        "DEAL-YUSHU-2026-001",
        "DISP-001",
        decision="resolved_with_conditions",
        rationale="Accept strategic view with finance follow-up.",
        required_followups=["Refresh margin bridge"],
        evidence_ids=["EVID-003"],
        resolved=True,
        dry_run=True,
        created_by={"id": 7, "username": "chair", "email": "hide@example.test"},
        wiki_root=tmp_path,
    )

    assert dry_run["schema_version"] == "siq_deal_r1_5_dispute_ruling_response_v1"
    assert dry_run["dry_run"] is True
    assert dry_run["would_write"] is False
    assert dry_run["summary"]["counts"]["resolved"] == 1
    assert dry_run["summary"]["counts"]["unresolved"] == 1
    assert (package_dir / "phases" / "r1_5_disputes.json").read_text(encoding="utf-8") == json_before
    assert (package_dir / "audit" / "audit_log.json").read_text(encoding="utf-8") == audit_before
    assert "hide@example.test" not in json.dumps(dry_run, ensure_ascii=False)

    first_write = deal_disputes.rule_deal_dispute(
        "DEAL-YUSHU-2026-001",
        "DISP-001",
        decision="resolved_with_conditions",
        rationale="Accept strategic view with finance follow-up.",
        required_followups=["Refresh margin bridge"],
        evidence_ids=["EVID-003"],
        resolved=True,
        dry_run=False,
        created_by={"id": 7, "username": "chair"},
        wiki_root=tmp_path,
    )

    assert first_write["written"] is True
    assert first_write["summary"]["counts"]["resolved"] == 1
    assert first_write["summary"]["counts"]["unresolved"] == 1
    disputes_payload = json.loads((package_dir / "phases" / "r1_5_disputes.json").read_text(encoding="utf-8"))
    by_id = {item["dispute_id"]: item for item in disputes_payload["disputes"]}
    assert by_id["DISP-001"]["resolved"] is True
    assert by_id["DISP-001"]["chairman_ruling"]["agent_id"] == "siq_ic_chairman"
    assert by_id["DISP-001"]["chairman_ruling"]["evidence_ids"] == ["EVID-003"]
    assert by_id["DISP-002"]["resolved"] is False
    workflow = json.loads((package_dir / "phases" / "workflow_state.json").read_text(encoding="utf-8"))
    assert workflow["current_phase"] == "R1.5"
    assert workflow["status"] == "r1_5_blocked"
    assert workflow["phases"]["R1.5"]["status"] == "blocked"
    assert workflow["phases"]["R1.5"]["resume_allowed"] is True
    assert workflow["phases"]["R1.5"]["terminal_reason"] == "needs_more_evidence"
    assert workflow["phases"].get("R2", {}).get("status") != "in_progress"
    audit = json.loads((package_dir / "audit" / "audit_log.json").read_text(encoding="utf-8"))
    assert audit["events"][-1]["event_type"] == "deal_r1_5_dispute_ruling_applied"

    with pytest.raises(ValueError, match="already has a chairman_ruling"):
        deal_disputes.rule_deal_dispute(
            "DEAL-YUSHU-2026-001",
            "DISP-001",
            decision="replace",
            rationale="Replacement should remain blocked without overwrite.",
            resolved=True,
            dry_run=False,
            wiki_root=tmp_path,
        )

    final_write = deal_disputes.rule_deal_dispute(
        "DEAL-YUSHU-2026-001",
        "DISP-002",
        decision="resolved_no_followup",
        rationale="Evidence gap accepted for R1.5.",
        resolved=True,
        dry_run=False,
        created_by={"id": 7, "username": "chair"},
        wiki_root=tmp_path,
    )

    assert final_write["summary"]["status"] == "pass"
    assert final_write["summary"]["counts"]["resolved"] == 2
    assert final_write["summary"]["counts"]["unresolved"] == 0
    workflow = json.loads((package_dir / "phases" / "workflow_state.json").read_text(encoding="utf-8"))
    assert workflow["current_phase"] == "R1.5"
    assert workflow["status"] == "r1_5_disputes_resolved"
    assert workflow["phases"]["R1.5"]["status"] == "completed"
    assert workflow["phases"]["R1.5"]["ruling_count"] == 2
    assert workflow["phases"]["R1.5"]["resume_allowed"] is False
    assert workflow["phases"]["R1.5"]["terminal_reason"] is None
    status = deal_status.summarize_deal_status("DEAL-YUSHU-2026-001", wiki_root=tmp_path)
    by_component = {item["id"]: item for item in status["components"]}
    assert by_component["r1_5_disputes"]["blocking"] is False


def test_chairman_ruling_task_and_batch_submission(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_json(
        package_dir / "phases" / "r1_5_disputes.json",
        {
            "schema_version": "siq_ic_disputes_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "disputes": [
                {
                    "dispute_id": "DISP-001",
                    "topic": "Recommendation divergence",
                    "dimension": "committee_alignment",
                    "severity": "high",
                    "agent_ids": ["siq_ic_strategist", "siq_ic_finance_auditor"],
                    "positions": [
                        {
                            "agent_id": "siq_ic_strategist",
                            "evidence_ids": ["EVID-001"],
                            "source_root": "/tmp/hidden",
                        },
                        {"agent_id": "siq_ic_finance_auditor", "evidence_ids": ["EVID-002"]},
                    ],
                    "required_followups": ["Document valuation tie-break rationale"],
                    "resolved": False,
                },
                {
                    "dispute_id": "DISP-002",
                    "topic": "Evidence gap",
                    "dimension": "evidence_sufficiency",
                    "severity": "medium",
                    "positions": [{"agent_id": "siq_ic_legal_scanner", "evidence_ids": ["EVID-003"]}],
                    "resolved": False,
                },
            ],
        },
    )
    (package_dir / deal_disputes.DISPUTES_MARKDOWN_PATH).write_text("# before\n", encoding="utf-8")
    json_before = (package_dir / "phases" / "r1_5_disputes.json").read_text(encoding="utf-8")
    audit_before = (package_dir / "audit" / "audit_log.json").read_text(encoding="utf-8")

    task = deal_disputes.build_chairman_ruling_task("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    assert task["schema_version"] == "siq_deal_r1_5_chairman_task_v1"
    assert task["allowed"] is True
    assert task["dispute_count"] == 2
    assert task["output_contract"]["endpoint"] == "/api/deals/DEAL-YUSHU-2026-001/workflow/disputes/chairman-rulings"
    by_task_id = {item["dispute_id"]: item for item in task["disputes"]}
    assert by_task_id["DISP-001"]["agent_ids"] == ["siq_ic_strategist", "siq_ic_finance_auditor"]
    assert by_task_id["DISP-001"]["evidence_ids"] == ["EVID-001", "EVID-002"]
    assert "/tmp/hidden" not in json.dumps(task, ensure_ascii=False)

    rulings = [
        {
            "dispute_id": "DISP-001",
            "decision": "resolved_with_conditions",
            "rationale": "Accept strategy view with finance sensitivity follow-up.",
            "required_followups": ["Refresh valuation sensitivity"],
            "evidence_ids": ["EVID-001", "EVID-002"],
            "resolved": True,
        },
        {
            "dispute_id": "DISP-002",
            "ruling_text": "resolved_no_followup",
            "rationale": "Legal evidence gap is immaterial for R2.",
            "evidence_ids": ["EVID-003"],
            "is_approved": True,
        },
    ]
    with pytest.raises(ValueError, match="ruling resolved is required"):
        deal_disputes.submit_chairman_rulings(
            "DEAL-YUSHU-2026-001",
            rulings=[
                {
                    "dispute_id": "DISP-001",
                    "decision": "resolved_with_conditions",
                    "rationale": "Missing explicit resolution should be rejected.",
                }
            ],
            dry_run=True,
            wiki_root=tmp_path,
        )

    dry_run = deal_disputes.submit_chairman_rulings(
        "DEAL-YUSHU-2026-001",
        rulings=rulings,
        dry_run=True,
        created_by={"id": 7, "username": "chair", "email": "hide@example.test"},
        wiki_root=tmp_path,
    )

    assert dry_run["schema_version"] == "siq_deal_r1_5_chairman_ruling_submission_v1"
    assert dry_run["dry_run"] is True
    assert dry_run["would_write"] is False
    assert dry_run["submitted_count"] == 2
    assert dry_run["can_proceed_to_r2"] is True
    assert dry_run["summary"]["status"] == "pass"
    assert dry_run["summary"]["counts"]["resolved"] == 2
    assert (package_dir / "phases" / "r1_5_disputes.json").read_text(encoding="utf-8") == json_before
    assert (package_dir / "audit" / "audit_log.json").read_text(encoding="utf-8") == audit_before
    assert "hide@example.test" not in json.dumps(dry_run, ensure_ascii=False)
    assert "/tmp/hidden" not in json.dumps(dry_run, ensure_ascii=False)

    write = deal_disputes.submit_chairman_rulings(
        "DEAL-YUSHU-2026-001",
        rulings=rulings,
        dry_run=False,
        created_by={"id": 7, "username": "chair"},
        wiki_root=tmp_path,
    )

    assert write["written"] is True
    assert write["submitted_count"] == 2
    assert write["can_proceed_to_r2"] is True
    assert write["summary"]["status"] == "pass"
    assert write["summary"]["counts"]["unresolved"] == 0
    persisted = json.loads((package_dir / "phases" / "r1_5_disputes.json").read_text(encoding="utf-8"))
    by_id = {item["dispute_id"]: item for item in persisted["disputes"]}
    assert by_id["DISP-001"]["chairman_ruling"]["decision"] == "resolved_with_conditions"
    assert by_id["DISP-002"]["chairman_ruling"]["decision"] == "resolved_no_followup"
    workflow = json.loads((package_dir / "phases" / "workflow_state.json").read_text(encoding="utf-8"))
    assert workflow["current_phase"] == "R1.5"
    assert workflow["status"] == "r1_5_disputes_resolved"
    assert workflow["phases"]["R1.5"]["status"] == "completed"
    audit = json.loads((package_dir / "audit" / "audit_log.json").read_text(encoding="utf-8"))
    assert audit["events"][-1]["event_type"] == "deal_r1_5_chairman_rulings_submitted"
    assert audit["events"][-1]["submitted_count"] == 2
    assert audit["events"][-1]["dispute_ids"] == ["DISP-001", "DISP-002"]
    assert audit["events"][-1]["can_proceed_to_r2"] is True
    assert audit["events"][-1]["json_path"] == deal_disputes.DISPUTES_JSON_PATH
    assert audit["events"][-1]["markdown_path"] == deal_disputes.DISPUTES_MARKDOWN_PATH
    assert audit["events"][-1]["created_by"] == {"id": 7, "username": "chair"}

    completed_task = deal_disputes.build_chairman_ruling_task("DEAL-YUSHU-2026-001", wiki_root=tmp_path)
    assert completed_task["allowed"] is False
    assert completed_task["blocking_reasons"] == ["no_unresolved_disputes"]


def test_generate_deal_dispute_rulings_dry_run_write_and_skip_existing(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_json(
        package_dir / "phases" / "r1_5_disputes.json",
        {
            "schema_version": "siq_ic_disputes_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "disputes": [
                {
                    "dispute_id": "DISP-001",
                    "topic": "Recommendation divergence",
                    "dimension": "committee_alignment",
                    "severity": "high",
                    "positions": [
                        {"agent_id": "siq_ic_strategist", "evidence_ids": ["EVID-001"]},
                        {"agent_id": "siq_ic_finance_auditor", "evidence_ids": ["EVID-002"]},
                    ],
                    "required_followups": ["Document valuation tie-break rationale"],
                    "resolved": False,
                },
                {
                    "dispute_id": "DISP-002",
                    "topic": "Evidence gap",
                    "dimension": "evidence_sufficiency",
                    "severity": "medium",
                    "positions": [{"agent_id": "siq_ic_legal_scanner", "evidence_ids": ["EVID-003"]}],
                    "resolved": False,
                },
                {
                    "dispute_id": "DISP-003",
                    "topic": "Already ruled",
                    "dimension": "risk",
                    "severity": "low",
                    "positions": [{"agent_id": "siq_ic_risk_controller", "evidence_ids": ["EVID-004"]}],
                    "resolved": True,
                    "chairman_ruling": {"decision": "keep_existing", "resolved": True},
                },
            ],
        },
    )
    (package_dir / deal_disputes.DISPUTES_MARKDOWN_PATH).write_text("# before\n", encoding="utf-8")
    json_before = (package_dir / "phases" / "r1_5_disputes.json").read_text(encoding="utf-8")
    audit_before = (package_dir / "audit" / "audit_log.json").read_text(encoding="utf-8")

    dry_run = deal_disputes.generate_deal_dispute_rulings(
        "DEAL-YUSHU-2026-001",
        dry_run=True,
        created_by={"id": 7, "username": "chair", "email": "hide@example.test"},
        wiki_root=tmp_path,
    )

    assert dry_run["schema_version"] == "siq_deal_r1_5_dispute_ruling_generation_v1"
    assert dry_run["dry_run"] is True
    assert dry_run["would_write"] is False
    assert dry_run["generated_count"] == 2
    assert dry_run["skipped_count"] == 1
    assert dry_run["summary"]["counts"]["resolved"] == 3
    assert dry_run["rulings"][0]["ruling"]["generation_mode"] == "deterministic_r1_5_dispute_scan_v1"
    assert dry_run["rulings"][0]["ruling"]["decision"] == "resolved_with_conditions"
    assert dry_run["rulings"][0]["ruling"]["required_followups"] == ["Document valuation tie-break rationale"]
    assert "hide@example.test" not in json.dumps(dry_run, ensure_ascii=False)
    assert (package_dir / "phases" / "r1_5_disputes.json").read_text(encoding="utf-8") == json_before
    assert (package_dir / "audit" / "audit_log.json").read_text(encoding="utf-8") == audit_before

    write = deal_disputes.generate_deal_dispute_rulings(
        "DEAL-YUSHU-2026-001",
        dry_run=False,
        created_by={"id": 7, "username": "chair"},
        wiki_root=tmp_path,
    )

    assert write["written"] is True
    assert write["generated_count"] == 2
    assert write["summary"]["status"] == "pass"
    assert write["summary"]["counts"]["resolved"] == 3
    assert write["summary"]["counts"]["unresolved"] == 0
    persisted = json.loads((package_dir / "phases" / "r1_5_disputes.json").read_text(encoding="utf-8"))
    by_id = {item["dispute_id"]: item for item in persisted["disputes"]}
    assert by_id["DISP-001"]["chairman_ruling"]["agent_id"] == "siq_ic_chairman"
    assert by_id["DISP-001"]["chairman_ruling"]["evidence_ids"] == ["EVID-001", "EVID-002"]
    assert by_id["DISP-002"]["chairman_ruling"]["required_followups"] == ["Resolve evidence sufficiency gaps before R2"]
    assert by_id["DISP-003"]["chairman_ruling"]["decision"] == "keep_existing"
    workflow = json.loads((package_dir / "phases" / "workflow_state.json").read_text(encoding="utf-8"))
    assert workflow["current_phase"] == "R1.5"
    assert workflow["status"] == "r1_5_disputes_resolved"
    assert workflow["phases"]["R1.5"]["status"] == "completed"
    audit = json.loads((package_dir / "audit" / "audit_log.json").read_text(encoding="utf-8"))
    assert audit["events"][-1]["event_type"] == "deal_r1_5_dispute_rulings_generated"


def test_deal_phase_artifacts_summary_tracks_r2_and_r3_skip(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_json(
        package_dir / "phases" / "r2_reports.json",
        {
            "schema_version": "siq_ic_r2_reports_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "source_root": "/tmp/secret",
            "reports": {
                "ic_finance_auditor": {
                    "summary": "补充估值敏感性分析",
                    "recommendation": "SUPPORT_WITH_TERMS",
                    "score": 81,
                    "created_by": {"id": 7, "username": "analyst", "email": "hide@example.test"},
                }
            },
        },
    )
    (package_dir / deal_phase_artifacts.R2_MARKDOWN_PATH).write_text("# R2\n", encoding="utf-8")
    _write_json(
        package_dir / "phases" / "r3_reports.json",
        {
            "schema_version": "siq_ic_r3_reports_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "mode": "skip",
            "reports": {},
        },
    )

    summary = deal_phase_artifacts.summarize_deal_phase_artifacts("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    assert summary["schema_version"] == "siq_deal_phase_artifacts_summary_v1"
    assert summary["status"] == "warn"
    assert summary["counts"]["phases"] == 6
    assert summary["counts"]["available_json"] == 3
    assert summary["counts"]["available_markdown"] == 1
    by_phase = {item["phase"]: item for item in summary["phases"]}
    assert by_phase["R0"]["status"] == "pass"
    assert by_phase["R2"]["status"] == "pass"
    assert by_phase["R2"]["counts"]["items"] == 1
    assert by_phase["R2"]["items_preview"][0]["agent_id"] == "siq_ic_finance_auditor"
    assert by_phase["R2"]["items_preview"][0]["summary"] == "补充估值敏感性分析"
    assert by_phase["R3"]["status"] == "pass"
    assert by_phase["R3"]["mode"] == "skip"
    assert by_phase["R3"]["skip_reason"] is None
    assert by_phase["R3"]["blocking"] is False
    payload_text = json.dumps(summary, ensure_ascii=False)
    assert "/tmp/secret" not in payload_text
    assert "hide@example.test" not in payload_text


def test_deal_phase_artifacts_summary_blocks_r3_skip_without_marker_payload(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_json(package_dir / "phases" / "r3_reports.json", {"mode": "skip"})

    summary = deal_phase_artifacts.summarize_deal_phase_artifacts("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    by_phase = {item["phase"]: item for item in summary["phases"]}
    assert by_phase["R3"]["status"] == "warn"
    assert by_phase["R3"]["blocking"] is True
    assert "r3_skip_reason_missing" in by_phase["R3"]["warnings"]
    assert summary["counts"]["blocking"] == 1


def test_deal_r1_agent_reports_summary_tracks_contract_and_artifacts(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="杭州宇树科技股份有限公司",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "agents": {
                "siq_ic_strategist": {
                    "agent_id": "siq_ic_strategist",
                    "receipt_id": "startup-siq_ic_strategist-R1-001",
                },
                "siq_ic_finance_auditor": {
                    "agent_id": "siq_ic_finance_auditor",
                    "receipt_id": "startup-siq_ic_finance_auditor-R1-001",
                },
            },
        },
    )
    _write_json(
        package_dir / "phases" / "r1_reports.json",
        {
            "ic_strategist": {
                "agent_id": "ic_strategist",
                "round_name": "R1",
                "score": 82,
                "recommendation": "SUPPORT",
                "verified": ["增长率"],
                "assumed": ["退出窗口"],
                "open_questions": ["核心客户续约"],
                "startup_receipt_id": "startup-siq_ic_strategist-R1-001",
                "summary": "战略窗口明确",
                "key_points": ["政策窗口"],
                "risk_flags": ["估值偏高"],
                "evidence_stats": {"shared": 1, "private": 0, "total": 1},
                "artifact_path": "discussion/01_R1_strategist_report.md",
                "created_at": "2026-07-03T10:30:00+08:00",
                "source_root": "/tmp/hidden",
            }
        },
    )
    (package_dir / "discussion" / "01_R1_strategist_report.md").write_text(
        "\n".join(
            [
                "# Strategist",
                "## 检索结果摘要",
                "### 共享底稿证据",
                "| # | 来源 | 核心事实 | 可信度 |",
                "|---|------|---------|--------|",
                "### 私有知识库证据",
                "| # | 来源 | 核心事实 | 可信度 |",
                "|---|------|---------|--------|",
                "### 信息缺口清单",
                "- [ ] 续约",
                "### 检索后观点",
                "战略窗口明确。",
            ]
        ),
        encoding="utf-8",
    )

    summary = deal_reports.list_r1_agent_reports("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    assert summary["schema_version"] == "siq_deal_r1_reports_summary_v1"
    assert summary["counts"]["agents"] == 6
    assert summary["counts"]["reports"] == 1
    assert summary["counts"]["receipts"] == 2
    assert summary["counts"]["pass"] == 1
    assert {
        "field": "risk_flags",
        "aliases": ["risk_flags", "risks"],
    } in summary["contract_field_groups"]
    by_agent = {item["agent_id"]: item for item in summary["agents"]}
    strategist = by_agent["siq_ic_strategist"]
    assert strategist["status"] == "pass"
    assert strategist["startup_receipt_linkage"] == "match"
    assert strategist["missing_required_fields"] == []
    assert strategist["missing_contract_fields"] == ["deal_id"]
    assert strategist["markdown_section_status"] == "pass"
    assert strategist["artifact_available"] is True
    finance = by_agent["siq_ic_finance_auditor"]
    assert finance["status"] == "missing"
    assert finance["has_startup_receipt"] is True
    assert finance["startup_receipt_linkage"] == "receipt_only"
    assert "score" in finance["missing_required_fields"]
    assert "risk_flags" in finance["missing_contract_fields"]
    assert "source_root" not in json.dumps(summary, ensure_ascii=False)
    assert "/tmp/hidden" not in json.dumps(summary, ensure_ascii=False)


def test_deal_r2_agent_reports_summary_tracks_openclaw_keyed_reports(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="杭州宇树科技股份有限公司",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_json(
        package_dir / "phases" / "r2_reports.json",
        {
            "ic_finance_auditor": {
                "round_name": "R2",
                "r1_score": 79,
                "r2_score": 82,
                "score_change": 3,
                "recommendation": "SUPPORT_WITH_TERMS",
                "confidence": "Medium",
                "summary": "估值敏感性分析补充完成。",
                "revisions": ["补充 IPO 估值区间敏感性分析"],
                "verified": ["估值区间"],
                "assumed": ["退出窗口"],
                "open_questions": [],
                "key_points": ["条款保护"],
                "source_root": "/tmp/hidden",
                "created_by": {"id": 7, "username": "analyst", "email": "hide@example.test"},
            }
        },
    )
    (package_dir / deal_reports.R2_REPORT_ARTIFACT_PATH).write_text("# R2\n", encoding="utf-8")

    summary = deal_reports.list_r2_agent_reports("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    assert summary["schema_version"] == "siq_deal_r2_reports_summary_v1"
    assert summary["counts"]["agents"] == 5
    assert summary["counts"]["reports"] == 1
    assert summary["counts"]["pass"] == 1
    assert summary["counts"]["missing"] == 4
    assert summary["counts"]["revisions"] == 1
    assert summary["artifact_available"] is True
    by_agent = {item["agent_id"]: item for item in summary["agents"]}
    finance = by_agent["siq_ic_finance_auditor"]
    assert finance["status"] == "pass"
    assert finance["r1_score"] == 79
    assert finance["r2_score"] == 82
    assert finance["score_change"] == 3
    assert finance["revision_count"] == 1
    assert finance["artifact_available"] is True
    assert by_agent["siq_ic_risk_controller"]["status"] == "missing"
    payload_text = json.dumps(summary, ensure_ascii=False)
    assert "/tmp/hidden" not in payload_text
    assert "hide@example.test" not in payload_text


def test_deal_r3_review_summary_accepts_openclaw_skip_envelope(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="杭州宇树科技股份有限公司",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_json(
        package_dir / "phases" / "r3_reports.json",
        {
            "mode": "skip",
            "reports": {},
            "source_root": "/tmp/hidden",
        },
    )
    (package_dir / deal_reports.R3_REVIEW_ARTIFACT_PATH).write_text("# R3\n\n模式：skip\n", encoding="utf-8")

    summary = deal_reports.summarize_r3_review("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    assert summary["schema_version"] == "siq_deal_r3_review_summary_v1"
    assert summary["status"] == "pass"
    assert summary["mode"] == "skip"
    assert summary["skipped"] is True
    assert summary["counts"]["reports"] == 0
    assert summary["counts"]["artifacts_available"] == 2
    assert summary["warnings"] == []
    assert summary["artifacts"]["markdown"]["available"] is True
    assert "/tmp/hidden" not in json.dumps(summary, ensure_ascii=False)


def test_deal_r4_decision_summary_tracks_contract_and_artifacts(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="杭州宇树科技股份有限公司",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_json(
        package_dir / "phases" / "r4_decision.json",
        {
            "schema_version": "siq_ic_r4_decision_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "decision": "pass",
            "final_score": 78.55,
            "weighted_agent_score": 84.2,
            "chairman_dimension_score": 78.55,
            "chairman_qualitative_decision": "建议投资，但需设置估值和退出保护条款",
            "conditions": ["设置 IPO 时间表触发的回购保护"],
            "monitoring_metrics": ["核心客户续约"],
            "human_confirmation": {
                "status": "confirmed",
                "confirmed_by": {"id": 7, "username": "chair", "email": "hide@example.test"},
                "confirmed_at": "2026-07-03T10:30:00+08:00",
            },
            "artifact_paths": {
                "markdown": "decision/IC_DECISION_REPORT.md",
                "html": "decision/IC_DECISION_REPORT.html",
            },
            "source_root": "/tmp/hidden",
        },
    )
    markdown_path = package_dir / "decision" / "IC_DECISION_REPORT.md"
    html_path = package_dir / "decision" / "IC_DECISION_REPORT.html"
    markdown_path.write_text("# IC Decision\n\n建议投资。", encoding="utf-8")
    html_path.write_text("<h1>IC Decision</h1>", encoding="utf-8")

    summary = deal_reports.summarize_r4_decision("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    assert summary["schema_version"] == "siq_deal_r4_decision_summary_v1"
    assert summary["status"] == "pass"
    assert summary["missing_required_fields"] == []
    assert summary["missing_advisory_fields"] == []
    assert summary["decision"] == {
        "value": "pass",
        "qualitative": "建议投资，但需设置估值和退出保护条款",
    }
    assert summary["scoring"] == {
        "weighted_agent_score": 84.2,
        "chairman_dimension_score": 78.55,
        "final_score": 78.55,
    }
    assert summary["decision_value"] == "pass"
    assert summary["weighted_agent_score"] == 84.2
    assert summary["chairman_dimension_score"] == 78.55
    assert summary["chairman_qualitative_decision"] == "建议投资，但需设置估值和退出保护条款"
    assert summary["human_confirmation"]["confirmed"] is True
    assert summary["human_confirmation"]["confirmed_by"] == {"id": 7, "username": "chair"}
    assert summary["artifacts"]["markdown"]["available"] is True
    assert summary["artifacts"]["markdown"]["sha256"] == _sha256(markdown_path)
    assert summary["artifacts"]["html"]["available"] is True
    assert summary["artifacts"]["raw"] == {
        "markdown": "decision/IC_DECISION_REPORT.md",
        "html": "decision/IC_DECISION_REPORT.html",
    }
    assert "source_root" not in json.dumps(summary, ensure_ascii=False)
    assert "/tmp/hidden" not in json.dumps(summary, ensure_ascii=False)
    assert "hide@example.test" not in json.dumps(summary, ensure_ascii=False)


def test_deal_r4_decision_summary_warns_on_incomplete_contract(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="杭州宇树科技股份有限公司",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_json(
        package_dir / "phases" / "r4_decision.json",
        {
            "schema_version": "siq_ic_r4_decision_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "decision": "watch",
            "weighted_agent_score": 71.2,
        },
    )

    summary = deal_reports.summarize_r4_decision("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    assert summary["status"] == "warn"
    assert summary["missing_required_fields"] == [
        "chairman_dimension_score",
        "chairman_qualitative_decision",
    ]
    assert "artifact_paths" in summary["missing_advisory_fields"]
    assert summary["artifacts"]["markdown"]["available"] is False


def test_deal_decision_human_confirmation_dry_run_does_not_write(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="杭州宇树科技股份有限公司",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_json(
        package_dir / "phases" / "r4_decision.json",
        {
            "schema_version": "siq_ic_r4_decision_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "decision": "pass",
            "final_score": 78.55,
            "weighted_agent_score": 84.2,
            "chairman_dimension_score": 78.55,
            "chairman_qualitative_decision": "建议投资",
            "human_confirmation": {"status": "pending", "confirmed_by": None, "confirmed_at": None},
            "artifact_paths": {"markdown": "decision/IC_DECISION_REPORT.md"},
        },
    )
    (package_dir / "decision" / "IC_DECISION_REPORT.md").write_text("# IC Decision\n", encoding="utf-8")
    _write_formal_confirmation_gate_pass(package_dir)

    result = deal_decision.update_human_confirmation(
        "DEAL-YUSHU-2026-001",
        status="confirmed",
        confirmed_by={"id": 7, "username": "ic-admin", "email": "hide@example.test"},
        dry_run=True,
        wiki_root=tmp_path,
    )

    assert result["schema_version"] == "siq_deal_r4_human_confirmation_update_v2"
    assert result["dry_run"] is True
    assert result["would_write"] is False
    assert result["human_confirmation"]["status"] == "confirmed"
    assert result["human_confirmation"]["confirmed_by"] == {"id": 7, "username": "ic-admin"}
    assert result["human_confirmation"]["report_id"] == "ICRPT-TEST-CONFIRMATION-0001"
    assert result["human_confirmation"]["report_revision"] == 1
    assert len(result["human_confirmation"]["decision_sha256"]) == 64
    stored = deal_store.read_json(package_dir / "phases" / "r4_decision.json", {})
    assert stored["human_confirmation"]["status"] == "pending"
    audit = deal_store.read_json(package_dir / "phases" / "audit_log.json", {})
    assert audit["events"] == []
    assert "hide@example.test" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize(
    ("artifact_name", "blocking_reason"),
    [
        ("report_quality.json", "report_quality_report_revision_mismatch"),
        ("factcheck.json", "factcheck_report_revision_mismatch"),
    ],
)
def test_deal_decision_human_confirmation_rejects_stale_review_revision(
    tmp_path,
    artifact_name,
    blocking_reason,
):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="杭州宇树科技股份有限公司",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_json(
        package_dir / "phases" / "r4_decision.json",
        {
            "schema_version": "siq_ic_r4_decision_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "human_confirmation": {"status": "pending"},
        },
    )
    _write_formal_confirmation_gate_pass(package_dir)
    artifact_path = package_dir / "decision" / artifact_name
    artifact = deal_store.read_json(artifact_path, {}) or {}
    artifact["report_revision"] = 2
    _write_json(artifact_path, artifact)

    with pytest.raises(ValueError, match=blocking_reason):
        deal_decision.update_human_confirmation(
            "DEAL-YUSHU-2026-001",
            status="confirmed",
            confirmed_by={"id": 7, "username": "ic-admin"},
            dry_run=False,
            wiki_root=tmp_path,
        )

    stored = deal_store.read_json(package_dir / "phases" / "r4_decision.json", {})
    assert stored["human_confirmation"]["status"] == "pending"


def test_deal_decision_human_confirmation_writes_audit_event(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="杭州宇树科技股份有限公司",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_json(
        package_dir / "phases" / "r4_decision.json",
        {
            "schema_version": "siq_ic_r4_decision_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "weighted_agent_score": 84.2,
            "chairman_dimension_score": 78.55,
            "chairman_qualitative_decision": "建议投资",
            "human_confirmation": {"status": "pending", "confirmed_by": None, "confirmed_at": None},
            "artifact_paths": {"markdown": "decision/IC_DECISION_REPORT.md"},
        },
    )
    (package_dir / "decision" / "IC_DECISION_REPORT.md").write_text("# IC Decision\n", encoding="utf-8")
    _write_formal_confirmation_gate_pass(package_dir)

    result = deal_decision.update_human_confirmation(
        "DEAL-YUSHU-2026-001",
        status="overridden",
        confirmed_by={"id": 7, "username": "ic-admin"},
        override_reason="估值保护条款未满足",
        override_decision="reject",
        override_score=65,
        dry_run=False,
        wiki_root=tmp_path,
    )

    assert result["dry_run"] is False
    assert result["would_write"] is True
    stored = deal_store.read_json(package_dir / "phases" / "r4_decision.json", {})
    assert stored["human_confirmation"]["status"] == "overridden"
    assert stored["human_confirmation"]["override_decision"] == "reject"
    workflow = deal_store.read_json(package_dir / "phases" / "workflow_state.json", {})
    assert workflow["phases"]["R4"]["human_confirmation_status"] == "overridden"
    assert workflow["phases"]["R4"]["manual_override_decision"] == "reject"
    assert workflow["final_decision"] == "manual_override"
    assert workflow["final_score"] == 65
    project_meta = deal_store.read_json(package_dir / "project_meta.json", {})
    assert project_meta["human_confirmation_status"] == "overridden"
    assert project_meta["final_decision"] == "manual_override"
    assert project_meta["final_score"] == 65
    assert project_meta["manual_override_decision"] == "reject"
    audit = deal_store.read_json(package_dir / "phases" / "audit_log.json", {})
    assert audit["events"][-1]["event_type"] == "r4_human_confirmation_updated"
    assert audit["events"][-1]["status"] == "overridden"
    assert audit["events"][-1]["override_decision"] == "reject"
    assert audit["events"][-1]["report_id"] == "ICRPT-TEST-CONFIRMATION-0001"
    workflow_runs = deal_store.read_json(package_dir / "phases" / "ic_workflow_runs.json", {})
    assert workflow_runs["runs"][0]["status"] == "completed"
    assert workflow_runs["runs"][0]["completion"]["decision_sha256"] == stored["human_confirmation"]["decision_sha256"]
    assert result["decision_contract"]["human_confirmation"]["status"] == "overridden"
    assert result["decision_contract"]["human_confirmation"]["confirmed"] is False


def test_deal_decision_human_confirmation_needs_revision_syncs_review_state(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="杭州宇树科技股份有限公司",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_json(
        package_dir / "phases" / "r4_decision.json",
        {
            "schema_version": "siq_ic_r4_decision_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "decision": "watch",
            "final_score": 72.5,
            "weighted_agent_score": 84.2,
            "chairman_dimension_score": 72.5,
            "chairman_qualitative_decision": "补证后再决策",
            "human_confirmation": {"status": "pending", "confirmed_by": None, "confirmed_at": None},
            "artifact_paths": {"markdown": "decision/IC_DECISION_REPORT.md"},
        },
    )
    (package_dir / "decision" / "IC_DECISION_REPORT.md").write_text("# IC Decision\n", encoding="utf-8")
    _write_formal_confirmation_gate_pass(package_dir)

    result = deal_decision.update_human_confirmation(
        "DEAL-YUSHU-2026-001",
        status="needs_revision",
        confirmed_by={"id": 7, "username": "ic-admin"},
        override_reason="R0 证据链仍缺少客户合同页码引用",
        dry_run=False,
        wiki_root=tmp_path,
    )

    assert result["dry_run"] is False
    stored = deal_store.read_json(package_dir / "phases" / "r4_decision.json", {})
    assert stored["human_confirmation"]["status"] == "needs_revision"
    assert stored["human_confirmation"]["confirmed"] is False
    assert stored["human_confirmation"]["override_reason"] == "R0 证据链仍缺少客户合同页码引用"
    workflow = deal_store.read_json(package_dir / "phases" / "workflow_state.json", {})
    assert workflow["phases"]["R4"]["human_confirmation_status"] == "needs_revision"
    assert "final_decision" not in workflow
    project_meta = deal_store.read_json(package_dir / "project_meta.json", {})
    assert project_meta["human_confirmation_status"] == "needs_revision"
    assert project_meta.get("final_decision") in (None, "")
    audit = deal_store.read_json(package_dir / "phases" / "audit_log.json", {})
    assert audit["events"][-1]["event_type"] == "r4_human_confirmation_updated"
    assert audit["events"][-1]["status"] == "needs_revision"
    assert result["decision_contract"]["human_confirmation"]["status"] == "needs_revision"


def test_deal_decision_human_confirmation_confirmed_syncs_final_state(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="杭州宇树科技股份有限公司",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_json(
        package_dir / "phases" / "r4_decision.json",
        {
            "schema_version": "siq_ic_r4_decision_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "decision": "pass",
            "final_score": 78.55,
            "weighted_agent_score": 84.2,
            "chairman_dimension_score": 78.55,
            "chairman_qualitative_decision": "建议投资",
            "human_confirmation": {"status": "pending", "confirmed_by": None, "confirmed_at": None},
            "artifact_paths": {"markdown": "decision/IC_DECISION_REPORT.md"},
        },
    )
    (package_dir / "decision" / "IC_DECISION_REPORT.md").write_text("# IC Decision\n", encoding="utf-8")
    _write_formal_confirmation_gate_pass(package_dir)

    result = deal_decision.update_human_confirmation(
        "DEAL-YUSHU-2026-001",
        status="confirmed",
        confirmed_by={"id": 7, "username": "ic-admin"},
        dry_run=False,
        wiki_root=tmp_path,
    )

    assert result["decision_contract"]["human_confirmation"]["status"] == "confirmed"
    workflow = deal_store.read_json(package_dir / "phases" / "workflow_state.json", {})
    assert workflow["phases"]["R4"]["human_confirmation_status"] == "confirmed"
    assert workflow["final_decision"] == "pass"
    assert workflow["final_score"] == 78.55
    workflow_runs = deal_store.read_json(package_dir / "phases" / "ic_workflow_runs.json", {})
    assert workflow_runs["runs"][0]["status"] == "completed"
    assert workflow_runs["runs"][0]["completion"]["report_id"] == "ICRPT-TEST-CONFIRMATION-0001"
    project_meta = deal_store.read_json(package_dir / "project_meta.json", {})
    assert project_meta["human_confirmation_status"] == "confirmed"
    assert project_meta["final_decision"] == "pass"
    assert project_meta["final_score"] == 78.55


def test_deal_audit_summary_tracks_sources_counts_and_redaction(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="杭州宇树科技股份有限公司",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    payload = {
        "events": [
            {
                "event_type": "deal_created",
                "created_at": "2026-07-03T09:00:00+08:00",
                "created_by": {"id": 7, "username": "analyst", "email": "hide@example.test"},
            },
            {
                "event_type": "openclaw_imported",
                "created_at": "2026-07-03T09:10:00+08:00",
                "source_root": "/tmp/hidden",
            },
            {
                "event_type": "r4_decision_generated",
                "created_at": "2026-07-03T09:20:00+08:00",
                "confirmed_by": {"id": 8, "username": "chair", "email": "chair@example.test"},
            },
            {
                "event_type": "r4_human_confirmation_updated",
                "status": "overridden",
                "created_at": "2026-07-03T09:30:00+08:00",
                "confirmed_by": {"id": 8, "username": "chair", "email": "chair@example.test"},
            },
        ]
    }
    _write_json(package_dir / "audit" / "audit_log.json", payload)
    _write_json(package_dir / "phases" / "audit_log.json", payload)

    summary = deal_audit.summarize_deal_audit("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    assert summary["schema_version"] == "siq_deal_audit_summary_v1"
    assert summary["status"] == "pass"
    assert summary["sources"]["selected"] == "primary"
    assert summary["sources"]["consistency"] == "match"
    assert summary["sources"]["primary"]["event_count"] == 4
    assert summary["counts"]["events"] == 4
    assert summary["counts"]["event_types"]["openclaw_imported"] == 1
    assert summary["counts"]["human_confirmation"] == 1
    assert summary["counts"]["manual_override"] == 1
    assert summary["latest_event"]["event_type"] == "r4_human_confirmation_updated"
    assert all(item["present"] for item in summary["required_event_status"] if item["required"])
    assert any(item["event_type"] == "deal_created" and item["required"] for item in summary["required_event_status"])
    serialized = json.dumps(summary, ensure_ascii=False)
    assert "source_root" not in serialized
    assert "/tmp/hidden" not in serialized
    assert "hide@example.test" not in serialized
    assert "chair@example.test" not in serialized


def test_deal_audit_summary_warns_on_mismatch_and_missing_required_events(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="杭州宇树科技股份有限公司",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_json(
        package_dir / "audit" / "audit_log.json",
        {
            "events": [
                {
                    "event_type": "deal_created",
                    "created_at": "2026-07-03T09:00:00+08:00",
                }
            ]
        },
    )
    _write_json(
        package_dir / "phases" / "audit_log.json",
        {
            "events": [
                {
                    "event_type": "deal_created",
                    "created_at": "2026-07-03T09:00:00+08:00",
                },
                {
                    "event_type": "deal_document_uploaded",
                    "created_at": "2026-07-03T09:05:00+08:00",
                },
            ]
        },
    )

    summary = deal_audit.summarize_deal_audit("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    assert summary["status"] == "warn"
    assert summary["sources"]["consistency"] == "mismatch"
    assert "audit_sources_mismatch" in summary["warnings"]
    assert "required_event_missing:openclaw_imported" not in summary["warnings"]
    by_event = {item["event_type"]: item for item in summary["required_event_status"]}
    assert by_event["deal_created"]["present"] is True
    assert by_event["deal_created"]["required"] is True
    assert by_event["openclaw_imported"]["present"] is False
    assert by_event["openclaw_imported"]["required"] is False


def test_deal_status_summary_blocks_draft_package_without_execution_artifacts(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )

    summary = deal_status.summarize_deal_status("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    assert summary["schema_version"] == "siq_deal_status_summary_v1"
    assert summary["status"] == "warn"
    assert summary["ready_for_next_action"] is False
    assert summary["next_action"] == "resolve_blocking_contracts"
    assert summary["counts"]["components"] == 8
    assert summary["counts"]["blocking"] >= 1
    by_component = {item["id"]: item for item in summary["components"]}
    assert by_component["preflight"]["status"] == "warn"
    assert by_component["r1_reports"]["blocking"] is True
    assert by_component["r1_5_disputes"]["status"] == "missing"
    assert by_component["r1_5_disputes"]["blocking"] is False
    assert by_component["r2_reports"]["status"] == "missing"
    assert by_component["r2_reports"]["blocking"] is False
    assert by_component["r3_review"]["status"] == "missing"
    assert by_component["r3_review"]["blocking"] is False
    assert by_component["r4_decision"]["status"] == "missing"
    assert by_component["r4_decision"]["blocking"] is False
    assert summary["sources"]["r1_5_disputes"]["schema_version"] == "siq_deal_r1_5_disputes_summary_v1"
    assert summary["sources"]["audit"]["status"] == "missing"
    assert by_component["audit"]["blocking"] is False


def test_deal_status_summary_marks_evidence_preflight_warning_blocking(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_minimum_complete_deal_contract(package_dir)
    (package_dir / "evidence" / "evidence_items.ndjson").write_text("", encoding="utf-8")

    summary = deal_status.summarize_deal_status("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    by_component = {item["id"]: item for item in summary["components"]}
    assert by_component["preflight"]["status"] == "warn"
    assert by_component["preflight"]["blocking"] is True
    assert "preflight_warn:evidence.gate" in by_component["preflight"]["message"]
    assert summary["ready_for_next_action"] is False


def test_deal_status_summary_blocks_unresolved_r1_5_disputes(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_json(
        package_dir / "phases" / "r1_5_disputes.json",
        {
            "disputes": [
                {
                    "dispute_id": "DISP-001",
                    "topic": "估值是否支撑 Pre-IPO 定价",
                    "dimension": "finance",
                    "severity": "high",
                    "positions": [{"agent_id": "ic_finance_auditor", "evidence_ids": ["EVID-001"]}],
                    "resolved": False,
                }
            ]
        },
    )

    summary = deal_status.summarize_deal_status("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    by_component = {item["id"]: item for item in summary["components"]}
    assert by_component["r1_5_disputes"]["status"] == "warn"
    assert by_component["r1_5_disputes"]["blocking"] is True
    assert by_component["r1_5_disputes"]["metrics"]["unresolved"] == 1
    assert "dispute_unresolved:DISP-001" in by_component["r1_5_disputes"]["warnings"]
    assert summary["next_action"] == "resolve_blocking_contracts"


def test_deal_status_summary_blocks_warn_r3_review_contract(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_json(package_dir / "phases" / "r3_reports.json", {})

    summary = deal_status.summarize_deal_status("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    by_component = {item["id"]: item for item in summary["components"]}
    assert by_component["r3_review"]["status"] == "warn"
    assert by_component["r3_review"]["blocking"] is True
    assert "r3_reports_empty" in by_component["r3_review"]["warnings"]
    assert summary["next_action"] == "resolve_blocking_contracts"


def test_deal_status_summary_allows_review_when_contracts_are_complete(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_minimum_complete_deal_contract(package_dir)

    summary = deal_status.summarize_deal_status("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    assert summary["ready_for_next_action"] is True
    assert summary["next_action"] == "confirm_r4_decision"
    by_component = {item["id"]: item for item in summary["components"]}
    assert by_component["preflight"]["status"] == "pass"
    assert by_component["r1_reports"]["status"] == "pass"
    assert by_component["r1_reports"]["blocking"] is False
    assert by_component["r1_5_disputes"]["status"] == "missing"
    assert by_component["r1_5_disputes"]["blocking"] is False
    assert by_component["r2_reports"]["status"] == "missing"
    assert by_component["r2_reports"]["blocking"] is False
    assert by_component["r3_review"]["status"] == "missing"
    assert by_component["r3_review"]["blocking"] is False
    assert by_component["r4_decision"]["status"] == "pass"
    assert by_component["r4_decision"]["blocking"] is False
    assert by_component["r4_decision"]["metrics"]["confirmation_status"] == "pending"
    assert by_component["r4_decision"]["metrics"]["confirmed"] is False
    assert "/home/maoyd" not in json.dumps(summary, ensure_ascii=False)


def test_deal_agents_summary_lists_profiles_with_readiness_and_reports(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_minimum_complete_deal_contract(package_dir)

    summary = deal_agents.summarize_deal_agents("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    assert summary["schema_version"] == "siq_deal_agents_summary_v1"
    assert summary["deal_id"] == "DEAL-YUSHU-2026-001"
    assert summary["counts"]["agents"] == 7
    assert summary["counts"]["r1_agents"] == 6
    assert summary["counts"]["reports"] == 6
    assert summary["counts"]["receipts"] == 6
    assert summary["counts"]["ready"] >= 1
    assert summary["r1_agent_sequence"] == [
        "siq_ic_strategist",
        "siq_ic_sector_expert",
        "siq_ic_finance_auditor",
        "siq_ic_legal_scanner",
        "siq_ic_risk_controller",
        "siq_ic_chairman",
    ]
    by_agent = {item["agent_id"]: item for item in summary["agents"]}
    assert set(by_agent) == {
        "siq_ic_master_coordinator",
        "siq_ic_strategist",
        "siq_ic_sector_expert",
        "siq_ic_finance_auditor",
        "siq_ic_legal_scanner",
        "siq_ic_risk_controller",
        "siq_ic_chairman",
    }
    assert by_agent["siq_ic_master_coordinator"]["is_r1_agent"] is False
    assert by_agent["siq_ic_master_coordinator"]["status"] == "non_r1"
    strategist = by_agent["siq_ic_strategist"]
    assert strategist["is_r1_agent"] is True
    assert strategist["status"] == "ready"
    assert strategist["readiness"]["allowed"] is True
    assert strategist["readiness"]["has_startup_receipt"] is True
    assert strategist["report"]["has_report"] is True
    assert strategist["report"]["status"] == "pass"
    assert strategist["receipt"]["present"] is True
    assert by_agent["siq_ic_finance_auditor"]["runtime"].get("model_name") is not None
    assert "/home/maoyd" not in json.dumps(summary, ensure_ascii=False)


def test_deal_reports_reject_unsafe_paths(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="杭州宇树科技股份有限公司",
        wiki_root=tmp_path,
    )

    with pytest.raises(ValueError):
        deal_reports.read_deal_report("DEAL-YUSHU-2026-001", "../manifest.json", wiki_root=tmp_path)
    with pytest.raises(ValueError):
        deal_reports.read_deal_report("DEAL-YUSHU-2026-001", "data_room/raw/file.pdf", wiki_root=tmp_path)
    with pytest.raises(ValueError):
        deal_reports.read_deal_report("DEAL-YUSHU-2026-001", "audit/audit_log.json", wiki_root=tmp_path)


def test_deal_id_rejects_path_escape(tmp_path):
    with pytest.raises(ValueError):
        deal_store.safe_deal_dir("../escape", wiki_root=tmp_path)


def test_import_openclaw_project_maps_core_files(tmp_path):
    openclaw_root = tmp_path / "openclaw" / "projects"
    source = openclaw_root / "SIQ-YUSHU-2026-002"
    _write_json(
        source / "project_meta.json",
        {
            "company_name": "杭州宇树科技股份有限公司",
            "industry": "机器人",
            "stage": "Pre-IPO",
        },
    )
    _write_json(
        source / "phases" / "workflow_state.json",
        {
            "company_name": "杭州宇树科技股份有限公司",
            "status": "r4_completed",
            "final_decision": "pass",
            "final_score": 78.55,
        },
    )
    _write_json(source / "phases" / "r1_reports.json", {"ic_strategist": {"score": 87}})
    _write_json(
        source / "phases" / "r4_decision.json",
        {
            "decision": "pass",
            "decision_text": "建议投资",
            "final_score": 78.55,
            "breakdown": {"ic_chairman": {"raw_score": 58}},
        },
    )
    (source / "discussion").mkdir(parents=True)
    (source / "discussion" / "05_最终投决报告.md").write_text("# Final", encoding="utf-8")
    (source / "40_decision").mkdir(parents=True)
    (source / "40_decision" / "IC_DECISION_REPORT.md").write_text("# IC Decision", encoding="utf-8")

    result = import_openclaw_project(
        source_root=source,
        deal_id="DEAL-YUSHU-2026-001",
        metadata={"memo": "IC import", "source_root": "/tmp/hidden"},
        wiki_root=tmp_path / "wiki",
        openclaw_projects_root=openclaw_root,
    )

    package_dir = tmp_path / "wiki" / "deals" / "DEAL-YUSHU-2026-001"
    assert result["deal"]["project_meta"]["legacy_project_id"] == "SIQ-YUSHU-2026-002"
    assert (package_dir / "phases" / "r1_reports.json").is_file()
    assert (package_dir / "discussion" / "05_最终投决报告.md").is_file()
    assert (package_dir / "decision" / "IC_DECISION_REPORT.md").read_text(encoding="utf-8") == "# IC Decision"
    workflow = json.loads((package_dir / "phases" / "workflow_state.json").read_text(encoding="utf-8"))
    assert workflow["deal_id"] == "DEAL-YUSHU-2026-001"
    assert workflow["legacy_project_id"] == "SIQ-YUSHU-2026-002"
    assert workflow["current_phase"] == "R4"
    r4_decision = json.loads((package_dir / "phases" / "r4_decision.json").read_text(encoding="utf-8"))
    assert r4_decision["schema_version"] == "siq_ic_r4_decision_v1"
    assert r4_decision["weighted_agent_score"] == 78.55
    assert r4_decision["chairman_dimension_score"] == 58
    assert r4_decision["chairman_qualitative_decision"] == "建议投资"
    assert r4_decision["threshold_result"] == "pass"
    assert r4_decision["human_confirmation"] == {
        "status": "pending",
        "confirmed_by": None,
        "confirmed_at": None,
        "override_reason": None,
    }
    assert r4_decision["artifact_paths"] == {
        "markdown": "decision/IC_DECISION_REPORT.md",
        "html": "decision/IC_DECISION_REPORT.html",
    }
    assert r4_decision["compatibility"]["source"] == "openclaw_legacy_r4_decision"
    r4_summary = deal_reports.summarize_r4_decision("DEAL-YUSHU-2026-001", wiki_root=tmp_path / "wiki")
    assert r4_summary["status"] == "pass"
    assert r4_summary["missing_advisory_fields"] == []
    assert r4_summary["human_confirmation"]["status"] == "pending"
    assert r4_summary["human_confirmation"]["confirmed"] is False
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["openclaw_import"]["file_count"] >= 4
    assert manifest["openclaw_import"]["metadata"] == {"memo": "IC import"}
    assert manifest["hashes"]["decision/IC_DECISION_REPORT.md"]
    assert manifest["hashes"]["phases/workflow_state.json"] == _sha256(package_dir / "phases" / "workflow_state.json")
    project_meta = json.loads((package_dir / "project_meta.json").read_text(encoding="utf-8"))
    assert project_meta["import_metadata"] == {"memo": "IC import"}
    assert "source_root" not in result["deal"]["manifest"]["openclaw_import"]
    assert not result["deal"]["summary"]["package_path"].startswith("/")


@pytest.mark.parametrize(
    ("legacy_receipts", "expected_shared_hits", "expected_private_hits"),
    [
        (
            {
                "generated_at": "2026-07-03T10:20:00+08:00",
                "agents": {"ic_strategist": {"count": 18, "types": {"shared": 18}}},
            },
            18,
            0,
        ),
        (
            {"ic_strategist": {"count": 12, "types": {"shared": 9, "private": 3}}},
            9,
            3,
        ),
    ],
)
def test_import_openclaw_project_normalizes_legacy_startup_receipts_for_preflight(
    tmp_path,
    legacy_receipts,
    expected_shared_hits,
    expected_private_hits,
):
    openclaw_root = tmp_path / "openclaw" / "projects"
    source = openclaw_root / "SIQ-LEGACY-RECEIPTS"
    _write_json(
        source / "project_meta.json",
        {
            "company_name": "Legacy Robotics",
            "industry": "Robotics",
            "stage": "Pre-IPO",
        },
    )
    _write_json(source / "phases" / "workflow_state.json", {"company_name": "Legacy Robotics"})
    _write_json(
        source / "phases" / "r1_reports.json",
        {
            "ic_strategist": {
                "agent_id": "ic_strategist",
                "score": 80,
                "recommendation": "SUPPORT",
                "verified": [],
                "assumed": [],
                "open_questions": [],
                "startup_receipt_id": "startup-siq_ic_strategist-R1-001",
            }
        },
    )
    _write_json(source / "phases" / "startup_receipts.json", legacy_receipts)

    import_openclaw_project(
        source_root=source,
        deal_id="DEAL-LEGACY-RECEIPTS-001",
        wiki_root=tmp_path / "wiki",
        openclaw_projects_root=openclaw_root,
    )

    package_dir = tmp_path / "wiki" / "deals" / "DEAL-LEGACY-RECEIPTS-001"
    stored = json.loads((package_dir / "phases" / "startup_receipts.json").read_text(encoding="utf-8"))
    receipt = stored["agents"]["siq_ic_strategist"]
    assert stored["schema_version"] == "siq_ic_startup_receipts_v1"
    assert stored["deal_id"] == "DEAL-LEGACY-RECEIPTS-001"
    assert stored["compatibility"]["source"] == "openclaw_legacy_startup_receipts"
    assert stored["compatibility"]["openclaw_legacy_summary"] == legacy_receipts
    assert receipt["agent_id"] == "siq_ic_strategist"
    assert receipt["receipt_id"] == "startup-siq_ic_strategist-R1-001"
    assert receipt["project_tag"] == "DEAL-LEGACY-RECEIPTS-001"
    assert receipt["shared_hits"] == expected_shared_hits
    assert receipt["private_hits"] == expected_private_hits
    assert receipt["workspace_rules_read"] == ["SOUL.md", "AGENTS.md"]
    assert receipt["gaps"] == []
    legacy_agent_summary = legacy_receipts.get("agents", legacy_receipts)["ic_strategist"]
    assert receipt["compatibility"]["openclaw_legacy_summary"] == legacy_agent_summary

    preflight = deal_contracts.run_deal_preflight("DEAL-LEGACY-RECEIPTS-001", wiki_root=tmp_path / "wiki")
    checks = {item["id"]: item for item in preflight["checks"]}
    assert checks["retrieval.receipt_contract"]["status"] == "pass"
    assert checks["retrieval.receipt_contract"]["details"]["issues"] == []


def test_deal_manifest_summary_tracks_openclaw_import_hashes(tmp_path):
    openclaw_root = tmp_path / "openclaw" / "projects"
    source = openclaw_root / "SIQ-YUSHU-2026-002"
    _write_json(source / "project_meta.json", {"company_name": "宇树", "industry": "机器人"})
    _write_json(source / "artifact_map.json", {"artifacts": []})
    _write_json(source / "phases" / "workflow_state.json", {"company_name": "宇树"})
    _write_json(source / "phases" / "r1_reports.json", {"siq_ic_strategist": {"score": 87}})
    _write_json(source / "phases" / "r1_5_disputes.json", {"disputes": []})
    _write_json(source / "phases" / "r2_reports.json", {})
    _write_json(source / "phases" / "r3_reports.json", {})
    _write_json(source / "phases" / "r4_decision.json", {"decision": "pass"})
    _write_json(source / "phases" / "startup_receipts.json", {"agents": {}})
    _write_json(source / "phases" / "round_context_receipts.json", {"rounds": {}})
    _write_json(source / "phases" / "audit_log.json", {"events": []})
    _write_json(source / "archive_manifest.json", {"schema_version": "openclaw_archive_v1"})
    (source / "40_decision").mkdir(parents=True)
    (source / "40_decision" / "IC_DECISION_REPORT.md").write_text("# IC Decision", encoding="utf-8")

    import_openclaw_project(
        source_root=source,
        deal_id="DEAL-YUSHU-2026-001",
        wiki_root=tmp_path / "wiki",
        openclaw_projects_root=openclaw_root,
    )

    summary = deal_manifest.summarize_deal_manifest("DEAL-YUSHU-2026-001", wiki_root=tmp_path / "wiki")

    assert summary["schema_version"] == "siq_deal_manifest_summary_v1"
    assert summary["status"] == "pass"
    assert summary["openclaw_import"]["present"] is True
    assert summary["openclaw_import"]["legacy_project_id"] == "SIQ-YUSHU-2026-002"
    assert summary["archive_manifest"]["available"] is True
    assert summary["archive_manifest"]["consistency"] == "match"
    assert summary["counts"]["imported_files"] == summary["openclaw_import"]["file_count"]
    assert summary["counts"]["files_missing_hash"] == 0
    by_target = {item["target"]: item for item in summary["files"]}
    assert by_target["project_meta.json"]["hash_recorded"] is True
    assert by_target["project_meta.json"]["hash_matches"] is True
    assert "/home/maoyd" not in json.dumps(summary, ensure_ascii=False)


def test_deal_manifest_summary_warns_on_rejected_import_files(tmp_path):
    openclaw_root = tmp_path / "openclaw" / "projects"
    source = openclaw_root / "SIQ-YUSHU-2026-002"
    _write_json(source / "project_meta.json", {"company_name": "宇树"})
    _write_json(source / "phases" / "workflow_state.json", {"company_name": "宇树"})
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    (source / "discussion").mkdir(parents=True)
    (source / "discussion" / "leak.md").symlink_to(outside)

    import_openclaw_project(
        source_root=source,
        deal_id="DEAL-YUSHU-2026-001",
        wiki_root=tmp_path / "wiki",
        openclaw_projects_root=openclaw_root,
    )

    summary = deal_manifest.summarize_deal_manifest("DEAL-YUSHU-2026-001", wiki_root=tmp_path / "wiki")

    assert summary["status"] == "warn"
    assert summary["counts"]["rejected_files"] == 1
    rejected = [item for item in summary["files"] if item["status"] == "rejected"]
    assert rejected[0]["target"] == "discussion/leak.md"
    assert rejected[0]["hash_matches"] is None
    assert "import_file_rejected:discussion/leak.md" in summary["warnings"]


def test_import_openclaw_project_rejects_source_outside_root(tmp_path):
    openclaw_root = tmp_path / "openclaw" / "projects"
    outside = tmp_path / "outside" / "SIQ-YUSHU-2026-002"
    outside.mkdir(parents=True)

    with pytest.raises(ValueError):
        import_openclaw_project(
            source_root=outside,
            deal_id="DEAL-YUSHU-2026-001",
            wiki_root=tmp_path / "wiki",
            openclaw_projects_root=openclaw_root,
        )


def test_import_openclaw_project_rejects_symlink_files(tmp_path):
    openclaw_root = tmp_path / "openclaw" / "projects"
    source = openclaw_root / "SIQ-YUSHU-2026-002"
    _write_json(source / "project_meta.json", {"company_name": "宇树"})
    _write_json(source / "phases" / "workflow_state.json", {"company_name": "宇树"})
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    (source / "discussion").mkdir(parents=True)
    (source / "discussion" / "leak.md").symlink_to(outside)

    result = import_openclaw_project(
        source_root=source,
        deal_id="DEAL-YUSHU-2026-001",
        wiki_root=tmp_path / "wiki",
        openclaw_projects_root=openclaw_root,
    )

    package_dir = tmp_path / "wiki" / "deals" / "DEAL-YUSHU-2026-001"
    assert not (package_dir / "discussion" / "leak.md").exists()
    files = result["archive_manifest"]["files"]
    rejected = [item for item in files if item.get("source") == "discussion/leak.md"]
    assert rejected and rejected[0]["status"] == "rejected"


def test_import_openclaw_project_overwrite_removes_stale_files(tmp_path):
    openclaw_root = tmp_path / "openclaw" / "projects"
    source = openclaw_root / "SIQ-YUSHU-2026-002"
    _write_json(source / "project_meta.json", {"company_name": "宇树"})
    _write_json(source / "phases" / "workflow_state.json", {"company_name": "宇树"})
    (source / "40_decision").mkdir(parents=True)
    (source / "40_decision" / "IC_DECISION_REPORT.md").write_text("# Old", encoding="utf-8")
    import_openclaw_project(
        source_root=source,
        deal_id="DEAL-YUSHU-2026-001",
        wiki_root=tmp_path / "wiki",
        openclaw_projects_root=openclaw_root,
    )
    package_dir = tmp_path / "wiki" / "deals" / "DEAL-YUSHU-2026-001"
    assert (package_dir / "decision" / "IC_DECISION_REPORT.md").is_file()

    (source / "40_decision" / "IC_DECISION_REPORT.md").unlink()
    import_openclaw_project(
        source_root=source,
        deal_id="DEAL-YUSHU-2026-001",
        wiki_root=tmp_path / "wiki",
        openclaw_projects_root=openclaw_root,
        overwrite=True,
    )

    assert not (package_dir / "decision" / "IC_DECISION_REPORT.md").exists()


def test_deal_job_status_uses_deal_job_service(monkeypatch):
    monkeypatch.setattr(
        deals.deal_job_service,
        "get",
        lambda job_id: {
            "job_id": job_id,
            "status": "running",
            "created_by": {"id": 7, "username": "analyst", "email": "hidden@example.com"},
            "result": {"source_root": "/tmp/secret", "deal_id": "DEAL-YUSHU-2026-001"},
        },
    )

    result = deals.get_deal_job_status(
        "deal-openclaw-import-abc123", current_user=SimpleNamespace(id=7, username="analyst")
    )

    assert result["job_id"] == "deal-openclaw-import-abc123"
    assert result["status"] == "running"
    assert result["created_by"] == {"id": 7, "username": "analyst"}
    assert result["result"] == {"deal_id": "DEAL-YUSHU-2026-001"}


def test_deal_workflow_artifacts_summarize_legacy_agent_reports(tmp_path):
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_json(
        package_dir / "phases" / "r1_reports.json",
        {
            "ic_strategist": {
                "agent_id": "ic_strategist",
                "score": 82,
                "recommendation": "SUPPORT",
                "confidence": "Medium",
                "summary": "战略窗口明确",
                "verified": ["增长率", "政策窗口"],
                "assumed": ["退出窗口"],
                "open_questions": ["核心客户续约"],
                "risk_flags": ["估值偏高"],
                "artifact_path": "discussion/01_R1_strategist_report.md",
                "source_root": "/tmp/secret",
            }
        },
    )
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "agents": {
                "ic_strategist": {
                    "agent_id": "ic_strategist",
                    "receipt_id": "startup-siq_ic_strategist-R1-001",
                    "source_root": "/tmp/secret",
                }
            }
        },
    )
    _write_json(
        package_dir / "phases" / "r1_5_disputes.json",
        {
            "disputes": [
                {
                    "topic": "估值是否支撑 Pre-IPO 定价",
                    "dimension": "finance",
                    "severity": "high",
                    "positions": [{"agent_id": "ic_finance_auditor"}, {"agent_id": "ic_risk_controller"}],
                    "chairman_ruling": {"decision": "resolved_with_conditions"},
                    "resolved": True,
                }
            ]
        },
    )

    result = deals._read_deal_workflow_artifacts(package_dir)

    strategist = result["agent_reports"][0]
    assert strategist["agent_id"] == "siq_ic_strategist"
    assert strategist["has_report"] is True
    assert strategist["has_startup_receipt"] is True
    assert strategist["score"] == 82
    assert strategist["recommendation"] == "SUPPORT"
    assert strategist["verified_count"] == 2
    assert strategist["startup_receipt_id"] == "startup-siq_ic_strategist-R1-001"
    assert result["agent_reports"][2]["agent_id"] == "siq_ic_finance_auditor"
    assert result["agent_reports"][2]["has_report"] is False
    assert result["startup_receipts"] == {"count": 1, "agents": ["siq_ic_strategist"]}
    assert result["disputes"][0]["position_count"] == 2
    assert result["artifact_status"] == {
        "r1_reports": True,
        "startup_receipts": True,
        "r1_5_disputes": True,
    }
    assert "source_root" not in json.dumps(result, ensure_ascii=False)
    assert "/tmp/secret" not in json.dumps(result, ensure_ascii=False)


def test_deal_preflight_warns_for_draft_package_without_execution_artifacts(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )

    result = deal_contracts.run_deal_preflight("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    checks = {item["id"]: item for item in result["checks"]}
    assert result["status"] == "warn"
    assert checks["core.project_meta"]["status"] == "pass"
    assert checks["core.manifest"]["status"] == "pass"
    assert checks["core.workflow_state"]["status"] == "pass"
    assert checks["r1.report_count"]["status"] == "warn"
    assert checks["r4.decision"]["status"] == "warn"


def test_deal_preflight_passes_complete_minimum_contract(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    report_agents = [
        "siq_ic_strategist",
        "siq_ic_sector_expert",
        "siq_ic_finance_auditor",
        "siq_ic_legal_scanner",
        "siq_ic_risk_controller",
    ]
    evidence_by_agent = {
        "siq_ic_strategist": "EVID-DEAL-YUSHU-2026-001-000001",
        "siq_ic_sector_expert": "EVID-DEAL-YUSHU-2026-001-000001",
        "siq_ic_finance_auditor": "EVID-DEAL-YUSHU-2026-001-000002",
        "siq_ic_legal_scanner": "EVID-DEAL-YUSHU-2026-001-000003",
        "siq_ic_risk_controller": "EVID-DEAL-YUSHU-2026-001-000004",
    }
    reports = {
        agent_id: {
            **(
                {
                    "schema_version": "siq_ic_expert_report_v2",
                    "claims": [
                        {
                            "claim_id": "CLM-PREFLIGHT-STRATEGIST-001",
                            "status": "verified",
                            "evidence_ids": [evidence_by_agent[agent_id]],
                        }
                    ],
                }
                if agent_id == "siq_ic_strategist"
                else {
                    "verified": [{"claim": "verified", "evidence_id": evidence_by_agent[agent_id]}],
                    "assumed": [],
                }
            ),
                "agent_id": agent_id,
                "score": 80,
                "recommendation": "SUPPORT",
                "open_questions": [],
                "startup_receipt_id": f"startup-{agent_id}-R1-001",
                "evidence_ids": [evidence_by_agent[agent_id]],
            }
            for agent_id in report_agents
    }
    _write_json(package_dir / "phases" / "r1_reports.json", reports)
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "agents": {
                agent_id: {
                    "agent_id": agent_id,
                    "receipt_id": f"startup-{agent_id}-R1-001",
                    "round_name": "R1",
                    "query": "宇树科技 机器人 Pre-IPO",
                    "project_tag": "DEAL-YUSHU-2026-001",
                    "shared_hits": 1,
                    "private_hits": 0,
                    "workspace_rules_read": ["SOUL.md", "AGENTS.md"],
                    "gaps": [],
                    "evidence_hits": [{"evidence_id": evidence_by_agent[agent_id]}],
                    "created_at": "2026-07-03T10:20:00+08:00",
                }
                for agent_id in report_agents
            },
        },
    )
    _write_ndjson(
        package_dir / "evidence" / "evidence_items.ndjson",
        [
            {
                "evidence_id": "EVID-DEAL-YUSHU-2026-001-000001",
                "evidence_type": "verified",
                "dimension": "business",
                "claim": "business",
            },
            {
                "evidence_id": "EVID-DEAL-YUSHU-2026-001-000002",
                "evidence_type": "verified",
                "dimension": "finance",
                "claim": "finance",
            },
            {
                "evidence_id": "EVID-DEAL-YUSHU-2026-001-000003",
                "evidence_type": "verified",
                "dimension": "legal",
                "claim": "legal",
            },
            {
                "evidence_id": "EVID-DEAL-YUSHU-2026-001-000004",
                "evidence_type": "verified",
                "dimension": "risk",
                "claim": "risk",
            },
        ],
    )
    _write_json(
        package_dir / "phases" / "r4_decision.json",
        {
            "weighted_agent_score": 82.5,
            "chairman_dimension_score": 78.0,
            "chairman_qualitative_decision": "建议投资但需保护条款",
        },
    )

    result = deal_contracts.run_deal_preflight("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    assert result["status"] == "pass"
    checks = {item["id"]: item for item in result["checks"]}
    assert checks["retrieval.receipt_contract"]["status"] == "pass"
    assert checks["r1.report_contract"]["status"] == "pass"
    assert checks["r1.report_evidence_refs"]["status"] == "pass"
    assert result["counts"] == {
        "r1_reports": 5,
        "startup_receipts": 5,
        "evidence_items": 4,
        "verified_evidence_items": 4,
    }


def test_deal_preflight_warns_for_invalid_receipt_and_unknown_evidence_refs(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_ndjson(
        package_dir / "evidence" / "evidence_items.ndjson",
        [
            {
                "evidence_id": "EVID-DEAL-YUSHU-2026-001-000001",
                "evidence_type": "verified",
                "dimension": "business",
                "claim": "business",
            },
            {
                "evidence_id": "EVID-DEAL-YUSHU-2026-001-000002",
                "evidence_type": "verified",
                "dimension": "finance",
                "claim": "finance",
            },
            {
                "evidence_id": "EVID-DEAL-YUSHU-2026-001-000003",
                "evidence_type": "verified",
                "dimension": "legal",
                "claim": "legal",
            },
            {
                "evidence_id": "EVID-DEAL-YUSHU-2026-001-000004",
                "evidence_type": "verified",
                "dimension": "risk",
                "claim": "risk",
            },
        ],
    )
    _write_json(
        package_dir / "phases" / "r1_reports.json",
        {
            "siq_ic_strategist": {
                "agent_id": "siq_ic_strategist",
                "score": 80,
                "recommendation": "SUPPORT",
                "verified": [{"evidence_id": "EVID-DEAL-YUSHU-2026-001-MISSING"}],
                "assumed": [],
                "open_questions": [],
                "startup_receipt_id": "startup-siq_ic_strategist-R1-001",
            }
        },
    )
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "agents": {
                "siq_ic_strategist": {
                    "agent_id": "siq_ic_strategist",
                    "receipt_id": "startup-siq_ic_strategist-R1-001",
                    "round_name": "R1",
                    "query": "宇树科技",
                    "project_tag": "DEAL-YUSHU-2026-001",
                    "shared_hits": 1,
                    "private_hits": 0,
                    "workspace_rules_read": [],
                    "gaps": "not-a-list",
                    "evidence_hits": [{"evidence_id": "EVID-DEAL-YUSHU-2026-001-UNKNOWN"}],
                    "created_at": "2026-07-03T10:20:00+08:00",
                }
            }
        },
    )

    result = deal_contracts.run_deal_preflight("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    checks = {item["id"]: item for item in result["checks"]}
    assert result["status"] == "warn"
    assert checks["retrieval.receipt_contract"]["status"] == "warn"
    receipt_issue = checks["retrieval.receipt_contract"]["details"]["issues"][0]
    assert receipt_issue["agent_id"] == "siq_ic_strategist"
    assert "workspace_rules_read_non_empty" in receipt_issue["missing_or_invalid"]
    assert "gaps_list" in receipt_issue["missing_or_invalid"]
    assert receipt_issue["unknown_evidence_ids"] == ["EVID-DEAL-YUSHU-2026-001-UNKNOWN"]
    assert checks["r1.report_evidence_refs"]["status"] == "warn"
    report_issue = checks["r1.report_evidence_refs"]["details"]["issues"][0]
    assert "known_evidence_id_reference" in report_issue["missing_or_invalid"]
    assert report_issue["unknown_evidence_ids"] == ["EVID-DEAL-YUSHU-2026-001-MISSING"]


def test_deal_preflight_allows_legacy_text_reports_as_advisory(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    report_agents = [
        "siq_ic_strategist",
        "siq_ic_sector_expert",
        "siq_ic_finance_auditor",
        "siq_ic_legal_scanner",
        "siq_ic_risk_controller",
    ]
    _write_ndjson(
        package_dir / "evidence" / "evidence_items.ndjson",
        [
            {
                "evidence_id": "EVID-DEAL-YUSHU-2026-001-000001",
                "evidence_type": "verified",
                "dimension": "business",
                "claim": "business",
            },
            {
                "evidence_id": "EVID-DEAL-YUSHU-2026-001-000002",
                "evidence_type": "verified",
                "dimension": "finance",
                "claim": "finance",
            },
            {
                "evidence_id": "EVID-DEAL-YUSHU-2026-001-000003",
                "evidence_type": "verified",
                "dimension": "legal",
                "claim": "legal",
            },
            {
                "evidence_id": "EVID-DEAL-YUSHU-2026-001-000004",
                "evidence_type": "verified",
                "dimension": "risk",
                "claim": "risk",
            },
        ],
    )
    _write_json(
        package_dir / "phases" / "r1_reports.json",
        {
            agent_id: {
                "agent_id": agent_id,
                "score": 80,
                "recommendation": "SUPPORT",
                "verified": ["增长率", "政策窗口"],
                "assumed": ["退出窗口"],
                "open_questions": ["核心客户续约"],
            }
            for agent_id in report_agents
        },
    )
    _write_json(
        package_dir / "phases" / "r4_decision.json",
        {
            "weighted_agent_score": 82.5,
            "chairman_dimension_score": 78.0,
            "chairman_qualitative_decision": "建议投资但需保护条款",
        },
    )

    result = deal_contracts.run_deal_preflight("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    checks = {item["id"]: item for item in result["checks"]}
    assert checks["r1.report_evidence_refs"]["status"] == "pass"
    assert checks["r1.report_evidence_advisory"]["status"] == "info"
    assert result["status"] == "warn"
    assert checks["retrieval.startup_receipts"]["status"] == "warn"


def test_deal_preflight_reads_manifest_evidence_items_path(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["evidence"]["items_path"] = "evidence/custom_items.ndjson"
    _write_json(package_dir / "manifest.json", manifest)
    _write_ndjson(
        package_dir / "evidence" / "custom_items.ndjson",
        [
            {
                "evidence_id": "EVID-DEAL-YUSHU-2026-001-000001",
                "evidence_type": "verified",
                "dimension": "business",
                "claim": "business",
            },
            {
                "evidence_id": "EVID-DEAL-YUSHU-2026-001-000002",
                "evidence_type": "verified",
                "dimension": "finance",
                "claim": "finance",
            },
            {
                "evidence_id": "EVID-DEAL-YUSHU-2026-001-000003",
                "evidence_type": "verified",
                "dimension": "legal",
                "claim": "legal",
            },
            {
                "evidence_id": "EVID-DEAL-YUSHU-2026-001-000004",
                "evidence_type": "verified",
                "dimension": "risk",
                "claim": "risk",
            },
        ],
    )

    result = deal_contracts.run_deal_preflight("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    assert result["counts"]["evidence_items"] == 4
    checks = {item["id"]: item for item in result["checks"]}
    assert checks["evidence.gate"]["details"]["verified_count"] == 4


def test_deal_document_upload_list_get_delete_updates_manifest(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )

    document = deal_documents.create_deal_document(
        deal_id="DEAL-YUSHU-2026-001",
        filename="../BP Final.PDF",
        content_type="application/pdf",
        stream=BytesIO(b"hello deal room"),
        document_type="business_plan",
        source_note="founder upload",
        created_by={"id": 7, "username": "analyst", "email": "hidden@example.com"},
        wiki_root=tmp_path,
    )

    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    assert document["document_id"].startswith("DOC-")
    assert document["original_filename"] == "BP Final.PDF"
    assert document["storage_path"].startswith("data_room/raw/DOC-")
    assert not document["storage_path"].startswith("/")
    assert document["created_by"] == {"id": 7, "username": "analyst"}
    raw_path = package_dir / document["storage_path"]
    assert raw_path.read_bytes() == b"hello deal room"
    assert document["sha256"] == hashlib.sha256(b"hello deal room").hexdigest()

    documents = deal_documents.list_deal_documents("DEAL-YUSHU-2026-001", wiki_root=tmp_path)
    assert [item["document_id"] for item in documents] == [document["document_id"]]
    loaded = deal_documents.get_deal_document("DEAL-YUSHU-2026-001", document["document_id"], wiki_root=tmp_path)
    assert loaded["document_type"] == "business_plan"
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["documents"][0]["document_id"] == document["document_id"]

    result = deal_documents.delete_deal_document(
        "DEAL-YUSHU-2026-001",
        document["document_id"],
        deleted_by={"id": 7, "username": "analyst"},
        wiki_root=tmp_path,
    )

    assert result == {"ok": True, "document_id": document["document_id"]}
    assert not raw_path.exists()
    assert deal_documents.list_deal_documents("DEAL-YUSHU-2026-001", wiki_root=tmp_path) == []
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["documents"] == []


def test_deal_document_rejects_oversized_upload(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )

    with pytest.raises(ValueError):
        deal_documents.create_deal_document(
            deal_id="DEAL-YUSHU-2026-001",
            filename="oversized.pdf",
            content_type="application/pdf",
            stream=BytesIO(b"abcdef"),
            wiki_root=tmp_path,
            max_bytes=5,
        )

    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    assert list((package_dir / "data_room" / "raw").iterdir()) == []


def test_deal_document_delete_removes_symlink_not_target(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    protected = package_dir / "data_room" / "raw" / "protected.txt"
    protected.write_text("keep", encoding="utf-8")
    link = package_dir / "data_room" / "raw" / "DOC-ABCDEF1234567890.txt"
    link.symlink_to(protected)
    metadata = {
        "schema_version": "siq_deal_document_v1",
        "deal_id": "DEAL-YUSHU-2026-001",
        "document_id": "DOC-ABCDEF1234567890",
        "original_filename": "link.txt",
        "storage_path": "data_room/raw/DOC-ABCDEF1234567890.txt",
    }
    _write_json(package_dir / "data_room" / "metadata" / "DOC-ABCDEF1234567890.json", metadata)

    result = deal_documents.delete_deal_document(
        "DEAL-YUSHU-2026-001",
        "DOC-ABCDEF1234567890",
        wiki_root=tmp_path,
    )

    assert result == {"ok": True, "document_id": "DOC-ABCDEF1234567890"}
    assert not link.exists()
    assert protected.read_text(encoding="utf-8") == "keep"


def test_deal_document_bind_parser_task_updates_metadata_manifest_and_audit(tmp_path, monkeypatch):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    document = deal_documents.create_deal_document(
        deal_id="DEAL-YUSHU-2026-001",
        filename="bp.pdf",
        content_type="application/pdf",
        stream=BytesIO(b"hello"),
        wiki_root=tmp_path,
    )
    parser_root = tmp_path / "parser-results"
    artifact = parser_root / "parser-task-1" / "document.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("# parsed", encoding="utf-8")
    monkeypatch.setattr(deal_documents, "DOCUMENT_PARSER_RESULTS_ROOT", parser_root)

    bound = deal_documents.bind_parser_task(
        "DEAL-YUSHU-2026-001",
        document["document_id"],
        task_id="parser-task-1",
        artifact_path="document.md",
        note="manual link",
        bound_by={"id": 7, "username": "analyst", "email": "hidden@example.com"},
        wiki_root=tmp_path,
    )

    assert bound["status"] == "parse_bound"
    assert bound["parse_task_id"] == "parser-task-1"
    assert bound["parsed_artifact_path"] == "document.md"
    assert bound["parser_status_url"] == "/api/documents/status/parser-task-1"
    assert bound["parser_artifact_url"] == "/api/documents/artifact/parser-task-1/document.md"
    assert bound["parser_artifact_exists"] is True
    assert bound["parse_bound_by"] == {"id": 7, "username": "analyst"}
    assert "hidden@example.com" not in json.dumps(bound, ensure_ascii=False)
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["documents"][0]["parse_task_id"] == "parser-task-1"
    audit = json.loads((package_dir / "audit" / "audit_log.json").read_text(encoding="utf-8"))
    assert audit["events"][-1]["event_type"] == "deal_document_parser_task_bound"


def test_deal_document_bind_parser_task_rejects_unsafe_inputs(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    document = deal_documents.create_deal_document(
        deal_id="DEAL-YUSHU-2026-001",
        filename="bp.pdf",
        content_type="application/pdf",
        stream=BytesIO(b"hello"),
        wiki_root=tmp_path,
    )

    with pytest.raises(ValueError):
        deal_documents.bind_parser_task(
            "DEAL-YUSHU-2026-001",
            document["document_id"],
            task_id="../bad",
            wiki_root=tmp_path,
        )
    with pytest.raises(ValueError):
        deal_documents.bind_parser_task(
            "DEAL-YUSHU-2026-001",
            document["document_id"],
            task_id="parser-task-1",
            artifact_path="../secret.md",
            wiki_root=tmp_path,
        )


def test_deal_evidence_builds_offline_package_from_bound_parser_docs(tmp_path, monkeypatch):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    financial_doc = deal_documents.create_deal_document(
        deal_id="DEAL-YUSHU-2026-001",
        filename="financial-model.pdf",
        content_type="application/pdf",
        stream=BytesIO(b"financial"),
        document_type="financial_model",
        wiki_root=tmp_path,
    )
    missing_doc = deal_documents.create_deal_document(
        deal_id="DEAL-YUSHU-2026-001",
        filename="license.pdf",
        content_type="application/pdf",
        stream=BytesIO(b"legal"),
        document_type="legal_contract",
        wiki_root=tmp_path,
    )
    parser_root = tmp_path / "parser-results"
    document_md = parser_root / "task-fin" / "document.md"
    document_md.parent.mkdir(parents=True)
    document_md.write_text(
        "\n".join(
            [
                "<!-- DOC_BLOCK: b000001 page=3 evidence=doc:task-fin:p3:b000001 -->",
                "# Revenue",
                "2025 revenue reached RMB 100m.",
                "",
                "<!-- DOC_BLOCK: b000002 page=4 evidence=doc:task-fin:p4:b000002 -->",
                "Gross margin improved after scale production.",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(deal_documents, "DOCUMENT_PARSER_RESULTS_ROOT", parser_root)
    monkeypatch.setattr(deal_evidence, "DOCUMENT_PARSER_RESULTS_ROOT", parser_root)
    deal_documents.bind_parser_task(
        "DEAL-YUSHU-2026-001",
        financial_doc["document_id"],
        task_id="task-fin",
        artifact_path="document.md",
        wiki_root=tmp_path,
    )
    deal_documents.bind_parser_task(
        "DEAL-YUSHU-2026-001",
        missing_doc["document_id"],
        task_id="task-missing",
        artifact_path="document.md",
        wiki_root=tmp_path,
    )

    result = deal_evidence.build_deal_evidence_package(
        "DEAL-YUSHU-2026-001",
        built_by={"id": 7, "username": "analyst", "email": "hidden@example.com"},
        wiki_root=tmp_path,
    )

    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    assert result["status"] == "warn"
    assert result["counts"]["documents_bound"] == 2
    assert result["counts"]["documents_indexed"] == 1
    assert result["counts"]["items"] == 2
    assert result["quality_report"]["llm_used"] is False
    assert result["quality_report"]["agent_used"] is False
    assert result["quality_report"]["milvus_written"] is False
    assert (package_dir / "evidence" / "evidence_index.json").is_file()
    assert (package_dir / "evidence" / "evidence_items.ndjson").is_file()
    assert (package_dir / "evidence" / "evidence_quality_report.json").is_file()

    rows = [
        json.loads(line)
        for line in (package_dir / "evidence" / "evidence_items.ndjson").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [row["evidence_id"] for row in rows] == [
        "EVID-DEAL-YUSHU-2026-001-000001",
        "EVID-DEAL-YUSHU-2026-001-000002",
    ]
    assert rows[0]["schema_version"] == "siq_deal_evidence_item_v1"
    assert rows[0]["document_id"] == financial_doc["document_id"]
    assert rows[0]["dimension"] == "finance"
    assert rows[0]["source_anchor"]["page"] == 3
    assert rows[0]["source_anchor"]["block_id"] == "b000001"
    assert rows[0]["source_anchor"]["md_line_start"] == 2
    assert rows[0]["source_url"] == "/api/documents/source/task-fin/block/b000001"
    assert "hidden@example.com" not in json.dumps(result, ensure_ascii=False)

    quality = json.loads((package_dir / "evidence" / "evidence_quality_report.json").read_text(encoding="utf-8"))
    quality_documents = {item["document_id"]: item for item in quality["documents"]}
    assert quality_documents[financial_doc["document_id"]]["status"] == "indexed"
    assert quality_documents[missing_doc["document_id"]]["status"] == "missing_task_dir"
    assert "business" in quality["missing_dimensions"]
    index = json.loads((package_dir / "evidence" / "evidence_index.json").read_text(encoding="utf-8"))
    assert index["items"][0]["locator"] == "document.md:L2-L3"
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["evidence"]["last_build"]["status"] == "warn"
    audit = json.loads((package_dir / "audit" / "audit_log.json").read_text(encoding="utf-8"))
    assert audit["events"][-1]["event_type"] == "deal_evidence_built"

    read_back = deal_evidence.read_deal_evidence_package("DEAL-YUSHU-2026-001", wiki_root=tmp_path)
    assert read_back["items_preview"][0]["evidence_id"] == "EVID-DEAL-YUSHU-2026-001-000001"
    filtered = deal_evidence.read_deal_evidence_package(
        "DEAL-YUSHU-2026-001",
        wiki_root=tmp_path,
        q="gross",
        dimension="finance",
        preview_limit=10,
    )
    assert filtered["matched_count"] == 1
    assert filtered["counts"]["items"] == 2
    assert filtered["items_preview"][0]["source_anchor"]["page"] == 4
    source_filtered = deal_evidence.read_deal_evidence_package(
        "DEAL-YUSHU-2026-001",
        wiki_root=tmp_path,
        source_url="/api/documents/source/task-fin/block/b000001",
    )
    assert source_filtered["matched_count"] == 1
    assert source_filtered["items_preview"][0]["source_anchor"]["block_id"] == "b000001"
    limited = deal_evidence.read_deal_evidence_package(
        "DEAL-YUSHU-2026-001",
        wiki_root=tmp_path,
        document_id=financial_doc["document_id"],
        preview_limit=1,
    )
    assert limited["matched_count"] == 2
    assert limited["total_item_count"] == 2
    assert len(limited["items_preview"]) == 1
    assert limited["available_filters"]["dimensions"] == ["finance"]
    assert financial_doc["document_id"] in limited["available_filters"]["document_ids"]
    item = deal_evidence.get_deal_evidence_item(
        "DEAL-YUSHU-2026-001",
        "EVID-DEAL-YUSHU-2026-001-000002",
        wiki_root=tmp_path,
    )
    assert item["evidence"]["source_anchor"]["page"] == 4

    dry_run = deal_evidence.build_deal_evidence_ingest_dry_run(
        "DEAL-YUSHU-2026-001",
        created_by={"id": 7, "username": "analyst", "email": "hidden@example.com"},
        wiki_root=tmp_path,
    )
    assert dry_run["schema_version"] == "siq_deal_evidence_ingest_dry_run_v1"
    assert dry_run["postgres_written"] is False
    assert dry_run["milvus_written"] is False
    assert dry_run["write_readiness"] == "fail"
    assert dry_run["plan_hash"]
    by_check = {item["id"]: item for item in dry_run["preflight_checks"]}
    assert by_check["quality.status"]["status"] == "fail"
    assert by_check["plan.count_consistency"]["status"] == "pass"
    assert dry_run["counts"]["items_total"] == 2
    assert dry_run["counts"]["postgres_rows_planned"] == 2
    assert dry_run["counts"]["milvus_chunks_planned"] == 2
    assert dry_run["target_postgres"]["write_enabled"] is False
    assert dry_run["target_milvus"]["write_enabled"] is False
    assert dry_run["postgres_rows_preview"][0]["artifact_path"] == "parser_results/task-fin/document.md"
    assert dry_run["milvus_chunks_preview"][0]["collection"] == "siq_deal_shared"
    assert dry_run["milvus_chunks_preview"][0]["evidence_id"] == "EVID-DEAL-YUSHU-2026-001-000001"
    assert dry_run["milvus_chunks_preview"][0]["confidence"] == 0.6
    assert "hidden@example.com" not in json.dumps(deal_store.redact_public_payload(dry_run), ensure_ascii=False)
    assert (package_dir / "evidence" / "evidence_ingest_dry_run.json").is_file()
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["evidence"]["last_ingest_dry_run"]["postgres_written"] is False
    assert manifest["evidence"]["last_ingest_dry_run"]["milvus_written"] is False
    loaded_dry_run = deal_evidence.read_deal_evidence_ingest_dry_run("DEAL-YUSHU-2026-001", wiki_root=tmp_path)
    assert loaded_dry_run["ingest_dry_run"]["counts"]["items_valid"] == 2


def test_deal_evidence_ingest_dry_run_marks_write_targets_as_readiness_warnings(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    rows = [
        {
            "schema_version": "siq_deal_evidence_item_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "document_id": f"doc-{dimension}",
            "evidence_id": f"EVID-DEAL-YUSHU-2026-001-00000{index}",
            "source_path": f"parsed_documents/doc-{dimension}.md",
            "quote": f"{dimension} quote",
            "claim": f"{dimension} claim",
            "evidence_type": "verified",
            "dimension": dimension,
            "confidence": 0.8,
        }
        for index, dimension in enumerate(("business", "finance", "legal", "risk"), start=1)
    ]
    _write_ndjson(package_dir / "evidence" / "evidence_items.ndjson", rows)
    _write_json(
        package_dir / "evidence" / "evidence_quality_report.json",
        {
            "schema_version": "siq_deal_evidence_quality_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "status": "pass",
        },
    )

    dry_run = deal_evidence.build_deal_evidence_ingest_dry_run(
        "DEAL-YUSHU-2026-001",
        wiki_root=tmp_path,
    )

    assert dry_run["status"] == "pass"
    assert dry_run["write_readiness"] == "warn"
    by_check = {item["id"]: item for item in dry_run["preflight_checks"]}
    assert by_check["items.required_fields"]["status"] == "pass"
    assert by_check["quality.status"]["status"] == "pass"
    assert by_check["plan.count_consistency"]["status"] == "pass"
    assert by_check["target.postgres_write"]["status"] == "warn"
    assert by_check["target.milvus_write"]["status"] == "warn"
    assert (
        dry_run["plan_hash"]
        == deal_store.read_json(
            package_dir / "evidence" / "evidence_ingest_dry_run.json",
            {},
        )["plan_hash"]
    )


def test_startup_retrieval_generates_local_receipt_from_evidence(tmp_path, monkeypatch):
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
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        industry="机器人",
        stage="Pre-IPO",
        wiki_root=tmp_path,
    )
    document = deal_documents.create_deal_document(
        deal_id="DEAL-YUSHU-2026-001",
        filename="financial-model.pdf",
        content_type="application/pdf",
        stream=BytesIO(b"financial"),
        document_type="financial_model",
        wiki_root=tmp_path,
    )
    parser_root = tmp_path / "parser-results"
    document_md = parser_root / "task-fin" / "document.md"
    document_md.parent.mkdir(parents=True)
    document_md.write_text("Revenue reached RMB 100m.\n\nGross margin improved.", encoding="utf-8")
    monkeypatch.setattr(deal_documents, "DOCUMENT_PARSER_RESULTS_ROOT", parser_root)
    monkeypatch.setattr(deal_evidence, "DOCUMENT_PARSER_RESULTS_ROOT", parser_root)
    deal_documents.bind_parser_task(
        "DEAL-YUSHU-2026-001",
        document["document_id"],
        task_id="task-fin",
        artifact_path="document.md",
        wiki_root=tmp_path,
    )
    deal_evidence.build_deal_evidence_package("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    receipt = ic_startup_retrieval.generate_startup_retrieval_receipt(
        "DEAL-YUSHU-2026-001",
        "ic_finance_auditor",
        created_by={"id": 7, "username": "analyst", "email": "hidden@example.com"},
        wiki_root=tmp_path,
    )

    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    assert receipt["receipt_id"] == "startup-siq_ic_finance_auditor-R1-001"
    assert receipt["agent_id"] == "siq_ic_finance_auditor"
    assert receipt["legacy_agent_id"] == "ic_finance_auditor"
    assert receipt["query"] == "宇树科技 机器人 Pre-IPO DEAL-YUSHU-2026-001"
    assert receipt["retrieval_mode"] == "local_evidence_package_v1"
    assert receipt["shared_hits"] == 1
    assert receipt["private_hits"] == 0
    assert receipt["hybrid_hit_count"] == 1
    assert receipt["hybrid_hits"][0]["evidence_id"] == "EVID-DEAL-YUSHU-2026-001-000001"
    assert receipt["milvus_used"] is False
    assert receipt["postgres_used"] is False
    assert receipt["reranker_used"] is False
    assert receipt["vector_retrieval"]["status"] == "skipped"
    assert receipt["rerank"]["status"] == "skipped"
    assert receipt["hermes_used"] is False
    assert receipt["evidence_hits"][0]["evidence_id"] == "EVID-DEAL-YUSHU-2026-001-000001"
    assert "SOUL.md" in receipt["workspace_rules_read"]
    assert "hidden@example.com" not in json.dumps(deal_store.redact_public_payload(receipt), ensure_ascii=False)

    stored = json.loads((package_dir / "phases" / "startup_receipts.json").read_text(encoding="utf-8"))
    assert stored["schema_version"] == "siq_ic_startup_receipts_v2"
    assert stored["agents"]["siq_ic_finance_auditor"]["shared_hits"] == 1
    assert stored["by_agent_phase"]["siq_ic_finance_auditor"]["R1"]["receipt_id"] == receipt["receipt_id"]
    loaded = ic_startup_retrieval.read_startup_retrieval_receipt(
        "DEAL-YUSHU-2026-001",
        "ic_finance",
        wiki_root=tmp_path,
    )
    assert loaded["agent_id"] == "siq_ic_finance_auditor"
    assert loaded["receipt"]["receipt_id"] == "startup-siq_ic_finance_auditor-R1-001"
    audit = json.loads((package_dir / "audit" / "audit_log.json").read_text(encoding="utf-8"))
    assert audit["events"][-1]["event_type"] == "deal_startup_retrieval_receipt_generated"
    assert audit["events"][-1]["vector_retrieval_enabled"] is True
    assert audit["events"][-1]["rerank_enabled"] is False


def test_startup_retrieval_rejects_unknown_profile_and_supports_coordinator(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )

    with pytest.raises(ValueError):
        ic_startup_retrieval.generate_startup_retrieval_receipt(
            "DEAL-YUSHU-2026-001",
            "siq_ic_unknown",
            wiki_root=tmp_path,
        )
    coordinator = ic_startup_retrieval.generate_startup_retrieval_receipt(
        "DEAL-YUSHU-2026-001",
        "siq_ic_master_coordinator",
        round_name="R0",
        include_vector=False,
        wiki_root=tmp_path,
    )
    assert coordinator["agent_id"] == "siq_ic_master_coordinator"
    assert coordinator["private_collection"] == "siq_ic_master_coordinator"


def test_ic_agent_task_dry_run_builds_payload_when_receipt_exists(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        industry="机器人",
        stage="Pre-IPO",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_ndjson(
        package_dir / "evidence" / "evidence_items.ndjson",
        [
            {
                "evidence_id": "EVID-DEAL-YUSHU-2026-001-000001",
                "evidence_type": "verified",
                "dimension": "business",
                "claim": "business",
            },
            {
                "evidence_id": "EVID-DEAL-YUSHU-2026-001-000002",
                "evidence_type": "verified",
                "dimension": "finance",
                "claim": "finance",
            },
            {
                "evidence_id": "EVID-DEAL-YUSHU-2026-001-000003",
                "evidence_type": "verified",
                "dimension": "legal",
                "claim": "legal",
            },
            {
                "evidence_id": "EVID-DEAL-YUSHU-2026-001-000004",
                "evidence_type": "verified",
                "dimension": "risk",
                "claim": "risk",
            },
        ],
    )
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "agents": {
                "siq_ic_finance_auditor": {
                    "receipt_id": "startup-siq_ic_finance_auditor-R1-001",
                    "agent_id": "siq_ic_finance_auditor",
                    "round_name": "R1",
                    "query": "宇树科技",
                    "project_tag": "DEAL-YUSHU-2026-001",
                    "shared_hits": 1,
                    "private_hits": 0,
                    "workspace_rules_read": ["SOUL.md", "AGENTS.md"],
                    "gaps": [],
                    "evidence_hits": [{"evidence_id": "EVID-DEAL-YUSHU-2026-001-000002"}],
                    "created_at": "2026-07-03T10:20:00+08:00",
                }
            },
        },
    )

    result = ic_agent_runtime.build_ic_agent_task_dry_run(
        "DEAL-YUSHU-2026-001",
        "ic_finance",
        wiki_root=tmp_path,
    )

    assert result["schema_version"] == "siq_ic_agent_task_dry_run_v1"
    assert result["agent_id"] == "siq_ic_finance_auditor"
    assert result["allowed"] is False
    assert result["blocking_reasons"] == ["startup_receipt_gate_blocked:receipt_gate_missing"]
    assert result["hermes_called"] is False
    assert result["report_written"] is False
    payload = result["payload"]
    assert payload["schema_version"] == "siq_ic_agent_task_v1"
    assert payload["deal_package_root"] == "data/wiki/deals/DEAL-YUSHU-2026-001"
    assert payload["workflow_policy_path"] == "agents/hermes/profiles/siq_ic_shared/ic_workflow_policy.json"
    assert payload["startup_receipt_path"] == "phases/startup_receipts.json"
    assert payload["output_contract"]["json_path"] == "phases/r1_reports.json"
    assert payload["output_contract"]["markdown_path"] == "discussion/01_R1_finance_auditor_report.md"
    assert "evidence_ids" in payload["output_contract"]["required_fields"]
    assert "必须先读取 startup receipt" in payload["hard_rules"]
    assert "/home/maoyd" not in json.dumps(result, ensure_ascii=False)


def test_ic_agent_task_dry_run_blocks_missing_receipt(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )

    result = ic_agent_runtime.build_ic_agent_task_dry_run(
        "DEAL-YUSHU-2026-001",
        "siq_ic_strategist",
        wiki_root=tmp_path,
    )

    assert result["allowed"] is False
    assert "startup_receipt_missing" in result["blocking_reasons"]
    assert result["payload"]["agent_id"] == "siq_ic_strategist"


def test_ic_agent_task_dry_run_blocks_evidence_gate_warning(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "agents": {
                "siq_ic_strategist": {
                    "receipt_id": "startup-siq_ic_strategist-R1-001",
                    "agent_id": "siq_ic_strategist",
                    "round_name": "R1",
                    "query": "宇树科技",
                    "project_tag": "DEAL-YUSHU-2026-001",
                    "shared_hits": 0,
                    "private_hits": 0,
                    "workspace_rules_read": ["SOUL.md", "AGENTS.md"],
                    "gaps": [],
                    "created_at": "2026-07-03T10:20:00+08:00",
                }
            },
        },
    )

    result = ic_agent_runtime.build_ic_agent_task_dry_run(
        "DEAL-YUSHU-2026-001",
        "siq_ic_strategist",
        wiki_root=tmp_path,
    )

    assert result["allowed"] is False
    assert "preflight_warn:evidence.gate" in result["blocking_reasons"]
    assert "preflight:evidence.gate:warn" in result["warnings"]


def test_ic_agent_task_dry_run_blocks_invalid_current_receipt_contract(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_ndjson(
        package_dir / "evidence" / "evidence_items.ndjson",
        [
            {
                "evidence_id": "EVID-DEAL-YUSHU-2026-001-000001",
                "evidence_type": "verified",
                "dimension": "business",
                "claim": "business",
            },
            {
                "evidence_id": "EVID-DEAL-YUSHU-2026-001-000002",
                "evidence_type": "verified",
                "dimension": "finance",
                "claim": "finance",
            },
            {
                "evidence_id": "EVID-DEAL-YUSHU-2026-001-000003",
                "evidence_type": "verified",
                "dimension": "legal",
                "claim": "legal",
            },
            {
                "evidence_id": "EVID-DEAL-YUSHU-2026-001-000004",
                "evidence_type": "verified",
                "dimension": "risk",
                "claim": "risk",
            },
        ],
    )
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "agents": {
                "siq_ic_strategist": {
                    "receipt_id": "startup-siq_ic_strategist-R1-001",
                    "agent_id": "siq_ic_strategist",
                    "round_name": "R1",
                    "query": "宇树科技",
                    "project_tag": "DEAL-YUSHU-2026-001",
                    "shared_hits": 1,
                    "private_hits": 0,
                    "workspace_rules_read": [],
                    "gaps": [],
                    "evidence_hits": [{"evidence_id": "EVID-DEAL-YUSHU-2026-001-000001"}],
                    "created_at": "2026-07-03T10:20:00+08:00",
                }
            },
        },
    )

    result = ic_agent_runtime.build_ic_agent_task_dry_run(
        "DEAL-YUSHU-2026-001",
        "siq_ic_strategist",
        wiki_root=tmp_path,
    )

    assert result["allowed"] is False
    assert "preflight_warn:retrieval.receipt_contract" in result["blocking_reasons"]
    assert "preflight_warn:evidence.gate" not in result["blocking_reasons"]


def test_workflow_r1_agent_run_dry_run_wraps_task_without_side_effects(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        industry="机器人",
        stage="Pre-IPO",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_verified_evidence_gate_pass(package_dir)
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "agents": {"siq_ic_strategist": _receipt_payload("siq_ic_strategist")},
        },
    )
    workflow_before = (package_dir / "phases" / "workflow_state.json").read_text(encoding="utf-8")
    audit_before = (package_dir / "audit" / "audit_log.json").read_text(encoding="utf-8")

    result = ic_agent_runtime.build_workflow_r1_agent_run_dry_run(
        "DEAL-YUSHU-2026-001",
        "siq_ic_strategist",
        wiki_root=tmp_path,
    )

    assert result["schema_version"] == "siq_ic_workflow_r1_agent_run_dry_run_v1"
    assert result["workflow_action"] == "run-r1-agent"
    assert result["dry_run"] is True
    assert result["queued"] is False
    assert result["job_id"] is None
    assert result["allowed"] is True
    assert result["would_queue"] is True
    assert result["agent_task"]["schema_version"] == "siq_ic_agent_task_dry_run_v1"
    assert result["payload"]["startup_receipt_id"] == "startup-siq_ic_strategist-R1-001"
    assert result["hermes_called"] is False
    assert result["report_written"] is False
    assert result["workflow_advanced"] is False
    assert "/home/maoyd" not in json.dumps(result, ensure_ascii=False)
    assert (package_dir / "phases" / "workflow_state.json").read_text(encoding="utf-8") == workflow_before
    assert (package_dir / "audit" / "audit_log.json").read_text(encoding="utf-8") == audit_before


def test_workflow_r1_agent_run_calls_hermes_and_persists_report(tmp_path, monkeypatch):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        industry="机器人",
        stage="Pre-IPO",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_verified_evidence_gate_pass(package_dir)
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "agents": {"siq_ic_strategist": _receipt_payload("siq_ic_strategist")},
        },
    )

    calls: list[dict[str, str]] = []

    async def fake_create_run(input, conversation_history, *, profile, session_id=None):
        calls.append({"profile": profile, "session_id": session_id, "input": str(input)})
        assert conversation_history == []
        return "run-strategist-001"

    async def fake_collect_run_result(run_id, *, profile, timeout=None):
        assert run_id == "run-strategist-001"
        assert profile == "siq_ic_strategist"
        return json.dumps(_r1_model_output(profile, score=82))

    monkeypatch.setattr(ic_agent_runtime.hermes_client, "create_run", fake_create_run)
    monkeypatch.setattr(ic_agent_runtime.hermes_client, "collect_run_result", fake_collect_run_result)

    result = asyncio.run(
        ic_agent_runtime.run_workflow_r1_agent(
            "DEAL-YUSHU-2026-001",
            "siq_ic_strategist",
            wiki_root=tmp_path,
            created_by={"id": 7, "username": "ic-admin"},
        )
    )

    assert result["schema_version"] == "siq_ic_workflow_r1_agent_run_v1"
    assert result["dry_run"] is False
    assert result["hermes_called"] is True
    assert result["hermes_run_id"] == "run-strategist-001"
    assert result["report_written"] is True
    assert result["workflow_advanced"] is True
    assert calls[0]["profile"] == "siq_ic_strategist"
    assert "siq_ic_agent_task_v2" in calls[0]["input"]
    report_path = package_dir / "discussion" / "01_R1_strategist_report.md"
    assert report_path.is_file()
    assert "## 核心判断与证据" in report_path.read_text(encoding="utf-8")
    reports = json.loads((package_dir / "phases" / "r1_reports.json").read_text(encoding="utf-8"))
    report = reports["siq_ic_strategist"]
    assert report["schema_version"] == "siq_ic_expert_report_v2"
    assert report["deal_id"] == "DEAL-YUSHU-2026-001"
    assert report["phase"] == "R1A"
    assert report["score"] == 82
    assert report["recommendation"] == "conditional_support"
    assert report["startup_receipt_id"] == "startup-siq_ic_strategist-R1-001"
    assert report["artifact_path"] == "discussion/01_R1_strategist_report.md"
    workflow = json.loads((package_dir / "phases" / "workflow_state.json").read_text(encoding="utf-8"))
    assert workflow["current_phase"] == "R1"
    assert workflow["status"] == "r1_in_progress"
    assert workflow["phases"]["R1"]["submitted_agents"] == ["siq_ic_strategist"]
    audit = json.loads((package_dir / "audit" / "audit_log.json").read_text(encoding="utf-8"))
    assert audit["events"][-1]["event_type"] == "deal_r1_agent_run_completed"
    assert audit["events"][-1]["agent_id"] == "siq_ic_strategist"


def test_workflow_r1_agent_run_atomically_rejects_duplicate_worker(tmp_path, monkeypatch):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        industry="机器人",
        stage="Pre-IPO",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_verified_evidence_gate_pass(package_dir)
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "agents": {"siq_ic_strategist": _receipt_payload("siq_ic_strategist")},
        },
    )
    entered_hermes = asyncio.Event()
    release_hermes = asyncio.Event()
    hermes_calls = 0

    async def fake_create_run(input, conversation_history, *, profile, session_id=None):
        nonlocal hermes_calls
        del input, conversation_history, profile, session_id
        hermes_calls += 1
        entered_hermes.set()
        await release_hermes.wait()
        return "run-atomic-claim-001"

    async def fake_collect_run_result(run_id, *, profile, timeout=None):
        del run_id, profile, timeout
        return json.dumps(_r1_model_output("siq_ic_strategist"))

    monkeypatch.setattr(ic_agent_runtime.hermes_client, "create_run", fake_create_run)
    monkeypatch.setattr(ic_agent_runtime.hermes_client, "collect_run_result", fake_collect_run_result)

    async def compete():
        first = asyncio.create_task(
            ic_agent_runtime.run_workflow_r1_agent(
                "DEAL-YUSHU-2026-001",
                "siq_ic_strategist",
                wiki_root=tmp_path,
            )
        )
        await entered_hermes.wait()
        with pytest.raises(ic_agent_runtime.ICTaskAlreadyClaimedError):
            await ic_agent_runtime.run_workflow_r1_agent(
                "DEAL-YUSHU-2026-001",
                "siq_ic_strategist",
                wiki_root=tmp_path,
            )
        release_hermes.set()
        return await first

    result = asyncio.run(compete())

    assert hermes_calls == 1
    assert result["task_claim"]["status"] == "succeeded"
    assert result["task_claim"]["attempt"] == 1
    leases = json.loads((package_dir / "phases" / "ic_task_leases.json").read_text(encoding="utf-8"))
    assert leases["claims"][0]["status"] == "succeeded"


def test_workflow_r1_agent_run_rejects_invalid_hermes_contract_without_phase_side_effects(tmp_path, monkeypatch):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        industry="机器人",
        stage="Pre-IPO",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_verified_evidence_gate_pass(package_dir)
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "agents": {"siq_ic_strategist": _receipt_payload("siq_ic_strategist")},
        },
    )

    async def fake_create_run(input, conversation_history, *, profile, session_id=None):
        del input, conversation_history, profile, session_id
        return "run-invalid-r1-001"

    async def fake_collect_run_result(run_id, *, profile, timeout=None):
        del run_id, profile, timeout
        return (
            "## 检索结果摘要\n\n### 共享底稿证据\n\n### 私有知识库证据\n\n"
            "### 信息缺口清单\n\n### 检索后观点\n\n缺少关键 JSON 字段。\n"
            "```json\n"
            '{"recommendation": "conditional_pass", '
            '"verified": ["market evidence"], "assumed": [], '
            '"open_questions": [], '
            '"evidence_ids": ["EVID-DEAL-YUSHU-2026-001-UNKNOWN"]}\n'
            "```"
        )

    monkeypatch.setattr(ic_agent_runtime.hermes_client, "create_run", fake_create_run)
    monkeypatch.setattr(ic_agent_runtime.hermes_client, "collect_run_result", fake_collect_run_result)

    with pytest.raises(ValueError, match="response_must_be_single_json_object"):
        asyncio.run(
            ic_agent_runtime.run_workflow_r1_agent(
                "DEAL-YUSHU-2026-001",
                "siq_ic_strategist",
                wiki_root=tmp_path,
                created_by={"id": 7, "username": "ic-admin"},
            )
        )

    assert not (package_dir / "discussion" / "01_R1_strategist_report.md").exists()
    assert not (package_dir / "phases" / "r1_reports.json").exists()
    workflow = json.loads((package_dir / "phases" / "workflow_state.json").read_text(encoding="utf-8"))
    assert workflow["current_phase"] == "R0"
    assert workflow["phases"]["R1"]["status"] == "pending"
    audit = json.loads((package_dir / "audit" / "audit_log.json").read_text(encoding="utf-8"))
    assert not any(
        event["event_type"] == "ic_phase_hermes_contract_repair_attempted"
        for event in audit["events"]
    )
    assert audit["events"][-1]["event_type"] == "ic_phase_hermes_task_failed"
    assert audit["events"][-1]["contract_validation"]["passed"] is False
    tasks = json.loads((package_dir / "phases" / "ic_agent_tasks.json").read_text(encoding="utf-8"))
    assert tasks["tasks"][-1]["status"] == "failed"
    assert len(tasks["tasks"][-1]["hermes_run_ids"]) == 1


def test_workflow_r1_agent_run_rejects_invalid_v2_contract_types(tmp_path, monkeypatch):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        industry="机器人",
        stage="Pre-IPO",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_verified_evidence_gate_pass(package_dir)
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "agents": {
                "siq_ic_strategist": _receipt_payload(
                    "siq_ic_strategist",
                    evidence_id="EVID-DEAL-YUSHU-2026-001-000002",
                )
            },
        },
    )

    async def fake_create_run(input, conversation_history, *, profile, session_id=None):
        del input, conversation_history, profile, session_id
        return "run-invalid-r1-types-001"

    async def fake_collect_run_result(run_id, *, profile, timeout=None):
        del run_id, profile, timeout
        return (
            "## 检索结果摘要\n\n### 共享底稿证据\n\n### 私有知识库证据\n\n"
            "### 信息缺口清单\n\n### 检索后观点\n\n字段类型和 receipt 引用不合格。\n"
            "```json\n"
            '{"score": 80, "recommendation": "conditional_pass", '
            '"verified": "market evidence", "assumed": [], '
            '"open_questions": [], '
            '"startup_receipt_id": "startup-siq_ic_strategist-R1-001", '
            '"evidence_ids": ["EVID-DEAL-YUSHU-2026-001-000001"]}\n'
            "```"
        )

    monkeypatch.setattr(ic_agent_runtime.hermes_client, "create_run", fake_create_run)
    monkeypatch.setattr(ic_agent_runtime.hermes_client, "collect_run_result", fake_collect_run_result)

    with pytest.raises(ValueError, match="response_must_be_single_json_object"):
        asyncio.run(
            ic_agent_runtime.run_workflow_r1_agent(
                "DEAL-YUSHU-2026-001",
                "siq_ic_strategist",
                wiki_root=tmp_path,
            )
        )

    assert not (package_dir / "discussion" / "01_R1_strategist_report.md").exists()
    assert not (package_dir / "phases" / "r1_reports.json").exists()
    tasks = json.loads((package_dir / "phases" / "ic_agent_tasks.json").read_text(encoding="utf-8"))
    assert tasks["tasks"][-1]["status"] == "failed"
    assert "response_must_be_single_json_object" in tasks["tasks"][-1]["failure_reason"]
    assert tasks["tasks"][-1]["contract_validation"]["passed"] is False


def test_workflow_r1_serial_dry_run_plans_contiguous_agents(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        industry="机器人",
        stage="Pre-IPO",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_verified_evidence_gate_pass(package_dir)
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "agents": {
                "siq_ic_strategist": _receipt_payload("siq_ic_strategist"),
                "siq_ic_sector_expert": _receipt_payload("siq_ic_sector_expert"),
            },
        },
    )

    plan = ic_agent_runtime.build_workflow_r1_serial_run_dry_run(
        "DEAL-YUSHU-2026-001",
        max_agents=2,
        wiki_root=tmp_path,
    )

    assert plan["schema_version"] == "siq_ic_workflow_r1_serial_run_dry_run_v1"
    assert plan["dry_run"] is True
    assert plan["planned_agent_ids"] == ["siq_ic_strategist", "siq_ic_sector_expert"]
    assert plan["next_agent_id"] == "siq_ic_strategist"
    assert plan["stop_reason"] == "max_agents_reached"
    assert [item["action"] for item in plan["agents"]] == ["would_run", "would_run", "not_planned_max_agents"]
    assert plan["hermes_called"] is False


def test_ic_workflow_state_snapshot_tracks_r1_active_agent(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        industry="机器人",
        stage="Pre-IPO",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_verified_evidence_gate_pass(package_dir)
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "agents": {
                "siq_ic_strategist": _receipt_payload("siq_ic_strategist"),
                "siq_ic_sector_expert": _receipt_payload("siq_ic_sector_expert"),
            },
        },
    )
    _write_json(
        package_dir / "phases" / "r1_reports.json",
        {
            "siq_ic_strategist": {
                "agent_id": "siq_ic_strategist",
                "round_name": "R1",
                "status": "completed",
                "score": 80,
                "recommendation": "SUPPORT",
            }
        },
    )
    workflow = deal_store.read_json(package_dir / "phases" / "workflow_state.json", {}) or {}
    workflow["phases"]["R1"]["submitted_agents"] = ["siq_ic_strategist"]
    deal_store.write_json(package_dir / "phases" / "workflow_state.json", workflow)

    state = ic_workflow.summarize_workflow_state(
        "DEAL-YUSHU-2026-001",
        allow_hermes=True,
        write_snapshot=True,
        created_by={"id": 7, "username": "ic-admin", "email": "hidden@example.com"},
        wiki_root=tmp_path,
    )

    r1 = state["rounds"]["R1"]
    assert state["schema_version"] == "siq_ic_workflow_state_v1"
    assert r1["submitted_agents"] == ["siq_ic_strategist"]
    assert r1["active_agent"] == "siq_ic_sector_expert"
    assert r1["counts"]["startup_receipts"] == 2
    assert r1["counts"]["reports"] == 1
    assert state["transition_plan"]["selected_action"] == "run-r0-coordinator"
    assert state["written"] is True
    assert (package_dir / "phases" / "round_state.json").is_file()
    assert "hidden@example.com" not in json.dumps(state, ensure_ascii=False)
    audit = deal_store.read_json(package_dir / "audit" / "audit_log.json", {}) or {}
    assert audit["events"][-1]["event_type"] == "deal_workflow_round_state_snapshot_written"


def test_workflow_r1_serial_run_executes_planned_agents_in_order(tmp_path, monkeypatch):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        industry="机器人",
        stage="Pre-IPO",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_verified_evidence_gate_pass(package_dir)
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "agents": {
                "siq_ic_strategist": _receipt_payload("siq_ic_strategist"),
                "siq_ic_sector_expert": _receipt_payload("siq_ic_sector_expert"),
            },
        },
    )

    calls: list[str] = []

    async def fake_create_run(input, conversation_history, *, profile, session_id=None):
        del input, conversation_history, session_id
        calls.append(profile)
        return f"run-{profile}"

    async def fake_collect_run_result(run_id, *, profile, timeout=None):
        del timeout
        assert run_id == f"run-{profile}"
        return json.dumps(_r1_model_output(profile))

    monkeypatch.setattr(ic_agent_runtime.hermes_client, "create_run", fake_create_run)
    monkeypatch.setattr(ic_agent_runtime.hermes_client, "collect_run_result", fake_collect_run_result)

    result = asyncio.run(
        ic_agent_runtime.run_workflow_r1_serial(
            "DEAL-YUSHU-2026-001",
            max_agents=2,
            wiki_root=tmp_path,
            created_by={"id": 7, "username": "ic-admin"},
        )
    )

    assert result["schema_version"] == "siq_ic_workflow_r1_serial_run_v1"
    assert result["dry_run"] is False
    assert result["executed_agent_ids"] == ["siq_ic_strategist", "siq_ic_sector_expert"]
    assert calls == ["siq_ic_strategist", "siq_ic_sector_expert"]
    assert result["hermes_called"] is True
    reports = json.loads((package_dir / "phases" / "r1_reports.json").read_text(encoding="utf-8"))
    assert sorted(reports) == ["siq_ic_sector_expert", "siq_ic_strategist"]
    workflow = json.loads((package_dir / "phases" / "workflow_state.json").read_text(encoding="utf-8"))
    assert workflow["phases"]["R1"]["submitted_agents"] == ["siq_ic_strategist", "siq_ic_sector_expert"]
    audit = json.loads((package_dir / "audit" / "audit_log.json").read_text(encoding="utf-8"))
    assert audit["events"][-1]["event_type"] == "deal_r1_serial_run_completed"
    assert audit["events"][-1]["executed_agent_ids"] == ["siq_ic_strategist", "siq_ic_sector_expert"]
    duplicate = ic_agent_runtime.build_ic_agent_task_dry_run(
        "DEAL-YUSHU-2026-001",
        "siq_ic_strategist",
        wiki_root=tmp_path,
    )
    assert duplicate["allowed"] is False
    assert "agent_already_submitted" in duplicate["blocking_reasons"]


def test_workflow_advance_next_blocks_r0_hermes_by_default(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        industry="机器人",
        stage="Pre-IPO",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_verified_evidence_gate_pass(package_dir)
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "agents": {"siq_ic_strategist": _receipt_payload("siq_ic_strategist")},
        },
    )

    plan = ic_agent_runtime.build_workflow_advance_next_dry_run(
        "DEAL-YUSHU-2026-001",
        wiki_root=tmp_path,
    )

    assert plan["schema_version"] == "siq_ic_workflow_advance_next_dry_run_v1"
    assert plan["selected_action"] == "run-r0-coordinator"
    assert plan["requires_hermes"] is True
    assert plan["allowed"] is False
    assert "r0_coordinator_requires_allow_hermes" in plan["blocking_reasons"]
    assert plan["action_dry_run"]["phase"] == "R0"


def test_workflow_advance_next_never_uses_deterministic_fallback_implicitly(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        industry="机器人",
        stage="Pre-IPO",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_minimum_complete_deal_contract(package_dir)
    (package_dir / "phases" / "r4_decision.json").unlink()
    (package_dir / "decision" / "IC_DECISION_REPORT.md").unlink()

    plan = ic_agent_runtime.build_workflow_advance_next_dry_run(
        "DEAL-YUSHU-2026-001",
        wiki_root=tmp_path,
    )
    assert plan["allowed"] is False
    assert plan["selected_action"] == "run-r0-coordinator"
    assert plan["requires_hermes"] is True
    with pytest.raises(ValueError, match="advance-next blocked"):
        asyncio.run(
            ic_agent_runtime.run_workflow_advance_next(
                "DEAL-YUSHU-2026-001",
                wiki_root=tmp_path,
                created_by={"id": 7, "username": "ic-admin"},
            )
        )
    assert not (package_dir / "phases" / "r2_reports.json").exists()
    assert not (package_dir / "phases" / "r3_reports.json").exists()


def test_workflow_advance_next_preserves_explicit_blocked_phase_result(monkeypatch):
    monkeypatch.setattr(
        ic_agent_runtime,
        "build_workflow_advance_next_dry_run",
        lambda *args, **kwargs: {
            "selected_action": "run-r3",
            "allowed": True,
            "requires_hermes": True,
        },
    )

    async def blocked_r3(*args, **kwargs):
        return {
            "phase": "R3",
            "status": "blocked",
            "hermes_called": True,
            "workflow_advanced": False,
            "workflow": {"status": "r3_blocked"},
        }

    monkeypatch.setattr(ic_agent_runtime, "run_workflow_r3_async", blocked_r3)

    result = asyncio.run(
        ic_agent_runtime.run_workflow_advance_next(
            "DEAL-YUSHU-2026-001",
            allow_hermes=True,
        )
    )

    assert result["action_result"]["status"] == "blocked"
    assert result["workflow_advanced"] is False


def test_workflow_r2_r3_r4_deterministic_closes_artifact_loop(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        industry="机器人",
        stage="Pre-IPO",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_minimum_complete_deal_contract(package_dir)
    (package_dir / "phases" / "r4_decision.json").unlink()
    (package_dir / "decision" / "IC_DECISION_REPORT.md").unlink()
    reports = json.loads((package_dir / "phases" / "r1_reports.json").read_text(encoding="utf-8"))
    reports["siq_ic_chairman"]["score"] = 78
    reports["siq_ic_finance_auditor"]["open_questions"] = ["补充 IPO 估值敏感性"]
    deal_store.write_json(package_dir / "phases" / "r1_reports.json", reports)
    _write_json(
        package_dir / "phases" / "r1_5_disputes.json",
        {
            "schema_version": "siq_ic_disputes_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "disputes": [
                {
                    "dispute_id": "DISP-001",
                    "topic": "估值保护",
                    "dimension": "finance",
                    "severity": "high",
                    "positions": [],
                    "chairman_ruling": {
                        "agent_id": "siq_ic_chairman",
                        "decision": "resolved_with_conditions",
                        "rationale": "加入估值和退出保护条款",
                        "required_followups": ["设置 IPO 时间表触发的回购保护"],
                    },
                    "resolved": True,
                }
            ],
        },
    )

    r2_dry_run = ic_agent_runtime.build_workflow_r2_run_dry_run("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    assert r2_dry_run["schema_version"] == "siq_ic_workflow_r2_run_dry_run_v1"
    assert r2_dry_run["allowed"] is True
    assert r2_dry_run["report_written"] is False
    assert not (package_dir / "phases" / "r2_reports.json").is_file()

    r2 = ic_agent_runtime.run_workflow_r2(
        "DEAL-YUSHU-2026-001",
        wiki_root=tmp_path,
        created_by={"id": 7, "username": "ic-admin"},
    )

    assert r2["schema_version"] == "siq_ic_workflow_r2_run_v1"
    assert r2["hermes_called"] is False
    assert r2["report_written"] is True
    assert r2["workflow_advanced"] is True
    assert (package_dir / "phases" / "r2_reports.json").is_file()
    assert (package_dir / deal_reports.R2_REPORT_ARTIFACT_PATH).is_file()
    r2_reports = json.loads((package_dir / "phases" / "r2_reports.json").read_text(encoding="utf-8"))
    assert sorted(r2_reports) == sorted(ic_agent_runtime.R2_AGENT_SEQUENCE)
    assert r2_reports["siq_ic_finance_auditor"]["round_name"] == "R2"
    assert r2_reports["siq_ic_finance_auditor"]["r2_score"] == 80
    workflow = json.loads((package_dir / "phases" / "workflow_state.json").read_text(encoding="utf-8"))
    assert workflow["status"] == "r2_completed"
    assert workflow["phases"]["R2"]["status"] == "completed"

    r3_dry_run = ic_agent_runtime.build_workflow_r3_run_dry_run(
        "DEAL-YUSHU-2026-001",
        skip=True,
        skip_reason="R2 已覆盖核心分歧，P0 留痕跳过。",
        wiki_root=tmp_path,
    )

    assert r3_dry_run["schema_version"] == "siq_ic_workflow_r3_run_dry_run_v1"
    assert r3_dry_run["allowed"] is True
    assert r3_dry_run["payload_preview"]["mode"] == "skip"

    r3 = ic_agent_runtime.run_workflow_r3(
        "DEAL-YUSHU-2026-001",
        skip=True,
        skip_reason="R2 已覆盖核心分歧，P0 留痕跳过。",
        wiki_root=tmp_path,
        created_by={"id": 7, "username": "ic-admin"},
    )

    assert r3["schema_version"] == "siq_ic_workflow_r3_run_v1"
    assert r3["mode"] == "skip"
    assert (package_dir / "phases" / "r3_reports.json").is_file()
    assert (package_dir / deal_reports.R3_REVIEW_ARTIFACT_PATH).is_file()
    r3_payload = json.loads((package_dir / "phases" / "r3_reports.json").read_text(encoding="utf-8"))
    assert r3_payload["mode"] == "skip"
    workflow = json.loads((package_dir / "phases" / "workflow_state.json").read_text(encoding="utf-8"))
    assert workflow["status"] == "r3_skipped"
    assert workflow["phases"]["R3"]["status"] == "skipped"

    r4_dry_run = ic_agent_runtime.build_workflow_r4_finalize_dry_run("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    assert r4_dry_run["schema_version"] == "siq_ic_workflow_r4_finalize_dry_run_v1"
    assert r4_dry_run["allowed"] is True
    assert r4_dry_run["decision_preview"]["weighted_agent_score"] == 79.4
    assert r4_dry_run["decision_preview"]["chairman_dimension_score"] == 78

    r4 = ic_agent_runtime.finalize_workflow_r4(
        "DEAL-YUSHU-2026-001",
        wiki_root=tmp_path,
        created_by={"id": 7, "username": "ic-admin"},
    )

    assert r4["schema_version"] == "siq_ic_workflow_r4_finalize_v1"
    assert r4["decision"]["schema_version"] == "siq_ic_r4_decision_v1"
    assert r4["decision"]["decision"] == "pass"
    assert r4["decision"]["weighted_agent_score"] == 79.4
    assert r4["decision"]["chairman_dimension_score"] == 78
    assert r4["decision"]["chairman_qualitative_decision"]
    assert r4["decision"]["human_confirmation"]["status"] == "pending"
    assert (package_dir / "phases" / "r4_decision.json").is_file()
    assert (package_dir / "decision" / "IC_DECISION_REPORT.md").is_file()
    assert (package_dir / "decision" / "IC_DECISION_REPORT.html").is_file()
    assert (package_dir / "decision" / "decision_payload.json").is_file()
    workflow = json.loads((package_dir / "phases" / "workflow_state.json").read_text(encoding="utf-8"))
    assert workflow["status"] == "r4_completed"
    assert workflow["phases"]["R4"]["human_confirmation_status"] == "pending"
    project_meta = json.loads((package_dir / "project_meta.json").read_text(encoding="utf-8"))
    assert project_meta["status"] == "r4_completed"
    assert project_meta["final_decision"] == "pass"
    assert project_meta["final_score"] == 78
    audit = json.loads((package_dir / "audit" / "audit_log.json").read_text(encoding="utf-8"))
    assert audit["events"][-3]["event_type"] == "deal_r2_run_completed"
    assert audit["events"][-2]["event_type"] == "deal_r3_run_completed"
    assert audit["events"][-1]["event_type"] == "r4_decision_generated"


def test_r1_agent_readiness_matrix_shares_preflight_and_sequence_rules(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        industry="机器人",
        stage="Pre-IPO",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    workflow = deal_store.read_json(package_dir / "phases" / "workflow_state.json", {}) or {}
    workflow["phases"]["R1"]["submitted_agents"] = ["siq_ic_strategist"]
    deal_store.write_json(package_dir / "phases" / "workflow_state.json", workflow)
    _write_verified_evidence_gate_pass(package_dir)
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v1",
            "deal_id": "DEAL-YUSHU-2026-001",
            "agents": {
                "siq_ic_strategist": _receipt_payload("siq_ic_strategist"),
                "siq_ic_finance_auditor": _receipt_payload(
                    "siq_ic_finance_auditor",
                    "EVID-DEAL-YUSHU-2026-001-000002",
                ),
            },
        },
    )

    result = ic_agent_runtime.build_r1_agent_readiness("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    assert result["schema_version"] == "siq_ic_r1_agent_readiness_v1"
    assert result["dry_run"] is True
    assert result["hermes_called"] is False
    assert result["report_written"] is False
    assert len(result["agents"]) == 6
    by_agent = {item["agent_id"]: item for item in result["agents"]}
    assert by_agent["siq_ic_strategist"]["allowed"] is True
    assert by_agent["siq_ic_strategist"]["submitted"] is True
    assert by_agent["siq_ic_finance_auditor"]["allowed"] is True
    assert by_agent["siq_ic_finance_auditor"]["blocking_reasons"] == []
    assert "startup_receipt_missing" in by_agent["siq_ic_sector_expert"]["blocking_reasons"]
    assert result["next_agent_id"] == "siq_ic_finance_auditor"
    assert "/home/maoyd" not in json.dumps(result, ensure_ascii=False)


def test_deal_evidence_build_is_idempotent_and_preflight_counts_items(tmp_path, monkeypatch):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    document = deal_documents.create_deal_document(
        deal_id="DEAL-YUSHU-2026-001",
        filename="bp.md",
        content_type="text/markdown",
        stream=BytesIO(b"bp"),
        document_type="business_plan",
        wiki_root=tmp_path,
    )
    parser_root = tmp_path / "parser-results"
    document_md = parser_root / "task-bp" / "document.md"
    document_md.parent.mkdir(parents=True)
    document_md.write_text(
        "# Business\n\nRobot demand is expanding.\n\nCustomers include industrial users.", encoding="utf-8"
    )
    monkeypatch.setattr(deal_documents, "DOCUMENT_PARSER_RESULTS_ROOT", parser_root)
    monkeypatch.setattr(deal_evidence, "DOCUMENT_PARSER_RESULTS_ROOT", parser_root)
    deal_documents.bind_parser_task(
        "DEAL-YUSHU-2026-001",
        document["document_id"],
        task_id="task-bp",
        artifact_path="document.md",
        wiki_root=tmp_path,
    )

    first = deal_evidence.build_deal_evidence_package("DEAL-YUSHU-2026-001", wiki_root=tmp_path)
    second = deal_evidence.build_deal_evidence_package("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    first_ids = [item["evidence_id"] for item in first["items_preview"]]
    second_ids = [item["evidence_id"] for item in second["items_preview"]]
    assert first_ids == second_ids
    assert second["counts"]["items"] == 1
    preflight = deal_contracts.run_deal_preflight("DEAL-YUSHU-2026-001", wiki_root=tmp_path)
    assert preflight["counts"]["evidence_items"] == 1
    assert preflight["counts"]["verified_evidence_items"] == 1
