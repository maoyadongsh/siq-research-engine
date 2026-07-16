from __future__ import annotations

import anyio
import pytest
from schemas import ChatContext, ChatContextPage
from services.hermes_client import StreamEvent

from services import agent_chat_runtime as runtime, primary_market_agent_runtime

PROFILE = "siq_ic_finance_auditor"
DEAL_ID = "DEAL-RUNTIME-001"
RAW_QUERY = "请评估收入确认和现金流风险"
SCOPED_MESSAGE = f"一级市场 IC profile 职责护栏:\n- profile_id: {PROFILE}\n\n主持人原始问题:\n\n{RAW_QUERY}"


def _context(*, retrieval_query: str | None = RAW_QUERY) -> ChatContext:
    return ChatContext(
        domain="primary_market",
        deal_id=DEAL_ID,
        profile_id=PROFILE,
        retrieval_query=retrieval_query,
        page=ChatContextPage(
            title=(f"一级市场会议室上下文:\n- deal_id: {DEAL_ID}\n- company_name: Alpha Robotics\n- phase: R1")
        ),
    )


def _forbidden(label: str):
    def fail(*_args, **_kwargs):
        raise AssertionError(f"primary-market IC runtime called {label}")

    return fail


async def _async_forbidden(label: str, *_args, **_kwargs):
    raise AssertionError(f"primary-market IC runtime called {label}")


def test_primary_market_context_fields_and_raw_retrieval_query_are_structured():
    context = _context()

    assert context.domain == "primary_market"
    assert context.deal_id == DEAL_ID
    assert context.profile_id == PROFILE
    assert context.retrieval_query == RAW_QUERY
    assert primary_market_agent_runtime.is_primary_market_ic_runtime(PROFILE, context) is True
    assert primary_market_agent_runtime.primary_market_retrieval_query(PROFILE, context) == RAW_QUERY
    assert primary_market_agent_runtime.primary_market_retrieval_query(PROFILE, _context(retrieval_query=None)) == ""


@pytest.mark.parametrize(
    "bad_context, error",
    [
        ({}, "domain=primary_market"),
        ({"domain": "secondary_market", "deal_id": DEAL_ID}, "domain=primary_market"),
        ({"domain": "primary_market"}, "deal_id"),
        (
            {
                "domain": "primary_market",
                "deal_id": DEAL_ID,
                "profile_id": "siq_ic_legal_scanner",
            },
            "profile_id does not match",
        ),
    ],
)
def test_ic_identity_never_falls_back_to_secondary_runtime_when_context_is_invalid(
    bad_context,
    error,
):
    assert primary_market_agent_runtime.is_primary_market_ic_runtime(PROFILE, bad_context) is True
    with pytest.raises(ValueError, match=error):
        runtime.build_session_contextual_input(
            RAW_QUERY,
            profile=PROFILE,
            session_id="invalid-primary-market-context",
            context=bad_context,
        )


def test_secondary_identity_cannot_enter_primary_market_runtime():
    with pytest.raises(ValueError, match="secondary-market profile"):
        runtime.build_session_contextual_input(
            "读取项目底稿",
            profile="siq_analysis",
            session_id="invalid-secondary-market-context",
            context={"domain": "primary_market", "deal_id": DEAL_ID},
        )


def test_ic_profile_wiki_root_is_fenced_to_deals_namespace():
    with runtime._profile_wiki_context(PROFILE):
        assert runtime.WIKI_ROOT._path() == runtime.PRIMARY_MARKET_DEALS_ROOT
        assert runtime.WIKI_ROOT._path() != runtime.PROJECT_WIKI_ROOT


