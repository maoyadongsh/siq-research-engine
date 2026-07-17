from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest
import yaml

from scripts.openshell.snapshot_siq_analysis_runtime import (
    FRESH_SNAPSHOT_MODE,
    MANIFEST_NAME,
    RUNTIME_DIRECTORIES,
    RUNTIME_STATE_DIRECTORY,
    SNAPSHOT_ROOT_RELATIVE,
    SOURCE_RELATIVE,
    SQLITE_SIDECARS,
    RuntimeSnapshotError,
    main,
    snapshot_runtime,
)


def _runtime_root(project_root: Path) -> Path:
    root = project_root / SOURCE_RELATIVE
    root.mkdir(parents=True)
    (root / "config.yaml").write_text(
        """\
model:
  provider: custom:test
  default: test-model
  key_env: SIQ_TEST_API_KEY
security:
  redact_secrets: true
""",
        encoding="utf-8",
    )
    for name in ("sessions", "checkpoints", "cron"):
        (root / name).mkdir()
    (root / "sessions/session-alpha.json").write_text('{"status":"complete"}\n', encoding="utf-8")
    (root / "checkpoints/current.json").write_text('{"stage":2}\n', encoding="utf-8")
    (root / "cron/jobs.json").write_text("[]\n", encoding="utf-8")
    (root / "cron/.tick.lock").write_text("host lock\n", encoding="utf-8")
    (root / "cron/access-token.json").write_text("secret-token-value\n", encoding="utf-8")
    (root / "memories").mkdir()
    (root / "memories/current.md").write_text("memory-state\n", encoding="utf-8")
    (root / "auth.json").write_text('{"token":"secret-token-value"}\n', encoding="utf-8")
    (root / "gateway.pid").write_text("4242\n", encoding="utf-8")
    (root / "gateway.lock").write_text("host lock\n", encoding="utf-8")
    return root


def _open_wal_database(path: Path, value: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
    connection.execute("PRAGMA wal_autocheckpoint = 0")
    connection.execute("CREATE TABLE runtime_state (value TEXT NOT NULL)")
    connection.execute("INSERT INTO runtime_state VALUES (?)", (value,))
    connection.commit()
    assert Path(f"{path}-wal").is_file()
    return connection


def _prepare_project(tmp_path: Path) -> tuple[Path, Path, list[sqlite3.Connection]]:
    project_root = tmp_path / "project"
    project_root.mkdir()
    runtime = _runtime_root(project_root)
    connections = [
        _open_wal_database(runtime / "state.db", "state-from-wal"),
        _open_wal_database(runtime / "response_store.db", "response-from-wal"),
    ]
    return project_root, runtime, connections


def _close_all(connections: list[sqlite3.Connection]) -> None:
    for connection in connections:
        connection.close()


def test_snapshot_uses_sqlite_backup_and_copies_only_allowlisted_runtime(tmp_path: Path) -> None:
    project_root, runtime, connections = _prepare_project(tmp_path)
    destination = project_root / SNAPSHOT_ROOT_RELATIVE / "test-snapshot"
    source_config_hash = hashlib.sha256((runtime / "config.yaml").read_bytes()).hexdigest()
    auth_content = (runtime / "auth.json").read_bytes()
    try:
        manifest = snapshot_runtime(project_root=project_root, destination=destination)
    finally:
        _close_all(connections)

    assert destination.is_dir()
    assert (destination / "sessions/session-alpha.json").is_file()
    assert (destination / "checkpoints/current.json").is_file()
    assert (destination / "cron/jobs.json").is_file()
    assert (destination / "memories/current.md").read_text(encoding="utf-8") == "memory-state\n"
    assert not (destination / "cron/.tick.lock").exists()
    assert not (destination / "cron/access-token.json").exists()
    assert not (destination / "auth.json").exists()
    assert not (destination / "gateway.pid").exists()
    assert not (destination / "gateway.lock").exists()
    runtime_state = destination / RUNTIME_STATE_DIRECTORY
    assert {path.name for path in runtime_state.glob("*-wal")} == {
        "state.db-wal",
        "response_store.db-wal",
    }
    assert {path.name for path in runtime_state.glob("*-shm")} == {
        "state.db-shm",
        "response_store.db-shm",
    }
    assert all((runtime_state / sidecar).read_bytes() == b"" for sidecar in SQLITE_SIDECARS)

    for database, expected in (
        ("state.db", "state-from-wal"),
        ("response_store.db", "response-from-wal"),
    ):
        with sqlite3.connect(runtime_state / database) as connection:
            assert connection.execute("SELECT value FROM runtime_state").fetchone() == (expected,)
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)

    serialized = (destination / MANIFEST_NAME).read_text(encoding="utf-8")
    assert json.loads(serialized) == manifest
    assert str(project_root) not in serialized
    assert "secret-token-value" not in serialized
    assert "session-alpha" not in serialized
    assert manifest["safeguards"]["sqlite_backup_api"] is True
    assert manifest["safeguards"]["credentials_copied"] is False
    assert manifest["safeguards"]["sqlite_sidecars_copied"] is False
    assert manifest["safeguards"]["sqlite_sidecars_materialized_empty"] is True
    assert [item["name"] for item in manifest["inventory"]["sqlite_sidecars"]] == list(SQLITE_SIDECARS)
    assert all(item["byte_count"] == 0 for item in manifest["inventory"]["sqlite_sidecars"])
    assert manifest["inventory"]["skipped_forbidden_artifact_count"] == 2
    assert all(
        item["backup_method"] == "python_sqlite3_connection_backup" for item in manifest["inventory"]["databases"]
    )

    assert hashlib.sha256((runtime / "config.yaml").read_bytes()).hexdigest() == source_config_hash
    assert (runtime / "auth.json").read_bytes() == auth_content
    assert (runtime / "gateway.pid").read_text(encoding="utf-8") == "4242\n"


