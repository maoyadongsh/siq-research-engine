import json
from contextlib import contextmanager
from pathlib import Path

import anyio
from models import ChatMessage

from services import agent_chat_runtime as runtime, hermes_client

SH_BANK_TASK_ID = "fb07089b-9570-4902-bf20-eb38578f2b76"


def test_us_task_scope_rejects_foreign_pdf_but_other_markets_keep_existing_behavior(monkeypatch, tmp_path):
    task_id = "dab4d056-3c8b-4e7d-8cf8-d46b743ca1bd"
    result_dir = tmp_path / task_id
    result_dir.mkdir()
    (result_dir / "result.md").write_text("Rio Tinto", encoding="utf-8")
    (result_dir / "quality_report.json").write_text(
        json.dumps(
            {
                "task_id": task_id,
                "filename": "Rio-Tinto-plc_EU_RIO_2025-12-31_年报.pdf",
                "market": "EU",
            }
        ),
        encoding="utf-8",
    )
    company_dir = tmp_path / "NVDA-NVIDIA-CORP"
    company_dir.mkdir()
    monkeypatch.setattr(runtime, "_resolve_company_dirs", lambda *_args, **_kwargs: [company_dir])
    monkeypatch.setattr(runtime, "_company_wiki_contains_task_id", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(runtime, "_pdf2md_task_result_dir", lambda _task_id: result_dir)

    us_context = {
        "company": {"market": "US", "name": "NVIDIA CORP", "code": "NVDA"},
        "research_identity": {
            "market": "US",
            "company_id": "US:0001045810",
            "filing_id": "US:0001045810:0001045810-26-000021",
            "parse_run_id": "run-nvda-2026",
        },
    }
    eu_context = {
        "company": {"market": "EU", "name": "NVIDIA CORP", "code": "NVDA"},
        "research_identity": {
            "market": "EU",
            "company_id": "EU:NVDA",
            "filing_id": "EU:NVDA:2025",
            "parse_run_id": "run-eu-2025",
        },
    }

    assert not runtime._task_id_matches_research_context(task_id, "分析英伟达", us_context)
    assert runtime._task_id_matches_research_context(task_id, "分析英伟达", eu_context)


class _FakePgCursor:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, *_args, **_kwargs):
        return None

    def fetchall(self):
        return []


class _FakePgConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    @contextmanager
    def cursor(self):
        yield _FakePgCursor()


class _FakeFinancialQueryModule:
    @staticmethod
    def get_connection():
        return _FakePgConnection()

    @staticmethod
    def merge_parse(_query_text, _use_hermes):
        return {
            "company_name": "上汽集团",
            "query_type": "company_all",
            "statement_scope": "consolidated",
        }

    @staticmethod
    def resolve_company(_cur, _parsed, _query_text):
        return {
            "company_id": "600104-上汽集团",
            "stock_code": "600104",
            "stock_name": "上汽集团",
            "exchange": "SSE",
        }

    @staticmethod
    def infer_metric_from_database(_cur, parsed, _company, query_text):
        if "商誉" in query_text:
            parsed["query_type"] = "metric"
            parsed["statement_type"] = "balance_sheet"
            parsed["metric_name"] = "商誉"
            parsed["canonical_name"] = "goodwill"
            parsed["metric_terms"] = ["商誉", "goodwill"]

    @staticmethod
    def query_metric_from_split_tables(_cur, parsed, _company, _limit):
        if parsed.get("metric_name") != "商誉":
            return [], []
        return ["pdf2md.financial_balance_sheet_items"], [
            {
                "source_table": "pdf2md.financial_balance_sheet_items",
                "task_id": "task-goodwill",
                "stock_name": "上汽集团",
                "statement_id": "balance_sheet",
                "period_key": "2025-12-31",
                "item_name": "商誉",
                "canonical_name": "goodwill",
                "raw_value": "1,183,122,320.47",
                "unit": "元",
                "source_page_number": 88,
                "source_table_index": 12,
            }
        ]

    @staticmethod
    def query_metric_from_wide(_cur, _parsed, _company, _limit):
        return [], []

    @staticmethod
    def query_company_all_metrics(_cur, _parsed, _company, _limit):
        return ["pdf2md.financial_all_metrics_wide"], [
            {"source_table": "pdf2md.financial_all_metrics_wide", "metric_name": "货币资金"},
            {"source_table": "pdf2md.financial_all_metrics_wide", "metric_name": "长期股权投资"},
        ]

    @staticmethod
    def dedupe_response_rows(rows, limit):
        return rows[:limit]

    @staticmethod
    def normalize_json(value):
        return value


class _FakeMultiMarketFinancialQueryModule(_FakeFinancialQueryModule):
    @staticmethod
    def query_market_agent_view_result(query_text, parsed, company_hint=None, *, limit=20, market=None):
        if "收入" not in query_text:
            return None
        assert company_hint["dir"].endswith("/data/wiki/hk/companies/00700-TENCENT")
        return {
            "question": query_text,
            "query_text": query_text,
            "parsed": {
                **parsed,
                "market": "HK",
                "query_type": "metric",
                "query_mode": "multi_market_agent_view",
                "resolved_company_id": "HK:00700",
                "resolved_stock_code": "00700",
                "resolved_stock_name": "Tencent",
                "metric_name": "收入",
            },
            "source_tables": ["pdf2md_hk.v_agent_financial_facts"],
            "rows": [
                {
                    "source_table": "pdf2md_hk.v_agent_financial_facts",
                    "task_id": "parse-hk-00700",
                    "filing_id": "HK:00700:2025-annual",
                    "company_id": "HK:00700",
                    "stock_code": "00700",
                    "stock_name": "Tencent",
                    "statement_id": "income_statement",
                    "period_key": "2025-12-31",
                    "item_name": "Revenues",
                    "metric_name": "收入",
                    "canonical_name": "revenue",
                    "raw_value": "751,766",
                    "unit": "RMB million",
                    "source_page_number": 42,
                    "source_table_index": 4,
                    "evidence_id": "ev-revenue",
                }
            ][:limit],
        }


def test_hermes_run_payload_includes_session_id_and_history():
    payload = hermes_client._build_run_payload(
        "siq_assistant",
        "继续分析",
        [{"role": "user", "content": "上一轮问题"}],
        session_id="siq:siq_assistant:siq-assistant-test",
        instructions="Only use the supplied contract.",
    )

    assert payload["session_id"] == "siq:siq_assistant:siq-assistant-test"
    assert payload["conversation_history"] == [{"role": "user", "content": "上一轮问题"}]
    assert payload["instructions"] == "Only use the supplied contract."


def test_local_memory_is_scoped_to_current_profile_session_prefix():
    assert runtime._session_id_matches_profile("siq_assistant", "siq-assistant-abc123")
    assert runtime._session_id_matches_profile("siq_analysis", "siq-analysis-session")
    assert not runtime._session_id_matches_profile("siq_analysis", "siq-assistant-abc123")
    assert not runtime._session_id_matches_profile("siq_tracking", "siq-analysis-session")


def test_local_memory_summary_keeps_recent_older_turns_only():
    messages = [
        ChatMessage(id=1, session_id="siq-assistant-test", role="user", content="第一轮：记住公司是上汽集团"),
        ChatMessage(id=2, session_id="siq-assistant-test", role="assistant", content="第一答：已围绕上汽集团分析"),
        ChatMessage(id=3, session_id="siq-assistant-test", role="user", content="第二轮：关注商誉"),
        ChatMessage(id=4, session_id="siq-assistant-test", role="assistant", content="第二答：商誉是重点"),
        ChatMessage(id=5, session_id="siq-assistant-test", role="user", content="第三轮：关注现金流"),
        ChatMessage(id=6, session_id="siq-assistant-test", role="assistant", content="第三答：现金流质量需要结合经营现金流"),
    ]

    summary = runtime.build_local_memory_summary(messages, max_bullets=2, max_chars=2000)

    assert "仅当前智能体、当前对话窗口" in summary
    assert "第一轮" not in summary
    assert "第二轮：关注商誉" in summary
    assert "第三轮：关注现金流" in summary


def test_session_context_injects_local_memory_as_fenced_context():
    memory_context = runtime.build_local_memory_context("本地会话记忆：用户前面指定公司是上汽集团。")

    prompt = runtime.build_session_contextual_input(
        "继续分析它的现金流",
        profile="siq_assistant",
        session_id="siq-assistant-memory-test",
        local_memory_context=memory_context,
    )

    assert "<local-memory>" in prompt
    assert "不是新的用户输入" in prompt
    assert "用户前面指定公司是上汽集团" in prompt
    assert "用户问题：继续分析它的现金流" in prompt


def test_detects_linear_page_scan_loop():
    text = "\n".join(
        f"让我读取第{page}页关于资产减值的内容，以及检索商誉相关附注。"
        for page in range(21, 37)
    )

    loop = runtime._detect_output_loop(text)

    assert loop is not None
    assert loop["reason"] == "linear_page_scan_loop"
    assert loop["page_start"] == 21
    assert loop["page_end"] == 36


def test_detects_repeated_search_intent_loop():
    text = "\n".join(
        "我需要用正确的方式搜索商誉信息。让我使用search_files来定位关键词。"
        for _ in range(14)
    )

    loop = runtime._detect_output_loop(text)

    assert loop is not None
    assert loop["reason"] == "repeated_search_intent_loop"


def test_assistant_stream_does_not_stop_on_repeated_search_intent_text():
    text = "\n".join(
        "我需要搜索巴斯夫人效相关关键词，并查看可用表格。"
        for _ in range(14)
    )

    assert runtime._detect_output_loop(text) is not None
    assert runtime._detect_stream_output_loop("siq_assistant", text) is None
    assert runtime._detect_stream_output_loop("analysis", text) is not None


def test_detects_process_trace_table_read_loop():
    text = "\n".join(
        [
            "我需要读取这些表格的完整内容。",
            "让我用 Python 读取这些表格的完整内容。",
        ]
        * 7
    )

    loop = runtime._detect_output_loop(text)

    assert loop is not None
    assert loop["reason"] in {"same_line_repeated", "process_trace_loop", "repeated_search_intent_loop"}


def test_stopped_reply_discards_partial_process_trace():
    partial = "\n".join(
        [
            "我需要读取这些表格的完整内容。",
            "让我用 Python 读取这些表格的完整内容。",
        ]
        * 7
    )

    reply = runtime._failed_run_reply_for_history(f"{partial}\n\n{runtime.STOPPED_MESSAGE}")

    assert reply == runtime.OUTPUT_LOOP_STOP_MESSAGE
    assert "Python 读取" not in reply


def test_loop_history_sanitizer_replaces_bad_context():
    text = "\n".join(
        f"让我读取第{page}页关于资产减值的内容，以及检索商誉相关附注。"
        for page in range(21, 37)
    )

    sanitized = runtime._sanitize_assistant_history_reply(text)

    assert "上一轮助手输出已因循环被系统截断" in sanitized
    assert "linear_page_scan_loop" in sanitized
    assert "第21页" not in sanitized


def test_loop_polluted_history_is_not_sent_back_to_hermes():
    polluted = (
        "[系统已整理] 上一轮助手输出疑似进入循环，详细重复内容已从后续上下文中移除。"
        "请基于当前用户问题重新定位数据，不要沿用上一轮的逐页扫描或重复搜索过程。\n\n"
        "## 引用来源\n"
        "[D1] source_type=wiki_document_links, file=semantic/document_links.json, "
        "metric=(1).商誉账面原值, task_id=7dbc35a7-7626-4e81-810e-5dbb764434e0, "
        "pdf_page=137, table_index=165, md_line=4186。"
    )
    messages = [
        ChatMessage(id=1, session_id="loop-history-test", role="user", content="分析一下上汽集团商誉"),
        ChatMessage(id=2, session_id="loop-history-test", role="assistant", content=polluted),
        ChatMessage(id=3, session_id="loop-history-test", role="user", content="重新回答"),
    ]

    history = runtime.normalize_history(messages)

    assert all("系统已整理" not in item["content"] for item in history)
    assert all("source_type=wiki_document_links" not in item["content"] for item in history)
    assert history == [{"role": "user", "content": "重新回答"}]


def test_loop_polluted_history_payload_displays_stop_message_only():
    polluted = (
        "[系统已整理] 上一轮助手输出疑似进入循环，详细重复内容已从后续上下文中移除。"
        "请基于当前用户问题重新定位数据，不要沿用上一轮的逐页扫描或重复搜索过程。\n\n"
        "## 引用来源\n"
        "[D1] source_type=wiki_document_links, file=semantic/document_links.json, "
        "metric=(2).商誉减值准备, task_id=7dbc35a7-7626-4e81-810e-5dbb764434e0, "
        "pdf_page=137, table_index=166, md_line=4196。"
    )
    message = ChatMessage(id=1, session_id="loop-history-payload-test", role="assistant", content=polluted)

    payload = runtime._chat_message_payload(message)

    assert payload["content"] == runtime.OUTPUT_LOOP_STOP_MESSAGE
    assert "系统已整理" not in payload["content"]
    assert "引用来源" not in payload["content"]
    assert "source_type=wiki_document_links" not in payload["content"]


def test_chat_message_payload_preserves_user_content_and_filters_attachment_json(tmp_path):
    attachment_path = tmp_path / "chart.png"
    attachment_path.write_bytes(b"fake-image")
    message = ChatMessage(
        id=10,
        session_id="payload-user-attachment-test",
        role="user",
        content="  请看附件里的趋势  ",
        attachments_json=json.dumps(
            [
                {
                    "filename": "chart.png",
                    "content_type": "image/png",
                    "kind": "image",
                    "size": attachment_path.stat().st_size,
                    "path": str(attachment_path),
                },
                {"filename": "missing-path.png", "path": "  "},
                "not-a-dict",
            ]
        ),
    )

    payload = runtime._chat_message_payload(message)

    assert payload["id"] == 10
    assert payload["session_id"] == "payload-user-attachment-test"
    assert payload["role"] == "user"
    assert payload["content"] == "  请看附件里的趋势  "
    assert payload["created_at"] == message.created_at
    assert payload["attachments"] == [
        {
            "filename": "chart.png",
            "content_type": "image/png",
            "kind": "image",
            "size": attachment_path.stat().st_size,
            "path": str(attachment_path),
        }
    ]


