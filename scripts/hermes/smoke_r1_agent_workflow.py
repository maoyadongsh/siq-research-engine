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
import socket
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

from services import (  # noqa: E402
    deal_store,
    ic_agent_runtime,
    ic_policy,
)

DEAL_ID = "DEAL-HERMES-SMOKE-001"
EVIDENCE_ID = "EVID-DEAL-HERMES-SMOKE-001-000001"
SMOKE_TOKEN = "local-smoke-token"
SMOKE_EVIDENCE_DIMENSIONS = ("business", "finance", "legal", "risk")


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


def is_tcp_port_open(host: str, port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def write_smoke_env_file(runtime_root: Path, token: str = SMOKE_TOKEN) -> Path:
    """Append-only env file for smoke-specific gateway auth overrides.

    run_gateway.sh sources SIQ_ENV_FILE after inheriting the parent env. Writing
    these values there keeps the gateway server token and this script's client
    token aligned even when the developer's local env already has a different
    API_SERVER_KEY.
    """
    env_path = runtime_root / "smoke.env"
    env_path.write_text(
        "\n".join([
            f"HERMES_API_KEY={token}",
            f"HERMES_TOKEN={token}",
            f"API_SERVER_KEY={token}",
            "",
        ]),
        encoding="utf-8",
    )
    return env_path


def start_gateway(profile_id: str, host: str, port: int, timeout_seconds: int) -> tuple[subprocess.Popen[bytes], Path]:
    if gateway_health(host, port):
        raise RuntimeError(f"Gateway is already healthy at http://{host}:{port}; refusing to replace it")
    if is_tcp_port_open(host, port):
        raise RuntimeError(
            f"Port {port} is already listening at {host}, but /health is not healthy; "
            "refusing to replace an existing process."
        )
    runtime_root = Path(tempfile.mkdtemp(prefix=f"siq-r1-gateway-{profile_id}-"))
    log_path = runtime_root / "gateway.log"
    smoke_env_file = write_smoke_env_file(runtime_root)
    env = os.environ.copy()
    env["HERMES_API_KEY"] = SMOKE_TOKEN
    env["HERMES_TOKEN"] = SMOKE_TOKEN
    env["API_SERVER_KEY"] = SMOKE_TOKEN
    env["SIQ_PROJECT_ROOT"] = str(PROJECT_ROOT)
    env["SIQ_HERMES_HOME"] = str(runtime_root / "home")
    env["SIQ_HERMES_PROFILES_ROOT"] = str(runtime_root / "home" / "profiles")
    env["SIQ_ENV_FILE"] = str(smoke_env_file)
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
                os.environ["HERMES_API_KEY"] = SMOKE_TOKEN
                os.environ["HERMES_TOKEN"] = SMOKE_TOKEN
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


def prior_r1_agents(profile_id: str) -> list[str]:
    canonical = ic_policy.canonical_ic_profile_id(profile_id)
    if canonical not in ic_policy.R1_AGENT_SEQUENCE:
        return []
    return list(ic_policy.R1_AGENT_SEQUENCE[:ic_policy.R1_AGENT_SEQUENCE.index(canonical)])


def seed_prior_r1_reports(package_dir: Path, profile_id: str) -> list[str]:
    prior_agents = prior_r1_agents(profile_id)
    if not prior_agents:
        return []
    report_payload = {
        agent_id: {
            "schema_version": "siq_ic_r1_agent_report_v1",
            "agent_id": agent_id,
            "round_name": "R1",
            "score": 75,
            "recommendation": "conditional_pass",
            "confidence": "medium",
            "summary": f"Synthetic prior R1 smoke report for {agent_id}.",
            "verified": [EVIDENCE_ID],
            "assumed": [],
            "open_questions": [],
            "risk_flags": [],
            "evidence_ids": [EVIDENCE_ID],
            "created_at": "2026-07-03T10:15:00+08:00",
        }
        for agent_id in prior_agents
    }
    write_json(package_dir / "phases" / "r1_reports.json", report_payload)

    workflow_path = package_dir / "phases" / "workflow_state.json"
    workflow = deal_store.read_json(workflow_path, {}) or {}
    phases = workflow.setdefault("phases", {})
    r1 = phases.setdefault("R1", {})
    r1.update({
        "status": "in_progress",
        "submitted_agents": prior_agents,
        "submitted_count": len(prior_agents),
        "updated_at": "2026-07-03T10:15:00+08:00",
    })
    workflow["current_phase"] = "R1"
    workflow["status"] = "r1_in_progress"
    workflow["updated_at"] = "2026-07-03T10:15:00+08:00"
    write_json(workflow_path, workflow)
    return prior_agents


def smoke_evidence_rows() -> list[dict[str, Any]]:
    return [
        {
            "evidence_id": f"EVID-DEAL-HERMES-SMOKE-001-{index:06d}",
            "evidence_type": "verified",
            "dimension": dimension,
            "claim": f"Temporary {dimension} smoke evidence for R1 workflow validation.",
            "quote": "This synthetic item exists only inside a temporary wiki root.",
            "document_id": f"smoke-doc-{index}",
            "source_path": f"parsed_documents/smoke-doc-{index}.md",
        }
        for index, dimension in enumerate(SMOKE_EVIDENCE_DIMENSIONS, start=1)
    ]


def smoke_startup_receipt(profile_id: str) -> dict[str, Any]:
    """Build an explicitly synthetic v2 receipt for the temporary smoke wiki."""

    agent_id = ic_policy.canonical_ic_profile_id(profile_id)
    private_collection = agent_id.removeprefix("siq_")
    physical_collections = {
        "siq_deal_shared": "ic_collaboration_shared",
        agent_id: private_collection,
    }
    background_ref = {
        "ref_id": f"KBREF-SMOKE-{agent_id}",
        "source_class": "background_knowledge",
        "collection": agent_id,
        "physical_collection": private_collection,
        "locator": "synthetic-smoke-methodology",
        "title": "Synthetic R1 smoke methodology",
        "usage": "methodology",
        "quote_preview": "Synthetic private-KB evidence for contract smoke only.",
    }
    return {
        "schema_version": "siq_ic_startup_receipt_v2",
        "receipt_id": f"startup-{agent_id}-R1-smoke",
        "deal_id": DEAL_ID,
        "agent_id": agent_id,
        "round_name": "R1",
        "query": "Hermes Smoke Robotics",
        "project_tag": DEAL_ID,
        "retrieval_mode": "synthetic_contract_smoke",
        "retrieval_status": "ready",
        "shared_hits": 1,
        "private_hits": 1,
        "milvus_used": True,
        "dual_kb_connected": True,
        "physical_collections": physical_collections,
        "vector_retrieval": {
            "shared_filter_applied": True,
            "shared_project_tag": DEAL_ID,
            "physical_collections": physical_collections,
        },
        "retrieval_strategy": {
            "mode": "dense_bm25_rrf",
            "embedding_model": "synthetic-contract-embedding",
        },
        "rerank_ready": True,
        "rerank": {
            "status": "completed",
            "model": "synthetic-contract-reranker",
        },
        "workspace_rules_read": ["SOUL.md", "AGENTS.md"],
        "gaps": [],
        "degraded_reasons": [],
        "evidence_hits": [{"evidence_id": EVIDENCE_ID}],
        "background_knowledge_refs": [background_ref],
        "methodology_refs": [background_ref],
        "gate": {"allowed_to_speak": True, "blocking_reasons": []},
        "created_at": "2026-07-03T10:20:00+08:00",
        "created_by": {"type": "synthetic_contract_smoke"},
    }


def build_smoke_package(wiki_root: Path, profile_id: str, *, seed_prior_reports: bool = False) -> Path:
    return build_smoke_package_for_profile(
        wiki_root,
        profile_id,
        seed_prior_reports=seed_prior_reports,
        all_receipts=False,
    )


def build_smoke_package_for_profile(
    wiki_root: Path,
    profile_id: str,
    *,
    seed_prior_reports: bool = False,
    all_receipts: bool = False,
) -> Path:
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
        smoke_evidence_rows(),
    )
    receipt_agent_ids = (
        ic_policy.R1_AGENT_SEQUENCE
        if all_receipts
        else [*prior_r1_agents(profile_id), ic_policy.canonical_ic_profile_id(profile_id)]
    )
    receipts = {
        agent_id: smoke_startup_receipt(agent_id)
        for agent_id in receipt_agent_ids
    }
    write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v2",
            "deal_id": DEAL_ID,
            "agents": receipts,
            "by_agent_phase": {
                agent_id: {"R1": receipt}
                for agent_id, receipt in receipts.items()
            },
            "updated_at": "2026-07-03T10:20:00+08:00",
        },
    )
    if seed_prior_reports:
        prior_agents = seed_prior_r1_reports(package_dir, profile_id)
        if prior_agents:
            print(f"Seeded prior R1 reports for sequence: {', '.join(prior_agents)}")
    return package_dir


