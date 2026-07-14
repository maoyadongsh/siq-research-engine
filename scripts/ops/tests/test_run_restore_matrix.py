from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "scripts" / "ops" / "run_restore_matrix.py"
spec = importlib.util.spec_from_file_location("run_restore_matrix_under_test", SOURCE)
assert spec and spec.loader
matrix = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = matrix
spec.loader.exec_module(matrix)


@pytest.fixture(autouse=True)
def _required_voiceprint_checkpoint(monkeypatch):
    monkeypatch.setenv(matrix.VOICEPRINT_EXPECTED_COUNT_ENV, "0")
    monkeypatch.setenv(
        matrix.VOICEPRINT_EXPECTED_HEAD_ENV,
        matrix.EMPTY_TOMBSTONE_HEAD_HMAC,
    )


def _backup_dir(tmp_path: Path) -> Path:
    root = tmp_path / "backup"
    postgres = root / "postgres"
    postgres.mkdir(parents=True)
    databases = [target.database for target in matrix.TARGETS]
    lines: list[str] = []
    manifest_lines = [
        "timestamp=20260713_120000",
        "backup_mode=required",
        "skip_large=0",
        f"postgres_databases={','.join(databases)}",
        f"schema_contract_version={matrix.SCHEMA_CONTRACT_VERSION}",
    ]
    for archive in matrix.REQUIRED_FILE_ARCHIVES:
        path = root / archive
        path.write_bytes(f"archive:{archive}\n".encode())
        manifest_lines.append(
            f"object={archive} status=ok size={path.stat().st_size} source=/runtime/{archive}"
        )
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {archive}")
    for database in databases:
        path = postgres / f"{database}.sql.gz"
        path.write_bytes(f"dump:{database}\n".encode())
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  postgres/{path.name}")
        schema_path = postgres / f"{database}.schema.sql.gz"
        schema_path.write_bytes(f"schema:{database}\n".encode())
        lines.append(f"{hashlib.sha256(schema_path.read_bytes()).hexdigest()}  postgres/{schema_path.name}")
        manifest_lines.extend(
            [
                f"schema_authority_sha256_{database}={matrix.schema_authority_sha256(database)}",
                f"schema_snapshot_{database}=postgres/{schema_path.name}",
            ]
        )
    (root / "manifest.txt").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    lines.append(
        f"{hashlib.sha256((root / 'manifest.txt').read_bytes()).hexdigest()}  manifest.txt"
    )
    (root / "checksums.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


class Runner:
    def __init__(self, failing_database: str = "", residual_databases: int = 0):
        self.failing_database = failing_database
        self.residual_databases = residual_databases
        self.calls: list[dict[str, str]] = []

    def __call__(self, command, *, env, **kwargs):
        if command[0] == "psql":
            self.cleanup_command = list(command)
            self.cleanup_env = dict(env)
            return subprocess.CompletedProcess(command, 0, stdout=f"{self.residual_databases}|160014\n", stderr="")
        self.calls.append(dict(env))
        database = Path(env["SIQ_RESTORE_SMOKE_SOURCE"]).name.removesuffix(".sql.gz")
        if database == self.failing_database:
            return subprocess.CompletedProcess(command, 1, stdout="failed /home/operator/private.sql", stderr="")
        output = (
            "restore_phase=schema_snapshot status=passed\n"
            "restore_phase=migration_compatibility status=passed\n"
        )
        if database == "siq_app":
            output += (
                "restore_phase=voiceprint_tombstone status=passed\n"
                + json.dumps(
                    {
                        "schema_version": "siq.meeting.voiceprint_tombstone_reconcile.v1",
                        "status": "passed",
                        "ledger_checkpoint_verified": True,
                        "ledger_entry_count": int(
                            env[matrix.VOICEPRINT_EXPECTED_COUNT_ENV]
                        ),
                        "ledger_head_hmac": env[matrix.VOICEPRINT_EXPECTED_HEAD_ENV],
                    },
                    sort_keys=True,
                )
                + "\n"
            )
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")


class LongFailureRunner(Runner):
    def __call__(self, command, *, env, **kwargs):
        if command[0] == "psql":
            return super().__call__(command, env=env, **kwargs)
        database = Path(env["SIQ_RESTORE_SMOKE_SOURCE"]).name.removesuffix(".sql.gz")
        if database == self.failing_database:
            return subprocess.CompletedProcess(
                command,
                1,
                stdout=("checksum ok " * 100) + "actual restore failure",
                stderr="",
            )
        return super().__call__(command, env=env, **kwargs)


class PhaseFailureRunner(Runner):
    def __call__(self, command, *, env, **kwargs):
        if command[0] == "psql":
            return super().__call__(command, env=env, **kwargs)
        database = Path(env["SIQ_RESTORE_SMOKE_SOURCE"]).name.removesuffix(".sql.gz")
        if database == self.failing_database:
            return subprocess.CompletedProcess(
                command,
                1,
                stdout=(
                    "restore_phase=schema_snapshot status=started\n"
                    "restore_phase=schema_snapshot status=failed\n"
                ),
                stderr="",
            )
        return super().__call__(command, env=env, **kwargs)


class VoiceprintFailureRunner(Runner):
    def __call__(self, command, *, env, **kwargs):
        if command[0] == "psql":
            return super().__call__(command, env=env, **kwargs)
        database = Path(env["SIQ_RESTORE_SMOKE_SOURCE"]).name.removesuffix(".sql.gz")
        if database == self.failing_database:
            return subprocess.CompletedProcess(
                command,
                1,
                stdout=(
                    "restore_phase=schema_snapshot status=passed\n"
                    "restore_phase=migration_compatibility status=passed\n"
                    "restore_phase=voiceprint_tombstone status=started\n"
                    '{"error_code":"VoiceprintTombstoneConfigurationError","status":"failed"}\n'
                    "restore_phase=voiceprint_tombstone status=failed\n"
                    "WARNING: trailing cleanup warning\n"
                ),
                stderr="",
            )
        return super().__call__(command, env=env, **kwargs)


class MissingVoiceprintEvidenceRunner(Runner):
    def __call__(self, command, *, env, **kwargs):
        if command[0] == "psql":
            return super().__call__(command, env=env, **kwargs)
        database = Path(env["SIQ_RESTORE_SMOKE_SOURCE"]).name.removesuffix(".sql.gz")
        if database == "siq_app":
            self.calls.append(dict(env))
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    "restore_phase=schema_snapshot status=passed\n"
                    "restore_phase=migration_compatibility status=passed\n"
                    "restore_phase=voiceprint_tombstone status=passed\n"
                ),
                stderr="",
            )
        return super().__call__(command, env=env, **kwargs)


