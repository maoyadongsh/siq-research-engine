from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
START_ALL = ROOT / "start_all.sh"
BEGIN_MARKER = "# ---------- OpenShell broker lifecycle (BEGIN) ----------"
END_MARKER = "# ---------- OpenShell broker lifecycle (END) ----------"


def _broker_block() -> str:
    source = START_ALL.read_text(encoding="utf-8")
    return source.split(BEGIN_MARKER, 1)[1].split(END_MARKER, 1)[0]


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def fake_project(tmp_path: Path) -> Path:
    scripts = tmp_path / "scripts/openshell"
    scripts.mkdir(parents=True)
    state = tmp_path / "broker-state"
    state.write_text("stopped\n", encoding="utf-8")
    calls = tmp_path / "calls.log"
    calls.write_text("", encoding="utf-8")

    _write_executable(
        scripts / "status_brokers.sh",
        r"""
        #!/usr/bin/env bash
        set -euo pipefail
        printf 'status%s\n' "${*:+ $*}" >> "${FAKE_CALL_LOG:?}"
        state="$(cat "${FAKE_BROKER_STATE:?}")"
        if [[ "$state" == invalid ]]; then
            printf '{"ok":false,"error_code":"fixture_invalid"}\n'
            exit 2
        fi
        strict=false
        [[ " $* " == *' --require-request-identity '* ]] && strict=true
        if [[ "$state" == permissive ]]; then
            state=running
            strict=false
        fi
        printf '{"action":"status","ok":true,"brokers":{"egress":{"state":"%s","request_identity_required":%s},"data":{"state":"%s","request_identity_required":%s}}}\n' \
            "$state" "$strict" "$state" "$strict"
        """,
    )
    _write_executable(
        scripts / "start_brokers.sh",
        r"""
        #!/usr/bin/env bash
        set -euo pipefail
        printf 'start%s\n' "${*:+ $*}" >> "${FAKE_CALL_LOG:?}"
        strict=false
        [[ " $* " == *' --require-request-identity '* ]] && strict=true
        case "${FAKE_START_MODE:-new}" in
            new) started='["egress","data"]' ;;
            race) started='[]' ;;
            fail)
                printf 'credential-secret-canary\n' >&2
                exit 2
                ;;
        esac
        printf 'running\n' > "${FAKE_BROKER_STATE:?}"
        printf '{"action":"start","ok":true,"request_identity_required":%s,"started_by_this_call":%s}\n' \
            "$strict" "$started"
        """,
    )
    _write_executable(
        scripts / "stop_brokers.sh",
        r"""
        #!/usr/bin/env bash
        set -euo pipefail
        printf 'stop\n' >> "${FAKE_CALL_LOG:?}"
        printf 'stopped\n' > "${FAKE_BROKER_STATE:?}"
        """,
    )
    for name in ("bridge_endpoint.py", "broker_lifecycle.py"):
        _write_executable(scripts / name, "#!/usr/bin/env bash\nexit 0\n")
    return tmp_path


def _write_secret(project: Path) -> Path:
    secret = project / "var/openshell/secrets/postgres-reader.env"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("fixture-only\n", encoding="utf-8")
    secret.chmod(0o600)
    return secret


