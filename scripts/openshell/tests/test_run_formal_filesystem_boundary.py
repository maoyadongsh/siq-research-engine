from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from scripts.openshell import probe_siq_analysis_sandbox as sandbox_probe, run_formal_filesystem_boundary as runner


def _binding(seed: str = "a") -> runner.ActiveBinding:
    digest = seed * 64
    return runner.ActiveBinding(
        transaction_receipt_sha256=digest,
        transaction_generation=12,
        manifest_sha256="b" * 64,
        sandbox_binding_sha256="c" * 64,
        host_receipt_sha256="d" * 64,
        run_id_sha256="e" * 64,
        sandbox_id_sha256="f" * 64,
        container_id_sha256="1" * 64,
        session_id_sha256="2" * 64,
        resource_receipts_sha256="3" * 64,
        image_sha256="4" * 64,
        policy_sha256="5" * 64,
        mount_plan_sha256="6" * 64,
        mount_contract_sha256="0" * 64,
        runtime_config_sha256="7" * 64,
    )


def _capture(binding: runner.ActiveBinding | None = None) -> runner.ActiveCapture:
    return runner.ActiveCapture(
        context=object(),
        binding=binding or _binding(),
        transaction_id="tx-formal-run",
        run_id="formal-run",
        sandbox_id="11111111-1111-1111-1111-111111111111",
        container_id="8" * 64,
        analysis_relative_path="data/wiki/companies/acme/analysis",
    )


def _probe_result() -> dict:
    response = {
        "ok": True,
        "check": "filesystem",
        "immutable_write_denials": {key: True for key in sandbox_probe.FILESYSTEM_IMMUTABLE_DENIALS},
        "sensitive_read_denials": {key: True for key in sandbox_probe.FILESYSTEM_SENSITIVE_DENIALS},
        "allowed_writes": {key: True for key in sandbox_probe.FILESYSTEM_ALLOWED_WRITES},
    }
    return {
        "schema_version": runner.PROBE_SCHEMA_VERSION,
        "ok": True,
        "profile": "siq_analysis",
        "run_id": "formal-run",
        "checks": [
            *sandbox_probe.FILESYSTEM_IDENTITY_CHECKS,
            *sandbox_probe.FILESYSTEM_IMMUTABLE_DENIALS,
            *sandbox_probe.FILESYSTEM_SENSITIVE_DENIALS,
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
            "analysis": "8" * 64,
            "runtime_state": "9" * 64,
            "runtime_session": "a" * 64,
            "runtime_memory": "b" * 64,
        },
        "filesystem_response_sha256": runner._canonical_sha256(response),
        "filesystem_probe_sha256": runner._sha256(sandbox_probe.FILESYSTEM_PROBE.encode("utf-8")),
        "cleanup_succeeded": True,
        "residual_host_sentinel_count": 0,
    }


def test_build_evidence_matches_strict_schema() -> None:
    capture = _capture()
    evidence = runner.build_evidence(
        project_root=runner.REPO_ROOT,
        generated_at="2026-07-16T12:00:00Z",
        before=capture,
        after=capture,
        probe_result=_probe_result(),
        raw_receipt_sha256="c" * 64,
    )
    schema = json.loads((runner.REPO_ROOT / runner.SCHEMA_RELATIVE).read_text(encoding="utf-8"))

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(evidence)
    assert evidence["business_inference_exercised"] is False
    assert evidence["overall_readiness_effect"] == "component_evidence_only"
    assert evidence["not_claimed"] == runner.NOT_CLAIMED
    serialized = json.dumps(evidence, ensure_ascii=True, sort_keys=True)
    assert capture.run_id not in serialized
    assert capture.sandbox_id not in serialized
    assert capture.analysis_relative_path not in serialized


