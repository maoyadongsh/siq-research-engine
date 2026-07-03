#!/usr/bin/env python3
"""Build a temporary R0/R1 deal package and smoke the R1 agent workflow.

Default mode is dry-run only: it verifies the package contract, startup receipt,
preflight, and workflow payload without calling Hermes or writing reports.
Pass --real only when the target Hermes profile gateway is already healthy.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = PROJECT_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services import deal_store  # noqa: E402
from services import ic_agent_runtime  # noqa: E402


DEAL_ID = "DEAL-HERMES-SMOKE-001"
EVIDENCE_ID = "EVID-DEAL-HERMES-SMOKE-001-000001"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_ndjson(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def profile_api_server(profile_id: str) -> tuple[str, int]:
    profile_dir = subprocess.check_output(
        [str(PROJECT_ROOT / "scripts" / "hermes" / "profile_dir.sh"), profile_id],
        text=True,
    ).strip()
    config_path = Path(profile_dir) / "config.yaml"
    host = "127.0.0.1"
    port: int | None = None
    for line in config_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("host:"):
            host = stripped.split(":", 1)[1].strip() or host
        if stripped.startswith("port:") and port is None:
            port = int(stripped.split(":", 1)[1].strip())
    if port is None:
        raise RuntimeError(f"Unable to parse api_server port from {config_path}")
    return host, port


def gateway_health(host: str, port: int) -> dict[str, Any] | None:
    try:
        with urlopen(f"http://{host}:{port}/health", timeout=1) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError):
        return None


def start_gateway(profile_id: str, host: str, port: int, timeout_seconds: int) -> tuple[subprocess.Popen[bytes], Path]:
    if gateway_health(host, port):
        raise RuntimeError(f"Gateway is already healthy at http://{host}:{port}; refusing to replace it")
    runtime_root = Path(tempfile.mkdtemp(prefix=f"siq-r1-gateway-{profile_id}-"))
    log_path = runtime_root / "gateway.log"
    env = os.environ.copy()
    env.setdefault("HERMES_API_KEY", "local-smoke-token")
    env["SIQ_PROJECT_ROOT"] = str(PROJECT_ROOT)
    env["SIQ_HERMES_HOME"] = str(runtime_root / "home")
    env["SIQ_HERMES_PROFILES_ROOT"] = str(runtime_root / "home" / "profiles")
    process = subprocess.Popen(
        [str(PROJECT_ROOT / "scripts" / "hermes" / "run_gateway.sh"), profile_id],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=log_path.open("wb"),
        stderr=subprocess.STDOUT,
    )
    try:
        for _ in range(timeout_seconds):
            health = gateway_health(host, port)
            if health:
                print(f"Gateway health OK: {json.dumps(health, ensure_ascii=False)}")
                os.environ.setdefault("HERMES_API_KEY", env["HERMES_API_KEY"])
                return process, runtime_root
            if process.poll() is not None:
                tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
                raise RuntimeError("Gateway exited before health became ready:\n" + "\n".join(tail))
            time.sleep(1)
        tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
        raise RuntimeError(f"Gateway did not become healthy within {timeout_seconds}s:\n" + "\n".join(tail))
    except Exception:
        stop_gateway(process, runtime_root, keep=False)
        raise


def stop_gateway(process: subprocess.Popen[bytes] | None, runtime_root: Path | None, keep: bool) -> None:
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=8)
    if runtime_root and keep:
        print(f"Kept gateway runtime: {runtime_root}")
    elif runtime_root:
        shutil.rmtree(runtime_root, ignore_errors=True)


def build_smoke_package(wiki_root: Path, profile_id: str) -> Path:
    deal_store.create_deal_package(
        deal_id=DEAL_ID,
        company_name="Hermes Smoke Robotics",
        industry="robotics",
        stage="R1 smoke",
        wiki_root=wiki_root,
    )
    package_dir = wiki_root / "deals" / DEAL_ID
    write_ndjson(
        package_dir / "evidence" / "evidence_items.ndjson",
        [
            {
                "evidence_id": EVIDENCE_ID,
                "evidence_type": "verified",
                "dimension": "business",
                "claim": "Temporary smoke evidence for R1 workflow validation.",
                "quote": "This synthetic item exists only inside a temporary wiki root.",
                "document_id": "smoke-doc-1",
                "source_path": "parsed_documents/smoke-doc-1.md",
            }
        ],
    )
    write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v1",
            "deal_id": DEAL_ID,
            "agents": {
                profile_id: {
                    "receipt_id": f"startup-{profile_id}-R1-smoke",
                    "agent_id": profile_id,
                    "round_name": "R1",
                    "query": "Hermes Smoke Robotics",
                    "project_tag": DEAL_ID,
                    "shared_hits": 1,
                    "private_hits": 0,
                    "workspace_rules_read": ["SOUL.md", "AGENTS.md"],
                    "gaps": [],
                    "evidence_hits": [{"evidence_id": EVIDENCE_ID}],
                    "created_at": "2026-07-03T10:20:00+08:00",
                }
            },
        },
    )
    return package_dir


def print_result(title: str, payload: dict[str, Any]) -> None:
    compact = {
        "schema_version": payload.get("schema_version"),
        "deal_id": payload.get("deal_id"),
        "agent_id": payload.get("agent_id"),
        "dry_run": payload.get("dry_run"),
        "allowed": payload.get("allowed"),
        "would_queue": payload.get("would_queue"),
        "hermes_called": payload.get("hermes_called"),
        "report_written": payload.get("report_written"),
        "workflow_advanced": payload.get("workflow_advanced"),
        "preflight_status": payload.get("preflight_status"),
        "blocking_reasons": payload.get("blocking_reasons"),
        "warnings": payload.get("warnings"),
    }
    print(f"{title}:")
    print(json.dumps(compact, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="siq_ic_strategist", help="R1 profile to smoke")
    parser.add_argument("--real", action="store_true", help="Call Hermes and write the report into the temporary package")
    parser.add_argument("--require-gateway-health", action="store_true", help="Fail unless the target profile gateway /health is ready")
    parser.add_argument("--start-gateway", action="store_true", help="Start the target profile gateway with a temporary runtime")
    parser.add_argument("--gateway-timeout", type=int, default=45, help="Seconds to wait for --start-gateway health")
    parser.add_argument("--timeout", type=float, default=120.0, help="Hermes collect timeout for --real")
    parser.add_argument("--keep", action="store_true", help="Keep the temporary wiki root for inspection")
    parser.add_argument("--keep-gateway-runtime", action="store_true", help="Keep temporary gateway runtime when using --start-gateway")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    temp_root = Path(tempfile.mkdtemp(prefix="siq-r1-agent-smoke-"))
    wiki_root = temp_root / "wiki"
    gateway_process: subprocess.Popen[bytes] | None = None
    gateway_runtime: Path | None = None
    try:
        host, port = profile_api_server(args.profile)
        if args.real:
            os.environ.setdefault("HERMES_API_KEY", "local-smoke-token")
        if args.start_gateway:
            gateway_process, gateway_runtime = start_gateway(args.profile, host, port, args.gateway_timeout)
        elif args.real or args.require_gateway_health:
            health = gateway_health(host, port)
            if not health:
                raise RuntimeError(
                    f"Gateway health is not ready at http://{host}:{port}/health; "
                    "start it first or pass --start-gateway"
                )
            print(f"Gateway health OK: {json.dumps(health, ensure_ascii=False)}")

        build_smoke_package(wiki_root, args.profile)
        dry_run = ic_agent_runtime.build_workflow_r1_agent_run_dry_run(
            DEAL_ID,
            args.profile,
            wiki_root=wiki_root,
        )
        print_result("R1 agent dry-run smoke", dry_run)
        if not dry_run.get("allowed"):
            return 1
        if args.real:
            result = asyncio.run(
                ic_agent_runtime.run_workflow_r1_agent(
                    DEAL_ID,
                    args.profile,
                    wiki_root=wiki_root,
                    timeout=args.timeout,
                    created_by={"username": "hermes-smoke"},
                )
            )
            print_result("R1 agent real smoke", result)
            return 0 if result.get("hermes_called") and result.get("report_written") else 1
        return 0
    finally:
        stop_gateway(gateway_process, gateway_runtime, args.keep_gateway_runtime)
        if args.keep:
            print(f"Kept smoke wiki root: {wiki_root}")
        else:
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