def test_matrix_runs_all_seven_databases_with_market_specific_probes(tmp_path):
    runner = Runner()
    report = matrix.run_matrix(
        backup_dir=_backup_dir(tmp_path),
        admin_url="postgresql://restore:secret@127.0.0.1:5432/postgres",
        runner=runner,
    )

    assert report["passed"] is True
    assert report["backup_id"] == "20260713_120000"
    assert report["summary"] == {
        "databases": 7,
        "passed_databases": 7,
        "failed_databases": 0,
        "residual_databases": 0,
    }
    assert report["cleanup"] == {
        "status": "passed",
        "passed": True,
        "temporary_database_prefix": "siq_restore_smoke_",
        "residual_database_count": 0,
        "server_version_num": 160014,
        "postgres_major": 16,
    }
    assert report["backup_evidence"]["database_dump_count"] == 7
    assert report["backup_evidence"]["backup_mode"] == "required"
    assert report["backup_evidence"]["skip_large"] == "0"
    assert report["backup_evidence"]["checksum_entry_count"] == 20
    assert report["backup_evidence"]["shared_checksum_manifest"] is True
    assert report["backup_evidence"]["schema_contract_version"] == matrix.SCHEMA_CONTRACT_VERSION
    assert report["backup_evidence"]["schema_authority_verified"] is True
    assert report["backup_evidence"]["schema_snapshot_count"] == 7
    assert report["backup_evidence"]["required_file_archive_count"] == 5
    assert report["backup_evidence"]["required_file_archives_verified"] is True
    assert report["backup_evidence"]["required_file_archives"] == list(
        matrix.REQUIRED_FILE_ARCHIVES
    )
    assert (
        report["backup_evidence"]["migration_compatibility_mode"]
        == "authority_chain_schema_convergence"
    )
    assert report["schema_compatibility"]["databases"] == [target.database for target in matrix.TARGETS]
    assert len(report["backup_evidence"]["checksum_manifest_sha256"]) == 64
    assert len(runner.calls) == 7
    assert runner.calls[0]["SIQ_RESTORE_SMOKE_NONEMPTY_RELATIONS"] == ""
    assert runner.calls[0]["SIQ_RESTORE_SMOKE_REQUIRE_AGENT_VIEW"] == "0"
    assert runner.calls[2]["SIQ_RESTORE_SMOKE_NONEMPTY_RELATIONS"] == "sec_us.v_agent_financial_facts"
    assert runner.calls[2]["SIQ_RESTORE_SMOKE_REQUIRE_AGENT_VIEW"] == "1"
    assert all(call["SIQ_RESTORE_SMOKE_MODE"] == "required" for call in runner.calls)
    assert runner.calls[0]["SIQ_RESTORE_SMOKE_DATABASE_NAME"] == "siq_app"
    assert runner.calls[0]["SIQ_RESTORE_SMOKE_VOICEPRINT_TOMBSTONE_REQUIRED"] == "1"
    assert runner.calls[0][matrix.VOICEPRINT_EXPECTED_COUNT_ENV] == "0"
    assert (
        runner.calls[0][matrix.VOICEPRINT_EXPECTED_HEAD_ENV]
        == matrix.EMPTY_TOMBSTONE_HEAD_HMAC
    )
    assert runner.calls[0]["SIQ_RESTORE_SMOKE_EXPECTED_SCHEMA_SNAPSHOT"].endswith(
        "/postgres/siq_app.schema.sql.gz"
    )
    app_authorities = runner.calls[0]["SIQ_RESTORE_SMOKE_COMPATIBILITY_MIGRATIONS"].splitlines()
    assert [Path(path).name for path in app_authorities] == [
        f"{index:03d}_{suffix}"
        for index, suffix in (
            (1, "create_auth_tables.sql"),
            (2, "create_meeting_tables.sql"),
            (3, "create_meeting_import_tables.sql"),
            (4, "create_meeting_native_capture_tables.sql"),
            (5, "create_meeting_native_capture_finalization_tables.sql"),
            (6, "create_runtime_coordination_tables.sql"),
            (7, "create_meeting_native_capture_manifest_entries.sql"),
            (8, "add_meeting_native_capture_epoch_manifest_digest.sql"),
        )
    ]
    assert runner.calls[2]["SIQ_RESTORE_SMOKE_COMPATIBILITY_MIGRATIONS"].endswith(
        "/db/ddl/010_create_sec_us_schema.sql"
    )
    assert "\n" not in runner.calls[2]["SIQ_RESTORE_SMOKE_COMPATIBILITY_MIGRATIONS"]
    assert all(item["migration_compatibility"]["status"] == "passed" for item in report["results"])
    assert report["results"][0]["migration_compatibility"]["authority_count"] == 8
    assert all(
        item["migration_compatibility"]["authority_count"] == 1
        for item in report["results"][1:]
    )
    assert all(item["schema_snapshot_validation"]["status"] == "passed" for item in report["results"])
    assert report["results"][0]["voiceprint_tombstone_validation"] == {
        "status": "passed",
        "required": True,
        "expected_entry_count": 0,
        "expected_head_hmac": matrix.EMPTY_TOMBSTONE_HEAD_HMAC,
        "checkpoint_sha256": report["voiceprint_tombstone_checkpoint"][
            "checkpoint_sha256"
        ],
        "actual_entry_count": 0,
        "actual_head_hmac": matrix.EMPTY_TOMBSTONE_HEAD_HMAC,
        "checkpoint_verified": True,
    }
    assert all(
        item["voiceprint_tombstone_validation"] == {
            "status": "not_requested",
            "required": False,
        }
        for item in report["results"][1:]
    )
    assert report["schema_compatibility"]["restored_snapshot_count"] == 7
    assert report["schema_compatibility"]["migration_dry_run_count"] == 7
    assert all(
        call["SIQ_RESTORE_SMOKE_VOICEPRINT_TOMBSTONE_REQUIRED"] == "0"
        for call in runner.calls[1:]
    )
    checkpoint = report["voiceprint_tombstone_checkpoint"]
    assert checkpoint["backup_id"] == report["backup_id"] == "20260713_120000"
    assert checkpoint["expected_entry_count"] == 0
    assert checkpoint["expected_head_hmac"] == matrix.EMPTY_TOMBSTONE_HEAD_HMAC
    assert checkpoint["verified"] is True
    assert checkpoint["actual_entry_count"] == 0
    assert checkpoint["actual_head_hmac"] == matrix.EMPTY_TOMBSTONE_HEAD_HMAC
    assert len(checkpoint["checkpoint_sha256"]) == 64
    assert len(checkpoint["backup_binding_sha256"]) == 64


