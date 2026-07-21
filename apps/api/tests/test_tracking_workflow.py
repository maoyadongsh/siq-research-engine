import importlib.util
import hashlib
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
from services.research_report_package import (
    enumerate_companies,
    enumerate_report_packages,
    resolve_report_package,
)
from tests.fact_surface_hash import assert_fact_surface_unchanged, snapshot_company_fact_surface
from tests.research_universe_fixture import build_six_market_wiki
from tests.specialist_workflow_fixture import write_analysis_target

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_tracking_script_module(filename: str, module_name: str):
    module_path = REPO_ROOT / "data" / "wiki" / "tracking" / "scripts_multi_market" / filename
    script_dir = str(module_path.parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    assert workflow.is_tracking_generation_request("生成舆情日报") is True
    assert workflow.is_tracking_generation_request("为什么持续跟踪智能体没有固化 run_all 能力？") is False


def test_build_sentiment_daily_request_preserves_complete_research_identity():
    identity = {
        "market": "CN",
        "company_id": "600104-上汽集团",
        "filing_id": "CN:600104-上汽集团:2025-annual",
        "parse_run_id": "7dbc35a7-7626-4e81-810e-5dbb764434e0",
    }
    request = workflow.build_tracking_workflow_request(
        "生成舆情日报",
        {
            "market": "CN",
            "company": {"dir": "600104-上汽集团", "code": "600104", "name": "上汽集团"},
            "research_target": {"research_identity": identity},
        },
    )

    assert request is not None
    assert request.workflow_kind == workflow.TRACKING_WORKFLOW_SENTIMENT_DAILY
    assert request.company_query == "600104-上汽集团"
    assert request.research_identity == identity
    assert request.research_context is None


def test_build_tracking_request_uses_current_company_context():
    request = workflow.build_tracking_workflow_request(
        "请刷新持续跟踪报告，并禁用搜索",
        {"company": {"dir": "600104-上汽集团", "code": "600104", "name": "上汽集团"}},
    )

    assert request is not None
    assert request.company_query == "600104-上汽集团"
    assert request.use_search is False


def test_build_tracking_request_keeps_cn_structured_context_on_legacy_path():
    request = workflow.build_tracking_workflow_request(
        "请刷新持续跟踪报告",
        {
            "market": "CN",
            "company_key": "cn-company-key",
            "report_id": "2025-annual",
            "upstream_analysis_artifact_id": "analysis-cn-new-template",
            "company": {
                "dir": "600104-上汽集团",
                "code": "600104",
                "name": "上汽集团",
            },
        },
    )

    assert request is not None
    assert request.research_context is None
    assert request.upstream_analysis_artifact_id == ""


def test_sentiment_report_writes_traceable_evidence_sidecar(tmp_path):
    module = _load_tracking_script_module("module2_sentiment_monitor.py", "tracking_sentiment_evidence_test")
    report_path = Path(
        module.generate_sentiment_report(
            "600104",
            "上汽集团",
            [
                {
                    "id": "600104-SENT-2026-07-22-001",
                    "date": "2026-07-22",
                    "source": "上汽集团官网",
                    "title": "上汽集团发布月度产销公告",
                    "content": "公告披露了最新月度产销数据。",
                    "sentiment": "中性",
                    "url": "https://www.saicmotor.com/example",
                    "published_at": "2026-07-22 09:00",
                    "relevance": "high",
                    "search_backend": "tavily",
                }
            ],
            str(tmp_path),
            "2026-07-22",
        )
    )
    evidence_path = module.sentiment_evidence_path(report_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

    assert evidence["schema_version"] == "siq_tracking_sentiment_evidence_v1"
    assert evidence["source_mode"] == "real"
    assert evidence["unresolved_evidence_ids"] == []
    assert evidence["citations"] == [
        {
            "source_type": "tracking_web_search",
            "source_url": "https://www.saicmotor.com/example",
            "evidence_id": "600104-SENT-2026-07-22-001",
            "quote": "上汽集团发布月度产销公告 公告披露了最新月度产销数据。",
            "title": "上汽集团发布月度产销公告",
            "published_at": "2026-07-22 09:00",
            "period": "2026-07-22",
            "sentiment": "中性",
            "relevance": "high",
            "search_backend": "tavily",
        }
    ]
    report_text = report_path.read_text(encoding="utf-8")
    assert "证据ID: `600104-SENT-2026-07-22-001`" in report_text
    assert "[打开来源](https://www.saicmotor.com/example)" in report_text


def test_sentiment_report_empty_result_is_auditable_degraded_artifact(tmp_path):
    module = _load_tracking_script_module("module2_sentiment_monitor.py", "tracking_sentiment_empty_test")

    result = module.run_sentiment_monitor_summary(
        "600104",
        "上汽集团",
        str(tmp_path),
        "2026-07-22",
        use_search=False,
        allow_simulated=False,
    )

    assert result["status"] == "partial_success"
    assert result["source_mode"] == "empty"
    assert result["summary"] == {
        "total": 0,
        "positive": 0,
        "negative": 0,
        "neutral": 0,
        "sentiment_score": 0,
        "trend": "中性",
    }
    assert result["citations"] == []
    assert Path(result["report_path"]).is_file()
    assert Path(result["evidence_path"]).is_file()


def test_sentiment_evidence_is_revalidated_and_bound_to_resolved_cn_company():
    request = workflow.TrackingWorkflowRequest(
        company_query="600104-上汽集团",
        workflow_kind=workflow.TRACKING_WORKFLOW_SENTIMENT_DAILY,
        research_identity={
            "market": "US",
            "company_id": "forged-company",
            "filing_id": "CN:600104-上汽集团:2025-annual",
            "parse_run_id": "parse-run-1",
        },
    )
    identity = workflow._sentiment_research_identity(
        request,
        {"company_id": "600104-上汽集团", "stock_code": "600104"},
        None,
    )
    citations = workflow._sentiment_citations(
        {
            "citations": [
                {
                    "source_url": "javascript:alert(1)",
                    "evidence_id": "bad",
                    "quote": "bad",
                },
                {
                    "source_url": "https://www.saicmotor.com/example",
                    "evidence_id": "600104-SENT-2026-07-22-001",
                    "quote": "上汽集团发布月度产销公告",
                },
            ]
        },
        identity,
    )

    assert identity["market"] == "CN"
    assert identity["company_id"] == "600104-上汽集团"
    assert len(citations) == 1
    assert citations[0]["research_identity"] == identity


def test_incomplete_non_cn_tracking_context_fails_closed_without_cn_fallback(monkeypatch, tmp_path):
    request = workflow.build_tracking_workflow_request(
        "请刷新持续跟踪报告",
        {"market": "US", "company": {"market": "US", "code": "AAPL"}},
    )

    assert request is not None
    assert request.research_context is not None
    assert request.research_context["market"] == "US"

    script = tmp_path / "run_all.py"
    script.write_text("# multi-market tracking\n", encoding="utf-8")
    monkeypatch.setattr(workflow, "_tracking_script", lambda *, multi_market=False: script)
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

    response = workflow.run_tracking_workflow(request)

    assert response.result["ok"] is False
    assert response.result["stage"] == "company_not_found"


def test_unknown_non_cn_tracking_market_is_rejected_before_script_selection(monkeypatch):
    request = workflow.build_tracking_workflow_request(
        "请刷新持续跟踪报告",
        {"market": "SG", "company": {"market": "SG", "code": "D05"}},
    )
    assert request is not None and request.research_context is not None
    monkeypatch.setattr(
        workflow,
        "_tracking_script",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must reject before selecting a script")),
    )

    response = workflow.run_tracking_workflow(request)

    assert response.result["ok"] is False
    assert response.result["stage"] == "market_not_supported"


def test_run_sentiment_daily_workflow_uses_server_evidence_and_identity(monkeypatch, tmp_path):
    script = tmp_path / "module2_sentiment_monitor.py"
    script.write_text("# sentiment runner\n", encoding="utf-8")
    wiki_root = tmp_path / "wiki"
    company_dir = wiki_root / "companies" / "600104-上汽集团"
    sentiment_dir = company_dir / "tracking" / "sentiment"
    sentiment_dir.mkdir(parents=True)
    report_path = sentiment_dir / "2026-07-22.md"
    evidence_path = sentiment_dir / "2026-07-22.evidence.json"
    identity = {
        "market": "CN",
        "company_id": "600104-上汽集团",
        "filing_id": "CN:600104-上汽集团:2025-annual",
        "parse_run_id": "7dbc35a7-7626-4e81-810e-5dbb764434e0",
    }
    calls = []

    def fake_run_command(args, *, cwd=None, timeout=None, env=None):
        calls.append({"args": list(args), "env": env})
        report_path.write_text("# 上汽集团舆情日报\n", encoding="utf-8")
        evidence = {
            "schema_version": "siq_tracking_sentiment_evidence_v1",
            "source_mode": "real",
            "summary": {"total": 1, "positive": 0, "negative": 0, "neutral": 1, "sentiment_score": 0},
            "real_item_count": 1,
            "unresolved_evidence_ids": [],
            "citations": [
                {
                    "source_type": "tracking_web_search",
                    "source_url": "https://www.saicmotor.com/example",
                    "evidence_id": "600104-SENT-2026-07-22-001",
                    "quote": "上汽集团发布月度产销公告",
                    "title": "上汽集团发布月度产销公告",
                    "period": "2026-07-22",
                }
            ],
        }
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False), encoding="utf-8")
        summary = {
            "status": "success",
            "report_path": str(report_path),
            "evidence_path": str(evidence_path),
        }
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(summary, ensure_ascii=False), stderr="")

    monkeypatch.setattr(workflow, "_tracking_sentiment_script", lambda *, multi_market=False: script)
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
    request = workflow.build_tracking_workflow_request(
        "生成舆情日报",
        {
            "market": "CN",
            "company": {"dir": "600104-上汽集团", "code": "600104", "name": "上汽集团"},
            "research_target": {"research_identity": identity},
        },
    )
    assert request is not None

    response = workflow.run_tracking_workflow(request)

    assert response.result["ok"] is True
    assert response.result["workflow_kind"] == workflow.TRACKING_WORKFLOW_SENTIMENT_DAILY
    assert response.result["citation_count"] == 1
    assert response.result["report_url"].endswith("/tracking/sentiment/2026-07-22.md")
    assert "舆情日报已生成" in response.reply
    assert "可核验引用: `1`" in response.reply
    assert calls and "--json-summary" in calls[0]["args"]
    assert "--real" in calls[0]["args"]
    citation = response.result["artifact"]["citations"][0]
    assert citation["research_identity"] == identity
    assert {field: citation[field] for field in identity} == identity
    assert Path(response.result["artifact_manifest_path"]).is_file()


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

    selected_modes: list[bool] = []

    def select_script(*, multi_market=False):
        selected_modes.append(multi_market)
        return script

    monkeypatch.setattr(workflow, "_tracking_script", select_script)
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
        workflow.TrackingWorkflowRequest(
            company_query="600104-上汽集团",
            use_search=False,
            research_context={
                "market": "CN",
                "company_key": "cn-company-key",
                "report_id": "2025-annual",
            },
            upstream_analysis_artifact_id="analysis-cn-new-template",
        )
    )

    assert response.result["ok"] is True
    assert calls
    cmd = calls[0]["args"]
    assert cmd[cmd.index("--stock") + 1] == "600104"
    assert cmd[cmd.index("--company") + 1] == "上汽集团"
    assert cmd[cmd.index("--wiki-base") + 1] == str(wiki_root)
    assert "--json-summary" in cmd
    assert "--no-search" in cmd
    assert "--target-json" not in cmd
    assert selected_modes == [False]
    assert calls[0]["env"]["SIQ_WIKI_ROOT"] == str(wiki_root)
    assert "已生成正式持续跟踪报告" in response.reply
    assert "/api/wiki/companies/600104-" in response.reply
    assert "HTML 跟踪报告" in response.reply
    assert response.result["artifact"]["artifact_type"] == "tracking"
    assert response.result["artifact"]["validation_result"]["ok"] is True
    assert response.result["artifact"]["metadata"]["postgres_query_status"] == "not_run"
    assert response.result["artifact"]["source_report_path"] == str(source_report_path)
    assert response.result["audit_trace_id"].startswith("aat_")


