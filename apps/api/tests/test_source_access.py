import sys
from pathlib import Path
from types import SimpleNamespace

import anyio
import httpx
import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routers import source


class DummyRequest:
    def __init__(self, query_params=None):
        self.query_params = query_params or {}


def test_source_access_token_is_bound_to_task(monkeypatch):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "test-secret-key-with-enough-length")
    monkeypatch.delenv("SIQ_SOURCE_TOKEN_SECRET", raising=False)

    token = source.create_source_access_token("task-a", ttl_seconds=60)

    assert source._valid_source_access_token("task-a", token)
    assert not source._valid_source_access_token("task-b", token)


def test_source_access_token_uses_independent_secret(monkeypatch):
    source_secret = "source-token-secret-with-enough-length-1"
    auth_secret = "auth-secret-with-enough-length-original"
    monkeypatch.setenv("SIQ_SOURCE_TOKEN_SECRET", source_secret)
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", auth_secret)
    monkeypatch.delenv("SIQ_SOURCE_ACCEPT_LEGACY_AUTH_SECRET", raising=False)

    token = source.create_source_access_token("task-a", ttl_seconds=60)
    expires_at = int(token.split(".", 1)[0])

    assert token.endswith(source._source_token_signature("task-a", expires_at, secret=source_secret))
    assert not token.endswith(source._source_token_signature("task-a", expires_at, secret=auth_secret))

    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "auth-secret-with-enough-length-rotated")

    assert source._valid_source_access_token("task-a", token)


def test_source_access_token_independent_secret_does_not_require_auth_secret(monkeypatch):
    monkeypatch.setenv("SIQ_SOURCE_TOKEN_SECRET", "source-token-secret-with-enough-length-no-auth")
    monkeypatch.delenv("SIQ_AUTH_SECRET_KEY", raising=False)
    monkeypatch.delenv("SIQ_SOURCE_ACCEPT_LEGACY_AUTH_SECRET", raising=False)

    token = source.create_source_access_token("task-a", ttl_seconds=60)

    assert source._valid_source_access_token("task-a", token)


def test_source_access_token_rejects_legacy_auth_secret_by_default(monkeypatch):
    legacy_auth_secret = "legacy-auth-secret-with-enough-length"
    monkeypatch.setenv("SIQ_SOURCE_TOKEN_SECRET", "source-token-secret-with-enough-length-2")
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", legacy_auth_secret)
    monkeypatch.delenv("SIQ_SOURCE_ACCEPT_LEGACY_AUTH_SECRET", raising=False)
    expires_at = 4_102_444_800
    legacy_signature = source._source_token_signature("task-a", expires_at, secret=legacy_auth_secret)
    legacy_token = f"{expires_at}.{legacy_signature}"

    assert not source._valid_source_access_token("task-a", legacy_token)


def test_source_access_token_accepts_legacy_auth_secret_when_enabled(monkeypatch):
    legacy_auth_secret = "legacy-auth-secret-with-enough-length"
    monkeypatch.setenv("SIQ_SOURCE_TOKEN_SECRET", "source-token-secret-with-enough-length-2")
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", legacy_auth_secret)
    monkeypatch.setenv("SIQ_SOURCE_ACCEPT_LEGACY_AUTH_SECRET", "1")
    expires_at = 4_102_444_800
    legacy_signature = source._source_token_signature("task-a", expires_at, secret=legacy_auth_secret)
    legacy_token = f"{expires_at}.{legacy_signature}"

    assert source._valid_source_access_token("task-a", legacy_token)


def test_source_access_token_rejects_expired_token(monkeypatch):
    monkeypatch.setenv("SIQ_SOURCE_TOKEN_SECRET", "source-token-secret-with-enough-length-3")
    expires_at = 1
    signature = source._source_token_signature(
        "task-a",
        expires_at,
        secret="source-token-secret-with-enough-length-3",
    )

    assert not source._valid_source_access_token("task-a", f"{expires_at}.{signature}")


def test_source_access_token_rejects_short_configured_secret(monkeypatch):
    monkeypatch.setenv("SIQ_SOURCE_TOKEN_SECRET", "too-short")

    with pytest.raises(RuntimeError, match="SIQ_SOURCE_TOKEN_SECRET"):
        source.create_source_access_token("task-a", ttl_seconds=60)


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


def test_request_pdf2md_does_not_forward_source_tokens(monkeypatch):
    calls = {}

    class QueryParams:
        def multi_items(self):
            return [
                ("format", "json"),
                ("access_token", "login-token"),
                ("source_token", "signed-source-token"),
                ("Access_Token", "login-token-upper"),
                ("SOURCE_TOKEN", "signed-source-token-upper"),
                ("keep", "1"),
            ]

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            calls["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def request(self, method, url, **kwargs):
            calls["method"] = method
            calls["url"] = url
            calls["kwargs"] = kwargs
            return httpx.Response(200, json={"ok": True})

    async def run_case():
        monkeypatch.setattr(source.httpx, "AsyncClient", FakeAsyncClient)
        monkeypatch.setattr(source, "PDF2MD_API_BASE", "https://pdf2md.internal")
        monkeypatch.setattr(source, "PDF2MD_ACCESS_TOKEN", "")
        request = SimpleNamespace(
            method="GET",
            query_params=QueryParams(),
            headers={"authorization": "Bearer login-token"},
        )

        response = await source._request_pdf2md(request, "/api/source/task-a/page/1")

        assert response.status_code == 200

    anyio.run(run_case)

    assert calls["method"] == "GET"
    assert calls["url"] == "https://pdf2md.internal/api/source/task-a/page/1"
    assert calls["kwargs"]["params"] == [("format", "json"), ("keep", "1")]
    assert "authorization" not in {key.lower() for key in calls["kwargs"]["headers"]}


def test_authorize_task_access_requires_token(monkeypatch):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "test-secret-key-with-enough-length")
    monkeypatch.delenv("SIQ_SOURCE_TOKEN_SECRET", raising=False)

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
    monkeypatch.delenv("SIQ_SOURCE_TOKEN_SECRET", raising=False)
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
    monkeypatch.delenv("SIQ_SOURCE_TOKEN_SECRET", raising=False)
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
    monkeypatch.delenv("SIQ_SOURCE_TOKEN_SECRET", raising=False)
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
