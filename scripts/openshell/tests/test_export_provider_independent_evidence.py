from __future__ import annotations

import json
from pathlib import Path

from scripts.openshell import export_provider_independent_evidence as exporter
from scripts.openshell import probe_siq_analysis_sandbox as probe


def test_projection_uses_truthful_control_credential_boundary(monkeypatch, tmp_path: Path) -> None:
    run_dir = tmp_path / "var/openshell/siq-analysis/security-probes/probe-0123456789ab"
    run_dir.mkdir(parents=True)
    raw = {
        "schema_version": exporter.RAW_SCHEMA_VERSION,
        "phase": "passed",
        "mode": "provider-independent",
        "profile": "siq_analysis",
        "probe_id": "probe-0123456789ab",
        "provider_calls": 0,
        "provider_calls_observed": True,
        "providers": [],
        "formal_business_sandbox": False,
        "host_runtime_unchanged": True,
        "quality_validated": False,
        "readiness_effect": "none",
        "checks": list(dict.fromkeys((
            *probe.FILESYSTEM_IMMUTABLE_DENIALS,
            *probe.FILESYSTEM_SENSITIVE_DENIALS,
            "analysis_bind_read_write",
            "runtime_state_directory_bind_read_write",
            "runtime_session_bind_read_write",
            "memory_path_read_write",
            "unknown_curl_upload_denied",
            "probe_sentinels_removed",
            "verified_probe_sandbox_deleted",
            "runtime_snapshot_removed",
            *[f"extra-{index}" for index in range(32)],
        ))),
        "mounts": {"business_mount_count": 7, "control_mount_count": 5, "total_mount_count": 12},
        "network_isolation": {key: {"result": "denied"} for key in (
            "cloud_metadata", "egress_broker", "internal_model", "public_https"
        )},
        "process_hardening": {"schema_version": "v", "uid": 1000, "gid": 1000, "non_root_identity": True},
        "container_hardening": {
            "privileged": False,
            "host_device_count": 0,
            "device_request_count": 0,
            "cap_add_profile": "openshell_v0.0.83_bootstrap_exact",
            "supervisor_user": "0",
        },
        "runtime_isolation": {key: 0 for key in (
            "api_listener_count", "auth_material_count", "hermes_process_count",
            "provider_call_capable_processes", "provider_env_count"
        )},
        "cleanup_error_code": "",
        "runtime_snapshot": "var/openshell/removed",
        "mount_plan_sha256": "a" * 64,
        "policy_sha256": "b" * 64,
        "image_id": "sha256:" + "c" * 64,
        "image_ref": "siq/test:fixed",
    }
    (run_dir / "probe.json").write_text(json.dumps(raw), encoding="utf-8")
    (run_dir / "task-policy.yaml").write_text("policy\n", encoding="ascii")
    raw["policy_sha256"] = exporter._sha256((run_dir / "task-policy.yaml").read_bytes())
    (run_dir / "probe.json").write_text(json.dumps(raw), encoding="utf-8")
    (run_dir / "task-policy.summary.json").write_text("{}\n", encoding="ascii")
    monkeypatch.setattr(exporter, "REPO_ROOT", tmp_path)

    result = exporter.build_projection(tmp_path, "probe-0123456789ab")

    assert result["checks"]["control_credentials_read_only"] is True
    assert "control_secrets_hidden" not in result["checks"]
    assert result["cleanup"]["sandbox_deleted"] is True
