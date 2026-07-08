import hashlib
import json
import json as json_module
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest
from fastapi import HTTPException
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.ext.asyncio.session import AsyncSession

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routers import workspace
from services.usage_service import PARSE_EVENT, UsageEvent, UserArtifact, WorkspaceProject, current_day_key


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'workspace.db'}")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


async def _with_async_session(tmp_path, db_name, callback):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / db_name}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        async with AsyncSession(engine) as async_session:
            return await callback(async_session)
    finally:
        await engine.dispose()


def _pdf_config_hash(market: str = "CN") -> str:
    return workspace._pdf_parse_config_hash(
        {
            "backend": "hybrid-http-client",
            "parse_method": "auto",
            "market": market,
            "start_page_id": "",
            "end_page_id": "",
            "formula_enable": "true",
            "table_enable": "true",
        }
    )


def test_pdf2md_headers_include_internal_parser_token(monkeypatch):
    monkeypatch.setattr(workspace, "PDF2MD_ACCESS_TOKEN", "internal-pdf-token")

    headers = workspace._pdf2md_headers(current_user=SimpleNamespace(id=7, role="analyst"))

    assert headers["X-PDF2MD-Token"] == "internal-pdf-token"
    assert headers["X-SIQ-User-Id"] == "7"
    assert headers["X-SIQ-User-Role"] == "analyst"


