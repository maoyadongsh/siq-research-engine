import json

import anyio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession
from models import ChatMessage

from services import agent_chat_runtime as runtime
from services import agent_runtime_preflight


class _Request:
    async def is_disconnected(self) -> bool:
        return False


def _event_payload(event: dict[str, str]) -> dict:
    return json.loads(event["data"])


def test_merge_preflight_memory_context_preserves_runtime_contract():
    assert agent_runtime_preflight.merge_preflight_memory_context(None, None) is None
    assert agent_runtime_preflight.merge_preflight_memory_context("<local>", None) == "<local>"
    assert agent_runtime_preflight.merge_preflight_memory_context(None, "<agent>") == "<agent>"
    assert agent_runtime_preflight.merge_preflight_memory_context("<local>", "<agent>") == (
        "<local>\n\n<agent>"
    )


def test_prepare_chat_request_envelope_uses_runtime_patch_points(monkeypatch):
    async def run_case():
        calls: list[str] = []
        recent_attachments = [
            {
                "path": "/tmp/reused.png",
                "kind": "image",
                "content_type": "image/png",
            }
        ]

        def fake_should_reuse_recent_attachments(message):
            calls.append("should_reuse_recent_attachments")
            assert message == "继续分析这张图片"
            return True

        async def fake_load_recent_session_attachments(_async_session, session_id):
            calls.append("load_recent_session_attachments")
            assert session_id == "preflight-helper-session"
            return recent_attachments

        def fake_dedupe_hash_with_attachments(message, context, attachments):
            calls.append("dedupe_hash_with_attachments")
            assert message == "继续分析这张图片"
            assert context == {"scope": "image"}
            assert attachments == recent_attachments
            return "hash-from-patch"

        def fake_display_message_with_attachments(message, attachments):
            calls.append("display_message_with_attachments")
            assert message == "前端展示文本"
            assert attachments == recent_attachments
            return "display-from-patch"

        monkeypatch.setattr(runtime, "_should_reuse_recent_attachments", fake_should_reuse_recent_attachments)
        monkeypatch.setattr(runtime, "load_recent_session_attachments", fake_load_recent_session_attachments)
        monkeypatch.setattr(runtime, "_dedupe_hash_with_attachments", fake_dedupe_hash_with_attachments)
        monkeypatch.setattr(runtime, "_display_message_with_attachments", fake_display_message_with_attachments)

        return await runtime._prepare_chat_request_envelope(
            "继续分析这张图片",
            object(),
            session_id="preflight-helper-session",
            context={"scope": "image"},
            display_message="前端展示文本",
            attachments=None,
        ), calls

    envelope, calls = anyio.run(run_case)

    assert calls == [
        "should_reuse_recent_attachments",
        "load_recent_session_attachments",
        "dedupe_hash_with_attachments",
        "display_message_with_attachments",
    ]
    assert envelope.all_attachments == [
        {
            "path": "/tmp/reused.png",
            "kind": "image",
            "content_type": "image/png",
        }
    ]
    assert envelope.message_hash == "hash-from-patch"
    assert envelope.user_display_message == "display-from-patch"


def test_load_chat_run_preflight_context_uses_runtime_patch_points(monkeypatch):
    async def run_case():
        calls: list[str] = []
        attachments = [{"path": "/tmp/report.pdf", "kind": "document"}]
        history = [{"role": "assistant", "content": "older reply"}]

        async def fake_load_history(_async_session, current_session_id, *, limit):
            calls.append("load_history")
            assert current_session_id == "preflight-context-session"
            assert limit == 3
            return history

        async def fake_ensure_local_memory_context(_async_session, profile, current_session_id):
            calls.append("ensure_local_memory_context")
            assert profile == "siq_assistant"
            assert current_session_id == "preflight-context-session"
            return "<local-memory>older turns only</local-memory>"

        async def forbidden_ensure_agent_memory_context(*_args, **_kwargs):
            raise AssertionError("message-free preflight must not query agent memory")

        monkeypatch.setattr(runtime, "load_history", fake_load_history)
        monkeypatch.setattr(runtime, "ensure_local_memory_context", fake_ensure_local_memory_context)
        monkeypatch.setattr(runtime, "ensure_agent_memory_context", forbidden_ensure_agent_memory_context)

        return await runtime._load_chat_run_preflight_context(
            object(),
            session_id="preflight-context-session",
            profile="siq_assistant",
            attachments=attachments,
            history_limit=3,
        ), calls, history, attachments

    preflight_context, calls, history, attachments = anyio.run(run_case)

    assert calls == ["load_history", "ensure_local_memory_context"]
    assert preflight_context.history is history
    assert preflight_context.local_memory_context == "<local-memory>older turns only</local-memory>"
    assert preflight_context.attachments is attachments
    assert preflight_context.allow_initialize is False
    assert runtime.ChatRunPreflightContext(history=[], local_memory_context=None, attachments=[]).allow_initialize is True


