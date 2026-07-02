from types import SimpleNamespace

import anyio
import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from database import get_async_session
from services.auth_dependencies import get_current_user
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
