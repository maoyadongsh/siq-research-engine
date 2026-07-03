import inspect
import sys
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime

import anyio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models import ChatMessage
from routers import agent_chat_router, agent_user_router, chat
from routers.agent_user_router import SpecialistAgentConfig, create_specialist_agent_router
from services.auth_service import User, UserRole
from schemas import ChatAttachment, ChatRequest


ROUTERS = [
    (create_specialist_agent_router(SpecialistAgentConfig(prefix="/analysis", tag="analysis", profile="analysis")), "analysis"),
    (create_specialist_agent_router(SpecialistAgentConfig(prefix="/factchecker", tag="factchecker", profile="factchecker")), "factchecker"),
    (create_specialist_agent_router(SpecialistAgentConfig(prefix="/tracking", tag="tracking", profile="tracking")), "tracking"),
    (create_specialist_agent_router(SpecialistAgentConfig(prefix="/legal", tag="legal", profile="legal")), "legal"),
]


def _sample_request() -> ChatRequest:
    return ChatRequest(
        message="请分析附件",
        display_message="请分析这个文件",
        attachments=[
            ChatAttachment(
                id="att-1",
                filename="sample.md",
                content_type="text/markdown",
                size=12,
                path="/home/maoyd/siq-research-engine/backend/data/chat_uploads/sample.md",
                url="/api/chat/attachments/sample.md",
                kind="document",
            )
        ],
    )


def test_specialized_agent_chat_routes_forward_attachments(monkeypatch):
    async def run_case(router_bundle, expected_profile):
        captured = {}

        async def fake_collect_chat_reply(message, async_session, **kwargs):
            captured["message"] = message
            captured["async_session"] = async_session
            captured.update(kwargs)
            return "ok"

        async def fake_resolve_or_create_session(*args, **kwargs):
            return "user-1-analysis-current"

        monkeypatch.setattr(agent_user_router, "maybe_handle_model_control", lambda message, profile: None)
        monkeypatch.setattr(agent_user_router, "collect_chat_reply", fake_collect_chat_reply)
        async def fake_enforce_quota_or_429_async(*args, **kwargs):
            return (0, None)

        async def fake_record_usage_async(*args, **kwargs):
            return None

        monkeypatch.setattr(agent_user_router, "enforce_quota_or_429_async", fake_enforce_quota_or_429_async)
        monkeypatch.setattr(agent_user_router, "record_usage_async", fake_record_usage_async)
        monkeypatch.setattr(agent_user_router, "resolve_or_create_session", fake_resolve_or_create_session)

        session = object()
        current_user = User(
            id=1,
            username="tester",
            email="tester@example.com",
            hashed_password="x",
            full_name="Tester",
            role=UserRole.VIEWER,
        )
        response = await router_bundle.routes[0].endpoint(
            _sample_request(),
            current_user=current_user,
            async_session=session,
        )

        assert response.reply == "ok"
        assert captured["message"] == "请分析附件"
        assert captured["async_session"] is session
        assert captured["profile"] == expected_profile
        assert captured["display_message"] == "请分析这个文件"
        assert len(captured["attachments"]) == 1
        assert captured["attachments"][0].filename == "sample.md"

    for router_bundle, expected_profile in ROUTERS:
        anyio.run(run_case, router_bundle, expected_profile)


def test_specialized_agent_stream_routes_forward_attachments_in_source():
    source = inspect.getsource(agent_user_router.create_specialist_agent_router)
    assert source.count("attachments=req.attachments") >= 2


def test_agent_router_session_id_callback_runs_for_created_session():
    session_updates = []
    bundle = agent_chat_router.create_agent_chat_router(
        agent_chat_router.AgentChatRouterConfig(
            prefix="/demo",
            tag="demo",
            profile="analysis",
            initial_session_id="siq-demo-session",
            session_prefix="siq-demo",
            endpoint_name_prefix="demo",
            on_session_id_change=session_updates.append,
        )
    )

    created = anyio.run(bundle.create_session)

    assert created["created"] is True
    assert created["session_id"].startswith("siq-demo-")
    assert session_updates == [created["session_id"]]


