import json
from contextlib import contextmanager
from pathlib import Path

import anyio
from models import ChatMessage

from services import agent_chat_runtime as runtime, hermes_client

SH_BANK_TASK_ID = "fb07089b-9570-4902-bf20-eb38578f2b76"


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
    )

    assert payload["session_id"] == "siq:siq_assistant:siq-assistant-test"
    assert payload["conversation_history"] == [{"role": "user", "content": "上一轮问题"}]


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
                        "aliases": ["苹果公司"],
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
