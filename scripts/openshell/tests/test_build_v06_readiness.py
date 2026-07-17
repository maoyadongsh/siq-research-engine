from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.openshell import build_v06_readiness as builder

ROOT = Path(__file__).resolve().parents[3]


def _write_json(path: Path, payload: object, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(mode)


def _copy(source: Path, destination: Path, *, mode: int = 0o644) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())
    destination.chmod(mode)


def _service_go() -> dict[str, object]:
    services: list[dict[str, object]] = []
    for service_id, (port, requirement) in builder.completion.SERVICE_SPECS.items():
        protocol_spec = builder.completion.SERVICE_PROTOCOL_SPECS.get(service_id)
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
                    "contract": protocol_spec[0] if protocol_spec else "not_applicable",
                    "method": "GET" if protocol_spec else "",
                    "path": protocol_spec[1] if protocol_spec else "",
                    "checked": bool(protocol_spec),
                    "available": True if protocol_spec else None,
                    "status": "pass" if protocol_spec else "not_applicable",
                    "error_code": "",
                    "latency_ms": 1 if protocol_spec else None,
                    "http_status": 200 if protocol_spec else None,
                },
            }
        )
    security_checks = [
        {
            "check_id": check_id,
            "status": "pass",
            "proof_present": True,
            "proof_source": "proof_file",
            "error_code": "",
        }
        for check_id in sorted(builder.completion.SERVICE_SECURITY_CHECKS)
    ]
    return {
        "schema_version": builder.completion.SERVICE_SCHEMA,
        "decision": "GO",
        "passed": True,
        "probe_scope": {
            "protocol": builder.completion.SERVICE_PROTOCOL,
            "read_only": True,
            "host_alias_kind": "loopback",
            "http_method": "GET",
            "request_body_sent": False,
            "redirects_followed": False,
            "response_body_recorded": False,
        },
        "services": services,
        "security_checks": security_checks,
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


def _complete_provider_inventory() -> dict[str, object]:
    return {
        "schema_version": builder.PROVIDER_SCHEMA,
        "openshell_version": builder.OPENSHELL_VERSION,
        "gateway": builder.GATEWAY,
        "providers": [{"name": name, "state": "configured"} for name in sorted(builder.REQUIRED_PROVIDERS)],
    }


def test_exa_is_deferred_without_removing_its_future_provider_contract() -> None:
    assert "siq-exa-search" not in builder.REQUIRED_PROVIDERS
    assert builder.DEFERRED_PROVIDERS == {"siq-exa-search"}


def _broker_status() -> dict[str, object]:
    return {
        "schema_version": builder.BROKER_SCHEMA,
        "ok": True,
        "action": "status",
        "bridge": {"network": builder.GATEWAY, "alias": "host.openshell.internal"},
        "brokers": {
            "data": {"port": 18793, "state": "running", "request_identity_required": True},
            "egress": {"port": 18792, "state": "running", "request_identity_required": True},
        },
    }


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    generated_at: datetime,
) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    _copy(ROOT / builder.DEFAULT_BASELINE, root / builder.DEFAULT_BASELINE)
    _write_json(root / builder.DEFAULT_SERVICE, _service_go())
    _write_json(root / builder.DEFAULT_PROVIDER_INVENTORY, _complete_provider_inventory(), mode=0o600)
    _write_json(root / builder.DEFAULT_BROKER_STATUS, _broker_status(), mode=0o600)

    probe = json.loads((ROOT / builder.DEFAULT_PROVIDER_PROBE).read_text(encoding="utf-8"))
    probe["generated_at"] = builder._format_timestamp(generated_at - timedelta(minutes=1))
    _write_json(root / builder.DEFAULT_PROVIDER_PROBE, probe)

    memory_module = builder.completion.memory_write_evidence
    _copy(ROOT / memory_module.BOUNDARY_RELATIVE, root / memory_module.BOUNDARY_RELATIVE)
    _copy(ROOT / memory_module.EVIDENCE_SCHEMA_RELATIVE, root / memory_module.EVIDENCE_SCHEMA_RELATIVE)
    _copy(ROOT / builder.DEFAULT_MEMORY, root / builder.DEFAULT_MEMORY, mode=0o600)
    _copy(ROOT / builder.DEFAULT_HOST_EGRESS, root / builder.DEFAULT_HOST_EGRESS)

    recent = (generated_at - timedelta(seconds=10)).timestamp()
    for relative in (builder.DEFAULT_SERVICE, builder.DEFAULT_PROVIDER_INVENTORY, builder.DEFAULT_BROKER_STATUS):
        os.utime(root / relative, (recent, recent))

    runbook_a = Path("docs/runbooks/openshell/a.md")
    runbook_b = Path("docs/runbooks/openshell/b.md")
    (root / runbook_a).parent.mkdir(parents=True, exist_ok=True)
    (root / runbook_a).write_text("# A\n", encoding="utf-8")
    (root / runbook_b).write_text("# B\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(
        root,
        "add",
        runbook_a.as_posix(),
        runbook_b.as_posix(),
        builder.DEFAULT_BASELINE.as_posix(),
        builder.DEFAULT_SERVICE.as_posix(),
        builder.DEFAULT_PROVIDER_PROBE.as_posix(),
        builder.DEFAULT_MEMORY.as_posix(),
        builder.DEFAULT_HOST_EGRESS.as_posix(),
    )
    monkeypatch.setattr(builder.completion, "REQUIRED_RUNBOOKS", (runbook_a, runbook_b), raising=False)
    monkeypatch.setattr(builder, "_repository_scan", lambda _root: True)
    return root


