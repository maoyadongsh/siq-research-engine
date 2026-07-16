from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
ANALYSIS_RUNNER = (
    REPO_ROOT
    / "agents/hermes/profiles/siq_analysis_multi_market/scripts/run_analysis_report.py"
)
FACTCHECK_RUNNER = (
    REPO_ROOT
    / "agents/hermes/profiles/siq_factchecker_multi_market/scripts/factcheck_cli.py"
)
TRACKING_RUNNER = REPO_ROOT / "data/wiki/tracking/scripts_multi_market/run_all.py"
TRACKING_AGENT = REPO_ROOT / "agents/hermes/profiles/siq_tracking_multi_market/agent.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "SIQ_PROJECT_ROOT": str(REPO_ROOT),
        },
        text=True,
        capture_output=True,
        check=False,
    )


def test_non_cn_profile_commands_require_server_resolved_bundles() -> None:
    analysis = _run(str(ANALYSIS_RUNNER))
    factcheck = _run(str(FACTCHECK_RUNNER), "verify")
    tracking = _run(str(TRACKING_RUNNER))

    assert analysis.returncode == 2
    assert "--input-bundle" in analysis.stderr
    assert factcheck.returncode == 2
    assert "--target-json" in factcheck.stderr
    assert tracking.returncode == 2
    assert "--target-json" in tracking.stderr


def test_tracking_profile_wrapper_imports_only_multi_market_scripts() -> None:
    probe = _run(
        "-c",
        (
            "import importlib.util, pathlib; "
            f"p=pathlib.Path({str(TRACKING_AGENT)!r}); "
            "s=importlib.util.spec_from_file_location('siq_tracking_multi_agent_probe', p); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
            "print(pathlib.Path(m.run_all.__code__.co_filename).resolve())"
        ),
    )

    assert probe.returncode == 0, probe.stderr
    assert Path(probe.stdout.strip()) == TRACKING_RUNNER.resolve()


def test_non_cn_profile_entrypoints_do_not_contain_openshell_publisher_hooks() -> None:
    for path in (ANALYSIS_RUNNER, FACTCHECK_RUNNER):
        source = path.read_text(encoding="utf-8").lower()
        assert "openshell" not in source
        assert "publish_company_index" not in source
