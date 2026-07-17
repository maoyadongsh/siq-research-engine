from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from scripts.openshell import check_v06_completion as module

ROOT = Path(__file__).resolve().parents[3]


def _write_json(path: Path, payload: object, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n", encoding="utf-8")
    if mode is not None:
        path.chmod(mode)


def _service_go() -> dict[str, object]:
    services = []
    for service_id, (port, requirement) in module.SERVICE_SPECS.items():
        expected_protocol = module.SERVICE_PROTOCOL_SPECS.get(service_id)
        services.append(
            {
                "service_id": service_id,
                "port": port,
                "requirement": requirement,
                "blocking": requirement == "required",
                "reachable": True,
                "status": "pass",
                "error_code": "",
                "protocol_check": {
                    "contract": expected_protocol[0] if expected_protocol else "not_applicable",
                    "method": "GET" if expected_protocol else "",
                    "path": expected_protocol[1] if expected_protocol else "",
                    "checked": bool(expected_protocol),
                    "available": True if expected_protocol else None,
                    "status": "pass" if expected_protocol else "not_applicable",
                    "error_code": "",
                    "latency_ms": 1 if expected_protocol else None,
                    "http_status": 200 if expected_protocol else None,
                },
            }
        )
    checks = [
        {
            "check_id": check_id,
            "status": "pass",
            "proof_present": True,
            "proof_source": "proof_file",
            "error_code": "",
        }
        for check_id in sorted(module.SERVICE_SECURITY_CHECKS)
    ]
    return {
        "schema_version": module.SERVICE_SCHEMA,
        "decision": "GO",
        "passed": True,
        "probe_scope": {
            "protocol": module.SERVICE_PROTOCOL,
            "read_only": True,
            "host_alias_kind": "loopback",
            "http_method": "GET",
            "request_body_sent": False,
            "redirects_followed": False,
            "response_body_recorded": False,
        },
        "services": services,
        "security_checks": checks,
        "blockers": [],
        "summary": {
            "required_total": 5,
            "required_reachable": 5,
            "optional_total": 3,
            "optional_reachable": 3,
            "required_protocol_total": 3,
            "required_protocol_available": 3,
            "optional_protocol_total": 3,
            "optional_protocol_available": 3,
            "security_proofs_required": 2,
            "security_proofs_present": 2,
            "blocking_count": 0,
            "warning_count": 0,
        },
    }


def _arm(execution_count: int) -> dict[str, object]:
    return {
        "execution_count": execution_count,
        "task_success_rate": 1.0,
        "answer_citation_rate": 1.0,
        "numeric_accuracy": 1.0,
        "hallucination_block_rate": 1.0,
        "evidence_coverage": 1.0,
        "tool_success_rate": 1.0,
        "tool_error_rate": 0.0,
        "tool_retry_rate": 0.0,
        "tool_recovery_rate": 1.0,
        "tool_unrecovered_failure_rate": 0.0,
        "fallback_success_rate": None,
        "fallback_telemetry_coverage": None,
        "fallback_expected_execution_count": 0,
        "fallback_telemetry_expected_count": 0,
        "report_completeness": 1.0,
        "timeout_rate": 0.0,
        "policy_false_positive_rate": 0.0,
        "sample_counts": {
            "answer_citation_rate": execution_count,
            "numeric_accuracy": execution_count,
            "hallucination_block_rate": execution_count,
            "evidence_coverage": execution_count,
            "tool_success_rate": execution_count,
            "report_completeness": execution_count,
            "policy_false_positive_rate": execution_count,
        },
        "contract_failure_count": 0,
        "unexpected_fallback_count": 0,
        "tool_runtime": {
            "attempt_count": execution_count,
            "success_count": execution_count,
            "failure_count": 0,
            "retry_count": 0,
            "failed_tool_state_count": 0,
            "recovered_tool_state_count": 0,
            "unrecovered_tool_state_count": 0,
        },
        "runtime_telemetry": {
            "expected_primary_provider": "primary-provider",
            "expected_primary_model": "primary-model",
            "telemetry_count": execution_count,
            "requested_model_match_count": execution_count,
            "configured_route_match_count": execution_count,
            "effective_route_match_count": execution_count,
            "fallback_inactive_count": execution_count,
            "configured_routes": [
                {"provider": "primary-provider", "model": "primary-model", "count": execution_count}
            ],
            "effective_routes": [
                {"provider": "primary-provider", "model": "primary-model", "count": execution_count}
            ],
        },
        "latency_ms": {
            "ttft_sample_count": execution_count,
            "ttft_p50": 10.0,
            "ttft_p95": 20.0,
            "total_sample_count": execution_count,
            "total_p50": 100.0,
            "total_p95": 150.0,
        },
    }


def test_service_transport_and_protocol_failures_are_valid_no_go_evidence() -> None:
    transport = _service_go()
    services = transport["services"]
    assert isinstance(services, list)
    embedding = next(item for item in services if item["service_id"] == "embedding")
    embedding.update({"reachable": False, "status": "no_go", "error_code": "connection_refused"})
    embedding["protocol_check"].update(
        {
            "checked": False,
            "available": False,
            "status": "not_run",
            "error_code": "transport_unreachable",
            "latency_ms": 0,
            "http_status": None,
        }
    )
    transport.update(
        {
            "decision": "NO_GO",
            "passed": False,
            "blockers": [
                {
                    "check_id": "service:embedding",
                    "kind": "service_connectivity",
                    "error_code": "embedding_service_unreachable",
                    "port": 8013,
                }
            ],
        }
    )
    transport["summary"].update({"required_reachable": 4, "required_protocol_available": 2, "blocking_count": 1})
    assert module._validate_service_report(transport) == (True, False)

    protocol = _service_go()
    services = protocol["services"]
    assert isinstance(services, list)
    embedding = next(item for item in services if item["service_id"] == "embedding")
    embedding.update({"status": "no_go", "error_code": "embedding_service_protocol_unavailable"})
    embedding["protocol_check"].update({"available": False, "status": "no_go", "error_code": "response_contract_invalid"})
    protocol.update(
        {
            "decision": "NO_GO",
            "passed": False,
            "blockers": [
                {
                    "check_id": "service:embedding",
                    "kind": "service_protocol",
                    "error_code": "embedding_service_protocol_unavailable",
                    "port": 8013,
                }
            ],
        }
    )
    protocol["summary"].update({"required_protocol_available": 2, "blocking_count": 1})
    assert module._validate_service_report(protocol) == (True, False)

    embedding["status"] = "pass"
    assert module._validate_service_report(protocol) == (False, False)


def test_git_index_evidence_binding_rejects_worktree_drift_and_private_paths(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    public = root / "artifacts/openshell/v0.6/evidence.sanitized.json"
    private = root / "var/openshell/proofs/evidence.json"
    public.parent.mkdir(parents=True)
    private.parent.mkdir(parents=True)
    public.write_text('{"decision":"GO"}\n', encoding="utf-8")
    private.write_text('{"decision":"GO"}\n', encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "add", public.relative_to(root).as_posix()], check=True)

    original_digest = hashlib.sha256(public.read_bytes()).hexdigest()
    assert module._git_index_evidence_matches(root, public, original_digest) is True
    assert (
        module._git_index_evidence_matches(
            root,
            private,
            hashlib.sha256(private.read_bytes()).hexdigest(),
        )
        is False
    )

    public.write_text('{"decision":"NO_GO"}\n', encoding="utf-8")
    drifted_digest = hashlib.sha256(public.read_bytes()).hexdigest()
    assert module._git_index_evidence_matches(root, public, drifted_digest) is False


def _ab_summary(
    *, evaluation_id: str = "eval-live-20260716", case_count: int = 10, repetitions: int = 3
) -> dict[str, object]:
    arm = _arm(case_count * repetitions)
    host = json.loads(json.dumps(arm))
    openshell = json.loads(json.dumps(arm))
    comparison, reasons = module.ab_eval.quality_comparison(
        host,
        openshell,
        case_count=case_count,
        repetitions=repetitions,
        require_fallback=False,
    )
    return {
        "schema_version": module.AB_SUMMARY_SCHEMA,
        "evaluation_id": evaluation_id,
        "prerequisites_path": f"var/openshell/eval/{evaluation_id}/prerequisites.json",
        "prerequisites_sha256": "0" * 64,
        "dataset_sha256": "a" * 64,
        "dataset_schema_version": module.ab_eval.DATASET_SCHEMA_VERSION,
        "profile": "siq_analysis",
        "model": "pinned-model-alias",
        "temperature": 0.1,
        "case_count": case_count,
        "repetitions": repetitions,
        "execution_count": case_count * repetitions * 2,
        "interleaving": "alternating_case_and_repetition",
        "arms": {"host": host, "openshell": openshell},
        "comparison": comparison,
        "quality_gate": {
            "passed": not reasons,
            "failure_reasons": reasons,
            "cutover_performed": False,
            "recommendation": "manual_review_only_no_automatic_cutover",
        },
        "sanitization": {
            "contains_api_keys": False,
            "contains_headers": False,
            "contains_prompt_or_input": False,
            "contains_raw_output": False,
            "t8_exporter_ready": True,
        },
    }


def _ab_prerequisites(summary: dict[str, object]) -> dict[str, object]:
    generated_at = datetime.now(timezone.utc).replace(microsecond=0)
    binding_generated_at = generated_at - timedelta(seconds=1)

    def binding(name: str, *, index: int, lifetime_seconds: int, size_bytes: int) -> dict[str, object]:
        return {
            "path": f"/evidence/{name}.json",
            "sha256": f"{index}" * 64,
            "size_bytes": size_bytes,
            "device": 1,
            "inode": index,
            "mode": 0o600,
            "mtime_ns": index,
            "ctime_ns": index,
            "generated_at": binding_generated_at.isoformat().replace("+00:00", "Z"),
            "expires_at": (binding_generated_at + timedelta(seconds=lifetime_seconds))
            .isoformat()
            .replace("+00:00", "Z"),
        }

    evidence = {
        "provider_inventory": binding("provider-inventory", index=1, lifetime_seconds=900, size_bytes=1024),
        "service_report": binding("service-report", index=2, lifetime_seconds=300, size_bytes=4096),
        "broker_report": binding("broker-report", index=3, lifetime_seconds=60, size_bytes=2048),
    }
    return {
        "schema_version": module.AB_PREREQUISITE_SCHEMA,
        "decision": "GO",
        "profile": "siq_analysis",
        "evaluation_id": summary["evaluation_id"],
        "host": {
            "scheme": "http",
            "port": 18651,
            "path": "/v1/runs",
            "normalized": "http://127.0.0.1:18651/v1/runs",
            "analysis_port": 18651,
        },
        "openshell": {
            "scheme": "http",
            "port": 28651,
            "path": "/v1/runs",
            "normalized": "http://127.0.0.1:28651/v1/runs",
            "expected_port": 28651,
        },
        "dataset": {
            "schema_version": module.ab_eval.DATASET_SCHEMA_VERSION,
            "sha256": summary["dataset_sha256"],
            "case_count": summary["case_count"],
            "repetitions": summary["repetitions"],
            "normal_case_count": 10,
            "fallback_case_count": 0,
        },
        "provenance": {
            "schema_version": module.AB_PROVENANCE_SCHEMA,
            "sha256": "b" * 64,
            "hermes_commit": module.HERMES_COMMIT,
            "host_runtime_verified": True,
            "host_runtime_receipt_sha256": "e" * 64,
            "runtime_contract_sha256": "f" * 64,
            "host_candidate_source_match": True,
            "arms_match": True,
        },
        "evaluation_id_valid": True,
        "key_fingerprints": {"host": "c" * 64, "openshell": "d" * 64},
        "evidence": evidence,
        "provider_count": len(module.ab_eval.PROVIDERS),
        "missing_provider_count": 0,
        "service_preflight_decision": "GO",
        "blockers": [],
        "network_probe_performed": True,
        "cutover_performed": False,
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "expires_at": evidence["broker_report"]["expires_at"],
    }


def _formal_sanitization() -> dict[str, object]:
    return {
        "contains_api_keys": False,
        "contains_headers": False,
        "contains_prompt_or_input": False,
        "contains_raw_output": False,
        "contains_local_paths": False,
        "exporter_ready": True,
    }


def _formal_runtime_sanitization() -> dict[str, object]:
    return {**_formal_sanitization(), "contains_runtime_identifiers": False}


def _install_sources(root: Path, relatives: tuple[Path, ...]) -> dict[Path, str]:
    result: dict[Path, str] = {}
    for relative in relatives:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / relative).read_bytes())
        result[relative] = hashlib.sha256(destination.read_bytes()).hexdigest()
    return result


