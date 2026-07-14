import io
from pathlib import Path
from types import SimpleNamespace

import anyio
from database import get_async_session
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from routers import primary_market_materials as router_module
from services.auth_dependencies import get_current_user
from services.auth_service import User, UserRole

from services import deal_store, primary_market_materials

DEAL_ID = "DEAL-PMM-ROUTES-001"


def _user(user_id: int, username: str) -> User:
    return User(
        id=user_id,
        username=username,
        email=f"{username}@example.test",
        hashed_password="x",
        full_name=username,
        role=UserRole.ANALYST,
    )


def _app(user: User) -> FastAPI:
    app = FastAPI()
    app.include_router(router_module.router, prefix="/api")

    async def current_user():
        return user

    async def fake_session():
        yield SimpleNamespace()

    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_async_session] = fake_session
    return app


def test_owner_upload_list_original_and_private_deal_bola(monkeypatch, tmp_path: Path):
    wiki_root = tmp_path / "wiki"
    monkeypatch.setattr(deal_store, "WIKI_ROOT", wiki_root)
    owner = _user(7, "owner")
    other = _user(8, "other")
    deal_store.create_deal_package(
        deal_id=DEAL_ID,
        company_name="Route Issuer",
        created_by={"id": 7, "username": "owner"},
        wiki_root=wiki_root,
    )

    async def no_material_artifact(*args, **kwargs):
        return None

    async def fake_submit(*, deal_id, document, parse_run, **kwargs):
        updated = primary_market_materials.update_parse_run_submission(
            deal_id,
            document["document_id"],
            parse_run["parse_run_id"],
            parser_task_id="route-parser-task-1",
            status="queued",
        )
        return updated, False

    monkeypatch.setattr(router_module, "_record_material_artifact", no_material_artifact)
    monkeypatch.setattr(router_module, "_submit_document_parse", fake_submit)
    owner_client = TestClient(_app(owner))

    uploaded = owner_client.post(
        f"/api/primary-market/projects/{DEAL_ID}/materials/prospectuses",
        data={"exchange": "SSE", "board": "star", "filing_stage": "registration_draft"},
        files={"file": ("issuer.pdf", b"%PDF-1.7\nroute prospectus", "application/pdf")},
    )

    assert uploaded.status_code == 202
    payload = uploaded.json()
    assert payload["document"]["parse_status"] == "queued"
    document_id = payload["document"]["document_id"]
    assert payload["document"]["original_url"].endswith(f"/{document_id}/original")
    listed = owner_client.get(f"/api/primary-market/projects/{DEAL_ID}/materials")
    assert listed.status_code == 200
    assert listed.json()["materials"][0]["document_id"] == document_id
    original = owner_client.get(
        f"/api/primary-market/projects/{DEAL_ID}/materials/{document_id}/original"
    )
    assert original.status_code == 200
    assert original.content.startswith(b"%PDF-")

    other_client = TestClient(_app(other))
    assert other_client.get(f"/api/primary-market/projects/{DEAL_ID}/materials").status_code == 404
    assert other_client.get(
        f"/api/primary-market/projects/{DEAL_ID}/materials/{document_id}/original"
    ).status_code == 404


def test_invalid_pdf_fails_before_submission(monkeypatch, tmp_path: Path):
    wiki_root = tmp_path / "wiki"
    monkeypatch.setattr(deal_store, "WIKI_ROOT", wiki_root)
    owner = _user(7, "owner")
    deal_store.create_deal_package(
        deal_id=DEAL_ID,
        company_name="Route Issuer",
        created_by={"id": 7},
        wiki_root=wiki_root,
    )

    async def no_material_artifact(*args, **kwargs):
        return None

    async def forbidden_submit(**kwargs):
        raise AssertionError("parser submission must not run")

    monkeypatch.setattr(router_module, "_record_material_artifact", no_material_artifact)
    monkeypatch.setattr(router_module, "_submit_document_parse", forbidden_submit)
    client = TestClient(_app(owner))

    response = client.post(
        f"/api/primary-market/projects/{DEAL_ID}/materials/prospectuses",
        files={"file": ("fake.pdf", b"not-a-pdf", "application/pdf")},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_pdf"
    assert primary_market_materials.list_primary_market_materials(DEAL_ID) == []


def test_parser_exception_marks_run_failed_but_keeps_raw(monkeypatch, tmp_path: Path):
    wiki_root = tmp_path / "wiki"
    monkeypatch.setattr(deal_store, "WIKI_ROOT", wiki_root)
    deal_store.create_deal_package(
        deal_id=DEAL_ID,
        company_name="Route Issuer",
        created_by={"id": 7},
        wiki_root=wiki_root,
    )
    created = primary_market_materials.create_prospectus_document(
        deal_id=DEAL_ID,
        filename="issuer.pdf",
        content_type="application/pdf",
        stream=io.BytesIO(b"%PDF-1.7\nparser unavailable"),
        created_by={"id": 7},
    )
    document = created["document"]
    run = primary_market_materials.create_parse_run(
        DEAL_ID, document["document_id"], submitted_by={"id": 7}
    )

    async def unavailable(**kwargs):
        raise HTTPException(502, detail="down")

    monkeypatch.setattr(router_module, "submit_pdf_parse", unavailable)

    async def run_case():
        try:
            await router_module._submit_document_parse(
                deal_id=DEAL_ID,
                document=document,
                parse_run=run,
                user=_user(7, "owner"),
                session=SimpleNamespace(),
            )
        except HTTPException as exc:
            assert exc.status_code == 502
        else:
            raise AssertionError("submission should fail")

    anyio.run(run_case)
    status = primary_market_materials.read_material_parse_status(
        DEAL_ID, document["document_id"]
    )
    assert status["parse_run"]["status"] == "failed"
    assert status["parse_run"]["failure_code"] == "pdf_parser_submit_failed"
    assert primary_market_materials.material_original_path(DEAL_ID, document["document_id"]).is_file()
