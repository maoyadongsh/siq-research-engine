import json

import anyio
import main
import pytest
from database import get_async_session
from fastapi.testclient import TestClient
from models import ChatMessage
from routers import chat
from schemas import ChatRequest
from services.auth_service import AuthService, User, UserRole
from services.usage_service import AGENT_QUESTION_EVENT, UsageEvent
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from services import (
    agent_chat_runtime as runtime,
    agent_runtime_answer_audit as audit,
    agent_runtime_wiki_context as wiki_context,
)


class _SessionManager:
    def __init__(self):
        self.message_count = 0

    def get_current_session_id(self, *args, **kwargs):
        return None

    def set_current_session(self, user_id, profile, session_id):
        return session_id

    def create_session(self, user_id, profile, **kwargs):
        return f"user-{user_id}-{profile}-new"

    def increment_message_count(self, session_id):
        self.message_count += 1


async def _with_chat_db(tmp_path, callback):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'chat-route.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with AsyncSession(engine) as session:
            await callback(session)
    finally:
        await engine.dispose()


async def _add_user(session: AsyncSession) -> User:
    user = User(
        username="analyst",
        email="analyst@example.test",
        full_name="Analyst",
        hashed_password="x",
        role=UserRole.ANALYST,
        is_active=True,
        approval_status="approved",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def test_chat_route_records_usage_without_expiring_current_user(monkeypatch, tmp_path):
    async def run_case(session):
        session_manager = _SessionManager()
        user = await _add_user(session)
        monkeypatch.setattr(chat, "get_session_manager", lambda: session_manager)
        monkeypatch.setattr(chat, "maybe_handle_model_control", lambda message, profile: "模型状态正常")

        response = await chat.chat(
            ChatRequest(message="/model status"),
            current_user=user,
            async_session=session,
        )

        usage = (await session.exec(select(UsageEvent).where(UsageEvent.event_type == AGENT_QUESTION_EVENT))).one()
        messages = (await session.exec(select(ChatMessage).order_by(ChatMessage.id))).all()

        assert response.reply == "模型状态正常"
        assert usage.user_id == user.id
        assert usage.source == "assistant"
        assert [message.role for message in messages] == ["user", "assistant"]
        assert messages[0].session_id == "user-1-assistant-new"
        assert session_manager.message_count == 1

    anyio.run(_with_chat_db, tmp_path, run_case)


def test_chat_http_history_keeps_per_message_identity_when_session_switches_company(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "chat-message-identity-secret-with-enough-length")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'chat-message-identity.db'}")
    original_overrides = main.app.dependency_overrides.copy()
    identity_a = {
        "market": "HK",
        "company_id": "HK:01398",
        "filing_id": "HK:01398:2025-annual",
        "parse_run_id": "parse-hk-01398",
    }
    identity_b = {
        "market": "US",
        "company_id": "US:0000320193",
        "filing_id": "US:AAPL:2025-10-K",
        "parse_run_id": "parse-us-aapl",
    }

    async def setup_database() -> tuple[int, str]:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with AsyncSession(engine) as session:
            user = await _add_user(session)
            return int(user.id), AuthService.create_access_token({"sub": str(user.id)})

    async def override_async_session():
        async with AsyncSession(engine) as session:
            yield session

    try:
        user_id, token = anyio.run(setup_database)
        session_id = f"user-{user_id}-assistant-identity-snapshot"
        session_manager = _SessionManager()
        session_manager.get_current_session_id = lambda *_args, **_kwargs: session_id
        session_manager.set_current_session = lambda _user_id, _profile, requested: requested
        monkeypatch.setattr(chat, "get_session_manager", lambda: session_manager)
        monkeypatch.setattr(
            chat,
            "maybe_handle_model_control",
            lambda message, _profile: f"已处理：{message}",
        )
        main.app.dependency_overrides[get_async_session] = override_async_session

        headers = {"Authorization": f"Bearer {token}"}
        with TestClient(main.app) as client:
            response_a = client.post(
                "/api/chat",
                headers=headers,
                json={"message": "查询工商银行", "context": {"research_identity": identity_a}},
            )
            response_b = client.post(
                "/api/chat",
                headers=headers,
                json={"message": "切换到苹果", "context": {"research_identity": identity_b}},
            )
            history_response = client.get(
                "/api/chat/history",
                headers=headers,
                params={"session_id": session_id, "limit": 10},
            )

        assert response_a.status_code == 200
        assert response_b.status_code == 200
        assert history_response.status_code == 200
        history = history_response.json()
        assert history["session_id"] == session_id
        assert [message["role"] for message in history["messages"]] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]
        assert [message["research_identity"] for message in history["messages"]] == [
            identity_a,
            identity_a,
            identity_b,
            identity_b,
        ]

        async def inspect_database():
            async with AsyncSession(engine) as session:
                return (await session.exec(select(ChatMessage).order_by(ChatMessage.id))).all()

        persisted = anyio.run(inspect_database)
        assert [json.loads(message.research_identity_json) for message in persisted] == [
            identity_a,
            identity_a,
            identity_b,
            identity_b,
        ]
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(original_overrides)
        anyio.run(engine.dispose)


