from __future__ import annotations

import gzip
import hashlib
import os
import re
import subprocess
import tarfile
from pathlib import Path

import pytest

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
        "#!/usr/bin/env bash\n"
        "case \"$PGDATABASE\" in\n"
        "  postgresql://backup:secret@127.0.0.1:5432/*?sslmode=disable) ;;\n"
        "  *) exit 91 ;;\n"
        "esac\n"
        "database_name=\"${PGDATABASE%%\\?*}\"\n"
        "printf 'database=%s args=%s\\n' \"${database_name##*/}\" \"$*\" >> \"$PG_DUMP_LOG\"\n"
        "printf '%s\\n' '-- portable test dump'\n",
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
        [
            *(f"{name}.sql.gz" for name in database_names),
            *(f"{name}.schema.sql.gz" for name in database_names),
        ]
    )
    subprocess.run(["sha256sum", "--check", "checksums.sha256"], cwd=run_dir, check=True, capture_output=True)
    calls = (tmp_path / "pg_dump.log").read_text(encoding="utf-8")
    for database in database_names:
        assert f"database={database} args=--no-owner --no-privileges" in calls
        assert f"database={database} args=--schema-only --no-owner --no-privileges" in calls
    assert "postgresql://" not in calls
    assert "secret" not in calls


def test_backup_accepts_large_nonempty_postgres_dump_with_pipefail(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "pg_dump",
        "#!/usr/bin/env bash\n"
        "for ((line = 0; line < 20000; line++)); do\n"
        "  printf '%s\\n' 'INSERT INTO sample VALUES (1, 2, 3, 4, 5);'\n"
        "done\n",
    )
    backup_root = tmp_path / "backups"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DATABASE_URL": "postgresql://backup:secret@127.0.0.1:5432/siq_app",
        "SIQ_BACKUP_DATABASES": "siq_app",
        "SIQ_BACKUP_DIR": str(backup_root),
        "SIQ_BACKUP_SKIP_LARGE": "1",
        "SIQ_BACKUP_RETENTION_DAYS": "0",
        "SIQ_BACKEND_DATA_ROOT": str(tmp_path / "missing-backend"),
    }

    result = subprocess.run([str(BACKUP_SCRIPT)], env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stdout + result.stderr
    run_dir = next(backup_root.iterdir())
    assert (run_dir / "postgres" / "siq_app.sql.gz").stat().st_size > 0


def test_backup_required_mode_fails_without_database_url(tmp_path):
    env = {
        **os.environ,
        "SIQ_BACKUP_DIR": str(tmp_path / "backups"),
        "SIQ_BACKUP_MODE": "required",
        "SIQ_BACKUP_RETENTION_DAYS": "0",
    }

    result = subprocess.run([str(BACKUP_SCRIPT)], env=env, capture_output=True, text=True)

    assert result.returncode != 0
    assert "DATABASE_URL" in result.stderr or "DATABASE_URL" in result.stdout


def test_backup_required_mode_rejects_skip_large(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "pg_dump",
        "#!/usr/bin/env bash\nprintf '%s\\n' pg_dump >> \"$BACKUP_COMMAND_LOG\"\n",
    )
    _write_executable(
        fake_bin / "tar",
        "#!/usr/bin/env bash\nprintf '%s\\n' tar >> \"$BACKUP_COMMAND_LOG\"\n",
    )
    command_log = tmp_path / "commands.log"
    backup_root = tmp_path / "backups"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "BACKUP_COMMAND_LOG": str(command_log),
        "SIQ_BACKUP_DIR": str(backup_root),
        "SIQ_BACKUP_MODE": "required",
        "SIQ_BACKUP_SKIP_LARGE": "1",
        "DATABASE_URL": "postgresql://backup:secret@127.0.0.1:5432/siq_app",
        "SIQ_BACKUP_RETENTION_DAYS": "0",
    }

    result = subprocess.run([str(BACKUP_SCRIPT)], env=env, capture_output=True, text=True)

    assert result.returncode != 0
    assert "禁止跳过大目录" in result.stdout
    assert not command_log.exists()
    assert not backup_root.exists()


