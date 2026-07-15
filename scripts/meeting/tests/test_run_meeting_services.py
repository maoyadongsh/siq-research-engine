from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = PROJECT_ROOT / "scripts" / "meeting" / "run_meeting_services.sh"
AI_WORKER = PROJECT_ROOT / "apps" / "api" / "scripts" / "meeting_ai_worker.py"
IMPORT_WORKER = PROJECT_ROOT / "apps" / "api" / "scripts" / "meeting_import_worker.py"


def test_disabled_meeting_service_group_exits_without_starting_children(tmp_path):
    environment = {
        **os.environ,
        "SIQ_PROJECT_ROOT": str(PROJECT_ROOT),
        "SIQ_ENV_FILE": str(tmp_path / "missing.env"),
        "SIQ_MEETINGS_ENABLED": "0",
    }
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0
    assert "meeting services are disabled" in result.stderr
    assert "starting meeting component" not in result.stdout


def test_service_group_is_config_driven_and_shell_syntax_is_valid():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "meeting_targets.py" in source
    assert "target_ids" in source
    assert "SIQ_MEETING_DELETE_WORKER_ENABLED" in source
    assert "SIQ_MEETING_STREAM_GATEWAY_MODE" in source
    assert "meeting_stream_gateway:app" in source
    assert "SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED" in source
    assert "scripts/meeting_native_capture_worker.py" in source
    assert "protected deployments require" in source
    assert "scripts/meeting_retention_worker.py" in source
    assert "scripts/meeting_import_worker.py --mode ingest" in source
    assert "scripts/meeting_ai_worker.py --lane finalization" in source
    assert "scripts/meeting_ai_worker.py --lane minutes" in source
    assert "scripts/meeting_ai_worker.py --lane correction" in source
    assert source.index("--mode ingest") < source.index("--lane finalization")
    ai_block = source.index('if is_enabled "$ai_flag"; then')
    assert source.index("--lane finalization") < ai_block
    assert source.index("--lane minutes") > ai_block
    assert source.index("--lane correction") > ai_block
    for forbidden_model_name in ("nemotron", "qwen", "minimax", "kimi", "gemma"):
        assert forbidden_model_name not in source.lower()
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True, timeout=5)


def test_frontend_receives_native_capture_feature_flag():
    start_script = (PROJECT_ROOT / "start_all.sh").read_text(encoding="utf-8")
    assert (
        'export VITE_SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED='
        '"${SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED:-0}"'
    ) in start_script


def test_worker_cli_keeps_compatible_defaults_and_exposes_isolated_modes():
    ai_help = subprocess.run(
        [sys.executable, str(AI_WORKER), "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout
    import_help = subprocess.run(
        [sys.executable, str(IMPORT_WORKER), "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout

    normalized_ai_help = " ".join(ai_help.split())
    normalized_import_help = " ".join(import_help.split())
    assert "--lane {all,finalization,minutes,correction}" in normalized_ai_help
    assert "default 'all'" in normalized_ai_help
    assert "--mode {all,ingest,postprocess}" in normalized_import_help
    assert "default 'all'" in normalized_import_help
