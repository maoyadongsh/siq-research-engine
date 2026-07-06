from types import SimpleNamespace

import anyio
import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from database import get_async_session
from routers import document_parser, settings, system, workspace
from services.auth_dependencies import get_current_user, require_permission
from services.auth_service import AuthService, User, UserRole


async def _with_auth_session(tmp_path, callback):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'auth-deps.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with AsyncSession(engine) as session:
            return await callback(session)
    finally:
        await engine.dispose()


def _credentials(token: str):
    return SimpleNamespace(credentials=token)


async def _add_user(
    session: AsyncSession,
    *,
    username: str,
    role: UserRole = UserRole.ANALYST,
    approval_status: str = "approved",
    approval_note: str | None = None,
    is_active: bool = True,
) -> User:
    user = User(
        username=username,
        email=f"{username}@example.test",
        hashed_password="x",
        full_name=username.title(),
        role=role,
        approval_status=approval_status,
        approval_note=approval_note,
        is_active=is_active,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def test_get_current_user_accepts_numeric_subject(monkeypatch, tmp_path):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "auth-deps-secret-with-enough-length")

    async def run_case(session):
        user = await _add_user(session, username="alice")
        token = AuthService.create_access_token({"sub": str(user.id)})

        resolved = await get_current_user(_credentials(token), session)

        assert resolved.id == user.id
        assert resolved.username == "alice"

    anyio.run(_with_auth_session, tmp_path, run_case)


def test_get_current_user_accepts_username_subject(monkeypatch, tmp_path):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "auth-deps-secret-with-enough-length")

    async def run_case(session):
        user = await _add_user(session, username="bob")
        token = AuthService.create_access_token({"sub": "bob"})

        resolved = await get_current_user(_credentials(token), session)

        assert resolved.id == user.id
        assert resolved.username == "bob"

    anyio.run(_with_auth_session, tmp_path, run_case)


def test_get_current_user_accepts_cookie_token_without_authorization(monkeypatch, tmp_path):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "auth-deps-secret-with-enough-length")

    async def run_case(session):
        user = await _add_user(session, username="cookie-user")
        token = AuthService.create_access_token({"sub": str(user.id)})

        resolved = await get_current_user(None, session, token)

        assert resolved.id == user.id
        assert resolved.username == "cookie-user"

    anyio.run(_with_auth_session, tmp_path, run_case)


def test_get_current_user_fastapi_dependency_uses_async_session(monkeypatch, tmp_path):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "auth-deps-secret-with-enough-length")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'auth-route.db'}")

    async def setup_user() -> int:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with AsyncSession(engine) as session:
            user = await _add_user(session, username="route-user")
            return int(user.id)

    async def override_async_session():
        async with AsyncSession(engine) as session:
            yield session

    app = FastAPI()

    @app.get("/protected")
    async def protected(current_user: User = Depends(get_current_user)):
        return {"id": current_user.id, "username": current_user.username}

    app.dependency_overrides[get_async_session] = override_async_session

    try:
        user_id = anyio.run(setup_user)
        token = AuthService.create_access_token({"sub": str(user_id)})

        with TestClient(app) as client:
            response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        assert response.json() == {"id": user_id, "username": "route-user"}
    finally:
        anyio.run(engine.dispose)


def test_get_current_user_fastapi_dependency_accepts_cookie_token(monkeypatch, tmp_path):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "auth-deps-secret-with-enough-length")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'auth-route-cookie.db'}")

    async def setup_user() -> tuple[int, str]:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with AsyncSession(engine) as session:
            user = await _add_user(session, username="route-cookie-user")
            token = AuthService.create_access_token({"sub": str(user.id)})
            return int(user.id), token

    async def override_async_session():
        async with AsyncSession(engine) as session:
            yield session

    app = FastAPI()

    @app.get("/protected")
    async def protected(current_user: User = Depends(get_current_user)):
        return {"id": current_user.id, "username": current_user.username}

    app.dependency_overrides[get_async_session] = override_async_session

    try:
        user_id, token = anyio.run(setup_user)

        with TestClient(app) as client:
            response = client.get("/protected", cookies={AuthService.ACCESS_COOKIE_NAME: token})

        assert response.status_code == 200
        assert response.json() == {"id": user_id, "username": "route-cookie-user"}
    finally:
        anyio.run(engine.dispose)


