import json

import anyio
import pytest
from models import ChatMessage
from services.agent_runtime_message_identity import encode_research_identity_snapshot
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from services import agent_chat_runtime as runtime, agent_runtime_history, hermes_client


def test_normalize_history_drops_diagnostic_only_assistant_message():
    diagnostic = "## 计算校验无效\nguardrail_status=warning\nguardrail_reason=financial_calculation_trace_missing"
    messages = [
        ChatMessage(session_id="diagnostic-history", role="user", content="旧问题"),
        ChatMessage(session_id="diagnostic-history", role="assistant", content=diagnostic),
        ChatMessage(session_id="diagnostic-history", role="user", content="新问题"),
        ChatMessage(session_id="diagnostic-history", role="assistant", content="新回答"),
    ]

    history = runtime.normalize_history(messages, limit=10)

    assert history == [
        {"role": "user", "content": "新问题"},
        {"role": "assistant", "content": "新回答"},
    ]
    assert all(item["content"] for item in history)


def test_normalize_history_odd_limit_keeps_only_complete_turns():
    messages = [
        ChatMessage(session_id="odd-limit", role="user", content="旧问题"),
        ChatMessage(session_id="odd-limit", role="assistant", content="旧回答"),
        ChatMessage(session_id="odd-limit", role="user", content="新问题"),
        ChatMessage(session_id="odd-limit", role="assistant", content="新回答"),
    ]

    assert runtime.normalize_history(messages, limit=3) == [
        {"role": "user", "content": "新问题"},
        {"role": "assistant", "content": "新回答"},
    ]
    assert runtime.normalize_history(messages, limit=1) == []


def test_normalize_history_scope_drops_half_scoped_and_invisible_pairs():
    target = {
        "market": "US",
        "company_id": "US:0000320193",
        "filing_id": "US:AAPL:2025-10-K",
        "parse_run_id": "parse-aapl-2025",
    }

    def message(role: str, content: str, identity=None) -> ChatMessage:
        return ChatMessage(
            session_id="half-scoped-history",
            role=role,
            content=content,
            research_identity_json=encode_research_identity_snapshot(identity),
        )

    history = runtime.normalize_history(
        [
            message("user", "matched user with missing assistant identity", target),
            message("assistant", "missing assistant identity"),
            message("user", "missing user identity"),
            message("assistant", "matched assistant with missing user identity", target),
            message("user", "complete user", target),
            message("assistant", "complete assistant", target),
            message("user", "user with invisible assistant", target),
            message("assistant", "", target),
        ],
        limit=9,
        research_identity_scope=target,
    )

    assert history == [
        {"role": "user", "content": "complete user"},
        {"role": "assistant", "content": "complete assistant"},
    ]


def test_load_history_applies_normalize_history_contract_to_real_db_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "CHAT_UPLOAD_ROOT", tmp_path)
    attachment_path = tmp_path / "chart.png"
    attachment_path.write_bytes(b"fake-image")
    attachment = {
        "filename": "chart.png",
        "content_type": "image/png",
        "kind": "image",
        "size": attachment_path.stat().st_size,
        "path": str(attachment_path),
        "url": "/api/chat/attachments/chart.png",
    }
    polluted = (
        runtime.LEGACY_HISTORY_LOOP_SANITIZED_PREFIX
        + "\nsource_type=wiki_document_links, file=semantic/document_links.json"
    )

    async def run_case():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'history-contract.db'}")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(SQLModel.metadata.create_all)
            async with AsyncSession(engine) as async_session:
                session_id = "history-contract-session"
                async_session.add_all(
                    [
                        ChatMessage(session_id="foreign-history-session", role="user", content="foreign question"),
                        ChatMessage(session_id=session_id, role="assistant", content="leading assistant removed"),
                        ChatMessage(session_id=session_id, role="user", content=""),
                        ChatMessage(session_id=session_id, role="tool", content="tool row ignored"),
                        ChatMessage(session_id=session_id, role="user", content="旧问题 1"),
                        ChatMessage(session_id=session_id, role="assistant", content="旧回答 1"),
                        ChatMessage(session_id=session_id, role="assistant", content="旧回答 2"),
                        ChatMessage(
                            session_id=session_id,
                            role="user",
                            content="",
                            attachments_json=json.dumps([attachment], ensure_ascii=False),
                        ),
                        ChatMessage(session_id=session_id, role="assistant", content=polluted),
                        ChatMessage(session_id=session_id, role="assistant", content="附件回答"),
                        ChatMessage(session_id=session_id, role="user", content="连续用户 1"),
                        ChatMessage(session_id=session_id, role="user", content="连续用户 2"),
                        ChatMessage(session_id=session_id, role="assistant", content="最终回答"),
                    ]
                )
                await async_session.commit()

                history = await runtime.load_history(async_session, session_id, limit=10)
                limited = await runtime.load_history(async_session, session_id, limit=2)
                return history, limited
        finally:
            await engine.dispose()

    history, limited = anyio.run(run_case)

    assert [item["role"] for item in history] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert history[0]["content"] == "旧问题 1"
    assert history[1]["content"] == "旧回答 2"
    assert "历史附件上下文" in history[2]["content"]
    assert "chart.png" in history[2]["content"]
    assert str(attachment_path.resolve()) in history[2]["content"]
    assert history[3]["content"] == "附件回答"
    assert history[4]["content"] == "连续用户 2"
    assert history[5]["content"] == "最终回答"

    joined = "\n".join(item["content"] for item in history)
    assert "foreign question" not in joined
    assert "leading assistant removed" not in joined
    assert "tool row ignored" not in joined
    assert "旧回答 1" not in joined
    assert "连续用户 1" not in joined
    assert runtime.LEGACY_HISTORY_LOOP_SANITIZED_PREFIX not in joined
    assert "source_type=wiki_document_links" not in joined
    assert limited == [
        {"role": "user", "content": "连续用户 2"},
        {"role": "assistant", "content": "最终回答"},
    ]


