import json
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from routers import deals
from services import deal_disputes
from services import deal_documents
from services import deal_evidence
from services import deal_phase_artifacts
from services import deal_reports
from services import deal_store
from services import ic_openclaw_importer
from services.auth_dependencies import get_current_user
from services.auth_service import User, UserRole


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _client(monkeypatch, tmp_path: Path) -> TestClient:
    monkeypatch.setattr(deal_store, "WIKI_ROOT", tmp_path / "wiki")
    app = FastAPI()
    app.include_router(deals.router, prefix="/api")

    async def current_user() -> User:
        return User(
            id=7,
            username="ic-admin",
            email="ic-admin@example.test",
            hashed_password="x",
            full_name="IC Admin",
            role=UserRole.SUPER_ADMIN,
        )

    app.dependency_overrides[get_current_user] = current_user
    return TestClient(app)


def test_deals_router_create_list_and_detail(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    created = client.post(
        "/api/deals",
        json={
            "deal_id": "DEAL-ROUTER-001",
            "company_name": "Router Robotics",
            "industry": "Robotics",
            "stage": "Series C",
        },
    )

    assert created.status_code == 200
    assert created.json()["deal"]["deal_id"] == "DEAL-ROUTER-001"

    listed = client.get("/api/deals", params={"q": "router"})
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["stats"]["total"] == 1
    assert payload["deals"][0]["company_name"] == "Router Robotics"

    detail = client.get("/api/deals/DEAL-ROUTER-001")
    assert detail.status_code == 200
    assert detail.json()["project_meta"]["created_by"] == {
        "id": 7,
        "username": "ic-admin",
    }


def test_deals_router_reports_index_and_detail(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.post(
        "/api/deals",
        json={"deal_id": "DEAL-ROUTER-REPORTS", "company_name": "Router Robotics"},
    ).status_code == 200
    package_dir = tmp_path / "wiki" / "deals" / "DEAL-ROUTER-REPORTS"
    _write_json(
        package_dir / "phases" / "r1_reports.json",
        {
            "siq_ic_strategist": {
                "agent_id": "siq_ic_strategist",
                "round_name": "R1",
                "score": 82,
                "recommendation": "SUPPORT",
                "verified": ["增长率"],
                "assumed": ["退出窗口"],
                "open_questions": ["核心客户续约"],
                "startup_receipt_id": "startup-siq_ic_strategist-R1-001",
                "source_root": "/tmp/hidden",
                "created_by": {"id": 7, "username": "ic-admin", "email": "hide@example.test"},
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
                }
            }
        },
    )
    _write_json(
        package_dir / "phases" / "r2_reports.json",
        {
            "reports": {
                "ic_risk_controller": {
                    "round_name": "R2",
                    "r2_score": 80,
                    "recommendation": "CAUTION",
                    "summary": "Risk follow-up completed.",
                    "revisions": ["Customer concentration sensitivity"],
                    "source_root": "/tmp/hidden",
                }
            }
        },
    )
    _write_json(
        package_dir / "phases" / "r3_reports.json",
        {
            "reports": {
                "ic_risk_controller": {
                    "stance": "red_team",
                    "recommendation": "REVIEW",
                    "summary": "Challenge customer concentration assumptions.",
                    "challenges": ["Customer concentration"],
                    "evidence_ids": ["EVID-001"],
                    "source_root": "/tmp/hidden",
                }
            }
        },
    )
    (package_dir / "discussion" / "01_R1_strategist_report.md").write_text("# R1\n\n战略窗口明确。", encoding="utf-8")
    (package_dir / deal_reports.R2_REPORT_ARTIFACT_PATH).write_text("# R2\n\nRisk follow-up.", encoding="utf-8")
    (package_dir / deal_reports.R3_REVIEW_ARTIFACT_PATH).write_text("# R3\n\nRed blue review.", encoding="utf-8")

    index = client.get("/api/deals/DEAL-ROUTER-REPORTS/reports")
    assert index.status_code == 200
    index_payload = index.json()
    assert index_payload["schema_version"] == "siq_deal_reports_index_v1"
    paths = {item["path"] for item in index_payload["reports"]}
    assert "phases/r1_reports.json" in paths
    assert "discussion/01_R1_strategist_report.md" in paths
    assert index_payload["counts"]["reports"] >= 3
    assert any(item["path"] == "decision/IC_DECISION_REPORT.md" for item in index_payload["missing_expected"])

    r1_agents = client.get("/api/deals/DEAL-ROUTER-REPORTS/reports/r1-agents")
    assert r1_agents.status_code == 200
    r1_payload = r1_agents.json()
    assert r1_payload["schema_version"] == "siq_deal_r1_reports_summary_v1"
    assert r1_payload["agents"][0]["agent_id"] == "siq_ic_strategist"
    assert r1_payload["agents"][0]["startup_receipt_linkage"] == "match"
    assert "source_root" not in json.dumps(r1_payload, ensure_ascii=False)
    assert "/tmp/hidden" not in json.dumps(r1_payload, ensure_ascii=False)

    r2_agents = client.get("/api/deals/DEAL-ROUTER-REPORTS/reports/r2-agents")
    assert r2_agents.status_code == 200
    r2_payload = r2_agents.json()
    assert r2_payload["schema_version"] == "siq_deal_r2_reports_summary_v1"
    assert r2_payload["counts"]["agents"] == 5
    assert r2_payload["counts"]["reports"] == 1
    by_r2_agent = {item["agent_id"]: item for item in r2_payload["agents"]}
    assert by_r2_agent["siq_ic_risk_controller"]["status"] == "pass"
    assert by_r2_agent["siq_ic_risk_controller"]["r2_score"] == 80
    assert by_r2_agent["siq_ic_risk_controller"]["revision_count"] == 1
    assert "source_root" not in json.dumps(r2_payload, ensure_ascii=False)
    assert "/tmp/hidden" not in json.dumps(r2_payload, ensure_ascii=False)

    r3_review = client.get("/api/deals/DEAL-ROUTER-REPORTS/reports/r3-review")
    assert r3_review.status_code == 200
    r3_payload = r3_review.json()
    assert r3_payload["schema_version"] == "siq_deal_r3_review_summary_v1"
    assert r3_payload["status"] == "pass"
    assert r3_payload["mode"] == "normal"
    assert r3_payload["counts"]["reports"] == 1
    assert r3_payload["counts"]["challenges"] == 1
    assert r3_payload["reports"][0]["agent_id"] == "siq_ic_risk_controller"
    assert r3_payload["reports"][0]["challenge_count"] == 1
    assert "source_root" not in json.dumps(r3_payload, ensure_ascii=False)
    assert "/tmp/hidden" not in json.dumps(r3_payload, ensure_ascii=False)

    detail = client.get("/api/deals/DEAL-ROUTER-REPORTS/reports/phases/r1_reports.json")
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["schema_version"] == "siq_deal_report_detail_v1"
    assert detail_payload["report"]["path"] == "phases/r1_reports.json"
    assert "/tmp/hidden" not in detail_payload["content"]
    assert "hide@example.test" not in detail_payload["content"]
    assert detail_payload["json"]["siq_ic_strategist"]["created_by"] == {"id": 7, "username": "ic-admin"}

    missing = client.get("/api/deals/DEAL-ROUTER-REPORTS/reports/discussion/missing.md")
    assert missing.status_code == 404
    blocked = client.get("/api/deals/DEAL-ROUTER-REPORTS/reports/data_room/raw/secret.pdf")
    assert blocked.status_code == 400
    audit_blocked = client.get("/api/deals/DEAL-ROUTER-REPORTS/reports/audit/audit_log.json")
    assert audit_blocked.status_code == 400


def test_deals_router_decision_includes_r4_contract(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.post(
        "/api/deals",
        json={"deal_id": "DEAL-ROUTER-DECISION", "company_name": "Router Robotics"},
    ).status_code == 200
    package_dir = tmp_path / "wiki" / "deals" / "DEAL-ROUTER-DECISION"
    _write_json(
        package_dir / "phases" / "r4_decision.json",
        {
            "schema_version": "siq_ic_r4_decision_v1",
            "deal_id": "DEAL-ROUTER-DECISION",
            "decision": "pass",
            "final_score": 78.55,
            "weighted_agent_score": 84.2,
            "chairman_dimension_score": 78.55,
            "chairman_qualitative_decision": "Invest with valuation protection.",
            "conditions": ["Customer renewal validation"],
            "monitoring_metrics": ["IPO timetable"],
            "human_confirmation": {
                "status": "pending",
                "confirmed_by": None,
                "confirmed_at": None,
            },
            "artifact_paths": {
                "markdown": "decision/IC_DECISION_REPORT.md",
                "html": "decision/IC_DECISION_REPORT.html",
            },
        },
    )
    (package_dir / "decision" / "IC_DECISION_REPORT.md").write_text("# IC Decision\n\nPass.", encoding="utf-8")

    response = client.get("/api/deals/DEAL-ROUTER-DECISION/decision")

    assert response.status_code == 200
    payload = response.json()
    assert payload["decision"]["decision"] == "pass"
    assert payload["report_path"] == "decision/IC_DECISION_REPORT.md"
    assert payload["report_markdown"].startswith("# IC Decision")
    assert payload["contract"]["schema_version"] == "siq_deal_r4_decision_summary_v1"
    assert payload["contract"]["status"] == "pass"
    assert payload["contract"]["missing_required_fields"] == []
    assert payload["contract"]["human_confirmation"]["status"] == "pending"
    assert payload["contract"]["human_confirmation"]["confirmed"] is False
    assert payload["contract"]["artifacts"]["markdown"]["available"] is True
    assert payload["contract"]["artifacts"]["html"]["available"] is False

    dry_run = client.post(
        "/api/deals/DEAL-ROUTER-DECISION/decision/human-confirmation",
        json={"status": "confirmed", "dry_run": True},
    )
    assert dry_run.status_code == 200
    dry_run_payload = dry_run.json()
    assert dry_run_payload["schema_version"] == "siq_deal_r4_human_confirmation_update_v1"
    assert dry_run_payload["dry_run"] is True
    assert dry_run_payload["would_write"] is False
    assert dry_run_payload["human_confirmation"]["confirmed_by"] == {"id": 7, "username": "ic-admin"}
    stored_after_dry_run = json.loads((package_dir / "phases" / "r4_decision.json").read_text(encoding="utf-8"))
    assert stored_after_dry_run["human_confirmation"]["status"] == "pending"

    confirmed = client.post(
        "/api/deals/DEAL-ROUTER-DECISION/decision/human-confirmation",
        json={"status": "confirmed", "dry_run": False},
    )
    assert confirmed.status_code == 200
    confirmed_payload = confirmed.json()
    assert confirmed_payload["dry_run"] is False
    assert confirmed_payload["decision_contract"]["human_confirmation"]["status"] == "confirmed"
    stored_after_confirm = json.loads((package_dir / "phases" / "r4_decision.json").read_text(encoding="utf-8"))
    assert stored_after_confirm["human_confirmation"]["status"] == "confirmed"
    audit = json.loads((package_dir / "phases" / "audit_log.json").read_text(encoding="utf-8"))
    assert audit["events"][-1]["event_type"] == "r4_human_confirmation_updated"


def test_deals_router_audit_includes_summary(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.post(
        "/api/deals",
        json={"deal_id": "DEAL-ROUTER-AUDIT", "company_name": "Router Robotics"},
    ).status_code == 200

    response = client.get("/api/deals/DEAL-ROUTER-AUDIT/audit")

    assert response.status_code == 200
    payload = response.json()
    assert payload["audit"]["events"][0]["event_type"] == "deal_created"
    assert payload["summary"]["schema_version"] == "siq_deal_audit_summary_v1"
    assert payload["summary"]["status"] == "pass"
    assert payload["summary"]["sources"]["consistency"] == "match"
    assert payload["summary"]["counts"]["events"] == 1
    by_event = {item["event_type"]: item for item in payload["summary"]["required_event_status"]}
    assert by_event["deal_created"]["present"] is True
    assert by_event["openclaw_imported"]["required"] is False


def test_deals_router_status_aggregates_read_only_contracts(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.post(
        "/api/deals",
        json={"deal_id": "DEAL-ROUTER-STATUS", "company_name": "Router Robotics"},
    ).status_code == 200

    response = client.get("/api/deals/DEAL-ROUTER-STATUS/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "siq_deal_status_summary_v1"
    assert payload["deal_id"] == "DEAL-ROUTER-STATUS"
    assert payload["counts"]["components"] == 8
    assert {item["id"] for item in payload["components"]} == {
        "preflight",
        "r1_readiness",
        "r1_reports",
        "r1_5_disputes",
        "r2_reports",
        "r3_review",
        "r4_decision",
        "audit",
    }
    by_component = {item["id"]: item for item in payload["components"]}
    assert by_component["r1_5_disputes"]["href"] == "workflow"
    assert by_component["r1_5_disputes"]["blocking"] is False
    assert by_component["r2_reports"]["href"] == "reports"
    assert by_component["r2_reports"]["blocking"] is False
    assert by_component["r3_review"]["href"] == "reports"
    assert by_component["r3_review"]["blocking"] is False
    assert payload["sources"]["audit"]["schema_version"] == "siq_deal_audit_summary_v1"


def test_deals_router_agents_summary_lists_ic_profiles(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.post(
        "/api/deals",
        json={"deal_id": "DEAL-ROUTER-AGENTS", "company_name": "Router Robotics"},
    ).status_code == 200

    response = client.get("/api/deals/DEAL-ROUTER-AGENTS/agents")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "siq_deal_agents_summary_v1"
    assert payload["deal_id"] == "DEAL-ROUTER-AGENTS"
    assert payload["counts"]["agents"] == 7
    assert payload["counts"]["r1_agents"] == 6
    by_agent = {item["agent_id"]: item for item in payload["agents"]}
    assert set(by_agent) == {
        "siq_ic_master_coordinator",
        "siq_ic_chairman",
        "siq_ic_strategist",
        "siq_ic_sector_expert",
        "siq_ic_finance_auditor",
        "siq_ic_legal_scanner",
        "siq_ic_risk_controller",
    }
    assert by_agent["siq_ic_master_coordinator"]["status"] == "non_r1"
    assert by_agent["siq_ic_finance_auditor"]["is_r1_agent"] is True
    assert "siq_ic_finance_auditor" in payload["r1_agent_sequence"]
    assert "/home/maoyd" not in json.dumps(payload, ensure_ascii=False)


def test_deals_router_disputes_summary(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.post(
        "/api/deals",
        json={"deal_id": "DEAL-ROUTER-DISPUTES", "company_name": "Router Robotics"},
    ).status_code == 200
    package_dir = tmp_path / "wiki" / "deals" / "DEAL-ROUTER-DISPUTES"
    _write_json(
        package_dir / "phases" / "r1_5_disputes.json",
        {
            "schema_version": "siq_ic_disputes_v1",
            "source_root": "/tmp/hidden",
            "disputes": [
                {
                    "dispute_id": "DISP-ROUTER-001",
                    "topic": "Valuation support",
                    "dimension": "finance",
                    "severity": "high",
                    "positions": [
                        {"agent_id": "ic_finance_auditor", "evidence_ids": ["EVID-001"]},
                        {"agent_id": "ic_risk_controller", "evidence_ids": ["EVID-002"]},
                    ],
                    "chairman_ruling": {
                        "decision": "resolved_with_conditions",
                        "required_followups": ["Sensitivity analysis"],
                    },
                    "resolved": True,
                }
            ],
        },
    )
    (package_dir / deal_disputes.DISPUTES_MARKDOWN_PATH).write_text("# R1.5\n", encoding="utf-8")

    response = client.get("/api/deals/DEAL-ROUTER-DISPUTES/disputes")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "siq_deal_r1_5_disputes_summary_v1"
    assert payload["deal_id"] == "DEAL-ROUTER-DISPUTES"
    assert payload["status"] == "pass"
    assert payload["counts"]["disputes"] == 1
    assert payload["counts"]["resolved"] == 1
    assert payload["counts"]["unresolved"] == 0
    assert payload["counts"]["positions"] == 2
    assert payload["counts"]["rulings"] == 1
    assert payload["counts"]["high_severity"] == 1
    assert payload["counts"]["artifacts"] == 2
    assert payload["artifacts"]["json"]["path"] == "phases/r1_5_disputes.json"
    assert payload["artifacts"]["markdown"]["available"] is True
    assert payload["disputes"][0]["agent_ids"] == ["siq_ic_finance_auditor", "siq_ic_risk_controller"]
    assert payload["disputes"][0]["evidence_ids"] == ["EVID-001", "EVID-002"]
    assert payload["disputes"][0]["required_followups"] == ["Sensitivity analysis"]
    assert payload["warnings"] == []
    assert "source_root" not in json.dumps(payload, ensure_ascii=False)
    assert "/tmp/hidden" not in json.dumps(payload, ensure_ascii=False)


def test_deals_router_phase_artifacts_summary(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.post(
        "/api/deals",
        json={"deal_id": "DEAL-ROUTER-PHASES", "company_name": "Router Robotics"},
    ).status_code == 200
    package_dir = tmp_path / "wiki" / "deals" / "DEAL-ROUTER-PHASES"
    _write_json(
        package_dir / "phases" / "r2_reports.json",
        {
            "reports": {
                "ic_risk_controller": {
                    "summary": "Risk follow-up completed.",
                    "recommendation": "SUPPORT",
                    "source_root": "/tmp/hidden",
                }
            }
        },
    )
    (package_dir / deal_phase_artifacts.R2_MARKDOWN_PATH).write_text("# R2\n", encoding="utf-8")
    _write_json(package_dir / "phases" / "r3_reports.json", {"mode": "skip", "reports": {}})

    response = client.get("/api/deals/DEAL-ROUTER-PHASES/phase-artifacts")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "siq_deal_phase_artifacts_summary_v1"
    assert payload["deal_id"] == "DEAL-ROUTER-PHASES"
    assert payload["counts"]["phases"] == 6
    by_phase = {item["phase"]: item for item in payload["phases"]}
    assert by_phase["R2"]["status"] == "pass"
    assert by_phase["R2"]["artifacts"]["json"]["path"] == "phases/r2_reports.json"
    assert by_phase["R2"]["artifacts"]["markdown"]["available"] is True
    assert by_phase["R2"]["items_preview"][0]["agent_id"] == "siq_ic_risk_controller"
    assert by_phase["R3"]["status"] == "pass"
    assert by_phase["R3"]["mode"] == "skip"
    assert by_phase["R3"]["blocking"] is False
    assert "source_root" not in json.dumps(payload, ensure_ascii=False)
    assert "/tmp/hidden" not in json.dumps(payload, ensure_ascii=False)


def test_deals_router_manifest_includes_import_summary(monkeypatch, tmp_path):
    openclaw_root = tmp_path / "openclaw" / "projects"
    source = openclaw_root / "SIQ-ROUTER-MANIFEST"
    _write_json(source / "project_meta.json", {"company_name": "Router Robotics"})
    _write_json(source / "phases" / "workflow_state.json", {"company_name": "Router Robotics"})
    monkeypatch.setattr(deals, "DEFAULT_OPENCLAW_PROJECTS_ROOT", openclaw_root)
    monkeypatch.setattr(ic_openclaw_importer, "DEFAULT_OPENCLAW_PROJECTS_ROOT", openclaw_root)
    client = _client(monkeypatch, tmp_path)
    assert client.post(
        "/api/deals/import/openclaw",
        params={"wait": "true"},
        json={"project_id": "SIQ-ROUTER-MANIFEST", "deal_id": "DEAL-ROUTER-MANIFEST"},
    ).status_code == 200

    response = client.get("/api/deals/DEAL-ROUTER-MANIFEST/manifest")

    assert response.status_code == 200
    payload = response.json()
    assert payload["manifest"]["openclaw_import"]["legacy_project_id"] == "SIQ-ROUTER-MANIFEST"
    assert payload["summary"]["schema_version"] == "siq_deal_manifest_summary_v1"
    assert payload["summary"]["openclaw_import"]["present"] is True
    assert payload["summary"]["archive_manifest"]["consistency"] == "match"
    assert payload["summary"]["counts"]["files_missing_hash"] == 0
    assert "source_root" not in json.dumps(payload, ensure_ascii=False)
    assert "/home/maoyd" not in json.dumps(payload, ensure_ascii=False)


def test_deals_router_wait_import_accepts_project_id(monkeypatch, tmp_path):
    openclaw_root = tmp_path / "openclaw" / "projects"
    source = openclaw_root / "SIQ-ROUTER-2026-001"
    _write_json(
        source / "project_meta.json",
        {"company_name": "Router Robotics", "industry": "Robotics"},
    )
    _write_json(
        source / "phases" / "workflow_state.json",
        {"company_name": "Router Robotics", "final_decision": "pass"},
    )
    monkeypatch.setattr(deals, "DEFAULT_OPENCLAW_PROJECTS_ROOT", openclaw_root)
    monkeypatch.setattr(ic_openclaw_importer, "DEFAULT_OPENCLAW_PROJECTS_ROOT", openclaw_root)
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/deals/import/openclaw",
        params={"wait": "true"},
        json={"project_id": "SIQ-ROUTER-2026-001", "deal_id": "DEAL-ROUTER-002"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["deal"]["project_meta"]["legacy_project_id"] == "SIQ-ROUTER-2026-001"
    assert "source_root" not in payload["deal"]["manifest"]["openclaw_import"]
    assert payload["archive_manifest"]["source_root"] == "SIQ-ROUTER-2026-001"
    assert payload["deal"]["summary"]["package_path"] == "deals/DEAL-ROUTER-002"


def test_deals_router_async_import_queues_compact_job(monkeypatch, tmp_path):
    seen = {}

    class FakeJobService:
        def start(self, kind, target, *, created_by=None):
            seen["kind"] = kind
            seen["created_by"] = created_by
            seen["target"] = target
            return {
                "job_id": "deal-openclaw-import-test",
                "kind": kind,
                "status": "queued",
            }

        def get(self, job_id):
            if job_id == "deal-openclaw-import-test":
                return {
                    "job_id": job_id,
                    "status": "succeeded",
                    "created_by": {"id": 7, "username": "ic-admin"},
                    "result": {"ok": True, "deal_id": "DEAL-ROUTER-003"},
                }
            return None

    monkeypatch.setattr(deals, "deal_job_service", FakeJobService())
    monkeypatch.setattr(deals, "_run_openclaw_import_job", lambda payload, created_by: {"ok": True})
    client = _client(monkeypatch, tmp_path)

    queued = client.post(
        "/api/deals/import/openclaw",
        json={
            "source_root": str(tmp_path / "openclaw" / "projects" / "SIQ-ROUTER-2026-003"),
            "deal_id": "DEAL-ROUTER-003",
        },
    )

    assert queued.status_code == 200
    assert queued.json()["queued"] is True
    assert queued.json()["job_id"] == "deal-openclaw-import-test"
    assert seen["kind"] == "deal-openclaw-import"
    assert seen["created_by"] == {"id": 7, "username": "ic-admin"}

    status = client.get("/api/deals/jobs/deal-openclaw-import-test")
    assert status.status_code == 200
    assert status.json()["created_by"] == {"id": 7, "username": "ic-admin"}


def test_deals_router_data_room_document_lifecycle(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.post(
        "/api/deals",
        json={"deal_id": "DEAL-ROUTER-004", "company_name": "Router Robotics"},
    )
    assert response.status_code == 200

    upload = client.post(
        "/api/deals/DEAL-ROUTER-004/documents",
        data={"document_type": "business_plan", "source_note": "founder upload"},
        files={"file": ("../bp.pdf", b"router bp", "application/pdf")},
    )
    assert upload.status_code == 200
    document = upload.json()["document"]
    assert document["document_id"].startswith("DOC-")
    assert document["original_filename"] == "bp.pdf"
    assert document["document_type"] == "business_plan"
    assert document["created_by"] == {"id": 7, "username": "ic-admin"}
    assert not document["storage_path"].startswith("/")

    listed = client.get("/api/deals/DEAL-ROUTER-004/documents")
    assert listed.status_code == 200
    assert listed.json()["documents"][0]["document_id"] == document["document_id"]

    detail = client.get(f"/api/deals/DEAL-ROUTER-004/documents/{document['document_id']}")
    assert detail.status_code == 200
    assert detail.json()["document"]["sha256"] == document["sha256"]

    deleted = client.delete(f"/api/deals/DEAL-ROUTER-004/documents/{document['document_id']}")
    assert deleted.status_code == 200
    assert deleted.json() == {"ok": True, "document_id": document["document_id"]}
    assert client.get("/api/deals/DEAL-ROUTER-004/documents").json()["documents"] == []


def test_deals_router_bind_parser_task_updates_document(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.post(
        "/api/deals",
        json={"deal_id": "DEAL-ROUTER-005", "company_name": "Router Robotics"},
    ).status_code == 200
    upload = client.post(
        "/api/deals/DEAL-ROUTER-005/documents",
        files={"file": ("bp.pdf", b"router bp", "application/pdf")},
    )
    assert upload.status_code == 200
    document_id = upload.json()["document"]["document_id"]

    response = client.post(
        f"/api/deals/DEAL-ROUTER-005/documents/{document_id}/bind-parser-task",
        json={"task_id": "parser-task-router-1", "artifact_path": "document.md", "note": "manual bind"},
    )

    assert response.status_code == 200
    document = response.json()["document"]
    assert document["status"] == "parse_bound"
    assert document["parse_task_id"] == "parser-task-router-1"
    assert document["parsed_artifact_path"] == "document.md"
    assert document["parser_page_url"] == "/documents?task=parser-task-router-1"
    assert document["parse_bound_by"] == {"id": 7, "username": "ic-admin"}

    detail = client.get(f"/api/deals/DEAL-ROUTER-005/documents/{document_id}")
    assert detail.status_code == 200
    assert detail.json()["document"]["parse_task_id"] == "parser-task-router-1"


def test_deals_router_bind_parser_task_requires_task_access(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.post(
        "/api/deals",
        json={"deal_id": "DEAL-ROUTER-006", "company_name": "Router Robotics"},
    ).status_code == 200
    upload = client.post(
        "/api/deals/DEAL-ROUTER-006/documents",
        files={"file": ("bp.pdf", b"router bp", "application/pdf")},
    )
    assert upload.status_code == 200
    document_id = upload.json()["document"]["document_id"]

    async def deny_task_access(*args, **kwargs):
        return False

    monkeypatch.setattr(deals, "_user_has_document_task_access", deny_task_access)

    response = client.post(
        f"/api/deals/DEAL-ROUTER-006/documents/{document_id}/bind-parser-task",
        json={"task_id": "parser-task-other-user"},
    )

    assert response.status_code == 403
    assert "does not belong" in response.json()["detail"]
    detail = client.get(f"/api/deals/DEAL-ROUTER-006/documents/{document_id}")
    assert detail.status_code == 200
    assert detail.json()["document"]["parse_task_id"] is None


def test_deals_router_build_and_read_evidence(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.post(
        "/api/deals",
        json={"deal_id": "DEAL-ROUTER-007", "company_name": "Router Robotics"},
    ).status_code == 200
    upload = client.post(
        "/api/deals/DEAL-ROUTER-007/documents",
        data={"document_type": "financial_model"},
        files={"file": ("model.pdf", b"router financial model", "application/pdf")},
    )
    assert upload.status_code == 200
    document_id = upload.json()["document"]["document_id"]

    parser_root = tmp_path / "parser-results"
    document_md = parser_root / "router-task-fin" / "document.md"
    document_md.parent.mkdir(parents=True)
    document_md.write_text(
        "<!-- DOC_BLOCK: b000001 page=2 evidence=doc:router-task-fin:p2:b000001 -->\n"
        "Revenue grew with a signed customer pipeline.\n\n"
        "<!-- DOC_BLOCK: b000002 page=3 evidence=doc:router-task-fin:p3:b000002 -->\n"
        "Gross margin is expected to improve after tooling investment.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(deal_documents, "DOCUMENT_PARSER_RESULTS_ROOT", parser_root)
    monkeypatch.setattr(deal_evidence, "DOCUMENT_PARSER_RESULTS_ROOT", parser_root)

    bind = client.post(
        f"/api/deals/DEAL-ROUTER-007/documents/{document_id}/bind-parser-task",
        json={"task_id": "router-task-fin", "artifact_path": "document.md"},
    )
    assert bind.status_code == 200

    built = client.post("/api/deals/DEAL-ROUTER-007/evidence/build")
    assert built.status_code == 200
    payload = built.json()
    assert payload["deal_id"] == "DEAL-ROUTER-007"
    assert payload["quality_report"]["llm_used"] is False
    assert payload["quality_report"]["milvus_written"] is False
    assert payload["counts"]["items"] == 2
    assert payload["items_preview"][0]["source_anchor"]["page"] == 2
    evidence_id = payload["items_preview"][0]["evidence_id"]

    read_back = client.get("/api/deals/DEAL-ROUTER-007/evidence")
    assert read_back.status_code == 200
    assert read_back.json()["evidence_index"]["counts"]["items"] == 2

    filtered = client.get(
        "/api/deals/DEAL-ROUTER-007/evidence",
        params={"q": "gross", "dimension": "finance", "document_id": document_id, "limit": 1},
    )
    assert filtered.status_code == 200
    filtered_payload = filtered.json()
    assert filtered_payload["matched_count"] == 1
    assert len(filtered_payload["items_preview"]) == 1
    assert filtered_payload["counts"]["items"] == 2
    assert filtered_payload["applied_filters"] == {
        "q": "gross",
        "dimension": "finance",
        "document_id": document_id,
        "source_url": "",
        "limit": 1,
    }
    assert "finance" in filtered_payload["available_filters"]["dimensions"]
    assert document_id in filtered_payload["available_filters"]["document_ids"]
    assert filtered_payload["available_filters"]["limits"] == [10, 20, 50, 100, 200]

    empty = client.get("/api/deals/DEAL-ROUTER-007/evidence", params={"q": "not-present"})
    assert empty.status_code == 200
    assert empty.json()["matched_count"] == 0
    assert empty.json()["items_preview"] == []

    quality = client.get("/api/deals/DEAL-ROUTER-007/evidence/quality")
    assert quality.status_code == 200
    assert quality.json()["quality_report"]["counts"]["documents_indexed"] == 1

    dry_run = client.post("/api/deals/DEAL-ROUTER-007/evidence/ingest/dry-run")
    assert dry_run.status_code == 200
    dry_run_payload = dry_run.json()["ingest_dry_run"]
    assert dry_run_payload["postgres_written"] is False
    assert dry_run_payload["milvus_written"] is False
    assert dry_run_payload["counts"]["items_valid"] == 2
    assert dry_run_payload["postgres_rows_preview"][0]["evidence_id"] == evidence_id
    assert dry_run_payload["milvus_chunks_preview"][0]["collection"] == "siq_deal_shared"

    dry_run_alias = client.get("/api/deals/DEAL-ROUTER-007/evidence/ingest-dry-run")
    assert dry_run_alias.status_code == 200
    assert dry_run_alias.json()["ingest_dry_run"]["counts"]["milvus_chunks_planned"] == 2

    receipt_response = client.post(
        "/api/deals/DEAL-ROUTER-007/agents/siq_ic_finance_auditor/startup-retrieval",
        json={"round_name": "R1", "limit": 1},
    )
    assert receipt_response.status_code == 200
    receipt = receipt_response.json()["receipt"]
    assert receipt_response.json()["deal_id"] == "DEAL-ROUTER-007"
    assert receipt_response.json()["agent_id"] == "siq_ic_finance_auditor"
    assert receipt["receipt_id"] == "startup-siq_ic_finance_auditor-R1-001"
    assert receipt["agent_id"] == "siq_ic_finance_auditor"
    assert receipt["shared_hits"] == 2
    assert receipt["private_hits"] == 0
    assert len(receipt["evidence_hits"]) == 1
    assert receipt["evidence_hits"][0]["evidence_id"] == evidence_id
    assert receipt["created_by"] == {"id": 7, "username": "ic-admin"}

    read_receipt = client.get("/api/deals/DEAL-ROUTER-007/agents/ic_finance/startup-retrieval")
    assert read_receipt.status_code == 200
    assert read_receipt.json()["agent_id"] == "siq_ic_finance_auditor"
    assert read_receipt.json()["receipt"]["receipt_id"] == "startup-siq_ic_finance_auditor-R1-001"

    task_payload = client.get("/api/deals/DEAL-ROUTER-007/agents/ic_finance/task-payload")
    assert task_payload.status_code == 200
    task_payload_json = task_payload.json()
    assert task_payload_json["schema_version"] == "siq_ic_agent_task_dry_run_v1"
    assert task_payload_json["agent_id"] == "siq_ic_finance_auditor"
    assert task_payload_json["payload"]["schema_version"] == "siq_ic_agent_task_v1"
    assert task_payload_json["payload"]["output_contract"]["markdown_path"] == "discussion/01_R1_finance_auditor_report.md"
    assert task_payload_json["hermes_called"] is False
    assert task_payload_json["report_written"] is False
    assert "/home/maoyd" not in json.dumps(task_payload_json, ensure_ascii=False)

    task_dry_run = client.post(
        "/api/deals/DEAL-ROUTER-007/agents/siq_ic_finance_auditor/dry-run",
        json={"round_name": "R1"},
    )
    assert task_dry_run.status_code == 200
    assert task_dry_run.json()["payload"]["startup_receipt_id"] == "startup-siq_ic_finance_auditor-R1-001"

    workflow_dry_run = client.post(
        "/api/deals/DEAL-ROUTER-007/workflow/run-r1-agent",
        json={"profile_id": "ic_finance", "round_name": "R1", "dry_run": True},
    )
    assert workflow_dry_run.status_code == 200
    workflow_dry_run_json = workflow_dry_run.json()
    assert workflow_dry_run_json["schema_version"] == "siq_ic_workflow_r1_agent_run_dry_run_v1"
    assert workflow_dry_run_json["workflow_action"] == "run-r1-agent"
    assert workflow_dry_run_json["agent_id"] == "siq_ic_finance_auditor"
    assert workflow_dry_run_json["queued"] is False
    assert workflow_dry_run_json["hermes_called"] is False
    assert workflow_dry_run_json["report_written"] is False
    assert workflow_dry_run_json["workflow_advanced"] is False
    assert workflow_dry_run_json["agent_task"]["schema_version"] == "siq_ic_agent_task_dry_run_v1"

    workflow_run = client.post(
        "/api/deals/DEAL-ROUTER-007/workflow/run-r1-agent",
        json={"profile_id": "ic_finance", "dry_run": False},
    )
    assert workflow_run.status_code == 400

    invalid_task_payload = client.get("/api/deals/DEAL-ROUTER-007/agents/siq_ic_master_coordinator/task-payload")
    assert invalid_task_payload.status_code == 400

    invalid_receipt = client.post(
        "/api/deals/DEAL-ROUTER-007/agents/siq_ic_master_coordinator/startup-retrieval",
        json={"round_name": "R1"},
    )
    assert invalid_receipt.status_code == 400

    item = client.get(f"/api/deals/DEAL-ROUTER-007/evidence/{evidence_id}")
    assert item.status_code == 200
    assert item.json()["evidence"]["document_id"] == document_id