def test_get_current_user_rejects_invalid_token(monkeypatch, tmp_path):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "auth-deps-secret-with-enough-length")

    async def run_case(session):
        with pytest.raises(HTTPException) as exc:
            await get_current_user(_credentials("not-a-jwt"), session)

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid or expired token"

    anyio.run(_with_auth_session, tmp_path, run_case)


def test_get_current_user_rejects_missing_subject(monkeypatch, tmp_path):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "auth-deps-secret-with-enough-length")

    async def run_case(session):
        token = AuthService.create_access_token({"role": "analyst"})

        with pytest.raises(HTTPException) as exc:
            await get_current_user(_credentials(token), session)

        assert exc.value.status_code == 401
        assert "missing subject" in str(exc.value.detail)

    anyio.run(_with_auth_session, tmp_path, run_case)


def test_get_current_user_rejects_missing_user(monkeypatch, tmp_path):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "auth-deps-secret-with-enough-length")

    async def run_case(session):
        token = AuthService.create_access_token({"sub": "missing-user"})

        with pytest.raises(HTTPException) as exc:
            await get_current_user(_credentials(token), session)

        assert exc.value.status_code == 401
        assert exc.value.detail == "User not found"

    anyio.run(_with_auth_session, tmp_path, run_case)


def test_get_current_user_rejects_pending_user(monkeypatch, tmp_path):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "auth-deps-secret-with-enough-length")

    async def run_case(session):
        await _add_user(session, username="pending", approval_status="pending")
        token = AuthService.create_access_token({"sub": "pending"})

        with pytest.raises(HTTPException) as exc:
            await get_current_user(_credentials(token), session)

        assert exc.value.status_code == 403
        assert "pending administrator approval" in str(exc.value.detail)

    anyio.run(_with_auth_session, tmp_path, run_case)


def test_get_current_user_rejects_rejected_user_with_note(monkeypatch, tmp_path):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "auth-deps-secret-with-enough-length")

    async def run_case(session):
        await _add_user(
            session,
            username="rejected",
            approval_status="rejected",
            approval_note="Need manager approval",
        )
        token = AuthService.create_access_token({"sub": "rejected"})

        with pytest.raises(HTTPException) as exc:
            await get_current_user(_credentials(token), session)

        assert exc.value.status_code == 403
        assert exc.value.detail == "Need manager approval"

    anyio.run(_with_auth_session, tmp_path, run_case)


def test_get_current_user_rejects_rejected_user_without_note(monkeypatch, tmp_path):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "auth-deps-secret-with-enough-length")

    async def run_case(session):
        await _add_user(session, username="rejected-default", approval_status="rejected")
        token = AuthService.create_access_token({"sub": "rejected-default"})

        with pytest.raises(HTTPException) as exc:
            await get_current_user(_credentials(token), session)

        assert exc.value.status_code == 403
        assert exc.value.detail == "User account request was rejected"

    anyio.run(_with_auth_session, tmp_path, run_case)


def test_get_current_user_rejects_disabled_user(monkeypatch, tmp_path):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "auth-deps-secret-with-enough-length")

    async def run_case(session):
        await _add_user(session, username="disabled", is_active=False)
        token = AuthService.create_access_token({"sub": "disabled"})

        with pytest.raises(HTTPException) as exc:
            await get_current_user(_credentials(token), session)

        assert exc.value.status_code == 403
        assert exc.value.detail == "User account is disabled"

    anyio.run(_with_auth_session, tmp_path, run_case)


