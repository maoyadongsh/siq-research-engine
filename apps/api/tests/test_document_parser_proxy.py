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
from services.usage_service import DOCUMENT_PARSE_EVENT, UsageEvent, UserArtifact, current_day_key


class DummyRequest:
    method = "GET"
    query_params = {}
    headers = {}

    async def body(self):
        return b""

    async def json(self):
        return {}


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


def test_import_document_from_mineru_records_usage_and_artifact(monkeypatch, tmp_path):
    seen: dict[str, object] = {}

    class ImportRequest:
        headers = {"content-type": "application/json"}

        async def json(self):
            return {"source_dir": "/home/maoyd/siq-research-engine/data/pdf-parser/results/case-a"}

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None):
            seen["url"] = url
            seen["json"] = json
            return SimpleNamespace(
                status_code=200,
                headers={"content-type": "application/json"},
                json=lambda: {
                    "task": {
                        "task_id": "mineru-task-a",
                        "filename": "result.md",
                        "status": "completed",
                        "parser_provider": "mineru_import",
                    }
                },
            )

    monkeypatch.setattr(document_parser.httpx, "AsyncClient", FakeAsyncClient)

    with make_session(tmp_path) as session:
        user = make_user(session, "alice")
        result = asyncio.run(
            document_parser.import_document_from_mineru(
                request=ImportRequest(),
                current_user=user,
                session=session,
            )
        )

        assert str(seen["url"]).endswith("/api/import/mineru")
        assert seen["json"] == {"source_dir": "/home/maoyd/siq-research-engine/data/pdf-parser/results/case-a"}
        assert result["task"]["task_id"] == "mineru-task-a"
        usage = session.exec(select(UsageEvent).where(UsageEvent.event_type == DOCUMENT_PARSE_EVENT)).one()
        assert usage.count == 1
        assert usage.source == "document_mineru_import"
        artifact = session.exec(select(UserArtifact).where(UserArtifact.artifact_key == "mineru-task-a")).one()
        assert artifact.user_id == user.id
        assert artifact.source == "document_mineru_import"
        assert artifact.path == "/documents?task=mineru-task-a"


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


def test_source_image_proxy_requires_access_and_preserves_payload(monkeypatch, tmp_path):
    seen: dict[str, object] = {}

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
            seen["method"] = method
            seen["url"] = url
            return SimpleNamespace(
                status_code=200,
                content=json.dumps(
                    {
                        "task_id": "task-img",
                        "image_id": "img-000001",
                        "page_number": 2,
                        "bbox": [1, 2, 3, 4],
                        "crop_url": "/api/artifact/task-img/images/crops/img.png",
                    }
                ).encode("utf-8"),
                headers={"content-type": "application/json"},
            )

    monkeypatch.setattr(document_parser.httpx, "AsyncClient", FakeAsyncClient)
    request = DummyRequest()
    request.query_params = QueryParams()

    with make_session(tmp_path) as session:
        user = make_user(session, "alice")
        document_parser._record_document_artifact(
            session,
            user_id=int(user.id),
            task_id="task-img",
            filename="diagram.png",
            source="document_upload",
        )
        response = asyncio.run(
            document_parser.source_image(
                request,
                "task-img",
                "img-000001",
                current_user=user,
                session=session,
            )
        )

    assert seen["method"] == "GET"
    assert str(seen["url"]).endswith("/api/source/task-img/image/img-000001")
    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["page_number"] == 2
    assert payload["bbox"] == [1, 2, 3, 4]


def test_table_relation_review_proxy_posts_json(monkeypatch, tmp_path):
    seen: dict[str, object] = {}

    class QueryParams:
        def multi_items(self):
            return []

    class ReviewRequest(DummyRequest):
        method = "POST"
        query_params = QueryParams()
        headers = {"content-type": "application/json"}

        async def json(self):
            return {"review_status": "accepted", "note": "ok"}

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, **kwargs):
            seen["method"] = method
            seen["url"] = url
            seen["json"] = kwargs.get("json")
            return SimpleNamespace(
                status_code=200,
                content=b'{"success":true}',
                headers={"content-type": "application/json"},
            )

    monkeypatch.setattr(document_parser.httpx, "AsyncClient", FakeAsyncClient)

    with make_session(tmp_path) as session:
        user = make_user(session, "alice")
        document_parser._record_document_artifact(
            session,
            user_id=int(user.id),
            task_id="task-table",
            filename="table.xlsx",
            source="document_upload",
        )
        response = asyncio.run(
            document_parser.review_document_table_relation(
                ReviewRequest(),
                "task-table",
                "rel-001",
                current_user=user,
                session=session,
            )
        )

    assert seen["method"] == "POST"
    assert str(seen["url"]).endswith("/api/table-relations/task-table/rel-001/review")
    assert seen["json"] == {"review_status": "accepted", "note": "ok"}
    assert response.status_code == 200


