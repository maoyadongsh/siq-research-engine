import json
import logging
import subprocess
from pathlib import Path
from types import SimpleNamespace

import anyio
from routers import agent_user_router
from routers.agent_user_router import SpecialistAgentConfig, create_specialist_agent_router
from schemas import ChatRequest
from services.auth_service import User, UserRole
from tests.fact_surface_hash import assert_fact_surface_unchanged, snapshot_company_fact_surface

from services import analysis_report_workflow as workflow


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


def _formal_context(*, market: str = "US", parse_run_id: str = "parse-1") -> dict:
    identity = {
        "market": market,
        "company_id": f"{market}:company-1",
        "filing_id": f"{market}:company-1:filing-1",
        "parse_run_id": parse_run_id,
    }
    return {
        "company": {
            "company_key": "opaque-company-key",
            "code": "TEST",
            "name": "Test Co",
            **identity,
        },
        "source_report": {"report_id": "report-1", **identity},
        "research_identity": identity,
    }


def _formal_target(*, market: str = "US", parse_run_id: str = "parse-1") -> dict:
    identity = {
        "market": market,
        "company_id": f"{market}:company-1",
        "filing_id": f"{market}:company-1:filing-1",
        "parse_run_id": parse_run_id,
    }
    return {
        "schema_version": "siq_research_target_v1",
        "company_key": "opaque-company-key",
        "company_wiki_id": "TEST-Test-Co",
        "display_code": "TEST",
        "display_name": "Test Co",
        "research_identity": identity,
        "source_report": {
            "report_id": "report-1",
            "source_family": "sec_ixbrl" if market == "US" else "pdf_market",
            "form_type": "10-K" if market == "US" else None,
            "fiscal_year": 2024,
            "period_end": "2024-09-28",
            "reporting_currency": "USD" if market == "US" else "HKD",
            "quality_status": "warning",
        },
    }


def test_latest_report_script_prefers_research_pack_capable_profile():
    script = workflow.latest_research_pack_report_script()

    assert script is not None
    assert script.as_posix().endswith("agents/hermes/profiles/siq_analysis/scripts/run_analysis_report.py")
    script_dir = script.parent
    for filename in [
        "html_renderer_v2.py",
        "financial_chart_design.py",
        "renderer_svg_charts.py",
        "renderer_assets.py",
        "run_research_subagents.py",
        "validate_research_packs.py",
        "merge_research_packs.py",
    ]:
        assert (script_dir / filename).is_file()

    source = script.read_text(encoding="utf-8")
    assert "--input-bundle" not in source
    assert "analysis_input_bundle" not in source
    assert workflow._script_supports_analysis_input_bundle(script) is False


def test_latest_multi_market_report_script_uses_isolated_profile():
    script = workflow.latest_multi_market_report_script()

    assert script is not None
    assert script.as_posix().endswith(
        "agents/hermes/profiles/siq_analysis_multi_market/scripts/run_analysis_report.py"
    )
    assert "--input-bundle" in script.read_text(encoding="utf-8")
    assert (script.parent / "analysis_input_bundle.py").is_file()
    assert (script.parent / "analysis_bundle_renderer.py").is_file()


def test_research_pack_script_requires_income_bridge_renderer_files(tmp_path):
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    script = script_dir / "run_analysis_report.py"
    script.write_text("--use-research-packs\n", encoding="utf-8")
    for filename in [
        "html_renderer_v2.py",
        "renderer_svg_charts.py",
        "renderer_assets.py",
        "run_research_subagents.py",
        "validate_research_packs.py",
        "merge_research_packs.py",
    ]:
        (script_dir / filename).write_text("# placeholder\n", encoding="utf-8")

    assert workflow._script_supports_research_packs(script) is False

    (script_dir / "financial_chart_design.py").write_text("# placeholder\n", encoding="utf-8")
    assert workflow._script_supports_research_packs(script) is True


def test_report_generation_intent_excludes_meta_questions():
    assert workflow.is_analysis_report_generation_request("请为美的集团生成完整分析报告") is True
    assert workflow.is_analysis_report_generation_request("为什么分析助手没有调用最新生成器生成报告？") is False


def test_formal_request_preserves_exact_report_identity_and_does_not_use_message_year():
    request = workflow.build_analysis_report_workflow_request(
        "请生成2025年完整分析报告",
        _formal_context(),
    )

    assert request is not None
    assert request.formal_target is True
    assert request.company_key == "opaque-company-key"
    assert request.report_id == "report-1"
    assert request.research_identity == {
        "market": "US",
        "company_id": "US:company-1",
        "filing_id": "US:company-1:filing-1",
        "parse_run_id": "parse-1",
    }
    # Year remains only for the CN compatibility branch; the formal selector is report_id.
    assert request.year == 2025


