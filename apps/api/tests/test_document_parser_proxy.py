from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, Session, create_engine, select
from sqlmodel.ext.asyncio.session import AsyncSession

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


async def with_async_session(tmp_path, callback):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'document-proxy-async.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with AsyncSession(engine) as async_session:
            return await callback(async_session)
    finally:
        await engine.dispose()


async def add_document_artifact_async(
    async_session: AsyncSession,
    *,
    user_id: int,
    task_id: str,
    filename: str,
    source: str,
    global_artifact_id: str | None = None,
) -> UserArtifact:
    item = UserArtifact(
        user_id=user_id,
        artifact_type="document_parse",
        artifact_key=task_id,
        title=filename,
        path=f"/documents?task={task_id}",
        source=source,
        global_artifact_id=global_artifact_id or task_id,
    )
    async_session.add(item)
    await async_session.commit()
    await async_session.refresh(item)
    return item


def patch_document_task_list(monkeypatch, tasks):
    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            assert url.endswith("/api/tasks")
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"tasks": tasks},
            )

    monkeypatch.setattr(document_parser.httpx, "AsyncClient", FakeAsyncClient)


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


def test_async_document_task_access_matches_key_and_global_id(tmp_path):
    async def run_case(async_session: AsyncSession):
        user = SimpleNamespace(id=1, role=UserRole.ANALYST)
        other = SimpleNamespace(id=2, role=UserRole.ANALYST)
        admin = SimpleNamespace(id=3, role=UserRole.SUPER_ADMIN)
        await add_document_artifact_async(
            async_session,
            user_id=1,
            task_id="task-key",
            filename="owned-key.pdf",
            source="document_upload",
        )
        async_session.add(
            UserArtifact(
                user_id=1,
                artifact_type="document_parse",
                artifact_key="legacy-key",
                title="owned-global.pdf",
                path="/documents?task=task-global",
                source="document_upload",
                global_artifact_id="task-global",
            )
        )
        await async_session.commit()

        return {
            "key": await document_parser._user_has_document_task_access_async(async_session, user, "task-key"),
            "global": await document_parser._user_has_document_task_access_async(async_session, user, "task-global"),
            "other": await document_parser._user_has_document_task_access_async(async_session, other, "task-key"),
            "admin": await document_parser._user_has_document_task_access_async(async_session, admin, "anything"),
        }

    result = asyncio.run(with_async_session(tmp_path, run_case))

    assert result == {"key": True, "global": True, "other": False, "admin": True}


def test_list_document_tasks_filters_to_current_user_workspace(monkeypatch, tmp_path):
    patch_document_task_list(
        monkeypatch,
        [
            {"task_id": "owned-key", "filename": "owned-key.pdf"},
            {"task_id": "owned-global", "filename": "owned-global.pdf"},
            {"task_id": "other-task", "filename": "other.pdf"},
            {"task_id": "unlinked-task", "filename": "unlinked.pdf"},
        ],
    )

    async def run_case(async_session: AsyncSession):
        user = SimpleNamespace(id=1, role=UserRole.ANALYST)
        other = SimpleNamespace(id=2, role=UserRole.ANALYST)
        await add_document_artifact_async(
            async_session,
            user_id=int(user.id),
            task_id="owned-key",
            filename="owned-key.pdf",
            source="document_upload",
        )
        async_session.add(
            UserArtifact(
                user_id=int(user.id),
                artifact_type="document_parse",
                artifact_key="legacy-key",
                title="owned-global.pdf",
                path="/documents?task=owned-global",
                source="document_upload",
                global_artifact_id="owned-global",
            )
        )
        await add_document_artifact_async(
            async_session,
            user_id=int(other.id),
            task_id="other-task",
            filename="other.pdf",
            source="document_upload",
        )
        await async_session.commit()

        return await document_parser.list_document_tasks(current_user=user, async_session=async_session)

    result = asyncio.run(with_async_session(tmp_path, run_case))
    assert result["scope"] == "workspace"
    assert [item["task_id"] for item in result["tasks"]] == ["owned-key", "owned-global"]