def test_extraction_templates_proxy(monkeypatch, tmp_path):
    seen: dict[str, object] = {}

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
            seen["method"] = method
            seen["url"] = url
            return SimpleNamespace(
                status_code=200,
                content=b'{"templates":[{"template_id":"contract_terms_v1"}]}',
                headers={"content-type": "application/json"},
            )

    monkeypatch.setattr(document_parser.httpx, "AsyncClient", FakeAsyncClient)
    request = DummyRequest()
    request.query_params = QueryParams()

    with make_session(tmp_path) as session:
        user = make_user(session, "alice")
        response = asyncio.run(document_parser.document_extraction_templates(request, current_user=user))

    assert seen["method"] == "GET"
    assert str(seen["url"]).endswith("/api/extraction/templates")
    assert response.status_code == 200


def test_mineru_candidates_proxy(monkeypatch, tmp_path):
    seen: dict[str, object] = {}

    class QueryParams:
        def multi_items(self):
            return [("limit", "3")]

    class CandidateRequest(DummyRequest):
        query_params = QueryParams()

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, **kwargs):
            seen["method"] = method
            seen["url"] = url
            seen["params"] = kwargs.get("params")
            return SimpleNamespace(
                status_code=200,
                content=b'{"candidates":[]}',
                headers={"content-type": "application/json"},
            )

    monkeypatch.setattr(document_parser.httpx, "AsyncClient", FakeAsyncClient)

    with make_session(tmp_path) as session:
        user = make_user(session, "alice")
        response = asyncio.run(document_parser.list_mineru_import_candidates(CandidateRequest(), current_user=user))

    assert seen["method"] == "GET"
    assert str(seen["url"]).endswith("/api/import/mineru/candidates")
    assert seen["params"] == [("limit", "3")]
    assert response.status_code == 200


def test_extraction_proxy_posts_template_json(monkeypatch, tmp_path):
    seen: dict[str, object] = {}

    class QueryParams:
        def multi_items(self):
            return []

    class ExtractRequest(DummyRequest):
        method = "POST"
        query_params = QueryParams()
        headers = {"content-type": "application/json"}

        async def json(self):
            return {"template_id": "contract_terms_v1", "require_evidence": True}

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, **kwargs):
            seen["method"] = method
            seen["url"] = url
            seen["json"] = kwargs.get("json")
            return SimpleNamespace(
                status_code=200,
                content=b'{"status":"completed","template_id":"contract_terms_v1"}',
                headers={"content-type": "application/json"},
            )

    monkeypatch.setattr(document_parser.httpx, "AsyncClient", FakeAsyncClient)

    with make_session(tmp_path) as session:
        user = make_user(session, "alice")
        document_parser._record_document_artifact(
            session,
            user_id=int(user.id),
            task_id="task-contract",
            filename="contract.md",
            source="document_upload",
        )
        response = asyncio.run(
            document_parser.extract_document_schema(
                ExtractRequest(),
                "task-contract",
                current_user=user,
                session=session,
            )
        )

    assert seen["method"] == "POST"
    assert str(seen["url"]).endswith("/api/extract/task-contract")
    assert seen["json"] == {"template_id": "contract_terms_v1", "require_evidence": True}
    assert response.status_code == 200


def test_batch_download_proxy_filters_to_accessible_tasks(monkeypatch, tmp_path):
    seen: dict[str, object] = {}

    class QueryParams:
        def multi_items(self):
            return []

    class BatchRequest(DummyRequest):
        method = "POST"
        query_params = QueryParams()
        headers = {"content-type": "application/json"}

        async def json(self):
            return {"task_ids": ["owned-task", "other-task", "owned-task"]}

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, **kwargs):
            seen["method"] = method
            seen["url"] = url
            seen["json"] = kwargs.get("json")
            return SimpleNamespace(
                status_code=200,
                content=b"ZIP",
                headers={"content-type": "application/zip"},
            )

    monkeypatch.setattr(document_parser.httpx, "AsyncClient", FakeAsyncClient)

    with make_session(tmp_path) as session:
        user = make_user(session, "alice")
        other = make_user(session, "bob")
        document_parser._record_document_artifact(
            session,
            user_id=int(user.id),
            task_id="owned-task",
            filename="owned.md",
            source="document_upload",
        )
        document_parser._record_document_artifact(
            session,
            user_id=int(other.id),
            task_id="other-task",
            filename="other.md",
            source="document_upload",
        )
        response = asyncio.run(document_parser.download_document_batch(BatchRequest(), current_user=user, session=session))

    assert seen["method"] == "POST"
    assert str(seen["url"]).endswith("/api/download/batch")
    assert seen["json"] == {"task_ids": ["owned-task"]}
    assert response.status_code == 200
    assert response.media_type == "application/zip"


