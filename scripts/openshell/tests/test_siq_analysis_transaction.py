from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path

import pytest

from scripts.openshell import siq_analysis_transaction as transaction

RESOURCES = dict(transaction.FORMAL_RESOURCES)
P0_RESOURCES = RESOURCES
SHA_A = "a" * 64
SHA_B = "b" * 64


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    return root


def _intent(run_id: str = "task-001") -> dict[str, str]:
    return {
        "profile": "siq_analysis",
        "run_id": run_id,
        "market": "cn",
        "company": "600104-SAIC",
        "run_dir": f"var/openshell/siq-analysis/runs/{run_id}",
        "sandbox_name": f"siq-analysis-{run_id}",
        "namespace": "siq-openshell-dev",
    }


def _create(root: Path, transaction_id: str = "tx-001", run_id: str = "task-001") -> dict:
    return transaction.create(
        root,
        transaction_id=transaction_id,
        intent=_intent(run_id),
        resources=RESOURCES,
    )


def _transition(root: Path, record: dict, phase: str, *, error_code: str | None = None) -> dict:
    return transaction.transition(
        root,
        record["transaction_id"],
        expected_generation=record["generation"],
        phase=phase,
        error_code=error_code,
    )


def _commit_all_present(root: Path, record: dict) -> dict:
    for resource in RESOURCES:
        record = transaction.bind_resource_intent(
            root,
            record["transaction_id"],
            expected_generation=record["generation"],
            resource=resource,
            intent_sha256=SHA_A,
        )
        record = transaction.commit_resource_present(
            root,
            record["transaction_id"],
            expected_generation=record["generation"],
            resource=resource,
            receipt_sha256=SHA_B,
        )
    return record


def _remove_all(root: Path, record: dict) -> dict:
    for resource in RESOURCES:
        if transaction.RESOURCE_DISPOSITIONS[resource] == "retain":
            continue
        record = transaction.update_resource(
            root,
            record["transaction_id"],
            expected_generation=record["generation"],
            resource=resource,
            state="removing",
        )
        record = transaction.update_resource(
            root,
            record["transaction_id"],
            expected_generation=record["generation"],
            resource=resource,
            state="removed",
        )
    return record


def _running(root: Path, record: dict) -> dict:
    record = _transition(root, record, "starting")
    record = _commit_all_present(root, record)
    return _transition(root, record, "running")


def _stopped(root: Path, record: dict) -> dict:
    record = _running(root, record)
    record = transaction.set_terminal_action(
        root,
        record["transaction_id"],
        expected_generation=record["generation"],
        action="stop",
    )
    record = _transition(root, record, "stopping")
    record = _remove_all(root, record)
    return _transition(root, record, "stopped")


def test_create_writes_private_durable_journal_before_exclusive_active_pointer(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path)
    fsync_calls: list[int] = []
    real_fsync = transaction.os.fsync

    def tracked_fsync(descriptor: int) -> None:
        fsync_calls.append(descriptor)
        real_fsync(descriptor)

    monkeypatch.setattr(transaction.os, "fsync", tracked_fsync)
    record = _create(root)

    state = root / "var/openshell/siq-analysis"
    journal = state / "transactions/tx-001.json"
    active = state / "active-run.json"
    assert record["schema_version"] == transaction.JOURNAL_SCHEMA
    assert record["phase"] == "intent"
    assert record["generation"] == 1
    assert record["terminal_action"] == ""
    assert set(record["resources"]) == set(RESOURCES)
    assert all(item["state"] == "pending" for item in record["resources"].values())
    assert {name: item["kind"] for name, item in record["resources"].items()} == RESOURCES
    assert {name: item["disposition"] for name, item in record["resources"].items()} == dict(
        transaction.RESOURCE_DISPOSITIONS
    )
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    assert stat.S_IMODE((state / "transactions").stat().st_mode) == 0o700
    assert stat.S_IMODE(journal.stat().st_mode) == 0o600
    assert stat.S_IMODE(active.stat().st_mode) == 0o600
    assert json.loads(active.read_text(encoding="utf-8"))["journal"] == (
        "var/openshell/siq-analysis/transactions/tx-001.json"
    )
    assert len(fsync_calls) >= 6


