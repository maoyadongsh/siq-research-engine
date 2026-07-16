from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts/maintenance/run_secondary_market_multi_market_real_smoke.py"


def _module():
    spec = importlib.util.spec_from_file_location("secondary_market_real_smoke", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _smoke_target(market: str) -> dict:
    code = "000333" if market == "CN" else "AAPL"
    identity = {
        "market": market,
        "company_id": f"{market}:{code}",
        "filing_id": f"{market}:{code}:filing",
        "parse_run_id": "parse-run",
    }
    return {
        "company_key": f"rk1_{market.lower()}_example",
        "company_wiki_id": f"{code}-Example",
        "display_code": code,
        "display_name": "示例公司" if market == "CN" else "Example Inc.",
        "research_identity": identity,
        "source_report": {
            "report_id": "2025-annual" if market == "CN" else "2025-10-K",
            "source_family": "cn_pdf" if market == "CN" else "sec_ixbrl",
            "fiscal_year": 2025,
            **identity,
        },
    }


def _write_cn_golden_artifacts(
    company_dir: Path,
    module,
    *,
    analysis_html: str = "<!doctype html><html><body>原有A股分析报告</body></html>",
    latest_report: str | None = None,
) -> None:
    analysis_dir = company_dir / "analysis"
    factcheck_dir = company_dir / "factcheck"
    tracking_dir = company_dir / "tracking"
    for directory in (analysis_dir, factcheck_dir, tracking_dir):
        directory.mkdir(parents=True, exist_ok=True)
    (company_dir / "company.json").write_text(
        json.dumps({"stock_code": module.CN_GOLDEN_COMPANY_CODE}, ensure_ascii=False),
        encoding="utf-8",
    )
    analysis_prefix = analysis_dir / module.CN_GOLDEN_ANALYSIS_STEM
    analysis_prefix.with_suffix(".html").write_text(analysis_html, encoding="utf-8")
    analysis_prefix.with_suffix(".md").write_text("# 原有A股分析报告\n", encoding="utf-8")
    analysis_prefix.with_suffix(".json").write_text(
        json.dumps({"schema_version": 1}, ensure_ascii=False),
        encoding="utf-8",
    )
    (factcheck_dir / module.CN_GOLDEN_FACTCHECK_FILENAME).write_text(
        "<!doctype html><html><body>原有A股事实核查报告</body></html>",
        encoding="utf-8",
    )
    (tracking_dir / module.CN_GOLDEN_TRACKING_FILENAME).write_text(
        "<!doctype html><html><body>原有A股持续跟踪报告</body></html>",
        encoding="utf-8",
    )
    (tracking_dir / module.CN_GOLDEN_TRACKING_MANIFEST).write_text(
        json.dumps(
            {"latest_report": latest_report or module.CN_GOLDEN_TRACKING_FILENAME},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _quality_artifacts(
    tmp_path: Path,
    *,
    claims: list[dict] | None = None,
    factcheck_verdict: str = "approve",
    analysis_details_attributes: str = 'class="evidence-catalog"',
    catalog_evidence_ids: tuple[str, ...] = ("evidence-1", "evidence-2"),
    section_raw_link_count: int = 0,
    tracking_statement: str = "舆情模块已跳过；缺少可比期，无法判断是否稳定。",
    comparable_periods: bool = False,
    language: str = "zh-CN",
    non_cn_unit: str = "USD million",
):
    narrative = "公司财务表现需要结合报告期、披露口径、经营环境与证据逐项审慎判断。" * 100
    raw_links = "".join(
        f'<a href="#evidence-evidence-{index}"><code>evidence-{index}</code></a>'
        for index in range(section_raw_link_count)
    )
    catalog_rows = "".join(
        f'<div class="evidence-reference" id="evidence-{evidence_id}"><span>报表定位 {index}</span></div>'
        for index, evidence_id in enumerate(catalog_evidence_ids, 1)
    )
    analysis_html = (
        f'<!doctype html><html lang="{language}"><body><main>'
        f"<section><h2>财务分析</h2><p>{narrative} {non_cn_unit}</p>{raw_links}</section>"
        f"<details {analysis_details_attributes}><summary>完整证据目录</summary>{catalog_rows}</details>"
        "</main></body></html>"
    )
    factcheck_html = (
        f'<!doctype html><html lang="{language}"><body><main><h1>事实核查</h1><p>{narrative}</p></main></body></html>'
    )
    tracking_html = (
        f'<!doctype html><html lang="{language}"><body><main><h1>持续跟踪</h1>'
        f"<p>{narrative}</p><p>{tracking_statement}</p></main></body></html>"
    )
    html_by_workflow = {
        "analysis": analysis_html,
        "factcheck": factcheck_html,
        "tracking": tracking_html,
    }
    analysis_facts = [
        {
            "metric_key": "operating_revenue",
            "period": "2025-12-31",
            "currency": "USD",
            "scope": "consolidated",
            "dimensions": {},
        }
    ]
    if comparable_periods:
        analysis_facts.append(
            {
                "metric_key": "operating_revenue",
                "period": "2024-12-31",
                "currency": "USD",
                "scope": "consolidated",
                "dimensions": {},
            }
        )
    analysis_report = {
        "facts": analysis_facts,
        "evidence_refs": [
            {"evidence_id": "evidence-1", "section_id": "financials"},
            {"evidence_id": "evidence-2", "section_id": "notes"},
        ],
    }
    (tmp_path / "analysis.json").write_text(json.dumps(analysis_report, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "factcheck.json").write_text(
        json.dumps({"verdict": factcheck_verdict}, ensure_ascii=False), encoding="utf-8"
    )
    if claims is None:
        claims = [
            {
                "claim_id": "claim-1",
                "claim": "营业收入已由报告证据支持。",
                "evidence_ids": ["evidence-1", "evidence-2"],
            }
        ]
    artifacts = {}
    for workflow, html_text in html_by_workflow.items():
        html_path = tmp_path / f"{workflow}.html"
        html_path.write_text(html_text, encoding="utf-8")
        metadata = {}
        if workflow == "analysis":
            metadata = {
                "json_file": "analysis.json",
                "claims": claims,
                "evidence_catalog": {
                    "rendered_count": 2,
                    "total_count": 2,
                    "limit": 64,
                    "full_evidence_file": "analysis.json",
                },
            }
        elif workflow == "factcheck":
            metadata = {
                "json_file": "factcheck.json",
                "verdict": factcheck_verdict,
                "checked_claim_count": len(claims),
                "verified_claim_count": len(claims),
                "contradicted_claim_count": 0,
                "unsupported_claim_count": 0,
            }
        artifacts[workflow] = (
            {"artifact_type": workflow, "metadata": metadata},
            tmp_path / f"{workflow}.artifact.json",
            html_path,
        )
    return artifacts


def test_context_preserves_exact_identity_without_paths():
    module = _module()
    target = {
        "company_key": "rk1_example",
        "company_wiki_id": "AAPL-Apple-Inc",
        "display_code": "AAPL",
        "display_name": "Apple Inc.",
        "research_identity": {
            "market": "US",
            "company_id": "US:0000320193",
            "filing_id": "US:0000320193:filing",
            "parse_run_id": "parse-run",
        },
        "source_report": {
            "report_id": "2025-10-K",
            "source_family": "sec_ixbrl",
        },
    }

    context = module._research_context(target, baseline_id="analysis_example")

    assert context["research_identity"] == target["research_identity"]
    assert context["report_id"] == "2025-10-K"
    assert context["upstream_analysis_artifact_id"] == "analysis_example"
    assert not any(key.endswith("path") for key in context)


@pytest.mark.parametrize("workflow", ["analysis", "factcheck", "tracking"])
def test_pipeline_request_guard_rejects_cn_formal_routing(workflow):
    module = _module()
    request = SimpleNamespace(
        formal_target=True,
        context_payload={"market": "CN"},
        company_key="rk1_cn_example",
        report_id="2025-annual",
        research_identity={"market": "CN"},
        research_context={"market": "CN"},
        upstream_analysis_artifact_id="analysis-id",
        report_path=Path("legacy-analysis.html"),
    )

    with pytest.raises(module.PipelineRegression) as exc_info:
        module._assert_pipeline_request("CN", workflow, request)

    assert exc_info.value.code == "cn_pipeline_regression"


@pytest.mark.parametrize("workflow", ["analysis", "factcheck", "tracking"])
def test_pipeline_request_guard_rejects_non_cn_legacy_routing(workflow):
    module = _module()
    request = SimpleNamespace(
        formal_target=False,
        context_payload=None,
        company_key="",
        report_id="",
        research_identity=None,
        research_context=None,
        upstream_analysis_artifact_id="",
        report_path=Path("legacy-analysis.html"),
    )

    with pytest.raises(module.PipelineRegression) as exc_info:
        module._assert_pipeline_request("US", workflow, request)

    assert exc_info.value.code == "non_cn_pipeline_regression"


def test_default_selection_uses_parsed_ready_preferred_company(tmp_path, monkeypatch):
    module = _module()
    from tests.research_universe_fixture import build_six_market_wiki

    wiki_root = tmp_path / "wiki"
    build_six_market_wiki(wiki_root)
    monkeypatch.setenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "1")
    monkeypatch.setenv("SIQ_US_SEC_ANALYSIS_ENABLED", "1")

    target = module._select_target_from_universe("US", wiki_root=wiki_root)

    assert target["display_code"] == "AAPL"
    assert target["source_report"]["form_type"] == "10-K"
    assert target["source_report"]["quality_status"] in {"pass", "warning"}


def test_sanitizer_rejects_local_paths():
    module = _module()

    with pytest.raises(RuntimeError, match="local filesystem path"):
        module._reject_sensitive_strings({"detail": "/home/example/private/report.md"})


def test_artifact_summary_keeps_only_release_gate_metadata():
    module = _module()
    summary = module._artifact_summary(
        {
            "artifact_id": "analysis_example",
            "status": "completed",
            "source_report_id": "2025-10-K",
            "source_family": "sec_ixbrl",
            "adapter_version": "1.0.0",
            "upstream_artifact_ids": [],
            "content_hash": "a" * 64,
            "quality": {"status": "pass", "warnings": []},
            "evidence_summary": {"citation_count": 3, "unresolved_count": 0},
            "metadata": {
                "research_pack_validation_status": "pass",
                "json_file": "/home/private/report.json",
            },
        }
    )

    assert summary["metadata"] == {"research_pack_validation_status": "pass"}
    module._reject_sensitive_strings(summary)


def test_content_quality_gate_passes_code_only_payload(tmp_path):
    module = _module()

    result = module._run_content_quality_checks("US", _quality_artifacts(tmp_path), skip_sentiment=True)

    assert result["status"] == "passed"
    assert result["failure_codes"] == []
    assert all(set(item) >= {"code", "passed"} for item in result["checks"])
    assert all(item["passed"] for item in result["checks"])
    module._reject_sensitive_strings(result)


def test_formal_content_quality_gate_never_applies_to_cn(tmp_path):
    module = _module()

    with pytest.raises(ValueError, match="only apply to overseas markets"):
        module._run_content_quality_checks(
            "CN",
            _quality_artifacts(tmp_path),
            skip_sentiment=True,
        )


def test_content_quality_gate_requires_chinese_language_and_narrative(tmp_path):
    module = _module()
    artifacts = _quality_artifacts(tmp_path, language="en")
    tracking_path = artifacts["tracking"][2]
    tracking_path.write_text(
        '<!doctype html><html lang="en"><body>tracking report</body></html>',
        encoding="utf-8",
    )

    result = module._run_content_quality_checks("US", artifacts, skip_sentiment=True)

    assert "analysis_html_lang_zh_cn" in result["failure_codes"]
    assert "factcheck_html_lang_zh_cn" in result["failure_codes"]
    assert "tracking_html_lang_zh_cn" in result["failure_codes"]
    assert "tracking_html_chinese_narrative" in result["failure_codes"]


def test_content_quality_gate_rejects_non_cn_yi_yuan(tmp_path):
    module = _module()

    result = module._run_content_quality_checks(
        "HK",
        _quality_artifacts(tmp_path, non_cn_unit="人民币亿元"),
        skip_sentiment=True,
    )

    assert "analysis_html_market_unit" in result["failure_codes"]


@pytest.mark.parametrize(
    ("claims", "expected_code"),
    [
        ([], "analysis_structured_claims_present"),
        (
            [{"claim_id": "claim-1", "evidence_ids": ["evidence-1"]}],
            "analysis_structured_claims_valid",
        ),
        (
            [{"claim_id": "claim-1", "claim": "缺少证据绑定。"}],
            "analysis_claims_evidence_bound",
        ),
    ],
)
def test_content_quality_gate_requires_structured_evidence_bound_claims(tmp_path, claims, expected_code):
    module = _module()

    result = module._run_content_quality_checks("US", _quality_artifacts(tmp_path, claims=claims), skip_sentiment=True)

    assert expected_code in result["failure_codes"]


def test_content_quality_gate_never_approves_without_claims(tmp_path):
    module = _module()

    result = module._run_content_quality_checks(
        "US",
        _quality_artifacts(tmp_path, claims=[], factcheck_verdict="approve"),
        skip_sentiment=True,
    )

    assert "factcheck_never_approves_without_claims" in result["failure_codes"]
    request_changes = module._run_content_quality_checks(
        "US",
        _quality_artifacts(tmp_path, claims=[], factcheck_verdict="request_changes"),
        skip_sentiment=True,
    )
    assert "factcheck_never_approves_without_claims" not in request_changes["failure_codes"]


def test_content_quality_gate_requires_every_analysis_claim_to_be_verified(tmp_path):
    module = _module()
    artifacts = _quality_artifacts(tmp_path)
    artifacts["factcheck"][0]["metadata"].update(
        checked_claim_count=1,
        verified_claim_count=0,
        contradicted_claim_count=0,
        unsupported_claim_count=1,
    )

    result = module._run_content_quality_checks(
        "US",
        artifacts,
        skip_sentiment=True,
    )

    assert "factcheck_claims_supported" in result["failure_codes"]


def test_content_quality_gate_requires_collapsed_complete_evidence_catalog(tmp_path):
    module = _module()

    expanded = module._run_content_quality_checks(
        "US",
        _quality_artifacts(tmp_path, analysis_details_attributes='open class="evidence-catalog"'),
        skip_sentiment=True,
    )
    incomplete = module._run_content_quality_checks(
        "US",
        _quality_artifacts(tmp_path, catalog_evidence_ids=("evidence-1",)),
        skip_sentiment=True,
    )

    assert "analysis_evidence_catalog_collapsed" in expanded["failure_codes"]
    assert "analysis_evidence_catalog_complete" in incomplete["failure_codes"]


def test_content_quality_gate_rejects_raw_evidence_id_link_flood(tmp_path):
    module = _module()

    result = module._run_content_quality_checks(
        "US",
        _quality_artifacts(
            tmp_path,
            section_raw_link_count=module.MAX_RAW_EVIDENCE_LINKS_PER_SECTION + 1,
        ),
        skip_sentiment=True,
    )

    assert "analysis_sections_without_raw_evidence_flood" in result["failure_codes"]


def test_content_quality_gate_requires_honest_skipped_sentiment_language(tmp_path):
    module = _module()

    missing_disclosure = module._run_content_quality_checks(
        "US",
        _quality_artifacts(tmp_path, tracking_statement="舆情模块等待后续核查。"),
        skip_sentiment=True,
    )
    misrepresented = module._run_content_quality_checks(
        "US",
        _quality_artifacts(tmp_path, tracking_statement="舆情模块已跳过，暂无舆情数据。"),
        skip_sentiment=True,
    )
    correctly_negated = module._run_content_quality_checks(
        "US",
        _quality_artifacts(
            tmp_path,
            tracking_statement="舆情模块已跳过，不能据此判断暂无相关舆情。",
        ),
        skip_sentiment=True,
    )

    assert "tracking_sentiment_skip_disclosed" in missing_disclosure["failure_codes"]
    assert "tracking_sentiment_skip_not_misrepresented" in misrepresented["failure_codes"]
    assert "tracking_sentiment_skip_not_misrepresented" not in correctly_negated["failure_codes"]


def test_content_quality_gate_rejects_stability_claim_without_comparable_period(tmp_path):
    module = _module()

    result = module._run_content_quality_checks(
        "JP",
        _quality_artifacts(
            tmp_path,
            tracking_statement="舆情来源不可用；财务表现总体稳定，未识别活跃预警。",
            comparable_periods=False,
        ),
        skip_sentiment=True,
    )

    assert "tracking_no_comparable_period_not_overclaimed" in result["failure_codes"]


def test_content_quality_gate_accepts_explicit_no_warning_caveat_without_comparables(tmp_path):
    module = _module()

    result = module._run_content_quality_checks(
        "JP",
        _quality_artifacts(
            tmp_path,
            tracking_statement=(
                "舆情来源不可用；当前没有已触发预警，但指标数据不足，"
                "不能据此排除风险，也无法得出无预警结论。"
            ),
            comparable_periods=False,
        ),
        skip_sentiment=False,
    )

    assert "tracking_no_comparable_period_not_overclaimed" not in result["failure_codes"]
    comparable = module._run_content_quality_checks(
        "JP",
        _quality_artifacts(
            tmp_path,
            tracking_statement="舆情来源不可用；财务表现总体稳定。",
            comparable_periods=True,
        ),
        skip_sentiment=True,
    )
    assert "tracking_no_comparable_period_not_overclaimed" not in comparable["failure_codes"]


def test_cn_market_reads_fixed_golden_artifacts_without_running_workflows(tmp_path, monkeypatch):
    module = _module()
    target = _smoke_target("CN")
    package = SimpleNamespace(company_dir=tmp_path)
    fact_surface = SimpleNamespace(digest="a" * 64, files=("facts.json",))
    _write_cn_golden_artifacts(tmp_path, module)

    monkeypatch.setattr(module, "_select_target_from_universe", lambda market: target)
    monkeypatch.setattr(module, "resolve_report_package_from_context", lambda context, agent_type: package)
    monkeypatch.setattr(module, "snapshot_company_fact_surface", lambda company_dir: fact_surface)
    monkeypatch.setattr(module, "assert_fact_surface_unchanged", lambda before, after: None)
    def forbidden_call(*args, **kwargs):
        raise AssertionError("CN golden regression must stay read-only")

    monkeypatch.setattr(module, "run_analysis_report_workflow", forbidden_call)
    monkeypatch.setattr(module, "run_factcheck_workflow", forbidden_call)
    monkeypatch.setattr(module, "run_tracking_workflow", forbidden_call)
    monkeypatch.setattr(module, "_find_artifact_files", forbidden_call)

    record = module._run_market(None, "CN", timeout=1.0)

    assert record["passed"] is True
    assert record["pipeline"] == {
        "expected": "legacy_golden_readonly",
        "validated": True,
        "generated": False,
    }
    assert record["legacy_compatibility_checks"]["status"] == "passed"
    assert "quality_checks" not in record
    assert all(
        summary["pipeline_mode"] == "legacy_golden_readonly"
        and summary["generated"] is False
        and summary["formal_markers_absent"] is True
        for summary in record["workflows"].values()
    )
    assert record["workflows"]["analysis"]["markdown_present"] is True
    assert record["workflows"]["analysis"]["json_present"] is True
    assert record["workflows"]["tracking"]["manifest_latest_verified"] is True


def test_cn_market_rejects_formal_marker_in_golden_html_without_running_workflows(
    tmp_path,
    monkeypatch,
):
    module = _module()
    target = _smoke_target("CN")
    package = SimpleNamespace(company_dir=tmp_path)
    fact_surface = SimpleNamespace(digest="a" * 64, files=("facts.json",))
    _write_cn_golden_artifacts(
        tmp_path,
        module,
        analysis_html=(
            '<!doctype html><html data-schema="siq_analysis_report_v2"><body>报告</body></html>'
        ),
    )
    monkeypatch.setattr(module, "_select_target_from_universe", lambda market: target)
    monkeypatch.setattr(module, "resolve_report_package_from_context", lambda context, agent_type: package)
    monkeypatch.setattr(module, "snapshot_company_fact_surface", lambda company_dir: fact_surface)
    monkeypatch.setattr(module, "assert_fact_surface_unchanged", lambda before, after: None)
    def forbidden_call(*args, **kwargs):
        raise AssertionError("CN golden regression must stay read-only")

    monkeypatch.setattr(module, "run_analysis_report_workflow", forbidden_call)
    monkeypatch.setattr(module, "run_factcheck_workflow", forbidden_call)
    monkeypatch.setattr(module, "run_tracking_workflow", forbidden_call)

    record = module._run_market(None, "CN", timeout=1.0)

    assert record["passed"] is False
    assert record["error"] == {"stage": "pipeline", "code": "cn_pipeline_regression"}


@pytest.mark.parametrize("filename", ["../outside.html", "/tmp/outside.html"])
def test_cn_golden_artifact_resolution_rejects_paths_outside_company_subdir(
    tmp_path,
    filename,
):
    module = _module()
    (tmp_path / "analysis").mkdir()

    with pytest.raises(module.PipelineRegression) as exc_info:
        module._safe_company_artifact_path(tmp_path, "analysis", filename)

    assert exc_info.value.code == "cn_pipeline_regression"


def test_cn_golden_tracking_manifest_must_name_fixed_latest_report(tmp_path):
    module = _module()
    _write_cn_golden_artifacts(
        tmp_path,
        module,
        latest_report="../outside.html",
    )

    with pytest.raises(module.PipelineRegression) as exc_info:
        module._read_cn_golden_artifacts(tmp_path)

    assert exc_info.value.code == "cn_pipeline_regression"


def test_non_cn_market_fails_with_stable_code_if_service_returns_legacy_pipeline(tmp_path, monkeypatch):
    module = _module()
    target = _smoke_target("US")
    package = SimpleNamespace(company_dir=tmp_path)
    fact_surface = SimpleNamespace(digest="a" * 64, files=("facts.json",))
    monkeypatch.setattr(module, "_select_target_from_universe", lambda market: target)
    monkeypatch.setattr(module, "resolve_report_package_from_context", lambda context, agent_type: package)
    monkeypatch.setattr(module, "snapshot_company_fact_surface", lambda company_dir: fact_surface)
    monkeypatch.setattr(module, "assert_fact_surface_unchanged", lambda before, after: None)
    monkeypatch.setattr(
        module,
        "run_analysis_report_workflow",
        lambda request, timeout: SimpleNamespace(
            result={"ok": True, "stage": "completed", "files": {}}
        ),
    )

    record = module._run_market(None, "US", timeout=1.0)

    assert record["passed"] is False
    assert record["error"] == {"stage": "pipeline", "code": "non_cn_pipeline_regression"}


def test_market_smoke_fails_closed_with_stable_quality_code(tmp_path, monkeypatch):
    module = _module()
    target = {
        "company_key": "rk1_example",
        "company_wiki_id": "AAPL-Apple-Inc",
        "display_code": "AAPL",
        "display_name": "Apple Inc.",
        "research_identity": {
            "market": "US",
            "company_id": "US:0000320193",
            "filing_id": "US:0000320193:filing",
            "parse_run_id": "parse-run",
        },
        "source_report": {
            "report_id": "2025-10-K",
            "source_family": "sec_ixbrl",
            "fiscal_year": 2025,
        },
    }
    package = SimpleNamespace(company_dir=tmp_path)
    fact_surface = SimpleNamespace(digest="a" * 64, files=("facts.json",))
    monkeypatch.setattr(module, "_select_target_from_universe", lambda market: target)
    monkeypatch.setattr(module, "resolve_report_package_from_context", lambda context, agent_type: package)
    monkeypatch.setattr(module, "snapshot_company_fact_surface", lambda company_dir: fact_surface)
    monkeypatch.setattr(module, "assert_fact_surface_unchanged", lambda before, after: None)
    monkeypatch.setattr(
        module,
        "run_analysis_report_workflow",
        lambda request, timeout: SimpleNamespace(
            result={
                "ok": True,
                "artifact_id": "analysis-id",
                "pipeline_mode": "formal_analysis_input_bundle",
            }
        ),
    )
    downstream_results = {
        "factcheck": {
            "ok": True,
            "artifact": {
                "schema_version": "siq_agent_artifact_v2",
                "artifact_id": "factcheck-id",
            },
        },
        "tracking": {
            "ok": True,
            "artifact": {
                "schema_version": "siq_agent_artifact_v2",
                "artifact_id": "tracking-id",
            },
        },
    }
    monkeypatch.setattr(
        module,
        "run_factcheck_workflow",
        lambda request, timeout: SimpleNamespace(result=downstream_results["factcheck"]),
    )
    monkeypatch.setattr(
        module,
        "run_tracking_workflow",
        lambda request, timeout: SimpleNamespace(result=downstream_results["tracking"]),
    )

    def artifact_files(_package, artifact_type, artifact_id):
        upstream = [] if artifact_type == "analysis" else ["analysis-id"]
        return (
            {
                "artifact_id": artifact_id,
                "status": "completed",
                "source_report_id": "2025-10-K",
                "source_family": "sec_ixbrl",
                "adapter_version": "test-v1",
                "upstream_artifact_ids": upstream,
                "content_hash": "b" * 64,
                "quality": {"status": "pass", "warnings": []},
                "evidence_summary": {"citation_count": 1, "unresolved_count": 0},
                "metadata": {},
            },
            tmp_path / f"{artifact_id}.artifact.json",
            tmp_path / f"{artifact_id}.html",
        )

    monkeypatch.setattr(module, "_find_artifact_files", artifact_files)
    monkeypatch.setattr(
        module,
        "_run_content_quality_checks",
        lambda market, artifacts, skip_sentiment: {
            "status": "failed",
            "failure_codes": ["analysis_structured_claims_present"],
            "checks": [{"code": "analysis_structured_claims_present", "passed": False}],
        },
    )

    record = module._run_market(None, "US", timeout=1.0)

    assert record["passed"] is False
    assert record["error"] == {
        "stage": "quality",
        "code": "analysis_structured_claims_present",
    }
    assert record["quality_checks"]["status"] == "failed"


def test_release_summary_counts_five_formal_markets_and_one_cn_compatibility(
    tmp_path,
    monkeypatch,
):
    module = _module()

    def market_record(_seed_root, market, _timeout):
        record = {
            "market": market,
            "passed": True,
            "pipeline": {
                "expected": "legacy_golden_readonly" if market == "CN" else "formal_bundle_v2",
                "validated": True,
                "generated": market != "CN",
            },
            "fact_surface": {"unchanged": True},
        }
        if market == "CN":
            record["legacy_compatibility_checks"] = {"status": "passed"}
        else:
            record["quality_checks"] = {"status": "passed"}
        return record

    monkeypatch.setenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "1")
    monkeypatch.setenv("SIQ_US_SEC_ANALYSIS_ENABLED", "1")
    monkeypatch.setattr(module, "_run_market", market_record)

    payload = module.run(None, tmp_path / "smoke.json", timeout=1.0)

    assert payload["release_gate"] == "passed"
    assert payload["scope"]["formal_quality_gate_markets"] == ["HK", "US", "EU", "KR", "JP"]
    assert payload["scope"]["pipeline_strategy"] == {
        "CN": "legacy_golden_readonly",
        "overseas": "formal_bundle_v2",
    }
    assert payload["scope"]["cn_target_selection"] == "fixed_golden_000333"
    assert payload["summary"] == {
        "market_count": 6,
        "passed_market_count": 6,
        "formal_quality_market_count": 5,
        "quality_passed_market_count": 5,
        "legacy_compatibility_market_count": 1,
        "legacy_compatibility_passed_market_count": 1,
        "fact_surface_unchanged_count": 6,
    }