def test_delete_shared_document_task_removes_workspace_link_without_upstream(monkeypatch, tmp_path):
    class QueryParams:
        def multi_items(self):
            return []

    class DeleteRequest(DummyRequest):
        method = "DELETE"
        query_params = QueryParams()

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, **kwargs):
            raise AssertionError("shared document deletion must not call upstream")

    monkeypatch.setattr(document_parser.httpx, "AsyncClient", FakeAsyncClient)

    with make_session(tmp_path) as session:
        user = make_user(session, "alice")
        other = make_user(session, "bob")
        other_id = int(other.id)
        document_parser._record_document_artifact(
            session,
            user_id=int(user.id),
            task_id="shared-task",
            filename="alice.md",
            source="document_upload",
        )
        document_parser._record_document_artifact(
            session,
            user_id=int(other.id),
            task_id="shared-task",
            filename="bob.md",
            source="document_upload",
        )

        result = asyncio.run(
            document_parser.delete_document_task(
                DeleteRequest(),
                "shared-task",
                current_user=user,
                session=session,
            )
        )

        links = session.exec(document_parser._artifact_statement("shared-task")).all()

    assert result == {"success": True, "upstream_deleted": False, "scope": "workspace"}
    assert len(links) == 1
    assert links[0].user_id == other_id


def test_delete_last_document_task_owner_proxies_upstream_delete(monkeypatch, tmp_path):
    seen: dict[str, object] = {}

    class QueryParams:
        def multi_items(self):
            return []

    class DeleteRequest(DummyRequest):
        method = "DELETE"
        query_params = QueryParams()

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, **kwargs):
            seen["method"] = method
            seen["url"] = url
            seen["params"] = kwargs.get("params")
            return SimpleNamespace(
                status_code=200,
                content=b'{"success":true}',
                headers={"content-type": "application/json"},
            )

    monkeypatch.setattr(document_parser.httpx, "AsyncClient", FakeAsyncClient)

    with make_session(tmp_path) as session:
        user = make_user(session, "alice")
        document_parser._record_document_artifact(
            session,
            user_id=int(user.id),
            task_id="last-task",
            filename="owned.md",
            source="document_upload",
        )

        response = asyncio.run(
            document_parser.delete_document_task(
                DeleteRequest(),
                "last-task",
                current_user=user,
                session=session,
            )
        )

        links = session.exec(document_parser._artifact_statement("last-task")).all()

    assert seen["method"] == "DELETE"
    assert str(seen["url"]).endswith("/api/tasks/last-task")
    assert seen["params"] == []
    assert response.status_code == 200
    assert links == []


def test_retry_document_task_proxies_and_records_usage(monkeypatch, tmp_path):
    seen: dict[str, object] = {}

    class QueryParams:
        def multi_items(self):
            return []

    class RetryRequest(DummyRequest):
        method = "POST"
        query_params = QueryParams()

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, **kwargs):
            seen["method"] = method
            seen["url"] = url
            return SimpleNamespace(
                status_code=202,
                content=b'{"task_id":"retry-task","status":"queued"}',
                headers={"content-type": "application/json"},
            )

    monkeypatch.setattr(document_parser.httpx, "AsyncClient", FakeAsyncClient)

    with make_session(tmp_path) as session:
        user = make_user(session, "alice")
        document_parser._record_document_artifact(
            session,
            user_id=int(user.id),
            task_id="retry-task",
            filename="retry.md",
            source="document_upload",
        )

        response = asyncio.run(
            document_parser.retry_document_task(
                RetryRequest(),
                "retry-task",
                current_user=user,
                session=session,
            )
        )

        usage = session.exec(select(UsageEvent).where(UsageEvent.source == "document_retry")).one()

    assert seen["method"] == "POST"
    assert str(seen["url"]).endswith("/api/retry/retry-task")
    assert response.status_code == 202
    assert usage.event_type == DOCUMENT_PARSE_EVENT
    assert usage.count == 1
    assert json.loads(usage.metadata_json or "{}") == {"task_id": "retry-task"}


def test_document_parse_quota_exceeded_returns_429(tmp_path):
    with make_session(tmp_path) as session:
        user = make_user(session, "alice")
        for _ in range(5):
            session.add(
                UsageEvent(
                    user_id=int(user.id),
                    event_type=DOCUMENT_PARSE_EVENT,
                    event_date=current_day_key(),
                    count=1,
                )
            )
        session.commit()

        with pytest.raises(HTTPException) as exc:
            document_parser._enforce_quota_or_429(session, user, increment=1)

    assert exc.value.status_code == 429
    assert exc.value.detail["error"] == "daily_quota_exceeded"
    assert exc.value.detail["type"] == DOCUMENT_PARSE_EVENT
