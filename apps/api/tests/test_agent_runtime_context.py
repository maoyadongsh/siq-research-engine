import re
from pathlib import Path

from schemas import ChatRequest

from services import agent_runtime_context


class _ModelLike:
    def __init__(self, payload):
        self._payload = payload

    def model_dump(self, exclude_none=True):
        if not exclude_none:
            return dict(self._payload)
        return {key: value for key, value in self._payload.items() if value is not None}


class _BadModelLike:
    def model_dump(self, exclude_none=True):
        return ["not", "a", "dict"]


def _write_analysis_package(
    company_dir: Path,
    *,
    stock_code: str = "600104",
    short_name: str = "SAIC",
    suffixes: tuple[str, ...] = (".md", ".json", ".html"),
) -> tuple[Path, Path]:
    analysis_dir = company_dir / "analysis"
    work_dir = analysis_dir / ".work" / f"{stock_code}-{short_name}-2025-analysis"
    work_dir.mkdir(parents=True)
    for suffix in suffixes:
        (analysis_dir / f"{stock_code}-{short_name}-2025-analysis{suffix}").write_text("ok", encoding="utf-8")
    (work_dir / "final_validation.json").write_text("{\"ok\": true}", encoding="utf-8")
    return analysis_dir, work_dir


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


def test_research_identity_normalizes_and_fans_out_to_runtime_context(tmp_path):
    wiki_root = tmp_path / "wiki"
    context = _ModelLike(
        {
            "research_identity": {
                "market": "us-sec",
                "company_id": "US_SEC:AAPL",
                "filing_id": "US_SEC:AAPL:10-K:2025",
                "parse_run_id": "parse-us-1",
            },
            "company": {"name": "Apple", "code": "AAPL"},
            "report": {"title": "10-K"},
        }
    )

    identity = agent_runtime_context.research_identity(context)
    normalized = agent_runtime_context.mutable_context_dict(context)
    formatted = agent_runtime_context.build_format_chat_context(
        wiki_root=wiki_root,
        context=context,
        context_header="HEADER",
    )

    assert identity == {
        "market": "US",
        "company_id": "US_SEC:AAPL",
        "filing_id": "US_SEC:AAPL:10-K:2025",
        "parse_run_id": "parse-us-1",
    }
    assert normalized["market"] == "US"
    assert normalized["company"]["company_id"] == "US_SEC:AAPL"
    assert normalized["report"]["parse_run_id"] == "parse-us-1"
    assert normalized["postgres"] == identity
    assert "ResearchIdentity: market=US / company_id=US_SEC:AAPL" in formatted
    assert "filing_id US_SEC:AAPL:10-K:2025" in formatted
    assert "parse_run_id parse-us-1" in formatted


def test_research_identity_can_be_inferred_from_prefixed_ids_without_mutating_legacy_shape():
    context = {
        "company": {"name": "Tencent", "id": "HK:00700"},
        "resolved_period": {"filing_id": "HK:00700:2025-annual"},
    }

    normalized = agent_runtime_context.mutable_context_dict(context)

    assert agent_runtime_context.research_identity(context) == {
        "market": "HK",
        "company_id": "HK:00700",
        "filing_id": "HK:00700:2025-annual",
    }
    assert normalized["research_identity"]["market"] == "HK"
    assert normalized["company"]["company_id"] == "HK:00700"
    assert normalized["resolved_period"]["filing_id"] == "HK:00700:2025-annual"
    assert agent_runtime_context.mutable_context_dict({"company": "demo"}) == {"company": "demo"}


def test_incomplete_non_cn_research_identity_requires_full_parse_scope():
    assert agent_runtime_context.incomplete_non_cn_research_identity(
        {"research_identity": {"market": "US_SEC", "company_id": "US:AAPL"}}
    ) == ("US", ("filing_id", "parse_run_id"))
    assert agent_runtime_context.incomplete_non_cn_research_identity(
        {
            "research_identity": {
                "market": "JP",
                "company_id": "JP:7203",
                "filing_id": "JP:7203:2025-annual",
                "parse_run_id": "parse-jp-7203",
            }
        }
    ) == ("JP", ())
    assert agent_runtime_context.incomplete_non_cn_research_identity({"market": "CN"}) == (None, ())
    assert agent_runtime_context.incomplete_non_cn_research_identity({}) == (None, ())