def test_new_user_pool_session_sends_isolated_namespace_without_foreign_history(
    tmp_path,
    monkeypatch,
):
    company_scope = {"market": "CN", "company_id": "600104"}
    foreign_session_id = "user-7-analysis-old"
    current_session_id = "user-8-analysis-new"
    lease_namespace = (
        "siq:openshell:pool:scope:canary-0123456789ab:siq_analysis:"
        "t111111111111:u888888888888:s999999999999"
    )
    captured = {}

    async def fake_create_run(
        run_input,
        conversation_history,
        *,
        profile,
        session_id,
        route,
    ):
        captured.update(
            {
                "run_input": run_input,
                "conversation_history": conversation_history,
                "profile": profile,
                "session_id": session_id,
                "route": route,
            }
        )
        return "hermes-current-user-run"

    async def run_case():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pool-user-history.db'}")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(SQLModel.metadata.create_all)
            async with AsyncSession(engine) as async_session:
                encoded_scope = encode_research_identity_snapshot(company_scope)
                async_session.add_all(
                    [
                        ChatMessage(
                            session_id=foreign_session_id,
                            role="user",
                            content="other user private question",
                            research_identity_json=encoded_scope,
                        ),
                        ChatMessage(
                            session_id=foreign_session_id,
                            role="assistant",
                            content="other user private answer",
                            research_identity_json=encoded_scope,
                        ),
                    ]
                )
                await async_session.commit()
                preflight = await runtime._load_chat_run_preflight_context(
                    async_session,
                    session_id=current_session_id,
                    profile="siq_analysis",
                    attachments=[],
                    history_limit=10,
                    isolate_runtime_context=True,
                    research_identity_scope=company_scope,
                )
                route = hermes_client.HermesRunRoute(
                    target="openshell",
                    base="http://127.0.0.1:28652/v1/runs",
                    model="siq_analysis",
                    authorization="Bearer " + "a" * 64,
                    session_namespace=lease_namespace,
                    canary_run_id="canary-0123456789ab",
                    pool_lease_id="lease-" + "1" * 32,
                    pool_tenant_id="default",
                    pool_user_id="8",
                )
                monkeypatch.setattr(runtime, "create_run", fake_create_run)
                result = await runtime._create_routed_run(
                    "current user question",
                    preflight.history,
                    profile="siq_analysis",
                    session_id=current_session_id,
                    route=route,
                )
                return preflight, route, result
        finally:
            await engine.dispose()

    preflight, route, result = anyio.run(run_case)

    assert preflight.history == []
    assert preflight.local_memory_context is None
    assert preflight.allow_initialize is True
    assert result == ("hermes-current-user-run", route)
    assert captured["conversation_history"] == []
    assert captured["session_id"] == lease_namespace
    assert captured["session_id"] != foreign_session_id
    assert foreign_session_id not in json.dumps(captured, ensure_ascii=False, default=str)


def test_history_scope_requires_canonical_company_identity():
    scope = agent_runtime_history.normalize_history_scope(
        {
            "market": " us_sec ",
            "company_id": "US:0000320193",
            "filing_id": "US:AAPL:2025-10-K",
            "parse_run_id": "parse-aapl-2025",
        }
    )

    assert scope == {
        "market": "US",
        "company_id": "US:0000320193",
        "filing_id": "US:AAPL:2025-10-K",
        "parse_run_id": "parse-aapl-2025",
    }
    with pytest.raises(ValueError, match="history_scope_requires_market_and_company_id"):
        agent_runtime_history.normalize_history_scope({"market": "US"})


