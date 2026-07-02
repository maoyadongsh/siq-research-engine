import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.ext.asyncio.session import AsyncSession

import main
from database import get_async_session, get_session
from services.auth_service import AuthService, User, UserRole


@pytest.fixture
def auth_smoke_client(monkeypatch, tmp_path):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "auth-smoke-secret-with-enough-length")
    db_path = tmp_path / "auth-smoke.db"
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    sync_engine = create_engine(f"sqlite:///{db_path}")
    state = {}
    original_overrides = main.app.dependency_overrides.copy()

    async def setup_user() -> None:
        async with async_engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with AsyncSession(async_engine) as session:
            user = User(
                username="smoke-user",
                email="smoke-user@example.test",
                hashed_password="x",
                full_name="Smoke User",
                role=UserRole.ANALYST,
                approval_status="approved",
                is_active=True,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            state["user_id"] = int(user.id)
            state["token"] = AuthService.create_access_token({"sub": str(user.id)})

    async def override_async_session():
        async with AsyncSession(async_engine) as session:
            yield session

    def override_sync_session():
        with Session(sync_engine) as session:
            yield session

    anyio.run(setup_user)
    main.app.dependency_overrides[get_async_session] = override_async_session
    main.app.dependency_overrides[get_session] = override_sync_session
    client = TestClient(main.app)
    try:
        yield client, state
    finally:
        client.close()
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(original_overrides)
        anyio.run(async_engine.dispose)
        sync_engine.dispose()


def test_bearer_token_resolves_real_user_on_main_protected_route(auth_smoke_client):
    client, state = auth_smoke_client

    response = client.get(
        "/api/workspace/summary",
        headers={"Authorization": f"Bearer {state['token']}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["id"] == state["user_id"]
    assert body["user"]["username"] == "smoke-user"
    assert body["user"]["email"] == "smoke-user@example.test"
    assert body["user"]["role"] == UserRole.ANALYST.value


def test_bearer_token_for_missing_user_is_rejected_on_main_protected_route(auth_smoke_client):
    client, state = auth_smoke_client
    missing_token = AuthService.create_access_token({"sub": str(state["user_id"] + 999_999)})

    response = client.get(
        "/api/workspace/summary",
        headers={"Authorization": f"Bearer {missing_token}"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "User not found"