def test_incomplete_non_cn_financial_identity_skips_wiki_and_parse_only_retrieval(monkeypatch):
    from services import agent_chat_runtime as runtime

    context = {"research_identity": {"market": "HK"}}
    monkeypatch.setattr(runtime, "_needs_financial_evidence_contract", lambda _message, _context: True)

    builders = (
        runtime.build_company_wiki_scope_context,
        runtime._wiki_fulltext_fallback_result,
        runtime.build_wiki_fulltext_fallback_context,
        runtime.build_three_statement_core_context,
        runtime.build_statement_metric_context,
        runtime.build_note_detail_context,
        runtime.build_human_capital_context,
        runtime.build_human_efficiency_evidence_context,
        runtime.build_pdf2md_parse_only_context,
        runtime.build_direct_statement_metric_reply,
        runtime.build_direct_note_detail_reply,
        runtime.build_direct_human_capital_reply,
    )

    for builder in builders:
        assert builder("2025 年营业收入是多少？", context) is None, builder.__name__


def test_complete_non_cn_identity_fails_closed_when_wiki_parse_run_does_not_match(tmp_path, monkeypatch):
    import json

    from services import agent_chat_runtime as runtime

    company_dir = tmp_path / "HK-00700-Tencent"
    report_dir = company_dir / "reports" / "2025-annual"
    report_dir.mkdir(parents=True)
    (company_dir / "company.json").write_text(
        json.dumps(
            {
                "company_id": "HK:00700",
                "reports": [{"report_id": "2025-annual", "filing_id": "HK:00700:2025-annual"}],
            }
        ),
        encoding="utf-8",
    )
    (report_dir / "manifest.json").write_text(
        json.dumps({"filing_id": "HK:00700:2025-annual", "parse_run_id": "parse-wiki"}),
        encoding="utf-8",
    )
    context = {
        "research_identity": {
            "market": "HK",
            "company_id": "HK:00700",
            "filing_id": "HK:00700:2025-annual",
            "parse_run_id": "parse-request",
        }
    }
    monkeypatch.setattr(runtime, "_resolve_company_dir", lambda _message, _context=None: company_dir)

    assert runtime.build_company_wiki_scope_context("腾讯 2025 年营业收入", context) is None
    assert context["_audit_fallback_events"][-1] == {
        "reason": "research_identity_report_mismatch",
        "stage": "wiki_report_selector_failed",
        "source": "wiki_identity_selector",
        "detail": "parse_run_id_not_found",
    }


def test_chat_request_schema_preserves_research_identity_fields():
    req = ChatRequest(
        message="腾讯 2025 收入是多少？",
        context={
            "research_identity": {
                "market": "HK",
                "company_id": "HK:00700",
                "filing_id": "HK:00700:2025-annual",
                "parse_run_id": "parse-hk-00700",
            },
            "company": {"name": "Tencent", "code": "00700", "market": "HK", "company_id": "HK:00700"},
            "report": {"title": "Annual Report", "filing_id": "HK:00700:2025-annual"},
            "market": "HK",
        },
    )

    payload = req.model_dump(exclude_none=True)

    assert payload["context"]["research_identity"] == {
        "market": "HK",
        "company_id": "HK:00700",
        "filing_id": "HK:00700:2025-annual",
        "parse_run_id": "parse-hk-00700",
    }
    assert payload["context"]["company"]["company_id"] == "HK:00700"
    assert payload["context"]["report"]["filing_id"] == "HK:00700:2025-annual"


def test_context_helpers_ignore_non_dict_payloads_and_nested_fields(tmp_path):
    wiki_root = tmp_path / "wiki"

    assert agent_runtime_context.context_dict(_BadModelLike()) == {}
    assert agent_runtime_context.context_company({"company": "SAIC"}) == {}
    assert agent_runtime_context.context_company_hint({"company": "SAIC", "report": ["bad"]}) == ""
    assert (
        agent_runtime_context.forced_context_company_dir(
            {"force_company": True, "company": "SAIC"},
            wiki_root=wiki_root,
        )
        is None
    )
    assert (
        agent_runtime_context.build_format_chat_context(
            wiki_root=wiki_root,
            context={"company": "SAIC", "report": ["bad"], "page": "overview"},
            context_header="HEADER",
        )
        == f"HEADER\n- Wiki 根目录: {wiki_root}\n- 路径规则: 所有 wiki/company/report 路径必须使用绝对路径，不得从 .hermes 或 profile home 推断。"
    )


