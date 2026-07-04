import importlib.util
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SMOKE_SPEC = importlib.util.spec_from_file_location(
    "smoke_r1_agent_workflow",
    PROJECT_ROOT / "scripts" / "hermes" / "smoke_r1_agent_workflow.py",
)
assert SMOKE_SPEC and SMOKE_SPEC.loader
smoke_r1_agent_workflow = importlib.util.module_from_spec(SMOKE_SPEC)
SMOKE_SPEC.loader.exec_module(smoke_r1_agent_workflow)


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