def test_chat_message_payload_tolerates_bad_attachment_json():
    message = ChatMessage(
        id=11,
        session_id="payload-bad-attachment-json-test",
        role="user",
        content="附件 JSON 坏了也要能展示文字",
        attachments_json="{not-json",
    )

    payload = runtime._chat_message_payload(message)

    assert payload["content"] == "附件 JSON 坏了也要能展示文字"
    assert payload["attachments"] == []


def test_chat_message_payload_normalizes_assistant_evidence_for_display():
    content = (
        "[1] source_type=report_md, file=reports/2025-annual/report.md, "
        f"metric=前十名普通股股东, task_id={SH_BANK_TASK_ID}, "
        "pdf_page=135, table_index=135, md_line=2428。"
    )
    message = ChatMessage(
        id=12,
        session_id="payload-assistant-evidence-test",
        role="assistant",
        content=content,
    )

    payload = runtime._chat_message_payload(message)

    assert payload["role"] == "assistant"
    assert "pdf_page=134" in payload["content"]
    assert "table_index=90" in payload["content"]
    assert "printed_page=133" in payload["content"]
    assert f"/api/pdf_page/{SH_BANK_TASK_ID}/134?format=html" in payload["content"]
    assert f"/api/source/{SH_BANK_TASK_ID}/table/90?format=html" in payload["content"]
    assert payload["attachments"] == []


def test_failed_loop_reply_is_saved_without_evidence_supplement():
    polluted = (
        "[系统已整理] 上一轮助手输出疑似进入循环，详细重复内容已从后续上下文中移除。"
        "请基于当前用户问题重新定位数据，不要沿用上一轮的逐页扫描或重复搜索过程。\n\n"
        "## 引用来源\n"
        "[D1] source_type=wiki_document_links, file=semantic/document_links.json, "
        "metric=(1).商誉账面原值, task_id=7dbc35a7-7626-4e81-810e-5dbb764434e0, "
        "pdf_page=137, table_index=165, md_line=4186。"
    )

    reply = runtime._failed_run_reply_for_history(polluted)

    assert reply == runtime.OUTPUT_LOOP_STOP_MESSAGE
    assert "引用来源" not in reply
    assert "source_type=wiki_document_links" not in reply


def test_normal_financial_answer_is_not_flagged():
    text = "广汽集团商誉账面价值来自合并资产负债表和商誉附注，风险评估应结合减值准备和资产组信息。"

    assert runtime._detect_output_loop(text) is None


def test_runtime_evidence_normalization_is_shared_by_all_agent_exits():
    text = (
        "[1] source_type=report_md, file=reports/2025-annual/report.md, "
        f"metric=前十名普通股股东, task_id={SH_BANK_TASK_ID}, "
        "pdf_page=135, table_index=135, md_line=2428。"
    )

    normalized = runtime.normalize_evidence_trace_for_display(text)

    assert "pdf_page=134" in normalized
    assert "table_index=90" in normalized
    assert "printed_page=133" in normalized
    assert f"/api/pdf_page/{SH_BANK_TASK_ID}/134?format=html" in normalized
    assert f"/api/source/{SH_BANK_TASK_ID}/table/90?format=html" in normalized
    assert f"/api/pdf_page/{SH_BANK_TASK_ID}/135" not in normalized


def test_replace_event_updates_active_run_snapshot_content():
    async def run_case():
        state = runtime.ActiveRunState(
            profile="siq_assistant",
            session_id="replace-content-test",
            run_id="run-replace-content-test",
        )
        runtime.ACTIVE_RUNS[("siq_assistant", "replace-content-test")] = state
        try:
            await runtime._append_state_event(state, "delta", {"content": "原始引用"})
            await runtime._append_state_event(state, "replace", {"content": "最终引用含PDF页"})
            snapshot = runtime.get_active_run_snapshot("siq_assistant", "replace-content-test")
        finally:
            runtime.ACTIVE_RUNS.pop(("siq_assistant", "replace-content-test"), None)
        return snapshot

    snapshot = anyio.run(run_case)

    assert snapshot["content"] == "最终引用含PDF页"
    assert "原始引用" not in snapshot["content"]


def test_active_run_snapshot_normalizes_content():
    state = runtime.ActiveRunState(
        profile="siq_assistant",
        session_id="test-session",
        run_id="run-test",
    )
    state.content = (
        "[1] source_type=report_md, file=reports/2025-annual/report.md, "
        f"metric=前十名普通股股东, task_id={SH_BANK_TASK_ID}, "
        "pdf_page=135, table_index=135, md_line=2428。"
    )
    runtime.ACTIVE_RUNS[("siq_assistant", "test-session")] = state
    try:
        snapshot = runtime.get_active_run_snapshot("siq_assistant", "test-session")
    finally:
        runtime.ACTIVE_RUNS.pop(("siq_assistant", "test-session"), None)

    assert "pdf_page=134" in snapshot["content"]
    assert f"/api/source/{SH_BANK_TASK_ID}/table/90?format=html" in snapshot["content"]


def test_note_detail_context_injects_wiki_table_rows_before_hermes():
    prompt = runtime.build_session_contextual_input(
        "上汽集团商誉明细是什么？",
        profile="siq_assistant",
        session_id="note-detail-test",
    )

    assert "后端从本地 Wiki 确定性解析出的附注表格行" in prompt
    assert "华域视觉科技(上海)有限公司" in prompt
    assert "上汽通用汽车金融有限责任公司" in prompt
    assert "source_type=wiki_document_links" in prompt
    assert "table_index=165" in prompt
    assert "/api/source/7dbc35a7-7626-4e81-810e-5dbb764434e0/table/165?format=html" in prompt


def test_direct_note_detail_reply_preserves_full_table_rows_and_blank_cells():
    reply = runtime.build_direct_note_detail_reply("上汽集团商誉明细是什么？")

    assert reply is not None
    assert "表格完整性：完整列出，共 8 行" in reply
    assert "Co wheels UK & Trip IQ" in reply
    assert "| Co wheels UK & Trip IQ | 66,724,864.08 |  |  | 66,724,864.08 |" in reply
    assert "空白单元格表示原表为空或未披露，不得改写为 `0`" in reply
    assert "本期计提了 **5,825,349.96 元**" not in reply
    assert "/api/source/7dbc35a7-7626-4e81-810e-5dbb764434e0/table/165?format=html" in reply
    assert "/api/source/7dbc35a7-7626-4e81-810e-5dbb764434e0/table/166?format=html" in reply


def test_direct_note_detail_reply_handles_composition_wording():
    reply = runtime.build_direct_note_detail_reply("查询一下上汽集团的商誉构成")

    assert reply is not None
    assert "## 1. (1).商誉账面原值" in reply
    assert "华域视觉科技(上海)有限公司" in reply
    assert "table_index=165" in reply
    assert "pdf_page=137" in reply
    assert "本期计提了 **5,825,349.96 元**" not in reply


def test_note_detail_context_injects_metric_analysis_evidence():
    prompt = runtime.build_session_contextual_input(
        "分析一下上汽集团的商誉",
        profile="siq_assistant",
        session_id="note-analysis-context-test",
    )

    assert "后端从本地 Wiki 确定性解析出的附注表格行" in prompt
    assert "后端从本地 Wiki 三大表" in prompt
    assert "1,183,122,320.47" in prompt
    assert prompt.index("file=metrics/three_statements.json") < prompt.index("table_index=165")
    assert "华域视觉科技(上海)有限公司" in prompt
    assert "source_type=wiki_document_links" in prompt
    assert "table_index=165" in prompt
    assert "PostgreSQL 补充底稿" not in prompt


def test_note_detail_context_handles_assistant_style_prefix_for_goodwill_analysis():
    prompt = runtime.build_session_contextual_input(
        "我来分析光环新网2025年年度报告中的商誉情况",
        profile="siq_assistant",
        session_id="halo-goodwill-prefix-test",
    )

    assert "后端从本地 Wiki 确定性解析出的附注表格行" in prompt
    assert "（2）商誉减值准备" in prompt
    assert "table_index=156" in prompt
    assert "pdf_page=182" in prompt
    assert "PostgreSQL 补充底稿" not in prompt


def test_financial_evidence_guard_replaces_uncited_financial_reply():
    reply = runtime.enforce_financial_evidence_contract(
        "查询一下上汽集团的商誉构成",
        None,
        "上汽集团商誉构成在当前资料中无法直接获得。",
    )

    assert "## 引用来源" in reply
    assert "## 主要数据溯源补充" not in reply
    assert "## 主要数据引用来源" not in reply
    assert "source_type=wiki_document_links" in reply
    assert "table_index=165" in reply


def test_financial_evidence_guard_keeps_structured_evidence_reply():
    cited = (
        "## 引用来源\n"
        "[1] source_type=wiki_document_links, file=semantic/document_links.json, "
        "task_id=7dbc35a7-7626-4e81-810e-5dbb764434e0, pdf_page=137, table_index=165, md_line=4186。"
    )

    reply = runtime.enforce_financial_evidence_contract("查询一下上汽集团的商誉构成", None, cited)

    assert reply.count("## 引用来源") == 1
    assert "table_index=165" in reply
    assert "company_id=600104-上汽集团" in reply
    assert "filing_id=CN:600104-上汽集团:2025-annual" in reply
    assert "parse_run_id=7dbc35a7-7626-4e81-810e-5dbb764434e0" in reply
    assert reply.count("## 引用来源") == 1
    assert "## 主要数据溯源补充" not in reply
    assert "## 主要数据引用来源" not in reply
    assert "source_type=wiki_document_links" in reply
    assert "table_index=165" in reply


def test_financial_evidence_guard_replaces_fake_task_id_citation():
    fake_task_id = "00000000-0000-4000-8000-000000000000"
    cited = (
        "## 结论\n"
        "- 上汽集团商誉构成如下。\n\n"
        "## 引用来源\n"
        "[1] source_type=wiki_document_links, file=semantic/document_links.json, "
        f"task_id={fake_task_id}, pdf_page=137, table_index=165, md_line=4186。"
    )

    reply = runtime.enforce_financial_evidence_contract("查询一下上汽集团的商誉构成", None, cited)

    assert "## 证据链无效" in reply
    assert fake_task_id in reply
    assert "后端已阻断原回答" in reply
    assert "source_type=wiki_document_links" in reply
    assert "task_id=7dbc35a7-7626-4e81-810e-5dbb764434e0" in reply
    assert "- 上汽集团商誉构成如下。" not in reply


def test_task_id_guard_blocks_fake_citation_even_without_financial_contract():
    fake_task_id = "00000000-0000-4000-8000-000000000000"
    cited = (
        "这里是一个普通回答。\n\n"
        "## 引用来源\n"
        f"[1] source_type=wiki_document_links, file=semantic/document_links.json, task_id={fake_task_id}, "
        "pdf_page=1, table_index=2, md_line=3。"
    )

    reply = runtime.enforce_financial_evidence_contract("介绍一下系统功能", None, cited)

    assert "## 证据链无效" in reply
    assert fake_task_id in reply
    assert "这里是一个普通回答" not in reply


def test_task_id_exists_accepts_pdf2md_result_dir():
    assert runtime._task_id_exists(SH_BANK_TASK_ID)


def test_financial_evidence_guard_adds_goodwill_source_chain_to_structured_fulltext():
    cited = (
        "## 结论\n"
        "- 光环新网 2025 年商誉减值准备计提 863,685,624.45 元。\n\n"
        "## 引用来源\n"
        "[1] source_type=wiki_report_fulltext, file=reports/2025-annual/report.md, "
        "metric=商誉, canonical_name=goodwill, value=863,685,624.45, unit=元, "
        "company_id=300383-光环新网, report_id=2025-annual, period=2025-annual, "
        "evidence_id=ev-halo-goodwill-impairment, quote=资产减值 | -863,685,624.45 | 报告期计提商誉减值损失, "
        "task_id=b6409cf4-f82a-4496-9d68-3592ebd19a49, pdf_page=47, table_index=26, md_line=768。"
    )

    reply = runtime.enforce_financial_evidence_contract("分析光环新网2025年年度报告中的商誉情况", None, cited)

    assert reply.count("## 引用来源") == 1
    # Goodwill queries now deliberately add the main-statement source before
    # the note/full-text source, even when the model already cited report text.
    assert "[D1]" in reply
    assert "source_type=wiki_metrics" in reply
    assert "table_index=26" in reply
    assert "table_index=21" not in reply
    assert "table_index=22" not in reply


def test_financial_evidence_guard_dedupes_existing_complete_citations():
    cited = (
        "## 结论\n"
        "- 商誉账面原值和减值准备如下。\n\n"
        "## 口径复算\n"
        "- financial_reconciliation_validator.py operation=goodwill_reconciliation status=passed\n\n"
        "## 引用来源\n"
        "[1] source_type=wiki_document_links, file=semantic/document_links.json, metric=(1).商誉账面原值, "
        "task_id=7dbc35a7-7626-4e81-810e-5dbb764434e0, pdf_page=137, table_index=165, md_line=4186。\n"
        "[2] source_type=wiki_document_links, file=semantic/document_links.json, metric=(2).商誉减值准备, "
        "task_id=7dbc35a7-7626-4e81-810e-5dbb764434e0, pdf_page=137, table_index=166, md_line=4196。"
    )

    reply = runtime.enforce_financial_evidence_contract("分析一下上汽集团的商誉", None, cited)

    assert reply.count("## 引用来源") == 1
    assert "## 主要数据溯源补充" not in reply
    assert "## 主要数据引用来源" not in reply
    assert reply.count("table_index=165") == 1
    assert reply.count("table_index=166") == 1


def test_midea_evidence_recompute_accepts_rounded_goodwill_equation_and_removes_old_warning(monkeypatch):
    monkeypatch.setenv("SIQ_FINANCIAL_GUARDRAIL_MODE", "warn")
    old_warning = (
        "## 计算校验无效\n"
        "- 上一轮后端诊断。\n\n"
        "guardrail_status=warning\n"
        "guardrail_reason=financial_calculation_trace_missing\n"
        "calculation_trace_reason=trace_unstructured"
    )
    reply = (
        "## 结论\n"
        "- 美的集团 2025-12-31 商誉账面原值 348.13 亿元 - 减值准备 5.56 亿元 = 账面净值 342.57 亿元。\n\n"
        "## 口径复算\n"
        "- 348.13 亿元 - 5.56 亿元 = 342.57 亿元。\n\n"
        "## 引用来源\n"
        "[D1] source_type=wiki_metrics, task_id=f4dead73-e0de-42b4-b1b7-d8cf217214ee, "
        "pdf_page=132, table_index=89, md_line=2497。\n"
        "[D2] source_type=wiki_document_links, task_id=f4dead73-e0de-42b4-b1b7-d8cf217214ee, "
        "pdf_page=206, table_index=163, md_line=4325。\n\n"
        f"{old_warning}"
    )

    guarded = runtime.enforce_financial_evidence_contract("分析美的集团的商誉", None, reply)

    assert "348.13 亿元 - 5.56 亿元 = 342.57 亿元" in guarded
    assert "## 计算校验无效" not in guarded
    assert "## 计算校验缺失" not in guarded
    assert "## 计算校验提示" not in guarded
    assert "guardrail_status=" not in guarded
    assert "table_index=89" in guarded
    assert "table_index=163" in guarded