@pytest.mark.parametrize(
    ("count", "head_hmac", "reason"),
    [
        (None, "0" * 64, "voiceprint_tombstone_expected_count_missing"),
        ("-1", "0" * 64, "voiceprint_tombstone_expected_count_invalid"),
        ("0", None, "voiceprint_tombstone_expected_head_hmac_missing"),
        ("0", "g" * 64, "voiceprint_tombstone_expected_head_hmac_invalid"),
        ("0", "1" * 64, "voiceprint_tombstone_empty_checkpoint_head_mismatch"),
    ],
)
def test_matrix_rejects_missing_or_invalid_voiceprint_checkpoint_before_restore(
    tmp_path, monkeypatch, count, head_hmac, reason
):
    if count is None:
        monkeypatch.delenv(matrix.VOICEPRINT_EXPECTED_COUNT_ENV, raising=False)
    else:
        monkeypatch.setenv(matrix.VOICEPRINT_EXPECTED_COUNT_ENV, count)
    if head_hmac is None:
        monkeypatch.delenv(matrix.VOICEPRINT_EXPECTED_HEAD_ENV, raising=False)
    else:
        monkeypatch.setenv(matrix.VOICEPRINT_EXPECTED_HEAD_ENV, head_hmac)
    runner = Runner()

    with pytest.raises(ValueError, match=reason):
        matrix.run_matrix(
            backup_dir=_backup_dir(tmp_path),
            admin_url="postgresql://restore:secret@127.0.0.1:5432/postgres",
            runner=runner,
        )

    assert runner.calls == []


