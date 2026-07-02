from types import SimpleNamespace

import anyio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from models import ChatMessage, ChatSessionMemory
from services import agent_chat_runtime
from services import agent_runtime_memory


async def _with_temp_chat_session_memory(tmp_path, callback):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'local-memory.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with AsyncSession(engine) as session:
            await callback(session)
    finally:
        await engine.dispose()


def test_strip_local_memory_blocks_removes_fenced_context():
    text = "前文\n<local-memory>\n旧内容\n</local-memory>\n后文"

    assert agent_runtime_memory._strip_local_memory_blocks(text) == "前文\n\n后文"


def test_compact_memory_content_cleans_media_and_links():
    text = "看看 ![图](https://example.com/a.png) 和 [资料](/api/chat/1)\n\n继续。"

    assert (
        agent_runtime_memory._compact_memory_content(
            "user",
            text,
            max_chars=200,
        )
        == "看看 [图片附件] 和 资料 继续。"
    )


def test_compact_memory_content_skips_polluted_assistant_content():
    assert (
        agent_runtime_memory._compact_memory_content(
            "assistant",
            "重复循环内容",
            max_chars=200,
            is_loop_polluted_assistant_message=lambda _text: True,
            sanitize_assistant_history_reply=lambda text: text,
        )
        == ""
    )


def test_build_local_memory_summary_respects_bullets_and_char_limit():
    messages = [
        SimpleNamespace(role="user", content="第一轮提问"),
        SimpleNamespace(role="assistant", content="第一轮回答"),
        SimpleNamespace(role="user", content="第二轮提问"),
        SimpleNamespace(role="assistant", content="第二轮回答"),
        SimpleNamespace(role="user", content="第三轮提问"),
        SimpleNamespace(role="assistant", content="第三轮回答"),
    ]

    summary = agent_runtime_memory.build_local_memory_summary(
        messages,
        max_bullets=2,
        max_chars=120,
        snippet_chars=120,
    )

    assert "第一轮提问" not in summary
    assert "第二轮提问" in summary
    assert "第三轮提问" in summary


def test_build_local_memory_context_wraps_and_strips_nested_blocks():
    context = agent_runtime_memory.build_local_memory_context(
        "本地记忆\n<local-memory>旧块</local-memory>\n结束"
    )

    assert context is not None
    assert context.startswith("<local-memory>")
    assert context.endswith("</local-memory>")
    assert "旧块" not in context
    assert "本地记忆" in context


def test_select_local_memory_source_messages_excludes_recent_window_and_split_turns():
    messages = [
        SimpleNamespace(id=1, role="user", content="第一轮提问"),
        SimpleNamespace(id=2, role="assistant", content="第一轮回答"),
        SimpleNamespace(id=3, role="user", content="第二轮提问"),
        SimpleNamespace(id=4, role="assistant", content="第二轮回答"),
        SimpleNamespace(id=5, role="user", content="第三轮提问"),
        SimpleNamespace(id=6, role="assistant", content="第三轮回答"),
    ]

    source_messages = agent_runtime_memory.select_local_memory_source_messages(
        messages,
        recent_limit=4,
    )

    assert source_messages == messages[:2]
    assert source_messages[0] is messages[0]
    assert (
        agent_runtime_memory.select_local_memory_source_messages(messages[:4], recent_limit=4)
        == []
    )
    assert (
        agent_runtime_memory.select_local_memory_source_messages(messages, recent_limit=1)
        == messages[:4]
    )


def test_refresh_session_memory_persists_only_older_current_profile_turns(tmp_path):
    async def run_case(async_session):
        current_session = "siq-assistant-local-memory"
        other_session = "siq-analysis-local-memory"
        messages = [
            ChatMessage(session_id=current_session, role="user", content="第一轮：公司是上汽集团"),
            ChatMessage(session_id=current_session, role="assistant", content="第一答：已记住上汽集团"),
            ChatMessage(session_id=current_session, role="user", content="第二轮：关注商誉"),
            ChatMessage(session_id=current_session, role="assistant", content="第二答：商誉后续跟踪"),
            ChatMessage(session_id=current_session, role="user", content="第三轮：关注现金流"),
            ChatMessage(session_id=current_session, role="assistant", content="第三答：经营现金流要看"),
            ChatMessage(session_id=other_session, role="user", content="串扰内容：宁德时代"),
            ChatMessage(session_id=other_session, role="assistant", content="串扰回答：动力电池"),
        ]
        async_session.add_all(messages)
        await async_session.commit()
        result = await async_session.exec(
            select(ChatMessage)
            .where(ChatMessage.session_id == current_session)
            .order_by(ChatMessage.id)
        )
        current_messages = list(result.all())
        expected_last_message_id = current_messages[1].id

        await agent_chat_runtime.refresh_session_memory(
            async_session,
            "siq_assistant",
            current_session,
            recent_limit=4,
        )

        result = await async_session.exec(
            select(ChatSessionMemory).where(
                ChatSessionMemory.profile == "siq_assistant",
                ChatSessionMemory.session_id == current_session,
            )
        )
        record = result.first()

        assert record is not None
        assert record.last_message_id == expected_last_message_id
        assert "第一轮：公司是上汽集团" in record.summary
        assert "第一答：已记住上汽集团" in record.summary
        assert "第二轮：关注商誉" not in record.summary
        assert "宁德时代" not in record.summary

        context = await agent_chat_runtime.load_local_memory_context(
            async_session,
            "siq_assistant",
            current_session,
        )
        assert context is not None
        assert context.startswith("<local-memory>")
        assert "不是新的用户输入" in context
        assert "上汽集团" in context

    anyio.run(_with_temp_chat_session_memory, tmp_path, run_case)