def test_midea_evidence_recompute_rejects_reversed_goodwill_equation(monkeypatch):
    monkeypatch.setenv("SIQ_FINANCIAL_GUARDRAIL_MODE", "warn")
    reply = (
        "## 结论\n"
        "- 美的集团 2025-12-31 商誉减值准备 5.56 亿元 - 账面原值 348.13 亿元 = 账面净值 342.57 亿元。\n\n"
        "## 勾稽校验\n"
        "- 5.56 亿元 - 348.13 亿元 = 342.57 亿元。\n\n"
        "## 引用来源\n"
        "[D1] source_type=wiki_metrics, task_id=f4dead73-e0de-42b4-b1b7-d8cf217214ee, "
        "pdf_page=132, table_index=89, md_line=2497。\n"
        "[D2] source_type=wiki_document_links, task_id=f4dead73-e0de-42b4-b1b7-d8cf217214ee, "
        "pdf_page=206, table_index=163, md_line=4325。"
    )

    guarded = runtime.enforce_financial_evidence_contract("分析美的集团的商誉", None, reply)

    assert guarded.count("## 计算校验无效") == 1
    # The three amount conversions are valid, but the reversed equation still
    # cannot satisfy the required gross - allowance = net reconciliation.
    assert "calculation_trace_reason=reconciliation_trace_missing" in guarded


def test_midea_evidence_recompute_accepts_visible_fact_rows_without_model_tool_call(monkeypatch):
    monkeypatch.setenv("SIQ_FINANCIAL_GUARDRAIL_MODE", "warn")
    reply = (
        "## 结论\n"
        "- 美的集团 2025-12-31 商誉净额 34,256,859 千元，较 2024-12-31 增长 15.8%。\n\n"
        "## 商誉明细\n"
        "| 项目 | 2025-12-31（千元） | 2024-12-31（千元） |\n"
        "| --- | ---: | ---: |\n"
        "| 商誉原值（未扣减） | 34,813,270 | 30,150,019 |\n"
        "| 减:减值准备 | (556,411) | (569,005) |\n"
        "| 商誉净额 | 34,256,859 | 29,581,014 |\n\n"
        "## 引用来源\n"
        "[D1] source_type=wiki_metrics, task_id=f4dead73-e0de-42b4-b1b7-d8cf217214ee, "
        "pdf_page=132, table_index=89, md_line=2497。\n"
        "[D2] source_type=wiki_document_links, task_id=f4dead73-e0de-42b4-b1b7-d8cf217214ee, "
        "pdf_page=206, table_index=163, md_line=4325。"
    )

    guarded = runtime.enforce_financial_evidence_contract("分析美的集团的商誉", None, reply)

    assert "## 计算校验无效" not in guarded
    assert "## 计算校验缺失" not in guarded
    assert "guardrail_status=" not in guarded


def test_midea_warn_mode_blocks_proven_wrong_allowance_change_unit_conversion(monkeypatch):
    monkeypatch.setenv("SIQ_FINANCIAL_GUARDRAIL_MODE", "warn")
    reply = (
        "## 结论\n"
        "- 商誉账面价值 342.57 亿元（34,256,859 千元）。\n"
        "- 减值准备余额由 569,005 千元降至 556,411 千元，减少 12.59 亿元。\n\n"
        "## 计算器校验\n"
        "- 减值准备变动额换算为 12.59 亿元；后端变动重算为 12,594 千元。\n\n"
        "- KUKA 集团占比：23,435,302 / 34,813,270 = 67.32%。\n\n"
        "## 勾稽校验\n"
        "- 商誉原值 34,813,270 千元 - 减值准备 556,411 千元 = "
        "主表净额 34,256,859 千元。\n\n"
        "## 引用来源\n"
        "[D1] source_type=wiki_metrics, task_id=f4dead73-e0de-42b4-b1b7-d8cf217214ee, "
        "pdf_page=132, table_index=89, md_line=2497。\n"
        "[D2] source_type=wiki_document_links, task_id=f4dead73-e0de-42b4-b1b7-d8cf217214ee, "
        "pdf_page=206, table_index=163, md_line=4325。"
    )

    guarded = runtime.enforce_financial_evidence_contract("分析美的集团的商誉", None, reply)

    assert "减少 12.59 亿元" not in guarded
    assert "## 计算校验无效" in guarded
    assert "calculation_trace_reason=trace_claim_result_mismatch" in guarded
    assert "operation=normalize_amount" in guarded
    assert "claimed_value=12.59" in guarded
    assert "expected_normalized_value=12594000.0" in guarded
    assert "evidence_value=12594.0" in guarded
    assert "guardrail_status=blocked" in guarded


def test_display_normalization_removes_leaked_process_preamble():
    reply = (
        "当前公司一致，无需重新路由。先验证文献，再用终端补充。\n\n"
        "x一顿乱投（又不是工具调用，只是打输一个表情）\n\n"
        "## 结论\n- 正式答案。"
    )

    displayed = runtime.normalize_evidence_trace_for_display(reply)

    assert displayed == "## 结论\n- 正式答案。"


def test_financial_repair_prompt_contains_exact_failures_and_forbids_process_text():
    prompt = runtime._financial_repair_run_input(
        message="分析上汽集团商誉",
        draft="## 结论\n- 商誉减少 20.91 亿元。",
        validation_failure=(
            "## 计算校验无效\n"
            "- failure_1: operation=normalize_amount claimed_value=20.91 "
            "expected_normalized_value=20913146\n"
            "guardrail_status=blocked"
        ),
    )

    assert "唯一一次校验修复" in prompt
    assert "不得重新路由" in prompt
    assert "failure_1: operation=normalize_amount" in prompt
    assert "expected_normalized_value=20913146" in prompt
    assert "第一轮草稿" in prompt


def test_second_financial_validation_failure_keeps_draft_and_appends_failures():
    reply = runtime._reply_with_financial_validation_failures(
        "## 结论\n- 第二轮正文仍正常输出。",
        "## 计算校验无效\n- failure_1: metric=goodwill_net\n"
        "guardrail_status=blocked\ncalculation_trace_reason=trace_claim_result_mismatch",
    )

    assert reply.startswith("## 结论\n- 第二轮正文仍正常输出。")
    assert "## 校验失败详情" in reply
    assert "失败明细：metric=goodwill_net" in reply
    assert "`trace_claim_result_mismatch`" in reply
    assert "guardrail_status=blocked" not in reply


def test_second_financial_validation_failure_removes_blocking_language():
    reply = runtime._reply_with_financial_validation_failures(
        "## 结论\n- 有证据支持的事实和分析。",
        "## 计算校验无效\n"
        "- 工具名、章节标题和手写 operation/result 文本不构成可信 trace；原始模型回答已被阻断。\n\n"
        "guardrail_status=blocked\n"
        "calculation_trace_reason=reconciliation_trace_missing",
    )

    assert "有证据支持的事实和分析" in reply
    assert "原始模型回答已被阻断" not in reply
    assert "勾稽校验运行记录缺失" in reply
    assert "`reconciliation_trace_missing`" in reply
    assert "后端确定性财务结果包" not in reply


def test_financial_repair_suggestion_keeps_streamed_draft_without_second_rewrite():
    reply = runtime._reply_with_financial_repair_suggestion(
        "## 结论\n- 原始高质量回答。\n\n## 解读要点\n- 保留分析深度。",
        "guardrail_status=blocked\ncalculation_trace_reason=trace_operation_missing",
    )

    assert "原始高质量回答" in reply
    assert "保留分析深度" in reply
    assert "## 计算器校验（存在待核对项）" in reply
    assert "`trace_operation_missing`" in reply
    assert "guardrail_status=blocked" not in reply


def test_financial_repair_suggestion_preserves_full_validation_cards():
    reply = runtime._reply_with_financial_repair_suggestion(
        "## 结论\n- 原始高质量回答。\n\n"
        "## 计算器校验\ntrace_id=model-authored\nstatus: ok",
        "## 计算校验无效\n"
        "guardrail_status=blocked\n"
        "calculation_trace_reason=trace_claim_result_mismatch\n\n"
        "## 计算器校验（存在待核对项）\n"
        "- 状态：2 项运行记录已检测，至少 1 项待核对。\n"
        "- ✅ goodwill_net / total_assets：11.83 ÷ 9602.07 = 0.12%\n"
        "- ✅ goodwill_net：1183122320.47 元 = 11.8312232047 亿元\n"
        "- ⚠️ 正文值与确定性重算结果不一致。\n\n"
        "## 勾稽校验（存在待核对项）\n"
        "- 状态：1 项运行记录已检测，至少 1 项待核对。\n"
        "- ✅ 12.82 − 0.99 = 11.83；与 goodwill_net 一致\n"
        "- ⚠️ 缺少可验证的勾稽运行记录。",
    )

    assert "原始高质量回答" in reply
    assert "## 计算器校验（存在待核对项）" in reply
    assert "goodwill_net / total_assets" in reply
    assert reply.count("✅") == 3
    assert reply.count("⚠️") == 2
    assert "## 勾稽校验（存在待核对项）" in reply
    assert "trace_id=model-authored" not in reply
    assert "guardrail_status=blocked" not in reply


def test_financial_repair_prompt_requires_readable_single_unit_output():
    prompt = runtime._financial_repair_run_input(
        message="分析商誉",
        draft="原稿",
        validation_failure="calculation_trace_reason=reconciliation_trace_missing",
    )

    assert "结论 / 关键数据 / 引用来源" in prompt
    assert "必须在关键数据后增加“解读要点”章节" in prompt
    assert "区分披露事实与分析判断" in prompt
    assert "同一金额只选一种易读单位" in prompt
    assert "不要输出后端结果包" in prompt
    assert "同时含三个金额的完整算式" in prompt
    assert "最小范围编辑" in prompt
    assert "不得重新概括或整体重写" in prompt


def test_financial_repair_quality_gate_rejects_shortened_analysis():
    original = (
        "## 结论\n- 经营现金流含金量较高。\n\n"
        "## 依据/数据\n" + "| 项目 | 金额 |\n| --- | ---: |\n| 经营现金流 | 533 |\n\n" * 2
        + "## 解读要点\n- 现金回流稳健。\n\n"
        + "## 风险/关注点\n- 关注回款节奏。\n\n"
        + "## 后续动作建议\n- 继续核对应收账款。\n\n"
        + "## 引用来源\n[D1] source_type=wiki_metrics\n"
        + "分析正文。" * 160
    )
    repaired = "## 结论\n- 经营现金流较好。\n\n## 引用来源\n[D1] source_type=wiki_metrics"

    assert not runtime._financial_repair_preserves_content_quality(original, repaired)


def test_low_quality_financial_repair_shows_original_and_suggestion():
    original = (
        "## 结论\n- 原始高质量结论。\n\n"
        "## 解读要点\n- 原始分析判断。\n\n"
        "## 风险/关注点\n- 原始风险提示。\n\n"
        "## 引用来源\n[D1] source_type=wiki_metrics\n"
        + "原始详细分析。" * 150
    )
    selected = runtime._select_financial_repair_result(
        first_draft=original,
        first_validation=(
            "guardrail_status=blocked\n"
            "calculation_trace_reason=trace_operation_missing"
        ),
        repaired_draft="## 结论\n- 简略回答。",
        repaired_validation="## 结论\n- 简略回答。",
    )

    assert "原始高质量结论" in selected
    assert "原始分析判断" in selected
    assert "简略回答" in selected
    assert "# 原始回答（流式原稿）" in selected
    assert "# 建议修复稿（对照）" in selected
    assert "内容保真检查未通过" in selected
    assert "## 校验失败详情" not in selected


def test_high_fidelity_financial_repair_shows_both_versions():
    original = (
        "## 结论\n- 占比 12%。\n\n"
        "## 解读要点\n- 风险较低。\n\n"
        "## 引用来源\n[D1] source_type=wiki_metrics"
    )
    repaired = original.replace("12%", "11.69%")

    selected = runtime._select_financial_repair_result(
        first_draft=original,
        first_validation="guardrail_status=blocked\ncalculation_trace_reason=trace_claim_result_mismatch",
        repaired_draft=repaired,
        repaired_validation=repaired,
    )

    assert "11.69%" in selected
    assert "占比 12%" in selected
    assert "# 原始回答（流式原稿）" in selected
    assert "# 建议修复稿（对照）" in selected
    assert "状态：校验通过" in selected
    assert "## 校验失败详情" not in selected


def test_failed_financial_repair_shows_both_versions_and_failure_status():
    selected = runtime._select_financial_repair_result(
        first_draft="## 结论\n- 原稿结论。",
        first_validation="guardrail_status=blocked\ncalculation_trace_reason=trace_operation_missing",
        repaired_draft="## 结论\n- 建议修复结论。",
        repaired_validation=(
            "guardrail_status=blocked\n"
            "calculation_trace_reason=trace_claim_result_mismatch"
        ),
    )

    assert "原稿结论" in selected
    assert "建议修复结论" in selected
    assert "状态：仍有校验项未通过" in selected
    assert "## 校验失败详情" in selected


def test_second_financial_validation_failure_hides_verbose_model_traces_and_backend_pack():
    reply = runtime._reply_with_financial_validation_failures(
        "## 结论\n- 正文。\n\n"
        "## 计算器校验\ntrace_id=calc:1\ninputs: many\nstatus: ok\n\n"
        "## 勾稽校验\ntrace_id=recon:1\ninputs: many\nstatus: ok\n\n"
        "## 引用来源\n[D1] source_type=wiki_metrics",
        "## 计算校验无效\ncalculation_trace_reason=reconciliation_trace_missing\n\n"
        "## 后端确定性财务结果包\n| calculation_id | metric |\n| huge | table |",
    )

    assert "## 结论\n- 正文。" in reply
    assert "## 引用来源\n[D1]" in reply
    assert "trace_id=calc:1" not in reply
    assert "trace_id=recon:1" not in reply
    assert "后端确定性财务结果包" not in reply
    assert reply.count("## 校验失败详情") == 1


