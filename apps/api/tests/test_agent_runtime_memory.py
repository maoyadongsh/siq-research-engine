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


def test_refresh_session_memory_uses_runtime_record_and_summary_patch_points(tmp_path, monkeypatch):
    async def run_case(async_session):
        session_id = "siq-assistant-wrapper-memory"
        async_session.add_all(
            [
                ChatMessage(session_id=session_id, role="user", content="第一轮：上汽集团"),
                ChatMessage(session_id=session_id, role="assistant", content="第一答：已记住"),
                ChatMessage(session_id=session_id, role="user", content="第二轮：商誉"),
                ChatMessage(session_id=session_id, role="assistant", content="第二答：继续跟踪"),
            ]
        )
        await async_session.commit()

        record_calls: list[tuple[str, str]] = []
        summary_calls: list[list[str]] = []

        async def fake_load_session_memory_record(_async_session, profile, current_session_id):
            record_calls.append((profile, current_session_id))
            return None

        def fake_build_local_memory_summary(messages):
            summary_calls.append([message.content for message in messages])
            return "patched memory summary"

        monkeypatch.setattr(
            agent_chat_runtime,
            "_load_session_memory_record",
            fake_load_session_memory_record,
        )
        monkeypatch.setattr(
            agent_chat_runtime,
            "build_local_memory_summary",
            fake_build_local_memory_summary,
        )

        await agent_chat_runtime.refresh_session_memory(
            async_session,
            "siq_assistant",
            session_id,
            recent_limit=2,
        )

        result = await async_session.exec(
            select(ChatSessionMemory).where(
                ChatSessionMemory.profile == "siq_assistant",
                ChatSessionMemory.session_id == session_id,
            )
        )
        record = result.first()

        assert record_calls == [("siq_assistant", session_id)]
        assert summary_calls == [["第一轮：上汽集团", "第一答：已记住"]]
        assert record is not None
        assert record.summary == "patched memory summary"
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


def test_refresh_session_memory_isolates_same_profile_sessions(tmp_path):
    async def run_case(async_session):
        session_a = "siq-assistant-session-a"
        session_b = "siq-assistant-session-b"
        async_session.add_all(
            [
                ChatMessage(session_id=session_a, role="user", content="A1：上汽集团"),
                ChatMessage(session_id=session_a, role="assistant", content="A1答：已记住 A"),
                ChatMessage(session_id=session_a, role="user", content="A2：商誉"),
                ChatMessage(session_id=session_a, role="assistant", content="A2答：商誉继续跟踪"),
                ChatMessage(session_id=session_a, role="user", content="A3：现金流"),
                ChatMessage(session_id=session_a, role="assistant", content="A3答：现金流继续跟踪"),
                ChatMessage(session_id=session_b, role="user", content="B1：宁德时代"),
                ChatMessage(session_id=session_b, role="assistant", content="B1答：已记住 B"),
                ChatMessage(session_id=session_b, role="user", content="B2：现金流"),
                ChatMessage(session_id=session_b, role="assistant", content="B2答：现金流继续跟踪"),
                ChatMessage(session_id=session_b, role="user", content="B3：商誉"),
                ChatMessage(session_id=session_b, role="assistant", content="B3答：商誉继续跟踪"),
            ]
        )
        await async_session.commit()

        await agent_chat_runtime.refresh_session_memory(
            async_session,
            "siq_assistant",
            session_a,
            recent_limit=2,
        )

        result_a = await async_session.exec(
            select(ChatSessionMemory).where(
                ChatSessionMemory.profile == "siq_assistant",
                ChatSessionMemory.session_id == session_a,
            )
        )
        record_a = result_a.first()
        result_b = await async_session.exec(
            select(ChatSessionMemory).where(
                ChatSessionMemory.profile == "siq_assistant",
                ChatSessionMemory.session_id == session_b,
            )
        )
        record_b = result_b.first()

        assert record_a is not None
        assert record_b is None
        assert "A1：上汽集团" in record_a.summary
        assert "B1：宁德时代" not in record_a.summary
        assert (
            await agent_chat_runtime.load_local_memory_context(
                async_session,
                "siq_assistant",
                session_a,
            )
        ) is not None
        assert (
            await agent_chat_runtime.load_local_memory_context(
                async_session,
                "siq_assistant",
                session_b,
            )
        ) is None

    anyio.run(_with_temp_chat_session_memory, tmp_path, run_case)


