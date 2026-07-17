from __future__ import annotations

import errno
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.openshell import runtime_state_lifecycle_smoke as smoke  # noqa: E402
from scripts.openshell.build_siq_analysis_mount_plan import (  # noqa: E402
    RUNTIME_STATE_DIRECTORY,
    SANDBOX_RUNTIME_STATE_ROOT,
    _expected_mounts,
)


def test_two_round_lifecycle_rebuilds_wal_and_metadata_then_cleans(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime-smoke"
    runtime.mkdir(mode=0o700)

    result = smoke.run_lifecycle_smoke(runtime)

    assert smoke.is_passed_lifecycle_result(result)
    assert result["rounds_completed"] == 2
    assert [item["generation"] for item in result["rounds"]] == [1, 2]
    for item in result["rounds"]:
        assert item["created"] == list(smoke.LIFECYCLE_FILES)
        assert item["deleted"] == list(smoke.LIFECYCLE_FILES)
        assert set(item["sqlite"]) == set(smoke.SQLITE_DATABASES)
        assert all(evidence["journal_mode"] == "wal" for evidence in item["sqlite"].values())
        assert item["metadata"]["gateway_state.json"] == "atomic_create_and_replace"
        assert item["metadata"]["processes.json"] == "atomic_create_and_replace"
    assert list(runtime.iterdir()) == []


def test_atomic_metadata_update_replaces_inode_and_generation(tmp_path: Path) -> None:
    path = tmp_path / "gateway_state.json"

    smoke._atomic_json_write(path, {"generation": 1})
    first_inode = path.stat().st_ino
    smoke._atomic_json_write(path, {"generation": 2})

    assert path.stat().st_ino != first_inode
    assert json.loads(path.read_text(encoding="utf-8")) == {"generation": 2}
    assert not tuple(tmp_path.glob(".siq-smoke-*.tmp"))


def test_cli_fails_closed_when_atomic_replace_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = tmp_path / "runtime-smoke"
    runtime.mkdir(mode=0o700)

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError(errno.EPERM, "replace denied")

    monkeypatch.setattr(smoke.os, "replace", fail_replace)

    assert smoke.main(["--runtime-root", str(runtime)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "error_code": "runtime_metadata_atomic_replace_failed",
        "readiness_effect": "none",
        "schema_version": smoke.SCHEMA_VERSION,
        "status": "failed",
    }
    assert list(runtime.iterdir()) == []


def test_cli_fails_closed_when_sqlite_sidecar_cannot_be_unlinked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = tmp_path / "runtime-smoke"
    runtime.mkdir(mode=0o700)
    original_unlink = Path.unlink

    def block_sidecar_unlink(path: Path, *args, **kwargs) -> None:
        if path.name.endswith("-wal"):
            raise OSError(errno.EBUSY, "bind-mounted file")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", block_sidecar_unlink)

    assert smoke.main(["--runtime-root", str(runtime)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "failed"
    assert result["error_code"] == "sqlite_sidecar_delete_failed"
    assert result["readiness_effect"] == "none"


def test_rejects_nonempty_or_protected_runtime_root_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "runtime-smoke"
    runtime.mkdir(mode=0o700)
    sentinel = runtime / "owned.txt"
    sentinel.write_text("preserve\n", encoding="utf-8")

    with pytest.raises(smoke.RuntimeStateSmokeError, match="runtime_smoke_root_not_empty"):
        smoke.run_lifecycle_smoke(runtime)
    assert sentinel.read_text(encoding="utf-8") == "preserve\n"

    sentinel.unlink()
    monkeypatch.setenv("HERMES_HOME", str(runtime))
    with pytest.raises(smoke.RuntimeStateSmokeError, match="runtime_smoke_root_overlaps_protected_path"):
        smoke.run_lifecycle_smoke(runtime)


def test_directory_mount_contract_resolves_file_bind_blockers_and_retains_live_gate(tmp_path: Path) -> None:
    project = tmp_path / "project"
    snapshot = project / "var/openshell/siq-analysis/runtime-snapshots/run-one"
    analysis = project / "data/wiki/companies/600001-Test/analysis"
    mounts = _expected_mounts(project, snapshot, analysis)

    assert {
        "type": "bind",
        "source": (snapshot / RUNTIME_STATE_DIRECTORY).as_posix(),
        "target": SANDBOX_RUNTIME_STATE_ROOT.as_posix(),
        "read_only": False,
    } in mounts
    assert not any(Path(mount["source"]).name in smoke.SQLITE_SIDECARS for mount in mounts)

    policy = json.loads(
        (Path(__file__).resolve().parents[3] / "infra/openshell/policies/profiles/siq-analysis.yaml").read_text(
            encoding="utf-8"
        )
    )
    writable = set(policy["filesystem_policy"]["read_write"])
    token_root = "${SIQ_PROJECT_ROOT}/data/hermes/home/profiles/siq_analysis"
    assert token_root not in writable
    assert all(f"{token_root}/{name}" not in writable for name in smoke.RUNTIME_METADATA)
    assert smoke.FORMAL_SANDBOX_REASON_CODES == ("formal_runtime_directory_bind_requires_live_sandbox_evidence",)


def test_passed_evidence_validator_rejects_tampered_scope(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime-smoke"
    runtime.mkdir(mode=0o700)
    result = smoke.run_lifecycle_smoke(runtime)
    result["readiness_effect"] = "formal"

    assert not smoke.is_passed_lifecycle_result(result)