def test_history_scope_keeps_company_pairs_and_can_narrow_to_one_research_identity():
    apple_2025 = {
        "market": "US",
        "company_id": "US:0000320193",
        "filing_id": "US:AAPL:2025-10-K",
        "parse_run_id": "parse-aapl-2025",
    }
    apple_2024 = {
        **apple_2025,
        "filing_id": "US:AAPL:2024-10-K",
        "parse_run_id": "parse-aapl-2024",
    }
    microsoft = {
        "market": "US",
        "company_id": "US:0000789019",
        "filing_id": "US:MSFT:2025-10-K",
        "parse_run_id": "parse-msft-2025",
    }

    def message(role: str, content: str, identity=None) -> ChatMessage:
        return ChatMessage(
            session_id="scoped-history",
            role=role,
            content=content,
            research_identity_json=encode_research_identity_snapshot(identity),
        )

    messages = [
        message("user", "legacy user"),
        message("assistant", "legacy assistant"),
        message("user", "apple 2025 user", apple_2025),
        message("assistant", "apple 2025 assistant", apple_2025),
        message("user", "microsoft user", microsoft),
        message("assistant", "microsoft assistant", microsoft),
        message("user", "apple 2024 user", apple_2024),
        message("assistant", "apple 2024 assistant", apple_2024),
    ]

    company_messages = agent_runtime_history.filter_history_by_research_identity(
        messages,
        research_identity_scope={"market": "us", "company_id": "US:0000320193"},
    )
    document_messages = agent_runtime_history.filter_history_by_research_identity(
        messages,
        research_identity_scope=apple_2025,
    )
    normalized_document_history = agent_runtime_history.normalize_history(
        messages,
        limit=10,
        research_identity_scope=apple_2025,
        chat_message_has_visible_payload=lambda item: bool(item.content),
        message_attachments=lambda _item: [],
        attachment_reference_context=lambda _attachments: "",
        is_loop_polluted_assistant_message=lambda _content: False,
        normalize_evidence_trace_for_display=lambda content: str(content or ""),
        sanitize_assistant_history_reply=lambda content: content,
    )

    assert [(item.role, item.content) for item in company_messages] == [
        ("user", "apple 2025 user"),
        ("assistant", "apple 2025 assistant"),
        ("user", "apple 2024 user"),
        ("assistant", "apple 2024 assistant"),
    ]
    assert [(item.role, item.content) for item in document_messages] == [
        ("user", "apple 2025 user"),
        ("assistant", "apple 2025 assistant"),
    ]
    assert normalized_document_history == [
        {"role": "user", "content": "apple 2025 user"},
        {"role": "assistant", "content": "apple 2025 assistant"},
    ]
    assert all(set(item) == {"role", "content"} for item in normalized_document_history)


def test_scoped_load_history_excludes_legacy_and_foreign_turns_without_losing_older_pair(tmp_path):
    target = {
        "market": "HK",
        "company_id": "HK:00700",
        "filing_id": "HK:00700:2025-annual",
        "parse_run_id": "parse-hk-00700-2025",
    }
    foreign = {
        "market": "HK",
        "company_id": "HK:00005",
        "filing_id": "HK:00005:2025-annual",
        "parse_run_id": "parse-hk-00005-2025",
    }

    async def run_case():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'scoped-history.db'}")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(SQLModel.metadata.create_all)
            async with AsyncSession(engine) as async_session:
                session_id = "mixed-company-session"
                async_session.add_all(
                    [
                        ChatMessage(
                            session_id=session_id,
                            role="user",
                            content="half target user",
                            research_identity_json=encode_research_identity_snapshot(target),
                        ),
                        ChatMessage(session_id=session_id, role="assistant", content="half legacy assistant"),
                        ChatMessage(session_id=session_id, role="user", content="half legacy user"),
                        ChatMessage(
                            session_id=session_id,
                            role="assistant",
                            content="half target assistant",
                            research_identity_json=encode_research_identity_snapshot(target),
                        ),
                        ChatMessage(
                            session_id=session_id,
                            role="user",
                            content="target user",
                            research_identity_json=encode_research_identity_snapshot(target),
                        ),
                        ChatMessage(
                            session_id=session_id,
                            role="assistant",
                            content="target assistant",
                            research_identity_json=encode_research_identity_snapshot(target),
                        ),
                        ChatMessage(session_id=session_id, role="user", content="legacy user"),
                        ChatMessage(session_id=session_id, role="assistant", content="legacy assistant"),
                        *[
                            ChatMessage(
                                session_id=session_id,
                                role="user" if index % 2 == 0 else "assistant",
                                content=f"foreign {index}",
                                research_identity_json=encode_research_identity_snapshot(foreign),
                            )
                            for index in range(12)
                        ],
                    ]
                )
                await async_session.commit()

                return await agent_runtime_history.load_history(
                    async_session,
                    session_id,
                    limit=2,
                    research_identity_scope=target,
                    normalize_messages=lambda messages: runtime.normalize_history(messages, limit=2),
                )
        finally:
            await engine.dispose()

    history = anyio.run(run_case)

    assert history == [
        {"role": "user", "content": "target user"},
        {"role": "assistant", "content": "target assistant"},
    ]
    assert all(set(item) == {"role", "content"} for item in history)