def test_evidence_contract_rejects_receipt_mismatch() -> None:
    capture = _capture()
    evidence = runner.build_evidence(
        project_root=runner.REPO_ROOT,
        generated_at="2026-07-16T12:00:00Z",
        before=capture,
        after=capture,
        probe_result=_probe_result(),
        raw_receipt_sha256="c" * 64,
    )
    evidence["transaction"]["after_receipt_sha256"] = "9" * 64

    with pytest.raises(runner.FormalFilesystemEvidenceError, match="formal_filesystem_binding_invalid"):
        runner.validate_evidence(evidence)


def test_build_evidence_rejects_transaction_change() -> None:
    with pytest.raises(runner.FormalFilesystemEvidenceError, match="formal_binding_changed_during_probe"):
        runner.build_evidence(
            project_root=runner.REPO_ROOT,
            generated_at="2026-07-16T12:00:00Z",
            before=_capture(),
            after=_capture(_binding("9")),
            probe_result=_probe_result(),
            raw_receipt_sha256="c" * 64,
        )


def _private_layout(root: Path) -> tuple[Path, Path]:
    for relative in (
        "artifacts",
        "artifacts/openshell",
        "artifacts/openshell/v0.6",
        "var",
        "var/openshell",
        "var/openshell/locks",
        "var/openshell/proofs",
    ):
        path = root / relative
        path.mkdir(exist_ok=True)
        path.chmod(0o700)
    return (
        Path("artifacts/openshell/v0.6/formal-filesystem-test.sanitized.json"),
        Path("artifacts/openshell/v0.6/formal-filesystem-test.sanitized.md"),
    )


def test_no_active_transaction_creates_no_go_artifact(monkeypatch, tmp_path: Path) -> None:
    json_relative, markdown_relative = _private_layout(tmp_path)
    monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        runner,
        "capture_active_binding",
        lambda **kwargs: (_ for _ in ()).throw(
            runner.FormalFilesystemEvidenceError("formal_active_transaction_required")
        ),
    )

    with pytest.raises(runner.FormalFilesystemEvidenceError, match="formal_active_transaction_required"):
        runner.run_and_publish(
            project_root=tmp_path,
            run_id="formal-run",
            artifact_json=json_relative,
            artifact_markdown=markdown_relative,
            timeout=5,
        )

    assert not (tmp_path / json_relative).exists()
    assert not (tmp_path / markdown_relative).exists()
    assert not (tmp_path / runner.RAW_ROOT_RELATIVE / "formal-run.raw.json").exists()


def test_success_bundle_is_owner_only_and_exclusive(monkeypatch, tmp_path: Path) -> None:
    json_relative, markdown_relative = _private_layout(tmp_path)
    capture = _capture()
    monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(runner, "capture_active_binding", lambda **kwargs: capture)
    monkeypatch.setattr(sandbox_probe, "run_filesystem_boundary_probe", lambda *args, **kwargs: _probe_result())
    monkeypatch.setattr(runner.check_sanitized_artifacts, "scan_content", lambda *args, **kwargs: [])
    monkeypatch.setattr(runner.check_sanitized_artifacts, "scan_paths", lambda *args, **kwargs: [])

    def fake_build(**kwargs):
        return {
            "schema_version": runner.SCHEMA_VERSION,
            "decision": "GO",
            "provenance": {"raw_receipt_sha256": kwargs["raw_receipt_sha256"]},
        }

    monkeypatch.setattr(runner, "build_evidence", fake_build)
    evidence, raw_path = runner.run_and_publish(
        project_root=tmp_path,
        run_id="formal-run",
        artifact_json=json_relative,
        artifact_markdown=markdown_relative,
        timeout=5,
    )

    paths = [raw_path, tmp_path / json_relative, tmp_path / markdown_relative]
    assert evidence["decision"] == "GO"
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in paths)
    with pytest.raises(runner.FormalFilesystemEvidenceError, match="artifact_output_exists"):
        runner.run_and_publish(
            project_root=tmp_path,
            run_id="formal-run",
            artifact_json=json_relative,
            artifact_markdown=markdown_relative,
            timeout=5,
        )