def test_extract_report_artifact_from_text_prefers_final_wiki_path(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki"
    company_dir = wiki_root / "companies" / "300017-网宿科技"
    company_dir.mkdir(parents=True)
    (company_dir / "company.json").write_text(
        '{"stock_code":"300017","company_short_name":"网宿科技"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(workspace, "WIKI_ROOT", wiki_root.resolve())

    payload = workspace.extract_report_artifact_from_text(
        "HTML：`/home/maoyd/wiki/companies/300017-网宿科技/analysis/300017-网宿科技-2025-analysis.html`"
    )

    assert payload
    assert payload["company_dir"] == "300017-网宿科技"
    assert payload["company_code"] == "300017"
    assert payload["company_name"] == "网宿科技"
    assert payload["artifact_key"] == "wiki:analysis:300017-网宿科技:300017-网宿科技-2025-analysis.html"
    assert payload["page_path"] == "/analysis?company=300017-%E7%BD%91%E5%AE%BF%E7%A7%91%E6%8A%80&result=300017-%E7%BD%91%E5%AE%BF%E7%A7%91%E6%8A%80-2025-analysis.html"


def test_record_user_artifact_upserts_workspace_project(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki"
    company_dir = wiki_root / "companies" / "300017-网宿科技"
    company_dir.mkdir(parents=True)
    (company_dir / "company.json").write_text(
        '{"stock_code":"300017","company_short_name":"网宿科技"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(workspace, "WIKI_ROOT", wiki_root.resolve())

    with _session(tmp_path) as session:
        item = workspace.record_user_artifact(
            session,
            user_id=1,
            artifact_type="report",
            artifact_key="wiki:analysis:300017-网宿科技:report.html",
            title="网宿科技 · 智能分析",
            path="/analysis?company=300017-%E7%BD%91%E5%AE%BF%E7%A7%91%E6%8A%80&result=report.html",
            source="analysis",
            global_artifact_id="/home/maoyd/wiki/companies/300017-网宿科技/analysis/report.html",
            company_dir="300017-网宿科技",
        )

        projects = session.exec(select(WorkspaceProject)).all()
        artifacts = session.exec(select(UserArtifact)).all()

    assert item.artifact_type == "report"
    assert len(artifacts) == 1
    assert len(projects) == 1
    assert projects[0].user_id == 1
    assert projects[0].company_code == "300017"
    assert projects[0].company_name == "网宿科技"
    assert projects[0].updated_at.tzinfo is None


def test_workspace_payload_serializes_naive_utc_datetimes_with_z_suffix():
    project = WorkspaceProject(
        id=3,
        user_id=1,
        name="示例项目",
        company_code="300017",
        company_name="网宿科技",
        created_at=datetime(2026, 7, 3, 1, 2, 3),
        updated_at=datetime(2026, 7, 3, 4, 5, 6),
    )
    artifact = UserArtifact(
        id=5,
        user_id=1,
        artifact_type="report",
        artifact_key="report-key",
        title="报告",
        path="/analysis",
        created_at=datetime(2026, 7, 3, 7, 8, 9),
    )

    project_payload = workspace._project_payload(project)
    artifact_payload = workspace._artifact_payload(artifact)

    assert project_payload["created_at"] == "2026-07-03T01:02:03Z"
    assert project_payload["updated_at"] == "2026-07-03T04:05:06Z"
    assert artifact_payload["createdAt"] == "2026-07-03T07:08:09Z"
    assert artifact_payload["created_at"] == "2026-07-03T07:08:09Z"


def test_search_workspace_artifacts_finds_document_parse_for_current_user(tmp_path):
    with _session(tmp_path) as session:
        session.add(
            UserArtifact(
                user_id=1,
                artifact_type="document_parse",
                artifact_key="doc-task-001",
                title="供应合同 Demo.pdf",
                path="/documents?task=doc-task-001",
                source="document_upload",
                global_artifact_id="doc-task-001",
            )
        )
        session.add(
            UserArtifact(
                user_id=2,
                artifact_type="document_parse",
                artifact_key="doc-task-002",
                title="供应合同 Other.pdf",
                path="/documents?task=doc-task-002",
                source="document_upload",
                global_artifact_id="doc-task-002",
            )
        )
        session.commit()

        result = workspace.search_workspace_artifacts(
            q="供应合同",
            limit=8,
            current_user=SimpleNamespace(id=1),
            session=session,
        )

    assert len(result["results"]) == 1
    item = result["results"][0]
    assert item["type"] == "document_parse"
    assert item["typeLabel"] == "文档解析"
    assert item["pageUrl"] == "/documents?task=doc-task-001"
    assert item["name"] == "供应合同 Demo.pdf"


def test_link_download_to_workspace_records_download_artifact(monkeypatch, tmp_path):
    downloads_root = tmp_path / "downloads"
    relative_path = "CN/贵州茅台/2025/年报/report.pdf"
    report_path = downloads_root / relative_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(b"%PDF-1.4\nfake")
    monkeypatch.setattr(workspace, "DOWNLOADS_ROOT", downloads_root.resolve())

    with _session(tmp_path) as session:
        result = workspace.link_download_to_workspace(
            {"relativePath": relative_path, "source": "manual_link"},
            current_user=SimpleNamespace(id=1),
            session=session,
        )
        artifact = session.exec(select(UserArtifact).where(UserArtifact.artifact_key == relative_path)).one()

    assert result["linked"] is True
    assert result["artifact"]["type"] == "download"
    assert result["artifact"]["key"] == relative_path
    assert result["artifact"]["path"] == relative_path
    assert artifact.user_id == 1
    assert artifact.title == "report.pdf"
    assert artifact.source == "manual_link"
    assert artifact.global_artifact_id == relative_path


def test_search_workspace_artifacts_derives_download_page_url(tmp_path):
    relative_path = "CN/贵州茅台/2025/年报/report final.pdf"
    with _session(tmp_path) as session:
        session.add(
            UserArtifact(
                user_id=1,
                artifact_type="download",
                artifact_key=relative_path,
                title="report final.pdf",
                path=relative_path,
                source="manual_link",
                global_artifact_id=relative_path,
            )
        )
        session.commit()

        result = workspace.search_workspace_artifacts(
            q="下载材料 贵州茅台",
            limit=8,
            current_user=SimpleNamespace(id=1),
            session=session,
        )

    assert len(result["results"]) == 1
    item = result["results"][0]
    assert item["type"] == "download"
    assert item["typeLabel"] == "下载材料"
    assert item["pageUrl"] == (
        "/api/downloads/report-file?path="
        "CN%2F%E8%B4%B5%E5%B7%9E%E8%8C%85%E5%8F%B0%2F2025%2F%E5%B9%B4%E6%8A%A5%2Freport%20final.pdf"
    )
    assert item["filename"] == "report final.pdf"


def test_proxy_pdf_task_rejects_non_owner_without_upstream_call(monkeypatch, tmp_path):
    called = {"proxy": False}

    async def fake_proxy_pdf2md(*args, **kwargs):
        called["proxy"] = True
        return Response(content=b"should not proxy", status_code=200)

    monkeypatch.setattr(workspace.source_proxy, "_proxy_pdf2md", fake_proxy_pdf2md)

    async def run_case(session):
        with pytest.raises(HTTPException) as exc:
            await workspace._proxy_pdf_task(
                SimpleNamespace(method="GET"),
                "blocked-task",
                "/api/result/blocked-task",
                current_user=SimpleNamespace(id=1, role="user"),
                session=session,
            )
        assert exc.value.status_code == 403
        assert exc.value.detail == "PDF task does not belong to current user"

    with _session(tmp_path) as session:
        anyio.run(run_case, session)

    assert called == {"proxy": False}


def test_proxy_pdf_task_for_owner_calls_expected_upstream(monkeypatch, tmp_path):
    calls = []

    async def fake_proxy_pdf2md(request, upstream_path, *, method=None, extra_headers=None):
        calls.append((request, upstream_path, method, extra_headers))
        return Response(content=b'{"ok": true}', status_code=202, media_type="application/json")

    monkeypatch.setattr(workspace.source_proxy, "_proxy_pdf2md", fake_proxy_pdf2md)
    request = SimpleNamespace(method="POST")

    async def run_case(session):
        response = await workspace._proxy_pdf_task(
            request,
            "owned-task",
            "/api/refetch/owned-task",
            current_user=SimpleNamespace(id=1, role="user"),
            session=session,
            method="POST",
        )
        assert response.status_code == 202
        assert response.body == b'{"ok": true}'

    with _session(tmp_path) as session:
        session.add(
            UserArtifact(
                user_id=1,
                artifact_type="parse",
                artifact_key="owned-task",
                title="owned-task.pdf",
                path="/pdf/result/owned-task",
                source="pdf_parser",
                global_artifact_id="owned-task",
            )
        )
        session.commit()
        anyio.run(run_case, session)

    assert calls == [
        (
            request,
            "/api/refetch/owned-task",
            "POST",
            {"X-SIQ-User-Id": "1", "X-SIQ-User-Role": "user", "X-SIQ-Allow-Legacy-Task": "1"},
        )
    ]


def test_proxy_pdf_task_accepts_async_session_for_owner(monkeypatch, tmp_path):
    calls = []

    async def fake_proxy_pdf2md(request, upstream_path, *, method=None, extra_headers=None):
        calls.append((request, upstream_path, method, extra_headers))
        return Response(content=b'{"ok": true}', status_code=200, media_type="application/json")

    monkeypatch.setattr(workspace.source_proxy, "_proxy_pdf2md", fake_proxy_pdf2md)
    request = SimpleNamespace(method="GET")

    async def run_case():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'workspace-async.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        async with AsyncSession(engine) as async_session:
            async_session.add(
                UserArtifact(
                    user_id=1,
                    artifact_type="parse",
                    artifact_key="async-owned-task",
                    title="async-owned-task.pdf",
                    path="/pdf/result/async-owned-task",
                    source="pdf_parser",
                    global_artifact_id="async-owned-task",
                )
            )
            await async_session.commit()
            response = await workspace._proxy_pdf_task(
                request,
                "async-owned-task",
                "/api/result/async-owned-task",
                current_user=SimpleNamespace(id=1, role="user"),
                session=async_session,
            )
            assert response.status_code == 200

    anyio.run(run_case)

    assert calls == [
        (
            request,
            "/api/result/async-owned-task",
            None,
            {"X-SIQ-User-Id": "1", "X-SIQ-User-Role": "user", "X-SIQ-Allow-Legacy-Task": "1"},
        )
    ]


def test_delete_shared_pdf_task_removes_workspace_link_without_upstream(monkeypatch, tmp_path):
    async def fake_proxy_pdf2md(*args, **kwargs):
        raise AssertionError("shared PDF deletion must not call upstream")

    monkeypatch.setattr(workspace.source_proxy, "_proxy_pdf2md", fake_proxy_pdf2md)

    async def run_case(async_session):
        async_session.add(
            UserArtifact(
                user_id=1,
                artifact_type="parse",
                artifact_key="shared-pdf-task",
                title="alice.pdf",
                path="/pdf/result/shared-pdf-task",
                source="pdf_parser",
                global_artifact_id="shared-pdf-task",
            )
        )
        async_session.add(
            UserArtifact(
                user_id=2,
                artifact_type="parse",
                artifact_key="shared-pdf-task",
                title="bob.pdf",
                path="/pdf/result/shared-pdf-task",
                source="pdf_parser",
                global_artifact_id="shared-pdf-task",
            )
        )
        await async_session.commit()

        result = await workspace.delete_my_pdf_task(
            SimpleNamespace(method="DELETE"),
            "shared-pdf-task",
            current_user=SimpleNamespace(id=1, role="user"),
            async_session=async_session,
        )
        query_result = await async_session.exec(workspace._parse_artifact_statement("shared-pdf-task"))
        links = query_result.all()
        assert result == {"success": True, "upstream_deleted": False, "scope": "workspace"}
        assert len(links) == 1
        assert links[0].user_id == 2

    anyio.run(_with_async_session, tmp_path, "workspace-delete-shared.db", run_case)


def test_delete_last_pdf_task_owner_proxies_upstream_delete(monkeypatch, tmp_path):
    calls = []

    async def fake_proxy_pdf2md(request, upstream_path, *, method=None, extra_headers=None):
        calls.append((request, upstream_path, method, extra_headers))
        return Response(content=b'{"success": true}', status_code=200, media_type="application/json")

    monkeypatch.setattr(workspace.source_proxy, "_proxy_pdf2md", fake_proxy_pdf2md)
    request = SimpleNamespace(method="DELETE")

    async def run_case(async_session):
        async_session.add(
            UserArtifact(
                user_id=1,
                artifact_type="parse",
                artifact_key="last-pdf-task",
                title="last.pdf",
                path="/pdf/result/last-pdf-task",
                source="pdf_parser",
                global_artifact_id="last-pdf-task",
            )
        )
        await async_session.commit()

        response = await workspace.delete_my_pdf_task(
            request,
            "last-pdf-task",
            current_user=SimpleNamespace(id=1, role="user"),
            async_session=async_session,
        )
        query_result = await async_session.exec(workspace._parse_artifact_statement("last-pdf-task"))
        links = query_result.all()
        assert response.status_code == 200
        assert response.headers["X-SIQ-Workspace-Unlinked"] == "1"
        assert links == []

    anyio.run(_with_async_session, tmp_path, "workspace-delete-last.db", run_case)

    assert calls == [
        (
            request,
            "/api/tasks/last-pdf-task",
            "DELETE",
            {"X-SIQ-User-Id": "1", "X-SIQ-User-Role": "user", "X-SIQ-Allow-Legacy-Task": "1"},
        )
    ]


def test_authenticated_pdf_upload_duplicate_content_records_reused_parse(monkeypatch, tmp_path):
    posted: dict[str, object] = {}
    file_bytes = b"%PDF-1.4\nexisting"
    file_sha256 = hashlib.sha256(file_bytes).hexdigest()
    config_hash = _pdf_config_hash()
    duplicate_payload = {
        "error": "duplicate_file_content",
        "filename": "annual.pdf",
        "existingTask": {
            "task_id": "existing-task",
            "filename": "annual.pdf",
            "file_sha256": file_sha256,
            "parse_config_hash": config_hash,
        },
    }

    class FakeUpload:
        filename = "annual.pdf"
        content_type = "application/pdf"

        async def read(self):
            return file_bytes

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, data=None, files=None, headers=None):
            posted["url"] = url
            posted["data"] = data
            posted["files"] = files
            posted["headers"] = headers
            return SimpleNamespace(
                status_code=409,
                headers={"content-type": "application/json"},
                content=json.dumps(duplicate_payload).encode("utf-8"),
                json=lambda: duplicate_payload,
            )

    async def fake_pdf_tasks_by_filename(**_kwargs):
        return {
            "annual.pdf": {
                "task_id": "existing-task",
                "filename": "annual.pdf",
                "file_sha256": file_sha256,
                "parse_config_hash": config_hash,
                "market": "CN",
            }
        }

    monkeypatch.setattr(workspace, "PDF2MD_API_BASE", "http://pdf2md.test")
    monkeypatch.setattr(workspace, "_pdf_tasks_by_filename", fake_pdf_tasks_by_filename)
    monkeypatch.setattr(workspace.httpx, "AsyncClient", FakeAsyncClient)

    async def run_case(async_session):
        response = await workspace.authenticated_pdf_upload(
            files=[FakeUpload()],
            current_user=SimpleNamespace(id=1, role="user"),
            async_session=async_session,
        )
        artifact_result = await async_session.exec(
            select(UserArtifact).where(UserArtifact.artifact_key == "existing-task")
        )
        usage_result = await async_session.exec(select(UsageEvent).where(UsageEvent.event_type == PARSE_EVENT))
        artifact = artifact_result.one()
        usage = usage_result.all()
        assert response.status_code == 409
        assert json.loads(response.body) == duplicate_payload
        assert artifact.user_id == 1
        assert artifact.artifact_type == "parse"
        assert artifact.title == "annual.pdf"
        assert artifact.path == "http://pdf2md.test/api/result/existing-task?market=CN"
        assert artifact.source == "reused_parse"
        assert artifact.global_artifact_id == "existing-task"
        assert usage == []
        return response

    response = anyio.run(_with_async_session, tmp_path, "workspace-upload-duplicate.db", run_case)

    assert posted["url"] == "http://pdf2md.test/api/upload"
    assert posted["files"][0][1][0] == "annual.pdf"
    assert posted["headers"]["X-SIQ-User-Id"] == "1"
    assert posted["headers"]["X-SIQ-User-Role"] == "user"
    assert response.status_code == 409


def test_authenticated_pdf_task_from_download_posts_reference_and_records_parse(monkeypatch, tmp_path):
    posted: dict[str, object] = {}
    downloads_root = tmp_path / "downloads"
    relative_path = "HK/00005-HSBC/2025/report.pdf"
    report_path = downloads_root / relative_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(b"%PDF-1.4\nfrom-download")
    file_sha256 = hashlib.sha256(report_path.read_bytes()).hexdigest()
    config_hash = _pdf_config_hash("HK")
    success_payload = {
        "tasks": [
            {
                "task_id": "download-task",
                "filename": "report.pdf",
                "status": "queued",
                "file_sha256": file_sha256,
                "parse_config_hash": config_hash,
                "market": "HK",
            },
        ],
        "task_id": "download-task",
        "batch_count": 1,
    }

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None):
            posted["url"] = url
            posted["json"] = json
            posted["headers"] = headers
            return SimpleNamespace(
                status_code=200,
                headers={"content-type": "application/json"},
                content=json_module.dumps(success_payload).encode("utf-8"),
                json=lambda: success_payload,
            )

    async def fake_pdf_tasks_by_filename(**_kwargs):
        return {}

    monkeypatch.setattr(workspace, "DOWNLOADS_ROOT", downloads_root.resolve())
    monkeypatch.setattr(workspace, "PDF2MD_API_BASE", "http://pdf2md.test")
    monkeypatch.setattr(workspace, "_pdf_tasks_by_filename", fake_pdf_tasks_by_filename)
    monkeypatch.setattr(workspace.httpx, "AsyncClient", FakeAsyncClient)

    async def run_case(async_session):
        async_session.add(
            UserArtifact(
                user_id=1,
                artifact_type="download",
                artifact_key=relative_path,
                title="report.pdf",
                path=relative_path,
                source="manual_link",
                global_artifact_id=relative_path,
            )
        )
        await async_session.commit()
        response = await workspace.authenticated_pdf_task_from_download(
            {
                "download_relative_path": relative_path,
                "filename": "report.pdf",
                "market": "HK",
                "backend": "hybrid-http-client",
                "parse_method": "auto",
                "formula_enable": "true",
                "table_enable": "true",
            },
            current_user=SimpleNamespace(id=1, role="user"),
            async_session=async_session,
        )
        artifacts_result = await async_session.exec(
            select(UserArtifact).where(UserArtifact.artifact_type == "parse")
        )
        usage_result = await async_session.exec(select(UsageEvent).where(UsageEvent.event_type == PARSE_EVENT))
        return response, artifacts_result.all(), usage_result.all()

    response, artifacts, usage = anyio.run(
        _with_async_session,
        tmp_path,
        "workspace-download-reference.db",
        run_case,
    )

    assert response == success_payload
    assert posted["url"] == "http://pdf2md.test/api/tasks/from-download"
    assert posted["json"]["source_path"] == str(report_path.resolve())
    assert posted["json"]["download_relative_path"] == relative_path
    assert posted["headers"]["X-SIQ-User-Id"] == "1"
    assert [(item.artifact_key, item.source) for item in artifacts] == [("download-task", "new_parse")]
    assert len(usage) == 1
    assert usage[0].source == "pdf_download_reference"
    assert usage[0].count == 1


def test_authenticated_pdf_task_from_download_requires_workspace_link(monkeypatch, tmp_path):
    called = {"post": False}
    downloads_root = tmp_path / "downloads"
    relative_path = "HK/00005-HSBC/2025/report.pdf"
    report_path = downloads_root / relative_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(b"%PDF-1.4\nfrom-download")

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *_args, **_kwargs):
            called["post"] = True
            raise AssertionError("unlinked downloads must not call parser")

    async def fake_pdf_tasks_by_filename(**_kwargs):
        return {}

    monkeypatch.setattr(workspace, "DOWNLOADS_ROOT", downloads_root.resolve())
    monkeypatch.setattr(workspace, "_pdf_tasks_by_filename", fake_pdf_tasks_by_filename)
    monkeypatch.setattr(workspace.httpx, "AsyncClient", FakeAsyncClient)

    async def run_case(async_session):
        with pytest.raises(HTTPException) as exc:
            await workspace.authenticated_pdf_task_from_download(
                {"download_relative_path": relative_path, "filename": "report.pdf", "market": "HK"},
                current_user=SimpleNamespace(id=1, role="user"),
                async_session=async_session,
            )
        assert exc.value.status_code == 403

    anyio.run(_with_async_session, tmp_path, "workspace-download-reference-forbidden.db", run_case)
    assert called == {"post": False}


def test_authenticated_pdf_task_from_download_rejects_market_path_mismatch(monkeypatch, tmp_path):
    downloads_root = tmp_path / "downloads"
    relative_path = "HK/00005-HSBC/2025/report.pdf"
    report_path = downloads_root / relative_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(b"%PDF-1.4\nfrom-download")

    async def fail_pdf_tasks_by_filename(**_kwargs):
        raise AssertionError("mismatched market must fail before parser lookup")

    monkeypatch.setattr(workspace, "DOWNLOADS_ROOT", downloads_root.resolve())
    monkeypatch.setattr(workspace, "_pdf_tasks_by_filename", fail_pdf_tasks_by_filename)

    async def run_case(async_session):
        with pytest.raises(HTTPException) as exc:
            await workspace.authenticated_pdf_task_from_download(
                {"download_relative_path": relative_path, "filename": "report.pdf", "market": "CN"},
                current_user=SimpleNamespace(id=99, role="super_admin"),
                async_session=async_session,
            )
        assert exc.value.status_code == 400
        assert exc.value.detail == "Downloaded PDF belongs to HK, not CN"

    anyio.run(_with_async_session, tmp_path, "workspace-download-reference-market.db", run_case)


def test_authenticated_pdf_upload_duplicate_content_ignores_full_quota(monkeypatch, tmp_path):
    file_bytes = b"%PDF-1.4\nexisting"
    file_sha256 = hashlib.sha256(file_bytes).hexdigest()
    config_hash = _pdf_config_hash()
    duplicate_payload = {
        "error": "duplicate_file_content",
        "filename": "annual.pdf",
        "existingTask": {
            "task_id": "existing-task",
            "filename": "annual.pdf",
            "file_sha256": file_sha256,
            "parse_config_hash": config_hash,
        },
    }

    class FakeUpload:
        filename = "annual.pdf"
        content_type = "application/pdf"

        async def read(self):
            return file_bytes

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, data=None, files=None, headers=None):
            return SimpleNamespace(
                status_code=409,
                headers={"content-type": "application/json"},
                content=json.dumps(duplicate_payload).encode("utf-8"),
                json=lambda: duplicate_payload,
            )

    async def fake_pdf_tasks_by_filename(**_kwargs):
        return {
            "annual.pdf": {
                "task_id": "existing-task",
                "filename": "annual.pdf",
                "file_sha256": file_sha256,
                "parse_config_hash": config_hash,
                "market": "CN",
            }
        }

    monkeypatch.setattr(workspace, "PDF2MD_API_BASE", "http://pdf2md.test")
    monkeypatch.setattr(workspace, "_pdf_tasks_by_filename", fake_pdf_tasks_by_filename)
    monkeypatch.setattr(workspace.httpx, "AsyncClient", FakeAsyncClient)

    async def run_case(async_session):
        async_session.add(
            UsageEvent(
                user_id=1,
                event_type=PARSE_EVENT,
                event_date=current_day_key(),
                count=2,
                source="existing_parse",
            )
        )
        await async_session.commit()

        response = await workspace.authenticated_pdf_upload(
            files=[FakeUpload()],
            current_user=SimpleNamespace(id=1, role="user"),
            async_session=async_session,
        )
        artifact_result = await async_session.exec(
            select(UserArtifact).where(UserArtifact.artifact_key == "existing-task")
        )
        usage_result = await async_session.exec(select(UsageEvent).where(UsageEvent.event_type == PARSE_EVENT))
        return response, artifact_result.one(), usage_result.all()

    response, artifact, usage = anyio.run(
        _with_async_session,
        tmp_path,
        "workspace-upload-duplicate-full-quota.db",
        run_case,
    )

    assert response.status_code == 409
    assert artifact.source == "reused_parse"
    assert sum(item.count for item in usage) == 2
    assert [item.source for item in usage] == ["existing_parse"]