def test_formal_request_survives_chat_context_schema_round_trip():
    raw = _formal_context()
    context = ChatRequest(message="请生成完整分析报告", context=raw).context

    request = workflow.build_analysis_report_workflow_request("请生成完整分析报告", context)

    assert request is not None
    assert request.formal_target is True
    assert request.company_key == "opaque-company-key"
    assert request.report_id == "report-1"
    assert request.research_identity == raw["research_identity"]
    assert request.validation_error is None


def test_non_cn_formal_request_fails_closed_before_resolution_when_identity_incomplete(monkeypatch):
    context = _formal_context()
    del context["research_identity"]["parse_run_id"]
    del context["company"]["parse_run_id"]
    del context["source_report"]["parse_run_id"]
    request = workflow.build_analysis_report_workflow_request("请生成完整分析报告", context)
    assert request is not None

    monkeypatch.setattr(workflow, "latest_research_pack_report_script", lambda: Path("unused"))
    response = workflow.run_analysis_report_workflow(
        request,
        package_resolver=lambda context: (_ for _ in ()).throw(AssertionError("resolver must not run")),
    )

    assert response.result["stage"] == "research_identity_incomplete"
    assert response.result["details"]["missing_fields"] == ["parse_run_id"]


def test_explicit_overseas_message_without_page_target_never_falls_back_to_cn_resolution(monkeypatch):
    request = workflow.build_analysis_report_workflow_request("请为美股 AAPL 生成完整分析报告")
    assert request is not None
    assert request.formal_target is True
    assert request.research_identity["market"] == "US"
    assert request.validation_error["code"] == "research_identity_incomplete"

    monkeypatch.setattr(workflow, "latest_research_pack_report_script", lambda: Path("unused"))
    response = workflow.run_analysis_report_workflow(
        request,
        package_resolver=lambda context: (_ for _ in ()).throw(AssertionError("resolver must not run")),
    )
    assert response.result["stage"] == "research_identity_incomplete"


def test_non_cn_formal_workflow_respects_multi_market_feature_flag(monkeypatch, tmp_path):
    request = workflow.build_analysis_report_workflow_request("请生成完整分析报告", _formal_context())
    assert request is not None
    script = tmp_path / "run_analysis_report.py"
    script.write_text("# runner", encoding="utf-8")
    monkeypatch.setattr(workflow, "latest_multi_market_report_script", lambda: script)
    monkeypatch.delenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", raising=False)

    response = workflow.run_analysis_report_workflow(
        request,
        package_resolver=lambda context: (_ for _ in ()).throw(AssertionError("resolver must not run")),
    )

    assert response.result["stage"] == "multi_market_research_disabled"


def test_feature_flag_on_keeps_cn_structured_context_on_original_legacy_workflow(monkeypatch, tmp_path):
    script = workflow.latest_research_pack_report_script()
    assert script is not None
    assert script.as_posix().endswith(
        "agents/hermes/profiles/siq_analysis/scripts/run_analysis_report.py"
    )
    calls: list[list[str]] = []

    def fake_run_command(args, *, cwd=None, timeout=None, env=None):
        calls.append(list(args))
        if args[1].endswith("resolve_company.py"):
            payload = _resolved_company_payload(tmp_path)
        else:
            payload = {"ok": True, "stage": "completed", "files": {}, "validation": {"ok": True}}
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(payload, ensure_ascii=False), stderr="")

    monkeypatch.setenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "1")
    monkeypatch.setattr(workflow, "run_command", fake_run_command)
    request = workflow.build_analysis_report_workflow_request(
        "请生成完整分析报告",
        _formal_context(market="CN"),
    )
    assert request is not None and request.formal_target is True
    assert request.validation_error is None

    response = workflow.run_analysis_report_workflow(
        request,
        package_resolver=lambda context: (_ for _ in ()).throw(AssertionError("formal resolver must stay disabled")),
    )

    assert response.result["ok"] is True
    assert len(calls) == 2
    assert Path(calls[0][1]) == script.parent / "resolve_company.py"
    assert Path(calls[1][1]) == script
    assert "--company" in calls[1]
    assert "--input-bundle" not in calls[1]
    assert "siq_analysis_multi_market" not in " ".join(calls[1])


