"""Read-only Deal OS status aggregation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from services import deal_audit
from services import deal_contracts
from services import deal_disputes
from services import deal_reports
from services import deal_store
from services import ic_agent_runtime


DEAL_STATUS_SUMMARY_SCHEMA = "siq_deal_status_summary_v1"
STATUS_RANK = {
    "pass": 0,
    "warn": 1,
    "fail": 2,
    "missing": 2,
    "unavailable": 2,
}


def _component(
    component_id: str,
    label: str,
    status: str,
    *,
    blocking: bool = False,
    message: str = "",
    href: str = "",
    metrics: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": component_id,
        "label": label,
        "status": status,
        "blocking": blocking,
        "message": message,
        "href": href or None,
        "metrics": metrics or {},
        "warnings": warnings or [],
    }


def _status_from_components(components: list[dict[str, Any]]) -> str:
    if not components:
        return "missing"
    statuses = {str(item.get("status") or "") for item in components}
    if "fail" in statuses:
        return "fail"
    if statuses.intersection({"warn", "missing", "unavailable"}):
        return "warn"
    return "pass"


def _safe_call(label: str, fn) -> tuple[Any | None, list[str]]:
    try:
        return fn(), []
    except FileNotFoundError:
        return None, [f"{label}_missing"]
    except ValueError as exc:
        return None, [f"{label}_invalid:{exc}"]


def _r1_readiness_component(readiness: dict[str, Any] | None, warnings: list[str]) -> dict[str, Any]:
    if readiness is None:
        return _component(
            "r1_readiness",
            "R1 Agent Readiness",
            "missing",
            blocking=True,
            message="R1 readiness is unavailable.",
            href="workflow",
            warnings=warnings,
        )
    ready_count = int(readiness.get("ready_count") or 0)
    blocked_count = int(readiness.get("blocked_count") or 0)
    status = "pass" if ready_count > 0 and blocked_count == 0 else "warn"
    return _component(
        "r1_readiness",
        "R1 Agent Readiness",
        status,
        blocking=ready_count <= 0,
        message=f"{ready_count} ready, {blocked_count} blocked.",
        href="workflow",
        metrics={
            "ready_count": ready_count,
            "blocked_count": blocked_count,
            "next_agent_id": readiness.get("next_agent_id"),
            "preflight_status": readiness.get("preflight_status"),
        },
        warnings=warnings,
    )


def _r1_reports_component(summary: dict[str, Any] | None, warnings: list[str]) -> dict[str, Any]:
    if summary is None:
        return _component(
            "r1_reports",
            "R1 Expert Reports",
            "missing",
            blocking=True,
            message="R1 report summary is unavailable.",
            href="reports",
            warnings=warnings,
        )
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    pass_count = int(counts.get("pass") or 0)
    warn_count = int(counts.get("warn") or 0)
    missing_count = int(counts.get("missing") or 0)
    status = "pass" if pass_count > 0 and warn_count == 0 and missing_count == 0 else "warn"
    return _component(
        "r1_reports",
        "R1 Expert Reports",
        status,
        blocking=missing_count > 0,
        message=f"{pass_count} pass, {warn_count} warn, {missing_count} missing.",
        href="reports",
        metrics=counts,
        warnings=warnings,
    )


def _r1_5_disputes_component(summary: dict[str, Any] | None, warnings: list[str]) -> dict[str, Any]:
    if summary is None:
        return _component(
            "r1_5_disputes",
            "R1.5 Disputes",
            "missing",
            blocking=False,
            message="R1.5 disputes summary is unavailable.",
            href="workflow",
            warnings=warnings,
        )
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    unresolved = int(counts.get("unresolved") or 0)
    resolved = int(counts.get("resolved") or 0)
    total = int(counts.get("disputes") or 0)
    status = str(summary.get("status") or "missing")
    summary_warnings = summary.get("warnings") if isinstance(summary.get("warnings"), list) else []
    return _component(
        "r1_5_disputes",
        "R1.5 Disputes",
        status,
        blocking=unresolved > 0 or (status != "missing" and bool(summary_warnings)),
        message=f"{resolved} resolved, {unresolved} unresolved, {total} total.",
        href="workflow",
        metrics={
            "disputes": total,
            "resolved": resolved,
            "unresolved": unresolved,
            "high_severity": counts.get("high_severity") or 0,
            "json_available": summary.get("artifacts", {}).get("json", {}).get("available"),
            "markdown_available": summary.get("artifacts", {}).get("markdown", {}).get("available"),
        },
        warnings=summary_warnings + warnings,
    )


def _r2_reports_component(summary: dict[str, Any] | None, warnings: list[str]) -> dict[str, Any]:
    if summary is None:
        return _component(
            "r2_reports",
            "R2 Revision Reports",
            "missing",
            blocking=False,
            message="R2 revision reports are unavailable.",
            href="reports",
            warnings=warnings,
        )
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    report_count = int(counts.get("reports") or 0)
    warn_count = int(counts.get("warn") or 0)
    missing_count = int(counts.get("missing") or 0)
    status = "missing" if report_count == 0 else "warn" if warn_count > 0 else "pass"
    return _component(
        "r2_reports",
        "R2 Revision Reports",
        status,
        blocking=False,
        message=f"{report_count} reports, {warn_count} warn, {missing_count} missing.",
        href="reports",
        metrics=counts,
        warnings=warnings,
    )


def _r3_review_component(summary: dict[str, Any] | None, warnings: list[str]) -> dict[str, Any]:
    if summary is None:
        return _component(
            "r3_review",
            "R3 Review",
            "missing",
            blocking=False,
            message="R3 review summary is unavailable.",
            href="reports",
            warnings=warnings,
        )
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    status = str(summary.get("status") or "missing")
    summary_warnings = summary.get("warnings") if isinstance(summary.get("warnings"), list) else []
    return _component(
        "r3_review",
        "R3 Review",
        status,
        blocking=status == "warn",
        message=f"R3 mode: {summary.get('mode') or 'unknown'}, reports: {counts.get('reports') or 0}.",
        href="reports",
        metrics={
            "mode": summary.get("mode"),
            "skipped": summary.get("skipped"),
            "reports": counts.get("reports") or 0,
            "challenges": counts.get("challenges") or 0,
            "warnings": counts.get("warnings") or 0,
            "markdown_available": summary.get("artifacts", {}).get("markdown", {}).get("available"),
        },
        warnings=summary_warnings + warnings,
    )


def _status_counts(components: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "components": len(components),
        "pass": 0,
        "warn": 0,
        "fail": 0,
        "missing": 0,
        "blocking": 0,
    }
    for item in components:
        status = str(item.get("status") or "")
        if status in counts:
            counts[status] += 1
        if item.get("blocking"):
            counts["blocking"] += 1
    return counts


def _missing_required_r4_fields(component: dict[str, Any]) -> list[str]:
    metrics = component.get("metrics")
    if not isinstance(metrics, dict):
        return []
    value = metrics.get("missing_required_fields")
    return value if isinstance(value, list) else []


def _r4_confirmation_pending(component: dict[str, Any]) -> bool:
    if component.get("id") != "r4_decision" or component.get("status") != "pass":
        return False
    metrics = component.get("metrics")
    if not isinstance(metrics, dict):
        return False
    return metrics.get("confirmed") is False


def _r4_decision_component(summary: dict[str, Any] | None, warnings: list[str]) -> dict[str, Any]:
    if summary is None:
        return _component(
            "r4_decision",
            "R4 Decision Contract",
            "missing",
            blocking=False,
            message="R4 decision has not been generated.",
            href="decision",
            warnings=warnings,
        )
    missing_required = summary.get("missing_required_fields")
    missing_required_fields = missing_required if isinstance(missing_required, list) else []
    status = str(summary.get("status") or "missing")
    human_confirmation = summary.get("human_confirmation") if isinstance(summary.get("human_confirmation"), dict) else {}
    return _component(
        "r4_decision",
        "R4 Decision Contract",
        status,
        blocking=status != "missing" and bool(missing_required_fields),
        message=f"R4 decision status: {status}.",
        href="decision",
        metrics={
            "missing_required_fields": missing_required_fields,
            "markdown_available": summary.get("artifacts", {}).get("markdown", {}).get("available"),
            "confirmation_status": human_confirmation.get("status"),
            "confirmed": human_confirmation.get("confirmed"),
        },
        warnings=warnings,
    )


def summarize_deal_status(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = deal_store.safe_deal_dir(deal_id, wiki_root=wiki_root)
    if not (package_dir / "manifest.json").is_file():
        raise FileNotFoundError(deal_id)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)

    preflight, preflight_warnings = _safe_call(
        "preflight",
        lambda: deal_contracts.run_deal_preflight(normalized_deal_id, wiki_root=wiki_root),
    )
    r1_readiness, r1_readiness_warnings = _safe_call(
        "r1_readiness",
        lambda: ic_agent_runtime.build_r1_agent_readiness(normalized_deal_id, wiki_root=wiki_root),
    )
    r1_reports, r1_reports_warnings = _safe_call(
        "r1_reports",
        lambda: deal_reports.list_r1_agent_reports(normalized_deal_id, wiki_root=wiki_root),
    )
    r1_5_disputes, r1_5_disputes_warnings = _safe_call(
        "r1_5_disputes",
        lambda: deal_disputes.summarize_deal_disputes(normalized_deal_id, wiki_root=wiki_root),
    )
    r2_reports, r2_reports_warnings = _safe_call(
        "r2_reports",
        lambda: deal_reports.list_r2_agent_reports(normalized_deal_id, wiki_root=wiki_root),
    )
    r3_review, r3_review_warnings = _safe_call(
        "r3_review",
        lambda: deal_reports.summarize_r3_review(normalized_deal_id, wiki_root=wiki_root),
    )
    r4_decision, r4_decision_warnings = _safe_call(
        "r4_decision",
        lambda: deal_reports.summarize_r4_decision(normalized_deal_id, wiki_root=wiki_root),
    )
    audit, audit_warnings = _safe_call(
        "audit",
        lambda: deal_audit.summarize_deal_audit(normalized_deal_id, wiki_root=wiki_root),
    )

    preflight_status = str(preflight.get("status") if isinstance(preflight, dict) else "missing")
    components = [
        _component(
            "preflight",
            "Deal Preflight",
            preflight_status,
            blocking=preflight_status == "fail",
            message=f"Preflight status: {preflight_status}.",
            href="workflow",
            metrics=preflight.get("counts") if isinstance(preflight, dict) else {},
            warnings=preflight_warnings,
        ),
        _r1_readiness_component(r1_readiness if isinstance(r1_readiness, dict) else None, r1_readiness_warnings),
        _r1_reports_component(r1_reports if isinstance(r1_reports, dict) else None, r1_reports_warnings),
        _r1_5_disputes_component(
            r1_5_disputes if isinstance(r1_5_disputes, dict) else None,
            r1_5_disputes_warnings,
        ),
        _r2_reports_component(r2_reports if isinstance(r2_reports, dict) else None, r2_reports_warnings),
        _r3_review_component(r3_review if isinstance(r3_review, dict) else None, r3_review_warnings),
        _r4_decision_component(r4_decision if isinstance(r4_decision, dict) else None, r4_decision_warnings),
        _component(
            "audit",
            "Audit Chain",
            str(audit.get("status") if isinstance(audit, dict) else "missing"),
            blocking=not isinstance(audit, dict),
            message=f"Audit status: {audit.get('status') if isinstance(audit, dict) else 'missing'}.",
            href="audit",
            metrics=audit.get("counts") if isinstance(audit, dict) else {},
            warnings=(audit.get("warnings", []) if isinstance(audit, dict) else []) + audit_warnings,
        ),
    ]

    counts = _status_counts(components)
    status = _status_from_components(components)
    blocking_components = [item for item in components if item.get("blocking")]
    ready = not blocking_components and status in {"pass", "warn"}
    if counts["blocking"]:
        next_action = "resolve_blocking_contracts"
    elif any(item.get("id") == "r4_decision" and _missing_required_r4_fields(item) for item in components):
        next_action = "complete_r4_decision_contract"
    elif any(_r4_confirmation_pending(item) for item in components):
        next_action = "confirm_r4_decision"
    elif status == "warn":
        next_action = "review_warnings"
    else:
        next_action = "continue_workflow"

    return deal_store.redact_public_payload({
        "schema_version": DEAL_STATUS_SUMMARY_SCHEMA,
        "deal_id": normalized_deal_id,
        "generated_at": deal_store.utc_now_iso(),
        "status": status,
        "ready_for_next_action": ready,
        "next_action": next_action,
        "counts": counts,
        "components": components,
        "sources": {
            "preflight": preflight,
            "r1_readiness": r1_readiness,
            "r1_reports": r1_reports,
            "r1_5_disputes": r1_5_disputes,
            "r2_reports": r2_reports,
            "r3_review": r3_review,
            "r4_decision": r4_decision,
            "audit": audit,
        },
    })
