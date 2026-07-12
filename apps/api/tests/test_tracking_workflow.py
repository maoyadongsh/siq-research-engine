import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import anyio
from routers import agent_user_router
from routers.agent_user_router import SpecialistAgentConfig, create_specialist_agent_router
from schemas import ChatRequest
from services.auth_service import User, UserRole

from services import tracking_workflow as workflow

REPO_ROOT = Path(__file__).resolve().parents[3]


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


def test_tracking_generation_intent_excludes_meta_questions():
    assert workflow.is_tracking_generation_request("请为美的集团生成持续跟踪报告") is True
    assert workflow.is_tracking_generation_request("为什么持续跟踪智能体没有固化 run_all 能力？") is False


def test_build_tracking_request_uses_current_company_context():
    request = workflow.build_tracking_workflow_request(
        "请刷新持续跟踪报告，并禁用搜索",
        {"company": {"dir": "600104-上汽集团", "code": "600104", "name": "上汽集团"}},
    )

    assert request is not None
    assert request.company_query == "600104-上汽集团"
    assert request.use_search is False


def test_run_tracking_workflow_calls_run_all_and_formats_html_link(monkeypatch, tmp_path):
    script = tmp_path / "run_all.py"
    script.write_text("# tracking runner\n", encoding="utf-8")
    wiki_root = tmp_path / "wiki"
    company_dir = wiki_root / "companies" / "600104-上汽集团"
    tracking_dir = company_dir / "tracking"
    tracking_dir.mkdir(parents=True)
    analysis_dir = company_dir / "analysis"
    analysis_dir.mkdir()
    source_report_path = analysis_dir / "600104-上汽集团-2025-analysis.md"
    source_report_path.write_text("# analysis\n", encoding="utf-8")
    html_path = tracking_dir / "600104-上汽集团-跟踪报告-2026-07-11.html"
    html_path.write_text("<html>tracking</html>", encoding="utf-8")
    items_path = tracking_dir / "tracking-items.md"
    metrics_path = tracking_dir / "metrics" / "2026-Q2.md"
    alerts_path = tracking_dir / "alerts" / "2026-07-11-warning-001.md"
    updates_path = tracking_dir / "updates" / "2026-07-11-update.md"
    for path in (items_path, metrics_path, alerts_path, updates_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            'source_refs=[{"task_id":"11111111-1111-1111-1111-111111111111","pdf_page":69}]',
            encoding="utf-8",
        )

    calls: list[dict] = []

    def fake_run_command(args, *, cwd=None, timeout=None, env=None):
        calls.append({"args": list(args), "cwd": cwd, "timeout": timeout, "env": env})
        summary = {
            "status": "success",
            "modules": {
                "module1": {"status": "success", "path": str(items_path)},
                "module2": {"status": "skipped"},
                "module3": {"status": "success", "path": str(metrics_path)},
                "module4": {"status": "success", "path": str(alerts_path)},
                "module5": {"status": "success", "path": str(updates_path)},
                "module6": {"status": "success", "path": str(html_path)},
            },
            "citation_check": {"passed": True, "issues": []},
            "postgres_query_status": "not_run",
            "postgres_queries": [],
        }
        stdout = "logs before json\n" + json.dumps(summary, ensure_ascii=False)
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(workflow, "_tracking_script", lambda: script)
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
    monkeypatch.setattr(workflow, "WIKI_ROOT", wiki_root)
    monkeypatch.setattr(workflow, "run_command", fake_run_command)

    response = workflow.run_tracking_workflow(
        workflow.TrackingWorkflowRequest(company_query="600104-上汽集团", use_search=False)
    )

    assert response.result["ok"] is True
    assert calls
    cmd = calls[0]["args"]
    assert cmd[cmd.index("--stock") + 1] == "600104"
    assert cmd[cmd.index("--company") + 1] == "上汽集团"
    assert cmd[cmd.index("--wiki-base") + 1] == str(wiki_root)
    assert "--json-summary" in cmd
    assert "--no-search" in cmd
    assert calls[0]["env"]["SIQ_WIKI_ROOT"] == str(wiki_root)
    assert "已生成正式持续跟踪报告" in response.reply
    assert "/api/wiki/companies/600104-" in response.reply
    assert "HTML 跟踪报告" in response.reply
    assert response.result["artifact"]["artifact_type"] == "tracking"
    assert response.result["artifact"]["validation_result"]["ok"] is True
    assert response.result["artifact"]["metadata"]["postgres_query_status"] == "not_run"
    assert response.result["artifact"]["source_report_path"] == str(source_report_path)
    assert response.result["audit_trace_id"].startswith("aat_")


