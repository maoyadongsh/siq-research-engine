import re
from pathlib import Path

from services import agent_runtime_context


class _ModelLike:
    def __init__(self, payload):
        self._payload = payload

    def model_dump(self, exclude_none=True):
        if not exclude_none:
            return dict(self._payload)
        return {key: value for key, value in self._payload.items() if value is not None}


def test_context_helpers_normalize_model_like_payload():
    context = _ModelLike(
        {
            "company": {"name": "上汽\n集团", "code": " 600104 ", "dir": "/wiki/companies/600104-SAIC"},
            "report": {"title": " 年报 ", "filename": "report.pdf"},
        }
    )

    assert agent_runtime_context.clean_context_value(" A\nB ") == "A B"
    assert agent_runtime_context.context_company(context)["code"] == " 600104 "
    assert "600104" in agent_runtime_context.context_company_hint(context)


def test_forced_context_company_dir_respects_root_boundary(tmp_path):
    wiki_root = tmp_path / "wiki"
    company_dir = wiki_root / "companies" / "600104-SAIC"
    company_dir.mkdir(parents=True)

    allowed = {"force_company": True, "company": {"dir": str(company_dir)}}
    blocked = {"force_company": True, "company": {"dir": str(tmp_path / "outside")}}

    assert agent_runtime_context.forced_context_company_dir(allowed, wiki_root=wiki_root) == company_dir.resolve()
    assert agent_runtime_context.forced_context_company_dir(blocked, wiki_root=wiki_root) is None


def test_analysis_completed_artifacts_and_format_context(tmp_path):
    wiki_root = tmp_path / "wiki"
    analysis_dir = wiki_root / "companies" / "600104-SAIC" / "analysis"
    work_dir = analysis_dir / ".work" / "600104-SAIC-2025-analysis"
    work_dir.mkdir(parents=True)
    for suffix in (".md", ".json", ".html"):
        (analysis_dir / f"600104-SAIC-2025-analysis{suffix}").write_text("ok", encoding="utf-8")
    (work_dir / "final_validation.json").write_text("{\"ok\": true}", encoding="utf-8")

    context = {"company": {"dir": str(analysis_dir.parent), "code": "600104", "name": "SAIC"}}

    artifacts = agent_runtime_context.analysis_completed_artifacts(
        context,
        read_json_file=lambda path: {"ok": True} if Path(path).name == "final_validation.json" else None,
        wiki_root=wiki_root,
    )
    assert artifacts is not None
    assert artifacts["md"].endswith(".md")
    assert "done" in agent_runtime_context.analysis_completion_reply(
        context,
        analysis_completed_artifacts=lambda current: artifacts,
        analysis_completed_message="done",
    )
    assert "用户原始问题" in agent_runtime_context.analysis_completion_guard_input("继续", artifacts)
    assert "当前公司" in agent_runtime_context.build_format_chat_context(
        wiki_root=wiki_root,
        context={"company": {"name": "SAIC", "code": "600104", "dir": str(analysis_dir.parent)}},
        context_header="HEADER",
    )


def test_analysis_completion_guard_intent_helpers():
    force_terms = ("强制重建", "--force")
    status_terms = ("完成了吗", "报告路径")
    report_terms = ("分析报告", "html", ".md")
    generation_terms = ("生成", "重建")

    assert agent_runtime_context.normalized_intent_text("  报告\n路径 ") == "报告路径"
    assert agent_runtime_context.force_rebuild_requested("请 --force 重新来", force_terms)

    assert agent_runtime_context.analysis_completed_guard_applies(
        "报告 路径 在哪",
        status_terms=status_terms,
        report_terms=report_terms,
        generation_terms=generation_terms,
    )
    assert agent_runtime_context.analysis_completed_guard_applies(
        "请重新生成年度分析报告",
        status_terms=status_terms,
        report_terms=report_terms,
        generation_terms=generation_terms,
    )
    assert not agent_runtime_context.analysis_completed_guard_applies(
        "这家公司收入为什么下降",
        status_terms=status_terms,
        report_terms=report_terms,
        generation_terms=generation_terms,
    )
    assert not agent_runtime_context.should_use_analysis_completion_guard(
        "强制重建年度分析报告",
        force_rebuild_terms=force_terms,
        status_terms=status_terms,
        report_terms=report_terms,
        generation_terms=generation_terms,
    )


def test_general_assistant_request_and_context_input():
    assert agent_runtime_context.is_general_assistant_request(
        "你能做什么？",
        request_terms=("你能做什么",),
        subject_terms=("你", "助手"),
    )
    assert not agent_runtime_context.is_general_assistant_request(
        "请分析这份报表",
        request_terms=("你能做什么",),
        subject_terms=("你", "助手"),
    )

    text = agent_runtime_context.build_general_assistant_context_input(
        "你是谁？",
        profile="siq_assistant",
        profile_label="通用助手",
        general_assistant_context="GENERAL",
    )
    assert text.splitlines()[0] == "GENERAL"
    assert "当前智能体 profile: siq_assistant" in text
    assert "当前智能体名称: 通用助手" in text


