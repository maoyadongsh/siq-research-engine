from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "write_release_artifact_manifest.py"
spec = importlib.util.spec_from_file_location("write_release_artifact_manifest_under_test", SOURCE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def test_manifest_records_relative_artifacts_and_checksums(tmp_path):
    (tmp_path / "gate.json").write_text('{"passed": true}\n', encoding="utf-8")
    (tmp_path / "gate.md").write_text("# Gate\n", encoding="utf-8")

    manifest = module.build_manifest(tmp_path, task_id="T12", environment_profile="test")
    module.write_outputs(manifest, tmp_path, output_name="manifest.json", checksum_name="checksums.sha256")

    assert manifest["result"] == "pass"
    assert manifest["missing_required_artifacts"] == []
    assert manifest["missing_included_artifacts"] == []
    assert manifest["policy_violations"] == []
    assert manifest["base_commit"]
    assert isinstance(manifest["worktree_dirty"], bool)
    assert manifest["command"]
    assert manifest["duration_seconds"] >= 0
    assert manifest["failures"] == []
    assert set(manifest["artifact_checksums"]) == {"gate.json", "gate.md"}
    assert manifest["artifact_count"] == 2
    assert {item["path"] for item in manifest["artifacts"]} == {"gate.json", "gate.md"}
    checksums = (tmp_path / "checksums.sha256").read_text(encoding="utf-8")
    assert "gate.json" in checksums and "gate.md" in checksums
    assert json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))["task_id"] == "T12"


