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
from services.usage_service import UserArtifact, WorkspaceProject


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
