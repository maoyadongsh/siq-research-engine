"""Read-only contract preflight checks for Deal OS packages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services import deal_store
from services import ic_policy


REQUIRED_CORE_FILES = {
    "project_meta": "project_meta.json",
    "manifest": "manifest.json",
    "workflow_state": "phases/workflow_state.json",
}
R4_REQUIRED_FIELDS = (
    "weighted_agent_score",
    "chairman_dimension_score",
    "chairman_qualitative_decision",
)


def _check(check_id: str, label: str, status: str, message: str, **details: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": check_id,
        "label": label,
        "status": status,
        "message": message,
    }
    if details:
        payload["details"] = deal_store.redact_public_payload(details)
    return payload


def _overall_status(checks: list[dict[str, Any]]) -> str:
    statuses = {str(check.get("status") or "") for check in checks}
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    return "pass"


def _canonical_keyed_payload(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    payload: dict[str, dict[str, Any]] = {}
    for key, item in value.items():
        if not isinstance(item, dict):
            continue
        profile_id = ic_policy.canonical_ic_profile_id(str(item.get("agent_id") or key))
        payload[profile_id] = item
    return payload


def _receipt_agents(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    agents = value.get("agents", value)
    return _canonical_keyed_payload(agents)


def _read_evidence_items(package_dir: Path) -> tuple[list[dict[str, Any]], int]:
    path = package_dir / "evidence" / "evidence_items.ndjson"
    items: list[dict[str, Any]] = []
    invalid = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return [], 0
    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            invalid += 1
            continue
        if isinstance(item, dict):
            items.append(item)
        else:
            invalid += 1
    return items, invalid


def _missing_required_fields(payload: dict[str, Any], fields: list[str] | tuple[str, ...]) -> list[str]:
    return [field for field in fields if field not in payload or payload.get(field) in (None, "")]


def run_deal_preflight(deal_id: str, *, wiki_root: Path | str | None = None) -> dict[str, Any]:
    package_dir = deal_store.safe_deal_dir(deal_id, wiki_root=wiki_root)
    if not package_dir.is_dir():
        raise FileNotFoundError(deal_id)

    checks: list[dict[str, Any]] = []
    loaded: dict[str, dict[str, Any]] = {}
    for name, relative in REQUIRED_CORE_FILES.items():
        path = package_dir / relative
        payload = deal_store.read_json(path, None)
        if payload is None:
            checks.append(_check(f"core.{name}", relative, "fail", f"Missing required file: {relative}"))
            loaded[name] = {}
        else:
            checks.append(_check(f"core.{name}", relative, "pass", f"Found {relative}"))
            loaded[name] = payload if isinstance(payload, dict) else {}

    expected_schemas = {
        "project_meta": deal_store.DEAL_PROJECT_SCHEMA,
        "manifest": deal_store.DEAL_MANIFEST_SCHEMA,
        "workflow_state": deal_store.DEAL_WORKFLOW_SCHEMA,
    }
    for name, expected in expected_schemas.items():
        payload = loaded.get(name) or {}
        actual = payload.get("schema_version")
        if not payload:
            continue
        status = "pass" if actual == expected else "warn"
        checks.append(_check(
            f"schema.{name}",
            f"{name} schema",
            status,
            "Schema version matches" if status == "pass" else "Schema version is missing or legacy",
            expected=expected,
            actual=actual,
        ))

    mismatches = []
    for name, payload in loaded.items():
        payload_deal_id = payload.get("deal_id")
        if payload and payload_deal_id and payload_deal_id != deal_id:
            mismatches.append({"file": REQUIRED_CORE_FILES[name], "deal_id": payload_deal_id})
    checks.append(_check(
        "identity.deal_id",
        "Deal ID consistency",
        "fail" if mismatches else "pass",
        "Deal ID mismatch found" if mismatches else "Core files agree on deal_id",
        mismatches=mismatches,
    ))

    policy = ic_policy.read_ic_workflow_policy()
    evidence_gate = policy.get("evidence_gate") if isinstance(policy.get("evidence_gate"), dict) else {}
    report_fields = evidence_gate.get("required_report_fields") or ["score", "recommendation"]
    report_metadata = evidence_gate.get("required_report_metadata") or ["verified", "assumed", "open_questions"]
    min_expert_reports = int(evidence_gate.get("min_expert_reports") or 5)

    reports = _canonical_keyed_payload(
        deal_store.read_json(package_dir / "phases" / "r1_reports.json", {}) or {}
    )
    report_count = len([profile_id for profile_id in ic_policy.R1_AGENT_SEQUENCE if profile_id in reports])
    checks.append(_check(
        "r1.report_count",
        "R1 expert report count",
        "pass" if report_count >= min_expert_reports else "warn",
        f"{report_count}/{min_expert_reports} required expert reports present",
        present=sorted(reports.keys()),
        required=list(ic_policy.R1_AGENT_SEQUENCE),
    ))

    report_issues = []
    for profile_id, report in reports.items():
        missing = _missing_required_fields(report, list(report_fields) + list(report_metadata))
        if missing:
            report_issues.append({"agent_id": profile_id, "missing": missing})
    checks.append(_check(
        "r1.report_contract",
        "R1 report contract",
        "warn" if report_issues else "pass",
        "R1 reports have missing contract fields" if report_issues else "R1 reports satisfy required fields",
        issues=report_issues,
    ))

    receipts = _receipt_agents(deal_store.read_json(package_dir / "phases" / "startup_receipts.json", {}) or {})
    missing_receipts = sorted(profile_id for profile_id in reports if profile_id not in receipts)
    checks.append(_check(
        "retrieval.startup_receipts",
        "Startup retrieval receipts",
        "warn" if missing_receipts else "pass",
        "Some reported agents are missing startup retrieval receipts" if missing_receipts else "Startup retrieval receipts cover reported agents",
        missing_agents=missing_receipts,
        receipt_count=len(receipts),
    ))

    evidence_items, invalid_evidence_lines = _read_evidence_items(package_dir)
    verified_items = [item for item in evidence_items if item.get("evidence_type") == "verified"]
    verified_dimensions = sorted({str(item.get("dimension")) for item in verified_items if item.get("dimension")})
    required_dimensions = list(evidence_gate.get("required_dimensions") or [])
    missing_dimensions = sorted(set(required_dimensions) - set(verified_dimensions))
    required_verified_items = int(evidence_gate.get("required_verified_items") or 0)
    evidence_status = "pass"
    if not evidence_items or len(verified_items) < required_verified_items or missing_dimensions or invalid_evidence_lines:
        evidence_status = "warn"
    checks.append(_check(
        "evidence.gate",
        "Evidence gate",
        evidence_status,
        "Evidence gate has warnings" if evidence_status == "warn" else "Evidence gate minimums are satisfied",
        item_count=len(evidence_items),
        verified_count=len(verified_items),
        required_verified_items=required_verified_items,
        verified_dimensions=verified_dimensions,
        missing_dimensions=missing_dimensions,
        invalid_lines=invalid_evidence_lines,
    ))

    r4_decision = deal_store.read_json(package_dir / "phases" / "r4_decision.json", None)
    if not isinstance(r4_decision, dict):
        checks.append(_check(
            "r4.decision",
            "R4 decision contract",
            "warn",
            "R4 decision has not been generated",
            missing_fields=list(R4_REQUIRED_FIELDS),
        ))
    else:
        missing_r4 = _missing_required_fields(r4_decision, R4_REQUIRED_FIELDS)
        checks.append(_check(
            "r4.decision",
            "R4 decision contract",
            "warn" if missing_r4 else "pass",
            "R4 decision is missing required scoring fields" if missing_r4 else "R4 decision contains required scoring fields",
            missing_fields=missing_r4,
        ))

    return {
        "deal_id": deal_id,
        "status": _overall_status(checks),
        "policy_version": policy.get("version"),
        "counts": {
            "r1_reports": report_count,
            "startup_receipts": len(receipts),
            "evidence_items": len(evidence_items),
            "verified_evidence_items": len(verified_items),
        },
        "checks": checks,
    }
