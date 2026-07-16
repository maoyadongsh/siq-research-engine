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
from services.research_report_package import enumerate_companies, resolve_report_package
from tests.fact_surface_hash import assert_fact_surface_unchanged, snapshot_company_fact_surface
from tests.research_universe_fixture import build_six_market_wiki
from tests.specialist_workflow_fixture import write_analysis_target

from services import factcheck_workflow as workflow

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


def test_latest_factcheck_script_uses_independent_profile_for_non_cn_market():
    script = workflow.latest_factcheck_script("US")

    assert script is not None
    assert script.as_posix().endswith(
        "agents/hermes/profiles/siq_factchecker_multi_market/scripts/factcheck_cli.py"
    )


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


def test_build_factcheck_request_keeps_cn_structured_context_on_legacy_route():
    request = workflow.build_factcheck_workflow_request(
        "请生成事实核查报告",
        {
            "market": "CN",
            "company_key": "cn-company-key",
            "report_id": "2025-annual",
            "company": {
                "market": "CN",
                "company_key": "cn-company-key",
                "dir": "600104-上汽集团",
                "code": "600104",
                "name": "上汽集团",
            },
            "report": {
                "type": "analysis",
                "filename": "600104-上汽集团-2025-analysis-research-pack.html",
            },
        },
    )

    assert request is not None
    assert request.research_context is None
    assert request.upstream_analysis_artifact_id == ""
    assert request.company_query == "600104-上汽集团"
    assert request.report_path is not None


def test_incomplete_non_cn_factcheck_context_fails_closed_without_cn_fallback(monkeypatch, tmp_path):
    request = workflow.build_factcheck_workflow_request(
        "请生成事实核查报告",
        {"market": "US", "company": {"market": "US", "code": "AAPL"}},
    )

    assert request is not None
    assert request.research_context is not None
    assert request.research_context["market"] == "US"

    script = tmp_path / "factcheck_cli.py"
    script.write_text("# --report-path --output\n", encoding="utf-8")
    monkeypatch.setattr(workflow, "latest_factcheck_script", lambda market="CN": script)
    monkeypatch.setattr(
        workflow,
        "resolve_specialist_target",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            workflow.ResearchUniverseError(
                "company_not_found",
                "The context does not specify a company key.",
                400,
            )
        ),
    )
    monkeypatch.setattr(
        workflow,
        "_resolve_company",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not enter CN fallback")),
    )

    response = workflow.run_factcheck_workflow(request)

    assert response.result["ok"] is False
    assert response.result["stage"] == "company_not_found"


def test_unknown_non_cn_factcheck_market_is_rejected_before_profile_selection(monkeypatch):
    request = workflow.build_factcheck_workflow_request(
        "请生成事实核查报告",
        {"market": "SG", "company": {"market": "SG", "code": "D05"}},
    )
    assert request is not None and request.research_context is not None
    monkeypatch.setattr(
        workflow,
        "latest_factcheck_script",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must reject before selecting a profile")),
    )

    response = workflow.run_factcheck_workflow(request)

    assert response.result["ok"] is False
    assert response.result["stage"] == "market_not_supported"


