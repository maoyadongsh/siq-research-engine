"""Read-only Deal OS audit chain summaries."""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services import deal_store


AUDIT_SUMMARY_SCHEMA = "siq_deal_audit_summary_v1"
AUDIT_SOURCES = {
    "primary": "audit/audit_log.json",
    "fallback": "phases/audit_log.json",
}
REQUIRED_AUDIT_EVENTS = (
    "deal_created",
)
TRACKED_AUDIT_EVENTS = (
    "deal_created",
    "openclaw_imported",
    "deal_r1_5_dispute_rulings_generated",
    "r4_decision_generated",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _modified_at(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        return None


def _audit_events(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    events = payload.get("events")
    if not isinstance(events, list):
        return []
    return [event for event in events if isinstance(event, dict)]


def _source_summary(package_dir: Path, relative_path: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    path = package_dir / relative_path
    payload = deal_store.read_json(path, None)
    if payload is None:
        return {
            "path": relative_path,
            "available": False,
            "event_count": 0,
        }, None
    events = _audit_events(payload)
    summary: dict[str, Any] = {
        "path": relative_path,
        "available": True,
        "event_count": len(events),
    }
    if path.is_file():
        stat = path.stat()
        summary.update({
            "size_bytes": stat.st_size,
            "sha256": _sha256(path),
            "updated_at": _modified_at(path),
        })
    return summary, payload if isinstance(payload, dict) else None


def _event_type(event: dict[str, Any]) -> str:
    value = event.get("event_type") or event.get("type") or "audit_event"
    return str(value)


def _event_sort_key(event: dict[str, Any]) -> str:
    value = event.get("created_at")
    return str(value) if value not in (None, "") else ""


def _is_manual_override_event(event: dict[str, Any]) -> bool:
    event_type = _event_type(event)
    if "override" in event_type:
        return True
    return str(event.get("status") or "").lower() == "overridden"


def _audit_status(consistency: str, event_count: int, missing_required: list[str]) -> str:
    if event_count <= 0:
        return "missing"
    if consistency == "mismatch" or missing_required:
        return "warn"
    return "pass"


def summarize_deal_audit(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = deal_store.safe_deal_dir(deal_id, wiki_root=wiki_root)
    if not (package_dir / "manifest.json").is_file():
        raise FileNotFoundError(deal_id)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)

    primary, primary_payload = _source_summary(package_dir, AUDIT_SOURCES["primary"])
    fallback, fallback_payload = _source_summary(package_dir, AUDIT_SOURCES["fallback"])
    if primary.get("available"):
        selected = "primary"
        selected_payload = primary_payload or {}
    elif fallback.get("available"):
        selected = "fallback"
        selected_payload = fallback_payload or {}
    else:
        selected = "none"
        selected_payload = {}

    if primary.get("available") and fallback.get("available"):
        consistency = "match" if primary.get("sha256") == fallback.get("sha256") else "mismatch"
    elif primary.get("available") or fallback.get("available"):
        consistency = "single_source"
    else:
        consistency = "missing"

    events = _audit_events(selected_payload)
    event_types = Counter(_event_type(event) for event in events)
    missing_required = [event_type for event_type in REQUIRED_AUDIT_EVENTS if event_types.get(event_type, 0) <= 0]
    latest_event = max(events, key=_event_sort_key) if events else None

    warnings: list[str] = []
    if consistency == "mismatch":
        warnings.append("audit_sources_mismatch")
    if consistency == "single_source":
        warnings.append("audit_single_source")
    if consistency == "missing":
        warnings.append("audit_sources_missing")
    for event_type in missing_required:
        warnings.append(f"required_event_missing:{event_type}")

    payload = {
        "schema_version": AUDIT_SUMMARY_SCHEMA,
        "deal_id": normalized_deal_id,
        "status": _audit_status(consistency, len(events), missing_required),
        "generated_at": deal_store.utc_now_iso(),
        "sources": {
            "primary": primary,
            "fallback": fallback,
            "selected": selected,
            "consistency": consistency,
        },
        "counts": {
            "events": len(events),
            "event_types": dict(sorted(event_types.items())),
            "human_confirmation": sum(1 for event in events if "confirmation" in _event_type(event)),
            "manual_override": sum(1 for event in events if _is_manual_override_event(event)),
        },
        "latest_event": latest_event,
        "required_event_status": [
            {
                "event_type": event_type,
                "present": event_types.get(event_type, 0) > 0,
                "count": event_types.get(event_type, 0),
                "required": event_type in REQUIRED_AUDIT_EVENTS,
            }
            for event_type in TRACKED_AUDIT_EVENTS
        ],
        "warnings": warnings,
    }
    return deal_store.redact_public_payload(payload)