def test_load_chat_run_preflight_context_uses_current_message_for_agent_memory(monkeypatch):
    async def run_case():
        calls: list[str] = []
        attachments = [{"path": "/tmp/report.pdf", "kind": "document"}]
        history = [{"role": "user", "content": "older question"}]

        async def fake_load_history(_async_session, current_session_id, *, limit):
            calls.append("load_history")
            assert current_session_id == "preflight-context-message-session"
            assert limit == 5
            return history

        async def fake_ensure_local_memory_context(_async_session, profile, current_session_id):
            calls.append("ensure_local_memory_context")
            assert profile == "siq_assistant"
            assert current_session_id == "preflight-context-message-session"
            return "<local-memory>older turns only</local-memory>"

        async def fake_ensure_agent_memory_context(_async_session, profile, current_session_id, message):
            calls.append("ensure_agent_memory_context")
            assert profile == "siq_assistant"
            assert current_session_id == "preflight-context-message-session"
            assert message == "当前问题要检索 agent memory"
            return "<agent-memory>matched current message</agent-memory>"

        monkeypatch.setattr(runtime, "load_history", fake_load_history)
        monkeypatch.setattr(runtime, "ensure_local_memory_context", fake_ensure_local_memory_context)
        monkeypatch.setattr(runtime, "ensure_agent_memory_context", fake_ensure_agent_memory_context)

        return await runtime._load_chat_run_preflight_context(
            object(),
            session_id="preflight-context-message-session",
            profile="siq_assistant",
            attachments=attachments,
            history_limit=5,
            message="当前问题要检索 agent memory",
        ), calls, history, attachments

    preflight_context, calls, history, attachments = anyio.run(run_case)

    assert calls == ["load_history", "ensure_local_memory_context", "ensure_agent_memory_context"]
    assert preflight_context.history is history
    assert preflight_context.local_memory_context == (
        "<local-memory>older turns only</local-memory>\n\n"
        "<agent-memory>matched current message</agent-memory>"
    )
    assert preflight_context.attachments is attachments
    assert preflight_context.allow_initialize is False


