import importlib.util
import json
import subprocess
import sys
from pathlib import Path


def _load_module():
    source = Path(__file__).resolve().parents[1] / "check_large_file_changes.py"
    spec = importlib.util.spec_from_file_location("check_large_file_changes_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_bytes(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def test_changed_file_gate_blocks_runtime_artifacts_and_allows_source_markers(tmp_path):
    module = _load_module()
    _write_bytes(tmp_path / "data" / "raw.mp4", 10)
    _write_bytes(tmp_path / "artifacts" / "report.zip", 10)
    _write_bytes(tmp_path / "var" / "jobs.json", 10)
    _write_bytes(tmp_path / "data" / "README.md", 10)
    _write_bytes(tmp_path / "artifacts" / "nested" / ".gitkeep", 0)

    findings = module.check_large_file_changes(
        tmp_path,
        paths=[
            "data/raw.mp4",
            "artifacts/report.zip",
            "var/jobs.json",
            "data/README.md",
            "artifacts/nested/.gitkeep",
        ],
        max_bytes=1,
    )

    assert [(finding.code, finding.path) for finding in findings] == [
        ("tracked_runtime_artifact_changed", "data/raw.mp4"),
        ("tracked_runtime_artifact_changed", "artifacts/report.zip"),
        ("tracked_runtime_artifact_changed", "var/jobs.json"),
    ]


def test_changed_file_gate_detects_force_tracked_ignored_runtime_artifact(tmp_path):
    module = _load_module()
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "ci@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "CI"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("data/**\nartifacts/**\nvar/**\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=tmp_path, check=True)
    _write_bytes(tmp_path / "data" / "wiki" / "package.json", 10)
    subprocess.run(["git", "add", "-f", "data/wiki/package.json"], cwd=tmp_path, check=True)

    findings = module.check_large_file_changes(tmp_path)

    assert [(finding.code, finding.path) for finding in findings] == [
        ("tracked_runtime_artifact_changed", "data/wiki/package.json"),
    ]


def test_changed_file_gate_blocks_media_archives_and_local_review_artifacts(tmp_path):
    module = _load_module()
    _write_bytes(tmp_path / "apps" / "web" / "public" / "demo.mp4", 10)
    _write_bytes(tmp_path / ".superpowers" / "sdd" / "review.diff", 10)

    findings = module.check_large_file_changes(
        tmp_path,
        paths=["apps/web/public/demo.mp4", ".superpowers/sdd/review.diff"],
    )

    assert [(finding.code, finding.path) for finding in findings] == [
        ("blocked_binary_artifact_changed", "apps/web/public/demo.mp4"),
        ("local_review_artifact_changed", ".superpowers/sdd/review.diff"),
    ]


def test_changed_file_gate_blocks_database_dump_and_backup_suffixes(tmp_path):
    module = _load_module()
    paths = ["db/snapshots/market.dump", "db/snapshots/market.backup", "db/snapshots/market.bak"]
    for path in paths:
        _write_bytes(tmp_path / path, 10)

    findings = module.check_large_file_changes(tmp_path, paths=paths)

    assert [(finding.code, finding.path) for finding in findings] == [
        ("blocked_binary_artifact_changed", path) for path in paths
    ]


def test_changed_file_gate_blocks_large_image_and_large_source_file(tmp_path):
    module = _load_module()
    _write_bytes(tmp_path / "apps" / "web" / "public" / "hero.webp", 11)
    _write_bytes(tmp_path / "apps" / "api" / "generated.json", 21)
    _write_bytes(tmp_path / "apps" / "api" / "small.py", 5)

    findings = module.check_large_file_changes(
        tmp_path,
        paths=[
            "apps/web/public/hero.webp",
            "apps/api/generated.json",
            "apps/api/small.py",
        ],
        max_bytes=20,
        image_max_bytes=10,
    )

    assert [(finding.code, finding.path, finding.size_bytes) for finding in findings] == [
        ("large_image_artifact_changed", "apps/web/public/hero.webp", 11),
        ("large_file_changed", "apps/api/generated.json", 21),
    ]


def test_main_reports_json_and_nonzero_for_findings(tmp_path, capsys):
    module = _load_module()
    _write_bytes(tmp_path / "apps" / "web" / "public" / "demo.mp4", 10)

    exit_code = module.main(
        [
            "--repo-root",
            str(tmp_path),
            "--path",
            "apps/web/public/demo.mp4",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["passed"] is False
    assert payload["findings"][0]["code"] == "blocked_binary_artifact_changed"


def test_main_returns_zero_without_findings(tmp_path, capsys):
    module = _load_module()
    _write_bytes(tmp_path / "apps" / "api" / "small.py", 5)

    exit_code = module.main(
        [
            "--repo-root",
            str(tmp_path),
            "--path",
            "apps/api/small.py",
            "--max-bytes",
            "10",
        ]
    )

    assert exit_code == 0
    assert "PASS large-file changed-file gate" in capsys.readouterr().out
