"""Read-only Deal OS R1.5 dispute summary helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from services import deal_store
from services import ic_policy


DEAL_DISPUTES_SUMMARY_SCHEMA = "siq_deal_r1_5_disputes_summary_v1"
DISPUTES_JSON_PATH = "phases/r1_5_disputes.json"
DISPUTES_MARKDOWN_PATH = "discussion/02_R1.5_\u88c1\u51b3\u8bb0\u5f55.md"


def _require_package_dir(deal_id: str, *, wiki_root: Path | str | None = None) -> Path:
    package_dir = deal_store.safe_deal_dir(deal_id, wiki_root=wiki_root)
    if not (package_dir / "manifest.json").is_file():
        raise FileNotFoundError(deal_id)
    return package_dir


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _artifact(package_dir: Path, relative_path: str) -> dict[str, Any]:
    return {
        "path": relative_path,
        "available": (package_dir / relative_path).is_file(),
    }


def _dedupe_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _string_values(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "resolved", "pass"}:
            return True
        if normalized in {"false", "no", "0", "unresolved", "warn", "missing"}:
            return False
    return bool(value)


def _canonical_agent_ids(values: list[Any]) -> list[str]:
    canonical: list[str] = []
    for value in values:
        agent_id = str(value or "").strip()
        if agent_id:
            canonical.append(ic_policy.canonical_ic_profile_id(agent_id))
    return _dedupe_strings(canonical)


def _position_agents(positions: list[Any]) -> list[Any]:
    agent_ids: list[Any] = []
    for position in positions:
        if isinstance(position, dict):
            agent_ids.append(position.get("agent_id") or position.get("profile_id"))
    return agent_ids


def _position_evidence_ids(positions: list[Any]) -> list[Any]:
    evidence_ids: list[Any] = []
    for position in positions:
        if not isinstance(position, dict):
            continue
        evidence_ids.extend(_string_values(position.get("evidence_ids")))
        evidence_ids.extend(_string_values(position.get("evidence_id")))
    return evidence_ids


def _required_followups(dispute: dict[str, Any], ruling: dict[str, Any]) -> list[str]:
    return _dedupe_strings(
        _string_values(dispute.get("required_followups"))
        + _string_values(dispute.get("required_followup"))
        + _string_values(ruling.get("required_followups"))
        + _string_values(ruling.get("required_followup"))
    )


def _summarize_dispute(dispute: dict[str, Any], index: int) -> dict[str, Any]:
    positions = _as_list(dispute.get("positions"))
    ruling = _as_dict(dispute.get("chairman_ruling"))
    agent_ids = _canonical_agent_ids(
        _string_values(dispute.get("agent_ids"))
        + _string_values(dispute.get("agent_id"))
        + _position_agents(positions)
    )
    evidence_ids = _dedupe_strings(
        _string_values(dispute.get("evidence_ids"))
        + _string_values(dispute.get("evidence_id"))
        + _position_evidence_ids(positions)
    )
    return {
        "dispute_id": str(dispute.get("dispute_id") or f"DISP-{index:03d}").strip(),
        "topic": dispute.get("topic"),
        "dimension": dispute.get("dimension"),
        "severity": dispute.get("severity"),
        "resolved": _coerce_bool(dispute.get("resolved")),
        "position_count": len(positions),
        "agent_ids": agent_ids,
        "evidence_ids": evidence_ids,
        "chairman_ruling": ruling or None,
        "required_followups": _required_followups(dispute, ruling),
    }


def _raw_dispute_items(raw: Any) -> list[Any]:
    if isinstance(raw, dict):
        return _as_list(raw.get("disputes"))
    return _as_list(raw)


def _warnings(disputes: list[dict[str, Any]], *, json_available: bool) -> list[str]:
    warnings: list[str] = []
    if not json_available:
        warnings.append("disputes_json_missing")
    for dispute in disputes:
        dispute_id = str(dispute.get("dispute_id") or "unknown")
        if not dispute.get("resolved"):
            warnings.append(f"dispute_unresolved:{dispute_id}")
        if int(dispute.get("position_count") or 0) == 0:
            warnings.append(f"dispute_positions_missing:{dispute_id}")
        if dispute.get("resolved") and not dispute.get("chairman_ruling"):
            warnings.append(f"resolved_dispute_missing_ruling:{dispute_id}")
    return warnings


def _status(
    *,
    json_available: bool,
    markdown_available: bool,
    disputes: list[dict[str, Any]],
    warnings: list[str],
) -> str:
    if not json_available and not markdown_available and not disputes:
        return "missing"
    if warnings or any(not item.get("resolved") for item in disputes):
        return "warn"
    return "pass"


def summarize_deal_disputes_package(package_dir: Path) -> dict[str, Any]:
    raw = deal_store.redact_public_payload(deal_store.read_json(package_dir / DISPUTES_JSON_PATH, {}) or {})
    dispute_items = _raw_dispute_items(raw)
    disputes = [
        _summarize_dispute(item, index)
        for index, item in enumerate(dispute_items, start=1)
        if isinstance(item, dict)
    ]
    artifacts = {
        "json": _artifact(package_dir, DISPUTES_JSON_PATH),
        "markdown": _artifact(package_dir, DISPUTES_MARKDOWN_PATH),
    }
    warnings = _warnings(disputes, json_available=bool(artifacts["json"]["available"]))
    resolved = sum(1 for item in disputes if item.get("resolved"))
    position_count = sum(int(item.get("position_count") or 0) for item in disputes)
    ruling_count = sum(1 for item in disputes if item.get("chairman_ruling"))
    high_severity = sum(1 for item in disputes if str(item.get("severity") or "").lower() == "high")
    payload = {
        "schema_version": DEAL_DISPUTES_SUMMARY_SCHEMA,
        "deal_id": package_dir.name,
        "generated_at": deal_store.utc_now_iso(),
        "status": _status(
            json_available=bool(artifacts["json"]["available"]),
            markdown_available=bool(artifacts["markdown"]["available"]),
            disputes=disputes,
            warnings=warnings,
        ),
        "counts": {
            "disputes": len(disputes),
            "resolved": resolved,
            "unresolved": len(disputes) - resolved,
            "positions": position_count,
            "rulings": ruling_count,
            "high_severity": high_severity,
            "artifacts": sum(1 for item in artifacts.values() if item.get("available")),
        },
        "artifacts": artifacts,
        "disputes": disputes,
        "warnings": warnings,
    }
    return deal_store.redact_public_payload(payload)


def summarize_deal_disputes(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    return summarize_deal_disputes_package(package_dir)
