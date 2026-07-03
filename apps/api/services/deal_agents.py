"""Read-only Deal OS IC agent observability summaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from services import deal_reports
from services import deal_store
from services import ic_agent_runtime
from services import ic_policy


DEAL_AGENTS_SUMMARY_SCHEMA = "siq_deal_agents_summary_v1"


def _by_agent(items: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list):
        return {}
    return {
        str(item.get("agent_id")): item
        for item in items
        if isinstance(item, dict) and item.get("agent_id")
    }


def _profile_runtime(profile: dict[str, Any]) -> dict[str, Any]:
    runtime = profile.get("runtime") if isinstance(profile.get("runtime"), dict) else {}
    base_url = runtime.get("base_url") or runtime.get("runs_url") or runtime.get("base")
    payload = {
        "enabled": bool(runtime.get("enabled")),
        "port": runtime.get("port"),
        "base_url": base_url,
        "runs_url": runtime.get("runs_url"),
        "model_name": runtime.get("model_name") or runtime.get("model"),
        "profile": runtime.get("profile"),
    }
    return {key: value for key, value in payload.items() if value not in (None, "")}


def _agent_status(profile_id: str, readiness: dict[str, Any], report: dict[str, Any]) -> str:
    if profile_id not in ic_policy.R1_AGENT_SEQUENCE:
        return "non_r1"
    if readiness.get("allowed"):
        return "ready"
    if not readiness.get("has_startup_receipt") or readiness.get("blocking_reasons"):
        return "blocked"
    if not report.get("has_report"):
        return "missing_report"
    return str(report.get("status") or "blocked")


def summarize_deal_agents(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = deal_store.safe_deal_dir(deal_id, wiki_root=wiki_root)
    if not (package_dir / "manifest.json").is_file():
        raise FileNotFoundError(deal_id)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)

    profiles = ic_policy.list_ic_profiles(include_runtime=True)
    readiness = ic_agent_runtime.build_r1_agent_readiness(normalized_deal_id, wiki_root=wiki_root)
    reports = deal_reports.list_r1_agent_reports(normalized_deal_id, wiki_root=wiki_root)
    readiness_by_agent = _by_agent(readiness.get("agents"))
    reports_by_agent = _by_agent(reports.get("agents"))

    agents: list[dict[str, Any]] = []
    for profile in profiles:
        profile_id = str(profile.get("id") or "")
        ready_item = readiness_by_agent.get(profile_id, {})
        report_item = reports_by_agent.get(profile_id, {})
        receipt_id = ready_item.get("startup_receipt_id") or report_item.get("startup_receipt_id")
        is_r1_agent = profile_id in ic_policy.R1_AGENT_SEQUENCE
        runtime = _profile_runtime(profile)
        agents.append({
            "agent_id": profile_id,
            "role": profile.get("role"),
            "label": profile.get("label") or profile_id,
            "aliases": profile.get("aliases") or [],
            "profile_path": profile.get("profile_path"),
            "config_exists": profile.get("config_exists"),
            "in_manifest": profile.get("in_manifest"),
            "r1_sequence_index": profile.get("r1_sequence_index"),
            "is_r1_agent": is_r1_agent,
            "startup_retrieval_required": profile.get("startup_retrieval_required"),
            "runtime": runtime,
            "readiness": {
                "allowed": bool(ready_item.get("allowed")),
                "would_queue": bool(ready_item.get("would_queue")),
                "blocking_reasons": list(ready_item.get("blocking_reasons") or []),
                "warnings": list(ready_item.get("warnings") or []),
                "has_report": bool(ready_item.get("has_report")),
                "has_startup_receipt": bool(ready_item.get("has_startup_receipt")),
                "startup_receipt_id": ready_item.get("startup_receipt_id"),
                "preflight_status": ready_item.get("preflight_status"),
                "submitted": bool(ready_item.get("submitted")),
            },
            "report": {
                "has_report": bool(report_item.get("has_report")),
                "status": report_item.get("status"),
                "score": report_item.get("score"),
                "recommendation": report_item.get("recommendation"),
                "artifact_path": report_item.get("artifact_path"),
                "artifact_available": bool(report_item.get("artifact_available")),
                "markdown_section_status": report_item.get("markdown_section_status"),
            },
            "receipt": {
                "receipt_id": receipt_id,
                "present": bool(receipt_id),
            },
            "status": _agent_status(profile_id, ready_item, report_item),
        })

    return deal_store.redact_public_payload({
        "schema_version": DEAL_AGENTS_SUMMARY_SCHEMA,
        "deal_id": normalized_deal_id,
        "generated_at": deal_store.utc_now_iso(),
        "counts": {
            "agents": len(agents),
            "r1_agents": len([item for item in agents if item.get("is_r1_agent")]),
            "ready": len([item for item in agents if item.get("status") == "ready"]),
            "blocked": len([item for item in agents if item.get("status") == "blocked"]),
            "reports": len([item for item in agents if item.get("report", {}).get("has_report")]),
            "receipts": len([item for item in agents if item.get("receipt", {}).get("present")]),
            "runtime_enabled": len([item for item in agents if item.get("runtime", {}).get("enabled")]),
        },
        "r1_agent_sequence": list(ic_policy.R1_AGENT_SEQUENCE),
        "agents": agents,
    })