def test_matrix_rejects_disabling_required_voiceprint_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("SIQ_RESTORE_MATRIX_VOICEPRINT_TOMBSTONE_REQUIRED", "0")
    runner = Runner()

    with pytest.raises(
        ValueError,
        match="voiceprint_tombstone_validation_must_be_required",
    ):
        matrix.run_matrix(
            backup_dir=_backup_dir(tmp_path),
            admin_url="postgresql://restore:secret@127.0.0.1:5432/postgres",
            runner=runner,
        )

    assert runner.calls == []


def test_matrix_fails_closed_and_redacts_failure_paths(tmp_path):
    report = matrix.run_matrix(
        backup_dir=_backup_dir(tmp_path),
        admin_url="postgresql://restore:secret@127.0.0.1:5432/postgres",
        runner=Runner("siq_kr"),
    )

    assert report["passed"] is False
    assert report["summary"]["failed_databases"] == 1
    serialized = json.dumps(report)
    assert "/home/operator" not in serialized
    assert "secret" not in serialized


def test_matrix_failure_summary_keeps_actual_error_after_long_checksum_output(tmp_path):
    report = matrix.run_matrix(
        backup_dir=_backup_dir(tmp_path),
        admin_url="postgresql://restore:secret@127.0.0.1:5432/postgres",
        runner=LongFailureRunner("siq_app"),
    )

    assert report["results"][0]["failure_summary"].endswith("actual restore failure")