@pytest.fixture
def csrf_route_client(monkeypatch, tmp_path):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "csrf-route-secret-with-enough-length")
    monkeypatch.setenv("SIQ_AUTH_COOKIE_MODE", "1")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'csrf-route.db'}")
    state = {}

    async def setup_user() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with AsyncSession(engine) as session:
            user = await _add_user(session, username="csrf-user")
            state["user_id"] = int(user.id)
            state["token"] = AuthService.create_access_token({"sub": str(user.id)})

    async def override_async_session():
        async with AsyncSession(engine) as session:
            yield session

    app = FastAPI()

    @app.post("/protected")
    async def protected(current_user: User = Depends(get_current_user)):
        return {"id": current_user.id, "username": current_user.username}

    app.dependency_overrides[get_async_session] = override_async_session

    anyio.run(setup_user)
    client = TestClient(app)
    try:
        yield client, state
    finally:
        client.close()
        anyio.run(engine.dispose)


def test_cookie_mode_post_without_csrf_header_is_rejected(csrf_route_client):
    client, state = csrf_route_client

    response = client.post(
        "/protected",
        headers={"Origin": "http://testserver"},
        cookies={AuthService.ACCESS_COOKIE_NAME: state["token"]},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF token missing or invalid"


def test_cookie_mode_post_with_matching_csrf_header_is_allowed(csrf_route_client):
    client, state = csrf_route_client
    csrf_token = "csrf-token"

    response = client.post(
        "/protected",
        headers={"Origin": "http://testserver", "X-CSRF-Token": csrf_token},
        cookies={
            AuthService.ACCESS_COOKIE_NAME: state["token"],
            AuthService.CSRF_COOKIE_NAME: csrf_token,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"id": state["user_id"], "username": "csrf-user"}


def test_bearer_post_does_not_require_csrf_header(csrf_route_client):
    client, state = csrf_route_client

    response = client.post(
        "/protected",
        headers={"Authorization": f"Bearer {state['token']}"},
        cookies={AuthService.ACCESS_COOKIE_NAME: state["token"]},
    )

    assert response.status_code == 200
    assert response.json() == {"id": state["user_id"], "username": "csrf-user"}


def test_viewer_cannot_call_document_pdf_or_workspace_write_routes():
    app = FastAPI()
    viewer = SimpleNamespace(id=1, role=UserRole.VIEWER, is_active=True)

    app.include_router(workspace.router, prefix="/api")
    app.include_router(document_parser.router, prefix="/api")
    app.include_router(workspace.pdf_router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: viewer

    with TestClient(app) as client:
        project_response = client.post("/api/workspace/projects", json={"name": "Viewer project"})
        link_response = client.post("/api/workspace/downloads/link", json={"relativePath": "blocked.pdf"})
        document_response = client.post("/api/documents/tasks", json={"source_type": "url"})
        upload_response = client.post(
            "/api/pdf/upload",
            files={"files": ("blocked.pdf", b"%PDF-1.4\nblocked", "application/pdf")},
        )

    assert project_response.status_code == 403
    assert link_response.status_code == 403
    assert document_response.status_code == 403
    assert upload_response.status_code == 403


def test_analyst_keeps_parse_create_permission():
    user = User(
        username="analyst",
        email="analyst@example.test",
        hashed_password="x",
        full_name="Analyst",
        role=UserRole.ANALYST,
        is_active=True,
    )
    checker = require_permission("report.create")

    resolved = anyio.run(checker, user)

    assert resolved is user


def test_settings_and_system_routes_share_service_auth_dependency_override(monkeypatch):
    app = FastAPI()
    admin = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN, is_active=True)

    async def fake_system_status():
        return {"status": "ok"}

    monkeypatch.setattr(settings, "load_llm_settings", lambda include_secrets=False: {"providers": {}})
    monkeypatch.setattr(system, "collect_system_status", fake_system_status)
    app.include_router(settings.router)
    app.include_router(system.router)
    app.dependency_overrides[get_current_user] = lambda: admin

    with TestClient(app) as client:
        settings_response = client.get("/settings/llm")
        system_response = client.get("/system/status")

    assert settings_response.status_code == 200
    assert settings_response.json() == {"providers": {}}
    assert system_response.status_code == 200
    assert system_response.json() == {"status": "ok"}
