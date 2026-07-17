#!/usr/bin/env python3
"""Health probe for direct-image and OpenShell-managed siq_analysis runtimes."""

from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path

PROC_ROOT = Path("/proc")
EXPECTED_SUPERVISOR_CMDLINE = (b"/opt/openshell/bin/openshell-sandbox",)
HERMES_BIN = b"/opt/siq/hermes/venv/bin/hermes"
EXPECTED_HERMES_CMDLINES = {
    (b"/opt/siq/hermes/venv/bin/python", HERMES_BIN, b"gateway", b"run"),
    (b"/opt/siq/hermes/venv/bin/python3", HERMES_BIN, b"gateway", b"run"),
    (HERMES_BIN, b"gateway", b"run"),
}
_SANDBOX_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,127}\Z")
_SANDBOX_ID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)


def _cmdline(path: Path) -> tuple[bytes, ...]:
    try:
        raw = path.read_bytes()
    except OSError:
        return ()
    if not raw or len(raw) > 4096:
        return ()
    return tuple(part for part in raw.split(b"\0") if part)


def _openshell_managed() -> bool:
    name = os.environ.get("OPENSHELL_SANDBOX", "").strip()
    sandbox_id = os.environ.get("OPENSHELL_SANDBOX_ID", "").strip()
    if not name and not sandbox_id:
        return False
    if _SANDBOX_NAME_RE.fullmatch(name) is None or _SANDBOX_ID_RE.fullmatch(sandbox_id) is None:
        raise SystemExit("OpenShell sandbox identity is invalid")
    return True


def _check_openshell_process_contract() -> None:
    # Docker executes HEALTHCHECK in the outer container network namespace,
    # while OpenShell places Hermes in its sandbox namespace. Business
    # readiness is therefore attested by the lifecycle's authenticated forward
    # and sandbox-exec probes; Docker health can only attest exact process
    # liveness without producing a permanently false loopback failure.
    if _cmdline(PROC_ROOT / "1" / "cmdline") != EXPECTED_SUPERVISOR_CMDLINE:
        raise SystemExit("OpenShell supervisor identity is invalid")
    hermes_processes = 0
    try:
        entries = tuple(PROC_ROOT.iterdir())
    except OSError as exc:
        raise SystemExit("OpenShell process table is unavailable") from exc
    for entry in entries:
        if entry.name.isdecimal() and _cmdline(entry / "cmdline") in EXPECTED_HERMES_CMDLINES:
            hermes_processes += 1
    if hermes_processes != 1:
        raise SystemExit("OpenShell Hermes process identity is invalid")


def _check_direct_image_http(key: str) -> None:
    request = urllib.request.Request(
        "http://127.0.0.1:28651/health",
        headers={"Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        payload = json.load(response)
    if response.status != 200 or payload.get("status") != "ok":
        raise SystemExit("Hermes health response is invalid")


def main() -> None:
    key = os.environ.get("API_SERVER_KEY", "")
    if len(key) < 32:
        raise SystemExit("API_SERVER_KEY is unavailable")
    if os.environ.get("API_SERVER_HOST") != "127.0.0.1" or os.environ.get(
        "API_SERVER_PORT"
    ) != "28651":
        raise SystemExit("Hermes listener contract is invalid")
    if _openshell_managed():
        _check_openshell_process_contract()
        return
    _check_direct_image_http(key)


if __name__ == "__main__":
    main()
