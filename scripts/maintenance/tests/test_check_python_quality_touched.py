import importlib.util
import json
import os
import shutil
import stat
import subprocess
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


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _init_repo(repo: Path, files: dict[str, str] | None = None) -> str:
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "quality-gate@example.invalid")
    _git(repo, "config", "user.name", "Quality Gate Test")
    _write(repo / "ruff.toml", '[lint]\nselect = ["E", "F"]\n')
    for name, text in (files or {}).items():
        _write(repo / name, text)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "baseline")
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _install_fake_ruff(tmp_path: Path, monkeypatch) -> Path:
    bin_dir = tmp_path / "bin"
    ruff = bin_dir / "ruff"
    ruff.parent.mkdir()
    ruff.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, re, sys\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('ruff 9.9.9')\n"
        "    raise SystemExit(0)\n"
        "if any(pathlib.Path(arg).is_file() and 'RUFF_CRASH' in pathlib.Path(arg).read_text() "
        "for arg in sys.argv[1:] if arg.endswith('.py')):\n"
        "    print('synthetic ruff crash', file=sys.stderr)\n"
        "    raise SystemExit(2)\n"
        "findings = []\n"
        "for arg in sys.argv[1:]:\n"
        "    if not arg.endswith('.py'):\n"
        "        continue\n"
        "    path = pathlib.Path(arg)\n"
        "    for row, line in enumerate(path.read_text().splitlines(), 1):\n"
        "        match = re.search(r'# ruff: ([A-Z][0-9]+) (.+)$', line)\n"
        "        if match:\n"
        "            findings.append({'filename': str(path.resolve()), 'location': {'row': row, 'column': 1}, "
        "'end_location': {'row': row, 'column': 2}, 'code': match.group(1), 'message': match.group(2), "
        "'url': None, 'fix': None, 'noqa_row': row})\n"
        "print(json.dumps(findings))\n"
        "raise SystemExit(bool(findings))\n",
        encoding="utf-8",
    )
    ruff.chmod(ruff.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", os.pathsep.join((str(bin_dir), os.environ.get("PATH", ""))))
    return ruff


def _hide_ruff_but_keep_git(monkeypatch) -> None:
    git = shutil.which("git")
    assert git
    monkeypatch.setenv("PATH", str(Path(git).parent))


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
    _init_repo(tmp_path, {"service.py": "print('before')\n"})
    _write(tmp_path / "service.py", "print('after')\n")
    _hide_ruff_but_keep_git(monkeypatch)

    exit_code, result = module.run_quality_checks(tmp_path, files=[Path("service.py")])

    assert exit_code == 0
    assert result.status == "advisory"
    assert result.files == ["service.py"]
    assert result.commands == []
    assert "ruff is not installed" in result.messages[0]


def test_missing_ruff_can_be_required(tmp_path, monkeypatch):
    module = _load_module()
    _init_repo(tmp_path, {"service.py": "print('before')\n"})
    _write(tmp_path / "service.py", "print('after')\n")
    _hide_ruff_but_keep_git(monkeypatch)

    exit_code, result = module.run_quality_checks(
        tmp_path,
        files=[Path("service.py")],
        require_ruff=True,
    )

    assert exit_code == 1
    assert result.status == "failed"


def test_historical_fingerprint_remains_non_blocking_when_line_moves(tmp_path, monkeypatch):
    module = _load_module()
    base_ref = _init_repo(tmp_path, {"service.py": "value = 1  # ruff: F401 unused import\n"})
    _write(tmp_path / "service.py", "\nvalue = 1  # ruff: F401 unused import\nprint('changed')\n")
    _install_fake_ruff(tmp_path, monkeypatch)

    exit_code, result = module.run_quality_checks(tmp_path, base_ref=base_ref, require_ruff=True)

    assert exit_code == 0
    assert result.status == "passed"
    assert result.ruff_version == "ruff 9.9.9"
    assert result.baseline_finding_count == 1
    assert result.current_finding_count == 1
    assert result.new_finding_count == 0
    assert result.new_findings == []


def test_new_fingerprint_fails_with_auditable_delta(tmp_path, monkeypatch):
    module = _load_module()
    base_ref = _init_repo(tmp_path, {"service.py": "value = 1  # ruff: F401 unused import\n"})
    _write(
        tmp_path / "service.py",
        "value = 1  # ruff: F401 unused import\nother = 2  # ruff: E711 comparison to None\n",
    )
    _install_fake_ruff(tmp_path, monkeypatch)

    exit_code, result = module.run_quality_checks(tmp_path, base_ref=base_ref, require_ruff=True)

    assert exit_code == 1
    assert result.status == "failed"
    assert result.baseline_finding_count == 1
    assert result.current_finding_count == 2
    assert result.new_finding_count == 1
    assert len(result.new_findings) == 1
    finding = result.new_findings[0]
    assert finding.fingerprint.startswith("sha256:")
    assert finding.path == "service.py"
    assert finding.code == "E711"
    assert finding.source == "other = 2 # ruff: E711 comparison to None"
    assert finding.baseline_count == 0
    assert finding.current_count == 1
    assert finding.new_count == 1


def test_duplicate_fingerprint_uses_occurrence_counts(tmp_path, monkeypatch):
    module = _load_module()
    line = "value = 1  # ruff: F401 unused import\n"
    base_ref = _init_repo(tmp_path, {"service.py": line})
    _write(tmp_path / "service.py", line + line)
    _install_fake_ruff(tmp_path, monkeypatch)

    exit_code, result = module.run_quality_checks(tmp_path, base_ref=base_ref, require_ruff=True)

    assert exit_code == 1
    assert result.new_finding_count == 1
    assert result.new_findings[0].baseline_count == 1
    assert result.new_findings[0].current_count == 2


def test_untracked_python_file_has_an_empty_baseline(tmp_path, monkeypatch):
    module = _load_module()
    base_ref = _init_repo(tmp_path)
    _write(tmp_path / "new_service.py", "value = 1  # ruff: F401 unused import\n")
    _install_fake_ruff(tmp_path, monkeypatch)

    exit_code, result = module.run_quality_checks(tmp_path, base_ref=base_ref, require_ruff=True)

    assert exit_code == 1
    assert result.files == ["new_service.py"]
    assert result.baseline_finding_count == 0
    assert result.new_finding_count == 1


def test_renamed_file_uses_original_path_content_as_baseline(tmp_path, monkeypatch):
    module = _load_module()
    base_ref = _init_repo(tmp_path, {"old_service.py": "value = 1  # ruff: F401 unused import\n"})
    _git(tmp_path, "mv", "old_service.py", "new_service.py")
    _install_fake_ruff(tmp_path, monkeypatch)

    exit_code, result = module.run_quality_checks(tmp_path, base_ref=base_ref, require_ruff=True)

    assert exit_code == 0
    assert result.files == ["new_service.py"]
    assert result.baseline_finding_count == 1
    assert result.current_finding_count == 1
    assert result.new_finding_count == 0


def test_invalid_base_ref_fails_instead_of_falling_back(tmp_path):
    module = _load_module()
    _init_repo(tmp_path, {"service.py": "print('ok')\n"})

    exit_code, result = module.run_quality_checks(tmp_path, base_ref="origin/missing", require_ruff=True)

    assert exit_code == 2
    assert result.status == "failed"
    assert "Cannot resolve Ruff baseline ref 'origin/missing'" in result.messages[0]


def test_ruff_execution_error_is_not_treated_as_a_lint_delta(tmp_path, monkeypatch):
    module = _load_module()
    base_ref = _init_repo(tmp_path, {"service.py": "print('before')\n"})
    _write(tmp_path / "service.py", "RUFF_CRASH = True\n")
    _install_fake_ruff(tmp_path, monkeypatch)

    exit_code, result = module.run_quality_checks(tmp_path, base_ref=base_ref, require_ruff=True)

    assert exit_code == 2
    assert result.status == "failed"
    assert result.messages == ["ruff check failed with exit code 2."]
    assert result.stderr == "synthetic ruff crash\n"


def test_main_emits_json_fingerprint_report(tmp_path, monkeypatch, capsys):
    module = _load_module()
    _init_repo(tmp_path, {"service.py": "print('before')\n"})
    _write(tmp_path / "service.py", "value = 1  # ruff: F401 unused import\n")
    _install_fake_ruff(tmp_path, monkeypatch)

    assert module.main(["--repo-root", str(tmp_path), "--require-ruff", "--json", "service.py"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
    assert payload["new_finding_count"] == 1
    assert payload["new_findings"][0]["code"] == "F401"