def _formal_host_rollback(root: Path, generated_at: str) -> dict[str, object]:
    producer = module.formal_host_rollback_evidence
    bindings = {
        "lifecycle_sha256": producer.LIFECYCLE_RELATIVE,
        "transaction_module_sha256": producer.TRANSACTION_RELATIVE,
        "mount_contract_module_sha256": producer.MOUNT_CONTRACT_RELATIVE,
        "runner_sha256": producer.RUNNER_RELATIVE,
        "wrapper_sha256": producer.ROLLBACK_WRAPPER_RELATIVE,
    }
    installed = _install_sources(root, (producer.SCHEMA_RELATIVE, *bindings.values()))
    return {
        "schema_version": module.FORMAL_HOST_ROLLBACK_SCHEMA,
        "generated_at": generated_at,
        "decision": "GO",
        "profile": "siq_analysis",
        "scope": "formal_business_sandbox",
        "formal_business_run": True,
        "action": "rollback_to_host",
        "status": "stopped",
        "runtime": "host",
        "host_runtime_unchanged": True,
        "cutover_performed": False,
        "transaction": {
            "contract": "formal_transaction_v2_terminal_receipt",
            "phase": "stopped",
            "terminal_action": "rollback_to_host",
            "generation": 24,
            "before_receipt_sha256": "8" * 64,
            "terminal_receipt_sha256": "9" * 64,
            "resource_receipts_sha256": "a" * 64,
            "run_id_sha256": "6" * 64,
            "sandbox_id_sha256": "7" * 64,
            "container_id_sha256": "b" * 64,
        },
        "host_identity": {
            "contract": "exact_receipt_before_and_after",
            "baseline_receipt_sha256": "1" * 64,
            "before_receipt_sha256": "1" * 64,
            "after_receipt_sha256": "1" * 64,
        },
        "cleanup": {
            "sandbox_deleted": True,
            "forward_port_released": True,
            "active_state_removed": True,
            "ephemeral_identity_removed": True,
            "transaction_finalized": True,
            "publisher_index_published": True,
            "publisher_receipt_sha256": "d" * 64,
        },
        "provenance": {
            "hermes_commit": module.HERMES_COMMIT,
            "image_sha256": "2" * 64,
            "policy_sha256": "3" * 64,
            "raw_mount_plan_sha256": "5" * 64,
            "mount_contract_sha256": "4" * 64,
            **{key: installed[relative] for key, relative in bindings.items()},
            "evidence_schema_sha256": installed[producer.SCHEMA_RELATIVE],
            "raw_receipt_sha256": "c" * 64,
        },
        "sanitization": _formal_runtime_sanitization(),
    }


def _formal_delete_guard(root: Path, generated_at: str) -> dict[str, object]:
    producer = module.formal_delete_evidence
    bindings = {
        "lifecycle_sha256": producer.LIFECYCLE_RELATIVE,
        "transaction_module_sha256": producer.TRANSACTION_RELATIVE,
        "destructive_guard_sha256": producer.GUARD_RELATIVE,
        "guard_worker_sha256": producer.GUARD_WORKER_RELATIVE,
        "mount_contract_module_sha256": producer.MOUNT_CONTRACT_RELATIVE,
        "runner_sha256": producer.RUNNER_RELATIVE,
    }
    installed = _install_sources(root, (producer.SCHEMA_RELATIVE, *bindings.values()))
    cases = []
    for index, mechanism in enumerate(sorted(module.FORMAL_DELETE_MECHANISMS), start=1):
        cases.append(
            {
                "mechanism": mechanism,
                "triggered": True,
                "reason_code": "deletion_count_gt_500",
                "sandbox_terminated": True,
                "snapshot_restored": True,
                "observed_deleted_file_count": 501,
                "restored_file_count": 501,
                "residual_missing_file_count": 0,
                "run_id_sha256": f"{index}" * 64,
                "sandbox_id_sha256": f"{index + 3}" * 64,
                "transaction_receipt_sha256": f"{index + 6}" * 64,
                "guard_event_sha256": "a" * 64,
                "snapshot_manifest_sha256": "b" * 64,
                "snapshot_tree_sha256": "c" * 64,
                "raw_case_receipt_sha256": "d" * 64,
            }
        )
    normal_run = "4" * 64
    normal_transaction = "0" * 64
    run_values = sorted([case["run_id_sha256"] for case in cases] + [normal_run])
    transaction_values = sorted(
        [case["transaction_receipt_sha256"] for case in cases] + [normal_transaction]
    )
    return {
        "schema_version": module.FORMAL_DELETE_GUARD_SCHEMA,
        "generated_at": generated_at,
        "decision": "GO",
        "profile": "siq_analysis",
        "scope": "formal_business_sandbox",
        "formal_business_run": True,
        "business_inference_exercised": False,
        "threshold": {
            "absolute_deleted_file_count": 500,
            "trigger_operator": "greater_than",
            "monitoring": "recursive_filesystem_events",
        },
        "transactions": {
            "contract": "four_distinct_formal_transaction_v2_receipts",
            "transaction_count": 4,
            "run_set_sha256": producer._canonical_sha256(run_values),
            "receipt_set_sha256": producer._canonical_sha256(transaction_values),
        },
        "cases": cases,
        "normal_cleanup": {
            "mechanism": "current_task_file_cleanup",
            "deleted_file_count": 3,
            "guard_triggered": False,
            "allowed": True,
            "sandbox_remained_healthy": True,
            "cleanup_succeeded": True,
            "unexpected_residual_file_count": 0,
            "mkdir_allowed": True,
            "create_allowed": True,
            "write_allowed": True,
            "overwrite_allowed": True,
            "rename_allowed": True,
            "small_delete_allowed": True,
            "recursive_cleanup_allowed": True,
            "run_id_sha256": normal_run,
            "sandbox_id_sha256": "7" * 64,
            "transaction_receipt_sha256": normal_transaction,
            "raw_case_receipt_sha256": "e" * 64,
        },
        "cleanup": {
            "sandbox_deleted": True,
            "forward_port_released": True,
            "active_state_removed": True,
            "ephemeral_identity_removed": True,
            "transaction_finalized": True,
            "snapshot_artifacts_removed": True,
            "fixture_removed": True,
            "outside_analysis_tree_unchanged": True,
        },
        "host_runtime_unchanged": True,
        "cutover_performed": False,
        "snapshot_integrity_verified": True,
        "analysis_tree_outside_fixture_unchanged": True,
        "provenance": {
            "hermes_commit": module.HERMES_COMMIT,
            "image_sha256": "2" * 64,
            "policy_sha256": "3" * 64,
            "mount_contract_sha256": "4" * 64,
            **{key: installed[relative] for key, relative in bindings.items()},
            "evidence_schema_sha256": installed[producer.SCHEMA_RELATIVE],
            "raw_receipt_sha256": "f" * 64,
        },
        "sanitization": _formal_runtime_sanitization(),
    }


def _formal_filesystem_boundary(root: Path, generated_at: str) -> dict[str, object]:
    producer = module.formal_filesystem_evidence
    source_bindings = {
        "probe_module_sha256": producer.PROBE_MODULE_RELATIVE,
        "lifecycle_sha256": producer.LIFECYCLE_RELATIVE,
        "transaction_module_sha256": producer.TRANSACTION_RELATIVE,
        "mount_contract_module_sha256": producer.MOUNT_CONTRACT_RELATIVE,
        "runner_sha256": producer.RUNNER_RELATIVE,
    }
    for relative in (producer.SCHEMA_RELATIVE, *source_bindings.values()):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / relative).read_bytes())
    schema_sha256 = hashlib.sha256((root / producer.SCHEMA_RELATIVE).read_bytes()).hexdigest()
    source_sha256 = {
        key: hashlib.sha256((root / relative).read_bytes()).hexdigest() for key, relative in source_bindings.items()
    }
    policy_sha256 = "3" * 64
    receipt_sha256 = "1" * 64
    return {
        "schema_version": module.FORMAL_FILESYSTEM_BOUNDARY_SCHEMA,
        "generated_at": generated_at,
        "decision": "GO",
        "profile": "siq_analysis",
        "scope": "formal_running_filesystem_boundary",
        "formal_business_sandbox": True,
        "business_inference_exercised": False,
        "component_acceptance": True,
        "overall_readiness_effect": "component_evidence_only",
        "host_runtime_unchanged": True,
        "traffic_cutover_performed": False,
        "transaction": {
            "contract": "active_transaction_v2_exact_receipts",
            "generation": 1,
            "before_receipt_sha256": receipt_sha256,
            "after_receipt_sha256": receipt_sha256,
            "run_id_sha256": "2" * 64,
            "sandbox_id_sha256": "4" * 64,
            "container_id_sha256": "5" * 64,
            "session_id_sha256": "6" * 64,
            "policy_sha256": policy_sha256,
            "resource_receipts_sha256": "7" * 64,
        },
        "stability": {
            "active_transaction_before": True,
            "active_transaction_after": True,
            "transaction_receipt_unchanged": True,
            "manifest_receipt_unchanged": True,
            "sandbox_binding_unchanged": True,
            "host_receipt_unchanged": True,
            "policy_receipt_unchanged": True,
            "sandbox_healthy_after": True,
            "guard_trigger_absent": True,
        },
        "mount_contract": {
            "business_mount_count": 7,
            "control_mount_count": 5,
            "total_mount_count": 12,
            "mount_plan_sha256": "4" * 64,
            "mount_contract_sha256": "4" * 64,
        },
        "immutable_write_denials": {key: True for key in producer.sandbox_probe.FILESYSTEM_IMMUTABLE_DENIALS},
        "sensitive_read_denials": {key: True for key in producer.sandbox_probe.FILESYSTEM_SENSITIVE_DENIALS},
        "allowed_writes": {key: True for key in producer.sandbox_probe.FILESYSTEM_ALLOWED_WRITES},
        "cleanup": {
            "sentinel_cleanup_attempted": True,
            "sentinel_cleanup_succeeded": True,
            "residual_host_sentinel_count": 0,
            "sandbox_remained_running": True,
        },
        "provenance": {
            "hermes_commit": module.HERMES_COMMIT,
            "image_sha256": "2" * 64,
            "policy_sha256": policy_sha256,
            "mount_contract_sha256": "4" * 64,
            "runtime_config_sha256": "8" * 64,
            "filesystem_probe_sha256": hashlib.sha256(
                producer.sandbox_probe.FILESYSTEM_PROBE.encode("utf-8")
            ).hexdigest(),
            **source_sha256,
            "evidence_schema_sha256": schema_sha256,
            "raw_receipt_sha256": "9" * 64,
        },
        "not_claimed": list(producer.NOT_CLAIMED),
        "sanitization": {
            "contains_api_keys": False,
            "contains_headers": False,
            "contains_prompt_or_input": False,
            "contains_raw_output": False,
            "contains_local_paths": False,
            "contains_runtime_identifiers": False,
            "exporter_ready": True,
        },
    }


