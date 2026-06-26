import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routers import source


class DummyRequest:
    def __init__(self, query_params=None):
        self.query_params = query_params or {}


def test_source_access_token_is_bound_to_task(monkeypatch):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "test-secret-key-with-enough-length")

    token = source.create_source_access_token("task-a", ttl_seconds=60)

    assert source._valid_source_access_token("task-a", token)
    assert not source._valid_source_access_token("task-b", token)


def test_append_source_token_strips_login_token():
    url = "https://example.test/api/source/task-a/page/1?format=json&access_token=jwt"

    signed_url = source._append_source_token(url, "signed")

    assert "source_token=signed" in signed_url
    assert "format=json" in signed_url
    assert "access_token=" not in signed_url


def test_resolve_source_open_path_adds_html_format():
    assert (
        source._resolve_source_open_path("pdf_page", "task-a", 3)
        == "/api/pdf_page/task-a/3?format=html"
    )
    assert (
        source._resolve_source_open_path("source_page", "task-a", 3)
        == "/api/source/task-a/page/3?format=html"
    )
    assert (
        source._resolve_source_open_path("source_table", "task-a", 9)
        == "/api/source/task-a/table/9?format=html"
    )


def test_source_url_keeps_format_when_adding_signed_token():
    url = source._source_url("/api/pdf_page/task-a/3?format=html", "signed")

    assert "format=html" in url
    assert "source_token=signed" in url
    assert "access_token=" not in url


def test_authorize_task_access_requires_token(monkeypatch):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "test-secret-key-with-enough-length")

    with pytest.raises(HTTPException) as exc:
        source._authorize_task_access(
            request=DummyRequest(),
            task_id="task-a",
            session=object(),
            credentials=None,
        )

    assert exc.value.status_code == 401


def test_authorize_task_access_rejects_wrong_owner(monkeypatch):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "test-secret-key-with-enough-length")
    monkeypatch.setattr(source, "_token_user", lambda token, session: SimpleNamespace(id=1, role="user"))
    monkeypatch.setattr(source, "_user_has_task_access", lambda session, user, task_id: False)

    with pytest.raises(HTTPException) as exc:
        source._authorize_task_access(
            request=DummyRequest({"access_token": "jwt"}),
            task_id="task-a",
            session=object(),
            credentials=None,
        )

    assert exc.value.status_code == 403


def test_authorize_task_access_mints_signed_token_for_owner(monkeypatch):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "test-secret-key-with-enough-length")
    monkeypatch.setattr(source, "_token_user", lambda token, session: SimpleNamespace(id=1, role="user"))
    monkeypatch.setattr(source, "_user_has_task_access", lambda session, user, task_id: True)

    token = source._authorize_task_access(
        request=DummyRequest({"access_token": "jwt"}),
        task_id="task-a",
        session=object(),
        credentials=None,
    )

    assert token != "jwt"
    assert source._valid_source_access_token("task-a", token)


def test_authorize_task_access_accepts_existing_signed_token(monkeypatch):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "test-secret-key-with-enough-length")
    token = source.create_source_access_token("task-a", ttl_seconds=60)

    returned = source._authorize_task_access(
        request=DummyRequest({"source_token": token}),
        task_id="task-a",
        session=object(),
        credentials=None,
    )

    assert returned == token


def test_user_has_task_access_accepts_parse_artifact(tmp_path):
    pytest.importorskip("sqlmodel")
    from sqlmodel import Session, SQLModel, create_engine
    from services.auth_service import User
    from services.usage_service import UserArtifact

    engine = create_engine(f"sqlite:///{tmp_path / 'source-access.db'}")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        user = User(username="alice", email="alice@example.test", full_name="Alice", hashed_password="x")
        session.add(user)
        session.commit()
        session.refresh(user)

        session.add(
            UserArtifact(
                user_id=int(user.id),
                artifact_type="parse",
                artifact_key="task-a",
                title="task-a.pdf",
                path="/api/pdf/result/task-a",
                source="test",
                global_artifact_id="task-a",
            )
        )
        session.commit()

        assert source._user_has_task_access(session, user, "task-a")
        assert not source._user_has_task_access(session, user, "task-b")