def test_project_local_citations_resolves_analysis_bullet_refs(tmp_path):
    module_path = REPO_ROOT / "agents" / "hermes" / "profiles" / "shared" / "scripts" / "local_citations.py"
    spec = importlib.util.spec_from_file_location("project_local_citations_for_tracking_test", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()
    task_id = "11111111-1111-1111-1111-111111111111"
    (analysis_dir / "report.md").write_text(
        f"- operating_revenue: task_id={task_id}，pdf_page=69，table_index=86，md_line=1874\n",
        encoding="utf-8",
    )

    refs = module.resolve_analysis_refs(tmp_path, "report.md")

    assert refs
    assert refs[0]["task_id"] == task_id
    assert refs[0]["pdf_page"] == 69
    assert refs[0]["table_index"] == 86
    assert refs[0]["md_line"] == 1874


def test_tracking_alert_metric_rules_preserve_source_refs():
    module_path = REPO_ROOT / "data" / "wiki" / "tracking" / "scripts" / "module4_alert_trigger.py"
    spec = importlib.util.spec_from_file_location("tracking_module4_alert_trigger_test", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source_ref = {
        "task_id": "11111111-1111-1111-1111-111111111111",
        "pdf_page": 69,
        "table_index": 86,
        "md_line": 1874,
    }

    alerts = module.evaluate_rules(
        {
            "items": [],
            "sentiment": [],
            "metrics": {
                "net_profit": {
                    "latest_yoy": -35.0,
                    "changes": {"yoy": {"2024": -5.0, "2025": -35.0}},
                    "source_refs": [source_ref],
                }
            },
        }
    )

    alert = next(item for item in alerts if item["rule_id"] == "RULE-001")
    assert alert["source_refs"] == [source_ref]
    assert alert["evidence_refs"] == [source_ref]


def test_tracking_citation_validator_checks_latest_alert_only(tmp_path):
    module_path = REPO_ROOT / "data" / "wiki" / "tracking" / "scripts" / "validate_citations.py"
    spec = importlib.util.spec_from_file_location("tracking_validate_citations_test", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    alerts_dir = tmp_path / "alerts"
    alerts_dir.mkdir()
    (alerts_dir / "2026-05-22-warning-001.md").write_text(
        '```json\n[{"id":"old","category":"异常指标"}]\n```\n',
        encoding="utf-8",
    )
    (alerts_dir / "2026-07-11-warning-001.md").write_text(
        '```json\n[{"id":"new","category":"异常指标","source_refs":[{"task_id":"11111111-1111-1111-1111-111111111111","pdf_page":69}]}]\n```\n',
        encoding="utf-8",
    )

    issues = module._validate_alerts(tmp_path)

    assert issues == []


def test_tracking_html_report_uses_review_template_and_clickable_evidence(tmp_path):
    module_path = REPO_ROOT / "data" / "wiki" / "tracking" / "scripts" / "module6_html_reporter.py"
    script_dir = str(module_path.parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    spec = importlib.util.spec_from_file_location("tracking_module6_html_reporter_test", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    converted = module.markdown_to_html(
        "[打开PDF页](https://example.test/pdf?x=1&y=2)\n\n"
        "| 指标 | 最新值 |\n"
        "| --- | --- |\n"
        "| 营业收入 | 100 |\n"
    )
    assert 'target="_blank"' in converted
    assert "https://example.test/pdf?x=1&amp;y=2" in converted
    assert "<thead>" in converted
    assert "<th>指标</th>" in converted

    wiki_root = tmp_path / "wiki"
    tracking_dir = wiki_root / "companies" / "600104-上汽集团" / "tracking"
    (tracking_dir / "metrics").mkdir(parents=True)
    (tracking_dir / "alerts").mkdir()
    (tracking_dir / "sentiment").mkdir()
    (tracking_dir / "updates").mkdir()
    (tracking_dir / "tracking-items.md").write_text(
        "# 上汽集团 (600104) 跟踪事项清单\n\n"
        "## 分类汇总\n\n"
        "- **风险信号**: 1 项\n\n"
        "## 跟踪事项明细\n\n"
        "### 🔴 600104-ITEM-001 | 风险信号\n\n"
        "**描述**: 现金流和利润质量需要复核。\n\n"
        "**状态**: open | **优先级**: high\n",
        encoding="utf-8",
    )
    (tracking_dir / "metrics" / "2026-Q2.md").write_text(
        "# 指标追踪面板\n\n"
        "| 指标 | 最新值 |\n| --- | --- |\n| 营业收入 | 100 |\n\n"
        "- [打开PDF页](https://example.test/pdf/1)",
        encoding="utf-8",
    )
    (tracking_dir / "alerts" / "2026-07-11-warning-001.md").write_text("# 预警报告\n", encoding="utf-8")
    (tracking_dir / "updates" / "2026-07-11-update.md").write_text("# 更新记录\n", encoding="utf-8")

    html = module.generate_html_report("600104", "上汽集团", str(wiki_root), "2026-07-11")

    assert "status-panel" in html
    assert "attention-panel" in html
    assert "report-nav" in html
    assert "section-header" in html
    assert "stat-card" in html
    assert "max-height: 600px" not in html
    assert 'target="_blank"' in html
    assert "跟踪事项" in html
    assert "指标追踪" in html
    assert "舆情监控" in html
    assert "预警状态" in html
    assert "更新记录" in html


def test_tracking_chat_routes_generation_to_workflow(monkeypatch):
    calls = {"collect": 0, "workflow": 0}
    saved: list[tuple[str, str, str | None]] = []
    trace_id = "aat_1234567890abcdef1234567890abcdef"

    async def noop_quota(*args, **kwargs):
        return (1, None)

    async def noop_usage(*args, **kwargs):
        return None

    async def fake_resolve_session(*args, **kwargs):
        return "user-7-tracking-session"

    async def fake_save_message(async_session, role, content, session_id, attachments=None, audit_trace_id=None):
        saved.append((role, content, audit_trace_id))
        return SimpleNamespace(id=len(saved), role=role, content=content, session_id=session_id)

    async def fake_collect_chat_reply(*args, **kwargs):
        calls["collect"] += 1
        raise AssertionError("Hermes chat should not run for formal tracking report generation")

    async def fake_record_workspace(*args, **kwargs):
        return {"workspace_synced": True}

    async def fake_workflow_reply(workflow_request):
        calls["workflow"] += 1
        assert workflow_request.company_query == "600104-上汽集团"
        assert workflow_request.session_id == "user-7-tracking-session"
        return SimpleNamespace(
            reply="已生成正式持续跟踪报告\n\n- 打开报告: [HTML 跟踪报告](/api/wiki/companies/600104/tracking/report.html)",
            result={"artifact": {"artifact_type": "tracking"}, "audit_trace_id": trace_id},
        )

    monkeypatch.setattr(agent_user_router, "enforce_quota_or_429_async", noop_quota)
    monkeypatch.setattr(agent_user_router, "record_usage_async", noop_usage)
    monkeypatch.setattr(agent_user_router, "resolve_or_create_session", fake_resolve_session)
    monkeypatch.setattr(agent_user_router, "save_message", fake_save_message)
    monkeypatch.setattr(agent_user_router, "collect_chat_reply", fake_collect_chat_reply)
    monkeypatch.setattr(agent_user_router, "_record_agent_workspace_artifact_background", fake_record_workspace)
    monkeypatch.setattr(agent_user_router, "_run_tracking_workflow_reply", fake_workflow_reply)
    monkeypatch.setattr(
        agent_user_router,
        "get_session_manager",
        lambda: SimpleNamespace(increment_message_count=lambda session_id: None),
    )

    router = create_specialist_agent_router(
        SpecialistAgentConfig(prefix="/tracking", tag="tracking", profile="siq_tracking")
    )
    endpoint = next(route.endpoint for route in router.routes if route.path.endswith("/chat") and "POST" in route.methods)

    async def run_case():
        payload = await endpoint(
            ChatRequest(
                message="请为当前公司生成持续跟踪报告",
                context={"company": {"dir": "600104-上汽集团", "code": "600104", "name": "上汽集团"}},
            ),
            current_user=_user(),
            async_session=SimpleNamespace(),
        )

        assert calls == {"collect": 0, "workflow": 1}
        assert payload.reply.startswith("已生成正式持续跟踪报告")
        assert saved[0] == ("user", "请为当前公司生成持续跟踪报告", None)
        assert saved[1][0] == "assistant"
        assert saved[1][2] == trace_id
        assert payload.audit_trace_id == trace_id
        assert payload.artifact == {"artifact_type": "tracking"}

    anyio.run(run_case)
