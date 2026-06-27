from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import BackgroundTasks
from sqlmodel import SQLModel, Session, create_engine, select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

spec = importlib.util.spec_from_file_location("chat_router", BACKEND_ROOT / "routers" / "chat.py")
chat = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(chat)

from services.auth_service import User, UserRole
from services.usage_service import DOCUMENT_PARSE_EVENT, UsageEvent, UserArtifact


def make_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'chat-attachments.db'}")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def make_user(session: Session) -> User:
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
    session.commit()
    session.refresh(user)
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

    with make_session(tmp_path) as session:
        user = make_user(session)
        metadata = asyncio.run(
            chat._submit_pdf_attachment_to_mineru(
                pdf_path,
                "stored-report.pdf",
                parse_dir,
                BackgroundTasks(),
                current_user=user,
                session=session,
            )
        )

        artifact = session.exec(select(UserArtifact).where(UserArtifact.artifact_key == "doc-chat-1")).one()
        usage = session.exec(select(UsageEvent).where(UsageEvent.event_type == DOCUMENT_PARSE_EVENT)).one()

    assert submitted["url"] == "http://document-parser.test/api/tasks"
    assert submitted["data"]["data_id"] == "chat_attachment:stored-report.pdf"
    assert submitted["files"]["files"][0] == "stored-report.pdf"
    assert metadata["document_parser_task_id"] == "doc-chat-1"
    assert metadata["document_parser_page_url"] == "/documents?task=doc-chat-1"
    assert metadata["queue_policy"] == "document_parser_chat_attachment"
    assert artifact.source == "chat_attachment"
    assert artifact.path == "/documents?task=doc-chat-1"
    assert usage.count == 1