def print_result(title: str, payload: dict[str, Any]) -> None:
    compact = {
        "schema_version": payload.get("schema_version"),
        "deal_id": payload.get("deal_id"),
        "agent_id": payload.get("agent_id"),
        "dry_run": payload.get("dry_run"),
        "allowed": payload.get("allowed"),
        "would_queue": payload.get("would_queue"),
        "would_run": payload.get("would_run"),
        "hermes_called": payload.get("hermes_called"),
        "report_written": payload.get("report_written"),
        "workflow_advanced": payload.get("workflow_advanced"),
        "preflight_status": payload.get("preflight_status"),
        "planned_agent_ids": payload.get("planned_agent_ids"),
        "planned_count": payload.get("planned_count"),
        "next_agent_id": payload.get("next_agent_id"),
        "stop_reason": payload.get("stop_reason"),
        "blocking_reasons": payload.get("blocking_reasons"),
        "warnings": payload.get("warnings"),
    }
    print(f"{title}:")
    print(json.dumps(compact, ensure_ascii=False, indent=2))


def run_dry_run_smoke(profile_id: str, *, keep: bool = False) -> dict[str, Any]:
    temp_root = Path(tempfile.mkdtemp(prefix=f"siq-r1-agent-smoke-{profile_id}-"))
    wiki_root = temp_root / "wiki"
    try:
        build_smoke_package(
            wiki_root,
            profile_id,
            seed_prior_reports=bool(prior_r1_agents(profile_id)),
        )
        dry_run = ic_agent_runtime.build_workflow_r1_agent_run_dry_run(
            DEAL_ID,
            profile_id,
            wiki_root=wiki_root,
        )
        print_result(f"R1 agent dry-run smoke ({profile_id})", dry_run)
        return dry_run
    finally:
        if keep:
            print(f"Kept smoke wiki root for {profile_id}: {wiki_root}")
        else:
            shutil.rmtree(temp_root, ignore_errors=True)