def test_list_document_tasks_admin_defaults_to_system_scope(monkeypatch, tmp_path):
    monkeypatch.delenv("SIQ_DOCUMENT_TASK_LIST_WORKSPACE_ONLY", raising=False)
    patch_document_task_list(
        monkeypatch,
        [
            {"task_id": "system-a", "filename": "system-a.pdf"},
            {"task_id": "system-b", "filename": "system-b.pdf"},
        ],
    )

    async def run_case(async_session: AsyncSession):
        admin = SimpleNamespace(id=1, role=UserRole.ADMIN)
        return await document_parser.list_document_tasks(current_user=admin, async_session=async_session)

    result = asyncio.run(with_async_session(tmp_path, run_case))
    assert result["scope"] == "system"
    assert [item["task_id"] for item in result["tasks"]] == ["system-a", "system-b"]


def test_list_document_tasks_admin_workspace_only_env_filters(monkeypatch, tmp_path):
    monkeypatch.setenv("SIQ_DOCUMENT_TASK_LIST_WORKSPACE_ONLY", "true")
    patch_document_task_list(
        monkeypatch,
        [
            {"task_id": "admin-owned", "filename": "admin-owned.pdf"},
            {"task_id": "other-owned", "filename": "other-owned.pdf"},
            {"task_id": "system-only", "filename": "system-only.pdf"},
        ],
    )

    async def run_case(async_session: AsyncSession):
        admin = SimpleNamespace(id=1, role=UserRole.ADMIN)
        other = SimpleNamespace(id=2, role=UserRole.ANALYST)
        await add_document_artifact_async(
            async_session,
            user_id=int(admin.id),
            task_id="admin-owned",
            filename="admin-owned.pdf",
            source="document_upload",
        )
        await add_document_artifact_async(
            async_session,
            user_id=int(other.id),
            task_id="other-owned",
            filename="other-owned.pdf",
            source="document_upload",
        )

        return await document_parser.list_document_tasks(current_user=admin, async_session=async_session)

    result = asyncio.run(with_async_session(tmp_path, run_case))
    assert result["scope"] == "workspace"
    assert [item["task_id"] for item in result["tasks"]] == ["admin-owned"]


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

    async def run_case(async_session: AsyncSession):
        user = SimpleNamespace(id=1, role=UserRole.ANALYST)
        result = await document_parser.create_document_tasks(
            request=JsonRequest(),
            files=None,
            current_user=user,
            async_session=async_session,
        )
        usage_result = await async_session.exec(select(UsageEvent).where(UsageEvent.event_type == DOCUMENT_PARSE_EVENT))
        artifact_result = await async_session.exec(select(UserArtifact).where(UserArtifact.artifact_key == "task-a"))
        return result, usage_result.one(), artifact_result.one(), int(user.id)

    result, usage, artifact, user_id = asyncio.run(with_async_session(tmp_path, run_case))

    assert result["tasks"][0]["task_id"] == "task-a"
    assert usage.count == 1
    assert artifact.user_id == user_id
    assert artifact.source == "document_url"


def test_create_document_tasks_quota_exceeded_does_not_call_upstream_or_record_side_effects(monkeypatch, tmp_path):
    class JsonRequest:
        headers = {"content-type": "application/json"}

        async def json(self):
            return {"source_type": "url", "url": "https://example.test/doc.html"}

    class ForbiddenAsyncClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("quota failure must not create an upstream client")

    monkeypatch.setattr(document_parser.httpx, "AsyncClient", ForbiddenAsyncClient)

    async def run_case(async_session: AsyncSession):
        user = SimpleNamespace(id=1, role=UserRole.ANALYST)
        async_session.add(
            UsageEvent(
                user_id=int(user.id),
                event_type=DOCUMENT_PARSE_EVENT,
                event_date=current_day_key(),
                count=5,
            )
        )
        await async_session.commit()

        with pytest.raises(HTTPException) as exc:
            await document_parser.create_document_tasks(
                request=JsonRequest(),
                files=None,
                current_user=user,
                async_session=async_session,
            )
        usage_result = await async_session.exec(select(UsageEvent).where(UsageEvent.user_id == int(user.id)))
        artifact_result = await async_session.exec(select(UserArtifact).where(UserArtifact.user_id == int(user.id)))
        return exc.value, usage_result.all(), artifact_result.all()

    exc, usage_events, artifacts = asyncio.run(with_async_session(tmp_path, run_case))

    assert exc.status_code == 429
    assert exc.detail["error"] == "daily_quota_exceeded"
    assert sum(item.count for item in usage_events) == 5
    assert artifacts == []