def test_collect_chat_reply_preflight_loads_context_before_saving_current_user(monkeypatch):
    async def run_case():
        session_id = "preflight-blocking-session"
        calls: list[str] = []
        history: list[dict[str, str]] = []
        refreshed_attachments = [
            {
                "path": "/tmp/report.pdf",
                "kind": "document",
                "content_type": "application/pdf",
                "metadata": {"parse_dir": "/tmp/parse", "markdown_path": "/tmp/parse/result.md"},
            }
        ]
        saved: list[tuple[str, str, list[dict] | None]] = []

        async def fake_load_history(_async_session, current_session_id, *, limit):
            calls.append("load_history")
            assert current_session_id == session_id
            assert limit == 7
            return history

        async def fake_ensure_local_memory_context(_async_session, profile, current_session_id):
            calls.append("ensure_local_memory_context")
            assert profile == "siq_assistant"
            assert current_session_id == session_id
            return "<local-memory>older turns only</local-memory>"

        async def fake_wait_for_pdf_attachment_parses(attachments):
            calls.append("wait_for_pdf_attachment_parses")
            assert attachments[0]["metadata"]["parse_dir"] == "/tmp/parse"
            return [{"mineru_parse_status": "completed"}]

        def fake_attachments_with_fresh_metadata(attachments):
            calls.append("attachments_with_fresh_metadata")
            assert attachments[0]["metadata"]["parse_dir"] == "/tmp/parse"
            return refreshed_attachments

        async def fake_save_message(_async_session, role, content, current_session_id, attachments=None):
            calls.append(f"save_{role}")
            assert current_session_id == session_id
            saved.append((role, content, attachments))

        async def fake_analyze_images_with_primary_model(message, attachments):
            calls.append("analyze_images_with_primary_model")
            assert message == "分析这份 PDF"
            assert attachments == refreshed_attachments
            return ("image analysis context", True)

        def fake_build_hermes_run_input(
            message,
            *,
            profile,
            session_id: str,
            context,
            allow_initialize,
            attachments,
            local_memory_context,
            image_analysis_context,
            use_hermes_image_fallback,
        ):
            calls.append("build_hermes_run_input")
            assert message == "分析这份 PDF"
            assert profile == "siq_assistant"
            assert session_id == "preflight-blocking-session"
            assert context == {"company": "demo"}
            assert allow_initialize is True
            assert attachments == refreshed_attachments
            assert local_memory_context == "<local-memory>older turns only</local-memory>"
            assert image_analysis_context == "image analysis context"
            assert use_hermes_image_fallback is False
            return "run input"

        async def fake_create_run(run_input, conversation_history, *, profile, session_id):
            calls.append("create_run")
            assert run_input == "run input"
            assert conversation_history == history
            assert all(item["content"] != "分析这份 PDF" for item in conversation_history)
            assert profile == "siq_assistant"
            assert session_id == "siq:siq_assistant:preflight-blocking-session"
            return "run-blocking"

        async def fake_collect_run_result(run_id, *, profile, timeout):
            calls.append("collect_run_result")
            assert run_id == "run-blocking"
            assert profile == "siq_assistant"
            return "assistant reply"

        async def fake_refresh_session_memory(_async_session, profile, current_session_id):
            calls.append("refresh_session_memory")
            assert profile == "siq_assistant"
            assert current_session_id == session_id

        def fake_remember_completed_run(profile, current_session_id, message_hash, reply):
            calls.append("remember_completed_run")
            assert profile == "siq_assistant"
            assert current_session_id == session_id
            assert message_hash
            assert reply == "assistant reply"

        monkeypatch.setattr(runtime, "load_history", fake_load_history)
        monkeypatch.setattr(runtime, "ensure_local_memory_context", fake_ensure_local_memory_context)
        monkeypatch.setattr(runtime, "wait_for_pdf_attachment_parses", fake_wait_for_pdf_attachment_parses)
        monkeypatch.setattr(runtime, "_attachments_with_fresh_metadata", fake_attachments_with_fresh_metadata)
        monkeypatch.setattr(runtime, "save_message", fake_save_message)
        monkeypatch.setattr(runtime, "analyze_images_with_primary_model", fake_analyze_images_with_primary_model)
        monkeypatch.setattr(runtime, "build_hermes_run_input", fake_build_hermes_run_input)
        monkeypatch.setattr(runtime, "create_run", fake_create_run)
        monkeypatch.setattr(runtime, "collect_run_result", fake_collect_run_result)
        monkeypatch.setattr(runtime, "refresh_session_memory", fake_refresh_session_memory)
        monkeypatch.setattr(runtime, "_remember_completed_run", fake_remember_completed_run)
        monkeypatch.setattr(runtime, "_recent_duplicate_reply", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(runtime, "normalize_evidence_trace_for_display", lambda value: value)
        monkeypatch.setattr(runtime, "enforce_financial_evidence_contract", lambda _message, _context, reply: reply)
        monkeypatch.setattr(runtime, "hermes_timeout", lambda: object())

        reply = await runtime.collect_chat_reply(
            "分析这份 PDF",
            object(),
            session_id=session_id,
            profile="siq_assistant",
            context={"company": "demo"},
            attachments=[
                {
                    "path": "/tmp/report.pdf",
                    "kind": "document",
                    "content_type": "application/pdf",
                    "metadata": {"parse_dir": "/tmp/parse"},
                }
            ],
            history_limit=7,
        )
        return reply, calls, saved

    reply, calls, saved = anyio.run(run_case)

    assert reply == "assistant reply"
    assert calls == [
        "load_history",
        "ensure_local_memory_context",
        "wait_for_pdf_attachment_parses",
        "attachments_with_fresh_metadata",
        "save_user",
        "analyze_images_with_primary_model",
        "build_hermes_run_input",
        "create_run",
        "collect_run_result",
        "save_assistant",
        "refresh_session_memory",
        "remember_completed_run",
    ]
    assert saved[0][0] == "user"
    assert saved[0][2][0]["metadata"]["markdown_path"] == "/tmp/parse/result.md"
    assert saved[1] == ("assistant", "assistant reply", None)


def test_collect_chat_reply_passes_old_history_before_saving_current_user(tmp_path, monkeypatch):
    async def run_case():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'preflight-history-order.db'}")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(SQLModel.metadata.create_all)
            async with AsyncSession(engine) as async_session:
                session_id = "preflight-history-order-session"
                async_session.add_all(
                    [
                        ChatMessage(session_id=session_id, role="user", content="旧问题 1"),
                        ChatMessage(session_id=session_id, role="assistant", content="旧回答 1"),
                        ChatMessage(session_id=session_id, role="user", content="旧问题 2"),
                        ChatMessage(session_id=session_id, role="assistant", content="旧回答 2"),
                    ]
                )
                await async_session.commit()

                calls: list[str] = []
                saved: list[tuple[str, str, list[dict] | None]] = []
                captured_history: list[dict[str, str]] = []
                original_load_history = runtime.load_history
                original_save_message = runtime.save_message

                async def wrapped_load_history(_async_session, current_session_id, *, limit):
                    calls.append("load_history")
                    assert current_session_id == session_id
                    assert limit == 7
                    return await original_load_history(_async_session, current_session_id, limit=limit)

                async def fake_ensure_local_memory_context(_async_session, profile, current_session_id):
                    calls.append("ensure_local_memory_context")
                    assert profile == "siq_assistant"
                    assert current_session_id == session_id
                    return "<local-memory>older turns only</local-memory>"

                async def wrapped_save_message(_async_session, role, content, current_session_id, attachments=None):
                    calls.append(f"save_{role}")
                    assert current_session_id == session_id
                    saved.append((role, content, attachments))
                    await original_save_message(
                        _async_session,
                        role,
                        content,
                        current_session_id,
                        attachments=attachments,
                    )

                async def fake_analyze_images_with_primary_model(message, attachments):
                    calls.append("analyze_images_with_primary_model")
                    assert message == "新增一轮问题"
                    assert attachments == []
                    return ("", True)

                def fake_build_hermes_run_input(
                    message,
                    *,
                    profile,
                    session_id: str,
                    context,
                    allow_initialize,
                    attachments,
                    local_memory_context,
                    image_analysis_context,
                    use_hermes_image_fallback,
                ):
                    calls.append("build_hermes_run_input")
                    assert message == "新增一轮问题"
                    assert profile == "siq_assistant"
                    assert session_id == "preflight-history-order-session"
                    assert allow_initialize is False
                    assert local_memory_context == "<local-memory>older turns only</local-memory>"
                    return "run input"

                async def fake_create_run(run_input, conversation_history, *, profile, session_id):
                    calls.append("create_run")
                    assert run_input == "run input"
                    assert profile == "siq_assistant"
                    assert session_id == "siq:siq_assistant:preflight-history-order-session"
                    captured_history.extend(conversation_history)
                    assert all(item["content"] != "新增一轮问题" for item in conversation_history)
                    return "run-order"

                async def fake_collect_run_result(run_id, *, profile, timeout):
                    calls.append("collect_run_result")
                    assert run_id == "run-order"
                    return "assistant reply"

                async def fake_refresh_session_memory(_async_session, profile, current_session_id):
                    calls.append("refresh_session_memory")
                    assert profile == "siq_assistant"
                    assert current_session_id == session_id

                def fake_remember_completed_run(profile, current_session_id, message_hash, reply):
                    calls.append("remember_completed_run")
                    assert profile == "siq_assistant"
                    assert current_session_id == session_id
                    assert reply == "assistant reply"

                monkeypatch.setattr(runtime, "load_history", wrapped_load_history)
                monkeypatch.setattr(runtime, "ensure_local_memory_context", fake_ensure_local_memory_context)
                monkeypatch.setattr(runtime, "save_message", wrapped_save_message)
                monkeypatch.setattr(runtime, "analyze_images_with_primary_model", fake_analyze_images_with_primary_model)
                monkeypatch.setattr(runtime, "build_hermes_run_input", fake_build_hermes_run_input)
                monkeypatch.setattr(runtime, "create_run", fake_create_run)
                monkeypatch.setattr(runtime, "collect_run_result", fake_collect_run_result)
                monkeypatch.setattr(runtime, "refresh_session_memory", fake_refresh_session_memory)
                monkeypatch.setattr(runtime, "_remember_completed_run", fake_remember_completed_run)
                monkeypatch.setattr(runtime, "_recent_duplicate_reply", lambda *_args, **_kwargs: None)
                monkeypatch.setattr(runtime, "build_wiki_catalog_reply", lambda _message: None)
                monkeypatch.setattr(runtime, "normalize_evidence_trace_for_display", lambda value: value)
                monkeypatch.setattr(runtime, "enforce_financial_evidence_contract", lambda _message, _context, reply: reply)
                monkeypatch.setattr(runtime, "hermes_timeout", lambda: object())

                reply = await runtime.collect_chat_reply(
                    "新增一轮问题",
                    async_session,
                    session_id=session_id,
                    profile="siq_assistant",
                    context={"company": "demo"},
                    history_limit=7,
                )
                result = await async_session.exec(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == session_id)
                    .order_by(ChatMessage.id)
                )
                persisted = [(message.role, message.content) for message in result.all()]
                return reply, calls, saved, captured_history, persisted
        finally:
            await engine.dispose()

    reply, calls, saved, captured_history, persisted = anyio.run(run_case)

    assert reply == "assistant reply"
    assert calls[:3] == [
        "load_history",
        "ensure_local_memory_context",
        "save_user",
    ]
    assert "analyze_images_with_primary_model" in calls
    assert calls.index("analyze_images_with_primary_model") > calls.index("save_user")
    assert calls.index("build_hermes_run_input") > calls.index("analyze_images_with_primary_model")
    assert "create_run" in calls
    assert calls.index("create_run") > calls.index("save_user")
    assert captured_history == [
        {"role": "user", "content": "旧问题 1"},
        {"role": "assistant", "content": "旧回答 1"},
        {"role": "user", "content": "旧问题 2"},
        {"role": "assistant", "content": "旧回答 2"},
    ]
    assert persisted == [
        ("user", "旧问题 1"),
        ("assistant", "旧回答 1"),
        ("user", "旧问题 2"),
        ("assistant", "旧回答 2"),
        ("user", "新增一轮问题"),
        ("assistant", "assistant reply"),
    ]
    assert saved[0][0] == "user"
    assert saved[1] == ("assistant", "assistant reply", None)


