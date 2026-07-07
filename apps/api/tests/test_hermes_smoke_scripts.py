import json
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SIQ_ANALYSIS_SCRIPT_DIR = PROJECT_ROOT / "agents" / "hermes" / "profiles" / "siq_analysis" / "scripts"
RUN_RESEARCH_SUBAGENTS_SCRIPT = SIQ_ANALYSIS_SCRIPT_DIR / "run_research_subagents.py"
VALIDATE_RESEARCH_PACKS_SCRIPT = SIQ_ANALYSIS_SCRIPT_DIR / "validate_research_packs.py"
MERGE_RESEARCH_PACKS_SCRIPT = SIQ_ANALYSIS_SCRIPT_DIR / "merge_research_packs.py"
RUN_ANALYSIS_REPORT_SCRIPT = SIQ_ANALYSIS_SCRIPT_DIR / "run_analysis_report.py"
SMOKE_SPEC = importlib.util.spec_from_file_location(
    "smoke_r1_agent_workflow",
    PROJECT_ROOT / "scripts" / "hermes" / "smoke_r1_agent_workflow.py",
)
assert SMOKE_SPEC and SMOKE_SPEC.loader
smoke_r1_agent_workflow = importlib.util.module_from_spec(SMOKE_SPEC)
SMOKE_SPEC.loader.exec_module(smoke_r1_agent_workflow)


SECTION_IDS = [
    "executive_summary",
    "key_changes",
    "operating_quality",
    "profitability_and_cost",
    "asset_quality_working_capital",
    "debt_liquidity",
    "cash_flow_quality",
    "industry_competition",
    "strategy_policy_external_risk",
    "governance_compliance_shareholders",
    "valuation_expectation_gap",
    "risk_chain_scenario",
    "tracking_checklist",
    "data_quality_traceability",
]