def test_schema_failure_is_not_misreported_as_migration_failure(tmp_path):
    report = matrix.run_matrix(
        backup_dir=_backup_dir(tmp_path),
        admin_url="postgresql://restore:secret@127.0.0.1:5432/postgres",
        runner=PhaseFailureRunner("siq_app"),
    )

    failed = report["results"][0]
    assert failed["schema_snapshot_validation"]["status"] == "failed"
    assert failed["migration_compatibility"]["status"] == "not_run"
    assert report["schema_compatibility"]["restored_snapshot_count"] == 6
    assert report["schema_compatibility"]["migration_dry_run_count"] == 6


def test_post_restore_failure_preserves_schema_success_and_structured_error(tmp_path):
    report = matrix.run_matrix(
        backup_dir=_backup_dir(tmp_path),
        admin_url="postgresql://restore:secret@127.0.0.1:5432/postgres",
        runner=VoiceprintFailureRunner("siq_app"),
    )

    failed = report["results"][0]
    assert report["passed"] is False
    assert report["schema_compatibility"]["passed"] is True
    assert failed["schema_snapshot_validation"]["status"] == "passed"
    assert failed["migration_compatibility"]["status"] == "passed"
    assert failed["voiceprint_tombstone_validation"]["status"] == "failed"
    assert "VoiceprintTombstoneConfigurationError" in failed["failure_summary"]
    assert "trailing cleanup warning" not in failed["failure_summary"]


def test_matrix_rejects_success_without_voiceprint_checkpoint_json_evidence(tmp_path):
    report = matrix.run_matrix(
        backup_dir=_backup_dir(tmp_path),
        admin_url="postgresql://restore:secret@127.0.0.1:5432/postgres",
        runner=MissingVoiceprintEvidenceRunner(),
    )

    assert report["passed"] is False
    assert report["results"][0]["voiceprint_tombstone_validation"]["status"] == "failed"
    assert (
        report["results"][0]["failure_summary"]
        == "voiceprint_checkpoint_evidence_missing_or_mismatch"
    )


def test_matrix_fails_closed_when_disposable_database_remains(tmp_path):
    report = matrix.run_matrix(
        backup_dir=_backup_dir(tmp_path),
        admin_url="postgresql://restore:secret@127.0.0.1:5432/postgres",
        runner=Runner(residual_databases=1),
    )

    assert report["passed"] is False
    assert report["status"] == "failed"
    assert report["summary"]["residual_databases"] == 1
    assert report["cleanup"]["passed"] is False


def test_cleanup_audit_keeps_admin_credentials_out_of_process_arguments(tmp_path):
    runner = Runner()
    matrix.run_matrix(
        backup_dir=_backup_dir(tmp_path),
        admin_url="postgresql://restore:secret@db.internal:5433/postgres?sslmode=require",
        runner=runner,
    )

    assert runner.cleanup_env["PGHOST"] == "db.internal"
    assert runner.cleanup_env["PGPORT"] == "5433"
    assert runner.cleanup_env["PGUSER"] == "restore"
    assert runner.cleanup_env["PGPASSWORD"] == "secret"
    assert runner.cleanup_env["PGDATABASE"] == "postgres"
    assert runner.cleanup_env["PGSSLMODE"] == "require"
    assert "secret" not in " ".join(runner.cleanup_command)
    assert "db.internal" not in " ".join(runner.cleanup_command)