class _EmptySessionManager:
    def list_user_sessions(self, *args, **kwargs):
        return []

    def get_current_session_id(self, *args, **kwargs):
        return None


class _SessionManagerWithEmptyCurrent:
    def list_user_sessions(self, *args, **kwargs):
        return [
            {
                "session_id": "user-7-analysis-empty",
                "user_id": "7",
                "profile": "analysis",
                "message_count": 0,
                "created_at": "2026-06-16T10:00:00",
                "updated_at": "2026-06-16T10:00:00",
            }
        ]

    def get_current_session_id(self, *args, **kwargs):
        return "user-7-analysis-empty"


async def _with_temp_chat_session(tmp_path, callback):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'chat_sessions.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with AsyncSession(engine) as session:
            await callback(session)
    finally:
        await engine.dispose()


class _MissingSessionManager:
    def __init__(self):
        self.restored = None

    def set_current_session(self, *args, **kwargs):
        raise HTTPException(404, "Session not found or expired")

    def get_current_session_id(self, *args, **kwargs):
        return None

    def restore_session(self, user_id, profile, session_id, **kwargs):
        self.restored = {
            "user_id": user_id,
            "profile": profile,
            "session_id": session_id,
            **kwargs,
        }
        return session_id

    def create_session(self, *args, **kwargs):
        return "user-1-assistant-new"


class _CommitSensitiveUser:
    def __init__(self):
        self.expired = False
        self.username = "commit-sensitive"
        self.email = "commit-sensitive@example.test"
        self.hashed_password = "x"
        self.full_name = "Commit Sensitive"
        self.is_active = True
        self.approval_status = "approved"

    @property
    def id(self):
        if self.expired:
            raise AssertionError("current_user.id was accessed after async usage commit")
        return 7

    @property
    def role(self):
        if self.expired:
            raise AssertionError("current_user.role was accessed after async usage commit")
        return UserRole.ANALYST


class _SpecialistSessionManager:
    def __init__(self):
        self.created = []
        self.incremented = []

    def get_current_session_id(self, *args, **kwargs):
        return None

    def create_session(self, user_id, profile, **kwargs):
        self.created.append((user_id, profile, kwargs))
        return f"user-{user_id}-{profile}-created"

    def increment_message_count(self, session_id):
        self.incremented.append(session_id)


def test_specialized_agent_chat_uses_stable_user_values_after_usage_commit(monkeypatch, tmp_path):
    async def run_case(async_session):
        user = _CommitSensitiveUser()
        session_mgr = _SpecialistSessionManager()
        captured = {}

        async def fake_enforce_quota_or_429_async(*args, **kwargs):
            assert not user.expired
            return (0, None)

        async def fake_record_usage_async(*args, **kwargs):
            user.expired = True
            return None

        async def fake_collect_chat_reply(message, async_session, **kwargs):
            captured["profile"] = kwargs["profile"]
            captured["session_id"] = kwargs["session_id"]
            return "ok"

        async def fake_record_agent_workspace_artifact_background(**kwargs):
            captured["workspace_user_id"] = kwargs["current_user_id"]
            return {"workspace_synced": True}

        monkeypatch.setattr(agent_user_router, "get_session_manager", lambda: session_mgr)
        monkeypatch.setattr(agent_user_router, "maybe_handle_model_control", lambda *args, **kwargs: None)
        monkeypatch.setattr(agent_user_router, "enforce_quota_or_429_async", fake_enforce_quota_or_429_async)
        monkeypatch.setattr(agent_user_router, "record_usage_async", fake_record_usage_async)
        monkeypatch.setattr(agent_user_router, "collect_chat_reply", fake_collect_chat_reply)
        monkeypatch.setattr(
            agent_user_router,
            "_record_agent_workspace_artifact_background",
            fake_record_agent_workspace_artifact_background,
        )

        router = create_specialist_agent_router(
            SpecialistAgentConfig(prefix="/analysis", tag="analysis", profile="siq_analysis")
        )
        endpoint = next(route.endpoint for route in router.routes if route.path == "/analysis/chat")
        response = await endpoint(_sample_request(), current_user=user, async_session=async_session)

        assert response.reply == "ok"
        assert session_mgr.created == [("7", "analysis", {"user_role": "analyst"})]
        assert session_mgr.incremented == ["user-7-analysis-created"]
        assert captured == {
            "profile": "siq_analysis",
            "session_id": "user-7-analysis-created",
            "workspace_user_id": 7,
        }

    anyio.run(_with_temp_chat_session, tmp_path, run_case)