def test_structured_tracking_publishes_degraded_v2_bound_to_exact_analysis(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki"
    build_six_market_wiki(wiki_root)
    monkeypatch.setenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "1")
    monkeypatch.setenv("SIQ_US_SEC_ANALYSIS_ENABLED", "1")
    company = next(item for item in enumerate_companies(wiki_root=wiki_root, markets=("US",)))
    package = resolve_report_package(
        market="US",
        company_key=company.company_key,
        report_id="2025-10-K-0000320193-25-000079",
        agent_type="tracking",
        wiki_root=wiki_root,
    )
    fact_surface_before = snapshot_company_fact_surface(package.company_dir)
    target = write_analysis_target(package)
    script = tmp_path / "run_all.py"
    script.write_text("# tracking runner\n", encoding="utf-8")
    calls: list[list[str]] = []
    observed_bundle: dict = {}

    def fake_run_command(args, *, cwd=None, timeout=None, env=None):
        calls.append(list(args))
        bundle_path = Path(args[args.index("--target-json") + 1])
        observed_bundle.update(json.loads(bundle_path.read_text(encoding="utf-8")))
        tracking_dir = package.output_dir_for("tracking")
        tracking_dir.mkdir(parents=True, exist_ok=True)
        evidence_ref = {
            "source_type": "sec_html_section",
            "report_id": package.report_id,
            "source_url": "https://www.sec.gov/example",
            "section_id": "item_1a",
            "html_anchor": "item_1a",
        }
        items = tracking_dir / "tracking-items.md"
        metrics = tracking_dir / "metrics" / "2026-Q2.md"
        updates = tracking_dir / "updates" / "2026-07-16-update.md"
        html = tracking_dir / "AAPL-Apple-Inc-跟踪报告-2026-07-16.html"
        for path in (items, metrics, updates):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("```json\n" + json.dumps([evidence_ref]) + "\n```\n", encoding="utf-8")
        html.write_text("<html>tracking</html>", encoding="utf-8")
        summary = {
            "status": "partial_success",
            "degraded_reasons": ["sentiment_source_unavailable"],
            "research_target": package.to_research_target_dict(),
            "source_report_path": str(target.analysis_artifact.html_path),
            "modules": {
                "module1": {"status": "success", "path": str(items)},
                "module2": {"status": "unavailable", "reason": "no_real_source"},
                "module3": {"status": "success", "path": str(metrics)},
                "module4": {"status": "success", "reason": "no_events"},
                "module5": {"status": "success", "path": str(updates)},
                "module6": {"status": "success", "path": str(html)},
            },
            "citation_check": {
                "passed": False,
                "issues": [{"item": "operating_revenue", "issue": "missing source_refs"}],
            },
            "citations": [evidence_ref],
        }
        return subprocess.CompletedProcess(args, 2, stdout=json.dumps(summary), stderr="")

    selected_modes: list[bool] = []

    def select_script(*, multi_market=False):
        selected_modes.append(multi_market)
        return script

    monkeypatch.setattr(workflow, "_tracking_script", select_script)
    monkeypatch.setattr(workflow, "resolve_specialist_target", lambda *args, **kwargs: target)
    monkeypatch.setattr(workflow, "WIKI_ROOT", wiki_root)
    monkeypatch.setattr(workflow, "run_command", fake_run_command)

    response = workflow.run_tracking_workflow(
        workflow.TrackingWorkflowRequest(
            company_query="AAPL-Apple-Inc",
            research_context={"market": "US", "company_key": company.company_key, "report_id": package.report_id},
            upstream_analysis_artifact_id=target.analysis_artifact.artifact.artifact_id,
            allow_simulated_sentiment=True,
            update_analysis=True,
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
    assert response.result["artifact"]["research_target"]["research_identity"] == package.research_identity.to_dict()
    assert "sentiment_source_unavailable" in response.result["artifact"]["metadata"]["degraded_reasons"]
    assert "citation_validation_incomplete" in response.result["artifact"]["metadata"]["degraded_reasons"]
    assert response.result["artifact"]["evidence_summary"]["unresolved_count"] == 1
    assert response.result["validation_result"]["checks"]["citation_validator_passed"] is False
    assert response.result["validation_result"]["failures"] == []
    assert "simulated_sentiment_not_permitted" in response.result["artifact"]["metadata"]["degraded_reasons"]
    assert response.result["html_url"].endswith("/content")
    assert Path(response.result["agent_artifact_v2_manifest_path"]).is_file()
    assert Path(response.result["agent_artifact_v2_html_path"]).is_file()
    assert calls and "--target-json" in calls[0]
    assert selected_modes == [True]
    assert "--stock" not in calls[0]
    assert "--allow-simulated-sentiment" not in calls[0]
    assert "--update-analysis" not in calls[0]
    assert observed_bundle["baseline_analysis_content_hash"] == target.analysis_artifact.artifact.content_hash
    assert "已生成降级持续跟踪报告" in response.reply


def test_tracking_citations_exclude_null_locators():
    located = {
        "source_type": "pdf_table",
        "task_id": "parse-run-1",
        "pdf_page": 17,
    }
    unlocated_metric = {
        "source_type": "normalized_metric",
        "task_id": "parse-run-1",
        "pdf_page": None,
        "table_index": None,
    }

    citations = workflow._tracking_citations(
        {"citations": [located, unlocated_metric]},
        {},
    )

    assert len(citations) == 1
    assert citations[0]["task_id"] == "parse-run-1"
    assert citations[0]["pdf_page"] == 17


def test_tracking_report_manifest_keeps_portable_report_name(tmp_path, monkeypatch):
    module_path = REPO_ROOT / "data" / "wiki" / "tracking" / "scripts_multi_market" / "finsight_tracking_rules.py"
    script_dir = str(module_path.parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    spec = importlib.util.spec_from_file_location("tracking_rules_manifest_test", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tracking_dir = tmp_path / "tracking"
    tracking_dir.mkdir()
    report = tracking_dir / "AAPL-Apple-跟踪报告-2026-07-16.html"
    report.write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(module, "get_tracking_dir", lambda *_args: str(tracking_dir))

    manifest_path = Path(module.write_report_manifest("AAPL", "Apple", str(report)))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["latest_report"] == report.name
    assert manifest["latest_report_path"] == report.name
    assert "/home/" not in manifest_path.read_text(encoding="utf-8")


def test_tracking_single_report_cleanup_preserves_v2_canonical_html(tmp_path, monkeypatch):
    module = _load_tracking_script_module(
        "finsight_tracking_rules.py",
        "tracking_rules_v2_cleanup_test",
    )
    tracking_dir = tmp_path / "tracking"
    tracking_dir.mkdir()
    monkeypatch.setattr(module, "get_tracking_dir", lambda *_args: str(tracking_dir))

    standard_name = module.generate_report_name("AAPL", "Apple Inc", "2026-07-16")
    (tracking_dir / standard_name).write_text("<html>raw</html>", encoding="utf-8")
    canonical_name = "tracking_20260716T052044_127cd2bc5c23.html"
    (tracking_dir / canonical_name).write_text("<html>canonical</html>", encoding="utf-8")
    (tracking_dir / canonical_name.replace(".html", ".artifact.json")).write_text(
        json.dumps(
            {
                "schema_version": "siq_agent_artifact_v2",
                "artifact_type": "tracking",
                "html_file": canonical_name,
            }
        ),
        encoding="utf-8",
    )
    (tracking_dir / "latest.html").symlink_to(standard_name)
    (tracking_dir / "manual-preview.html").write_text("<html>manual</html>", encoding="utf-8")

    archived = module.enforce_single_report_policy(
        "AAPL",
        "Apple Inc",
        date="2026-07-16",
    )

    assert (tracking_dir / canonical_name).is_file()
    assert (tracking_dir / "latest.html").is_file()
    assert not (tracking_dir / "manual-preview.html").exists()
    assert any(Path(path).name == "manual-preview.html" for path in archived)
    assert module.validate_single_report_policy("AAPL", "Apple Inc", date="2026-07-16") is True

    handled = module.delete_manual_html_files("AAPL", "Apple Inc")
    assert handled == []
    assert (tracking_dir / canonical_name).is_file()
    assert (tracking_dir / "latest.html").is_file()


def test_tracking_uses_only_exact_identity_analysis_baseline_citations(tmp_path, monkeypatch):
    wiki_root = tmp_path / "wiki"
    build_six_market_wiki(wiki_root)
    monkeypatch.setenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "1")
    monkeypatch.setenv("SIQ_US_SEC_ANALYSIS_ENABLED", "1")
    company = next(item for item in enumerate_companies(wiki_root=wiki_root, markets=("JP",)))
    package = enumerate_report_packages(company, agent_type="tracking")[0]
    target = write_analysis_target(package, artifact_id="analysis-jp-baseline")
    exact_identity = package.research_identity.to_dict()
    analysis_json = target.analysis_artifact.html_path.with_suffix(".json")
    analysis_bytes = json.dumps(
        {
            "research_identity": exact_identity,
            "evidence_refs": [
                {
                    "report_id": package.report_id,
                    "research_identity": exact_identity,
                    "local_source_id": "metrics/normalized_metrics.json",
                    "pdf_task_id": "jp-task",
                    "pdf_page": 123,
                    "table_id": "117",
                },
                {
                    "report_id": package.report_id,
                    "research_identity": {**exact_identity, "company_id": "JP:OTHER"},
                    "local_source_id": "metrics/normalized_metrics.json",
                    "pdf_page": 1,
                },
            ],
        }
    ).encode("utf-8")
    analysis_json.write_bytes(analysis_bytes)
    sidecar_path = target.analysis_artifact.sidecar_path
    assert sidecar_path is not None
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar.setdefault("metadata", {})["json_file"] = analysis_json.name
    sidecar["metadata"]["content_hashes"] = {
        "json": hashlib.sha256(analysis_bytes).hexdigest(),
    }
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")

    citations = workflow._analysis_baseline_citations(target)

    assert len(citations) == 1
    assert citations[0]["pdf_page"] == 123
    assert citations[0]["research_identity"] == exact_identity

    analysis_json.write_text("{}", encoding="utf-8")
    assert workflow._analysis_baseline_citations(target) == []


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
    module_path = REPO_ROOT / "data" / "wiki" / "tracking" / "scripts_multi_market" / "module4_alert_trigger.py"
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


def test_tracking_normalized_metrics_preserve_currency_period_basis_and_xbrl(monkeypatch):
    module_path = REPO_ROOT / "data" / "wiki" / "tracking" / "scripts_multi_market" / "module3_metrics_tracker.py"
    script_dir = str(module_path.parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    spec = importlib.util.spec_from_file_location("tracking_module3_market_test", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    metrics_path = (
        REPO_ROOT
        / "data/wiki/us/companies/PEN-Penumbra-Inc/reports/2025-10-K-0001321732-26-000007/metrics/normalized_metrics.json"
    )
    if not metrics_path.is_file():
        return
    monkeypatch.setenv("SIQ_TRACKING_REPORT_ID", "2025-10-K-0001321732-26-000007")
    monkeypatch.setenv(
        "SIQ_TRACKING_RESEARCH_IDENTITY",
        json.dumps(
            {
                "market": "US",
                "company_id": "US:0001321732",
                "filing_id": "US:0001321732:0001321732-26-000007",
                "parse_run_id": "c2ee20a6477038cb",
            }
        ),
    )

    normalized = module.load_metrics(str(metrics_path))
    revenue = next(item for item in normalized["data"] if item["canonical_name"] == "operating_revenue")
    changes = module.calculate_changes(revenue["values"])

    assert revenue["currency"] == "USD"
    assert revenue["period_basis"] == "fy:FY:annual"
    assert sorted(revenue["values"]) == ["2023-12-31", "2024-12-31", "2025-12-31"]
    assert changes["latest_yoy"] == 17.5
    assert changes["cagr"] == 15.15
    latest_ref = revenue["evidence_refs_by_period"]["2025-12-31"][0]
    assert latest_ref["report_id"] == "2025-10-K-0001321732-26-000007"
    assert latest_ref["xbrl_fact_id"]
    assert "pdf_page" not in latest_ref


def test_tracking_does_not_compare_incompatible_currency_or_accounting_basis(monkeypatch):
    module_path = REPO_ROOT / "data" / "wiki" / "tracking" / "scripts_multi_market" / "module3_metrics_tracker.py"
    script_dir = str(module_path.parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    spec = importlib.util.spec_from_file_location("tracking_module3_comparability_test", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    payload = {
        "metrics": [
            {
                "canonical_name": "operating_revenue",
                "period_key": "2025-12-31",
                "value": 100,
                "unit": "USD",
                "currency": "USD",
                "accounting_standard": "US_GAAP",
                "qtd_ytd_type": "fy",
                "fiscal_period": "FY",
                "duration_days": 365,
            },
            {
                "canonical_name": "operating_revenue",
                "period_key": "2024-12-31",
                "value": 90,
                "unit": "EUR",
                "currency": "EUR",
                "accounting_standard": "IFRS",
                "qtd_ytd_type": "fy",
                "fiscal_period": "FY",
                "duration_days": 366,
            },
        ]
    }

    groups = module.normalize_metrics_payload(payload)["data"]

    assert len(groups) == 2
    assert {item["currency"] for item in groups} == {"USD", "EUR"}
    assert all(module.calculate_changes(item["values"]) == {} for item in groups)


def test_tracking_movement_text_uses_delta_sign_not_preferred_direction():
    module = _load_tracking_script_module(
        "module3_metrics_tracker.py",
        "tracking_module3_movement_test",
    )

    cases = (
        (7.17, "同比上升 7.17%", "变化不利"),  # HK liabilities
        (2.36, "同比上升 2.36%", "变化不利"),  # KR liabilities
        (-7.31, "同比下降 7.31%", "变化有利"),  # US liabilities
    )
    last_metric = None
    for delta, movement_copy, assessment_copy in cases:
        trend = module.assess_trend({"latest_yoy": delta}, {"direction": "down"})
        last_metric = {
            "canonical_name": "total_liabilities",
            "name": "总负债",
            "latest_yoy": delta,
            "movement": module.classify_movement(delta),
            "assessment": module.ASSESSMENT_BY_TREND[trend],
        }
        rendered = module.render_trend_interpretation(last_metric)
        assert movement_copy in rendered
        assert assessment_copy in rendered

    assert "增长 -7.31%" not in rendered
    assert module.validate_metrics_panel_semantics(rendered, [last_metric]) == []
    assert module.validate_metrics_panel_semantics(
        rendered.replace("同比下降 7.31%", "同比增长 -7.31%"),
        [last_metric],
    ) == ["trend_interpretation_mismatch:total_liabilities"]


def test_tracking_movement_normalizes_positive_and_negative_zero():
    module = _load_tracking_script_module(
        "module3_metrics_tracker.py",
        "tracking_module3_zero_movement_test",
    )

    for delta in (0.0, -0.0, 0.004, -0.004):
        assert module.classify_movement(delta) == "flat"
        rendered = module.render_trend_interpretation(
            {"name": "总负债", "latest_yoy": delta, "movement": "flat"}
        )
        assert "同比持平（0.00%）" in rendered
        assert "影响保持中性" in rendered
        assert "-0.00" not in rendered


def test_tracking_single_period_metrics_are_visible_but_not_judged(tmp_path):
    module = _load_tracking_script_module(
        "module3_metrics_tracker.py",
        "tracking_module3_single_period_test",
    )
    metrics = tmp_path / "normalized_metrics.json"
    metrics.write_text(
        json.dumps(
            {
                "metrics": [
                    {
                        "canonical_name": "operating_revenue",
                        "metric_name": "营业收入",
                        "period_key": "2025-02-28",
                        "value": 11_972_762,
                        "unit": "JPY million",
                        "currency": "JPY",
                        "accounting_standard": "IFRS",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    output = Path(
        module.generate_metrics_panel(
            "3382",
            "Seven & i Holdings Co., Ltd",
            str(metrics),
            str(tmp_path / "tracking" / "metrics"),
            period="2026-Q2",
        )
    )
    content = output.read_text(encoding="utf-8")

    assert "**追踪指标数**: 1 项" in content
    assert "**可比较指标**: 0 项" in content
    assert "缺少可比期间，无法判定同比趋势" in content
    assert "11,972,762.00 JPY million" in content
    assert '"display_unit": "JPY million"' in content
    assert '"display_unit": "亿元"' not in content


def test_tracking_units_distinguish_cn_normalized_base_from_foreign_reported_units():
    module = _load_tracking_script_module(
        "module3_metrics_tracker.py",
        "tracking_module3_market_unit_test",
    )

    assert module.format_metric_value(
        131_442_000_000,
        "operating_revenue",
        "人民币百万元",
        module.CORE_METRICS["operating_revenue"],
        value_basis="normalized_base",
    ) == "1,314.42 亿元"
    assert module.format_metric_value(
        3_027_368,
        "total_liabilities",
        "million",
        module.CORE_METRICS["total_liabilities"],
        currency="USD",
        value_basis="reported_unit",
    ) == "3,027,368.00 USD million"
    assert module.resolve_display_unit(
        "operating_revenue",
        "USD",
        "USD",
        module.CORE_METRICS["operating_revenue"],
    ) == "USD"
    assert module.resolve_display_unit(
        "operating_revenue",
        "KRW million",
        "KRW",
        module.CORE_METRICS["operating_revenue"],
    ) == "KRW million"


def test_tracking_markdown_renderer_elides_yaml_payload_and_closes_lists():
    module = _load_tracking_script_module(
        "module6_html_reporter.py",
        "tracking_module6_markdown_test",
    )
    rendered = module.markdown_to_html(
        "- **风险信号**: 1 项\n\n## 结构化数据\n\n```yaml\nitems:\n  - id: AAPL-1\n```\n"
    )

    assert "完整结构化数据保留在跟踪工作文件中" in rendered
    assert "AAPL-1" not in rendered
    assert '<pre class="code-block yaml">' not in rendered
    assert '<details class="raw-technical-details">' not in rendered
    assert rendered.index("</ul>") < rendered.index("<h2>结构化数据</h2>")


def test_tracking_html_discloses_skipped_sentiment_and_insufficient_metrics(monkeypatch, tmp_path):
    module = _load_tracking_script_module(
        "module6_html_reporter.py",
        "tracking_module6_degraded_disclosure_test",
    )
    company_dir = tmp_path / "companies" / "3382-Seven-and-i"
    tracking_dir = company_dir / "tracking"
    for directory in ("sentiment", "metrics", "alerts", "updates"):
        (tracking_dir / directory).mkdir(parents=True, exist_ok=True)
    (tracking_dir / "tracking-items.md").write_text(
        "# items\n\n## 分类汇总\n\n- **风险信号**: 0 项\n",
        encoding="utf-8",
    )
    (tracking_dir / "metrics" / "2026-Q2.md").write_text(
        "## 指标摘要\n\n- **追踪指标数**: 3 项\n- **可比较指标**: 0 项\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "company_dir_path", lambda *_args: company_dir)
    monkeypatch.setenv("SIQ_TRACKING_SENTIMENT_STATUS", "skipped")
    monkeypatch.setenv("SIQ_TRACKING_SENTIMENT_REASON", "skipped_by_request")

    rendered = module.generate_html_report(
        "3382",
        "Seven & i Holdings Co., Ltd",
        str(tmp_path),
        "2026-07-16",
    )

    assert "数据不足" in rendered
    assert "当前无法判定趋势" in rendered
    assert "不能据此得出无预警结论" in rendered
    assert "本次未执行舆情检索" in rendered
    assert "不能用于判断舆情数量、方向或风险程度" in rendered
    assert "当前无活跃预警" not in rendered


def test_tracking_update_does_not_claim_no_alert_when_metrics_are_insufficient(monkeypatch, tmp_path):
    module = _load_tracking_script_module(
        "module5_report_updater.py",
        "tracking_module5_degraded_update_test",
    )
    metrics = tmp_path / "2026-Q2.md"
    metrics.write_text(
        "## 指标摘要\n\n- **追踪指标数**: 7 项\n- **可比较指标**: 0 项\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SIQ_TRACKING_SENTIMENT_STATUS", "skipped")

    section = module.generate_update_section(
        {"tracking_items": None, "sentiment": None, "metrics": str(metrics), "alerts": None},
        "2026-07-16",
    )

    assert "本次未执行舆情检索" in section
    assert "指标数据不足，不能据此排除风险" in section
    assert "当前无活跃预警" not in section


def test_tracking_citation_validator_checks_latest_alert_only(tmp_path):
    module_path = REPO_ROOT / "data" / "wiki" / "tracking" / "scripts_multi_market" / "validate_citations.py"
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
    module_path = REPO_ROOT / "data" / "wiki" / "tracking" / "scripts_multi_market" / "module6_html_reporter.py"
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


def test_tracking_sentiment_quick_question_routes_sync_and_stream_to_workflow(monkeypatch):
    calls = {"collect": 0, "stream": 0, "workflow": 0}
    saved: list[tuple[str, str | None]] = []
    trace_id = "aat_abcdef1234567890abcdef1234567890"

    async def noop_quota(*args, **kwargs):
        return (1, None)

    async def noop_usage(*args, **kwargs):
        return None

    async def fake_resolve_session(*args, **kwargs):
        return "user-7-tracking-sentiment-session"

    async def fake_save_message(
        async_session,
        role,
        content,
        session_id,
        attachments=None,
        audit_trace_id=None,
        **kwargs,
    ):
        saved.append((role, audit_trace_id))
        return SimpleNamespace(id=len(saved), role=role, content=content, session_id=session_id)

    async def fake_collect_chat_reply(*args, **kwargs):
        calls["collect"] += 1
        raise AssertionError("Hermes chat should not run for sentiment daily generation")

    async def fake_stream_chat_reply(*args, **kwargs):
        calls["stream"] += 1
        raise AssertionError("Hermes stream should not run for sentiment daily generation")
        yield None

    async def fake_workflow_reply(workflow_request):
        calls["workflow"] += 1
        assert workflow_request.workflow_kind == workflow.TRACKING_WORKFLOW_SENTIMENT_DAILY
        assert workflow_request.session_id == "user-7-tracking-sentiment-session"
        return SimpleNamespace(
            reply="舆情日报已生成",
            result={"artifact": {"artifact_type": "tracking"}, "audit_trace_id": trace_id},
        )

    async def fake_record_workspace(*args, **kwargs):
        return {"workspace_synced": True}

    monkeypatch.setattr(agent_user_router, "enforce_quota_or_429_async", noop_quota)
    monkeypatch.setattr(agent_user_router, "record_usage_async", noop_usage)
    monkeypatch.setattr(agent_user_router, "resolve_or_create_session", fake_resolve_session)
    monkeypatch.setattr(agent_user_router, "save_message", fake_save_message)
    monkeypatch.setattr(agent_user_router, "collect_chat_reply", fake_collect_chat_reply)
    monkeypatch.setattr(agent_user_router, "stream_chat_reply", fake_stream_chat_reply)
    monkeypatch.setattr(agent_user_router, "_run_tracking_workflow_reply", fake_workflow_reply)
    monkeypatch.setattr(agent_user_router, "_record_agent_workspace_artifact_background", fake_record_workspace)
    monkeypatch.setattr(
        agent_user_router,
        "get_session_manager",
        lambda: SimpleNamespace(increment_message_count=lambda session_id: None),
    )
    router = create_specialist_agent_router(
        SpecialistAgentConfig(prefix="/tracking", tag="tracking", profile="siq_tracking")
    )
    sync_endpoint = next(
        route.endpoint for route in router.routes if route.path.endswith("/chat") and "POST" in route.methods
    )
    stream_endpoint = next(route.endpoint for route in router.routes if route.path.endswith("/chat/stream"))
    request_payload = ChatRequest(
        message="生成舆情日报",
        context={"company": {"dir": "600104-上汽集团", "code": "600104", "name": "上汽集团"}},
    )

    async def run_case():
        sync_payload = await sync_endpoint(
            request_payload,
            current_user=_user(),
            async_session=SimpleNamespace(),
        )
        assert sync_payload.audit_trace_id == trace_id
        assert sync_payload.artifact == {"artifact_type": "tracking"}

        response = await stream_endpoint(
            request_payload,
            request=SimpleNamespace(),
            current_user=_user(),
            async_session=SimpleNamespace(),
        )
        chunks = [chunk async for chunk in response.body_iterator]
        progress = next(chunk for chunk in chunks if chunk.get("event") == "progress")
        done = next(chunk for chunk in chunks if chunk.get("event") == "done")
        assert json.loads(progress["data"])["title"] == "正在生成舆情日报"
        assert json.loads(done["data"])["audit_trace_id"] == trace_id

    anyio.run(run_case)
    assert calls == {"collect": 0, "stream": 0, "workflow": 2}
    assert saved == [("user", None), ("assistant", trace_id), ("user", None), ("assistant", trace_id)]
