"""Build a unified Deal OS IC discussion Markdown artifact from phase JSON."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from services import deal_store
from services import ic_policy


DEAL_DISCUSSION_SCHEMA = "siq_deal_discussion_builder_v1"
DISCUSSION_MARKDOWN_PATH = "discussion/IC_DISCUSSION.md"
PREVIEW_CHARS = 4_000
MAX_TABLE_TEXT_CHARS = 180
MAX_LIST_ITEMS = 8

PHASE_SPECS: tuple[dict[str, str], ...] = (
    {"phase": "R0", "label": "R0 Intake", "json_path": "phases/r0_intake.json"},
    {"phase": "R1", "label": "R1 Expert Diligence", "json_path": "phases/r1_reports.json"},
    {"phase": "R1.5", "label": "R1.5 Disputes", "json_path": "phases/r1_5_disputes.json"},
    {"phase": "R2", "label": "R2 Opinion Refinement", "json_path": "phases/r2_reports.json"},
    {"phase": "R3", "label": "R3 Red Blue Review", "json_path": "phases/r3_reports.json"},
    {"phase": "R4", "label": "R4 Decision", "json_path": "phases/r4_decision.json"},
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
    "generated_at",
    "created_at",
    "updated_at",
    "created_by",
    "updated_by",
    "generated_by",
    "warnings",
    "counts",
    "summary",
}
PHASE_ALIASES = {
    "R0": "R0",
    "0": "R0",
    "R1": "R1",
    "1": "R1",
    "R1.5": "R1.5",
    "R1_5": "R1.5",
    "R15": "R1.5",
    "1.5": "R1.5",
    "R2": "R2",
    "2": "R2",
    "R3": "R3",
    "3": "R3",
    "R4": "R4",
    "4": "R4",
}

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
LOCAL_PATH_RE = re.compile(r"(?<![\w:/])/(?:home|tmp|var|private|Users)/[^\s`)]+")


def _require_package_dir(deal_id: str, *, wiki_root: Path | str | None = None) -> Path:
    package_dir = deal_store.safe_deal_dir(deal_id, wiki_root=wiki_root)
    if not (package_dir / "manifest.json").is_file():
        raise FileNotFoundError(deal_id)
    return package_dir


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _string_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    if value in (None, ""):
        return []
    return [str(value).strip()]


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _number(value: Any) -> int | float | None:
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            return None
        return int(parsed) if parsed.is_integer() else parsed
    return None


def _dedupe(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _canonical_agent_id(value: Any) -> str | None:
    agent_id = _text(value)
    if not agent_id:
        return None
    try:
        return ic_policy.canonical_ic_profile_id(agent_id)
    except KeyError:
        return agent_id


def _truncate(value: Any, limit: int = MAX_TABLE_TEXT_CHARS) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _table_cell(value: Any) -> str:
    text = _truncate(value)
    if not text:
        return "-"
    return text.replace("|", "\\|")


def _format_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value in (None, ""):
        return "-"
    return str(value)


def _format_inline_list(values: list[Any], *, limit: int = MAX_LIST_ITEMS) -> str:
    items = _dedupe(values)
    if not items:
        return "-"
    shown = items[:limit]
    suffix = f" (+{len(items) - limit})" if len(items) > limit else ""
    return ", ".join(shown) + suffix


def _markdown_bullets(values: list[Any], *, empty: str = "- None") -> list[str]:
    items = _dedupe(values)
    if not items:
        return [empty]
    return [f"- {_truncate(item, 260)}" for item in items[:MAX_LIST_ITEMS]]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _modified_at(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        return None


def _file_artifact(package_dir: Path, relative_path: str) -> dict[str, Any]:
    path = package_dir / relative_path
    payload: dict[str, Any] = {
        "path": relative_path,
        "available": path.is_file(),
    }
    if path.is_file():
        stat = path.stat()
        payload.update({
            "size_bytes": stat.st_size,
            "sha256": _sha256(path),
            "updated_at": _modified_at(path),
        })
    return payload


def _normalize_phases(phases: Iterable[str] | str | None) -> list[dict[str, str]]:
    if phases is None:
        selected = [spec["phase"] for spec in PHASE_SPECS]
    elif isinstance(phases, str):
        selected = [phases]
    else:
        selected = list(phases)
    normalized: list[str] = []
    for phase in selected:
        key = str(phase or "").strip().upper().replace("-", "_")
        canonical = PHASE_ALIASES.get(key)
        if not canonical:
            raise ValueError(f"unsupported discussion phase: {phase}")
        if canonical not in normalized:
            normalized.append(canonical)
    if not normalized:
        raise ValueError("phases must include at least one phase")
    specs_by_phase = {spec["phase"]: spec for spec in PHASE_SPECS}
    return [specs_by_phase[phase] for phase in normalized]


def _read_phase_payload(package_dir: Path, json_path: str) -> tuple[bool, Any]:
    path = package_dir / json_path
    if not path.is_file():
        return False, {}
    raw = deal_store.read_json(path, {}) or {}
    return True, deal_store.redact_public_payload(raw)


def _canonical_report_items(raw: Any) -> list[dict[str, Any]]:
    payload = raw
    if isinstance(raw, dict):
        for key in ("reports", "items", "agents", "entries"):
            if key in raw:
                payload = raw.get(key)
                break
    if isinstance(payload, list):
        items = [dict(item) for item in payload if isinstance(item, dict)]
    elif isinstance(payload, dict):
        items = []
        for key, item in payload.items():
            if str(key) in META_KEYS:
                continue
            if not isinstance(item, dict):
                continue
            normalized = dict(item)
            normalized.setdefault("agent_id", key)
            items.append(normalized)
    else:
        return []
    for item in items:
        agent_id = _canonical_agent_id(item.get("agent_id") or item.get("profile_id"))
        if agent_id:
            item["agent_id"] = agent_id
    return items


def _report_summary(report: dict[str, Any]) -> str | None:
    return _text(
        report.get("summary")
        or report.get("rationale")
        or report.get("output_preview")
        or report.get("challenge")
        or " ".join(_string_values(report.get("key_points"))[:2])
    )


def _report_score(report: dict[str, Any]) -> Any:
    for field in ("r2_score", "score", "final_score", "chairman_dimension_score"):
        if report.get(field) not in (None, ""):
            return report.get(field)
    return None


def _report_recommendation(report: dict[str, Any]) -> str | None:
    return _text(report.get("recommendation") or report.get("decision"))


def _dispute_items(raw: Any) -> list[dict[str, Any]]:
    payload = raw.get("disputes") if isinstance(raw, dict) else raw
    if isinstance(payload, dict):
        items = []
        for key, item in payload.items():
            if str(key) in META_KEYS:
                continue
            if isinstance(item, dict):
                normalized = dict(item)
                normalized.setdefault("dispute_id", key)
                items.append(normalized)
        return items
    return [dict(item) for item in _as_list(payload) if isinstance(item, dict)]


def _dispute_agent_ids(dispute: dict[str, Any]) -> list[str]:
    agent_ids = _string_values(dispute.get("agent_ids")) + _string_values(dispute.get("agent_id"))
    for position in _as_list(dispute.get("positions")):
        if isinstance(position, dict):
            agent_ids.extend(_string_values(position.get("agent_id") or position.get("profile_id")))
    return _dedupe(_canonical_agent_id(agent_id) or agent_id for agent_id in agent_ids)


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


def _r0_section(raw: Any, *, available: bool, spec: dict[str, str]) -> tuple[dict[str, Any], list[str]]:
    payload = _as_dict(raw)
    scorecard = _as_dict(payload.get("scorecard"))
    task = _as_dict(payload.get("task_description"))
    discrepancies = _string_values(payload.get("discrepancies"))
    gaps = _string_values(payload.get("coverage_gaps"))
    warnings: list[str] = []
    if not available:
        warnings.append("phase_json_missing:R0")
    elif not scorecard:
        warnings.append("r0_scorecard_missing")
    counts = {
        "items": 1 if payload else 0,
        "discrepancies": len(discrepancies),
        "coverage_gaps": len(gaps),
        "public_sources": int(_as_dict(payload.get("public_facts")).get("source_count") or 0),
        "warnings": len(warnings),
    }
    status = _phase_status(available=available, item_count=counts["items"], warnings=warnings)
    section = {
        "phase": spec["phase"],
        "label": spec["label"],
        "heading": f"{spec['phase']} - {spec['label']}",
        "json_path": spec["json_path"],
        "status": status,
        "counts": counts,
        "warnings": warnings,
        "items_preview": [{
            "company_name": _text(payload.get("company_name") or task.get("company_name")),
            "verification_mode": _text(payload.get("verification_mode")),
            "action": _text(scorecard.get("action")),
            "level": _text(scorecard.get("level") or scorecard.get("level_code")),
        }] if payload else [],
    }
    lines = [
        f"## {section['heading']}",
        "",
        f"- source: `{spec['json_path']}`",
        f"- status: `{status}`",
        f"- verification_mode: `{_format_scalar(payload.get('verification_mode'))}`",
        f"- scorecard_action: `{_format_scalar(scorecard.get('action'))}`",
        f"- scorecard_level: `{_format_scalar(scorecard.get('level') or scorecard.get('level_code'))}`",
        f"- company_name: {_format_scalar(payload.get('company_name') or task.get('company_name'))}",
        f"- industry: {_format_scalar(task.get('industry'))}",
        f"- stage: {_format_scalar(task.get('stage'))}",
        "",
        "### Discrepancies",
        "",
        *_markdown_bullets(discrepancies),
        "",
        "### Coverage Gaps",
        "",
        *_markdown_bullets(gaps),
        "",
    ]
    return section, lines


def _reports_section(
    raw: Any,
    *,
    available: bool,
    spec: dict[str, str],
    score_field_label: str = "Score",
) -> tuple[dict[str, Any], list[str]]:
    reports = _canonical_report_items(raw)
    warnings: list[str] = []
    if not available:
        warnings.append(f"phase_json_missing:{spec['phase']}")
    elif not reports:
        warnings.append(f"phase_reports_empty:{spec['phase']}")
    counts = {
        "items": len(reports),
        "reports": len(reports),
        "with_scores": sum(1 for item in reports if _report_score(item) not in (None, "")),
        "with_recommendations": sum(1 for item in reports if _report_recommendation(item)),
        "open_questions": sum(len(_string_values(item.get("open_questions"))) for item in reports),
        "warnings": len(warnings),
    }
    status = _phase_status(available=available, item_count=len(reports), warnings=warnings)
    section = {
        "phase": spec["phase"],
        "label": spec["label"],
        "heading": f"{spec['phase']} - {spec['label']}",
        "json_path": spec["json_path"],
        "status": status,
        "counts": counts,
        "warnings": warnings,
        "items_preview": [
            {
                "agent_id": item.get("agent_id"),
                "score": _report_score(item),
                "recommendation": _report_recommendation(item),
                "summary": _report_summary(item),
            }
            for item in reports[:5]
        ],
    }
    lines = [
        f"## {section['heading']}",
        "",
        f"- source: `{spec['json_path']}`",
        f"- status: `{status}`",
        f"- reports: `{len(reports)}`",
        "",
        f"| Agent | {score_field_label} | Recommendation | Summary |",
        "| --- | ---: | --- | --- |",
    ]
    if reports:
        for report in reports:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _table_cell(report.get("agent_id")),
                        _table_cell(_report_score(report)),
                        _table_cell(_report_recommendation(report)),
                        _table_cell(_report_summary(report)),
                    ]
                )
                + " |"
            )
    else:
        lines.append("| - | - | - | - |")
    lines.append("")
    return section, lines


def _disputes_section(raw: Any, *, available: bool, spec: dict[str, str]) -> tuple[dict[str, Any], list[str]]:
    disputes = _dispute_items(raw)
    resolved = sum(1 for item in disputes if _coerce_bool(item.get("resolved")))
    high_severity = sum(1 for item in disputes if str(item.get("severity") or "").lower() == "high")
    followups = sum(
        len(
            _string_values(item.get("required_followups"))
            + _string_values(_as_dict(item.get("chairman_ruling")).get("required_followups"))
        )
        for item in disputes
    )
    warnings: list[str] = []
    if not available:
        warnings.append("phase_json_missing:R1.5")
    for item in disputes:
        if not _coerce_bool(item.get("resolved")):
            warnings.append(f"dispute_unresolved:{item.get('dispute_id') or 'unknown'}")
    counts = {
        "items": len(disputes),
        "disputes": len(disputes),
        "resolved": resolved,
        "unresolved": len(disputes) - resolved,
        "high_severity": high_severity,
        "followups": followups,
        "warnings": len(warnings),
    }
    status = _phase_status(available=available, item_count=len(disputes), warnings=warnings, allow_empty=True)
    section = {
        "phase": spec["phase"],
        "label": spec["label"],
        "heading": f"{spec['phase']} - {spec['label']}",
        "json_path": spec["json_path"],
        "status": status,
        "counts": counts,
        "warnings": warnings,
        "items_preview": [
            {
                "dispute_id": _text(item.get("dispute_id")),
                "topic": _text(item.get("topic")),
                "severity": _text(item.get("severity")),
                "resolved": _coerce_bool(item.get("resolved")),
                "agent_ids": _dispute_agent_ids(item),
            }
            for item in disputes[:5]
        ],
    }
    lines = [
        f"## {section['heading']}",
        "",
        f"- source: `{spec['json_path']}`",
        f"- status: `{status}`",
        f"- disputes: `{len(disputes)}`",
        f"- resolved: `{resolved}`",
        f"- unresolved: `{len(disputes) - resolved}`",
        "",
        "| Dispute | Severity | Resolved | Agents | Topic |",
        "| --- | --- | --- | --- | --- |",
    ]
    if disputes:
        for dispute in disputes:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _table_cell(dispute.get("dispute_id")),
                        _table_cell(dispute.get("severity")),
                        _table_cell(_coerce_bool(dispute.get("resolved"))),
                        _table_cell(_format_inline_list(_dispute_agent_ids(dispute), limit=4)),
                        _table_cell(dispute.get("topic")),
                    ]
                )
                + " |"
            )
    else:
        lines.append("| - | - | - | - | No explicit disputes. |")
    lines.append("")
    return section, lines


def _r3_section(raw: Any, *, available: bool, spec: dict[str, str]) -> tuple[dict[str, Any], list[str]]:
    payload = _as_dict(raw)
    reports = _canonical_report_items(raw)
    mode = str(payload.get("mode") or payload.get("status") or ("normal" if reports else "")).strip().lower()
    skipped = mode in {"skip", "skipped"}
    skip_reason = _text(payload.get("skip_reason") or payload.get("reason"))
    warnings: list[str] = []
    if not available:
        warnings.append("phase_json_missing:R3")
    elif skipped and not skip_reason and "reports" not in payload:
        warnings.append("r3_skip_reason_missing")
    elif not skipped and not reports:
        warnings.append("phase_reports_empty:R3")
    counts = {
        "items": len(reports),
        "reports": len(reports),
        "challenges": sum(
            len(_string_values(item.get("challenges")) + _string_values(item.get("red_flags")) + _string_values(item.get("risk_flags")))
            for item in reports
        ),
        "warnings": len(warnings),
    }
    status = _phase_status(available=available, item_count=len(reports), warnings=warnings, allow_empty=skipped)
    section = {
        "phase": spec["phase"],
        "label": spec["label"],
        "heading": f"{spec['phase']} - {spec['label']}",
        "json_path": spec["json_path"],
        "status": status,
        "mode": "skip" if skipped else (mode or "unknown"),
        "skip_reason": skip_reason,
        "counts": counts,
        "warnings": warnings,
        "items_preview": [
            {
                "agent_id": item.get("agent_id"),
                "stance": _text(item.get("stance")),
                "recommendation": _report_recommendation(item),
                "summary": _report_summary(item),
            }
            for item in reports[:5]
        ],
    }
    lines = [
        f"## {section['heading']}",
        "",
        f"- source: `{spec['json_path']}`",
        f"- status: `{status}`",
        f"- mode: `{section['mode']}`",
        f"- skip_reason: {_format_scalar(skip_reason)}",
        "",
        "| Agent | Stance | Recommendation | Summary |",
        "| --- | --- | --- | --- |",
    ]
    if reports:
        for report in reports:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _table_cell(report.get("agent_id")),
                        _table_cell(report.get("stance")),
                        _table_cell(_report_recommendation(report)),
                        _table_cell(_report_summary(report)),
                    ]
                )
                + " |"
            )
    else:
        lines.append("| - | - | - | R3 skipped or no red-blue reports. |")
    lines.append("")
    return section, lines


def _r4_section(raw: Any, *, available: bool, spec: dict[str, str]) -> tuple[dict[str, Any], list[str]]:
    decision = _as_dict(raw)
    conditions = _string_values(decision.get("conditions"))
    monitoring = _string_values(decision.get("monitoring_metrics"))
    missing_required = [
        field
        for field in ("weighted_agent_score", "chairman_dimension_score", "chairman_qualitative_decision")
        if decision.get(field) in (None, "")
    ]
    warnings: list[str] = []
    if not available:
        warnings.append("phase_json_missing:R4")
    if available and missing_required:
        warnings.extend(f"r4_required_field_missing:{field}" for field in missing_required)
    counts = {
        "items": 1 if decision else 0,
        "conditions": len(conditions),
        "monitoring_metrics": len(monitoring),
        "warnings": len(warnings),
    }
    status = _phase_status(available=available, item_count=counts["items"], warnings=warnings)
    human_confirmation = _as_dict(decision.get("human_confirmation"))
    section = {
        "phase": spec["phase"],
        "label": spec["label"],
        "heading": f"{spec['phase']} - {spec['label']}",
        "json_path": spec["json_path"],
        "status": status,
        "counts": counts,
        "warnings": warnings,
        "items_preview": [{
            "decision": _text(decision.get("decision")),
            "final_score": _number(decision.get("final_score")),
            "weighted_agent_score": _number(decision.get("weighted_agent_score")),
            "chairman_dimension_score": _number(decision.get("chairman_dimension_score")),
            "qualitative": _text(decision.get("chairman_qualitative_decision")),
        }] if decision else [],
    }
    lines = [
        f"## {section['heading']}",
        "",
        f"- source: `{spec['json_path']}`",
        f"- status: `{status}`",
        f"- decision: `{_format_scalar(decision.get('decision'))}`",
        f"- final_score: `{_format_scalar(decision.get('final_score'))}`",
        f"- weighted_agent_score: `{_format_scalar(decision.get('weighted_agent_score'))}`",
        f"- chairman_dimension_score: `{_format_scalar(decision.get('chairman_dimension_score'))}`",
        f"- human_confirmation: `{_format_scalar(human_confirmation.get('status') or 'pending')}`",
        "",
        "### Chairman Qualitative Decision",
        "",
        _format_scalar(decision.get("chairman_qualitative_decision")),
        "",
        "### Conditions",
        "",
        *_markdown_bullets(conditions),
        "",
        "### Monitoring Metrics",
        "",
        *_markdown_bullets(monitoring),
        "",
    ]
    return section, lines


def _phase_status(
    *,
    available: bool,
    item_count: int,
    warnings: list[str],
    allow_empty: bool = False,
) -> str:
    if not available:
        return "missing"
    if warnings:
        return "warn"
    if item_count == 0 and not allow_empty:
        return "warn"
    return "pass"


def _build_section(package_dir: Path, spec: dict[str, str]) -> tuple[dict[str, Any], list[str]]:
    available, raw = _read_phase_payload(package_dir, spec["json_path"])
    phase = spec["phase"]
    if phase == "R0":
        return _r0_section(raw, available=available, spec=spec)
    if phase == "R1":
        return _reports_section(raw, available=available, spec=spec)
    if phase == "R1.5":
        return _disputes_section(raw, available=available, spec=spec)
    if phase == "R2":
        return _reports_section(raw, available=available, spec=spec, score_field_label="R2 Score")
    if phase == "R3":
        return _r3_section(raw, available=available, spec=spec)
    if phase == "R4":
        return _r4_section(raw, available=available, spec=spec)
    raise ValueError(f"unsupported discussion phase: {phase}")


def _overall_status(sections: list[dict[str, Any]], blocking_reasons: list[str]) -> str:
    if blocking_reasons:
        return "warn"
    statuses = {str(section.get("status") or "") for section in sections}
    if statuses == {"missing"}:
        return "missing"
    if statuses.intersection({"warn", "missing"}):
        return "warn"
    return "pass"


def _counts(sections: list[dict[str, Any]], markdown: str, warnings: list[str]) -> dict[str, int]:
    return {
        "phases": len(sections),
        "sections": len(sections),
        "pass": sum(1 for section in sections if section.get("status") == "pass"),
        "warn": sum(1 for section in sections if section.get("status") == "warn"),
        "missing": sum(1 for section in sections if section.get("status") == "missing"),
        "items": sum(int(_as_dict(section.get("counts")).get("items") or 0) for section in sections),
        "warnings": len(warnings),
        "markdown_chars": len(markdown),
    }


def _redact_preview_text(markdown: str) -> str:
    preview = markdown[:PREVIEW_CHARS]
    preview = EMAIL_RE.sub("[redacted-email]", preview)
    preview = LOCAL_PATH_RE.sub("[redacted-path]", preview)
    return preview


def _render_markdown(*, deal_id: str, generated_at: str, sections: list[dict[str, Any]], section_lines: list[list[str]]) -> str:
    lines = [
        "# IC Discussion",
        "",
        f"- deal_id: `{deal_id}`",
        f"- generated_at: `{generated_at}`",
        f"- phases: `{', '.join(str(section.get('phase')) for section in sections)}`",
        "",
        "## Discussion Summary",
        "",
        "| Phase | Status | Items | Warnings | Source |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for section in sections:
        counts = _as_dict(section.get("counts"))
        lines.append(
            "| "
            + " | ".join(
                [
                    _table_cell(section.get("phase")),
                    _table_cell(section.get("status")),
                    _table_cell(counts.get("items") or 0),
                    _table_cell(counts.get("warnings") or 0),
                    _table_cell(section.get("json_path")),
                ]
            )
            + " |"
        )
    lines.append("")
    for block in section_lines:
        lines.extend(block)
    return "\n".join(lines).rstrip() + "\n"


def build_deal_discussion(
    deal_id: str,
    dry_run: bool = True,
    overwrite: bool = False,
    phases: Iterable[str] | str | None = None,
    created_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    """Build discussion/IC_DISCUSSION.md from Deal OS phase JSON artifacts."""

    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    selected_specs = _normalize_phases(phases)
    generated_at = deal_store.utc_now_iso()
    built_sections: list[dict[str, Any]] = []
    built_lines: list[list[str]] = []
    for spec in selected_specs:
        section, lines = _build_section(package_dir, spec)
        built_sections.append(section)
        built_lines.append(lines)

    markdown = _render_markdown(
        deal_id=normalized_deal_id,
        generated_at=generated_at,
        sections=built_sections,
        section_lines=built_lines,
    )
    target_path = package_dir / DISCUSSION_MARKDOWN_PATH
    target_exists = target_path.is_file()
    blocking_reasons = ["discussion_markdown_exists"] if target_exists and not overwrite else []
    warnings = [
        warning
        for section in built_sections
        for warning in _as_list(section.get("warnings"))
    ] + blocking_reasons
    status = _overall_status(built_sections, blocking_reasons)
    counts = _counts(built_sections, markdown, warnings)
    would_write = bool(not dry_run and not blocking_reasons)

    if not dry_run and blocking_reasons:
        raise FileExistsError(f"discussion artifact already exists: {DISCUSSION_MARKDOWN_PATH}")

    result: dict[str, Any] = {
        "schema_version": DEAL_DISCUSSION_SCHEMA,
        "deal_id": normalized_deal_id,
        "status": status,
        "dry_run": bool(dry_run),
        "would_write": would_write,
        "overwrite": bool(overwrite),
        "generated_at": generated_at,
        "markdown_path": DISCUSSION_MARKDOWN_PATH,
        "artifacts": {
            "markdown": {
                **_file_artifact(package_dir, DISCUSSION_MARKDOWN_PATH),
                "would_write": would_write,
                "written": False,
            },
            "inputs": {
                spec["phase"]: _file_artifact(package_dir, spec["json_path"])
                for spec in selected_specs
            },
        },
        "counts": counts,
        "section_counts": {
            str(section.get("phase")): _as_dict(section.get("counts"))
            for section in built_sections
        },
        "sections": built_sections,
        "warnings": warnings,
        "blocking_reasons": blocking_reasons,
        "redacted_preview": _redact_preview_text(markdown),
    }

    if dry_run:
        return deal_store.redact_public_payload(result)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(markdown, encoding="utf-8")
    markdown_artifact = _file_artifact(package_dir, DISCUSSION_MARKDOWN_PATH)
    markdown_artifact.update({"would_write": True, "written": True})
    result["artifacts"]["markdown"] = markdown_artifact
    result["written"] = True
    audit_event = deal_store.append_audit_event(
        normalized_deal_id,
        {
            "event_type": "deal_discussion_markdown_built",
            "deal_id": normalized_deal_id,
            "status": status,
            "markdown_path": DISCUSSION_MARKDOWN_PATH,
            "phases": [spec["phase"] for spec in selected_specs],
            "phase_count": len(selected_specs),
            "section_count": len(built_sections),
            "counts": counts,
            "overwrite": bool(overwrite),
            "created_by": created_by,
        },
        wiki_root=wiki_root,
    )
    result["audit_event"] = audit_event
    return deal_store.redact_public_payload(result)
