#!/usr/bin/env python3
"""Run a disposable TLS -> production Web -> API Cookie/CSRF smoke."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_SMOKE_PATH = REPO_ROOT / "scripts" / "maintenance" / "run_production_compose_smoke.py"
DEFAULT_OUTPUT = REPO_ROOT / "artifacts" / "eval-runs" / "local" / "production-tls-proxy-smoke.json"
TLS_IMAGE = "nginxinc/nginx-unprivileged:1.27-alpine"


def _compose_smoke_module():
    name = "siq_production_compose_smoke_for_tls"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, COMPOSE_SMOKE_PATH)
    if not spec or not spec.loader:
        raise RuntimeError("cannot load production compose smoke helpers")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def tls_smoke_environment(base: dict[str, str], tls_origin: str) -> dict[str, str]:
    return {
        **base,
        "SIQ_CORS_ALLOW_ORIGINS": tls_origin,
        "SIQ_AUTH_CSRF_ALLOWED_ORIGINS": tls_origin,
        "SIQ_AUTH_COOKIE_SECURE": "1",
        "SIQ_AUTH_COOKIE_SAMESITE": "lax",
    }


def tls_proxy_config(*, listen_port: int, upstream_port: int) -> str:
    return f"""pid /tmp/nginx.pid;
worker_processes 1;
events {{ worker_connections 128; }}
http {{
  access_log /dev/stdout;
  error_log /dev/stderr warn;
  client_body_temp_path /tmp/client-body;
  proxy_temp_path /tmp/proxy;
  fastcgi_temp_path /tmp/fastcgi;
  uwsgi_temp_path /tmp/uwsgi;
  scgi_temp_path /tmp/scgi;
  server_tokens off;
  server {{
    listen 127.0.0.1:{listen_port} ssl;
    ssl_certificate /etc/nginx/tls/cert.pem;
    ssl_certificate_key /etc/nginx/tls/key.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    location / {{
      proxy_pass http://127.0.0.1:{upstream_port};
      proxy_http_version 1.1;
      proxy_set_header Host $http_host;
      proxy_set_header X-Forwarded-Proto https;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_buffering off;
    }}
  }}
}}
"""


def generate_certificate(directory: Path) -> tuple[Path, Path]:
    certificate = directory / "cert.pem"
    private_key = directory / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=127.0.0.1",
            "-addext",
            "subjectAltName=IP:127.0.0.1,DNS:localhost",
            "-keyout",
            str(private_key),
            "-out",
            str(certificate),
        ],
        check=True,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    certificate.chmod(0o644)
    private_key.chmod(0o644)
    return certificate, private_key


def json_request(
    opener,
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload: dict | None = None,
) -> tuple[int, dict, dict[str, list[str]]]:
    compose_smoke = _compose_smoke_module()
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request_headers = dict(headers or {})
    if body is not None:
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, method=method, headers=request_headers, data=body)
    try:
        with opener.open(request, timeout=20) as response:
            return response.status, json.loads(response.read()), compose_smoke.collect_response_headers(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read()), compose_smoke.collect_response_headers(exc.headers)


def assert_tls_contract(report: dict) -> None:
    expected = {
        "https_login_status": 200,
        "https_cookie_authenticated_status": 200,
        "https_csrf_missing_status": 403,
        "https_csrf_wrong_origin_status": 403,
        "https_csrf_valid_status": 200,
        "http_cookiejar_status": 401,
    }
    for field, value in expected.items():
        if report.get(field) != value:
            raise AssertionError(f"{field} expected {value}, got {report.get(field)}")
    for field in (
        "access_cookie_secure",
        "access_cookie_httponly",
        "csrf_cookie_secure",
        "cookie_paths_are_root",
        "logout_access_cookie_cleared",
        "logout_csrf_cookie_cleared",
        "temporary_self_signed_certificate",
        "tls_proxy_read_only",
        "tls_proxy_capabilities_dropped",
    ):
        if not report.get(field):
            raise AssertionError(f"{field} was not proven")
    if report.get("csrf_cookie_httponly"):
        raise AssertionError("CSRF cookie must remain client-readable")
    if report.get("production_compose_modified"):
        raise AssertionError("test TLS smoke must not modify production compose")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args(argv)
    compose_smoke = _compose_smoke_module()
    tls_port = compose_smoke.available_port()
    tls_origin = f"https://127.0.0.1:{tls_port}"
    env = tls_smoke_environment(compose_smoke.smoke_environment(), tls_origin)
    project = f"siq-production-tls-smoke-{os.getpid()}-{os.urandom(3).hex()}"
    proxy_name = f"{project}-proxy"
    web_url = f"http://127.0.0.1:{env['SIQ_FRONTEND_PORT']}"
    password = f"tls-{os.urandom(24).hex()}"
    env["SIQ_SMOKE_USER_PASSWORD"] = password
    report: dict = {
        "schema_version": "siq_production_tls_proxy_smoke_v1",
        "status": "failed",
        "tls_origin": tls_origin,
        "temporary_self_signed_certificate": True,
        "production_compose_modified": False,
        "not_covered": ["production_ca_certificate", "external_load_balancer", "hsts_preload"],
    }
    started = time.monotonic()

    try:
        up_args = ["up"]
        if not args.no_build:
            up_args.append("--build")
        up_args.extend(["--detach", "--wait", "web"])
        compose_smoke.run(compose_smoke.compose_command(project, *up_args), env=env)

        create_user = (
            "import os; from database import engine; from services.auth_service import AuthService,User,UserRole; "
            "from sqlmodel import Session,select; s=Session(engine); "
            "u=s.exec(select(User).where(User.username=='tls-smoke')).first(); "
            "u=u or User(username='tls-smoke',email='tls-smoke@example.test',hashed_password='',"
            "full_name='TLS Smoke',role=UserRole.ANALYST,approval_status='approved',is_active=True); "
            "u.hashed_password=AuthService.hash_password(os.environ['SIQ_SMOKE_USER_PASSWORD']); "
            "u.role=UserRole.ANALYST; u.approval_status='approved'; u.is_active=True; "
            "s.add(u); s.commit(); s.close()"
        )
        compose_smoke.run(
            compose_smoke.compose_command(
                project, "exec", "-T", "-e", "SIQ_SMOKE_USER_PASSWORD", "api", "python", "-c", create_user
            ),
            env=env,
        )

        with tempfile.TemporaryDirectory(prefix="siq-tls-proxy-") as directory:
            tls_dir = Path(directory)
            certificate, private_key = generate_certificate(tls_dir)
            config = tls_dir / "nginx.conf"
            config.write_text(
                tls_proxy_config(listen_port=tls_port, upstream_port=int(env["SIQ_FRONTEND_PORT"])),
                encoding="utf-8",
            )
            compose_smoke.run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--rm",
                    "--name",
                    proxy_name,
                    "--network",
                    "host",
                    "--read-only",
                    "--tmpfs",
                    "/tmp:rw,noexec,nosuid,size=32m",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--volume",
                    f"{config}:/etc/nginx/nginx.conf:ro",
                    "--volume",
                    f"{certificate}:/etc/nginx/tls/cert.pem:ro",
                    "--volume",
                    f"{private_key}:/etc/nginx/tls/key.pem:ro",
                    TLS_IMAGE,
                    "nginx",
                    "-c",
                    "/etc/nginx/nginx.conf",
                    "-g",
                    "daemon off;",
                ],
                env=env,
            )
            context = ssl._create_unverified_context()
            cookie_jar = CookieJar()
            opener = urllib.request.build_opener(
                urllib.request.HTTPSHandler(context=context),
                urllib.request.HTTPCookieProcessor(cookie_jar),
            )
            compose_smoke.wait_for_health(f"http://127.0.0.1:{env['SIQ_BACKEND_PORT']}")
            deadline = time.monotonic() + 30
            while True:
                try:
                    https_health_status, _health, _headers = json_request(opener, f"{tls_origin}/api/health")
                    if https_health_status == 200:
                        break
                except (OSError, urllib.error.URLError):
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.5)

            login_status, login, login_headers = json_request(
                opener,
                f"{tls_origin}/api/auth/login",
                method="POST",
                headers={"Origin": tls_origin},
                payload={"username": "tls-smoke", "password": password},
            )
            set_cookie = login_headers.get("set-cookie", [])
            access = compose_smoke.response_cookie(set_cookie, "siq_access_token")
            csrf = compose_smoke.response_cookie(set_cookie, "siq_csrf_token")
            cookie_header = "; ".join(
                f"{name}={morsel.value}"
                for name, morsel in (("siq_access_token", access), ("siq_csrf_token", csrf))
                if morsel is not None
            )
            csrf_token = csrf.value if csrf else ""
            authenticated_status, authenticated, _headers = json_request(
                opener,
                f"{tls_origin}/api/workspace/summary",
                headers={"Cookie": cookie_header, "Origin": tls_origin},
            )
            http_status, _http, _headers = json_request(opener, f"{web_url}/api/workspace/summary")
            missing_status, _missing, _headers = json_request(
                opener,
                f"{tls_origin}/api/auth/logout",
                method="POST",
                headers={"Cookie": cookie_header, "Origin": tls_origin},
            )
            wrong_origin_status, _wrong, _headers = json_request(
                opener,
                f"{tls_origin}/api/auth/logout",
                method="POST",
                headers={
                    "Cookie": cookie_header,
                    "Origin": web_url,
                    "X-CSRF-Token": csrf_token,
                },
            )
            valid_status, valid, valid_headers = json_request(
                opener,
                f"{tls_origin}/api/auth/logout",
                method="POST",
                headers={
                    "Cookie": cookie_header,
                    "Origin": tls_origin,
                    "X-CSRF-Token": csrf_token,
                },
            )
            cleared = valid_headers.get("set-cookie", [])
            report.update(
                {
                    "https_health_status": https_health_status,
                    "https_login_status": login_status,
                    "https_login_username": login.get("user", {}).get("username"),
                    "https_cookie_authenticated_status": authenticated_status,
                    "https_cookie_authenticated_username": authenticated.get("user", {}).get("username"),
                    "https_csrf_missing_status": missing_status,
                    "https_csrf_wrong_origin_status": wrong_origin_status,
                    "https_csrf_valid_status": valid_status,
                    "https_csrf_valid_message": valid.get("message"),
                    "http_cookiejar_status": http_status,
                    "access_cookie_secure": bool(access and access["secure"]),
                    "access_cookie_httponly": bool(access and access["httponly"]),
                    "csrf_cookie_secure": bool(csrf and csrf["secure"]),
                    "csrf_cookie_httponly": bool(csrf and csrf["httponly"]),
                    "cookie_paths_are_root": bool(access and csrf and access["path"] == "/" and csrf["path"] == "/"),
                    "logout_access_cookie_cleared": compose_smoke.cookie_was_cleared(cleared, "siq_access_token"),
                    "logout_csrf_cookie_cleared": compose_smoke.cookie_was_cleared(cleared, "siq_csrf_token"),
                    "certificate_sha256": hashlib.sha256(certificate.read_bytes()).hexdigest(),
                    "tls_proxy_read_only": True,
                    "tls_proxy_capabilities_dropped": True,
                    "duration_seconds": round(time.monotonic() - started, 3),
                }
            )
            assert_tls_contract(report)
            report["status"] = "passed"
    finally:
        subprocess.run(
            ["docker", "rm", "--force", proxy_name],
            cwd=REPO_ROOT,
            env=env,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        compose_smoke.run(
            compose_smoke.compose_command(project, "down", "--volumes", "--remove-orphans"),
            env=env,
            check=False,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            compose_smoke.redacted_report_json(report, secret_values=(password,)),
            encoding="utf-8",
        )

    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
