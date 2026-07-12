import importlib.util
import json
import urllib.request
from email.message import Message
from pathlib import Path

import pytest


def load_module():
    path = Path(__file__).resolve().parents[1] / "run_production_compose_smoke.py"
    spec = importlib.util.spec_from_file_location("production_compose_smoke", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def test_smoke_environment_uses_unique_loopback_ports_and_production_guards(monkeypatch):
    module = load_module()
    ports = iter(range(21000, 21007))
    monkeypatch.setattr(module, "available_port", lambda: next(ports))
    monkeypatch.setattr(module.secrets, "token_urlsafe", lambda _size: "fixed-secret")
    monkeypatch.setenv("SIQ_POSTGRES_DATA_VOLUME", "/real/production/postgres")
    monkeypatch.setenv("MINERU_API_URL", "https://real-mineru.example")
    monkeypatch.setenv("VLM_API_URL", "https://real-vlm.example")
    monkeypatch.setenv("SIQ_AGENT_MEMORY_WRITE_ENABLED", "true")
    monkeypatch.setenv("SIQ_AGENT_MEMORY_PGVECTOR_ENABLED", "true")
    monkeypatch.setenv("SIQ_AGENT_MEMORY_MILVUS_COLLECTION", "siq_agent_memory")
    monkeypatch.setenv("SIQ_AGENT_MEMORY_EMBEDDING_BASE_URL", "https://real-embedding.example")
    monkeypatch.setenv("SIQ_MILVUS_HOST", "real-milvus.internal")

    env = module.smoke_environment()

    assert env["SIQ_DEPLOYMENT_PROFILE"] == "production"
    assert env["SIQ_CORS_ALLOW_ORIGINS"] == "http://127.0.0.1:21001"
    assert len({env[name] for name in ("SIQ_BACKEND_PORT", "SIQ_FRONTEND_PORT", "SIQ_REPORT_FINDER_PORT", "SIQ_PDF2MD_PORT", "SIQ_DOCUMENT_PARSER_PORT", "SIQ_POSTGRES_PORT", "SIQ_REDIS_PORT")}) == 7
    assert env["POSTGRES_PASSWORD"] == "pg-fixed-secret"
    assert env["SIQ_POSTGRES_DATA_VOLUME"] == "postgres_data"
    assert env["SIQ_POSTGRES_IMAGE"] == "postgres:16-alpine"
    assert env["MINERU_API_URL"] == "http://127.0.0.1:9"
    assert env["VLM_API_URL"] == "http://127.0.0.1:9"
    assert env["SIQ_AGENT_MEMORY_ENABLED"] == "true"
    assert env["SIQ_AGENT_MEMORY_WRITE_ENABLED"] == "true"
    assert env["SIQ_AGENT_MEMORY_RETRIEVAL_ENABLED"] == "false"
    assert env["SIQ_AGENT_MEMORY_PGVECTOR_ENABLED"] == "false"
    assert env["SIQ_AGENT_MEMORY_MILVUS_COLLECTION"] == "siq_agent_memory_active"
    assert env["SIQ_AGENT_MEMORY_EMBEDDING_BASE_URL"] == "http://127.0.0.1:9"
    assert env["SIQ_MILVUS_HOST"] == "127.0.0.1"
    assert env["SIQ_MILVUS_PORT"] == "9"
    assert env["HERMES_API_KEY"] == "hermes-fixed-secret"
    assert "host.docker.internal" in env["NO_PROXY"]
    assert env["no_proxy"] == env["NO_PROXY"]
    assert env["SIQ_AUTH_COOKIE_MODE"] == "1"
    assert env["SIQ_AUTH_COOKIE_SECURE"] == "0"


def test_production_compose_requires_agent_memory_alias_and_examples_configure_it():
    repo_root = Path(__file__).resolve().parents[3]
    compose = (repo_root / "infra/docker/docker-compose.yml").read_text(encoding="utf-8")
    production_env = (repo_root / "infra/env/production.example").read_text(encoding="utf-8")
    docker_env = (repo_root / "infra/env/docker.example").read_text(encoding="utf-8")

    required_contract = (
        "SIQ_AGENT_MEMORY_MILVUS_COLLECTION="
        "${SIQ_AGENT_MEMORY_MILVUS_COLLECTION:?SIQ_AGENT_MEMORY_MILVUS_COLLECTION is required}"
    )
    assert required_contract in compose
    assert "${SIQ_AGENT_MEMORY_MILVUS_COLLECTION:-siq_agent_memory}" not in compose
    assert "SIQ_AGENT_MEMORY_MILVUS_COLLECTION=siq_agent_memory_active" in production_env
    assert "SIQ_AGENT_MEMORY_MILVUS_COLLECTION=siq_agent_memory_active" in docker_env
    assert "HERMES_API_KEY=${HERMES_API_KEY:-}" in compose
    assert "SIQ_HERMES_ASSISTANT_RUNS_URL=${SIQ_HERMES_ASSISTANT_RUNS_URL:-}" in compose
    assert '"host.docker.internal:host-gateway"' in compose
    assert compose.count("condition: service_healthy") >= 4
    assert "HERMES_API_KEY=replace-with-hermes-gateway-token" in production_env
    assert "SIQ_HERMES_ASSISTANT_RUNS_URL=https://hermes-assistant.example.internal/v1/runs" in production_env


def test_browser_smoke_environment_keeps_credentials_process_local(tmp_path):
    module = load_module()

    env = module.browser_smoke_environment(
        {"PATH": "/usr/bin"},
        project="isolated-project",
        web_url="http://127.0.0.1:21001",
        backend_url="http://127.0.0.1:21000",
        username="compose-smoke",
        password="temporary-password",
        output_dir=str(tmp_path),
    )

    assert env["PLAYWRIGHT_BASE_URL"] == "http://127.0.0.1:21001"
    assert env["SIQ_E2E_BACKEND_URL"] == "http://127.0.0.1:21000"
    assert env["SIQ_E2E_COMPOSE_PROJECT"] == "isolated-project"
    assert env["SIQ_PRODUCTION_COMPOSE_BROWSER_SMOKE"] == "1"
    assert env["SIQ_E2E_USERNAME"] == "compose-smoke"
    assert env["SIQ_E2E_PASSWORD"] == "temporary-password"
    assert env["SIQ_E2E_OUTPUT_DIR"] == str(tmp_path)


def test_controlled_hermes_gateway_stub_validates_auth_and_does_not_record_prompt():
    module = load_module()
    stub = module.HermesGatewayContractStub("temporary-hermes-token")
    stub.start()
    try:
        create_request = urllib.request.Request(
            f"http://127.0.0.1:{stub.port}/v1/runs",
            method="POST",
            headers={
                "Authorization": "Bearer temporary-hermes-token",
                "Content-Type": "application/json",
            },
            data=json.dumps(
                {
                    "model": "siq_assistant",
                    "input": "sensitive prompt must not be retained",
                    "session_id": "session-1",
                    "conversation_history": [{"role": "user", "content": "private"}],
                }
            ).encode(),
        )
        with urllib.request.urlopen(create_request) as response:
            run_id = json.loads(response.read())["run_id"]
        events_request = urllib.request.Request(
            f"http://127.0.0.1:{stub.port}/v1/runs/{run_id}/events",
            headers={"Authorization": "Bearer temporary-hermes-token"},
        )
        with urllib.request.urlopen(events_request) as response:
            assert "run.completed" in response.read().decode()

        snapshot = stub.state.redacted_snapshot()
        assert snapshot["create_run_calls"] == 1
        assert snapshot["stream_calls"] == 1
        assert snapshot["authorization_verified"] is True
        assert snapshot["input_present"] is True
        assert snapshot["conversation_history_count"] == 1
        assert snapshot["session_id_present"] is True
        assert snapshot["prompt_recorded"] is False
        assert "sensitive prompt" not in json.dumps(snapshot)
    finally:
        stub.close()


def test_response_headers_preserve_duplicate_set_cookie_and_detect_cookie_clears():
    module = load_module()
    headers = Message()
    headers.add_header("Set-Cookie", 'siq_access_token=""; Max-Age=0; Path=/; SameSite=lax')
    headers.add_header("Set-Cookie", 'siq_csrf_token=""; Max-Age=0; Path=/; SameSite=lax')

    collected = module.collect_response_headers(headers)

    assert len(collected["set-cookie"]) == 2
    assert module.cookie_was_cleared(collected["set-cookie"], "siq_access_token")
    assert module.cookie_was_cleared(collected["set-cookie"], "siq_csrf_token")


def test_response_cookie_extracts_login_cookie_value_and_security_attributes():
    module = load_module()
    headers = [
        "siq_access_token=jwt-value; HttpOnly; Max-Age=1800; Path=/; SameSite=lax",
        "siq_csrf_token=csrf-value; Max-Age=1800; Path=/; SameSite=lax",
    ]

    access_cookie = module.response_cookie(headers, "siq_access_token")
    csrf_cookie = module.response_cookie(headers, "siq_csrf_token")

    assert access_cookie is not None
    assert access_cookie.value == "jwt-value"
    assert access_cookie["httponly"] is True
    assert access_cookie["path"] == "/"
    assert csrf_cookie is not None
    assert csrf_cookie.value == "csrf-value"
    assert csrf_cookie["httponly"] == ""


def test_cookie_clear_requires_exact_root_path_and_zero_max_age():
    module = load_module()

    assert not module.cookie_was_cleared(
        ['siq_access_token=""; Max-Age=0; Path=/api'],
        "siq_access_token",
    )
    assert not module.cookie_was_cleared(
        ['siq_access_token=""; Max-Age=60; Path=/'],
        "siq_access_token",
    )


def test_report_serialization_redacts_password_even_from_failure_details():
    module = load_module()
    password = "temporary-password-value"

    rendered = module.redacted_report_json(
        {"status": "failed", "error": f"unexpected detail: {password}"},
        secret_values=(password,),
    )

    assert password not in rendered
    assert "unexpected detail: [REDACTED]" in rendered


def test_assert_smoke_contract_accepts_complete_real_boundary_report():
    module = load_module()
    module.assert_smoke_contract(
        {
            "health_status": 200,
            "unauthenticated_status": 401,
            "web_login_status": 200,
            "web_login_username": "compose-smoke",
            "web_login_role": "analyst",
            "web_login_token_type": "bearer",
            "web_login_access_token_returned": True,
            "web_login_access_cookie_set": True,
            "web_login_access_cookie_httponly": True,
            "web_login_csrf_cookie_set": True,
            "web_login_csrf_cookie_httponly": False,
            "web_login_cookie_paths_are_root": True,
            "web_cookie_authenticated_after_restart_status": 200,
            "web_cookie_authenticated_after_restart_username": "compose-smoke",
            "public_table_count": 12,
            "required_index_count": 2,
            "smoke_user_count_after_restart": 1,
            "web_cookie_authenticated_status": 200,
            "web_cookie_authenticated_username": "compose-smoke",
            "web_cookie_csrf_missing_status": 403,
            "web_cookie_csrf_valid_status": 200,
            "web_cookie_csrf_valid_message": "\u767b\u51fa\u6210\u529f",
            "web_logout_access_cookie_cleared": True,
            "web_logout_csrf_cookie_cleared": True,
            "browser_playwright_status": "passed",
            "chat_http_status": 200,
            "chat_guardrail_blocked": True,
            "chat_guardrail_reason": "financial_evidence_missing",
            "chat_audit_trace_returned": True,
            "chat_history_status": 200,
            "chat_history_message_count": 2,
            "chat_history_assistant_audit_linked": True,
            "chat_audit_status": 200,
            "chat_audit_guardrail_blocked": True,
            "chat_audit_violation_reason": "value_mismatch",
            "chat_db_message_count": 2,
            "chat_db_audited_assistant_count": 1,
            "agent_memory_db_message_count": 2,
            "agent_memory_db_session_count": 1,
            "hermes_gateway_mode": "controlled_gateway_stub",
            "hermes_gateway_contract": {
                "create_run_calls": 1,
                "stream_calls": 1,
                "authorization_verified": True,
                "prompt_recorded": False,
            },
        }
    )


def test_assert_smoke_contract_rejects_missing_database_indexes():
    module = load_module()
    with pytest.raises(AssertionError, match="indexes"):
        module.assert_smoke_contract(
            {
                "health_status": 200,
                "unauthenticated_status": 401,
                "web_login_status": 200,
                "web_login_username": "compose-smoke",
                "web_login_role": "analyst",
                "web_login_token_type": "bearer",
                "web_login_access_token_returned": True,
                "web_login_access_cookie_set": True,
                "web_login_access_cookie_httponly": True,
                "web_login_csrf_cookie_set": True,
                "web_login_csrf_cookie_httponly": False,
                "web_login_cookie_paths_are_root": True,
                "web_cookie_authenticated_after_restart_status": 200,
                "web_cookie_authenticated_after_restart_username": "compose-smoke",
                "public_table_count": 12,
                "required_index_count": 1,
                "smoke_user_count_after_restart": 1,
                "web_cookie_authenticated_status": 200,
                "web_cookie_authenticated_username": "compose-smoke",
                "web_cookie_csrf_missing_status": 403,
                "web_cookie_csrf_valid_status": 200,
                "web_cookie_csrf_valid_message": "\u767b\u51fa\u6210\u529f",
                "web_logout_access_cookie_cleared": True,
                "web_logout_csrf_cookie_cleared": True,
                "browser_playwright_status": "passed",
                "chat_http_status": 200,
                "chat_guardrail_blocked": True,
                "chat_guardrail_reason": "financial_claim_mismatch",
                "chat_audit_trace_returned": True,
                "chat_history_status": 200,
                "chat_history_message_count": 2,
                "chat_history_assistant_audit_linked": True,
                "chat_audit_status": 200,
                "chat_audit_guardrail_blocked": True,
                "chat_audit_violation_reason": "value_mismatch",
                "chat_db_message_count": 2,
                "chat_db_audited_assistant_count": 1,
                "agent_memory_db_message_count": 2,
                "agent_memory_db_session_count": 1,
                "hermes_gateway_mode": "controlled_gateway_stub",
                "hermes_gateway_contract": {
                    "create_run_calls": 1,
                    "stream_calls": 1,
                    "authorization_verified": True,
                    "prompt_recorded": False,
                },
            }
        )


def test_smoke_uses_password_login_without_manual_token_or_csrf_issuance():
    module = load_module()
    source = Path(module.__file__).read_text(encoding="utf-8")

    assert "AuthService.hash_password" in source
    assert 'f"{web_url}/api/auth/login"' in source
    assert "AuthService.create_access_token" not in source
    assert "AuthService.create_csrf_token" not in source
    assert 'env["SIQ_SMOKE_USER_PASSWORD"]' in source
    assert "playwright.production-compose.config.ts" in source
    assert "production-compose-password-login.spec.ts" in source