def test_run_factcheck_workflow_uses_explicit_report_and_output(monkeypatch, tmp_path):
    monkeypatch.setenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "1")
    monkeypatch.setenv("SIQ_US_SEC_ANALYSIS_ENABLED", "1")
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
                    "checks": {"traceability": {"status": "pass", "issues": []}},
                    "evidence_summary": [
                        {
                            "source_type": "wiki_evidence",
                            "source_path": "companies/600104/reports/2025/report.md",
                            "pdf_page": 69,
                            "metric_or_claim": "operating_revenue",
                        }
                    ],
                    "recommendations": ["补充证据链"],
                    "verified_at": "2026-07-11T00:00:00+08:00",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        output_path.with_suffix(".html").write_text("<html>factcheck</html>", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

    selected_markets: list[str] = []
    selected_profiles: list[str] = []

    def fake_latest_factcheck_script(market="CN"):
        selected_markets.append(market)
        return script

    original_finalize = workflow.finalize_specialist_artifact

    def capture_profile(**kwargs):
        selected_profiles.append(kwargs["profile"])
        return original_finalize(**kwargs)

    monkeypatch.setattr(workflow, "latest_factcheck_script", fake_latest_factcheck_script)
    monkeypatch.setattr(workflow, "finalize_specialist_artifact", capture_profile)
    monkeypatch.setattr(
        workflow,
        "resolve_specialist_target",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("CN must not resolve a structured target")),
    )
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
            research_context={
                "market": "CN",
                "company_key": "cn-company-key",
                "report_id": "2025-annual",
            },
        )
    )

    assert response.result["ok"] is True
    assert calls
    assert selected_markets == ["CN"]
    assert selected_profiles == ["siq_factchecker"]
    cmd = calls[0]
    assert "verify" in cmd
    assert "--report-path" in cmd
    assert "--target-json" not in cmd
    assert cmd[cmd.index("--report-path") + 1] == str(requested_report)
    assert "--output" in cmd
    assert "factcheck-" in cmd[cmd.index("--output") + 1]
    assert "已生成正式事实核查报告" in response.reply
    assert "HTML 核查报告" in response.reply
    assert response.result["artifact"]["artifact_type"] == "factcheck"
    assert response.result["artifact"]["validation_result"]["ok"] is True
    assert response.result["artifact"]["metadata"]["claim_verdicts"][0]["claim_id"] == "traceability"
    assert response.result["audit_trace_id"].startswith("aat_")