def test_backup_manifest_records_object_status_size_and_source(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "pg_dump",
        "#!/usr/bin/env bash\nprintf '%s\n' '-- portable test dump'\n",
    )
    source_dir = tmp_path / "backend"
    source_dir.mkdir()
    (source_dir / "marker.txt").write_text("marker\n", encoding="utf-8")
    backup_root = tmp_path / "backups"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DATABASE_URL": "postgresql://backup:secret@127.0.0.1:5432/siq_app",
        "SIQ_BACKUP_DIR": str(backup_root),
        "SIQ_BACKUP_SKIP_LARGE": "1",
        "SIQ_BACKUP_RETENTION_DAYS": "0",
        "SIQ_BACKEND_DATA_ROOT": str(source_dir),
    }

    subprocess.run([str(BACKUP_SCRIPT)], env=env, check=True, capture_output=True, text=True)

    run_dir = next(backup_root.iterdir())
    manifest = (run_dir / "manifest.txt").read_text(encoding="utf-8")
    assert "object=postgres/siq_app.sql.gz status=ok size=" in manifest
    assert "object=postgres/siq_app.schema.sql.gz status=ok size=" in manifest
    assert "source=DATABASE_URL" in manifest
    assert "object=backend-data.tar.gz status=ok size=" in manifest
    assert f"source={source_dir}" in manifest
    checksum_lines = (run_dir / "checksums.sha256").read_text(encoding="utf-8")
    assert "postgres/siq_app.sql.gz" in checksum_lines
    assert "postgres/siq_app.schema.sql.gz" in checksum_lines
    assert "backend-data.tar.gz" in checksum_lines
    assert "object=hermes-home.tar.gz status=skipped size=0" in manifest
    assert "hermes-profiles.tar.gz" not in manifest
    assert "schema_contract_version=siq_postgres_schema_contract_v1" in manifest
    authority = re.search(r"schema_authority_sha256_siq_app=([0-9a-f]{64})", manifest)
    assert authority is not None
    assert "schema_snapshot_siq_app=postgres/siq_app.schema.sql.gz" in manifest


def test_backup_required_mode_archives_complete_hermes_home_once(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "pg_dump",
        "#!/usr/bin/env bash\nprintf '%s\n' '-- portable test dump'\n",
    )
    backend = tmp_path / "backend"
    pdf_parser = tmp_path / "pdf-parser"
    wiki = tmp_path / "wiki"
    downloads = tmp_path / "downloads"
    hermes_home = tmp_path / "hermes" / "home"
    for source in (backend, pdf_parser, wiki, downloads):
        source.mkdir(parents=True)
        (source / "marker.txt").write_text("marker\n", encoding="utf-8")
    (hermes_home / "profiles" / "research").mkdir(parents=True)
    (hermes_home / "profiles" / "research" / "profile.yaml").write_text(
        "name: research\n",
        encoding="utf-8",
    )
    (hermes_home / "state.db").write_text("durable-state\n", encoding="utf-8")
    backup_root = tmp_path / "backups"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DATABASE_URL": "postgresql://backup:secret@127.0.0.1:5432/siq_app",
        "SIQ_BACKUP_DATABASES": "siq_app",
        "SIQ_BACKUP_DIR": str(backup_root),
        "SIQ_BACKUP_MODE": "required",
        "SIQ_BACKUP_SKIP_LARGE": "0",
        "SIQ_BACKUP_RETENTION_DAYS": "0",
        "SIQ_BACKEND_DATA_ROOT": str(backend),
        "SIQ_PDF2MD_DATA_DIR": str(pdf_parser),
        "SIQ_WIKI_ROOT": str(wiki),
        "SIQ_REPORT_DOWNLOADS_ROOT": str(downloads),
        "SIQ_HERMES_HOME": str(hermes_home),
    }

    subprocess.run([str(BACKUP_SCRIPT)], env=env, check=True, capture_output=True, text=True)

    run_dir = next(backup_root.iterdir())
    archive = run_dir / "hermes-home.tar.gz"
    assert archive.is_file() and archive.stat().st_size > 0
    assert not (run_dir / "hermes-profiles.tar.gz").exists()
    with tarfile.open(archive, mode="r:gz") as bundle:
        members = set(bundle.getnames())
    assert "home/state.db" in members
    assert "home/profiles/research/profile.yaml" in members
    manifest = (run_dir / "manifest.txt").read_text(encoding="utf-8")
    assert "object=hermes-home.tar.gz status=ok size=" in manifest
    assert f"source={hermes_home}" in manifest
    assert "hermes-profiles.tar.gz" not in manifest
    checksums = (run_dir / "checksums.sha256").read_text(encoding="utf-8")
    assert "  hermes-home.tar.gz\n" in checksums