def test_authenticated_pdf_upload_duplicate_file_content_records_reused_parse_without_quota(
    monkeypatch, tmp_path
):
    posted: dict[str, object] = {}
    file_bytes = b"%PDF-1.4\nsame-content"
    file_sha256 = hashlib.sha256(file_bytes).hexdigest()
    config_hash = _pdf_config_hash()
    duplicate_payload = {
        "error": "duplicate_file_content",
        "filename": "renamed.pdf",
        "existingTask": {
            "task_id": "existing-task",
            "filename": "original.pdf",
            "file_sha256": file_sha256,
            "parse_config_hash": config_hash,
        },
    }
    quota_calls: list[dict[str, object]] = []

    class FakeUpload:
        filename = "renamed.pdf"
        content_type = "application/pdf"

        async def read(self):
            return file_bytes

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, data=None, files=None, headers=None):
            posted["url"] = url
            posted["files"] = files
            return SimpleNamespace(
                status_code=409,
                headers={"content-type": "application/json"},
                content=json.dumps(duplicate_payload).encode("utf-8"),
                json=lambda: duplicate_payload,
            )

    async def fake_pdf_tasks_by_filename(**_kwargs):
        return {
            "original.pdf": {
                "task_id": "existing-task",
                "filename": "original.pdf",
                "file_sha256": file_sha256,
                "parse_config_hash": config_hash,
                "market": "CN",
            }
        }

    async def fake_enforce_quota(async_session, current_user, event_type, increment=1):
        quota_calls.append({"event_type": event_type, "increment": increment})
        return (0, 2)

    monkeypatch.setattr(workspace, "PDF2MD_API_BASE", "http://pdf2md.test")
    monkeypatch.setattr(workspace, "_pdf_tasks_by_filename", fake_pdf_tasks_by_filename)
    monkeypatch.setattr(workspace, "enforce_quota_or_429_async", fake_enforce_quota)
    monkeypatch.setattr(workspace.httpx, "AsyncClient", FakeAsyncClient)

    async def run_case(async_session):
        response = await workspace.authenticated_pdf_upload(
            files=[FakeUpload()],
            current_user=SimpleNamespace(id=1, role="user"),
            async_session=async_session,
        )
        artifact_result = await async_session.exec(
            select(UserArtifact).where(UserArtifact.artifact_key == "existing-task")
        )
        usage_result = await async_session.exec(select(UsageEvent).where(UsageEvent.event_type == PARSE_EVENT))
        return response, artifact_result.one(), usage_result.all()

    response, artifact, usage = anyio.run(
        _with_async_session,
        tmp_path,
        "workspace-upload-duplicate-content.db",
        run_case,
    )

    assert posted["url"] == "http://pdf2md.test/api/upload"
    assert quota_calls == []
    assert response.status_code == 409
    assert json.loads(response.body) == duplicate_payload
    assert artifact.source == "reused_parse"
    assert artifact.artifact_key == "existing-task"
    assert usage == []