def test_forced_context_company_dir_respects_root_boundary(tmp_path):
    wiki_root = tmp_path / "wiki"
    company_dir = wiki_root / "companies" / "600104-SAIC"
    company_dir.mkdir(parents=True)

    allowed = {"force_company": True, "company": {"dir": str(company_dir)}}
    blocked = {"force_company": True, "company": {"dir": str(tmp_path / "outside")}}

    assert agent_runtime_context.forced_context_company_dir(allowed, wiki_root=wiki_root) == company_dir.resolve()
    assert agent_runtime_context.forced_context_company_dir(blocked, wiki_root=wiki_root) is None


def test_secondary_company_context_rejects_deal_wiki_path(tmp_path):
    wiki_root = tmp_path / "wiki"
    deal_dir = wiki_root / "deals" / "DEAL-SECRET-001"
    deal_dir.mkdir(parents=True)
    context = {"force_company": True, "company": {"dir": str(deal_dir)}}

    assert agent_runtime_context.forced_context_company_dir(context, wiki_root=wiki_root) is None

    analysis_dir, _work_dir = _write_analysis_package(deal_dir)
    assert analysis_dir.is_dir()
    assert (
        agent_runtime_context.analysis_completed_artifacts(
            {"company": {"dir": str(deal_dir), "code": "DEAL"}},
            read_json_file=lambda _path: {"ok": True},
            wiki_root=wiki_root,
        )
        is None
    )


def test_secondary_company_context_requires_companies_as_top_level_namespace(tmp_path):
    wiki_root = tmp_path / "wiki"
    nested_company_dir = wiki_root / "deals" / "DEAL-SECRET-001" / "companies" / "600000-Fake"
    nested_company_dir.mkdir(parents=True)
    context = {
        "force_company": True,
        "company": {"dir": str(nested_company_dir), "code": "600000", "name": "Fake"},
    }

    assert agent_runtime_context.forced_context_company_dir(context, wiki_root=wiki_root) is None
    assert (
        agent_runtime_context.analysis_completed_artifacts(
            context,
            read_json_file=lambda _path: {"ok": True},
            wiki_root=wiki_root,
        )
        is None
    )


def test_analysis_completed_artifacts_and_format_context(tmp_path):
    wiki_root = tmp_path / "wiki"
    analysis_dir, _work_dir = _write_analysis_package(wiki_root / "companies" / "600104-SAIC")

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


def test_session_default_context_is_cached_lazily_by_active_key():
    cache = {}
    format_calls = []

    def active_key(profile, session_id: str):
        normalized_profile = "siq_assistant" if profile == "assistant" else profile
        return (normalized_profile, session_id)

    def format_chat_context(context):
        format_calls.append(context)
        if not context:
            return None
        return f"ctx:{context['company']}"

    assert (
        agent_runtime_context.get_session_default_context(
            "assistant",
            "session-a",
            {"company": "A"},
            allow_initialize=False,
            session_default_contexts=cache,
            active_key=active_key,
            format_chat_context=format_chat_context,
        )
        is None
    )
    assert cache == {}
    assert format_calls == []

    assert (
        agent_runtime_context.get_session_default_context(
            "assistant",
            "session-a",
            {"company": "A"},
            allow_initialize=True,
            session_default_contexts=cache,
            active_key=active_key,
            format_chat_context=format_chat_context,
        )
        == "ctx:A"
    )
    assert cache == {("siq_assistant", "session-a"): "ctx:A"}
    assert format_calls == [{"company": "A"}]

    assert (
        agent_runtime_context.get_session_default_context(
            "siq_assistant",
            "session-a",
            {"company": "ignored"},
            allow_initialize=True,
            session_default_contexts=cache,
            active_key=active_key,
            format_chat_context=format_chat_context,
        )
        == "ctx:A"
    )
    assert format_calls == [{"company": "A"}]

    assert (
        agent_runtime_context.get_session_default_context(
            "siq_analysis",
            "session-empty",
            None,
            allow_initialize=True,
            session_default_contexts=cache,
            active_key=active_key,
            format_chat_context=format_chat_context,
        )
        is None
    )
    assert ("siq_analysis", "session-empty") not in cache