def test_create_document_tasks_multipart_quota_precheck_runs_before_file_read(monkeypatch, tmp_path):
    class MultipartRequest:
        headers = {"content-type": "multipart/form-data; boundary=test"}

    class UnreadableUpload:
        filename = "blocked.pdf"
        content_type = "application/pdf"

        async def read(self):
            raise AssertionError("quota failure must happen before reading uploaded files")

    class ForbiddenAsyncClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("quota failure must not create an upstream client")

    monkeypatch.setattr(document_parser.httpx, "AsyncClient", ForbiddenAsyncClient)

    async def run_case(async_session: AsyncSession):
        user = SimpleNamespace(id=1, role=UserRole.ANALYST)
        async_session.add(
            UsageEvent(
                user_id=int(user.id),
                event_type=DOCUMENT_PARSE_EVENT,
                event_date=current_day_key(),
                count=4,
            )
        )
        await async_session.commit()

        with pytest.raises(HTTPException) as exc:
            await document_parser.create_document_tasks(
                request=MultipartRequest(),
                files=[UnreadableUpload(), UnreadableUpload()],
                current_user=user,
                async_session=async_session,
            )
        usage_result = await async_session.exec(select(UsageEvent).where(UsageEvent.user_id == int(user.id)))
        artifact_result = await async_session.exec(select(UserArtifact).where(UserArtifact.user_id == int(user.id)))
        return exc.value, usage_result.all(), artifact_result.all()

    exc, usage_events, artifacts = asyncio.run(with_async_session(tmp_path, run_case))

    assert exc.status_code == 429
    assert exc.detail["error"] == "daily_quota_exceeded"
    assert sum(item.count for item in usage_events) == 4
    assert artifacts == []


def test_create_document_tasks_upstream_error_does_not_record_usage_or_artifact(monkeypatch, tmp_path):
    class JsonRequest:
        headers = {"content-type": "application/json"}

        async def json(self):
            return {"source_type": "url", "url": "https://example.test/doc.html"}

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
                status_code=500,
                headers={"content-type": "application/json"},
                json=lambda: {"detail": "upstream failed"},
            )

    monkeypatch.setattr(document_parser.httpx, "AsyncClient", FakeAsyncClient)

    async def run_case(async_session: AsyncSession):
        user = SimpleNamespace(id=1, role=UserRole.ANALYST)
        response = await document_parser.create_document_tasks(
            request=JsonRequest(),
            files=None,
            current_user=user,
            async_session=async_session,
        )
        usage_result = await async_session.exec(select(UsageEvent).where(UsageEvent.user_id == int(user.id)))
        artifact_result = await async_session.exec(select(UserArtifact).where(UserArtifact.user_id == int(user.id)))
        return response, usage_result.all(), artifact_result.all()

    response, usage_events, artifacts = asyncio.run(with_async_session(tmp_path, run_case))

    assert response.status_code == 500
    assert usage_events == []
    assert artifacts == []


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

    async def run_case(async_session: AsyncSession):
        user = SimpleNamespace(id=1, role=UserRole.ANALYST)
        result = await document_parser.import_document_from_mineru(
            request=ImportRequest(),
            current_user=user,
            async_session=async_session,
        )
        usage_result = await async_session.exec(select(UsageEvent).where(UsageEvent.event_type == DOCUMENT_PARSE_EVENT))
        artifact_result = await async_session.exec(select(UserArtifact).where(UserArtifact.artifact_key == "mineru-task-a"))
        return result, usage_result.one(), artifact_result.one(), int(user.id)

    result, usage, artifact, user_id = asyncio.run(with_async_session(tmp_path, run_case))

    assert str(seen["url"]).endswith("/api/import/mineru")
    assert seen["json"] == {"source_dir": "/home/maoyd/siq-research-engine/data/pdf-parser/results/case-a"}
    assert result["task"]["task_id"] == "mineru-task-a"
    assert usage.count == 1
    assert usage.source == "document_mineru_import"
    assert artifact.user_id == user_id
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

    async def run_case(async_session: AsyncSession):
        user = SimpleNamespace(id=1, role=UserRole.ANALYST)
        await add_document_artifact_async(
            async_session,
            user_id=int(user.id),
            task_id="task-img",
            filename="diagram.png",
            source="document_upload",
        )
        return await document_parser.source_image(
            request,
            "task-img",
            "img-000001",
            current_user=user,
            async_session=async_session,
        )

    response = asyncio.run(with_async_session(tmp_path, run_case))
    assert seen["method"] == "GET"
    assert str(seen["url"]).endswith("/api/source/task-img/image/img-000001")
    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["page_number"] == 2
    assert payload["bbox"] == [1, 2, 3, 4]


