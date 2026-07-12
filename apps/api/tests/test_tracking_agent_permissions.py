from datetime import datetime
import inspect
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import main
from database import get_async_session, get_session
from routers import agent_user_router
from services.auth_dependencies import get_current_user
from services.auth_service import User, UserRole


def _user(role: UserRole) -> User:
    return User(
        id=7,
        username=f"{role.value}-user",
        email=f"{role.value}@example.test",
        hashed_password="x",
        full_name=role.value,
        role=role,
        is_active=True,
        approval_status="approved",
    )


async def _fake_async_session():
    yield SimpleNamespace()


def _fake_sync_session():
    yield SimpleNamespace()


def _install_user(role: UserRole) -> None:
    async def current_user():
        return _user(role)

    main.app.dependency_overrides[get_current_user] = current_user
    main.app.dependency_overrides[get_async_session] = _fake_async_session
    main.app.dependency_overrides[get_session] = _fake_sync_session


def test_tracking_chat_requires_login():
    main.app.dependency_overrides.clear()
    client = TestClient(main.app)

    response = client.post("/api/tracking/chat", json={"message": "hello"})

    assert response.status_code in {401, 403}


def test_tracking_chat_rejects_low_permission_user(monkeypatch):
    called = {"collect": False}

    async def fake_collect_chat_reply(*args, **kwargs):
        called["collect"] = True
        return "should not run"

    monkeypatch.setattr(agent_user_router, "collect_chat_reply", fake_collect_chat_reply)
    _install_user(UserRole.VIEWER)
    client = TestClient(main.app)
    try:
        response = client.post("/api/tracking/chat", json={"message": "hello"})
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 403
    assert not called["collect"]


def test_tracking_read_route_rejects_low_permission_user(monkeypatch):
    called = {"active": False}

    async def fake_resolve_or_create_session(*args, **kwargs):
        return "user-7-tracking-session"

    def fake_get_active_run_snapshot(*args, **kwargs):
        called["active"] = True
        return {"active": False}

    monkeypatch.setattr(agent_user_router, "resolve_or_create_session", fake_resolve_or_create_session)
    monkeypatch.setattr(agent_user_router, "get_active_run_snapshot", fake_get_active_run_snapshot)
    _install_user(UserRole.VIEWER)
    client = TestClient(main.app)
    try:
        response = client.get("/api/tracking/chat/active")
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 403
    assert not called["active"]


def test_tracking_chat_with_write_permission_reaches_runtime_patch_point(monkeypatch):
    called = {}

    async def fake_collect_chat_reply(message, async_session, **kwargs):
        called["message"] = message
        called["profile"] = kwargs.get("profile")
        return "patched runtime reply"

    async def fake_resolve_or_create_session(*args, **kwargs):
        return "user-7-tracking-session"

    class FakeSessionManager:
        def increment_message_count(self, session_id):
            called["incremented"] = session_id

    monkeypatch.setattr(agent_user_router, "maybe_handle_model_control", lambda *args, **kwargs: None)
    monkeypatch.setattr(agent_user_router, "collect_chat_reply", fake_collect_chat_reply)
    monkeypatch.setattr(agent_user_router, "resolve_or_create_session", fake_resolve_or_create_session)
    async def fake_enforce_quota_or_429_async(*args, **kwargs):
        return None

    async def fake_record_usage_async(*args, **kwargs):
        return None

    monkeypatch.setattr(agent_user_router, "enforce_quota_or_429_async", fake_enforce_quota_or_429_async)
    monkeypatch.setattr(agent_user_router, "record_usage_async", fake_record_usage_async)
    async def fake_record_agent_workspace_artifact_background(*args, **kwargs):
        return {"workspace_synced": False}

    monkeypatch.setattr(
        agent_user_router,
        "_record_agent_workspace_artifact_background",
        fake_record_agent_workspace_artifact_background,
    )
    monkeypatch.setattr(agent_user_router, "get_session_manager", lambda: FakeSessionManager())
    _install_user(UserRole.ANALYST)
    client = TestClient(main.app)
    try:
        response = client.post("/api/tracking/chat", json={"message": "hello"})
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["reply"] == "patched runtime reply"
    assert called["message"] == "hello"
    assert called["profile"] == "siq_tracking"
    assert called["incremented"] == "user-7-tracking-session"


def test_tracking_read_route_requires_tracking_read_permission(monkeypatch):
    called = {"active": False}

    async def fake_resolve_or_create_session(*args, **kwargs):
        return "user-7-tracking-session"

    def fake_get_active_run_snapshot(profile, session_id):
        called["active"] = True
        return {"profile": profile, "session_id": session_id, "active": False}

    monkeypatch.setattr(agent_user_router, "resolve_or_create_session", fake_resolve_or_create_session)
    monkeypatch.setattr(agent_user_router, "get_active_run_snapshot", fake_get_active_run_snapshot)
    _install_user(UserRole.ANALYST)
    client = TestClient(main.app)
    try:
        response = client.get("/api/tracking/chat/active")
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "profile": "siq_tracking",
        "session_id": "user-7-tracking-session",
        "active": False,
    }
    assert called["active"]