def test_session_default_context_refreshes_for_authoritative_identity_switch():
    cache = {}

    def active_key(profile, session_id: str):
        return (profile, session_id)

    def format_chat_context(context):
        identity = agent_runtime_context.research_identity_line(context)
        return f"- ResearchIdentity: {identity}" if identity else None

    old_context = {
        "research_identity": {
            "market": "HK",
            "company_id": "HK:00700",
            "filing_id": "HK:00700:2024-annual",
            "parse_run_id": "parse-hk-00700-2024",
        }
    }
    current_context = {
        "research_identity": {
            "market": "US",
            "company_id": "US_SEC:AAPL",
            "filing_id": "US_SEC:AAPL:10-K:2025",
            "parse_run_id": "parse-us-aapl-2025",
        }
    }
    next_report_context = {
        "research_identity": {
            "market": "US",
            "company_id": "US_SEC:AAPL",
            "filing_id": "US_SEC:AAPL:10-K:2026",
            "parse_run_id": "parse-us-aapl-2026",
        }
    }

    old_default = agent_runtime_context.get_session_default_context(
        "siq_analysis",
        "session-switch",
        old_context,
        allow_initialize=True,
        session_default_contexts=cache,
        active_key=active_key,
        format_chat_context=format_chat_context,
    )
    current_default = agent_runtime_context.get_session_default_context(
        "siq_analysis",
        "session-switch",
        current_context,
        allow_initialize=False,
        session_default_contexts=cache,
        active_key=active_key,
        format_chat_context=format_chat_context,
    )

    assert "company_id=HK:00700" in old_default
    assert "company_id=US_SEC:AAPL" in current_default
    assert "filing_id=US_SEC:AAPL:10-K:2025" in current_default
    assert "parse_run_id=parse-us-aapl-2025" in current_default
    assert "HK:00700" not in current_default
    assert cache[("siq_analysis", "session-switch")] == current_default

    next_report_default = agent_runtime_context.get_session_default_context(
        "siq_analysis",
        "session-switch",
        next_report_context,
        allow_initialize=False,
        session_default_contexts=cache,
        active_key=active_key,
        format_chat_context=format_chat_context,
    )

    assert "filing_id=US_SEC:AAPL:10-K:2026" in next_report_default
    assert "parse_run_id=parse-us-aapl-2026" in next_report_default
    assert "2025" not in next_report_default
    assert cache[("siq_analysis", "session-switch")] == next_report_default

    assert agent_runtime_context.get_session_default_context(
        "siq_analysis",
        "session-switch",
        {"research_identity": {"market": "HK", "company_id": "HK:00005"}},
        allow_initialize=False,
        session_default_contexts=cache,
        active_key=active_key,
        format_chat_context=format_chat_context,
    ) == next_report_default


def test_analysis_completed_artifacts_uses_company_code_fallback(tmp_path):
    wiki_root = tmp_path / "wiki"
    analysis_dir, work_dir = _write_analysis_package(wiki_root / "companies" / "600104-SAIC")
    read_paths = []

    artifacts = agent_runtime_context.analysis_completed_artifacts(
        {"company": {"dir": str(tmp_path / "missing-company-dir"), "code": "600104"}},
        read_json_file=lambda path: read_paths.append(Path(path)) or {"ok": True},
        wiki_root=wiki_root,
    )

    assert artifacts == {
        "md": str(analysis_dir / "600104-SAIC-2025-analysis.md"),
        "json": str(analysis_dir / "600104-SAIC-2025-analysis.json"),
        "html": str(analysis_dir / "600104-SAIC-2025-analysis.html"),
        "validation": str(work_dir / "final_validation.json"),
    }
    assert read_paths == [work_dir / "final_validation.json"]


