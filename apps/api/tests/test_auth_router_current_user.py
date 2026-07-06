import anyio
import pytest
from fastapi import Depends, FastAPI, Response
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from routers import auth
from services.auth_service import AuthService, User, UserRole


async def _add_user(session: AsyncSession, username: str) -> User:
    user = User(
        username=username,
        email=f"{username}@example.test",
        hashed_password="x",
        full_name=username.title(),
        role=UserRole.ANALYST,
        approval_status="approved",
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest.fixture
def auth_router_client(monkeypatch, tmp_path):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "auth-router-secret-with-enough-length")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'auth-router.db'}")
    state = {}

    async def setup_user() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with AsyncSession(engine) as session:
            user = await _add_user(session, "router-user")
            state["user_id"] = int(user.id)
            state["token"] = AuthService.create_access_token({"sub": user.username})

    async def override_async_session():
        async with AsyncSession(engine) as session:
            yield session

    app = FastAPI()

    @app.get("/me")
    async def me(current_user: User = Depends(auth.get_current_user)):
        return {"id": current_user.id, "username": current_user.username}

    app.dependency_overrides[auth.get_async_session] = override_async_session
    anyio.run(setup_user)
    client = TestClient(app)
    try:
        yield client, state
    finally:
        client.close()
        anyio.run(engine.dispose)


def test_auth_router_get_current_user_uses_async_session(auth_router_client):
    client, state = auth_router_client

    response = client.get("/me", headers={"Authorization": f"Bearer {state['token']}"})

    assert response.status_code == 200
    assert response.json() == {"id": state["user_id"], "username": "router-user"}


def test_auth_router_get_current_user_accepts_cookie_token(auth_router_client):
    client, state = auth_router_client

    response = client.get("/me", cookies={AuthService.ACCESS_COOKIE_NAME: state["token"]})

    assert response.status_code == 200
    assert response.json() == {"id": state["user_id"], "username": "router-user"}


def test_auth_router_get_current_user_keeps_missing_user_error(auth_router_client):
    client, _state = auth_router_client
    token = AuthService.create_access_token({"sub": "missing-router-user"})

    response = client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json()["detail"] == "用户不存在"


def test_auth_router_cookie_mode_sets_and_clears_httponly_cookie(monkeypatch):
    monkeypatch.setenv("SIQ_AUTH_COOKIE_MODE", "1")
    monkeypatch.setenv("SIQ_AUTH_COOKIE_SECURE", "0")

    response = Response()
    auth._set_access_cookie(response, "jwt-token")

    cookie_header = response.headers["set-cookie"]
    assert f"{AuthService.ACCESS_COOKIE_NAME}=jwt-token" in cookie_header
    assert "HttpOnly" in cookie_header
    assert "SameSite=lax" in cookie_header

    clear_response = Response()
    auth._clear_access_cookie(clear_response)

    clear_header = clear_response.headers["set-cookie"]
    assert f"{AuthService.ACCESS_COOKIE_NAME}=" in clear_header
    assert "Max-Age=0" in clear_header
