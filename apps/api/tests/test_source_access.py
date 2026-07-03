import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit

import anyio
import httpx
import pytest
from fastapi import HTTPException
from fastapi.responses import Response
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
from routers import source


class DummyRequest:
    def __init__(self, query_params=None):
        self.query_params = query_params or {}


@pytest.fixture
def source_route_client():
    original_overrides = main.app.dependency_overrides.copy()

    def fake_session():
        yield object()

    main.app.dependency_overrides[source.get_async_session] = fake_session
    client = TestClient(main.app)
    try:
        yield client
    finally:
        client.close()
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(original_overrides)


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

    token = source.create_source_access_token("task-a", ttl_seconds=60)
    expires_at = int(token.split(".", 1)[0])

    assert token.endswith(source._source_token_signature("task-a", expires_at, secret=source_secret))
    assert not token.endswith(source._source_token_signature("task-a", expires_at, secret=auth_secret))

    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "auth-secret-with-enough-length-rotated")

    assert source._valid_source_access_token("task-a", token)


def test_source_access_token_independent_secret_does_not_require_auth_secret(monkeypatch):
    monkeypatch.setenv("SIQ_SOURCE_TOKEN_SECRET", "source-token-secret-with-enough-length-no-auth")
    monkeypatch.delenv("SIQ_AUTH_SECRET_KEY", raising=False)

    token = source.create_source_access_token("task-a", ttl_seconds=60)

    assert source._valid_source_access_token("task-a", token)


def test_source_access_token_accepts_legacy_auth_secret_when_source_secret_exists(monkeypatch):
    legacy_auth_secret = "legacy-auth-secret-with-enough-length"
    monkeypatch.setenv("SIQ_SOURCE_TOKEN_SECRET", "source-token-secret-with-enough-length-2")
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", legacy_auth_secret)
    monkeypatch.setenv("SIQ_SOURCE_ACCEPT_LEGACY_AUTH_SECRET", "1")
    expires_at = 4_102_444_800
    legacy_signature = source._source_token_signature("task-a", expires_at, secret=legacy_auth_secret)
    legacy_token = f"{expires_at}.{legacy_signature}"

    assert source._valid_source_access_token("task-a", legacy_token)


def test_source_access_token_rejects_legacy_auth_secret_by_default(monkeypatch):
    legacy_auth_secret = "legacy-auth-secret-with-enough-length"
    monkeypatch.setenv("SIQ_SOURCE_TOKEN_SECRET", "source-token-secret-with-enough-length-2")
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", legacy_auth_secret)
    monkeypatch.delenv("SIQ_SOURCE_ACCEPT_LEGACY_AUTH_SECRET", raising=False)
    expires_at = 4_102_444_800
    legacy_signature = source._source_token_signature("task-a", expires_at, secret=legacy_auth_secret)
    legacy_token = f"{expires_at}.{legacy_signature}"

    assert not source._valid_source_access_token("task-a", legacy_token)


def test_source_access_token_rejects_legacy_auth_secret_when_disabled(monkeypatch):
    legacy_auth_secret = "legacy-auth-secret-with-enough-length"
    monkeypatch.setenv("SIQ_SOURCE_TOKEN_SECRET", "source-token-secret-with-enough-length-2")
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", legacy_auth_secret)
    monkeypatch.setenv("SIQ_SOURCE_ACCEPT_LEGACY_AUTH_SECRET", "0")
    expires_at = 4_102_444_800
    legacy_signature = source._source_token_signature("task-a", expires_at, secret=legacy_auth_secret)
    legacy_token = f"{expires_at}.{legacy_signature}"

    assert not source._valid_source_access_token("task-a", legacy_token)


def test_source_access_token_rejects_wrong_signature(monkeypatch):
    wrong_secret = "wrong-source-token-secret-with-enough-length"
    monkeypatch.setenv("SIQ_SOURCE_TOKEN_SECRET", "source-token-secret-with-enough-length-2")
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "legacy-auth-secret-with-enough-length")
    expires_at = 4_102_444_800
    wrong_signature = source._source_token_signature("task-a", expires_at, secret=wrong_secret)
    wrong_token = f"{expires_at}.{wrong_signature}"

    assert not source._valid_source_access_token("task-a", wrong_token)


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
    url = (
        "https://example.test/api/source/task-a/page/1"
        "?format=json&access_token=jwt&Access_Token=jwt-upper"
        "&source_token=old-signed&SOURCE_TOKEN=old-signed-upper&keep=1"
    )

    signed_url = source._append_source_token(url, "signed")
    query = parse_qsl(urlsplit(signed_url).query, keep_blank_values=True)
    query_keys = {key.lower() for key, _value in query}

    assert query == [("format", "json"), ("keep", "1"), ("source_token", "signed")]
    assert "access_token" not in query_keys
    assert [value for key, value in query if key == "source_token"] == ["signed"]


def test_source_access_route_requires_token_without_upstream_proxy(
    source_route_client,
    monkeypatch,
):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("unauthorized source access must not call PDF2MD upstream")

    monkeypatch.setattr(source, "_request_pdf2md", fail_if_called)
    monkeypatch.setattr(source, "_proxy_pdf2md", fail_if_called)

    response = source_route_client.get("/api/source_access/source_page/task-a/3")

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing source access token"


