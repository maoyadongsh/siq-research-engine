from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts.openshell import run_memory_write_probe as probe


def _postgres_outcomes() -> dict[str, dict[str, object]]:
    outcome = {
        "insert": True,
        "readback": True,
        "rollback": True,
        "post_rollback_verify": True,
        "residual_count": 0,
    }
    return {group: dict(outcome) for group in probe.AGENT_GROUPS}


def _milvus_outcomes() -> dict[str, dict[str, object]]:
    outcome = {
        "upsert": True,
        "get": True,
        "search": True,
        "delete": True,
        "post_delete_verify": True,
        "residual_count": 0,
    }
    return {group: dict(outcome) for group in probe.AGENT_GROUPS}


def test_receipts_match_strict_builder_contract_without_record_identifiers() -> None:
    postgres, milvus = probe._receipts(
        started=2_000_000_000,
        completed=2_000_000_010,
        nonce=b"x" * 32,
        postgres_outcomes=_postgres_outcomes(),
        postgres_residual=0,
        milvus_outcomes=_milvus_outcomes(),
        milvus_residual=0,
        physical_collection="siq_agent_memory__v2",
    )

    assert set(postgres) == {
        "agent_groups",
        "backend",
        "captured_at_unix",
        "completed_at_unix",
        "executor",
        "probe_sha256",
        "residual_count",
        "schema_version",
    }
    assert set(milvus) == {
        "agent_groups",
        "backend",
        "captured_at_unix",
        "completed_at_unix",
        "executor",
        "logical_alias",
        "physical_collection",
        "probe_sha256",
        "required_schema_version",
        "residual_count",
        "schema_preflight_passed",
        "schema_version",
    }
    serialized = json.dumps([postgres, milvus], sort_keys=True)
    assert "openshell-memory-probe-primary" not in serialized
    assert "openshell-memory-probe-secondary" not in serialized
    assert "postgresql://" not in serialized
    assert postgres["residual_count"] == 0
    assert milvus["residual_count"] == 0


def test_receipt_writer_is_atomic_owner_only_and_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "proofs" / "receipt.json"
    probe._write_atomic(target, {"ok": True})
    assert json.loads(target.read_text(encoding="ascii")) == {"ok": True}
    assert os.stat(target).st_mode & 0o777 == 0o600

    target.unlink()
    outside = tmp_path / "outside.json"
    outside.write_text("unchanged\n", encoding="ascii")
    target.symlink_to(outside)
    with pytest.raises(probe.MemoryProbeError, match="^receipt_output_unsafe$"):
        probe._write_atomic(target, {"ok": False})
    assert outside.read_text(encoding="ascii") == "unchanged\n"


def test_probe_lock_rejects_parallel_holder(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir(mode=0o700)
    with probe._probe_lock(root):
        with pytest.raises(probe.MemoryProbeError, match="^memory_probe_already_running$"):
            with probe._probe_lock(root):
                pass


def test_runtime_environment_requires_postgres_and_fixed_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in probe.RUNTIME_ENV_NAMES:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(probe.MemoryProbeError, match="^postgres_runtime_not_configured$"):
        probe._apply_runtime_environment(None, project_root=probe.REPO_ROOT)

    monkeypatch.setenv("SIQ_APP_DATABASE_URL", "postgresql://redacted")
    monkeypatch.setenv("SIQ_AGENT_MEMORY_MILVUS_COLLECTION", "unexpected")
    with pytest.raises(probe.MemoryProbeError, match="^milvus_alias_not_allowlisted$"):
        probe._apply_runtime_environment(None, project_root=probe.REPO_ROOT)


def test_cli_never_echoes_unexpected_exception(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def fail(**_kwargs: object) -> tuple[Path, Path]:
        raise RuntimeError("postgresql://user:secret@host/database")

    monkeypatch.setattr(probe, "run_probe", fail)
    assert probe.main([]) == 1
    output = capsys.readouterr().out
    assert "secret" not in output
    assert json.loads(output) == {
        "decision": "NO_GO",
        "error_code": "memory_write_probe_failed",
        "ok": False,
    }
