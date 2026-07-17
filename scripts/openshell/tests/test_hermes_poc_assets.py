from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
POC = ROOT / "infra" / "openshell" / "poc" / "hermes-minimal"


def test_dockerfile_pins_compatible_arm64_runtime() -> None:
    text = (POC / "Dockerfile").read_text(encoding="utf-8")
    assert "python@sha256:baf89808ec37adeaab83cec287adb4a2afa4a11c1d51e961c7ec737877e61af6" in text
    assert "iproute2" in text
    assert "nftables" in text
    assert "ai.siq.poc-fixture-sha256" in text
    assert "useradd --uid 10001" in text
    assert "bookworm" not in text
    assert ":latest" not in text


def test_poc_config_is_loopback_only_and_has_no_fallback() -> None:
    config = yaml.safe_load((POC / "config.yaml").read_text(encoding="utf-8"))
    assert config["model"]["provider"] == "custom:siq-poc"
    assert config["model"]["base_url"] == "http://127.0.0.1:19000/v1"
    assert config["model"]["context_length"] == 65536
    assert config["fallback_providers"] == []
    assert config["platform_toolsets"]["api_server"] == ["terminal", "file", "code_execution", "no_mcp"]
    assert config["platforms"]["api_server"]["extra"] == {
        "host": "127.0.0.1",
        "port": 28642,
        "model_name": "siq-hermes-minimal-poc",
    }


def test_poc_policy_requires_non_root_landlock_and_no_egress() -> None:
    policy = yaml.safe_load((POC / "policy.yaml").read_text(encoding="utf-8"))
    assert policy["landlock"]["compatibility"] == "hard_requirement"
    assert policy["process"] == {"run_as_group": "sandbox", "run_as_user": "sandbox"}
    assert policy["network_policies"] == {}
    assert "/dev" not in policy["filesystem_policy"]["read_write"]
    assert "/dev/null" in policy["filesystem_policy"]["read_write"]
    assert "/dev/urandom" in policy["filesystem_policy"]["read_only"]
    assert "/opt/hermes-agent" in policy["filesystem_policy"]["read_only"]
    assert "/home/sandbox/.hermes" in policy["filesystem_policy"]["read_write"]


def test_prepare_script_pins_frozen_rollback_material() -> None:
    text = (ROOT / "scripts" / "openshell" / "prepare_hermes_poc.sh").read_text(encoding="utf-8")
    assert 'HERMES_COMMIT="ddb8d8fa842283ef651a6e4514f8f561f736c72e"' in text
    assert 'PATCH_SHA256="856d6e1820fe4f41669535a3e21c34a153e98318bcce90a607509c24d423d8c5"' in text
    assert "check_mount_safety.py" in text
    assert "hermes-agent/venv" not in text


def test_start_script_injects_only_explicit_poc_runtime_environment() -> None:
    text = (ROOT / "scripts" / "openshell" / "start_hermes_poc.sh").read_text(encoding="utf-8")
    assert '--env "HOME=/home/sandbox"' in text
    assert '--env "HERMES_HOME=/home/sandbox/.hermes"' in text
    assert '--env "API_SERVER_KEY=$api_key"' in text
    assert "/home/sandbox/.hermes/logs/entrypoint.log" in text


def test_poc_lifecycle_uses_nonce_identity_and_verified_process_termination() -> None:
    start = (ROOT / "scripts" / "openshell" / "start_hermes_poc.sh").read_text(encoding="utf-8")
    stop = (ROOT / "scripts" / "openshell" / "stop_hermes_poc.sh").read_text(encoding="utf-8")
    helper = (ROOT / "scripts" / "openshell" / "process_helpers.sh").read_text(encoding="utf-8")

    assert '--label "ai.siq.poc-run=$run_nonce"' in start
    assert "sandbox_create_attempted=1" in start
    assert "siq_openshell_verified_sandbox_container_id" in start
    assert "siq_openshell_verified_sandbox_container_id" in stop
    assert "openshell.ai/sandbox-id" in helper
    assert "openshell.ai/sandbox-name" in helper
    assert "openshell.ai/sandbox-namespace" in helper
    assert "find_forward_pids" in stop
    assert "siq_openshell_terminate_matching_pid" in start
    assert "siq_openshell_terminate_matching_pid" in stop
    assert "kill -TERM" in helper
    assert "did not stop cleanly" in helper


def test_model_stub_rejects_non_loopback_binding() -> None:
    text = (POC / "model_stub.py").read_text(encoding="utf-8")
    assert 'args.host not in {"127.0.0.1", "::1", "localhost"}' in text
    assert "SIQ_POC_TOOL_EXECUTED" in text
    assert "provider request contract mismatch" in text
    assert "ALLOWED_TOOL_NAMES" in text