def test_restore_smoke_is_noop_without_explicit_enable():
    result = subprocess.run([str(RESTORE_SCRIPT)], check=True, capture_output=True, text=True, env={**os.environ})

    assert "SIQ_RESTORE_SMOKE=1" in result.stdout


def test_restore_smoke_uses_disposable_database_and_cleans_up(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log_path = tmp_path / "commands.log"
    _write_executable(
        fake_bin / "createdb",
        "#!/usr/bin/env bash\n"
        "case \"$PGDATABASE\" in postgresql://admin:secret@127.0.0.1:5432/postgres) ;; *) exit 91 ;; esac\n"
        "printf 'createdb %s\\n' \"$*\" >> \"$RESTORE_COMMAND_LOG\"\n",
    )
    _write_executable(
        fake_bin / "dropdb",
        "#!/usr/bin/env bash\n"
        "case \"$PGDATABASE\" in postgresql://admin:secret@127.0.0.1:5432/postgres) ;; *) exit 92 ;; esac\n"
        "printf 'dropdb %s\\n' \"$*\" >> \"$RESTORE_COMMAND_LOG\"\n",
    )
    _write_executable(
        fake_bin / "psql",
        """#!/usr/bin/env bash
case "$PGDATABASE" in
  postgresql://admin:secret@127.0.0.1:5432/siq_restore_smoke_*) ;;
  *) exit 93 ;;
esac
	printf 'psql %s\\n' "$*" >> "$RESTORE_COMMAND_LOG"
case "$*" in
  *"to_regclass('sec_us.filings') is not null"*) printf '%s\\n' 't' ;;
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
    created = re.search(r"createdb (siq_restore_smoke_[0-9_]+)$", commands, re.MULTILINE)
    dropped = re.search(r"dropdb --if-exists (siq_restore_smoke_[0-9_]+)$", commands, re.MULTILINE)
    assert created is not None and dropped is not None
    assert created.group(1) == dropped.group(1)
    assert "postgresql://" not in commands
    assert "secret" not in commands


def test_restore_smoke_cleans_up_after_restore_failure_without_credentials_in_argv(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log_path = tmp_path / "commands.log"
    _write_executable(
        fake_bin / "createdb",
        "#!/usr/bin/env bash\n"
        "case \"$PGDATABASE\" in postgresql://admin:secret@127.0.0.1:5432/postgres) ;; *) exit 91 ;; esac\n"
        "printf 'createdb %s\\n' \"$*\" >> \"$RESTORE_COMMAND_LOG\"\n",
    )
    _write_executable(
        fake_bin / "dropdb",
        "#!/usr/bin/env bash\n"
        "case \"$PGDATABASE\" in postgresql://admin:secret@127.0.0.1:5432/postgres) ;; *) exit 92 ;; esac\n"
        "printf 'dropdb %s\\n' \"$*\" >> \"$RESTORE_COMMAND_LOG\"\n",
    )
    _write_executable(
        fake_bin / "psql",
        "#!/usr/bin/env bash\n"
        "case \"$PGDATABASE\" in postgresql://admin:secret@127.0.0.1:5432/siq_restore_smoke_*) ;; *) exit 93 ;; esac\n"
        "printf 'psql %s\\n' \"$*\" >> \"$RESTORE_COMMAND_LOG\"\n"
        "exit 42\n",
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

    result = subprocess.run([str(RESTORE_SCRIPT)], env=env, capture_output=True, text=True)

    assert result.returncode != 0
    commands = log_path.read_text(encoding="utf-8")
    created = re.search(r"createdb (siq_restore_smoke_[0-9_]+)$", commands, re.MULTILINE)
    dropped = re.search(r"dropdb --if-exists (siq_restore_smoke_[0-9_]+)$", commands, re.MULTILINE)
    assert created is not None and dropped is not None
    assert created.group(1) == dropped.group(1)
    assert "postgresql://" not in commands
    assert "secret" not in commands


def test_restore_smoke_allows_explicit_non_market_relation_probe(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "createdb", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(fake_bin / "dropdb", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        fake_bin / "psql",
        """#!/usr/bin/env bash
case "$*" in
  *"select relkind"*) printf '%s\n' 'r' ;;
  *"to_regclass('public.users') is not null"*) printf '%s\n' 't' ;;