def _formal_filesystem(
    root: Path,
    *,
    generated_at: str,
    image_sha256: str,
    policy_sha256: str,
    mount_plan_sha256: str,
) -> dict[str, object]:
    runner = builder.completion.formal_filesystem_evidence
    for relative in (
        runner.SCHEMA_RELATIVE,
        runner.PROBE_MODULE_RELATIVE,
        runner.LIFECYCLE_RELATIVE,
        runner.TRANSACTION_RELATIVE,
        runner.MOUNT_CONTRACT_RELATIVE,
        runner.RUNNER_RELATIVE,
    ):
        _copy(ROOT / relative, root / relative)
    binding = runner.ActiveBinding(
        transaction_receipt_sha256="1" * 64,
        transaction_generation=1,
        manifest_sha256="2" * 64,
        sandbox_binding_sha256="3" * 64,
        host_receipt_sha256="4" * 64,
        run_id_sha256="5" * 64,
        sandbox_id_sha256="6" * 64,
        container_id_sha256="7" * 64,
        session_id_sha256="8" * 64,
        resource_receipts_sha256="9" * 64,
        image_sha256=image_sha256,
        policy_sha256=policy_sha256,
        mount_plan_sha256=mount_plan_sha256,
        mount_contract_sha256=mount_plan_sha256,
        runtime_config_sha256="a" * 64,
    )
    capture = runner.ActiveCapture(
        context=object(),
        binding=binding,
        transaction_id="tx-formal-run",
        run_id="formal-run",
        sandbox_id="11111111-1111-1111-1111-111111111111",
        container_id="b" * 64,
        analysis_relative_path="data/wiki/companies/acme/analysis",
    )
    response = {
        "ok": True,
        "check": "filesystem",
        "immutable_write_denials": {key: True for key in runner.sandbox_probe.FILESYSTEM_IMMUTABLE_DENIALS},
        "sensitive_read_denials": {key: True for key in runner.sandbox_probe.FILESYSTEM_SENSITIVE_DENIALS},
        "allowed_writes": {key: True for key in runner.sandbox_probe.FILESYSTEM_ALLOWED_WRITES},
    }
    probe_result = {
        "schema_version": runner.PROBE_SCHEMA_VERSION,
        "ok": True,
        "profile": "siq_analysis",
        "run_id": "formal-run",
        "checks": [
            *runner.sandbox_probe.FILESYSTEM_IDENTITY_CHECKS,
            *runner.sandbox_probe.FILESYSTEM_IMMUTABLE_DENIALS,
            *runner.sandbox_probe.FILESYSTEM_SENSITIVE_DENIALS,
            "analysis_bind_read_write",
            "runtime_state_directory_bind_read_write",
            "runtime_session_bind_read_write",
            "runtime_memory_bind_read_write",
            "tmp_scratch_write",
            "probe_sentinels_removed",
        ],
        "mounts": {"business_mount_count": 7, "control_mount_count": 5, "total_mount_count": 12},
        "immutable_write_denials": response["immutable_write_denials"],
        "sensitive_read_denials": response["sensitive_read_denials"],
        "allowed_writes": response["allowed_writes"],
        "host_visibility_receipts": {
            "analysis": "c" * 64,
            "runtime_state": "d" * 64,
            "runtime_session": "e" * 64,
            "runtime_memory": "f" * 64,
        },
        "filesystem_response_sha256": runner._canonical_sha256(response),
        "filesystem_probe_sha256": runner._sha256(runner.sandbox_probe.FILESYSTEM_PROBE.encode("utf-8")),
        "cleanup_succeeded": True,
        "residual_host_sentinel_count": 0,
    }
    return runner.build_evidence(
        project_root=root,
        generated_at=generated_at,
        before=capture,
        after=capture,
        probe_result=probe_result,
        raw_receipt_sha256="0" * 64,
    )