def test_stream_chat_reply_preflight_refreshes_pdf_metadata_before_saving_user(tmp_path, monkeypatch):
    async def run_case():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stream-preflight-history-order.db'}")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(SQLModel.metadata.create_all)
            async with AsyncSession(engine) as async_session:
                session_id = "preflight-stream-session"
                async_session.add_all(
                    [
                        ChatMessage(session_id=session_id, role="user", content="previous streaming question"),
                        ChatMessage(session_id=session_id, role="assistant", content="previous streaming answer"),
                    ]
                )
                await async_session.commit()

                calls: list[str] = []
                captured_history: list[dict[str, str]] = []
                refreshed_attachments = [
                    {
                        "path": "/tmp/report.pdf",
                        "kind": "document",
                        "content_type": "application/pdf",
                        "metadata": {"parse_dir": "/tmp/parse", "markdown_path": "/tmp/parse/result.md"},
                    }
                ]
                saved: list[tuple[str, list[dict] | None]] = []
                original_load_history = runtime.load_history
                original_save_message = runtime.save_message

                async def wrapped_load_history(_async_session, current_session_id, *, limit):
                    calls.append("load_history")
                    assert current_session_id == session_id
                    assert limit == 5
                    return await original_load_history(_async_session, current_session_id, limit=limit)

                async def fake_ensure_local_memory_context(_async_session, profile, current_session_id):
                    calls.append("ensure_local_memory_context")
                    assert profile == "siq_assistant"
                    assert current_session_id == session_id
                    return "<local-memory>stream memory</local-memory>"

                def fake_pdf_attachment_parse_dirs(attachments):
                    calls.append("pdf_attachment_parse_dirs")
                    assert attachments[0]["metadata"]["parse_dir"] == "/tmp/parse"
                    return ["/tmp/parse"]

                async def fake_wait_for_pdf_attachment_parses(attachments):
                    calls.append("wait_for_pdf_attachment_parses")
                    assert attachments[0]["metadata"]["parse_dir"] == "/tmp/parse"
                    return [{"mineru_parse_status": "completed"}]

                def fake_attachments_with_fresh_metadata(attachments):
                    calls.append("attachments_with_fresh_metadata")
                    assert attachments[0]["metadata"]["parse_dir"] == "/tmp/parse"
                    return refreshed_attachments

                async def wrapped_save_message(_async_session, role, _content, current_session_id, attachments=None):
                    calls.append(f"save_{role}")
                    assert current_session_id == session_id
                    saved.append((role, attachments))
                    await original_save_message(
                        _async_session,
                        role,
                        _content,
                        current_session_id,
                        attachments=attachments,
                    )

                async def fake_analyze_images_with_primary_model(message, attachments):
                    calls.append("analyze_images_with_primary_model")
                    assert message == "流式分析这份 PDF"
                    assert attachments == refreshed_attachments
                    return ("", False)

                def fake_build_hermes_run_input(
                    message,
                    *,
                    profile,
                    session_id: str,
                    context,
                    allow_initialize,
                    attachments,
                    local_memory_context,
                    image_analysis_context,
                    use_hermes_image_fallback,
                ):
                    calls.append("build_hermes_run_input")
                    assert message == "流式分析这份 PDF"
                    assert profile == "siq_assistant"
                    assert session_id == "preflight-stream-session"
                    assert context is None
                    assert allow_initialize is False
                    assert attachments == refreshed_attachments
                    assert local_memory_context == "<local-memory>stream memory</local-memory>"
                    assert image_analysis_context == ""
                    assert use_hermes_image_fallback is True
                    return "stream run input"

                async def fake_create_run(run_input, conversation_history, *, profile, session_id):
                    calls.append("create_run")
                    assert run_input == "stream run input"
                    captured_history.extend(conversation_history)
                    assert all(item["content"] != "流式分析这份 PDF" for item in conversation_history)
                    assert profile == "siq_assistant"
                    assert session_id == "siq:siq_assistant:preflight-stream-session"
                    return "run-stream"

                async def fake_collect_stream_run(state, done_payload_factory, enforce_evidence_contract=True):
                    calls.append("collect_stream_run")
                    assert state.session_id == session_id
                    assert enforce_evidence_contract is True
                    done_payload = await done_payload_factory("stream reply")
                    await runtime._append_completed_active_run(state, {**done_payload, "content": "stream reply"})

                monkeypatch.setattr(runtime, "load_history", wrapped_load_history)
                monkeypatch.setattr(runtime, "ensure_local_memory_context", fake_ensure_local_memory_context)
                monkeypatch.setattr(runtime, "_pdf_attachment_parse_dirs", fake_pdf_attachment_parse_dirs)
                monkeypatch.setattr(runtime, "wait_for_pdf_attachment_parses", fake_wait_for_pdf_attachment_parses)
                monkeypatch.setattr(runtime, "_attachments_with_fresh_metadata", fake_attachments_with_fresh_metadata)
                monkeypatch.setattr(runtime, "save_message", wrapped_save_message)
                monkeypatch.setattr(runtime, "analyze_images_with_primary_model", fake_analyze_images_with_primary_model)
                monkeypatch.setattr(runtime, "build_hermes_run_input", fake_build_hermes_run_input)
                monkeypatch.setattr(runtime, "create_run", fake_create_run)
                monkeypatch.setattr(runtime, "_collect_stream_run", fake_collect_stream_run)
                monkeypatch.setattr(runtime, "_recent_duplicate_reply", lambda *_args, **_kwargs: None)
                monkeypatch.setattr(runtime, "build_wiki_catalog_reply", lambda _message: None)

                events: list[dict[str, str]] = []
                try:
                    async for event in runtime.stream_chat_reply(
                        "流式分析这份 PDF",
                        _Request(),
                        async_session,
                        session_id=session_id,
                        profile="siq_assistant",
                        attachments=[
                            {
                                "path": "/tmp/report.pdf",
                                "kind": "document",
                                "content_type": "application/pdf",
                                "metadata": {"parse_dir": "/tmp/parse"},
                            }
                        ],
                        history_limit=5,
                    ):
                        events.append(event)
                finally:
                    runtime.ACTIVE_RUNS.pop(runtime._active_key("siq_assistant", session_id), None)
                result = await async_session.exec(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == session_id)
                    .order_by(ChatMessage.id)
                )
                persisted = result.all()
                return calls, saved, events, captured_history, persisted
        finally:
            await engine.dispose()

    calls, saved, events, captured_history, persisted = anyio.run(run_case)

    assert calls[:5] == [
        "load_history",
        "ensure_local_memory_context",
        "pdf_attachment_parse_dirs",
        "wait_for_pdf_attachment_parses",
        "attachments_with_fresh_metadata",
    ]
    assert calls.index("load_history") < calls.index("save_user")
    assert calls.index("attachments_with_fresh_metadata") < calls.index("save_user")
    assert calls.index("save_user") < calls.index("analyze_images_with_primary_model")
    assert calls.index("analyze_images_with_primary_model") < calls.index("build_hermes_run_input")
    assert calls.index("build_hermes_run_input") < calls.index("create_run")
    assert "collect_stream_run" in calls
    assert len(saved) == 1
    assert saved[0][0] == "user"
    refreshed_attachments = saved[0][1]
    assert refreshed_attachments is not None
    assert refreshed_attachments[0]["metadata"]["markdown_path"] == "/tmp/parse/result.md"
    assert captured_history == [
        {"role": "user", "content": "previous streaming question"},
        {"role": "assistant", "content": "previous streaming answer"},
    ]
    assert [message.role for message in persisted] == ["user", "assistant", "user"]
    current_user = persisted[-1]
    assert current_user.content == "流式分析这份 PDF\n\n[文档: report.pdf]"
    assert current_user.attachments_json is not None
    current_user_attachments = json.loads(current_user.attachments_json)
    assert current_user_attachments[0]["metadata"]["markdown_path"] == "/tmp/parse/result.md"
    assert [event["event"] for event in events] == ["progress", "run", "progress", "done"]
    assert _event_payload(events[-1]) == {"new_achievements": [], "content": "stream reply"}