def test_reset_session_does_not_read_expired_current_user_after_commit(monkeypatch, tmp_path):
    class ResetSessionManager:
        def __init__(self):
            self.deleted = []

        def get_current_session_id(self, user_id, profile):
            return f"user-{user_id}-{profile}-old"

        def set_current_session(self, user_id, profile, session_id):
            return session_id

        def delete_session(self, session_id, user_id):
            self.deleted.append((session_id, user_id))

        def create_session(self, user_id, profile, **kwargs):
            assert kwargs["user_role"] == UserRole.ANALYST
            assert kwargs["return_deleted"] is True
            return f"user-{user_id}-{profile}-new", []

    async def run_case(session):
        session_manager = ResetSessionManager()
        user = await _add_user(session)
        user_id = str(user.id)
        old_session_id = f"user-{user_id}-assistant-old"
        session.add(ChatMessage(session_id=old_session_id, role="user", content="old"))
        await session.commit()
        await session.refresh(user)
        monkeypatch.setattr(chat, "get_session_manager", lambda: session_manager)

        response = await chat.reset_session(
            session_id=old_session_id,
            current_user=user,
            async_session=session,
        )

        remaining = (await session.exec(select(ChatMessage))).all()
        assert response == {
            "session_id": f"user-{user_id}-assistant-new",
            "deleted_old": old_session_id,
        }
        assert session_manager.deleted == [(old_session_id, user_id)]
        assert remaining == []

    anyio.run(_with_chat_db, tmp_path, run_case)


def test_chat_route_returns_answer_audit_trace_id(monkeypatch, tmp_path):
    async def run_case(session):
        user = await _add_user(session)
        monkeypatch.setattr(chat, "get_session_manager", lambda: _SessionManager())
        monkeypatch.setattr(chat, "maybe_handle_model_control", lambda message, profile: None)

        async def fake_update_agent_and_achievements(_session):
            return []

        monkeypatch.setattr(chat, "update_agent_and_achievements", fake_update_agent_and_achievements)

        async def fake_collect_chat_reply(*_args, **kwargs):
            kwargs["answer_audit_callback"]({"trace_id": "aat_1234567890abcdef1234567890abcdef"})
            return "带来源的回答"

        monkeypatch.setattr(chat, "collect_chat_reply", fake_collect_chat_reply)

        response = await chat.chat(
            ChatRequest(message="腾讯收入是多少？"),
            current_user=user,
            async_session=session,
        )

        assert response.reply == "带来源的回答"
        assert response.audit_trace_id == "aat_1234567890abcdef1234567890abcdef"

    anyio.run(_with_chat_db, tmp_path, run_case)