esac
cat >/dev/null || true
""",
    )
    source = tmp_path / "siq_app.sql"
    source.write_text("select 1;\n", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "SIQ_RESTORE_SMOKE": "1",
        "SIQ_RESTORE_SMOKE_SOURCE": str(source),
        "SIQ_RESTORE_SMOKE_ADMIN_URL": "postgresql://admin:secret@127.0.0.1:5432/postgres",
        "SIQ_RESTORE_SMOKE_EXPECTED_RELATIONS": "public.users",
        "SIQ_RESTORE_SMOKE_AGENT_VIEW": "public.users",
        "SIQ_RESTORE_SMOKE_REQUIRE_AGENT_VIEW": "0",
    }

    result = subprocess.run([str(RESTORE_SCRIPT)], env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stdout + result.stderr


def test_siq_app_required_restore_invokes_voiceprint_tombstone_acceptance(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    invocation = tmp_path / "voiceprint-reconcile.log"
    _write_executable(fake_bin / "createdb", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(fake_bin / "dropdb", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        fake_bin / "psql",
        "#!/usr/bin/env bash\n"
        "case \"$*\" in\n"
        "  *\"to_regclass('public.users') is not null\"*) printf '%s\\n' 't' ;;\n"
        "  *\"select relkind\"*) printf '%s\\n' 'r' ;;\n"
        "esac\n"
        "cat >/dev/null || true\n",
    )
    _write_executable(
        fake_bin / "api-python",
        "#!/usr/bin/env bash\n"
        "case \"$SIQ_APP_DATABASE_URL\" in postgresql+psycopg://admin:secret@127.0.0.1:5432/siq_restore_smoke_*) ;; *) exit 94 ;; esac\n"
        "case \"$*\" in *reconcile_meeting_voiceprint_tombstones.py*--apply*--require-ledger-file*--require-ledger-checkpoint*) ;; *) exit 95 ;; esac\n"
        "printf '%s\\n' invoked > \"$VOICEPRINT_INVOCATION_LOG\"\n",
    )
    source = tmp_path / "siq_app.sql"
    source.write_text("select 1;\n", encoding="utf-8")
    checksum = tmp_path / "checksums.sha256"
    checksum.write_text(
        f"{hashlib.sha256(source.read_bytes()).hexdigest()}  {source.name}\n",
        encoding="utf-8",
    )
    ledger = tmp_path / "security" / "voiceprint-tombstones.jsonl"
    ledger.parent.mkdir(mode=0o700)
    ledger.touch(mode=0o600)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "VOICEPRINT_INVOCATION_LOG": str(invocation),
        "SIQ_API_PYTHON": str(fake_bin / "api-python"),
        "SIQ_RESTORE_SMOKE": "1",
        "SIQ_RESTORE_SMOKE_MODE": "required",
        "SIQ_RESTORE_SMOKE_SOURCE": str(source),
        "SIQ_RESTORE_SMOKE_ADMIN_URL": "postgresql://admin:secret@127.0.0.1:5432/postgres",
        "SIQ_RESTORE_SMOKE_EXPECTED_RELATIONS": "public.users",
        "SIQ_RESTORE_SMOKE_AGENT_VIEW": "public.users",
        "SIQ_RESTORE_SMOKE_REQUIRE_AGENT_VIEW": "0",
        "SIQ_RESTORE_SMOKE_CHECKSUM_MANIFEST": str(checksum),
        "SIQ_RESTORE_SMOKE_NONEMPTY_RELATIONS": "",
        "SIQ_RESTORE_SMOKE_DATABASE_NAME": "siq_app",
        "SIQ_RESTORE_SMOKE_VOICEPRINT_TOMBSTONE_REQUIRED": "1",
        "SIQ_MEETING_VOICEPRINT_TOMBSTONE_PATH": str(ledger),
        "SIQ_MEETING_VOICEPRINT_TOMBSTONE_HMAC_KEY": "test-only",
        "SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_COUNT": "0",
        "SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_HEAD_HMAC": "0" * 64,
    }

    result = subprocess.run([str(RESTORE_SCRIPT)], env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert invocation.read_text(encoding="utf-8") == "invoked\n"
    assert "restore_phase=voiceprint_tombstone status=passed" in result.stdout


@pytest.mark.parametrize(
    ("count", "head_hmac", "message"),
    [
        (None, "0" * 64, "EXPECTED_COUNT"),
        ("-1", "0" * 64, "EXPECTED_COUNT"),
        ("0", None, "EXPECTED_HEAD_HMAC"),
        ("0", "g" * 64, "EXPECTED_HEAD_HMAC"),
        ("0", "1" * 64, "全零 EXPECTED_HEAD_HMAC"),
    ],
)
def test_required_voiceprint_restore_rejects_invalid_checkpoint_before_createdb(
    tmp_path, count, head_hmac, message
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    command_log = tmp_path / "commands.log"
    for command in ("createdb", "dropdb", "psql"):
        _write_executable(
            fake_bin / command,
            "#!/usr/bin/env bash\nprintf '%s\\n' invoked >> \"$RESTORE_COMMAND_LOG\"\n",
        )
    source = tmp_path / "siq_app.sql"
    source.write_text("select 1;\n", encoding="utf-8")
    checksum = tmp_path / "checksums.sha256"
    checksum.write_text(
        f"{hashlib.sha256(source.read_bytes()).hexdigest()}  {source.name}\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RESTORE_COMMAND_LOG": str(command_log),
        "SIQ_RESTORE_SMOKE": "1",
        "SIQ_RESTORE_SMOKE_MODE": "required",
        "SIQ_RESTORE_SMOKE_SOURCE": str(source),
        "SIQ_RESTORE_SMOKE_ADMIN_URL": "postgresql://admin:secret@127.0.0.1:5432/postgres",
        "SIQ_RESTORE_SMOKE_EXPECTED_RELATIONS": "public.users",
        "SIQ_RESTORE_SMOKE_AGENT_VIEW": "public.users",
        "SIQ_RESTORE_SMOKE_CHECKSUM_MANIFEST": str(checksum),
        "SIQ_RESTORE_SMOKE_DATABASE_NAME": "siq_app",
        "SIQ_RESTORE_SMOKE_VOICEPRINT_TOMBSTONE_REQUIRED": "1",
    }
    if count is None:
        env.pop("SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_COUNT", None)
    else:
        env["SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_COUNT"] = count
    if head_hmac is None:
        env.pop("SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_HEAD_HMAC", None)
    else:
        env["SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_HEAD_HMAC"] = head_hmac

    result = subprocess.run([str(RESTORE_SCRIPT)], env=env, capture_output=True, text=True)

    assert result.returncode != 0
    assert message in result.stdout
    assert not command_log.exists()


def test_restore_required_mode_requires_checksum_manifest_and_source_entry(tmp_path):
    source = tmp_path / "siq_us.sql"
    source.write_text("select 1;\n", encoding="utf-8")
    env = {
        **os.environ,
        "SIQ_RESTORE_SMOKE": "1",
        "SIQ_RESTORE_SMOKE_MODE": "required",
        "SIQ_RESTORE_SMOKE_SOURCE": str(source),
        "SIQ_RESTORE_SMOKE_ADMIN_URL": "postgresql://admin:secret@127.0.0.1:5432/postgres",
        "SIQ_RESTORE_SMOKE_EXPECTED_RELATIONS": "sec_us.filings",
        "SIQ_RESTORE_SMOKE_AGENT_VIEW": "sec_us.v_agent_financial_facts",
    }

    result = subprocess.run([str(RESTORE_SCRIPT)], env=env, capture_output=True, text=True)

    assert result.returncode != 0
    assert "校验清单" in result.stdout or "checksum" in result.stdout.lower()


def test_restore_required_mode_honors_explicit_empty_nonempty_probe(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "createdb", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(fake_bin / "dropdb", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        fake_bin / "psql",
        """#!/usr/bin/env bash