def test_analysis_completed_artifacts_returns_none_for_incomplete_or_invalid_state(tmp_path):
    wiki_root = tmp_path / "wiki"

    missing_dir = wiki_root / "companies" / "600104-SAIC"
    _write_analysis_package(missing_dir, suffixes=(".md", ".html"))
    read_paths = []
    assert (
        agent_runtime_context.analysis_completed_artifacts(
            {"company": {"code": "600104"}},
            read_json_file=lambda path: read_paths.append(Path(path)) or {"ok": True},
            wiki_root=wiki_root,
        )
        is None
    )
    assert read_paths == []

    invalid_dir = wiki_root / "companies" / "000001-PAB"
    _write_analysis_package(invalid_dir, stock_code="000001", short_name="PAB")
    invalid_read_paths = []
    assert (
        agent_runtime_context.analysis_completed_artifacts(
            {"company": {"code": "000001"}},
            read_json_file=lambda path: invalid_read_paths.append(Path(path)) or {"ok": False},
            wiki_root=wiki_root,
        )
        is None
    )
    assert invalid_read_paths == [
        invalid_dir / "analysis" / ".work" / "000001-PAB-2025-analysis" / "final_validation.json"
    ]

    none_validation_dir = wiki_root / "companies" / "000002-CMB"
    _write_analysis_package(none_validation_dir, stock_code="000002", short_name="CMB")
    none_validation_read_paths = []
    assert (
        agent_runtime_context.analysis_completed_artifacts(
            {"company": {"code": "000002"}},
            read_json_file=lambda path: none_validation_read_paths.append(Path(path)),
            wiki_root=wiki_root,
        )
        is None
    )
    assert none_validation_read_paths == [
        none_validation_dir / "analysis" / ".work" / "000002-CMB-2025-analysis" / "final_validation.json"
    ]

    no_match_read_paths = []
    assert (
        agent_runtime_context.analysis_completed_artifacts(
            {"company": {"code": "300750"}},
            read_json_file=lambda path: no_match_read_paths.append(Path(path)) or {"ok": True},
            wiki_root=wiki_root,
        )
        is None
    )
    assert no_match_read_paths == []


