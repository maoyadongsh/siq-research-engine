from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
RESTORE_SCRIPT = REPO_ROOT / "scripts" / "ops" / "restore_smoke.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _base_env(tmp_path: Path, fake_bin: Path) -> dict[str, str]:
    source = tmp_path / "siq_app.sql"
    source.write_text("select 1;\n", encoding="utf-8")
    return {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RESTORE_COMMAND_LOG": str(tmp_path / "commands.log"),
        "SIQ_RESTORE_SMOKE": "1",
        "SIQ_RESTORE_SMOKE_SOURCE": str(source),
        "SIQ_RESTORE_SMOKE_ADMIN_URL": "postgresql://admin:secret@127.0.0.1:5432/postgres",
        "SIQ_RESTORE_SMOKE_EXPECTED_RELATIONS": "public.users",
        "SIQ_RESTORE_SMOKE_AGENT_VIEW": "public.users",
        "SIQ_RESTORE_SMOKE_REQUIRE_AGENT_VIEW": "0",
    }


def test_restore_does_not_drop_preexisting_database_when_primary_create_collides(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "createdb",
        "#!/usr/bin/env bash\nprintf 'createdb %s\\n' \"$*\" >> \"$RESTORE_COMMAND_LOG\"\nexit 42\n",
    )
    _write_executable(
        fake_bin / "dropdb",
        "#!/usr/bin/env bash\nprintf 'dropdb %s\\n' \"$*\" >> \"$RESTORE_COMMAND_LOG\"\n",
    )
    _write_executable(fake_bin / "psql", "#!/usr/bin/env bash\nexit 0\n")

    result = subprocess.run(
        [str(RESTORE_SCRIPT)],
        env=_base_env(tmp_path, fake_bin),
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    commands = (tmp_path / "commands.log").read_text(encoding="utf-8")
    assert "createdb siq_restore_smoke_" in commands
    assert "dropdb" not in commands


def test_restore_does_not_drop_preexisting_schema_database_on_collision(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "createdb",
        """#!/usr/bin/env bash
printf 'createdb %s\n' "$*" >> "$RESTORE_COMMAND_LOG"
case "$1" in
  *_schema) exit 42 ;;
esac
""",
    )
    _write_executable(
        fake_bin / "dropdb",
        "#!/usr/bin/env bash\nprintf 'dropdb %s\\n' \"$*\" >> \"$RESTORE_COMMAND_LOG\"\n",
    )
    _write_executable(fake_bin / "psql", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(fake_bin / "pg_dump", "#!/usr/bin/env bash\nexit 0\n")
    schema = tmp_path / "siq_app.schema.sql"
    schema.write_text("select 1;\n", encoding="utf-8")
    env = _base_env(tmp_path, fake_bin)
    env["SIQ_RESTORE_SMOKE_EXPECTED_SCHEMA_SNAPSHOT"] = str(schema)

    result = subprocess.run([str(RESTORE_SCRIPT)], env=env, capture_output=True, text=True)

    assert result.returncode != 0
    commands = (tmp_path / "commands.log").read_text(encoding="utf-8")
    created = re.findall(r"^createdb (siq_restore_smoke_[0-9_]+(?:_schema)?)$", commands, re.MULTILINE)
    dropped = re.findall(r"^dropdb --if-exists (siq_restore_smoke_[0-9_]+(?:_schema)?)$", commands, re.MULTILINE)
    assert len(created) == 2
    assert created[1].endswith("_schema")
    assert dropped == [created[0]]