def test_single_financial_repair_creates_exactly_one_run(monkeypatch):
    calls = []

    async def fake_create(run_input, history, **kwargs):
        calls.append((run_input, history, kwargs))
        return "repair-run", kwargs.get("route")

    async def fake_collect(run_id, **kwargs):
        assert run_id == "repair-run"
        return "## 结论\n- 已修复。"

    monkeypatch.setattr(runtime, "_create_routed_run", fake_create)
    monkeypatch.setattr(runtime, "_collect_routed_run_result", fake_collect)

    async def run_repair():
        return await runtime._run_single_financial_repair(
            profile="siq_assistant",
            session_id="session-1",
            route=None,
            message="分析商誉",
            draft="错误草稿",
            validation_failure="guardrail_status=blocked",
        )

    repaired, _ = anyio.run(run_repair)

    assert repaired == "## 结论\n- 已修复。"
    assert len(calls) == 1
    assert calls[0][1] == []


def test_missing_cross_page_calculation_locator_is_added_once():
    reply = (
        "## 结论\n- 商誉占归母权益 15.35%。\n\n"
        "## 引用来源\n"
        "[D1] source_type=wiki_metrics, task_id=task-1, pdf_page=132, table_index=89, md_line=2497."
    )
    evidence = (
        {"task_id": "task-1", "pdf_page": 132, "table_index": 89, "md_line": 2497},
        {"task_id": "task-1", "pdf_page": 133, "table_index": 90, "md_line": 2508},
        {"task_id": "task-1", "pdf_page": 133, "table_index": 90, "md_line": 2508},
    )

    completed = runtime._append_missing_calculation_evidence_locators(reply, evidence)

    assert completed.count("pdf_page=132") == 1
    assert completed.count("pdf_page=133") == 1
    assert completed.count("table_index=90") == 1


def test_inline_financial_evidence_labels_are_hidden_after_validation():
    displayed = runtime._strip_inline_financial_evidence_labels_for_display(
        "商誉增加 46.76 亿元 [calc:change-1]；占比 15.35% "
        "（[calc:net-1] / [calc:equity-1]）。引用见 [1]。"
    )

    assert displayed == "商誉增加 46.76 亿元；占比 15.35%。引用见 [1]。"


def test_fullwidth_financial_evidence_labels_are_hidden():
    displayed = runtime._strip_inline_financial_evidence_labels_for_display(
        "商誉净值 11.83 亿元。〔calc:net-1〕引用见 [1]。"
    )

    assert displayed == "商誉净值 11.83 亿元。引用见 [1]。"


def test_financial_display_sanitizer_hides_real_answer_trace_pollution():
    displayed = runtime._sanitize_financial_reply_for_display(
        "## 结论\n\n"
        "- 商誉净值 342.56859 亿元（34,256,859 千元，[calc:7994652a09e1cde3]）。\n"
        "- 商誉占比 15.35%（[calc:net] / [calc:equity]）。\n\n"
        "| 科目 | 金额 |\n| --- | ---: |\n"
        "| 商誉 | 342.56859 亿元（[calc:net]） |\n\n"
        "## 计算器校验\ntrace_id=calc:net\nstatus: ok\n\n"
        "## 勾稽校验\ntrace_id=recon:goodwill\nstatus: ok\n\n"
        "## 引用来源\n[1] source_type=wiki_metrics, pdf_page=132。"
    )

    assert "[calc:" not in displayed
    assert "trace_id=" not in displayed
    assert "## 计算器校验" not in displayed
    assert "## 勾稽校验" not in displayed
    assert "（）" not in displayed
    assert "34,256,859 千元）。" in displayed
    assert "## 引用来源" in displayed
    assert "[1] source_type=wiki_metrics" in displayed


def test_financial_display_sanitizer_keeps_safe_validation_summaries():
    displayed = runtime._sanitize_financial_reply_for_display(
        "## 结论\n- 商誉净值 11.83 亿元。\n\n"
        "## 计算器校验\n"
        "- 商誉净值占总资产 0.12%，后端证据重算一致。\n"
        "trace_id=calc:ratio\nstatus: ok\n\n"
        "## 勾稽校验\n"
        "- 原值 12.82 亿元 - 减值准备 0.99 亿元 = 净值 11.83 亿元，校验通过。\n"
        "schema_version=siq_financial_reconciliation_trace_v1\n\n"
        "## 引用来源\n[1] source_type=wiki_metrics"
    )

    assert "## 计算器校验" in displayed
    assert "后端证据重算一致" in displayed
    assert "## 勾稽校验" in displayed
    assert "校验通过" in displayed
    assert "trace_id=" not in displayed
    assert "status: ok" not in displayed
    assert "schema_version=" not in displayed


def test_saic_wrong_change_conversions_are_blocked_and_replaced_by_deterministic_pack(monkeypatch):
    monkeypatch.setenv("SIQ_FINANCIAL_GUARDRAIL_MODE", "block")
    reply = (
        "## 结论\n"
        "- 本期商誉原值减少 20.91 亿元，减值准备减少 5.83 亿元。\n"
        "- 上海机动车回收服务中心转出 1,508.78 万元。\n\n"
        "## 计算器校验\n"
        "- 商誉 1,183,122,320.47 元约为 11.83 亿元。\n\n"
        "## 勾稽校验\n"
        "- 原值 1,282,085,915.36 元 - 减值准备 98,963,594.89 元 = "
        "净额 1,183,122,320.47 元。\n\n"
        "## 引用来源\n"
        "[D1] source_type=wiki_metrics task_id=7dbc35a7-7626-4e81-810e-5dbb764434e0 "
        "pdf_page=65 table_index=84 md_line=1840\n"
        "[D2] source_type=wiki_document_links task_id=7dbc35a7-7626-4e81-810e-5dbb764434e0 "
        "pdf_page=137 table_index=165 md_line=4186\n"
        "[D3] source_type=wiki_document_links task_id=7dbc35a7-7626-4e81-810e-5dbb764434e0 "
        "pdf_page=137 table_index=166 md_line=4196"
    )

    guarded = runtime.enforce_financial_evidence_contract("分析上汽集团的商誉", None, reply)

    assert "本期商誉原值减少 20.91 亿元" not in guarded
    assert "减值准备减少 5.83 亿元" not in guarded
    assert "guardrail_status=blocked" in guarded
    assert "## 后端确定性财务结果包" in guarded
    assert "goodwill_gross_absolute_change" in guarded
    assert "2091.3146 万元" in guarded
    assert "0.20913146 亿元" in guarded
    assert "goodwill_impairment_allowance_absolute_change" in guarded
    assert "582.535 万元" in guarded
    assert "0.0582535 亿元" in guarded


def test_midea_evidence_recompute_rejects_unbound_model_authored_ratio(monkeypatch):
    monkeypatch.setenv("SIQ_FINANCIAL_GUARDRAIL_MODE", "warn")
    reply = (
        "## 结论\n"
        "- 美的集团 2025-12-31 商誉净额 34,256,859 千元，较 2024-12-31 增长 15.8%。\n"
        "- KUKA 集团 23,435,302 千元，占 68.4%。\n\n"
        "## 商誉明细\n"
        "| 项目 | 2025-12-31（千元） | 2024-12-31（千元） |\n"
        "| --- | ---: | ---: |\n"
        "| 商誉原值（未扣减） | 34,813,270 | 30,150,019 |\n"
        "| 减:减值准备 | (556,411) | (569,005) |\n"
        "| 商誉净额 | 34,256,859 | 29,581,014 |\n\n"
        "## 引用来源\n"
        "[D1] source_type=wiki_metrics, task_id=f4dead73-e0de-42b4-b1b7-d8cf217214ee, "
        "pdf_page=132, table_index=89, md_line=2497。\n"
        "[D2] source_type=wiki_document_links, task_id=f4dead73-e0de-42b4-b1b7-d8cf217214ee, "
        "pdf_page=206, table_index=163, md_line=4325。"
    )

    guarded = runtime.enforce_financial_evidence_contract("分析美的集团的商誉", None, reply)

    assert guarded.count("## 计算校验无效") == 1
    assert "calculation_trace_reason=trace_operation_missing" in guarded


def test_saic_split_note_evidence_recompute_accepts_traceable_calculations(monkeypatch):
    monkeypatch.setenv("SIQ_FINANCIAL_GUARDRAIL_MODE", "warn")
    reply = (
        "## 结论\n"
        "- 上汽集团 2025-12-31 商誉净额 1,183,122,320.47 元，"
        "较 2024-12-31 的 1,198,210,116.59 元降幅 1.26%。\n"
        "- 华域视觉与上汽通用汽车金融合计 1,114,493,515.41 元，"
        "占商誉原值 1,282,085,915.36 元的 86.91%。\n\n"
        "## 勾稽校验\n"
        "- gross - allowance = 1,282,085,915.36 - 98,963,594.89 "
        "= 1,183,122,320.47 = 三表商誉净额。\n\n"
        "## 引用来源\n"
        "[D1] source_type=wiki_metrics, task_id=7dbc35a7-7626-4e81-810e-5dbb764434e0, "
        "pdf_page=65, table_index=84, md_line=1840。\n"
        "[D2] source_type=wiki_document_links, task_id=7dbc35a7-7626-4e81-810e-5dbb764434e0, "
        "pdf_page=137, table_index=165, md_line=4186。\n"
        "[D3] source_type=wiki_document_links, task_id=7dbc35a7-7626-4e81-810e-5dbb764434e0, "
        "pdf_page=137, table_index=166, md_line=4196。"
    )

    guarded = runtime.enforce_financial_evidence_contract("分析一下上汽集团的商誉", None, reply)

    assert "## 计算校验无效" not in guarded
    assert "## 计算校验缺失" not in guarded
    assert "guardrail_status=" not in guarded


def test_saic_attachment_unicode_reconciliation_is_recognized_but_wrong_ratios_still_fail(monkeypatch):
    monkeypatch.setenv("SIQ_FINANCIAL_GUARDRAIL_MODE", "warn")
    reply = (
        "## 结论\n"
        "- 商誉净额 2025-12-31 为 1,183,122,320.47 元，较 2024 年的 "
        "1,198,210,116.59 元下降 1.26%。\n"
        "- 商誉账面原值 2025 年末 1,282,085,915.36 元，较 2024 年的 "
        "1,302,999,061.44 元下降 1.62%。\n"
        "- 商誉减值准备期末 98,963,594.89 元，较 2024 年的 "
        "104,788,944.85 元下降 2.2%。\n"
        "- 商誉高度集中：华域视觉（占 66.1%）与上汽通用汽车金融"
        "（占 26.1%）合计占比 92.2%。\n\n"
        "## 依据/数据\n"
        "| 项目 | 2025 | 2024 |\n"
        "| 商誉净额 | 1,183,122,320.47 元 | 1,198,210,116.59 元 |\n"
        "| 商誉原值 | 1,282,085,915.36 元 | 1,302,999,061.44 元 |\n"
        "| 商誉减值准备 | 98,963,594.89 元 | 104,788,944.85 元 |\n"
        "| 华域视觉 | 781,115,081.73 元 | - |\n"
        "| 上汽通用汽车金融 | 333,378,433.68 元 | - |\n\n"
        "## 勾稽校验\n"
        "- 1,282,085,915.36（原值）‑ 98,963,594.89（减值准备）"
        "= 1,183,122,320.47（净额），校验通过。\n\n"
        "## 引用来源\n"
        "[D1] source_type=wiki_metrics, task_id=7dbc35a7-7626-4e81-810e-5dbb764434e0, "
        "pdf_page=65, table_index=84, md_line=1840。\n"
        "[D2] source_type=wiki_document_links, task_id=7dbc35a7-7626-4e81-810e-5dbb764434e0, "
        "pdf_page=137, table_index=165, md_line=4186。\n"
        "[D3] source_type=wiki_document_links, task_id=7dbc35a7-7626-4e81-810e-5dbb764434e0, "
        "pdf_page=137, table_index=166, md_line=4196。"
    )

    guarded = runtime.enforce_financial_evidence_contract("分析一下上汽集团的商誉", None, reply)

    assert guarded.count("## 计算校验无效") == 1
    assert "guardrail_status=blocked" in guarded
    assert "calculation_trace_reason=reconciliation_trace_missing" not in guarded


def test_saic_correct_natural_language_calculations_pass_with_one_consistent_denominator(monkeypatch):
    monkeypatch.setenv("SIQ_FINANCIAL_GUARDRAIL_MODE", "warn")
    reply = (
        "## 结论\n"
        "- 商誉净额 2025-12-31 为 1,183,122,320.47 元，较 2024-12-31 的 "
        "1,198,210,116.59 元下降 1.26%。\n"
        "- 商誉账面原值 2025-12-31 为 1,282,085,915.36 元，较 2024-12-31 的 "
        "1,302,999,061.44 元下降 1.60%。\n"
        "- 商誉减值准备 2025-12-31 为 98,963,594.89 元，较 2024-12-31 的 "
        "104,788,944.85 元下降 5.56%。\n"
        "- 商誉高度集中：华域视觉（占商誉原值 60.93%）与上汽通用汽车金融"
        "（占商誉原值 26.00%）两者合计占商誉原值 86.93%。\n\n"
        "## 依据/数据\n"
        "| 项目 | 2025-12-31 | 2024-12-31 |\n"
        "| 商誉净额 | 1,183,122,320.47 元 | 1,198,210,116.59 元 |\n"
        "| 商誉原值 | 1,282,085,915.36 元 | 1,302,999,061.44 元 |\n"
        "| 商誉减值准备 | 98,963,594.89 元 | 104,788,944.85 元 |\n"
        "| 华域视觉 | 781,115,081.73 元 | - |\n"
        "| 上汽通用汽车金融 | 333,378,433.68 元 | - |\n\n"
        "## 勾稽校验\n"
        "- 1,282,085,915.36（原值）‑ 98,963,594.89（减值准备）"
        "= 1,183,122,320.47（净额），校验通过。\n\n"
        "## 引用来源\n"
        "[D1] source_type=wiki_metrics, task_id=7dbc35a7-7626-4e81-810e-5dbb764434e0, "
        "pdf_page=65, table_index=84, md_line=1840。\n"
        "[D2] source_type=wiki_document_links, task_id=7dbc35a7-7626-4e81-810e-5dbb764434e0, "
        "pdf_page=137, table_index=165, md_line=4186。\n"
        "[D3] source_type=wiki_document_links, task_id=7dbc35a7-7626-4e81-810e-5dbb764434e0, "
        "pdf_page=137, table_index=166, md_line=4196。"
    )

    guarded = runtime.enforce_financial_evidence_contract("分析一下上汽集团的商誉", None, reply)

    assert "## 计算校验无效" not in guarded
    assert "## 计算校验缺失" not in guarded
    assert "guardrail_status=" not in guarded