@pytest.mark.parametrize(
    ("route_name", "extra_args", "expected_suffix"),
    [
        ("get_document_task", (), "/api/tasks/task-leaf"),
        ("get_document_status", (), "/api/status/task-leaf"),
        ("get_document_result", (), "/api/result/task-leaf"),
        ("get_document_artifact", ("tables/result.json",), "/api/artifact/task-leaf/tables/result.json"),
        ("download_document_package", (), "/api/download/task-leaf"),
        ("source_page", (7,), "/api/source/task-leaf/page/7"),
        ("source_page_image", (7,), "/api/source/task-leaf/page-image/7"),
        ("source_block", ("block-1",), "/api/source/task-leaf/block/block-1"),
        ("source_table", ("table-1",), "/api/source/task-leaf/table/table-1"),
        ("document_figures", (), "/api/figures/task-leaf"),
        ("document_figure", ("img-1",), "/api/figures/task-leaf/img-1"),
        ("document_table_relations", (), "/api/table-relations/task-leaf"),
        ("get_document_extraction", ("extract-1",), "/api/extract/task-leaf/extract-1"),
    ],
)
def test_core_document_leaf_get_routes_use_async_access_and_proxy_path(
    monkeypatch,
    tmp_path,
    route_name,
    extra_args,
    expected_suffix,
):
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
            seen["params"] = kwargs.get("params")
            return SimpleNamespace(
                status_code=200,
                content=b'{"ok":true}',
                headers={"content-type": "application/json"},
            )

    monkeypatch.setattr(document_parser.httpx, "AsyncClient", FakeAsyncClient)
    request = DummyRequest()
    request.query_params = QueryParams()

    async def run_case(async_session: AsyncSession):
        user = SimpleNamespace(id=1, role=UserRole.ANALYST)
        await add_document_artifact_async(
            async_session,
            user_id=1,
            task_id="task-leaf",
            filename="leaf.pdf",
            source="document_upload",
        )
        route = getattr(document_parser, route_name)
        return await route(
            request,
            "task-leaf",
            *extra_args,
            current_user=user,
            async_session=async_session,
        )

    response = asyncio.run(with_async_session(tmp_path, run_case))

    assert seen["method"] == "GET"
    assert str(seen["url"]).endswith(expected_suffix)
    assert seen["params"] == []
    assert response.status_code == 200


def test_cancel_document_task_uses_async_access_and_posts_upstream(monkeypatch, tmp_path):
    seen: dict[str, object] = {}

    class QueryParams:
        def multi_items(self):
            return []

    class CancelRequest(DummyRequest):
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
            seen["params"] = kwargs.get("params")
            return SimpleNamespace(
                status_code=200,
                content=b'{"cancelled":true}',
                headers={"content-type": "application/json"},
            )

    monkeypatch.setattr(document_parser.httpx, "AsyncClient", FakeAsyncClient)

    async def run_case(async_session: AsyncSession):
        user = SimpleNamespace(id=1, role=UserRole.ANALYST)
        await add_document_artifact_async(
            async_session,
            user_id=1,
            task_id="task-cancel",
            filename="cancel.md",
            source="document_upload",
        )
        return await document_parser.cancel_document_task(
            CancelRequest(),
            "task-cancel",
            current_user=user,
            async_session=async_session,
        )

    response = asyncio.run(with_async_session(tmp_path, run_case))

    assert seen["method"] == "POST"
    assert str(seen["url"]).endswith("/api/cancel/task-cancel")
    assert seen["params"] == []
    assert response.status_code == 200


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

    async def run_case(async_session: AsyncSession):
        user = SimpleNamespace(id=1, role=UserRole.ANALYST)
        await add_document_artifact_async(
            async_session,
            user_id=int(user.id),
            task_id="task-table",
            filename="table.xlsx",
            source="document_upload",
        )
        return await document_parser.review_document_table_relation(
            ReviewRequest(),
            "task-table",
            "rel-001",
            current_user=user,
            async_session=async_session,
        )

    response = asyncio.run(with_async_session(tmp_path, run_case))

    assert seen["method"] == "POST"
    assert str(seen["url"]).endswith("/api/table-relations/task-table/rel-001/review")
    assert seen["json"] == {"review_status": "accepted", "note": "ok"}
    assert response.status_code == 200


