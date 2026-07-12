#!/usr/bin/env python3
"""Run the production Web image against a deterministic mock API.

This proves the image, Nginx runtime substitution, static SPA serving, and
proxy header/cookie behavior. It deliberately does not claim FastAPI auth,
database, or a production reverse-proxy/TLS deployment has been validated.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "apps/web/container-smoke/compose.yml"


def _available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _compose_command(project_name: str, *args: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        project_name,
        "--file",
        str(COMPOSE_FILE),
        *args,
    ]


def _wait_for_web(base_url: str, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/healthz", timeout=3) as response:
                if response.status == 200 and response.read() == b"ok\n":
                    return
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
        time.sleep(1)
    raise RuntimeError(f"web container did not become ready: {last_error}")


def _request(request: urllib.request.Request) -> tuple[int, bytes, object]:
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, response.read(), response.headers


def _request_allow_http_error(request: urllib.request.Request) -> tuple[int, bytes, object]:
    try:
        return _request(request)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers


def _assert_smoke_contract(base_url: str) -> None:
    status, body, headers = _request(urllib.request.Request(f"{base_url}/"))
    if status != 200 or b'<div id="root"></div>' not in body:
        raise AssertionError("production container did not serve the built SPA index")
    if "text/html" not in headers.get("Content-Type", ""):
        raise AssertionError("SPA index did not use a text/html content type")

    status, body, headers = _request(urllib.request.Request(f"{base_url}/api/health"))
    health = json.loads(body)
    if status != 200 or health["path"] != "/health":
        raise AssertionError(f"/api/health rewrite failed: {health}")
    if headers.get("X-SIQ-Smoke-Upstream") != "mock-api":
        raise AssertionError("health response did not traverse the mock API")

    status, body, headers = _request(urllib.request.Request(f"{base_url}/api/pdf/health"))
    pdf_health = json.loads(body)
    if status != 200 or pdf_health["path"] != "/api/pdf/health":
        raise AssertionError(f"protected /api/pdf route changed unexpectedly: {pdf_health}")
    if headers.get("X-SIQ-Smoke-Upstream") != "mock-api":
        raise AssertionError("protected /api/pdf route did not traverse the backend")

    status, body, headers = _request(urllib.request.Request(f"{base_url}/api/finder-only"))
    finder = json.loads(body)
    if status != 200 or finder["path"] != "/finder-only":
        raise AssertionError(f"finder fallback rewrite failed: {finder}")
    if headers.get("X-SIQ-Smoke-Upstream") != "mock-finder":
        raise AssertionError("generic /api fallback did not traverse the report finder")

    payload = json.dumps({"message": "container-smoke"}).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/api/auth/session?rotate=1",
        data=payload,
        method="POST",
        headers={
            "Authorization": "Bearer smoke-access-token",
            "Content-Type": "application/json",
            "Cookie": "siq_access_token=session-token; siq_csrf_token=csrf-token",
            "X-CSRF-Token": "csrf-token",
        },
    )
    status, body, headers = _request(request)
    proxied = json.loads(body)
    expected = {
        "method": "POST",
        "path": "/api/auth/session?rotate=1",
        "authorization": "Bearer smoke-access-token",
        "cookie": "siq_access_token=session-token; siq_csrf_token=csrf-token",
        "csrf_token": "csrf-token",
        "forwarded_proto": "http",
        "body": payload.decode("utf-8"),
    }
    if status != 200 or proxied != expected:
        raise AssertionError(f"auth/cookie/CSRF proxy contract failed: {proxied}")
    set_cookies = headers.get_all("Set-Cookie") or []
    if not any("siq_access_token=rotated-session" in value for value in set_cookies):
        raise AssertionError("upstream Set-Cookie was not returned through nginx")

    status, _body, headers = _request_allow_http_error(urllib.request.Request(f"{base_url}/pdfapi/tasks"))
    if status != 404:
        raise AssertionError(f"legacy public /pdfapi unexpectedly reachable: status={status}")
    if headers.get("X-SIQ-Smoke-Upstream"):
        raise AssertionError("legacy public /pdfapi request reached an upstream")


def main() -> int:
    port = _available_port()
    project_name = f"siq-web-smoke-{os.getpid()}"
    environment = os.environ.copy()
    environment["SIQ_WEB_CONTAINER_SMOKE_PORT"] = str(port)
    base_url = f"http://127.0.0.1:{port}"

    try:
        subprocess.run(
            _compose_command(project_name, "up", "--build", "--detach", "--wait"),
            cwd=REPO_ROOT,
            env=environment,
            check=True,
        )
        _wait_for_web(base_url)
        _assert_smoke_contract(base_url)
    except Exception:
        subprocess.run(
            _compose_command(project_name, "ps", "--all"),
            cwd=REPO_ROOT,
            env=environment,
            check=False,
        )
        subprocess.run(
            _compose_command(project_name, "logs", "--no-color"),
            cwd=REPO_ROOT,
            env=environment,
            check=False,
        )
        raise
    finally:
        subprocess.run(
            _compose_command(project_name, "down", "--volumes", "--remove-orphans"),
            cwd=REPO_ROOT,
            env=environment,
            check=False,
        )

    print("Web container smoke passed: SPA, protected API proxy, and /pdfapi closed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