case "$*" in
  *"to_regclass('public.users') is not null"*) printf '%s\n' 't' ;;
  *"select relkind"*) printf '%s\n' 'r' ;;
  *"count(*)"*) exit 99 ;;
esac
cat >/dev/null || true
""",
    )
    source = tmp_path / "siq_app.sql"
    source.write_text("select 1;\n", encoding="utf-8")
    checksum = tmp_path / "checksums.sha256"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    checksum.write_text(f"{digest}  {source.name}\n", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "SIQ_RESTORE_SMOKE": "1",
        "SIQ_RESTORE_SMOKE_MODE": "required",
        "SIQ_RESTORE_SMOKE_SOURCE": str(source),
        "SIQ_RESTORE_SMOKE_ADMIN_URL": "postgresql://admin:secret@127.0.0.1:5432/postgres",
        "SIQ_RESTORE_SMOKE_EXPECTED_RELATIONS": "public.users",
        "SIQ_RESTORE_SMOKE_AGENT_VIEW": "public.users",
        "SIQ_RESTORE_SMOKE_REQUIRE_AGENT_VIEW": "0",
        "SIQ_RESTORE_SMOKE_CHECKSUM_MANIFEST": str(checksum),
        "SIQ_RESTORE_SMOKE_NONEMPTY_RELATIONS": "",
    }

    result = subprocess.run([str(RESTORE_SCRIPT)], env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stdout + result.stderr


def test_restore_required_mode_rejects_source_missing_from_checksum_manifest(tmp_path):
    source = tmp_path / "siq_us.sql"
    source.write_text("select 1;\n", encoding="utf-8")
    other = tmp_path / "other.sql"
    other.write_text("select 2;\n", encoding="utf-8")
    digest = hashlib.sha256(other.read_bytes()).hexdigest()
    checksum = tmp_path / "checksums.sha256"
    checksum.write_text(f"{digest}  {other.name}\n", encoding="utf-8")
    env = {
        **os.environ,
        "SIQ_RESTORE_SMOKE": "1",
        "SIQ_RESTORE_SMOKE_MODE": "required",
        "SIQ_RESTORE_SMOKE_SOURCE": str(source),
        "SIQ_RESTORE_SMOKE_ADMIN_URL": "postgresql://admin:secret@127.0.0.1:5432/postgres",
        "SIQ_RESTORE_SMOKE_EXPECTED_RELATIONS": "sec_us.filings",
        "SIQ_RESTORE_SMOKE_AGENT_VIEW": "sec_us.v_agent_financial_facts",
        "SIQ_RESTORE_SMOKE_CHECKSUM_MANIFEST": str(checksum),
    }

    result = subprocess.run([str(RESTORE_SCRIPT)], env=env, capture_output=True, text=True)

    assert result.returncode != 0
    assert "未包含恢复源" in result.stdout


def test_restore_nonempty_probe_rejects_empty_agent_view(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "createdb", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(fake_bin / "dropdb", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        fake_bin / "psql",
        """#!/usr/bin/env bash