def test_direct_note_detail_reply_prioritizes_specific_intent_tables():
    reply = runtime.build_direct_note_detail_reply("上汽集团应收账款账龄是什么？")

    assert reply is not None
    assert "## 1. (1).按账龄披露" in reply
    assert "table_index=103" in reply
    assert "1至2年" in reply
    assert "85,164,501,892.50" in reply
    assert "table_index=105" not in reply
    assert "实际核销的应收账款" not in reply


def test_direct_cash_flow_statement_reply_uses_main_statement_not_document_links():
    reply = runtime.build_direct_statement_metric_reply("上汽集团现金流量表核心数据是多少？")

    assert reply is not None
    assert "34,306,601,905.98" in reply
    assert "-27,561,161,950.79" in reply
    assert "-53,218,400,555.93" in reply
    assert "source_type=wiki_metrics" in reply
    assert "file=metrics/three_statements.json" in reply
    assert "pdf_page=72" in reply
    assert "table_index=88" in reply
    assert "md_line=1904" in reply
    assert "23,910,291,453.29" not in reply
    assert "table_index=163" not in reply
    assert "source_type=wiki_document_links" not in reply


def test_balance_sheet_statement_reply_uses_body_tables_before_notes():
    reply = runtime.build_direct_statement_metric_reply("美的集团资产负债表构成概览")

    assert reply is not None
    assert "85,247,150" in reply
    assert "资产总计" in reply
    assert "608,791,766" in reply
    assert "负债和股东权益总计" in reply
    assert "source_type=wiki_metrics" in reply
    assert "pdf_page=132" in reply
    assert "pdf_page=133" in reply
    assert "table_index=89" in reply
    assert "table_index=90" in reply
    assert "table_index=179" not in reply
    assert "185,420,115,320.45" not in reply
    assert "source_type=wiki_document_links" not in reply


def test_goodwill_book_value_routes_to_balance_sheet_main_statement():
    reply = runtime.build_direct_statement_metric_reply("上汽集团2025年报商誉账面价值是多少？")

    assert reply is not None
    assert "| 商誉 |" in reply
    assert "1,183,122,320.47" in reply
    assert "source_type=wiki_metrics" in reply
    assert "file=metrics/three_statements.json" in reply
    assert "pdf_page=65" in reply
    assert "table_index=84" in reply
    assert "md_line=1840" in reply
    assert "source_type=wiki_document_links" not in reply


def test_goodwill_mixed_query_injects_main_statement_and_note_contexts():
    prompt = runtime.build_session_contextual_input(
        "针对上汽集团（600104）2025 年报商誉情况，给出三层结论（账面价值/原值/减值准备），并核对口径",
        profile="siq_assistant",
        session_id="goodwill-mixed-source-order-test",
    )

    assert "后端从本地 Wiki 三大表结构化数据确定性解析出的主表数据" in prompt
    assert "后端从本地 Wiki 确定性解析出的附注表格行" in prompt
    assert "file=metrics/three_statements.json" in prompt
    assert "table_index=84" in prompt
    assert "1,183,122,320.47" in prompt
    assert "semantic/document_links.json" in prompt
    assert "table_index=165" in prompt
    assert "table_index=166" in prompt
    assert "## 后端确定性财务结果包" in prompt
    assert "goodwill_gross_absolute_change" in prompt
    assert "2091.3146 万元" in prompt
    assert "0.20913146 亿元" in prompt
    assert "goodwill_impairment_allowance_absolute_change" in prompt
    assert "582.535 万元" in prompt
    assert "0.0582535 亿元" in prompt
    assert "goodwill_net_absolute_change" in prompt
    assert "1508.7796 万元" in prompt
    assert "0.15087796 亿元" in prompt
    assert "所有金额换算、变动额及其项目归属只能逐字采用结果包" in prompt


def test_us_financial_prompt_prefers_usd_or_billion_usd_not_hundred_million_guess():
    prompt = runtime.build_session_contextual_input(
        "分析英伟达 2026 年营收",
        profile="siq_assistant",
        session_id="us-sec-unit-display-contract-test",
    )

    assert "美股 SEC/XBRL 金额优先保留原始 USD 或后端结果包中的 billion USD" in prompt
    assert "美股 SEC/XBRL 金额默认优先复制“原始 USD”或“billion USD”列" in prompt
    assert "不得把 billion 数字直接改成亿" in prompt


def test_us_net_asset_query_binds_total_equity_sec_anchor_and_calculation_pack():
    message = "告诉我英伟达的净资产"
    context = runtime._resolved_research_context(message, None)

    result = runtime._three_statement_core_result(message, context)
    assert result is not None
    rows = result["rows"]
    assert len(rows) == 1
    row = rows[0]
    assert row["metric_key"] == "total_equity"
    assert row["raw_value"] == "157293000000"
    assert row["unit"] == "USD"
    assert row["evidence_source_type"] == "sec_xbrl_fact"
    assert row["source_anchor"] == "f-211"
    assert row["source_url"].startswith("https://www.sec.gov/Archives/")
    assert row["pdf_page"] is None
    assert row["table_index"] is None

    evidence = runtime._trusted_financial_calculation_evidence(message, context)
    equity = [
        item
        for item in evidence
        if item.get("metric") == "total_equity" and item.get("period") == "2026-01-25"
    ]
    assert len(equity) == 1
    assert equity[0]["display_billion"] == "157.293 billion USD"
    assert equity[0]["display_100m"] == "1,572.93 亿美元"
    pack = runtime.agent_runtime_financial_evidence.render_deterministic_calculation_pack(evidence)
    assert "157.293 billion USD" in pack
    assert "1,572.93 亿美元" in pack


def test_us_net_asset_answer_uses_sec_anchor_and_successful_calculation_summary():
    message = "告诉我英伟达的净资产"
    reply = "\n".join(
        (
            "## 结论",
            "- 英伟达（NVDA）2026 财年期末净资产（股东权益）为 **157.293 billion USD**，折合 **1,572.93 亿美元**。",
            "",
            "## 引用来源",
            "[1] source_type=sec_xbrl_fact, file=document_full.json, metric=total_equity, period=2026-01-25, "
            "source_anchor=f-211, xbrl_tag=us-gaap:StockholdersEquity",
        )
    )

    guarded = runtime.enforce_financial_evidence_contract(message, None, reply)

    assert "guardrail_status=blocked" not in guarded
    assert "## 计算器校验（全部通过）" in guarded
    assert "状态：1/1 项通过" in guarded
    assert "source_url=https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm" in guarded
    assert "source_anchor=f-211" in guarded
    assert "task_id=" not in guarded
    assert "pdf_page=" not in guarded
    assert "table_index=" not in guarded
    assert "/api/pdf_page/" not in guarded
    assert len([line for line in guarded.splitlines() if "source_type=" in line]) == 1


def test_us_sec_answer_drops_foreign_pdf_task_before_invalid_task_guard():
    message = "分析英伟达的营收"
    wrong_pdf_task = "dab4d056-3c8b-4e7d-8cf8-d46b743ca1bd"
    reply = "\n".join(
        (
            "## 结论",
            "- 英伟达 FY2026 营收为 **215.938 billion USD**，折合 **2,159.38 亿美元**。",
            "",
            "## 引用来源",
            f"[1] source_type=wiki_metrics, file=metrics/three_statements.json, metric=Rio PDF, "
            f"period=2025-annual, task_id={wrong_pdf_task}, pdf_page=223, table_index=258，"
            f"[打开PDF定位页223](/api/pdf_page/{wrong_pdf_task}/223)",
            "[2] source_type=sec_xbrl_fact, file=document_full.json, metric=operating_revenue, "
            "period=2026-01-25, xbrl_tag=us-gaap:Revenues",
        )
    )

    guarded = runtime.enforce_financial_evidence_contract(message, None, reply)

    assert "## 证据链无效" not in guarded
    assert wrong_pdf_task not in guarded
    assert "task_id=" not in guarded
    assert "pdf_page=" not in guarded
    assert "/api/pdf_page/" not in guarded
    assert "source_anchor=f-72" in guarded
    assert "## 计算器校验（全部通过）" in guarded


def test_goodwill_mixed_query_guard_adds_missing_main_statement_source(monkeypatch):
    monkeypatch.setenv("SIQ_FINANCIAL_GUARDRAIL_MODE", "warn")
    cited = (
        "## 结论\n"
        "- 主表商誉账面价值需要与附注原值、减值准备勾稽。\n\n"
        "## 勾稽校验\n"
        "- financial_reconciliation_validator.py operation=goodwill_reconciliation status=passed\n\n"
        "## 引用来源\n"
        "[1] source_type=wiki_document_links, file=semantic/document_links.json, metric=(1).商誉账面原值, "
        "period=2025-annual, task_id=7dbc35a7-7626-4e81-810e-5dbb764434e0, "
        "pdf_page=137, table_index=165, md_line=4186。\n"
        "[2] source_type=wiki_document_links, file=semantic/document_links.json, metric=(2).商誉减值准备, "
        "period=2025-annual, task_id=7dbc35a7-7626-4e81-810e-5dbb764434e0, "
        "pdf_page=137, table_index=166, md_line=4196。"
    )

    reply = runtime.enforce_financial_evidence_contract(
        "针对上汽集团（600104）2025 年报商誉情况，给出三层结论（账面价值/原值/减值准备），并核对口径",
        None,
        cited,
    )

    assert reply.count("## 引用来源") == 1
    assert "source_type=wiki_metrics" in reply
    assert "file=metrics/three_statements.json" in reply
    assert "pdf_page=65" in reply
    assert "table_index=84" in reply
    assert "md_line=1840" in reply
    assert reply.count("table_index=165") == 1
    assert reply.count("table_index=166") == 1


def test_financial_source_routing_contract_is_exposed_to_agent_profiles():
    repo_root = Path(__file__).resolve().parents[3]
    contract_path = repo_root / "agents/hermes/profiles/shared/rules/financial_source_routing_contract.md"
    agent_fact_contract_path = repo_root / "docs/architecture/agent-financial-query-contract.md"
    assistant_soul = (repo_root / "agents/hermes/profiles/siq_assistant/SOUL.md").read_text(encoding="utf-8")
    contract = contract_path.read_text(encoding="utf-8")
    agent_fact_contract = agent_fact_contract_path.read_text(encoding="utf-8")

    assert "financial_source_routing_contract.md" in runtime.CHAT_OUTPUT_CONTRACT
    assert "financial_source_routing_contract.md" in assistant_soul
    assert "混合口径" in contract
    assert "metrics/reports/<report_id>/three_statements.json" in contract
    assert "semantic/document_links.json" in contract
    assert "AgentFinancialFact" in contract
    assert "agent-financial-query-contract.md" in contract
    assert "{schema}.v_agent_financial_facts" in contract
    assert "source_type=postgresql_agent_view" in contract
    assert "financial_source_routing_contract.md" in agent_fact_contract
    for field in (
        "market",
        "schema",
        "company_id",
        "filing_id",
        "parse_run_id",
        "metric_name",
        "canonical_name",
        "period",
        "value",
        "raw_value",
        "unit",
        "currency",
        "source_page",
        "table_index",
        "bbox",
        "evidence_id",
        "quote",
        "source_url",
        "wiki_report_path",
        "source_type",
    ):
        assert f"`{field}`" in agent_fact_contract
        assert field in contract


def test_statement_context_excludes_note_detail_context_for_main_statement_query():
    prompt = runtime.build_session_contextual_input(
        "请评估上汽集团现金流质量",
        profile="siq_assistant",
        session_id="statement-context-test",
    )

    assert "后端从本地 Wiki 三大表结构化数据确定性解析出的主表数据" in prompt
    assert "34,306,601,905.98" in prompt
    assert "source_type=wiki_metrics" in prompt
    assert "table_index=88" in prompt
    assert "semantic/document_links.json` 附注表溯源" in prompt
    assert "后端从本地 Wiki 确定性解析出的附注表格行" not in prompt


def test_postgres_fallback_context_renders_traceable_citations():
    context = runtime._render_postgres_fallback_context(
        {
            "parsed": {
                "query_type": "metric",
                "resolved_stock_name": "测试公司",
                "resolved_stock_code": "000001",
                "resolved_company_id": "000001-测试公司",
                "metric_name": "营业收入",
            },
            "source_tables": ["pdf2md.financial_income_statement_items"],
            "rows": [
                {
                    "source_table": "pdf2md.financial_income_statement_items",
                    "task_id": "task-pg-test",
                    "stock_name": "测试公司",
                    "statement_id": "income_statement",
                    "period_key": "2025",
                    "item_name": "营业收入",
                    "raw_value": "123.45",
                    "unit": "万元",
                    "source_page_number": 42,
                    "source_table_index": 7,
                }
            ],
        }
    )

    assert "PostgreSQL `pdf2md` schema 只读查询" in context
    assert "source_type=postgresql" in context
    assert "table=pdf2md.financial_income_statement_items" in context
    assert "task_id=task-pg-test" in context
    assert "pdf_page=42" in context
    assert "table_index=7" in context
    assert "/api/source/task-pg-test/table/7?format=html" in context


def test_postgres_fallback_prefers_explicit_metric_over_company_all(monkeypatch):
    monkeypatch.setattr(runtime, "_load_financial_query_api", lambda: _FakeFinancialQueryModule)

    context = runtime.build_postgres_fallback_context("分析一下上汽集团的商誉")

    assert context is not None
    assert "查询类型: metric" in context
    assert "metric=商誉" in context
    assert "| pdf2md.financial_balance_sheet_items | 商誉 | 2025-12-31 | 1,183,122,320.47 | 元 |" in context
    assert "metric=货币资金" not in context
    assert "metric=长期股权投资" not in context