def test_start_streaming_chat_run_uses_runtime_patch_points(monkeypatch):
    async def run_case():
        session_id = "preflight-start-stream-session"
        calls: list[tuple] = []
        original_append_state_event = runtime._append_state_event

        async def fake_append_state_event(state, event_name, payload):
            calls.append(("append", event_name, payload.copy()))
            await original_append_state_event(state, event_name, payload)

        async def fake_collect_stream_run(state, done_payload_factory, enforce_evidence_contract=True):
            calls.append(
                (
                    "collect",
                    state.run_id,
                    state.message_hash,
                    state.original_message,
                    state.context,
                    enforce_evidence_contract,
                )
            )
            done_payload = await done_payload_factory("helper reply")
            await runtime._append_completed_active_run(state, {**done_payload, "content": "helper reply"})

        async def fake_done_payload(reply):
            calls.append(("done_payload", reply))
            return {"new_achievements": ["helper"], "reply_seen": reply}

        monkeypatch.setattr(runtime, "_append_state_event", fake_append_state_event)
        monkeypatch.setattr(runtime, "_collect_stream_run", fake_collect_stream_run)

        key = runtime._active_key("siq_assistant", session_id)
        try:
            state = await runtime._start_streaming_chat_run(
                profile="siq_assistant",
                session_id=session_id,
                run_id="run-start-stream",
                message_hash="hash-start-stream",
                message="启动流式运行",
                context={"scope": "patch-points"},
                done_payload_factory=fake_done_payload,
            )
            assert runtime.ACTIVE_RUNS[key] is state
            assert state.task is not None
            await state.task
            return state, calls, key in runtime.ACTIVE_RUNS
        finally:
            runtime.ACTIVE_RUNS.pop(key, None)

    state, calls, still_active = anyio.run(run_case)

    assert state.profile == "siq_assistant"
    assert state.session_id == "preflight-start-stream-session"
    assert state.run_id == "run-start-stream"
    assert calls[:3] == [
        (
            "append",
            "run",
            {"run_id": "run-start-stream", "session_id": "preflight-start-stream-session"},
        ),
        (
            "collect",
            "run-start-stream",
            "hash-start-stream",
            "启动流式运行",
            {"scope": "patch-points"},
            True,
        ),
        ("done_payload", "helper reply"),
    ]
    assert _event_payload(state.events[-1]) == {
        "new_achievements": ["helper"],
        "reply_seen": "helper reply",
        "content": "helper reply",
    }
    assert still_active is True


