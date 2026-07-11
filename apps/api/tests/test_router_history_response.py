from types import SimpleNamespace

import anyio

from routers import agent_chat_router, agent_user_router, chat
from routers.agent_user_router import SpecialistAgentConfig, create_specialist_agent_router
from services import agent_runtime_answer_audit as audit
from services.auth_service import User, UserRole


def _user(user_id: int = 7) -> User:
    return User(
        id=user_id,
        username=f"user-{user_id}",
        email=f"user-{user_id}@example.test",
        hashed_password="x",
        full_name=f"User {user_id}",
        role=UserRole.ANALYST,
        is_active=True,
        approval_status="approved",
    )


def test_assistant_chat_history_wraps_runtime_payload_and_resolved_session(monkeypatch):
    expected_messages = [{"id": 1, "role": "assistant", "content": "ok"}]
    calls = {}

    async def fake_resolve_or_create_session(async_session, current_user, profile, session_id):
        calls["resolve"] = {
            "async_session": async_session,
            "current_user": current_user,
            "profile": profile,
            "session_id": session_id,
        }
        return "resolved-assistant-session"

    async def fake_chat_history_response(async_session, session_id, *, limit):
        calls["history"] = {
            "async_session": async_session,
            "session_id": session_id,
            "limit": limit,
        }
        return expected_messages

    async def run_case():
        async_session = SimpleNamespace(name="async-session")
        current_user = _user()
        monkeypatch.setattr(chat, "resolve_or_create_session", fake_resolve_or_create_session)
        monkeypatch.setattr(chat, "chat_history_response", fake_chat_history_response)

        payload = await chat.chat_history(
            session_id="requested-session",
            limit=17,
            current_user=current_user,
            async_session=async_session,
        )

        assert payload == {
            "messages": expected_messages,
            "session_id": "resolved-assistant-session",
        }
        assert calls["resolve"] == {
            "async_session": async_session,
            "current_user": current_user,
            "profile": "assistant",
            "session_id": "requested-session",
        }
        assert calls["history"] == {
            "async_session": async_session,
            "session_id": "resolved-assistant-session",
            "limit": 17,
        }

    anyio.run(run_case)


def test_specialist_chat_history_wraps_runtime_payload_and_resolved_session(monkeypatch):
    expected_messages = [{"id": 2, "role": "user", "content": "analysis question"}]
    calls = {}

    async def fake_resolve_or_create_session(async_session, current_user, profile, session_id):
        calls["resolve"] = {
            "async_session": async_session,
            "current_user": current_user,
            "profile": profile,
            "session_id": session_id,
        }
        return "resolved-analysis-session"

    async def fake_chat_history_response(async_session, session_id, *, limit):
        calls["history"] = {
            "async_session": async_session,
            "session_id": session_id,
            "limit": limit,
        }
        return expected_messages

    async def run_case():
        async_session = SimpleNamespace(name="async-session")
        current_user = _user()
        monkeypatch.setattr(agent_user_router, "resolve_or_create_session", fake_resolve_or_create_session)
        monkeypatch.setattr(agent_user_router, "chat_history_response", fake_chat_history_response)
        router = create_specialist_agent_router(
            SpecialistAgentConfig(prefix="/analysis", tag="analysis", profile="analysis")
        )
        endpoint = next(route.endpoint for route in router.routes if route.path.endswith("/chat/history"))

        payload = await endpoint(
            session_id="requested-analysis-session",
            limit=11,
            current_user=current_user,
            async_session=async_session,
        )

        assert payload == {
            "messages": expected_messages,
            "session_id": "resolved-analysis-session",
        }
        assert calls["resolve"] == {
            "async_session": async_session,
            "current_user": current_user,
            "profile": "analysis",
            "session_id": "requested-analysis-session",
        }
        assert calls["history"] == {
            "async_session": async_session,
            "session_id": "resolved-analysis-session",
            "limit": 11,
        }

    anyio.run(run_case)


def test_specialist_answer_audit_trace_route_uses_profile_session_ownership():
    router = create_specialist_agent_router(
        SpecialistAgentConfig(prefix="/analysis", tag="analysis", profile="siq_analysis")
    )
    endpoint = next(route.endpoint for route in router.routes if route.path.endswith("/chat/audit-traces/{trace_id}"))
    trace = audit.record_answer_audit_trace(
        audit.build_answer_audit_trace(
            message="question_id=q-analysis-audit 营收是多少？",
            final_reply="[D1] source_type=wiki_metrics, metric=营收",
            profile="siq_analysis",
            session_id="user-7-analysis-session",
        ),
        log_path="/tmp/siq-test-specialist-answer-audit-trace.jsonl",
    )

    async def run_case():
        payload = await endpoint(trace["trace_id"], current_user=_user(7))
        assert payload["trace_id"] == trace["trace_id"]
        assert payload["trace"]["session_id"] == "user-7-analysis-session"

        try:
            await endpoint(trace["trace_id"], current_user=_user(8))
        except agent_user_router.HTTPException as exc:
            assert exc.status_code == 404
        else:
            raise AssertionError("other user unexpectedly read specialist audit trace")

    anyio.run(run_case)


def test_fixed_agent_chat_history_returns_runtime_payload_directly(monkeypatch):
    expected_messages = [{"id": 3, "role": "assistant", "content": "fixed reply"}]
    calls = {}

    async def fake_chat_history_response(async_session, session_id, *, limit):
        calls["history"] = {
            "async_session": async_session,
            "session_id": session_id,
            "limit": limit,
        }
        return expected_messages

    async def run_case():
        async_session = SimpleNamespace(name="async-session")
        monkeypatch.setattr(agent_chat_router, "chat_history_response", fake_chat_history_response)
        bundle = agent_chat_router.create_agent_chat_router(
            agent_chat_router.AgentChatRouterConfig(
                prefix="/demo",
                tag="demo",
                profile="analysis",
                initial_session_id="fixed-history-session",
                session_prefix="fixed-history",
                endpoint_name_prefix="demo",
            )
        )

        payload = await bundle.chat_history(limit=5, async_session=async_session)

        assert payload is expected_messages
        assert calls["history"] == {
            "async_session": async_session,
            "session_id": "fixed-history-session",
            "limit": 5,
        }

    anyio.run(run_case)