def test_matrix_rejects_incomplete_or_reordered_database_set(tmp_path):
    backup = _backup_dir(tmp_path)
    manifest = backup / "manifest.txt"
    manifest.write_text("timestamp=one\npostgres_databases=siq_app,siq_us\n", encoding="utf-8")

    try:
        matrix.run_matrix(backup_dir=backup, admin_url="postgresql://host/postgres", runner=Runner())
    except ValueError as exc:
        assert str(exc) == "backup_database_set_or_order_mismatch"
    else:
        raise AssertionError("matrix must reject an incomplete backup set")


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("backup_mode", "optional", "backup_mode_not_release_grade"),
        ("backup_mode", "development", "backup_mode_not_release_grade"),
        ("backup_mode", "", "backup_mode_not_release_grade"),
        ("skip_large", "1", "backup_skip_large_not_release_grade"),
        ("skip_large", "", "backup_skip_large_not_release_grade"),
    ],
)
def test_matrix_rejects_non_release_grade_backup_manifest(tmp_path, field, value, reason):
    backup = _backup_dir(tmp_path)
    manifest = backup / "manifest.txt"
    lines = manifest.read_text(encoding="utf-8").splitlines()
    lines = [f"{field}={value}" if line.startswith(f"{field}=") else line for line in lines]
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    runner = Runner()

    with pytest.raises(ValueError, match=reason):
        matrix.run_matrix(backup_dir=backup, admin_url="postgresql://host/postgres", runner=runner)

    assert runner.calls == []


def test_matrix_accepts_release_backup_mode(tmp_path):
    backup = _backup_dir(tmp_path)
    manifest = backup / "manifest.txt"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("backup_mode=required", "backup_mode=release", 1),
        encoding="utf-8",
    )

    report = matrix.run_matrix(
        backup_dir=backup,
        admin_url="postgresql://restore:secret@127.0.0.1:5432/postgres",
        runner=Runner(),
    )

    assert report["passed"] is True
    assert report["backup_evidence"]["backup_mode"] == "release"


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("missing", "backup_required_archive_manifest_record_mismatch:wiki.tar.gz"),
        ("duplicate", "backup_required_archive_manifest_record_mismatch:wiki.tar.gz"),
        ("skipped", "backup_required_archive_not_ok:wiki.tar.gz"),
        ("malformed", "backup_required_archive_not_ok:wiki.tar.gz"),
        ("wrong_size", "backup_required_archive_size_mismatch:wiki.tar.gz"),
    ],
)
def test_matrix_rejects_invalid_required_archive_manifest_before_restore(
    tmp_path, mutation, reason
):
    backup = _backup_dir(tmp_path)
    manifest = backup / "manifest.txt"
    lines = manifest.read_text(encoding="utf-8").splitlines()
    record = next(line for line in lines if line.startswith("object=wiki.tar.gz "))
    if mutation == "missing":
        lines.remove(record)
    elif mutation == "duplicate":
        lines.append(record)
    elif mutation == "skipped":
        lines[lines.index(record)] = record.replace("status=ok", "status=skipped")
    elif mutation == "malformed":
        lines[lines.index(record)] = "object=wiki.tar.gz status=ok"
    else:
        lines[lines.index(record)] = re.sub(r"size=\d+", "size=1", record)
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    runner = Runner()

    with pytest.raises(ValueError, match=re.escape(reason)):
        matrix.run_matrix(backup_dir=backup, admin_url="postgresql://host/postgres", runner=runner)

    assert runner.calls == []