def test_stream_chat_reply_duplicate_short_circuits_before_preflight(monkeypatch):
    async def run_case():
        calls: list[str] = []

        async def forbidden(*_args, **_kwargs):
            raise AssertionError("duplicate path must not enter preflight")

        monkeypatch.setattr(runtime, "load_history", forbidden)
        monkeypatch.setattr(runtime, "ensure_local_memory_context", forbidden)
        monkeypatch.setattr(runtime, "save_message", forbidden)
        monkeypatch.setattr(runtime, "refresh_session_memory", forbidden)
        monkeypatch.setattr(runtime, "wait_for_pdf_attachment_parses", forbidden)
        monkeypatch.setattr(runtime, "analyze_images_with_primary_model", forbidden)
        monkeypatch.setattr(runtime, "create_run", forbidden)
        monkeypatch.setattr(runtime, "_collect_stream_run", forbidden)
        monkeypatch.setattr(runtime, "_start_streaming_chat_run", forbidden)
        monkeypatch.setattr(runtime, "_load_chat_run_preflight_context", forbidden)
        monkeypatch.setattr(runtime, "build_wiki_catalog_reply", lambda _message: None)
        monkeypatch.setattr(runtime, "_is_general_assistant_request", lambda _message: False)

        def fake_recent_duplicate_reply(profile, session_id, message_hash):
            calls.append("recent_duplicate_reply")
            assert profile == "siq_assistant"
            assert session_id == "preflight-duplicate-session"
            assert message_hash
            return "[已处理] duplicate reply"

        monkeypatch.setattr(runtime, "_recent_duplicate_reply", fake_recent_duplicate_reply)

        events = []
        async for event in runtime.stream_chat_reply(
            "重复问题",
            _Request(),
            object(),
            session_id="preflight-duplicate-session",
            profile="siq_assistant",
        ):
            events.append(event)
        return calls, events

    calls, events = anyio.run(run_case)

    assert calls == ["recent_duplicate_reply"]
    assert [event["event"] for event in events] == ["delta", "done"]
    assert _event_payload(events[0]) == {"content": "[已处理] duplicate reply"}
    assert _event_payload(events[1]) == {
        "new_achievements": [],
        "deduped": True,
        "content": "[已处理] duplicate reply",
    }