def test_postgres_fallback_prefers_multi_market_agent_view(monkeypatch):
    monkeypatch.setattr(runtime, "_load_financial_query_api", lambda: _FakeMultiMarketFinancialQueryModule)

    context = runtime.build_postgres_fallback_context(
        "这家公司2025收入是多少？",
        {
            "company": {
                "name": "Tencent",
                "code": "00700",
                "dir": "/home/maoyd/siq-research-engine/data/wiki/hk/companies/00700-TENCENT",
            }
        },
    )

    assert context is not None
    assert "PostgreSQL `pdf2md_hk` schema 只读查询" in context
    assert "查询类型: metric" in context
    assert "metric=收入" in context
    assert "pdf2md_hk.v_agent_financial_facts" in context
    assert "task_id=parse-hk-00700" in context
    assert "pdf_page=42" in context
    assert "table_index=4" in context


def test_session_context_injects_postgres_fallback_when_wiki_evidence_missing(monkeypatch):
    monkeypatch.setattr(runtime, "build_human_efficiency_evidence_context", lambda message, context=None: None)
    monkeypatch.setattr(runtime, "build_human_capital_context", lambda message, context=None: None)
    monkeypatch.setattr(runtime, "build_three_statement_core_context", lambda message, context=None: None)
    monkeypatch.setattr(runtime, "build_statement_metric_context", lambda message, context=None: None)
    monkeypatch.setattr(runtime, "build_note_detail_context", lambda message, context=None: None)
    monkeypatch.setattr(runtime, "build_postgres_fallback_context", lambda message, context=None: "PG_FALLBACK_CONTEXT")
    monkeypatch.setattr(runtime, "build_wiki_fulltext_fallback_context", lambda message, context=None: None)

    prompt = runtime.build_session_contextual_input(
        "查询000001营业收入",
        profile="siq_assistant",
        session_id="pg-fallback-context-test",
    )

    assert "PG_FALLBACK_CONTEXT" in prompt
    assert "用户问题：查询000001营业收入" in prompt


def test_shanghai_bank_annual_query_uses_annual_report_even_if_primary_is_quarterly():
    prompt = runtime.build_session_contextual_input(
        "上海银行2025年报前十名普通股股东是什么？",
        profile="siq_assistant",
        session_id="sh-bank-annual-report-context-test",
    )

    assert "company_id=601229-上海银行" in prompt
    assert "主报告: report_id=2025-annual" in prompt
    assert "reports/2025-annual/report.md" in prompt
    assert "reports/2025-annual/document_full.json" in prompt
    assert "2025-quarterly-report/report.md" not in prompt


def test_wiki_fulltext_fallback_searches_report_md_before_document_full():
    context = runtime.build_wiki_fulltext_fallback_context("上汽集团市场占有率是多少？")

    assert context is not None
    assert "完整年报 Markdown" in context
    assert "完整解析 JSON" in context
    assert "reports/2025-annual/report.md" in context
    assert "reports/2025-annual/document_full.json" in context
    assert "graph/report.md" in context
    assert "市场占有率 13.1%" in context
    assert "source_type=wiki_report_fulltext" in context
    assert "file=reports/2025-annual/report.md" in context
    assert "task_id=7dbc35a7-7626-4e81-810e-5dbb764434e0" in context
    assert "pdf_page=" in context
    assert "table_index=未返回" in context
    assert "md_line=184" in context


def test_session_context_uses_wiki_fulltext_before_postgres(monkeypatch):
    monkeypatch.setattr(runtime, "build_postgres_fallback_context", lambda message, context=None: "PG_FALLBACK_CONTEXT")
    monkeypatch.setattr(runtime, "build_three_statement_core_context", lambda message, context=None: None)
    monkeypatch.setattr(runtime, "build_statement_metric_context", lambda message, context=None: None)

    prompt = runtime.build_session_contextual_input(
        "上汽集团市场占有率是多少？",
        profile="siq_assistant",
        session_id="wiki-fulltext-before-pg-test",
    )

    assert "全文兜底证据" in prompt
    assert "市场占有率 13.1%" in prompt
    assert "PG_FALLBACK_CONTEXT" not in prompt


def test_agent_intro_does_not_inject_statement_context():
    message = (
        "请以 SIQ 全局财报问答助手的身份，进行一次深度、结构化、但易读的自我介绍。"
        "请说明：你能解决哪些财报研究问题；你如何使用已入库财报、Wiki、PostgreSQL、"
        "语义检索和可回溯证据链；适合用户怎样提问；你会如何处理营收、利润、现金流、"
        "资产负债、毛利率、研发投入等问题；你的输出边界、风险提示和最佳使用方式。"
    )

    prompt = runtime.build_session_contextual_input(
        message,
        profile="siq_assistant",
        session_id="assistant-intro-test",
    )

    assert runtime._is_statement_query(message) is False
    assert runtime._needs_financial_evidence_contract(message) is False
    assert runtime.build_direct_statement_metric_reply(message) is None
    assert (
        runtime.enforce_financial_evidence_contract(
            message,
            None,
            "我是 SIQ 全局财报问答助手，可以帮助你围绕已入库财报提问。",
        )
        == "我是 SIQ 全局财报问答助手，可以帮助你围绕已入库财报提问。"
    )
    assert "后端从本地 Wiki 三大表结构化数据确定性解析出的主表数据" not in prompt
    assert "source_type=wiki_metrics" not in prompt
    assert "请由当前 Hermes profile 的模型按自身角色设定回答" in prompt
    assert "回答格式要求" not in prompt
    assert "财务派生计算硬约束" not in prompt
    assert "用户问题：请以 SIQ 全局财报问答助手的身份" in prompt


def test_agent_intro_ignores_stale_evidence_fallback_cache():
    message = (
        "请以 SIQ 全局财报问答助手的身份，进行一次深度、结构化、但易读的自我介绍。"
        "请说明你会如何处理营收、利润、现金流、资产负债、毛利率、研发投入等问题。"
    )
    session_id = "assistant-intro-cache-test"
    message_hash = runtime._dedupe_hash_with_attachments(message, None, None)
    runtime._remember_completed_run(
        "siq_assistant",
        session_id,
        message_hash,
        "## 证据校验\n- 旧的兜底证据。",
    )

    runtime._forget_recent_completed_run("siq_assistant", session_id, message_hash)

    assert runtime._recent_duplicate_reply("siq_assistant", session_id, message_hash) is None


def test_agent_intro_payload_preserves_active_profile_role():
    analysis_prompt = runtime.build_hermes_run_input(
        "智能体简介",
        profile="siq_analysis",
        session_id="siq-analysis-intro-test",
    )
    tracking_prompt = runtime.build_hermes_run_input(
        "智能体简介",
        profile="siq_tracking",
        session_id="siq-tracking-intro-test",
    )
    legal_prompt = runtime.build_hermes_run_input(
        "智能体简介",
        profile="siq_legal",
        session_id="siq-legal-intro-test",
    )

    assert isinstance(analysis_prompt, str)
    assert isinstance(tracking_prompt, str)
    assert isinstance(legal_prompt, str)
    assert "当前智能体 profile: siq_analysis" in analysis_prompt
    assert "当前智能体名称: 智能分析助手" in analysis_prompt
    assert "当前智能体 profile: siq_tracking" in tracking_prompt
    assert "当前智能体名称: 跟踪助手" in tracking_prompt
    assert "当前智能体 profile: siq_legal" in legal_prompt
    assert "当前智能体名称: 法务助手" in legal_prompt
    assert analysis_prompt != tracking_prompt != legal_prompt
    assert "SIQ 全局财报问答助手" not in analysis_prompt
    assert "SIQ 全局财报问答助手" not in tracking_prompt
    assert "SIQ 全局财报问答助手" not in legal_prompt


def test_financial_runtime_contract_uses_real_cli_subcommands():
    contract = runtime.FINANCIAL_CALCULATION_RUNTIME_CONTRACT

    assert "--format json yoy --current" in contract
    assert "--format json ratio --numerator" in contract
    assert "--format json goodwill --company" in contract
    assert "--current-unit '人民币千元'" in contract
    assert "--denominator-unit '人民币千元'" in contract
    assert "--format json normalize --value" in contract
    assert "不能依赖默认 `元`" in contract
    assert "不存在 `--operation`" in contract
    assert "`growth` 或 `proportion`" in contract
    assert "不得原样重试" in contract