def _run(
    project: Path,
    *,
    mode: str = "auto",
    gateway: str = "1",
    start_mode: str = "new",
    cleanup: bool = True,
) -> subprocess.CompletedProcess[str]:
    harness = project / "broker-harness.sh"
    harness.write_text(
        textwrap.dedent(
            f"""
            #!/usr/bin/env bash
            set -euo pipefail
            SIQ_PROJECT_ROOT={project}
            START_OPENSHELL_GATEWAY={gateway}
            START_OPENSHELL_BROKERS={mode}
            OPENSHELL_BROKERS_STARTED_BY_START_ALL=0
            OPENSHELL_BROKERS_LIFECYCLE_STATE=disabled
            log() {{ :; }}
            ok() {{ :; }}
            warn() {{ :; }}
            die() {{ printf 'FAIL: %s\n' "$*" >&2; exit 91; }}
            {_broker_block()}
            ensure_openshell_brokers
            printf 'before_cleanup owned=%s state=%s\n' \
                "$OPENSHELL_BROKERS_STARTED_BY_START_ALL" \
                "$OPENSHELL_BROKERS_LIFECYCLE_STATE"
            if [[ {1 if cleanup else 0} == 1 ]]; then
                stop_owned_openshell_brokers
            fi
            printf 'after_cleanup owned=%s state=%s\n' \
                "$OPENSHELL_BROKERS_STARTED_BY_START_ALL" \
                "$OPENSHELL_BROKERS_LIFECYCLE_STATE"
            """
        ).lstrip(),
        encoding="utf-8",
    )
    harness.chmod(0o755)
    environment = os.environ.copy()
    environment.update(
        {
            "FAKE_CALL_LOG": str(project / "calls.log"),
            "FAKE_BROKER_STATE": str(project / "broker-state"),
            "FAKE_START_MODE": start_mode,
        }
    )
    return subprocess.run(
        [str(harness)],
        cwd=project,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def _calls(project: Path) -> list[str]:
    return (project / "calls.log").read_text(encoding="utf-8").splitlines()


def test_auto_mode_without_secret_preserves_host_runtime(fake_project: Path) -> None:
    result = _run(fake_project)

    assert result.returncode == 0, result.stderr
    assert _calls(fake_project) == []
    assert "state=skipped_missing_secret" in result.stdout


def test_explicit_mode_requires_private_reader_setup(fake_project: Path) -> None:
    result = _run(fake_project, mode="1")

    assert result.returncode == 91
    assert _calls(fake_project) == []
    assert "reader secret" in result.stderr


def test_preexisting_brokers_are_reused_and_not_stopped(fake_project: Path) -> None:
    _write_secret(fake_project)
    (fake_project / "broker-state").write_text("running\n", encoding="utf-8")

    result = _run(fake_project)

    assert result.returncode == 0, result.stderr
    assert _calls(fake_project) == ["status --require-request-identity"]
    assert "owned=0 state=preexisting" in result.stdout


def test_brokers_started_by_start_all_are_stopped_on_cleanup(fake_project: Path) -> None:
    _write_secret(fake_project)

    result = _run(fake_project)

    assert result.returncode == 0, result.stderr
    assert _calls(fake_project) == [
        "status --require-request-identity",
        "start --require-request-identity",
        "status --require-request-identity",
        "stop",
    ]
    assert "before_cleanup owned=1 state=started_by_start_all" in result.stdout
    assert "after_cleanup owned=0 state=stopped" in result.stdout


def test_concurrent_broker_start_is_not_claimed(fake_project: Path) -> None:
    _write_secret(fake_project)

    result = _run(fake_project, start_mode="race")

    assert result.returncode == 0, result.stderr
    assert _calls(fake_project) == [
        "status --require-request-identity",
        "start --require-request-identity",
        "status --require-request-identity",
    ]
    assert "owned=0 state=preexisting_or_concurrent" in result.stdout


def test_broker_start_failure_does_not_leak_captured_output(fake_project: Path) -> None:
    _write_secret(fake_project)

    result = _run(fake_project, start_mode="fail")

    assert result.returncode == 91
    assert "brokers 启动失败" in result.stderr
    assert "credential-secret-canary" not in result.stderr


def test_preexisting_permissive_brokers_are_rejected(fake_project: Path) -> None:
    _write_secret(fake_project)
    (fake_project / "broker-state").write_text("permissive\n", encoding="utf-8")

    result = _run(fake_project)

    assert result.returncode == 91
    assert _calls(fake_project) == ["status --require-request-identity"]
    assert "状态异常" in result.stderr


def test_gateway_disabled_skips_auto_brokers_and_rejects_explicit_mode(fake_project: Path) -> None:
    _write_secret(fake_project)
    automatic = _run(fake_project, gateway="0")
    explicit = _run(fake_project, gateway="0", mode="1")

    assert automatic.returncode == 0
    assert "skipped_gateway_disabled" in automatic.stdout
    assert explicit.returncode == 91
    assert "不能禁用项目 gateway" in explicit.stderr


def test_start_all_orders_gateway_then_brokers_then_host_hermes() -> None:
    source = START_ALL.read_text(encoding="utf-8")

    assert 'START_OPENSHELL_BROKERS="$(printf' in source
    assert "${SIQ_START_OPENSHELL_BROKERS:-auto}" in source
    assert source.index("\nensure_openshell_gateway\n") < source.index("\nensure_openshell_brokers\n")
    assert source.index("\nensure_openshell_brokers\n") < source.index("\nstart_hermes_gateway()")
    assert source.index("stop_owned_openshell_brokers") < source.index("stop_owned_openshell_gateway")
