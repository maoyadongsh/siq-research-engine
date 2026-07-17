from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
POC = ROOT / "infra/openshell/poc/siq-analysis-observe"
SANDBOX = ROOT / "infra/openshell/sandbox"
SCRIPTS = ROOT / "scripts/openshell"


def _load_snapshot_module():
    path = SCRIPTS / "snapshot_observe_host_invariants.py"
    spec = importlib.util.spec_from_file_location("snapshot_observe_host_invariants_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_contract_module():
    path = SCRIPTS / "test_siq_analysis_observe_contract.py"
    spec = importlib.util.spec_from_file_location("test_siq_analysis_observe_contract_unit", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_observe_policy_has_no_host_business_mount_and_disposable_writes_only() -> None:
    policy = yaml.safe_load((POC / "policy.yaml").read_text(encoding="utf-8"))
    filesystem = policy["filesystem_policy"]

    assert policy["landlock"]["compatibility"] == "hard_requirement"
    assert policy["process"] == {"run_as_group": "sandbox", "run_as_user": "sandbox"}
    assert policy["network_policies"] == {
        "siq_observe_nemotron_fallback": {
            "name": "siq-observe-nemotron-fallback",
            "endpoints": [{"host": "host.openshell.internal", "port": 8007}],
            "binaries": [{"path": "/opt/siq/hermes/venv/bin/python"}],
        }
    }
    assert "/etc" not in filesystem["read_only"]
    assert "/etc/openshell" not in filesystem["read_only"]
    assert "/etc/ssl" in filesystem["read_only"]
    assert "/home/maoyd/siq-research-engine" in filesystem["read_only"]
    assert "/sandbox" in filesystem["read_write"]
    assert not any("data/wiki" in path for path in filesystem["read_write"])
    assert not any("data/hermes" in path for path in filesystem["read_write"])


def test_observe_entrypoint_seeds_a_disposable_runtime_without_weakening_formal_entrypoint() -> None:
    observe = (SANDBOX / "observe-entrypoint.sh").read_text(encoding="utf-8")
    formal = (SANDBOX / "entrypoint.sh").read_text(encoding="utf-8")

    assert "NOT_PRODUCTION" in observe
    assert 'EXPECTED_HOME="$OBSERVE_ROOT/hermes-home"' in observe
    assert 'cp -R "$PROFILE_SOURCE" "$EXPECTED_HOME"' in observe
    assert 'chmod -R u+rwX,go-rwx "$EXPECTED_HOME"' in observe
    assert 'exec "$HERMES_BIN" gateway run' in observe
    assert 'EXPECTED_HOME="$EXPECTED_PROJECT_ROOT/data/hermes/home/profiles/siq_analysis"' in formal
    assert "SIQ_OBSERVE_ONLY" not in formal


def test_observe_lifecycle_is_fixed_name_loopback_only_and_never_switches_host_runtime() -> None:
    start = (SCRIPTS / "start_siq_analysis_observe_poc.sh").read_text(encoding="utf-8")
    stop = (SCRIPTS / "stop_siq_analysis_observe_poc.sh").read_text(encoding="utf-8")

    assert "--acknowledge-not-production" in start
    assert 'SANDBOX_NAME="siq-analysis-observe-poc"' in start
    assert 'PORT="28651"' in start
    assert '--local "127.0.0.1:$PORT"' in start
    assert '--provider "$PROVIDER"' in start
    assert 'PROVIDER="siq-minimax-cn-pool"' in start
    assert "check_siq_services.py" not in start
    assert "stop_hermes_gateway.sh" not in start
    assert "18651" not in start
    assert "start_all.sh" not in start
    assert "siq_openshell_verified_sandbox_container_id" in start
    assert "siq_openshell_verified_sandbox_container_id" in stop
    assert "host Hermes and the isolated gateway were left running" in stop


def test_observe_contract_requires_real_tool_sse_stop_and_no_readiness_effect() -> None:
    contract = (SCRIPTS / "test_siq_analysis_observe_contract.py").read_text(encoding="utf-8")
    smoke = (SCRIPTS / "smoke_siq_analysis_observe_poc.sh").read_text(encoding="utf-8")

    for token in ("message.delta", "tool.started", "tool.completed", "run.completed", "run.cancelled"):
        assert token in contract
    assert "SIQ_OBSERVE_SUM=16" in contract
    assert '"readiness_effect": "none"' in contract
    assert '"mode": "NOT_PRODUCTION_OBSERVE_ONLY"' in contract
    assert "snapshot_observe_host_invariants.py" in smoke
    assert 'cmp -s -- "$before" "$after"' in smoke
    assert "Unexpected observe host mount" in smoke
    assert "Expected five read-only OpenShell control mounts" in smoke


def test_observe_tool_contract_accepts_one_terminal_invocation(monkeypatch) -> None:
    module = _load_contract_module()
    events = [
        {"event": "message.delta", "run_id": "run_observe"},
        {"event": "tool.started", "run_id": "run_observe", "tool": "terminal"},
        {"event": "tool.completed", "run_id": "run_observe", "tool": "terminal"},
        {
            "event": "run.completed",
            "run_id": "run_observe",
            "output": "SIQ_OBSERVE_SUM=16",
        },
    ]
    monkeypatch.setattr(module, "start_run", lambda _base_url, _prompt: "run_observe")
    monkeypatch.setattr(module, "collect_events", lambda _base_url, _run_id: events)
    monkeypatch.setattr(
        module,
        "wait_for_status",
        lambda _base_url, _run_id, _expected: {
            "status": "completed",
            "output": "SIQ_OBSERVE_SUM=16",
        },
    )

    result, observed = module._exercise_tool_run("http://127.0.0.1:28651")
    assert result["status"] == "completed"
    assert observed == events


def test_observe_tool_contract_rejects_extra_tool_invocations(monkeypatch) -> None:
    module = _load_contract_module()
    events = [
        {"event": "message.delta", "run_id": "run_observe"},
        {"event": "tool.started", "run_id": "run_observe", "tool": "terminal"},
        {"event": "tool.completed", "run_id": "run_observe", "tool": "terminal"},
        {"event": "tool.started", "run_id": "run_observe", "tool": "terminal"},
        {"event": "tool.completed", "run_id": "run_observe", "tool": "terminal"},
        {"event": "run.completed", "run_id": "run_observe"},
    ]
    monkeypatch.setattr(module, "start_run", lambda _base_url, _prompt: "run_observe")
    monkeypatch.setattr(module, "collect_events", lambda _base_url, _run_id: events)

    try:
        module._exercise_tool_run("http://127.0.0.1:28651")
    except AssertionError as exc:
        assert "exactly one tool invocation" in str(exc)
    else:
        raise AssertionError("extra tool invocation was accepted")


def test_host_invariant_snapshot_detects_static_and_immutable_changes(tmp_path: Path) -> None:
    module = _load_snapshot_module()
    root = tmp_path / "project"
    source = root / "agents/hermes/profiles/siq_analysis"
    host = root / "data/hermes/home/profiles/siq_analysis"
    immutable = root / "data/wiki/companies/acme/reports/2025"
    source.mkdir(parents=True)
    host.mkdir(parents=True)
    immutable.mkdir(parents=True)
    (source / "SOUL.md").write_text("prompt-v1\n", encoding="utf-8")
    (host / "SOUL.md").write_text("prompt-v1\n", encoding="utf-8")
    cache = source / "scripts/__pycache__"
    cache.mkdir(parents=True)
    (cache / "ignored.cpython-313.pyc").write_bytes(b"cache")
    (source / "auth.json").write_text("must-not-be-snapshotted", encoding="utf-8")
    (source / "state.db-wal").write_bytes(b"runtime")
    (immutable / "report.json").write_text('{"ok":true}\n', encoding="utf-8")
    registry = root / "var/openshell/registry/immutable-paths.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "path": "data/wiki/companies/acme/reports/2025",
                        "recursive": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    module.ROOT = root
    module.REGISTRY = registry
    module.PROFILE_SOURCE = source
    module.HOST_PROFILE = host
    before = module.snapshot()
    assert before == module.snapshot()

    (host / "SOUL.md").write_text("prompt-v2\n", encoding="utf-8")
    after_profile = module.snapshot()
    assert before["profile_static_content_sha256"] != after_profile["profile_static_content_sha256"]

    (host / "SOUL.md").write_text("prompt-v1\n", encoding="utf-8")
    (immutable / "new-file").write_text("changed\n", encoding="utf-8")
    after_immutable = module.snapshot()
    assert before["immutable_metadata_sha256"] != after_immutable["immutable_metadata_sha256"]


def test_observe_readme_is_explicitly_non_production_and_scopes_the_proof() -> None:
    readme = (POC / "README.md").read_text(encoding="utf-8")

    assert "NOT_PRODUCTION / OBSERVE-ONLY" in readme
    assert "must not\n> receive SIQ API traffic" in readme
    assert "does not alter `start_all.sh`" in readme
    assert "It does **not** prove report quality" in readme
    assert "stop_siq_analysis_observe_poc.sh" in readme
