#!/usr/bin/env python3
"""Run a disposable production Compose Web/password-auth/PostgreSQL smoke."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.cookies import CookieError, Morsel, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "infra/docker/docker-compose.yml"
ENV_FILE = REPO_ROOT / "infra/env/docker.example"
NOT_COVERED = [
    "web_tls_reverse_proxy",
    "milvus",
    "mineru_vlm_inference",
]
HERMES_GUARD_SMOKE_REPLY = (
    "工商银行 2025 年营业收入为 6,351.26 亿元。\n\n"
    "[P1] source_type=wiki_metrics company_id=HK:01398 filing_id=2025-annual "
    "canonical_name=operating_revenue metric_name=营业收入 period_key=2025 "
    'value=8382.70 unit=亿元 evidence_id=EVID-COMPOSE-SMOKE quote="营业收入 838,270" task_id=task-smoke'
)


class HermesGatewayStubState:
    def __init__(self) -> None:
        self.create_run_calls = 0
        self.stream_calls = 0
        self.authorization_verified = False
        self.input_present = False
        self.conversation_history_count = 0
        self.session_id_present = False
        self.model = ""

    def redacted_snapshot(self) -> dict[str, object]:
        return {
            "create_run_calls": self.create_run_calls,
            "stream_calls": self.stream_calls,
            "authorization_verified": self.authorization_verified,
            "input_present": self.input_present,
            "conversation_history_count": self.conversation_history_count,
            "session_id_present": self.session_id_present,
            "model": self.model,
            "prompt_recorded": False,
            "raw_model_output_recorded": False,
        }


class HermesGatewayContractStub:
    def __init__(self, token: str):
        self.token = token
        self.state = HermesGatewayStubState()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args) -> None:
                return

            def _authorized(self) -> bool:
                expected = f"Bearer {stub.token}"
                supplied = str(self.headers.get("Authorization") or "")
                authorized = hmac.compare_digest(supplied, expected)
                stub.state.authorization_verified = stub.state.authorization_verified or authorized
                return authorized

            def _send_json(self, status: int, payload: dict) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802
                if not self._authorized():
                    self._send_json(401, {"detail": "unauthorized"})
                    return
                if self.path != "/v1/runs":
                    self._send_json(404, {"detail": "not found"})
                    return
                length = min(int(self.headers.get("Content-Length") or 0), 1_000_000)
                try:
                    payload = json.loads(self.rfile.read(length))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self._send_json(400, {"detail": "invalid json"})
                    return
                stub.state.create_run_calls += 1
                stub.state.input_present = bool(payload.get("input"))
                history = payload.get("conversation_history")
                stub.state.conversation_history_count = len(history) if isinstance(history, list) else 0
                stub.state.session_id_present = bool(payload.get("session_id"))
                stub.state.model = str(payload.get("model") or "")[:128]
                self._send_json(200, {"run_id": f"compose-smoke-run-{stub.state.create_run_calls}"})

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/health":
                    self._send_json(200, {"status": "ok"})
                    return
                if not self._authorized():
                    self._send_json(401, {"detail": "unauthorized"})
                    return
                if not self.path.startswith("/v1/runs/compose-smoke-run-") or not self.path.endswith("/events"):
                    self._send_json(404, {"detail": "not found"})
                    return
                stub.state.stream_calls += 1
                event = json.dumps(
                    {"event": "run.completed", "output": HERMES_GUARD_SMOKE_REPLY},
                    ensure_ascii=False,
                )
                body = f"data: {event}\n\n".encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def smoke_environment() -> dict[str, str]:
    ports = {
        name: str(available_port())
        for name in ("backend", "frontend", "report", "pdf", "document", "postgres", "redis")
    }
    suffix = secrets.token_urlsafe(24)
    return {
        **os.environ,
        "SIQ_DEPLOYMENT_PROFILE": "production",
        "SIQ_CORS_ALLOW_ORIGINS": f"http://127.0.0.1:{ports['frontend']}",
        "SIQ_COMPOSE_BIND_HOST": "127.0.0.1",
        "SIQ_BACKEND_PORT": ports["backend"],
        "SIQ_FRONTEND_PORT": ports["frontend"],
        "SIQ_REPORT_FINDER_PORT": ports["report"],
        "SIQ_PDF2MD_PORT": ports["pdf"],
        "SIQ_DOCUMENT_PARSER_PORT": ports["document"],
        "SIQ_POSTGRES_PORT": ports["postgres"],
        "SIQ_REDIS_PORT": ports["redis"],
        "SIQ_POSTGRES_DATA_VOLUME": "postgres_data",
        "SIQ_POSTGRES_IMAGE": "postgres:16-alpine",
        "MINERU_API_URL": "http://127.0.0.1:9",
        "VLM_API_URL": "http://127.0.0.1:9",
        "SIQ_AGENT_MEMORY_ENABLED": "true",
        "SIQ_AGENT_MEMORY_VECTOR_BACKEND": "milvus",
        "SIQ_AGENT_MEMORY_PGVECTOR_ENABLED": "false",
        "SIQ_AGENT_MEMORY_WRITE_ENABLED": "true",
        "SIQ_AGENT_MEMORY_RETRIEVAL_ENABLED": "false",
        "SIQ_AGENT_MEMORY_EXTRACTION_ENABLED": "false",
        "SIQ_AGENT_MEMORY_RERANK_ENABLED": "false",
        "SIQ_AGENT_MEMORY_MILVUS_COLLECTION": "siq_agent_memory_active",
        "SIQ_AGENT_MEMORY_EMBEDDING_BASE_URL": "http://127.0.0.1:9",
        "SIQ_MILVUS_HOST": "127.0.0.1",
        "SIQ_MILVUS_PORT": "9",
        "HERMES_API_KEY": f"hermes-{suffix}",
        "NO_PROXY": (
            "127.0.0.1,localhost,host.docker.internal,postgres,redis,pdf-parser,"
            "document-parser,report-finder"
        ),
        "no_proxy": (
            "127.0.0.1,localhost,host.docker.internal,postgres,redis,pdf-parser,"
            "document-parser,report-finder"
        ),
        "POSTGRES_PASSWORD": f"pg-{suffix}",
        "SIQ_AUTH_SECRET_KEY": f"auth-{suffix}",
        "SIQ_AUTH_COOKIE_MODE": "1",
        "SIQ_AUTH_COOKIE_SECURE": "0",
        "SIQ_AUTH_COOKIE_SAMESITE": "lax",
        "SIQ_SOURCE_TOKEN_SECRET": f"source-{suffix}",
        "PDF2MD_ACCESS_TOKEN": f"pdf-{suffix}",
        "SIQ_DOCUMENT_PARSER_ACCESS_TOKEN": f"document-{suffix}",
        "SIQ_INITIAL_ADMIN_PASSWORD": f"admin-{suffix}",
        "GRAFANA_PASSWORD": f"grafana-{suffix}",
    }


def compose_command(project: str, *args: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        project,
        "--file",
        str(COMPOSE_FILE),
        "--env-file",
        str(ENV_FILE),
        *args,
    ]


def browser_smoke_environment(
    env: dict[str, str],
    *,
    project: str,
    web_url: str,
    backend_url: str,
    username: str,
    password: str,
    output_dir: str,
) -> dict[str, str]:
    return {
        **env,
        "PLAYWRIGHT_BASE_URL": web_url,
        "SIQ_E2E_BACKEND_URL": backend_url,
        "SIQ_E2E_COMPOSE_PROJECT": project,
        "SIQ_E2E_COMPOSE_FILE": str(COMPOSE_FILE),
        "SIQ_E2E_COMPOSE_ENV_FILE": str(ENV_FILE),
        "SIQ_PRODUCTION_COMPOSE_BROWSER_SMOKE": "1",
        "SIQ_E2E_USERNAME": username,
        "SIQ_E2E_PASSWORD": password,
        "SIQ_E2E_OUTPUT_DIR": output_dir,
    }


def run_browser_smoke(
    *,
    env: dict[str, str],
    project: str,
    web_url: str,
    backend_url: str,
    username: str,
    password: str,
) -> float:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="siq-production-browser-smoke-") as output_dir:
        browser_env = browser_smoke_environment(
            env,
            project=project,
            web_url=web_url,
            backend_url=backend_url,
            username=username,
            password=password,
            output_dir=output_dir,
        )
        run(
            [
                "npm",
                "--prefix",
                "apps/web",
                "run",
                "e2e",
                "--",
                "--config",
                "playwright.production-compose.config.ts",
                "e2e/tests/production-compose-password-login.spec.ts",
            ],
            env=browser_env,
        )
    return round(time.monotonic() - started, 3)


def run(command: list[str], *, env: dict[str, str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def collect_response_headers(headers) -> dict[str, list[str]]:
    collected: dict[str, list[str]] = {}
    for name in headers.keys():
        key = name.lower()
        if key not in collected:
            collected[key] = list(headers.get_all(name) or [])
    return collected


def cookie_was_cleared(set_cookie_headers: list[str], cookie_name: str) -> bool:
    for header in set_cookie_headers:
        cookie = SimpleCookie()
        try:
            cookie.load(header)
        except CookieError:
            continue
        morsel = cookie.get(cookie_name)
        if morsel is not None and morsel["max-age"] == "0" and morsel["path"] == "/":
            return True
    return False


def response_cookie(set_cookie_headers: list[str], cookie_name: str) -> Morsel | None:
    for header in set_cookie_headers:
        cookie = SimpleCookie()
        try:
            cookie.load(header)
        except CookieError:
            continue
        morsel = cookie.get(cookie_name)
        if morsel is not None:
            return morsel
    return None


def request_json(
    url: str,
    token: str | None = None,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload: dict | None = None,
    timeout: float = 15,
) -> tuple[int, dict, dict[str, list[str]]]:
    request_headers = dict(headers or {})
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    if body is not None:
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, headers=request_headers, method=method, data=body)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read()), collect_response_headers(response.headers)
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"detail": "non-json HTTP error response"}
        return exc.code, payload, collect_response_headers(exc.headers)


def redacted_report_json(report: dict, *, secret_values: tuple[str, ...] = ()) -> str:
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    for secret_value in secret_values:
        if secret_value:
            rendered = rendered.replace(secret_value, "[REDACTED]")
    return rendered + "\n"


def wait_for_health(base_url: str, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            status, _payload, _headers = request_json(f"{base_url}/health")
            if status == 200:
                return
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = exc
        time.sleep(1)
    raise RuntimeError(f"API did not become healthy after restart: {last_error}")


def assert_smoke_contract(report: dict) -> None:
    if report["health_status"] != 200:
        raise AssertionError(f"API health failed: {report['health_status']}")
    if report["unauthenticated_status"] != 401:
        raise AssertionError(f"protected route did not reject anonymous access: {report['unauthenticated_status']}")
    if report["web_login_status"] != 200:
        raise AssertionError(f"Web proxy password login failed: {report['web_login_status']}")
    if report["web_login_username"] != "compose-smoke":
        raise AssertionError("password login response did not resolve the PostgreSQL smoke user")
    if report["web_login_role"] != "analyst":
        raise AssertionError("password login response did not preserve the analyst role")
    if report["web_login_token_type"] != "bearer" or not report["web_login_access_token_returned"]:
        raise AssertionError("password login response did not return the access-token contract")
    if not report["web_login_access_cookie_set"] or not report["web_login_access_cookie_httponly"]:
        raise AssertionError("password login did not set the HttpOnly access cookie")
    if not report["web_login_csrf_cookie_set"] or report["web_login_csrf_cookie_httponly"]:
        raise AssertionError("password login did not set the client-readable CSRF cookie")
    if not report["web_login_cookie_paths_are_root"]:
        raise AssertionError("password login cookies did not use the root path")
    if report["web_cookie_authenticated_after_restart_status"] != 200:
        raise AssertionError(
            "Web proxy Cookie auth did not survive API restart: "
            f"{report['web_cookie_authenticated_after_restart_status']}"
        )
    if report["web_cookie_authenticated_after_restart_username"] != "compose-smoke":
        raise AssertionError("Cookie-authenticated response changed user identity after API restart")
    if report["public_table_count"] < 12:
        raise AssertionError(f"siq_app schema is incomplete: {report['public_table_count']} public tables")
    if report["required_index_count"] != 2:
        raise AssertionError(f"required app indexes are incomplete: {report['required_index_count']}")
    if report["smoke_user_count_after_restart"] != 1:
        raise AssertionError("PostgreSQL smoke user was not durable across API restart")
    if report["web_cookie_authenticated_status"] != 200:
        raise AssertionError("Web proxy did not accept the Cookie-authenticated protected request")
    if report["web_cookie_authenticated_username"] != "compose-smoke":
        raise AssertionError("Web proxy Cookie auth did not resolve the PostgreSQL smoke user")
    if report["web_cookie_csrf_missing_status"] != 403:
        raise AssertionError("Web proxy did not reject a Cookie-authenticated mutation without CSRF")
    if report["web_cookie_csrf_valid_status"] != 200:
        raise AssertionError("Web proxy did not accept the Cookie-authenticated mutation with CSRF")
    if report["web_cookie_csrf_valid_message"] != "\u767b\u51fa\u6210\u529f":
        raise AssertionError("Web proxy Cookie-authenticated logout returned an unexpected response")
    if not report["web_logout_access_cookie_cleared"]:
        raise AssertionError("Web proxy logout did not clear the access cookie")
    if not report["web_logout_csrf_cookie_cleared"]:
        raise AssertionError("Web proxy logout did not clear the CSRF cookie")
    if report["browser_playwright_status"] != "passed":
        raise AssertionError("Playwright Chromium password-login UX smoke did not pass")
    if report["chat_http_status"] != 200:
        raise AssertionError(f"authenticated /api/chat failed: {report['chat_http_status']}")
    if not report["chat_guardrail_blocked"]:
        raise AssertionError("financial chat response was not blocked by the runtime guard")
    if not report["chat_audit_trace_returned"]:
        raise AssertionError("guarded chat response did not return an audit trace id")
    if report["chat_history_status"] != 200 or report["chat_history_message_count"] != 2:
        raise AssertionError("guarded user/assistant messages were not available from DB-backed history")
    if not report["chat_history_assistant_audit_linked"]:
        raise AssertionError("DB-backed assistant history did not retain the audit trace link")
    if report["chat_audit_status"] != 200 or not report["chat_audit_guardrail_blocked"]:
        raise AssertionError("answer audit trace did not preserve the blocked guard result")
    if report["chat_db_message_count"] != 2 or report["chat_db_audited_assistant_count"] != 1:
        raise AssertionError("PostgreSQL chat history/audit-link counts are incomplete")
    if report["agent_memory_db_message_count"] != 2 or report["agent_memory_db_session_count"] != 1:
        raise AssertionError("PostgreSQL Agent Memory mirror counts are incomplete")
    if report["hermes_gateway_mode"] == "controlled_gateway_stub":
        gateway = report["hermes_gateway_contract"]
        if gateway["create_run_calls"] != 1 or gateway["stream_calls"] != 1:
            raise AssertionError("controlled Hermes gateway did not observe one complete run")
        if not gateway["authorization_verified"] or gateway["prompt_recorded"]:
            raise AssertionError("controlled Hermes gateway auth/redaction contract failed")
        if not str(report["chat_guardrail_reason"]).startswith("financial_"):
            raise AssertionError("controlled Hermes reply did not reach a financial guard")
        if report["chat_audit_violation_reason"] != "value_mismatch":
            raise AssertionError("controlled Hermes audit trace did not retain the value mismatch")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "artifacts/smoke/production-compose.json")
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument(
        "--live-hermes-runs-url",
        default="",
        help="Opt in to a live Hermes /v1/runs endpoint; token comes from SIQ_PRODUCTION_SMOKE_HERMES_LIVE_API_KEY.",
    )
    args = parser.parse_args(argv)
    env = smoke_environment()
    smoke_password = f"login-{secrets.token_urlsafe(24)}"
    env["SIQ_SMOKE_USER_PASSWORD"] = smoke_password
    project = f"siq-production-smoke-{os.getpid()}-{secrets.token_hex(3)}"
    base_url = f"http://127.0.0.1:{env['SIQ_BACKEND_PORT']}"
    web_url = f"http://127.0.0.1:{env['SIQ_FRONTEND_PORT']}"
    live_runs_url = str(args.live_hermes_runs_url or "").strip().rstrip("/")
    gateway_stub: HermesGatewayContractStub | None = None
    if live_runs_url:
        live_token = str(os.getenv("SIQ_PRODUCTION_SMOKE_HERMES_LIVE_API_KEY") or "").strip()
        if not live_token:
            parser.error(
                "SIQ_PRODUCTION_SMOKE_HERMES_LIVE_API_KEY is required with --live-hermes-runs-url"
            )
        env["HERMES_API_KEY"] = live_token
        env["SIQ_HERMES_ASSISTANT_RUNS_URL"] = live_runs_url
        hermes_gateway_mode = "opt_in_live_gateway_model"
        not_covered = list(NOT_COVERED)
    else:
        gateway_stub = HermesGatewayContractStub(env["HERMES_API_KEY"])
        gateway_stub.start()
        env["SIQ_HERMES_ASSISTANT_RUNS_URL"] = (
            f"http://host.docker.internal:{gateway_stub.port}/v1/runs"
        )
        hermes_gateway_mode = "controlled_gateway_stub"
        not_covered = [*NOT_COVERED, "hermes_live_model_inference"]
    report = {
        "schema_version": "siq_production_compose_smoke_v4",
        "not_covered": not_covered,
        "hermes_gateway_mode": hermes_gateway_mode,
        "hermes_evidence_scope": (
            "live_gateway_model" if live_runs_url else "orchestration_contract_with_controlled_stub"
        ),
    }
    started = time.monotonic()

    try:
        up_args = ["up"]
        if not args.no_build:
            up_args.append("--build")
        up_args.extend(["--detach", "--wait", "web"])
        result = run(compose_command(project, *up_args), env=env)
        report["compose_up"] = result.returncode == 0

        health_status, _health, _health_headers = request_json(f"{base_url}/health")
        unauthenticated_status, _unauthenticated, _unauthenticated_headers = request_json(
            f"{web_url}/api/workspace/summary"
        )

        create_user = (
            "import os; from database import engine; from services.auth_service import AuthService,User,UserRole; "
            "from sqlmodel import Session,select; s=Session(engine); "
            "u=s.exec(select(User).where(User.username=='compose-smoke')).first(); "
            "u=u or User(username='compose-smoke',email='compose-smoke@example.test',hashed_password='',"
            "full_name='Compose Smoke',role=UserRole.ANALYST,approval_status='approved',is_active=True); "
            "u.hashed_password=AuthService.hash_password(os.environ['SIQ_SMOKE_USER_PASSWORD']); "
            "u.role=UserRole.ANALYST; u.approval_status='approved'; u.is_active=True; "
            "s.add(u); s.commit(); s.close()"
        )
        run(
            compose_command(
                project,
                "exec",
                "-T",
                "-e",
                "SIQ_SMOKE_USER_PASSWORD",
                "api",
                "python",
                "-c",
                create_user,
            ),
            env=env,
        )
        web_origin = web_url
        web_login_status, web_login, web_login_headers = request_json(
            f"{web_url}/api/auth/login",
            method="POST",
            headers={"Origin": web_origin, "User-Agent": "siq-production-compose-smoke"},
            payload={"username": "compose-smoke", "password": smoke_password},
        )
        login_set_cookie_headers = web_login_headers.get("set-cookie", [])
        access_cookie = response_cookie(login_set_cookie_headers, "siq_access_token")
        csrf_cookie = response_cookie(login_set_cookie_headers, "siq_csrf_token")
        cookie_header = "; ".join(
            f"{name}={morsel.value}"
            for name, morsel in (
                ("siq_access_token", access_cookie),
                ("siq_csrf_token", csrf_cookie),
            )
            if morsel is not None
        )
        csrf_token = csrf_cookie.value if csrf_cookie is not None else ""
        web_cookie_authenticated_status, web_cookie_authenticated, _web_cookie_headers = request_json(
            f"{web_url}/api/workspace/summary",
            headers={"Cookie": cookie_header, "Origin": web_origin},
        )

        run(compose_command(project, "restart", "api"), env=env)
        wait_for_health(base_url)
        (
            web_cookie_authenticated_after_restart_status,
            web_cookie_authenticated_after_restart,
            _web_cookie_after_restart_headers,
        ) = request_json(
            f"{web_url}/api/workspace/summary",
            headers={"Cookie": cookie_header, "Origin": web_origin},
        )

        chat_status, chat_payload, _chat_headers = request_json(
            f"{web_url}/api/chat",
            method="POST",
            headers={
                "Cookie": cookie_header,
                "Origin": web_origin,
                "X-CSRF-Token": csrf_token,
            },
            payload={
                "message": "工商银行 2025 年营业收入是多少？",
                "context": {
                    "research_identity": {
                        "market": "HK",
                        "company_id": "HK:01398",
                        "filing_id": "HK:01398:2025-annual",
                        "parse_run_id": "parse-hk-01398",
                    },
                    "company": {"name": "工商银行", "code": "01398"},
                },
            },
            timeout=180,
        )
        audit_trace_id = str(chat_payload.get("audit_trace_id") or "")
        guarded_reply = str(chat_payload.get("reply") or "")
        history_status, history_payload, _history_headers = request_json(
            f"{web_url}/api/chat/history?limit=10",
            headers={"Cookie": cookie_header, "Origin": web_origin},
        )
        audit_status, audit_payload, _audit_headers = request_json(
            f"{web_url}/api/chat/audit-traces/{audit_trace_id}",
            headers={"Cookie": cookie_header, "Origin": web_origin},
        )
        history_messages = history_payload.get("messages") or []
        assistant_history = next(
            (item for item in reversed(history_messages) if item.get("role") == "assistant"),
            {},
        )
        audit_trace = audit_payload.get("trace") or {}
        audit_guardrail = audit_trace.get("guardrail_result") or {}
        violations = (audit_trace.get("claim_verifier_result") or {}).get("violations") or []

        web_cookie_csrf_missing_status, _web_cookie_csrf_missing, _web_cookie_csrf_missing_headers = request_json(
            f"{web_url}/api/auth/logout",
            method="POST",
            headers={"Cookie": cookie_header, "Origin": web_origin},
        )
        web_cookie_csrf_valid_status, web_cookie_csrf_valid, web_cookie_csrf_valid_headers = request_json(
            f"{web_url}/api/auth/logout",
            method="POST",
            headers={
                "Cookie": cookie_header,
                "Origin": web_origin,
                "X-CSRF-Token": csrf_token,
            },
        )
        logout_set_cookie_headers = web_cookie_csrf_valid_headers.get("set-cookie", [])

        browser_duration_seconds = run_browser_smoke(
            env=env,
            project=project,
            web_url=web_url,
            backend_url=base_url,
            username="compose-smoke",
            password=smoke_password,
        )

        db_probe = run(
            compose_command(
                project,
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "postgres",
                "-d",
                "siq_app",
                "-Atc",
                "select count(*) from information_schema.tables where table_schema='public'; "
                "select count(*) from pg_indexes where schemaname='public' and indexname in "
                "('idx_chatmessage_session_created_at','idx_usage_events_user_type_date'); "
                "select count(*) from users where username='compose-smoke' and approval_status='approved' and is_active=true; "
                "select count(*) from chatmessage; "
                "select count(*) from chatmessage where role='assistant' and audit_trace_id is not null; "
                "select count(*) from agent_memory.messages; "
                "select count(*) from agent_memory.sessions;",
            ),
            env=env,
        ).stdout.strip().splitlines()
        report.update(
            {
                "health_status": health_status,
                "unauthenticated_status": unauthenticated_status,
                "web_login_status": web_login_status,
                "web_login_username": web_login.get("user", {}).get("username"),
                "web_login_role": web_login.get("user", {}).get("role"),
                "web_login_token_type": web_login.get("token_type"),
                "web_login_access_token_returned": bool(web_login.get("access_token")),
                "web_login_access_cookie_set": access_cookie is not None,
                "web_login_access_cookie_httponly": bool(access_cookie and access_cookie["httponly"]),
                "web_login_csrf_cookie_set": csrf_cookie is not None,
                "web_login_csrf_cookie_httponly": bool(csrf_cookie and csrf_cookie["httponly"]),
                "web_login_cookie_paths_are_root": bool(
                    access_cookie
                    and csrf_cookie
                    and access_cookie["path"] == "/"
                    and csrf_cookie["path"] == "/"
                ),
                "public_table_count": int(db_probe[-7]),
                "required_index_count": int(db_probe[-6]),
                "smoke_user_count_after_restart": int(db_probe[-5]),
                "chat_db_message_count": int(db_probe[-4]),
                "chat_db_audited_assistant_count": int(db_probe[-3]),
                "agent_memory_db_message_count": int(db_probe[-2]),
                "agent_memory_db_session_count": int(db_probe[-1]),
                "web_cookie_authenticated_status": web_cookie_authenticated_status,
                "web_cookie_authenticated_username": web_cookie_authenticated.get("user", {}).get("username"),
                "web_cookie_authenticated_after_restart_status": web_cookie_authenticated_after_restart_status,
                "web_cookie_authenticated_after_restart_username": web_cookie_authenticated_after_restart.get(
                    "user", {}
                ).get("username"),
                "web_cookie_csrf_missing_status": web_cookie_csrf_missing_status,
                "web_cookie_csrf_valid_status": web_cookie_csrf_valid_status,
                "web_cookie_csrf_valid_message": web_cookie_csrf_valid.get("message"),
                "web_logout_access_cookie_cleared": cookie_was_cleared(
                    logout_set_cookie_headers, "siq_access_token"
                ),
                "web_logout_csrf_cookie_cleared": cookie_was_cleared(
                    logout_set_cookie_headers, "siq_csrf_token"
                ),
                "browser_playwright_status": "passed",
                "browser_playwright_duration_seconds": browser_duration_seconds,
                "chat_http_status": chat_status,
                "chat_guardrail_blocked": "guardrail_status=blocked" in guarded_reply,
                "chat_guardrail_reason": str(audit_guardrail.get("reason") or ""),
                "chat_audit_trace_returned": bool(audit_trace_id),
                "chat_history_status": history_status,
                "chat_history_message_count": len(history_messages),
                "chat_history_assistant_audit_linked": bool(
                    audit_trace_id and assistant_history.get("audit_trace_id") == audit_trace_id
                ),
                "chat_audit_status": audit_status,
                "chat_audit_guardrail_blocked": bool(audit_guardrail.get("blocked")),
                "chat_audit_violation_reason": (
                    str(violations[0].get("reason") or "") if violations else ""
                ),
                "hermes_gateway_contract": (
                    gateway_stub.state.redacted_snapshot() if gateway_stub else None
                ),
                "duration_seconds": round(time.monotonic() - started, 3),
            }
        )
        assert_smoke_contract(report)
        if smoke_password in json.dumps(report, ensure_ascii=False):
            raise AssertionError("smoke password leaked into the report")
        report["status"] = "passed"
    except Exception as exc:
        report.update({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)})
        for diagnostic in (("ps", "--all"), ("logs", "--no-color", "--tail=200")):
            output = run(compose_command(project, *diagnostic), env=env, check=False).stdout
            print(output, file=sys.stderr)
        raise
    finally:
        run(compose_command(project, "down", "--volumes", "--remove-orphans"), env=env, check=False)
        if gateway_stub is not None:
            gateway_stub.close()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            redacted_report_json(report, secret_values=(smoke_password,)),
            encoding="utf-8",
        )

    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