def test_matrix_rejects_empty_required_archive_before_restore(tmp_path):
    backup = _backup_dir(tmp_path)
    (backup / "hermes-home.tar.gz").write_bytes(b"")
    runner = Runner()

    with pytest.raises(
        ValueError,
        match="backup_required_archive_missing_or_empty:hermes-home.tar.gz",
    ):
        matrix.run_matrix(backup_dir=backup, admin_url="postgresql://host/postgres", runner=runner)

    assert runner.calls == []


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("missing", "backup_required_archive_checksum_entry_mismatch:report-downloads.tar.gz"),
        ("duplicate", "backup_required_archive_checksum_entry_mismatch:report-downloads.tar.gz"),
        ("mismatch", "backup_required_archive_checksum_mismatch:report-downloads.tar.gz"),
    ],
)
def test_matrix_rejects_invalid_required_archive_checksum_before_restore(
    tmp_path, mutation, reason
):
    backup = _backup_dir(tmp_path)
    checksums = backup / "checksums.sha256"
    lines = checksums.read_text(encoding="utf-8").splitlines()
    entry = next(line for line in lines if line.endswith("  report-downloads.tar.gz"))
    if mutation == "missing":
        lines.remove(entry)
    elif mutation == "duplicate":
        lines.append(entry)
    else:
        lines[lines.index(entry)] = f"{'0' * 64}  report-downloads.tar.gz"
    checksums.write_text("\n".join(lines) + "\n", encoding="utf-8")
    runner = Runner()

    with pytest.raises(ValueError, match=re.escape(reason)):
        matrix.run_matrix(backup_dir=backup, admin_url="postgresql://host/postgres", runner=runner)

    assert runner.calls == []


def test_matrix_rejects_schema_contract_or_migration_authority_drift(tmp_path):
    backup = _backup_dir(tmp_path)
    manifest = backup / "manifest.txt"
    original = manifest.read_text(encoding="utf-8")
    manifest.write_text(
        original.replace(matrix.SCHEMA_CONTRACT_VERSION, "siq_postgres_schema_contract_v0", 1),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="backup_schema_contract_version_mismatch"):
        matrix.run_matrix(backup_dir=backup, admin_url="postgresql://host/postgres", runner=Runner())

    manifest.write_text(
        original.replace(
            f"schema_authority_sha256_siq_hk={matrix.schema_authority_sha256('siq_hk')}",
            f"schema_authority_sha256_siq_hk={'0' * 64}",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="backup_schema_authority_mismatch:siq_hk"):
        matrix.run_matrix(backup_dir=backup, admin_url="postgresql://host/postgres", runner=Runner())


def test_matrix_rejects_missing_schema_snapshot_without_running_restore(tmp_path):
    backup = _backup_dir(tmp_path)
    (backup / "postgres" / "siq_eu.schema.sql.gz").unlink()
    runner = Runner()

    with pytest.raises(ValueError, match="backup_schema_snapshot_missing:siq_eu"):
        matrix.run_matrix(backup_dir=backup, admin_url="postgresql://host/postgres", runner=runner)

    assert runner.calls == []


def test_cli_writes_blocked_report_without_admin_url(tmp_path):
    backup = _backup_dir(tmp_path)
    output = tmp_path / "report.json"
    markdown = tmp_path / "report.md"
    completed = subprocess.run(
        [
            sys.executable,
            str(SOURCE),
            "--backup-dir",
            str(backup),
            "--output",
            str(output),
            "--markdown",
            str(markdown),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={},
    )

    assert completed.returncode == 1
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "blocked"
    assert markdown.is_file()


def test_cli_does_not_expose_admin_url_argument():
    completed = subprocess.run([sys.executable, str(SOURCE), "--help"], capture_output=True, text=True, check=True)
    assert "--admin-url" not in completed.stdout
    assert "SIQ_RESTORE_MATRIX_ADMIN_URL" not in completed.stdout