def test_builder_is_deterministic_and_completion_document_compatible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    root = _project(tmp_path, monkeypatch, generated_at=generated)

    first = builder.build_readiness(project_root=root, generated_at=generated)
    second = builder.build_readiness(project_root=root, generated_at=generated)

    assert first == second
    assert builder._canonical_json(first) == builder._canonical_json(second)
    assert first["decision"] == "NO_GO"
    assert builder.completion._validate_readiness_document(first) is True
    assert "formal_host_rollback_evidence_missing" in first["blockers"]
    assert "formal_ab_evidence_missing" in first["blockers"]
    assert "service_preflight_stale" not in first["blockers"]
    assert (
        first["verification"]["memory_write_evidence_sha256"]
        == hashlib.sha256((root / builder.DEFAULT_MEMORY).read_bytes()).hexdigest()
    )
    assert builder.completion._evidence_binding(
        first,
        verification_key="memory_write_evidence",
        digest_key="memory_write_evidence_sha256",
        path=(root / builder.DEFAULT_MEMORY).resolve(),
        root=root.resolve(),
        digest=first["verification"]["memory_write_evidence_sha256"],
    )


def test_hand_written_service_go_cannot_override_contradictory_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    root = _project(tmp_path, monkeypatch, generated_at=generated)
    service = _service_go()
    qwen = next(item for item in service["services"] if item["service_id"] == "qwen_local")
    qwen["reachable"] = False
    qwen["status"] = "pass"
    _write_json(root / builder.DEFAULT_SERVICE, service)
    recent = (generated - timedelta(seconds=5)).timestamp()
    os.utime(root / builder.DEFAULT_SERVICE, (recent, recent))

    result = builder.build_readiness(project_root=root, generated_at=generated)

    assert result["decision"] == "NO_GO"
    assert "service_preflight_contract_invalid" in result["blockers"]
    assert result["runtime_state"]["service_preflight"] == "NO_GO"


def test_git_index_runbook_digest_ignores_unstaged_worktree_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    runbook_a = Path("docs/runbooks/openshell/a.md")
    runbook_b = Path("docs/runbooks/openshell/b.md")
    (root / runbook_a).parent.mkdir(parents=True)
    (root / runbook_a).write_text("A v1\n", encoding="utf-8")
    (root / runbook_b).write_text("B v1\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", runbook_a.as_posix(), runbook_b.as_posix())
    monkeypatch.setattr(builder.completion, "REQUIRED_RUNBOOKS", (runbook_b, runbook_a), raising=False)

    initial = builder.git_index_runbook_bundle_sha256(root)
    (root / runbook_a).write_text("A unstaged\n", encoding="utf-8")
    assert builder.git_index_runbook_bundle_sha256(root) == initial
    _git(root, "add", runbook_a.as_posix())
    assert builder.git_index_runbook_bundle_sha256(root) != initial