def test_fresh_snapshot_copies_no_host_runtime_records(tmp_path: Path) -> None:
    project_root, runtime, connections = _prepare_project(tmp_path)
    destination = project_root / SNAPSHOT_ROOT_RELATIVE / "fresh-snapshot"
    try:
        manifest = snapshot_runtime(project_root=project_root, destination=destination, fresh=True)
    finally:
        _close_all(connections)

    assert not list((destination / RUNTIME_STATE_DIRECTORY).iterdir())
    assert all(not list((destination / name).iterdir()) for name in RUNTIME_DIRECTORIES)
    assert (runtime / "sessions/session-alpha.json").is_file()
    assert (runtime / "memories/current.md").is_file()
    for database, expected in (
        ("state.db", "state-from-wal"),
        ("response_store.db", "response-from-wal"),
    ):
        with sqlite3.connect(runtime / database) as connection:
            assert connection.execute("SELECT value FROM runtime_state").fetchone() == (expected,)

    assert manifest["snapshot_mode"] == FRESH_SNAPSHOT_MODE
    assert manifest["host_runtime_records_copied"] is False
    assert manifest["source_scope"] == "current_project_siq_analysis_config_only"
    assert manifest["copy_policy"]["sqlite_databases"] == []
    assert manifest["copy_policy"]["sqlite_sidecars"] == []
    assert manifest["safeguards"]["host_runtime_records_copied"] is False
    assert manifest["safeguards"]["sqlite_backup_api"] is False
    assert manifest["safeguards"]["sqlite_sidecars_materialized_empty"] is False
    assert manifest["inventory"]["databases"] == []
    assert manifest["inventory"]["sqlite_sidecars"] == []
    assert manifest["inventory"]["skipped_forbidden_artifact_count"] == 0
    assert manifest["inventory"]["total_file_bytes"] == manifest["inventory"]["config"]["byte_count"]
    assert all(
        entry["source_copied"] is False
        and entry["materialized_empty"] is True
        and entry["file_count"] == 0
        and entry["directory_count"] == 1
        for entry in manifest["inventory"]["runtime_entries"].values()
    )


