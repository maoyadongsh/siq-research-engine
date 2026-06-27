from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import SQLModel, Session, create_engine, select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

spec = importlib.util.spec_from_file_location("document_parser_router", BACKEND_ROOT / "routers" / "document_parser.py")
document_parser = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(document_parser)

from services.auth_service import User, UserRole
from services.usage_service import DOCUMENT_PARSE_EVENT, UsageEvent, UserArtifact


class DummyRequest:
    method = "GET"
    query_params = {}
    headers = {}

    async def body(self):
        return b""


def make_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'document-proxy.db'}")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def make_user(session: Session, username: str, role=UserRole.ANALYST) -> User:
    user = User(
        username=username,
        email=f"{username}@example.test",
        full_name=username,
        hashed_password="x",
        role=role,
        is_active=True,
        approval_status="approved",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def test_record_document_artifact_is_idempotent(tmp_path):
    with make_session(tmp_path) as session:
        user = make_user(session, "alice")

        first = document_parser._record_document_artifact(
            session,
            user_id=int(user.id),
            task_id="task-a",
            filename="old.pdf",
            source="document_upload",
        )
        second = document_parser._record_document_artifact(
            session,
            user_id=int(user.id),
            task_id="task-a",
            filename="new.pdf",
            source="document_upload",
        )

        artifacts = session.exec(select(UserArtifact)).all()
        assert len(artifacts) == 1
        assert first.id == second.id
        assert artifacts[0].title == "new.pdf"
        assert artifacts[0].path == "/documents?task=task-a"


def test_non_owner_document_task_access_is_rejected(tmp_path):
    with make_session(tmp_path) as session:
        owner = make_user(session, "owner")
        other = make_user(session, "other")
        document_parser._record_document_artifact(
            session,
            user_id=int(owner.id),
            task_id="task-a",
            filename="owned.pdf",
            source="document_upload",
        )

        assert document_parser._user_has_document_task_access(session, owner, "task-a")
        assert not document_parser._user_has_document_task_access(session, other, "task-a")
        with pytest.raises(HTTPException) as exc:
            document_parser._ensure_document_task_access(session, other, "task-a")
        assert exc.value.status_code == 403


def test_admin_can_access_any_document_task(tmp_path):
    with make_session(tmp_path) as session:
        admin = make_user(session, "admin", role=UserRole.SUPER_ADMIN)
        assert document_parser._user_has_document_task_access(session, admin, "task-a")


def test_create_document_tasks_records_usage_and_artifacts(monkeypatch, tmp_path):
    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None):
            assert url.endswith("/api/tasks")
            return SimpleNamespace(
                status_code=200,
                headers={"content-type": "application/json"},
                json=lambda: {
                    "tasks": [
                        {"task_id": "task-a", "filename": "url-doc.html", "status": "queued"},
                    ]
                },
            )

    class JsonRequest:
        headers = {"content-type": "application/json"}

        async def json(self):
            return {"source_type": "url", "url": "https://example.test/doc.html"}

    monkeypatch.setattr(document_parser.httpx, "AsyncClient", FakeAsyncClient)

    with make_session(tmp_path) as session:
        user = make_user(session, "alice")
        result = asyncio.run(
            document_parser.create_document_tasks(
                request=JsonRequest(),
                files=None,
                current_user=user,
                session=session,
            )
        )

        assert result["tasks"][0]["task_id"] == "task-a"
        usage = session.exec(select(UsageEvent).where(UsageEvent.event_type == DOCUMENT_PARSE_EVENT)).one()
        assert usage.count == 1
        artifact = session.exec(select(UserArtifact).where(UserArtifact.artifact_key == "task-a")).one()
        assert artifact.user_id == user.id
        assert artifact.source == "document_url"


def test_proxy_preserves_upstream_content_type(monkeypatch):
    class QueryParams:
        def multi_items(self):
            return []

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, **kwargs):
            return SimpleNamespace(status_code=200, content=b"PNG", headers={"content-type": "image/png"})

    monkeypatch.setattr(document_parser.httpx, "AsyncClient", FakeAsyncClient)
    request = DummyRequest()
    request.query_params = QueryParams()

    response = asyncio.run(document_parser._proxy_document_parser(request, "/api/figures/task-a/img-1.png"))

    assert response.status_code == 200
    assert response.media_type == "image/png"
    assert response.body == b"PNG"
