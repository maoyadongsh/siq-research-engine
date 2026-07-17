from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.openshell import build_tracked_artifact_manifest as builder  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / "build_tracked_artifact_manifest.py"
LOG_FIXTURE_ROOT = REPO_ROOT / "artifacts/openshell/v0.6/logs-20260716-final"


def _write(path: Path, content: str = "safe evidence\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    return project


def _copy_log_pair(project: Path) -> tuple[Path, Path]:
    root = project / "artifacts/openshell/v0.6/log-contract"
    root.mkdir(parents=True)
    json_path = root / "logs.sanitized.json"
    markdown_path = root / "logs.sanitized.md"
    json_path.write_bytes((LOG_FIXTURE_ROOT / json_path.name).read_bytes())
    markdown_path.write_bytes((LOG_FIXTURE_ROOT / markdown_path.name).read_bytes())
    return json_path, markdown_path


def _run(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(project), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def test_cli_builds_sorted_deterministic_private_manifest_and_refreshes(tmp_path: Path) -> None:
    project = _project(tmp_path)
    readiness = _write(project / "artifacts/openshell/v0.6/readiness.json", '{"decision":"NO_GO"}\n')
    readme = _write(project / "artifacts/openshell/README.md")
    output = project / "artifacts/openshell/tracked-artifacts.json"

    completed = _run(
        project,
        "--artifact",
        "readiness=artifacts/openshell/v0.6/readiness.json",
        "--artifact",
        "public_document=artifacts/openshell/README.md",
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert set(payload) == {"schema_version", "artifacts"}
    assert payload["schema_version"] == "siq.openshell.tracked-artifacts.v1"
    assert "generated_at" not in output.read_text(encoding="utf-8")
    assert [entry["path"] for entry in payload["artifacts"]] == sorted(
        ["artifacts/openshell/v0.6/readiness.json", "artifacts/openshell/README.md"]
    )
    for entry in payload["artifacts"]:
        assert set(entry) == {"path", "classification", "sha256", "size_bytes"}
        source = project / entry["path"]
        assert entry["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
        assert entry["size_bytes"] == source.stat().st_size
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    original = output.read_bytes()

    assert _run(project, "--refresh").returncode == 0
    assert output.read_bytes() == original

    readiness.write_text('{"decision":"GO"}\n', encoding="utf-8")
    assert _run(project, "--refresh").returncode == 0
    refreshed = json.loads(output.read_text(encoding="utf-8"))
    entry = next(item for item in refreshed["artifacts"] if item["classification"] == "readiness")
    assert entry["sha256"] == hashlib.sha256(readiness.read_bytes()).hexdigest()
    assert readme.is_file()


@pytest.mark.parametrize(
    ("relative", "classification"),
    [
        ("var/openshell/manifests/toolchain.sanitized.json", "sanitized_manifest"),
        ("artifacts/openshell/v0.6/proof.sanitized.json", "sanitized_evidence"),
        ("artifacts/openshell/v0.6/logs-20260716/logs.sanitized.json", "sanitized_log"),
        ("artifacts/openshell/v0.6/baseline.md", "baseline"),
        ("var/openshell/README.md", "public_document"),
    ],
)
def test_all_supported_path_classifications_are_accepted(
    tmp_path: Path,
    relative: str,
    classification: str,
) -> None:
    project = _project(tmp_path)
    source = _write(project / relative)

    payload, _ = builder.build_manifest(
        project_root=project,
        output=Path("artifacts/openshell/tracked-artifacts.json"),
        artifacts=[(classification, source)],
    )

    assert payload["artifacts"][0]["classification"] == classification


def test_scan_failure_prevents_manifest_write(tmp_path: Path) -> None:
    project = _project(tmp_path)
    source = _write(
        project / "artifacts/openshell/v0.6/unsafe.sanitized.json",
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz\n",
    )
    output = project / "artifacts/openshell/tracked-artifacts.json"

    completed = _run(project, "--artifact", f"sanitized_evidence={source}")

    assert completed.returncode == 2
    assert "artifact_sanitization_failed" in completed.stderr
    assert not output.exists()


def test_nonregular_file_is_rejected_before_open(tmp_path: Path) -> None:
    project = _project(tmp_path)
    fifo = project / "artifacts/openshell/v0.6/pipe.sanitized.json"
    fifo.parent.mkdir(parents=True)
    os.mkfifo(fifo)

    with pytest.raises(builder.TrackedArtifactManifestError, match="artifact_not_regular_file"):
        builder.build_manifest(
            project_root=project,
            output=Path("artifacts/openshell/tracked-artifacts.json"),
            artifacts=[("sanitized_evidence", fifo)],
        )


def test_rejects_outside_symlink_hardlink_duplicate_and_manifest_self(tmp_path: Path) -> None:
    project = _project(tmp_path)
    source = _write(project / "artifacts/openshell/v0.6/proof.sanitized.json", "{}\n")
    outside = _write(tmp_path / "outside.sanitized.json", "{}\n")
    symlink = project / "artifacts/openshell/v0.6/link.sanitized.json"
    symlink.symlink_to(source)
    hardlink = project / "artifacts/openshell/v0.6/hard.sanitized.json"
    os.link(source, hardlink)
    output = project / "artifacts/openshell/tracked-artifacts.json"

    cases = [
        ([("sanitized_evidence", outside)], "artifact_outside_project"),
        ([("sanitized_evidence", symlink)], "symlink_not_allowed"),
        ([("sanitized_evidence", source)], "artifact_hardlink_not_allowed"),
        (
            [("sanitized_evidence", hardlink), ("sanitized_evidence", hardlink)],
            "artifact_hardlink_not_allowed",
        ),
    ]
    for artifacts, error in cases:
        with pytest.raises(builder.TrackedArtifactManifestError, match=error):
            builder.build_manifest(project_root=project, output=output, artifacts=artifacts)

    hardlink.unlink()
    with pytest.raises(builder.TrackedArtifactManifestError, match="artifact_duplicate"):
        builder.build_manifest(
            project_root=project,
            output=output,
            artifacts=[("sanitized_evidence", source), ("sanitized_evidence", source)],
        )
    _write(output, '{"schema_version":"siq.openshell.tracked-artifacts.v1","artifacts":[]}\n')
    with pytest.raises(builder.TrackedArtifactManifestError, match="manifest_cannot_include_itself"):
        builder.build_manifest(
            project_root=project,
            output=output,
            artifacts=[("sanitized_evidence", output)],
        )


@pytest.mark.parametrize(
    ("relative", "classification", "error"),
    [
        ("var/openshell/raw.log", "sanitized_log", "artifact_path_not_trackable"),
        (
            "var/openshell/manifests/nested/proof.sanitized.json",
            "sanitized_manifest",
            "artifact_path_not_trackable",
        ),
        ("artifacts/openshell/v0.6/raw.json", "sanitized_evidence", "artifact_path_not_trackable"),
        (
            "artifacts/openshell/v0.6/proof.sanitized.json",
            "sanitized_log",
            "artifact_classification_mismatch",
        ),
    ],
)
def test_path_policy_and_classification_fail_closed(
    tmp_path: Path,
    relative: str,
    classification: str,
    error: str,
) -> None:
    project = _project(tmp_path)
    source = _write(project / relative, "{}\n")

    with pytest.raises(builder.TrackedArtifactManifestError, match=error):
        builder.build_manifest(
            project_root=project,
            output=Path("artifacts/openshell/tracked-artifacts.json"),
            artifacts=[(classification, source)],
        )


def test_refresh_rejects_extra_fields_and_duplicate_paths(tmp_path: Path) -> None:
    project = _project(tmp_path)
    source = _write(project / "artifacts/openshell/v0.6/proof.sanitized.json", "{}\n")
    output = project / "artifacts/openshell/tracked-artifacts.json"
    assert _run(project, "--artifact", f"sanitized_evidence={source}").returncode == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["generated_at"] = "not deterministic"
    output.write_text(json.dumps(payload), encoding="utf-8")

    extra = _run(project, "--refresh")

    assert extra.returncode == 2
    assert "refresh_manifest_fields_invalid" in extra.stderr
    payload.pop("generated_at")
    payload["artifacts"].append(dict(payload["artifacts"][0]))
    output.write_text(json.dumps(payload), encoding="utf-8")

    duplicate = _run(project, "--refresh")

    assert duplicate.returncode == 2
    assert "artifact_duplicate" in duplicate.stderr


def test_atomic_writer_detects_mutation_during_sanitized_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    source = _write(project / "artifacts/openshell/v0.6/proof.sanitized.json", "{}\n")
    output = project / "artifacts/openshell/tracked-artifacts.json"

    def mutate(_paths: object) -> list[object]:
        source.write_text('{"changed":true}\n', encoding="utf-8")
        return []

    monkeypatch.setattr(builder.check_sanitized_artifacts, "scan_paths", mutate)

    with pytest.raises(builder.TrackedArtifactManifestError, match="artifact_changed_during_scan"):
        builder.write_manifest(
            project_root=project,
            output=output,
            artifacts=[("sanitized_evidence", source)],
        )
    assert not output.exists()


def test_manifest_rejects_free_body_in_log_bundle_and_markdown_tampering(tmp_path: Path) -> None:
    project = _project(tmp_path)
    json_path, markdown_path = _copy_log_pair(project)
    original_json = json_path.read_bytes()
    original_markdown = markdown_path.read_bytes()
    artifacts = [("sanitized_log", json_path), ("sanitized_log", markdown_path)]

    payload = json.loads(original_json)
    payload["free_body"] = "private internal acquisition plans"
    json_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="ascii")
    with pytest.raises(builder.TrackedArtifactManifestError, match="bundle_fields_invalid"):
        builder.write_manifest(
            project_root=project,
            output=Path("artifacts/openshell/tracked-artifacts.json"),
            artifacts=artifacts,
        )

    json_path.write_bytes(original_json)
    markdown_path.write_bytes(original_markdown + b"private appended line\n")
    with pytest.raises(builder.TrackedArtifactManifestError, match="markdown_mismatch"):
        builder.write_manifest(
            project_root=project,
            output=Path("artifacts/openshell/tracked-artifacts.json"),
            artifacts=artifacts,
        )


def test_manifest_requires_both_deterministic_log_bundle_files(tmp_path: Path) -> None:
    project = _project(tmp_path)
    json_path, _ = _copy_log_pair(project)

    with pytest.raises(builder.TrackedArtifactManifestError, match="sanitized_log_pair_missing"):
        builder.write_manifest(
            project_root=project,
            output=Path("artifacts/openshell/tracked-artifacts.json"),
            artifacts=[("sanitized_log", json_path)],
        )
