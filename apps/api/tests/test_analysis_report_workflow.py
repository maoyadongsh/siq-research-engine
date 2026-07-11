import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import anyio

from routers import agent_user_router
from routers.agent_user_router import SpecialistAgentConfig, create_specialist_agent_router
from schemas import ChatRequest
from services import analysis_report_workflow as workflow
from services.auth_service import User, UserRole


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


def _resolved_company_payload(tmp_path: Path) -> dict:
    company_dir = tmp_path / "data" / "wiki" / "companies" / "000333-美的集团"
    company_dir.mkdir(parents=True)
    return {
        "ok": True,
        "company": {
            "stock_code": "000333",
            "company_short_name": "美的集团",
            "company_path": "companies/000333-美的集团",
        },
        "paths": {"company_dir": {"path": str(company_dir), "exists": True}},
    }


def test_latest_report_script_prefers_research_pack_capable_profile():
    script = workflow.latest_research_pack_report_script()

    assert script is not None
    assert script.as_posix().endswith("agents/hermes/profiles/siq_analysis/scripts/run_analysis_report.py")


def test_report_generation_intent_excludes_meta_questions():
    assert workflow.is_analysis_report_generation_request("请为美的集团生成完整分析报告") is True
    assert workflow.is_analysis_report_generation_request("为什么分析助手没有调用最新生成器生成报告？") is False


def test_run_analysis_report_workflow_uses_research_pack_command(monkeypatch, tmp_path):
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    script = script_dir / "run_analysis_report.py"
    script.write_text("# runner\n", encoding="utf-8")
    (script_dir / "resolve_company.py").write_text("# resolver\n", encoding="utf-8")
    calls: list[list[str]] = []
    resolved = _resolved_company_payload(tmp_path)

    def fake_latest_script():
        return script

    def fake_run_command(args, *, cwd=None, timeout=None, env=None):
        calls.append(list(args))
        if args[1].endswith("resolve_company.py"):
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(resolved, ensure_ascii=False), stderr="")
        output_prefix = Path(args[args.index("--output-prefix") + 1])
        payload = {
            "ok": True,
            "stage": "completed",
            "company_query": "000333-美的集团",
            "year": 2025,
            "files": {
                "html": str(output_prefix.with_suffix(".html")),
                "md": str(output_prefix.with_suffix(".md")),
                "json": str(output_prefix.with_suffix(".json")),
            },
            "checkpoints": {
                "research_pack_validation": str(output_prefix.parent / ".work" / "research_pack_validation.json"),
                "research_subagent_run_manifest": str(output_prefix.parent / ".work" / "research_subagent_run_manifest.json"),
            },
            "validation": {"ok": True, "status": "pass"},
        }
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(payload, ensure_ascii=False), stderr="")

    monkeypatch.setattr(workflow, "latest_research_pack_report_script", fake_latest_script)
    monkeypatch.setattr(workflow, "run_command", fake_run_command)

    response = workflow.run_analysis_report_workflow(
        workflow.AnalysisReportWorkflowRequest(
            company_query="000333-美的集团",
            prompt="请为美的集团生成完整分析报告",
        )
    )

    assert response.result["ok"] is True
    assert len(calls) == 2
    report_cmd = calls[1]
    assert "--use-research-packs" in report_cmd
    assert report_cmd[report_cmd.index("--research-subagent-mode") + 1] == "deterministic"
    assert "--output-prefix" in report_cmd
    assert "--allow-overwrite" not in report_cmd
    assert "analysis-research-pack-" in report_cmd[report_cmd.index("--output-prefix") + 1]
    assert "--research-subagent-prompt" in report_cmd
    assert "/api/wiki/companies/000333-" in response.reply
    assert "Research pack 校验" in response.reply