def test_structured_factcheck_publishes_v2_for_exact_analysis(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki"
    build_six_market_wiki(wiki_root)
    monkeypatch.setenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "1")
    monkeypatch.setenv("SIQ_US_SEC_ANALYSIS_ENABLED", "1")
    company = next(item for item in enumerate_companies(wiki_root=wiki_root, markets=("US",)))
    package = resolve_report_package(
        market="US",
        company_key=company.company_key,
        report_id="2025-10-K-0000320193-25-000079",
        agent_type="factcheck",
        wiki_root=wiki_root,
    )
    fact_surface_before = snapshot_company_fact_surface(package.company_dir)
    target = write_analysis_target(package)
    script = tmp_path / "factcheck_cli.py"
    script.write_text("# --report-path --output --target-json\n", encoding="utf-8")
    calls: list[list[str]] = []
    observed_bundle: dict = {}

    def fake_run_command(args, *, cwd=None, timeout=None, env=None):
        calls.append(list(args))
        bundle_path = Path(args[args.index("--target-json") + 1])
        observed_bundle.update(json.loads(bundle_path.read_text(encoding="utf-8")))
        output = Path(args[args.index("--output") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        evidence = {
            "source_type": "sec_html_section",
            "report_id": package.report_id,
            "source_url": "https://www.sec.gov/example",
            "section_id": "item_1a",
            "html_anchor": "item_1a",
        }
        output.write_text(
            json.dumps(
                {
                    "schema_version": "siq_market_factcheck_v1",
                    "verdict": "request_changes",
                    "company_id": package.research_identity.company_id,
                    "research_identity": package.research_identity.to_dict(),
                    "report_file": target.analysis_artifact.html_path.name,
                    "summary": {"critical": 0, "warning": 1, "suggestion": 0},
                    "checks": {
                        "identity_consistency": {"status": "pass", "issues": []},
                        "traceability": {"status": "pass", "issues": []},
                    },
                    "evidence_summary": [evidence],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        output.with_suffix(".html").write_text("<html>factcheck</html>", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout="completed", stderr="")

    selected_markets: list[str] = []
    selected_profiles: list[str] = []

    def fake_latest_factcheck_script(market="CN"):
        selected_markets.append(market)
        return script

    original_finalize = workflow.finalize_specialist_artifact

    def capture_profile(**kwargs):
        selected_profiles.append(kwargs["profile"])
        return original_finalize(**kwargs)

    monkeypatch.setattr(workflow, "latest_factcheck_script", fake_latest_factcheck_script)
    monkeypatch.setattr(workflow, "finalize_specialist_artifact", capture_profile)
    monkeypatch.setattr(workflow, "resolve_specialist_target", lambda *args, **kwargs: target)
    monkeypatch.setattr(workflow, "WIKI_ROOT", wiki_root)
    monkeypatch.setattr(workflow, "run_command", fake_run_command)

    response = workflow.run_factcheck_workflow(
        workflow.FactcheckWorkflowRequest(
            company_query="AAPL-Apple-Inc",
            research_context={"market": "US", "company_key": company.company_key, "report_id": package.report_id},
            upstream_analysis_artifact_id=target.analysis_artifact.artifact.artifact_id,
        )
    )

    assert response.result["ok"] is True
    assert_fact_surface_unchanged(
        fact_surface_before,
        snapshot_company_fact_surface(package.company_dir),
    )
    assert response.result["stage"] == "degraded"
    assert response.result["artifact"]["schema_version"] == "siq_agent_artifact_v2"
    assert response.result["artifact"]["upstream_artifact_ids"] == [
        target.analysis_artifact.artifact.artifact_id
    ]
    assert response.result["artifact"]["metadata"]["checked_claim_count"] == 0
    assert response.result["html_url"].endswith("/content")
    assert Path(response.result["agent_artifact_v2_manifest_path"]).is_file()
    assert Path(response.result["agent_artifact_v2_html_path"]).is_file()
    assert calls and "--target-json" in calls[0]
    assert selected_markets == ["US"]
    assert selected_profiles == ["siq_factchecker_multi_market"]
    assert package.research_identity.company_id not in calls[0]
    assert observed_bundle["baseline_analysis_artifact_id"] == target.analysis_artifact.artifact.artifact_id
    assert "已生成降级事实核查报告" in response.reply


def test_factcheck_citations_exclude_unlocated_normalized_metrics():
    located = {
        "source_type": "pdf_table",
        "report_id": "2025-annual",
        "task_id": "parse-run-1",
        "pdf_page_number": 17,
        "table_index": 9,
    }
    unlocated_metric = {
        "source_type": "normalized_metric",
        "report_id": "2025-annual",
        "xbrl_fact_id": None,
        "html_anchor": None,
    }

    citations = workflow._factcheck_citations(
        {
            "evidence_summary": [located],
            "metric_evidence_map": {"revenue": unlocated_metric},
        }
    )

    assert len(citations) == 1
    assert citations[0]["report_id"] == "2025-annual"
    assert citations[0]["pdf_page"] == 17
    assert citations[0]["table_index"] == 9


def test_factcheck_prefers_structured_claim_verdicts_over_dimension_placeholders():
    verdicts = workflow._claim_verdicts(
        {
            "claim_verdicts": [
                {
                    "claim_id": "revenue-current",
                    "claim": "本期营业收入为 100 美元。",
                    "claim_type": "metric_value",
                    "metric_key": "operating_revenue",
                    "period": "2025-12-31",
                    "status": "verified",
                    "reason": "",
                },
                {
                    "claim_id": "profit-current",
                    "claim": "本期净利润为 20 美元。",
                    "claim_type": "metric_value",
                    "metric_key": "net_profit",
                    "period": "2025-12-31",
                    "status": "contradicted",
                    "reason": "源指标语义不一致",
                },
            ],
            "checks": {"traceability": {"status": "pass", "issues": []}},
        }
    )

    assert [item["claim_id"] for item in verdicts] == ["revenue-current", "profit-current"]
    assert [item["verdict"] for item in verdicts] == ["verified", "contradicted"]
    assert verdicts[1]["reason"] == "源指标语义不一致"


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


def test_factcheck_pg_config_reuses_project_app_url_for_pdf2md(monkeypatch):
    for key in (
        "SIQ_PDF2MD_DATABASE_URL",
        "SIQ_CN_DATABASE_URL",
        "SIQ_APP_DATABASE_URL",
        "SIQ_PGHOST",
        "SIQ_PGPORT",
        "SIQ_PGDATABASE",
        "SIQ_PGUSER",
        "SIQ_PGPASSWORD",
        "PGHOST",
        "PGPORT",
        "PGDATABASE",
        "PGUSER",
        "PGPASSWORD",
        "POSTGRES_PASSWORD",
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SIQ_APP_DATABASE_URL", "postgresql+psycopg://postgres:secret@postgres:5432/siq_app")

    config = factcheck_cli._project_pdf2md_pg_config()

    assert config == {
        "host": "postgres",
        "port": 5432,
        "dbname": "siq",
        "user": "postgres",
        "password": "secret",
    }


def test_factchecker_chat_routes_generation_to_workflow(monkeypatch):
    calls = {"collect": 0, "workflow": 0}
    saved: list[tuple[str, str, str | None]] = []
    trace_id = "aat_1234567890abcdef1234567890abcdef"

    async def noop_quota(*args, **kwargs):
        return (1, None)

    async def noop_usage(*args, **kwargs):
        return None

    async def fake_resolve_session(*args, **kwargs):
        return "user-7-factchecker-session"

    async def fake_save_message(async_session, role, content, session_id, attachments=None, audit_trace_id=None):
        saved.append((role, content, audit_trace_id))
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
        assert workflow_request.session_id == "user-7-factchecker-session"
        return SimpleNamespace(
            reply="已生成正式事实核查报告\n\n- 打开报告: [HTML 核查报告](/api/wiki/companies/600104/factcheck/report.html)",
            result={"artifact": {"artifact_type": "factcheck"}, "audit_trace_id": trace_id},
        )

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
        assert saved[0] == ("user", "请为当前分析报告生成事实核查报告", None)
        assert saved[1][0] == "assistant"
        assert saved[1][2] == trace_id
        assert payload.audit_trace_id == trace_id
        assert payload.artifact == {"artifact_type": "factcheck"}

    anyio.run(run_case)


def test_factchecker_stream_done_returns_artifact_and_persisted_trace(monkeypatch):
    trace_id = "aat_1234567890abcdef1234567890abcdef"
    saved: list[tuple[str, str | None]] = []

    async def noop_quota(*args, **kwargs):
        return (1, None)

    async def noop_usage(*args, **kwargs):
        return None

    async def fake_resolve_session(*args, **kwargs):
        return "user-7-factchecker-session"

    async def fake_save_message(async_session, role, content, session_id, attachments=None, audit_trace_id=None):
        saved.append((role, audit_trace_id))
        return SimpleNamespace(id=len(saved), role=role, content=content, session_id=session_id)

    async def fake_workflow_reply(workflow_request):
        return SimpleNamespace(
            reply="已生成正式事实核查报告",
            result={"artifact": {"artifact_type": "factcheck"}, "audit_trace_id": trace_id},
        )

    async def fake_record_workspace(*args, **kwargs):
        return {"workspace_synced": True}

    monkeypatch.setattr(agent_user_router, "enforce_quota_or_429_async", noop_quota)
    monkeypatch.setattr(agent_user_router, "record_usage_async", noop_usage)
    monkeypatch.setattr(agent_user_router, "resolve_or_create_session", fake_resolve_session)
    monkeypatch.setattr(agent_user_router, "save_message", fake_save_message)
    monkeypatch.setattr(agent_user_router, "_run_factcheck_workflow_reply", fake_workflow_reply)
    monkeypatch.setattr(agent_user_router, "_record_agent_workspace_artifact_background", fake_record_workspace)
    monkeypatch.setattr(
        agent_user_router,
        "get_session_manager",
        lambda: SimpleNamespace(increment_message_count=lambda session_id: None),
    )

    router = create_specialist_agent_router(
        SpecialistAgentConfig(prefix="/factcheck", tag="factchecker", profile="siq_factchecker")
    )
    endpoint = next(route.endpoint for route in router.routes if route.path.endswith("/chat/stream"))

    async def run_case():
        response = await endpoint(
            ChatRequest(
                message="请为当前分析报告生成事实核查报告",
                context={
                    "company": {"dir": "600104-上汽集团", "code": "600104", "name": "上汽集团"},
                    "report": {"type": "analysis", "filename": "report.md"},
                },
            ),
            request=SimpleNamespace(),
            current_user=_user(),
            async_session=SimpleNamespace(),
        )
        chunks = [chunk async for chunk in response.body_iterator]
        done = next(chunk for chunk in chunks if chunk.get("event") == "done")
        payload = json.loads(done["data"])
        assert payload["audit_trace_id"] == trace_id
        assert payload["artifact"] == {"artifact_type": "factcheck"}
        assert saved[-1] == ("assistant", trace_id)

    anyio.run(run_case)