def test_chat_route_blocks_financial_claim_mismatch_through_runtime_guard(monkeypatch, tmp_path):
    async def run_case(session):
        user = await _add_user(session)
        monkeypatch.setattr(chat, "get_session_manager", lambda: _SessionManager())
        monkeypatch.setattr(chat, "maybe_handle_model_control", lambda message, profile: None)

        async def fake_update_agent_and_achievements(_session):
            return []

        raw_reply = (
            "工商银行 2025 年营业收入为 6,351.26 亿元。\n\n"
            "[P1] source_type=wiki_metrics market=HK company_id=HK:01398 "
            "filing_id=HK:01398:2025-annual parse_run_id=parse-hk-01398 "
            "file=metrics/three_statements.json canonical_name=operating_revenue "
            "metric_name=营业收入 period_key=2025 pdf_page=1 table_index=1 md_line=1 "
            "task_id=00000000-0000-0000-0000-000000000101 "
            'value=8382.70 unit=亿元 evidence_id=EVID-REV-2025 quote="营业收入 838,270"'
        )

        async def fake_prepare_envelope(*_args, **_kwargs):
            return runtime.ChatRequestEnvelope(
                all_attachments=[],
                message_hash="hash-route-claim-mismatch",
                user_display_message="工商银行 2025 年营业收入是多少？",
            )

        async def fake_preflight(*_args, **_kwargs):
            return runtime.ChatRunPreflightContext(history=[], local_memory_context=None, attachments=[])

        async def fake_wait_for_pdf_attachment_parses(_attachments):
            return None

        async def fake_analyze_images(*_args, **_kwargs):
            return None, True

        async def fake_refresh_session_memory(*_args, **_kwargs):
            return None

        async def fake_create_run(run_input, history, *, profile, session_id):
            assert run_input["message"] == "工商银行 2025 年营业收入是多少？"
            assert history == []
            assert profile == "siq_assistant"
            assert session_id == runtime.hermes_runs_session_id("siq_assistant", "user-1-assistant-new")
            return "run-route-claim-mismatch"

        async def fake_collect_run_result(run_id, *, profile, timeout):
            assert run_id == "run-route-claim-mismatch"
            assert profile == "siq_assistant"
            assert timeout == runtime.hermes_timeout()
            return raw_reply

        monkeypatch.setenv("SIQ_ANSWER_AUDIT_TRACE_LOG_PATH", str(tmp_path / "route-claim-audit.jsonl"))
        audit.RECENT_ANSWER_AUDIT_TRACES.clear()
        monkeypatch.setattr(chat, "update_agent_and_achievements", fake_update_agent_and_achievements)
        monkeypatch.setattr(runtime, "_prepare_chat_request_envelope", fake_prepare_envelope)
        monkeypatch.setattr(runtime, "build_wiki_catalog_reply", lambda _message: None)
        monkeypatch.setattr(runtime, "_load_chat_run_preflight_context", fake_preflight)
        monkeypatch.setattr(runtime, "wait_for_pdf_attachment_parses", fake_wait_for_pdf_attachment_parses)
        monkeypatch.setattr(runtime, "_attachments_with_fresh_metadata", lambda attachments: attachments)
        monkeypatch.setattr(runtime, "refresh_session_memory", fake_refresh_session_memory)
        monkeypatch.setattr(runtime, "analyze_images_with_primary_model", fake_analyze_images)
        monkeypatch.setattr(runtime, "normalize_evidence_trace_for_display", lambda reply: reply)
        monkeypatch.setattr(
            runtime.agent_runtime_financial_sources,
            "append_primary_data_evidence_if_needed",
            lambda _message, _context, reply, *, deps: reply,
        )
        monkeypatch.setattr(
            runtime.agent_runtime_task_ids,
            "invalid_task_ids_in_reply",
            lambda *_args, **_kwargs: [],
        )
        monkeypatch.setattr(runtime, "build_hermes_run_input", lambda message, **kwargs: {"message": message, **kwargs})
        monkeypatch.setattr(runtime, "create_run", fake_create_run)
        monkeypatch.setattr(runtime, "collect_run_result", fake_collect_run_result)
        monkeypatch.setattr(runtime, "_remember_completed_run", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(runtime, "_record_financial_llm_provenance_if_needed", lambda **_kwargs: None)

        response = await chat.chat(
            ChatRequest(
                message="工商银行 2025 年营业收入是多少？",
                context={
                    "research_identity": {
                        "market": "HK",
                        "company_id": "HK:01398",
                        "filing_id": "HK:01398:2025-annual",
                        "parse_run_id": "parse-hk-01398",
                    },
                    "company": {"name": "工商银行", "code": "01398"},
                },
            ),
            current_user=user,
            async_session=session,
        )

        messages = (await session.exec(select(ChatMessage).order_by(ChatMessage.id))).all()
        trace = audit.get_answer_audit_trace(response.audit_trace_id)

        assert "## 财务数值证据不一致" in response.reply
        assert "guardrail_status=blocked" in response.reply
        assert "guardrail_reason=financial_claim_mismatch" in response.reply
        assert "6,351.26 亿元" not in response.reply
        assert response.audit_trace_id
        assert [message.role for message in messages] == ["user", "assistant"]
        assert messages[-1].content == response.reply
        assert messages[-1].audit_trace_id == response.audit_trace_id
        assert trace["guardrail_result"]["blocked"] is True
        assert trace["claim_verifier_result"]["violations"][0]["reason"] == "value_mismatch"

    anyio.run(_with_chat_db, tmp_path, run_case)


@pytest.mark.parametrize("auth_mode", ("bearer", "cookie_csrf"))
def test_chat_http_route_blocks_financial_claim_mismatch_with_real_auth_and_serialization(
    monkeypatch,
    tmp_path,
    auth_mode,
):
    """Exercise the protected HTTP boundary while keeping Hermes itself deterministic."""

    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "chat-http-smoke-secret-with-enough-length")
    monkeypatch.setenv("SIQ_AUTH_COOKIE_MODE", "1" if auth_mode == "cookie_csrf" else "0")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'chat-http-route.db'}")
    original_overrides = main.app.dependency_overrides.copy()

    async def setup_database() -> tuple[int, str]:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with AsyncSession(engine) as session:
            user = await _add_user(session)
            token = AuthService.create_access_token({"sub": str(user.id)})
            return int(user.id), token

    async def override_async_session():
        async with AsyncSession(engine) as session:
            yield session

    raw_reply = (
        "工商银行 2025 年营业收入为 6,351.26 亿元。\n\n"
        "[P1] source_type=wiki_metrics market=HK company_id=HK:01398 "
        "filing_id=HK:01398:2025-annual parse_run_id=parse-hk-01398 "
        "file=metrics/three_statements.json canonical_name=operating_revenue "
        "metric_name=营业收入 period_key=2025 pdf_page=1 table_index=1 md_line=1 "
        "task_id=00000000-0000-0000-0000-000000000102 "
        'value=8382.70 unit=亿元 evidence_id=EVID-REV-HTTP quote="营业收入 838,270"'
    )

    async def fake_update_agent_and_achievements(_session):
        return []

    async def fake_prepare_envelope(*_args, **_kwargs):
        return runtime.ChatRequestEnvelope(
            all_attachments=[],
            message_hash="hash-http-claim-mismatch",
            user_display_message="工商银行 2025 年营业收入是多少？",
        )

    async def fake_preflight(*_args, **_kwargs):
        return runtime.ChatRunPreflightContext(history=[], local_memory_context=None, attachments=[])

    async def fake_wait_for_pdf_attachment_parses(_attachments):
        return None

    async def fake_analyze_images(*_args, **_kwargs):
        return None, True

    async def fake_refresh_session_memory(*_args, **_kwargs):
        return None

    mirrored_created_at = []

    async def fake_record_memory_message(_session, _context, **kwargs):
        mirrored_created_at.append(kwargs["created_at"])
        return len(mirrored_created_at)

    async def fake_promote_explicit_memory(*_args, **_kwargs):
        return None

    async def fake_create_run(run_input, history, *, profile, session_id):
        assert run_input["message"] == "工商银行 2025 年营业收入是多少？"
        assert history == []
        assert profile == "siq_assistant"
        assert session_id == runtime.hermes_runs_session_id("siq_assistant", f"user-{user_id}-assistant-http")
        assert run_input["context"]["research_identity"] == {
            "market": "HK",
            "company_id": "HK:01398",
            "filing_id": "HK:01398:2025-annual",
            "parse_run_id": "parse-hk-01398",
        }
        return "run-http-claim-mismatch"

    async def fake_collect_run_result(run_id, *, profile, timeout):
        assert run_id == "run-http-claim-mismatch"
        assert profile == "siq_assistant"
        assert timeout == runtime.hermes_timeout()
        return raw_reply

    try:
        user_id, token = anyio.run(setup_database)
        session_manager = _SessionManager()
        session_manager.get_current_session_id = lambda *_args, **_kwargs: f"user-{user_id}-assistant-http"
        monkeypatch.setattr(chat, "get_session_manager", lambda: session_manager)
        monkeypatch.setattr(chat, "update_agent_and_achievements", fake_update_agent_and_achievements)
        monkeypatch.setattr(runtime, "_prepare_chat_request_envelope", fake_prepare_envelope)
        monkeypatch.setattr(runtime, "build_wiki_catalog_reply", lambda _message: None)
        monkeypatch.setattr(runtime, "_load_chat_run_preflight_context", fake_preflight)
        monkeypatch.setattr(runtime, "wait_for_pdf_attachment_parses", fake_wait_for_pdf_attachment_parses)
        monkeypatch.setattr(runtime, "_attachments_with_fresh_metadata", lambda attachments: attachments)
        monkeypatch.setattr(runtime, "refresh_session_memory", fake_refresh_session_memory)
        monkeypatch.setattr(runtime, "analyze_images_with_primary_model", fake_analyze_images)
        monkeypatch.setattr(runtime, "normalize_evidence_trace_for_display", lambda reply: reply)
        monkeypatch.setattr(
            runtime.agent_runtime_financial_sources,
            "append_primary_data_evidence_if_needed",
            lambda _message, _context, reply, *, deps: reply,
        )
        monkeypatch.setattr(
            runtime.agent_runtime_task_ids,
            "invalid_task_ids_in_reply",
            lambda *_args, **_kwargs: [],
        )
        monkeypatch.setattr(runtime, "build_hermes_run_input", lambda message, **kwargs: {"message": message, **kwargs})
        monkeypatch.setattr(runtime, "create_run", fake_create_run)
        monkeypatch.setattr(runtime, "collect_run_result", fake_collect_run_result)
        monkeypatch.setattr(runtime, "_remember_completed_run", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(runtime, "_record_financial_llm_provenance_if_needed", lambda **_kwargs: None)
        monkeypatch.setattr(runtime.agent_memory_service, "context_from_session_id", lambda *_args, **_kwargs: object())
        monkeypatch.setattr(runtime.agent_memory_service, "record_message", fake_record_memory_message)
        monkeypatch.setattr(
            runtime.agent_memory_service,
            "maybe_promote_explicit_memory",
            fake_promote_explicit_memory,
        )
        main.app.dependency_overrides[get_async_session] = override_async_session
        monkeypatch.setenv("SIQ_ANSWER_AUDIT_TRACE_LOG_PATH", str(tmp_path / "http-claim-audit.jsonl"))
        audit.RECENT_ANSWER_AUDIT_TRACES.clear()

        request_headers: dict[str, str] = {}
        request_cookies: dict[str, str] = {}
        if auth_mode == "bearer":
            request_headers["Authorization"] = f"Bearer {token}"
        else:
            csrf_token = "chat-http-csrf-token"
            request_headers.update({"Origin": "http://testserver", "X-CSRF-Token": csrf_token})
            request_cookies.update(
                {
                    AuthService.ACCESS_COOKIE_NAME: token,
                    AuthService.CSRF_COOKIE_NAME: csrf_token,
                }
            )

        with TestClient(main.app) as client:
            for name, value in request_cookies.items():
                client.cookies.set(name, value)
            if auth_mode == "cookie_csrf":
                rejected = client.post(
                    "/api/chat",
                    headers={"Origin": "http://testserver"},
                    json={"message": "工商银行 2025 年营业收入是多少？"},
                )
                assert rejected.status_code == 403
                assert rejected.json()["detail"] == "CSRF token missing or invalid"
            response = client.post(
                "/api/chat",
                headers=request_headers,
                json={
                    "message": "工商银行 2025 年营业收入是多少？",
                    "context": {
                        "research_identity": {
                            "market": "HK",
                            "company_id": "HK:01398",
                            "filing_id": "HK:01398:2025-annual",
                            "parse_run_id": "parse-hk-01398",
                        },
                        "company": {"name": "工商银行", "code": "01398"},
                    },
                },
            )
            response_payload = response.json()
            history_response = client.get(
                "/api/chat/history",
                headers=request_headers,
                params={"limit": 10},
            )
            audit_response = client.get(
                f"/api/chat/audit-traces/{response_payload.get('audit_trace_id')}",
                headers=request_headers,
            )

        assert response.status_code == 200
        payload = response_payload
        assert payload["reply"].startswith("## 财务数值证据不一致")
        assert "guardrail_status=blocked" in payload["reply"]
        assert "guardrail_reason=financial_claim_mismatch" in payload["reply"]
        assert "6,351.26 亿元" not in payload["reply"]
        assert payload["audit_trace_id"]

        async def inspect_database():
            async with AsyncSession(engine) as session:
                messages = (await session.exec(select(ChatMessage).order_by(ChatMessage.id))).all()
                trace = audit.get_answer_audit_trace(payload["audit_trace_id"])
                return messages, trace

        messages, trace = anyio.run(inspect_database)
        assert [message.role for message in messages] == ["user", "assistant"]
        assert messages[-1].content == payload["reply"]
        assert messages[-1].audit_trace_id == payload["audit_trace_id"]
        assert trace["guardrail_result"]["blocked"] is True
        assert trace["claim_verifier_result"]["violations"][0]["reason"] == "value_mismatch"
        assert trace["resolved_company"] == {
            "id": "HK:01398",
            "name": "工商银行",
            "code": "01398",
            "market": "HK",
        }
        assert trace["resolved_period"]["filing_id"] == "HK:01398:2025-annual"
        assert trace["resolved_period"]["parse_run_id"] == "parse-hk-01398"
        assert history_response.status_code == 200
        history_payload = history_response.json()
        assert history_payload["session_id"] == f"user-{user_id}-assistant-http"
        assert [message["role"] for message in history_payload["messages"]] == ["user", "assistant"]
        assert history_payload["messages"][-1]["audit_trace_id"] == payload["audit_trace_id"]
        assert audit_response.status_code == 200
        audit_payload = audit_response.json()
        assert audit_payload["trace_id"] == payload["audit_trace_id"]
        assert audit_payload["trace"]["resolved_company"]["market"] == "HK"
        assert audit_payload["trace"]["resolved_period"] == trace["resolved_period"]
        assert len(mirrored_created_at) == 2
        assert all(created_at is not None for created_at in mirrored_created_at)
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(original_overrides)
        anyio.run(engine.dispose)


@pytest.mark.parametrize(
    ("market", "company_id", "filing_id", "parse_run_id", "company_name", "company_code"),
    (
        ("HK", "HK:01398", "HK:01398:2025-annual", "parse-hk-01398", "工商银行", "01398"),
        ("JP", "JP:7203", "JP:7203:2025-annual", "parse-jp-7203", "Toyota Motor", "7203"),
        ("KR", "KR:005930", "KR:005930:2025-annual", "parse-kr-005930", "Samsung Electronics", "005930"),
        ("EU", "EU:BASF", "EU:BASF:2025-annual", "parse-eu-basf", "BASF", "BASF"),
        ("US", "US:0000320193", "US:AAPL:2025-10-K", "parse-us-aapl", "Apple", "AAPL"),
    ),
)
def test_chat_http_allows_exact_complete_non_cn_research_identity(
    monkeypatch,
    tmp_path,
    market,
    company_id,
    filing_id,
    parse_run_id,
    company_name,
    company_code,
):
    identity = {
        "market": market,
        "company_id": company_id,
        "filing_id": filing_id,
        "parse_run_id": parse_run_id,
    }
    task_id = {
        "HK": "00000000-0000-0000-0000-000000000001",
        "JP": "00000000-0000-0000-0000-000000000002",
        "KR": "00000000-0000-0000-0000-000000000003",
        "EU": "00000000-0000-0000-0000-000000000004",
        "US": "00000000-0000-0000-0000-000000000005",
    }[market]
    report_id = "2025-10-K" if market == "US" else "2025-annual"
    company_dir = tmp_path / "wiki" / "companies" / company_id.replace(":", "-")
    report_dir = company_dir / "reports" / report_id
    report_dir.mkdir(parents=True)
    (company_dir / "company.json").write_text(
        json.dumps(
            {
                "market": market,
                "company_id": company_id,
                "company_short_name": company_name,
                "stock_code": company_code,
                "reports": [
                    {
                        "report_id": report_id,
                        "filing_id": filing_id,
                        "parser_result_task_id": task_id,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (report_dir / "manifest.json").write_text(
        json.dumps({"filing_id": filing_id, "parse_run_id": parse_run_id}),
        encoding="utf-8",
    )

    def read_json(path):
        return json.loads(path.read_text(encoding="utf-8"))

    selected_report = wiki_context.primary_report_for_company(
        company_dir,
        "2025 annual revenue",
        local_citation_module=None,
        read_json_file=read_json,
        annual_terms=("annual",),
        quarterly_terms=("quarter",),
        research_identity=identity,
    )
    assert selected_report["selection_status"] == "identity_exact"
    assert selected_report["filing_id"] == filing_id
    assert selected_report["parse_run_id"] == parse_run_id
    assert selected_report["task_id"] == task_id

    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "chat-identity-positive-secret-with-enough-length")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'chat-identity-positive.db'}")
    original_overrides = main.app.dependency_overrides.copy()
    observed_run_inputs = []

    async def setup_database() -> tuple[int, str]:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with AsyncSession(engine) as session:
            user = await _add_user(session)
            return int(user.id), AuthService.create_access_token({"sub": str(user.id)})

    async def override_async_session():
        async with AsyncSession(engine) as session:
            yield session

    async def fake_update_agent_and_achievements(_session):
        return []

    async def fake_prepare_envelope(*_args, **_kwargs):
        return runtime.ChatRequestEnvelope(
            all_attachments=[],
            message_hash=f"hash-{market.lower()}-complete-identity",
            user_display_message="2025 年营业收入是多少？",
        )

    async def fake_preflight(*_args, **_kwargs):
        return runtime.ChatRunPreflightContext(history=[], local_memory_context=None, attachments=[])

    async def fake_wait_for_pdf_attachment_parses(_attachments):
        return None

    async def fake_analyze_images(*_args, **_kwargs):
        return None, True

    async def fake_refresh_session_memory(*_args, **_kwargs):
        return None

    def observable_build_hermes_run_input(message, **kwargs):
        run_identity = runtime.agent_runtime_context.research_identity(kwargs["context"])
        run_input = {"message": message, **kwargs, "research_identity": run_identity}
        observed_run_inputs.append(run_input)
        return run_input

    async def fake_create_run(run_input, history, *, profile, session_id):
        assert run_input["research_identity"] == identity
        assert run_input["context"]["research_identity"] == identity
        assert history == []
        assert profile == "siq_assistant"
        assert session_id == runtime.hermes_runs_session_id(
            "siq_assistant",
            f"user-{user_id}-assistant-positive-{market.lower()}",
        )
        return f"run-{market.lower()}-complete-identity"

    raw_reply = (
        f"{company_name} 2025 年营业收入为 100.00 亿元。\n\n"
        f"[P1] source_type=wiki_metrics market={market} company_id={company_id} "
        f"filing_id={filing_id} parse_run_id={parse_run_id} "
        "file=metrics/three_statements.json canonical_name=operating_revenue "
        "metric_name=营业收入 period_key=2025 pdf_page=1 table_index=1 md_line=1 "
        f"task_id={task_id} "
        f'value=100.00 unit=亿元 evidence_id=EVID-{market}-REV-2025 quote="营业收入 100.00"'
    )

    async def fake_collect_run_result(run_id, *, profile, timeout):
        assert run_id == f"run-{market.lower()}-complete-identity"
        assert profile == "siq_assistant"
        assert timeout == runtime.hermes_timeout()
        return raw_reply

    try:
        user_id, token = anyio.run(setup_database)
        session_id = f"user-{user_id}-assistant-positive-{market.lower()}"
        session_manager = _SessionManager()
        session_manager.get_current_session_id = lambda *_args, **_kwargs: session_id
        monkeypatch.setattr(chat, "get_session_manager", lambda: session_manager)
        monkeypatch.setattr(chat, "update_agent_and_achievements", fake_update_agent_and_achievements)
        monkeypatch.setattr(runtime, "_prepare_chat_request_envelope", fake_prepare_envelope)
        monkeypatch.setattr(runtime, "build_wiki_catalog_reply", lambda _message: None)
        monkeypatch.setattr(runtime, "_load_chat_run_preflight_context", fake_preflight)
        monkeypatch.setattr(runtime, "wait_for_pdf_attachment_parses", fake_wait_for_pdf_attachment_parses)
        monkeypatch.setattr(runtime, "_attachments_with_fresh_metadata", lambda attachments: attachments)
        monkeypatch.setattr(runtime, "refresh_session_memory", fake_refresh_session_memory)
        monkeypatch.setattr(runtime, "analyze_images_with_primary_model", fake_analyze_images)
        monkeypatch.setattr(runtime, "normalize_evidence_trace_for_display", lambda reply: reply)
        # This matrix isolates HTTP/auth/identity propagation. The source line is
        # still validated by the real guard and claim verifier below.
        monkeypatch.setattr(
            runtime.agent_runtime_financial_sources,
            "append_primary_data_evidence_if_needed",
            lambda _message, _context, reply, *, deps: reply,
        )
        monkeypatch.setattr(runtime, "_resolve_company_dirs", lambda *_args, **_kwargs: [company_dir])
        monkeypatch.setattr(runtime, "build_hermes_run_input", observable_build_hermes_run_input)
        monkeypatch.setattr(runtime, "create_run", fake_create_run)
        monkeypatch.setattr(runtime, "collect_run_result", fake_collect_run_result)
        monkeypatch.setattr(runtime, "_remember_completed_run", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(runtime, "_record_financial_llm_provenance_if_needed", lambda **_kwargs: None)
        monkeypatch.setattr(runtime.agent_memory_service, "context_from_session_id", lambda *_args, **_kwargs: None)
        main.app.dependency_overrides[get_async_session] = override_async_session
        monkeypatch.setenv(
            "SIQ_ANSWER_AUDIT_TRACE_LOG_PATH",
            str(tmp_path / f"{market.lower()}-identity-positive-audit.jsonl"),
        )
        audit.RECENT_ANSWER_AUDIT_TRACES.clear()

        headers = {"Authorization": f"Bearer {token}"}
        with TestClient(main.app) as client:
            response = client.post(
                "/api/chat",
                headers=headers,
                json={
                    "message": "2025 年营业收入是多少？",
                    "context": {
                        "research_identity": identity,
                        "company": {"name": company_name, "code": company_code},
                    },
                },
            )
            payload = response.json()
            history_response = client.get("/api/chat/history", headers=headers, params={"limit": 10})
            audit_response = client.get(
                f"/api/chat/audit-traces/{payload.get('audit_trace_id')}",
                headers=headers,
            )

        assert response.status_code == 200
        assert payload["reply"] == raw_reply
        assert payload["audit_trace_id"]
        assert len(observed_run_inputs) == 1
        assert observed_run_inputs[0]["research_identity"] == identity

        assert history_response.status_code == 200
        history = history_response.json()
        assert history["session_id"] == session_id
        assert [message["role"] for message in history["messages"]] == ["user", "assistant"]
        assert [message["research_identity"] for message in history["messages"]] == [
            identity,
            identity,
        ]
        assert history["messages"][-1]["content"] == raw_reply
        assert history["messages"][-1]["audit_trace_id"] == payload["audit_trace_id"]

        assert audit_response.status_code == 200
        trace = audit_response.json()["trace"]
        assert trace["resolved_company"] == {
            "id": company_id,
            "name": company_name,
            "code": company_code,
            "market": market,
        }
        assert trace["resolved_period"]["filing_id"] == filing_id
        assert trace["resolved_period"]["parse_run_id"] == parse_run_id
        assert trace["guardrail_result"]["output_was_guarded"] is False
        assert trace["guardrail_result"].get("blocked") is not True
        assert trace["claim_verifier_result"]["allowed"] is True

        async def inspect_database():
            async with AsyncSession(engine) as session:
                return (await session.exec(select(ChatMessage).order_by(ChatMessage.id))).all()

        persisted_messages = anyio.run(inspect_database)
        assert [message.role for message in persisted_messages] == ["user", "assistant"]
        assert persisted_messages[-1].content == raw_reply
        assert persisted_messages[-1].audit_trace_id == payload["audit_trace_id"]
        assert [json.loads(message.research_identity_json) for message in persisted_messages] == [
            identity,
            identity,
        ]
        persisted_trace = audit.get_answer_audit_trace(persisted_messages[-1].audit_trace_id)
        assert persisted_trace["resolved_company"] == trace["resolved_company"]
        assert persisted_trace["resolved_period"] == trace["resolved_period"]
        assert persisted_trace["resolved_company"]["id"] == identity["company_id"]
        assert persisted_trace["resolved_period"]["filing_id"] == identity["filing_id"]
        assert persisted_trace["resolved_period"]["parse_run_id"] == identity["parse_run_id"]
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(original_overrides)
        anyio.run(engine.dispose)


@pytest.mark.parametrize("market", ("HK", "JP", "KR", "EU", "US"))
def test_chat_http_financial_question_fail_closes_incomplete_non_cn_identity(monkeypatch, tmp_path, market):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "chat-identity-smoke-secret-with-enough-length")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / f'chat-{market.lower()}-identity.db'}")
    original_overrides = main.app.dependency_overrides.copy()
    calls = {"market_view": [], "legacy": 0}

    async def setup_database() -> tuple[int, str]:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with AsyncSession(engine) as session:
            user = await _add_user(session)
            return int(user.id), AuthService.create_access_token({"sub": str(user.id)})

    async def override_async_session():
        async with AsyncSession(engine) as session:
            yield session

    class FakeFinancialQueryModule:
        @staticmethod
        def merge_parse(_query_text, _strict):
            return {}

        @staticmethod
        def query_market_agent_view_result(query_text, parsed, company, *, limit, market=None):
            calls["market_view"].append(
                {
                    "query_text": query_text,
                    "parsed": dict(parsed),
                    "company": dict(company),
                    "limit": limit,
                    "market": market,
                }
            )
            return {"rows": []}

        @staticmethod
        def get_connection():
            calls["legacy"] += 1
            raise AssertionError("non-CN identity must not open the legacy A-share connection")

    async def fake_update_agent_and_achievements(_session):
        return []

    async def fake_prepare_envelope(*_args, **_kwargs):
        return runtime.ChatRequestEnvelope(
            all_attachments=[],
            message_hash=f"hash-{market.lower()}-incomplete-identity",
            user_display_message="2025 年营业收入是多少？",
        )

    async def fake_preflight(*_args, **_kwargs):
        return runtime.ChatRunPreflightContext(history=[], local_memory_context=None, attachments=[])

    async def fake_wait_for_pdf_attachment_parses(_attachments):
        return None

    async def fake_analyze_images(*_args, **_kwargs):
        return None, True

    async def fake_refresh_session_memory(*_args, **_kwargs):
        return None

    def fake_build_hermes_run_input(message, **kwargs):
        assert runtime.build_postgres_fallback_context(message, kwargs["context"]) is None
        return {"message": message, **kwargs}

    async def fake_create_run(run_input, history, *, profile, session_id):
        assert run_input["context"]["research_identity"] == {"market": market}
        assert history == []
        assert profile == "siq_assistant"
        assert session_id == runtime.hermes_runs_session_id(
            "siq_assistant",
            f"user-{user_id}-assistant-identity-{market.lower()}",
        )
        return f"run-{market.lower()}-incomplete-identity"

    async def fake_collect_run_result(run_id, *, profile, timeout):
        assert run_id == f"run-{market.lower()}-incomplete-identity"
        assert profile == "siq_assistant"
        assert timeout == runtime.hermes_timeout()
        return "2025 年营业收入为 100 亿元。"

    try:
        user_id, token = anyio.run(setup_database)
        session_manager = _SessionManager()
        session_manager.get_current_session_id = (
            lambda *_args, **_kwargs: f"user-{user_id}-assistant-identity-{market.lower()}"
        )
        monkeypatch.setattr(chat, "get_session_manager", lambda: session_manager)
        monkeypatch.setattr(chat, "update_agent_and_achievements", fake_update_agent_and_achievements)
        monkeypatch.setattr(runtime, "_prepare_chat_request_envelope", fake_prepare_envelope)
        monkeypatch.setattr(runtime, "build_wiki_catalog_reply", lambda _message: None)
        monkeypatch.setattr(runtime, "_load_chat_run_preflight_context", fake_preflight)
        monkeypatch.setattr(runtime, "wait_for_pdf_attachment_parses", fake_wait_for_pdf_attachment_parses)
        monkeypatch.setattr(runtime, "_attachments_with_fresh_metadata", lambda attachments: attachments)
        monkeypatch.setattr(runtime, "refresh_session_memory", fake_refresh_session_memory)
        monkeypatch.setattr(runtime, "analyze_images_with_primary_model", fake_analyze_images)
        monkeypatch.setattr(runtime, "normalize_evidence_trace_for_display", lambda reply: reply)
        monkeypatch.setattr(runtime, "build_hermes_run_input", fake_build_hermes_run_input)
        monkeypatch.setattr(runtime, "_load_financial_query_api", lambda: FakeFinancialQueryModule())
        monkeypatch.setattr(runtime.agent_memory_service, "context_from_session_id", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(runtime, "create_run", fake_create_run)
        monkeypatch.setattr(runtime, "collect_run_result", fake_collect_run_result)
        monkeypatch.setattr(runtime, "_remember_completed_run", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(runtime, "_record_financial_llm_provenance_if_needed", lambda **_kwargs: None)
        main.app.dependency_overrides[get_async_session] = override_async_session
        monkeypatch.setenv(
            "SIQ_ANSWER_AUDIT_TRACE_LOG_PATH",
            str(tmp_path / f"{market.lower()}-identity-audit.jsonl"),
        )
        audit.RECENT_ANSWER_AUDIT_TRACES.clear()

        with TestClient(main.app) as client:
            response = client.post(
                "/api/chat",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "message": "2025 年营业收入是多少？",
                    "context": {
                        "research_identity": {"market": market},
                        "company": {"name": f"{market} Test Company"},
                    },
                },
            )
            payload = response.json()
            audit_response = client.get(
                f"/api/chat/audit-traces/{payload.get('audit_trace_id')}",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert response.status_code == 200
        assert payload["reply"].startswith("## 研究身份不完整")
        assert f"identity_market={market}" in payload["reply"]
        assert "identity_missing_fields=company_id,filing_id,parse_run_id" in payload["reply"]
        assert "guardrail_reason=financial_research_identity_incomplete" in payload["reply"]
        assert "100 亿元" not in payload["reply"]
        assert calls["legacy"] == 0
        assert calls["market_view"] == []

        assert audit_response.status_code == 200
        trace = audit_response.json()["trace"]
        assert trace["guardrail_result"]["blocked"] is True
        assert trace["guardrail_result"]["reason"] == "financial_research_identity_incomplete"
        assert trace["fallback_reason"] == "research_identity_incomplete"
        assert any(
            event.get("reason") == "research_identity_incomplete"
            and event.get("stage") == "market_agent_view_skipped_for_incomplete_identity"
            for event in trace["fallback_events"]
        )
        assert any(
            event.get("reason") == "market_boundary_closed"
            and event.get("stage") == "legacy_fallback_skipped_for_non_cn_market"
            for event in trace["fallback_events"]
        )
        assert trace["fallback_events"][-1]["reason"] == "research_identity_incomplete"
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(original_overrides)
        anyio.run(engine.dispose)


def test_chat_stream_records_usage_before_returning_sse(monkeypatch, tmp_path):
    async def run_case(session):
        user = await _add_user(session)
        monkeypatch.setattr(chat, "get_session_manager", lambda: _SessionManager())

        response = await chat.chat_stream(
            ChatRequest(message="请分析财报"),
            request=object(),
            current_user=user,
            async_session=session,
        )

        usage = (await session.exec(select(UsageEvent).where(UsageEvent.event_type == AGENT_QUESTION_EVENT))).one()

        assert response is not None
        assert usage.user_id == user.id
        assert usage.source == "assistant"

    anyio.run(_with_chat_db, tmp_path, run_case)


def test_chat_answer_audit_trace_route_requires_current_user_session(monkeypatch):
    trace = audit.record_answer_audit_trace(
        audit.build_answer_audit_trace(
            message="question_id=q-route 收入是多少？",
            final_reply="[D1] source_type=wiki_metrics, metric=收入",
            profile="siq_assistant",
            session_id="user-7-assistant-session",
        ),
        log_path="/tmp/siq-test-answer-audit-trace-route.jsonl",
    )
    user = User(
        id=7,
        username="analyst",
        email="analyst@example.test",
        full_name="Analyst",
        hashed_password="x",
        role=UserRole.ANALYST,
        is_active=True,
        approval_status="approved",
    )

    response = anyio.run(chat.get_chat_answer_audit_trace, trace["trace_id"], user)

    assert response["trace_id"] == trace["trace_id"]
    assert response["trace"]["session_id"] == "user-7-assistant-session"


def test_chat_answer_audit_trace_route_hides_other_users_trace():
    trace = audit.record_answer_audit_trace(
        audit.build_answer_audit_trace(
            message="question_id=q-route-other 收入是多少？",
            final_reply="[D1] source_type=wiki_metrics, metric=收入",
            profile="siq_assistant",
            session_id="user-8-assistant-session",
        ),
        log_path="/tmp/siq-test-answer-audit-trace-route-other.jsonl",
    )
    user = User(
        id=7,
        username="analyst",
        email="analyst@example.test",
        full_name="Analyst",
        hashed_password="x",
        role=UserRole.ANALYST,
        is_active=True,
        approval_status="approved",
    )

    with pytest.raises(chat.HTTPException) as exc_info:
        anyio.run(chat.get_chat_answer_audit_trace, trace["trace_id"], user)

    assert exc_info.value.status_code == 404
