from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
START_ALL = ROOT / "start_all.sh"
BEGIN_MARKER = "# ---------- OpenShell gateway lifecycle (BEGIN) ----------"
END_MARKER = "# ---------- OpenShell gateway lifecycle (END) ----------"


def _lifecycle_block() -> str:
    source = START_ALL.read_text(encoding="utf-8")
    return source.split(BEGIN_MARKER, 1)[1].split(END_MARKER, 1)[0]


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def fake_project(tmp_path: Path) -> Path:
    scripts = tmp_path / "scripts" / "openshell"
    scripts.mkdir(parents=True)
    state = tmp_path / "gateway-state"
    state.write_text("stopped\n", encoding="utf-8")
    call_log = tmp_path / "calls.log"

    _write_executable(
        scripts / "status_gateway.sh",
        r"""
        #!/usr/bin/env bash
        set -euo pipefail
        printf 'status\n' >> "${FAKE_CALL_LOG:?}"
        if [[ "$(cat "${FAKE_GATEWAY_STATE:?}")" == "running" ]]; then
            cat <<'EOF'
        Process: running (PID 12345)
        Health: reachable
        Server: https://127.0.0.1:17671
        Status: Connected
        Version: 0.0.83
        EOF
        else
            printf 'Process: stopped\nHealth: unreachable\n'
        fi
        """,
    )
    _write_executable(
        scripts / "start_gateway.sh",
        r"""
        #!/usr/bin/env bash
        set -euo pipefail
        printf 'start\n' >> "${FAKE_CALL_LOG:?}"
        case "${FAKE_START_MODE:-new}" in
            new)
                printf 'running\n' > "${FAKE_GATEWAY_STATE:?}"
                printf 'OpenShell gateway siq-openshell-dev connected on 127.0.0.1:17671 (PID 12345)\n'
                ;;
            race)
                printf 'running\n' > "${FAKE_GATEWAY_STATE:?}"
                printf 'Gateway already running with PID 23456\n'
                ;;
            fail)
                printf 'postgresql://user:secret@unknown.example/data\n' >&2
                exit 2
                ;;
            *)
                printf 'unrecognized success output\n'
                ;;
        esac
        """,
    )
    _write_executable(
        scripts / "stop_gateway.sh",
        r"""
        #!/usr/bin/env bash
        set -euo pipefail
        printf 'stop\n' >> "${FAKE_CALL_LOG:?}"
        printf 'stopped\n' > "${FAKE_GATEWAY_STATE:?}"
        """,
    )
    (scripts / "render_gateway_config.py").write_text(
        "import os\nraise SystemExit(0 if os.environ.get('FAKE_CONFIG_OK', '1') == '1' else 2)\n",
        encoding="utf-8",
    )
    for component in (
        "env.sh",
        "gateway_bind_contract.py",
        "gateway_runtime_identity.py",
        "gateway_start_recovery.py",
        "prepare_gateway.sh",
        "run_cli.sh",
    ):
        _write_executable(scripts / component, "#!/usr/bin/env bash\nexit 0\n")
    (scripts / "render_gateway_config.py").chmod(0o755)

    bin_root = tmp_path / "var" / "openshell" / "toolchains" / "v0.0.83" / "bin"
    for component in ("openshell", "openshell-gateway", "openshell-sandbox"):
        _write_executable(
            bin_root / component,
            f"""
            #!/usr/bin/env bash
            printf '{component} 0.0.83\\n'
            """,
        )

    gateway_root = tmp_path / "var" / "openshell" / "gateway" / "siq-openshell-dev"
    gateway_root.mkdir(parents=True)
    (gateway_root / "gateway.toml").write_text("[openshell]\nversion = 1\n", encoding="utf-8")
    required_tls = (
        "ca.crt",
        "client/tls.crt",
        "client/tls.key",
        "jwt/kid",
        "jwt/public.pem",
        "jwt/signing.pem",
        "server/tls.crt",
        "server/tls.key",
    )
    for tls_root in (
        gateway_root / "tls",
        tmp_path / "var" / "openshell" / "xdg" / "state" / "openshell" / "tls",
    ):
        for relative in required_tls:
            target = tls_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("test-only\n", encoding="utf-8")

    call_log.write_text("", encoding="utf-8")
    return tmp_path


