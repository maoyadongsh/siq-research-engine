"""File-backed R1 expert report submission boundary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services import deal_reports
from services import deal_store
from services import ic_policy


R1_EXPERT_REPORT_SUBMISSION_SCHEMA = "siq_ic_r1_expert_report_submission_v1"
R1_AGENT_REPORT_SCHEMA = "siq_ic_r1_agent_report_v1"
R1_REPORTS_PATH = "phases/r1_reports.json"
STARTUP_RECEIPTS_PATH = "phases/startup_receipts.json"


def _require_package_dir(deal_id: str, *, wiki_root: Path | str | None = None) -> Path:
    package_dir = deal_store.safe_deal_dir(deal_id, wiki_root=wiki_root)
    if not (package_dir / "manifest.json").is_file():
        raise FileNotFoundError(deal_id)
    return package_dir


def _coalesce_payload(
    report_payload: dict[str, Any] | None,
    *,
    payload: dict[str, Any] | None = None,
    report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected = report_payload if report_payload is not None else payload if payload is not None else report
    if not isinstance(selected, dict):
        raise ValueError("report_payload must be a JSON object")
    return dict(selected)


def _canonical_r1_agent(raw_agent_id: str | None) -> str:
    canonical = ic_policy.canonical_ic_profile_id(raw_agent_id)
    if canonical not in ic_policy.R1_AGENT_SEQUENCE:
        raise ValueError(f"Unknown R1 IC profile: {raw_agent_id}")
    return canonical


def _resolve_agent_id(
    *,
    agent_id: str | None,
    profile_id: str | None,
    report_payload: dict[str, Any],
) -> str:
    values = [
        value
        for value in (
            agent_id,
            profile_id,
            report_payload.get("agent_id"),
            report_payload.get("profile_id"),
        )
        if str(value or "").strip()
    ]
    if not values:
        raise ValueError("agent_id or profile_id is required")
    canonical_values = [_canonical_r1_agent(str(value)) for value in values]
    canonical = canonical_values[0]
    if any(value != canonical for value in canonical_values):
        raise ValueError("agent_id/profile_id mismatch")
    return canonical


def _round_name(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    if not normalized:
        return ""
    if normalized != "R1":
        raise ValueError("round_name must be R1")
    return "R1"


def _score(value: Any) -> int | float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0 or parsed > 100:
        raise ValueError("score must be between 0 and 100")
    return int(parsed) if parsed.is_integer() else parsed


def _missing_required_fields(payload: dict[str, Any]) -> list[str]:
    return [
        field
        for field in deal_reports.R1_REPORT_REQUIRED_FIELDS
        if field not in payload or payload.get(field) in (None, "")
    ]


def _validate_list_fields(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in ("verified", "assumed", "open_questions"):
        if field in payload and payload.get(field) not in (None, "") and not isinstance(payload.get(field), list):
            errors.append(f"{field}_not_list")
    return errors


def _string_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    if value in (None, ""):
        return []
    return [str(value).strip()]


def _artifact_path(agent_id: str) -> str:
    return f"discussion/01_R1_{agent_id.removeprefix('siq_ic_')}_report.md"


def _receipt_agents(raw: Any) -> dict[str, dict[str, Any]]:
    payload = raw if isinstance(raw, dict) else {}
    agents = payload.get("agents") if isinstance(payload.get("agents"), dict) else payload
    receipts: dict[str, dict[str, Any]] = {}
    if not isinstance(agents, dict):
        return receipts
    for key, item in agents.items():
        if not isinstance(item, dict):
            continue
        try:
            profile_id = _canonical_r1_agent(str(item.get("agent_id") or item.get("profile_id") or key))
        except ValueError:
            continue
        normalized = dict(item)
        normalized["agent_id"] = profile_id
        receipts[profile_id] = normalized
    return receipts


def _startup_receipt_status(
    package_dir: Path,
    *,
    agent_id: str,
    startup_receipt_id: str,
) -> dict[str, Any]:
    path = package_dir / STARTUP_RECEIPTS_PATH
    if not path.exists():
        raise ValueError("startup_receipts_missing")
    raw = deal_store.read_json(path, None)
    if not isinstance(raw, dict):
        raise ValueError("startup_receipts.json must contain a JSON object")
    receipt = _receipt_agents(raw).get(agent_id)
    if not isinstance(receipt, dict):
        raise ValueError(f"startup_receipt_missing:{agent_id}")
    expected_receipt_id = str(receipt.get("receipt_id") or receipt.get("startup_receipt_id") or "").strip()
    if not expected_receipt_id:
        raise ValueError(f"startup_receipt_id_missing:{agent_id}")
    if expected_receipt_id != startup_receipt_id:
        raise ValueError("startup_receipt_id_mismatch")
    receipt_round = str(receipt.get("round_name") or receipt.get("phase") or "").strip().upper()
    if receipt_round and receipt_round != "R1":
        raise ValueError("startup_receipt_round_name_mismatch")
    return {
        "path": STARTUP_RECEIPTS_PATH,
        "available": True,
        "linkage": "match",
        "agent_id": agent_id,
        "receipt_id": expected_receipt_id,
        "evidence_ids": sorted(_extract_evidence_ids(receipt.get("evidence_hits"))),
    }


def _known_evidence_ids(package_dir: Path) -> set[str]:
    ids: set[str] = set()
    path = package_dir / "evidence" / "evidence_items.ndjson"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return ids
    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("evidence_id"):
            ids.add(str(item.get("evidence_id")))
    return ids


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


def _report_evidence_ids(report: dict[str, Any]) -> list[str]:
    ids: set[str] = set()
    for key in (
        "evidence_ids",
        "evidence_refs",
        "citations",
        "verified",
        "assumed",
        "key_points",
        "risk_flags",
    ):
        ids.update(_extract_evidence_ids(report.get(key)))
    evidence_stats = report.get("evidence_stats")
    if isinstance(evidence_stats, dict):
        ids.update(_extract_evidence_ids(evidence_stats))
    return sorted(ids)


def _validate_evidence_linkage(
    package_dir: Path,
    *,
    normalized_report: dict[str, Any],
    receipt_status: dict[str, Any],
) -> None:
    evidence_ids = _report_evidence_ids(normalized_report)
    if not evidence_ids:
        raise ValueError("evidence_ids_missing")
    known_ids = _known_evidence_ids(package_dir)
    unknown_ids = sorted(set(evidence_ids) - known_ids)
    if unknown_ids:
        raise ValueError("evidence_ids_unknown:" + ",".join(unknown_ids))
    receipt_ids = set(_string_values(receipt_status.get("evidence_ids")))
    if receipt_ids and not receipt_ids.intersection(evidence_ids):
        raise ValueError("evidence_ids_not_in_startup_receipt")
    normalized_report["evidence_ids"] = evidence_ids


def _read_r1_reports(path: Path) -> dict[str, dict[str, Any]]:
    raw = deal_store.read_json(path, None)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("r1_reports.json must contain a JSON object")
    reports = raw.get("reports") if isinstance(raw.get("reports"), dict) else raw
    normalized: dict[str, dict[str, Any]] = {}
    for key, item in reports.items():
        if not isinstance(item, dict):
            continue
        try:
            profile_id = _canonical_r1_agent(str(item.get("agent_id") or item.get("profile_id") or key))
        except ValueError:
            continue
        report = dict(item)
        report["agent_id"] = profile_id
        normalized[profile_id] = report
    return normalized


def _validate_deal_reference(payload: dict[str, Any], deal_id: str) -> None:
    for field in ("deal_id", "project_id"):
        value = str(payload.get(field) or "").strip()
        if value and value != deal_id:
            raise ValueError(f"{field}_mismatch")


def _normalize_report(
    *,
    deal_id: str,
    agent_id: str,
    report_payload: dict[str, Any],
    markdown_path: str,
    created_by: dict[str, Any] | None,
) -> dict[str, Any]:
    _validate_deal_reference(report_payload, deal_id)
    normalized = dict(report_payload)
    normalized["schema_version"] = str(normalized.get("schema_version") or R1_AGENT_REPORT_SCHEMA)
    normalized["deal_id"] = deal_id
    normalized["agent_id"] = agent_id
    normalized["profile_id"] = agent_id
    normalized["round_name"] = _round_name(normalized.get("round_name") or normalized.get("phase"))
    normalized["phase"] = "R1"
    normalized["score"] = _score(normalized.get("score"))
    normalized["artifact_path"] = markdown_path
    normalized["markdown_path"] = markdown_path
    normalized["startup_receipt_id"] = str(normalized.get("startup_receipt_id") or "").strip()
    normalized["recommendation"] = str(normalized.get("recommendation") or "").strip()
    normalized.setdefault("status", "completed")
    normalized.setdefault("key_points", [])
    normalized.setdefault("risk_flags", [])
    normalized.setdefault("evidence_stats", {})
    if created_by is not None:
        normalized["created_by"] = created_by

    missing = _missing_required_fields(normalized)
    errors = [f"{field}_missing" for field in missing]
    errors.extend(_validate_list_fields(normalized))
    if normalized.get("score") is None:
        errors.append("score_missing_or_not_numeric")
    if errors:
        raise ValueError("R1 report contract invalid: " + "; ".join(errors))

    now = deal_store.utc_now_iso()
    normalized.setdefault("created_at", now)
    normalized["updated_at"] = now
    return deal_store.redact_public_payload(normalized)


def _markdown_scalar(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _markdown_list(value: Any, *, empty: str = "- None") -> list[str]:
    if isinstance(value, list) and value:
        return [f"- {_markdown_scalar(item)}" for item in value]
    if isinstance(value, dict) and value:
        return [f"- {_markdown_scalar(value)}"]
    text = str(value or "").strip()
    if text:
        return [f"- {text}"]
    return [empty]


def _render_markdown(report: dict[str, Any]) -> str:
    evidence_ids = report.get("evidence_ids")
    summary = str(report.get("summary") or "").strip() or "No summary provided."
    private_evidence = report.get("private_evidence") or report.get("private_hits") or []
    lines = [
        f"# R1 Expert Report - {report['agent_id']}",
        "",
        f"- deal_id: `{report['deal_id']}`",
        f"- agent_id: `{report['agent_id']}`",
        f"- round_name: `{report['round_name']}`",
        f"- startup_receipt_id: `{report['startup_receipt_id']}`",
        f"- score: `{report['score']}`",
        f"- recommendation: `{report['recommendation']}`",
        "",
        "## 检索结果摘要",
        "",
        summary,
        "",
        "### 共享底稿证据",
        "",
        *_markdown_list(evidence_ids),
        "",
        "### 私有知识库证据",
        "",
        *_markdown_list(private_evidence),
        "",
        "### 信息缺口清单",
        "",
        *_markdown_list(report.get("open_questions")),
        "",
        "### 检索后观点",
        "",
        "#### Verified",
        "",
        *_markdown_list(report.get("verified")),
        "",
        "#### Assumed",
        "",
        *_markdown_list(report.get("assumed")),
        "",
        "#### Key Points",
        "",
        *_markdown_list(report.get("key_points")),
        "",
        "#### Risk Flags",
        "",
        *_markdown_list(report.get("risk_flags")),
        "",
    ]
    return "\n".join(lines)


def _advance_workflow_for_r1_report(package_dir: Path, profile_id: str) -> dict[str, Any]:
    workflow_path = package_dir / "phases" / "workflow_state.json"
    workflow = deal_store.read_json(workflow_path, {}) or {}
    if not isinstance(workflow, dict):
        workflow = {}
    now = deal_store.utc_now_iso()
    phases = workflow.setdefault("phases", {})
    if not isinstance(phases, dict):
        phases = {}
        workflow["phases"] = phases
    r1 = phases.setdefault("R1", {})
    if not isinstance(r1, dict):
        r1 = {}
        phases["R1"] = r1

    submitted = r1.get("submitted_agents")
    if not isinstance(submitted, list):
        submitted = []
    normalized_submitted = [
        ic_policy.canonical_ic_profile_id(str(item))
        for item in submitted
        if str(item or "").strip()
    ]
    if profile_id not in normalized_submitted:
        normalized_submitted.append(profile_id)

    complete = all(agent_id in set(normalized_submitted) for agent_id in ic_policy.R1_AGENT_SEQUENCE)
    r1.update({
        "status": "completed" if complete else "in_progress",
        "submitted_agents": normalized_submitted,
        "latest_agent_id": profile_id,
        "updated_at": now,
    })
    r1.setdefault("started_at", now)
    if complete:
        r1["completed_at"] = now
        workflow["status"] = "r1_completed"
    else:
        r1.pop("completed_at", None)
        workflow["status"] = "r1_in_progress"
    workflow["current_phase"] = "R1"
    workflow["updated_at"] = now
    deal_store.write_json(workflow_path, workflow)
    return workflow


def submit_r1_expert_report(
    deal_id: str,
    agent_id: str | None = None,
    report_payload: dict[str, Any] | None = None,
    *,
    profile_id: str | None = None,
    payload: dict[str, Any] | None = None,
    report: dict[str, Any] | None = None,
    dry_run: bool = True,
    overwrite: bool = False,
    created_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    """Validate and optionally persist one R1 expert report."""

    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    package_dir = _require_package_dir(normalized_deal_id, wiki_root=wiki_root)
    raw_report = _coalesce_payload(report_payload, payload=payload, report=report)
    canonical_agent_id = _resolve_agent_id(
        agent_id=agent_id,
        profile_id=profile_id,
        report_payload=raw_report,
    )
    markdown_path = _artifact_path(canonical_agent_id)
    normalized_report = _normalize_report(
        deal_id=normalized_deal_id,
        agent_id=canonical_agent_id,
        report_payload=raw_report,
        markdown_path=markdown_path,
        created_by=created_by,
    )
    receipt_status = _startup_receipt_status(
        package_dir,
        agent_id=canonical_agent_id,
        startup_receipt_id=normalized_report["startup_receipt_id"],
    )
    _validate_evidence_linkage(
        package_dir,
        normalized_report=normalized_report,
        receipt_status=receipt_status,
    )

    reports_path = package_dir / R1_REPORTS_PATH
    reports = _read_r1_reports(reports_path)
    existing_report = canonical_agent_id in reports
    markdown_exists = (package_dir / markdown_path).is_file()
    conflict = (existing_report or markdown_exists) and not overwrite
    blocking_reasons = ["r1_report_already_exists"] if conflict else []
    if conflict and not dry_run:
        raise FileExistsError(f"R1 report already exists for {canonical_agent_id}")

    action = "update" if existing_report or markdown_exists else "create"
    audit_event: dict[str, Any] | None = None
    workflow: dict[str, Any] | None = None
    if not dry_run:
        reports[canonical_agent_id] = normalized_report
        deal_store.write_json(reports_path, reports)
        markdown_file = package_dir / markdown_path
        markdown_file.parent.mkdir(parents=True, exist_ok=True)
        markdown_file.write_text(_render_markdown(normalized_report), encoding="utf-8")
        workflow = _advance_workflow_for_r1_report(package_dir, canonical_agent_id)
        audit_event = deal_store.append_audit_event(
            normalized_deal_id,
            {
                "event_type": "deal_r1_expert_report_submitted",
                "deal_id": normalized_deal_id,
                "agent_id": canonical_agent_id,
                "round_name": "R1",
                "action": action,
                "overwrite": bool(overwrite),
                "startup_receipt_id": normalized_report["startup_receipt_id"],
                "json_path": R1_REPORTS_PATH,
                "markdown_path": markdown_path,
                "report_path": markdown_path,
                "score": normalized_report.get("score"),
                "recommendation": normalized_report.get("recommendation"),
                "workflow_status": workflow.get("status") if isinstance(workflow, dict) else None,
                "created_by": normalized_report.get("created_by"),
            },
            wiki_root=wiki_root,
        )

    return deal_store.redact_public_payload({
        "schema_version": R1_EXPERT_REPORT_SUBMISSION_SCHEMA,
        "deal_id": normalized_deal_id,
        "agent_id": canonical_agent_id,
        "profile_id": canonical_agent_id,
        "round_name": "R1",
        "status": "blocked" if conflict else "validated" if dry_run else "submitted",
        "action": action,
        "allowed": not conflict,
        "blocking_reasons": blocking_reasons,
        "dry_run": bool(dry_run),
        "overwrite": bool(overwrite),
        "report_written": not dry_run,
        "audit_written": audit_event is not None,
        "paths": {
            "json": R1_REPORTS_PATH,
            "markdown": markdown_path,
        },
        "startup_receipt": receipt_status,
        "report": normalized_report,
        "workflow": workflow,
        "audit_event": audit_event,
    })
