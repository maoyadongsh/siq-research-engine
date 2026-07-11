import importlib.util
import json
from pathlib import Path
import types

import anyio

from services import agent_chat_runtime as runtime
from services import agent_runtime_answer_audit as audit


WIKI_TASK_ID = "11111111-1111-1111-1111-111111111111"
POSTGRES_TASK_ID = "22222222-2222-2222-2222-222222222222"
REPO_ROOT = Path(__file__).resolve().parents[3]


class _StreamEvent:
    def __init__(self, event_type: str, text: str = ""):
        self.type = event_type
        self.text = text
        self.tool = None
        self.preview = None
        self.duration = None
        self.error = None


def _load_financial_qa_benchmark_module():
    source = REPO_ROOT / "scripts" / "maintenance" / "run_financial_qa_benchmark.py"
    spec = importlib.util.spec_from_file_location("run_financial_qa_benchmark_for_runtime_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def test_answer_audit_trace_extracts_wiki_source_and_guardrail_fields():
    reply = f"""营业收入同比提升，计算过程已复核。

## 计算器校验
- financial_calculator.py operation=ratio numerator=120 denominator=100

## 引用来源
[D1] source_type=wiki_metrics, file=metrics/three_statements.json, statement_type=income_statement, metric=营业收入, canonical_name=revenue, period=2025, value=120, raw_value=120, unit=RMB million, currency=RMB, scale=1000000, task_id={WIKI_TASK_ID}, pdf_page=7, table_index=2, html_anchor=revenue-2025, evidence_id=evidence-1, md_line=50
"""
    context = {
        "question_id": "q-wiki-001",
        "company": {"name": "上汽集团", "code": "600104"},
        "query_plan": {"mode": "wiki_first"},
    }

    record = audit.build_answer_audit_trace(
        message="上汽集团 2025 年营业收入同比是多少？",
        context=context,
        profile="siq_assistant",
        session_id="session-wiki",
        raw_reply="营业收入同比提升。",
        final_reply=reply,
    )

    assert record["question_id"] == "q-wiki-001"
    assert record["resolved_company"] == {"name": "上汽集团", "code": "600104"}
    assert record["resolved_period"] == {"period": "2025"}
    assert record["wiki_facts"][0]["source_type"] == "wiki_metrics"
    assert record["wiki_facts"][0]["metric"] == "营业收入"
    assert record["wiki_facts"][0]["metric_name"] == "营业收入"
    assert record["wiki_facts"][0]["statement_type"] == "income_statement"
    assert record["wiki_facts"][0]["canonical_name"] == "revenue"
    assert record["wiki_facts"][0]["value"] == "120"
    assert record["wiki_facts"][0]["raw_value"] == "120"
    assert record["wiki_facts"][0]["unit"] == "RMB million"
    assert record["wiki_facts"][0]["currency"] == "RMB"
    assert record["wiki_facts"][0]["scale"] == "1000000"
    assert record["wiki_facts"][0]["task_id"] == WIKI_TASK_ID
    assert record["wiki_facts"][0]["source_page"] == "7"
    assert record["wiki_facts"][0]["html_anchor"] == "revenue-2025"
    assert record["wiki_facts"][0]["evidence_id"] == "evidence-1"
    assert record["postgres_facts"] == []
    assert record["query_plan"]["mode"] == "wiki_first"
    assert record["query_plan"]["observed_source_types"] == ["wiki_metrics"]
    assert record["calculator_runs"][1]["operation"] == "ratio"
    assert record["guardrail_result"]["output_was_guarded"] is True
    assert record["guardrail_result"]["has_wiki_facts"] is True


def test_answer_audit_trace_extracts_postgresql_source_and_fallback_reason():
    reply = f"""PostgreSQL fallback 返回了商誉指标。

## 引用来源
[P1] source_type=postgresql, table=document_parser.financial_metrics, statement_id=stmt-1, statement_type=balance_sheet, filing_id=CN:600104:2025, report_id=annual-2025, metric=商誉, canonical_name=goodwill, period_key=2025FY, value=42, raw_value=42, unit=RMB million, currency=RMB, task_id={POSTGRES_TASK_ID}, pdf_page=88, table_index=12, md_line=500, bbox=10:20:30:40, quote_text=商誉42
"""
    context = {
        "resolved_company": {"id": "CN:600104", "name": "上汽集团", "stock_code": "600104"},
        "resolved_period": {"fiscal_year": "2025", "period_end": "2025-12-31"},
        "fallback_reason": "wiki_miss_then_postgres",
        "query_plan": {"mode": "postgres_fallback"},
    }

    record = audit.build_answer_audit_trace(
        message="上汽集团商誉是多少？",
        context=context,
        profile="siq_assistant",
        session_id="session-postgres",
        final_reply=reply,
    )

    assert record["fallback_reason"] == "wiki_miss_then_postgres"
    assert record["resolved_company"]["id"] == "CN:600104"
    assert record["postgres_facts"][0]["source_type"] == "postgresql"
    assert record["postgres_facts"][0]["table"] == "document_parser.financial_metrics"
    assert record["postgres_facts"][0]["statement_type"] == "balance_sheet"
    assert record["postgres_facts"][0]["metric_name"] == "商誉"
    assert record["postgres_facts"][0]["canonical_name"] == "goodwill"
    assert record["postgres_facts"][0]["value"] == "42"
    assert record["postgres_facts"][0]["filing_id"] == "CN:600104:2025"
    assert record["postgres_facts"][0]["bbox"] == "10:20:30:40"
    assert record["postgres_facts"][0]["source_page"] == "88"
    assert record["postgres_facts"][0]["quote"] == "商誉42"
    assert record["postgres_facts"][0]["period_key"] == "2025FY"
    assert record["resolved_period"]["fiscal_year"] == "2025"
    assert record["resolved_period"]["period_end"] == "2025-12-31"
    assert record["resolved_period"]["period_key"] == "2025FY"
    assert record["resolved_period"]["report_id"] == "annual-2025"
    assert record["resolved_period"]["filing_id"] == "CN:600104:2025"
    assert record["wiki_facts"] == []
    assert record["citations"][0]["label"] == "[P1]"
    assert record["guardrail_result"]["has_postgres_facts"] is True


def test_answer_audit_trace_preserves_grouped_numbers_and_pipe_quotes():
    reply = f"""收入引用行包含千分位和表格片段。

## 引用来源
| [D2] source_type=wiki_metrics | file=metrics/three_statements.json | metric=Revenue | canonical_name=revenue | period=2025 | value=751,766 | raw_value=751,766 | unit=HKD million | currency=HKD | quote=Revenues | 751,766 | 660,257 | task_id={WIKI_TASK_ID} | pdf_page=15 | table_index=4 | source_url=https://source.test/report?source_token=secret-token&format=html |
"""

    record = audit.build_answer_audit_trace(
        message="Revenue 是多少？",
        profile="siq_assistant",
        session_id="session-pipe-quote",
        final_reply=reply,
    )

    fact = record["wiki_facts"][0]
    citation = record["citations"][0]
    assert fact["value"] == "751,766"
    assert fact["raw_value"] == "751,766"
    assert fact["quote"] == "Revenues | 751,766 | 660,257"
    assert fact["unit"] == "HKD million"
    assert fact["currency"] == "HKD"
    assert citation["source_url"] == "https://source.test/report?source_token=[REDACTED]&format=html"
    assert "source_token" not in fact


def test_answer_audit_trace_prefers_structured_fallback_events():
    reply = "未直接展示数据库引用，但运行时记录了兜底阶段。"
    context = {
        "_audit_fallback_events": [
            {"reason": "wiki_structured_miss", "stage": "postgres_fallback_started"},
            {"reason": "postgres_unavailable", "stage": "legacy_postgres_exception"},
        ],
    }

    record = audit.build_answer_audit_trace(
        message="收入是多少？",
        context=context,
        profile="siq_assistant",
        session_id="session-fallback-events",
        final_reply=reply,
    )

    assert record["fallback_reason"] == "postgres_unavailable"
    assert record["fallback_events"][1]["stage"] == "legacy_postgres_exception"


def test_answer_audit_trace_redacts_database_urls_tokens_and_passwords():
    reply = f"""引用行带有需要脱敏的调试 URL。

## 引用来源
[P1] source_type=postgresql, table=financial_metrics, metric=收入, task_id={POSTGRES_TASK_ID}, pdf_page=1, table_index=2, md_line=3，[打开PDF页](https://source.test/page?source_token=reply-source-token&keep=1)
"""
    context = {
        "question_id": "qid-secret",
        "company": {"name": "测试公司", "password": "company-password"},
        "query_plan": {
            "database_url": "postgresql://postgres:context-password@db/siq",
            "token": "context-token",
            "safe": "kept",
        },
    }

    record = audit.build_answer_audit_trace(
        message=(
            "qid=message-q database_url=postgresql://user:message-password@db/siq "
            "token=message-token password=message-password direct=postgresql://reader:direct-password@db/siq"
        ),
        context=context,
        profile="siq_assistant",
        session_id="session-secret",
        final_reply=reply,
    )
    serialized = json.dumps(record, ensure_ascii=False, sort_keys=True)

    for secret in (
        "company-password",
        "context-password",
        "context-token",
        "message-password",
        "message-token",
        "direct-password",
        "reply-source-token",
        "postgresql://user",
        "postgresql://reader",
    ):
        assert secret not in serialized
    assert audit.REDACTED_DATABASE_URL in serialized
    assert "source_token=[REDACTED]" in serialized
    assert record["query_plan"]["database_url"] == audit.REDACTED
    assert record["query_plan"]["token"] == audit.REDACTED
    assert record["query_plan"]["safe"] == "kept"


def test_record_answer_audit_trace_writes_jsonl(tmp_path):
    log_path = tmp_path / "audit" / "answer_audit_trace.jsonl"
    first = audit.build_answer_audit_trace(
        message="question_id=q-jsonl-1 收入是多少？",
        final_reply=f"[D1] source_type=wiki_metrics, metric=收入, task_id={WIKI_TASK_ID}, pdf_page=7",
        profile="siq_assistant",
        session_id="session-jsonl-1",
    )
    second = audit.build_answer_audit_trace(
        message="商誉是多少？",
        final_reply=f"[P1] source_type=postgresql, metric=商誉, task_id={POSTGRES_TASK_ID}, table_index=3",
        profile="siq_assistant",
        session_id="session-jsonl-2",
    )

    stored_first = audit.record_answer_audit_trace(first, log_path=log_path)
    stored_second = audit.record_answer_audit_trace(second, log_path=log_path)

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    payloads = [json.loads(line) for line in lines]
    assert payloads == [stored_first, stored_second]
    assert payloads[0]["session_id"] == "session-jsonl-1"
    assert payloads[1]["postgres_facts"][0]["source_type"] == "postgresql"
    assert audit.is_answer_audit_trace_id(payloads[0]["trace_id"])
    assert payloads[0]["trace_id"] != payloads[1]["trace_id"]


def test_get_answer_audit_trace_reads_recent_and_jsonl_records(tmp_path):
    log_path = tmp_path / "audit" / "answer_audit_trace.jsonl"
    audit.RECENT_ANSWER_AUDIT_TRACES.clear()
    first = audit.record_answer_audit_trace(
        audit.build_answer_audit_trace(
            message="question_id=q-readable 收入是多少？",
            final_reply=f"[D1] source_type=wiki_metrics, metric=收入, task_id={WIKI_TASK_ID}, pdf_page=7",
            profile="siq_assistant",
            session_id="session-readable",
        ),
        log_path=log_path,
    )

    assert audit.get_answer_audit_trace(first["trace_id"], log_path=log_path) == first

    audit.RECENT_ANSWER_AUDIT_TRACES.clear()
    loaded = audit.get_answer_audit_trace(first["trace_id"], log_path=log_path)

    assert loaded == first
    assert audit.get_answer_audit_trace("bad-trace-id", log_path=log_path) is None


def test_render_answer_audit_summary_and_append_are_stable():
    record = {
        "schema_version": audit.ANSWER_AUDIT_TRACE_SCHEMA,
        "question_id": "q-audit-ui-1",
        "query_plan": {"observed_source_types": ["wiki_metrics", "postgresql"]},
        "wiki_facts": [{"source_type": "wiki_metrics"}],
        "postgres_facts": [{"source_type": "postgresql"}],
        "citations": [{"label": "[D1]"}, {"label": "[P1]"}],
        "fallback_reason": "market_view_hit",
        "calculator_runs": [{"operation": "yoy"}],
        "guardrail_result": {"blocked": False},
    }

    summary = audit.render_answer_audit_summary(record)

    assert summary.startswith("## 审计详情")
    assert "trace_id: `aat_" in summary
    assert "question_id: `q-audit-ui-1`" in summary
    assert "source_counts: `wiki=1, postgres=1, citations=2`" in summary
    assert "fallback_reason: `market_view_hit`" in summary
    assert "calculator_runs: `1`" in summary
    assert "guardrail: `passed`" in summary
    assert "observed_sources: `wiki_metrics, postgresql`" in summary

    appended = audit.append_answer_audit_summary("最终回答", record)
    assert appended.endswith(summary)
    assert audit.append_answer_audit_summary(appended, record) == appended
    assert audit.append_answer_audit_summary("最终回答\n\n### 审计详情：\n- 已存在", record).count("审计详情") == 1
    assert audit.append_answer_audit_summary("   ", record) == "   "


def test_collect_chat_reply_records_answer_audit_after_non_stream_guard(monkeypatch):
    async def run_case():
        saved: list[tuple[str, str, str, str | None]] = []
        remembered: list[tuple[str, str, str | None, str]] = []
        provenance_calls: list[dict[str, object]] = []
        audit_calls: list[dict[str, object]] = []
        captured_audit_records: list[dict[str, object]] = []
        refreshed: list[tuple[str, str]] = []
        raw_reply = f"""最终回答

## 引用来源
[D1] source_type=wiki_metrics, metric=收入, canonical_name=revenue, period=2025, value=120, task_id={WIKI_TASK_ID}, pdf_page=7
"""

        async def fake_prepare_envelope(*_args, **_kwargs):
            return runtime.ChatRequestEnvelope(
                all_attachments=[{"name": "report.pdf"}],
                message_hash="hash-non-stream",
                user_display_message="收入是多少？\n\n[attachment: report.pdf]",
            )

        async def fake_preflight(*_args, **kwargs):
            return runtime.ChatRunPreflightContext(
                history=[],
                local_memory_context=None,
                attachments=kwargs["attachments"],
            )

        async def fake_save_message(_session, role, content, session_id, *, attachments=None):
            saved.append((role, content, session_id, json.dumps(attachments, ensure_ascii=False) if attachments else None))

        async def fake_refresh(_session, profile, session_id):
            refreshed.append((profile, session_id))

        async def fake_analyze_images(*_args, **_kwargs):
            return None, True

        async def fake_wait_for_pdf_attachment_parses(_attachments):
            return None

        async def fake_create_run(run_input, history, *, profile, session_id):
            assert run_input["message"] == "收入是多少？"
            assert history == []
            assert profile == "siq_assistant"
            assert session_id == runtime.hermes_runs_session_id("siq_assistant", "audit-non-stream-session")
            return "run-audit-non-stream"

        async def fake_collect_run_result(run_id, *, profile, timeout):
            assert run_id == "run-audit-non-stream"
            assert profile == "siq_assistant"
            assert timeout == runtime.hermes_timeout()
            return raw_reply

        def fake_record_answer_audit_trace_for_reply(**kwargs):
            audit_calls.append(kwargs)
            return {
                "schema_version": audit.ANSWER_AUDIT_TRACE_SCHEMA,
                "trace_id": "aat_1234567890abcdef1234567890abcdef",
                "question_id": "q-non-stream-audit",
                "wiki_facts": [{"source_type": "wiki_metrics"}],
                "postgres_facts": [],
                "citations": [{"label": "[D1]"}],
                "calculator_runs": [],
                "guardrail_result": {"blocked": False},
            }

        def fake_remember(profile, session_id, message_hash, reply):
            remembered.append((profile, session_id, message_hash, reply))

        def fake_provenance(**kwargs):
            provenance_calls.append(kwargs)

        monkeypatch.setattr(runtime, "_prepare_chat_request_envelope", fake_prepare_envelope)
        monkeypatch.setattr(runtime, "build_wiki_catalog_reply", lambda _message: None)
        monkeypatch.setattr(runtime, "_load_chat_run_preflight_context", fake_preflight)
        monkeypatch.setattr(runtime, "wait_for_pdf_attachment_parses", fake_wait_for_pdf_attachment_parses)
        monkeypatch.setattr(runtime, "_attachments_with_fresh_metadata", lambda attachments: attachments)
        monkeypatch.setattr(runtime, "save_message", fake_save_message)
        monkeypatch.setattr(runtime, "refresh_session_memory", fake_refresh)
        monkeypatch.setattr(runtime, "analyze_images_with_primary_model", fake_analyze_images)
        monkeypatch.setattr(runtime, "normalize_evidence_trace_for_display", lambda reply: reply)
        monkeypatch.setattr(runtime, "build_hermes_run_input", lambda message, **kwargs: {"message": message, **kwargs})
        monkeypatch.setattr(runtime, "create_run", fake_create_run)
        monkeypatch.setattr(runtime, "collect_run_result", fake_collect_run_result)
        monkeypatch.setattr(runtime, "_remember_completed_run", fake_remember)
        monkeypatch.setattr(runtime, "_record_financial_llm_provenance_if_needed", fake_provenance)
        monkeypatch.setattr(runtime.agent_runtime_answer_audit, "record_answer_audit_trace_for_reply", fake_record_answer_audit_trace_for_reply)

        session = types.SimpleNamespace()
        reply = await runtime._collect_chat_reply_impl(
            "收入是多少？",
            session,
            session_id="audit-non-stream-session",
            profile="siq_assistant",
            context={"question_id": "q-non-stream-audit"},
            enforce_evidence_contract=False,
            answer_audit_callback=captured_audit_records.append,
        )
        return raw_reply, reply, saved, refreshed, remembered, provenance_calls, audit_calls, captured_audit_records

    raw_reply, reply, saved, refreshed, remembered, provenance_calls, audit_calls, captured_audit_records = anyio.run(run_case)

    assert reply == raw_reply
    assert saved[0] == (
        "user",
        "收入是多少？\n\n[attachment: report.pdf]",
        "audit-non-stream-session",
        '[{"name": "report.pdf"}]',
    )
    assert saved[1] == ("assistant", reply, "audit-non-stream-session", None)
    assert refreshed == [("siq_assistant", "audit-non-stream-session")]
    assert remembered == [("siq_assistant", "audit-non-stream-session", "hash-non-stream", reply)]
    assert provenance_calls[0]["raw_output"] == raw_reply
    assert provenance_calls[0]["stored_output"] == reply
    assert audit_calls[0]["raw_reply"] == raw_reply
    assert audit_calls[0]["final_reply"] == raw_reply
    assert audit_calls[0]["enforce_evidence_contract"] is False
    assert captured_audit_records[0]["trace_id"] == "aat_1234567890abcdef1234567890abcdef"


def test_live_runtime_answer_audit_trace_feeds_financial_qa_benchmark(monkeypatch, tmp_path):
    benchmark = _load_financial_qa_benchmark_module()
    case_root = tmp_path / "bench"
    trace_log = case_root / "traces.jsonl"
    case = {
        "schema_version": "siq_financial_qa_benchmark_case_v1",
        "case_id": "live-runtime-trace-1",
        "tier": "P0",
        "market": "HK",
        "company_id": "HK:00700",
        "filing_id": "HK:00700:2025-annual",
        "period": "2025-12-31",
        "question": "腾讯 2025 年收入是多少？",
        "source_policy": {
            "primary": "wiki_metrics",
            "allow_postgres_fallback": True,
            "allowed_fallback_reasons": ["wiki_missing"],
            "forbid_semantic_numeric_source": True,
        },
        "expected_facts": [
            {
                "canonical_name": "revenue",
                "statement_type": "income_statement",
                "period": "2025-12-31",
                "value": "100",
                "raw_value": "100",
                "unit": "RMB million",
                "currency": "RMB",
                "tolerance_ratio": 0,
                "required_source_types": ["wiki_metrics"],
                "fallback_source_types": ["postgresql_agent_view"],
                "required_evidence": ["table_index", "quote"],
            }
        ],
        "required_evidence": [{"table_index": 4, "quote": "Revenue 100"}],
        "expected_guardrail": {"should_answer": True},
        "expected_trace": {"must_have_wiki_facts": True, "fallback_reason": None},
    }
    raw_reply = (
        "[D1] source_type=wiki_metrics, company_id=HK:00700, filing_id=HK:00700:2025-annual, "
        "statement_type=income_statement, canonical_name=revenue, period=2025-12-31, "
        "value=100, raw_value=100, unit=RMB million, currency=RMB, table_index=4, quote=Revenue 100"
    )
    saved_messages: list[tuple[str, str, str]] = []
    captured_records: list[dict] = []

    async def fake_prepare_envelope(message, *_args, **_kwargs):
        return runtime.ChatRequestEnvelope(
            all_attachments=[],
            message_hash="hash-live-runtime-trace-1",
            user_display_message=message,
        )

    async def fake_preflight(*_args, **_kwargs):
        return runtime.ChatRunPreflightContext(history=[], local_memory_context=None, attachments=[])

    async def fake_save_message(_session, role, content, session_id, **_kwargs):
        saved_messages.append((role, content, session_id))

    async def fake_refresh(_session, _profile, _session_id):
        return None

    async def fake_wait_for_pdf_attachment_parses(_attachments):
        return None

    async def fake_analyze_images(*_args, **_kwargs):
        return None, True

    async def fake_create_run(run_input, history, *, profile, session_id):
        assert run_input["message"] == case["question"]
        assert history == []
        assert profile == "siq_assistant"
        assert session_id == runtime.hermes_runs_session_id("siq_assistant", "live-runtime-session")
        return "run-live-runtime-trace-1"

    async def fake_collect_run_result(run_id, *, profile, timeout):
        assert run_id == "run-live-runtime-trace-1"
        assert profile == "siq_assistant"
        assert timeout == runtime.hermes_timeout()
        return raw_reply

    monkeypatch.setenv("SIQ_ANSWER_AUDIT_TRACE_LOG_PATH", str(trace_log))
    audit.RECENT_ANSWER_AUDIT_TRACES.clear()
    monkeypatch.setattr(runtime, "_prepare_chat_request_envelope", fake_prepare_envelope)
    monkeypatch.setattr(runtime, "build_wiki_catalog_reply", lambda _message: None)
    monkeypatch.setattr(runtime, "_load_chat_run_preflight_context", fake_preflight)
    monkeypatch.setattr(runtime, "wait_for_pdf_attachment_parses", fake_wait_for_pdf_attachment_parses)
    monkeypatch.setattr(runtime, "_attachments_with_fresh_metadata", lambda attachments: attachments)
    monkeypatch.setattr(runtime, "save_message", fake_save_message)
    monkeypatch.setattr(runtime, "refresh_session_memory", fake_refresh)
    monkeypatch.setattr(runtime, "analyze_images_with_primary_model", fake_analyze_images)
    monkeypatch.setattr(runtime, "normalize_evidence_trace_for_display", lambda reply: reply)
    monkeypatch.setattr(runtime, "build_hermes_run_input", lambda message, **kwargs: {"message": message, **kwargs})
    monkeypatch.setattr(runtime, "create_run", fake_create_run)
    monkeypatch.setattr(runtime, "collect_run_result", fake_collect_run_result)
    monkeypatch.setattr(runtime, "_remember_completed_run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "_record_financial_llm_provenance_if_needed", lambda **_kwargs: None)

    async def run_case():
        return await runtime._collect_chat_reply_impl(
            case["question"],
            object(),
            session_id="live-runtime-session",
            profile="siq_assistant",
            context={
                "question_id": case["case_id"],
                "company": {"market": "HK", "id": "HK:00700"},
                "resolved_period": {"period": "2025-12-31", "filing_id": "HK:00700:2025-annual"},
                "query_plan": {"mode": "wiki_first", "allow_postgres_fallback": True},
            },
            enforce_evidence_contract=False,
            answer_audit_callback=captured_records.append,
        )

    reply = anyio.run(run_case)
    _write_jsonl(case_root / "cases.jsonl", [case])

    report = benchmark.run_benchmark(case_root=case_root, trace_log=trace_log, mode="trace-offline")

    assert reply == raw_reply
    assert saved_messages == [
        ("user", case["question"], "live-runtime-session"),
        ("assistant", raw_reply, "live-runtime-session"),
    ]
    assert captured_records and captured_records[0]["question_id"] == "live-runtime-trace-1"
    assert trace_log.exists()
    assert report["passed"] is True
    assert report["results"][0]["facts"][0]["source_bucket"] == "wiki_facts"
    assert report["summary"]["key_fact_accuracy"] == 1.0


def test_collect_stream_run_records_answer_audit_without_changing_visible_reply(monkeypatch):
    async def run_case():
        state = runtime.ActiveRunState(
            profile="siq_assistant",
            session_id="audit-stream-session",
            run_id="run-audit-stream",
        )
        state.original_message = "收入是多少？"
        state.context = {"question_id": "q-stream-audit"}
        saved: list[tuple[str, str, str, str]] = []
        remembered: list[tuple[str, str, str | None, str]] = []
        done_replies: list[str] = []

        async def fake_stream_run(*_args, **_kwargs):
            yield _StreamEvent("delta", "最终回答")
            yield _StreamEvent("done", "最终回答")

        async def fake_save_message_in_background(role, content, session_id, *, profile):
            saved.append((role, content, session_id, profile))

        async def fake_done_payload(reply):
            done_replies.append(reply)
            return {"new_achievements": [], "reply_seen": reply}

        def fake_remember(profile, session_id, message_hash, reply):
            remembered.append((profile, session_id, message_hash, reply))

        monkeypatch.setattr(runtime, "stream_run", fake_stream_run)
        monkeypatch.setattr(runtime, "save_message_in_background", fake_save_message_in_background)
        monkeypatch.setattr(runtime, "_remember_completed_run", fake_remember)
        monkeypatch.setattr(runtime, "_record_financial_llm_provenance_if_needed", lambda **_kwargs: None)
        monkeypatch.setattr(
            runtime.agent_runtime_answer_audit,
            "record_answer_audit_trace_for_reply",
            lambda **_kwargs: {
                "schema_version": audit.ANSWER_AUDIT_TRACE_SCHEMA,
                "trace_id": "aat_fedcba0987654321fedcba0987654321",
                "question_id": "q-stream-audit",
                "wiki_facts": [],
                "postgres_facts": [],
                "citations": [],
                "calculator_runs": [],
                "guardrail_result": {"blocked": False},
            },
        )
        await runtime._collect_stream_run(
            state,
            fake_done_payload,
            enforce_evidence_contract=False,
            emit_audit_trace_id=True,
        )
        return state, saved, remembered, done_replies

    state, saved, remembered, done_replies = anyio.run(run_case)

    assert [event["event"] for event in state.events] == ["progress", "delta", "progress", "done"]
    assert state.content == "最终回答"
    assert state.done_payload is not None
    assert state.done_payload["content"] == "最终回答"
    assert state.done_payload["audit_trace_id"] == "aat_fedcba0987654321fedcba0987654321"
    assert done_replies == [state.content]
    assert saved == [("assistant", state.content, "audit-stream-session", "siq_assistant")]
    assert remembered == [("siq_assistant", "audit-stream-session", None, state.content)]
