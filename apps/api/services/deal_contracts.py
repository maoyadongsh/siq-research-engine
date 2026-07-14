"""Read-only contract preflight checks for Deal OS packages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services import deal_store, ic_policy, ic_report_contracts

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
RECEIPT_REQUIRED_FIELDS = (
    "receipt_id",
    "agent_id",
    "round_name",
    "query",
    "project_tag",
    "shared_hits",
    "private_hits",
    "workspace_rules_read",
    "gaps",
    "created_at",
)
REPORT_EVIDENCE_KEYS = (
    "evidence_ids",
    "evidence_refs",
    "citations",
    "verified",
    "assumed",
    "key_points",
    "risk_flags",
    "claims",
    "scorecard",
    "red_flags",
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
    path = _evidence_items_path(package_dir)
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


def _safe_manifest_relative_path(package_dir: Path, value: Any, default: str) -> Path:
    raw = str(value or default).strip().replace("\\", "/")
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        return package_dir / default
    candidate = (package_dir / path).resolve()
    try:
        candidate.relative_to(package_dir.resolve())
    except ValueError:
        return package_dir / default
    return candidate


def _evidence_items_path(package_dir: Path) -> Path:
    manifest = deal_store.read_json(package_dir / "manifest.json", {}) or {}
    evidence = manifest.get("evidence") if isinstance(manifest.get("evidence"), dict) else {}
    return _safe_manifest_relative_path(package_dir, evidence.get("items_path"), "evidence/evidence_items.ndjson")


def _missing_required_fields(payload: dict[str, Any], fields: list[str] | tuple[str, ...]) -> list[str]:
    return [field for field in fields if field not in payload or payload.get(field) in (None, "")]


def _collect_evidence_ids(items: list[dict[str, Any]]) -> set[str]:
    return {str(item.get("evidence_id")) for item in items if item.get("evidence_id")}


def _extract_evidence_ids(value: Any) -> set[str]:
    ids: set[str] = set()
    if isinstance(value, str):
        if value.startswith("EVID-"):
            ids.add(value)
        return ids
    if isinstance(value, dict):
        for key in ("evidence_id", "id"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.startswith("EVID-"):
                ids.add(candidate)
        for nested in value.values():
            ids.update(_extract_evidence_ids(nested))
        return ids
    if isinstance(value, list):
        for item in value:
            ids.update(_extract_evidence_ids(item))
    return ids


def _report_evidence_ids(report: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for key in REPORT_EVIDENCE_KEYS:
        ids.update(_extract_evidence_ids(report.get(key)))
    evidence_stats = report.get("evidence_stats")
    if isinstance(evidence_stats, dict):
        ids.update(_extract_evidence_ids(evidence_stats))
    return ids


def _receipt_contract_issues(
    receipts: dict[str, dict[str, Any]],
    *,
    evidence_ids: set[str],
    deal_id: str,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for profile_id, receipt in sorted(receipts.items()):
        missing = _missing_required_fields(receipt, RECEIPT_REQUIRED_FIELDS)
        if receipt.get("agent_id") and ic_policy.canonical_ic_profile_id(str(receipt.get("agent_id"))) != profile_id:
            missing.append("agent_id_matches_key")
        if receipt.get("project_tag") and receipt.get("project_tag") != deal_id:
            missing.append("project_tag_matches_deal")
        if not isinstance(receipt.get("workspace_rules_read"), list) or not receipt.get("workspace_rules_read"):
            missing.append("workspace_rules_read_non_empty")
        if not isinstance(receipt.get("gaps"), list):
            missing.append("gaps_list")
        if not isinstance(receipt.get("shared_hits"), int) or receipt.get("shared_hits", 0) < 0:
            missing.append("shared_hits_non_negative_int")
        if not isinstance(receipt.get("private_hits"), int) or receipt.get("private_hits", 0) < 0:
            missing.append("private_hits_non_negative_int")
        evidence_hits = receipt.get("evidence_hits")
        if evidence_hits is not None and not isinstance(evidence_hits, list):
            missing.append("evidence_hits_list")
        referenced = _extract_evidence_ids(evidence_hits)
        unknown = sorted(referenced - evidence_ids)
        if unknown:
            missing.append("evidence_hits_known")
        if missing or unknown:
            issues.append({
                "agent_id": profile_id,
                "missing_or_invalid": sorted(set(missing)),
                "unknown_evidence_ids": unknown,
            })
    return issues


def _report_evidence_issues(
    reports: dict[str, dict[str, Any]],
    receipts: dict[str, dict[str, Any]],
    evidence_ids: set[str],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for profile_id, report in sorted(reports.items()):
        report_ids = _report_evidence_ids(report)
        unknown = sorted(report_ids - evidence_ids)
        if unknown:
            issues.append({
                "agent_id": profile_id,
                "missing_or_invalid": ["known_evidence_id_reference"],
                "unknown_evidence_ids": unknown,
            })
    return issues


def _report_evidence_advisories(
    reports: dict[str, dict[str, Any]],
    receipts: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    advisories: list[dict[str, Any]] = []
    for profile_id, report in sorted(reports.items()):
        report_ids = _report_evidence_ids(report)
        receipt = receipts.get(profile_id, {})
        receipt_ids = _extract_evidence_ids(receipt.get("evidence_hits")) if isinstance(receipt, dict) else set()
        notes: list[str] = []
        if not report.get("startup_receipt_id"):
            notes.append("startup_receipt_id_missing")
        elif receipt and report.get("startup_receipt_id") != receipt.get("receipt_id"):
            notes.append("startup_receipt_id_differs_from_receipt")
        if not report_ids:
            notes.append("structured_evidence_id_missing")
        if receipt_ids and report_ids and not report_ids.intersection(receipt_ids):
            notes.append("report_does_not_reference_startup_receipt_hits")
        if notes:
            advisories.append({"agent_id": profile_id, "notes": notes})
    return advisories


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
        metadata = (
            ["claims", "open_questions"]
            if report.get("schema_version") == ic_report_contracts.IC_EXPERT_REPORT_SCHEMA
            else list(report_metadata)
        )
        missing = _missing_required_fields(report, list(report_fields) + metadata)
        if missing:
            report_issues.append({"agent_id": profile_id, "missing": missing})
    checks.append(_check(
        "r1.report_contract",
        "R1 report contract",
        "warn" if report_issues else "pass",
        "R1 reports have missing contract fields" if report_issues else "R1 reports satisfy required fields",
        issues=report_issues,
    ))

    evidence_items, invalid_evidence_lines = _read_evidence_items(package_dir)
    evidence_ids = _collect_evidence_ids(evidence_items)

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

    receipt_issues = _receipt_contract_issues(receipts, evidence_ids=evidence_ids, deal_id=deal_id)
    checks.append(_check(
        "retrieval.receipt_contract",
        "Startup receipt contract",
        "warn" if receipt_issues else "pass",
        "Startup receipts have contract issues" if receipt_issues else "Startup receipts satisfy required fields",
        issues=receipt_issues,
        required_fields=list(RECEIPT_REQUIRED_FIELDS),
    ))

    snapshot = deal_store.read_json(package_dir / "evidence" / "evidence_snapshot.json", {}) or {}
    current_snapshot_hash = str(snapshot.get("snapshot_hash") or "")
    current_source_ids = sorted(
        str(item) for item in snapshot.get("source_ids") or [] if str(item or "").strip()
    )
    snapshot_issues: list[dict[str, Any]] = []
    if current_snapshot_hash:
        for profile_id, receipt in sorted(receipts.items()):
            reasons: list[str] = []
            if receipt.get("evidence_snapshot_hash") != current_snapshot_hash:
                reasons.append("evidence_snapshot_hash_stale_or_missing")
            receipt_source_ids = sorted(
                str(item) for item in receipt.get("source_ids") or [] if str(item or "").strip()
            )
            if receipt_source_ids != current_source_ids:
                reasons.append("source_ids_stale_or_missing")
            if receipt.get("readiness_status") == "stale":
                reasons.append("receipt_marked_stale")
            if reasons:
                snapshot_issues.append({"agent_id": profile_id, "reasons": sorted(set(reasons))})
    checks.append(_check(
        "retrieval.evidence_snapshot",
        "Evidence snapshot identity",
        "fail" if snapshot_issues else "pass",
        "Startup receipts use a stale evidence snapshot" if snapshot_issues else "Startup receipts match the current evidence snapshot",
        current_snapshot_hash=current_snapshot_hash or None,
        current_source_ids=current_source_ids,
        issues=snapshot_issues,
    ))

    report_evidence_issues = _report_evidence_issues(reports, receipts, evidence_ids)
    checks.append(_check(
        "r1.report_evidence_refs",
        "R1 report evidence references",
        "warn" if report_evidence_issues else "pass",
        "R1 reports reference unknown evidence IDs" if report_evidence_issues else "R1 reports do not reference unknown evidence IDs",
        issues=report_evidence_issues,
    ))
    report_evidence_advisories = _report_evidence_advisories(reports, receipts)
    checks.append(_check(
        "r1.report_evidence_advisory",
        "R1 report evidence advisory",
        "info" if report_evidence_advisories else "pass",
        "R1 reports use legacy or broad evidence references" if report_evidence_advisories else "R1 reports include structured startup evidence references",
        advisories=report_evidence_advisories,
    ))

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