def test_source_access_route_rejects_non_owner_without_upstream_proxy(
    source_route_client,
    monkeypatch,
):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("forbidden source access must not call PDF2MD upstream")

    monkeypatch.setattr(source, "_request_pdf2md", fail_if_called)
    monkeypatch.setattr(source, "_proxy_pdf2md", fail_if_called)
    async def fake_token_user(token, async_session):
        return SimpleNamespace(id=9, role="user")

    async def fake_user_has_task_access(async_session, user, task_id):
        return False

    monkeypatch.setattr(source, "_token_user_async", fake_token_user)
    monkeypatch.setattr(source, "_user_has_task_access_async", fake_user_has_task_access)

    response = source_route_client.get("/api/source_access/source_page/task-a/3?access_token=jwt")

    assert response.status_code == 403
    assert response.json()["detail"] == "PDF task does not belong to current user"


def test_source_access_route_mints_clean_signed_url_for_owner(
    source_route_client,
    monkeypatch,
):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "source-route-secret-with-enough-length")
    monkeypatch.delenv("SIQ_SOURCE_TOKEN_SECRET", raising=False)
    async def fake_token_user(token, async_session):
        return SimpleNamespace(id=7, role="user")

    async def fake_user_has_task_access(async_session, user, task_id):
        return True

    monkeypatch.setattr(source, "_token_user_async", fake_token_user)
    monkeypatch.setattr(source, "_user_has_task_access_async", fake_user_has_task_access)

    response = source_route_client.get(
        "/api/source_access/source_page/task-a/3"
        "?access_token=jwt&Access_Token=jwt-upper&SOURCE_TOKEN=old-source"
    )

    assert response.status_code == 200
    body = response.json()
    parsed = urlsplit(body["url"])
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query_keys = {key.lower() for key, _value in query}
    signed_token = dict(query)["source_token"]

    assert parsed.path == "/api/source/task-a/page/3"
    assert query == [("format", "html"), ("source_token", signed_token)]
    assert "access_token" not in query_keys
    assert source._valid_source_access_token("task-a", signed_token)
    assert body["expires_in"] == source.SOURCE_ACCESS_TOKEN_TTL_SECONDS


def test_source_access_route_reuses_existing_signed_token_without_user_lookup(
    source_route_client,
    monkeypatch,
):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "source-route-secret-with-enough-length")
    monkeypatch.delenv("SIQ_SOURCE_TOKEN_SECRET", raising=False)
    signed_token = source.create_source_access_token("task-a", ttl_seconds=60)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("valid source_token must not fall back to user token lookup")

    monkeypatch.setattr(source, "_token_user_async", fail_if_called)
    monkeypatch.setattr(source, "_user_has_task_access_async", fail_if_called)

    response = source_route_client.get(
        f"/api/source_access/source_table/task-a/9?source_token={signed_token}&Access_Token=jwt"
    )

    assert response.status_code == 200
    parsed = urlsplit(response.json()["url"])
    query = parse_qsl(parsed.query, keep_blank_values=True)

    assert parsed.path == "/api/source/task-a/table/9"
    assert query == [("format", "html"), ("source_token", signed_token)]


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


def test_source_table_route_uses_async_authorization_before_proxy(monkeypatch):
    calls = {}

    class QueryParams(dict):
        def multi_items(self):
            return list(self.items())

    async def fake_authorize_task_access_async(*, request, task_id, async_session, credentials):
        calls["auth"] = (request, task_id, async_session, credentials)
        return "signed-source-token"

    async def fake_proxy_pdf2md(request, upstream_path, *, method=None, json_body=None):
        calls["proxy"] = (request, upstream_path, method, json_body)
        return Response(content=b'{"ok": true}', status_code=200, media_type="application/json")

    async def run_case():
        monkeypatch.setattr(source, "_authorize_task_access_async", fake_authorize_task_access_async)
        monkeypatch.setattr(source, "_proxy_pdf2md", fake_proxy_pdf2md)
        request = SimpleNamespace(
            method="GET",
            query_params=QueryParams(),
            headers={"accept": "application/json"},
        )

        response = await source.get_source_table(
            request,
            "task-a",
            9,
            credentials=None,
            async_session=object(),
        )

        assert response.status_code == 200

    anyio.run(run_case)

    assert calls["auth"][1] == "task-a"
    assert calls["proxy"][1] == "/api/source/task-a/table/9"
    assert calls["proxy"][2] is None
    assert calls["proxy"][3] is None


def test_source_table_correction_uses_async_access_check(monkeypatch):
    calls = {}

    class QueryParams(dict):
        def multi_items(self):
            return list(self.items())

    class CorrectionRequest:
        method = "POST"
        query_params = QueryParams()
        headers = {"content-type": "application/json"}

        async def json(self):
            return {"cells": [{"row": 1, "column": 2, "value": "ok"}]}

    async def fake_user_has_task_access_async(async_session, user, task_id):
        calls["access"] = (async_session, user, task_id)
        return True

    async def fake_proxy_pdf2md(request, upstream_path, *, method=None, json_body=None):
        calls["proxy"] = (request, upstream_path, method, json_body)
        return Response(content=b'{"saved": true}', status_code=200, media_type="application/json")

    async def run_case():
        monkeypatch.setattr(source, "_user_has_task_access_async", fake_user_has_task_access_async)
        monkeypatch.setattr(source, "_proxy_pdf2md", fake_proxy_pdf2md)

        response = await source.submit_source_table_correction(
            CorrectionRequest(),
            "task-a",
            9,
            current_user=SimpleNamespace(id=1, role="user"),
            async_session=object(),
        )

        assert response.status_code == 200

    anyio.run(run_case)

    assert calls["access"][2] == "task-a"
    assert calls["proxy"][1] == "/api/source/task-a/table/9/correction"
    assert calls["proxy"][2] == "POST"
    assert calls["proxy"][3] == {"cells": [{"row": 1, "column": 2, "value": "ok"}]}


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
