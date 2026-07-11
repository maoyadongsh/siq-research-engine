import importlib.util
import json
import stat
import sys
from pathlib import Path


def _load_module():
    source = Path(__file__).resolve().parents[1] / "check_python_quality_touched.py"
    spec = importlib.util.spec_from_file_location("check_python_quality_touched_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, text: str = "print('ok')\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_select_python_files_filters_runtime_dirs_and_non_python(tmp_path):
    module = _load_module()
    _write(tmp_path / "apps" / "api" / "service.py")
    _write(tmp_path / "scripts" / "task.sh", "#!/usr/bin/env bash\n")
    _write(tmp_path / "data" / "runtime.py")
    _write(tmp_path / "var" / "cache.py")

    selected = module.select_python_files(
        tmp_path,
        [
            Path("apps/api/service.py"),
            Path("scripts/task.sh"),
            Path("data/runtime.py"),
            Path("var/cache.py"),
        ],
        base_ref="HEAD",
        include_untracked=False,
    )

    assert selected == ["apps/api/service.py"]


def test_missing_ruff_is_advisory_by_default(tmp_path, monkeypatch):
    module = _load_module()
    _write(tmp_path / "service.py")
    monkeypatch.setenv("PATH", "")

    exit_code, result = module.run_quality_checks(tmp_path, files=[Path("service.py")])

    assert exit_code == 0
    assert result.status == "advisory"
    assert result.files == ["service.py"]
    assert result.commands == []
    assert result.messages == ["ruff is not installed; touched-file Python quality check is advisory only."]


def test_missing_ruff_can_be_required(tmp_path, monkeypatch):
    module = _load_module()
    _write(tmp_path / "service.py")
    monkeypatch.setenv("PATH", "")

    exit_code, result = module.run_quality_checks(tmp_path, files=[Path("service.py")], require_ruff=True)

    assert exit_code == 1
    assert result.status == "failed"


def test_fake_ruff_is_invoked_for_selected_files(tmp_path, monkeypatch):
    module = _load_module()
    _write(tmp_path / "service.py")
    bin_dir = tmp_path / "bin"
    ruff = bin_dir / "ruff"
    ruff.parent.mkdir()
    ruff.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        "pathlib.Path('ruff-args.json').write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    ruff.chmod(ruff.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(bin_dir))

    exit_code, result = module.run_quality_checks(tmp_path, files=[Path("service.py")])

    assert exit_code == 0
    assert result.status == "passed"
    assert result.files == ["service.py"]
    assert result.stdout == ""
    assert result.stderr == ""
    assert json.loads((tmp_path / "ruff-args.json").read_text(encoding="utf-8")) == ["check", "service.py"]


def test_fake_ruff_failure_captures_output_for_json_reports(tmp_path, monkeypatch):
    module = _load_module()
    _write(tmp_path / "service.py")
    bin_dir = tmp_path / "bin"
    ruff = bin_dir / "ruff"
    ruff.parent.mkdir()
    ruff.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "print('F401 unused import')\n"
        "print('ruff internal detail', file=sys.stderr)\n"
        "raise SystemExit(2)\n",
        encoding="utf-8",
    )
    ruff.chmod(ruff.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(bin_dir))

    exit_code, result = module.run_quality_checks(tmp_path, files=[Path("service.py")])

    assert exit_code == 2
    assert result.status == "failed"
    assert result.messages == ["ruff check failed with exit code 2."]
    assert result.stdout == "F401 unused import\n"
    assert result.stderr == "ruff internal detail\n"


def test_main_emits_json_for_advisory_result(tmp_path, monkeypatch, capsys):
    module = _load_module()
    _write(tmp_path / "service.py")
    monkeypatch.setenv("PATH", "")

    assert module.main(["--repo-root", str(tmp_path), "--json", "service.py"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "advisory"
    assert payload["files"] == ["service.py"]
