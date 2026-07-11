import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import anyio

from routers import agent_user_router
from routers.agent_user_router import SpecialistAgentConfig, create_specialist_agent_router
from schemas import ChatRequest
from services import factcheck_workflow as workflow
from services.auth_service import User, UserRole


REPO_ROOT = Path(__file__).resolve().parents[3]
FACTCHECK_SCRIPT_DIR = REPO_ROOT / "agents" / "hermes" / "profiles" / "siq_factchecker" / "scripts"
if str(FACTCHECK_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(FACTCHECK_SCRIPT_DIR))

import factcheck_cli  # noqa: E402


def _user(user_id: int = 7) -> User:
    return User(
        id=user_id,
        username=f"user-{user_id}",
        email=f"user-{user_id}@example.test",
        hashed_password="x",
        full_name=f"User {user_id}",
        role=UserRole.ANALYST,
        is_active=True,
        approval_status="approved",
    )


def test_latest_factcheck_script_prefers_report_path_capable_profile():
    script = workflow.latest_factcheck_script()

    assert script is not None
    assert script.as_posix().endswith("agents/hermes/profiles/siq_factchecker/scripts/factcheck_cli.py")


def test_factcheck_generation_intent_excludes_meta_questions():
    assert workflow.is_factcheck_generation_request("请为当前分析报告生成事实核查报告") is True
    assert workflow.is_factcheck_generation_request("为什么事实核查智能体没有调用最新能力？") is False


def test_build_factcheck_request_uses_current_analysis_report_context():
    request = workflow.build_factcheck_workflow_request(
        "请生成事实核查报告",
        {
            "company": {"dir": "600104-上汽集团", "code": "600104", "name": "上汽集团"},
            "report": {
                "type": "analysis",
                "url": "/api/wiki/companies/600104-%E4%B8%8A%E6%B1%BD%E9%9B%86%E5%9B%A2/analysis/600104-%E4%B8%8A%E6%B1%BD%E9%9B%86%E5%9B%A2-2025-analysis-research-pack.html",
            },
        },
    )

    assert request is not None
    assert request.company_query == "600104-上汽集团"
    assert request.report_path is not None
    assert request.report_path.as_posix().endswith(
        "data/wiki/companies/600104-上汽集团/analysis/600104-上汽集团-2025-analysis-research-pack.md"
    )


