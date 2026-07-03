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
