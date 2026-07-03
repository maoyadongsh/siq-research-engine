import json
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from routers import deals
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
