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


def _run_api_start(tmp_path, database_url):
    repo_root = Path(__file__).resolve().parents[3]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    fake_uv = bin_dir / "uv"
    fake_uv.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_uv.chmod(0o700)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "SIQ_AUTH_SECRET_KEY": "startup-guard-secret-with-enough-length",
        "SIQ_ENV_FILE": str(tmp_path / "missing.env"),
    }
    env.pop("DATABASE_URL", None)
    env.pop("SIQ_APP_DATABASE_URL", None)
    if database_url is not None:
        env["SIQ_APP_DATABASE_URL"] = database_url
    return subprocess.run(
        ["bash", "apps/api/start.sh"],
        cwd=repo_root,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def test_api_start_logs_only_configured_for_database_urls_with_at_password_and_query_secrets(tmp_path):
    database_urls = (
        "postgresql+psycopg://postgres:abc@def@db.internal:5432/siq_app?sslmode=require",
        "postgresql+psycopg://postgres@db.internal:5432/siq_app?password=query-secret&sslpassword=tls-secret",
    )

    for index, database_url in enumerate(database_urls):
        result = _run_api_start(tmp_path / str(index), database_url)

        assert result.returncode == 0
        assert "SIQ_APP_DATABASE_URL: configured" in result.stdout
        assert database_url not in result.stdout
        for secret_fragment in ("postgresql", "abc@def", "db.internal", "query-secret", "tls-secret"):
            assert secret_fragment not in result.stdout


def test_api_start_logs_not_configured_without_database_url(tmp_path):
    result = _run_api_start(tmp_path, None)

    assert result.returncode == 0
    assert "SIQ_APP_DATABASE_URL: not configured" in result.stdout


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


def test_start_all_fails_fast_when_any_service_process_exits():
    repo_root = Path(__file__).resolve().parents[3]
    script = (repo_root / "start_all.sh").read_text(encoding="utf-8")

    assert 'wait -n "${pids[@]}"' in script
    assert "服务子进程已退出" in script
    assert script.rstrip().endswith(
        'die "服务子进程已退出 (exit $child_exit_code)，正在停止其余服务。请检查上方日志后重新启动。"'
    )


def test_start_all_repairs_stale_local_pdf_endpoint_from_legacy_env():
    repo_root = Path(__file__).resolve().parents[3]
    script = (repo_root / "start_all.sh").read_text(encoding="utf-8")

    assert "LOADED_LEGACY_ENV=1" in script
    assert 'case "${SIQ_PDF2MD_API_BASE:-}" in' in script
    assert 'SIQ_PDF2MD_API_BASE="http://127.0.0.1:$PDF2MD_PORT"' in script
    assert 'SIQ_PDF2MD_HEALTH_URL="http://127.0.0.1:$PDF2MD_PORT/api/ready"' in script
    assert 'SIQ_DOCUMENT_PARSER_HEALTH_URL="http://127.0.0.1:$DOCUMENT_PARSER_PORT/api/ready"' in script
    assert 'wait_for_http "http://localhost:$PDF2MD_PORT/api/ready"' in script
    assert 'wait_for_http "http://localhost:$DOCUMENT_PARSER_PORT/api/ready"' in script


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