def test_refresh_session_memory_keeps_foreign_profile_record_isolated_for_same_session_id(tmp_path):
    async def run_case(async_session):
        session_id = "siq-assistant-foreign-record"
        async_session.add_all(
            [
                ChatMessage(session_id=session_id, role="user", content="当前会话：上汽集团"),
                ChatMessage(session_id=session_id, role="assistant", content="当前会话答：已记住"),
                ChatMessage(session_id=session_id, role="user", content="当前会话：商誉"),
                ChatMessage(session_id=session_id, role="assistant", content="当前会话答：商誉继续跟踪"),
                ChatMessage(session_id=session_id, role="user", content="当前会话：现金流"),
                ChatMessage(session_id=session_id, role="assistant", content="当前会话答：现金流继续跟踪"),
                ChatSessionMemory(
                    profile="siq_analysis",
                    session_id=session_id,
                    summary="foreign sentinel",
                    last_message_id=999,
                ),
            ]
        )
        await async_session.commit()

        await agent_chat_runtime.refresh_session_memory(
            async_session,
            "siq_assistant",
            session_id,
            recent_limit=2,
        )

        result = await async_session.exec(
            select(ChatSessionMemory).where(ChatSessionMemory.session_id == session_id)
        )
        records = result.all()

        assistant_record = next(record for record in records if record.profile == "siq_assistant")
        analysis_record = next(record for record in records if record.profile == "siq_analysis")

        assert assistant_record.summary is not None
        assert "当前会话：上汽集团" in assistant_record.summary
        assert analysis_record.summary == "foreign sentinel"
        assert analysis_record.last_message_id == 999

    anyio.run(_with_temp_chat_session_memory, tmp_path, run_case)


def test_ensure_local_memory_context_refreshes_and_respects_profile_prefix(tmp_path):
    async def run_case(async_session):
        matching_session = "siq-assistant-prefix-gate"
        foreign_session = "siq-analysis-prefix-gate"
        async_session.add_all(
            [
                *[
                    item
                    for i in range(1, 14)
                    for item in (
                        ChatMessage(session_id=matching_session, role="user", content=f"匹配会话 {i}"),
                        ChatMessage(session_id=matching_session, role="assistant", content=f"匹配会话答 {i}"),
                    )
                ],
                ChatMessage(session_id=foreign_session, role="user", content="外部会话旧问题 1"),
                ChatMessage(session_id=foreign_session, role="assistant", content="外部会话旧回答 1"),
            ]
        )
        await async_session.commit()

        matching_context = await agent_chat_runtime.ensure_local_memory_context(
            async_session,
            "siq_assistant",
            matching_session,
        )
        foreign_context = await agent_chat_runtime.ensure_local_memory_context(
            async_session,
            "siq_assistant",
            foreign_session,
        )

        result = await async_session.exec(select(ChatSessionMemory))
        records = result.all()

        assert matching_context is not None
        assert matching_context.startswith("<local-memory>")
        assert "匹配会话 1" in matching_context
        assert foreign_context is None
        assert len(records) == 1
        assert records[0].profile == "siq_assistant"
        assert records[0].session_id == matching_session

    anyio.run(_with_temp_chat_session_memory, tmp_path, run_case)


def test_ensure_local_memory_context_uses_runtime_refresh_and_load_patch_points(monkeypatch):
    async def run_case():
        calls: list[tuple[str, str, str]] = []

        async def fake_refresh_session_memory(_async_session, profile, current_session_id):
            calls.append(("refresh", profile, current_session_id))

        async def fake_load_local_memory_context(_async_session, profile, current_session_id):
            calls.append(("load", profile, current_session_id))
            return "<local-memory>patched wrapper memory</local-memory>"

        monkeypatch.setattr(
            agent_chat_runtime,
            "refresh_session_memory",
            fake_refresh_session_memory,
        )
        monkeypatch.setattr(
            agent_chat_runtime,
            "load_local_memory_context",
            fake_load_local_memory_context,
        )

        context = await agent_chat_runtime.ensure_local_memory_context(
            object(),
            "siq_assistant",
            "siq-assistant-wrapper-memory",
        )
        return calls, context

    calls, context = anyio.run(run_case)

    assert calls == [
        ("refresh", "siq_assistant", "siq-assistant-wrapper-memory"),
        ("load", "siq_assistant", "siq-assistant-wrapper-memory"),
    ]
    assert context == "<local-memory>patched wrapper memory</local-memory>"


def test_ensure_local_memory_context_clears_stale_record_when_recent_window_has_no_source(tmp_path):
    async def run_case(async_session):
        session_id = "siq-assistant-stale-memory-clear"
        async_session.add_all(
            [
                ChatSessionMemory(
                    profile="siq_assistant",
                    session_id=session_id,
                    summary="stale sentinel：旧公司上汽集团",
                    last_message_id=999,
                ),
                ChatMessage(session_id=session_id, role="user", content="当前问题：现金流"),
                ChatMessage(session_id=session_id, role="assistant", content="当前回答：只看当前轮"),
            ]
        )
        await async_session.commit()

        context = await agent_chat_runtime.ensure_local_memory_context(
            async_session,
            "siq_assistant",
            session_id,
        )

        result = await async_session.exec(
            select(ChatSessionMemory).where(
                ChatSessionMemory.profile == "siq_assistant",
                ChatSessionMemory.session_id == session_id,
            )
        )
        record = result.first()

        assert context is None
        assert record is not None
        assert record.summary == ""
        assert record.last_message_id is None

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