def test_primary_market_ic_prompt_bypasses_secondary_market_context_builders(monkeypatch):
    secondary_context_builders = (
        "_resolve_company_dirs",
        "_context_for_company_dir",
        "_message_for_company",
        "build_company_wiki_scope_context",
        "build_human_efficiency_evidence_context",
        "build_human_capital_context",
        "build_three_statement_core_context",
        "build_statement_metric_context",
        "build_note_detail_context",
        "build_wiki_fulltext_fallback_context",
        "build_postgres_fallback_context",
        "build_pdf2md_parse_only_context",
    )
    for name in secondary_context_builders:
        monkeypatch.setattr(runtime, name, _forbidden(name))

    prompt = runtime.build_session_contextual_input(
        SCOPED_MESSAGE,
        profile=PROFILE,
        session_id="primary-market-prompt-session",
        context=_context(),
        allow_initialize=True,
        local_memory_context="<agent-memory>项目专属记忆</agent-memory>",
    )

    assert "一级市场 IC 专用运行时上下文:" in prompt
    assert f"deal_id: {DEAL_ID}" in prompt
    assert f"runtime_profile: {PROFILE}" in prompt
    assert "Alpha Robotics" in prompt
    assert "<agent-memory>项目专属记忆</agent-memory>" in prompt
    assert SCOPED_MESSAGE in prompt
    assert "一级市场 IC 回答展示规范:" in prompt
    assert "标准 Markdown 二级、三级标题" in prompt
    assert "不要用整段粗体" in prompt
    assert "不机械罗列完整 R0-R4 流程" in prompt
    assert "不得再让用户“启动检索”" in prompt
    assert "上传/解析底稿或重建 Evidence" in prompt
    assert "Wiki 根目录" not in prompt
    assert runtime.CHAT_OUTPUT_CONTRACT not in prompt
    assert runtime.FINANCIAL_CALCULATION_RUNTIME_CONTRACT not in prompt


def test_non_primary_context_keeps_existing_runtime_builder(monkeypatch):
    captured = {}

    def fake_builder(message, **kwargs):
        captured["message"] = message
        captured.update(kwargs)
        return "secondary-runtime"

    monkeypatch.setattr(runtime.agent_runtime_context, "build_session_contextual_input", fake_builder)

    result = runtime.build_session_contextual_input(
        "分析上市公司年报",
        profile="siq_analysis",
        session_id="secondary-runtime-session",
        context={"domain": "secondary_market"},
    )

    assert result == "secondary-runtime"
    assert captured["message"] == "分析上市公司年报"


def test_primary_market_nonstream_uses_deal_scoped_memory_and_skips_financial_postprocessing(monkeypatch):
    async def run_case():
        saved_messages: list[tuple[str, str]] = []
        captured: dict[str, object] = {}

        async def fake_prepare(*_args, **_kwargs):
            return runtime.ChatRequestEnvelope(
                all_attachments=[],
                message_hash="primary-market-nonstream-hash",
                user_display_message=RAW_QUERY,
            )

        async def fake_preflight(*_args, **kwargs):
            captured["retrieval_query"] = kwargs["message"]
            return runtime.ChatRunPreflightContext(history=[], local_memory_context=None, attachments=[])

        async def fake_save(_session, role, content, _session_id, **kwargs):
            saved_messages.append((role, content))
            captured.setdefault("memory_saves", []).append(kwargs)

        async def fake_noop(*_args, **_kwargs):
            return None

        async def fake_true(*_args, **_kwargs):
            return True

        async def fake_images(*_args, **_kwargs):
            return None, True

        async def fake_create(*_args, **_kwargs):
            return "run-primary-market-nonstream"

        async def fake_collect(*_args, **_kwargs):
            return "基于项目材料的 IC 回答"

        def fake_run_input(message, **kwargs):
            captured["run_message"] = message
            captured["run_context"] = kwargs["context"]
            return "primary-market-run-input"

        monkeypatch.setattr(runtime, "_prepare_chat_request_envelope", fake_prepare)
        monkeypatch.setattr(runtime, "build_wiki_catalog_reply", _forbidden("Wiki catalog"))
        monkeypatch.setattr(runtime, "_recent_duplicate_reply", lambda *_args: None)
        monkeypatch.setattr(runtime, "_load_chat_run_preflight_context", fake_preflight)
        monkeypatch.setattr(runtime, "wait_for_pdf_attachment_parses", fake_noop)
        monkeypatch.setattr(runtime, "_attachments_with_fresh_metadata", lambda attachments: attachments)
        monkeypatch.setattr(runtime, "save_message", fake_save)
        monkeypatch.setattr(runtime, "refresh_session_memory", fake_noop)
        monkeypatch.setattr(runtime, "analyze_images_with_primary_model", fake_images)
        monkeypatch.setattr(runtime, "build_hermes_run_input", fake_run_input)
        monkeypatch.setattr(runtime, "create_run", fake_create)
        monkeypatch.setattr(runtime, "collect_run_result", fake_collect)
        monkeypatch.setattr(runtime, "_claim_durable_active_run", fake_true)
        monkeypatch.setattr(runtime, "_bind_durable_active_run", fake_true)
        monkeypatch.setattr(runtime, "_release_durable_lease", fake_noop)
        monkeypatch.setattr(runtime, "_trusted_financial_receipts_after_run", _async_forbidden)
        monkeypatch.setattr(runtime, "recover_financial_tool_loop_reply", _forbidden("financial recovery"))
        monkeypatch.setattr(runtime, "deterministic_pdf_market_reply", _forbidden("deterministic report reply"))
        monkeypatch.setattr(runtime, "enforce_financial_evidence_contract", _forbidden("financial evidence guard"))
        monkeypatch.setattr(runtime, "_record_answer_audit_trace_compat", _forbidden("financial answer audit"))
        monkeypatch.setattr(runtime, "_record_financial_llm_provenance_if_needed", _forbidden("financial provenance"))
        monkeypatch.setattr(
            runtime.agent_runtime_postgres_fallback,
            "audit_context_for_final_reply",
            _forbidden("PostgreSQL audit fallback"),
        )
        monkeypatch.setattr(runtime, "normalize_evidence_trace_for_display", lambda reply: reply)
        monkeypatch.setattr(runtime, "_remember_completed_run", lambda *_args: None)

        reply = await runtime._collect_chat_reply_impl(
            SCOPED_MESSAGE,
            object(),
            session_id="primary-market-nonstream-session",
            profile=PROFILE,
            context=_context(),
            display_message=RAW_QUERY,
            enforce_evidence_contract=False,
        )
        return reply, saved_messages, captured

    reply, saved_messages, captured = anyio.run(run_case)

    assert reply == "基于项目材料的 IC 回答"
    assert captured["retrieval_query"] == RAW_QUERY
    assert captured["run_message"] == SCOPED_MESSAGE
    assert captured["run_context"]["domain"] == "primary_market"
    assert saved_messages == [("user", RAW_QUERY), ("assistant", reply)]
    assert captured["memory_saves"] == [
        {
            "attachments": [],
            "profile": PROFILE,
            "deal_id": DEAL_ID,
            "visibility": "project_shared",
        },
        {
            "profile": PROFILE,
            "deal_id": DEAL_ID,
            "visibility": "project_shared",
            "audit_trace_id": None,
        },
    ]