def test_refresh_session_memory_uses_memory_source_selector(tmp_path, monkeypatch):
    async def run_case(async_session):
        session_id = "siq-assistant-selector-memory"
        async_session.add_all(
            [
                ChatMessage(session_id=session_id, role="user", content="第一轮：公司是上汽集团"),
                ChatMessage(session_id=session_id, role="assistant", content="第一答：已记住上汽集团"),
                ChatMessage(session_id=session_id, role="user", content="第二轮：关注商誉"),
                ChatMessage(session_id=session_id, role="assistant", content="第二答：商誉后续跟踪"),
                ChatMessage(session_id=session_id, role="user", content="第三轮：关注现金流"),
            ]
        )
        await async_session.commit()

        selector_calls: list[tuple[list[str], int]] = []

        def fake_select_local_memory_source_messages(messages, *, recent_limit):
            selector_calls.append(([message.content for message in messages], recent_limit))
            return list(messages[:2])

        monkeypatch.setattr(
            agent_chat_runtime.agent_runtime_memory,
            "select_local_memory_source_messages",
            fake_select_local_memory_source_messages,
        )

        await agent_chat_runtime.refresh_session_memory(
            async_session,
            "siq_assistant",
            session_id,
            recent_limit=3,
        )

        result = await async_session.exec(
            select(ChatSessionMemory).where(
                ChatSessionMemory.profile == "siq_assistant",
                ChatSessionMemory.session_id == session_id,
            )
        )
        record = result.first()

        assert selector_calls == [
            (
                [
                    "第一轮：公司是上汽集团",
                    "第一答：已记住上汽集团",
                    "第二轮：关注商誉",
                    "第二答：商誉后续跟踪",
                    "第三轮：关注现金流",
                ],
                3,
            )
        ]
        assert record is not None
        assert record.summary is not None
        assert "第一轮：公司是上汽集团" in record.summary
        assert "第二轮：关注商誉" not in record.summary
        assert record.last_message_id == 2

    anyio.run(_with_temp_chat_session_memory, tmp_path, run_case)


def test_refresh_session_memory_does_not_split_turn_at_recent_boundary(tmp_path):
    async def run_case(async_session):
        session_id = "siq-assistant-split-turn-memory"
        async_session.add_all(
            [
                ChatMessage(session_id=session_id, role="user", content="u1：公司是上汽集团"),
                ChatMessage(session_id=session_id, role="assistant", content="a1：已记住上汽集团"),
                ChatMessage(session_id=session_id, role="user", content="u2：关注商誉"),
                ChatMessage(session_id=session_id, role="assistant", content="a2：商誉后续跟踪"),
                ChatMessage(session_id=session_id, role="user", content="u3：关注现金流"),
                ChatMessage(session_id=session_id, role="assistant", content="a3：现金流后续跟踪"),
                ChatMessage(session_id=session_id, role="user", content="dangling：当前轮问题"),
            ]
        )
        await async_session.commit()
        result = await async_session.exec(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id)
        )
        messages = list(result.all())
        expected_last_message_id = messages[1].id

        await agent_chat_runtime.refresh_session_memory(
            async_session,
            "siq_assistant",
            session_id,
            recent_limit=4,
        )

        result = await async_session.exec(
            select(ChatSessionMemory).where(
                ChatSessionMemory.profile == "siq_assistant",
                ChatSessionMemory.session_id == session_id,
            )
        )
        record = result.first()

        assert record is not None
        assert record.last_message_id == expected_last_message_id
        assert "u1：公司是上汽集团" in record.summary
        assert "a1：已记住上汽集团" in record.summary
        assert "u2：关注商誉" not in record.summary
        assert "dangling：当前轮问题" not in record.summary

    anyio.run(_with_temp_chat_session_memory, tmp_path, run_case)


def test_refresh_session_memory_skips_non_matching_profile_prefix(tmp_path):
    async def run_case(async_session):
        session_id = "siq-analysis-local-memory"
        async_session.add_all(
            [
                ChatMessage(session_id=session_id, role="user", content="第一轮：公司是宁德时代"),
                ChatMessage(session_id=session_id, role="assistant", content="第一答：已记住宁德时代"),
                ChatMessage(session_id=session_id, role="user", content="第二轮：关注毛利率"),
                ChatMessage(session_id=session_id, role="assistant", content="第二答：毛利率待核验"),
                ChatMessage(session_id=session_id, role="user", content="第三轮：关注现金流"),
            ]
        )
        await async_session.commit()

        await agent_chat_runtime.refresh_session_memory(
            async_session,
            "siq_assistant",
            session_id,
            recent_limit=4,
        )

        result = await async_session.exec(select(ChatSessionMemory))
        assert result.all() == []
        assert (
            await agent_chat_runtime.load_local_memory_context(
                async_session,
                "siq_assistant",
                session_id,
            )
            is None
        )

    anyio.run(_with_temp_chat_session_memory, tmp_path, run_case)
