from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from scripts.openshell import run_formal_delete_guard as runner
from scripts.openshell.destructive_action_guard import DeletionSnapshot, SnapshotDirectory, SnapshotFile


def _case(mechanism: str, seed: str) -> dict[str, object]:
    return {
        "mechanism": mechanism,
        "triggered": True,
        "reason_code": "deletion_count_gt_500",
        "sandbox_terminated": True,
        "snapshot_restored": True,
        "observed_deleted_file_count": 501,
        "restored_file_count": 501,
        "residual_missing_file_count": 0,
        "run_id_sha256": seed * 64,
        "sandbox_id_sha256": chr(ord(seed) + 3) * 64,
        "transaction_receipt_sha256": chr(ord(seed) + 6) * 64,
        "guard_event_sha256": "a" * 64,
        "snapshot_manifest_sha256": "b" * 64,
        "snapshot_tree_sha256": "c" * 64,
        "raw_case_receipt_sha256": "d" * 64,
    }


def _normal() -> dict[str, object]:
    return {
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
        "run_id_sha256": "4" * 64,
        "sandbox_id_sha256": "7" * 64,
        "transaction_receipt_sha256": "0" * 64,
        "raw_case_receipt_sha256": "e" * 64,
    }


def _final_raw() -> dict[str, object]:
    cases = [_case("shell_rm", "1"), _case("python_shutil", "2"), _case("node_fs", "3")]
    normal = _normal()
    runs = sorted([case["run_id_sha256"] for case in cases] + [normal["run_id_sha256"]])
    transactions = sorted(
        [case["transaction_receipt_sha256"] for case in cases] + [normal["transaction_receipt_sha256"]]
    )
    return {
        "generated_at": "2026-07-16T12:00:00Z",
        "cases": cases,
        "normal_cleanup": normal,
        "run_set_sha256": runner._canonical_sha256(runs),
        "transaction_receipt_set_sha256": runner._canonical_sha256(transactions),
        "image_sha256": "1" * 64,
        "policy_sha256": "2" * 64,
        "mount_contract_sha256": "3" * 64,
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
        "source_sha256": runner._source_sha256(runner.REPO_ROOT),
    }


