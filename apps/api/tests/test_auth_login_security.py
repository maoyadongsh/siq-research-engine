from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers import auth
from services.auth_login_guard import LoginAttemptGuard
from services.auth_service import AuditLog, AuthService, User, UserRole
from sqlmodel import Session, SQLModel, create_engine, select


@pytest.fixture(autouse=True)
def reset_login_guard(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    LoginAttemptGuard.reset_for_tests()
    yield
    LoginAttemptGuard.reset_for_tests()


def test_login_guard_uses_separate_user_and_ip_budgets(monkeypatch):
    monkeypatch.setenv("SIQ_AUTH_LOGIN_MAX_FAILURES", "2")
    monkeypatch.setenv("SIQ_AUTH_LOGIN_IP_MAX_FAILURES", "4")
    monkeypatch.setenv("SIQ_AUTH_LOGIN_LOCKOUT_SECONDS", "37")

    assert not LoginAttemptGuard.record_failure("alice", "127.0.0.1").blocked
    decision = LoginAttemptGuard.record_failure("alice", "127.0.0.1")
    assert decision.blocked
    assert decision.retry_after == 37
    assert LoginAttemptGuard.check("alice", "127.0.0.1").blocked
    assert not LoginAttemptGuard.check("bob", "127.0.0.1").blocked


def test_login_guard_memory_fallback_is_bounded(monkeypatch):
    monkeypatch.setenv("SIQ_AUTH_LOGIN_MEMORY_MAX_ENTRIES", "4")
    for index in range(20):
        LoginAttemptGuard.record_failure(f"user-{index}", f"192.0.2.{index}")

    assert len(LoginAttemptGuard._memory) <= 4


def test_new_access_tokens_include_legacy_compatible_claims(monkeypatch):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "auth-token-claims-secret-with-enough-length")

    token = AuthService.create_access_token({"sub": "alice"})
    payload = AuthService.decode_token(token)

    assert payload is not None
    assert payload["sub"] == "alice"
    assert payload["typ"] == "access"
    assert payload["jti"]
    assert isinstance(payload["iat"], int)
    assert int(payload["exp"]) - int(payload["iat"]) == AuthService.ACCESS_TOKEN_EXPIRE_MINUTES * 60


def test_production_profile_defaults_to_cookie_mode(monkeypatch):
    monkeypatch.delenv("SIQ_AUTH_COOKIE_MODE", raising=False)
    monkeypatch.delenv("SIQ_AUTH_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("SIQ_DEPLOYMENT_PROFILE", "production")

    assert AuthService.cookie_mode_enabled()
    assert AuthService.access_cookie_secure()

    monkeypatch.setenv("SIQ_AUTH_COOKIE_MODE", "0")
    assert not AuthService.cookie_mode_enabled()


@pytest.fixture
def login_client(monkeypatch, tmp_path):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "auth-login-secret-with-enough-length")
    engine = create_engine(f"sqlite:///{tmp_path / 'auth-login.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            User(
                username="alice",
                email="alice@example.test",
                hashed_password=AuthService.hash_password("correct-password"),
                full_name="Alice",
                role=UserRole.ANALYST,
                approval_status="approved",
                is_active=True,
            )
        )
        session.commit()

    def override_session():
        with Session(engine) as session:
            yield session

    app = FastAPI()
    app.state.auth_test_engine = engine
    app.include_router(auth.router, prefix="/api/auth")
    app.dependency_overrides[auth.get_session] = override_session
    with TestClient(app) as client:
        yield client
    engine.dispose()


def test_login_rate_limit_returns_generic_429_after_failed_attempts(login_client, monkeypatch):
    monkeypatch.setenv("SIQ_AUTH_LOGIN_MAX_FAILURES", "2")
    monkeypatch.setenv("SIQ_AUTH_LOGIN_IP_MAX_FAILURES", "10")

    first = login_client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "wrong"},
    )
    second = login_client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "wrong"},
    )

    assert first.status_code == 401
    assert second.status_code == 429
    assert second.json()["detail"] == "登录尝试过于频繁，请稍后再试"
    assert int(second.headers["retry-after"]) > 0
    with Session(login_client.app.state.auth_test_engine) as session:
        audit_logs = session.exec(select(AuditLog).where(AuditLog.action == "LOGIN_FAILED")).all()
    assert len(audit_logs) == 2
    assert all("alice" not in str(item.details) for item in audit_logs)


def test_successful_login_clears_username_ip_bucket(login_client, monkeypatch):
    monkeypatch.setenv("SIQ_AUTH_LOGIN_MAX_FAILURES", "2")
    monkeypatch.setenv("SIQ_AUTH_LOGIN_IP_MAX_FAILURES", "10")

    login_client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "wrong"},
    )
    response = login_client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "correct-password"},
    )

    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    assert LoginAttemptGuard.check("alice", "testclient").blocked is False