def _load_script_module(script_path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec and spec.loader
    original_sys_path = sys.path.copy()
    sys.path.insert(0, str(script_path.parent))
    try:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = original_sys_path
    return module


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _run_json(args: list[str]) -> dict:
    result = subprocess.run(args, cwd=PROJECT_ROOT, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def _write_research_pack_workdir(work_dir: Path) -> None:
    sections = [
        {
            "section_id": section_id,
            "title": section_id,
            "facts": ["baseline fact"],
            "calculations": [],
            "judgements": ["baseline judgement"],
            "risks_or_improvement_conditions": ["baseline risk -> financial impact"],
            "evidence_ids": ["metric:operating_revenue:2025"],
            "narrative_blocks": [
                {"title": "核心诊断", "role": "diagnosis", "items": ["营业收入改善但现金流仍需验证。"]}
            ],
        }
        for section_id in SECTION_IDS
    ]
    _write_json(work_dir / "section_drafts.json", {"sections": sections, "quality_report": {"review_queue": []}})
    _write_json(
        work_dir / "preflight.json",
        {"company_id": "test-co", "industry_sw1": "制造业", "industry_sw2": "通用设备", "industry_sw3": "设备制造"},
    )
    _write_json(work_dir / "wiki_inventory.json", {"company_id": "test-co", "file_count": 8, "missing_required_files": []})
    _write_json(
        work_dir / "metric_snapshot.json",
        {
            "company_id": "test-co",
            "metrics": {
                "operating_revenue": {
                    "display_name": "营业收入",
                    "unit": "亿元",
                    "values": {"2025": 120.0, "2024": 100.0},
                    "sources": {"2025": {"file": "metrics/key_metrics.json", "pdf_page": 10, "table_index": 1}},
                },
                "parent_net_profit": {"display_name": "归母净利润", "unit": "亿元", "values": {"2025": 8.0}},
                "operating_cash_flow_net": {"display_name": "经营现金流", "unit": "亿元", "values": {"2025": 5.0}},
                "total_assets": {"display_name": "总资产", "unit": "亿元", "values": {"2025": 300.0}},
                "total_liabilities": {"display_name": "总负债", "unit": "亿元", "values": {"2025": 150.0}},
                "equity_attributable_parent": {"display_name": "归母权益", "unit": "亿元", "values": {"2025": 130.0}},
            },
            "missing_core_metrics": [],
        },
    )
    _write_json(work_dir / "evidence_package.json", {"company_id": "test-co"})
    _write_json(
        work_dir / "analysis_outline.json",
        {
            "company_id": "test-co",
            "core_judgment": "营业收入改善但经营现金流覆盖仍需验证。",
            "core_contradiction": "利润改善与现金流质量之间仍需交叉验证。",
            "red_flags": ["现金流覆盖不足"],
            "improvement_items": ["毛利率改善"],
            "falsifying_evidence": ["应收继续扩大"],
        },
    )
    _write_json(
        work_dir / "peer_metrics.json",
        {
            "company_id": "test-co",
            "strict_ok": False,
            "peer_count": 0,
            "selection_method": "same_industry_sw3",
            "interpretation": [],
            "warnings": ["same_industry_sample_below_minimum"],
        },
    )
    _write_json(
        work_dir / "qualitative_snapshot.json",
        {
            "company_id": "test-co",
            "strict_ok": True,
            "buckets": {
                "strategy": [{"text": "公司推进产品升级并改善渠道效率。", "evidence_ids": ["q1"]}],
                "governance": [{"text": "治理披露未发现明显异常。", "evidence_ids": ["q2"]}],
            },
        },
    )
    _write_json(work_dir / "market_snapshot.json", {"company_id": "test-co", "strict_ok": False})
    _write_json(
        work_dir / "industry_research.json",
        {"company_id": "test-co", "strict_ok": False, "results": [], "warnings": ["external_sources_missing"]},
    )


def test_start_gateway_refuses_listening_port_without_health(monkeypatch):
    monkeypatch.setattr(smoke_r1_agent_workflow, "gateway_health", lambda host, port: None)
    monkeypatch.setattr(smoke_r1_agent_workflow, "is_tcp_port_open", lambda host, port: True)

    with pytest.raises(RuntimeError, match="already listening"):
        smoke_r1_agent_workflow.start_gateway("siq_ic_strategist", "127.0.0.1", 18662, 1)


def test_write_smoke_env_file_aligns_client_and_gateway_tokens(tmp_path):
    env_file = smoke_r1_agent_workflow.write_smoke_env_file(tmp_path, token="token-123")

    assert env_file.read_text(encoding="utf-8") == (
        "HERMES_API_KEY=token-123\n"
        "HERMES_TOKEN=token-123\n"
        "API_SERVER_KEY=token-123\n"
    )


def test_siq_analysis_script_command_redaction_hides_prompt_values():
    report_runner = _load_script_module(RUN_ANALYSIS_REPORT_SCRIPT, "siq_analysis_report_runner_for_redaction_test")
    research_runner = _load_script_module(
        RUN_RESEARCH_SUBAGENTS_SCRIPT,
        "siq_analysis_research_runner_for_redaction_test",
    )
    cmd = [
        sys.executable,
        "runner.py",
        "--research-subagent-prompt",
        "private task prompt",
        "--research-prompt=private downstream prompt",
        "--research-benchmark-hint",
        "private benchmark",
        "--benchmark-hint=private downstream benchmark",
        "--work-dir",
        "/tmp/work",
    ]

    for runner in (report_runner, research_runner):
        redacted = runner.redact_cmd(cmd)
        assert "private task prompt" not in redacted
        assert "--research-prompt=private downstream prompt" not in redacted
        assert "private benchmark" not in redacted
        assert "--benchmark-hint=private downstream benchmark" not in redacted
        assert redacted[redacted.index("--research-subagent-prompt") + 1] == "<redacted>"
        assert "--research-prompt=<redacted>" in redacted
        assert redacted[redacted.index("--research-benchmark-hint") + 1] == "<redacted>"
        assert "--benchmark-hint=<redacted>" in redacted
        assert "/tmp/work" in redacted


def test_prior_r1_agents_respects_fixed_sequence():
    assert smoke_r1_agent_workflow.prior_r1_agents("siq_ic_finance_auditor") == [
        "siq_ic_strategist",
        "siq_ic_sector_expert",
    ]
    assert smoke_r1_agent_workflow.prior_r1_agents("siq_ic_strategist") == []


def test_build_smoke_package_satisfies_evidence_gate_for_default_dry_run(tmp_path):
    smoke_r1_agent_workflow.build_smoke_package(tmp_path, "siq_ic_strategist")

    dry_run = smoke_r1_agent_workflow.ic_agent_runtime.build_workflow_r1_agent_run_dry_run(
        smoke_r1_agent_workflow.DEAL_ID,
        "siq_ic_strategist",
        wiki_root=tmp_path,
    )

    assert dry_run["allowed"] is True
    assert dry_run["blocking_reasons"] == []
    assert dry_run["preflight_status"] == "warn"
    assert "preflight:evidence.gate:warn" not in dry_run["warnings"]


def test_build_smoke_package_seed_prior_reports_allows_later_sequence_profile(tmp_path):
    package_dir = smoke_r1_agent_workflow.build_smoke_package(
        tmp_path,
        "siq_ic_legal_scanner",
        seed_prior_reports=True,
    )

    dry_run = smoke_r1_agent_workflow.ic_agent_runtime.build_workflow_r1_agent_run_dry_run(
        smoke_r1_agent_workflow.DEAL_ID,
        "siq_ic_legal_scanner",
        wiki_root=tmp_path,
    )

    assert dry_run["allowed"] is True
    assert dry_run["blocking_reasons"] == []
    workflow = smoke_r1_agent_workflow.deal_store.read_json(
        package_dir / "phases" / "workflow_state.json",
        {},
    )
    assert workflow["phases"]["R1"]["submitted_agents"] == [
        "siq_ic_strategist",
        "siq_ic_sector_expert",
        "siq_ic_finance_auditor",
    ]


def test_r1_profile_matrix_covers_all_sequence_profiles(monkeypatch, tmp_path):
    roots = iter(tmp_path / profile_id for profile_id in smoke_r1_agent_workflow.ic_policy.R1_AGENT_SEQUENCE)
    monkeypatch.setattr(
        smoke_r1_agent_workflow.tempfile,
        "mkdtemp",
        lambda prefix: str(next(roots)),
    )

    summary = smoke_r1_agent_workflow.run_r1_profile_matrix()

    assert summary["schema_version"] == "siq_ic_r1_smoke_matrix_v1"
    assert summary["allowed_count"] == len(smoke_r1_agent_workflow.ic_policy.R1_AGENT_SEQUENCE)
    assert summary["blocked_count"] == 0
    assert [item["agent_id"] for item in summary["profiles"]] == list(
        smoke_r1_agent_workflow.ic_policy.R1_AGENT_SEQUENCE
    )
    assert all(item["blocking_reasons"] == [] for item in summary["profiles"])


def test_serial_dry_run_smoke_plans_full_r1_sequence(monkeypatch, tmp_path):
    monkeypatch.setattr(
        smoke_r1_agent_workflow.tempfile,
        "mkdtemp",
        lambda prefix: str(tmp_path / "serial"),
    )

    dry_run = smoke_r1_agent_workflow.run_serial_dry_run_smoke()

    assert dry_run["schema_version"] == "siq_ic_workflow_r1_serial_run_dry_run_v1"
    assert dry_run["allowed"] is True
    assert dry_run["planned_agent_ids"] == list(smoke_r1_agent_workflow.ic_policy.R1_AGENT_SEQUENCE)
    assert dry_run["planned_count"] == len(smoke_r1_agent_workflow.ic_policy.R1_AGENT_SEQUENCE)
    assert dry_run["blocking_reasons"] == []
    assert [item["action"] for item in dry_run["agents"]] == ["would_run"] * len(
        smoke_r1_agent_workflow.ic_policy.R1_AGENT_SEQUENCE
    )


def test_siq_analysis_research_pack_runner_validates_and_merges_minimal_workdir(tmp_path):
    work_dir = tmp_path / "analysis" / ".work" / "test-report"
    _write_research_pack_workdir(work_dir)
    prompt_file = tmp_path / "benchmark_prompt.md"
    prompt_file.write_text("请补充韩国新能源汽车供应链标杆，但不要混入 A 股同业分位。", encoding="utf-8")

    deterministic = _run_json([
        sys.executable,
        str(RUN_RESEARCH_SUBAGENTS_SCRIPT),
        "--work-dir",
        str(work_dir),
        "--year",
        "2025",
        "--mode",
        "deterministic",
        "--compact",
    ])
    validation = _run_json([sys.executable, str(VALIDATE_RESEARCH_PACKS_SCRIPT), str(work_dir), "--compact"])
    merged = _run_json([
        sys.executable,
        str(MERGE_RESEARCH_PACKS_SCRIPT),
        "--work-dir",
        str(work_dir),
        "--section-drafts",
        str(work_dir / "section_drafts.json"),
    ])
    prompt_only = _run_json([
        sys.executable,
        str(RUN_RESEARCH_SUBAGENTS_SCRIPT),
        "--work-dir",
        str(work_dir),
        "--year",
        "2025",
        "--mode",
        "prompt-only",
        "--research-prompt",
        "请检索日本汽车标杆作为 cross_market_reference。",
        "--research-prompt-file",
        str(prompt_file),
        "--benchmark-hint",
        "日本汽车标杆",
        "--benchmark-hint",
        "韩国新能源汽车供应链",
        "--compact",
    ])

    assert deterministic["ok"] is True
    assert deterministic["pack_sources"]["industry_peer_researcher"] == "deterministic"
    assert deterministic["metrics"]["pack_count"] == 5
    assert deterministic["metrics"]["pack_source_counts"]["deterministic"] == 5
    assert deterministic["metrics"]["validation_ok"] is True
    assert validation["ok"] is True
    assert validation["metrics"]["pack_count"] == 5
    assert merged["ok"] is True
    assert len(merged["manifest"]["changed_sections"]) == 14
    assert prompt_only["ok"] is True
    assert prompt_only["stage"] == "prompt_bundle_ready"
    assert prompt_only["started_at"]
    assert prompt_only["completed_at"]
    assert prompt_only["elapsed_ms"] >= 0
    assert prompt_only["metrics"]["prompt_agent_count"] == 6
    assert prompt_only["metrics"]["benchmark_hint_count"] == 2
    benchmark_context = prompt_only["benchmark_research_context"]
    assert benchmark_context["mode"] == "prompt_driven_query"
    assert "日本汽车标杆" in benchmark_context["research_prompt"]
    assert "韩国新能源汽车供应链" in benchmark_context["research_prompt"]
    assert prompt_only["metrics"]["research_prompt_chars"] == len(benchmark_context["research_prompt"])
    assert benchmark_context["benchmark_hints"] == ["日本汽车标杆", "韩国新能源汽车供应链"]
    assert {root["market"] for root in benchmark_context["search_roots"]} >= {"A", "JP", "KR", "downloads"}
    assert any("不得在脚本层硬编码" in item for item in benchmark_context["query_policy"])
    assert any("不得把海外标杆" in item for item in benchmark_context["output_policy"])
    prompt_bundle = json.loads((work_dir / "research_subagent_prompts.json").read_text(encoding="utf-8"))
    industry_agent = next(
        agent for agent in prompt_bundle["agents"] if agent["agent_id"] == "industry_peer_researcher"
    )
    assert industry_agent["benchmark_research_context"] == benchmark_context


def test_siq_analysis_research_pack_runner_rejects_missing_prompt_file(tmp_path):
    work_dir = tmp_path / "analysis" / ".work" / "test-report"
    _write_research_pack_workdir(work_dir)

    result = subprocess.run(
        [
            sys.executable,
            str(RUN_RESEARCH_SUBAGENTS_SCRIPT),
            "--work-dir",
            str(work_dir),
            "--year",
            "2025",
            "--mode",
            "prompt-only",
            "--research-prompt-file",
            str(tmp_path / "missing.md"),
            "--compact",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "research prompt file unreadable" in result.stderr


def test_siq_analysis_report_runner_uses_research_subagent_runner_for_modes():
    source = RUN_ANALYSIS_REPORT_SCRIPT.read_text(encoding="utf-8")

    assert "str(RUN_RESEARCH_SUBAGENTS_SCRIPT)" in source
    assert "args.research_subagent_mode" in source
    assert '"--external-pack-dir"' in source
    assert "args.no_research_subagent_fallback" in source
    assert "args.research_subagent_prompt" in source
    assert '"--research-prompt"' in source
    assert "args.research_subagent_prompt_file" in source
    assert '"--research-prompt-file"' in source
    assert "args.research_benchmark_hint" in source
    assert '"--benchmark-hint"' in source
    assert "GENERATE_RESEARCH_PACKS_SCRIPT" not in source