def test_formal_workflow_uses_resolved_bundle_without_company_or_year_resolution(monkeypatch, tmp_path, caplog):
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    script = script_dir / "run_analysis_report.py"
    script.write_text("# runner", encoding="utf-8")
    company_dir = tmp_path / "wiki" / "us" / "companies" / "TEST-Test-Co"
    report_dir = company_dir / "reports" / "report-1"
    report_dir.mkdir(parents=True)
    manifest_path = report_dir / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    fact_surface_before = snapshot_company_fact_surface(company_dir)
    target = _formal_target()
    package = SimpleNamespace(
        company_dir=company_dir,
        report_dir=report_dir,
        manifest_path=manifest_path,
        to_research_target_dict=lambda: target,
    )
    calls: list[list[str]] = []
    written: list[tuple[Path, dict]] = []

    def fake_build_bundle(**kwargs):
        assert kwargs["research_target"] == target
        assert kwargs["company_dir"] == company_dir
        assert kwargs["report_dir"] == report_dir
        return {
            "schema_version": "siq_analysis_input_bundle_v1",
            "research_identity": target["research_identity"],
            "source_report": target["source_report"],
            "adapter": {"source_family": "sec_ixbrl", "version": "1.0.0"},
        }

    def fake_write_bundle(path, bundle):
        written.append((path, bundle))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(bundle), encoding="utf-8")

    def fake_run_command(args, *, cwd=None, timeout=None, env=None):
        calls.append(list(args))
        payload = {
            "ok": True,
            "stage": "completed",
            "pipeline_mode": "formal_analysis_input_bundle",
            "artifact_id": "analysis_artifact_1",
            "files": {"html": str(company_dir / "analysis" / "report.html")},
            "validation": {"ok": True, "status": "pass"},
        }
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "1")
    caplog.set_level(logging.INFO, logger=workflow.__name__)
    monkeypatch.setattr(workflow, "latest_multi_market_report_script", lambda: script)
    monkeypatch.setattr(workflow, "run_command", fake_run_command)
    request = workflow.build_analysis_report_workflow_request("请生成完整分析报告", _formal_context())
    assert request is not None

    response = workflow.run_analysis_report_workflow(
        request,
        package_resolver=lambda context: package,
        bundle_builder=fake_build_bundle,
        bundle_writer=fake_write_bundle,
    )

    assert response.result["ok"] is True
    assert_fact_surface_unchanged(
        fact_surface_before,
        snapshot_company_fact_surface(company_dir),
    )
    assert len(calls) == 1
    command = calls[0]
    assert "--input-bundle" in command
    assert "--company" not in command
    assert "--year" not in command
    assert written[0][0] == Path(command[command.index("--input-bundle") + 1])
    assert response.result["research_identity"] == target["research_identity"]
    assert "/api/research-universe/artifacts/analysis_artifact_1/content" in response.reply
    assert str(company_dir) not in response.reply
    structured = [json.loads(record.message) for record in caplog.records if record.message.startswith("{")]
    completed_log = next(item for item in structured if item.get("event") == "formal_analysis_workflow_completed")
    assert completed_log["market"] == "US"
    assert completed_log["company_id"] == "US:company-1"
    assert completed_log["filing_id"] == "US:company-1:filing-1"
    assert completed_log["parse_run_id"] == "parse-1"
    assert completed_log["source_family"] == "sec_ixbrl"
    assert completed_log["company_ref"] == workflow._company_key_summary("opaque-company-key")
    assert str(company_dir) not in record_messages(caplog)
    assert "请生成完整分析报告" not in record_messages(caplog)


def test_formal_workflow_rejects_resolver_identity_drift(monkeypatch, tmp_path):
    script = tmp_path / "run_analysis_report.py"
    script.write_text("# runner", encoding="utf-8")
    company_dir = tmp_path / "wiki" / "us" / "companies" / "TEST-Test-Co"
    report_dir = company_dir / "reports" / "report-1"
    report_dir.mkdir(parents=True)
    target = _formal_target(parse_run_id="different-parse")
    package = SimpleNamespace(
        company_dir=company_dir,
        report_dir=report_dir,
        manifest_path=report_dir / "manifest.json",
        to_research_target_dict=lambda: target,
    )
    monkeypatch.setenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "1")
    monkeypatch.setattr(workflow, "latest_multi_market_report_script", lambda: script)
    request = workflow.build_analysis_report_workflow_request("请生成完整分析报告", _formal_context())
    assert request is not None

    response = workflow.run_analysis_report_workflow(request, package_resolver=lambda context: package)

    assert response.result["stage"] == "research_identity_mismatch"


def record_messages(caplog) -> str:
    return "\n".join(record.message for record in caplog.records)


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
