from __future__ import annotations

import anyio
import pytest
from database import get_async_session, get_session
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers import auth, source
from services.auth_service import AuthService, User, UserRole, UserUpdate
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.ext.asyncio.session import AsyncSession


@pytest.fixture
def revocation_client(monkeypatch, tmp_path):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "auth-revocation-secret-with-enough-length")
    monkeypatch.setenv("SIQ_AUTH_COOKIE_MODE", "0")
    db_path = tmp_path / "auth-revocation.db"
    sync_engine = create_engine(f"sqlite:///{db_path}")
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    SQLModel.metadata.create_all(sync_engine)
    with Session(sync_engine) as session:
        session.add_all(
            [
                User(
                    username="alice",
                    email="alice@example.test",
                    hashed_password=AuthService.hash_password("correct-password"),
                    full_name="Alice",
                    role=UserRole.ANALYST,
                    approval_status="approved",
                    is_active=True,
                ),
                User(
                    username="admin",
                    email="admin@example.test",
                    hashed_password=AuthService.hash_password("admin-password"),
                    full_name="Admin",
                    role=UserRole.SUPER_ADMIN,
                    approval_status="approved",
                    is_active=True,
                ),
            ]
        )
        session.commit()

    def override_sync_session():
        with Session(sync_engine) as session:
            yield session

    async def override_async_session():
        async with AsyncSession(async_engine) as session:
            yield session

    app = FastAPI()
    app.include_router(auth.router, prefix="/api/auth")
    app.dependency_overrides[get_session] = override_sync_session
    app.dependency_overrides[get_async_session] = override_async_session
    app.state.auth_test_engine = sync_engine
    app.state.auth_test_async_engine = async_engine
    with TestClient(app) as client:
        yield client

    anyio.run(async_engine.dispose)
    sync_engine.dispose()


def _login(
    client: TestClient,
    password: str = "correct-password",
    username: str = "alice",
) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_login_issues_token_bound_to_user_version(revocation_client):
    token = _login(revocation_client)

    payload = AuthService.decode_token(token)

    assert payload is not None
    assert payload["ver"] == 0


def test_logout_revokes_previously_issued_access_token(revocation_client):
    token = _login(revocation_client)

    logout = revocation_client.post(
        "/api/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    stale_request = revocation_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert logout.status_code == 200
    assert stale_request.status_code == 401
    with Session(revocation_client.app.state.auth_test_engine) as session:
        user = session.get(User, 1)
        assert user.token_version == 1


def test_source_router_sync_lookup_rejects_revoked_access_token(revocation_client):
    token = _login(revocation_client)
    with Session(revocation_client.app.state.auth_test_engine) as session:
        user = session.get(User, 1)
        assert source._token_user(token, session).id == user.id
        AuthService.bump_token_version(user)
        session.add(user)
        session.commit()
        assert source._token_user(token, session) is None


def test_source_router_async_lookup_rejects_revoked_access_token(revocation_client):
    token = _login(revocation_client)

    async def run_case():
        async with AsyncSession(revocation_client.app.state.auth_test_async_engine) as session:
            resolved = await source._token_user_async(token, session)
            assert resolved is not None

        with Session(revocation_client.app.state.auth_test_engine) as session:
            user = session.get(User, 1)
            AuthService.bump_token_version(user)
            session.add(user)
            session.commit()

        async with AsyncSession(revocation_client.app.state.auth_test_async_engine) as session:
            assert await source._token_user_async(token, session) is None

    anyio.run(run_case)


def test_persisted_token_version_bump_composes_without_lost_state(revocation_client):
    with Session(revocation_client.app.state.auth_test_engine) as session:
        user = session.get(User, 1)
        assert AuthService.bump_persisted_token_version(session, user) == 1
        assert AuthService.bump_persisted_token_version(session, user) == 2
        session.commit()

    with Session(revocation_client.app.state.auth_test_engine) as session:
        assert session.get(User, 1).token_version == 2


def test_admin_role_change_immediately_revokes_target_token(revocation_client):
    alice_token = _login(revocation_client)
    admin_token = _login(revocation_client, "admin-password", "admin")

    updated = revocation_client.patch(
        "/api/auth/users/1",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"role": UserRole.REVIEWER.value},
    )
    stale_request = revocation_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {alice_token}"},
    )

    assert updated.status_code == 200, updated.text
    assert stale_request.status_code == 401


def test_password_change_revokes_token_and_requires_new_password(revocation_client):
    token = _login(revocation_client)

    changed = revocation_client.post(
        "/api/auth/password",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "current_password": "correct-password",
            "new_password": "new-correct-password",
        },
    )
    stale_request = revocation_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    old_login = revocation_client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "correct-password"},
    )

    assert changed.status_code == 200
    assert stale_request.status_code == 401
    assert old_login.status_code == 401
    new_token = _login(revocation_client, "new-correct-password")
    assert AuthService.decode_token(new_token)["ver"] == 1


def test_admin_security_changes_bump_token_version_once():
    admin = User(
        id=1,
        username="admin",
        email="admin@example.test",
        hashed_password="x",
        full_name="Admin",
        role=UserRole.SUPER_ADMIN,
    )
    target = User(
        id=2,
        username="analyst",
        email="analyst@example.test",
        hashed_password="x",
        full_name="Analyst",
        role=UserRole.ANALYST,
        is_active=True,
    )

    security_changed = auth._apply_user_update_fields(target, UserUpdate(full_name="Renamed"), admin)
    assert security_changed is False
    assert target.token_version == 0

    security_changed = auth._apply_user_update_fields(target, UserUpdate(role=UserRole.REVIEWER), admin)
    assert security_changed is True
    assert target.token_version == 0

    # Reapplying the already-current role must not request another invalidation.
    security_changed = auth._apply_user_update_fields(target, UserUpdate(role=UserRole.REVIEWER), admin)
    assert security_changed is False
