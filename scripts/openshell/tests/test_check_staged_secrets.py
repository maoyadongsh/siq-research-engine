from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "check_staged_secrets.py"


def _module():
    spec = importlib.util.spec_from_file_location("siq_check_staged_secrets_under_test", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "ci@example.invalid")
    _git(root, "config", "user.name", "CI")
    return root


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _fake_gitleaks(tmp_path: Path) -> Path:
    executable = tmp_path / "bin" / "gitleaks"
    executable.parent.mkdir()
    executable.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys

if sys.argv[1:] == ["version"]:
    print("v8.24.2")
    raise SystemExit(0)
if sys.argv[1] != "detect":
    raise SystemExit(9)
config = next((item.split("=", 1)[1] for item in sys.argv if item.startswith("--config=")), None)
ignore = next((item.split("=", 1)[1] for item in sys.argv if item.startswith("--gitleaks-ignore-path=")), None)
if config is None or pathlib.Path(config).read_bytes() != b"[extend]\\nuseDefault = true\\n":
    raise SystemExit(9)
if ignore is None or pathlib.Path(ignore).read_bytes() != b"":
    raise SystemExit(9)
content = b"".join(
    path.read_bytes()
    for path in pathlib.Path(".").rglob("*")
    if path.is_file() and not path.is_symlink()
)
if b"SECRET_MARKER_DO_NOT_PRINT" in content:
    print("SECRET_MARKER_DO_NOT_PRINT", file=sys.stderr)
    raise SystemExit(1)
raise SystemExit(0)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _run(root: Path, fake: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{fake.parent}:{environment['PATH']}"
    return subprocess.run(
        [sys.executable, str(SOURCE), "--repo-root", str(root), "--scanner", "local"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def test_scans_staged_blob_and_ignores_dirty_worktree(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    source = root / "safe.txt"
    _write(source, "staged safe content\n")
    _git(root, "add", "safe.txt")
    _write(source, "SECRET_MARKER_DO_NOT_PRINT\n")

    completed = _run(root, _fake_gitleaks(tmp_path))

    assert completed.returncode == 0, completed.stderr
    assert "staged_secret_scan=passed" in completed.stdout


def test_detected_secret_is_nonzero_and_scanner_output_is_suppressed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _write(root / "unsafe.txt", "SECRET_MARKER_DO_NOT_PRINT\n")
    _git(root, "add", "unsafe.txt")

    completed = _run(root, _fake_gitleaks(tmp_path))

    assert completed.returncode == 1
    assert "potential_secret_detected" in completed.stderr
    assert "scanner_output=suppressed" in completed.stderr
    assert "SECRET_MARKER_DO_NOT_PRINT" not in completed.stdout
    assert "SECRET_MARKER_DO_NOT_PRINT" not in completed.stderr


def test_symlink_is_scanned_as_index_blob_without_following_target(tmp_path: Path) -> None:
    module = _module()
    root = _repo(tmp_path)
    outside = tmp_path / "outside"
    _write(outside, "SECRET_MARKER_DO_NOT_PRINT\n")
    link = root / "outside-link"
    link.symlink_to(outside)
    _git(root, "add", "outside-link")
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir(mode=0o700)

    stats = module.materialize_index(root, snapshot)

    materialized = snapshot / "outside-link"
    assert stats.blob_entries == 1
    assert materialized.is_file()
    assert not materialized.is_symlink()
    assert materialized.read_text(encoding="utf-8") == str(outside)


def test_local_scanner_requires_exact_pinned_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    executable = _fake_gitleaks(tmp_path)
    monkeypatch.setattr(module.shutil, "which", lambda name: str(executable) if name == "gitleaks" else None)
    executable.write_text("#!/bin/sh\necho v8.25.0\n", encoding="utf-8")
    executable.chmod(0o755)

    with pytest.raises(module.StagedSecretScanError, match="pinned_gitleaks_unavailable"):
        module.select_scanner("local")


def test_missing_scanner_is_explicit_nonzero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    root = _repo(tmp_path)
    monkeypatch.setattr(module, "select_scanner", lambda _preference: (_ for _ in ()).throw(
        module.StagedSecretScanError("pinned_gitleaks_unavailable")
    ))

    result = module.main(["--repo-root", str(root), "--scanner", "auto"])

    assert result == 2


def test_auto_prefers_matching_local_scanner(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    local = module.Scanner("local", "/local/gitleaks")
    docker_called = False

    monkeypatch.setattr(module, "_local_scanner", lambda: local)

    def docker():
        nonlocal docker_called
        docker_called = True
        return module.Scanner("docker", "/usr/bin/docker")

    monkeypatch.setattr(module, "_docker_scanner", docker)

    assert module.select_scanner("auto") == local
    assert not docker_called


def test_scan_environment_removes_all_gitleaks_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    monkeypatch.setenv("GITLEAKS_CONFIG", "/untrusted/config")
    monkeypatch.setenv("GITLEAKS_CONFIG_TOML", "untrusted")
    monkeypatch.setenv("GITLEAKS_CONFIG_TOML_BASE64", "dW50cnVzdGVk")

    environment = module._scan_environment()

    assert not any(key.startswith("GITLEAKS_") for key in environment)


def test_auto_falls_back_to_docker_when_local_probe_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    docker = module.Scanner("docker", "/usr/bin/docker")
    monkeypatch.setattr(
        module,
        "_local_scanner",
        lambda: (_ for _ in ()).throw(module.StagedSecretScanError("scanner_execution_failed")),
    )
    monkeypatch.setattr(module, "_docker_scanner", lambda: docker)

    assert module.select_scanner("auto") == docker


def test_docker_command_is_pinned_read_only_and_networkless(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    captured: list[str] = []

    def run(command, **_kwargs):
        captured.extend(command)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(module, "_run", run)

    assert module.scan_snapshot(module.Scanner("docker", "/usr/bin/docker"), tmp_path) == 0
    assert module.GITLEAKS_IMAGE in captured
    assert "--network" in captured and "none" in captured
    assert f"type=bind,src={tmp_path},dst=/repo,readonly" in captured
    assert "--config=/siq-gitleaks-config/gitleaks.toml" in captured
    assert "--gitleaks-ignore-path=/siq-gitleaks-config/gitleaksignore" in captured
    assert "--redact=100" in captured
