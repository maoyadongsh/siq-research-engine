from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "build_immutable_path_registry.py"
SHA = "a" * 64


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    company = project / "data" / "wiki" / "companies" / "600001-Test"
    _write_json(
        company / "company.json",
        {
            "company_id": "CN:600001",
            "reports": [{"report_id": "2025-annual", "status": "ready", "task_id": "task-1"}],
        },
    )
    _write_json(
        company / "reports" / "2025-annual" / "artifact_manifest.json",
        {
            "task_id": "task-1",
            "core": {"status": "ready", "ready": True, "bundle_sha256": SHA},
            "artifacts": {"report.md": {"exists": True, "sha256": SHA}},
        },
    )
    return project


def _run(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(project), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def test_dry_run_prints_registry_without_writing(tmp_path: Path) -> None:
    project = _project(tmp_path)

    completed = _run(project, "--dry-run")

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["summary"]["entry_count"] == 1
    assert not (project / "var").exists()


def test_write_check_and_diff_do_not_mutate_existing_output(tmp_path: Path) -> None:
    project = _project(tmp_path)
    written = _run(project)
    output = project / "var" / "openshell" / "registry" / "immutable-paths.json"
    original = output.read_bytes()

    assert written.returncode == 0
    assert _run(project, "--check").returncode == 0

    company_manifest = project / "data" / "wiki" / "companies" / "600001-Test" / "company.json"
    payload = json.loads(company_manifest.read_text(encoding="utf-8"))
    payload["reports"][0]["status"] = "staging"
    _write_json(company_manifest, payload)

    checked = _run(project, "--check", "--diff")

    assert checked.returncode == 1
    assert "--- immutable-paths.current" in checked.stdout
    assert "+++ immutable-paths.generated" in checked.stdout
    assert output.read_bytes() == original


def test_output_outside_project_is_rejected(tmp_path: Path) -> None:
    project = _project(tmp_path)
    escaped = tmp_path / "escaped.json"

    completed = _run(project, "--output", "../escaped.json")

    assert completed.returncode == 2
    assert "outside the project root" in completed.stderr
    assert not escaped.exists()