def test_default_cli_does_not_replace_canonical_readiness_and_output_is_opt_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    root = _project(tmp_path, monkeypatch, generated_at=generated)
    canonical = root / "artifacts/openshell/v0.6/readiness.json"
    canonical.write_bytes(b"canonical-sentinel\n")

    arguments = [
        "--project-root",
        str(root),
        "--generated-at",
        builder._format_timestamp(generated),
    ]
    assert builder.main(arguments) == 0
    stdout = json.loads(capsys.readouterr().out)
    assert stdout["decision"] == "NO_GO"
    assert canonical.read_bytes() == b"canonical-sentinel\n"

    output = Path("artifacts/openshell/v0.6/readiness.generated.json")
    assert builder.main([*arguments, "--output", str(output)]) == 0
    written = json.loads((root / output).read_text(encoding="utf-8"))
    assert written["decision"] == "NO_GO"
    capsys.readouterr()

    before = (root / output).read_bytes()
    assert builder.main([*arguments, "--output", str(output)]) == 2
    assert json.loads(capsys.readouterr().err)["error_code"] == "output_exists_replace_required"
    assert (root / output).read_bytes() == before
    assert builder.main([*arguments, "--output", str(output), "--replace"]) == 0


def test_full_evidence_facts_can_compose_go_without_trusting_input_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    root = _project(tmp_path, monkeypatch, generated_at=generated)
    probe = json.loads((root / builder.DEFAULT_PROVIDER_PROBE).read_text(encoding="utf-8"))
    provenance = probe["provenance"]
    image_sha = provenance["image_id"][7:]
    policy_sha = provenance["policy_sha256"]
    mount_sha = provenance["mount_plan_sha256"]
    generated_text = builder._format_timestamp(generated - timedelta(seconds=1))

    shared_provenance = {
        "image_sha256": image_sha,
        "policy_sha256": policy_sha,
        "mount_contract_sha256": mount_sha,
    }
    _write_json(
        root / builder.DEFAULT_FORMAL_HOST_ROLLBACK,
        {
            "generated_at": generated_text,
            "provenance": shared_provenance,
            "cleanup": {"publisher_index_published": True},
        },
    )
    _write_json(
        root / builder.DEFAULT_FORMAL_DELETE_GUARD,
        {"generated_at": generated_text, "provenance": shared_provenance},
    )
    _write_json(
        root / builder.DEFAULT_FORMAL_FILESYSTEM_BOUNDARY,
        _formal_filesystem(
            root,
            generated_at=generated_text,
            image_sha256=image_sha,
            policy_sha256=policy_sha,
            mount_plan_sha256=mount_sha,
        ),
    )
    _write_json(
        root / builder.DEFAULT_FORMAL_EGRESS,
        {"generated_at": generated_text, "provenance": shared_provenance},
    )
    _write_json(root / builder.DEFAULT_FORMAL_AUDIT, {"generated_at": generated_text})
    _write_json(
        root / builder.DEFAULT_FORMAL_FALLBACK_DRILL,
        {"generated_at": generated_text, "decision": "PASS"},
    )

    evaluation_id = "eval-live-go"
    summary_path = Path("artifacts/openshell/v0.6/formal-ab-summary.sanitized.json")
    prerequisites_path = Path(f"var/openshell/eval/{evaluation_id}/prerequisites.json")
    prerequisites = {"decision": "GO"}
    _write_json(root / prerequisites_path, prerequisites)
    prerequisites_digest = hashlib.sha256((root / prerequisites_path).read_bytes()).hexdigest()
    _write_json(
        root / summary_path,
        {
            "decision": "GO",
            "quality_gate": {"passed": True, "failure_reasons": []},
            "prerequisites_path": prerequisites_path.as_posix(),
            "prerequisites_sha256": prerequisites_digest,
        },
    )
    _git(
        root,
        "add",
        builder.DEFAULT_FORMAL_HOST_ROLLBACK.as_posix(),
        builder.DEFAULT_FORMAL_DELETE_GUARD.as_posix(),
        builder.DEFAULT_FORMAL_FILESYSTEM_BOUNDARY.as_posix(),
        builder.DEFAULT_FORMAL_EGRESS.as_posix(),
        builder.DEFAULT_FORMAL_AUDIT.as_posix(),
        builder.DEFAULT_FORMAL_FALLBACK_DRILL.as_posix(),
        summary_path.as_posix(),
    )
    monkeypatch.setattr(builder.completion, "_validate_formal_host_rollback", lambda *args, **kwargs: True)
    monkeypatch.setattr(builder.completion, "_validate_formal_delete_guard", lambda *args, **kwargs: True)
    monkeypatch.setattr(builder.completion, "_validate_formal_egress_sandbox", lambda *args, **kwargs: True)
    monkeypatch.setattr(builder.completion, "_validate_formal_structured_audit", lambda *args, **kwargs: True)
    monkeypatch.setattr(builder.completion, "_validate_ab_summary", lambda *args, **kwargs: True)
    monkeypatch.setattr(builder.completion, "_validate_formal_fallback_drill", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        builder.completion,
        "_formal_fallback_runtime_provenance_matches",
        lambda *args, **kwargs: True,
    )

    def fake_prerequisites(
        _root: Path,
        path: Path,
        **_kwargs: object,
    ) -> tuple[dict[str, object], Path, str]:
        content = path.read_bytes()
        return prerequisites, path, hashlib.sha256(content).hexdigest()

    monkeypatch.setattr(builder.completion, "_ab_prerequisites", fake_prerequisites)

    result = builder.build_readiness(
        project_root=root,
        generated_at=generated,
        ab_summary_path=summary_path,
        ab_prerequisites_path=prerequisites_path,
    )

    assert result["blockers"] == []
    assert result["decision"] == "GO"
    assert result["contracts"]["api_and_output_paths_unchanged"] is True
    assert result["runtime_state"]["formal_fallback_drill"] == "passed"
    assert builder.completion._readiness_runtime_go(result, service_go=True) is True