def test_fresh_snapshot_does_not_require_host_databases_or_runtime_directories(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    runtime = project_root / SOURCE_RELATIVE
    runtime.mkdir(parents=True)
    (runtime / "config.yaml").write_text(
        "model:\n  provider: custom:test\n  default: test-model\n",
        encoding="utf-8",
    )
    destination = project_root / SNAPSHOT_ROOT_RELATIVE / "fresh-from-config-only"

    manifest = snapshot_runtime(project_root=project_root, destination=destination, fresh=True)

    assert manifest["host_runtime_records_copied"] is False
    assert not list((destination / RUNTIME_STATE_DIRECTORY).iterdir())
    assert all(not list((destination / name).iterdir()) for name in RUNTIME_DIRECTORIES)


def test_compiled_snapshot_binds_sandbox_routes_and_runtime_contract(tmp_path: Path) -> None:
    project_root, runtime, connections = _prepare_project(tmp_path)
    (runtime / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "model": {"provider": "custom:test", "default": "test-model"},
                "fallback_providers": [
                    {
                        "provider": "custom:qwen-local",
                        "model": "qwen-test",
                        "base_url": "http://127.0.0.1:8004/v1",
                    }
                ],
                "toolsets": ["terminal", "file", "code_execution", "web"],
                "terminal": {
                    "env_passthrough": ["PATH", "HOME"],
                    "auto_source_bashrc": True,
                    "shell_init_files": ["~/.bashrc"],
                },
                "security": {"redact_secrets": False},
                "platforms": {"api_server": {"key": "", "extra": {"port": 18651}}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    destination = project_root / SNAPSHOT_ROOT_RELATIVE / "compiled-snapshot"
    try:
        manifest = snapshot_runtime(
            project_root=project_root,
            destination=destination,
            compile_config=True,
        )
    finally:
        _close_all(connections)

    compiled = yaml.safe_load((destination / "config.yaml").read_text(encoding="utf-8"))
    assert compiled["fallback_providers"][0]["base_url"] == "http://host.openshell.internal:8004/v1"
    assert compiled["platforms"]["api_server"]["extra"]["port"] == 28651
    assert compiled["terminal"]["auto_source_bashrc"] is False
    assert compiled["terminal"]["shell_init_files"] == []
    assert "SIQ_PG_QUERY_BROKER_URL" in compiled["terminal"]["env_passthrough"]
    assert "TAVILY_API_KEY" in compiled["terminal"]["env_passthrough"]
    config = manifest["inventory"]["config"]
    assert config["compiled"] is True
    assert config["compiler_schema_version"] == "siq.openshell.hermes_runtime_config.v1"
    assert config["compiled_sha256"] == config["tree_sha256"]
    assert config["compiled_sha256"] == hashlib.sha256((destination / "config.yaml").read_bytes()).hexdigest()


def test_snapshot_rejects_symlink_in_allowed_runtime_tree_and_cleans_staging(tmp_path: Path) -> None:
    project_root, runtime, connections = _prepare_project(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("outside\n", encoding="utf-8")
    (runtime / "sessions/escape.json").symlink_to(outside)
    destination = project_root / SNAPSHOT_ROOT_RELATIVE / "symlink-rejected"
    try:
        with pytest.raises(RuntimeSnapshotError, match="contains a symlink"):
            snapshot_runtime(project_root=project_root, destination=destination)
    finally:
        _close_all(connections)

    assert not destination.exists()
    assert not list((project_root / SNAPSHOT_ROOT_RELATIVE).glob(".snapshot-staging-*"))


@pytest.mark.parametrize(
    "destination_factory",
    [
        lambda root, temp: temp / "outside-snapshot",
        lambda root, temp: root / SNAPSHOT_ROOT_RELATIVE,
        lambda root, temp: root / SNAPSHOT_ROOT_RELATIVE / ".." / "escaped",
        lambda root, temp: root,
    ],
)
def test_snapshot_rejects_out_of_bounds_and_dangerous_targets(
    tmp_path: Path,
    destination_factory,
) -> None:
    project_root, _, connections = _prepare_project(tmp_path)
    destination = destination_factory(project_root, tmp_path)
    try:
        with pytest.raises(RuntimeSnapshotError, match="(managed snapshot root|dangerous|must not contain)"):
            snapshot_runtime(project_root=project_root, destination=destination)
    finally:
        _close_all(connections)


def test_snapshot_rejects_symlinked_snapshot_root(tmp_path: Path) -> None:
    project_root, _, connections = _prepare_project(tmp_path)
    outside = tmp_path / "outside-root"
    outside.mkdir()
    (project_root / "var").symlink_to(outside, target_is_directory=True)
    destination = project_root / SNAPSHOT_ROOT_RELATIVE / "unsafe-parent"
    try:
        with pytest.raises(RuntimeSnapshotError, match="snapshot root component is unsafe"):
            snapshot_runtime(project_root=project_root, destination=destination)
    finally:
        _close_all(connections)


def test_snapshot_rejects_symlinked_operation_lock(tmp_path: Path) -> None:
    project_root, _, connections = _prepare_project(tmp_path)
    snapshot_root = project_root / SNAPSHOT_ROOT_RELATIVE
    snapshot_root.mkdir(parents=True)
    outside = tmp_path / "outside-lock"
    outside.write_text("preserve\n", encoding="utf-8")
    (snapshot_root / ".snapshot-operation.lock").symlink_to(outside)
    destination = snapshot_root / "unsafe-lock"
    try:
        with pytest.raises(RuntimeSnapshotError, match="lock path is unsafe"):
            snapshot_runtime(project_root=project_root, destination=destination)
    finally:
        _close_all(connections)

    assert outside.read_text(encoding="utf-8") == "preserve\n"
    assert not destination.exists()


def test_snapshot_rejects_existing_destination_without_modifying_it(tmp_path: Path) -> None:
    project_root, _, connections = _prepare_project(tmp_path)
    destination = project_root / SNAPSHOT_ROOT_RELATIVE / "existing"
    destination.mkdir(parents=True)
    marker = destination / "owned-by-user"
    marker.write_text("preserve\n", encoding="utf-8")
    try:
        with pytest.raises(RuntimeSnapshotError, match="already exists"):
            snapshot_runtime(project_root=project_root, destination=destination)
    finally:
        _close_all(connections)

    assert marker.read_text(encoding="utf-8") == "preserve\n"


def test_snapshot_rejects_inline_config_secret(tmp_path: Path) -> None:
    project_root, runtime, connections = _prepare_project(tmp_path)
    (runtime / "config.yaml").write_text("provider:\n  api_key: do-not-copy\n", encoding="utf-8")
    destination = project_root / SNAPSHOT_ROOT_RELATIVE / "secret-config"
    try:
        with pytest.raises(RuntimeSnapshotError, match="inline secret"):
            snapshot_runtime(project_root=project_root, destination=destination)
    finally:
        _close_all(connections)

    assert not destination.exists()


def test_snapshot_requires_both_runtime_databases(tmp_path: Path) -> None:
    project_root, runtime, connections = _prepare_project(tmp_path)
    _close_all(connections)
    (runtime / "response_store.db").unlink()
    destination = project_root / SNAPSHOT_ROOT_RELATIVE / "missing-database"

    with pytest.raises(RuntimeSnapshotError, match="required SQLite database is missing"):
        snapshot_runtime(project_root=project_root, destination=destination)

    assert not destination.exists()


def test_snapshot_materializes_missing_runtime_directories(tmp_path: Path) -> None:
    project_root, runtime, connections = _prepare_project(tmp_path)
    (runtime / "checkpoints/current.json").unlink()
    (runtime / "checkpoints").rmdir()
    destination = project_root / SNAPSHOT_ROOT_RELATIVE / "missing-runtime-directory"
    try:
        manifest = snapshot_runtime(project_root=project_root, destination=destination)
    finally:
        _close_all(connections)

    assert (destination / "checkpoints").is_dir()
    summary = manifest["inventory"]["runtime_entries"]["checkpoints"]
    assert summary["present"] is True
    assert summary["source_present"] is False
    assert summary["materialized_empty"] is True


def test_cli_writes_snapshot_and_reports_only_destination(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project_root, runtime, connections = _prepare_project(tmp_path)
    destination = project_root / SNAPSHOT_ROOT_RELATIVE / "cli-snapshot"
    try:
        result = main(
            [
                "--project-root",
                str(project_root),
                "--source",
                str(runtime),
                "--output",
                str(destination),
            ]
        )
    finally:
        _close_all(connections)

    output = json.loads(capsys.readouterr().out)
    assert result == 0
    assert output["snapshot"] == str(destination)
    assert output["manifest"] == MANIFEST_NAME
    assert destination.is_dir()


def test_cli_fresh_option_reports_isolated_runtime(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project_root, runtime, connections = _prepare_project(tmp_path)
    destination = project_root / SNAPSHOT_ROOT_RELATIVE / "cli-fresh-snapshot"
    try:
        result = main(
            [
                "--project-root",
                str(project_root),
                "--source",
                str(runtime),
                "--output",
                str(destination),
                "--fresh",
            ]
        )
    finally:
        _close_all(connections)

    output = json.loads(capsys.readouterr().out)
    assert result == 0
    assert output["snapshot_mode"] == FRESH_SNAPSHOT_MODE
    assert output["host_runtime_records_copied"] is False
    assert not list((destination / RUNTIME_STATE_DIRECTORY).iterdir())


def test_snapshot_rejects_non_current_source(tmp_path: Path) -> None:
    project_root, _, connections = _prepare_project(tmp_path)
    alternate = project_root / "other-profile"
    alternate.mkdir()
    destination = project_root / SNAPSHOT_ROOT_RELATIVE / "wrong-source"
    try:
        with pytest.raises(RuntimeSnapshotError, match="current project siq_analysis"):
            snapshot_runtime(project_root=project_root, source=alternate, destination=destination)
    finally:
        _close_all(connections)


def test_snapshot_rejects_fifo_in_allowed_tree(tmp_path: Path) -> None:
    project_root, runtime, connections = _prepare_project(tmp_path)
    os.mkfifo(runtime / "sessions/runtime.pipe")
    destination = project_root / SNAPSHOT_ROOT_RELATIVE / "fifo-rejected"
    try:
        with pytest.raises(RuntimeSnapshotError, match="non-regular entry"):
            snapshot_runtime(project_root=project_root, destination=destination)
    finally:
        _close_all(connections)

    assert not destination.exists()