def test_specialized_agent_stream_resolves_session_after_usage_commit_without_user_reload(monkeypatch, tmp_path):
    async def run_case(async_session):
        user = _CommitSensitiveUser()
        session_mgr = _SpecialistSessionManager()

        async def fake_enforce_quota_or_429_async(*args, **kwargs):
            assert not user.expired
            return (0, None)

        async def fake_record_usage_async(*args, **kwargs):
            user.expired = True
            return None

        monkeypatch.setattr(agent_user_router, "get_session_manager", lambda: session_mgr)
        monkeypatch.setattr(agent_user_router, "enforce_quota_or_429_async", fake_enforce_quota_or_429_async)
        monkeypatch.setattr(agent_user_router, "record_usage_async", fake_record_usage_async)

        router = create_specialist_agent_router(
            SpecialistAgentConfig(prefix="/legal", tag="legal", profile="siq_legal")
        )
        endpoint = next(route.endpoint for route in router.routes if route.path == "/legal/chat/stream")
        response = await endpoint(
            _sample_request(),
            request=SimpleNamespace(),
            current_user=user,
            async_session=async_session,
        )

        assert response.status_code == 200
        assert session_mgr.created == [("7", "legal", {"user_role": "analyst"})]

    anyio.run(_with_temp_chat_session, tmp_path, run_case)


def test_chat_resolves_stale_memory_session_from_db(monkeypatch, tmp_path):
    async def run_case(async_session):
        session_mgr = _MissingSessionManager()
        monkeypatch.setattr(chat, "get_session_manager", lambda: session_mgr)
        current_user = User(
            id=1,
            username="tester",
            email="tester@example.com",
            hashed_password="x",
            full_name="Tester",
            role=UserRole.ANALYST,
        )
        session_id = "user-1-assistant-restored"
        async_session.add(
            ChatMessage(
                session_id=session_id,
                role="user",
                content="帮我看一下万华化学的现金流",
                created_at=datetime(2026, 6, 21, 9, 0, 0),
            )
        )
        async_session.add(
            ChatMessage(
                session_id=session_id,
                role="assistant",
                content="经营现金流需要和净利润一起看。",
                created_at=datetime(2026, 6, 21, 9, 1, 0),
            )
        )
        await async_session.commit()

        resolved = await chat.resolve_or_create_session(
            async_session,
            current_user,
            "assistant",
            session_id,
        )

        assert resolved == session_id
        assert session_mgr.restored["session_id"] == session_id
        assert session_mgr.restored["message_count"] == 2

    anyio.run(_with_temp_chat_session, tmp_path, run_case)


def test_specialized_chat_resolves_stale_memory_session_from_db(monkeypatch, tmp_path):
    async def run_case(async_session):
        session_mgr = _MissingSessionManager()
        monkeypatch.setattr(agent_user_router, "get_session_manager", lambda: session_mgr)
        current_user = User(
            id=7,
            username="analyst",
            email="analyst@example.com",
            hashed_password="x",
            full_name="Analyst",
            role=UserRole.ANALYST,
        )
        session_id = "user-7-analysis-restored"
        async_session.add(
            ChatMessage(
                session_id=session_id,
                role="user",
                content="请分析上汽集团现金流",
                created_at=datetime(2026, 6, 21, 9, 0, 0),
            )
        )
        async_session.add(
            ChatMessage(
                session_id=session_id,
                role="assistant",
                content="现金流质量需要结合经营现金流和利润匹配度。",
                created_at=datetime(2026, 6, 21, 9, 1, 0),
            )
        )
        await async_session.commit()

        resolved = await agent_user_router.resolve_or_create_session(
            async_session,
            current_user,
            "analysis",
            session_id,
        )

        assert resolved == session_id
        assert session_mgr.restored["session_id"] == session_id
        assert session_mgr.restored["profile"] == "analysis"
        assert session_mgr.restored["message_count"] == 2

    anyio.run(_with_temp_chat_session, tmp_path, run_case)