def test_authenticated_pdf_upload_mixed_existing_and_new_uses_new_parse_quota(monkeypatch, tmp_path):
    quota_calls: list[dict[str, object]] = []
    config_hash = _pdf_config_hash()
    old_sha256 = hashlib.sha256(b"%PDF-1.4\nold.pdf").hexdigest()
    success_payload = {
        "tasks": [
            {
                "task_id": "new-task",
                "filename": "new.pdf",
                "status": "queued",
                "file_sha256": hashlib.sha256(b"%PDF-1.4\nnew.pdf").hexdigest(),
                "parse_config_hash": config_hash,
                "market": "CN",
            },
        ]
    }

    class FakeUpload:
        def __init__(self, filename: str):
            self.filename = filename
            self.content_type = "application/pdf"

        async def read(self):
            return f"%PDF-1.4\n{self.filename}".encode("utf-8")

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, data=None, files=None, headers=None):
            assert url == "http://pdf2md.test/api/upload"
            assert [item[1][0] for item in files] == ["old.pdf", "new.pdf"]
            return SimpleNamespace(
                status_code=200,
                headers={"content-type": "application/json"},
                content=json.dumps(success_payload).encode("utf-8"),
                json=lambda: success_payload,
            )

    async def fake_pdf_tasks_by_filename(**_kwargs):
        return {
            "old.pdf": {
                "task_id": "old-task",
                "filename": "old.pdf",
                "file_sha256": old_sha256,
                "parse_config_hash": config_hash,
                "market": "CN",
            }
        }

    async def fake_enforce_quota(async_session, current_user, event_type, increment=1):
        quota_calls.append({"event_type": event_type, "increment": increment})
        return (0, 2)

    monkeypatch.setattr(workspace, "PDF2MD_API_BASE", "http://pdf2md.test")
    monkeypatch.setattr(workspace, "_pdf_tasks_by_filename", fake_pdf_tasks_by_filename)
    monkeypatch.setattr(workspace, "enforce_quota_or_429_async", fake_enforce_quota)
    monkeypatch.setattr(workspace.httpx, "AsyncClient", FakeAsyncClient)

    async def run_case(async_session):
        response = await workspace.authenticated_pdf_upload(
            files=[FakeUpload("old.pdf"), FakeUpload("new.pdf")],
            current_user=SimpleNamespace(id=1, role="user"),
            async_session=async_session,
        )
        artifacts_result = await async_session.exec(select(UserArtifact).order_by(UserArtifact.artifact_key))
        usage_result = await async_session.exec(select(UsageEvent).where(UsageEvent.event_type == PARSE_EVENT))
        return response, artifacts_result.all(), usage_result.all()

    response, artifacts, usage = anyio.run(
        _with_async_session,
        tmp_path,
        "workspace-upload-mixed-existing-new.db",
        run_case,
    )

    assert response == success_payload
    assert quota_calls == [{"event_type": PARSE_EVENT, "increment": 1}]
    assert [(item.artifact_key, item.source) for item in artifacts] == [("new-task", "new_parse")]
    assert len(usage) == 1
    assert usage[0].count == 1
    assert usage[0].source == "pdf_upload"


