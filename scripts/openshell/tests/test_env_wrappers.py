from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ENV = ROOT / "scripts" / "openshell" / "env.sh"
RUN = ROOT / "scripts" / "openshell" / "run_cli.sh"


def _isolated_wrapper(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    scripts = project / "scripts" / "openshell"
    scripts.mkdir(parents=True)
    shutil.copy2(ENV, scripts / "env.sh")
    shutil.copy2(RUN, scripts / "run_cli.sh")

    binary = project / "var" / "openshell" / "toolchains" / "v0.0.83" / "bin" / "openshell"
    binary.parent.mkdir(parents=True)
    (project / "var" / "openshell").chmod(0o700)
    binary.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*"\n', encoding="utf-8")
    binary.chmod(0o700)
    return scripts / "run_cli.sh", project / "var" / "openshell" / "locks" / "maintenance.lock"


def test_env_wrapper_points_all_state_to_project_and_uses_new_gateway() -> None:
    empty_gateway_env = dict(os.environ)
    empty_gateway_env["OPENSHELL_GATEWAY"] = ""
    completed = subprocess.run(
        ["bash", str(ENV)],
        check=False,
        capture_output=True,
        text=True,
        env=empty_gateway_env,
    )
    assert completed.returncode == 2

    clean_env = dict(os.environ)
    clean_env.pop("OPENSHELL_GATEWAY", None)
    clean_env["XDG_CONFIG_HOME"] = "/tmp/must-not-leak"
    completed = subprocess.run(["bash", str(ENV)], check=True, capture_output=True, text=True, env=clean_env)
    values = dict(line.split("=", 1) for line in completed.stdout.splitlines())
    assert values["SIQ_PROJECT_ROOT"] == str(ROOT)
    assert values["OPENSHELL_GATEWAY"] == "siq-openshell-dev"
    assert values["XDG_CONFIG_HOME"].startswith(str(ROOT / "var" / "openshell"))
    assert values["XDG_STATE_HOME"].startswith(str(ROOT / "var" / "openshell"))
    assert values["SIQ_OPENSHELL_BIN"].startswith(str(ROOT / "var" / "openshell"))


def test_run_cli_fails_closed_without_project_binary_and_never_falls_back(tmp_path: Path) -> None:
    isolated_scripts = tmp_path / "project" / "scripts" / "openshell"
    isolated_scripts.mkdir(parents=True)
    shutil.copy2(ENV, isolated_scripts / "env.sh")
    shutil.copy2(RUN, isolated_scripts / "run_cli.sh")

    clean_env = dict(os.environ)
    clean_env.pop("OPENSHELL_GATEWAY", None)
    completed = subprocess.run(
        ["bash", str(isolated_scripts / "run_cli.sh"), "doctor", "check"],
        check=False,
        capture_output=True,
        text=True,
        env=clean_env,
    )

    assert completed.returncode == 2
    assert "Project-local OpenShell binary not installed" in completed.stderr


def test_run_cli_rejects_legacy_gateway_argument_before_binary_lookup() -> None:
    clean_env = dict(os.environ)
    clean_env.pop("OPENSHELL_GATEWAY", None)
    completed = subprocess.run(
        ["bash", str(RUN), "gateway", "nemoclaw"],
        check=False,
        capture_output=True,
        text=True,
        env=clean_env,
    )

    assert completed.returncode == 2
    assert "legacy nemoclaw" in completed.stderr


def test_run_cli_rejects_global_gateway_selectors_before_binary_lookup() -> None:
    clean_env = dict(os.environ)
    clean_env.pop("OPENSHELL_GATEWAY", None)
    for arguments in (
        ("--gateway-endpoint", "https://127.0.0.1:18789", "doctor", "check"),
        ("--gateway-endpoint=https://127.0.0.1:18789", "doctor", "check"),
        ("-g", "nemoclaw", "doctor", "check"),
    ):
        completed = subprocess.run(
            ["bash", str(RUN), *arguments],
            check=False,
            capture_output=True,
            text=True,
            env=clean_env,
        )
        assert completed.returncode == 2
        assert "gateway" in completed.stderr.lower()


def test_run_cli_locks_sandbox_mutations_with_root_flags_and_aliases(tmp_path: Path) -> None:
    wrapper, lock_path = _isolated_wrapper(tmp_path)
    lock_path.parent.mkdir(mode=0o700)
    clean_env = {key: value for key, value in os.environ.items() if key != "OPENSHELL_GATEWAY"}

    with lock_path.open("a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        for arguments in (
            ("-v", "sandbox", "create", "--help"),
            ("--gateway-insecure", "sandbox", "delete", "poc"),
            ("sb", "create", "--help"),
            ("-vv", "sb", "delete", "poc"),
        ):
            completed = subprocess.run(
                ["bash", str(wrapper), *arguments],
                check=False,
                capture_output=True,
                text=True,
                env=clean_env,
            )
            assert completed.returncode == 75, arguments
            assert "lifecycle operation is in progress" in completed.stderr


def test_run_cli_does_not_lock_non_mutating_sandbox_commands(tmp_path: Path) -> None:
    wrapper, lock_path = _isolated_wrapper(tmp_path)
    lock_path.parent.mkdir(mode=0o700)
    clean_env = {key: value for key, value in os.environ.items() if key != "OPENSHELL_GATEWAY"}

    with lock_path.open("a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        completed = subprocess.run(
            ["bash", str(wrapper), "-v", "sb", "list"],
            check=False,
            capture_output=True,
            text=True,
            env=clean_env,
        )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "-v sb list"