def test_invalid_formal_go_label_is_rejected_by_authoritative_validator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    root = _project(tmp_path, monkeypatch, generated_at=generated)
    _write_json(root / builder.DEFAULT_FORMAL_HOST_ROLLBACK, {"decision": "GO"})

    result = builder.build_readiness(project_root=root, generated_at=generated)

    assert result["decision"] == "NO_GO"
    assert "formal_host_rollback_invalid" in result["blockers"]


def test_missing_broker_evidence_fails_closed_without_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    root = _project(tmp_path, monkeypatch, generated_at=generated)
    (root / builder.DEFAULT_BROKER_STATUS).unlink()

    result = builder.build_readiness(project_root=root, generated_at=generated)

    assert result["decision"] == "NO_GO"
    assert "broker_status_evidence_missing" in result["blockers"]
    assert result["runtime_state"]["host_brokers"] == "unverified"


def test_provider_independent_probe_cannot_claim_formal_filesystem_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    root = _project(tmp_path, monkeypatch, generated_at=generated)

    result = builder.build_readiness(project_root=root, generated_at=generated)

    assert "formal_filesystem_boundary_evidence_missing" in result["blockers"]
    assert result["runtime_state"]["provider_independent_probe"] == "passed"
    assert result["security_controls"] == {
        "project_code_readonly": False,
        "agent_control_files_readonly": False,
        "finalized_ingested_paths_readonly": False,
        "task_analysis_path_writable": False,
        "runtime_session_and_memory_paths_writable": False,
        "unknown_file_upload_blocked": False,
        "high_risk_delete_guard": False,
    }


def test_file_output_requires_explicit_generated_at(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert builder.main(["--project-root", str(tmp_path), "--output", "readiness.json"]) == 2
    assert json.loads(capsys.readouterr().err)["error_code"] == "output_requires_generated_at"


def test_unstaged_evidence_drift_cannot_report_index_scan_passed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    root = _project(tmp_path, monkeypatch, generated_at=generated)
    baseline_path = root / builder.DEFAULT_BASELINE
    baseline_path.write_bytes(baseline_path.read_bytes() + b"\n")

    result = builder.build_readiness(project_root=root, generated_at=generated)

    assert result["decision"] == "NO_GO"
    assert result["verification"]["published_evidence_index_scan"] == "failed"
    assert "published_evidence_index_scan_failed" in result["blockers"]