def test_authenticated_pdf_upload_mixed_existing_hash_and_new_uses_new_parse_quota(monkeypatch, tmp_path):
    quota_calls: list[dict[str, object]] = []
    shared_bytes = b"%PDF-1.4\nshared-content"
    shared_sha256 = hashlib.sha256(shared_bytes).hexdigest()
    config_hash = _pdf_config_hash()
    success_payload = {
        "tasks": [
            {
                "task_id": "new-task",
                "filename": "new.pdf",
                "status": "queued",
                "file_sha256": hashlib.sha256(b"%PDF-1.4\nnew-content").hexdigest(),
                "parse_config_hash": config_hash,
                "market": "CN",
            },
        ]
    }

    class FakeUpload:
        def __init__(self, filename: str, content: bytes):
            self.filename = filename
            self.content_type = "application/pdf"
            self._content = content

        async def read(self):
            return self._content

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, data=None, files=None, headers=None):
            assert url == "http://pdf2md.test/api/upload"
            assert [item[1][0] for item in files] == ["renamed.pdf", "new.pdf"]
            return SimpleNamespace(
                status_code=200,
                headers={"content-type": "application/json"},
                content=json.dumps(success_payload).encode("utf-8"),
                json=lambda: success_payload,
            )

    async def fake_pdf_tasks_by_filename(**_kwargs):
        return {
            "original.pdf": {
                "task_id": "shared-task",
                "filename": "original.pdf",
                "file_sha256": shared_sha256,
                "parse_config_hash": config_hash,
                "market": "CN",
            }
        }

    async def fake_enforce_quota(async_session, current_user, event_type, increment=1):
        quota_calls.append({"event_type": event_type, "increment": increment})
        return (0, 2)

    monkeypatch.setattr(workspace, "PDF2MD_API_BASE", "http://pdf2md.test")
    monkeypatch.setattr(workspace, "_pdf_tasks_by_filename", fake_pdf_tasks_by_filename)
    monkeypatch.setattr(workspace, "enforce_quota_or_429_async", fake_enforce_quota)
    monkeypatch.setattr(workspace.httpx, "AsyncClient", FakeAsyncClient)

    async def run_case(async_session):
        response = await workspace.authenticated_pdf_upload(
            files=[
                FakeUpload("renamed.pdf", shared_bytes),
                FakeUpload("new.pdf", b"%PDF-1.4\nnew-content"),
            ],
            current_user=SimpleNamespace(id=1, role="user"),
            async_session=async_session,
        )
        artifacts_result = await async_session.exec(select(UserArtifact).order_by(UserArtifact.artifact_key))
        usage_result = await async_session.exec(select(UsageEvent).where(UsageEvent.event_type == PARSE_EVENT))
        return response, artifacts_result.all(), usage_result.all()

    response, artifacts, usage = anyio.run(
        _with_async_session,
        tmp_path,
        "workspace-upload-mixed-existing-hash-new.db",
        run_case,
    )

    assert response == success_payload
    assert quota_calls == [{"event_type": PARSE_EVENT, "increment": 1}]
    assert [(item.artifact_key, item.source) for item in artifacts] == [("new-task", "new_parse")]
    assert len(usage) == 1
    assert usage[0].count == 1
    assert usage[0].source == "pdf_upload"


