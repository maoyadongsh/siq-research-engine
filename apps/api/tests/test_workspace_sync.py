import json
import sys
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest
from fastapi import HTTPException
from fastapi.responses import Response
from sqlmodel import Session, SQLModel, create_engine, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routers import workspace
from services.usage_service import PARSE_EVENT, UsageEvent, UserArtifact, WorkspaceProject


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'workspace.db'}")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


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

    async def fake_proxy_pdf2md(request, upstream_path, *, method=None):
        calls.append((request, upstream_path, method))
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

    assert calls == [(request, "/api/refetch/owned-task", "POST")]


def test_delete_shared_pdf_task_removes_workspace_link_without_upstream(monkeypatch, tmp_path):
    async def fake_proxy_pdf2md(*args, **kwargs):
        raise AssertionError("shared PDF deletion must not call upstream")

    monkeypatch.setattr(workspace.source_proxy, "_proxy_pdf2md", fake_proxy_pdf2md)

    async def run_case(session):
        result = await workspace.delete_my_pdf_task(
            SimpleNamespace(method="DELETE"),
            "shared-pdf-task",
            current_user=SimpleNamespace(id=1, role="user"),
            session=session,
        )
        links = session.exec(workspace._parse_artifact_statement("shared-pdf-task")).all()
        assert result == {"success": True, "upstream_deleted": False, "scope": "workspace"}
        assert len(links) == 1
        assert links[0].user_id == 2

    with _session(tmp_path) as session:
        session.add(
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
        session.add(
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
        session.commit()
        anyio.run(run_case, session)


def test_delete_last_pdf_task_owner_proxies_upstream_delete(monkeypatch, tmp_path):
    calls = []

    async def fake_proxy_pdf2md(request, upstream_path, *, method=None):
        calls.append((request, upstream_path, method))
        return Response(content=b'{"success": true}', status_code=200, media_type="application/json")

    monkeypatch.setattr(workspace.source_proxy, "_proxy_pdf2md", fake_proxy_pdf2md)
    request = SimpleNamespace(method="DELETE")

    async def run_case(session):
        response = await workspace.delete_my_pdf_task(
            request,
            "last-pdf-task",
            current_user=SimpleNamespace(id=1, role="user"),
            session=session,
        )
        links = session.exec(workspace._parse_artifact_statement("last-pdf-task")).all()
        assert response.status_code == 200
        assert response.headers["X-SIQ-Workspace-Unlinked"] == "1"
        assert links == []

    with _session(tmp_path) as session:
        session.add(
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
        session.commit()
        anyio.run(run_case, session)

    assert calls == [(request, "/api/tasks/last-pdf-task", "DELETE")]


def test_authenticated_pdf_upload_duplicate_filename_records_reused_parse(monkeypatch, tmp_path):
    posted: dict[str, object] = {}
    duplicate_payload = {
        "error": "duplicate_filename",
        "filename": "annual.pdf",
        "existingTask": {"task_id": "existing-task", "filename": "annual.pdf"},
    }

    class FakeUpload:
        filename = "annual.pdf"
        content_type = "application/pdf"

        async def read(self):
            return b"%PDF-1.4\nexisting"

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

    async def fake_pdf_tasks_by_filename():
        return {"annual.pdf": {"task_id": "existing-task", "filename": "annual.pdf"}}

    monkeypatch.setattr(workspace, "PDF2MD_API_BASE", "http://pdf2md.test")
    monkeypatch.setattr(workspace, "_pdf_tasks_by_filename", fake_pdf_tasks_by_filename)
    monkeypatch.setattr(workspace.httpx, "AsyncClient", FakeAsyncClient)

    async def run_case(session):
        return await workspace.authenticated_pdf_upload(
            files=[FakeUpload()],
            current_user=SimpleNamespace(id=1, role="user"),
            session=session,
        )

    with _session(tmp_path) as session:
        response = anyio.run(run_case, session)
        artifact = session.exec(select(UserArtifact).where(UserArtifact.artifact_key == "existing-task")).one()
        usage = session.exec(select(UsageEvent).where(UsageEvent.event_type == PARSE_EVENT)).all()

    assert posted["url"] == "http://pdf2md.test/api/upload"
    assert posted["files"][0][1][0] == "annual.pdf"
    assert response.status_code == 409
    assert json.loads(response.body) == duplicate_payload
    assert artifact.user_id == 1
    assert artifact.artifact_type == "parse"
    assert artifact.title == "annual.pdf"
    assert artifact.path == "http://pdf2md.test/api/result/existing-task"
    assert artifact.source == "reused_parse"
    assert artifact.global_artifact_id == "existing-task"
    assert usage == []
