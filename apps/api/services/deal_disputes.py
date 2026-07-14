"""Deal OS R1.5 dispute summary and deterministic identification helpers."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from services import deal_store, ic_policy

DEAL_DISPUTES_SUMMARY_SCHEMA = "siq_deal_r1_5_disputes_summary_v1"
DEAL_DISPUTES_SCHEMA = "siq_ic_disputes_v1"
DEAL_DISPUTES_IDENTIFICATION_SCHEMA = "siq_deal_r1_5_disputes_identification_v1"
DEAL_DISPUTE_RULING_SCHEMA = "siq_deal_r1_5_dispute_ruling_v1"
DEAL_DISPUTE_RULING_RESPONSE_SCHEMA = "siq_deal_r1_5_dispute_ruling_response_v1"
DEAL_DISPUTE_RULING_GENERATION_SCHEMA = "siq_deal_r1_5_dispute_ruling_generation_v1"
DEAL_DISPUTE_CHAIRMAN_TASK_SCHEMA = "siq_deal_r1_5_chairman_task_v1"
DEAL_DISPUTE_RULING_SUBMISSION_SCHEMA = "siq_deal_r1_5_chairman_ruling_submission_v1"
DEAL_DISPUTES_GENERATION_MODE = "deterministic_r1_report_scan_v1"
DEAL_DISPUTE_RULING_GENERATION_MODE = "deterministic_r1_5_dispute_scan_v1"
DISPUTES_JSON_PATH = "phases/r1_5_disputes.json"
DISPUTES_MARKDOWN_PATH = "discussion/02_R1.5_\u88c1\u51b3\u8bb0\u5f55.md"
NEGATIVE_RECOMMENDATIONS = {"reject", "no_go", "pass_on", "caution", "insufficient_evidence"}
POSITIVE_RECOMMENDATIONS = {"support", "pass", "conditional_pass", "go"}
SCORE_SPREAD_THRESHOLD = 20


def _require_package_dir(deal_id: str, *, wiki_root: Path | str | None = None) -> Path:
    package_dir = deal_store.safe_deal_dir(deal_id, wiki_root=wiki_root)
    if not (package_dir / "manifest.json").is_file():
        raise FileNotFoundError(deal_id)
    return package_dir


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


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


def _dedupe_items(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value in (None, "") or value in result:
            continue
        result.append(deepcopy(value))
    return result


def _normalized_materiality(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _open_question_is_blocking(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("blocking") is True or value.get("required") is True:
        return True
    materiality = _normalized_materiality(
        value.get("severity") or value.get("decision_impact") or value.get("priority")
    )
    return materiality in {"critical", "high", "material", "p0", "重大", "高"}


def _red_flag_is_material(value: Any) -> bool:
    if not isinstance(value, dict):
        # Legacy R1 reports used unstructured risk flag strings. Preserve their
        # blocking behavior until those artifacts are upgraded.
        return bool(str(value or "").strip())
    if value.get("veto") is True or value.get("blocking") is True:
        return True
    materiality = _normalized_materiality(value.get("severity") or value.get("level") or value.get("decision_impact"))
    flag_type = _normalized_materiality(value.get("type"))
    return materiality in {"critical", "high", "material", "重大", "高"} or flag_type in {
        "diligence_blocker",
        "veto",
    }


def _canonical_keyed_payload(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    payload = value.get("reports") if isinstance(value.get("reports"), dict) else value
    result: dict[str, dict[str, Any]] = {}
    for key, item in payload.items():
        if not isinstance(item, dict):
            continue
        try:
            canonical = ic_policy.canonical_ic_profile_id(str(item.get("agent_id") or item.get("profile_id") or key))
        except KeyError:
            continue
        normalized = dict(item)
        normalized["agent_id"] = canonical
        result[canonical] = normalized
    return result


def _recommendation_bucket(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in POSITIVE_RECOMMENDATIONS:
        return "positive"
    if normalized in NEGATIVE_RECOMMENDATIONS:
        return "negative"
    if normalized in {"review", "hold", "needs_review", "revise"}:
        return "review"
    return "unknown"


def _report_position(report: dict[str, Any]) -> dict[str, Any]:
    red_flags = _dedupe_items(_string_values(report.get("red_flags")) + _string_values(report.get("risk_flags")))
    return {
        "agent_id": report.get("agent_id"),
        "recommendation": report.get("recommendation"),
        "score": report.get("score"),
        "summary": report.get("summary") or report.get("output_preview"),
        "evidence_ids": _dedupe_strings(
            _string_values(report.get("evidence_ids")) + _string_values(report.get("evidence_id"))
        ),
        "open_questions": _dedupe_items(_string_values(report.get("open_questions"))),
        "red_flags": red_flags,
    }


def _material_gap_position(position: dict[str, Any]) -> dict[str, Any] | None:
    recommendation = str(position.get("recommendation") or "").strip().lower()
    open_questions = [
        item for item in _string_values(position.get("open_questions")) if _open_question_is_blocking(item)
    ]
    red_flags = [item for item in _string_values(position.get("red_flags")) if _red_flag_is_material(item)]
    if recommendation != "insufficient_evidence" and not open_questions and not red_flags:
        return None
    result = deepcopy(position)
    if recommendation != "insufficient_evidence":
        result["open_questions"] = open_questions
        result["red_flags"] = red_flags
    return result


def _material_gap_severity(positions: list[dict[str, Any]]) -> str:
    if any(str(item.get("recommendation") or "").strip().lower() == "insufficient_evidence" for item in positions):
        return "high"
    for position in positions:
        for flag in _string_values(position.get("red_flags")):
            if not isinstance(flag, dict):
                return "high"
            materiality = _normalized_materiality(
                flag.get("severity") or flag.get("level") or flag.get("decision_impact")
            )
            if materiality in {"critical", "high", "重大", "高"} or flag.get("veto") is True:
                return "high"
    return "medium"


def _dispute_id(deal_id: str, index: int) -> str:
    return f"DISP-{deal_id}-{index:03d}"


def _build_r1_disputes(deal_id: str, reports: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    ordered_reports = [reports[agent_id] for agent_id in ic_policy.R1_AGENT_SEQUENCE if agent_id in reports]
    if not ordered_reports:
        return [], ["r1_reports_missing"]

    disputes: list[dict[str, Any]] = []
    positions = [_report_position(report) for report in ordered_reports]
    agent_ids = [str(report.get("agent_id")) for report in ordered_reports if report.get("agent_id")]
    evidence_ids = _dedupe_strings(
        [item for position in positions for item in _string_values(position.get("evidence_ids"))]
    )

    buckets = {_recommendation_bucket(report.get("recommendation")) for report in ordered_reports}
    meaningful_buckets = buckets - {"unknown"}
    if len(meaningful_buckets) > 1:
        disputes.append(
            {
                "dispute_id": _dispute_id(deal_id, len(disputes) + 1),
                "topic": "R1 recommendation divergence",
                "dimension": "committee_alignment",
                "severity": "high"
                if "negative" in meaningful_buckets and "positive" in meaningful_buckets
                else "medium",
                "resolved": False,
                "agent_ids": agent_ids,
                "evidence_ids": evidence_ids,
                "positions": positions,
                "required_followups": ["Chairman ruling on divergent R1 recommendations"],
                "detection_rules": ["recommendation_bucket_divergence"],
            }
        )

    scored = [(report, _number(report.get("score"))) for report in ordered_reports]
    scored = [(report, score) for report, score in scored if score is not None]
    if len(scored) >= 2:
        scores = [score for _report, score in scored]
        spread = max(scores) - min(scores)
        if spread >= SCORE_SPREAD_THRESHOLD:
            disputes.append(
                {
                    "dispute_id": _dispute_id(deal_id, len(disputes) + 1),
                    "topic": f"R1 score spread {spread:.1f}",
                    "dimension": "scoring_consistency",
                    "severity": "high" if spread >= 30 else "medium",
                    "resolved": False,
                    "agent_ids": [str(report.get("agent_id")) for report, _score in scored if report.get("agent_id")],
                    "evidence_ids": evidence_ids,
                    "positions": [{**_report_position(report), "score": score} for report, score in scored],
                    "required_followups": ["Review score assumptions and normalize scoring basis"],
                    "detection_rules": ["score_spread_threshold"],
                }
            )

    gap_positions = [
        material_gap for position in positions if (material_gap := _material_gap_position(position)) is not None
    ]
    if gap_positions:
        disputes.append(
            {
                "dispute_id": _dispute_id(deal_id, len(disputes) + 1),
                "topic": "R1 unresolved diligence gaps",
                "dimension": "evidence_sufficiency",
                "severity": _material_gap_severity(gap_positions),
                "resolved": False,
                "agent_ids": _dedupe_strings(
                    [str(position.get("agent_id")) for position in gap_positions if position.get("agent_id")]
                ),
                "evidence_ids": _dedupe_strings(
                    [item for position in gap_positions for item in _string_values(position.get("evidence_ids"))]
                ),
                "positions": gap_positions,
                "required_followups": ["Resolve open questions and risk flags before R2"],
                "detection_rules": ["blocking_questions_or_material_red_flags_present"],
            }
        )

    if len(ordered_reports) < len(ic_policy.R1_AGENT_SEQUENCE):
        missing = [agent_id for agent_id in ic_policy.R1_AGENT_SEQUENCE if agent_id not in reports]
        warnings.append(f"r1_reports_incomplete:{','.join(missing)}")
    return disputes, warnings


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
            try:
                canonical.append(ic_policy.canonical_ic_profile_id(agent_id))
            except KeyError:
                canonical.append(agent_id)
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
        _string_values(dispute.get("agent_ids")) + _string_values(dispute.get("agent_id")) + _position_agents(positions)
    )
    evidence_ids = _dedupe_strings(
        _string_values(dispute.get("evidence_ids"))
        + _string_values(dispute.get("evidence_id"))
        + _position_evidence_ids(positions)
        + _string_values(ruling.get("evidence_ids"))
        + _string_values(ruling.get("evidence_id"))
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


def _summarize_deal_disputes_raw(package_dir: Path, raw: Any) -> dict[str, Any]:
    raw = deal_store.redact_public_payload(raw)
    dispute_items = _raw_dispute_items(raw)
    disputes = [
        _summarize_dispute(item, index) for index, item in enumerate(dispute_items, start=1) if isinstance(item, dict)
    ]
    top_level_warnings = _dedupe_strings(_string_values(raw.get("warnings") if isinstance(raw, dict) else None))
    artifacts = {
        "json": _artifact(package_dir, DISPUTES_JSON_PATH),
        "markdown": _artifact(package_dir, DISPUTES_MARKDOWN_PATH),
    }
    warnings = _dedupe_strings(
        top_level_warnings + _warnings(disputes, json_available=bool(artifacts["json"]["available"]))
    )
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


def summarize_deal_disputes_package(package_dir: Path) -> dict[str, Any]:
    raw = deal_store.read_json(package_dir / DISPUTES_JSON_PATH, {}) or {}
    return _summarize_deal_disputes_raw(package_dir, raw)


def summarize_deal_disputes(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    return summarize_deal_disputes_package(package_dir)


def _markdown_for_disputes(payload: dict[str, Any]) -> str:
    lines = [
        "# R1.5 Dispute Identification",
        "",
        f"- deal_id: `{payload.get('deal_id')}`",
        f"- generation_mode: `{payload.get('generation_mode')}`",
        f"- disputes: `{len(_as_list(payload.get('disputes')))}`",
        "",
    ]
    disputes = _as_list(payload.get("disputes"))
    if not disputes:
        lines.extend(
            [
                "## No Explicit Disputes",
                "",
                "No deterministic R1.5 disputes were identified from current R1 reports.",
                "",
            ]
        )
    for dispute in disputes:
        if not isinstance(dispute, dict):
            continue
        lines.extend(
            [
                f"## {dispute.get('dispute_id')} · {dispute.get('topic')}",
                "",
                f"- dimension: `{dispute.get('dimension')}`",
                f"- severity: `{dispute.get('severity')}`",
                f"- resolved: `{dispute.get('resolved')}`",
                f"- agents: `{', '.join(_string_values(dispute.get('agent_ids')))}`",
                "",
                "### Required Follow-ups",
                "",
            ]
        )
        followups = _string_values(dispute.get("required_followups"))
        if followups:
            lines.extend([f"- {item}" for item in followups])
        else:
            lines.append("- None")
        lines.append("")
        ruling = _as_dict(dispute.get("chairman_ruling"))
        if ruling:
            lines.extend(
                [
                    "### Chairman Ruling",
                    "",
                    f"- decision: `{ruling.get('decision')}`",
                    f"- resolved: `{ruling.get('resolved')}`",
                    f"- ruled_at: `{ruling.get('ruled_at')}`",
                ]
            )
            if ruling.get("rationale"):
                lines.extend(["", str(ruling.get("rationale"))])
            ruling_followups = _string_values(ruling.get("required_followups"))
            if ruling_followups:
                lines.extend(["", "#### Ruling Follow-ups", ""])
                lines.extend([f"- {item}" for item in ruling_followups])
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _load_disputes_payload(package_dir: Path) -> dict[str, Any]:
    path = package_dir / DISPUTES_JSON_PATH
    if not path.is_file():
        raise ValueError("r1_5_disputes.json is missing; run identify-disputes before ruling")
    payload = deal_store.read_json(path, {}) or {}
    if not isinstance(payload, dict):
        raise ValueError("r1_5_disputes.json must be an object with disputes")
    if not isinstance(payload.get("disputes"), list):
        raise ValueError("r1_5_disputes.json disputes must be a list")
    return deepcopy(payload)


def _chairman_task_dispute(dispute: dict[str, Any]) -> dict[str, Any]:
    ruling = _as_dict(dispute.get("chairman_ruling"))
    return {
        "dispute_id": str(dispute.get("dispute_id") or "").strip(),
        "topic": dispute.get("topic"),
        "dimension": dispute.get("dimension"),
        "severity": dispute.get("severity"),
        "resolved": _coerce_bool(dispute.get("resolved")),
        "agent_ids": _dispute_agent_ids(dispute),
        "evidence_ids": _dispute_evidence_ids(dispute),
        "positions": _as_list(dispute.get("positions")),
        "required_followups": _required_followups(dispute, ruling),
        "existing_chairman_ruling": ruling or None,
    }


def build_chairman_ruling_task(
    deal_id: str,
    *,
    only_unresolved: bool = True,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    payload = _load_disputes_payload(package_dir)
    raw_disputes = [item for item in _as_list(payload.get("disputes")) if isinstance(item, dict)]
    task_disputes = [
        _chairman_task_dispute(item)
        for item in raw_disputes
        if not only_unresolved or not _coerce_bool(item.get("resolved"))
    ]
    summary = summarize_deal_disputes(normalized_deal_id, wiki_root=wiki_root)
    blocking_reasons: list[str] = []
    if not task_disputes:
        blocking_reasons.append("no_unresolved_disputes" if only_unresolved else "no_disputes")
    return deal_store.redact_public_payload(
        {
            "schema_version": DEAL_DISPUTE_CHAIRMAN_TASK_SCHEMA,
            "deal_id": normalized_deal_id,
            "phase": "R1.5",
            "round_name": "R1.5",
            "agent_id": "siq_ic_chairman",
            "only_unresolved": bool(only_unresolved),
            "allowed": not blocking_reasons,
            "blocking_reasons": blocking_reasons,
            "dispute_count": len(task_disputes),
            "summary": summary,
            "input_artifacts": {
                "disputes": DISPUTES_JSON_PATH,
                "r1_reports": "phases/r1_reports.json",
                "workflow_state": "phases/workflow_state.json",
                "evidence_items": "evidence/evidence_items.ndjson",
            },
            "output_contract": {
                "endpoint": f"/api/deals/{normalized_deal_id}/workflow/disputes/chairman-rulings",
                "json_path": DISPUTES_JSON_PATH,
                "markdown_path": DISPUTES_MARKDOWN_PATH,
                "required_fields": ["dispute_id", "decision", "rationale", "resolved"],
                "optional_fields": ["required_followups", "evidence_ids", "ruling_value", "is_approved"],
                "compatibility_aliases": {
                    "decision": ["ruling_text"],
                    "resolved": ["is_approved"],
                },
            },
            "hard_rules": [
                "Chairman must rule only on listed dispute_id values.",
                "Every ruling must include a decision and rationale.",
                "Use evidence_ids from the dispute positions whenever possible.",
                "If unresolved issues remain, set resolved=false and add required_followups.",
                "API service writes JSON, Markdown, workflow state, and audit events.",
            ],
            "disputes": task_disputes,
            "dry_run": True,
            "hermes_called": False,
            "workflow_advanced": False,
        }
    )


def _apply_dispute_ruling(
    payload: dict[str, Any],
    *,
    dispute_id: str,
    ruling: dict[str, Any],
    overwrite: bool = False,
) -> dict[str, Any]:
    disputes = _as_list(payload.get("disputes"))
    for index, dispute in enumerate(disputes):
        if not isinstance(dispute, dict):
            continue
        if str(dispute.get("dispute_id") or "").strip() != dispute_id:
            continue
        if dispute.get("chairman_ruling") and not overwrite:
            raise ValueError(f"Dispute already has a chairman_ruling: {dispute_id}")
        updated_dispute = dict(dispute)
        updated_dispute["resolved"] = bool(ruling.get("resolved"))
        if ruling.get("evidence_snapshot_hash"):
            updated_dispute["evidence_snapshot_hash"] = ruling["evidence_snapshot_hash"]
        updated_dispute["chairman_ruling"] = ruling
        disputes[index] = updated_dispute
        payload["disputes"] = disputes
        payload["last_ruled_at"] = ruling.get("ruled_at")
        payload["last_ruled_dispute_id"] = dispute_id
        return updated_dispute
    raise ValueError(f"Dispute not found: {dispute_id}")


def _normalize_submitted_ruling(
    *,
    deal_id: str,
    item: dict[str, Any],
    created_by: dict[str, Any] | None,
    now: str,
) -> dict[str, Any]:
    dispute_id = str(item.get("dispute_id") or "").strip()
    if not dispute_id:
        raise ValueError("ruling dispute_id is required")
    decision = str(item.get("decision") or item.get("ruling_text") or "").strip()
    if not decision:
        raise ValueError(f"ruling decision is required for {dispute_id}")
    rationale = str(item.get("rationale") or item.get("reason") or "").strip()
    if not rationale:
        raise ValueError(f"ruling rationale is required for {dispute_id}")
    if "resolved" not in item and "is_approved" not in item:
        raise ValueError(f"ruling resolved is required for {dispute_id}")
    resolved_value = item.get("resolved")
    if resolved_value is None:
        resolved_value = item.get("is_approved")
    resolved = _coerce_bool(resolved_value)
    followups = _dedupe_strings(_string_values(item.get("required_followups") or item.get("required_followup") or []))
    if not resolved and not followups:
        raise ValueError(f"ruling required_followups is required when unresolved for {dispute_id}")
    ruling = {
        "schema_version": DEAL_DISPUTE_RULING_SCHEMA,
        "deal_id": deal_id,
        "dispute_id": dispute_id,
        "agent_id": "siq_ic_chairman",
        "chairman_agent_id": "siq_ic_chairman",
        "decision": decision,
        "rationale": rationale,
        "required_followups": followups,
        "evidence_ids": _dedupe_strings(_string_values(item.get("evidence_ids") or item.get("evidence_id") or [])),
        "resolved": resolved,
        "created_at": now,
        "created_by": created_by,
        "ruled_at": item.get("signed_at") or item.get("ruled_at") or now,
        "ruled_by": created_by or {"agent_id": item.get("signed_by") or "siq_ic_chairman"},
        "submission_mode": "structured_chairman_rulings_v1",
    }
    if item.get("schema_version") is not None:
        ruling["submission_schema_version"] = deepcopy(item.get("schema_version"))
    if item.get("created_at") is not None:
        ruling["source_created_at"] = deepcopy(item.get("created_at"))
    if item.get("ruling_value") is not None:
        ruling["ruling_value"] = item.get("ruling_value")
    for field in (
        "ruling",
        "accepted_claim_ids",
        "rejected_claim_ids",
        "counter_evidence_ids",
        "decision_impact",
        "generation_mode",
        "task_id",
        "workflow_run_id",
        "evidence_snapshot_hash",
        "input_digest",
        "handoff_digest",
        "hermes_run_id",
    ):
        if item.get(field) is not None:
            ruling[field] = deepcopy(item.get(field))
    return ruling


def submit_chairman_rulings(
    deal_id: str,
    *,
    rulings: list[dict[str, Any]],
    overwrite: bool = False,
    dry_run: bool = True,
    created_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    payload = _load_disputes_payload(package_dir)
    if not isinstance(rulings, list) or not rulings:
        raise ValueError("rulings must be a non-empty list")
    now = deal_store.utc_now_iso()
    applied: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rulings:
        if not isinstance(raw, dict):
            raise ValueError("each ruling must be an object")
        ruling = _normalize_submitted_ruling(
            deal_id=normalized_deal_id,
            item=raw,
            created_by=created_by,
            now=now,
        )
        dispute_id = str(ruling["dispute_id"])
        if dispute_id in seen:
            raise ValueError(f"duplicate ruling dispute_id: {dispute_id}")
        seen.add(dispute_id)
        updated_dispute = _apply_dispute_ruling(
            payload,
            dispute_id=dispute_id,
            ruling=ruling,
            overwrite=overwrite,
        )
        applied.append(
            {
                "dispute_id": dispute_id,
                "ruling": ruling,
                "dispute": updated_dispute,
            }
        )

    payload["schema_version"] = str(payload.get("schema_version") or DEAL_DISPUTES_SCHEMA)
    payload["deal_id"] = str(payload.get("deal_id") or normalized_deal_id)
    payload["last_submitted_rulings_at"] = now
    payload["last_submitted_ruling_count"] = len(applied)
    preview_summary = _summarize_deal_disputes_raw(package_dir, payload)
    counts = preview_summary.get("counts") if isinstance(preview_summary.get("counts"), dict) else {}
    can_proceed_to_r2 = int(counts.get("unresolved") or 0) == 0 and str(preview_summary.get("status") or "") == "pass"
    result = {
        "schema_version": DEAL_DISPUTE_RULING_SUBMISSION_SCHEMA,
        "deal_id": normalized_deal_id,
        "dry_run": bool(dry_run),
        "would_write": not dry_run,
        "overwrite": bool(overwrite),
        "submitted_count": len(applied),
        "json_path": DISPUTES_JSON_PATH,
        "markdown_path": DISPUTES_MARKDOWN_PATH,
        "can_proceed_to_r2": can_proceed_to_r2,
        "rulings": applied,
        "payload": payload,
        "summary": preview_summary,
    }
    if dry_run:
        return deal_store.redact_public_payload(result)

    deal_store.write_json(package_dir / DISPUTES_JSON_PATH, payload)
    markdown_path = package_dir / DISPUTES_MARKDOWN_PATH
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(_markdown_for_disputes(payload), encoding="utf-8")
    summary = summarize_deal_disputes(normalized_deal_id, wiki_root=wiki_root)
    workflow = _update_workflow_after_ruling(package_dir, summary=summary, now=now)
    audit_event = deal_store.append_audit_event(
        normalized_deal_id,
        {
            "event_type": "deal_r1_5_chairman_rulings_submitted",
            "deal_id": normalized_deal_id,
            "submitted_count": len(applied),
            "dispute_ids": [item["dispute_id"] for item in applied],
            "overwrite": bool(overwrite),
            "can_proceed_to_r2": can_proceed_to_r2,
            "json_path": DISPUTES_JSON_PATH,
            "markdown_path": DISPUTES_MARKDOWN_PATH,
            "created_by": created_by,
        },
        wiki_root=wiki_root,
    )
    result["written"] = True
    result["summary"] = summary
    result["workflow"] = workflow
    result["audit_event"] = audit_event
    return deal_store.redact_public_payload(result)


def _preserve_existing_rulings(
    package_dir: Path,
    disputes: list[dict[str, Any]],
    *,
    preserve_rulings: bool,
) -> tuple[int, list[str]]:
    if not preserve_rulings:
        return 0, []
    path = package_dir / DISPUTES_JSON_PATH
    if not path.is_file():
        return 0, []
    existing_payload = deal_store.read_json(path, {}) or {}
    existing_disputes = _as_list(existing_payload.get("disputes") if isinstance(existing_payload, dict) else None)
    existing_by_id = {
        str(item.get("dispute_id") or "").strip(): item
        for item in existing_disputes
        if isinstance(item, dict) and item.get("chairman_ruling") and str(item.get("dispute_id") or "").strip()
    }
    if not existing_by_id:
        return 0, []
    preserved = 0
    seen: set[str] = set()
    for dispute in disputes:
        dispute_id = str(dispute.get("dispute_id") or "").strip()
        existing = existing_by_id.get(dispute_id)
        if not existing:
            continue
        seen.add(dispute_id)
        dispute["chairman_ruling"] = deepcopy(existing.get("chairman_ruling"))
        dispute["resolved"] = _coerce_bool(existing.get("resolved"))
        preserved += 1
    unmatched = [dispute_id for dispute_id in existing_by_id if dispute_id not in seen]
    return preserved, [f"previous_ruling_unmatched:{dispute_id}" for dispute_id in unmatched]


def _update_workflow_after_ruling(
    package_dir: Path,
    *,
    summary: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    workflow = deal_store.read_json(package_dir / "phases" / "workflow_state.json", {}) or {}
    phases = workflow.setdefault("phases", {})
    if not isinstance(phases, dict):
        phases = {}
        workflow["phases"] = phases
    r1_5 = phases.setdefault("R1.5", {})
    if not isinstance(r1_5, dict):
        r1_5 = {}
        phases["R1.5"] = r1_5

    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    warnings = summary.get("warnings") if isinstance(summary.get("warnings"), list) else []
    unresolved = int(counts.get("unresolved") or 0)
    if unresolved > 0:
        r1_5_status = "in_progress"
        workflow_status = "r1_5_ruling_recorded"
    elif str(summary.get("status") or "") == "pass":
        r1_5_status = "completed"
        workflow_status = "r1_5_disputes_resolved"
    else:
        r1_5_status = "blocked"
        workflow_status = "r1_5_blocked"

    r1_5.update(
        {
            "status": r1_5_status,
            "dispute_count": counts.get("disputes") or 0,
            "resolved_count": counts.get("resolved") or 0,
            "unresolved_count": unresolved,
            "ruling_count": counts.get("rulings") or 0,
            "warnings": warnings,
            "updated_at": now,
        }
    )
    r1_5.setdefault("started_at", now)
    if r1_5_status == "completed":
        r1_5["completed_at"] = now
    else:
        r1_5.pop("completed_at", None)
    workflow["current_phase"] = "R1.5"
    workflow["status"] = workflow_status
    workflow["updated_at"] = now
    deal_store.write_json(package_dir / "phases" / "workflow_state.json", workflow)
    return workflow


def _dispute_evidence_ids(dispute: dict[str, Any]) -> list[str]:
    return _dedupe_strings(
        _string_values(dispute.get("evidence_ids"))
        + _string_values(dispute.get("evidence_id"))
        + _position_evidence_ids(_as_list(dispute.get("positions")))
    )


def _dispute_agent_ids(dispute: dict[str, Any]) -> list[str]:
    return _canonical_agent_ids(
        _string_values(dispute.get("agent_ids"))
        + _string_values(dispute.get("agent_id"))
        + _position_agents(_as_list(dispute.get("positions")))
    )


def _ruling_decision_for_dispute(dispute: dict[str, Any], followups: list[str]) -> str:
    severity = str(dispute.get("severity") or "").strip().lower()
    dimension = str(dispute.get("dimension") or "").strip().lower()
    if severity in {"high", "critical"} or followups or dimension in {"evidence_sufficiency", "scoring_consistency"}:
        return "resolved_with_conditions"
    return "resolved_no_followup"


def _ruling_followups_for_dispute(dispute: dict[str, Any]) -> list[str]:
    followups = _dedupe_strings(
        _string_values(dispute.get("required_followups")) + _string_values(dispute.get("required_followup"))
    )
    if followups:
        return followups
    dimension = str(dispute.get("dimension") or "").strip().lower()
    if dimension == "evidence_sufficiency":
        return ["Resolve evidence sufficiency gaps before R2"]
    if dimension == "scoring_consistency":
        return ["Normalize score assumptions before R2"]
    if dimension == "committee_alignment":
        return ["Document chairman tie-break rationale before R2"]
    return []


def _ruling_rationale_for_dispute(dispute: dict[str, Any], *, decision: str) -> str:
    dispute_id = str(dispute.get("dispute_id") or "").strip()
    topic = str(dispute.get("topic") or "R1.5 dispute").strip()
    dimension = str(dispute.get("dimension") or "unknown").strip()
    severity = str(dispute.get("severity") or "unknown").strip()
    agents = ", ".join(_dispute_agent_ids(dispute)) or "unknown agents"
    return (
        f"Deterministic chairman draft for {dispute_id or topic}: {topic}. "
        f"Dimension={dimension}, severity={severity}, agents={agents}. "
        f"Decision={decision}; final human review is still required before R2/R4 progression."
    )


def _build_generated_ruling(
    normalized_deal_id: str,
    dispute: dict[str, Any],
    *,
    now: str,
    created_by: dict[str, Any] | None,
) -> dict[str, Any]:
    followups = _ruling_followups_for_dispute(dispute)
    decision = _ruling_decision_for_dispute(dispute, followups)
    normalized_dispute_id = str(dispute.get("dispute_id") or "").strip()
    return {
        "schema_version": DEAL_DISPUTE_RULING_SCHEMA,
        "deal_id": normalized_deal_id,
        "dispute_id": normalized_dispute_id,
        "agent_id": "siq_ic_chairman",
        "chairman_agent_id": "siq_ic_chairman",
        "decision": decision,
        "rationale": _ruling_rationale_for_dispute(dispute, decision=decision),
        "required_followups": followups,
        "evidence_ids": _dispute_evidence_ids(dispute),
        "resolved": True,
        "generation_mode": DEAL_DISPUTE_RULING_GENERATION_MODE,
        "created_at": now,
        "created_by": created_by,
        "ruled_at": now,
        "ruled_by": created_by,
    }


def generate_deal_dispute_rulings(
    deal_id: str,
    *,
    dry_run: bool = True,
    overwrite: bool = False,
    created_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    payload = _load_disputes_payload(package_dir)
    now = deal_store.utc_now_iso()
    generated: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    warnings: list[str] = []

    for dispute in _as_list(payload.get("disputes")):
        if not isinstance(dispute, dict):
            warnings.append("invalid_dispute_item_skipped")
            continue
        dispute_id = str(dispute.get("dispute_id") or "").strip()
        if not dispute_id:
            warnings.append("dispute_id_missing_skipped")
            continue
        if dispute.get("chairman_ruling") and not overwrite:
            skipped.append({"dispute_id": dispute_id, "reason": "chairman_ruling_exists"})
            continue
        ruling = _build_generated_ruling(
            normalized_deal_id,
            dispute,
            now=now,
            created_by=created_by,
        )
        updated_dispute = _apply_dispute_ruling(
            payload,
            dispute_id=dispute_id,
            ruling=ruling,
            overwrite=True,
        )
        generated.append(
            {
                "dispute_id": dispute_id,
                "ruling": ruling,
                "dispute": updated_dispute,
            }
        )

    payload["schema_version"] = str(payload.get("schema_version") or DEAL_DISPUTES_SCHEMA)
    payload["deal_id"] = str(payload.get("deal_id") or normalized_deal_id)
    payload["last_generated_rulings_at"] = now
    payload["last_generated_ruling_count"] = len(generated)
    if warnings:
        payload["warnings"] = _dedupe_strings(_string_values(payload.get("warnings")) + warnings)
    summary = _summarize_deal_disputes_raw(package_dir, payload)
    result = {
        "schema_version": DEAL_DISPUTE_RULING_GENERATION_SCHEMA,
        "deal_id": normalized_deal_id,
        "dry_run": bool(dry_run),
        "would_write": bool(generated) and not dry_run,
        "overwrite": bool(overwrite),
        "generation_mode": DEAL_DISPUTE_RULING_GENERATION_MODE,
        "json_path": DISPUTES_JSON_PATH,
        "markdown_path": DISPUTES_MARKDOWN_PATH,
        "generated_count": len(generated),
        "skipped_count": len(skipped),
        "warnings": warnings,
        "skipped": skipped,
        "rulings": generated,
        "payload": payload,
        "summary": summary,
    }
    if dry_run or not generated:
        if not dry_run:
            result["written"] = False
        return deal_store.redact_public_payload(result)

    deal_store.write_json(package_dir / DISPUTES_JSON_PATH, payload)
    markdown_path = package_dir / DISPUTES_MARKDOWN_PATH
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(_markdown_for_disputes(payload), encoding="utf-8")
    summary = summarize_deal_disputes(normalized_deal_id, wiki_root=wiki_root)
    workflow = _update_workflow_after_ruling(package_dir, summary=summary, now=now)
    audit_event = deal_store.append_audit_event(
        normalized_deal_id,
        {
            "event_type": "deal_r1_5_dispute_rulings_generated",
            "deal_id": normalized_deal_id,
            "generated_count": len(generated),
            "skipped_count": len(skipped),
            "overwrite": bool(overwrite),
            "warnings": warnings,
            "json_path": DISPUTES_JSON_PATH,
            "markdown_path": DISPUTES_MARKDOWN_PATH,
            "created_by": created_by,
        },
        wiki_root=wiki_root,
    )
    result["written"] = True
    result["summary"] = summary
    result["workflow"] = workflow
    result["audit_event"] = audit_event
    return deal_store.redact_public_payload(result)


def rule_deal_dispute(
    deal_id: str,
    dispute_id: str,
    *,
    decision: str,
    rationale: str | None = None,
    required_followups: list[Any] | None = None,
    evidence_ids: list[Any] | None = None,
    resolved: bool | None = None,
    overwrite: bool = False,
    dry_run: bool = True,
    created_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    normalized_dispute_id = str(dispute_id or "").strip()
    if not normalized_dispute_id:
        raise ValueError("dispute_id is required")
    normalized_decision = str(decision or "").strip()
    if not normalized_decision:
        raise ValueError("decision is required")
    normalized_rationale = str(rationale or "").strip()
    if not normalized_rationale:
        raise ValueError("rationale is required")
    if resolved is None:
        raise ValueError("resolved is required")
    normalized_followups = _dedupe_strings(_string_values(required_followups or []))
    if not bool(resolved) and not normalized_followups:
        raise ValueError("required_followups is required when unresolved")

    payload = _load_disputes_payload(package_dir)
    now = deal_store.utc_now_iso()
    ruling = {
        "schema_version": DEAL_DISPUTE_RULING_SCHEMA,
        "deal_id": normalized_deal_id,
        "dispute_id": normalized_dispute_id,
        "agent_id": "siq_ic_chairman",
        "chairman_agent_id": "siq_ic_chairman",
        "decision": normalized_decision,
        "rationale": normalized_rationale,
        "required_followups": normalized_followups,
        "evidence_ids": _dedupe_strings(_string_values(evidence_ids or [])),
        "resolved": bool(resolved),
        "created_at": now,
        "created_by": created_by,
        "ruled_at": now,
        "ruled_by": created_by,
    }
    updated_dispute = _apply_dispute_ruling(
        payload,
        dispute_id=normalized_dispute_id,
        ruling=ruling,
        overwrite=overwrite,
    )
    payload["schema_version"] = str(payload.get("schema_version") or DEAL_DISPUTES_SCHEMA)
    payload["deal_id"] = str(payload.get("deal_id") or normalized_deal_id)
    preview_summary = _summarize_deal_disputes_raw(package_dir, payload)
    result = {
        "schema_version": DEAL_DISPUTE_RULING_RESPONSE_SCHEMA,
        "deal_id": normalized_deal_id,
        "dispute_id": normalized_dispute_id,
        "dry_run": bool(dry_run),
        "would_write": not dry_run,
        "json_path": DISPUTES_JSON_PATH,
        "markdown_path": DISPUTES_MARKDOWN_PATH,
        "ruling": ruling,
        "overwrite": bool(overwrite),
        "dispute": updated_dispute,
        "payload": payload,
        "summary": preview_summary,
    }
    if dry_run:
        return deal_store.redact_public_payload(result)

    deal_store.write_json(package_dir / DISPUTES_JSON_PATH, payload)
    markdown_path = package_dir / DISPUTES_MARKDOWN_PATH
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(_markdown_for_disputes(payload), encoding="utf-8")
    summary = summarize_deal_disputes(normalized_deal_id, wiki_root=wiki_root)
    workflow = _update_workflow_after_ruling(package_dir, summary=summary, now=now)
    audit_event = deal_store.append_audit_event(
        normalized_deal_id,
        {
            "event_type": "deal_r1_5_dispute_ruling_applied",
            "deal_id": normalized_deal_id,
            "dispute_id": normalized_dispute_id,
            "resolved": bool(resolved),
            "decision": normalized_decision,
            "required_followups": ruling["required_followups"],
            "evidence_ids": ruling["evidence_ids"],
            "overwrite": bool(overwrite),
            "warnings": summary.get("warnings") if isinstance(summary.get("warnings"), list) else [],
            "json_path": DISPUTES_JSON_PATH,
            "markdown_path": DISPUTES_MARKDOWN_PATH,
            "created_by": created_by,
        },
        wiki_root=wiki_root,
    )
    result["written"] = True
    result["summary"] = summary
    result["workflow"] = workflow
    result["audit_event"] = audit_event
    return deal_store.redact_public_payload(result)


def identify_deal_disputes(
    deal_id: str,
    *,
    dry_run: bool = True,
    preserve_rulings: bool = True,
    created_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    reports = _canonical_keyed_payload(deal_store.read_json(package_dir / "phases" / "r1_reports.json", {}) or {})
    disputes, warnings = _build_r1_disputes(normalized_deal_id, reports)
    preserved_ruling_count, preserve_warnings = _preserve_existing_rulings(
        package_dir,
        disputes,
        preserve_rulings=preserve_rulings,
    )
    warnings = _dedupe_strings(warnings + preserve_warnings)
    payload = {
        "schema_version": DEAL_DISPUTES_SCHEMA,
        "deal_id": normalized_deal_id,
        "phase": "R1.5",
        "generation_mode": DEAL_DISPUTES_GENERATION_MODE,
        "generated_at": deal_store.utc_now_iso(),
        "generated_by": created_by,
        "source_reports_count": len(reports),
        "dry_run": bool(dry_run),
        "preserve_rulings": bool(preserve_rulings),
        "preserved_ruling_count": preserved_ruling_count,
        "disputes": disputes,
        "warnings": warnings,
    }
    result = {
        "schema_version": DEAL_DISPUTES_IDENTIFICATION_SCHEMA,
        "deal_id": normalized_deal_id,
        "dry_run": bool(dry_run),
        "would_write": not dry_run,
        "json_path": DISPUTES_JSON_PATH,
        "markdown_path": DISPUTES_MARKDOWN_PATH,
        "dispute_count": len(disputes),
        "preserve_rulings": bool(preserve_rulings),
        "preserved_ruling_count": preserved_ruling_count,
        "warnings": warnings,
        "payload": payload,
        "summary": _summarize_deal_disputes_raw(package_dir, payload),
    }
    if dry_run:
        return deal_store.redact_public_payload(result)

    deal_store.write_json(package_dir / DISPUTES_JSON_PATH, payload)
    markdown_path = package_dir / DISPUTES_MARKDOWN_PATH
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(_markdown_for_disputes(payload), encoding="utf-8")

    workflow = deal_store.read_json(package_dir / "phases" / "workflow_state.json", {}) or {}
    phases = workflow.setdefault("phases", {})
    if not isinstance(phases, dict):
        phases = {}
        workflow["phases"] = phases
    r1_5 = phases.setdefault("R1.5", {})
    if not isinstance(r1_5, dict):
        r1_5 = {}
        phases["R1.5"] = r1_5
    now = deal_store.utc_now_iso()
    summary = summarize_deal_disputes(normalized_deal_id, wiki_root=wiki_root)
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    unresolved = int(counts.get("unresolved") or 0)
    if unresolved > 0:
        r1_5_status = "in_progress"
        workflow_status = "r1_5_disputes_identified"
    elif str(summary.get("status") or "") == "pass":
        r1_5_status = "completed"
        workflow_status = "r1_5_disputes_resolved" if preserved_ruling_count else "r1_5_clear"
    else:
        r1_5_status = "blocked"
        workflow_status = "r1_5_blocked"
    r1_5.update(
        {
            "status": r1_5_status,
            "dispute_count": len(disputes),
            "resolved_count": counts.get("resolved") or 0,
            "unresolved_count": unresolved,
            "ruling_count": counts.get("rulings") or 0,
            "preserved_ruling_count": preserved_ruling_count,
            "warnings": warnings,
            "updated_at": now,
        }
    )
    r1_5.setdefault("started_at", now)
    if r1_5_status == "completed":
        r1_5["completed_at"] = now
    else:
        r1_5.pop("completed_at", None)
    workflow["current_phase"] = "R1.5"
    workflow["status"] = workflow_status
    workflow["updated_at"] = now
    deal_store.write_json(package_dir / "phases" / "workflow_state.json", workflow)

    audit_event = deal_store.append_audit_event(
        normalized_deal_id,
        {
            "event_type": "deal_r1_5_disputes_identified",
            "deal_id": normalized_deal_id,
            "dispute_count": len(disputes),
            "preserve_rulings": bool(preserve_rulings),
            "preserved_ruling_count": preserved_ruling_count,
            "warnings": warnings,
            "json_path": DISPUTES_JSON_PATH,
            "markdown_path": DISPUTES_MARKDOWN_PATH,
            "created_by": created_by,
        },
        wiki_root=wiki_root,
    )
    result["written"] = True
    result["audit_event"] = audit_event
    result["summary"] = summary
    return deal_store.redact_public_payload(result)