def test_authenticated_pdf_upload_mixed_reused_and_new_tasks_classifies_usage_and_artifacts(
    monkeypatch, tmp_path
):
    quota_calls: list[dict[str, object]] = []
    config_hash = _pdf_config_hash()
    success_payload = {
        "tasks": [
            {
                "task_id": "old-task",
                "filename": "old.pdf",
                "status": "completed",
                "file_sha256": hashlib.sha256(b"%PDF-1.4\nold.pdf").hexdigest(),
                "parse_config_hash": config_hash,
                "market": "CN",
            },
            {
                "task_id": "shared-task",
                "filename": "shared.pdf",
                "status": "completed",
                "file_sha256": hashlib.sha256(b"%PDF-1.4\nshared.pdf").hexdigest(),
                "parse_config_hash": config_hash,
                "market": "CN",
            },
            {
                "task_id": "new-task",
                "filename": "new.pdf",
                "status": "queued",
                "file_sha256": hashlib.sha256(b"%PDF-1.4\nnew.pdf").hexdigest(),
                "parse_config_hash": config_hash,
                "market": "CN",
            },
        ]
    }

    class FakeUpload:
        def __init__(self, filename: str):
            self.filename = filename
            self.content_type = "application/pdf"

        async def read(self):
            return f"%PDF-1.4\n{self.filename}".encode("utf-8")

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, data=None, files=None, headers=None):
            assert url == "http://pdf2md.test/api/upload"
            assert [item[1][0] for item in files] == ["old.pdf", "shared.pdf", "new.pdf"]
            return SimpleNamespace(
                status_code=200,
                headers={"content-type": "application/json"},
                content=json.dumps(success_payload).encode("utf-8"),
                json=lambda: success_payload,
            )

    async def fake_pdf_tasks_by_filename(**_kwargs):
        return {
            "old.pdf": {
                "task_id": "old-task",
                "filename": "old.pdf",
                "file_sha256": hashlib.sha256(b"%PDF-1.4\nold.pdf").hexdigest(),
                "parse_config_hash": config_hash,
                "market": "CN",
            },
            "shared.pdf": {
                "task_id": "shared-task",
                "filename": "shared.pdf",
                "file_sha256": hashlib.sha256(b"%PDF-1.4\nshared.pdf").hexdigest(),
                "parse_config_hash": config_hash,
                "market": "CN",
            },
        }

    async def fake_enforce_quota(async_session, current_user, event_type, increment=1):
        quota_calls.append({"event_type": event_type, "increment": increment})
        return (0, 2)

    monkeypatch.setattr(workspace, "PDF2MD_API_BASE", "http://pdf2md.test")
    monkeypatch.setattr(workspace, "_pdf_tasks_by_filename", fake_pdf_tasks_by_filename)
    monkeypatch.setattr(workspace, "enforce_quota_or_429_async", fake_enforce_quota)
    monkeypatch.setattr(workspace.httpx, "AsyncClient", FakeAsyncClient)

    async def run_case(async_session):
        await workspace.record_user_artifact_async(
            async_session,
            user_id=1,
            artifact_type="parse",
            artifact_key="old-task",
            title="old.pdf",
            path="http://pdf2md.test/api/result/old-task",
            source="existing_parse",
            global_artifact_id="old-task",
        )

        response = await workspace.authenticated_pdf_upload(
            files=[FakeUpload("old.pdf"), FakeUpload("shared.pdf"), FakeUpload("new.pdf")],
            current_user=SimpleNamespace(id=1, role="user"),
            async_session=async_session,
        )
        artifacts_result = await async_session.exec(
            select(UserArtifact).where(UserArtifact.artifact_type == "parse").order_by(UserArtifact.artifact_key)
        )
        usage_result = await async_session.exec(select(UsageEvent).where(UsageEvent.event_type == PARSE_EVENT))
        return response, artifacts_result.all(), usage_result.all()

    response, artifacts, usage = anyio.run(
        _with_async_session,
        tmp_path,
        "workspace-upload-mixed-reused-new.db",
        run_case,
    )

    assert response == success_payload
    assert quota_calls == [{"event_type": PARSE_EVENT, "increment": 1}]
    assert [(item.artifact_key, item.source) for item in artifacts] == [
        ("new-task", "new_parse"),
        ("old-task", "existing_parse"),
        ("shared-task", "reused_parse"),
    ]
    assert len(usage) == 1
    assert usage[0].count == 1
    assert usage[0].source == "pdf_upload"
    assert json.loads(usage[0].metadata_json or "{}") == {
        "tasks": [
            {
                "task_id": "new-task",
                "filename": "new.pdf",
                "status": "queued",
                "file_sha256": hashlib.sha256(b"%PDF-1.4\nnew.pdf").hexdigest(),
                "parse_config_hash": config_hash,
                "market": "CN",
            }
        ]
    }


