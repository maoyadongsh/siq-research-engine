import importlib.util
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