def _formal_fallback_drill(
    root: Path,
    generated_at: str,
    *,
    summary_path: Path,
    prerequisites_path: Path,
) -> dict[str, object]:
    producer = module.formal_fallback_drill_evidence
    bindings = {
        "evidence_schema_sha256": producer.SCHEMA_RELATIVE,
        "runner_sha256": producer.RUNNER_RELATIVE,
        "validator_sha256": producer.VALIDATOR_RELATIVE,
        "lifecycle_sha256": producer.LIFECYCLE_RELATIVE,
        "evaluator_sha256": producer.EVALUATOR_RELATIVE,
    }
    installed = _install_sources(root, tuple(bindings.values()))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    prerequisites = json.loads(prerequisites_path.read_text(encoding="utf-8"))
    return {
        "schema_version": producer.SCHEMA_VERSION,
        "generated_at": generated_at,
        "decision": "PASS",
        "profile": "siq_analysis",
        "evaluation_id": summary["evaluation_id"],
        "dataset_sha256": summary["dataset_sha256"],
        "normal_summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
        "prerequisites_sha256": hashlib.sha256(prerequisites_path.read_bytes()).hexdigest(),
        "provenance_sha256": prerequisites["provenance"]["sha256"],
        "transaction": {
            "api_runtime_receipt_before_sha256": "1" * 64,
            "api_runtime_receipt_after_sha256": "1" * 64,
            "transaction_receipt_sha256": "2" * 64,
            "run_id_sha256": "3" * 64,
            "sandbox_id_sha256": "4" * 64,
            "container_id_sha256": "5" * 64,
            "host_receipt_before_sha256": "6" * 64,
            "host_receipt_after_sha256": "6" * 64,
            "gateway_receipt_before_sha256": "7" * 64,
            "gateway_receipt_after_sha256": "7" * 64,
            "image_id": "sha256:" + "2" * 64,
            "policy_sha256": "3" * 64,
            "mount_plan_sha256": "4" * 64,
            "mount_contract_sha256": "4" * 64,
            "runtime_config_sha256": "8" * 64,
            "fault_injection_sha256": "9" * 64,
        },
        "fault_injection": {
            "kind": "primary_http_503",
            "bind_scope": "verified_docker_bridge_gateway_only",
            "bind_port": 8004,
            "expected_status": 503,
            "target_url_sha256": "a" * 64,
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
            "configured_provider": "primary-provider",
            "configured_model": "pinned-model-alias",
            "effective_providers": ["fallback-provider"],
            "effective_models": ["fallback-model"],
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
            **{key: installed[relative] for key, relative in bindings.items()},
            "primary_provider": "primary-provider",
            "primary_model": "pinned-model-alias",
            "fallback_route_sha256": "b" * 64,
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


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _formal_transaction() -> dict[str, object]:
    return {
        "transaction_receipt_sha256": _digest("transaction-receipt"),
        "run_id_sha256": _digest("formal-run"),
        "sandbox_id_sha256": _digest("formal-sandbox"),
        "session_id_sha256": _digest("formal-session"),
        "policy_sha256": "3" * 64,
        "audit_records_sha256": _digest("formal-audit-records"),
    }


def _host_egress_component(now: datetime) -> dict[str, object]:
    cases = []
    for case_id, (decision, rule_id) in module.HOST_EGRESS_CASES.items():
        denied = decision == "deny"
        cases.append(
            {
                "case_id": case_id,
                "decision": decision,
                "outer_http_status": 403 if denied else 200,
                "rule_id": rule_id,
                "upstream_http_status": None if denied else (405 if case_id == "unknown_small_json" else 200),
            }
        )
    environment_digest_fields = {
        "allowlist_sha256",
        "allowlist_contract_sha256",
        "audit_contract_sha256",
        "broker_lifecycle_sha256",
        "broker_identity_contract_sha256",
        "egress_decision_sha256",
        "egress_guard_sha256",
        "evidence_schema_sha256",
        "mihomo_runtime_config_sha256",
        "proof_runner_sha256",
        "runtime_source_bundle_sha256",
        "siq_fetch_sha256",
        "toolchain_manifest_sha256",
    }
    return {
        "schema_version": module.HOST_EGRESS_COMPONENT_SCHEMA,
        "decision": "GO",
        "passed": True,
        "captured_at_unix": int(now.timestamp()) - 60,
        "valid_until_unix": int(now.timestamp()) + 3600,
        "scope": "host_egress_broker",
        "formal_business_run": False,
        "formal_business_sandbox_evidence": False,
        "readiness_effect": "none",
        "eligible_for_completion": False,
        "gateway": "siq-openshell-dev",
        "environment_binding": {
            **{key: _digest(key) for key in environment_digest_fields},
            "request_identity_required": True,
            "resolver_audit_rule": "mihomo_fake_ip_compat_resolved",
            "resolver_mode": "mihomo_fake_ip_verified",
        },
        "audit_binding": {
            "audit_record_count": 13,
            "audit_records_sha256": _digest("host-audit-records"),
            "run_id_sha256": _digest("host-run"),
        },
        "checks": {
            "public_get_allowed": True,
            "public_head_allowed": True,
            "missing_identity_denied": True,
            "wrong_audience_denied": True,
            "unknown_small_json_audit_only": True,
            "unknown_multipart_denied": True,
            "unknown_octet_stream_denied": True,
            "unknown_put_denied": True,
            "cloud_metadata_denied": True,
            "audit_records_bound": True,
            "target_values_stored": False,
            "request_payloads_stored": False,
            "response_payloads_stored": False,
            "runtime_credentials_stored": False,
        },
        "cases": cases,
        "not_claimed": [
            "formal_business_sandbox",
            "direct_transfer_client_execution",
            "provider_route_availability",
            "restart_persistence",
            "semantic_dlp",
        ],
    }


def _formal_egress(
    root: Path,
    generated_at: str,
    *,
    host_path: Path,
    host_digest: str,
    host_component: dict[str, object],
) -> dict[str, object]:
    source_bindings = {
        "egress_guard_sha256": module.EGRESS_GUARD_RELATIVE,
        "request_identity_contract_sha256": module.REQUEST_IDENTITY_RELATIVE,
        "evidence_schema_sha256": module.FORMAL_EGRESS_SCHEMA_RELATIVE,
        "exporter_sha256": module.FORMAL_EGRESS_EXPORTER_RELATIVE,
        "audit_contract_sha256": module.AUDIT_CONTRACT_RELATIVE,
        "aggregator_sha256": module.AUDIT_AGGREGATOR_RELATIVE,
        "audit_evidence_schema_sha256": module.FORMAL_AUDIT_SCHEMA_RELATIVE,
    }
    for relative in source_bindings.values():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / relative).read_bytes())
    transaction = _formal_transaction()
    cases = [
        {
            "case_id": case_id,
            "decision": expected[0],
            "enforcement_layer": expected[1],
            "reason_code": expected[2],
            "audit_record_sha256": _digest(f"formal-egress-case:{case_id}"),
        }
        for case_id, expected in module.FORMAL_EGRESS_CASES.items()
    ]
    return {
        "schema_version": module.FORMAL_EGRESS_SANDBOX_SCHEMA,
        "generated_at": generated_at,
        "decision": "GO",
        "profile": "siq_analysis",
        "scope": "formal_business_sandbox",
        "formal_business_run": True,
        "eligible_for_completion": True,
        "host_runtime_unchanged": True,
        "cutover_performed": False,
        "transaction": transaction,
        "host_egress_component": {
            "path": host_path.as_posix(),
            "sha256": host_digest,
            "schema_version": module.HOST_EGRESS_COMPONENT_SCHEMA,
            "scope": "host_egress_broker",
            "decision": "GO",
            "passed": True,
            "eligible_for_completion": False,
            "captured_at_unix": host_component["captured_at_unix"],
            "valid_until_unix": host_component["valid_until_unix"],
        },
        "sandbox_network_enforcement": {
            "egress_mode": "broker_and_approved_providers_only",
            "direct_public_tcp_denied": True,
            "direct_public_udp_denied": True,
            "direct_public_websocket_denied": True,
            "cloud_metadata_denied": True,
            "broker_request_identity_required": True,
            "unknown_raw_socket_route_present": False,
        },
        "direct_denial_contract": {
            "receiver_binding": "verified_bridge_gateway_ephemeral_tcp_udp",
            "controlled_endpoint_permission_present": False,
            "connection_observed": False,
            "client_exit_status_only_accepted": False,
            "protocol_or_auth_failure_accepted": False,
        },
        "transfer_clients_tested": ["curl_upload", "rclone", "rsync", "scp", "sftp"],
        "cases": cases,
        "provenance": {
            "hermes_commit": module.HERMES_COMMIT,
            "image_sha256": "2" * 64,
            "policy_sha256": transaction["policy_sha256"],
            "mount_contract_sha256": "4" * 64,
            "runtime_config_sha256": "8" * 64,
            "egress_guard_sha256": hashlib.sha256((root / module.EGRESS_GUARD_RELATIVE).read_bytes()).hexdigest(),
            "request_identity_contract_sha256": hashlib.sha256(
                (root / module.REQUEST_IDENTITY_RELATIVE).read_bytes()
            ).hexdigest(),
            "evidence_schema_sha256": hashlib.sha256(
                (root / module.FORMAL_EGRESS_SCHEMA_RELATIVE).read_bytes()
            ).hexdigest(),
            "exporter_sha256": hashlib.sha256(
                (root / module.FORMAL_EGRESS_EXPORTER_RELATIVE).read_bytes()
            ).hexdigest(),
            "transaction_receipt_sha256": transaction["transaction_receipt_sha256"],
        },
        "sanitization": _formal_sanitization(),
    }


def _formal_audit(root: Path, generated_at: str, formal_egress: dict[str, object]) -> dict[str, object]:
    transaction = formal_egress["transaction"]
    assert isinstance(transaction, dict)
    cases = formal_egress["cases"]
    assert isinstance(cases, list)
    return {
        "schema_version": module.FORMAL_STRUCTURED_AUDIT_SCHEMA,
        "generated_at": generated_at,
        "decision": "GO",
        "profile": "siq_analysis",
        "scope": "formal_business_sandbox",
        "formal_business_run": True,
        "eligible_for_completion": True,
        "host_runtime_unchanged": True,
        "cutover_performed": False,
        "transaction": dict(transaction),
        "source_contract": {
            "record_schema_version": "siq.openshell.audit.v1",
            "aggregate_schema_version": "siq.openshell.audit-summary.v1",
            "source_file_count": 1,
            "record_count": 20,
            "audit_records_sha256": transaction["audit_records_sha256"],
            "source_set_sha256": _digest("formal-source-file-set"),
            "chronological_order_verified": True,
            "strict_schema_validated": True,
            "transaction_filtered": True,
            "single_transaction": True,
            "single_policy": True,
        },
        "identity_coverage": {
            "profile_present": True,
            "sandbox_identity_projected": True,
            "siq_run_identity_projected": True,
            "session_identity_projected": True,
            "operation_class_present": True,
            "target_projected": True,
            "decision_present": True,
            "policy_digest_present": True,
            "error_code_present": True,
            "duration_present": True,
        },
        "decision_counts": {"allow": 5, "deny": 14, "audit_only": 1},
        "operation_counts": {
            "database.query": 0,
            "filesystem.delete": 0,
            "filesystem.write": 0,
            "immutable.write": 0,
            "network.request": 17,
            "publisher.index": 0,
            "runtime.route": 0,
            "sandbox.lifecycle": 2,
            "service.preflight": 1,
        },
        "event_classification": {
            "formal_runner_observation_count": 3,
            "security_probe_event_count": 17,
            "unclassified_count": 0,
        },
        "security_case_event_sha256": [str(case["audit_record_sha256"]) for case in cases],
        "metrics": {
            "policy_deny_count": 14,
            "audit_only_count": 1,
            "external_upload_blocks": 10,
            "immutable_write_blocks": 0,
            "sandbox_start_failures": 0,
            "gateway_overhead_ms": {"sample_count": 0, "p50": None, "p95": None},
        },
        "provenance": {
            "audit_contract_sha256": hashlib.sha256((root / module.AUDIT_CONTRACT_RELATIVE).read_bytes()).hexdigest(),
            "aggregator_sha256": hashlib.sha256((root / module.AUDIT_AGGREGATOR_RELATIVE).read_bytes()).hexdigest(),
            "evidence_schema_sha256": hashlib.sha256(
                (root / module.FORMAL_AUDIT_SCHEMA_RELATIVE).read_bytes()
            ).hexdigest(),
            "exporter_sha256": hashlib.sha256(
                (root / module.FORMAL_EGRESS_EXPORTER_RELATIVE).read_bytes()
            ).hexdigest(),
            "source_file_set_sha256": _digest("formal-source-file-set"),
            "transaction_receipt_sha256": transaction["transaction_receipt_sha256"],
        },
        "content_absence": {
            "contains_api_keys": False,
            "contains_headers": False,
            "contains_prompt_or_input": False,
            "contains_raw_output": False,
            "contains_local_paths": False,
            "contains_request_or_response_content": False,
            "contains_sql_or_vector_payload": False,
            "contains_target_values": False,
            "contains_unprojected_session_id": False,
            "exporter_ready": True,
        },
    }


def _memory_write_evidence(root: Path, now: datetime) -> Path:
    evidence = module.memory_write_evidence
    boundary_path = root / evidence.BOUNDARY_RELATIVE
    schema_path = root / evidence.EVIDENCE_SCHEMA_RELATIVE
    _write_json(boundary_path, evidence.EXPECTED_BOUNDARY)
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    schema_path.write_bytes((ROOT / evidence.EVIDENCE_SCHEMA_RELATIVE).read_bytes())

    completed = int(now.timestamp())
    started = completed - 10
    postgres_outcome = {
        "insert": True,
        "readback": True,
        "rollback": True,
        "post_rollback_verify": True,
        "residual_count": 0,
    }
    milvus_outcome = {
        "upsert": True,
        "get": True,
        "search": True,
        "delete": True,
        "post_delete_verify": True,
        "residual_count": 0,
    }
    payload = {
        "schema_version": evidence.SCHEMA_VERSION,
        "decision": "GO",
        "passed": True,
        "evidence_window": {
            "started_at_unix": started,
            "completed_at_unix": completed,
        },
        "contract_binding": {
            "boundary_contract_sha256": hashlib.sha256(boundary_path.read_bytes()).hexdigest(),
            "evidence_schema_sha256": hashlib.sha256(schema_path.read_bytes()).hexdigest(),
            "executor": evidence.EXECUTOR,
            "logical_alias": evidence.LOGICAL_ALIAS,
            "physical_collection_sha256": hashlib.sha256(b"siq_agent_memory__v2").hexdigest(),
            "required_schema_version": evidence.REQUIRED_COLLECTION_SCHEMA,
            "sandbox_direct_milvus": False,
        },
        "agent_groups": list(evidence.AGENT_GROUPS),
        "backends": {
            "postgresql": {
                "captured_at_unix": started,
                "completed_at_unix": completed,
                "probe_sha256": "a" * 64,
                "receipt_sha256": "b" * 64,
                "operation_outcomes": {
                    "primary_market": dict(postgres_outcome),
                    "secondary_market": dict(postgres_outcome),
                },
                "residual_count": 0,
            },
            "milvus": {
                "captured_at_unix": started,
                "completed_at_unix": completed,
                "probe_sha256": "c" * 64,
                "receipt_sha256": "d" * 64,
                "schema_preflight_passed": True,
                "operation_outcomes": {
                    "primary_market": dict(milvus_outcome),
                    "secondary_market": dict(milvus_outcome),
                },
                "residual_count": 0,
            },
        },
    }
    path = root / module.DEFAULT_MEMORY_WRITE_EVIDENCE
    _write_json(path, payload, mode=0o600)
    return path


def _review_payload(
    *,
    reviewed_at: str,
    readiness_sha256: str,
    verification: dict[str, object],
) -> dict[str, object]:
    return {
        "schema_version": module.ARCHITECTURE_REVIEW_SCHEMA,
        "review_id": "review-live-20260716",
        "decision": "approved",
        "reviewed_at": reviewed_at,
        "reviewer": {
            "name": "Independent Reviewer",
            "role": "Security Architect",
            "organization": "SIQ Review Board",
        },
        "scope": {
            "profile": "siq_analysis",
            "openshell_version": "0.0.83",
            "hermes_commit": module.HERMES_COMMIT,
        },
        "evidence": {
            "readiness_sha256": readiness_sha256,
            "service_preflight_sha256": verification["service_preflight_sha256"],
            "formal_ab_summary_sha256": verification["formal_ab_summary_sha256"],
            "formal_fallback_drill_sha256": verification["formal_fallback_drill_sha256"],
            "formal_host_rollback_sha256": verification["formal_host_rollback_sha256"],
            "formal_delete_guard_sha256": verification["formal_delete_guard_sha256"],
            "formal_egress_sandbox_sha256": verification["formal_egress_sandbox_sha256"],
            "formal_structured_audit_sha256": verification["formal_structured_audit_sha256"],
        },
        "checklist": {field: True for field in module.REVIEW_CHECKLIST_FIELDS},
        "cutover_performed": False,
    }


def _complete_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, Path, dict[str, object], dict[str, object]]:
    root = tmp_path / "project"
    service_path = root / "artifacts/openshell/v0.6/service-preflight.sanitized.json"
    rollback_path = root / module.DEFAULT_FORMAL_HOST_ROLLBACK
    delete_guard_path = root / module.DEFAULT_FORMAL_DELETE_GUARD
    filesystem_boundary_path = root / module.DEFAULT_FORMAL_FILESYSTEM_BOUNDARY
    host_egress_path = root / module.DEFAULT_HOST_EGRESS_COMPONENT
    formal_egress_path = root / module.DEFAULT_FORMAL_EGRESS_SANDBOX
    formal_audit_path = root / module.DEFAULT_FORMAL_STRUCTURED_AUDIT
    formal_fallback_path = root / module.DEFAULT_FORMAL_FALLBACK_DRILL
    memory_evidence_path = root / module.DEFAULT_MEMORY_WRITE_EVIDENCE
    summary_path = root / "var/openshell/eval/eval-live-20260716/summary.json"
    prerequisites_path = root / "var/openshell/eval/eval-live-20260716/prerequisites.json"
    now_datetime = datetime.now(timezone.utc).replace(microsecond=0)
    now = now_datetime.isoformat().replace("+00:00", "Z")
    _write_json(service_path, _service_go())
    summary = _ab_summary()
    _write_json(prerequisites_path, _ab_prerequisites(summary))
    summary["prerequisites_sha256"] = hashlib.sha256(prerequisites_path.read_bytes()).hexdigest()
    _write_json(summary_path, summary)
    _write_json(
        formal_fallback_path,
        _formal_fallback_drill(
            root,
            now,
            summary_path=summary_path,
            prerequisites_path=prerequisites_path,
        ),
    )
    for relative in (
        "artifacts/openshell/v0.6/baseline.json",
        "artifacts/openshell/v0.6/immutable-registry.sanitized.json",
        "artifacts/openshell/v0.6/milvus-write-protection.sanitized.json",
    ):
        _write_json(root / relative, {"schema_version": "fixture.v1"})
    _write_json(rollback_path, _formal_host_rollback(root, now))
    _write_json(delete_guard_path, _formal_delete_guard(root, now))
    _write_json(filesystem_boundary_path, _formal_filesystem_boundary(root, now))
    host_egress = _host_egress_component(now_datetime)
    _write_json(host_egress_path, host_egress)
    formal_egress = _formal_egress(
        root,
        now,
        host_path=host_egress_path.relative_to(root),
        host_digest=hashlib.sha256(host_egress_path.read_bytes()).hexdigest(),
        host_component=host_egress,
    )
    _write_json(formal_egress_path, formal_egress)
    _write_json(formal_audit_path, _formal_audit(root, now, formal_egress))
    assert _memory_write_evidence(root, now_datetime) == memory_evidence_path
    service_relative = service_path.relative_to(root).as_posix()
    rollback_relative = rollback_path.relative_to(root).as_posix()
    delete_guard_relative = delete_guard_path.relative_to(root).as_posix()
    filesystem_boundary_relative = filesystem_boundary_path.relative_to(root).as_posix()
    host_egress_relative = host_egress_path.relative_to(root).as_posix()
    formal_egress_relative = formal_egress_path.relative_to(root).as_posix()
    formal_audit_relative = formal_audit_path.relative_to(root).as_posix()
    formal_fallback_relative = formal_fallback_path.relative_to(root).as_posix()
    memory_evidence_relative = memory_evidence_path.relative_to(root).as_posix()
    summary_relative = summary_path.relative_to(root).as_posix()
    prerequisites_relative = prerequisites_path.relative_to(root).as_posix()
    runbook_digest = "e" * 64
    monkeypatch.setattr(module, "_runbook_index_digest", lambda _root: runbook_digest)
    readiness = {
        "schema_version": module.READINESS_SCHEMA,
        "generated_at": now,
        "decision": "GO",
        "decision_scope": "formal_hermes_traffic_cutover",
        "default_runtime": "host",
        "runtime_state": {
            "project_gateway": "healthy",
            "host_brokers": "healthy",
            "formal_image_smoke": "passed",
            "provider_independent_probe": "passed",
            "broker_preflight": "passed",
            "service_preflight": "passed",
            "formal_business_sandbox_created": True,
            "formal_ab_completed": True,
            "quality_validated": True,
            "formal_fallback_drill": "passed",
        },
        "network_contract": {
            "internal_model_reachability": {str(port): "online" for port in (8004, 8006, 8007, 8013)},
        },
        "providers": {
            "configured": [
                "siq-minimax-cn-pool",
                "siq-stepfun",
                "siq-kimi-coding",
                "siq-tavily-search",
            ],
            "required_missing": [],
        },
        "data_boundary": {
            "postgres_readonly_verified": True,
            "milvus_sandbox_write_proof": True,
        },
        "security_controls": {
            "project_code_readonly": True,
            "agent_control_files_readonly": True,
            "finalized_ingested_paths_readonly": True,
            "task_analysis_path_writable": True,
            "runtime_session_and_memory_paths_writable": True,
            "unknown_file_upload_blocked": True,
            "high_risk_delete_guard": True,
        },
        "lifecycle_safety": {"host_rollback_identity": "exact_receipt_before_and_after"},
        "contracts": {"api_and_output_paths_unchanged": True},
        "verification": {
            "service_preflight_evidence": service_relative,
            "service_preflight_sha256": hashlib.sha256(service_path.read_bytes()).hexdigest(),
            "formal_ab_evidence": summary_relative,
            "formal_ab_summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
            "formal_ab_prerequisites_evidence": prerequisites_relative,
            "formal_ab_prerequisites_sha256": hashlib.sha256(prerequisites_path.read_bytes()).hexdigest(),
            "formal_fallback_drill_evidence": formal_fallback_relative,
            "formal_fallback_drill_sha256": hashlib.sha256(formal_fallback_path.read_bytes()).hexdigest(),
            "formal_host_rollback_evidence": rollback_relative,
            "formal_host_rollback_sha256": hashlib.sha256(rollback_path.read_bytes()).hexdigest(),
            "formal_delete_guard_evidence": delete_guard_relative,
            "formal_delete_guard_sha256": hashlib.sha256(delete_guard_path.read_bytes()).hexdigest(),
            "formal_filesystem_boundary_evidence": filesystem_boundary_relative,
            "formal_filesystem_boundary_sha256": hashlib.sha256(filesystem_boundary_path.read_bytes()).hexdigest(),
            "host_egress_component_evidence": host_egress_relative,
            "host_egress_component_sha256": hashlib.sha256(host_egress_path.read_bytes()).hexdigest(),
            "formal_egress_sandbox_evidence": formal_egress_relative,
            "formal_egress_sandbox_sha256": hashlib.sha256(formal_egress_path.read_bytes()).hexdigest(),
            "formal_structured_audit_evidence": formal_audit_relative,
            "formal_structured_audit_sha256": hashlib.sha256(formal_audit_path.read_bytes()).hexdigest(),
            "memory_write_evidence": memory_evidence_relative,
            "memory_write_evidence_sha256": hashlib.sha256(memory_evidence_path.read_bytes()).hexdigest(),
            "sanitized_artifact_scan": "passed",
            "tracked_state_scan": "passed",
            "published_evidence_index_scan": "passed",
            "openshell_docs_sha256": runbook_digest,
        },
        "blockers": [],
    }
    readiness_path = root / "artifacts/openshell/v0.6/readiness.json"
    _write_json(readiness_path, readiness)
    review_path = root / "review.sanitized.json"
    verification = readiness["verification"]
    assert isinstance(verification, dict)
    _write_json(
        review_path,
        _review_payload(
            reviewed_at=now,
            readiness_sha256=hashlib.sha256(readiness_path.read_bytes()).hexdigest(),
            verification=verification,
        ),
    )
    monkeypatch.setattr(module.check_tracked_state, "scan_tracked_state", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(module, "_git_index_evidence_matches", lambda *_args, **_kwargs: True)
    return root, readiness_path, service_path, readiness, summary


def test_current_evidence_is_explicitly_no_go_without_ab_or_review() -> None:
    report = module.build_report(project_root=ROOT)

    assert report["decision"] == "NO_GO"
    assert report["total_count"] == 13
    assert report["cutover_performed"] is False
    assert "formal_ab_missing_or_failed" in report["blockers"]
    assert "human_review_missing" in report["blockers"]


def test_cli_no_go_is_diagnostic_unless_require_go(tmp_path: Path, capsys) -> None:
    readiness = tmp_path / "readiness.json"
    service = tmp_path / "service.json"
    for path, payload in (
        (readiness, {"schema_version": "siq.openshell.readiness.v1", "default_runtime": "host"}),
        (service, {"schema_version": "siq.openshell.service_preflight.v1", "decision": "NO_GO", "passed": False}),
    ):
        path.write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "artifacts/openshell/v0.6").mkdir(parents=True)
    # The custom evidence paths are enough to exercise the CLI error contract;
    # missing required default evidence is reported as configuration failure.
    assert (
        module.main(
            ["--project-root", str(tmp_path), "--readiness", str(readiness), "--service-report", str(service), "--json"]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().err)["error_code"] == "evidence_path_missing"


def test_complete_fresh_cross_bound_evidence_can_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _, _, _, _ = _complete_project(tmp_path, monkeypatch)

    report = module.build_report(
        project_root=root,
        ab_summary_path=Path("var/openshell/eval/eval-live-20260716/summary.json"),
        ab_prerequisites_path=Path("var/openshell/eval/eval-live-20260716/prerequisites.json"),
        review_record_path=Path("review.sanitized.json"),
    )

    assert report["decision"] == "GO"
    assert report["passed_count"] == report["total_count"] == 13
    assert report["blockers"] == []


@pytest.mark.parametrize(
    "corruption",
    ("missing", "evaluation", "summary_digest", "provenance_digest", "runtime_config"),
)
def test_formal_fallback_drill_is_required_and_cross_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    root, _, _, readiness, _ = _complete_project(tmp_path, monkeypatch)
    path = root / module.DEFAULT_FORMAL_FALLBACK_DRILL
    if corruption == "missing":
        path.unlink()
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if corruption == "evaluation":
            payload["evaluation_id"] = "eval-live-other"
        elif corruption == "summary_digest":
            payload["normal_summary_sha256"] = "f" * 64
        elif corruption == "provenance_digest":
            payload["provenance_sha256"] = "f" * 64
        else:
            payload["transaction"]["runtime_config_sha256"] = "f" * 64
        _write_json(path, payload)
        verification = readiness["verification"]
        assert isinstance(verification, dict)
        verification["formal_fallback_drill_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        _write_json(root / module.DEFAULT_READINESS, readiness)

    report = _completion_report(root)
    checks = {item["check_id"]: item["status"] for item in report["checks"]}

    assert report["decision"] == "NO_GO"
    assert checks["real_host_openshell_ab"] == "no_go"
    assert checks["quality_gate"] == "no_go"
    assert checks["reproducible_sanitized_evidence"] == "no_go"


def test_formal_fallback_allows_transaction_specific_raw_mount_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _, readiness, _ = _complete_project(tmp_path, monkeypatch)
    path = root / module.DEFAULT_FORMAL_FALLBACK_DRILL
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["transaction"]["mount_plan_sha256"] = "f" * 64
    _write_json(path, payload)
    verification = readiness["verification"]
    assert isinstance(verification, dict)
    verification["formal_fallback_drill_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    _write_json(root / module.DEFAULT_READINESS, readiness)

    report = _completion_report(root)

    assert report["decision"] == "GO"


def test_formal_fallback_rejects_different_normalized_mount_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _, readiness, _ = _complete_project(tmp_path, monkeypatch)
    path = root / module.DEFAULT_FORMAL_FALLBACK_DRILL
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["transaction"]["mount_contract_sha256"] = "f" * 64
    _write_json(path, payload)
    verification = readiness["verification"]
    assert isinstance(verification, dict)
    verification["formal_fallback_drill_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    _write_json(root / module.DEFAULT_READINESS, readiness)

    report = _completion_report(root)
    checks = {item["check_id"]: item["status"] for item in report["checks"]}

    assert report["decision"] == "NO_GO"
    assert checks["real_host_openshell_ab"] == "no_go"
    assert checks["quality_gate"] == "no_go"
    assert checks["reproducible_sanitized_evidence"] == "no_go"


def test_normal_ab_summary_requires_only_fallback_deltas_to_be_null() -> None:
    summary = _ab_summary()
    assert module._validate_ab_summary(summary) is True

    summary["schema_version"] = "siq.openshell.siq-analysis-ab-summary.v2"
    assert module._validate_ab_summary(summary) is False

    summary = _ab_summary()
    summary["comparison"]["metric_deltas"]["fallback_success_rate"] = 0.0
    assert module._validate_ab_summary(summary) is False

    summary = _ab_summary()
    summary["comparison"]["metric_deltas"]["numeric_accuracy"] = None
    assert module._validate_ab_summary(summary) is False


def test_formal_filesystem_evidence_requires_readiness_path_and_digest_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, readiness_path, _, readiness, _ = _complete_project(tmp_path, monkeypatch)
    verification = readiness["verification"]
    assert isinstance(verification, dict)
    verification.pop("formal_filesystem_boundary_sha256")
    _write_json(readiness_path, readiness)

    report = module.build_report(
        project_root=root,
        ab_summary_path=Path("var/openshell/eval/eval-live-20260716/summary.json"),
        ab_prerequisites_path=Path("var/openshell/eval/eval-live-20260716/prerequisites.json"),
    )

    checks = {item["check_id"]: item for item in report["checks"]}
    assert checks["immutable_write_denials"]["status"] == "no_go"
    assert checks["normal_analysis_and_memory_writes"]["status"] == "no_go"
    assert checks["reproducible_sanitized_evidence"]["status"] == "no_go"


def test_formal_filesystem_tampering_stays_no_go_after_digest_rebinding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, readiness_path, _, readiness, _ = _complete_project(tmp_path, monkeypatch)
    evidence_path = root / module.DEFAULT_FORMAL_FILESYSTEM_BOUNDARY
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["transaction"]["policy_sha256"] = "f" * 64
    _write_json(evidence_path, evidence)
    verification = readiness["verification"]
    assert isinstance(verification, dict)
    verification["formal_filesystem_boundary_sha256"] = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    _write_json(readiness_path, readiness)

    report = module.build_report(
        project_root=root,
        ab_summary_path=Path("var/openshell/eval/eval-live-20260716/summary.json"),
        ab_prerequisites_path=Path("var/openshell/eval/eval-live-20260716/prerequisites.json"),
    )

    checks = {item["check_id"]: item for item in report["checks"]}
    assert checks["immutable_write_denials"]["status"] == "no_go"
    assert checks["normal_analysis_and_memory_writes"]["status"] == "no_go"


def test_formal_runtime_provenance_mismatch_cannot_be_rebound_to_go(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, readiness_path, _, readiness, _ = _complete_project(tmp_path, monkeypatch)
    delete_path = root / module.DEFAULT_FORMAL_DELETE_GUARD
    delete_evidence = json.loads(delete_path.read_text(encoding="utf-8"))
    delete_evidence["provenance"]["policy_sha256"] = "f" * 64
    _write_json(delete_path, delete_evidence)
    verification = readiness["verification"]
    assert isinstance(verification, dict)
    verification["formal_delete_guard_sha256"] = hashlib.sha256(delete_path.read_bytes()).hexdigest()
    _write_json(readiness_path, readiness)

    report = module.build_report(
        project_root=root,
        ab_summary_path=Path("var/openshell/eval/eval-live-20260716/summary.json"),
        ab_prerequisites_path=Path("var/openshell/eval/eval-live-20260716/prerequisites.json"),
    )

    checks = {item["check_id"]: item for item in report["checks"]}
    assert checks["immutable_write_denials"]["status"] == "no_go"
    assert checks["unknown_file_upload_denied"]["status"] == "no_go"
    assert checks["formal_delete_guard_evidence"]["status"] == "no_go"


def test_markdown_approval_string_is_not_review_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _, _, _ = _complete_project(tmp_path, monkeypatch)
    forged = root / "forged-review.md"
    forged.write_text("评审结论：`批准灰度`\nreview_decision: approved\n", encoding="utf-8")

    report = module.build_report(
        project_root=root,
        ab_summary_path=Path("var/openshell/eval/eval-live-20260716/summary.json"),
        ab_prerequisites_path=Path("var/openshell/eval/eval-live-20260716/prerequisites.json"),
        review_record_path=forged.relative_to(root),
    )

    checks = {item["check_id"]: item for item in report["checks"]}
    assert checks["human_architecture_security_review"]["status"] == "no_go"


@pytest.mark.parametrize("mutation", ("evidence_digest", "checklist", "placeholder_reviewer"))
def test_review_requires_current_evidence_and_non_placeholder_human_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    root, _, _, _, _ = _complete_project(tmp_path, monkeypatch)
    review_path = root / "review.sanitized.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if mutation == "evidence_digest":
        review["evidence"]["formal_ab_summary_sha256"] = "0" * 64
    elif mutation == "checklist":
        review["checklist"]["quality_ab_approved"] = False
    else:
        review["reviewer"]["name"] = "<填写评审人>"
    _write_json(review_path, review)

    report = module.build_report(
        project_root=root,
        ab_summary_path=Path("var/openshell/eval/eval-live-20260716/summary.json"),
        ab_prerequisites_path=Path("var/openshell/eval/eval-live-20260716/prerequisites.json"),
        review_record_path=review_path.relative_to(root),
    )

    checks = {item["check_id"]: item for item in report["checks"]}
    assert checks["human_architecture_security_review"]["status"] == "no_go"


def test_docs_gate_requires_readiness_bound_git_index_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _, _, _ = _complete_project(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "_runbook_index_digest", lambda _root: "f" * 64)

    report = _completion_report(root)

    checks = {item["check_id"]: item for item in report["checks"]}
    assert checks["docs_and_audit_complete"]["status"] == "no_go"


def test_runbook_digest_binds_stage_zero_blobs_not_dirty_worktree(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    for relative in module.REQUIRED_RUNBOOKS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {relative.as_posix()}\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", *[item.as_posix() for item in module.REQUIRED_RUNBOOKS]], cwd=root, check=True)

    first = module._runbook_index_digest(root)
    assert first is not None
    changed = root / module.REQUIRED_RUNBOOKS[0]
    changed.write_text("dirty worktree content\n", encoding="utf-8")
    assert module._runbook_index_digest(root) == first

    subprocess.run(["git", "add", "--", module.REQUIRED_RUNBOOKS[0].as_posix()], cwd=root, check=True)
    assert module._runbook_index_digest(root) != first


def test_architecture_review_schema_accepts_strict_approved_shape() -> None:
    verification = {
        "service_preflight_sha256": "1" * 64,
        "formal_ab_summary_sha256": "2" * 64,
        "formal_fallback_drill_sha256": "7" * 64,
        "formal_host_rollback_sha256": "3" * 64,
        "formal_delete_guard_sha256": "4" * 64,
        "formal_egress_sandbox_sha256": "5" * 64,
        "formal_structured_audit_sha256": "6" * 64,
    }
    payload = _review_payload(
        reviewed_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        readiness_sha256="0" * 64,
        verification=verification,
    )
    schema = json.loads(
        (ROOT / "infra/openshell/schemas/architecture-security-review.schema.json").read_text(encoding="utf-8")
    )

    Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(payload)


def _completion_report(root: Path) -> dict[str, object]:
    _refresh_review(root)
    return module.build_report(
        project_root=root,
        ab_summary_path=Path("var/openshell/eval/eval-live-20260716/summary.json"),
        ab_prerequisites_path=Path("var/openshell/eval/eval-live-20260716/prerequisites.json"),
        review_record_path=Path("review.sanitized.json"),
    )


def _refresh_review(root: Path) -> None:
    readiness_path = root / module.DEFAULT_READINESS
    readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
    verification = readiness["verification"]
    assert isinstance(verification, dict)
    _write_json(
        root / "review.sanitized.json",
        _review_payload(
            reviewed_at=readiness["generated_at"],
            readiness_sha256=hashlib.sha256(readiness_path.read_bytes()).hexdigest(),
            verification=verification,
        ),
    )


def _rewrite_memory_binding(
    root: Path,
    readiness: dict[str, object],
    payload: dict[str, object],
) -> None:
    path = root / module.DEFAULT_MEMORY_WRITE_EVIDENCE
    _write_json(path, payload, mode=0o600)
    verification = readiness["verification"]
    assert isinstance(verification, dict)
    verification["memory_write_evidence_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    _write_json(root / module.DEFAULT_READINESS, readiness)


@pytest.mark.parametrize("binding_corruption", ("path", "digest"))
def test_memory_evidence_requires_readiness_path_and_digest_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    binding_corruption: str,
) -> None:
    root, _, _, readiness, _ = _complete_project(tmp_path, monkeypatch)
    verification = readiness["verification"]
    assert isinstance(verification, dict)
    if binding_corruption == "path":
        verification["memory_write_evidence"] = "artifacts/openshell/v0.6/unbound-memory.sanitized.json"
    else:
        verification["memory_write_evidence_sha256"] = "f" * 64
    _write_json(root / module.DEFAULT_READINESS, readiness)

    report = _completion_report(root)
    checks = {item["check_id"]: item for item in report["checks"]}

    assert report["decision"] == "NO_GO"
    assert checks["normal_analysis_and_memory_writes"]["status"] == "no_go"
    assert "normal_write_evidence_missing" in report["blockers"]
    assert "reproducible_evidence_missing" in report["blockers"]


def test_missing_memory_evidence_is_no_go_not_configuration_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _, _, _ = _complete_project(tmp_path, monkeypatch)
    (root / module.DEFAULT_MEMORY_WRITE_EVIDENCE).unlink()

    report = _completion_report(root)

    assert report["decision"] == "NO_GO"
    assert "normal_write_evidence_missing" in report["blockers"]
    assert "reproducible_evidence_missing" in report["blockers"]


@pytest.mark.parametrize(
    "corruption",
    ("schema", "operation", "residual", "contract_digest", "timestamp_overflow"),
)
def test_memory_evidence_tampering_fails_closed_even_when_digest_is_rebound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    root, _, _, readiness, _ = _complete_project(tmp_path, monkeypatch)
    path = root / module.DEFAULT_MEMORY_WRITE_EVIDENCE
    payload = json.loads(path.read_text(encoding="utf-8"))
    if corruption == "schema":
        payload["schema_version"] = "siq.openshell.memory-write-evidence.v0"
    elif corruption == "operation":
        payload["backends"]["milvus"]["operation_outcomes"]["primary_market"]["search"] = False
    elif corruption == "residual":
        payload["backends"]["postgresql"]["residual_count"] = 1
    elif corruption == "contract_digest":
        payload["contract_binding"]["boundary_contract_sha256"] = "e" * 64
    else:
        huge = 10**100
        payload["evidence_window"] = {"started_at_unix": huge - 1, "completed_at_unix": huge}
        for backend in ("postgresql", "milvus"):
            payload["backends"][backend]["captured_at_unix"] = huge - 1
            payload["backends"][backend]["completed_at_unix"] = huge
    _rewrite_memory_binding(root, readiness, payload)

    report = _completion_report(root)

    assert report["decision"] == "NO_GO"
    assert "normal_write_evidence_missing" in report["blockers"]
    assert "reproducible_evidence_missing" in report["blockers"]


def test_memory_evidence_cannot_be_newer_than_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _, readiness, _ = _complete_project(tmp_path, monkeypatch)
    path = root / module.DEFAULT_MEMORY_WRITE_EVIDENCE
    payload = json.loads(path.read_text(encoding="utf-8"))
    for key in ("started_at_unix", "completed_at_unix"):
        payload["evidence_window"][key] += 1
    for backend in ("postgresql", "milvus"):
        payload["backends"][backend]["captured_at_unix"] += 1
        payload["backends"][backend]["completed_at_unix"] += 1
    _rewrite_memory_binding(root, readiness, payload)

    report = _completion_report(root)

    assert report["decision"] == "NO_GO"
    assert "normal_write_evidence_missing" in report["blockers"]


def test_historical_memory_evidence_has_no_wall_clock_ttl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _, readiness, _ = _complete_project(tmp_path, monkeypatch)
    path = root / module.DEFAULT_MEMORY_WRITE_EVIDENCE
    payload = json.loads(path.read_text(encoding="utf-8"))
    offset = 365 * 24 * 60 * 60
    for key in ("started_at_unix", "completed_at_unix"):
        payload["evidence_window"][key] -= offset
    for backend in ("postgresql", "milvus"):
        payload["backends"][backend]["captured_at_unix"] -= offset
        payload["backends"][backend]["completed_at_unix"] -= offset
    _rewrite_memory_binding(root, readiness, payload)

    report = _completion_report(root)

    assert report["decision"] == "GO"


def test_memory_evidence_cannot_replace_normal_analysis_write_condition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _, readiness, _ = _complete_project(tmp_path, monkeypatch)
    controls = readiness["security_controls"]
    assert isinstance(controls, dict)
    controls["task_analysis_path_writable"] = False
    _write_json(root / module.DEFAULT_READINESS, readiness)

    report = _completion_report(root)
    checks = {item["check_id"]: item for item in report["checks"]}

    assert checks["normal_analysis_and_memory_writes"]["status"] == "no_go"
    assert checks["normal_analysis_and_memory_writes"]["error_code"] == "normal_write_evidence_missing"


def test_memory_evidence_cli_has_fixed_sanitized_default() -> None:
    args = module._parser().parse_args([])

    assert args.memory_write_evidence == module.DEFAULT_MEMORY_WRITE_EVIDENCE


def test_cli_selected_memory_evidence_path_requires_and_honors_readiness_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _, readiness, _ = _complete_project(tmp_path, monkeypatch)
    source = root / module.DEFAULT_MEMORY_WRITE_EVIDENCE
    custom = root / "artifacts/openshell/v0.6/archive/memory-write-evidence.sanitized.json"
    custom.parent.mkdir(parents=True)
    source.rename(custom)
    verification = readiness["verification"]
    assert isinstance(verification, dict)
    verification["memory_write_evidence"] = custom.relative_to(root).as_posix()
    verification["memory_write_evidence_sha256"] = hashlib.sha256(custom.read_bytes()).hexdigest()
    _write_json(root / module.DEFAULT_READINESS, readiness)
    _refresh_review(root)

    without_cli_path = _completion_report(root)
    with_cli_path = module.build_report(
        project_root=root,
        memory_write_evidence_path=custom.relative_to(root),
        ab_summary_path=Path("var/openshell/eval/eval-live-20260716/summary.json"),
        ab_prerequisites_path=Path("var/openshell/eval/eval-live-20260716/prerequisites.json"),
        review_record_path=Path("review.sanitized.json"),
    )

    assert without_cli_path["decision"] == "NO_GO"
    assert with_cli_path["decision"] == "GO"


def _formal_fixture_path(root: Path, evidence_kind: str) -> Path:
    return root / (
        module.DEFAULT_FORMAL_HOST_ROLLBACK if evidence_kind == "rollback" else module.DEFAULT_FORMAL_DELETE_GUARD
    )


def _rewrite_formal_binding(
    root: Path,
    readiness: dict[str, object],
    evidence_kind: str,
    payload: dict[str, object],
) -> None:
    path = _formal_fixture_path(root, evidence_kind)
    _write_json(path, payload)
    verification = readiness["verification"]
    assert isinstance(verification, dict)
    digest_key = "formal_host_rollback_sha256" if evidence_kind == "rollback" else "formal_delete_guard_sha256"
    verification[digest_key] = hashlib.sha256(path.read_bytes()).hexdigest()
    _write_json(root / module.DEFAULT_READINESS, readiness)


@pytest.mark.parametrize("evidence_kind", ("rollback", "delete_guard"))
@pytest.mark.parametrize("binding_corruption", ("path", "digest"))
def test_formal_evidence_requires_readiness_path_and_digest_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    evidence_kind: str,
    binding_corruption: str,
) -> None:
    root, _, _, readiness, _ = _complete_project(tmp_path, monkeypatch)
    verification = readiness["verification"]
    assert isinstance(verification, dict)
    if evidence_kind == "rollback":
        path_key = "formal_host_rollback_evidence"
        digest_key = "formal_host_rollback_sha256"
        expected_blocker = "formal_host_rollback_missing"
    else:
        path_key = "formal_delete_guard_evidence"
        digest_key = "formal_delete_guard_sha256"
        expected_blocker = "formal_delete_guard_evidence_missing"
    if binding_corruption == "path":
        verification[path_key] = "artifacts/openshell/v0.6/other.sanitized.json"
    else:
        verification[digest_key] = "f" * 64
    _write_json(root / module.DEFAULT_READINESS, readiness)

    report = _completion_report(root)

    assert report["decision"] == "NO_GO"
    assert expected_blocker in report["blockers"]
    assert "reproducible_evidence_missing" in report["blockers"]


def test_cli_selected_formal_evidence_paths_still_require_readiness_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _, readiness, _ = _complete_project(tmp_path, monkeypatch)
    rollback_source = root / module.DEFAULT_FORMAL_HOST_ROLLBACK
    delete_source = root / module.DEFAULT_FORMAL_DELETE_GUARD
    rollback_custom = root / "artifacts/openshell/v0.6/archive/rollback.sanitized.json"
    delete_custom = root / "artifacts/openshell/v0.6/archive/delete.sanitized.json"
    rollback_custom.parent.mkdir(parents=True)
    rollback_source.rename(rollback_custom)
    delete_source.rename(delete_custom)
    verification = readiness["verification"]
    assert isinstance(verification, dict)
    verification["formal_host_rollback_evidence"] = rollback_custom.relative_to(root).as_posix()
    verification["formal_host_rollback_sha256"] = hashlib.sha256(rollback_custom.read_bytes()).hexdigest()
    verification["formal_delete_guard_evidence"] = delete_custom.relative_to(root).as_posix()
    verification["formal_delete_guard_sha256"] = hashlib.sha256(delete_custom.read_bytes()).hexdigest()
    _write_json(root / module.DEFAULT_READINESS, readiness)
    _refresh_review(root)

    without_cli_paths = _completion_report(root)
    with_cli_paths = module.build_report(
        project_root=root,
        formal_host_rollback_path=rollback_custom.relative_to(root),
        formal_delete_guard_path=delete_custom.relative_to(root),
        ab_summary_path=Path("var/openshell/eval/eval-live-20260716/summary.json"),
        ab_prerequisites_path=Path("var/openshell/eval/eval-live-20260716/prerequisites.json"),
        review_record_path=Path("review.sanitized.json"),
    )

    assert without_cli_paths["decision"] == "NO_GO"
    assert with_cli_paths["decision"] == "GO"


@pytest.mark.parametrize(
    ("evidence_kind", "corruption"),
    (
        ("rollback", "schema"),
        ("rollback", "receipt_tamper"),
        ("rollback", "cleanup_false"),
        ("delete_guard", "schema"),
        ("delete_guard", "restore_tamper"),
        ("delete_guard", "cleanup_false"),
        ("delete_guard", "missing_case"),
    ),
)
def test_formal_evidence_content_tamper_remains_no_go_even_when_digest_is_rebound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    evidence_kind: str,
    corruption: str,
) -> None:
    root, _, _, readiness, _ = _complete_project(tmp_path, monkeypatch)
    path = _formal_fixture_path(root, evidence_kind)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if corruption == "schema":
        payload["schema_version"] = "siq.openshell.fabricated.v1"
    elif corruption == "receipt_tamper":
        payload["host_identity"]["after_receipt_sha256"] = "e" * 64
    elif corruption == "restore_tamper":
        payload["cases"][0]["restored_file_count"] = 500
    elif corruption == "missing_case":
        payload["cases"].pop()
    elif evidence_kind == "rollback":
        payload["cleanup"]["sandbox_deleted"] = False
    else:
        payload["cleanup"]["snapshot_artifacts_removed"] = False
    _rewrite_formal_binding(root, readiness, evidence_kind, payload)

    report = _completion_report(root)

    assert report["decision"] == "NO_GO"
    expected = "formal_host_rollback_missing" if evidence_kind == "rollback" else "formal_delete_guard_evidence_missing"
    assert expected in report["blockers"]


@pytest.mark.parametrize("evidence_kind", ("rollback", "delete_guard"))
def test_missing_formal_evidence_file_is_a_no_go_result_not_a_configuration_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    evidence_kind: str,
) -> None:
    root, _, _, _, _ = _complete_project(tmp_path, monkeypatch)
    _formal_fixture_path(root, evidence_kind).unlink()

    report = _completion_report(root)

    assert report["decision"] == "NO_GO"
    expected = "formal_host_rollback_missing" if evidence_kind == "rollback" else "formal_delete_guard_evidence_missing"
    assert expected in report["blockers"]


def test_hand_written_passed_strings_cannot_replace_formal_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _, readiness, _ = _complete_project(tmp_path, monkeypatch)
    verification = readiness["verification"]
    assert isinstance(verification, dict)
    verification["formal_host_rollback"] = "passed"
    verification["formal_host_rollback_evidence"] = "passed"
    verification["formal_delete_guard_evidence"] = "passed"
    _write_json(root / module.DEFAULT_READINESS, readiness)

    report = _completion_report(root)

    assert report["decision"] == "NO_GO"
    assert "formal_host_rollback_missing" in report["blockers"]
    assert "formal_delete_guard_evidence_missing" in report["blockers"]


def test_formal_evidence_is_scanned_by_the_sanitizer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _, _, _ = _complete_project(tmp_path, monkeypatch)
    rejected = (root / module.DEFAULT_FORMAL_HOST_ROLLBACK).resolve()
    real_scan = module.check_sanitized_artifacts.scan_paths

    def reject_rollback(paths, **kwargs):
        requested = [Path(path).resolve() for path in paths]
        return [object()] if rejected in requested else real_scan(paths, **kwargs)

    monkeypatch.setattr(module.check_sanitized_artifacts, "scan_paths", reject_rollback)

    report = _completion_report(root)

    assert report["decision"] == "NO_GO"
    assert "formal_host_rollback_missing" in report["blockers"]


def test_historical_formal_evidence_has_no_24_hour_ttl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _, readiness, _ = _complete_project(tmp_path, monkeypatch)
    historical = (
        (datetime.now(timezone.utc) - timedelta(days=365)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    for evidence_kind in ("rollback", "delete_guard"):
        path = _formal_fixture_path(root, evidence_kind)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["generated_at"] = historical
        _rewrite_formal_binding(root, readiness, evidence_kind, payload)

    report = _completion_report(root)

    assert report["decision"] == "GO"


@pytest.mark.parametrize("evidence_kind", ("rollback", "delete_guard"))
def test_formal_evidence_cannot_be_newer_than_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    evidence_kind: str,
) -> None:
    root, _, _, readiness, _ = _complete_project(tmp_path, monkeypatch)
    path = _formal_fixture_path(root, evidence_kind)
    payload = json.loads(path.read_text(encoding="utf-8"))
    readiness_at = datetime.fromisoformat(str(readiness["generated_at"]).replace("Z", "+00:00"))
    payload["generated_at"] = (readiness_at + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    _rewrite_formal_binding(root, readiness, evidence_kind, payload)

    report = _completion_report(root)

    assert report["decision"] == "NO_GO"


@pytest.mark.parametrize(
    ("schema_path", "schema_version"),
    (
        (
            "infra/openshell/schemas/formal-host-rollback-evidence.schema.json",
            module.FORMAL_HOST_ROLLBACK_SCHEMA,
        ),
        (
            "infra/openshell/schemas/formal-delete-guard-evidence.schema.json",
            module.FORMAL_DELETE_GUARD_SCHEMA,
        ),
        (
            "infra/openshell/schemas/formal-egress-sandbox-evidence.schema.json",
            module.FORMAL_EGRESS_SANDBOX_SCHEMA,
        ),
        (
            "infra/openshell/schemas/formal-structured-audit-evidence.schema.json",
            module.FORMAL_STRUCTURED_AUDIT_SCHEMA,
        ),
    ),
)
def test_formal_evidence_schemas_are_strict(schema_path: str, schema_version: str) -> None:
    schema = json.loads((ROOT / schema_path).read_text(encoding="utf-8"))

    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"] == {"const": schema_version}


def test_new_formal_security_schemas_accept_only_the_strict_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _, _, _ = _complete_project(tmp_path, monkeypatch)
    pairs = (
        (
            "infra/openshell/schemas/formal-egress-sandbox-evidence.schema.json",
            root / module.DEFAULT_FORMAL_EGRESS_SANDBOX,
        ),
        (
            "infra/openshell/schemas/formal-structured-audit-evidence.schema.json",
            root / module.DEFAULT_FORMAL_STRUCTURED_AUDIT,
        ),
    )
    for schema_relative, evidence_path in pairs:
        schema = json.loads((ROOT / schema_relative).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
        validator.validate(payload)
        payload["fabricated_pass"] = True
        assert list(validator.iter_errors(payload))
        payload.pop("fabricated_pass")
        if payload["schema_version"] == module.FORMAL_EGRESS_SANDBOX_SCHEMA:
            payload["cases"][0]["decision"] = "deny"
        else:
            payload["content_absence"]["contains_target_values"] = True
        assert list(validator.iter_errors(payload))


@pytest.mark.parametrize("evidence_kind", ("host_component", "formal_egress", "formal_audit"))
@pytest.mark.parametrize("binding_corruption", ("path", "digest"))
def test_formal_egress_and_audit_require_readiness_path_and_digest_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    evidence_kind: str,
    binding_corruption: str,
) -> None:
    root, _, _, readiness, _ = _complete_project(tmp_path, monkeypatch)
    keys = {
        "host_component": ("host_egress_component_evidence", "host_egress_component_sha256"),
        "formal_egress": ("formal_egress_sandbox_evidence", "formal_egress_sandbox_sha256"),
        "formal_audit": ("formal_structured_audit_evidence", "formal_structured_audit_sha256"),
    }
    verification = readiness["verification"]
    assert isinstance(verification, dict)
    path_key, digest_key = keys[evidence_kind]
    if binding_corruption == "path":
        verification[path_key] = "artifacts/openshell/v0.6/unbound.sanitized.json"
    else:
        verification[digest_key] = "f" * 64
    _write_json(root / module.DEFAULT_READINESS, readiness)

    report = _completion_report(root)

    assert report["decision"] == "NO_GO"
    assert "upload_guard_missing" in report["blockers"]
    assert "reproducible_evidence_missing" in report["blockers"]
    if evidence_kind == "formal_audit":
        assert "docs_or_audit_evidence_missing" in report["blockers"]


def test_host_component_alone_is_explicitly_ineligible_for_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _, _, _ = _complete_project(tmp_path, monkeypatch)
    (root / module.DEFAULT_FORMAL_EGRESS_SANDBOX).unlink()
    (root / module.DEFAULT_FORMAL_STRUCTURED_AUDIT).unlink()

    report = _completion_report(root)

    assert "upload_guard_missing" in report["blockers"]
    assert "docs_or_audit_evidence_missing" in report["blockers"]
    assert "reproducible_evidence_missing" in report["blockers"]


def test_docs_and_audit_cannot_pass_without_formal_business_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _, _, _ = _complete_project(tmp_path, monkeypatch)
    (root / module.DEFAULT_FORMAL_STRUCTURED_AUDIT).unlink()

    report = _completion_report(root)
    checks = {item["check_id"]: item for item in report["checks"]}

    assert checks["docs_and_audit_complete"]["status"] == "no_go"
    assert checks["docs_and_audit_complete"]["error_code"] == "docs_or_audit_evidence_missing"


@pytest.mark.parametrize(
    ("evidence_kind", "corruption"),
    (
        ("formal_egress", "scope"),
        ("formal_egress", "host_component_eligibility"),
        ("formal_egress", "duplicate_case_event"),
        ("formal_audit", "transaction"),
        ("formal_audit", "raw_content"),
        ("formal_audit", "security_case_binding"),
    ),
)
def test_formal_egress_or_audit_tampering_stays_no_go_after_digest_rebinding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    evidence_kind: str,
    corruption: str,
) -> None:
    root, _, _, readiness, _ = _complete_project(tmp_path, monkeypatch)
    path = root / (
        module.DEFAULT_FORMAL_EGRESS_SANDBOX
        if evidence_kind == "formal_egress"
        else module.DEFAULT_FORMAL_STRUCTURED_AUDIT
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if corruption == "scope":
        payload["scope"] = "host_egress_broker"
    elif corruption == "host_component_eligibility":
        payload["host_egress_component"]["eligible_for_completion"] = True
    elif corruption == "duplicate_case_event":
        payload["cases"][0]["audit_record_sha256"] = payload["cases"][1]["audit_record_sha256"]
    elif corruption == "transaction":
        payload["transaction"]["policy_sha256"] = _digest("different-policy")
    elif corruption == "raw_content":
        payload["content_absence"]["contains_request_or_response_content"] = True
    else:
        payload["security_case_event_sha256"][0] = _digest("unbound-security-event")
    _write_json(path, payload)
    verification = readiness["verification"]
    assert isinstance(verification, dict)
    digest_key = (
        "formal_egress_sandbox_sha256" if evidence_kind == "formal_egress" else "formal_structured_audit_sha256"
    )
    verification[digest_key] = hashlib.sha256(path.read_bytes()).hexdigest()
    _write_json(root / module.DEFAULT_READINESS, readiness)

    report = _completion_report(root)

    assert report["decision"] == "NO_GO"
    assert "upload_guard_missing" in report["blockers"]
    if evidence_kind == "formal_audit":
        assert "docs_or_audit_evidence_missing" in report["blockers"]


def test_formal_audit_must_not_predate_the_bound_egress_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _, readiness, _ = _complete_project(tmp_path, monkeypatch)
    path = root / module.DEFAULT_FORMAL_STRUCTURED_AUDIT
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["generated_at"] = (
        (datetime.fromisoformat(str(payload["generated_at"]).replace("Z", "+00:00")) - timedelta(seconds=1))
        .isoformat()
        .replace("+00:00", "Z")
    )
    _write_json(path, payload)
    verification = readiness["verification"]
    assert isinstance(verification, dict)
    verification["formal_structured_audit_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    _write_json(root / module.DEFAULT_READINESS, readiness)

    report = _completion_report(root)

    assert "docs_or_audit_evidence_missing" in report["blockers"]
    assert "upload_guard_missing" in report["blockers"]


def test_cli_exposes_fixed_formal_egress_and_audit_defaults() -> None:
    args = module._parser().parse_args([])

    assert args.formal_filesystem_boundary_evidence == module.DEFAULT_FORMAL_FILESYSTEM_BOUNDARY
    assert args.host_egress_component == module.DEFAULT_HOST_EGRESS_COMPONENT
    assert args.formal_egress_sandbox_evidence == module.DEFAULT_FORMAL_EGRESS_SANDBOX
    assert args.formal_structured_audit_evidence == module.DEFAULT_FORMAL_STRUCTURED_AUDIT


def test_ab_summary_cannot_pass_without_bound_provenance_prerequisites(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _, _, _ = _complete_project(tmp_path, monkeypatch)

    report = module.build_report(
        project_root=root,
        ab_summary_path=Path("var/openshell/eval/eval-live-20260716/summary.json"),
        review_record_path=Path("review.sanitized.json"),
    )

    assert report["decision"] == "NO_GO"
    assert "formal_ab_missing_or_failed" in report["blockers"]


def test_fabricated_prerequisite_provenance_cannot_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _, readiness, summary = _complete_project(tmp_path, monkeypatch)
    path = root / "var/openshell/eval/eval-live-20260716/prerequisites.json"
    prerequisites = json.loads(path.read_text(encoding="utf-8"))
    prerequisites["provenance"]["arms_match"] = False
    _write_json(path, prerequisites)
    verification = readiness["verification"]
    assert isinstance(verification, dict)
    prerequisite_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    verification["formal_ab_prerequisites_sha256"] = prerequisite_digest
    summary["prerequisites_sha256"] = prerequisite_digest
    _rewrite_summary_binding(root, readiness, summary)

    report = module.build_report(
        project_root=root,
        ab_summary_path=Path("var/openshell/eval/eval-live-20260716/summary.json"),
        ab_prerequisites_path=Path("var/openshell/eval/eval-live-20260716/prerequisites.json"),
        review_record_path=Path("review.sanitized.json"),
    )

    assert report["decision"] == "NO_GO"
    assert "formal_ab_missing_or_failed" in report["blockers"]


def test_legacy_v2_prerequisite_or_missing_live_probe_cannot_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _, readiness, summary = _complete_project(tmp_path, monkeypatch)
    path = root / "var/openshell/eval/eval-live-20260716/prerequisites.json"
    prerequisites = json.loads(path.read_text(encoding="utf-8"))
    prerequisites["schema_version"] = "siq.openshell.siq-analysis-ab-prerequisites.v2"
    prerequisites["network_probe_performed"] = False
    _rewrite_prerequisite_cross_binding(root, readiness, summary, prerequisites)

    report = _completion_report(root)

    assert report["decision"] == "NO_GO"
    assert "formal_ab_missing_or_failed" in report["blockers"]


def _rewrite_summary_binding(root: Path, readiness: dict[str, object], summary: dict[str, object]) -> None:
    summary_path = root / "var/openshell/eval/eval-live-20260716/summary.json"
    _write_json(summary_path, summary)
    verification = readiness["verification"]
    assert isinstance(verification, dict)
    verification["formal_ab_summary_sha256"] = hashlib.sha256(summary_path.read_bytes()).hexdigest()
    _write_json(root / "artifacts/openshell/v0.6/readiness.json", readiness)


def _rewrite_prerequisite_cross_binding(
    root: Path,
    readiness: dict[str, object],
    summary: dict[str, object],
    prerequisites: dict[str, object],
) -> None:
    prerequisites_path = root / "var/openshell/eval/eval-live-20260716/prerequisites.json"
    _write_json(prerequisites_path, prerequisites)
    digest = hashlib.sha256(prerequisites_path.read_bytes()).hexdigest()
    summary["prerequisites_path"] = prerequisites_path.relative_to(root).as_posix()
    summary["prerequisites_sha256"] = digest
    verification = readiness["verification"]
    assert isinstance(verification, dict)
    verification["formal_ab_prerequisites_evidence"] = prerequisites_path.relative_to(root).as_posix()
    verification["formal_ab_prerequisites_sha256"] = digest
    _rewrite_summary_binding(root, readiness, summary)
    summary_path = root / "var/openshell/eval/eval-live-20260716/summary.json"
    fallback_path = root / module.DEFAULT_FORMAL_FALLBACK_DRILL
    fallback = json.loads(fallback_path.read_text(encoding="utf-8"))
    fallback["normal_summary_sha256"] = hashlib.sha256(summary_path.read_bytes()).hexdigest()
    fallback["prerequisites_sha256"] = digest
    fallback["provenance_sha256"] = prerequisites["provenance"]["sha256"]
    _write_json(fallback_path, fallback)
    verification["formal_fallback_drill_sha256"] = hashlib.sha256(fallback_path.read_bytes()).hexdigest()
    _write_json(root / module.DEFAULT_READINESS, readiness)


@pytest.mark.parametrize(
    "summary_corruption",
    ("missing_path", "missing_digest", "wrong_path", "wrong_digest"),
)
def test_ab_summary_requires_exact_prerequisite_path_and_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    summary_corruption: str,
) -> None:
    root, _, _, readiness, summary = _complete_project(tmp_path, monkeypatch)
    if summary_corruption == "missing_path":
        summary.pop("prerequisites_path")
    elif summary_corruption == "missing_digest":
        summary.pop("prerequisites_sha256")
    elif summary_corruption == "wrong_path":
        summary["prerequisites_path"] = "var/openshell/eval/other/prerequisites.json"
    else:
        summary["prerequisites_sha256"] = "f" * 64
    _rewrite_summary_binding(root, readiness, summary)

    report = _completion_report(root)

    assert report["decision"] == "NO_GO"
    assert "formal_ab_missing_or_failed" in report["blockers"]


def test_cli_prerequisite_path_must_match_summary_repo_relative_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _, readiness, _ = _complete_project(tmp_path, monkeypatch)
    original = root / "var/openshell/eval/eval-live-20260716/prerequisites.json"
    alternate = root / "var/openshell/eval/eval-live-20260716/alternate-prerequisites.json"
    original.rename(alternate)
    verification = readiness["verification"]
    assert isinstance(verification, dict)
    verification["formal_ab_prerequisites_evidence"] = alternate.relative_to(root).as_posix()
    verification["formal_ab_prerequisites_sha256"] = hashlib.sha256(alternate.read_bytes()).hexdigest()
    _write_json(root / module.DEFAULT_READINESS, readiness)

    report = module.build_report(
        project_root=root,
        ab_summary_path=Path("var/openshell/eval/eval-live-20260716/summary.json"),
        ab_prerequisites_path=alternate.relative_to(root),
        review_record_path=Path("review.sanitized.json"),
    )

    assert report["decision"] == "NO_GO"
    assert "formal_ab_missing_or_failed" in report["blockers"]


def test_v1_prerequisite_is_rejected_even_when_all_digests_are_rebound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _, readiness, summary = _complete_project(tmp_path, monkeypatch)
    path = root / "var/openshell/eval/eval-live-20260716/prerequisites.json"
    prerequisites = json.loads(path.read_text(encoding="utf-8"))
    prerequisites["schema_version"] = "siq.openshell.siq-analysis-ab-prerequisites.v1"
    _rewrite_prerequisite_cross_binding(root, readiness, summary, prerequisites)

    report = _completion_report(root)

    assert report["decision"] == "NO_GO"
    assert "formal_ab_missing_or_failed" in report["blockers"]


@pytest.mark.parametrize(
    "prerequisite_corruption",
    ("missing_evidence", "binding_digest", "missing_generated_at", "expiry_order", "extra_top_level"),
)
def test_v2_prerequisite_shape_tamper_is_rejected_after_cross_rebinding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prerequisite_corruption: str,
) -> None:
    root, _, _, readiness, summary = _complete_project(tmp_path, monkeypatch)
    path = root / "var/openshell/eval/eval-live-20260716/prerequisites.json"
    prerequisites = json.loads(path.read_text(encoding="utf-8"))
    if prerequisite_corruption == "missing_evidence":
        prerequisites["evidence"].pop("broker_report")
    elif prerequisite_corruption == "binding_digest":
        prerequisites["evidence"]["service_report"]["sha256"] = "not-a-sha256"
    elif prerequisite_corruption == "missing_generated_at":
        prerequisites.pop("generated_at")
    elif prerequisite_corruption == "expiry_order":
        prerequisites["expires_at"] = prerequisites["generated_at"]
    else:
        prerequisites["passed"] = True
    _rewrite_prerequisite_cross_binding(root, readiness, summary, prerequisites)

    report = _completion_report(root)

    assert report["decision"] == "NO_GO"
    assert "formal_ab_missing_or_failed" in report["blockers"]


def test_expired_v2_prerequisite_remains_valid_historical_release_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _, readiness, summary = _complete_project(tmp_path, monkeypatch)
    path = root / "var/openshell/eval/eval-live-20260716/prerequisites.json"
    prerequisites = json.loads(path.read_text(encoding="utf-8"))

    def shift_back(value: str) -> str:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00")) - timedelta(days=365)
        return timestamp.isoformat().replace("+00:00", "Z")

    prerequisites["generated_at"] = shift_back(prerequisites["generated_at"])
    prerequisites["expires_at"] = shift_back(prerequisites["expires_at"])
    for binding in prerequisites["evidence"].values():
        binding["generated_at"] = shift_back(binding["generated_at"])
        binding["expires_at"] = shift_back(binding["expires_at"])
    _rewrite_prerequisite_cross_binding(root, readiness, summary, prerequisites)

    report = _completion_report(root)

    assert report["decision"] == "GO"


def test_v2_private_machine_paths_are_accepted_as_binding_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _, readiness, summary = _complete_project(tmp_path, monkeypatch)
    path = root / "var/openshell/eval/eval-live-20260716/prerequisites.json"
    prerequisites = json.loads(path.read_text(encoding="utf-8"))
    for name, binding in prerequisites["evidence"].items():
        binding["path"] = f"/home/redacted/siq/{name}.json"
    _rewrite_prerequisite_cross_binding(root, readiness, summary, prerequisites)

    report = _completion_report(root)

    assert report["decision"] == "GO"


@pytest.mark.parametrize(
    "corruption",
    ("execution_count", "arm_metric", "quality_reasons", "sanitization", "case_repetition"),
)
def test_fabricated_or_incomplete_ab_summary_is_no_go(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    root, _, _, readiness, summary = _complete_project(tmp_path, monkeypatch)
    if corruption == "execution_count":
        summary["execution_count"] = 2
    elif corruption == "arm_metric":
        arms = summary["arms"]
        assert isinstance(arms, dict) and isinstance(arms["openshell"], dict)
        arms["openshell"].pop("numeric_accuracy")
    elif corruption == "quality_reasons":
        quality = summary["quality_gate"]
        assert isinstance(quality, dict)
        quality["failure_reasons"] = ["invented_failure"]
    elif corruption == "sanitization":
        sanitization = summary["sanitization"]
        assert isinstance(sanitization, dict)
        sanitization["contains_raw_output"] = True
    else:
        summary["repetitions"] = 2
    _rewrite_summary_binding(root, readiness, summary)

    report = module.build_report(
        project_root=root,
        ab_summary_path=Path("var/openshell/eval/eval-live-20260716/summary.json"),
        ab_prerequisites_path=Path("var/openshell/eval/eval-live-20260716/prerequisites.json"),
        review_record_path=Path("review.sanitized.json"),
    )

    assert report["decision"] == "NO_GO"
    assert "formal_ab_missing_or_failed" in report["blockers"]
    assert "quality_ab_missing_or_failed" in report["blockers"]


def test_legitimate_quality_failure_remains_valid_evidence_but_cannot_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _, readiness, summary = _complete_project(tmp_path, monkeypatch)
    arms = summary["arms"]
    assert isinstance(arms, dict) and isinstance(arms["openshell"], dict)
    arms["openshell"]["task_success_rate"] = 0.5
    comparison, reasons = module.ab_eval.quality_comparison(
        arms["host"],
        arms["openshell"],
        case_count=int(summary["case_count"]),
        repetitions=int(summary["repetitions"]),
        require_fallback=False,
    )
    summary["comparison"] = comparison
    quality = summary["quality_gate"]
    assert isinstance(quality, dict)
    quality["passed"] = False
    quality["failure_reasons"] = reasons
    _rewrite_summary_binding(root, readiness, summary)

    assert module._validate_ab_summary(summary) is True
    report = module.build_report(
        project_root=root,
        ab_summary_path=Path("var/openshell/eval/eval-live-20260716/summary.json"),
        ab_prerequisites_path=Path("var/openshell/eval/eval-live-20260716/prerequisites.json"),
        review_record_path=Path("review.sanitized.json"),
    )

    assert report["decision"] == "NO_GO"
    assert "formal_ab_missing_or_failed" in report["blockers"]


@pytest.mark.parametrize("service_corruption", ("shallow", "decision", "missing_broker_service"))
def test_shallow_or_inconsistent_service_go_cannot_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service_corruption: str,
) -> None:
    root, _, service_path, readiness, _ = _complete_project(tmp_path, monkeypatch)
    service = _service_go()
    if service_corruption == "shallow":
        service = {"schema_version": module.SERVICE_SCHEMA, "decision": "GO", "passed": True}
    elif service_corruption == "decision":
        service["decision"] = "NO_GO"
    else:
        services = service["services"]
        assert isinstance(services, list)
        service["services"] = [item for item in services if item["service_id"] != "embedding"]
    _write_json(service_path, service)
    verification = readiness["verification"]
    assert isinstance(verification, dict)
    verification["service_preflight_sha256"] = hashlib.sha256(service_path.read_bytes()).hexdigest()
    _write_json(root / "artifacts/openshell/v0.6/readiness.json", readiness)

    report = module.build_report(
        project_root=root,
        ab_summary_path=Path("var/openshell/eval/eval-live-20260716/summary.json"),
        ab_prerequisites_path=Path("var/openshell/eval/eval-live-20260716/prerequisites.json"),
        review_record_path=Path("review.sanitized.json"),
    )

    assert report["decision"] == "NO_GO"
    assert "service_or_provider_preflight_no_go" in report["blockers"]


@pytest.mark.parametrize(
    "readiness_state",
    ("stale", "no_go_with_true_controls", "wrong_service_digest", "wrong_ab_digest"),
)
def test_stale_or_inconsistent_readiness_cannot_promote_shallow_booleans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    readiness_state: str,
) -> None:
    root, _, _, readiness, _ = _complete_project(tmp_path, monkeypatch)
    if readiness_state == "stale":
        readiness["generated_at"] = (
            (datetime.now(timezone.utc) - timedelta(days=2)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        )
    elif readiness_state == "no_go_with_true_controls":
        readiness["decision"] = "NO_GO"
        readiness["blockers"] = ["fabricated_blocker"]
    elif readiness_state == "wrong_service_digest":
        verification = readiness["verification"]
        assert isinstance(verification, dict)
        verification["service_preflight_sha256"] = "f" * 64
    else:
        verification = readiness["verification"]
        assert isinstance(verification, dict)
        verification["formal_ab_summary_sha256"] = "e" * 64
    _write_json(root / "artifacts/openshell/v0.6/readiness.json", readiness)

    report = module.build_report(
        project_root=root,
        ab_summary_path=Path("var/openshell/eval/eval-live-20260716/summary.json"),
        ab_prerequisites_path=Path("var/openshell/eval/eval-live-20260716/prerequisites.json"),
        review_record_path=Path("review.sanitized.json"),
    )

    assert report["decision"] == "NO_GO"
    assert report["passed_count"] < report["total_count"]
    assert "formal_ab_missing_or_failed" in report["blockers"]