def test_create_uses_o_excl_and_does_not_leave_second_journal_on_active_conflict(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _create(root)

    with pytest.raises(transaction.TransactionError, match="active_run_conflict"):
        _create(root, transaction_id="tx-002", run_id="task-002")

    assert not (root / "var/openshell/siq-analysis/transactions/tx-002.json").exists()
    with pytest.raises(transaction.TransactionError, match="active_run_conflict"):
        _create(root)


def test_create_durably_aborts_when_active_parent_fsync_is_uncertain(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path)
    transaction.recover_discovery(root)
    state = root / "var/openshell/siq-analysis"
    active = state / "active-run.json"
    real_fsync_directory = transaction._fsync_directory_descriptor

    def fail_after_active_write(descriptor: int) -> None:
        if active.exists():
            raise transaction.TransactionError("directory_fsync_failed")
        real_fsync_directory(descriptor)

    monkeypatch.setattr(transaction, "_fsync_directory_descriptor", fail_after_active_write)
    with pytest.raises(transaction.TransactionError, match="directory_fsync_failed"):
        _create(root)

    assert not active.exists()
    assert not (state / "transactions/tx-001.json").exists()
    monkeypatch.setattr(transaction, "_fsync_directory_descriptor", real_fsync_directory)
    discovered = transaction.recover_discovery(root)
    assert discovered.has_active_pointer is False
    assert discovered.orphaned is False
    assert discovered.transaction is None


def test_load_rejects_symlink_wrong_mode_and_unknown_schema_without_leaking_paths(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _create(root)
    journal = root / "var/openshell/siq-analysis/transactions/tx-001.json"

    journal.chmod(0o644)
    with pytest.raises(transaction.TransactionError, match="state_file_unsafe") as failure:
        transaction.load(root, "tx-001")
    assert str(root) not in str(failure.value)

    journal.chmod(0o600)
    payload = json.loads(journal.read_text(encoding="utf-8"))
    payload["unexpected"] = True
    journal.write_text(json.dumps(payload), encoding="utf-8")
    journal.chmod(0o600)
    with pytest.raises(transaction.TransactionError, match="transaction_journal_invalid"):
        transaction.load(root, "tx-001")

    journal.unlink()
    journal.symlink_to(root / "outside.json")
    with pytest.raises(transaction.TransactionError, match="state_file_unsafe"):
        transaction.load(root, "tx-001")


def test_create_rejects_symlinked_private_state_directory(tmp_path: Path) -> None:
    root = _project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "var").mkdir()
    (root / "var/openshell").symlink_to(outside, target_is_directory=True)

    with pytest.raises(transaction.TransactionError, match="state_directory_invalid"):
        _create(root)

    assert list(outside.iterdir()) == []


def test_phase_graph_and_generation_are_strict_and_monotonic(tmp_path: Path) -> None:
    root = _project(tmp_path)
    record = _create(root)
    generations = [record["generation"]]
    record = _transition(root, record, "starting")
    generations.append(record["generation"])
    record = _commit_all_present(root, record)
    record = _transition(root, record, "running")
    generations.append(record["generation"])
    record = transaction.set_terminal_action(
        root,
        "tx-001",
        expected_generation=record["generation"],
        action="stop",
    )
    record = _transition(root, record, "stopping")
    generations.append(record["generation"])
    record = _remove_all(root, record)
    record = _transition(root, record, "stopped")
    generations.append(record["generation"])

    assert generations == sorted(generations)
    assert len(set(generations)) == len(generations)
    assert record["phase"] == "stopped"
    with pytest.raises(transaction.TransactionError, match="phase_transition_invalid"):
        transaction.transition(
            root,
            "tx-001",
            expected_generation=record["generation"],
            phase="running",
        )
    with pytest.raises(transaction.TransactionError, match="transaction_generation_conflict"):
        transaction.transition(root, "tx-001", expected_generation=1, phase="starting")
    with pytest.raises(transaction.TransactionError, match="transaction_generation_conflict"):
        transaction.transition(root, "tx-001", expected_generation=True, phase="starting")


def test_rollback_graph_is_fixed_and_records_stable_error_code(tmp_path: Path) -> None:
    root = _project(tmp_path)
    record = _transition(root, _create(root), "starting")
    record = transaction.bind_resource_intent(
        root,
        "tx-001",
        expected_generation=record["generation"],
        resource="run_dir",
        intent_sha256=SHA_A,
    )
    record = transaction.commit_resource_present(
        root,
        "tx-001",
        expected_generation=record["generation"],
        resource="run_dir",
        receipt_sha256=SHA_B,
    )
    record = transaction.set_terminal_action(
        root,
        "tx-001",
        expected_generation=record["generation"],
        action="failed_start",
    )
    record = _transition(root, record, "rollback_pending", error_code="sandbox_create_interrupted")
    with pytest.raises(transaction.TransactionError, match="transaction_resources_incomplete"):
        _transition(root, record, "rolled_back")
    record = _remove_all(root, record)
    record = _transition(root, record, "rolled_back")

    assert record["phase"] == "rolled_back"
    assert record["error_code"] == "sandbox_create_interrupted"
    assert record["terminal_action"] == "failed_start"
    assert record["resources"]["run_dir"]["state"] == "present"


def test_resource_receipts_follow_fixed_states_and_share_journal_generation(tmp_path: Path) -> None:
    root = _project(tmp_path)
    record = _transition(root, _create(root), "starting")
    record = transaction.bind_resource_intent(
        root,
        "tx-001",
        expected_generation=record["generation"],
        resource="guard",
        intent_sha256=SHA_A,
    )
    record = transaction.commit_resource_present(
        root,
        "tx-001",
        expected_generation=record["generation"],
        resource="guard",
        receipt_sha256=SHA_B,
    )
    assert record["resources"]["guard"]["state"] == "present"
    record = transaction.set_terminal_action(
        root,
        "tx-001",
        expected_generation=record["generation"],
        action="failed_start",
    )
    record = _transition(root, record, "rollback_pending")
    for state in ("removing", "removed"):
        record = transaction.update_resource(
            root,
            "tx-001",
            expected_generation=record["generation"],
            resource="guard",
            state=state,
        )
        assert record["resources"]["guard"]["generation"] == record["generation"]
        assert record["resources"]["guard"]["state"] == state

    with pytest.raises(transaction.TransactionError, match="resource_transition_invalid"):
        transaction.update_resource(
            root,
            "tx-001",
            expected_generation=record["generation"],
            resource="guard",
            state="present",
        )


def test_pending_resource_can_enter_cleanup_without_false_present_receipt(tmp_path: Path) -> None:
    root = _project(tmp_path)
    record = _transition(root, _create(root), "starting")
    record = transaction.set_terminal_action(
        root,
        "tx-001",
        expected_generation=record["generation"],
        action="failed_start",
    )
    record = _transition(root, record, "rollback_pending")
    record = transaction.update_resource(
        root,
        "tx-001",
        expected_generation=record["generation"],
        resource="sandbox",
        state="removing",
    )
    record = transaction.update_resource(
        root,
        "tx-001",
        expected_generation=record["generation"],
        resource="sandbox",
        state="removed",
    )
    assert record["resources"]["sandbox"]["state"] == "removed"


def test_unacquired_retained_run_dir_can_finish_failed_start_rollback(tmp_path: Path) -> None:
    root = _project(tmp_path)
    record = _transition(root, _create(root), "starting")
    record = transaction.set_terminal_action(
        root,
        "tx-001",
        expected_generation=record["generation"],
        action="failed_start",
    )
    record = _transition(root, record, "rollback_pending")
    for resource in RESOURCES:
        record = transaction.update_resource(
            root,
            "tx-001",
            expected_generation=record["generation"],
            resource=resource,
            state="removing",
        )
        record = transaction.update_resource(
            root,
            "tx-001",
            expected_generation=record["generation"],
            resource=resource,
            state="removed",
        )

    record = _transition(root, record, "rolled_back")
    assert record["resources"]["run_dir"]["state"] == "removed"
    assert not record["resources"]["run_dir"]["receipt_sha256"]


def test_atomic_replace_preserves_private_mode_and_changes_inode(tmp_path: Path) -> None:
    root = _project(tmp_path)
    record = _create(root)
    journal = root / "var/openshell/siq-analysis/transactions/tx-001.json"
    before = journal.stat()

    _transition(root, record, "starting")

    after = journal.stat()
    assert before.st_ino != after.st_ino
    assert stat.S_IMODE(after.st_mode) == 0o600


def test_finalize_requires_terminal_journal_then_fsync_unlinks_active_and_is_idempotent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _project(tmp_path)
    record = _create(root)
    with pytest.raises(transaction.TransactionError, match="transaction_not_terminal"):
        transaction.finalize(root, "tx-001")

    record = _stopped(root, record)
    assert record["resources"]["run_dir"]["state"] == "present"
    assert record["resources"]["run_dir"]["disposition"] == "retain"
    assert all(receipt["state"] == "removed" for name, receipt in record["resources"].items() if name != "run_dir")
    fsync_calls: list[int] = []
    real_fsync = transaction.os.fsync

    def tracked_fsync(descriptor: int) -> None:
        fsync_calls.append(descriptor)
        real_fsync(descriptor)

    monkeypatch.setattr(transaction.os, "fsync", tracked_fsync)
    finalized = transaction.finalize(root, "tx-001")

    assert finalized["phase"] == "stopped"
    assert not (root / transaction.ACTIVE_RELATIVE).exists()
    assert (root / "var/openshell/siq-analysis/transactions/tx-001.json").exists()
    assert fsync_calls
    assert transaction.finalize(root, "tx-001")["phase"] == "stopped"


def test_retained_run_directory_cannot_enter_removal(tmp_path: Path) -> None:
    root = _project(tmp_path)
    record = _running(root, _create(root))

    with pytest.raises(transaction.TransactionError, match="resource_transition_invalid"):
        transaction.update_resource(
            root,
            "tx-001",
            expected_generation=record["generation"],
            resource="run_dir",
            state="removing",
        )


def test_recovery_discovers_active_orphan_and_terminal_pending_finalize(tmp_path: Path) -> None:
    root = _project(tmp_path)
    record = _create(root)

    active = transaction.recover_discovery(root)
    assert active.transaction == record
    assert active.has_active_pointer is True
    assert active.orphaned is False
    assert active.terminal_pending_finalize is False

    active_path = root / transaction.ACTIVE_RELATIVE
    active_path.unlink()
    orphan = transaction.recover_discovery(root)
    assert orphan.transaction == record
    assert orphan.has_active_pointer is False
    assert orphan.orphaned is True

    pointer = {
        "schema_version": transaction.ACTIVE_SCHEMA,
        "transaction_id": "tx-001",
        "run_id": "task-001",
        "journal": "var/openshell/siq-analysis/transactions/tx-001.json",
        "created_at": record["created_at"],
    }
    transaction._write_exclusive_json(active_path, pointer, conflict_code="active_run_conflict")
    record = _stopped(root, record)
    terminal = transaction.recover_discovery(root)
    assert terminal.transaction == record
    assert terminal.terminal_pending_finalize is True


def test_recovery_removes_safe_stale_atomic_temp_and_rejects_ambiguous_nonterminal_journals(tmp_path: Path) -> None:
    root = _project(tmp_path)
    first = _create(root)
    state = root / "var/openshell/siq-analysis"
    active = state / "active-run.json"
    active.unlink()
    temporary = state / "transactions/.tx-001.json.0123456789abcdef.tmp"
    temporary.touch()
    temporary.chmod(0o600)

    discovered = transaction.recover_discovery(root)
    assert discovered.transaction == first
    assert not temporary.exists()

    second = dict(first)
    second["transaction_id"] = "tx-002"
    second["intent"] = _intent("task-002")
    journal = state / "transactions/tx-002.json"
    transaction._write_exclusive_json(journal, second, conflict_code="transaction_conflict")
    with pytest.raises(transaction.TransactionError, match="recovery_state_conflict"):
        transaction.recover_discovery(root)


def test_errors_are_stable_and_do_not_include_invalid_values(tmp_path: Path) -> None:
    root = _project(tmp_path)
    secret_value = "secret/value/that/must/not/leak"
    intent = _intent()
    intent["company"] = secret_value

    with pytest.raises(transaction.TransactionError) as failure:
        transaction.create(root, transaction_id="tx-001", intent=intent, resources=RESOURCES)

    assert failure.value.code == "transaction_intent_invalid"
    assert secret_value not in str(failure.value)


def test_p0_create_rejects_second_transaction_when_nonterminal_orphan_exists(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _create(root)
    active = root / transaction.ACTIVE_RELATIVE
    active.unlink()
    transaction._fsync_directory(active.parent)

    with pytest.raises(transaction.TransactionError, match="recovery_required"):
        transaction.create(
            root,
            transaction_id="tx-002",
            intent=_intent("task-002"),
            resources=P0_RESOURCES,
        )

    assert not (root / "var/openshell/siq-analysis/transactions/tx-002.json").exists()


def test_p0_claim_orphan_reinstalls_exact_active_pointer(tmp_path: Path) -> None:
    root = _project(tmp_path)
    record = _create(root)
    active = root / transaction.ACTIVE_RELATIVE
    active.unlink()
    transaction._fsync_directory(active.parent)

    with pytest.raises(transaction.TransactionError, match="recovery_state_conflict"):
        transaction.claim_orphan(root, "tx-002")
    assert not active.exists()

    claimed = transaction.claim_orphan(root, "tx-001")

    assert claimed == record
    assert json.loads(active.read_text(encoding="utf-8")) == {
        "schema_version": transaction.ACTIVE_SCHEMA,
        "transaction_id": "tx-001",
        "run_id": "task-001",
        "journal": "var/openshell/siq-analysis/transactions/tx-001.json",
        "created_at": record["created_at"],
    }
    assert transaction.recover_discovery(root).has_active_pointer is True


def test_p0_resource_intent_and_receipt_are_digest_bound(tmp_path: Path) -> None:
    root = _project(tmp_path)
    record = transaction.create(
        root,
        transaction_id="tx-001",
        intent=_intent(),
        resources=P0_RESOURCES,
    )
    record = _transition(root, record, "starting")
    guard = record["resources"]["guard"]
    assert guard == {
        "kind": "process",
        "disposition": "remove",
        "state": "pending",
        "generation": 1,
        "intent_sha256": "",
        "receipt_sha256": "",
        "updated_at": record["created_at"],
    }

    with pytest.raises(transaction.TransactionError, match="resource_digest_invalid"):
        transaction.bind_resource_intent(
            root,
            "tx-001",
            expected_generation=record["generation"],
            resource="guard",
            intent_sha256="A" * 64,
        )
    with pytest.raises(transaction.TransactionError, match="resource_transition_invalid"):
        transaction.commit_resource_present(
            root,
            "tx-001",
            expected_generation=record["generation"],
            resource="guard",
            receipt_sha256=SHA_B,
        )

    record = transaction.bind_resource_intent(
        root,
        "tx-001",
        expected_generation=record["generation"],
        resource="guard",
        intent_sha256=SHA_A,
    )
    record = transaction.commit_resource_present(
        root,
        "tx-001",
        expected_generation=record["generation"],
        resource="guard",
        receipt_sha256=SHA_B,
    )
    assert record["resources"]["guard"]["intent_sha256"] == SHA_A
    assert record["resources"]["guard"]["receipt_sha256"] == SHA_B
    assert record["resources"]["guard"]["state"] == "present"

    record = transaction.set_terminal_action(
        root,
        "tx-001",
        expected_generation=record["generation"],
        action="failed_start",
    )
    record = _transition(root, record, "rollback_pending")
    record = transaction.update_resource(
        root,
        "tx-001",
        expected_generation=record["generation"],
        resource="guard",
        state="removing",
    )
    record = transaction.update_resource(
        root,
        "tx-001",
        expected_generation=record["generation"],
        resource="guard",
        state="removed",
    )
    assert record["resources"]["guard"]["intent_sha256"] == SHA_A
    assert record["resources"]["guard"]["receipt_sha256"] == SHA_B


def test_resource_mutations_are_rejected_before_wrong_phase_is_persisted(tmp_path: Path) -> None:
    root = _project(tmp_path)
    created = _create(root)
    journal = root / transaction.TRANSACTIONS_RELATIVE / "tx-001.json"
    before = journal.read_bytes()

    with pytest.raises(transaction.TransactionError, match="resource_transition_invalid"):
        transaction.bind_resource_intent(
            root,
            "tx-001",
            expected_generation=created["generation"],
            resource="guard",
            intent_sha256=SHA_A,
        )
    assert journal.read_bytes() == before

    running = _running(root, created)
    before = journal.read_bytes()
    with pytest.raises(transaction.TransactionError, match="resource_transition_invalid"):
        transaction.update_resource(
            root,
            "tx-001",
            expected_generation=running["generation"],
            resource="guard",
            state="removing",
        )
    assert journal.read_bytes() == before
    assert transaction.load(root, "tx-001") == running


def test_p0_pending_resources_block_running_and_removed_gate_blocks_terminal(tmp_path: Path) -> None:
    root = _project(tmp_path)
    record = transaction.create(
        root,
        transaction_id="tx-001",
        intent=_intent(),
        resources=P0_RESOURCES,
    )
    record = _transition(root, record, "starting")
    with pytest.raises(transaction.TransactionError, match="transaction_resources_incomplete"):
        _transition(root, record, "running")

    for resource in P0_RESOURCES:
        record = transaction.bind_resource_intent(
            root,
            "tx-001",
            expected_generation=record["generation"],
            resource=resource,
            intent_sha256=SHA_A,
        )
        record = transaction.commit_resource_present(
            root,
            "tx-001",
            expected_generation=record["generation"],
            resource=resource,
            receipt_sha256=SHA_B,
        )
    record = _transition(root, record, "running")
    record = transaction.set_terminal_action(
        root,
        "tx-001",
        expected_generation=record["generation"],
        action="stop",
    )
    record = _transition(root, record, "stopping")
    with pytest.raises(transaction.TransactionError, match="transaction_resources_incomplete"):
        _transition(root, record, "stopped")
    with pytest.raises(transaction.TransactionError, match="transaction_not_terminal"):
        transaction.finalize(root, "tx-001")


def test_p0_repair_private_partial_active_from_unique_nonterminal_journal(tmp_path: Path) -> None:
    root = _project(tmp_path)
    record = _create(root)
    active = root / transaction.ACTIVE_RELATIVE
    active.unlink()
    active.touch(mode=0o600)
    transaction._fsync_directory(active.parent)

    repaired = transaction.repair_active_from_journal(root)

    assert repaired == record
    assert transaction.recover_discovery(root).has_active_pointer is True


def test_repair_private_corrupt_active_from_unique_nonterminal_journal(tmp_path: Path) -> None:
    root = _project(tmp_path)
    record = _create(root)
    active = root / transaction.ACTIVE_RELATIVE
    active.write_text('{"schema_version":', encoding="utf-8")
    active.chmod(0o600)

    repaired = transaction.repair_active_from_journal(root)

    assert repaired == record
    assert json.loads(active.read_text(encoding="utf-8"))["created_at"] == record["created_at"]


def test_terminal_action_is_required_once_and_phase_scoped(tmp_path: Path) -> None:
    root = _project(tmp_path)
    record = _running(root, _create(root))

    with pytest.raises(transaction.TransactionError, match="terminal_action_required"):
        _transition(root, record, "stopping")
    with pytest.raises(transaction.TransactionError, match="terminal_action_invalid"):
        transaction.set_terminal_action(
            root,
            "tx-001",
            expected_generation=record["generation"],
            action="failed_start",
        )

    record = transaction.set_terminal_action(
        root,
        "tx-001",
        expected_generation=record["generation"],
        action="rollback_to_host",
    )
    with pytest.raises(transaction.TransactionError, match="terminal_action_invalid"):
        transaction.set_terminal_action(
            root,
            "tx-001",
            expected_generation=record["generation"],
            action="stop",
        )
    record = _transition(root, record, "stopping")
    assert record["terminal_action"] == "rollback_to_host"


def test_cross_field_invalid_terminal_journal_blocks_finalize_and_new_create(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _create(root)
    journal = root / transaction.TRANSACTIONS_RELATIVE / "tx-001.json"
    payload = json.loads(journal.read_text(encoding="utf-8"))
    payload["phase"] = "stopped"
    payload["terminal_action"] = "stop"
    payload["generation"] += 1
    transaction._replace_json(journal, payload)

    with pytest.raises(transaction.TransactionError, match="transaction_resources_invalid"):
        transaction.finalize(root, "tx-001")

    active = root / transaction.ACTIVE_RELATIVE
    active.unlink()
    transaction._fsync_directory(active.parent)
    with pytest.raises(transaction.TransactionError, match="transaction_resources_invalid"):
        transaction.create(
            root,
            transaction_id="tx-002",
            intent=_intent("task-002"),
            resources=RESOURCES,
        )
    assert not (root / transaction.TRANSACTIONS_RELATIVE / "tx-002.json").exists()


def test_finalize_repeats_terminal_resource_gate(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path)
    journal = _create(root)
    journal["phase"] = "stopped"
    journal["terminal_action"] = "stop"

    def invalid_terminal(*args, **kwargs) -> dict:
        return journal

    monkeypatch.setattr(transaction, "_load_journal", invalid_terminal)
    with pytest.raises(transaction.TransactionError, match="transaction_resources_incomplete"):
        transaction.finalize(root, "tx-001")


@pytest.mark.parametrize("unsafe_kind", ["mode", "symlink"])
def test_repair_rejects_unsafe_active_pointer(tmp_path: Path, unsafe_kind: str) -> None:
    root = _project(tmp_path)
    _create(root)
    active = root / transaction.ACTIVE_RELATIVE
    active.unlink()
    if unsafe_kind == "mode":
        active.touch(mode=0o600)
        active.chmod(0o644)
    else:
        active.symlink_to(root / transaction.TRANSACTIONS_RELATIVE / "tx-001.json")

    with pytest.raises(transaction.TransactionError, match="active_pointer_unsafe"):
        transaction.repair_active_from_journal(root)


def test_repair_rejects_multiple_nonterminal_journals(tmp_path: Path) -> None:
    root = _project(tmp_path)
    first = _create(root)
    active = root / transaction.ACTIVE_RELATIVE
    active.unlink()
    active.touch(mode=0o600)
    second = json.loads(json.dumps(first))
    second["transaction_id"] = "tx-002"
    second["intent"] = _intent("task-002")
    transaction._write_exclusive_json(
        root / transaction.TRANSACTIONS_RELATIVE / "tx-002.json",
        second,
        conflict_code="transaction_conflict",
    )

    with pytest.raises(transaction.TransactionError, match="recovery_state_conflict"):
        transaction.repair_active_from_journal(root)


def test_active_pointer_created_at_is_bound_to_journal(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _create(root)
    active = root / transaction.ACTIVE_RELATIVE
    payload = json.loads(active.read_text(encoding="utf-8"))
    payload["created_at"] = "2000-01-01T00:00:00Z"
    transaction._replace_json(active, payload)

    with pytest.raises(transaction.TransactionError, match="recovery_state_conflict"):
        transaction.recover_discovery(root)


def test_exclusive_install_links_only_after_staged_file_fsync(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path)
    transaction.recover_discovery(root)
    target = root / transaction.TRANSACTIONS_RELATIVE / "tx-001.json"
    events: list[str] = []
    real_write_all = transaction._write_all
    real_link = transaction._link_descriptor_exclusive

    def tracked_write_all(descriptor: int, content: bytes) -> None:
        real_write_all(descriptor, content)
        events.append("file_fsynced")

    def tracked_link(*args, **kwargs) -> None:
        events.append("link")
        real_link(*args, **kwargs)

    monkeypatch.setattr(transaction, "_write_all", tracked_write_all)
    monkeypatch.setattr(transaction, "_link_descriptor_exclusive", tracked_link)
    transaction._write_exclusive_json(target, {"complete": True}, conflict_code="transaction_conflict")

    assert events == ["file_fsynced", "link"]
    assert json.loads(target.read_text(encoding="utf-8")) == {"complete": True}


def test_staged_fd_content_tamper_removes_incorrect_final_entry(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path)
    transaction.recover_discovery(root)
    target = root / transaction.TRANSACTIONS_RELATIVE / "tx-001.json"
    real_link = transaction._link_descriptor_exclusive

    def tamper_then_link(descriptor: int, parent_descriptor: int, target_name: str) -> None:
        transaction.os.pwrite(descriptor, b"X", 0)
        real_link(descriptor, parent_descriptor, target_name)

    monkeypatch.setattr(transaction, "_link_descriptor_exclusive", tamper_then_link)
    with pytest.raises(transaction.TransactionError, match="temporary_state_changed"):
        transaction._write_exclusive_json(target, {"complete": True}, conflict_code="transaction_conflict")

    assert not target.exists()
    assert not any(transaction.TEMPORARY_RE.fullmatch(item.name) for item in target.parent.iterdir())


def test_replace_rollback_refuses_partial_exchange_identity(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path)
    transaction.recover_discovery(root)
    target = root / transaction.TRANSACTIONS_RELATIVE / "tx-001.json"
    transaction._write_exclusive_json(target, {"generation": 1}, conflict_code="transaction_conflict")
    real_exchange = transaction._rename_exchange
    injected = False

    def replace_displaced_target(parent_descriptor: int, left_name: str, right_name: str) -> None:
        nonlocal injected
        real_exchange(parent_descriptor, left_name, right_name)
        if injected:
            return
        injected = True
        held_name = f"{left_name}.held"
        os.rename(left_name, held_name, src_dir_fd=parent_descriptor, dst_dir_fd=parent_descriptor)
        descriptor = os.open(
            left_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=parent_descriptor,
        )
        try:
            os.write(descriptor, b'{"attacker":true}\n')
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    monkeypatch.setattr(transaction, "_rename_exchange", replace_displaced_target)
    with pytest.raises(transaction.TransactionError, match="state_replace_rollback_failed"):
        transaction._replace_json(target, {"generation": 2})

    assert json.loads(target.read_text(encoding="utf-8")) == {"generation": 2}
    assert b"attacker" not in target.read_bytes()


def test_locked_transaction_dirfd_survives_namespace_path_swap(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path)
    record = _create(root)
    state = root / transaction.STATE_RELATIVE
    transactions = root / transaction.TRANSACTIONS_RELATIVE
    hidden = state / "transactions-held"
    real_replace = transaction._replace_json
    swapped = False

    def swap_namespace(path: Path, value: dict, *, parent_descriptor: int | None = None) -> None:
        nonlocal swapped
        if not swapped:
            swapped = True
            transactions.rename(hidden)
            transactions.mkdir(mode=0o700)
            try:
                real_replace(path, value, parent_descriptor=parent_descriptor)
            finally:
                transactions.rmdir()
                hidden.rename(transactions)
        else:
            real_replace(path, value, parent_descriptor=parent_descriptor)

    monkeypatch.setattr(transaction, "_replace_json", swap_namespace)
    transitioned = _transition(root, record, "starting")

    assert transitioned["phase"] == "starting"
    assert json.loads((transactions / "tx-001.json").read_text(encoding="utf-8"))["phase"] == "starting"


def test_snapshot_rejects_same_size_mtime_restored_rewrite(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _create(root)
    journal = root / transaction.TRANSACTIONS_RELATIVE / "tx-001.json"
    before = journal.stat()
    content = journal.read_bytes().replace(b"600104-SAIC", b"600104-SBIC")
    journal.write_bytes(content)
    os.utime(journal, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = journal.stat()
    assert before.st_size == after.st_size
    assert before.st_mtime_ns == after.st_mtime_ns
    assert before.st_ctime_ns != after.st_ctime_ns
    with pytest.raises(transaction.TransactionError, match="state_file_changed"):
        transaction._replace_json_matching(journal, json.loads(content), before)


def test_torn_staged_journal_never_appears_at_final_path(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path)
    transaction.recover_discovery(root)
    transactions = root / transaction.TRANSACTIONS_RELATIVE
    target = transactions / "tx-001.json"

    def fail_partial_write(descriptor: int, content: bytes) -> None:
        transaction.os.write(descriptor, content[:8])
        raise transaction.TransactionError("state_write_failed")

    monkeypatch.setattr(transaction, "_write_all", fail_partial_write)
    with pytest.raises(transaction.TransactionError, match="state_write_failed"):
        transaction._write_exclusive_json(target, {"complete": True}, conflict_code="transaction_conflict")

    assert not target.exists()
    assert not any(transaction.TEMPORARY_RE.fullmatch(item.name) for item in transactions.iterdir())


def test_torn_staged_active_cleans_new_journal_and_never_appears_partial(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path)
    real_write_all = transaction._write_all
    calls = 0

    def fail_second_write(descriptor: int, content: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            transaction.os.write(descriptor, content[:8])
            raise transaction.TransactionError("state_write_failed")
        real_write_all(descriptor, content)

    monkeypatch.setattr(transaction, "_write_all", fail_second_write)
    with pytest.raises(transaction.TransactionError, match="state_write_failed"):
        _create(root)

    state = root / transaction.STATE_RELATIVE
    transactions = root / transaction.TRANSACTIONS_RELATIVE
    assert not (root / transaction.ACTIVE_RELATIVE).exists()
    assert not (transactions / "tx-001.json").exists()
    assert not any(transaction.TEMPORARY_RE.fullmatch(item.name) for item in state.iterdir())
    assert not any(transaction.TEMPORARY_RE.fullmatch(item.name) for item in transactions.iterdir())


def test_identity_market_and_formal_resource_set_are_exact(tmp_path: Path) -> None:
    root = _project(tmp_path)
    long_intent = _intent("a" * 49)
    with pytest.raises(transaction.TransactionError, match="run_id_invalid"):
        transaction.create(root, transaction_id="tx-001", intent=long_intent, resources=RESOURCES)

    invalid_market = _intent()
    invalid_market["market"] = "ca"
    with pytest.raises(transaction.TransactionError, match="transaction_intent_invalid"):
        transaction.create(root, transaction_id="tx-001", intent=invalid_market, resources=RESOURCES)

    missing_resource = dict(RESOURCES)
    missing_resource.pop("run_dir")
    with pytest.raises(transaction.TransactionError, match="transaction_resources_invalid"):
        transaction.create(root, transaction_id="tx-001", intent=_intent(), resources=missing_resource)

    wrong_kind = dict(RESOURCES)
    wrong_kind["guard"] = "sandbox"
    with pytest.raises(transaction.TransactionError, match="transaction_resources_invalid"):
        transaction.create(root, transaction_id="tx-001", intent=_intent(), resources=wrong_kind)


def test_readable_legacy_journal_is_explicitly_rejected_without_fake_migration(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _create(root)
    journal = root / transaction.TRANSACTIONS_RELATIVE / "tx-001.json"
    payload = json.loads(journal.read_text(encoding="utf-8"))
    payload["schema_version"] = transaction.LEGACY_JOURNAL_SCHEMA
    transaction._replace_json(journal, payload)

    with pytest.raises(transaction.TransactionError, match="legacy_state_migration_required"):
        transaction.load(root, "tx-001")


def test_readable_legacy_active_pointer_is_not_overwritten_by_repair(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _create(root)
    active = root / transaction.ACTIVE_RELATIVE
    payload = json.loads(active.read_text(encoding="utf-8"))
    payload["schema_version"] = transaction.LEGACY_ACTIVE_SCHEMA
    transaction._replace_json(active, payload)

    with pytest.raises(transaction.TransactionError, match="legacy_state_migration_required"):
        transaction.repair_active_from_journal(root)
    assert json.loads(active.read_text(encoding="utf-8"))["schema_version"] == transaction.LEGACY_ACTIVE_SCHEMA


def test_replacing_lock_filename_cannot_create_a_second_mutex(tmp_path: Path) -> None:
    root = _project(tmp_path)
    transaction.recover_discovery(root)
    holder_entered = threading.Event()
    holder_release = threading.Event()
    contender_entered = threading.Event()
    holder_errors: list[str] = []
    contender_errors: list[str] = []

    def hold_lock() -> None:
        try:
            with transaction._state_lock(root):
                holder_entered.set()
                holder_release.wait(2)
        except transaction.TransactionError as exc:
            holder_errors.append(exc.code)

    def contend_for_lock() -> None:
        try:
            with transaction._state_lock(root):
                contender_entered.set()
        except transaction.TransactionError as exc:
            contender_errors.append(exc.code)

    holder = threading.Thread(target=hold_lock)
    contender = threading.Thread(target=contend_for_lock)
    holder.start()
    assert holder_entered.wait(2)
    lock_path = root / transaction.LOCK_RELATIVE
    lock_path.unlink()
    lock_path.touch(mode=0o600)
    contender.start()
    try:
        assert not contender_entered.wait(0.2)
    finally:
        holder_release.set()
    holder.join(2)
    contender.join(2)

    assert not holder.is_alive()
    assert not contender.is_alive()
    assert contender_entered.is_set()
    assert holder_errors == ["transaction_lock_changed"]
    assert contender_errors == []
