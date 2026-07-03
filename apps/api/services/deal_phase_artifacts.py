"""Read-only Deal OS R0-R4 phase artifact summary helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from services import deal_store
from services import ic_policy


DEAL_PHASE_ARTIFACTS_SUMMARY_SCHEMA = "siq_deal_phase_artifacts_summary_v1"
R0_MARKDOWN_PATH = "discussion/00_\u9879\u76ee\u4fe1\u606f_R0.md"
R1_MARKDOWN_PATH = "discussion/01_R1_\u5c3d\u8c03\u6c47\u603b.md"
R1_5_MARKDOWN_PATH = "discussion/02_R1.5_\u88c1\u51b3\u8bb0\u5f55.md"
R2_MARKDOWN_PATH = "discussion/03_R2_\u89c2\u70b9\u5b8c\u5584\u6c47\u603b.md"
R3_MARKDOWN_PATH = "discussion/04_R3_\u7ea2\u84dd\u5bf9\u6297.md"

PHASE_ARTIFACTS: tuple[dict[str, str], ...] = (
    {"phase": "R0", "label": "R0 Intake", "json_path": "project_meta.json", "markdown_path": R0_MARKDOWN_PATH},
    {"phase": "R1", "label": "R1 Expert Diligence", "json_path": "phases/r1_reports.json", "markdown_path": R1_MARKDOWN_PATH},
    {"phase": "R1.5", "label": "R1.5 Disputes", "json_path": "phases/r1_5_disputes.json", "markdown_path": R1_5_MARKDOWN_PATH},
    {"phase": "R2", "label": "R2 Opinion Refinement", "json_path": "phases/r2_reports.json", "markdown_path": R2_MARKDOWN_PATH},
    {"phase": "R3", "label": "R3 Red Blue Review", "json_path": "phases/r3_reports.json", "markdown_path": R3_MARKDOWN_PATH},
    {"phase": "R4", "label": "R4 Decision", "json_path": "phases/r4_decision.json", "markdown_path": "decision/IC_DECISION_REPORT.md"},
)
META_KEYS = {
    "schema_version",
    "deal_id",
    "project_id",
    "legacy_project_id",
    "round_name",
    "phase",
    "mode",
    "status",
    "skip_reason",
    "reason",
    "created_at",
    "updated_at",
}


def _require_package_dir(deal_id: str, *, wiki_root: Path | str | None = None) -> Path:
    package_dir = deal_store.safe_deal_dir(deal_id, wiki_root=wiki_root)
    if not (package_dir / "manifest.json").is_file():
        raise FileNotFoundError(deal_id)
    return package_dir


def _artifact(package_dir: Path, relative_path: str) -> dict[str, Any]:
    return {
        "path": relative_path,
        "available": (package_dir / relative_path).is_file(),
    }


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict_items(value: dict[str, Any], *, key_field: str) -> list[Any]:
    items: list[Any] = []
    for key, item in value.items():
        if isinstance(item, dict):
            normalized = dict(item)
            normalized.setdefault(key_field, key)
            items.append(normalized)
            continue
        items.append(item)
    return items


def _phase_items(raw: Any, phase: str) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, dict) or not raw:
        return []
    for key in ("reports", "items", "disputes", "agents", "entries"):
        value = raw.get(key)
        if isinstance(value, dict):
            key_field = "dispute_id" if key == "disputes" else "agent_id"
            return _dict_items(value, key_field=key_field)
        if isinstance(value, list):
            return value
    keyed_items = [
        dict(value, agent_id=key) if "agent_id" not in value and "profile_id" not in value else value
        for key, value in raw.items()
        if key not in META_KEYS and isinstance(value, dict)
    ]
    if keyed_items:
        return keyed_items
    return [raw] if phase in {"R0", "R4"} else []


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _mode(raw: Any, phase: str) -> str:
    payload = _as_dict(raw)
    mode = str(payload.get("mode") or payload.get("status") or "").strip().lower()
    if phase == "R3" and mode in {"skip", "skipped"}:
        return "skip"
    if mode in {"skip", "skipped"}:
        return "skip"
    if payload:
        return "normal"
    return "unknown"


def _skip_reason(raw: Any) -> str | None:
    payload = _as_dict(raw)
    return _text(payload.get("skip_reason") or payload.get("reason"))


def _has_skip_reports_marker(raw: Any) -> bool:
    payload = _as_dict(raw)
    return "reports" in payload and isinstance(payload.get("reports"), (dict, list))


def _preview_item(item: Any, index: int) -> dict[str, Any]:
    payload = _as_dict(item)
    agent_id = _text(payload.get("agent_id") or payload.get("profile_id"))
    if agent_id:
        agent_id = ic_policy.canonical_ic_profile_id(agent_id)
    summary = (
        payload.get("summary")
        or payload.get("topic")
        or payload.get("rationale")
        or payload.get("chairman_qualitative_decision")
        or payload.get("decision")
    )
    return {
        "item_id": _text(payload.get("report_id") or payload.get("dispute_id") or payload.get("id"))
        or f"ITEM-{index:03d}",
        "agent_id": agent_id,
        "summary": _text(summary),
        "recommendation": _text(payload.get("recommendation") or payload.get("decision")),
        "score": payload.get("score") if payload.get("score") not in (None, "") else payload.get("final_score"),
        "severity": _text(payload.get("severity")),
        "resolved": payload.get("resolved") if isinstance(payload.get("resolved"), bool) else None,
    }


def _phase_warnings(
    phase: str,
    *,
    json_available: bool,
    markdown_available: bool,
    mode: str,
    skip_reason: str | None,
    skip_reports_marker: bool,
    item_count: int,
) -> list[str]:
    warnings: list[str] = []
    if not json_available and not markdown_available:
        warnings.append(f"phase_artifacts_missing:{phase}")
    if json_available and item_count == 0 and phase in {"R1", "R2", "R4"}:
        warnings.append(f"phase_items_empty:{phase}")
    if phase == "R3" and mode == "skip" and not skip_reason and not skip_reports_marker:
        warnings.append("r3_skip_reason_missing")
    if phase == "R3" and json_available and item_count == 0 and mode != "skip":
        warnings.append("phase_items_empty:R3")
    return warnings


def _phase_status(json_available: bool, markdown_available: bool, warnings: list[str]) -> str:
    if not json_available and not markdown_available:
        return "missing"
    if warnings:
        return "warn"
    return "pass"


def _summarize_phase(package_dir: Path, spec: dict[str, str]) -> dict[str, Any]:
    phase = spec["phase"]
    json_artifact = _artifact(package_dir, spec["json_path"])
    markdown_artifact = _artifact(package_dir, spec["markdown_path"])
    raw = deal_store.redact_public_payload(deal_store.read_json(package_dir / spec["json_path"], {}) or {})
    items = _phase_items(raw, phase)
    mode = _mode(raw, phase)
    skip_reason = _skip_reason(raw)
    skip_reports_marker = _has_skip_reports_marker(raw)
    warnings = _phase_warnings(
        phase,
        json_available=bool(json_artifact["available"]),
        markdown_available=bool(markdown_artifact["available"]),
        mode=mode,
        skip_reason=skip_reason,
        skip_reports_marker=skip_reports_marker,
        item_count=len(items),
    )
    return {
        "phase": phase,
        "label": spec["label"],
        "status": _phase_status(bool(json_artifact["available"]), bool(markdown_artifact["available"]), warnings),
        "blocking": phase == "R3" and "r3_skip_reason_missing" in warnings,
        "mode": mode,
        "skip_reason": skip_reason,
        "artifacts": {
            "json": json_artifact,
            "markdown": markdown_artifact,
        },
        "counts": {
            "items": len(items),
            "warnings": len(warnings),
        },
        "items_preview": [
            _preview_item(item, index)
            for index, item in enumerate(items[:5], start=1)
            if isinstance(item, dict)
        ],
        "warnings": warnings,
    }


def _summary_status(phases: list[dict[str, Any]]) -> str:
    statuses = {str(item.get("status") or "") for item in phases}
    if statuses == {"missing"}:
        return "missing"
    if statuses.intersection({"warn", "missing"}):
        return "warn"
    return "pass"


def _counts(phases: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "phases": len(phases),
        "pass": 0,
        "warn": 0,
        "missing": 0,
        "available_json": 0,
        "available_markdown": 0,
        "items": 0,
        "blocking": 0,
    }
    for phase in phases:
        status = str(phase.get("status") or "")
        if status in counts:
            counts[status] += 1
        artifacts = _as_dict(phase.get("artifacts"))
        if _as_dict(artifacts.get("json")).get("available"):
            counts["available_json"] += 1
        if _as_dict(artifacts.get("markdown")).get("available"):
            counts["available_markdown"] += 1
        phase_counts = _as_dict(phase.get("counts"))
        counts["items"] += int(phase_counts.get("items") or 0)
        if phase.get("blocking"):
            counts["blocking"] += 1
    return counts


def summarize_deal_phase_artifacts(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    phases = [_summarize_phase(package_dir, spec) for spec in PHASE_ARTIFACTS]
    payload = {
        "schema_version": DEAL_PHASE_ARTIFACTS_SUMMARY_SCHEMA,
        "deal_id": deal_store.validate_deal_id(deal_id),
        "generated_at": deal_store.utc_now_iso(),
        "status": _summary_status(phases),
        "counts": _counts(phases),
        "phases": phases,
        "warnings": [
            warning
            for phase in phases
            for warning in _as_list(phase.get("warnings"))
        ],
    }
    return deal_store.redact_public_payload(payload)
