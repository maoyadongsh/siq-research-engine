from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from scripts.openshell import (
    formal_fallback_drill_evidence as evidence_contract,
    run_siq_analysis_ab_eval as ab_eval,
    run_siq_analysis_fallback_drill as module,
)

ROOT = Path(__file__).resolve().parents[3]


def _observation(**overrides: Any) -> ab_eval.RunObservation:
    values: dict[str, Any] = {
        "status": "completed",
        "output": "FALLBACK_DRILL_OK",
        "create_contract_ok": True,
        "sse_contract_ok": True,
        "terminal_contract_ok": True,
        "fallback_activated": True,
        "requested_model": "MiniMax-M3",
        "configured_provider": "minimax-cn",
        "configured_model": "MiniMax-M3",
        "effective_provider": "custom:stepfun-step-3.7-flash",
        "effective_model": "step-3.7-flash",
        "policy_denied": False,
    }
    values.update(overrides)
    return ab_eval.RunObservation(**values)


def _evidence() -> dict[str, Any]:
    source = {
        "evidence_schema_sha256": evidence_contract.source_sha256(ROOT, evidence_contract.SCHEMA_RELATIVE),
        "runner_sha256": evidence_contract.source_sha256(ROOT, evidence_contract.RUNNER_RELATIVE),
        "validator_sha256": evidence_contract.source_sha256(ROOT, evidence_contract.VALIDATOR_RELATIVE),
        "lifecycle_sha256": evidence_contract.source_sha256(ROOT, evidence_contract.LIFECYCLE_RELATIVE),
        "evaluator_sha256": evidence_contract.source_sha256(ROOT, evidence_contract.EVALUATOR_RELATIVE),
    }
    return {
        "schema_version": evidence_contract.SCHEMA_VERSION,
        "generated_at": "2026-07-16T12:00:00Z",
        "decision": "PASS",
        "profile": "siq_analysis",
        "evaluation_id": "eval-20260716-a",
        "dataset_sha256": "1" * 64,
        "normal_summary_sha256": "2" * 64,
        "prerequisites_sha256": "3" * 64,
        "provenance_sha256": "4" * 64,
        "transaction": {
            "api_runtime_receipt_before_sha256": "3" * 64,
            "api_runtime_receipt_after_sha256": "3" * 64,
            "transaction_receipt_sha256": "5" * 64,
            "run_id_sha256": "6" * 64,
            "sandbox_id_sha256": "7" * 64,
            "container_id_sha256": "8" * 64,
            "host_receipt_before_sha256": "9" * 64,
            "host_receipt_after_sha256": "9" * 64,
            "gateway_receipt_before_sha256": "a" * 64,
            "gateway_receipt_after_sha256": "a" * 64,
            "image_id": "sha256:" + "b" * 64,
            "policy_sha256": "c" * 64,
            "mount_plan_sha256": "d" * 64,
            "mount_contract_sha256": "e" * 64,
            "runtime_config_sha256": "f" * 64,
            "fault_injection_sha256": "0" * 64,
        },
        "fault_injection": {
            "kind": "primary_http_503",
            "bind_scope": "verified_docker_bridge_gateway_only",
            "bind_port": 8004,
            "expected_status": 503,
            "target_url_sha256": "1" * 64,
            "stub_request_count": 3,
            "activated_for_sandbox_only": True,
            "credential_values_persisted": False,
            "request_headers_persisted": False,
            "request_body_persisted": False,
            "response_body_persisted": False,
        },
        "results": {
            "execution_count": 3,
            "completed_count": 3,
            "telemetry_count": 3,
            "fallback_activated_count": 3,
            "configured_provider": "minimax-cn",
            "configured_model": "MiniMax-M3",
            "effective_providers": ["custom:stepfun-step-3.7-flash"],
            "effective_models": ["step-3.7-flash"],
            "silent_failure_count": 0,
            "policy_denial_count": 0,
            "contract_failure_count": 0,
            "timeout_count": 0,
        },
        "cleanup": {
            "sandbox_removed": True,
            "container_removed": True,
            "forward_listener_removed": True,
            "stub_listener_removed": True,
            "temporary_secret_files_removed": True,
            "host_listener_identity_unchanged": True,
            "default_route_unchanged": True,
            "production_gateway_untouched": True,
            "residual_process_count": 0,
            "residual_listener_count": 0,
        },
        "provenance": {
            **source,
            "primary_provider": "minimax-cn",
            "primary_model": "MiniMax-M3",
            "fallback_route_sha256": "2" * 64,
        },
        "sanitization": {
            "contains_api_keys": False,
            "contains_headers": False,
            "contains_prompt_or_input": False,
            "contains_raw_output": False,
            "contains_local_paths": False,
            "exporter_ready": True,
        },
    }


