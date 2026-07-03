"""Deal OS R4 decision human confirmation helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from services import deal_reports
from services import deal_store


DECISION_HUMAN_CONFIRMATION_SCHEMA = "siq_deal_r4_human_confirmation_update_v1"
R4_DECISION_PATH = "phases/r4_decision.json"
ALLOWED_CONFIRMATION_STATUSES = {"confirmed", "rejected", "overridden"}
REASON_REQUIRED_STATUSES = {"rejected", "overridden"}


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
        raise ValueError("status must be confirmed, rejected, or overridden")
    return status


def _reason(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


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
        raise ValueError("override_reason is required for rejected or overridden decisions")
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
    }
    if dry_run:
        result["decision_contract"] = deal_reports.summarize_r4_decision(normalized_deal_id, wiki_root=wiki_root)
        return deal_store.redact_public_payload(result)

    deal_store.write_json(decision_path, planned_decision)
    deal_store.append_audit_event(
        normalized_deal_id,
        {
            "event_type": "r4_human_confirmation_updated",
            "status": confirmation["status"],
            "confirmed_by": confirmation.get("confirmed_by"),
            "override_reason": confirmation.get("override_reason"),
        },
        wiki_root=wiki_root,
    )
    result["decision_contract"] = deal_reports.summarize_r4_decision(normalized_deal_id, wiki_root=wiki_root)
    return deal_store.redact_public_payload(result)