def test_specialized_agent_sessions_include_db_history_without_redis(monkeypatch, tmp_path):
    async def run_case(async_session):
        monkeypatch.setattr(agent_user_router, "get_session_manager", lambda: _EmptySessionManager())
        current_user = User(
            id=7,
            username="analyst",
            email="analyst@example.com",
            hashed_password="x",
            full_name="Analyst",
            role=UserRole.ANALYST,
        )
        session_id = "user-7-analysis-dbonly"
        async_session.add(
            ChatMessage(
                session_id=session_id,
                role="user",
                content="请分析上汽集团现金流",
                created_at=datetime(2026, 6, 15, 10, 0, 0),
            )
        )
        async_session.add(
            ChatMessage(
                session_id=session_id,
                role="assistant",
                content="现金流质量需要结合经营现金流和利润匹配度。",
                created_at=datetime(2026, 6, 15, 10, 1, 0),
            )
        )
        await async_session.commit()

        router = create_specialist_agent_router(
            SpecialistAgentConfig(prefix="/analysis", tag="analysis", profile="analysis")
        )
        endpoint = next(route.endpoint for route in router.routes if route.path.endswith("/chat/sessions"))
        payload = await endpoint(limit=100, current_user=current_user, async_session=async_session)

        assert payload["sessions"] == [
            {
                "session_id": session_id,
                "title": "请分析上汽集团现金流",
                "preview": "现金流质量需要结合经营现金流和利润匹配度。",
                "message_count": 2,
                "first_message_at": datetime(2026, 6, 15, 10, 0, 0),
                "last_message_at": datetime(2026, 6, 15, 10, 1, 0),
                "current": False,
            }
        ]

    anyio.run(_with_temp_chat_session, tmp_path, run_case)


def test_specialized_agent_sessions_hide_empty_current_shell(monkeypatch, tmp_path):
    async def run_case(async_session):
        monkeypatch.setattr(agent_user_router, "get_session_manager", lambda: _SessionManagerWithEmptyCurrent())
        current_user = User(
            id=7,
            username="analyst",
            email="analyst@example.com",
            hashed_password="x",
            full_name="Analyst",
            role=UserRole.ANALYST,
        )
        router = create_specialist_agent_router(
            SpecialistAgentConfig(prefix="/analysis", tag="analysis", profile="analysis")
        )
        endpoint = next(route.endpoint for route in router.routes if route.path.endswith("/chat/sessions"))
        payload = await endpoint(limit=100, current_user=current_user, async_session=async_session)

        assert payload["sessions"] == []

    anyio.run(_with_temp_chat_session, tmp_path, run_case)


def test_chat_merge_sessions_hides_empty_session_manager_shell():
    sessions = chat._merge_sessions(
        [
            {
                "session_id": "user-1-assistant-empty",
                "message_count": 0,
                "created_at": "2026-06-16T10:00:00",
                "updated_at": "2026-06-16T10:00:00",
            },
            {
                "session_id": "user-1-assistant-stale",
                "message_count": 9,
                "created_at": "2026-06-15T10:00:00",
                "updated_at": "2026-06-15T10:00:00",
            },
            {
                "session_id": "user-1-assistant-real",
                "message_count": 0,
                "created_at": "2026-06-16T09:00:00",
                "updated_at": "2026-06-16T09:00:00",
            },
        ],
        [
            {
                "session_id": "user-1-assistant-real",
                "message_count": 2,
                "last_message_at": "2026-06-16T09:05:00",
            }
        ],
        limit=100,
    )

    assert [item["session_id"] for item in sessions] == ["user-1-assistant-real"]
    assert sessions[0]["message_count"] == 2


def test_agent_user_merge_sessions_hides_stale_session_manager_shell():
    sessions = agent_user_router._merge_sessions(
        [
            {
                "session_id": "user-7-analysis-stale",
                "message_count": 8,
                "created_at": "2026-06-15T10:00:00",
                "updated_at": "2026-06-15T10:00:00",
            }
        ],
        [],
        limit=100,
    )

    assert sessions == []
