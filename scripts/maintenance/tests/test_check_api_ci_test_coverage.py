import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    source = Path(__file__).resolve().parents[1] / "check_api_ci_test_coverage.py"
    spec = importlib.util.spec_from_file_location("check_api_ci_test_coverage_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_audit_lists_uncovered_non_slow_network_api_tests(tmp_path):
    module = _load_module()
    _write(tmp_path / "apps/api/tests/test_covered.py", "def test_ok():\n    pass\n")
    _write(tmp_path / "apps/api/tests/test_uncovered.py", "def test_missing():\n    pass\n")
    _write(
        tmp_path / "apps/api/tests/test_slow_only.py",
        "import pytest\n\npytestmark = pytest.mark.slow\n\ndef test_expensive():\n    pass\n",
    )
    _write(
        tmp_path / ".github/workflows/ci.yml",
        """
jobs:
  api-focused:
    steps:
      - name: Run API focused tests
        working-directory: apps/api
        run: |
          uv run python -m pytest \
            tests/test_covered.py \
            -q
""",
    )

    exit_code, result = module.audit_api_ci_test_coverage(tmp_path)

    assert exit_code == 0
    assert result.status == "advisory"
    assert result.covered_files == ["apps/api/tests/test_covered.py"]
    assert result.excluded_slow_network_files == ["apps/api/tests/test_slow_only.py"]
    assert result.uncovered_files == ["apps/api/tests/test_uncovered.py"]


def test_fail_on_uncovered_returns_nonzero(tmp_path):
    module = _load_module()
    _write(tmp_path / "apps/api/tests/test_uncovered.py", "def test_missing():\n    pass\n")
    _write(tmp_path / ".github/workflows/ci.yml", "jobs: {}\n")

    exit_code, result = module.audit_api_ci_test_coverage(tmp_path, fail_on_uncovered=True)

    assert exit_code == 1
    assert result.status == "failed"
    assert result.uncovered_files == ["apps/api/tests/test_uncovered.py"]


def test_main_emits_machine_auditable_json(tmp_path, capsys):
    module = _load_module()
    _write(tmp_path / "apps/api/tests/test_uncovered.py", "def test_missing():\n    pass\n")
    _write(tmp_path / ".github/workflows/ci.yml", "jobs: {}\n")

    assert module.main(["--repo-root", str(tmp_path), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "advisory"
    assert payload["uncovered_files"] == ["apps/api/tests/test_uncovered.py"]
    assert "API CI execution audit" in payload["messages"][0]