def test_stream_chat_reply_catalog_short_circuits_before_streaming_run_start(monkeypatch):
    async def run_case():
        calls: list[tuple] = []

        async def forbidden(*_args, **_kwargs):
            raise AssertionError("catalog path must not enter streaming run startup")

        async def fake_save_message(_async_session, role, content, current_session_id, attachments=None):
            calls.append(("save", role, content, current_session_id, attachments))

        async def fake_refresh_session_memory(_async_session, profile, current_session_id):
            calls.append(("refresh_session_memory", profile, current_session_id))

        def fake_remember_completed_run(profile, current_session_id, message_hash, reply):
            calls.append(("remember_completed_run", profile, current_session_id, bool(message_hash), reply))

        monkeypatch.setattr(runtime, "build_wiki_catalog_reply", lambda _message: "catalog reply")
        monkeypatch.setattr(runtime, "save_message", fake_save_message)
        monkeypatch.setattr(runtime, "refresh_session_memory", fake_refresh_session_memory)
        monkeypatch.setattr(runtime, "_remember_completed_run", fake_remember_completed_run)
        monkeypatch.setattr(runtime, "load_history", forbidden)
        monkeypatch.setattr(runtime, "ensure_local_memory_context", forbidden)
        monkeypatch.setattr(runtime, "wait_for_pdf_attachment_parses", forbidden)
        monkeypatch.setattr(runtime, "analyze_images_with_primary_model", forbidden)
        monkeypatch.setattr(runtime, "create_run", forbidden)
        monkeypatch.setattr(runtime, "_collect_stream_run", forbidden)
        monkeypatch.setattr(runtime, "_start_streaming_chat_run", forbidden)
        monkeypatch.setattr(runtime, "_load_chat_run_preflight_context", forbidden)

        events = []
        async for event in runtime.stream_chat_reply(
            "现在入库了多少家公司",
            _Request(),
            object(),
            session_id="preflight-catalog-session",
            profile="siq_assistant",
        ):
            events.append(event)
        return calls, events

    calls, events = anyio.run(run_case)

    assert calls == [
        ("save", "user", "现在入库了多少家公司", "preflight-catalog-session", []),
        ("save", "assistant", "catalog reply", "preflight-catalog-session", None),
        ("refresh_session_memory", "siq_assistant", "preflight-catalog-session"),
        ("remember_completed_run", "siq_assistant", "preflight-catalog-session", True, "catalog reply"),
    ]
    assert [event["event"] for event in events] == ["delta", "done"]
    assert _event_payload(events[0]) == {"content": "catalog reply"}
    assert _event_payload(events[1]) == {
        "new_achievements": [],
        "catalog": True,
        "content": "catalog reply",
    }


def test_collect_chat_reply_catalog_short_circuits_before_preflight(monkeypatch):
    async def run_case():
        calls: list[tuple] = []

        async def forbidden(*_args, **_kwargs):
            raise AssertionError("catalog path must not enter preflight")

        async def fake_save_message(_async_session, role, content, current_session_id, attachments=None):
            calls.append(("save", role, content, current_session_id, attachments))

        async def fake_refresh_session_memory(_async_session, profile, current_session_id):
            calls.append(("refresh_session_memory", profile, current_session_id))

        def fake_remember_completed_run(profile, current_session_id, message_hash, reply):
            calls.append(("remember_completed_run", profile, current_session_id, bool(message_hash), reply))

        monkeypatch.setattr(runtime, "build_wiki_catalog_reply", lambda _message: "catalog reply")
        monkeypatch.setattr(runtime, "save_message", fake_save_message)
        monkeypatch.setattr(runtime, "refresh_session_memory", fake_refresh_session_memory)
        monkeypatch.setattr(runtime, "_remember_completed_run", fake_remember_completed_run)
        monkeypatch.setattr(runtime, "load_history", forbidden)
        monkeypatch.setattr(runtime, "ensure_local_memory_context", forbidden)
        monkeypatch.setattr(runtime, "wait_for_pdf_attachment_parses", forbidden)
        monkeypatch.setattr(runtime, "analyze_images_with_primary_model", forbidden)
        monkeypatch.setattr(runtime, "create_run", forbidden)
        monkeypatch.setattr(runtime, "collect_run_result", forbidden)
        monkeypatch.setattr(runtime, "_load_chat_run_preflight_context", forbidden)

        reply = await runtime.collect_chat_reply(
            "现在入库了多少家公司",
            object(),
            session_id="preflight-blocking-catalog-session",
            profile="siq_assistant",
        )
        return reply, calls

    reply, calls = anyio.run(run_case)

    assert reply == "catalog reply"
    assert calls == [
        ("save", "user", "现在入库了多少家公司", "preflight-blocking-catalog-session", []),
        ("save", "assistant", "catalog reply", "preflight-blocking-catalog-session", None),
        ("refresh_session_memory", "siq_assistant", "preflight-blocking-catalog-session"),
        ("remember_completed_run", "siq_assistant", "preflight-blocking-catalog-session", True, "catalog reply"),
    ]