def test_primary_market_stream_skips_financial_reply_replacement_and_audit(monkeypatch):
    async def run_case():
        state = runtime.ActiveRunState(
            profile=PROFILE,
            session_id="primary-market-stream-session",
            run_id="run-primary-market-stream",
        )
        state.original_message = SCOPED_MESSAGE
        state.context = _context().model_dump(exclude_none=True)
        saved_messages: list[tuple[str, dict]] = []

        async def fake_stream_run(*_args, **_kwargs):
            yield StreamEvent(type="delta", text="基于项目材料的流式回答")
            yield StreamEvent(type="done", text="基于项目材料的流式回答")

        async def fake_save(_role, content, _session_id, **kwargs):
            saved_messages.append((content, kwargs))

        monkeypatch.setattr(runtime, "stream_run", fake_stream_run)
        monkeypatch.setattr(runtime, "hermes_timeout", lambda: 1)
        monkeypatch.setattr(runtime, "stream_idle_timeout", lambda _profile: 1)
        monkeypatch.setattr(runtime, "save_message_in_background", fake_save)
        monkeypatch.setattr(runtime, "_trusted_financial_receipts_after_run", _async_forbidden)
        monkeypatch.setattr(runtime, "recover_financial_tool_loop_reply", _forbidden("financial recovery"))
        monkeypatch.setattr(runtime, "deterministic_pdf_market_reply", _forbidden("deterministic report reply"))
        monkeypatch.setattr(runtime, "enforce_financial_evidence_contract", _forbidden("financial evidence guard"))
        monkeypatch.setattr(runtime, "_record_answer_audit_trace_compat", _forbidden("financial answer audit"))
        monkeypatch.setattr(runtime, "_record_financial_llm_provenance_if_needed", _forbidden("financial provenance"))
        monkeypatch.setattr(
            runtime.agent_runtime_postgres_fallback,
            "audit_context_for_final_reply",
            _forbidden("PostgreSQL audit fallback"),
        )
        monkeypatch.setattr(runtime, "normalize_evidence_trace_for_display", lambda reply: reply)
        monkeypatch.setattr(runtime, "_remember_completed_run", lambda *_args: None)

        await runtime._collect_stream_run(state, None, enforce_evidence_contract=False)
        return state, saved_messages

    state, saved_messages = anyio.run(run_case)

    assert state.content == "基于项目材料的流式回答"
    assert state.done_payload["content"] == state.content
    assert saved_messages == [
        (
            state.content,
            {
                "profile": PROFILE,
                "audit_trace_id": None,
                "request_context": state.context,
            },
        )
    ]