def test_tracking_history_route_wraps_messages_and_session_id(monkeypatch):
    messages = [
        {
            "id": 1,
            "session_id": "resolved-tracking-session",
            "role": "assistant",
            "content": "历史回复",
            "created_at": datetime(2026, 1, 2, 3, 4, 5),
            "attachments": [],
            "audit_trace_id": "aat_1234567890abcdef1234567890abcdef",
        }
    ]
    calls = {}

    async def fake_resolve_or_create_session(async_session, current_user, profile, session_id):
        calls["resolve"] = {
            "async_session": async_session,
            "current_user": current_user,
            "profile": profile,
            "session_id": session_id,
        }
        return "resolved-tracking-session"

    async def fake_chat_history_response(async_session, session_id, *, limit):
        calls["history"] = {
            "async_session": async_session,
            "session_id": session_id,
            "limit": limit,
        }
        return messages

    monkeypatch.setattr(agent_user_router, "resolve_or_create_session", fake_resolve_or_create_session)
    monkeypatch.setattr(agent_user_router, "chat_history_response", fake_chat_history_response)
    _install_user(UserRole.ANALYST)
    client = TestClient(main.app)
    try:
        response = client.get("/api/tracking/chat/history?session_id=requested-session&limit=2")
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "messages": [
            {
                "id": 1,
                "session_id": "resolved-tracking-session",
                "role": "assistant",
                "content": "历史回复",
                "created_at": "2026-01-02T03:04:05",
                "attachments": [],
                "audit_trace_id": "aat_1234567890abcdef1234567890abcdef",
                "research_identity": None,
            }
        ],
        "session_id": "resolved-tracking-session",
    }
    assert calls["resolve"]["profile"] == "tracking"
    assert calls["resolve"]["session_id"] == "requested-session"
    assert calls["history"]["session_id"] == "resolved-tracking-session"
    assert calls["history"]["limit"] == 2


@pytest.mark.parametrize("role", [UserRole.VIEWER, UserRole.REVIEWER])
def test_tracking_history_route_rejects_low_permission_user(monkeypatch, role):
    called = {"resolve": False, "history": False}

    async def fake_resolve_or_create_session(*args, **kwargs):
        called["resolve"] = True
        return "blocked-session"

    async def fake_chat_history_response(*args, **kwargs):
        called["history"] = True
        return []

    monkeypatch.setattr(agent_user_router, "resolve_or_create_session", fake_resolve_or_create_session)
    monkeypatch.setattr(agent_user_router, "chat_history_response", fake_chat_history_response)
    _install_user(role)
    client = TestClient(main.app)
    try:
        response = client.get("/api/tracking/chat/history?session_id=blocked&limit=2")
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 403
    assert called == {"resolve": False, "history": False}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/tracking/chat/session"),
        ("post", "/api/tracking/chat/session/existing-session"),
        ("delete", "/api/tracking/chat/session"),
    ],
)
@pytest.mark.parametrize("role", [UserRole.VIEWER, UserRole.REVIEWER])
def test_tracking_session_write_routes_reject_low_permission_user(monkeypatch, method, path, role):
    called = {"session_manager": False, "resolve": False, "delete": False}

    class FakeSessionManager:
        def create_session(self, *args, **kwargs):
            called["session_manager"] = True
            return "should-not-create", []

        def delete_session(self, *args, **kwargs):
            called["delete"] = True

    async def fake_resolve_or_create_session(*args, **kwargs):
        called["resolve"] = True
        return "should-not-resolve"

    async def fake_delete_chat_messages(*args, **kwargs):
        called["delete"] = True

    monkeypatch.setattr(agent_user_router, "get_session_manager", lambda: FakeSessionManager())
    monkeypatch.setattr(agent_user_router, "resolve_or_create_session", fake_resolve_or_create_session)
    monkeypatch.setattr(agent_user_router, "_delete_chat_messages", fake_delete_chat_messages)
    _install_user(role)
    client = TestClient(main.app)
    try:
        response = getattr(client, method)(path)
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 403
    assert called == {"session_manager": False, "resolve": False, "delete": False}


def test_main_app_does_not_expose_legacy_tracking_router():
    paths = {route.path for route in main.app.routes}

    assert "/api/tracking/chat" in paths
    assert "/api/tracking/process" not in paths
    assert "/api/tracking/dashboard/{stock_code}" not in paths


def _tracking_permission_dependencies(route: APIRoute) -> list[str]:
    permissions = []
    for dependency in route.dependencies:
        call = dependency.dependency
        if getattr(call, "__name__", None) != "permission_checker":
            continue
        permission = inspect.getclosurevars(call).nonlocals.get("permission")
        if permission in {"tracking.read", "tracking.write"}:
            permissions.append(permission)
    return permissions


def test_main_app_tracking_routes_bind_expected_permissions_by_method():
    routes = [
        route
        for route in main.app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/tracking/")
    ]
    paths = {route.path for route in routes}

    assert routes
    assert "/api/tracking/process" not in paths
    assert "/api/tracking/dashboard/{stock_code}" not in paths

    for route in routes:
        expected_permission = "tracking.read" if "GET" in route.methods else "tracking.write"

        assert _tracking_permission_dependencies(route) == [expected_permission], (
            route.path,
            route.methods,
        )