@pytest.mark.parametrize(
    ("route_name", "extra_args", "body", "expected_suffix"),
    [
        (
            "split_document_logical_table",
            ("logical-1",),
            {"split_after_row": 4},
            "/api/logical-tables/task-body/logical-1/split",
        ),
        (
            "merge_document_logical_tables",
            (),
            {"logical_table_ids": ["logical-1", "logical-2"]},
            "/api/logical-tables/task-body/merge",
        ),
    ],
)
def test_logical_table_body_proxy_routes_use_async_access_and_forward_json(
    monkeypatch,
    tmp_path,
    route_name,
    extra_args,
    body,
    expected_suffix,
):
    seen: dict[str, object] = {}

    class QueryParams:
        def multi_items(self):
            return []

    class BodyRequest(DummyRequest):
        method = "POST"
        query_params = QueryParams()
        headers = {"content-type": "application/json"}

        async def json(self):
            return body

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

    async def run_case(async_session: AsyncSession):
        user = SimpleNamespace(id=1, role=UserRole.ANALYST)
        await add_document_artifact_async(
            async_session,
            user_id=1,
            task_id="task-body",
            filename="body.md",
            source="document_upload",
        )
        route = getattr(document_parser, route_name)
        return await route(
            BodyRequest(),
            "task-body",
            *extra_args,
            current_user=user,
            async_session=async_session,
        )

    response = asyncio.run(with_async_session(tmp_path, run_case))

    assert seen["method"] == "POST"
    assert str(seen["url"]).endswith(expected_suffix)
    assert seen["json"] == body
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

    async def run_case(async_session: AsyncSession):
        user = SimpleNamespace(id=1, role=UserRole.ANALYST)
        await add_document_artifact_async(
            async_session,
            user_id=int(user.id),
            task_id="task-contract",
            filename="contract.md",
            source="document_upload",
        )
        return await document_parser.extract_document_schema(
            ExtractRequest(),
            "task-contract",
            current_user=user,
            async_session=async_session,
        )

    response = asyncio.run(with_async_session(tmp_path, run_case))
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

    async def run_case(async_session: AsyncSession):
        user = SimpleNamespace(id=1, role=UserRole.ANALYST)
        other = SimpleNamespace(id=2, role=UserRole.ANALYST)
        await add_document_artifact_async(
            async_session,
            user_id=int(user.id),
            task_id="owned-task",
            filename="owned.md",
            source="document_upload",
        )
        await add_document_artifact_async(
            async_session,
            user_id=int(other.id),
            task_id="other-task",
            filename="other.md",
            source="document_upload",
        )
        return await document_parser.download_document_batch(BatchRequest(), current_user=user, async_session=async_session)

    response = asyncio.run(with_async_session(tmp_path, run_case))
    assert seen["method"] == "POST"
    assert str(seen["url"]).endswith("/api/download/batch")
    assert seen["json"] == {"task_ids": ["owned-task"]}
    assert response.status_code == 200
    assert response.media_type == "application/zip"


