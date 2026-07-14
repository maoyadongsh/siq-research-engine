"""Deal OS R4 decision human confirmation helpers."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from services import deal_reports, deal_store

DECISION_HUMAN_CONFIRMATION_SCHEMA = "siq_deal_r4_human_confirmation_update_v2"
HUMAN_CONFIRMATION_ATTESTATION_SCHEMA = "siq_ic_human_confirmation_attestation_v1"
R4_DECISION_PATH = "phases/r4_decision.json"
ALLOWED_CONFIRMATION_STATUSES = {"confirmed", "rejected", "needs_revision", "overridden"}
REASON_REQUIRED_STATUSES = {"rejected", "needs_revision", "overridden"}
R4_QUALITY_PATH = "decision/report_quality.json"
R4_FACTCHECK_PATH = "decision/factcheck.json"
WORKFLOW_RUNS_PATH = "phases/ic_workflow_runs.json"
HUMAN_CONFIRMATION_PROVENANCE_FIELDS = (
    "status",
    "confirmed_by",
    "confirmed_at",
    "report_id",
    "report_revision",
    "workflow_run_id",
    "evidence_snapshot_hash",
    "decision_sha256",
    "quality_sha256",
    "factcheck_sha256",
)
HUMAN_CONFIRMATION_ALLOWED_FIELDS = {
    *HUMAN_CONFIRMATION_PROVENANCE_FIELDS,
    "attestation_schema_version",
    "confirmed",
    "override_reason",
    "override_decision",
    "override_score",
}
HUMAN_CONFIRMATION_AUDIT_FIELDS = (
    "status",
    "confirmed_by",
    "report_id",
    "report_revision",
    "workflow_run_id",
    "evidence_snapshot_hash",
    "decision_sha256",
    "quality_sha256",
    "factcheck_sha256",
    "override_reason",
    "override_decision",
    "override_score",
)


def _require_package_dir(deal_id: str, *, wiki_root: Path | str | None = None) -> Path:
    package_dir = deal_store.safe_deal_dir(deal_id, wiki_root=wiki_root)
    if not (package_dir / "manifest.json").is_file():
        raise FileNotFoundError(deal_id)
    return package_dir


def _public_user_payload(user: dict[str, Any] | None) -> dict[str, Any]:
    payload = user if isinstance(user, dict) else {}
    return {
        key: payload[key]
        for key in ("id", "username")
        if payload.get(key) not in (None, "")
    }


def _confirmation_status(value: str) -> str:
    status = str(value or "").strip().lower()
    if status not in ALLOWED_CONFIRMATION_STATUSES:
        raise ValueError("status must be confirmed, rejected, needs_revision, or overridden")
    return status


def _reason(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _payload_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _reviewed_decision_body(decision: dict[str, Any]) -> dict[str, Any]:
    body = deepcopy(decision)
    body.pop("human_confirmation", None)
    return body


def _confirmation_attestation(
    decision: dict[str, Any],
    *,
    quality: dict[str, Any],
    factcheck: dict[str, Any],
) -> dict[str, Any]:
    report_id = str(decision.get("report_id") or "").strip()
    workflow_run_id = str(decision.get("workflow_run_id") or "").strip()
    snapshot_hash = str(decision.get("evidence_snapshot_hash") or "").strip()
    revision = decision.get("revision")
    if not report_id or not workflow_run_id or not snapshot_hash or not isinstance(revision, int):
        raise ValueError("R4 human confirmation requires report_id, revision, workflow_run_id, and snapshot")
    return {
        "attestation_schema_version": HUMAN_CONFIRMATION_ATTESTATION_SCHEMA,
        "report_id": report_id,
        "report_revision": revision,
        "workflow_run_id": workflow_run_id,
        "evidence_snapshot_hash": snapshot_hash,
        "decision_sha256": _payload_sha256(_reviewed_decision_body(decision)),
        "quality_sha256": _payload_sha256(quality),
        "factcheck_sha256": _payload_sha256(factcheck),
    }


def _valid_aware_timestamp(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _validate_confirmation_provenance(
    confirmation: Mapping[str, Any],
    *,
    deal_id: Any,
    workflow_runs: Mapping[str, Any],
    audit: Mapping[str, Any],
) -> None:
    workflow_run_id = confirmation.get("workflow_run_id")
    matching_runs = [
        item
        for item in workflow_runs.get("runs", [])
        if isinstance(item, dict) and item.get("workflow_run_id") == workflow_run_id
    ]
    if len(matching_runs) != 1 or matching_runs[0].get("status") != "completed":
        raise ValueError("R4 human confirmation workflow completion is missing or ambiguous")
    run = matching_runs[0]
    if (
        run.get("deal_id") != deal_id
        or run.get("evidence_snapshot_hash")
        != confirmation.get("evidence_snapshot_hash")
    ):
        raise ValueError("R4 human confirmation workflow identity mismatch")
    if run.get("completed_at") != confirmation.get("confirmed_at"):
        raise ValueError("R4 human confirmation workflow completion timestamp mismatch")
    completion = run.get("completion")
    if not isinstance(completion, dict) or any(
        completion.get(key) != confirmation.get(key)
        for key in HUMAN_CONFIRMATION_PROVENANCE_FIELDS
    ):
        raise ValueError("R4 human confirmation workflow attestation mismatch")

    matching_events = [
        item
        for item in audit.get("events", [])
        if isinstance(item, dict)
        and item.get("event_type") == "r4_human_confirmation_updated"
        and all(
            item.get(key) == confirmation.get(key)
            for key in HUMAN_CONFIRMATION_AUDIT_FIELDS
        )
    ]
    if len(matching_events) != 1:
        raise ValueError("R4 human confirmation audit provenance is missing or ambiguous")


def validate_human_confirmation_attestation(
    decision: Mapping[str, Any],
    *,
    quality: Mapping[str, Any],
    factcheck: Mapping[str, Any],
    workflow_runs: Mapping[str, Any] | None = None,
    audit: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    decision_payload = dict(decision)
    confirmation = decision_payload.get("human_confirmation")
    if not isinstance(confirmation, dict):
        raise ValueError("R4 human confirmation attestation is missing")
    unexpected = sorted(set(confirmation) - HUMAN_CONFIRMATION_ALLOWED_FIELDS)
    if unexpected:
        raise ValueError(
            "R4 human confirmation contains unexpected fields: " + ",".join(unexpected)
        )
    status = str(confirmation.get("status") or "").strip().lower()
    if status not in {"confirmed", "overridden"}:
        raise ValueError("R4 decision does not have a terminal trusted human confirmation")
    if confirmation.get("confirmed") is not (status == "confirmed"):
        raise ValueError("R4 human confirmation boolean is inconsistent with status")
    if status == "overridden" and not str(confirmation.get("override_reason") or "").strip():
        raise ValueError("R4 overridden decision is missing override_reason")
    actor = confirmation.get("confirmed_by")
    if not isinstance(actor, dict) or not actor.get("id") or not actor.get("username"):
        raise ValueError("R4 human confirmation actor identity is incomplete")
    if not _valid_aware_timestamp(confirmation.get("confirmed_at")):
        raise ValueError("R4 human confirmation timestamp is invalid")
    expected = _confirmation_attestation(
        decision_payload,
        quality=dict(quality),
        factcheck=dict(factcheck),
    )
    mismatches = [
        key
        for key, value in expected.items()
        if confirmation.get(key) != value
    ]
    if mismatches:
        raise ValueError(
            "R4 human confirmation attestation mismatch: " + ",".join(mismatches)
        )
    if str(decision_payload.get("generation_mode") or "") != "model":
        raise ValueError("R4 human confirmation is not bound to a model decision")
    if (
        quality.get("allowed_for_human_confirmation") is not True
        or quality.get("report_id") != decision_payload.get("report_id")
        or quality.get("report_revision") != decision_payload.get("revision")
        or quality.get("evidence_snapshot_hash")
        != decision_payload.get("evidence_snapshot_hash")
    ):
        raise ValueError("R4 human confirmation quality contract is not eligible or current")
    if (
        str(factcheck.get("status") or "").lower() == "fail"
        or factcheck.get("report_id") != decision_payload.get("report_id")
        or factcheck.get("report_revision") != decision_payload.get("revision")
        or factcheck.get("evidence_snapshot_hash")
        != decision_payload.get("evidence_snapshot_hash")
    ):
        raise ValueError("R4 human confirmation factcheck contract is failed or stale")
    if (workflow_runs is None) != (audit is None):
        raise ValueError("R4 human confirmation provenance inputs must be provided together")
    if workflow_runs is not None and audit is not None:
        _validate_confirmation_provenance(
            confirmation,
            deal_id=decision_payload.get("deal_id"),
            workflow_runs=workflow_runs,
            audit=audit,
        )
    return deepcopy(confirmation)


def build_human_confirmation_payload(
    *,
    status: str,
    confirmed_by: dict[str, Any] | None,
    override_reason: str | None = None,
    override_decision: str | None = None,
    override_score: float | int | str | None = None,
) -> dict[str, Any]:
    normalized_status = _confirmation_status(status)
    reason = _reason(override_reason)
    if normalized_status in REASON_REQUIRED_STATUSES and not reason:
        raise ValueError("override_reason is required for rejected, needs_revision, or overridden decisions")
    payload: dict[str, Any] = {
        "status": normalized_status,
        "confirmed": normalized_status == "confirmed",
        "confirmed_by": _public_user_payload(confirmed_by),
        "confirmed_at": deal_store.utc_now_iso(),
        "override_reason": reason,
    }
    decision = _reason(override_decision)
    if decision:
        payload["override_decision"] = decision
    if override_score not in (None, ""):
        payload["override_score"] = override_score
    return payload


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _human_confirmation_gate(
    package_dir: Path,
    decision: dict[str, Any],
    *,
    status: str,
) -> dict[str, Any]:
    quality = deal_store.read_json(package_dir / R4_QUALITY_PATH, None)
    factcheck = deal_store.read_json(package_dir / R4_FACTCHECK_PATH, None)
    workflow_runs = deal_store.read_json(package_dir / WORKFLOW_RUNS_PATH, None)
    workflow_run_id = str(decision.get("workflow_run_id") or "")
    matching_workflow_run = next(
        (
            item
            for item in (workflow_runs.get("runs") or [])
            if isinstance(item, dict) and item.get("workflow_run_id") == workflow_run_id
        ),
        None,
    ) if isinstance(workflow_runs, dict) else None
    if status not in {"confirmed", "overridden"}:
        return {
            "allowed": True,
            "blocking_reasons": [],
            "quality": quality if isinstance(quality, dict) else None,
            "factcheck": factcheck if isinstance(factcheck, dict) else None,
            "workflow_run": matching_workflow_run,
        }
    blocks: list[str] = []
    if str(decision.get("generation_mode") or "") != "model":
        blocks.append("formal_model_r4_required")
    if not workflow_run_id:
        blocks.append("workflow_run_id_missing")
    elif not isinstance(matching_workflow_run, dict):
        blocks.append("workflow_run_missing")
    else:
        if matching_workflow_run.get("deal_id") != decision.get("deal_id"):
            blocks.append("workflow_run_deal_id_mismatch")
        if matching_workflow_run.get("evidence_snapshot_hash") != decision.get("evidence_snapshot_hash"):
            blocks.append("workflow_run_snapshot_mismatch")
        if matching_workflow_run.get("status") != "active":
            blocks.append("workflow_run_not_active")
    if not isinstance(quality, dict):
        blocks.append("report_quality_missing")
    else:
        if quality.get("allowed_for_human_confirmation") is not True:
            blocks.extend(str(item) for item in quality.get("blocking_reasons") or ["report_quality_blocked"])
        if quality.get("report_id") != decision.get("report_id"):
            blocks.append("report_quality_report_id_mismatch")
        if quality.get("report_revision") != decision.get("revision"):
            blocks.append("report_quality_report_revision_mismatch")
        if quality.get("evidence_snapshot_hash") != decision.get("evidence_snapshot_hash"):
            blocks.append("report_quality_snapshot_mismatch")
    if not isinstance(factcheck, dict):
        blocks.append("factcheck_missing")
    else:
        if str(factcheck.get("status") or "").lower() == "fail":
            blocks.append("factcheck_failed")
        if factcheck.get("report_id") != decision.get("report_id"):
            blocks.append("factcheck_report_id_mismatch")
        if factcheck.get("report_revision") != decision.get("revision"):
            blocks.append("factcheck_report_revision_mismatch")
        if factcheck.get("evidence_snapshot_hash") != decision.get("evidence_snapshot_hash"):
            blocks.append("factcheck_snapshot_mismatch")
    return {
        "allowed": not blocks,
        "blocking_reasons": list(dict.fromkeys(blocks)),
        "quality": quality if isinstance(quality, dict) else None,
        "factcheck": factcheck if isinstance(factcheck, dict) else None,
        "workflow_run": matching_workflow_run,
    }


def _complete_workflow_run(
    package_dir: Path,
    *,
    confirmation: dict[str, Any],
) -> dict[str, Any] | None:
    if confirmation.get("status") not in {"confirmed", "overridden"}:
        return None
    workflow_run_id = str(confirmation.get("workflow_run_id") or "")
    completed: dict[str, Any] | None = None

    def update(current: Any) -> dict[str, Any]:
        nonlocal completed
        if not isinstance(current, dict):
            raise ValueError("workflow run store is missing")
        runs = [dict(item) for item in current.get("runs") or [] if isinstance(item, dict)]
        for run in runs:
            if run.get("workflow_run_id") != workflow_run_id:
                continue
            if run.get("status") != "active":
                raise ValueError("workflow run is not active")
            run["status"] = "completed"
            run["completed_at"] = confirmation["confirmed_at"]
            run["updated_at"] = confirmation["confirmed_at"]
            run["completion"] = {
                key: confirmation.get(key)
                for key in HUMAN_CONFIRMATION_PROVENANCE_FIELDS
            }
            completed = dict(run)
            break
        if completed is None:
            raise ValueError("workflow run is missing")
        return {**current, "runs": runs, "updated_at": confirmation["confirmed_at"]}

    deal_store.update_json(package_dir / WORKFLOW_RUNS_PATH, update, default={})
    return completed


def _sync_confirmation_state(
    package_dir: Path,
    *,
    decision: dict[str, Any],
    confirmation: dict[str, Any],
) -> None:
    now = deal_store.utc_now_iso()
    status = str(confirmation.get("status") or "pending")
    override_decision = confirmation.get("override_decision")
    override_score = _numeric(confirmation.get("override_score"))
    decision_value = decision.get("decision")
    decision_score = _numeric(decision.get("final_score"))

    workflow_path = package_dir / "phases" / "workflow_state.json"
    workflow = deal_store.read_json(workflow_path, {}) or {}
    if isinstance(workflow, dict):
        phases = workflow.setdefault("phases", {})
        if not isinstance(phases, dict):
            phases = {}
            workflow["phases"] = phases
        r4 = phases.setdefault("R4", {})
        if not isinstance(r4, dict):
            r4 = {}
            phases["R4"] = r4
        r4.update({
            "human_confirmation_status": status,
            "human_confirmation": confirmation,
            "human_confirmation_updated_at": now,
        })
        if override_decision:
            r4["manual_override_decision"] = override_decision
        if override_score is not None:
            r4["manual_override_score"] = override_score
        if status == "confirmed":
            if decision_value:
                workflow["final_decision"] = decision_value
            if decision_score is not None:
                workflow["final_score"] = decision_score
        if status == "overridden":
            workflow["final_decision"] = "manual_override"
            if override_decision:
                workflow["manual_override_decision"] = override_decision
            if override_score is not None:
                workflow["final_score"] = override_score
        workflow["updated_at"] = now
        deal_store.write_json(workflow_path, workflow)

    project_meta_path = package_dir / "project_meta.json"
    project_meta = deal_store.read_json(project_meta_path, {}) or {}
    if isinstance(project_meta, dict):
        project_meta.update({
            "human_confirmation_status": status,
            "human_confirmation": confirmation,
            "updated_at": now,
        })
        if status == "confirmed":
            project_meta["final_decision"] = decision_value or project_meta.get("final_decision")
            if decision_score is not None:
                project_meta["final_score"] = decision_score
        if status == "overridden":
            project_meta["final_decision"] = "manual_override"
            if override_decision:
                project_meta["manual_override_decision"] = override_decision
            if override_score is not None:
                project_meta["final_score"] = override_score
        deal_store.write_json(project_meta_path, project_meta)


def update_human_confirmation(
    deal_id: str,
    *,
    status: str,
    confirmed_by: dict[str, Any] | None = None,
    override_reason: str | None = None,
    override_decision: str | None = None,
    override_score: float | int | str | None = None,
    dry_run: bool = True,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    decision_path = package_dir / R4_DECISION_PATH
    decision = deal_store.read_json(decision_path, None)
    if not isinstance(decision, dict) or not decision:
        raise FileNotFoundError(R4_DECISION_PATH)
    previous = decision.get("human_confirmation") if isinstance(decision.get("human_confirmation"), dict) else {}
    confirmation = build_human_confirmation_payload(
        status=status,
        confirmed_by=confirmed_by,
        override_reason=override_reason,
        override_decision=override_decision,
        override_score=override_score,
    )
    confirmation_gate = _human_confirmation_gate(
        package_dir,
        decision,
        status=confirmation["status"],
    )
    if not confirmation_gate["allowed"]:
        raise ValueError(
            "R4 human confirmation blocked: "
            + ", ".join(confirmation_gate["blocking_reasons"])
        )
    actor = confirmation.get("confirmed_by")
    if not isinstance(actor, dict) or not actor.get("id") or not actor.get("username"):
        raise ValueError("confirmed_by must include id and username")
    confirmation.update(
        _confirmation_attestation(
            decision,
            quality=confirmation_gate["quality"],
            factcheck=confirmation_gate["factcheck"],
        )
    )
    planned_decision = dict(decision)
    planned_decision["human_confirmation"] = confirmation

    result: dict[str, Any] = {
        "schema_version": DECISION_HUMAN_CONFIRMATION_SCHEMA,
        "deal_id": normalized_deal_id,
        "dry_run": bool(dry_run),
        "would_write": not dry_run,
        "decision_path": R4_DECISION_PATH,
        "previous_human_confirmation": previous,
        "human_confirmation": confirmation,
        "confirmation_gate": confirmation_gate,
    }
    if dry_run:
        result["decision_contract"] = deal_reports.summarize_r4_decision(normalized_deal_id, wiki_root=wiki_root)
        return deal_store.redact_public_payload(result)

    deal_store.write_json(decision_path, planned_decision)
    _sync_confirmation_state(
        package_dir,
        decision=planned_decision,
        confirmation=confirmation,
    )
    completed_workflow_run = _complete_workflow_run(
        package_dir,
        confirmation=confirmation,
    )
    deal_store.append_audit_event(
        normalized_deal_id,
        {
            "event_type": "r4_human_confirmation_updated",
            "status": confirmation["status"],
            "confirmed_by": confirmation.get("confirmed_by"),
            "override_reason": confirmation.get("override_reason"),
            "override_decision": confirmation.get("override_decision"),
            "override_score": confirmation.get("override_score"),
            "report_id": confirmation.get("report_id"),
            "report_revision": confirmation.get("report_revision"),
            "workflow_run_id": confirmation.get("workflow_run_id"),
            "evidence_snapshot_hash": confirmation.get("evidence_snapshot_hash"),
            "decision_sha256": confirmation.get("decision_sha256"),
            "quality_sha256": confirmation.get("quality_sha256"),
            "factcheck_sha256": confirmation.get("factcheck_sha256"),
        },
        wiki_root=wiki_root,
    )
    result["decision_contract"] = deal_reports.summarize_r4_decision(normalized_deal_id, wiki_root=wiki_root)
    result["workflow_run"] = completed_workflow_run
    return deal_store.redact_public_payload(result)
