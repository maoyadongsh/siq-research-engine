from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ENV = ROOT / "scripts" / "openshell" / "env.sh"


def _run_lock_script(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    project = tmp_path / "project"
    (project / "scripts" / "openshell").mkdir(parents=True)
    (project / "var" / "openshell").mkdir(parents=True)
    (project / "var" / "openshell").chmod(0o700)
    copied_env = project / "scripts" / "openshell" / "env.sh"
    copied_env.write_text(ENV.read_text(encoding="utf-8"), encoding="utf-8")
    return subprocess.run(
        ["bash", "-c", body, "bash", str(copied_env)],
        check=False,
        capture_output=True,
        text=True,
        env={key: value for key, value in os.environ.items() if key != "OPENSHELL_GATEWAY"},
    )


def test_maintenance_lock_is_reentrant_for_inherited_fd(tmp_path: Path) -> None:
    completed = _run_lock_script(
        tmp_path,
        'OPENSHELL_GATEWAY=siq-openshell-dev; export OPENSHELL_GATEWAY; source "$1"; siq_openshell_acquire_maintenance_lock; '
        'before="$SIQ_OPENSHELL_MAINTENANCE_FD"; '
        'source "$1"; siq_openshell_acquire_maintenance_lock; '
        'test "$before" = "$SIQ_OPENSHELL_MAINTENANCE_FD"',
    )
    assert completed.returncode == 0, completed.stderr


def test_maintenance_lock_rejects_a_competing_process(tmp_path: Path) -> None:
    completed = _run_lock_script(
        tmp_path,
        'OPENSHELL_GATEWAY=siq-openshell-dev; export OPENSHELL_GATEWAY; source "$1"; siq_openshell_acquire_maintenance_lock; '
        '(siq_openshell_close_maintenance_lock_copy; OPENSHELL_GATEWAY=siq-openshell-dev; export OPENSHELL_GATEWAY; source "$1"; '
        "siq_openshell_acquire_maintenance_lock) && exit 1; "
        'test "$?" -eq 75',
    )
    assert completed.returncode == 0, completed.stderr


def test_long_running_entrypoints_close_inherited_lock_copies() -> None:
    start_gateway = (ROOT / "scripts" / "openshell" / "start_gateway.sh").read_text(encoding="utf-8")
    start_poc = (ROOT / "scripts" / "openshell" / "start_hermes_poc.sh").read_text(encoding="utf-8")
    assert "siq_openshell_close_maintenance_lock_copy" in start_gateway
    assert "siq_openshell_close_maintenance_lock_copy" in start_poc


def test_lock_directory_is_checked_before_and_after_creation() -> None:
    script = ENV.read_text(encoding="utf-8")
    function = script.split("siq_openshell_acquire_maintenance_lock() {", 1)[1].split("\n}\n", 1)[0]
    checks = [
        index
        for index in range(len(function))
        if function.startswith('siq_openshell_assert_state_path "$lock_dir"', index)
    ]
    install = function.index('install -d -m 700 -- "$lock_dir"')

    assert len(checks) == 2
    assert checks[0] < install < checks[1]