def test_batch_download_rejects_when_no_accessible_tasks_without_upstream(monkeypatch, tmp_path):
    class QueryParams:
        def multi_items(self):
            return []

    class BatchRequest(DummyRequest):
        method = "POST"
        query_params = QueryParams()
        headers = {"content-type": "application/json"}

        async def json(self):
            return {"task_ids": ["other-task", "missing-task"]}

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            raise AssertionError("batch download must not call upstream when no tasks are accessible")

    monkeypatch.setattr(document_parser.httpx, "AsyncClient", FakeAsyncClient)

    async def run_case(async_session: AsyncSession):
        user = SimpleNamespace(id=1, role=UserRole.ANALYST)
        other = SimpleNamespace(id=2, role=UserRole.ANALYST)
        await add_document_artifact_async(
            async_session,
            user_id=int(other.id),
            task_id="other-task",
            filename="other.md",
            source="document_upload",
        )

        with pytest.raises(HTTPException) as exc:
            await document_parser.download_document_batch(BatchRequest(), current_user=user, async_session=async_session)
        return exc.value

    exc = asyncio.run(with_async_session(tmp_path, run_case))

    assert exc.status_code == 403
    assert exc.detail == "No selected document tasks are accessible"


def test_batch_download_admin_accepts_task_ids_alias_trim_and_dedupe(monkeypatch, tmp_path):
    seen: dict[str, object] = {}

    class QueryParams:
        def multi_items(self):
            return []

    class BatchRequest(DummyRequest):
        method = "POST"
        query_params = QueryParams()
        headers = {"content-type": "application/json"}

        async def json(self):
            return {"taskIds": [" task-a ", "task-a", "", None, " task-b "]}

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

    async def run_case(async_session: AsyncSession):
        admin = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN)
        return await document_parser.download_document_batch(BatchRequest(), current_user=admin, async_session=async_session)

    response = asyncio.run(with_async_session(tmp_path, run_case))
    assert seen["method"] == "POST"
    assert str(seen["url"]).endswith("/api/download/batch")
    assert seen["json"] == {"task_ids": ["task-a", "task-b"]}
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

    async def run_case(async_session: AsyncSession):
        user = SimpleNamespace(id=1, role=UserRole.ANALYST)
        other = SimpleNamespace(id=2, role=UserRole.ANALYST)
        await add_document_artifact_async(
            async_session,
            user_id=int(user.id),
            task_id="shared-task",
            filename="alice.md",
            source="document_upload",
        )
        await add_document_artifact_async(
            async_session,
            user_id=int(other.id),
            task_id="shared-task",
            filename="bob.md",
            source="document_upload",
        )

        result = await document_parser.delete_document_task(
            DeleteRequest(),
            "shared-task",
            current_user=user,
            async_session=async_session,
        )
        links_result = await async_session.exec(document_parser._artifact_statement("shared-task"))
        return result, links_result.all(), int(other.id)

    result, links, other_id = asyncio.run(with_async_session(tmp_path, run_case))

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

    async def run_case(async_session: AsyncSession):
        user = SimpleNamespace(id=1, role=UserRole.ANALYST)
        await add_document_artifact_async(
            async_session,
            user_id=int(user.id),
            task_id="last-task",
            filename="owned.md",
            source="document_upload",
        )

        response = await document_parser.delete_document_task(
            DeleteRequest(),
            "last-task",
            current_user=user,
            async_session=async_session,
        )
        links_result = await async_session.exec(document_parser._artifact_statement("last-task"))
        return response, links_result.all()

    response, links = asyncio.run(with_async_session(tmp_path, run_case))

    assert seen["method"] == "DELETE"
    assert str(seen["url"]).endswith("/api/tasks/last-task")
    assert seen["params"] == []
    assert response.status_code == 200
    assert json.loads(response.body) == {"success": True}
    assert links == []


