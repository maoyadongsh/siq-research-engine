import importlib.util
import json
import stat
import sys
from pathlib import Path


def _load_module():
    source = Path(__file__).resolve().parents[1] / "check_mypy_whitelist.py"
    spec = importlib.util.spec_from_file_location("check_mypy_whitelist_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_config(repo: Path) -> Path:
    _write(repo / "mypy.ini", "[mypy]\npython_version = 3.11\n")
    config = repo / "scripts" / "maintenance" / "mypy_whitelist.toml"
    _write(
        config,
        "\n".join(
            [
                "[settings]",
                'config_file = "mypy.ini"',
                "",
                "[[target]]",
                'name = "quality"',
                'paths = ["scripts/maintenance/gate.py"]',
                "",
                "[[target]]",
                'name = "contracts"',
                'paths = ["packages/contracts/src/contracts.py"]',
                "",
            ]
        ),
    )
    _write(repo / "scripts" / "maintenance" / "gate.py", "value: int = 1\n")
    _write(repo / "packages" / "contracts" / "src" / "contracts.py", "value: int = 2\n")
    return config


def _fake_python(tmp_path: Path, *, fail_mypy: bool = False, missing_mypy: bool = False) -> tuple[Path, Path]:
    calls_path = tmp_path / "mypy_calls.json"
    fake = tmp_path / "fake_python.py"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        f"calls = pathlib.Path({str(calls_path)!r})\n"
        "if sys.argv[1:3] == ['-m', 'mypy'] and '--version' in sys.argv[3:]:\n"
        f"    raise SystemExit({1 if missing_mypy else 0})\n"
        "if sys.argv[1:3] == ['-m', 'mypy']:\n"
        "    calls.write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n"
        f"    print({'mypy failure'!r} if {fail_mypy!r} else {'Success: no issues found'!r})\n"
        f"    raise SystemExit({1 if fail_mypy else 0})\n"
        "raise SystemExit(99)\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return fake, calls_path


def test_run_mypy_whitelist_checks_selected_target(tmp_path):
    module = _load_module()
    config = _write_config(tmp_path)
    fake_python, calls_path = _fake_python(tmp_path)

    exit_code, result = module.run_mypy_whitelist(
        tmp_path,
        whitelist_config=config,
        selected_names=["quality"],
        python_executable=str(fake_python),
        require_mypy=True,
    )

    assert exit_code == 0
    assert result.status == "passed"
    assert result.selected_targets == ["quality"]
    assert result.checked_paths == ["scripts/maintenance/gate.py"]
    assert json.loads(calls_path.read_text(encoding="utf-8")) == [
        "-m",
        "mypy",
        "--config-file",
        "mypy.ini",
        "scripts/maintenance/gate.py",
    ]


def test_missing_mypy_is_advisory_by_default(tmp_path):
    module = _load_module()
    config = _write_config(tmp_path)
    fake_python, _calls_path = _fake_python(tmp_path, missing_mypy=True)

    exit_code, result = module.run_mypy_whitelist(
        tmp_path,
        whitelist_config=config,
        python_executable=str(fake_python),
    )

    assert exit_code == 0
    assert result.status == "advisory"
    assert result.commands == []
    assert "mypy is not installed" in result.messages[0]


def test_missing_mypy_can_be_required(tmp_path):
    module = _load_module()
    config = _write_config(tmp_path)
    fake_python, _calls_path = _fake_python(tmp_path, missing_mypy=True)

    exit_code, result = module.run_mypy_whitelist(
        tmp_path,
        whitelist_config=config,
        python_executable=str(fake_python),
        require_mypy=True,
    )

    assert exit_code == 1
    assert result.status == "failed"


def test_unknown_target_fails_as_config_error(tmp_path, capsys):
    module = _load_module()
    config = _write_config(tmp_path)
    fake_python, _calls_path = _fake_python(tmp_path)

    exit_code = module.main(
        [
            "--repo-root",
            str(tmp_path),
            "--config",
            str(config),
            "--target",
            "missing",
            "--python-executable",
            str(fake_python),
            "--require-mypy",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["status"] == "failed"
    assert payload["messages"] == ["unknown mypy whitelist target(s): missing"]


def test_mypy_failure_propagates_exit_code(tmp_path):
    module = _load_module()
    config = _write_config(tmp_path)
    fake_python, _calls_path = _fake_python(tmp_path, fail_mypy=True)

    exit_code, result = module.run_mypy_whitelist(
        tmp_path,
        whitelist_config=config,
        python_executable=str(fake_python),
        require_mypy=True,
    )

    assert exit_code == 1
    assert result.status == "failed"
    assert "mypy failure" in result.stdout