def run_r1_profile_matrix(*, keep: bool = False) -> dict[str, Any]:
    results = [
        run_dry_run_smoke(profile_id, keep=keep)
        for profile_id in ic_policy.R1_AGENT_SEQUENCE
    ]
    summary = {
        "schema_version": "siq_ic_r1_smoke_matrix_v1",
        "profiles": [
            {
                "agent_id": item.get("agent_id"),
                "allowed": item.get("allowed"),
                "blocking_reasons": item.get("blocking_reasons"),
                "warnings": item.get("warnings"),
            }
            for item in results
        ],
        "allowed_count": sum(1 for item in results if item.get("allowed")),
        "blocked_count": sum(1 for item in results if not item.get("allowed")),
    }
    print("R1 profile dry-run smoke matrix:")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def run_serial_dry_run_smoke(*, keep: bool = False) -> dict[str, Any]:
    temp_root = Path(tempfile.mkdtemp(prefix="siq-r1-serial-smoke-"))
    wiki_root = temp_root / "wiki"
    try:
        build_smoke_package_for_profile(
            wiki_root,
            "siq_ic_strategist",
            all_receipts=True,
        )
        dry_run = ic_agent_runtime.build_workflow_r1_serial_run_dry_run(
            DEAL_ID,
            wiki_root=wiki_root,
        )
        print_result("R1 serial dry-run smoke", dry_run)
        return dry_run
    finally:
        if keep:
            print(f"Kept serial smoke wiki root: {wiki_root}")
        else:
            shutil.rmtree(temp_root, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="siq_ic_strategist", help="R1 profile to smoke")
    parser.add_argument("--all-r1-profiles", action="store_true", help="Run dry-run smoke for every R1 profile in policy order")
    parser.add_argument("--serial", action="store_true", help="Run R1 serial workflow dry-run smoke with receipts for every R1 profile")
    parser.add_argument("--real", action="store_true", help="Call Hermes and write the report into the temporary package")
    parser.add_argument("--require-gateway-health", action="store_true", help="Fail unless the target profile gateway /health is ready")
    parser.add_argument("--start-gateway", action="store_true", help="Start the target profile gateway with a temporary runtime")
    parser.add_argument("--gateway-timeout", type=int, default=45, help="Seconds to wait for --start-gateway health")
    parser.add_argument("--timeout", type=float, default=120.0, help="Hermes collect timeout for --real")
    parser.add_argument("--seed-prior-reports", action="store_true", help="Seed synthetic prior R1 reports in the temporary package so later sequence agents can be smoked without running earlier agents")
    parser.add_argument("--keep", action="store_true", help="Keep the temporary wiki root for inspection")
    parser.add_argument("--keep-gateway-runtime", action="store_true", help="Keep temporary gateway runtime when using --start-gateway")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.all_r1_profiles:
        if args.real or args.start_gateway or args.require_gateway_health:
            raise RuntimeError("--all-r1-profiles is dry-run only; run real gateway smoke one profile at a time")
        summary = run_r1_profile_matrix(keep=args.keep)
        return 0 if summary.get("blocked_count") == 0 else 1
    if args.serial:
        if args.real or args.start_gateway or args.require_gateway_health:
            raise RuntimeError("--serial is dry-run only; run real gateway smoke one profile at a time")
        dry_run = run_serial_dry_run_smoke(keep=args.keep)
        return 0 if dry_run.get("allowed") and dry_run.get("planned_count") == len(ic_policy.R1_AGENT_SEQUENCE) else 1

    temp_root = Path(tempfile.mkdtemp(prefix="siq-r1-agent-smoke-"))
    wiki_root = temp_root / "wiki"
    gateway_process: subprocess.Popen[bytes] | None = None
    gateway_runtime: Path | None = None
    try:
        host, port = profile_api_server(args.profile)
        if args.real:
            os.environ["HERMES_API_KEY"] = SMOKE_TOKEN
            os.environ["HERMES_TOKEN"] = SMOKE_TOKEN
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

        build_smoke_package(wiki_root, args.profile, seed_prior_reports=args.seed_prior_reports)
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
