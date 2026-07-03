import importlib.util
import sys
from pathlib import Path


def _load_module(name: str, relative: str):
    source = Path(__file__).resolve().parents[1] / "services" / relative
    spec = importlib.util.spec_from_file_location(name, source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


command_runner = _load_module("temp_command_runner", "command_runner.py")


def test_format_command_redacts_database_url():
    command = command_runner.format_command(["python", "script.py", "--database-url", "postgres://secret", "--ddl"])

    assert "postgres://secret" not in command
    assert command.endswith("--ddl")
    assert "--database-url ***" in command


def test_format_command_redacts_database_url_equals_form():
    command = command_runner.format_command(["python", "script.py", "--database-url=postgres://secret", "--ddl"])

    assert "postgres://secret" not in command
    assert "--database-url=***" in command
    assert command.endswith("--ddl")


def test_format_command_redacts_common_secret_options():
    command = command_runner.format_command(
        [
            "python",
            "script.py",
            "--db-url",
            "postgres://secret",
            "--api-key=sk-secret",
            "--password",
            "p@ssword",
            "DATABASE_URL=postgres://env-secret",
            "PGPASSWORD=env-password",
            "--safe",
            "visible",
        ]
    )

    assert "postgres://secret" not in command
    assert "sk-secret" not in command
    assert "p@ssword" not in command
    assert "postgres://env-secret" not in command
    assert "env-password" not in command
    assert "--db-url ***" in command
    assert "--api-key=***" in command
    assert "--password ***" in command
    assert "DATABASE_URL=***" in command
    assert "PGPASSWORD=***" in command
    assert "--safe visible" in command


def test_run_command_returns_nonzero_result_without_raising(tmp_path):
    completed = command_runner.run_command(
        [
            sys.executable,
            "-c",
            "import sys; print('stdout-ok'); print('stderr-ok', file=sys.stderr); sys.exit(7)",
        ],
        cwd=tmp_path,
        timeout=5,
    )

    assert completed.returncode == 7
    assert completed.stdout.strip() == "stdout-ok"
    assert completed.stderr.strip() == "stderr-ok"


def test_run_command_passes_env_mapping(tmp_path):
    completed = command_runner.run_command(
        [
            sys.executable,
            "-c",
            "import os; print(os.environ['COMMAND_RUNNER_TEST_VALUE'])",
        ],
        cwd=tmp_path,
        timeout=5,
        env={"COMMAND_RUNNER_TEST_VALUE": "from-env"},
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "from-env"
