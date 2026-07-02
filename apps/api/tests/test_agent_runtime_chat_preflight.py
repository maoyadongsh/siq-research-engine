import json

import anyio

from services import agent_chat_runtime as runtime


class _Request:
    async def is_disconnected(self) -> bool:
        return False


def _event_payload(event: dict[str, str]) -> dict:
    return json.loads(event["data"])


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


def test_stream_chat_reply_preflight_refreshes_pdf_metadata_before_saving_user(monkeypatch):
    async def run_case():
        session_id = "preflight-stream-session"
        calls: list[str] = []
        history = [{"role": "user", "content": "previous streaming question"}]
        refreshed_attachments = [
            {
                "path": "/tmp/report.pdf",
                "kind": "document",
                "content_type": "application/pdf",
                "metadata": {"parse_dir": "/tmp/parse", "markdown_path": "/tmp/parse/result.md"},
            }
        ]
        saved: list[tuple[str, list[dict] | None]] = []

        async def fake_load_history(_async_session, current_session_id, *, limit):
            calls.append("load_history")
            assert current_session_id == session_id
            assert limit == 5
            return history

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

        async def fake_save_message(_async_session, role, _content, current_session_id, attachments=None):
            calls.append(f"save_{role}")
            assert current_session_id == session_id
            saved.append((role, attachments))

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
            assert conversation_history == history
            assert all(item["content"] != "流式分析这份 PDF" for item in conversation_history)
            assert profile == "siq_assistant"
            assert session_id == "siq:siq_assistant:preflight-stream-session"
            return "run-stream"

        async def fake_collect_stream_run(state, done_payload_factory):
            calls.append("collect_stream_run")
            assert state.session_id == session_id
            done_payload = await done_payload_factory("stream reply")
            await runtime._append_completed_active_run(state, {**done_payload, "content": "stream reply"})

        monkeypatch.setattr(runtime, "load_history", fake_load_history)
        monkeypatch.setattr(runtime, "ensure_local_memory_context", fake_ensure_local_memory_context)
        monkeypatch.setattr(runtime, "_pdf_attachment_parse_dirs", fake_pdf_attachment_parse_dirs)
        monkeypatch.setattr(runtime, "wait_for_pdf_attachment_parses", fake_wait_for_pdf_attachment_parses)
        monkeypatch.setattr(runtime, "_attachments_with_fresh_metadata", fake_attachments_with_fresh_metadata)
        monkeypatch.setattr(runtime, "save_message", fake_save_message)
        monkeypatch.setattr(runtime, "analyze_images_with_primary_model", fake_analyze_images_with_primary_model)
        monkeypatch.setattr(runtime, "build_hermes_run_input", fake_build_hermes_run_input)
        monkeypatch.setattr(runtime, "create_run", fake_create_run)
        monkeypatch.setattr(runtime, "_collect_stream_run", fake_collect_stream_run)
        monkeypatch.setattr(runtime, "_recent_duplicate_reply", lambda *_args, **_kwargs: None)

        events: list[dict[str, str]] = []
        try:
            async for event in runtime.stream_chat_reply(
                "流式分析这份 PDF",
                _Request(),
                object(),
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
        return calls, saved, events

    calls, saved, events = anyio.run(run_case)

    assert calls[:9] == [
        "load_history",
        "ensure_local_memory_context",
        "pdf_attachment_parse_dirs",
        "wait_for_pdf_attachment_parses",
        "attachments_with_fresh_metadata",
        "save_user",
        "analyze_images_with_primary_model",
        "build_hermes_run_input",
        "create_run",
    ]
    assert "collect_stream_run" in calls
    assert len(saved) == 1
    assert saved[0][0] == "user"
    refreshed_attachments = saved[0][1]
    assert refreshed_attachments is not None
    assert refreshed_attachments[0]["metadata"]["markdown_path"] == "/tmp/parse/result.md"
    assert [event["event"] for event in events] == ["progress", "run", "progress", "done"]
    assert _event_payload(events[-1]) == {"new_achievements": [], "content": "stream reply"}


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