def test_three_complete_fallback_observations_are_required() -> None:
    results = module.summarize_observations(
        [_observation(), _observation(), _observation()],
        primary_provider="minimax-cn",
        primary_model="MiniMax-M3",
    )

    assert results["completed_count"] == 3
    assert results["fallback_activated_count"] == 3
    assert results["effective_providers"] == ["custom:stepfun-step-3.7-flash"]

    with pytest.raises(module.FallbackDrillError, match="fallback_drill_observations_failed"):
        module.summarize_observations(
            [_observation(), _observation(fallback_activated=False), _observation()],
            primary_provider="minimax-cn",
            primary_model="MiniMax-M3",
        )


def test_fallback_consumer_requires_current_ab_contract_versions() -> None:
    assert module.ab_eval.SUMMARY_SCHEMA_VERSION == "siq.openshell.siq-analysis-ab-summary.v3"
    assert module.ab_prerequisites.SCHEMA_VERSION == "siq.openshell.siq-analysis-ab-prerequisites.v3"
    assert module.PROVENANCE_SCHEMA == "siq.openshell.siq-analysis-ab-provenance.v3"


def test_terminal_resource_shape_retains_only_run_directory() -> None:
    resources = {
        name: {
            "kind": kind,
            "disposition": "retain" if name == "run_dir" else "remove",
            "state": "present" if name == "run_dir" else "removed",
        }
        for name, kind in module.transaction.FORMAL_RESOURCES.items()
    }

    assert module._terminal_resources_valid(resources) is True
    resources["run_dir"]["state"] = "removed"
    assert module._terminal_resources_valid(resources) is False
    resources["run_dir"]["state"] = "present"
    resources["sandbox"]["state"] = "present"
    assert module._terminal_resources_valid(resources) is False


def test_503_stub_counts_only_bounded_approved_post_without_persisting_content() -> None:
    state = module.StubState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), module._stub_handler(state))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/v1/messages",
            data=b'{"secret":"must-not-persist"}',
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": "Bearer must-not-persist"},
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=2)
        assert error.value.code == 503
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert state.request_count == 1
    assert state.invalid_request_count == 0
    assert set(vars(state)) == {"request_count", "invalid_request_count", "lock"}
    assert "must-not-persist" not in json.dumps({"request_count": state.request_count})


def test_fallback_evidence_schema_semantics_and_normal_bindings() -> None:
    evidence = _evidence()
    evidence_contract.validate_evidence(evidence)
    summary = {"evaluation_id": "eval-20260716-a", "dataset_sha256": "1" * 64}
    provenance = {
        "dataset_sha256": "1" * 64,
        "arms": {
            "openshell": {
                "image_id": "sha256:" + "b" * 64,
                "policy_sha256": "c" * 64,
                "mount_contract_sha256": "e" * 64,
                "runtime_config_sha256": "f" * 64,
            }
        },
        "runtime_attestation": {
            "fallback_route_sha256": "2" * 64,
            "primary_provider": "minimax-cn",
            "primary_model": "MiniMax-M3",
        },
    }
    evidence_contract.validate_bindings(
        evidence,
        root=ROOT,
        normal_summary=summary,
        normal_summary_sha256="2" * 64,
        prerequisites_sha256="3" * 64,
        provenance_report=provenance,
        provenance_sha256="4" * 64,
    )

    tampered = json.loads(json.dumps(evidence))
    tampered["results"]["effective_providers"] = ["minimax-cn"]
    with pytest.raises(evidence_contract.FallbackEvidenceError, match="fallback_evidence_semantics_invalid"):
        evidence_contract.validate_evidence(tampered)


def test_cli_requires_explicit_live_confirmation_before_reading_inputs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = module.main(
        [
            "--project-root",
            str(tmp_path),
            "--evaluation-id",
            "eval-20260716-a",
            "--company",
            "600104-company",
            "--dataset",
            str(tmp_path / "missing-dataset.json"),
            "--normal-summary",
            str(tmp_path / "missing-summary.json"),
            "--prerequisites",
            str(tmp_path / "missing-prerequisites.json"),
            "--provenance",
            str(tmp_path / "missing-provenance.json"),
        ]
    )

    assert result == 2
    assert "fallback_live_drill_not_confirmed" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []
