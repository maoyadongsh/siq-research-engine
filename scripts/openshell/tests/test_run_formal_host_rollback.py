from __future__ import annotations

import json
from dataclasses import asdict

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from scripts.openshell import run_formal_host_rollback as runner


def _before() -> dict[str, object]:
    return {
        "transaction_receipt_sha256": "1" * 64,
        "transaction_generation": 13,
        "manifest_sha256": "2" * 64,
        "sandbox_binding_sha256": "3" * 64,
        "host_receipt_sha256": "4" * 64,
        "run_id_sha256": "5" * 64,
        "sandbox_id_sha256": "6" * 64,
        "container_id_sha256": "7" * 64,
        "session_id_sha256": "5" * 64,
        "resource_receipts_sha256": "8" * 64,
        "image_sha256": "9" * 64,
        "policy_sha256": "a" * 64,
        "mount_plan_sha256": "b" * 64,
        "mount_contract_sha256": "c" * 64,
        "runtime_config_sha256": "d" * 64,
    }


def _terminal() -> runner.TerminalCapture:
    return runner.TerminalCapture(
        transaction_receipt_sha256="e" * 64,
        transaction_generation=24,
        resource_receipts_sha256="f" * 64,
        manifest_sha256="0" * 64,
        host_receipt_sha256="4" * 64,
        run_id_sha256="5" * 64,
        sandbox_id_sha256="6" * 64,
        container_id_sha256="7" * 64,
        image_sha256="9" * 64,
        policy_sha256="a" * 64,
        raw_mount_plan_sha256="b" * 64,
        mount_contract_sha256="c" * 64,
    )


def _lifecycle_result() -> dict[str, object]:
    return {
        "ok": True,
        "profile": "siq_analysis",
        "run_id": "formal-run",
        "status": "stopped",
        "runtime": "host",
        "host_runs_url": "http://127.0.0.1:18651/v1/runs",
        "host_runtime_unchanged": True,
        "host_receipt_sha256": "4" * 64,
        "publisher": {"status": "published"},
    }


def test_build_evidence_is_bound_to_terminal_and_producer(monkeypatch: pytest.MonkeyPatch) -> None:
    terminal = _terminal()
    sources = {
        "lifecycle_sha256": "1" * 64,
        "transaction_module_sha256": "2" * 64,
        "mount_contract_module_sha256": "3" * 64,
        "runner_sha256": "4" * 64,
        "wrapper_sha256": "5" * 64,
    }
    monkeypatch.setattr(runner, "_terminal_capture", lambda *_args, **_kwargs: terminal)
    monkeypatch.setattr(
        runner,
        "_stable_file",
        lambda path, **_kwargs: (
            (runner.REPO_ROOT / runner.SCHEMA_RELATIVE).read_bytes()
            if path == runner.REPO_ROOT / runner.SCHEMA_RELATIVE
            else bytes.fromhex(sources[
                {
                    runner.LIFECYCLE_RELATIVE: "lifecycle_sha256",
                    runner.TRANSACTION_RELATIVE: "transaction_module_sha256",
                    runner.MOUNT_CONTRACT_RELATIVE: "mount_contract_module_sha256",
                    runner.RUNNER_RELATIVE: "runner_sha256",
                    runner.ROLLBACK_WRAPPER_RELATIVE: "wrapper_sha256",
                }[path.relative_to(runner.REPO_ROOT)]
            ])
        ),
    )
    # The source helper hashes bytes. Replace the expected map with those hashes.
    current_sources = {
        key: runner._sha256(bytes.fromhex(value)) for key, value in sources.items()
    }
    raw = {
        "generated_at": "2026-07-16T12:00:00Z",
        "runtime_identifiers": {"run_id": "formal-run"},
        "before": _before(),
        "terminal": asdict(terminal),
        "lifecycle_result": _lifecycle_result(),
        "source_sha256": current_sources,
    }

    evidence = runner.build_evidence(
        project_root=runner.REPO_ROOT,
        raw=raw,
        raw_receipt_sha256="f" * 64,
    )

    schema = json.loads((runner.REPO_ROOT / runner.SCHEMA_RELATIVE).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(evidence)
    assert evidence["transaction"]["before_receipt_sha256"] != evidence["transaction"]["terminal_receipt_sha256"]
    assert evidence["host_identity"]["before_receipt_sha256"] == evidence["host_identity"]["after_receipt_sha256"]
    assert evidence["cleanup"]["publisher_index_published"] is True
    assert evidence["cleanup"]["publisher_receipt_sha256"] == runner._canonical_sha256(_lifecycle_result())
    assert "api_and_output_paths_unchanged" not in evidence["cleanup"]
    assert "formal-run" not in json.dumps(evidence, sort_keys=True)


def test_validate_rejects_same_running_and_terminal_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    schema = (runner.REPO_ROOT / runner.SCHEMA_RELATIVE).read_bytes()
    payload = {
        "schema_version": runner.SCHEMA_VERSION,
        "generated_at": "2026-07-16T12:00:00Z",
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
            "before_receipt_sha256": "1" * 64,
            "terminal_receipt_sha256": "1" * 64,
            "resource_receipts_sha256": "2" * 64,
            "run_id_sha256": "3" * 64,
            "sandbox_id_sha256": "4" * 64,
            "container_id_sha256": "5" * 64,
        },
        "host_identity": {
            "contract": "exact_receipt_before_and_after",
            "baseline_receipt_sha256": "6" * 64,
            "before_receipt_sha256": "6" * 64,
            "after_receipt_sha256": "6" * 64,
        },
        "cleanup": {
            "sandbox_deleted": True,
            "forward_port_released": True,
            "active_state_removed": True,
            "ephemeral_identity_removed": True,
            "transaction_finalized": True,
            "publisher_index_published": True,
            "publisher_receipt_sha256": "7" * 64,
        },
        "provenance": {
            "hermes_commit": runner.HERMES_COMMIT,
            **{key: str(index % 10) * 64 for index, key in enumerate((
                "image_sha256", "policy_sha256", "raw_mount_plan_sha256", "mount_contract_sha256",
                "lifecycle_sha256", "transaction_module_sha256", "mount_contract_module_sha256",
                "runner_sha256", "wrapper_sha256", "evidence_schema_sha256", "raw_receipt_sha256"
            ), start=1)},
        },
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

    with pytest.raises(runner.FormalHostRollbackError, match="rollback_evidence_binding_invalid"):
        runner.validate_evidence(payload, schema_bytes=schema)


def test_lifecycle_result_requires_successful_index_publication() -> None:
    base = {
        "ok": True,
        "profile": "siq_analysis",
        "run_id": "formal-run",
        "status": "stopped",
        "runtime": "host",
        "host_runs_url": "http://127.0.0.1:18651/v1/runs",
        "host_runtime_unchanged": True,
        "host_receipt_sha256": "a" * 64,
        "publisher": {"status": "published"},
    }

    assert runner._validate_lifecycle_result(
        base,
        run_id="formal-run",
        host_receipt_sha256="a" * 64,
    )["publisher"] == {"status": "published"}

    deferred = {**base, "publisher": {"status": "deferred", "error_code": "company_index_publish_failed"}}
    with pytest.raises(runner.FormalHostRollbackError, match="rollback_lifecycle_result_invalid"):
        runner._validate_lifecycle_result(
            deferred,
            run_id="formal-run",
            host_receipt_sha256="a" * 64,
        )
