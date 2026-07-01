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