def test_financial_intent_helpers_are_parameterized_and_exclude_general_requests():
    is_general = lambda message: agent_runtime_context.is_general_assistant_request(
        message,
        request_terms=("你能做什么",),
        subject_terms=("你", "助手"),
    )
    statement_terms = ("营业收入", "现金流")
    note_terms = ("明细", "构成")
    note_metric_terms = ("商誉", "营业收入")
    action_terms = (*note_terms, "多少")

    assert agent_runtime_context.statement_query_applies(
        "营业 收入是多少？",
        statement_terms=statement_terms,
        is_general_assistant_request=is_general,
    )
    assert not agent_runtime_context.statement_query_applies(
        "你能做什么？可以回答营业收入吗？",
        statement_terms=statement_terms,
        is_general_assistant_request=is_general,
    )
    assert agent_runtime_context.note_detail_query_applies(
        "商誉构成明细",
        note_detail_query_terms=note_terms,
        note_detail_exclude_terms=("生成报告",),
        financial_note_metric_terms=note_metric_terms,
        statement_terms=statement_terms,
        is_general_assistant_request=is_general,
    )
    assert not agent_runtime_context.note_detail_query_applies(
        "营业收入是多少",
        note_detail_query_terms=note_terms,
        note_detail_exclude_terms=("生成报告",),
        financial_note_metric_terms=note_metric_terms,
        statement_terms=statement_terms,
        is_general_assistant_request=is_general,
    )
    assert agent_runtime_context.financial_note_metric_query_applies(
        "商誉多少",
        note_detail_query_terms=note_terms,
        note_detail_exclude_terms=("生成报告",),
        financial_note_metric_terms=note_metric_terms,
        financial_evidence_action_terms=action_terms,
        statement_terms=statement_terms,
        is_general_assistant_request=is_general,
    )
    assert agent_runtime_context.direct_statement_answer_applies(
        "现金流核心数据",
        statement_terms=statement_terms,
        statement_direct_terms=("核心数据",),
        note_detail_analysis_terms=("分析",),
        is_general_assistant_request=is_general,
    )
    assert not agent_runtime_context.direct_statement_answer_applies(
        "现金流分析",
        statement_terms=statement_terms,
        statement_direct_terms=("核心数据", "分析"),
        note_detail_analysis_terms=("分析",),
        is_general_assistant_request=is_general,
    )


def test_note_detail_direct_and_context_intent_combinations():
    is_general = lambda message: agent_runtime_context.is_general_assistant_request(
        message,
        request_terms=("你能做什么",),
        subject_terms=("你", "助手"),
    )
    kwargs = {
        "note_detail_query_terms": ("明细", "构成"),
        "note_detail_exclude_terms": ("生成报告",),
        "financial_note_metric_terms": ("商誉", "递延所得税"),
        "statement_terms": ("营业收入", "现金流"),
        "is_general_assistant_request": is_general,
    }
    direct_kwargs = {
        **kwargs,
        "note_detail_direct_terms": ("多少", "列出"),
        "note_detail_analysis_terms": ("分析", "趋势"),
    }
    context_kwargs = {
        **kwargs,
        "financial_evidence_action_terms": ("多少", "证据", "来源"),
    }

    assert agent_runtime_context.direct_note_detail_answer_applies("商誉构成明细多少", **direct_kwargs)
    assert not agent_runtime_context.direct_note_detail_answer_applies("商誉构成明细趋势分析", **direct_kwargs)
    assert not agent_runtime_context.direct_note_detail_answer_applies("你能做什么？商誉构成明细多少", **direct_kwargs)
    assert not agent_runtime_context.direct_note_detail_answer_applies("生成报告里的商誉构成明细多少", **direct_kwargs)

    assert agent_runtime_context.note_detail_context_applies("商誉证据来源", **context_kwargs)
    assert agent_runtime_context.note_detail_context_applies("递延所得税构成", **context_kwargs)
    assert not agent_runtime_context.note_detail_context_applies("营业收入是多少", **context_kwargs)
    assert not agent_runtime_context.note_detail_context_applies("你能做什么？商誉证据来源", **context_kwargs)


