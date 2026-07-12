import importlib.util
import json
import stat
import sys
from pathlib import Path


def _load_module():
    source = Path(__file__).resolve().parents[1] / "check_python_dependency_audit.py"
    spec = importlib.util.spec_from_file_location("check_python_dependency_audit_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _repo_with_targets(repo: Path) -> None:
    for relative in ("apps/api", "services/market-report-finder", "services/market-report-rules"):
        _write(repo / relative / "pyproject.toml", "[project]\nname = \"demo\"\nversion = \"0\"\n")
        _write(repo / relative / "uv.lock", "")


def _fake_uv(tmp_path: Path) -> tuple[Path, Path]:
    calls_path = tmp_path / "uv_calls.jsonl"
    fake = tmp_path / "uv"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        f"calls = pathlib.Path({str(calls_path)!r})\n"
        "with calls.open('a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "output = pathlib.Path(sys.argv[sys.argv.index('--output-file') + 1])\n"
        "output.write_text('starlette==1.3.1\\n', encoding='utf-8')\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return fake, calls_path


def _fake_pip_audit(
    tmp_path: Path,
    payload: dict,
    *,
    returncode: int = 0,
) -> tuple[Path, Path]:
    calls_path = tmp_path / "pip_audit_calls.jsonl"
    fake = tmp_path / "pip-audit"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        f"calls = pathlib.Path({str(calls_path)!r})\n"
        "with calls.open('a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        f"print(json.dumps({payload!r}))\n"
        f"raise SystemExit({returncode})\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return fake, calls_path


def test_dependency_audit_exports_prod_requirements_and_passes_clean_report(tmp_path):
    module = _load_module()
    _repo_with_targets(tmp_path)
    fake_uv, uv_calls = _fake_uv(tmp_path)
    fake_audit, audit_calls = _fake_pip_audit(tmp_path, {"dependencies": [], "fixes": []})
    output_dir = tmp_path / "artifacts"

    exit_code, result = module.run_dependency_audit(
        tmp_path,
        output_dir=output_dir,
        uv_executable=str(fake_uv),
        pip_audit_executable=str(fake_audit),
        require_pip_audit=True,
        block_all_vulnerabilities=True,
    )

    assert exit_code == 0
    assert result.status == "passed"
    assert [target.name for target in result.targets] == [
        "api",
        "market-report-finder",
        "market-report-rules",
    ]
    first_uv_call = json.loads(uv_calls.read_text(encoding="utf-8").splitlines()[0])
    assert "--no-dev" in first_uv_call
    assert "--no-emit-local" in first_uv_call
    first_audit_call = json.loads(audit_calls.read_text(encoding="utf-8").splitlines()[0])
    assert first_audit_call[:2] == ["-r", str(output_dir / "api-requirements.txt")]


def test_dependency_audit_blocks_vulnerability_findings(tmp_path):
    module = _load_module()
    _repo_with_targets(tmp_path)
    fake_uv, _uv_calls = _fake_uv(tmp_path)
    payload = {
        "dependencies": [
            {
                "name": "starlette",
                "version": "0.27.0",
                "vulns": [
                    {
                        "id": "PYSEC-1",
                        "aliases": ["CVE-1"],
                        "fix_versions": ["1.3.1"],
                        "description": "host header bypass",
                    }
                ],
            }
        ],
        "fixes": [],
    }
    fake_audit, _audit_calls = _fake_pip_audit(tmp_path, payload, returncode=1)

    exit_code, result = module.run_dependency_audit(
        tmp_path,
        targets=["api"],
        output_dir=tmp_path / "artifacts",
        uv_executable=str(fake_uv),
        pip_audit_executable=str(fake_audit),
        require_pip_audit=True,
        block_all_vulnerabilities=True,
    )

    assert exit_code == 1
    assert result.status == "failed"
    assert result.targets[0].blocking_vulnerability_count == 1
    vuln = result.targets[0].vulnerabilities[0]
    assert vuln.package == "starlette"
    assert vuln.vulnerability_id == "PYSEC-1"
    assert vuln.fix_versions == ["1.3.1"]


def test_dependency_audit_can_block_high_severity_only(tmp_path):
    module = _load_module()
    _repo_with_targets(tmp_path)
    fake_uv, _uv_calls = _fake_uv(tmp_path)
    payload = {
        "dependencies": [
            {"name": "pkg-low", "version": "1", "vulns": [{"id": "LOW", "severity": "low"}]},
            {"name": "pkg-high", "version": "1", "vulns": [{"id": "HIGH", "severity": "HIGH"}]},
        ],
    }
    fake_audit, _audit_calls = _fake_pip_audit(tmp_path, payload, returncode=1)

    exit_code, result = module.run_dependency_audit(
        tmp_path,
        targets=["api"],
        output_dir=tmp_path / "artifacts",
        uv_executable=str(fake_uv),
        pip_audit_executable=str(fake_audit),
        require_pip_audit=True,
        block_all_vulnerabilities=False,
    )

    assert exit_code == 1
    assert result.targets[0].vulnerability_count == 2
    assert result.targets[0].blocking_vulnerability_count == 1


def test_missing_pip_audit_is_failed_when_required(tmp_path):
    module = _load_module()
    _repo_with_targets(tmp_path)

    exit_code, result = module.run_dependency_audit(
        tmp_path,
        pip_audit_executable=str(tmp_path / "missing-pip-audit"),
        require_pip_audit=True,
    )

    assert exit_code == 1
    assert result.status == "failed"
    assert "pip-audit executable not found" in result.messages[0]


def test_unknown_target_fails_as_config_error(tmp_path, capsys):
    module = _load_module()

    exit_code = module.main(["--repo-root", str(tmp_path), "--target", "missing", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["status"] == "failed"
    assert payload["messages"] == ["unknown Python dependency audit target(s): missing"]