def test_run_analysis_report_workflow_allows_explicit_overwrite(monkeypatch, tmp_path):
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    script = script_dir / "run_analysis_report.py"
    script.write_text("# runner\n", encoding="utf-8")
    (script_dir / "resolve_company.py").write_text("# resolver\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run_command(args, *, cwd=None, timeout=None, env=None):
        calls.append(list(args))
        if args[1].endswith("resolve_company.py"):
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=json.dumps(_resolved_company_payload(tmp_path), ensure_ascii=False),
                stderr="",
            )
        payload = {"ok": True, "stage": "completed", "files": {}, "validation": {"ok": True}}
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(workflow, "latest_research_pack_report_script", lambda: script)
    monkeypatch.setattr(workflow, "run_command", fake_run_command)

    workflow.run_analysis_report_workflow(
        workflow.AnalysisReportWorkflowRequest(
            company_query="000333",
            allow_overwrite=True,
            prompt="请覆盖现有分析报告",
        )
    )

    report_cmd = calls[1]
    assert "--allow-overwrite" in report_cmd
    assert "--output-prefix" not in report_cmd


def test_analysis_chat_routes_report_generation_to_workflow(monkeypatch):
    calls = {"collect": 0, "workflow": 0}
    saved: list[tuple[str, str]] = []

    async def noop_quota(*args, **kwargs):
        return (1, None)

    async def noop_usage(*args, **kwargs):
        return None

    async def fake_resolve_session(*args, **kwargs):
        return "user-7-analysis-session"

    async def fake_save_message(async_session, role, content, session_id, attachments=None, audit_trace_id=None):
        saved.append((role, content))
        return SimpleNamespace(id=len(saved), role=role, content=content, session_id=session_id)

    async def fake_collect_chat_reply(*args, **kwargs):
        calls["collect"] += 1
        raise AssertionError("Hermes chat should not run for formal report generation")

    async def fake_record_workspace(*args, **kwargs):
        return {"workspace_synced": True}

    async def fake_workflow_reply(workflow_request):
        calls["workflow"] += 1
        assert workflow_request.company_query == "000333-美的集团"
        return "已使用 research-pack 报告生成器完成正式分析报告\n\n- 打开报告: [HTML 报告](/api/wiki/companies/000333-%E7%BE%8E%E7%9A%84%E9%9B%86%E5%9B%A2/analysis/report.html)"

    monkeypatch.setattr(agent_user_router, "enforce_quota_or_429_async", noop_quota)
    monkeypatch.setattr(agent_user_router, "record_usage_async", noop_usage)
    monkeypatch.setattr(agent_user_router, "resolve_or_create_session", fake_resolve_session)
    monkeypatch.setattr(agent_user_router, "save_message", fake_save_message)
    monkeypatch.setattr(agent_user_router, "collect_chat_reply", fake_collect_chat_reply)
    monkeypatch.setattr(agent_user_router, "_record_agent_workspace_artifact_background", fake_record_workspace)
    monkeypatch.setattr(agent_user_router, "_run_analysis_report_workflow_reply", fake_workflow_reply)
    monkeypatch.setattr(
        agent_user_router,
        "get_session_manager",
        lambda: SimpleNamespace(increment_message_count=lambda session_id: None),
    )

    router = create_specialist_agent_router(
        SpecialistAgentConfig(prefix="/analysis", tag="analysis", profile="siq_analysis")
    )
    endpoint = next(route.endpoint for route in router.routes if route.path.endswith("/chat") and "POST" in route.methods)

    async def run_case():
        payload = await endpoint(
            ChatRequest(
                message="请为当前公司生成完整分析报告",
                context={"company": {"dir": "000333-美的集团", "code": "000333", "name": "美的集团"}},
            ),
            current_user=_user(),
            async_session=SimpleNamespace(),
        )

        assert calls == {"collect": 0, "workflow": 1}
        assert payload.reply.startswith("已使用 research-pack 报告生成器")
        assert saved[0] == ("user", "请为当前公司生成完整分析报告")
        assert saved[1][0] == "assistant"

    anyio.run(run_case)