def test_wiki_catalog_reply_reads_current_catalog_for_count_and_list(tmp_path, monkeypatch):
    wiki_root = tmp_path / "wiki"
    meta_dir = wiki_root / "_meta"
    meta_dir.mkdir(parents=True)
    (meta_dir / "company_catalog.json").write_text(
        """
{
  "schema_version": 1,
  "generated_at": "2026-06-12T00:00:00Z",
  "company_count": 2,
  "companies": [
    {
      "company_id": "600104-上汽集团",
      "stock_code": "600104",
      "company_short_name": "上汽集团",
      "status": "ready",
      "report_count": 1,
      "has_three_statement_metrics": true
    },
    {
      "company_id": "GENBASF-BASF",
      "stock_code": "GENBASF",
      "company_short_name": "BASF",
      "status": "ready",
      "report_count": 1,
      "has_three_statement_metrics": false
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime, "WIKI_ROOT", wiki_root)

    count_reply = runtime.build_wiki_catalog_reply("现在入库了多少家公司")
    list_reply = runtime.build_wiki_catalog_reply("请列表展示已入库的公司")
    natural_list_reply = runtime.build_wiki_catalog_reply("现在入库了哪些公司")

    assert count_reply is not None
    assert "一共 **2 家**" in count_reply
    assert "company_catalog.json" in count_reply
    assert "一共 **121 家**" not in count_reply
    assert list_reply is not None
    assert natural_list_reply is not None
    assert "一共 **2 家**" in natural_list_reply
    assert "1. 600104 上汽集团" in list_reply
    assert "2. GENBASF BASF" in list_reply
    assert "三大表指标=无" in list_reply


def test_company_catalog_resolution_falls_back_to_existing_wiki_root(tmp_path, monkeypatch):
    primary_wiki = tmp_path / "primary_wiki"
    fallback_wiki = tmp_path / "fallback_wiki"
    meta_dir = primary_wiki / "_meta"
    meta_dir.mkdir(parents=True)
    (meta_dir / "company_catalog.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "companies": [
                    {
                        "company_id": "GENBASF-BASF",
                        "stock_code": "GENBASF",
                        "company_short_name": "BASF",
                        "aliases": ["巴斯夫"],
                        "company_path": "companies/GENBASF-BASF",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    company_dir = fallback_wiki / "companies" / "GENBASF-BASF"
    company_dir.mkdir(parents=True)
    (company_dir / "company.json").write_text(
        json.dumps({"company_short_name": "BASF", "stock_code": "GENBASF"}, ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr(runtime, "WIKI_ROOT", primary_wiki)
    monkeypatch.setattr(runtime, "PROJECT_WIKI_ROOT", primary_wiki)
    monkeypatch.setattr(runtime, "ASSISTANT_WIKI_ROOT", primary_wiki)
    monkeypatch.setattr(runtime, "WIKI_FALLBACK_ROOTS", (fallback_wiki,))

    assert runtime._resolve_company_dir("分析一下巴斯夫的人效") == company_dir
    assert runtime._resolve_company_dirs("对比万华化学和巴斯夫的人效") == [company_dir]


def test_context_company_hint_wins_over_openshell_legacy_alias_false_positive(tmp_path, monkeypatch):
    wiki_root = tmp_path / "wiki"
    cn_meta = wiki_root / "_meta"
    cn_meta.mkdir(parents=True)
    saic_dir = wiki_root / "companies" / "600104-上汽集团"
    saic_dir.mkdir(parents=True)
    (saic_dir / "company.json").write_text(
        json.dumps(
            {
                "market": "CN",
                "company_id": "600104-上汽集团",
                "stock_code": "600104",
                "company_short_name": "上汽集团",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (cn_meta / "company_catalog.json").write_text(
        json.dumps(
            {
                "market": "CN",
                "companies": [
                    {
                        "market": "CN",
                        "company_id": "600104-上汽集团",
                        "stock_code": "600104",
                        "company_short_name": "上汽集团",
                        "company_path": "companies/600104-上汽集团",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    shell_dir = wiki_root / "eu" / "companies" / "SHELL-Shell-plc"
    shell_dir.mkdir(parents=True)

    class LegacyCitationStub:
        @staticmethod
        def find_company_dir_from_text(text, _wiki_root):
            return shell_dir if "openshell" in str(text).lower() else None

    monkeypatch.setattr(runtime, "WIKI_ROOT", wiki_root)
    monkeypatch.setattr(runtime, "PROJECT_WIKI_ROOT", wiki_root)
    monkeypatch.setattr(runtime, "ASSISTANT_WIKI_ROOT", wiki_root)
    monkeypatch.setattr(runtime, "WIKI_FALLBACK_ROOTS", ())
    monkeypatch.setattr(runtime, "_load_local_citation_module", lambda: LegacyCitationStub())

    context = {
        "market": "cn",
        "company_key": "600104-上汽集团",
        "company": {
            "market": "cn",
            "dir": "600104-上汽集团",
            "name": "上汽集团",
            "code": "600104",
            "company_key": "600104-上汽集团",
        },
    }

    assert runtime._resolve_company_dir("请确认 OpenShell 上的上汽集团分析助手链路", context) == saic_dir.resolve()


def test_openshell_connectivity_question_does_not_trigger_financial_evidence_contract(monkeypatch):
    monkeypatch.setattr(runtime, "_resolve_company_dir", lambda *_args, **_kwargs: Path("/wiki/companies/600104-上汽集团"))
    context = {"company": {"market": "cn", "name": "上汽集团", "code": "600104"}}

    assert not runtime._needs_financial_evidence_contract(
        "请确认 OpenShell 上汽集团分析助手链路在线",
        context,
    )
    assert runtime._needs_financial_evidence_contract(
        "请分析上汽集团的业绩表现",
        context,
    )


def test_company_catalog_resolution_uses_market_specific_catalog(tmp_path, monkeypatch):
    wiki_root = tmp_path / "wiki"
    us_root = wiki_root / "us"
    meta_dir = us_root / "_meta"
    meta_dir.mkdir(parents=True)
    company_dir = us_root / "companies" / "AAPL-Apple-Inc"
    company_dir.mkdir(parents=True)
    (meta_dir / "company_catalog.json").write_text(
        json.dumps(
            {
                "market": "US",
                "companies": [
                    {
                        "market": "US",
                        "company_id": "US:0000320193",
                        "company_wiki_id": "AAPL-Apple-Inc",
                        "ticker": "AAPL",
                        "company_name": "Apple Inc.",
                        "company_wiki_path": "data/wiki/us/companies/AAPL-Apple-Inc",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (company_dir / "company.json").write_text(
        json.dumps({"market": "US", "company_name": "Apple Inc.", "ticker": "AAPL"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime, "WIKI_ROOT", wiki_root)
    monkeypatch.setattr(runtime, "PROJECT_WIKI_ROOT", wiki_root)
    monkeypatch.setattr(runtime, "ASSISTANT_WIKI_ROOT", wiki_root)
    monkeypatch.setattr(runtime, "WIKI_FALLBACK_ROOTS", ())

    assert runtime._resolve_company_dir_from_catalog("分析美股苹果公司的营收") == company_dir.resolve()
    assert runtime._resolve_company_dirs("US AAPL revenue") == [company_dir.resolve()]
    assert runtime._resolve_company_dirs("US Apple Inc revenue") == [company_dir.resolve()]
    assert runtime._resolve_company_dirs("请解释 aapple") == []


def test_explicit_us_financial_query_without_company_identity_fails_closed(monkeypatch):
    monkeypatch.setattr(runtime, "_resolve_company_dir", lambda *_args, **_kwargs: None)

    resolved = runtime._resolved_research_context("分析美股市场不存在公司的营收", None)
    guarded = runtime.enforce_financial_evidence_contract(
        "分析美股市场不存在公司的营收",
        None,
        "该公司营收为 100 亿美元。",
    )

    assert runtime.agent_runtime_context.research_identity(resolved) == {"market": "US"}
    assert guarded.startswith("## 研究身份不完整")
    assert "identity_market=US" in guarded
    assert "100 亿美元" not in guarded


def test_explicit_non_cn_company_resolution_does_not_fall_back_to_cn_local_alias(tmp_path, monkeypatch):
    wiki_root = tmp_path / "wiki"
    us_root = wiki_root / "us"
    us_company_dir = us_root / "companies" / "BA-Boeing"
    us_company_dir.mkdir(parents=True)
    cn_company_dir = wiki_root / "companies" / "000001-平安银行"
    cn_company_dir.mkdir(parents=True)
    meta_dir = us_root / "_meta"
    meta_dir.mkdir(parents=True)
    (meta_dir / "company_catalog.json").write_text(
        json.dumps(
            {
                "market": "US",
                "companies": [
                    {
                        "market": "US",
                        "company_id": "US:0000012927",
                        "company_wiki_id": "BA-Boeing",
                        "ticker": "BA",
                        "company_name": "Boeing",
                        "company_wiki_path": "data/wiki/us/companies/BA-Boeing",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime, "WIKI_ROOT", wiki_root)
    monkeypatch.setattr(runtime, "PROJECT_WIKI_ROOT", wiki_root)
    monkeypatch.setattr(runtime, "ASSISTANT_WIKI_ROOT", wiki_root)
    monkeypatch.setattr(runtime, "WIKI_FALLBACK_ROOTS", ())
    monkeypatch.setattr(
        runtime,
        "_load_local_citation_module",
        lambda: type(
            "LocalCitationStub",
            (),
            {"find_company_dir_from_text": staticmethod(lambda *_args: cn_company_dir)},
        )(),
    )

    assert runtime._resolve_company_dir("查询 US BA revenue") == us_company_dir.resolve()


def test_non_cn_catalog_alias_fallback_requires_exact_bound_report_identity(tmp_path, monkeypatch):
    company_dir = tmp_path / "eu" / "companies" / "AZN-AstraZeneca"
    company_dir.mkdir(parents=True)
    context = {
        "research_identity": {
            "market": "EU",
            "company_id": "EU:GB:AZN",
            "filing_id": "EU:AZN:2025-annual:run-azn",
            "parse_run_id": "EU:run-azn",
        },
        "company": {"name": "AstraZeneca", "code": "AZN"},
    }
    calls = []

    def resolve_from_catalog(message, catalog_context):
        calls.append((message, catalog_context))
        return None if catalog_context is context else company_dir

    monkeypatch.setattr(runtime, "_resolve_company_dir_from_catalog", resolve_from_catalog)
    monkeypatch.setattr(
        runtime,
        "_primary_report_for_company",
        lambda *_args, **_kwargs: {"selection_status": "identity_exact"},
    )

    assert runtime._resolve_company_dir("查询 EU AZN 总资产", context) == company_dir
    assert len(calls) == 2
    assert calls[0][1] is context
    assert calls[1][1] is None


def test_non_cn_catalog_alias_fallback_rejects_report_identity_mismatch(tmp_path, monkeypatch):
    company_dir = tmp_path / "eu" / "companies" / "AZN-AstraZeneca"
    company_dir.mkdir(parents=True)
    context = {
        "research_identity": {
            "market": "EU",
            "company_id": "EU:GB:AZN",
            "filing_id": "EU:AZN:2025-annual:run-azn",
            "parse_run_id": "EU:run-azn",
        }
    }
    monkeypatch.setattr(
        runtime,
        "_resolve_company_dir_from_catalog",
        lambda _message, catalog_context: None if catalog_context is context else company_dir,
    )
    monkeypatch.setattr(
        runtime,
        "_primary_report_for_company",
        lambda *_args, **_kwargs: {"selection_status": "identity_mismatch"},
    )

    assert runtime._resolve_company_dir("查询 EU AZN 总资产", context) is None


def test_resolved_context_prefers_authoritative_report_company_id_over_legacy_catalog(tmp_path, monkeypatch):
    company_dir = tmp_path / "4502-Takeda"
    company_dir.mkdir()
    (company_dir / "company.json").write_text(
        json.dumps(
            {
                "market": "JP",
                "company_id": "JP:JP:4502",
                "ticker": "4502",
                "company_name": "Takeda",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime, "_resolve_company_dir", lambda _message, _context=None: company_dir)
    monkeypatch.setattr(
        runtime,
        "_primary_report_for_company",
        lambda *_args, **_kwargs: {
            "market": "JP",
            "company_id": "JP:4502",
            "report_id": "2025-annual",
            "filing_id": "JP:4502:2025-annual:run-4502",
            "parse_run_id": "JP:run-4502",
        },
    )

    context = runtime._resolved_research_context("分析 JP 4502 总资产")

    assert context["research_identity"] == {
        "market": "JP",
        "company_id": "JP:4502",
        "filing_id": "JP:4502:2025-annual:run-4502",
        "parse_run_id": "JP:run-4502",
    }
    assert context["company"]["company_id"] == "JP:4502"


def test_runtime_catalog_resolution_uses_authoritative_company_id_without_name(tmp_path, monkeypatch):
    wiki_root = tmp_path / "wiki"
    us_root = wiki_root / "us"
    company_dir = us_root / "companies" / "AAPL-Apple-Inc"
    company_dir.mkdir(parents=True)
    meta_dir = us_root / "_meta"
    meta_dir.mkdir(parents=True)
    (meta_dir / "company_catalog.json").write_text(
        json.dumps(
            {
                "market": "US",
                "companies": [
                    {
                        "market": "US",
                        "company_id": "US:0000320193",
                        "company_wiki_id": "AAPL-Apple-Inc",
                        "company_name": "Apple Inc.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime, "WIKI_ROOT", wiki_root)
    monkeypatch.setattr(runtime, "PROJECT_WIKI_ROOT", wiki_root)
    monkeypatch.setattr(runtime, "ASSISTANT_WIKI_ROOT", wiki_root)
    monkeypatch.setattr(runtime, "WIKI_FALLBACK_ROOTS", ())
    identity = {
        "research_identity": {
            "market": "US",
            "company_id": "US:0000320193",
            "filing_id": "US:AAPL:2025-10-K",
            "parse_run_id": "parse-us-aapl",
        }
    }

    assert runtime._resolve_company_dirs_from_catalog("2025 revenue", identity) == [company_dir.resolve()]
    identity["research_identity"]["company_id"] = "US:0000789019"
    assert runtime._resolve_company_dirs_from_catalog("Apple 2025 revenue", identity) == []


def test_result_identity_rejects_cross_filing_without_overwriting_conflict():
    context = {
        "research_identity": {
            "market": "CN",
            "company_id": "000333-美的集团",
            "filing_id": "CN:000333-美的集团:2025-annual",
            "parse_run_id": "task-midea-2025",
        },
        "resolved_period": {"report_id": "2025-annual"},
    }
    result = {
        "company_id": "000333-美的集团",
        "filing_id": "CN:000333-美的集团:2024-annual",
        "report_id": "2025-annual",
        "task_id": "task-midea-2025",
        "tables": [],
    }

    guarded = runtime._result_with_research_identity(result, context)

    assert guarded["filing_id"] == "CN:000333-美的集团:2024-annual"
    assert guarded["report_id"] == "2025-annual"
    assert "task_id" not in guarded
    assert "parse_run_id" not in guarded


def test_result_identity_rejects_cross_report_without_overwriting_conflict():
    context = {
        "research_identity": {
            "market": "CN",
            "company_id": "000333-美的集团",
            "filing_id": "CN:000333-美的集团:2025-annual",
            "parse_run_id": "task-midea-2025",
        },
        "resolved_period": {"report_id": "2025-annual"},
    }
    result = {
        "company_id": "000333-美的集团",
        "filing_id": "CN:000333-美的集团:2025-annual",
        "report_id": "2024-annual",
        "task_id": "task-midea-2025",
        "tables": [],
    }

    guarded = runtime._result_with_research_identity(result, context)

    assert guarded["filing_id"] == "CN:000333-美的集团:2025-annual"
    assert guarded["report_id"] == "2024-annual"
    assert "task_id" not in guarded
    assert "parse_run_id" not in guarded


def test_result_identity_rejects_mixed_tables_without_stamping_expected_identity():
    context = {
        "research_identity": {
            "market": "CN",
            "company_id": "000333-美的集团",
            "filing_id": "CN:000333-美的集团:2025-annual",
            "parse_run_id": "task-midea-2025",
        },
        "resolved_period": {"report_id": "2025-annual"},
    }
    result = {
        "company_id": "000333-美的集团",
        "report_id": "2025-annual",
        "task_id": "task-midea-2025",
        "tables": [
            {
                "company_id": "000333-美的集团",
                "report_id": "2025-annual",
                "task_id": "task-midea-2025",
            },
            {
                "company_id": "600104-上汽集团",
                "report_id": "2025-annual",
                "task_id": "task-saic-2025",
            },
        ],
    }

    guarded = runtime._result_with_research_identity(result, context)

    assert "task_id" not in guarded
    assert "parse_run_id" not in guarded
    assert "filing_id" not in guarded
    assert "filing_id" not in guarded["tables"][0]
    assert guarded["tables"][1]["company_id"] == "600104-上汽集团"
    assert guarded["tables"][1]["task_id"] == "task-saic-2025"


def test_result_identity_checks_conflicting_task_aliases_in_each_row():
    context = {
        "research_identity": {
            "market": "CN",
            "company_id": "000333-美的集团",
            "filing_id": "CN:000333-美的集团:2025-annual",
            "parse_run_id": "task-midea-2025",
        },
        "resolved_period": {"report_id": "2025-annual"},
    }
    result = {
        "company_id": "000333-美的集团",
        "report_id": "2025-annual",
        "task_id": "task-midea-2025",
        "rows": [
            {
                "company_id": "000333-美的集团",
                "report_id": "2025-annual",
                "parse_run_id": "task-midea-2025",
                "task_id": "task-other-filing",
            }
        ],
    }

    guarded = runtime._result_with_research_identity(result, context)

    assert "task_id" not in guarded
    assert "parse_run_id" not in guarded
    assert "filing_id" not in guarded
    assert guarded["rows"][0]["task_id"] == "task-other-filing"
    assert guarded["rows"][0]["parse_run_id"] == "task-midea-2025"


def test_result_identity_keeps_parse_hash_when_parser_task_id_is_separate():
    context = {
        "research_identity": {
            "market": "US",
            "company_id": "US:0000012927",
            "filing_id": "US:0000012927:0001628280-26-004357",
            "parse_run_id": "parse-hash-ba",
        },
        "resolved_period": {"report_id": "2025-10-K-0001628280-26-004357"},
    }
    result = {
        "market": "US",
        "company_id": "US:0000012927",
        "filing_id": "US:0000012927:0001628280-26-004357",
        "parse_run_id": "parse-hash-ba",
        "task_id": "BA-10-K-0001628280-26-004357",
        "report_id": "2025-10-K-0001628280-26-004357",
        "rows": [{"metric_key": "operating_revenue", "report_id": "2025-10-K-0001628280-26-004357"}],
    }

    bound = runtime._result_with_research_identity(result, context)

    assert bound["parse_run_id"] == "parse-hash-ba"
    assert bound["task_id"] == "BA-10-K-0001628280-26-004357"
    assert bound["rows"][0]["parse_run_id"] == "parse-hash-ba"
    assert bound["rows"][0]["task_id"] == "BA-10-K-0001628280-26-004357"


def test_non_cn_statement_metric_rejects_cross_company_resolver_result(monkeypatch):
    renderer = object()
    calls = []

    def resolver(company_text, _lookup_message):
        calls.append(company_text)
        return {
            "market": "CN",
            "company_id": "000001-平安银行",
            "filing_id": "CN:000001-平安银行:2025-annual",
            "parse_run_id": "parse-cn-pingan",
            "task_id": "parse-cn-pingan",
            "tables": [
                {
                    "market": "CN",
                    "company_id": "000001-平安银行",
                    "filing_id": "CN:000001-平安银行:2025-annual",
                    "task_id": "parse-cn-pingan",
                }
            ],
        }

    monkeypatch.setattr(runtime, "_load_statement_metric_tools", lambda _context: (resolver, renderer))
    context = {
        "research_identity": {
            "market": "US",
            "company_id": "US:0000012927",
            "filing_id": "US:0000012927:0001628280-26-004357",
            "parse_run_id": "parse-us-ba",
        },
        "company": {"name": "Boeing", "code": "BA"},
    }

    result, actual_renderer = runtime._statement_metric_result("分析 US BA 的营业收入", context)

    assert result is None
    assert actual_renderer is renderer
    assert calls == ["分析 US BA 的营业收入", "Boeing BA", "分析 US BA 的营业收入\nBoeing BA"]


def test_non_cn_statement_metric_retries_company_hint_after_cross_company_result(monkeypatch):
    renderer = object()
    calls = []
    expected_identity = {
        "market": "US",
        "company_id": "US:0000018230",
        "filing_id": "US:0000018230:0000018230-26-000008",
        "parse_run_id": "parse-us-cat",
    }

    def resolver(company_text, _lookup_message):
        calls.append(company_text)
        if len(calls) == 1:
            return {
                "market": "CN",
                "company_id": "000001-平安银行",
                "report_id": "2025-annual",
                "task_id": "parse-cn-pingan",
                "tables": [{"company_id": "000001-平安银行", "task_id": "parse-cn-pingan"}],
            }
        return {
            **expected_identity,
            "tables": [
                {
                    **expected_identity,
                    "metric_name": "total_liabilities",
                    "value": "89154800000",
                }
            ],
        }

    monkeypatch.setattr(runtime, "_load_statement_metric_tools", lambda _context: (resolver, renderer))
    context = {
        "research_identity": expected_identity,
        "company": {"name": "Caterpillar", "code": "CAT"},
    }

    result, actual_renderer = runtime._statement_metric_result("查看 US CAT 报告期总负债", context)

    assert result is not None
    assert actual_renderer is renderer
    assert runtime.agent_runtime_context.research_identity(result) == expected_identity
    assert result["tables"][0]["company_id"] == "US:0000018230"
    assert calls == ["查看 US CAT 报告期总负债", "Caterpillar CAT"]


def test_non_cn_statement_metric_rejects_nested_cross_company_scope(monkeypatch):
    renderer = object()
    expected_identity = {
        "market": "US",
        "company_id": "US:0000012927",
        "filing_id": "US:0000012927:0001628280-26-004357",
        "parse_run_id": "parse-us-ba",
    }

    def resolver(_company_text, _lookup_message):
        return {
            **expected_identity,
            "resolved_period": {"parse_run_id": expected_identity["parse_run_id"]},
            "tables": [
                {
                    "market": "CN",
                    "company_id": "000001-平安银行",
                    "filing_id": "CN:000001-平安银行:2025-annual",
                    "task_id": "parse-cn-pingan",
                }
            ],
        }

    monkeypatch.setattr(runtime, "_load_statement_metric_tools", lambda _context: (resolver, renderer))
    context = {"research_identity": expected_identity}

    result, actual_renderer = runtime._statement_metric_result("分析 US BA 的营业收入", context)

    assert result is None
    assert actual_renderer is renderer


def test_hk_company_wiki_financial_data_is_core_metrics_candidate(tmp_path, monkeypatch):
    wiki_root = tmp_path / "wiki" / "hk"
    company_dir = wiki_root / "companies" / "00700-TENCENT"
    metrics_file = company_dir / "reports" / "2025-annual-12100024" / "metrics" / "financial_data.json"
    metrics_file.parent.mkdir(parents=True)
    metrics_file.write_text(json.dumps({"statements": []}), encoding="utf-8")
    (company_dir / "company.json").write_text(
        json.dumps(
            {
                "market": "HK",
                "company_id": "HK:00700",
                "stock_code": "00700",
                "company_short_name": "TENCENT",
                "primary_report_id": "2025-annual-12100024",
                "metrics": {
                    "latest": {
                        "financial_data": "reports/2025-annual-12100024/metrics/financial_data.json"
                    }
                },
                "reports": [
                    {
                        "report_id": "2025-annual-12100024",
                        "financial_data": "reports/2025-annual-12100024/metrics/financial_data.json",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime, "WIKI_ROOT", wiki_root)

    paths = runtime._company_artifact_paths(company_dir, "2025-annual-12100024")

    assert paths["three_statements"] == metrics_file


def test_agent_intro_does_not_trigger_wiki_catalog_reply():
    assert runtime.build_wiki_catalog_reply("智能体简介") is None
    assert runtime.build_wiki_catalog_reply(
        "请以 SIQ 智能分析助手的身份自我介绍，并说明提问示例里的公司必须来自当前已入库公司。"
    ) is None


def test_agent_intro_ignores_page_company_context():
    prompt = runtime.build_session_contextual_input(
        "智能体简介",
        profile="siq_tracking",
        session_id="siq-tracking-intro-test",
        context={
            "company": {
                "name": "美的集团",
                "code": "000333",
                "dir": "/home/maoyd/wiki/companies/000333-美的集团",
            },
            "page": {"title": "持续跟踪"},
        },
        allow_initialize=True,
        local_memory_context=runtime.build_local_memory_context("上轮页面公司是美的集团。"),
    )

    assert "profile 元信息请求" in prompt
    assert "用户问题：智能体简介" in prompt
    assert "当前公司" not in prompt
    assert "当前页面" not in prompt
    assert "美的集团" not in prompt
    assert "上轮页面公司" not in prompt


def test_direct_human_capital_reply_keeps_kingfa_structured_table_trace():
    reply = runtime.build_direct_human_capital_reply("分析一下金发科技的人员结构")
    assert reply

    normalized = runtime.normalize_evidence_trace_for_display(reply)

    assert "source_type=wiki_report_table" in normalized
    assert "task_id=23658e24-111e-4399-8c0b-e42c41eeb943" in normalized
    assert "pdf_page=68" in normalized
    assert "table_index=47" in normalized
    assert "md_line=1567" in normalized
    assert "/api/source/23658e24-111e-4399-8c0b-e42c41eeb943/table/47?format=html" in normalized
    assert "pdf_page=29" not in normalized
    assert "table_index=26" not in normalized


def test_direct_human_capital_reply_keeps_midea_structured_table_trace():
    reply = runtime.build_direct_human_capital_reply("根据美的集团2025年年报，分析人员结构")
    assert reply

    normalized = runtime.normalize_evidence_trace_for_display(reply)

    assert "source_type=wiki_report_table" in normalized
    assert "task_id=f4dead73-e0de-42b4-b1b7-d8cf217214ee" in normalized
    assert "pdf_page=77" in normalized
    assert "table_index=39" in normalized
    assert "md_line=1117" in normalized
    assert "/api/source/f4dead73-e0de-42b4-b1b7-d8cf217214ee/table/39?format=html" in normalized
    assert "pdf_page=57" not in normalized
    assert "table_index=28" not in normalized


def test_human_efficiency_query_appends_metric_level_sources_for_basf(monkeypatch):
    assert runtime._resolve_company_dir("分析一下巴斯夫的人效").name == "GENBASF-BASF"
    assert runtime._needs_financial_evidence_contract("分析一下巴斯夫的人效")

    monkeypatch.setenv("SIQ_FINANCIAL_GUARDRAIL_MODE", "warn")
    reply = runtime.enforce_financial_evidence_contract(
        "分析一下巴斯夫的人效",
        None,
        (
            "## 结论\n- BASF 人均营收约 €55.1 万/人。\n\n"
            "## 计算器校验\n"
            "- financial_calculator.py operation=per_capita result=551000"
        ),
    )
    normalized = runtime.normalize_evidence_trace_for_display(reply)

    assert "## 引用来源" in normalized
    assert "## 财务指标溯源补充" not in normalized
    assert "## 指标级引用来源" not in normalized
    assert "source_type=wiki_report_table" in normalized
    assert "metric=营业收入" in normalized
    assert "metric=人力成本" in normalized
    assert "metric=年末员工数" in normalized
    assert "pdf_page=302, table_index=145" in normalized
    assert "pdf_page=412, table_index=260" in normalized
    assert "pdf_page=412, table_index=261" in normalized
    assert "/api/source/03690a47-062e-42eb-9ad7-d609a87cf777/table/145?format=html" in normalized
    assert "/api/source/03690a47-062e-42eb-9ad7-d609a87cf777/table/260?format=html" in normalized


def test_multi_company_human_efficiency_context_includes_each_company_scope_and_basf_sources():
    prompt = runtime.build_session_contextual_input(
        "对比万华化学和巴斯夫的人效",
        profile="siq_assistant",
        session_id="multi-company-human-efficiency-test",
    )

    assert [path.name for path in runtime._resolve_company_dirs("对比万华化学和巴斯夫的人效")] == [
        "600309-万华化学",
        "GENBASF-BASF",
    ]
    assert "本轮问题命中多家公司" in prompt
    assert "company_id=600309-万华化学" in prompt
    assert "company_id=GENBASF-BASF" in prompt
    assert "task_id=f256875c-dad2-4fbf-9240-ef288fea0b0f" in prompt
    assert "task_id=03690a47-062e-42eb-9ad7-d609a87cf777" in prompt
    assert "metric=年末员工数" in prompt
    assert "metric=应付职工薪酬" in prompt
    assert "pdf_page=48, table_index=52" in prompt
    assert "pdf_page=157, table_index=145" in prompt
    assert "/api/source/f256875c-dad2-4fbf-9240-ef288fea0b0f/table/52?format=html" in prompt
    assert "/api/source/f256875c-dad2-4fbf-9240-ef288fea0b0f/table/145?format=html" in prompt
    assert "metric=人力成本" in prompt
    assert "pdf_page=412, table_index=260" in prompt
    assert "/api/source/03690a47-062e-42eb-9ad7-d609a87cf777/table/260?format=html" in prompt


def test_empty_basf_evidence_trace_does_not_satisfy_evidence_contract():
    empty_evidence_reply = (
        "## 结论\n"
        "- BASF evidence_index 和 pdf_refs 为空，因此无法溯源。\n\n"
        "## 引用来源\n"
        "[1] source_type=wiki_evidence, "
        "file=/home/maoyd/wiki/companies/GENBASF-BASF/evidence/evidence_index.json, "
        "company_id=GENBASF-BASF, task_id=03690a47-062e-42eb-9ad7-d609a87cf777, "
        "evidence_count=0, pdf_page=未返回, table_index=未返回\n"
    )

    assert runtime._has_structured_evidence_trace(empty_evidence_reply) is False

    reply = runtime.enforce_financial_evidence_contract(
        "分析一下巴斯夫的人效",
        None,
        empty_evidence_reply,
    )
    normalized = runtime.normalize_evidence_trace_for_display(reply)

    assert "source_type=wiki_report_table" in normalized
    assert "metric=人力成本" in normalized
    assert "pdf_page=412, table_index=260" in normalized
    assert "/api/source/03690a47-062e-42eb-9ad7-d609a87cf777/table/260?format=html" in normalized


def test_financial_answer_with_generic_citations_gets_primary_data_sources():
    reply = runtime.enforce_financial_evidence_contract(
        "上汽集团营业收入和净利润是多少",
        None,
        (
            "## 结论\n"
            "- 上汽集团营业收入和净利润如下。\n\n"
            "## 引用来源\n"
            "[1] source_type=wiki_metrics, file=metrics/three_statements.json, "
            "task_id=7dbc35a7-7626-4e81-810e-5dbb764434e0, pdf_page=70, table_index=86, md_line=1850。"
        ),
    )
    normalized = runtime.normalize_evidence_trace_for_display(reply)

    assert normalized.count("## 引用来源") == 1
    assert "## 主要数据溯源补充" not in normalized
    assert "## 主要数据引用来源" not in normalized
    assert "营业收入" in normalized
    assert "净利润" in normalized
    assert "task_id=7dbc35a7-7626-4e81-810e-5dbb764434e0" in normalized
    assert "pdf_page=" in normalized
    assert "table_index=" in normalized
    assert "查看可读表格86" in normalized


def test_broad_financial_analysis_gets_core_metric_sources():
    assert runtime._needs_financial_evidence_contract("分析一下上汽集团")

    reply = runtime.enforce_financial_evidence_contract(
        "分析一下上汽集团",
        None,
        "## 结论\n- 上汽集团经营表现需要结合收入、利润、现金流和资产负债结构判断。",
    )

    assert "## 引用来源" in reply
    assert "## 主要数据溯源补充" not in reply
    assert "## 主要数据引用来源" not in reply
    assert "利润表" in reply
    assert "现金流量表" in reply
    assert "资产负债表" in reply
    assert "source_type=wiki_metrics" in reply


def test_fulltext_ratio_without_bound_inputs_is_blocked_after_source_lookup():
    reply = runtime.enforce_financial_evidence_contract(
        "上汽集团市场占有率是多少？",
        None,
        "## 结论\n- 上汽集团市场占有率为 13.1%。",
    )

    assert "## 计算校验缺失" in reply
    assert "guardrail_reason=financial_calculation_trace_missing" in reply
    assert "calculation_trace_reason=calculator_trace_missing" in reply


def test_wiki_fulltext_fallback_requires_specific_terms_for_halo_goodwill():
    result = runtime._wiki_fulltext_fallback_result("分析光环新网2025年年度报告中的商誉情况", None)

    assert result is not None
    rows = result.get("rows") or []
    assert rows
    assert any(row.get("table_index") == 26 for row in rows)
    assert {row.get("pdf_page") for row in rows}.isdisjoint({24, 34, 45})
    assert {row.get("table_index") for row in rows}.isdisjoint({11, 21, 22})


def test_jp_goodwill_query_uses_multilingual_fulltext_fallback():
    result = runtime._wiki_fulltext_fallback_result("分析丰田汽车的商誉", None)

    assert result is not None
    rows = result.get("rows") or []
    assert len(rows) == 1
    assert "のれん" in str(rows[0].get("snippet") or "")
    assert rows[0].get("pdf_page") == 137
    assert runtime._resolve_company_dir("分析丰田汽车的商誉", None).name == "7203-Toyota-Motor-Corporation"


def test_pdf_evidence_url_carries_task_bound_source_token(monkeypatch):
    from routers import source

    monkeypatch.setattr(source, "create_source_access_token", lambda task_id: f"signed-{task_id}")
    monkeypatch.setattr(
        runtime,
        "_load_note_detail_module",
        lambda: runtime.SimpleNamespace(public_api_url=lambda path: f"https://example.test{path}"),
    )

    url = runtime._evidence_url("task-kr", 180, 145, "pdf")

    assert url == "https://example.test/api/pdf_page/task-kr/180?format=html&source_token=signed-task-kr"


def test_source_access_token_replaces_existing_auth_query(monkeypatch):
    from routers import source

    monkeypatch.setattr(source, "create_source_access_token", lambda _task_id: "fresh-token")

    url = runtime._append_source_access_token(
        "https://example.test/api/source/task/table/1?format=html&access_token=jwt&source_token=old",
        "task",
    )

    assert url == "https://example.test/api/source/task/table/1?format=html&source_token=fresh-token"
