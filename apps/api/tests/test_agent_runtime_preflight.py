import anyio

from services import agent_runtime_preflight as preflight


def test_prepare_chat_request_envelope_uses_injected_dependencies():
    async def run_case():
        calls: list[str] = []
        recent_attachments = [{"path": "/tmp/reused.png", "kind": "image"}]

        def attachment_dicts(attachments):
            calls.append("attachment_dicts")
            assert attachments is None
            return []

        def should_reuse_recent_attachments(message):
            calls.append("should_reuse_recent_attachments")
            assert message == "继续分析这张图片"
            return True

        async def load_recent_session_attachments(_async_session, session_id):
            calls.append("load_recent_session_attachments")
            assert session_id == "session-1"
            return recent_attachments

        def dedupe_hash_with_attachments(message, context, attachments):
            calls.append("dedupe_hash_with_attachments")
            assert message == "继续分析这张图片"
            assert context == {"scope": "image"}
            assert attachments is recent_attachments
            return "hash-1"

        def display_message_with_attachments(message, attachments):
            calls.append("display_message_with_attachments")
            assert message == "展示文本"
            assert attachments is recent_attachments
            return "display-1"

        envelope = await preflight.prepare_chat_request_envelope(
            "继续分析这张图片",
            object(),
            session_id="session-1",
            context={"scope": "image"},
            display_message=" 展示文本 ",
            attachments=None,
            attachment_dicts=attachment_dicts,
            should_reuse_recent_attachments=should_reuse_recent_attachments,
            load_recent_session_attachments=load_recent_session_attachments,
            dedupe_hash_with_attachments=dedupe_hash_with_attachments,
            display_message_with_attachments=display_message_with_attachments,
        )
        return envelope, calls

    envelope, calls = anyio.run(run_case)

    assert calls == [
        "attachment_dicts",
        "should_reuse_recent_attachments",
        "load_recent_session_attachments",
        "dedupe_hash_with_attachments",
        "display_message_with_attachments",
    ]
    assert envelope == preflight.ChatRequestEnvelope(
        all_attachments=[{"path": "/tmp/reused.png", "kind": "image"}],
        message_hash="hash-1",
        user_display_message="display-1",
    )


def test_prepare_chat_request_envelope_skips_recent_attachment_lookup_when_explicit_attachments_exist():
    async def run_case():
        calls: list[str] = []
        explicit_attachments = [{"path": "/tmp/report.pdf", "kind": "document"}]

        async def forbidden_recent_lookup(_async_session, _session_id):
            raise AssertionError("recent attachment lookup should not run")

        envelope = await preflight.prepare_chat_request_envelope(
            "分析 PDF",
            object(),
            session_id="session-1",
            attachments=explicit_attachments,
            attachment_dicts=lambda attachments: calls.append("attachment_dicts") or attachments,
            should_reuse_recent_attachments=lambda _message: calls.append("should_reuse_recent_attachments") or True,
            load_recent_session_attachments=forbidden_recent_lookup,
            dedupe_hash_with_attachments=lambda _message, _context, attachments: calls.append("dedupe_hash_with_attachments") or f"hash:{len(attachments)}",
            display_message_with_attachments=lambda message, attachments: calls.append("display_message_with_attachments") or f"{message}:{len(attachments)}",
        )
        return envelope, calls

    envelope, calls = anyio.run(run_case)

    assert calls == [
        "attachment_dicts",
        "dedupe_hash_with_attachments",
        "display_message_with_attachments",
    ]
    assert envelope.all_attachments == [{"path": "/tmp/report.pdf", "kind": "document"}]
    assert envelope.message_hash == "hash:1"
    assert envelope.user_display_message == "分析 PDF:1"


def test_load_chat_run_preflight_context_uses_injected_dependencies_and_allow_initialize():
    async def run_case():
        calls: list[str] = []
        history = [{"role": "assistant", "content": "older reply"}]
        attachments = [{"path": "/tmp/report.pdf", "kind": "document"}]

        async def load_history(_async_session, session_id, *, limit):
            calls.append("load_history")
            assert session_id == "session-1"
            assert limit == 5
            return history

        async def ensure_local_memory_context(_async_session, profile, session_id):
            calls.append("ensure_local_memory_context")
            assert profile == "siq_assistant"
            assert session_id == "session-1"
            return "<memory>older turns</memory>"

        context = await preflight.load_chat_run_preflight_context(
            object(),
            session_id="session-1",
            profile="siq_assistant",
            attachments=attachments,
            history_limit=5,
            load_history=load_history,
            ensure_local_memory_context=ensure_local_memory_context,
        )
        return context, calls, history, attachments

    context, calls, history, attachments = anyio.run(run_case)

    assert calls == ["load_history", "ensure_local_memory_context"]
    assert context.history is history
    assert context.local_memory_context == "<memory>older turns</memory>"
    assert context.attachments is attachments
    assert context.allow_initialize is False
    assert preflight.ChatRunPreflightContext(history=[], local_memory_context=None, attachments=[]).allow_initialize is True


def test_plan_chat_preflight_short_circuit_skips_duplicate_for_catalog_reply():
    plan = preflight.plan_chat_preflight_short_circuit(
        catalog_reply="catalog reply",
        is_general_assistant_request=False,
    )

    assert plan == preflight.ChatPreflightShortCircuitPlan(
        forget_recent_completed_run=True,
        should_check_duplicate=False,
        catalog_reply="catalog reply",
    )


def test_plan_chat_preflight_short_circuit_skips_duplicate_for_general_assistant_request():
    plan = preflight.plan_chat_preflight_short_circuit(
        catalog_reply=None,
        is_general_assistant_request=True,
    )

    assert plan == preflight.ChatPreflightShortCircuitPlan(
        forget_recent_completed_run=True,
        should_check_duplicate=False,
        catalog_reply=None,
    )


def test_plan_chat_preflight_short_circuit_checks_duplicate_for_normal_request():
    plan = preflight.plan_chat_preflight_short_circuit(
        catalog_reply=None,
        is_general_assistant_request=False,
    )

    assert plan == preflight.ChatPreflightShortCircuitPlan(
        forget_recent_completed_run=False,
        should_check_duplicate=True,
        catalog_reply=None,
    )