def test_build_evidence_matches_strict_schema() -> None:
    evidence = runner.build_evidence(
        project_root=runner.REPO_ROOT,
        final_raw=_final_raw(),
        raw_receipt_sha256="f" * 64,
    )
    schema = json.loads((runner.REPO_ROOT / runner.SCHEMA_RELATIVE).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(evidence)
    assert evidence["transactions"]["transaction_count"] == 4
    assert evidence["business_inference_exercised"] is False
    assert evidence["analysis_tree_outside_fixture_unchanged"] is True


def test_validate_rejects_transaction_reuse() -> None:
    evidence = runner.build_evidence(
        project_root=runner.REPO_ROOT,
        final_raw=_final_raw(),
        raw_receipt_sha256="f" * 64,
    )
    evidence["cases"][1]["run_id_sha256"] = evidence["cases"][0]["run_id_sha256"]

    with pytest.raises(runner.FormalDeleteGuardError, match="delete_evidence_transaction_reuse"):
        runner.validate_evidence(evidence)


def test_normal_cleanup_exercises_full_leaf_lifecycle(tmp_path: Path) -> None:
    target = tmp_path / "normal" / "delete"
    target.mkdir(parents=True)
    for index in range(runner.NORMAL_FILE_COUNT):
        (target / f"delete-{index:04d}.dat").write_text("fixture\n", encoding="ascii")

    result = subprocess.run(
        [sys.executable, "-c", runner.NORMAL_DELETE, str(target)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert not result.stderr
    payload = json.loads(result.stdout)
    assert payload["deleted_file_count"] == runner.NORMAL_FILE_COUNT
    assert all(payload[key] is True for key in (
        "mkdir", "create", "write", "overwrite", "rename", "small_delete", "recursive_cleanup"
    ))
    assert list(target.iterdir()) == []
    assert not (target.parent / "agent-created-leaf").exists()


def test_maintenance_lock_rejects_competing_process(tmp_path: Path) -> None:
    lock_dir = tmp_path / "var/openshell/locks"
    lock_dir.mkdir(parents=True, mode=0o700)
    lock_path = lock_dir / "maintenance.lock"
    lock_path.write_bytes(b"")
    lock_path.chmod(0o600)
    code = (
        "from pathlib import Path\n"
        "from scripts.openshell import run_formal_delete_guard as r\n"
        "try:\n"
        "  with r._maintenance_lock(Path(__import__('sys').argv[1])):\n"
        "    print('acquired')\n"
        "except r.FormalDeleteGuardError as exc:\n"
        "  print(exc.code)\n"
    )
    environment = os.environ.copy()
    environment.pop("SIQ_OPENSHELL_MAINTENANCE_FD", None)

    with runner._maintenance_lock(tmp_path):
        result = subprocess.run(
            [sys.executable, "-c", code, str(tmp_path)],
            cwd=runner.REPO_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.strip() == "delete_maintenance_lock_busy"


def test_snapshot_cleanup_verifier_allows_manifest_only_empty_directories(tmp_path: Path) -> None:
    snapshot_root = tmp_path / "snapshot"
    files_root = snapshot_root / "files/nonempty"
    files_root.mkdir(parents=True)
    content = b"fixed\n"
    (files_root / "file.txt").write_bytes(content)
    (snapshot_root / "snapshot-manifest.json").write_text("{}\n", encoding="ascii")
    snapshot = DeletionSnapshot(
        path=snapshot_root,
        analysis_relative_path="data/wiki/cn/companies/fixed/analysis",
        root_mode=0o700,
        files={
            "nonempty/file.txt": SnapshotFile(
                relative_path="nonempty/file.txt",
                byte_count=len(content),
                sha256=runner._sha256(content),
                mode=0o600,
                source_mtime_ns=1,
            )
        },
        directories={
            "empty": SnapshotDirectory(relative_path="empty", mode=0o700),
            "nonempty": SnapshotDirectory(relative_path="nonempty", mode=0o700),
        },
        tree_sha256="a" * 64,
    )

    runner._verify_snapshot_tree_for_removal(snapshot)


def test_cleanup_journal_resumes_after_delete_before_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir()
    analysis_root = tmp_path / "analysis"
    fixture_name = ".siq-openshell-delete-proof-resume"
    fixture = analysis_root / fixture_name
    fixture.mkdir(parents=True)
    (fixture / "fixed.dat").write_text("fixed\n", encoding="ascii")
    snapshot_root = tmp_path / runner.SNAPSHOT_ROOT_RELATIVE
    snapshot_root.mkdir(parents=True)
    run_ids = [f"formal-{index}" for index in range(4)]
    manifest_content = {run_id: f"manifest:{run_id}\n".encode("ascii") for run_id in run_ids}
    for run_id in run_ids:
        path = snapshot_root / run_id
        path.mkdir()
        (path / "snapshot-manifest.json").write_bytes(manifest_content[run_id])

    sources = {"runner_sha256": "1" * 64}
    outside_digest = "e" * 64
    fixture_digest = "f" * 64
    suite = {
        "suite_id": "resume",
        "fixture_name": fixture_name,
        "outside_analysis_tree_sha256": outside_digest,
    }
    projection = {
        "generated_at": "2026-07-16T12:00:00Z",
        "suite_id": "resume",
        "profile": "siq_analysis",
        "cases": [],
        "normal_cleanup": {},
        "case_receipt_sha256": {},
        "run_set_sha256": "2" * 64,
        "transaction_receipt_set_sha256": "3" * 64,
        "image_sha256": "4" * 64,
        "policy_sha256": "5" * 64,
        "mount_contract_sha256": "6" * 64,
        "outside_analysis_tree_sha256": outside_digest,
        "source_sha256": sources,
        "credential_material_present": False,
    }
    state = {
        "schema_version": runner.CLEANUP_STATE_SCHEMA_VERSION,
        "suite_id": "resume",
        "phase": "intent",
        "generated_at": "2026-07-16T12:00:00Z",
        "outside_analysis_tree_sha256": outside_digest,
        "fixture": {
            "name": fixture_name,
            "status": "pending",
            "file_count": 1,
            "tree_sha256": fixture_digest,
        },
        "snapshots": [
            {
                "run_id": run_id,
                "status": "pending",
                "manifest_sha256": runner._sha256(manifest_content[run_id]),
                "tree_sha256": "789a"[index] * 64,
            }
            for index, run_id in enumerate(run_ids)
        ],
        "final_projection": projection,
        "source_sha256": sources,
        "final_raw_sha256": "",
    }
    runner._write_exclusive(
        runner._cleanup_state_path(suite_dir),
        json.dumps(state, ensure_ascii=True, indent=2, sort_keys=True).encode("ascii") + b"\n",
    )

    monkeypatch.setattr(runner, "_source_sha256", lambda _root: sources)
    monkeypatch.setattr(
        runner,
        "_load_snapshot",
        lambda _root, run_id, _analysis: DeletionSnapshot(
            path=snapshot_root / run_id,
            analysis_relative_path="data/wiki/cn/companies/fixed/analysis",
            root_mode=0o700,
            files={},
            directories={},
            tree_sha256=next(item["tree_sha256"] for item in state["snapshots"] if item["run_id"] == run_id),
        ),
    )
    monkeypatch.setattr(runner, "_verify_snapshot_tree_for_removal", lambda _snapshot: None)
    original_stable = runner._stable_file
    monkeypatch.setattr(
        runner,
        "_stable_file",
        lambda path, **kwargs: (
            path.read_bytes() if path.name == "snapshot-manifest.json" else original_stable(path, **kwargs)
        ),
    )
    monkeypatch.setattr(
        runner,
        "_verify_fixture",
        lambda *_args, **_kwargs: {"file_count": 1, "tree_sha256": fixture_digest},
    )
    monkeypatch.setattr(runner, "_tree_sha256", lambda *_args, **_kwargs: outside_digest)
    original_rmtree = runner.shutil.rmtree
    calls = 0

    def fail_after_second_delete(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal calls
        original_rmtree(path, *args, **kwargs)
        calls += 1
        if calls == 2:
            raise RuntimeError("injected_crash")

    monkeypatch.setattr(runner.shutil, "rmtree", fail_after_second_delete)
    with pytest.raises(RuntimeError, match="injected_crash"):
        runner._resume_cleanup_sources(tmp_path, suite, suite_dir, analysis_root, state)

    checkpoint = json.loads(runner._cleanup_state_path(suite_dir).read_text(encoding="ascii"))
    assert checkpoint["phase"] == "cleaning"
    assert checkpoint["snapshots"][0]["status"] == "removed"
    assert checkpoint["snapshots"][1]["status"] == "pending"
    assert not (snapshot_root / run_ids[1]).exists()

    monkeypatch.setattr(runner.shutil, "rmtree", original_rmtree)
    resumed = runner._resume_cleanup_sources(tmp_path, suite, suite_dir, analysis_root, checkpoint)

    assert resumed["phase"] == "cleaned"
    assert resumed["fixture"]["status"] == "removed"
    assert all(item["status"] == "removed" for item in resumed["snapshots"])
    assert not fixture.exists()
