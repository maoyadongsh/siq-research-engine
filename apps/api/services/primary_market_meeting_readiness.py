"""Readiness summary for the primary-market multi-agent meeting room."""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from services import deal_reports
from services import deal_store
from services import ic_agent_runtime
from services import ic_policy
from services import ic_profile_contract
from services import ic_startup_retrieval


PRIMARY_MARKET_MEETING_READINESS_SCHEMA = "siq_primary_market_meeting_readiness_v1"


def _require_package_dir(deal_id: str, *, wiki_root: Path | str | None = None) -> Path:
    package_dir = deal_store.safe_deal_dir(deal_id, wiki_root=wiki_root)
    if not (package_dir / "manifest.json").is_file():
        raise FileNotFoundError(deal_id)
    return package_dir


def _tcp_open(host: str, port: int, *, timeout: float = 0.12) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _tcp_port_open(host: str, port: int, *, timeout: float = 0.12) -> bool:
    return _tcp_open(host, port, timeout=timeout)


def _profile_runtime(profile: dict[str, Any]) -> dict[str, Any]:
    runtime = profile.get("runtime") if isinstance(profile.get("runtime"), dict) else {}
    runs_url = str(runtime.get("base") or runtime.get("runs_url") or "").strip()
    parsed = urlparse(runs_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or int(profile.get("default_port") or 0)
    enabled = bool(port and _tcp_port_open(host, int(port)))
    return {
        "enabled": enabled,
        "status": "running" if enabled else "not_running",
        "health": "running" if enabled else "configured",
        "host": host,
        "port": port,
        "runs_url": runs_url,
        "model": runtime.get("model"),
    }


def _by_agent(items: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list):
        return {}
    return {
        str(item.get("agent_id")): item
        for item in items
        if isinstance(item, dict) and item.get("agent_id")
    }


def _read_receipt(deal_id: str, profile_id: str, *, wiki_root: Path | str | None = None) -> dict[str, Any]:
    payload = ic_startup_retrieval.read_startup_retrieval_receipt(deal_id, profile_id, wiki_root=wiki_root)
    receipt = payload.get("receipt") if isinstance(payload.get("receipt"), dict) else None
    return {
        "required": True,
        "present": receipt is not None,
        "skipped": False,
        "receipt_id": receipt.get("receipt_id") if isinstance(receipt, dict) else None,
        "shared_hits": receipt.get("shared_hits") if isinstance(receipt, dict) else None,
        "private_hits": receipt.get("private_hits") if isinstance(receipt, dict) else None,
        "gaps": receipt.get("gaps") if isinstance(receipt, dict) else [],
        "created_at": receipt.get("created_at") if isinstance(receipt, dict) else None,
    }


def build_meeting_readiness(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
    include_runtime: bool = True,
) -> dict[str, Any]:
    """Return profile, runtime, receipt, and R1 workflow readiness in one payload."""

    _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    profiles = ic_policy.list_ic_profiles(include_runtime=include_runtime)
    try:
        r1_readiness = ic_agent_runtime.build_r1_agent_readiness(normalized_deal_id, wiki_root=wiki_root)
    except Exception as exc:
        r1_readiness = {
            "status": "unavailable",
            "error": str(exc) or exc.__class__.__name__,
            "agents": [],
        }
    try:
        r1_reports = deal_reports.list_r1_agent_reports(normalized_deal_id, wiki_root=wiki_root)
    except Exception as exc:
        r1_reports = {
            "status": "unavailable",
            "error": str(exc) or exc.__class__.__name__,
            "agents": [],
        }
    readiness_by_agent = _by_agent(r1_readiness.get("agents"))
    reports_by_agent = _by_agent(r1_reports.get("agents"))

    agents: list[dict[str, Any]] = []
    for profile in profiles:
        profile_id = str(profile.get("id") or "")
        contract = ic_profile_contract.get_ic_profile_contract(profile_id)
        runtime = _profile_runtime(profile) if include_runtime else {
            "enabled": False,
            "status": "not_checked",
            "health": "not_checked",
            "host": None,
            "port": profile.get("default_port"),
            "runs_url": None,
            "model": None,
        }
        receipt = _read_receipt(normalized_deal_id, profile_id, wiki_root=wiki_root)
        workflow_item = readiness_by_agent.get(profile_id, {})
        report_item = reports_by_agent.get(profile_id, {})
        is_r1_agent = profile_id in ic_policy.R1_AGENT_SEQUENCE
        blocking_reasons = list(workflow_item.get("blocking_reasons") or [])
        if receipt.get("required") and not receipt.get("present") and "startup_receipt_missing" not in blocking_reasons:
            blocking_reasons.append("startup_receipt_missing")
        if include_runtime and not runtime.get("enabled"):
            blocking_reasons.append("hermes_runtime_not_running")
        ready_for_formal_task = bool(
            (runtime.get("enabled") or not include_runtime)
            and not blocking_reasons
        )
        report_summary = {
            "present": bool(report_item.get("has_report")),
            "required": is_r1_agent,
            "has_report": bool(report_item.get("has_report")),
            "status": report_item.get("status") or ("not_required" if not is_r1_agent else "missing"),
            "score": report_item.get("score"),
            "recommendation": report_item.get("recommendation"),
            "artifact_path": report_item.get("artifact_path"),
        }
        quality = {
            "ready_for_formal_task": ready_for_formal_task,
            "blocking_reasons": blocking_reasons,
            "warnings": list(workflow_item.get("warnings") or [])
            + ([] if report_summary["present"] or not is_r1_agent else ["r1_report_missing"]),
        }
        agents.append({
            "profile_id": profile_id,
            "agent_id": profile_id,
            "label": profile.get("label") or contract.get("label") or profile_id,
            "role": profile.get("role") or contract.get("role"),
            "contract": contract,
            "runtime": runtime,
            "startup_receipt": receipt,
            "workflow": {
                "is_r1_agent": is_r1_agent,
                "allowed": bool(workflow_item.get("allowed")),
                "would_queue": bool(workflow_item.get("would_queue")),
                "blocking_reasons": blocking_reasons,
                "warnings": list(workflow_item.get("warnings") or []),
                "preflight_status": workflow_item.get("preflight_status"),
                "submitted": bool(workflow_item.get("submitted")),
            },
            "report": report_summary,
            "r1_report": report_summary,
            "quality": quality,
            "ready_for_formal_task": ready_for_formal_task,
            "blocking_reasons": blocking_reasons,
        })

    return deal_store.redact_public_payload({
        "schema_version": PRIMARY_MARKET_MEETING_READINESS_SCHEMA,
        "deal_id": normalized_deal_id,
        "generated_at": deal_store.utc_now_iso(),
        "summary": {
            "profiles": len(agents),
            "agents": len(agents),
            "runtime_running": sum(1 for item in agents if item.get("runtime", {}).get("enabled")),
            "ready_for_formal_task": sum(1 for item in agents if item.get("ready_for_formal_task")),
            "startup_receipts": sum(1 for item in agents if item.get("startup_receipt", {}).get("present")),
            "receipt_present": sum(1 for item in agents if item.get("startup_receipt", {}).get("present")),
            "receipt_required": sum(1 for item in agents if item.get("startup_receipt", {}).get("required")),
            "r1_reports": sum(1 for item in agents if item.get("report", {}).get("has_report")),
            "r1_reports_present": sum(1 for item in agents if item.get("report", {}).get("has_report")),
            "blocking_profiles": [
                item.get("profile_id")
                for item in agents
                if item.get("blocking_reasons")
            ],
        },
        "r1_readiness": r1_readiness,
        "r1_reports": r1_reports,
        "profiles": agents,
        "agents": agents,
    })
