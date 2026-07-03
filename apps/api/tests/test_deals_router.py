import json
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from routers import deals
from services import deal_documents
from services import deal_evidence
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
                "score": 82,
                "recommendation": "SUPPORT",
                "source_root": "/tmp/hidden",
                "created_by": {"id": 7, "username": "ic-admin", "email": "hide@example.test"},
            }
        },
    )
    (package_dir / "discussion" / "01_R1_strategist_report.md").write_text("# R1\n\n战略窗口明确。", encoding="utf-8")

    index = client.get("/api/deals/DEAL-ROUTER-REPORTS/reports")
    assert index.status_code == 200
    index_payload = index.json()
    assert index_payload["schema_version"] == "siq_deal_reports_index_v1"
    paths = {item["path"] for item in index_payload["reports"]}
    assert "phases/r1_reports.json" in paths
    assert "discussion/01_R1_strategist_report.md" in paths
    assert index_payload["counts"]["reports"] >= 4
    assert any(item["path"] == "decision/IC_DECISION_REPORT.md" for item in index_payload["missing_expected"])

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

    item = client.get(f"/api/deals/DEAL-ROUTER-007/evidence/{evidence_id}")
    assert item.status_code == 200
    assert item.json()["evidence"]["document_id"] == document_id
