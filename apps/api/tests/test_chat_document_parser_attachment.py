from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import anyio
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

spec = importlib.util.spec_from_file_location("chat_router", BACKEND_ROOT / "routers" / "chat.py")
chat = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(chat)

from services.auth_service import User, UserRole
from services.usage_service import DOCUMENT_PARSE_EVENT, UsageEvent, UserArtifact


async def _with_session(tmp_path, callback):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'chat-attachments.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with AsyncSession(engine) as session:
            await callback(session)
    finally:
        await engine.dispose()


async def make_user(session: AsyncSession) -> User:
    user = User(
        username="alice",
        email="alice@example.test",
        full_name="Alice",
        hashed_password="x",
        role=UserRole.ANALYST,
        is_active=True,
        approval_status="approved",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def test_pdf_chat_attachment_submits_to_document_parser(monkeypatch, tmp_path):
    submitted: dict[str, object] = {}

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, data=None, files=None, headers=None):
            submitted["url"] = url
            submitted["data"] = data
            submitted["files"] = files
            submitted["headers"] = headers
            return SimpleNamespace(
                is_success=True,
                status_code=200,
                content=b"{}",
                json=lambda: {"tasks": [{"task_id": "doc-chat-1", "filename": "report.pdf", "status": "queued"}]},
            )

    monkeypatch.setattr(chat.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(chat, "DOCUMENT_PARSER_API_BASE", "http://document-parser.test")

    pdf_path = tmp_path / "report.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nfake")
    parse_dir = tmp_path / "parse"

    async def run_case(session):
        user = await make_user(session)
        metadata = await chat._submit_pdf_attachment_to_mineru(
                pdf_path,
                "stored-report.pdf",
                parse_dir,
                BackgroundTasks(),
                current_user_id=int(user.id),
                current_user_role=str(user.role.value if hasattr(user.role, "value") else user.role),
                async_session=session,
            )

        artifact = (await session.exec(select(UserArtifact).where(UserArtifact.artifact_key == "doc-chat-1"))).one()
        usage = (await session.exec(select(UsageEvent).where(UsageEvent.event_type == DOCUMENT_PARSE_EVENT))).one()

        assert metadata["document_parser_task_id"] == "doc-chat-1"
        assert metadata["document_parser_page_url"] == "/documents?task=doc-chat-1"
        assert metadata["queue_policy"] == "document_parser_chat_attachment"
        assert artifact.source == "chat_attachment"
        assert artifact.path == "/documents?task=doc-chat-1"
        assert usage.count == 1

    anyio.run(_with_session, tmp_path, run_case)

    assert submitted["url"] == "http://document-parser.test/api/tasks"
    assert submitted["data"]["data_id"] == "chat_attachment:stored-report.pdf"
    assert submitted["files"]["files"][0] == "stored-report.pdf"


def test_pdf_chat_attachment_submit_failure_does_not_record_usage_or_artifact(monkeypatch, tmp_path):
    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, data=None, files=None, headers=None):
            return SimpleNamespace(
                is_success=False,
                status_code=500,
                content=b'{"error":"parser unavailable"}',
                text="parser unavailable",
                json=lambda: {"error": "parser unavailable"},
            )

    monkeypatch.setattr(chat.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(chat, "DOCUMENT_PARSER_API_BASE", "http://document-parser.test")

    pdf_path = tmp_path / "failed.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nfake")
    parse_dir = tmp_path / "parse-failed"
    background_tasks = BackgroundTasks()

    async def run_case(session):
        user = await make_user(session)
        metadata = await chat._submit_pdf_attachment_to_mineru(
                pdf_path,
                "stored-failed.pdf",
                parse_dir,
                background_tasks,
                current_user_id=int(user.id),
                current_user_role=str(user.role.value if hasattr(user.role, "value") else user.role),
                async_session=session,
            )

        artifacts = (await session.exec(select(UserArtifact))).all()
        usage = (await session.exec(select(UsageEvent))).all()

        assert metadata["document_parser_submit_status"] == "failed"
        assert metadata["document_parser_submit_http_status"] == 500
        assert metadata["document_parser_submit_error"] == "parser unavailable"
        assert artifacts == []
        assert usage == []

    anyio.run(_with_session, tmp_path, run_case)

    assert background_tasks.tasks == []
    assert (parse_dir / "metadata.json").exists()


def test_pdf_chat_attachment_without_task_id_does_not_record_usage_or_artifact(monkeypatch, tmp_path):
    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, data=None, files=None, headers=None):
            return SimpleNamespace(
                is_success=True,
                status_code=200,
                content=b'{"tasks":[]}',
                text='{"tasks":[]}',
                json=lambda: {"tasks": []},
            )

    monkeypatch.setattr(chat.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(chat, "DOCUMENT_PARSER_API_BASE", "http://document-parser.test")

    pdf_path = tmp_path / "no-task.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nfake")
    parse_dir = tmp_path / "parse-no-task"
    background_tasks = BackgroundTasks()

    async def run_case(session):
        user = await make_user(session)
        metadata = await chat._submit_pdf_attachment_to_mineru(
                pdf_path,
                "stored-no-task.pdf",
                parse_dir,
                background_tasks,
                current_user_id=int(user.id),
                current_user_role=str(user.role.value if hasattr(user.role, "value") else user.role),
                async_session=session,
            )

        artifacts = (await session.exec(select(UserArtifact))).all()
        usage = (await session.exec(select(UsageEvent))).all()

        assert metadata["document_parser_submit_status"] == "submitted_without_task_id"
        assert metadata["document_parser_status"] == "unknown"
        assert metadata["document_parser_task_id"] is None
        assert metadata["document_parser_page_url"] == ""
        assert artifacts == []
        assert usage == []

    anyio.run(_with_session, tmp_path, run_case)

    assert background_tasks.tasks == []
    assert (parse_dir / "metadata.json").exists()