def test_run_factcheck_workflow_uses_explicit_report_and_output(monkeypatch, tmp_path):
    script = tmp_path / "factcheck_cli.py"
    script.write_text("# factcheck runner\n# --report-path --output\n", encoding="utf-8")
    calls: list[list[str]] = []
    requested_report = tmp_path / "analysis" / "600104-上汽集团-2025-analysis-research-pack.md"
    requested_report.parent.mkdir()
    requested_report.write_text("# report\n", encoding="utf-8")

    def fake_run_command(args, *, cwd=None, timeout=None, env=None):
        calls.append(list(args))
        output_path = Path(args[args.index("--output") + 1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(
                {
                    "verdict": "request_changes",
                    "company_id": "600104-上汽集团",
                    "report_file": requested_report.name,
                    "summary": {"critical": 0, "warning": 1, "suggestion": 0},
                    "checks": {},
                    "evidence_summary": [],
                    "recommendations": ["补充证据链"],
                    "verified_at": "2026-07-11T00:00:00+08:00",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        output_path.with_suffix(".html").write_text("<html>factcheck</html>", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

    monkeypatch.setattr(workflow, "latest_factcheck_script", lambda: script)
    monkeypatch.setattr(
        workflow,
        "_resolve_company",
        lambda query: {
            "company_id": "600104-上汽集团",
            "stock_code": "600104",
            "company_short_name": "上汽集团",
            "company_path": "companies/600104-上汽集团",
        },
    )
    monkeypatch.setattr(workflow, "WIKI_ROOT", tmp_path / "wiki")
    monkeypatch.setattr(workflow, "run_command", fake_run_command)

    response = workflow.run_factcheck_workflow(
        workflow.FactcheckWorkflowRequest(
            company_query="600104-上汽集团",
            year=2025,
            report_path=requested_report,
        )
    )

    assert response.result["ok"] is True
    assert calls
    cmd = calls[0]
    assert "verify" in cmd
    assert "--report-path" in cmd
    assert cmd[cmd.index("--report-path") + 1] == str(requested_report)
    assert "--output" in cmd
    assert "factcheck-" in cmd[cmd.index("--output") + 1]
    assert "已生成正式事实核查报告" in response.reply
    assert "HTML 核查报告" in response.reply


def test_factcheck_engine_prefers_research_pack_report(tmp_path):
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()
    canonical = analysis_dir / "600104-上汽集团-2025-analysis.md"
    research_pack = analysis_dir / "600104-上汽集团-2025-analysis-research-pack-20260711.md"
    canonical.write_text("# old report\n", encoding="utf-8")
    research_pack.write_text("# better report\n", encoding="utf-8")

    accessor = SimpleNamespace(get_analysis_dir=lambda company_id: analysis_dir)
    engine = factcheck_cli.FactCheckEngine(accessor)
    company = SimpleNamespace(company_id="600104-上汽集团", stock_code="600104", company_short_name="上汽集团")

    selected = engine._select_analysis_report(company, 2025)

    assert selected is not None
    assert selected.md_path == research_pack
    assert selected.selection_reason == "auto_selected:600104-上汽集团-2025-analysis-research-pack-20260711.md"


def test_factcheck_engine_accepts_explicit_html_report_path(tmp_path):
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()
    report_md = analysis_dir / "600104-上汽集团-2025-analysis-siq-depth.md"
    report_html = report_md.with_suffix(".html")
    report_md.write_text("# depth report\n", encoding="utf-8")
    report_html.write_text("<html></html>", encoding="utf-8")

    accessor = SimpleNamespace(get_analysis_dir=lambda company_id: analysis_dir)
    engine = factcheck_cli.FactCheckEngine(accessor)
    company = SimpleNamespace(company_id="600104-上汽集团", stock_code="600104", company_short_name="上汽集团")

    selected = engine._select_analysis_report(company, 2025, report_path=report_html)

    assert selected is not None
    assert selected.md_path == report_md
    assert selected.selection_reason == f"explicit:{report_md.name}"


def test_factcheck_engine_accepts_project_relative_report_path(monkeypatch, tmp_path):
    analysis_dir = tmp_path / "data" / "wiki" / "companies" / "600104-上汽集团" / "analysis"
    analysis_dir.mkdir(parents=True)
    report_md = analysis_dir / "600104-上汽集团-2025-analysis-research-pack.md"
    report_html = report_md.with_suffix(".html")
    report_md.write_text("# research pack\n", encoding="utf-8")
    report_html.write_text("<html></html>", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    accessor = SimpleNamespace(get_analysis_dir=lambda company_id: analysis_dir)
    engine = factcheck_cli.FactCheckEngine(accessor)
    company = SimpleNamespace(company_id="600104-上汽集团", stock_code="600104", company_short_name="上汽集团")

    selected = engine._select_analysis_report(
        company,
        2025,
        report_path=Path("data/wiki/companies/600104-上汽集团/analysis/600104-上汽集团-2025-analysis-research-pack.html"),
    )

    assert selected is not None
    assert selected.md_path == report_md
    assert selected.selection_reason == f"explicit:{report_md.name}"


def test_factchecker_chat_routes_generation_to_workflow(monkeypatch):
    calls = {"collect": 0, "workflow": 0}
    saved: list[tuple[str, str]] = []

    async def noop_quota(*args, **kwargs):
        return (1, None)

    async def noop_usage(*args, **kwargs):
        return None

    async def fake_resolve_session(*args, **kwargs):
        return "user-7-factchecker-session"

    async def fake_save_message(async_session, role, content, session_id, attachments=None, audit_trace_id=None):
        saved.append((role, content))
        return SimpleNamespace(id=len(saved), role=role, content=content, session_id=session_id)

    async def fake_collect_chat_reply(*args, **kwargs):
        calls["collect"] += 1
        raise AssertionError("Hermes chat should not run for formal factcheck generation")

    async def fake_record_workspace(*args, **kwargs):
        return {"workspace_synced": True}

    async def fake_workflow_reply(workflow_request):
        calls["workflow"] += 1
        assert workflow_request.company_query == "600104-上汽集团"
        assert workflow_request.report_path is not None
        return "已生成正式事实核查报告\n\n- 打开报告: [HTML 核查报告](/api/wiki/companies/600104-%E4%B8%8A%E6%B1%BD%E9%9B%86%E5%9B%A2/factcheck/report.html)"

    monkeypatch.setattr(agent_user_router, "enforce_quota_or_429_async", noop_quota)
    monkeypatch.setattr(agent_user_router, "record_usage_async", noop_usage)
    monkeypatch.setattr(agent_user_router, "resolve_or_create_session", fake_resolve_session)
    monkeypatch.setattr(agent_user_router, "save_message", fake_save_message)
    monkeypatch.setattr(agent_user_router, "collect_chat_reply", fake_collect_chat_reply)
    monkeypatch.setattr(agent_user_router, "_record_agent_workspace_artifact_background", fake_record_workspace)
    monkeypatch.setattr(agent_user_router, "_run_factcheck_workflow_reply", fake_workflow_reply)
    monkeypatch.setattr(
        agent_user_router,
        "get_session_manager",
        lambda: SimpleNamespace(increment_message_count=lambda session_id: None),
    )

    router = create_specialist_agent_router(
        SpecialistAgentConfig(prefix="/factchecker", tag="factchecker", profile="siq_factchecker")
    )
    endpoint = next(route.endpoint for route in router.routes if route.path.endswith("/chat") and "POST" in route.methods)

    async def run_case():
        payload = await endpoint(
            ChatRequest(
                message="请为当前分析报告生成事实核查报告",
                context={
                    "company": {"dir": "600104-上汽集团", "code": "600104", "name": "上汽集团"},
                    "report": {
                        "type": "analysis",
                        "url": "/api/wiki/companies/600104-%E4%B8%8A%E6%B1%BD%E9%9B%86%E5%9B%A2/analysis/600104-%E4%B8%8A%E6%B1%BD%E9%9B%86%E5%9B%A2-2025-analysis-research-pack.html",
                    },
                },
            ),
            current_user=_user(),
            async_session=SimpleNamespace(),
        )

        assert calls == {"collect": 0, "workflow": 1}
        assert payload.reply.startswith("已生成正式事实核查报告")
        assert saved[0] == ("user", "请为当前分析报告生成事实核查报告")
        assert saved[1][0] == "assistant"

    anyio.run(run_case)
