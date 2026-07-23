from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
START_ALL = ROOT / "start_all.sh"
BEGIN_MARKER = "# ---------- OpenShell siq_analysis pool lifecycle (BEGIN) ----------"
END_MARKER = "# ---------- OpenShell siq_analysis pool lifecycle (END) ----------"


def _analysis_lifecycle_block() -> str:
    source = START_ALL.read_text(encoding="utf-8")
    return source.split(BEGIN_MARKER, 1)[1].split(END_MARKER, 1)[0]


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    path.chmod(0o755)


def test_formal_api_enables_required_pool_recovery_by_default() -> None:
    source = START_ALL.read_text(encoding="utf-8")

    assert 'export SIQ_BACKEND_PORT="$BACKEND_PORT"' in source
    assert (
        'export SIQ_OPENSHELL_POOL_RECOVERY_ENABLED="${SIQ_OPENSHELL_POOL_RECOVERY_ENABLED:-1}"'
        in source
    )
    assert (
        'export SIQ_OPENSHELL_POOL_RECOVERY_REQUIRED="${SIQ_OPENSHELL_POOL_RECOVERY_REQUIRED:-1}"'
        in source
    )


def test_degraded_status_is_parsed_and_stale_binding_is_stopped(tmp_path: Path) -> None:
    lifecycle = tmp_path / "scripts" / "openshell" / "run_siq_analysis_pool_lifecycle.sh"
    call_log = tmp_path / "calls.log"
    _write_executable(
        lifecycle,
        """
        #!/usr/bin/env bash
        set -euo pipefail
        printf '%s\\n' "$*" >> "${FAKE_CALL_LOG:?}"
        case "$1" in
            status)
                printf '%s\\n' '{"ok":false,"status":"degraded","run_id":"canary-111111111111","local_port":28652}'
                exit 1
                ;;
            stop)
                exit 0
                ;;
            *)
                exit 2
                ;;
        esac
        """,
    )
    harness = tmp_path / "harness.sh"
    harness.write_text(
        textwrap.dedent(
            f"""
            #!/usr/bin/env bash
            set -euo pipefail
            SIQ_PROJECT_ROOT={tmp_path}
            OPENSHELL_ANALYSIS_MARKET=cn
            OPENSHELL_ANALYSIS_COMPANY=600104-上汽集团
            OPENSHELL_ANALYSIS_RUN_ID=""
            OPENSHELL_ANALYSIS_ACTIVE_PORT=""
            OPENSHELL_ANALYSIS_STARTED_BY_START_ALL=0
            OPENSHELL_ANALYSIS_LIFECYCLE_STATE=disabled
            log() {{ :; }}
            ok() {{ :; }}
            warn() {{ :; }}
            die() {{ printf 'FAIL: %s\\n' "$*" >&2; exit 91; }}
            {_analysis_lifecycle_block()}
            stop_registered_openshell_analysis_binding
            printf 'run_id=%s port=%s\\n' "$OPENSHELL_ANALYSIS_RUN_ID" "$OPENSHELL_ANALYSIS_ACTIVE_PORT"
            """
        ).lstrip(),
        encoding="utf-8",
    )
    harness.chmod(0o755)

    result = subprocess.run(
        [str(harness)],
        cwd=tmp_path,
        env={**os.environ, "FAKE_CALL_LOG": str(call_log)},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "run_id= port=" in result.stdout
    calls = call_log.read_text(encoding="utf-8").splitlines()
    assert calls == [
        "status --market cn --company 600104-上汽集团",
        "stop --market cn --company 600104-上汽集团 --run-id canary-111111111111",
    ]


def test_preexisting_probe_updates_binding_in_parent_shell(tmp_path: Path) -> None:
    lifecycle = tmp_path / "scripts" / "openshell" / "run_siq_analysis_pool_lifecycle.sh"
    switch = tmp_path / "scripts" / "openshell" / "switch_siq_analysis_runtime.sh"
    _write_executable(
        lifecycle,
        """
        #!/usr/bin/env bash
        set -euo pipefail
        [[ "$1" == probe ]]
        printf '%s\n' '{"ok":true,"status":"probe_passed","run_id":"canary-222222222222","local_port":28653}'
        """,
    )
    _write_executable(
        switch,
        """
        #!/usr/bin/env bash
        set -euo pipefail
        [[ "$1" == openshell ]]
        printf '%s\n' '{"profile":"siq_analysis","target":"openshell"}'
        """,
    )
    harness = tmp_path / "harness.sh"
    harness.write_text(
        textwrap.dedent(
            f"""
            #!/usr/bin/env bash
            set -euo pipefail
            SIQ_PROJECT_ROOT={tmp_path}
            START_OPENSHELL_ANALYSIS=auto
            START_OPENSHELL_GATEWAY=1
            IS_PRODUCTION=0
            SIQ_HERMES_RUNTIME_SELECTION_ENABLED=1
            OPENSHELL_ANALYSIS_MARKET=cn
            OPENSHELL_ANALYSIS_COMPANY=600104-上汽集团
            OPENSHELL_ANALYSIS_LOCAL_PORT=28652
            OPENSHELL_ANALYSIS_RUN_ID=""
            OPENSHELL_ANALYSIS_ACTIVE_PORT=""
            OPENSHELL_ANALYSIS_STARTED_BY_START_ALL=0
            OPENSHELL_ANALYSIS_LIFECYCLE_STATE=disabled
            log() {{ :; }}
            ok() {{ :; }}
            warn() {{ :; }}
            die() {{ printf 'FAIL: %s\n' "$*" >&2; exit 91; }}
            {_analysis_lifecycle_block()}
            ensure_openshell_analysis_pool
            printf 'run_id=%s port=%s state=%s\n' \
                "$OPENSHELL_ANALYSIS_RUN_ID" \
                "$OPENSHELL_ANALYSIS_ACTIVE_PORT" \
                "$OPENSHELL_ANALYSIS_LIFECYCLE_STATE"
            """
        ).lstrip(),
        encoding="utf-8",
    )
    harness.chmod(0o755)

    result = subprocess.run(
        [str(harness)],
        cwd=tmp_path,
        env=os.environ,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert (
        "run_id=canary-222222222222 port=28653 state=preexisting"
        in result.stdout
    )
