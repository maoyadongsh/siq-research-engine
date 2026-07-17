from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from scripts.openshell import provision_postgres_reader as reader


def test_plan_pins_six_database_schema_pairs_and_is_non_mutating() -> None:
    plan = reader._plan()

    assert plan["mutated"] is False
    assert [(item["database"], item["schema"]) for item in plan["database_schemas"]] == list(reader.DATABASE_SCHEMAS)
    assert plan["security"] == {
        "default_transaction_read_only": True,
        "request_database_selection": False,
        "schema_create": False,
        "table_privileges": ["SELECT"],
        "sequence_privileges": [],
    }


def test_cluster_sql_sets_all_role_safety_flags_without_leaking_to_plan() -> None:
    password = "A" * 64
    sql = reader._cluster_role_sql(password)

    assert 'ALTER ROLE "siq_openshell_reader"' in sql
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS" in sql
    assert "default_transaction_read_only = on" in sql
    assert "search_path = pg_catalog" in sql
    assert reader.ROLE_MARKER in sql
    assert password in sql
    assert password not in json.dumps(reader._plan())


def test_grant_and_revoke_sql_are_fixed_to_select_and_known_owners() -> None:
    grant = reader._database_grant_sql(database="siq", schema="pdf2md", owners=("dgx", "postgres"))
    revoke = reader._database_revoke_sql(database="siq", schema="pdf2md", owners=("dgx", "postgres"))

    assert 'GRANT SELECT ON ALL TABLES IN SCHEMA "pdf2md"' in grant
    assert 'REVOKE CREATE ON SCHEMA "pdf2md"' in grant
    assert "GRANT INSERT" not in grant
    assert "GRANT UPDATE" not in grant
    assert "GRANT DELETE" not in grant
    assert "GRANT USAGE ON ALL SEQUENCES" not in grant
    assert grant.count("ALTER DEFAULT PRIVILEGES FOR ROLE") == 2
    assert revoke.count("ALTER DEFAULT PRIVILEGES FOR ROLE") == 2
    assert 'REVOKE CONNECT ON DATABASE "siq"' in revoke


def test_secret_file_round_trip_requires_owner_only_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reader, "PROJECT_ROOT", tmp_path)
    secret_path = tmp_path / "var/openshell/secrets/postgres-reader.env"
    password = "B" * 64
    reader._atomic_private_write(
        secret_path,
        reader._secret_content(password=password, host="127.0.0.1", host_port=15432),
    )

    loaded = reader._load_secret(secret_path)
    assert loaded["SIQ_OPENSHELL_PG_RO_PASSWORD"] == password
    assert stat.S_IMODE(secret_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(secret_path.parent.stat().st_mode) == 0o700

    secret_path.chmod(0o640)
    with pytest.raises(reader.ReaderProvisionError, match="private_state_unsafe"):
        reader._load_secret(secret_path)


def test_secret_rejects_paths_outside_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(reader, "PROJECT_ROOT", project)

    with pytest.raises(reader.ReaderProvisionError, match="state_path_outside_project"):
        reader._ensure_project_local(tmp_path / "elsewhere.env")


class RecordingRunner:
    def __init__(self, result: reader.CommandResult) -> None:
        self.result = result
        self.calls: list[tuple[tuple[str, ...], str]] = []

    def run(self, command, *, input_text):
        self.calls.append((tuple(command), input_text))
        return self.result


def test_reader_password_is_sent_only_over_stdin() -> None:
    password = "C" * 64
    runner = RecordingRunner(reader.CommandResult(0, '{"ok":true}\n'))
    backend = reader.DockerPostgres(
        container="docker-postgres-1",
        admin_user="postgres",
        container_port=5432,
        runner=runner,
    )

    backend.reader("siq", password, "SELECT 1;\n")

    command, input_text = runner.calls[0]
    assert password not in " ".join(command)
    assert input_text.startswith(password + "\n")
    assert "PGPASSWORD=" not in " ".join(command)


def test_admin_failure_never_surfaces_backend_stderr() -> None:
    canary = "database-secret-canary"
    runner = RecordingRunner(reader.CommandResult(1, "", canary))
    backend = reader.DockerPostgres(
        container="docker-postgres-1",
        admin_user="postgres",
        container_port=5432,
        runner=runner,
    )

    with pytest.raises(reader.ReaderProvisionError) as exc_info:
        backend.admin("siq", "SELECT 1;\n")
    assert canary not in str(exc_info.value)


def test_verification_contract_requires_no_write_or_sequence_privileges() -> None:
    sql = reader._verification_sql("pdf2md")

    assert "transaction_read_only" in sql
    assert "schema_create" in sql
    assert "non_select_table_privileges" in sql
    assert "missing_select_relations" in sql
    assert "sequence_privileges" in sql
    assert "INSERT" not in sql


def test_private_file_rejects_symlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reader, "PROJECT_ROOT", tmp_path)
    target = tmp_path / "target"
    target.write_text("value", encoding="utf-8")
    target.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(target)

    with pytest.raises(reader.ReaderProvisionError, match="state_path_symlink_blocked"):
        reader._read_private_file(link)


def test_cli_plan_does_not_require_confirmation(capsys: pytest.CaptureFixture[str]) -> None:
    assert reader.main(["plan"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mutated"] is False


def test_cli_mutation_requires_exact_role_confirmation(capsys: pytest.CaptureFixture[str]) -> None:
    assert reader.main(["apply"]) == 2
    assert "exact --confirm-role" in capsys.readouterr().err


def test_generated_secret_has_no_shell_metacharacters() -> None:
    password = reader.secrets.token_urlsafe(48)
    assert reader.PASSWORD_RE.fullmatch(password)
    assert all(character not in password for character in "'\"$`\\ ")


def test_state_path_mode_helper_does_not_change_process_umask(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reader, "PROJECT_ROOT", tmp_path)
    before = os.umask(0o077)
    os.umask(before)
    reader._atomic_private_write(tmp_path / "var/state.json", "{}\n")
    after = os.umask(before)
    os.umask(after)
    assert after == before


def test_preflight_backup_is_private_and_contains_no_role_marker_value_when_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reader, "PROJECT_ROOT", tmp_path)
    relative = reader._write_preflight_backup(
        tmp_path / "var/openshell/backups/postgres-reader",
        marker=None,
        inventory=[{"database": "siq", "schema": "pdf2md", "owners": ["dgx"]}],
    )

    path = tmp_path / relative
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["role_present"] is False
    assert payload["role_managed"] is False
    assert reader.ROLE_MARKER not in path.read_text(encoding="utf-8")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