def test_delete_last_document_task_owner_deletes_workspace_link_before_upstream_500(monkeypatch, tmp_path):
    seen: dict[str, object] = {}
    session_ref: dict[str, AsyncSession] = {}

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
            result = await session_ref["session"].exec(document_parser._artifact_statement("failing-task"))
            seen["links_during_upstream"] = result.all()
            return SimpleNamespace(
                status_code=500,
                content=b'{"error":"upstream failed"}',
                headers={"content-type": "application/json"},
            )

    monkeypatch.setattr(document_parser.httpx, "AsyncClient", FakeAsyncClient)

    async def run_case(async_session: AsyncSession):
        session_ref["session"] = async_session
        user = SimpleNamespace(id=1, role=UserRole.ANALYST)
        await add_document_artifact_async(
            async_session,
            user_id=int(user.id),
            task_id="failing-task",
            filename="owned.md",
            source="document_upload",
        )

        response = await document_parser.delete_document_task(
            DeleteRequest(),
            "failing-task",
            current_user=user,
            async_session=async_session,
        )
        links_result = await async_session.exec(document_parser._artifact_statement("failing-task"))
        return response, links_result.all()

    response, links_after_response = asyncio.run(with_async_session(tmp_path, run_case))

    assert seen["method"] == "DELETE"
    assert str(seen["url"]).endswith("/api/tasks/failing-task")
    assert seen["links_during_upstream"] == []
    assert response.status_code == 500
    assert json.loads(response.body) == {"error": "upstream failed"}
    assert links_after_response == []


def test_delete_last_document_task_owner_request_error_keeps_workspace_link_deleted(monkeypatch, tmp_path):
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
            raise document_parser.httpx.RequestError("parser offline")

    monkeypatch.setattr(document_parser.httpx, "AsyncClient", FakeAsyncClient)

    async def run_case(async_session: AsyncSession):
        user = SimpleNamespace(id=1, role=UserRole.ANALYST)
        await add_document_artifact_async(
            async_session,
            user_id=int(user.id),
            task_id="offline-task",
            filename="owned.md",
            source="document_upload",
        )

        with pytest.raises(HTTPException) as exc_info:
            await document_parser.delete_document_task(
                DeleteRequest(),
                "offline-task",
                current_user=user,
                async_session=async_session,
            )
        links_result = await async_session.exec(document_parser._artifact_statement("offline-task"))
        return exc_info, links_result.all()

    exc_info, links_after_error = asyncio.run(with_async_session(tmp_path, run_case))

    assert seen["method"] == "DELETE"
    assert str(seen["url"]).endswith("/api/tasks/offline-task")
    assert exc_info.value.status_code == 502
    assert links_after_error == []


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
            seen["params"] = kwargs.get("params")
            return SimpleNamespace(
                status_code=202,
                content=b'{"task_id":"retry-task","status":"queued"}',
                headers={"content-type": "application/json"},
            )

    monkeypatch.setattr(document_parser.httpx, "AsyncClient", FakeAsyncClient)

    async def run_case(async_session: AsyncSession):
        user = SimpleNamespace(id=1, role=UserRole.ANALYST)
        user_id = int(user.id)
        await add_document_artifact_async(
            async_session,
            user_id=user_id,
            task_id="retry-task",
            filename="retry.md",
            source="document_upload",
        )

        response = await document_parser.retry_document_task(
            RetryRequest(),
            "retry-task",
            current_user=user,
            async_session=async_session,
        )
        result = await async_session.exec(select(UsageEvent).where(UsageEvent.source == "document_retry"))
        usage = result.one()
        return response, usage, user_id

    response, usage, user_id = asyncio.run(with_async_session(tmp_path, run_case))
    assert seen["method"] == "POST"
    assert str(seen["url"]).endswith("/api/retry/retry-task")
    assert seen["params"] == []
    assert response.status_code == 202
    assert usage.user_id == user_id
    assert usage.event_type == DOCUMENT_PARSE_EVENT
    assert usage.count == 1
    assert json.loads(usage.metadata_json or "{}") == {"task_id": "retry-task"}


def test_retry_document_task_upstream_error_does_not_record_usage(monkeypatch, tmp_path):
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
            assert method == "POST"
            assert str(url).endswith("/api/retry/retry-task")
            return SimpleNamespace(
                status_code=500,
                content=b'{"detail":"retry failed"}',
                headers={"content-type": "application/json"},
            )

    monkeypatch.setattr(document_parser.httpx, "AsyncClient", FakeAsyncClient)

    async def run_case(async_session: AsyncSession):
        user = SimpleNamespace(id=1, role=UserRole.ANALYST)
        await add_document_artifact_async(
            async_session,
            user_id=int(user.id),
            task_id="retry-task",
            filename="retry.md",
            source="document_upload",
        )

        response = await document_parser.retry_document_task(
            RetryRequest(),
            "retry-task",
            current_user=user,
            async_session=async_session,
        )
        result = await async_session.exec(select(UsageEvent).where(UsageEvent.source == "document_retry"))
        return response, result.all()

    response, retry_usage = asyncio.run(with_async_session(tmp_path, run_case))
    assert response.status_code == 500
    assert retry_usage == []