def test_collect_chat_reply_duplicate_short_circuits_before_preflight(monkeypatch):
    async def run_case():
        calls: list[str] = []

        async def forbidden(*_args, **_kwargs):
            raise AssertionError("duplicate path must not enter preflight")

        monkeypatch.setattr(runtime, "load_history", forbidden)
        monkeypatch.setattr(runtime, "ensure_local_memory_context", forbidden)
        monkeypatch.setattr(runtime, "save_message", forbidden)
        monkeypatch.setattr(runtime, "refresh_session_memory", forbidden)
        monkeypatch.setattr(runtime, "wait_for_pdf_attachment_parses", forbidden)
        monkeypatch.setattr(runtime, "analyze_images_with_primary_model", forbidden)
        monkeypatch.setattr(runtime, "create_run", forbidden)
        monkeypatch.setattr(runtime, "collect_run_result", forbidden)
        monkeypatch.setattr(runtime, "_load_chat_run_preflight_context", forbidden)
        monkeypatch.setattr(runtime, "build_wiki_catalog_reply", lambda _message: None)
        monkeypatch.setattr(runtime, "_is_general_assistant_request", lambda _message: False)

        def fake_recent_duplicate_reply(profile, session_id, message_hash):
            calls.append("recent_duplicate_reply")
            assert profile == "siq_assistant"
            assert session_id == "preflight-blocking-duplicate-session"
            assert message_hash
            return "[已处理] blocking duplicate reply"

        monkeypatch.setattr(runtime, "_recent_duplicate_reply", fake_recent_duplicate_reply)

        reply = await runtime.collect_chat_reply(
            "重复普通问题",
            object(),
            session_id="preflight-blocking-duplicate-session",
            profile="siq_assistant",
        )
        return calls, reply

    calls, reply = anyio.run(run_case)

    assert calls == ["recent_duplicate_reply"]
    assert reply == "[已处理] blocking duplicate reply"


def test_stream_chat_reply_existing_active_run_join_skips_preflight_side_effects(monkeypatch):
    async def run_case():
        session_id = "preflight-join-session"
        state = runtime.ActiveRunState(
            profile="siq_assistant",
            session_id=session_id,
            run_id="run-preflight-join",
        )
        key = runtime._active_key("siq_assistant", session_id)
        runtime.ACTIVE_RUNS[key] = state

        async def forbidden(*_args, **_kwargs):
            raise AssertionError("join path must not enter preflight")

        def forbidden_sync(*_args, **_kwargs):
            raise AssertionError("join path must not enter preflight")

        monkeypatch.setattr(runtime, "build_wiki_catalog_reply", forbidden_sync)
        monkeypatch.setattr(runtime, "_recent_duplicate_reply", forbidden_sync)
        monkeypatch.setattr(runtime, "_forget_recent_completed_run", forbidden_sync)
        monkeypatch.setattr(runtime, "_remember_completed_run", forbidden_sync)
        monkeypatch.setattr(runtime, "load_history", forbidden)
        monkeypatch.setattr(runtime, "ensure_local_memory_context", forbidden)
        monkeypatch.setattr(runtime, "save_message", forbidden)
        monkeypatch.setattr(runtime, "refresh_session_memory", forbidden)
        monkeypatch.setattr(runtime, "create_run", forbidden)
        monkeypatch.setattr(runtime, "_collect_stream_run", forbidden)
        monkeypatch.setattr(runtime, "_start_streaming_chat_run", forbidden)
        monkeypatch.setattr(runtime, "_load_chat_run_preflight_context", forbidden)

        try:
            await runtime._append_state_event(state, "run", {"run_id": state.run_id, "session_id": state.session_id})
            await runtime._append_state_event(state, "delta", {"content": "already running"})

            async def finish_run():
                await anyio.sleep(0)
                await runtime._append_state_event(state, "done", {"content": "already running"})

            events = []
            async with anyio.create_task_group() as tg:
                tg.start_soon(finish_run)
                async for event in runtime.stream_chat_reply(
                    "join active run",
                    _Request(),
                    object(),
                    session_id=session_id,
                    profile="siq_assistant",
                ):
                    events.append(event)
            return events
        finally:
            runtime.ACTIVE_RUNS.pop(key, None)

    events = anyio.run(run_case)

    assert [event["event"] for event in events] == ["run", "delta", "done"]
    assert _event_payload(events[0]) == {
        "run_id": "run-preflight-join",
        "session_id": "preflight-join-session",
    }
