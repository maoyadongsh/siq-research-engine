"""Readiness summary for the primary-market multi-agent meeting room."""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from services import deal_reports, deal_store, ic_agent_runtime, ic_policy, ic_profile_contract, ic_startup_retrieval

PRIMARY_MARKET_MEETING_READINESS_SCHEMA = "siq_primary_market_meeting_readiness_v1"
REQUIRED_ROLE_CONTRACT_FILES = frozenset({"IDENTITY.md", "AGENTS.md", "SOUL.md", "TOOLS.md"})


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
    gate = receipt.get("gate") if isinstance(receipt, dict) and isinstance(receipt.get("gate"), dict) else {}
    physical_mapping = (
        receipt.get("physical_collections")
        if isinstance(receipt, dict) and isinstance(receipt.get("physical_collections"), dict)
        else {}
    )
    rerank = receipt.get("rerank") if isinstance(receipt, dict) and isinstance(receipt.get("rerank"), dict) else {}
    vector_retrieval = (
        receipt.get("vector_retrieval")
        if isinstance(receipt, dict) and isinstance(receipt.get("vector_retrieval"), dict)
        else {}
    )
    actual_collections = [str(item) for item in vector_retrieval.get("collections") or []]
    vector_completed = vector_retrieval.get("status") == "completed" and bool(
        vector_retrieval.get("milvus_used", receipt.get("milvus_used") if isinstance(receipt, dict) else False)
    )
    if isinstance(receipt, dict) and isinstance(receipt.get("shared_connected"), bool):
        shared_connected = receipt["shared_connected"]
    else:
        shared_connected = bool(
            vector_completed
            and "siq_deal_shared" in actual_collections
            and physical_mapping.get("siq_deal_shared") == "ic_collaboration_shared"
            and vector_retrieval.get("shared_filter_applied") is True
            and vector_retrieval.get("shared_project_tag") == deal_id
        )
    if isinstance(receipt, dict) and isinstance(receipt.get("private_connected"), bool):
        private_connected = receipt["private_connected"]
    else:
        private_connected = bool(
            vector_completed
            and profile_id in actual_collections
            and physical_mapping.get(profile_id) == profile_id.removeprefix("siq_")
        )
    dual_kb_connected = shared_connected and private_connected
    return {
        "required": True,
        "present": receipt is not None,
        "skipped": False,
        "receipt_id": receipt.get("receipt_id") if isinstance(receipt, dict) else None,
        "shared_hits": receipt.get("shared_hits") if isinstance(receipt, dict) else None,
        "private_hits": receipt.get("private_hits") if isinstance(receipt, dict) else None,
        "evidence_hit_count": receipt.get("evidence_hit_count") if isinstance(receipt, dict) else None,
        "gaps": receipt.get("gaps") if isinstance(receipt, dict) else [],
        "collections": receipt.get("retrieval_collections") if isinstance(receipt, dict) else [],
        "physical_collections": list(dict.fromkeys(str(value) for value in physical_mapping.values() if value)),
        "shared_collection": physical_mapping.get("siq_deal_shared") or "ic_collaboration_shared",
        "private_collection": physical_mapping.get(profile_id),
        "shared_connected": shared_connected,
        "private_connected": private_connected,
        "collections_connected": dual_kb_connected,
        "dual_kb_connected": dual_kb_connected,
        "connection_status": receipt.get("connection_status") if isinstance(receipt, dict) else "not_checked",
        "connection_errors": receipt.get("connection_errors") if isinstance(receipt, dict) else [],
        "connection_checked_at": receipt.get("connection_checked_at") if isinstance(receipt, dict) else None,
        "chat_retrieval_ready": dual_kb_connected,
        "chat_retrieval_status": receipt.get("chat_retrieval_status") if isinstance(receipt, dict) else "missing",
        "retrieval_status": receipt.get("retrieval_status") if isinstance(receipt, dict) else "missing",
        "degraded_reasons": receipt.get("degraded_reasons") if isinstance(receipt, dict) else [],
        "blocking_reasons": gate.get("blocking_reasons") if isinstance(gate.get("blocking_reasons"), list) else [],
        "shared_ready": receipt.get("shared_ready") if isinstance(receipt, dict) else False,
        "private_ready": receipt.get("private_ready") if isinstance(receipt, dict) else False,
        "rerank_ready": receipt.get("rerank_ready") if isinstance(receipt, dict) else False,
        "rerank_status": rerank.get("status"),
        "rerank_candidate_count": rerank.get("candidate_count", 0),
        "rerank_result_count": rerank.get("result_count", 0),
        "shared_vector_hit_count": receipt.get("shared_vector_hit_count", 0) if isinstance(receipt, dict) else 0,
        "local_evidence_hit_count": receipt.get("local_evidence_hit_count", 0) if isinstance(receipt, dict) else 0,
        "private_selected_hit_count": receipt.get("private_selected_hit_count", 0) if isinstance(receipt, dict) else 0,
        "content_warnings": receipt.get("content_warnings") if isinstance(receipt, dict) else [],
        "retrieval_strategy": receipt.get("retrieval_strategy") if isinstance(receipt, dict) else {},
        "collection_candidate_counts": receipt.get("collection_candidate_counts") if isinstance(receipt, dict) else {},
        "evidence_snapshot_hash": receipt.get("evidence_snapshot_hash") if isinstance(receipt, dict) else None,
        "capability_restrictions": receipt.get("capability_restrictions") if isinstance(receipt, dict) else [],
        "stale": bool(
            isinstance(receipt, dict)
            and (
                receipt.get("readiness_status") == "stale"
                or receipt.get("stale_reason")
                or (
                    isinstance(receipt.get("freshness"), dict)
                    and receipt["freshness"].get("stale")
                )
            )
        ),
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
        contract_source_names = {
            Path(str(item)).name
            for item in contract.get("source_files") or []
            if str(item or "").strip()
        }
        role_contract_ready = REQUIRED_ROLE_CONTRACT_FILES.issubset(contract_source_names)
        workflow_item = readiness_by_agent.get(profile_id, {})
        report_item = reports_by_agent.get(profile_id, {})
        is_r1_agent = profile_id in ic_policy.R1_AGENT_SEQUENCE
        blocking_reasons = list(workflow_item.get("blocking_reasons") or [])
        if receipt.get("required") and not receipt.get("present") and "startup_receipt_missing" not in blocking_reasons:
            blocking_reasons.append("startup_receipt_missing")
        for reason in receipt.get("blocking_reasons") or []:
            if reason not in blocking_reasons:
                blocking_reasons.append(reason)
        if receipt.get("present") and receipt.get("retrieval_status") != "ready" and not receipt.get("blocking_reasons"):
            blocking_reasons.append("startup_retrieval_not_ready")
        if include_runtime and not runtime.get("enabled"):
            blocking_reasons.append("hermes_runtime_not_running")
        chat_blocking_reasons: list[str] = []
        if include_runtime and not runtime.get("enabled"):
            chat_blocking_reasons.append("hermes_runtime_not_running")
        if not role_contract_ready:
            chat_blocking_reasons.append("role_contract_incomplete")
        if not receipt.get("present"):
            chat_blocking_reasons.append("dual_kb_connection_not_checked")
        else:
            if not receipt.get("shared_connected"):
                chat_blocking_reasons.append("shared_collection_unavailable")
            if not receipt.get("private_connected"):
                chat_blocking_reasons.append("private_collection_unavailable")
        content_warnings = [str(item) for item in receipt.get("content_warnings") or []]
        if receipt.get("shared_connected") and not int(receipt.get("shared_vector_hit_count") or 0):
            content_warnings.append("deal_scoped_shared_kb_empty")
        if receipt.get("shared_connected") and not receipt.get("evidence_snapshot_hash"):
            content_warnings.append("evidence_snapshot_unavailable")
        if receipt.get("private_connected") and not int(receipt.get("private_selected_hit_count") or receipt.get("private_hits") or 0):
            content_warnings.append("private_background_hits_empty")
        if not receipt.get("rerank_ready"):
            content_warnings.append("reranker_unavailable")
        if receipt.get("stale"):
            content_warnings.append("startup_receipt_stale")
        content_warnings = list(dict.fromkeys(content_warnings))
        service_ready_for_chat = not chat_blocking_reasons
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
            "service_ready_for_chat": service_ready_for_chat,
            "chat_blocking_reasons": chat_blocking_reasons,
            "content_warnings": content_warnings,
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
            "role_contract_ready": role_contract_ready,
            "dual_kb_connected": bool(receipt.get("dual_kb_connected")),
            "service_ready_for_chat": service_ready_for_chat,
            "chat_blocking_reasons": chat_blocking_reasons,
            "content_warnings": content_warnings,
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
            "dual_kb_connected": sum(1 for item in agents if item.get("dual_kb_connected")),
            "ready_for_chat": sum(1 for item in agents if item.get("service_ready_for_chat")),
            "service_ready_for_chat": sum(1 for item in agents if item.get("service_ready_for_chat")),
            "formal_task_ready": sum(1 for item in agents if item.get("ready_for_formal_task")),
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