def test_retry_document_task_checks_owner_before_quota_or_upstream(monkeypatch, tmp_path):
    class QueryParams:
        def multi_items(self):
            return []

    class RetryRequest(DummyRequest):
        method = "POST"
        query_params = QueryParams()

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            raise AssertionError("retry without owner access must not create an upstream client")

    monkeypatch.setattr(document_parser.httpx, "AsyncClient", FakeAsyncClient)

    async def run_case(async_session: AsyncSession):
        user = SimpleNamespace(id=1, role=UserRole.ANALYST)
        for _ in range(5):
            async_session.add(
                UsageEvent(
                    user_id=int(user.id),
                    event_type=DOCUMENT_PARSE_EVENT,
                    event_date=current_day_key(),
                    count=1,
                )
            )
        await async_session.commit()

        with pytest.raises(HTTPException) as exc:
            await document_parser.retry_document_task(
                RetryRequest(),
                "missing-task",
                current_user=user,
                async_session=async_session,
            )
        result = await async_session.exec(select(UsageEvent).where(UsageEvent.source == "document_retry"))
        return exc.value, result.all()

    exc, retry_usage = asyncio.run(with_async_session(tmp_path, run_case))

    assert exc.status_code == 403
    assert retry_usage == []


def test_retry_document_task_quota_exceeded_does_not_call_upstream_or_record_usage(monkeypatch, tmp_path):
    class QueryParams:
        def multi_items(self):
            return []

    class RetryRequest(DummyRequest):
        method = "POST"
        query_params = QueryParams()

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            raise AssertionError("retry quota failure must not create an upstream client")

    monkeypatch.setattr(document_parser.httpx, "AsyncClient", FakeAsyncClient)

    async def run_case(async_session: AsyncSession):
        user = SimpleNamespace(id=1, role=UserRole.ANALYST)
        await add_document_artifact_async(
            async_session,
            user_id=int(user.id),
            task_id="retry-task",
            filename="retry.md",
            source="document_upload",
        )
        for _ in range(5):
            async_session.add(
                UsageEvent(
                    user_id=int(user.id),
                    event_type=DOCUMENT_PARSE_EVENT,
                    event_date=current_day_key(),
                    count=1,
                )
            )
        await async_session.commit()

        with pytest.raises(HTTPException) as exc:
            await document_parser.retry_document_task(
                RetryRequest(),
                "retry-task",
                current_user=user,
                async_session=async_session,
            )
        result = await async_session.exec(select(UsageEvent).where(UsageEvent.source == "document_retry"))
        return exc.value, result.all()

    exc, retry_usage = asyncio.run(with_async_session(tmp_path, run_case))

    assert exc.status_code == 429
    assert exc.detail["error"] == "daily_quota_exceeded"
    assert retry_usage == []


def test_document_parse_quota_uses_async_session_payload(tmp_path):
    async def run_case():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'document-proxy-async.db'}")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(SQLModel.metadata.create_all)
            async with AsyncSession(engine) as async_session:
                async_session.add(
                    UsageEvent(
                        user_id=31,
                        event_type=DOCUMENT_PARSE_EVENT,
                        event_date=current_day_key(),
                        count=2,
                        source="document_quota_test",
                    )
                )
                await async_session.commit()

                payload = await document_parser.document_parse_quota(
                    current_user=SimpleNamespace(id=31, role=UserRole.ANALYST),
                    async_session=async_session,
                )
        finally:
            await engine.dispose()
        return payload

    payload = asyncio.run(run_case())

    assert payload["eventType"] == DOCUMENT_PARSE_EVENT
    assert payload["used"] == 2
    assert payload["limit"] == 5
    assert payload["remaining"] == 3
    assert payload["resetAt"]


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
