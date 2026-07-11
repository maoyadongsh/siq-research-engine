import os
import subprocess
from pathlib import Path


def _production_reload_env() -> dict[str, str]:
    return {
        **os.environ,
        "SIQ_DEPLOYMENT_PROFILE": "production",
        "SIQ_UVICORN_RELOAD": "1",
        "SIQ_AUTH_SECRET_KEY": "startup-guard-secret-with-enough-length",
        "SIQ_ENV_FILE": "/tmp/siq-startup-guard-missing.env",
        "SIQ_FRONTEND_ENV_FILE": "/tmp/siq-startup-guard-missing-frontend.env",
    }


def test_api_start_disables_reload_by_default_in_production():
    repo_root = Path(__file__).resolve().parents[3]
    script = (repo_root / "apps/api/start.sh").read_text(encoding="utf-8")

    assert "SIQ_DEPLOYMENT_PROFILE" in script
    assert "IS_PRODUCTION=1" in script
    assert 'UVICORN_HOST="127.0.0.1"' in script
    assert 'UVICORN_RELOAD="0"' in script
    assert "SIQ_UVICORN_RELOAD must not be enabled" in script
    assert "FLASK_DEBUG must not be enabled" in script
    assert "uvicorn_args+=(--reload)" in script
    assert 'uv run python -m uvicorn "${uvicorn_args[@]}"' in script
    assert "uv run python -m uvicorn main:app --host 0.0.0.0" not in script


def test_api_start_fails_before_starting_when_production_reload_enabled():
    repo_root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        ["bash", "apps/api/start.sh"],
        cwd=repo_root,
        env=_production_reload_env(),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    assert result.returncode == 1
    assert "SIQ_UVICORN_RELOAD must not be enabled" in result.stdout
    assert "启动 SIQ Research Engine 后端服务" not in result.stdout


def test_start_all_disables_backend_reload_by_default_in_production():
    repo_root = Path(__file__).resolve().parents[3]
    script = (repo_root / "start_all.sh").read_text(encoding="utf-8")

    assert "SIQ_DEPLOYMENT_PROFILE" in script
    assert "BACKEND_HOST=\"127.0.0.1\"" in script
    assert "BACKEND_RELOAD=\"0\"" in script
    assert "SIQ_UVICORN_RELOAD must not be enabled" in script
    assert "FLASK_DEBUG must not be enabled" in script
    assert "uvicorn_args+=(--reload)" in script
    assert 'uv run python -m uvicorn "${uvicorn_args[@]}"' in script
    assert "uv run python -m uvicorn main:app --reload --host 0.0.0.0" not in script


def test_start_all_fails_before_starting_when_production_reload_enabled():
    repo_root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        ["bash", "start_all.sh"],
        cwd=repo_root,
        env={
            **_production_reload_env(),
            "SIQ_START_HERMES_GATEWAYS": "0",
            "SIQ_START_MARKET_REPORT_FINDER": "0",
            "SIQ_START_MARKET_REPORT_RULES": "0",
            "SIQ_START_VECTOR_INGEST": "0",
        },
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    assert result.returncode == 1
    assert "SIQ_UVICORN_RELOAD must not be enabled" in result.stdout