def _run_lifecycle(
    project: Path,
    *,
    enabled: str = "1",
    runtime: str = "host",
    start_mode: str = "new",
    cleanup: bool = True,
) -> subprocess.CompletedProcess[str]:
    harness = project / "lifecycle-harness.sh"
    harness.write_text(
        textwrap.dedent(
            f"""
            #!/usr/bin/env bash
            set -euo pipefail
            SIQ_PROJECT_ROOT={project}
            START_OPENSHELL_GATEWAY={enabled}
            HERMES_RUNTIME={runtime}
            OPENSHELL_GATEWAY_ENDPOINT=https://127.0.0.1:17671
            OPENSHELL_GATEWAY_VERSION=0.0.83
            OPENSHELL_GATEWAY_STARTED_BY_START_ALL=0
            OPENSHELL_GATEWAY_LIFECYCLE_STATE=disabled
            log() {{ :; }}
            ok() {{ :; }}
            warn() {{ :; }}
            die() {{ printf 'FAIL: %s\\n' "$*" >&2; exit 91; }}
            {_lifecycle_block()}
            ensure_openshell_gateway
            printf 'before_cleanup owned=%s state=%s runtime=%s\\n' \
                "$OPENSHELL_GATEWAY_STARTED_BY_START_ALL" \
                "$OPENSHELL_GATEWAY_LIFECYCLE_STATE" \
                "$SIQ_HERMES_RUNTIME"
            if [[ {1 if cleanup else 0} == 1 ]]; then
                stop_owned_openshell_gateway
            fi
            printf 'after_cleanup owned=%s state=%s\\n' \
                "$OPENSHELL_GATEWAY_STARTED_BY_START_ALL" \
                "$OPENSHELL_GATEWAY_LIFECYCLE_STATE"
            """
        ).lstrip(),
        encoding="utf-8",
    )
    harness.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "FAKE_CALL_LOG": str(project / "calls.log"),
            "FAKE_GATEWAY_STATE": str(project / "gateway-state"),
            "FAKE_START_MODE": start_mode,
            "FAKE_CONFIG_OK": "1",
        }
    )
    return subprocess.run(
        [str(harness)],
        cwd=project,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _calls(project: Path) -> list[str]:
    return (project / "calls.log").read_text(encoding="utf-8").splitlines()


def test_start_all_defaults_to_host_runtime_and_orders_gateway_before_hermes() -> None:
    source = START_ALL.read_text(encoding="utf-8")
    block = _lifecycle_block()

    assert 'START_OPENSHELL_GATEWAY="${SIQ_START_OPENSHELL_GATEWAY:-1}"' in source
    assert '"${SIQ_HERMES_RUNTIME:-host}"' in source
    assert '[[ "$HERMES_RUNTIME" != "host" ]]' in block
    assert "export SIQ_HERMES_RUNTIME=host" in block
    assert source.index("\nensure_openshell_gateway\n") < source.index("\nstart_hermes_gateway()")
    assert 'scripts/openshell/status_gateway.sh"' in block
    assert 'scripts/openshell/start_gateway.sh"' in block
    assert 'scripts/openshell/stop_gateway.sh"' in block
    assert '"$openshell_dir/prepare_gateway.sh"' not in block
    assert "Hermes environment fallback: $SIQ_HERMES_RUNTIME" in source
    assert "SIQ analysis runtime selection:" in source
    assert "OpenShell gateway: running (pre-existing) -> $OPENSHELL_GATEWAY_ENDPOINT" in source


def test_disabled_mode_does_not_touch_openshell(fake_project: Path) -> None:
    result = _run_lifecycle(fake_project, enabled="0")

    assert result.returncode == 0, result.stderr
    assert _calls(fake_project) == []
    assert "before_cleanup owned=0 state=disabled runtime=host" in result.stdout


def test_preexisting_gateway_is_never_owned_or_stopped(fake_project: Path) -> None:
    (fake_project / "gateway-state").write_text("running\n", encoding="utf-8")
    result = _run_lifecycle(fake_project)

    assert result.returncode == 0, result.stderr
    assert _calls(fake_project) == ["status"]
    assert "before_cleanup owned=0 state=preexisting runtime=host" in result.stdout
    assert "after_cleanup owned=0 state=preexisting" in result.stdout


def test_gateway_started_by_start_all_is_stopped_during_cleanup(fake_project: Path) -> None:
    result = _run_lifecycle(fake_project)

    assert result.returncode == 0, result.stderr
    assert _calls(fake_project) == ["status", "start", "status", "stop"]
    assert "before_cleanup owned=1 state=started_by_start_all runtime=host" in result.stdout
    assert "after_cleanup owned=0 state=stopped" in result.stdout


def test_concurrent_start_is_treated_as_preexisting(fake_project: Path) -> None:
    result = _run_lifecycle(fake_project, start_mode="race")

    assert result.returncode == 0, result.stderr
    assert _calls(fake_project) == ["status", "start", "status"]
    assert "before_cleanup owned=0 state=preexisting runtime=host" in result.stdout


def test_non_host_runtime_is_rejected_before_gateway_probe(fake_project: Path) -> None:
    result = _run_lifecycle(fake_project, runtime="openshell")

    assert result.returncode == 91
    assert _calls(fake_project) == []
    assert "环境基线必须保持 SIQ_HERMES_RUNTIME=host" in result.stderr


def test_invalid_gateway_switch_is_rejected_before_asset_checks(fake_project: Path) -> None:
    result = _run_lifecycle(fake_project, enabled="yes")

    assert result.returncode == 91
    assert _calls(fake_project) == []
    assert "仅支持 0 或 1" in result.stderr


def test_start_failure_is_clear_without_leaking_captured_output(fake_project: Path) -> None:
    result = _run_lifecycle(fake_project, start_mode="fail")

    assert result.returncode == 91
    assert _calls(fake_project) == ["status", "start"]
    assert "gateway 启动失败" in result.stderr
    assert "postgresql://" not in result.stderr
    assert "secret" not in result.stderr


def test_missing_assets_fail_before_start(fake_project: Path) -> None:
    (fake_project / "var" / "openshell" / "toolchains" / "v0.0.83" / "bin" / "openshell").unlink()
    result = _run_lifecycle(fake_project)

    assert result.returncode == 91
    assert _calls(fake_project) == []
    assert "项目本地工具链不完整" in result.stderr
