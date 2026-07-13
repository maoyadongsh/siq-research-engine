import json

import anyio
from models import ChatMessage
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from services import agent_chat_runtime as runtime


def test_normalize_history_drops_diagnostic_only_assistant_message():
    diagnostic = (
        "## 计算校验无效\n"
        "guardrail_status=warning\n"
        "guardrail_reason=financial_calculation_trace_missing"
    )
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