def test_manifest_uses_repo_relative_checksum_keys_for_repo_artifacts(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    artifact_dir = repo_root / "artifacts" / "release"
    artifact_dir.mkdir(parents=True)
    artifact = artifact_dir / "gate.json"
    artifact.write_text('{"passed": true}\n', encoding="utf-8")
    monkeypatch.setattr(
        module,
        "_source_state",
        lambda _repo_root: {
            "available": True,
            "head_commit": "a" * 40,
            "head_tree": "b" * 40,
            "dirty": False,
        },
    )

    manifest = module.build_manifest(
        artifact_dir,
        task_id="T12",
        environment_profile="test",
        repo_root=repo_root,
    )

    assert set(manifest["artifact_checksums"]) == {"artifacts/release/gate.json"}
    assert manifest["artifacts"][0]["path"] == "gate.json"


def test_manifest_marks_failed_json_artifact(tmp_path):
    (tmp_path / "failed.json").write_text('{"passed": false}\n', encoding="utf-8")
    manifest = module.build_manifest(tmp_path, task_id="T10", environment_profile="test")

    assert manifest["result"] == "fail"
    assert manifest["failed_artifacts"] == ["failed.json"]


@pytest.mark.parametrize("payload", [{"status": "failed"}, {"status": "blocked"}, {"result": "fail"}, {"summary": {"p0_gate_passed": False}}])
def test_manifest_marks_non_boolean_failure_contracts(tmp_path, payload):
    (tmp_path / "failed.json").write_text(json.dumps(payload) + "\n", encoding="utf-8")

    manifest = module.build_manifest(tmp_path, task_id="T12", environment_profile="test")

    assert manifest["result"] == "fail"
    assert manifest["failed_artifacts"] == ["failed.json"]


@pytest.mark.parametrize("local_path", ["/home/operator/project/data.json", "/tmp/release/task.json", r"C:\\Users\\operator\\report.json"])
def test_manifest_rejects_local_absolute_paths_in_reports(tmp_path, local_path):
    (tmp_path / "gate.json").write_text(json.dumps({"passed": True, "source": local_path}) + "\n", encoding="utf-8")

    manifest = module.build_manifest(tmp_path, task_id="T12", environment_profile="test")

    assert manifest["result"] == "fail"
    assert manifest["failed_artifacts"] == []
    assert manifest["policy_violations"] == [{"path": "gate.json", "code": "local_absolute_path"}]


def test_manifest_fails_when_allowlisted_required_artifact_is_missing(tmp_path):
    (tmp_path / "gate.json").write_text('{"passed": true}\n', encoding="utf-8")

    manifest = module.build_manifest(
        tmp_path,
        task_id="T12",
        environment_profile="test",
        required_artifacts=["gate.json", "restore-matrix.json"],
    )

    assert manifest["result"] == "fail"
    assert manifest["required_artifacts"] == ["gate.json", "restore-matrix.json"]
    assert manifest["missing_required_artifacts"] == ["restore-matrix.json"]


def test_manifest_rejects_required_artifact_paths(tmp_path):
    with pytest.raises(ValueError, match="plain file names"):
        module.build_manifest(
            tmp_path,
            task_id="T12",
            environment_profile="test",
            required_artifacts=["../restore-matrix.json"],
        )


def test_manifest_includes_sibling_live_restore_and_preflight_evidence(tmp_path):
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    evidence_dir = tmp_path / "controlled"
    evidence_dir.mkdir()
    for name in ("live-market-qa-smoke.json", "restore-matrix.json", "production-config-preflight.json"):
        (evidence_dir / name).write_text('{"passed": true}\n', encoding="utf-8")

    manifest = module.build_manifest(
        release_dir,
        task_id="T12",
        environment_profile="test",
        required_artifacts=[
            "live-market-qa-smoke.json",
            "restore-matrix.json",
            "production-config-preflight.json",
        ],
        include_dirs=[evidence_dir],
    )

    assert manifest["result"] == "pass"
    assert manifest["missing_required_artifacts"] == []
    assert {item["path"] for item in manifest["artifacts"]} == {
        "../controlled/live-market-qa-smoke.json",
        "../controlled/production-config-preflight.json",
        "../controlled/restore-matrix.json",
    }
    assert str(tmp_path) not in json.dumps(manifest)


def test_manifest_fails_when_explicit_include_is_missing(tmp_path):
    release_dir = tmp_path / "release"
    release_dir.mkdir()

    manifest = module.build_manifest(
        release_dir,
        task_id="T12",
        environment_profile="test",
        include_artifacts=[tmp_path / "controlled" / "restore-matrix.json"],
    )

    assert manifest["result"] == "fail"
    assert manifest["missing_included_artifacts"] == ["../controlled/restore-matrix.json"]


def test_manifest_does_not_persist_credential_values(tmp_path):
    secret = "super-secret-release-token"
    (tmp_path / "preflight.json").write_text(
        json.dumps({"passed": True, "auth_token": secret}) + "\n", encoding="utf-8"
    )

    manifest = module.build_manifest(tmp_path, task_id="T12", environment_profile="test")

    serialized = json.dumps(manifest)
    assert manifest["result"] == "fail"
    assert secret not in serialized
    assert manifest["policy_violations"] == [{"path": "preflight.json", "code": "credential_value"}]


def test_manifest_allows_redacted_credential_health_states(tmp_path):
    (tmp_path / "preflight.json").write_text(
        json.dumps(
            {
                "passed": True,
                "fields": {
                    "SIQ_AUTH_SECRET_KEY": "configured",
                    "SIQ_SOURCE_TOKEN_SECRET": "missing",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    manifest = module.build_manifest(tmp_path, task_id="T12", environment_profile="test")

    assert manifest["result"] == "pass"
    assert manifest["policy_violations"] == []


def test_manifest_source_state_is_verifiable_and_redacted(tmp_path):
    (tmp_path / "gate.json").write_text('{"passed": true}\n', encoding="utf-8")

    manifest = module.build_manifest(tmp_path, task_id="T12", environment_profile="test")

    source = manifest["source_state"]
    assert source["available"] is True
    assert re.fullmatch(r"[0-9a-f]{40}", source["head_commit"])
    assert re.fullmatch(r"[0-9a-f]{64}", source["worktree_fingerprint_sha256"])
    assert isinstance(source["dirty"], bool)
    assert "paths" not in json.dumps(source)
    assert str(module.REPO_ROOT) not in json.dumps(source)


def test_manifest_fails_closed_when_source_state_is_unavailable(tmp_path, monkeypatch):
    (tmp_path / "gate.json").write_text('{"passed": true}\n', encoding="utf-8")
    monkeypatch.setattr(
        module,
        "_source_state",
        lambda _repo_root: {
            "available": False,
            "head_commit": "unknown",
            "head_tree": "unknown",
            "dirty": None,
        },
    )

    manifest = module.build_manifest(tmp_path, task_id="T12", environment_profile="test")

    assert manifest["result"] == "fail"
    assert manifest["policy_violations"] == [
        {"path": "source_state", "code": "source_state_unavailable"}
    ]