def test_authenticated_pdf_upload_records_market_on_tasks_and_artifacts(monkeypatch, tmp_path):
    success_payload = {
        "tasks": [
            {"task_id": "eu-task", "filename": "annual-report.pdf", "status": "queued"},
        ]
    }

    class FakeUpload:
        filename = "annual-report.pdf"
        content_type = "application/pdf"

        async def read(self):
            return b"%PDF-1.4\nmarket"

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, data=None, files=None, headers=None):
            assert data["market"] == "EU"
            return SimpleNamespace(
                status_code=200,
                headers={"content-type": "application/json"},
                content=json.dumps(success_payload).encode("utf-8"),
                json=lambda: success_payload,
            )

    async def fake_pdf_tasks_by_filename(**_kwargs):
        return {}

    monkeypatch.setattr(workspace, "PDF2MD_API_BASE", "http://pdf2md.test")
    monkeypatch.setattr(workspace, "_pdf_tasks_by_filename", fake_pdf_tasks_by_filename)
    monkeypatch.setattr(workspace.httpx, "AsyncClient", FakeAsyncClient)

    async def run_case(async_session):
        response = await workspace.authenticated_pdf_upload(
            files=[FakeUpload()],
            market="EU",
            current_user=SimpleNamespace(id=1, role="user"),
            async_session=async_session,
        )
        artifact_result = await async_session.exec(select(UserArtifact).where(UserArtifact.artifact_key == "eu-task"))
        return response, artifact_result.one()

    response, artifact = anyio.run(
        _with_async_session,
        tmp_path,
        "workspace-upload-market.db",
        run_case,
    )

    assert response["tasks"][0]["market"] == "EU"
    assert artifact.path == "http://pdf2md.test/api/result/eu-task?market=EU"