def test_attachment_helpers_normalize_and_classify_payloads():
    attachments = [
        _ModelLike({"kind": "image", "path": " /tmp/chart.png ", "filename": "chart.png"}),
        {"kind": "document", "path": "/tmp/report.pdf"},
        {"path": "/tmp/default-image.png"},
        {"kind": "image", "path": " "},
        object(),
    ]

    items = agent_runtime_context.attachment_dicts(attachments)

    assert [item["path"] for item in items] == [
        " /tmp/chart.png ",
        "/tmp/report.pdf",
        "/tmp/default-image.png",
    ]
    assert [item["path"] for item in agent_runtime_context.image_attachment_dicts(items)] == [
        " /tmp/chart.png ",
        "/tmp/default-image.png",
    ]
    assert agent_runtime_context.document_attachment_dicts(items) == [{"kind": "document", "path": "/tmp/report.pdf"}]
    assert agent_runtime_context.should_reuse_recent_attachments(
        "继续看刚才那张图",
        re.compile(r"(继续|刚才|图片)"),
    )
    assert not agent_runtime_context.should_reuse_recent_attachments("", re.compile(r"继续"))


def test_session_context_scoping_helpers_build_prompt_text():
    dirs = [Path("/wiki/companies/600104-SAIC"), Path("/wiki/companies/000001-PAB")]

    blocks, items = agent_runtime_context.build_company_context_items(
        "对比收入",
        {"company": {"name": "ignored"}},
        dirs,
        context_for_company_dir=lambda path: {"company": {"dir": str(path)}},
        message_for_company=lambda message, path: f"{path.name} {message}",
    )

    assert blocks == [agent_runtime_context.MULTI_COMPANY_SCOPE_NOTICE]
    assert [item[0] for item in items] == [
        "600104-SAIC 对比收入",
        "000001-PAB 对比收入",
    ]
    assert items[0][1]["company"]["dir"] == str(dirs[0])
    assert agent_runtime_context.scoped_evidence_input("对比收入", {"original": True}, items) == (
        "对比收入",
        {"original": True},
    )

    single_blocks, single_items = agent_runtime_context.build_company_context_items(
        "看收入",
        {"company": {"name": "SAIC"}},
        dirs[:1],
        context_for_company_dir=lambda path: {"company": {"dir": str(path)}},
        message_for_company=lambda message, path: f"{path.name} {message}",
    )
    assert single_blocks == []
    assert agent_runtime_context.scoped_evidence_input("看收入", None, single_items) == (
        "看收入",
        {"company": {"name": "SAIC"}},
    )

    prompt = agent_runtime_context.build_session_contextual_input_text(
        "看收入",
        ["CTX"],
        chat_output_contract="CHAT",
        financial_calculation_runtime_contract="CALC",
    )
    assert prompt == "CTX\n\nCHAT\n\nCALC\n\n用户问题：看收入"


def test_company_context_items_handles_empty_dirs_and_multi_company_notice_override():
    empty_blocks, empty_items = agent_runtime_context.build_company_context_items(
        "看收入",
        {"company": {"name": "SAIC"}},
        [],
        context_for_company_dir=lambda path: {"company": {"dir": str(path)}},
        message_for_company=lambda message, path: f"{path.name} {message}",
    )

    assert empty_blocks == []
    assert empty_items == [("看收入", {"company": {"name": "SAIC"}}, Path())]
    assert agent_runtime_context.scoped_evidence_input("看收入", {"original": True}, empty_items) == (
        "看收入",
        {"company": {"name": "SAIC"}},
    )

    dirs = [Path("/wiki/companies/600104-SAIC"), Path("/wiki/companies/000001-PAB")]
    blocks, items = agent_runtime_context.build_company_context_items(
        "对比",
        None,
        dirs,
        context_for_company_dir=lambda path: None if path.name.startswith("000001") else {"company": {"dir": str(path)}},
        message_for_company=lambda message, path: f"{path.name}:{message}",
        multi_company_scope_notice="CUSTOM NOTICE",
    )

    assert blocks == ["CUSTOM NOTICE"]
    assert items == [
        ("600104-SAIC:对比", {"company": {"dir": "/wiki/companies/600104-SAIC"}}, dirs[0]),
        ("000001-PAB:对比", None, dirs[1]),
    ]
    assert agent_runtime_context.scoped_evidence_input("对比", {"original": True}, items) == (
        "对比",
        {"original": True},
    )


def test_hermes_run_input_text_helpers():
    hints = agent_runtime_context.image_attachment_path_hints(
        [
            {"path": "/tmp/a.png"},
            {"filename": "missing-path.png"},
            {"path": "/tmp/b.png"},
        ]
    )
    assert hints == "[Image attached at: /tmp/a.png]\n[Image attached at: /tmp/b.png]"

    text = agent_runtime_context.build_hermes_run_text(
        "CTX",
        document_context="DOC",
        image_analysis_context="IMG ANALYSIS",
        image_path_hints=hints,
    )
    assert text == "CTX\n\nDOC\n\nIMG ANALYSIS\n\n" + hints

    multimodal = agent_runtime_context.build_hermes_multimodal_run_input(
        text,
        ["data:image/png;base64,aaa", ""],
    )
    assert multimodal == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,aaa"}},
            ],
        }
    ]
