from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKUP_SCRIPT = ROOT / "scripts" / "ops" / "backup.sh"
RESTORE_SCRIPT = ROOT / "scripts" / "ops" / "restore_smoke.sh"
DATABASE_DDL = ROOT / "infra" / "docker" / "postgres-init" / "001_create_databases.sql"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def test_backup_database_defaults_match_postgres_init_contract():
    database_names = re.findall(r"\('([^']+)'\)", DATABASE_DDL.read_text(encoding="utf-8"))
    body = BACKUP_SCRIPT.read_text(encoding="utf-8")

    configured = re.search(r"SIQ_BACKUP_DATABASES:-([^}]+)", body)
    assert configured is not None
    assert configured.group(1).split(",") == database_names


def test_backup_exports_every_business_database_and_writes_valid_checksums(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "pg_dump",
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> \"$PG_DUMP_LOG\"\nprintf '%s\\n' '-- portable test dump'\n",
    )
    backup_root = tmp_path / "backups"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "PG_DUMP_LOG": str(tmp_path / "pg_dump.log"),
        "DATABASE_URL": "postgresql://backup:secret@127.0.0.1:5432/siq_app?sslmode=disable",
        "SIQ_BACKUP_DIR": str(backup_root),
        "SIQ_BACKUP_SKIP_LARGE": "1",
        "SIQ_BACKUP_RETENTION_DAYS": "0",
        "SIQ_BACKEND_DATA_ROOT": str(tmp_path / "missing-backend"),
    }

    subprocess.run([str(BACKUP_SCRIPT)], env=env, check=True, capture_output=True, text=True)

    run_dir = next(backup_root.iterdir())
    database_names = re.findall(r"\('([^']+)'\)", DATABASE_DDL.read_text(encoding="utf-8"))
    assert sorted(path.name for path in (run_dir / "postgres").glob("*.sql.gz")) == sorted(
        f"{name}.sql.gz" for name in database_names
    )
    subprocess.run(["sha256sum", "--check", "checksums.sha256"], cwd=run_dir, check=True, capture_output=True)
    calls = (tmp_path / "pg_dump.log").read_text(encoding="utf-8")
    for database in database_names:
        assert f"/{database}?sslmode=disable" in calls


def test_restore_smoke_is_noop_without_explicit_enable():
    result = subprocess.run([str(RESTORE_SCRIPT)], check=True, capture_output=True, text=True, env={**os.environ})

    assert "SIQ_RESTORE_SMOKE=1" in result.stdout


def test_restore_smoke_uses_disposable_database_and_cleans_up(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log_path = tmp_path / "commands.log"
    _write_executable(
        fake_bin / "createdb",
        "#!/usr/bin/env bash\nprintf 'createdb %s\\n' \"$*\" >> \"$RESTORE_COMMAND_LOG\"\n",
    )
    _write_executable(
        fake_bin / "dropdb",
        "#!/usr/bin/env bash\nprintf 'dropdb %s\\n' \"$*\" >> \"$RESTORE_COMMAND_LOG\"\n",
    )
    _write_executable(
        fake_bin / "psql",
        """#!/usr/bin/env bash
printf 'psql %s\\n' "$*" >> "$RESTORE_COMMAND_LOG"
case "$*" in
  *"to_regclass('sec_us.filings')"*) printf '%s\\n' 'sec_us.filings' ;;
  *"select relkind"*) printf '%s\\n' 'v' ;;
  *) cat >/dev/null || true ;;
esac
""",
    )
    source = tmp_path / "siq_us.sql"
    source.write_text("select 1;\n", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RESTORE_COMMAND_LOG": str(log_path),
        "SIQ_RESTORE_SMOKE": "1",
        "SIQ_RESTORE_SMOKE_SOURCE": str(source),
        "SIQ_RESTORE_SMOKE_ADMIN_URL": "postgresql://admin:secret@127.0.0.1:5432/postgres",
        "SIQ_RESTORE_SMOKE_EXPECTED_RELATIONS": "sec_us.filings",
        "SIQ_RESTORE_SMOKE_AGENT_VIEW": "sec_us.v_agent_financial_facts",
    }

    result = subprocess.run([str(RESTORE_SCRIPT)], env=env, check=True, capture_output=True, text=True)

    assert "恢复冒烟通过" in result.stdout
    commands = log_path.read_text(encoding="utf-8")
    created = re.search(r"createdb .* (siq_restore_smoke_[0-9_]+)$", commands, re.MULTILINE)
    dropped = re.search(r"dropdb .* (siq_restore_smoke_[0-9_]+)$", commands, re.MULTILINE)
    assert created is not None and dropped is not None
    assert created.group(1) == dropped.group(1)
