from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
START_SCRIPT = REPO_ROOT / "apps" / "api" / "scripts" / "start_meeting_voiceprint_worker.sh"
SYSTEMD_UNIT = REPO_ROOT / "infra" / "systemd-user" / "siq-meeting-voiceprint-worker.service"


def test_independent_voiceprint_startup_assets_are_fail_closed(tmp_path):
    assert stat.S_IMODE(START_SCRIPT.stat().st_mode) == 0o755
    body = START_SCRIPT.read_text(encoding="utf-8")
    assert "uv run --frozen python scripts/run_meeting_voiceprint_worker.py" in body
    assert "umask 077" in body

    result = subprocess.run(
        [str(START_SCRIPT)],
        env={
            **os.environ,
            "SIQ_ENV_FILE": str(tmp_path / "missing.env"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "does not exist" in result.stderr

    unit = SYSTEMD_UNIT.read_text(encoding="utf-8")
    assert "start_meeting_voiceprint_worker.sh" in unit
    assert "Restart=on-failure" in unit
    assert "UMask=0077" in unit
    assert "NoNewPrivileges=true" in unit