def test_authenticated_pdf_upload_upstream_error_does_not_record_usage_or_artifact(monkeypatch, tmp_path):
    posted: dict[str, object] = {}
    error_payload = {"error": "upstream_failed"}

    class FakeUpload:
        filename = "annual-new.pdf"
        content_type = "application/pdf"

        async def read(self):
            return b"%PDF-1.4\nnew"

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, data=None, files=None, headers=None):
            posted["url"] = url
            posted["files"] = files
            return SimpleNamespace(
                status_code=500,
                headers={"content-type": "application/json"},
                content=json.dumps(error_payload).encode("utf-8"),
                json=lambda: error_payload,
            )

    async def fake_pdf_tasks_by_filename(**_kwargs):
        return {}

    monkeypatch.setattr(workspace, "PDF2MD_API_BASE", "http://pdf2md.test")
    monkeypatch.setattr(workspace, "_pdf_tasks_by_filename", fake_pdf_tasks_by_filename)
    monkeypatch.setattr(workspace.httpx, "AsyncClient", FakeAsyncClient)

    async def run_case(async_session):
        response = await workspace.authenticated_pdf_upload(
            files=[FakeUpload()],
            current_user=SimpleNamespace(id=1, role="user"),
            async_session=async_session,
        )
        artifacts_result = await async_session.exec(select(UserArtifact))
        usage_result = await async_session.exec(select(UsageEvent).where(UsageEvent.event_type == PARSE_EVENT))
        return response, artifacts_result.all(), usage_result.all()

    response, artifacts, usage = anyio.run(
        _with_async_session,
        tmp_path,
        "workspace-upload-upstream-error.db",
        run_case,
    )

    assert posted["url"] == "http://pdf2md.test/api/upload"
    assert posted["files"][0][1][0] == "annual-new.pdf"
    assert response.status_code == 500
    assert response.body == json.dumps(error_payload, ensure_ascii=False).encode("utf-8")
    assert usage == []
    assert artifacts == []


def test_list_my_pdf_tasks_defaults_to_workspace_scope_for_non_admin(monkeypatch, tmp_path):
    upstream_tasks = {
        "tasks": [
            {"task_id": "eu-task", "filename": "plain.pdf", "status": "queued"},
            {"task_id": "us-task", "filename": "NVIDIA_US_manual_upload.pdf", "status": "queued"},
        ]
    }

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            assert url == "http://pdf2md.test/api/tasks"
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: upstream_tasks,
            )

    monkeypatch.setattr(workspace, "PDF2MD_API_BASE", "http://pdf2md.test")
    monkeypatch.setattr(workspace.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.delenv("SIQ_PDF_TASK_LIST_WORKSPACE_ONLY", raising=False)
    monkeypatch.delenv("SIQ_PDF_TASK_LIST_SYSTEM_VISIBLE", raising=False)

    async def run_case(async_session):
        await workspace.record_user_artifact_async(
            async_session,
            user_id=2,
            artifact_type="parse",
            artifact_key="eu-task",
            title="plain.pdf",
            path="http://pdf2md.test/api/result/eu-task?market=EU",
            source="new_parse",
            global_artifact_id="eu-task",
        )
        return await workspace.list_my_pdf_tasks(
            current_user=SimpleNamespace(id=1, role="user"),
            async_session=async_session,
        )

    result = anyio.run(
        _with_async_session,
        tmp_path,
        "workspace-list-market-default-workspace.db",
        run_case,
    )

    assert result["scope"] == "workspace"
    assert result["tasks"] == []


def test_list_my_pdf_tasks_enriches_market_for_explicit_system_scope(monkeypatch, tmp_path):
    upstream_tasks = {
        "tasks": [
            {"task_id": "eu-task", "filename": "plain.pdf", "status": "queued"},
            {"task_id": "us-task", "filename": "NVIDIA_US_manual_upload.pdf", "status": "queued"},
        ]
    }

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            assert url == "http://pdf2md.test/api/tasks"
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: upstream_tasks,
            )

    monkeypatch.setattr(workspace, "PDF2MD_API_BASE", "http://pdf2md.test")
    monkeypatch.setattr(workspace.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.delenv("SIQ_PDF_TASK_LIST_WORKSPACE_ONLY", raising=False)
    monkeypatch.setenv("SIQ_PDF_TASK_LIST_SYSTEM_VISIBLE", "1")

    async def run_case(async_session):
        await workspace.record_user_artifact_async(
            async_session,
            user_id=2,
            artifact_type="parse",
            artifact_key="eu-task",
            title="plain.pdf",
            path="http://pdf2md.test/api/result/eu-task?market=EU",
            source="new_parse",
            global_artifact_id="eu-task",
        )
        return await workspace.list_my_pdf_tasks(
            current_user=SimpleNamespace(id=1, role="user"),
            async_session=async_session,
        )

    result = anyio.run(
        _with_async_session,
        tmp_path,
        "workspace-list-market-system.db",
        run_case,
    )

    assert result["scope"] == "system"
    assert [(item["task_id"], item.get("market")) for item in result["tasks"]] == [
        ("eu-task", "EU"),
        ("us-task", "US"),
    ]


def test_list_my_pdf_tasks_workspace_only_enriches_market_from_user_artifact(monkeypatch, tmp_path):
    upstream_tasks = {
        "tasks": [
            {"task_id": "eu-task", "filename": "plain.pdf", "status": "queued"},
            {"task_id": "hk-task", "filename": "Tencent_HK_00700_annual.pdf", "status": "queued"},
        ]
    }

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            assert url == "http://pdf2md.test/api/tasks"
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: upstream_tasks,
            )

    monkeypatch.setattr(workspace, "PDF2MD_API_BASE", "http://pdf2md.test")
    monkeypatch.setattr(workspace.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setenv("SIQ_PDF_TASK_LIST_WORKSPACE_ONLY", "true")
    monkeypatch.setenv("SIQ_PDF_TASK_LIST_SYSTEM_VISIBLE", "1")

    async def run_case(async_session):
        await workspace.record_user_artifact_async(
            async_session,
            user_id=1,
            artifact_type="parse",
            artifact_key="eu-task",
            title="plain.pdf",
            path="http://pdf2md.test/api/result/eu-task?market=EU",
            source="new_parse",
            global_artifact_id="eu-task",
        )
        return await workspace.list_my_pdf_tasks(
            current_user=SimpleNamespace(id=1, role="user"),
            async_session=async_session,
        )

    result = anyio.run(
        _with_async_session,
        tmp_path,
        "workspace-list-market-workspace-only.db",
        run_case,
    )

    assert result["scope"] == "workspace"
    assert [(item["task_id"], item.get("market")) for item in result["tasks"]] == [("eu-task", "EU")]