def test_analysis_completion_guard_intent_helpers():
    force_terms = ("强制重建", "覆盖重建", "--force")
    status_terms = ("完成了吗", "报告路径")
    report_terms = ("分析报告", "html", ".md")
    generation_terms = ("生成", "重建")

    assert agent_runtime_context.normalized_intent_text("  报告\n路径 ") == "报告路径"
    assert agent_runtime_context.normalized_intent_text(" HTML\nReport ") == "htmlreport"
    assert agent_runtime_context.force_rebuild_requested("请 --force 重新来", force_terms)
    assert not agent_runtime_context.force_rebuild_requested("请重新来", force_terms)

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
    assert agent_runtime_context.analysis_completed_guard_applies(
        "请生成 HTML",
        status_terms=status_terms,
        report_terms=report_terms,
        generation_terms=generation_terms,
    )
    assert not agent_runtime_context.analysis_completed_guard_applies(
        "  ",
        status_terms=status_terms,
        report_terms=report_terms,
        generation_terms=generation_terms,
    )
    assert not agent_runtime_context.analysis_completed_guard_applies(
        "请生成",
        status_terms=status_terms,
        report_terms=report_terms,
        generation_terms=generation_terms,
    )
    assert not agent_runtime_context.analysis_completed_guard_applies(
        "年度分析报告",
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
    assert agent_runtime_context.should_use_analysis_completion_guard(
        "报告路径在哪里",
        force_rebuild_terms=force_terms,
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
    assert not agent_runtime_context.should_use_analysis_completion_guard(
        "请覆盖重建年度分析报告",
        force_rebuild_terms=force_terms,
        status_terms=status_terms,
        report_terms=report_terms,
        generation_terms=generation_terms,
    )

    assert agent_runtime_context.analysis_completion_reply(
        {"company": {"code": "600104"}},
        analysis_completed_artifacts=lambda _context: None,
        analysis_completed_message="done",
    ) is None

    artifacts = {"md": "/tmp/report.md", "html": "/tmp/report.html", "validation": "/tmp/final_validation.json"}
    reply = agent_runtime_context.analysis_completion_reply(
        {"company": {"code": "600104"}},
        analysis_completed_artifacts=lambda context: artifacts if context["company"]["code"] == "600104" else None,
        analysis_completed_message="done",
    )
    assert reply is not None
    assert "Markdown：/tmp/report.md" in reply
    assert "HTML：/tmp/report.html" in reply
    assert "验收结果：/tmp/final_validation.json" in reply

    guard_input = agent_runtime_context.analysis_completion_guard_input("报告在哪？", artifacts)
    assert "Markdown 路径：/tmp/report.md" in guard_input
    assert "HTML 路径：/tmp/report.html" in guard_input
    assert "用户原始问题：报告在哪？" in guard_input


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
    assert not agent_runtime_context.is_general_assistant_request(
        "",
        request_terms=("你能做什么",),
        subject_terms=("你", "助手"),
    )
    assert not agent_runtime_context.is_general_assistant_request(
        "你能做什么？",
        request_terms=("你能做什么",),
        subject_terms=("助手",),
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
    assert text.endswith("用户问题：你是谁？")


def test_financial_intent_helpers_are_parameterized_and_exclude_general_requests():
    def is_general(message):
        return agent_runtime_context.is_general_assistant_request(
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
    assert agent_runtime_context.statement_query_applies(
        "Samsung Electronics TOTAL ASSETS",
        statement_terms=(*statement_terms, "total assets"),
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

    assert agent_runtime_context.goodwill_main_statement_query_applies(
        "商誉账面价值是多少？",
        goodwill_main_statement_terms=("账面价值", "资产负债表"),
        is_general_assistant_request=is_general,
    )
    assert not agent_runtime_context.goodwill_main_statement_query_applies(
        "你能做什么？可以回答商誉账面价值吗？",
        goodwill_main_statement_terms=("账面价值", "资产负债表"),
        is_general_assistant_request=is_general,
    )
    assert agent_runtime_context.statement_query_with_goodwill_applies(
        "商誉在资产负债表中的余额",
        statement_terms=statement_terms,
        goodwill_main_statement_terms=("账面价值", "资产负债表", "余额"),
        is_general_assistant_request=is_general,
    )
    assert agent_runtime_context.statement_query_with_goodwill_applies(
        "分析商誉明细和减值准备",
        statement_terms=statement_terms,
        goodwill_main_statement_terms=("账面价值", "资产负债表", "余额"),
        is_general_assistant_request=is_general,
    )
    assert agent_runtime_context.direct_statement_answer_with_goodwill_applies(
        "商誉账面价值是多少？",
        statement_terms=statement_terms,
        statement_direct_terms=("多少", "核心数据"),
        note_detail_analysis_terms=("分析",),
        goodwill_main_statement_terms=("账面价值", "资产负债表"),
        is_general_assistant_request=is_general,
    )
    assert not agent_runtime_context.direct_statement_answer_with_goodwill_applies(
        "分析商誉账面价值趋势",
        statement_terms=statement_terms,
        statement_direct_terms=("多少", "趋势"),
        note_detail_analysis_terms=("分析", "趋势"),
        goodwill_main_statement_terms=("账面价值", "资产负债表"),
        is_general_assistant_request=is_general,
    )


def test_note_detail_direct_and_context_intent_combinations():
    def is_general(message):
        return agent_runtime_context.is_general_assistant_request(
            message,
            request_terms=("你能做什么",),
            subject_terms=("你", "助手"),
        )

    kwargs = {
        "note_detail_query_terms": ("明细", "构成"),
        "note_detail_exclude_terms": ("生成报告",),
        "financial_note_metric_terms": ("商誉", "递延所得税", "营业收入"),
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
    assert not agent_runtime_context.direct_note_detail_answer_applies("商誉构成明细", **direct_kwargs)
    assert not agent_runtime_context.direct_note_detail_answer_applies("商誉多少", **direct_kwargs)
    assert agent_runtime_context.direct_note_detail_answer_applies("营业收入构成多少", **direct_kwargs)
    assert not agent_runtime_context.direct_note_detail_answer_applies("营业收入构成明细", **direct_kwargs)
    assert not agent_runtime_context.direct_note_detail_answer_applies("商誉构成明细多少趋势分析", **direct_kwargs)
    assert not agent_runtime_context.direct_note_detail_answer_applies(None, **direct_kwargs)

    assert agent_runtime_context.note_detail_query_applies("营业收入构成明细", **kwargs)
    assert not agent_runtime_context.note_detail_query_applies("现金流构成明细", **kwargs)
    assert not agent_runtime_context.note_detail_query_applies("你能做什么？商誉构成明细", **kwargs)
    assert not agent_runtime_context.note_detail_query_applies("生成报告里的商誉构成明细", **kwargs)
    assert not agent_runtime_context.note_detail_query_applies("  ", **kwargs)

    assert agent_runtime_context.financial_note_metric_query_applies("营业收入构成多少", **context_kwargs)
    assert not agent_runtime_context.financial_note_metric_query_applies("现金流构成多少", **context_kwargs)
    assert not agent_runtime_context.financial_note_metric_query_applies("营业收入证据来源", **context_kwargs)
    assert not agent_runtime_context.financial_note_metric_query_applies("你能做什么？商誉证据来源", **context_kwargs)
    assert not agent_runtime_context.financial_note_metric_query_applies("生成报告里的商誉证据来源", **context_kwargs)
    assert not agent_runtime_context.financial_note_metric_query_applies("  ", **context_kwargs)

    assert agent_runtime_context.note_detail_context_applies("商誉证据来源", **context_kwargs)
    assert agent_runtime_context.note_detail_context_applies("递延所得税构成", **context_kwargs)
    assert agent_runtime_context.note_detail_context_applies("营业收入构成明细", **context_kwargs)
    assert agent_runtime_context.note_detail_context_applies("商誉构成明细", **context_kwargs)
    assert not agent_runtime_context.note_detail_context_applies("现金流构成明细", **context_kwargs)
    assert not agent_runtime_context.note_detail_context_applies("营业收入是多少", **context_kwargs)
    assert not agent_runtime_context.note_detail_context_applies("营业收入证据来源", **context_kwargs)
    assert not agent_runtime_context.note_detail_context_applies("你能做什么？商誉证据来源", **context_kwargs)
    assert not agent_runtime_context.note_detail_context_applies("生成报告里的商誉证据来源", **context_kwargs)
    assert not agent_runtime_context.note_detail_context_applies("  ", **context_kwargs)


def test_attachment_helpers_normalize_and_classify_payloads():
    attachments = [
        _ModelLike({"kind": "image", "path": " /tmp/chart.png ", "filename": "chart.png"}),
        _BadModelLike(),
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


def test_build_session_contextual_input_owner_bypasses_session_state_for_general_requests():
    def forbidden(*_args, **_kwargs):
        raise AssertionError("general assistant requests should not initialize session context")

    prompt = agent_runtime_context.build_session_contextual_input(
        "你是谁",
        profile="siq_assistant",
        profile_label="SIQ Assistant",
        session_id="session-1",
        is_general_assistant_request=lambda _message: True,
        session_default_context=forbidden,
        resolve_company_dirs=forbidden,
        context_for_company_dir=forbidden,
        message_for_company=forbidden,
        build_company_wiki_scope_context=forbidden,
        build_human_efficiency_evidence_context=forbidden,
        build_human_capital_context=forbidden,
        build_three_statement_core_context=forbidden,
        build_statement_metric_context=forbidden,
        build_note_detail_context=forbidden,
        build_wiki_fulltext_fallback_context=forbidden,
        build_postgres_fallback_context=forbidden,
        build_pdf2md_parse_only_context=forbidden,
        general_assistant_context="GENERAL",
        chat_output_contract="CHAT",
        financial_calculation_runtime_contract="CALC",
    )

    assert prompt == (
        "GENERAL\n\n"
        "当前智能体 profile: siq_assistant\n\n"
        "当前智能体名称: SIQ Assistant\n\n"
        "请由当前 Hermes profile 的模型按自身角色设定回答，不要使用后端固定简介模板。\n\n"
        "用户问题：你是谁"
    )


def test_build_session_contextual_input_owner_orders_scope_and_fallback_blocks():
    calls: list[str] = []
    dirs = [Path("/wiki/companies/600104-SAIC"), Path("/wiki/companies/000001-PAB")]

    def session_default_context(profile, session_id, context, *, allow_initialize=False):
        calls.append(f"default:{profile}:{session_id}:{allow_initialize}:{context['scope']}")
        return "DEFAULT"

    def build_company_wiki_scope_context(message, context):
        calls.append(f"scope:{message}:{Path(context['company']['dir']).name}")
        return f"SCOPE:{Path(context['company']['dir']).name}"

    def none_builder(label):
        def _builder(message, _context):
            calls.append(f"{label}:{message}")
            return None

        return _builder

    def postgres_builder(message, _context):
        calls.append(f"postgres:{message}")
        return "POSTGRES"

    def forbidden_parse_only(*_args, **_kwargs):
        raise AssertionError("parse-only fallback should not run after PostgreSQL context is available")

    prompt = agent_runtime_context.build_session_contextual_input(
        "对比收入",
        profile="siq_analysis",
        profile_label="SIQ Analysis",
        session_id="session-2",
        context={"scope": "original"},
        allow_initialize=True,
        local_memory_context="MEMORY",
        is_general_assistant_request=lambda _message: False,
        session_default_context=session_default_context,
        resolve_company_dirs=lambda _message, _context: dirs,
        context_for_company_dir=lambda path: {"company": {"dir": str(path)}},
        message_for_company=lambda message, path: f"{path.name}:{message}",
        build_company_wiki_scope_context=build_company_wiki_scope_context,
        build_human_efficiency_evidence_context=none_builder("human-efficiency"),
        build_human_capital_context=none_builder("human-capital"),
        build_three_statement_core_context=none_builder("three-statement"),
        build_statement_metric_context=none_builder("statement"),
        build_note_detail_context=none_builder("note"),
        build_wiki_fulltext_fallback_context=none_builder("wiki-fulltext"),
        build_postgres_fallback_context=postgres_builder,
        build_pdf2md_parse_only_context=forbidden_parse_only,
        general_assistant_context="GENERAL",
        chat_output_contract="CHAT",
        financial_calculation_runtime_contract="CALC",
    )

    assert prompt == (
        "DEFAULT\n\n"
        "MEMORY\n\n"
        f"{agent_runtime_context.MULTI_COMPANY_SCOPE_NOTICE}\n\n"
        "SCOPE:600104-SAIC\n\n"
        "SCOPE:000001-PAB\n\n"
        "POSTGRES\n\n"
        "CHAT\n\n"
        "CALC\n\n"
        "用户问题：对比收入"
    )
    assert calls == [
        "default:siq_analysis:session-2:True:original",
        "scope:600104-SAIC:对比收入:600104-SAIC",
        "human-efficiency:600104-SAIC:对比收入",
        "human-capital:600104-SAIC:对比收入",
        "scope:000001-PAB:对比收入:000001-PAB",
        "human-efficiency:000001-PAB:对比收入",
        "human-capital:000001-PAB:对比收入",
        "three-statement:对比收入",
        "statement:对比收入",
        "note:对比收入",
        "wiki-fulltext:对比收入",
        "postgres:对比收入",
    ]


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


def test_hermes_run_input_payload_returns_contextual_text_without_attachments():
    assert agent_runtime_context.build_hermes_run_input_payload(
        "CTX",
        has_attachments=False,
        document_context="ignored",
        image_data_urls=["data:image/png;base64,aaa"],
    ) == "CTX"


def test_hermes_run_input_payload_uses_text_when_image_fallback_disabled():
    payload = agent_runtime_context.build_hermes_run_input_payload(
        "CTX",
        has_attachments=True,
        document_context="DOC",
        image_analysis_context="IMG ANALYSIS",
        image_path_hints="[Image attached at: /tmp/a.png]",
        image_data_urls=["data:image/png;base64,aaa"],
        use_hermes_image_fallback=False,
    )

    assert payload == "CTX\n\nDOC\n\nIMG ANALYSIS\n\n[Image attached at: /tmp/a.png]"


def test_hermes_run_input_payload_builds_multimodal_parts_and_skips_empty_urls():
    payload = agent_runtime_context.build_hermes_run_input_payload(
        "CTX",
        has_attachments=True,
        image_data_urls=["", "data:image/png;base64,aaa"],
    )

    assert payload == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "CTX"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,aaa"}},
            ],
        }
    ]
