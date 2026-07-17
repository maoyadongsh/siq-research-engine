from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from scripts.openshell import switch_siq_analysis_runtime as runtime_switch


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    return root


def test_switch_is_atomic_private_and_status_reads_runtime_file(tmp_path: Path) -> None:
    root = _project(tmp_path)

    opened = runtime_switch.switch_runtime(root, target="openshell", session_mode="all")
    state = root / opened["state"]

    assert opened["target"] == "openshell"
    assert opened["unmatched_scope"] == "host"
    assert stat.S_IMODE(state.stat().st_mode) == 0o600
    assert stat.S_IMODE(state.parent.stat().st_mode) == 0o700
    assert json.loads(state.read_text(encoding="ascii"))["session_mode"] == "all"
    assert runtime_switch.runtime_status(root)["source"] == "runtime_file"

    rolled_back = runtime_switch.switch_runtime(root, target="host", session_mode="allowlist")
    assert rolled_back["target"] == "host"
    assert runtime_switch.runtime_status(root)["target"] == "host"


def test_switch_rejects_symlinked_state_file(tmp_path: Path) -> None:
    root = _project(tmp_path)
    state_dir = root / runtime_switch.STATE_RELATIVE
    state_dir.mkdir(parents=True, mode=0o700)
    (root / "var" / "openshell").chmod(0o700)
    outside = tmp_path / "outside.json"
    outside.write_text("preserve\n", encoding="ascii")
    (state_dir / runtime_switch.STATE_NAME).symlink_to(outside)

    with pytest.raises(runtime_switch.RuntimeSwitchError, match="file_unsafe"):
        runtime_switch.switch_runtime(root, target="openshell", session_mode="all")

    assert outside.read_text(encoding="ascii") == "preserve\n"


def test_status_uses_environment_defaults_before_first_switch(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path)
    monkeypatch.setenv("SIQ_HERMES_RUNTIME", "host")

    status = runtime_switch.runtime_status(root)

    assert status["source"] == "environment"
    assert status["target"] == "host"
    assert status["session_mode"] == "allowlist"