case "$*" in
  *"to_regclass('sec_us.filings') is not null"*) printf '%s\\n' 't' ;;
  *"select relkind"*) printf '%s\\n' 'v' ;;
  *"count(*)"*) printf '%s\\n' '0' ;;
esac
cat >/dev/null || true
""",
    )
    source = tmp_path / "siq_us.sql"
    source.write_text("select 1;\n", encoding="utf-8")
    checksum = tmp_path / "checksums.sha256"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    checksum.write_text(f"{digest} *{source.name}\n", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "SIQ_RESTORE_SMOKE": "1",
        "SIQ_RESTORE_SMOKE_MODE": "required",
        "SIQ_RESTORE_SMOKE_SOURCE": str(source),
        "SIQ_RESTORE_SMOKE_ADMIN_URL": "postgresql://admin:secret@127.0.0.1:5432/postgres",
        "SIQ_RESTORE_SMOKE_EXPECTED_RELATIONS": "sec_us.filings",
        "SIQ_RESTORE_SMOKE_AGENT_VIEW": "sec_us.v_agent_financial_facts",
        "SIQ_RESTORE_SMOKE_CHECKSUM_MANIFEST": str(checksum),
        "SIQ_RESTORE_SMOKE_NONEMPTY_RELATIONS": "sec_us.v_agent_financial_facts",
    }

    result = subprocess.run([str(RESTORE_SCRIPT)], env=env, capture_output=True, text=True)

    assert result.returncode != 0
    assert "非空" in result.stdout or "empty" in result.stdout.lower()


def test_restore_required_mode_compares_restored_schema_snapshot(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "createdb", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(fake_bin / "dropdb", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        fake_bin / "psql",
        "#!/usr/bin/env bash\n"
        "case \"$*\" in\n"
        "  *\"to_regclass('public.users') is not null\"*) printf '%s\\n' 't' ;;\n"
        "  *\"select relkind\"*) printf '%s\\n' 'r' ;;\n"
        "esac\n"
        "cat >> \"$PSQL_STDIN_LOG\" || true\n",
    )
    _write_executable(
        fake_bin / "pg_dump",
        "#!/usr/bin/env bash\n"
        "case \"$PGDATABASE\" in\n"
        "  *_schema) printf '%s\\n' \"$EXPECTED_CANONICAL_SCHEMA\" ;;\n"
        "  *) printf '%s\\n' \"$RESTORED_SCHEMA\" ;;\n"
        "esac\n",
    )
    source = tmp_path / "siq_app.sql"
    source.write_text("select 1;\n", encoding="utf-8")
    schema = tmp_path / "siq_app.schema.sql.gz"
    schema.write_bytes(gzip.compress(b"CREATE TABLE users ();\n"))
    migration = tmp_path / "006_additive.sql"
    migration.write_text("CREATE TABLE IF NOT EXISTS durable_jobs ();\n", encoding="utf-8")
    psql_stdin_log = tmp_path / "psql-stdin.log"
    checksum = tmp_path / "checksums.sha256"
    checksum.write_text(
        "\n".join(
            [
                f"{hashlib.sha256(source.read_bytes()).hexdigest()}  {source.name}",
                f"{hashlib.sha256(schema.read_bytes()).hexdigest()}  {schema.name}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "SIQ_RESTORE_SMOKE": "1",
        "SIQ_RESTORE_SMOKE_MODE": "required",
        "SIQ_RESTORE_SMOKE_SOURCE": str(source),
        "SIQ_RESTORE_SMOKE_ADMIN_URL": "postgresql://admin:secret@127.0.0.1:5432/postgres",
        "SIQ_RESTORE_SMOKE_EXPECTED_RELATIONS": "public.users",
        "SIQ_RESTORE_SMOKE_AGENT_VIEW": "public.users",
        "SIQ_RESTORE_SMOKE_REQUIRE_AGENT_VIEW": "0",
        "SIQ_RESTORE_SMOKE_CHECKSUM_MANIFEST": str(checksum),
        "SIQ_RESTORE_SMOKE_NONEMPTY_RELATIONS": "",
        "SIQ_RESTORE_SMOKE_EXPECTED_SCHEMA_SNAPSHOT": str(schema),
        "EXPECTED_CANONICAL_SCHEMA": "CREATE TABLE users ();",
        "RESTORED_SCHEMA": "CREATE TABLE users ();",
        "PSQL_STDIN_LOG": str(psql_stdin_log),
        "SIQ_RESTORE_SMOKE_COMPATIBILITY_MIGRATION": str(migration),
    }

    result = subprocess.run([str(RESTORE_SCRIPT)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    migration_input = psql_stdin_log.read_text(encoding="utf-8")
    assert "BEGIN;" in migration_input
    assert "CREATE TABLE IF NOT EXISTS durable_jobs ();" in migration_input
    assert "ROLLBACK;" in migration_input
    assert "restore_phase=schema_snapshot status=passed" in result.stdout
    assert "restore_phase=migration_compatibility status=passed" in result.stdout

    env["RESTORED_SCHEMA"] = "CREATE TABLE users (id bigint);"
    result = subprocess.run([str(RESTORE_SCRIPT)], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "schema 版本" in result.stdout
    assert "restore_phase=schema_snapshot status=failed" in result.stdout
    assert "restore_phase=migration_compatibility status=started" not in result.stdout


def test_restore_rejects_backup_schema_behind_complete_app_authority(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    authority_log = tmp_path / "authority.log"
    authority_applied = tmp_path / "authority-applied"
    _write_executable(fake_bin / "createdb", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(fake_bin / "dropdb", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        fake_bin / "psql",
        "#!/usr/bin/env bash\n"
        "if [[ \"$PGDATABASE\" == *_schema && \"$*\" == *' -f '* ]]; then\n"
        "  migration=\"${!#}\"\n"
        "  basename \"$migration\" >> \"$AUTHORITY_LOG\"\n"
        "  case \"$migration\" in *006_create_runtime_coordination_tables.sql) touch \"$AUTHORITY_APPLIED\" ;; esac\n"
        "fi\n"
        "cat >/dev/null || true\n",
    )
    _write_executable(
        fake_bin / "pg_dump",
        "#!/usr/bin/env bash\n"
        "if [[ \"$PGDATABASE\" == *_schema && -f \"$AUTHORITY_APPLIED\" ]]; then\n"
        "  printf '%s\\n' 'CREATE TABLE authority_added ();'\n"
        "else\n"
        "  printf '%s\\n' 'CREATE TABLE base_schema ();'\n"
        "fi\n",
    )
    source = tmp_path / "siq_app.sql"
    source.write_text("select 1;\n", encoding="utf-8")
    schema = tmp_path / "siq_app.schema.sql.gz"
    schema.write_bytes(gzip.compress(b"CREATE TABLE base_schema ();\n"))
    checksum = tmp_path / "checksums.sha256"
    checksum.write_text(
        "\n".join(
            [
                f"{hashlib.sha256(source.read_bytes()).hexdigest()}  {source.name}",
                f"{hashlib.sha256(schema.read_bytes()).hexdigest()}  {schema.name}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    authorities = sorted((ROOT / "apps" / "api" / "migrations").glob("*.sql"))
    assert len(authorities) == 8
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "AUTHORITY_LOG": str(authority_log),
        "AUTHORITY_APPLIED": str(authority_applied),
        "SIQ_RESTORE_SMOKE": "1",
        "SIQ_RESTORE_SMOKE_MODE": "required",
        "SIQ_RESTORE_SMOKE_SOURCE": str(source),
        "SIQ_RESTORE_SMOKE_ADMIN_URL": "postgresql://admin:secret@127.0.0.1:5432/postgres",
        "SIQ_RESTORE_SMOKE_EXPECTED_RELATIONS": "public.users",
        "SIQ_RESTORE_SMOKE_AGENT_VIEW": "public.users",
        "SIQ_RESTORE_SMOKE_REQUIRE_AGENT_VIEW": "0",
        "SIQ_RESTORE_SMOKE_CHECKSUM_MANIFEST": str(checksum),
        "SIQ_RESTORE_SMOKE_NONEMPTY_RELATIONS": "",
        "SIQ_RESTORE_SMOKE_DATABASE_NAME": "siq_app",
        "SIQ_RESTORE_SMOKE_EXPECTED_SCHEMA_SNAPSHOT": str(schema),
        "SIQ_RESTORE_SMOKE_COMPATIBILITY_MIGRATIONS": "\n".join(
            str(path) for path in authorities
        ),
    }

    result = subprocess.run([str(RESTORE_SCRIPT)], env=env, capture_output=True, text=True)

    assert result.returncode != 0
    assert "restore_phase=schema_snapshot status=passed" in result.stdout
    assert "restore_phase=migration_compatibility status=failed" in result.stdout
    assert "backup_schema_behind_authority" in result.stdout
    assert authority_log.read_text(encoding="utf-8").splitlines() == [
        path.name for path in authorities
    ]
