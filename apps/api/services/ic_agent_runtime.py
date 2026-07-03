"""IC agent runtime contracts."""

from __future__ import annotations

import json
import re
from html import escape
from pathlib import Path
from typing import Any

from services import deal_contracts
from services import deal_disputes
from services import deal_reports
from services import deal_store
from services import hermes_client
from services import ic_policy


AGENT_TASK_SCHEMA = "siq_ic_agent_task_v1"
AGENT_TASK_DRY_RUN_SCHEMA = "siq_ic_agent_task_dry_run_v1"
WORKFLOW_R1_AGENT_RUN_DRY_RUN_SCHEMA = "siq_ic_workflow_r1_agent_run_dry_run_v1"
WORKFLOW_R1_AGENT_RUN_SCHEMA = "siq_ic_workflow_r1_agent_run_v1"
WORKFLOW_R1_SERIAL_RUN_DRY_RUN_SCHEMA = "siq_ic_workflow_r1_serial_run_dry_run_v1"
WORKFLOW_R1_SERIAL_RUN_SCHEMA = "siq_ic_workflow_r1_serial_run_v1"
WORKFLOW_R2_RUN_DRY_RUN_SCHEMA = "siq_ic_workflow_r2_run_dry_run_v1"
WORKFLOW_R2_RUN_SCHEMA = "siq_ic_workflow_r2_run_v1"
WORKFLOW_R3_RUN_DRY_RUN_SCHEMA = "siq_ic_workflow_r3_run_dry_run_v1"
WORKFLOW_R3_RUN_SCHEMA = "siq_ic_workflow_r3_run_v1"
WORKFLOW_R4_FINALIZE_DRY_RUN_SCHEMA = "siq_ic_workflow_r4_finalize_dry_run_v1"
WORKFLOW_R4_FINALIZE_SCHEMA = "siq_ic_workflow_r4_finalize_v1"
R1_AGENT_READINESS_SCHEMA = "siq_ic_r1_agent_readiness_v1"
R1_AGENT_REPORT_SCHEMA = "siq_ic_r1_agent_report_v1"
R2_AGENT_REPORT_SCHEMA = "siq_ic_r2_agent_report_v1"
R3_REVIEW_SCHEMA = "siq_ic_r3_review_v1"
R3_AGENT_REVIEW_SCHEMA = "siq_ic_r3_agent_review_v1"
R4_DECISION_SCHEMA = "siq_ic_r4_decision_v1"
SUPPORTED_ROUNDS = {"R1"}
REPORT_STEMS = {
    "siq_ic_strategist": "strategist",
    "siq_ic_sector_expert": "sector_expert",
    "siq_ic_finance_auditor": "finance_auditor",
    "siq_ic_legal_scanner": "legal_scanner",
    "siq_ic_risk_controller": "risk_controller",
    "siq_ic_chairman": "chairman",
}
R1_SERIAL_MAX_AGENTS = len(REPORT_STEMS)
LEGACY_BY_PROFILE = {value: key for key, value in ic_policy.LEGACY_PROFILE_IDS.items()}
R2_AGENT_SEQUENCE = tuple(agent_id for agent_id in ic_policy.R1_AGENT_SEQUENCE if agent_id != "siq_ic_chairman")
ROLE_AGENT_FALLBACK = {
    "chairman": "siq_ic_chairman",
    "strategy": "siq_ic_strategist",
    "sector": "siq_ic_sector_expert",
    "finance": "siq_ic_finance_auditor",
    "legal": "siq_ic_legal_scanner",
    "risk": "siq_ic_risk_controller",
}
R1_GLOBAL_BLOCKING_PREFLIGHT_WARN_IDS = frozenset({"evidence.gate"})
R1_AGENT_BLOCKING_PREFLIGHT_WARN_IDS = frozenset({"evidence.gate", "retrieval.receipt_contract"})
R1_AGENT_RECEIPT_PREFLIGHT_WARN_IDS = frozenset({"retrieval.receipt_contract"})
DOWNSTREAM_BLOCKING_PREFLIGHT_WARN_IDS = frozenset({
    "evidence.gate",
    "retrieval.startup_receipts",
    "retrieval.receipt_contract",
    "r1.report_contract",
    "r1.report_evidence_refs",
})


def _require_package_dir(deal_id: str, *, wiki_root: Path | str | None = None) -> Path:
    package_dir = deal_store.safe_deal_dir(deal_id, wiki_root=wiki_root)
    if not (package_dir / "manifest.json").is_file():
        raise FileNotFoundError(deal_id)
    return package_dir


def _normalize_profile_id(profile_id: str) -> str:
    canonical = ic_policy.canonical_ic_profile_id(profile_id)
    if canonical not in ic_policy.IC_PROFILE_IDS:
        raise ValueError(f"Unknown IC profile: {profile_id}")
    if canonical == "siq_ic_master_coordinator":
        raise ValueError("IC agent task dry-run currently supports R1 expert/chairman profiles only")
    return canonical


def _normalize_round_name(round_name: str | None) -> str:
    value = str(round_name or "R1").strip().upper()
    if value not in SUPPORTED_ROUNDS:
        raise ValueError("IC agent task dry-run currently supports R1 only")
    return value


def _profile_label(profile_id: str) -> str:
    for profile in ic_policy.list_ic_profiles(include_runtime=False):
        if profile.get("id") == profile_id:
            return str(profile.get("label") or profile_id)
    return profile_id


def _canonical_keyed_payload(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    payload: dict[str, dict[str, Any]] = {}
    for key, item in value.items():
        if not isinstance(item, dict):
            continue
        canonical = ic_policy.canonical_ic_profile_id(str(item.get("agent_id") or key))
        normalized = dict(item)
        normalized["agent_id"] = canonical
        payload[canonical] = normalized
    return payload


def _receipt_agents(package_dir: Path) -> dict[str, dict[str, Any]]:
    payload = deal_store.read_json(package_dir / "phases" / "startup_receipts.json", {}) or {}
    agents = payload.get("agents", payload) if isinstance(payload, dict) else {}
    return _canonical_keyed_payload(agents)


def _r1_reports(package_dir: Path) -> dict[str, dict[str, Any]]:
    return _canonical_keyed_payload(deal_store.read_json(package_dir / "phases" / "r1_reports.json", {}) or {})


def _submitted_agents(workflow: dict[str, Any]) -> set[str]:
    r1_state = workflow.get("phases", {}).get("R1", {}) if isinstance(workflow.get("phases"), dict) else {}
    submitted = r1_state.get("submitted_agents") if isinstance(r1_state, dict) else []
    if not isinstance(submitted, list):
        return set()
    return {
        ic_policy.canonical_ic_profile_id(str(item))
        for item in submitted
        if str(item or "").strip()
    }


def _workflow_phase_allows(workflow: dict[str, Any], profile_id: str, round_name: str) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    current_phase = str(workflow.get("current_phase") or "R0")
    status = str(workflow.get("status") or "")
    if round_name == "R1" and current_phase not in {"R0", "R1"}:
        reasons.append(f"workflow_phase_not_r1_ready:{current_phase}")
    if status in {"r4_completed", "archived", "closed"}:
        reasons.append(f"workflow_status_closed:{status}")

    sequence = list(ic_policy.R1_AGENT_SEQUENCE)
    if profile_id in sequence:
        index = sequence.index(profile_id)
        submitted_set = _submitted_agents(workflow)
        missing_previous = [agent_id for agent_id in sequence[:index] if agent_id not in submitted_set]
        if missing_previous:
            reasons.append(f"r1_sequence_waiting_for:{','.join(missing_previous)}")
    return not reasons, reasons


def _receipt_for(package_dir: Path, deal_id: str, profile_id: str) -> dict[str, Any] | None:
    del deal_id
    return _receipt_agents(package_dir).get(profile_id)


def _preflight_blocks(
    preflight: dict[str, Any],
    *,
    warn_check_ids: set[str] | frozenset[str] | tuple[str, ...] = (),
    current_agent_id: str | None = None,
) -> list[str]:
    blocks: list[str] = []
    warn_ids = {str(item) for item in warn_check_ids}
    current_agent = ic_policy.canonical_ic_profile_id(str(current_agent_id)) if current_agent_id else None
    for check in preflight.get("checks", []):
        if not isinstance(check, dict):
            continue
        check_id = str(check.get("id") or "")
        status = str(check.get("status") or "")
        if status == "fail":
            blocks.append(f"preflight_fail:{check_id}")
            continue
        if status != "warn" or check_id not in warn_ids:
            continue
        if current_agent and check_id == "retrieval.receipt_contract" and not _preflight_check_mentions_agent(check, current_agent):
            continue
        blocks.append(f"preflight_warn:{check_id}")
    return blocks


def _preflight_warnings(preflight: dict[str, Any]) -> list[str]:
    return [
        f"preflight:{check.get('id')}:{check.get('status')}"
        for check in preflight.get("checks", [])
        if isinstance(check, dict) and check.get("status") in {"warn", "info"}
    ]


def preflight_execution_blocks(
    preflight: dict[str, Any],
    *,
    downstream: bool = False,
    current_agent_id: str | None = None,
) -> list[str]:
    return _preflight_blocks(
        preflight,
        warn_check_ids=DOWNSTREAM_BLOCKING_PREFLIGHT_WARN_IDS if downstream else R1_AGENT_BLOCKING_PREFLIGHT_WARN_IDS,
        current_agent_id=current_agent_id,
    )


def _preflight_check_mentions_agent(check: dict[str, Any], agent_id: str) -> bool:
    details = check.get("details") if isinstance(check.get("details"), dict) else {}
    for key in ("issues", "missing_agents", "advisories"):
        value = details.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    candidate = item.get("agent_id")
                else:
                    candidate = item
                if candidate and ic_policy.canonical_ic_profile_id(str(candidate)) == agent_id:
                    return True
    return False


def _workflow_global_blocks(workflow: dict[str, Any], round_name: str) -> list[str]:
    reasons: list[str] = []
    current_phase = str(workflow.get("current_phase") or "R0")
    status = str(workflow.get("status") or "")
    if round_name == "R1" and current_phase not in {"R0", "R1"}:
        reasons.append(f"workflow_phase_not_r1_ready:{current_phase}")
    if status in {"r4_completed", "archived", "closed"}:
        reasons.append(f"workflow_status_closed:{status}")
    return reasons


def _output_contract(profile_id: str, round_name: str) -> dict[str, Any]:
    stem = REPORT_STEMS.get(profile_id, profile_id.removeprefix("siq_ic_"))
    return {
        "json_path": "phases/r1_reports.json",
        "json_key": profile_id,
        "markdown_path": f"discussion/01_R1_{stem}_report.md",
        "required_fields": ["score", "recommendation", "verified", "assumed", "open_questions"],
        "round_name": round_name,
    }


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _text_preview(value: str, *, limit: int = 500) -> str:
    compact = " ".join(str(value or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "..."


def _extract_json_object(text: str) -> dict[str, Any]:
    """Best-effort extraction for Hermes outputs that include a JSON summary."""

    raw = str(text or "").strip()
    if not raw:
        return {}
    candidates = [raw]
    candidates.extend(match.group(1) for match in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL))
    first = raw.find("{")
    last = raw.rfind("}")
    if first != -1 and last > first:
        candidates.append(raw[first : last + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _agent_prompt(payload: dict[str, Any]) -> str:
    return (
        "你正在为 SIQ Research Engine 执行一级市场投委会 R1 专家任务。\n"
        "请严格遵守 payload 中的 hard_rules、input_artifacts 和 output_contract。\n"
        "输出应先给可读 Markdown 报告；必须在报告末尾附上一个 JSON 摘要，"
        "字段至少包括 score、recommendation、verified、assumed、open_questions、evidence_ids。\n\n"
        "Markdown 报告必须包含以下章节：## 检索结果摘要、### 共享底稿证据、"
        "### 私有知识库证据、### 信息缺口清单、### 检索后观点。\n\n"
        "任务 payload:\n"
        "```json\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
        "```"
    )


def _build_report_entry(
    *,
    task: dict[str, Any],
    run_id: str,
    output_text: str,
    parsed: dict[str, Any],
    created_by: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    output_contract = payload.get("output_contract") if isinstance(payload.get("output_contract"), dict) else {}
    now = deal_store.utc_now_iso()
    return {
        "schema_version": R1_AGENT_REPORT_SCHEMA,
        "deal_id": task.get("deal_id"),
        "agent_id": task.get("agent_id"),
        "legacy_agent_id": payload.get("legacy_agent_id"),
        "phase": "R1",
        "round_name": task.get("round_name"),
        "status": "completed",
        "score": parsed.get("score"),
        "recommendation": parsed.get("recommendation"),
        "verified": _as_list(parsed.get("verified")),
        "assumed": _as_list(parsed.get("assumed")),
        "open_questions": _as_list(parsed.get("open_questions")),
        "risk_flags": _as_list(parsed.get("risk_flags")),
        "key_points": _as_list(parsed.get("key_points")),
        "confidence": parsed.get("confidence"),
        "summary": parsed.get("summary") or _text_preview(output_text, limit=240),
        "evidence_ids": _as_list(parsed.get("evidence_ids")),
        "evidence_stats": parsed.get("evidence_stats") if isinstance(parsed.get("evidence_stats"), dict) else {},
        "startup_receipt_id": payload.get("startup_receipt_id"),
        "artifact_path": output_contract.get("markdown_path"),
        "markdown_path": output_contract.get("markdown_path"),
        "hermes_run_id": run_id,
        "output_preview": _text_preview(output_text),
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
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


def _report_evidence_ids(parsed: dict[str, Any]) -> list[str]:
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
        ids.update(_extract_evidence_ids(parsed.get(key)))
    evidence_stats = parsed.get("evidence_stats")
    if isinstance(evidence_stats, dict):
        ids.update(_extract_evidence_ids(evidence_stats))
    return sorted(ids)


def _validate_r1_report_contract(
    *,
    package_dir: Path,
    task: dict[str, Any],
    parsed: dict[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    if not parsed:
        errors.append("json_summary_missing_or_invalid")

    score = _numeric(parsed.get("score"))
    if score is None:
        errors.append("score_missing_or_not_numeric")
    elif score < 0 or score > 100:
        errors.append("score_out_of_range")

    recommendation = str(parsed.get("recommendation") or "").strip()
    if not recommendation:
        errors.append("recommendation_missing")

    for field in ("verified", "assumed", "open_questions"):
        if field not in parsed or parsed.get(field) in (None, ""):
            errors.append(f"{field}_missing")

    parsed_agent_id = str(parsed.get("agent_id") or "").strip()
    if parsed_agent_id:
        parsed_agent_id = ic_policy.canonical_ic_profile_id(parsed_agent_id)
        if parsed_agent_id != task.get("agent_id"):
            errors.append("agent_id_mismatch")

    parsed_round = str(parsed.get("round_name") or parsed.get("phase") or "").strip().upper()
    if parsed_round and parsed_round != "R1":
        errors.append("round_name_mismatch")

    known_ids = _known_evidence_ids(package_dir)
    evidence_ids = _report_evidence_ids(parsed)
    if not evidence_ids:
        errors.append("evidence_ids_missing")
    unknown_ids = sorted(set(evidence_ids) - known_ids)
    if unknown_ids:
        errors.append("evidence_ids_unknown:" + ",".join(unknown_ids))

    if errors:
        raise ValueError("R1 Hermes output contract invalid: " + "; ".join(errors))

    normalized = dict(parsed)
    normalized["score"] = int(score) if score.is_integer() else score
    normalized["recommendation"] = recommendation
    normalized["evidence_ids"] = evidence_ids
    return normalized


def _write_markdown_report(
    package_dir: Path,
    *,
    task: dict[str, Any],
    run_id: str,
    output_text: str,
) -> str:
    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    output_contract = payload.get("output_contract") if isinstance(payload.get("output_contract"), dict) else {}
    relative = str(output_contract.get("markdown_path") or "discussion/01_R1_agent_report.md")
    path = package_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    header = [
        f"# R1 Agent Report - {task.get('agent_id')}",
        "",
        f"- deal_id: `{task.get('deal_id')}`",
        f"- round_name: `{task.get('round_name')}`",
        f"- hermes_run_id: `{run_id}`",
        f"- startup_receipt_id: `{payload.get('startup_receipt_id')}`",
        "",
        "## 检索结果摘要",
        "",
        "### 共享底稿证据",
        "",
        f"- Startup receipt: `{payload.get('startup_receipt_id')}`",
        f"- Evidence index: `{(payload.get('input_artifacts') or {}).get('evidence_index')}`",
        "",
        "### 私有知识库证据",
        "",
        "- 由对应 Hermes profile 在执行时按任务 payload 和 profile 规则补充。",
        "",
        "### 信息缺口清单",
        "",
        "- 以 Hermes 输出和 JSON 摘要中的 `open_questions` 为准。",
        "",
        "### 检索后观点",
        "",
        "---",
        "",
    ]
    path.write_text("\n".join(header) + str(output_text or "").strip() + "\n", encoding="utf-8")
    return relative


def _merge_r1_report(package_dir: Path, profile_id: str, report_entry: dict[str, Any]) -> str:
    relative = "phases/r1_reports.json"
    path = package_dir / relative
    reports = deal_store.read_json(path, {}) or {}
    if not isinstance(reports, dict) or isinstance(reports.get("reports"), dict):
        reports = dict(reports.get("reports") or reports) if isinstance(reports, dict) else {}
    reports[profile_id] = report_entry
    deal_store.write_json(path, reports)
    return relative


def _advance_workflow_for_r1_report(package_dir: Path, profile_id: str) -> dict[str, Any]:
    workflow_path = package_dir / "phases" / "workflow_state.json"
    workflow = deal_store.read_json(workflow_path, {}) or {}
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
    submitted = [ic_policy.canonical_ic_profile_id(str(item)) for item in submitted if str(item or "").strip()]
    if profile_id not in submitted:
        submitted.append(profile_id)

    complete = all(agent_id in set(submitted) for agent_id in ic_policy.R1_AGENT_SEQUENCE)
    r1.update({
        "status": "completed" if complete else "in_progress",
        "submitted_agents": submitted,
        "latest_agent_id": profile_id,
        "updated_at": now,
    })
    r1.setdefault("started_at", now)
    workflow["current_phase"] = "R1"
    if complete:
        r1["completed_at"] = now
        workflow["status"] = "r1_completed"
    else:
        workflow["status"] = "r1_in_progress"
    workflow["updated_at"] = now
    deal_store.write_json(workflow_path, workflow)
    return workflow


def _touch_package_status(package_dir: Path, status: str) -> None:
    now = deal_store.utc_now_iso()
    for relative in ("manifest.json", "project_meta.json"):
        path = package_dir / relative
        payload = deal_store.read_json(path, {}) or {}
        if not isinstance(payload, dict):
            continue
        payload["updated_at"] = now
        if relative == "project_meta.json":
            payload["status"] = status
        deal_store.write_json(path, payload)


def _update_project_decision(
    package_dir: Path,
    *,
    status: str,
    final_decision: str | None = None,
    final_score: float | None = None,
) -> None:
    path = package_dir / "project_meta.json"
    payload = deal_store.read_json(path, {}) or {}
    if not isinstance(payload, dict):
        return
    payload["updated_at"] = deal_store.utc_now_iso()
    payload["status"] = status
    if final_decision is not None:
        payload["final_decision"] = final_decision
    if final_score is not None:
        payload["final_score"] = final_score
    deal_store.write_json(path, payload)


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


def _round_score(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 2)


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


def _string_items(value: Any) -> list[str]:
    if isinstance(value, list):
        return _dedupe_strings(value)
    if value in (None, ""):
        return []
    return _dedupe_strings([value])


def _markdown_list(values: list[Any], *, empty: str = "暂无") -> str:
    items = _dedupe_strings(values)
    if not items:
        return f"- {empty}"
    return "\n".join(f"- {item}" for item in items)


def _phase_state(workflow: dict[str, Any], phase: str) -> dict[str, Any]:
    phases = workflow.setdefault("phases", {})
    if not isinstance(phases, dict):
        phases = {}
        workflow["phases"] = phases
    state = phases.setdefault(phase, {})
    if not isinstance(state, dict):
        state = {}
        phases[phase] = state
    return state


def _advance_workflow_phase(
    package_dir: Path,
    *,
    phase: str,
    workflow_status: str,
    phase_status: str = "completed",
    artifacts: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    workflow_path = package_dir / "phases" / "workflow_state.json"
    workflow = deal_store.read_json(workflow_path, {}) or {}
    if not isinstance(workflow, dict):
        workflow = {}
    now = deal_store.utc_now_iso()
    phase_payload = _phase_state(workflow, phase)
    phase_payload.setdefault("started_at", now)
    phase_payload.update({
        "status": phase_status,
        "updated_at": now,
    })
    if phase_status in {"completed", "skipped"}:
        phase_payload["completed_at"] = now
    if artifacts:
        phase_payload["artifacts"] = artifacts
    if extra:
        phase_payload.update(extra)
    workflow["current_phase"] = phase
    workflow["status"] = workflow_status
    workflow["updated_at"] = now
    deal_store.write_json(workflow_path, workflow)
    return workflow


def _policy() -> dict[str, Any]:
    try:
        return ic_policy.read_ic_workflow_policy()
    except (FileNotFoundError, ValueError):
        return {}


def _policy_min_expert_reports(policy: dict[str, Any]) -> int:
    gate = policy.get("evidence_gate") if isinstance(policy.get("evidence_gate"), dict) else {}
    value = _numeric(gate.get("min_expert_reports"))
    if value is None:
        return len(R2_AGENT_SEQUENCE)
    return max(1, int(value))


def _policy_max_unresolved_disputes(policy: dict[str, Any]) -> int:
    gate = policy.get("evidence_gate") if isinstance(policy.get("evidence_gate"), dict) else {}
    value = _numeric(gate.get("max_unresolved_disputes"))
    if value is None:
        return 0
    return max(0, int(value))


def _role_agent_ids(policy: dict[str, Any]) -> dict[str, str]:
    roles = policy.get("roles") if isinstance(policy.get("roles"), dict) else {}
    mapping: dict[str, str] = {}
    for role, fallback in ROLE_AGENT_FALLBACK.items():
        role_payload = roles.get(role) if isinstance(roles.get(role), dict) else {}
        mapping[role] = ic_policy.canonical_ic_profile_id(str(role_payload.get("agent_id") or fallback))
    return mapping


def _agent_role(agent_id: str, policy: dict[str, Any]) -> str:
    for role, role_agent_id in _role_agent_ids(policy).items():
        if role_agent_id == agent_id:
            return role
    return agent_id.removeprefix("siq_ic_")


def _preflight_failures(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
    warn_check_ids: set[str] | frozenset[str] | tuple[str, ...] = (),
) -> tuple[dict[str, Any], list[str], list[str]]:
    preflight = deal_contracts.run_deal_preflight(deal_id, wiki_root=wiki_root)
    blocks = _preflight_blocks(preflight, warn_check_ids=warn_check_ids)
    warnings = _preflight_warnings(preflight)
    return preflight, blocks, warnings


def _r1_5_summary(deal_id: str, *, wiki_root: Path | str | None = None) -> dict[str, Any]:
    try:
        return deal_disputes.summarize_deal_disputes(deal_id, wiki_root=wiki_root)
    except FileNotFoundError:
        raise
    except ValueError:
        return {}


def _r2_reports(package_dir: Path) -> dict[str, dict[str, Any]]:
    return _canonical_keyed_payload(deal_store.read_json(package_dir / "phases" / "r2_reports.json", {}) or {})


def _r3_payload(package_dir: Path) -> dict[str, Any]:
    raw = deal_store.read_json(package_dir / "phases" / "r3_reports.json", {}) or {}
    return raw if isinstance(raw, dict) else {}


def _resolved_rulings(disputes_summary: dict[str, Any]) -> list[str]:
    rulings: list[str] = []
    disputes = disputes_summary.get("disputes") if isinstance(disputes_summary.get("disputes"), list) else []
    for dispute in disputes:
        if not isinstance(dispute, dict) or not dispute.get("resolved"):
            continue
        ruling = dispute.get("chairman_ruling") if isinstance(dispute.get("chairman_ruling"), dict) else {}
        decision = str(ruling.get("decision") or "").strip()
        rationale = str(ruling.get("rationale") or "").strip()
        topic = str(dispute.get("topic") or dispute.get("dispute_id") or "").strip()
        text = " - ".join(item for item in (topic, decision, rationale) if item)
        if text:
            rulings.append(text)
    return _dedupe_strings(rulings)


def _unresolved_dispute_count(disputes_summary: dict[str, Any]) -> int:
    counts = disputes_summary.get("counts") if isinstance(disputes_summary.get("counts"), dict) else {}
    return int(counts.get("unresolved") or 0)


def _write_text_artifact(package_dir: Path, relative_path: str, content: str) -> str:
    path = package_dir / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return relative_path


def _write_r2_markdown(
    package_dir: Path,
    *,
    deal_id: str,
    reports: dict[str, dict[str, Any]],
    rulings: list[str],
) -> str:
    lines = [
        "# R2 观点完善汇总",
        "",
        f"- deal_id: `{deal_id}`",
        "- generation_mode: `deterministic_r1_5_revision_v1`",
        "",
        "## 主席裁决引用",
        "",
        _markdown_list(rulings, empty="未发现已裁决分歧，R2 保留 R1 结论并补充留痕。"),
        "",
        "## 专家修订",
        "",
    ]
    for agent_id in R2_AGENT_SEQUENCE:
        report = reports.get(agent_id, {})
        lines.extend([
            f"### {_profile_label(agent_id)}",
            "",
            f"- R1 score: `{report.get('r1_score')}`",
            f"- R2 score: `{report.get('r2_score')}`",
            f"- Recommendation: `{report.get('recommendation')}`",
            "",
            str(report.get("summary") or ""),
            "",
            "#### Revisions",
            "",
            _markdown_list(report.get("revisions") if isinstance(report.get("revisions"), list) else []),
            "",
        ])
    return _write_text_artifact(package_dir, deal_reports.R2_REPORT_ARTIFACT_PATH, "\n".join(lines))


def _write_r3_markdown(
    package_dir: Path,
    *,
    deal_id: str,
    payload: dict[str, Any],
) -> str:
    mode = str(payload.get("mode") or "normal")
    lines = [
        "# R3 红蓝对抗",
        "",
        f"- deal_id: `{deal_id}`",
        f"- mode: `{mode}`",
        f"- generation_mode: `{payload.get('generation_mode')}`",
        "",
    ]
    if mode == "skip":
        lines.extend([
            "## Skip Reason",
            "",
            str(payload.get("skip_reason") or "未提供跳过原因"),
            "",
        ])
    else:
        lines.extend(["## Challenges", ""])
        reports = _canonical_keyed_payload(payload.get("reports") or {})
        for agent_id in ic_policy.R1_AGENT_SEQUENCE:
            report = reports.get(agent_id)
            if not report:
                continue
            lines.extend([
                f"### {_profile_label(agent_id)}",
                "",
                f"- stance: `{report.get('stance')}`",
                f"- recommendation: `{report.get('recommendation')}`",
                "",
                str(report.get("summary") or ""),
                "",
                "#### Challenge Items",
                "",
                _markdown_list(report.get("challenges") if isinstance(report.get("challenges"), list) else []),
                "",
            ])
    return _write_text_artifact(package_dir, deal_reports.R3_REVIEW_ARTIFACT_PATH, "\n".join(lines))


def _r2_gate(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    policy = _policy()
    workflow = deal_store.read_json(package_dir / "phases" / "workflow_state.json", {}) or {}
    r1_reports = _r1_reports(package_dir)
    preflight, preflight_blocks, preflight_warnings = _preflight_failures(
        normalized_deal_id,
        wiki_root=wiki_root,
        warn_check_ids=DOWNSTREAM_BLOCKING_PREFLIGHT_WARN_IDS,
    )
    disputes = _r1_5_summary(normalized_deal_id, wiki_root=wiki_root)

    blocks = list(preflight_blocks)
    warnings = list(preflight_warnings)
    expert_reports = [agent_id for agent_id in R2_AGENT_SEQUENCE if agent_id in r1_reports]
    min_reports = _policy_min_expert_reports(policy)
    if len(expert_reports) < min_reports:
        blocks.append(f"r1_expert_reports_below_min:{len(expert_reports)}/{min_reports}")
    missing_experts = [agent_id for agent_id in R2_AGENT_SEQUENCE if agent_id not in r1_reports]
    if missing_experts:
        warnings.append(f"r1_expert_reports_missing:{','.join(missing_experts)}")
    unresolved = _unresolved_dispute_count(disputes)
    max_unresolved = _policy_max_unresolved_disputes(policy)
    if unresolved > max_unresolved:
        blocks.append(f"r1_5_unresolved_disputes:{unresolved}/{max_unresolved}")
    if str(disputes.get("status") or "missing") == "missing":
        blocks.append("r1_5_disputes_missing")
    if str(workflow.get("status") or "") in {"r4_completed", "archived", "closed"}:
        blocks.append(f"workflow_status_closed:{workflow.get('status')}")
    return {
        "package_dir": package_dir,
        "deal_id": normalized_deal_id,
        "policy": policy,
        "workflow": workflow,
        "r1_reports": r1_reports,
        "preflight": preflight,
        "disputes": disputes,
        "blocking_reasons": _dedupe_strings(blocks),
        "warnings": _dedupe_strings(warnings),
        "counts": {
            "r1_expert_reports": len(expert_reports),
            "required_expert_reports": min_reports,
            "unresolved_disputes": unresolved,
        },
    }


def _build_r2_reports_payload(plan: dict[str, Any], *, created_by: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    now = deal_store.utc_now_iso()
    r1_reports = plan["r1_reports"]
    rulings = _resolved_rulings(plan.get("disputes") or {})
    reports: dict[str, dict[str, Any]] = {}
    for agent_id in R2_AGENT_SEQUENCE:
        r1_report = r1_reports.get(agent_id, {})
        r1_score = r1_report.get("score")
        r2_score = r1_report.get("r2_score", r1_score)
        r1_numeric = _numeric(r1_score)
        r2_numeric = _numeric(r2_score)
        revisions = [
            "保留 R1 核心结论，并基于 R1.5 裁决完成 R2 留痕。",
        ]
        revisions.extend(rulings)
        reports[agent_id] = {
            "schema_version": R2_AGENT_REPORT_SCHEMA,
            "deal_id": plan["deal_id"],
            "agent_id": agent_id,
            "legacy_agent_id": LEGACY_BY_PROFILE.get(agent_id),
            "round_name": "R2",
            "status": "completed",
            "r1_score": r1_score,
            "r2_score": r2_score,
            "score": r2_score,
            "score_change": (
                _round_score(r2_numeric - r1_numeric)
                if r1_numeric is not None and r2_numeric is not None
                else None
            ),
            "recommendation": r1_report.get("recommendation"),
            "confidence": r1_report.get("confidence"),
            "summary": (
                f"{_profile_label(agent_id)} R2 deterministic revision retained the R1 position "
                "and incorporated available chairman rulings."
            ),
            "revisions": revisions,
            "verified": _as_list(r1_report.get("verified")),
            "assumed": _as_list(r1_report.get("assumed")),
            "open_questions": _as_list(r1_report.get("open_questions")),
            "risk_flags": _as_list(r1_report.get("risk_flags")),
            "key_points": _as_list(r1_report.get("key_points")),
            "evidence_ids": _as_list(r1_report.get("evidence_ids")),
            "artifact_path": deal_reports.R2_REPORT_ARTIFACT_PATH,
            "generation_mode": "deterministic_r1_5_revision_v1",
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }
    return reports


def build_workflow_r2_run_dry_run(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    plan = _r2_gate(deal_id, wiki_root=wiki_root)
    allowed = not plan["blocking_reasons"]
    reports = _build_r2_reports_payload(plan) if allowed else {}
    return deal_store.redact_public_payload({
        "schema_version": WORKFLOW_R2_RUN_DRY_RUN_SCHEMA,
        "deal_id": plan["deal_id"],
        "workflow_action": "run-r2",
        "dry_run": True,
        "allowed": allowed,
        "would_write": allowed,
        "queued": False,
        "job_id": None,
        "blocking_reasons": plan["blocking_reasons"],
        "warnings": plan["warnings"],
        "counts": plan["counts"],
        "output_paths": {
            "json": "phases/r2_reports.json",
            "markdown": deal_reports.R2_REPORT_ARTIFACT_PATH,
        },
        "reports_preview": reports,
        "hermes_called": False,
        "report_written": False,
        "workflow_advanced": False,
    })


def run_workflow_r2(
    deal_id: str,
    *,
    created_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    plan = _r2_gate(deal_id, wiki_root=wiki_root)
    if plan["blocking_reasons"]:
        raise ValueError(f"R2 run blocked: {', '.join(plan['blocking_reasons'])}")
    reports = _build_r2_reports_payload(plan, created_by=created_by)
    package_dir = plan["package_dir"]
    json_path = "phases/r2_reports.json"
    deal_store.write_json(package_dir / json_path, reports)
    markdown_path = _write_r2_markdown(
        package_dir,
        deal_id=plan["deal_id"],
        reports=reports,
        rulings=_resolved_rulings(plan.get("disputes") or {}),
    )
    workflow = _advance_workflow_phase(
        package_dir,
        phase="R2",
        workflow_status="r2_completed",
        artifacts=[json_path, markdown_path],
    )
    _touch_package_status(package_dir, "r2_completed")
    audit_event = deal_store.append_audit_event(
        plan["deal_id"],
        {
            "event_type": "deal_r2_run_completed",
            "deal_id": plan["deal_id"],
            "report_count": len(reports),
            "json_path": json_path,
            "markdown_path": markdown_path,
            "created_by": created_by,
        },
        wiki_root=wiki_root,
    )
    return deal_store.redact_public_payload({
        "schema_version": WORKFLOW_R2_RUN_SCHEMA,
        "deal_id": plan["deal_id"],
        "workflow_action": "run-r2",
        "dry_run": False,
        "allowed": True,
        "queued": False,
        "job_id": None,
        "blocking_reasons": [],
        "warnings": plan["warnings"],
        "counts": {**plan["counts"], "r2_reports": len(reports)},
        "output_paths": {"json": json_path, "markdown": markdown_path},
        "reports": reports,
        "hermes_called": False,
        "report_written": True,
        "workflow_advanced": True,
        "workflow": workflow,
        "audit_event": audit_event,
    })


def _r3_gate(
    deal_id: str,
    *,
    skip: bool = False,
    skip_reason: str | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    workflow = deal_store.read_json(package_dir / "phases" / "workflow_state.json", {}) or {}
    r2_reports = _r2_reports(package_dir)
    preflight, preflight_blocks, preflight_warnings = _preflight_failures(
        normalized_deal_id,
        wiki_root=wiki_root,
        warn_check_ids=DOWNSTREAM_BLOCKING_PREFLIGHT_WARN_IDS,
    )
    blocks: list[str] = list(preflight_blocks)
    warnings: list[str] = list(preflight_warnings)
    if len(r2_reports) < len(R2_AGENT_SEQUENCE):
        blocks.append(f"r2_reports_incomplete:{len(r2_reports)}/{len(R2_AGENT_SEQUENCE)}")
    missing = [agent_id for agent_id in R2_AGENT_SEQUENCE if agent_id not in r2_reports]
    if missing:
        warnings.append(f"r2_reports_missing:{','.join(missing)}")
    if skip and not str(skip_reason or "").strip():
        blocks.append("r3_skip_reason_required")
    if str(workflow.get("status") or "") in {"r4_completed", "archived", "closed"}:
        blocks.append(f"workflow_status_closed:{workflow.get('status')}")
    return {
        "package_dir": package_dir,
        "deal_id": normalized_deal_id,
        "workflow": workflow,
        "r2_reports": r2_reports,
        "preflight": preflight,
        "skip": bool(skip),
        "skip_reason": str(skip_reason or "").strip(),
        "blocking_reasons": _dedupe_strings(blocks),
        "warnings": _dedupe_strings(warnings),
        "counts": {
            "r2_reports": len(r2_reports),
            "required_r2_reports": len(R2_AGENT_SEQUENCE),
        },
    }


def _build_r3_payload(plan: dict[str, Any], *, created_by: dict[str, Any] | None = None) -> dict[str, Any]:
    now = deal_store.utc_now_iso()
    if plan.get("skip"):
        return {
            "schema_version": R3_REVIEW_SCHEMA,
            "deal_id": plan["deal_id"],
            "round_name": "R3",
            "mode": "skip",
            "skipped": True,
            "skip_reason": plan.get("skip_reason"),
            "reports": {},
            "generation_mode": "deterministic_r3_skip_v1",
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }

    reports: dict[str, dict[str, Any]] = {}
    for agent_id, r2_report in plan["r2_reports"].items():
        challenges = _dedupe_strings(
            _string_items(r2_report.get("open_questions"))
            + _string_items(r2_report.get("risk_flags"))
            + _string_items(r2_report.get("required_followups"))
        )
        if not challenges:
            challenges = ["No material deterministic challenge was found in the R2 report."]
        reports[agent_id] = {
            "schema_version": R3_AGENT_REVIEW_SCHEMA,
            "deal_id": plan["deal_id"],
            "agent_id": agent_id,
            "round_name": "R3",
            "status": "completed",
            "stance": "challenge" if challenges else "support_with_checks",
            "recommendation": r2_report.get("recommendation"),
            "summary": f"{_profile_label(agent_id)} R3 deterministic review challenged unresolved R2 assumptions.",
            "challenges": challenges,
            "evidence_ids": _as_list(r2_report.get("evidence_ids")),
            "generation_mode": "deterministic_r2_red_blue_review_v1",
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }
    return {
        "schema_version": R3_REVIEW_SCHEMA,
        "deal_id": plan["deal_id"],
        "round_name": "R3",
        "mode": "normal",
        "skipped": False,
        "reports": reports,
        "generation_mode": "deterministic_r2_red_blue_review_v1",
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
    }


def build_workflow_r3_run_dry_run(
    deal_id: str,
    *,
    skip: bool = False,
    skip_reason: str | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    plan = _r3_gate(deal_id, skip=skip, skip_reason=skip_reason, wiki_root=wiki_root)
    allowed = not plan["blocking_reasons"]
    payload = _build_r3_payload(plan) if allowed else {}
    return deal_store.redact_public_payload({
        "schema_version": WORKFLOW_R3_RUN_DRY_RUN_SCHEMA,
        "deal_id": plan["deal_id"],
        "workflow_action": "run-r3",
        "dry_run": True,
        "allowed": allowed,
        "would_write": allowed,
        "queued": False,
        "job_id": None,
        "blocking_reasons": plan["blocking_reasons"],
        "warnings": plan["warnings"],
        "counts": plan["counts"],
        "mode": "skip" if skip else "normal",
        "skip_reason": plan.get("skip_reason") or None,
        "output_paths": {
            "json": "phases/r3_reports.json",
            "markdown": deal_reports.R3_REVIEW_ARTIFACT_PATH,
        },
        "payload_preview": payload,
        "hermes_called": False,
        "report_written": False,
        "workflow_advanced": False,
    })


def run_workflow_r3(
    deal_id: str,
    *,
    skip: bool = False,
    skip_reason: str | None = None,
    created_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    plan = _r3_gate(deal_id, skip=skip, skip_reason=skip_reason, wiki_root=wiki_root)
    if plan["blocking_reasons"]:
        raise ValueError(f"R3 run blocked: {', '.join(plan['blocking_reasons'])}")
    payload = _build_r3_payload(plan, created_by=created_by)
    package_dir = plan["package_dir"]
    json_path = "phases/r3_reports.json"
    deal_store.write_json(package_dir / json_path, payload)
    markdown_path = _write_r3_markdown(package_dir, deal_id=plan["deal_id"], payload=payload)
    workflow_status = "r3_skipped" if payload.get("mode") == "skip" else "r3_completed"
    workflow = _advance_workflow_phase(
        package_dir,
        phase="R3",
        workflow_status=workflow_status,
        phase_status="skipped" if payload.get("mode") == "skip" else "completed",
        artifacts=[json_path, markdown_path],
        extra={"mode": payload.get("mode"), "skip_reason": payload.get("skip_reason")},
    )
    _touch_package_status(package_dir, workflow_status)
    audit_event = deal_store.append_audit_event(
        plan["deal_id"],
        {
            "event_type": "deal_r3_run_completed",
            "deal_id": plan["deal_id"],
            "mode": payload.get("mode"),
            "skip_reason": payload.get("skip_reason"),
            "json_path": json_path,
            "markdown_path": markdown_path,
            "created_by": created_by,
        },
        wiki_root=wiki_root,
    )
    return deal_store.redact_public_payload({
        "schema_version": WORKFLOW_R3_RUN_SCHEMA,
        "deal_id": plan["deal_id"],
        "workflow_action": "run-r3",
        "dry_run": False,
        "allowed": True,
        "queued": False,
        "job_id": None,
        "blocking_reasons": [],
        "warnings": plan["warnings"],
        "counts": {
            **plan["counts"],
            "r3_reports": len(payload.get("reports") or {}),
        },
        "mode": payload.get("mode"),
        "skip_reason": payload.get("skip_reason"),
        "output_paths": {"json": json_path, "markdown": markdown_path},
        "payload": payload,
        "hermes_called": False,
        "report_written": True,
        "workflow_advanced": True,
        "workflow": workflow,
        "audit_event": audit_event,
    })


def _r4_gate(
    deal_id: str,
    *,
    overwrite: bool = False,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    r1_reports = _r1_reports(package_dir)
    r2_reports = _r2_reports(package_dir)
    r3_summary = deal_reports.summarize_r3_review(normalized_deal_id, wiki_root=wiki_root)
    preflight, preflight_blocks, preflight_warnings = _preflight_failures(
        normalized_deal_id,
        wiki_root=wiki_root,
        warn_check_ids=DOWNSTREAM_BLOCKING_PREFLIGHT_WARN_IDS,
    )
    blocks: list[str] = list(preflight_blocks)
    warnings: list[str] = list(preflight_warnings)
    if len(r2_reports) < len(R2_AGENT_SEQUENCE):
        blocks.append(f"r2_reports_incomplete:{len(r2_reports)}/{len(R2_AGENT_SEQUENCE)}")
    r3_status = str(r3_summary.get("status") or "missing")
    if r3_status == "missing":
        blocks.append("r3_review_missing")
    elif r3_status == "warn":
        blocks.append("r3_review_warn")
    chairman_report = r1_reports.get("siq_ic_chairman", {})
    chairman_score = _numeric(chairman_report.get("chairman_dimension_score", chairman_report.get("score")))
    if chairman_score is None:
        blocks.append("chairman_score_missing_for_dimension_score")
    existing_decision = deal_store.read_json(package_dir / "phases" / "r4_decision.json", None)
    existing_markdown = (package_dir / "decision" / "IC_DECISION_REPORT.md").is_file()
    if not overwrite and (isinstance(existing_decision, dict) and existing_decision or existing_markdown):
        blocks.append("r4_decision_already_exists")
    workflow = deal_store.read_json(package_dir / "phases" / "workflow_state.json", {}) or {}
    if str(workflow.get("status") or "") in {"r4_completed", "archived", "closed"}:
        warnings.append(f"workflow_status_already_final:{workflow.get('status')}")
    return {
        "package_dir": package_dir,
        "deal_id": normalized_deal_id,
        "policy": _policy(),
        "workflow": workflow,
        "preflight": preflight,
        "r1_reports": r1_reports,
        "r2_reports": r2_reports,
        "r3_summary": r3_summary,
        "chairman_score": chairman_score,
        "blocking_reasons": _dedupe_strings(blocks),
        "warnings": _dedupe_strings(warnings),
        "counts": {
            "r2_reports": len(r2_reports),
            "required_r2_reports": len(R2_AGENT_SEQUENCE),
            "r3_reports": int((r3_summary.get("counts") or {}).get("reports") or 0) if isinstance(r3_summary.get("counts"), dict) else 0,
        },
    }


def _weighted_agent_score(plan: dict[str, Any]) -> tuple[float | None, list[dict[str, Any]], list[str]]:
    policy = plan.get("policy") if isinstance(plan.get("policy"), dict) else {}
    weights = policy.get("weights") if isinstance(policy.get("weights"), dict) else {}
    role_agents = _role_agent_ids(policy)
    scoring_inputs: list[dict[str, Any]] = []
    warnings: list[str] = []
    weighted_sum = 0.0
    weight_sum = 0.0
    for role, raw_weight in weights.items():
        weight = _numeric(raw_weight)
        if weight is None or weight <= 0:
            continue
        agent_id = role_agents.get(str(role), ROLE_AGENT_FALLBACK.get(str(role), str(role)))
        report = plan["r2_reports"].get(agent_id) or plan["r1_reports"].get(agent_id) or {}
        score = _numeric(report.get("r2_score", report.get("score")))
        scoring_inputs.append({
            "role": role,
            "agent_id": agent_id,
            "weight": weight,
            "score": score,
            "source_round": report.get("round_name"),
        })
        if score is None:
            warnings.append(f"weighted_score_missing:{role}:{agent_id}")
            continue
        weighted_sum += score * weight
        weight_sum += weight
    if weight_sum <= 0:
        return None, scoring_inputs, warnings
    if weight_sum < sum(float(item["weight"]) for item in scoring_inputs if item.get("weight") is not None):
        warnings.append(f"weighted_score_normalized_weight_sum:{weight_sum:.2f}")
    return _round_score(weighted_sum / weight_sum), scoring_inputs, warnings


def _threshold_result(score: float, policy: dict[str, Any]) -> str:
    thresholds = policy.get("thresholds") if isinstance(policy.get("thresholds"), dict) else {}
    pass_min = _numeric(thresholds.get("pass")) or 70.0
    review_min = _numeric(thresholds.get("review_min")) or 68.0
    review_max = _numeric(thresholds.get("review_max")) or pass_min - 1
    if score >= pass_min:
        return "pass"
    if review_min <= score <= review_max:
        return "review"
    return "fail"


def _qualitative_decision(threshold_result: str) -> str:
    if threshold_result == "pass":
        return "建议投资，但需设置估值、退出和关键客户验证保护条款"
    if threshold_result == "review":
        return "建议复核后再议，需补齐关键证据和条款保护"
    return "暂缓投资，待核心风险和证据缺口关闭后重新提交"


def _r4_conditions(plan: dict[str, Any]) -> list[str]:
    conditions: list[Any] = []
    disputes = _r1_5_summary(plan["deal_id"], wiki_root=plan.get("wiki_root"))
    for dispute in disputes.get("disputes", []) if isinstance(disputes.get("disputes"), list) else []:
        if not isinstance(dispute, dict):
            continue
        conditions.extend(_string_items(dispute.get("required_followups")))
    r3 = _r3_payload(plan["package_dir"])
    r3_reports = _canonical_keyed_payload(r3.get("reports") or {})
    for report in r3_reports.values():
        conditions.extend(_string_items(report.get("challenges")))
    for report in plan["r2_reports"].values():
        conditions.extend(_string_items(report.get("open_questions")))
        conditions.extend(_string_items(report.get("risk_flags")))
    values = _dedupe_strings(conditions)
    return values[:12] or ["投委会人工确认后方可进入投资执行流程"]


def _r4_monitoring_metrics(plan: dict[str, Any]) -> list[str]:
    del plan
    return [
        "核心客户续约和收入确认质量",
        "现金流、毛利率和估值敏感性",
        "重大合同、知识产权、诉讼和资质状态",
        "供应链、舆情和黑天鹅风险",
    ]


def _build_r4_decision_payload(plan: dict[str, Any], *, created_by: dict[str, Any] | None = None) -> dict[str, Any]:
    weighted_score, scoring_inputs, scoring_warnings = _weighted_agent_score(plan)
    chairman_score = _round_score(plan.get("chairman_score"))
    if weighted_score is None:
        raise ValueError("R4 finalize blocked: weighted_agent_score_unavailable")
    if chairman_score is None:
        raise ValueError("R4 finalize blocked: chairman_dimension_score_unavailable")
    final_score = chairman_score
    threshold = _threshold_result(final_score, plan["policy"])
    now = deal_store.utc_now_iso()
    return {
        "schema_version": R4_DECISION_SCHEMA,
        "deal_id": plan["deal_id"],
        "decision": threshold,
        "final_score": final_score,
        "weighted_agent_score": weighted_score,
        "chairman_dimension_score": chairman_score,
        "chairman_qualitative_decision": _qualitative_decision(threshold),
        "threshold_result": threshold,
        "conditions": _r4_conditions(plan),
        "monitoring_metrics": _r4_monitoring_metrics(plan),
        "human_confirmation": {
            "status": "pending",
            "confirmed_by": None,
            "confirmed_at": None,
            "override_reason": None,
        },
        "artifact_paths": {
            "markdown": "decision/IC_DECISION_REPORT.md",
            "html": "decision/IC_DECISION_REPORT.html",
        },
        "scoring_inputs": {
            "weighted_agent_score": scoring_inputs,
            "chairman_dimension_source": "siq_ic_chairman.r1_report_score",
            "warnings": scoring_warnings,
        },
        "generation_mode": "deterministic_siq_r4_finalize_v1",
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
    }


def _write_r4_markdown(package_dir: Path, decision: dict[str, Any]) -> str:
    lines = [
        "# IC Decision Report",
        "",
        "## Conclusion",
        "",
        f"- Decision: `{decision.get('decision')}`",
        f"- Final score: `{decision.get('final_score')}`",
        f"- Chairman qualitative decision: {decision.get('chairman_qualitative_decision')}",
        "",
        "## Evidence sufficiency",
        "",
        f"- Weighted agent score: `{decision.get('weighted_agent_score')}`",
        f"- Chairman dimension score: `{decision.get('chairman_dimension_score')}`",
        f"- Threshold result: `{decision.get('threshold_result')}`",
        "",
        "## Key verified facts",
        "",
        "- See R1/R2 expert reports and deal evidence package for source-linked facts.",
        "",
        "## Key unverified assumptions",
        "",
        "- See R2 open questions and R3 challenge items.",
        "",
        "## Core disagreements and chairman ruling",
        "",
        "- See `phases/r1_5_disputes.json` and `discussion/02_R1.5_裁决记录.md`.",
        "",
        "## Investment conditions and post-investment monitoring metrics",
        "",
        "### Conditions",
        "",
        _markdown_list(decision.get("conditions") if isinstance(decision.get("conditions"), list) else []),
        "",
        "### Monitoring Metrics",
        "",
        _markdown_list(decision.get("monitoring_metrics") if isinstance(decision.get("monitoring_metrics"), list) else []),
        "",
        "## Human Confirmation",
        "",
        f"- Status: `{(decision.get('human_confirmation') or {}).get('status')}`",
    ]
    return _write_text_artifact(package_dir, "decision/IC_DECISION_REPORT.md", "\n".join(lines))


def _write_r4_html(package_dir: Path, decision: dict[str, Any], markdown_path: str) -> str:
    markdown = (package_dir / markdown_path).read_text(encoding="utf-8", errors="replace")
    body = "\n".join(
        f"<p>{escape(line)}</p>" if line.strip() else ""
        for line in markdown.splitlines()
    )
    html = (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<title>IC Decision Report</title></head><body>"
        f"{body}</body></html>"
    )
    return _write_text_artifact(package_dir, "decision/IC_DECISION_REPORT.html", html)


def build_workflow_r4_finalize_dry_run(
    deal_id: str,
    *,
    overwrite: bool = False,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    plan = _r4_gate(deal_id, overwrite=overwrite, wiki_root=wiki_root)
    plan["wiki_root"] = wiki_root
    allowed = not plan["blocking_reasons"]
    payload: dict[str, Any] = {}
    if allowed:
        payload = _build_r4_decision_payload(plan)
    return deal_store.redact_public_payload({
        "schema_version": WORKFLOW_R4_FINALIZE_DRY_RUN_SCHEMA,
        "deal_id": plan["deal_id"],
        "workflow_action": "finalize-r4",
        "dry_run": True,
        "allowed": allowed,
        "would_write": allowed,
        "overwrite": bool(overwrite),
        "queued": False,
        "job_id": None,
        "blocking_reasons": plan["blocking_reasons"],
        "warnings": plan["warnings"],
        "counts": plan["counts"],
        "output_paths": {
            "json": "phases/r4_decision.json",
            "markdown": "decision/IC_DECISION_REPORT.md",
            "html": "decision/IC_DECISION_REPORT.html",
            "decision_payload": "decision/decision_payload.json",
        },
        "decision_preview": payload,
        "hermes_called": False,
        "report_written": False,
        "workflow_advanced": False,
    })


def finalize_workflow_r4(
    deal_id: str,
    *,
    overwrite: bool = False,
    created_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    plan = _r4_gate(deal_id, overwrite=overwrite, wiki_root=wiki_root)
    plan["wiki_root"] = wiki_root
    if plan["blocking_reasons"]:
        raise ValueError(f"R4 finalize blocked: {', '.join(plan['blocking_reasons'])}")
    package_dir = plan["package_dir"]
    decision = _build_r4_decision_payload(plan, created_by=created_by)
    json_path = "phases/r4_decision.json"
    deal_store.write_json(package_dir / json_path, decision)
    markdown_path = _write_r4_markdown(package_dir, decision)
    html_path = _write_r4_html(package_dir, decision, markdown_path)
    deal_store.write_json(package_dir / "decision" / "decision_payload.json", decision)
    workflow = _advance_workflow_phase(
        package_dir,
        phase="R4",
        workflow_status="r4_completed",
        artifacts=[json_path, markdown_path, html_path, "decision/decision_payload.json"],
        extra={
            "decision": decision.get("decision"),
            "final_score": decision.get("final_score"),
            "human_confirmation_status": "pending",
        },
    )
    _update_project_decision(
        package_dir,
        status="r4_completed",
        final_decision=str(decision.get("decision") or ""),
        final_score=_numeric(decision.get("final_score")),
    )
    audit_event = deal_store.append_audit_event(
        plan["deal_id"],
        {
            "event_type": "r4_decision_generated",
            "deal_id": plan["deal_id"],
            "decision": decision.get("decision"),
            "final_score": decision.get("final_score"),
            "weighted_agent_score": decision.get("weighted_agent_score"),
            "chairman_dimension_score": decision.get("chairman_dimension_score"),
            "json_path": json_path,
            "markdown_path": markdown_path,
            "html_path": html_path,
            "created_by": created_by,
        },
        wiki_root=wiki_root,
    )
    return deal_store.redact_public_payload({
        "schema_version": WORKFLOW_R4_FINALIZE_SCHEMA,
        "deal_id": plan["deal_id"],
        "workflow_action": "finalize-r4",
        "dry_run": False,
        "allowed": True,
        "overwrite": bool(overwrite),
        "queued": False,
        "job_id": None,
        "blocking_reasons": [],
        "warnings": plan["warnings"],
        "counts": plan["counts"],
        "output_paths": {
            "json": json_path,
            "markdown": markdown_path,
            "html": html_path,
            "decision_payload": "decision/decision_payload.json",
        },
        "decision": decision,
        "hermes_called": False,
        "report_written": True,
        "workflow_advanced": True,
        "workflow": workflow,
        "audit_event": audit_event,
    })


def _task_payload(
    *,
    package_dir: Path,
    deal_id: str,
    profile_id: str,
    round_name: str,
    workflow: dict[str, Any],
    receipt: dict[str, Any] | None,
) -> dict[str, Any]:
    project_meta = deal_store.read_json(package_dir / "project_meta.json", {}) or {}
    manifest = deal_store.read_json(package_dir / "manifest.json", {}) or {}
    evidence = manifest.get("evidence") if isinstance(manifest.get("evidence"), dict) else {}
    return {
        "schema_version": AGENT_TASK_SCHEMA,
        "deal_id": deal_id,
        "company_name": project_meta.get("company_name") or workflow.get("company_name"),
        "industry": project_meta.get("industry") or workflow.get("industry") or "",
        "stage": project_meta.get("stage") or workflow.get("stage") or "",
        "phase": "R1",
        "round_name": round_name,
        "agent_id": profile_id,
        "legacy_agent_id": LEGACY_BY_PROFILE.get(profile_id),
        "agent_label": _profile_label(profile_id),
        "deal_package_root": f"data/wiki/deals/{deal_id}",
        "workflow_policy_path": "agents/hermes/profiles/siq_ic_shared/ic_workflow_policy.json",
        "startup_receipt_path": "phases/startup_receipts.json",
        "startup_receipt_id": receipt.get("receipt_id") if isinstance(receipt, dict) else None,
        "input_artifacts": {
            "manifest": "manifest.json",
            "evidence_index": evidence.get("index_path") or "evidence/evidence_index.json",
            "evidence_items": evidence.get("items_path") or "evidence/evidence_items.ndjson",
            "evidence_quality": evidence.get("quality_path") or "evidence/evidence_quality_report.json",
            "workflow_state": "phases/workflow_state.json",
        },
        "output_contract": _output_contract(profile_id, round_name),
        "hard_rules": [
            "必须先读取 startup receipt",
            "必须区分 verified/assumed",
            "必须引用已知 evidence_id；历史文本引用只能作为兼容说明",
            "不得访问 data/wiki/companies，除非任务显式授权",
            "不得输出投资执行指令，只输出投委会建议",
            "API 服务层负责最终写文件、审计和阶段状态更新",
        ],
    }


def build_ic_agent_task_dry_run(
    deal_id: str,
    profile_id: str,
    *,
    round_name: str | None = "R1",
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    canonical_profile = _normalize_profile_id(profile_id)
    normalized_round = _normalize_round_name(round_name)
    workflow = deal_store.read_json(package_dir / "phases" / "workflow_state.json", {}) or {}
    receipt = _receipt_for(package_dir, normalized_deal_id, canonical_profile)
    preflight = deal_contracts.run_deal_preflight(normalized_deal_id, wiki_root=wiki_root)

    blocking_reasons: list[str] = []
    _, workflow_reasons = _workflow_phase_allows(workflow, canonical_profile, normalized_round)
    blocking_reasons.extend(workflow_reasons)
    blocking_reasons.extend(_preflight_blocks(
        preflight,
        warn_check_ids=R1_AGENT_BLOCKING_PREFLIGHT_WARN_IDS,
        current_agent_id=canonical_profile,
    ))
    if canonical_profile in _submitted_agents(workflow):
        blocking_reasons.append("agent_already_submitted")
    if receipt is None:
        blocking_reasons.append("startup_receipt_missing")

    payload = _task_payload(
        package_dir=package_dir,
        deal_id=normalized_deal_id,
        profile_id=canonical_profile,
        round_name=normalized_round,
        workflow=workflow,
        receipt=receipt,
    )
    return {
        "schema_version": AGENT_TASK_DRY_RUN_SCHEMA,
        "deal_id": normalized_deal_id,
        "agent_id": canonical_profile,
        "round_name": normalized_round,
        "allowed": not blocking_reasons,
        "blocking_reasons": blocking_reasons,
        "warnings": _preflight_warnings(preflight),
        "preflight_status": preflight.get("status"),
        "receipt": deal_store.redact_public_payload(receipt) if isinstance(receipt, dict) else None,
        "payload": deal_store.redact_public_payload(payload),
        "dry_run": True,
        "hermes_called": False,
        "report_written": False,
        "workflow_advanced": False,
    }


def build_r1_agent_readiness(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    """Return the R1 agent run matrix using one shared preflight pass."""

    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    round_name = "R1"
    workflow = deal_store.read_json(package_dir / "phases" / "workflow_state.json", {}) or {}
    preflight = deal_contracts.run_deal_preflight(normalized_deal_id, wiki_root=wiki_root)
    warnings = _preflight_warnings(preflight)
    receipts = _receipt_agents(package_dir)
    reports = _r1_reports(package_dir)
    submitted = _submitted_agents(workflow)
    profiles = {profile["id"]: profile for profile in ic_policy.list_ic_profiles(include_runtime=False)}

    agents: list[dict[str, Any]] = []
    for profile_id in ic_policy.R1_AGENT_SEQUENCE:
        profile = profiles.get(profile_id, {"id": profile_id, "label": profile_id, "role": profile_id})
        receipt = receipts.get(profile_id)
        report = reports.get(profile_id)
        _, workflow_reasons = _workflow_phase_allows(workflow, profile_id, round_name)
        preflight_blocks = _preflight_blocks(
            preflight,
            warn_check_ids=R1_AGENT_BLOCKING_PREFLIGHT_WARN_IDS,
            current_agent_id=profile_id,
        )
        blocking_reasons = [*workflow_reasons, *preflight_blocks]
        if receipt is None:
            blocking_reasons.append("startup_receipt_missing")
        allowed = not blocking_reasons
        agents.append({
            "agent_id": profile_id,
            "role": profile.get("role"),
            "label": profile.get("label") or profile_id,
            "r1_sequence_index": profile.get("r1_sequence_index"),
            "round_name": round_name,
            "allowed": allowed,
            "would_queue": allowed,
            "blocking_reasons": blocking_reasons,
            "warnings": warnings,
            "preflight_status": preflight.get("status"),
            "has_startup_receipt": receipt is not None,
            "startup_receipt_id": receipt.get("receipt_id") if isinstance(receipt, dict) else None,
            "has_report": report is not None,
            "submitted": profile_id in submitted,
            "dry_run": True,
            "hermes_called": False,
            "report_written": False,
            "workflow_advanced": False,
        })

    next_agent_id = next(
        (item["agent_id"] for item in agents if item.get("allowed") and not item.get("submitted")),
        None,
    )
    return deal_store.redact_public_payload({
        "schema_version": R1_AGENT_READINESS_SCHEMA,
        "deal_id": normalized_deal_id,
        "round_name": round_name,
        "workflow_action": "run-r1-agent",
        "dry_run": True,
        "current_phase": workflow.get("current_phase"),
        "workflow_status": workflow.get("status"),
        "preflight_status": preflight.get("status"),
        "next_agent_id": next_agent_id,
        "ready_count": sum(1 for item in agents if item.get("allowed")),
        "blocked_count": sum(1 for item in agents if not item.get("allowed")),
        "agents": agents,
        "hermes_called": False,
        "report_written": False,
        "workflow_advanced": False,
    })


def build_workflow_r1_agent_run_dry_run(
    deal_id: str,
    profile_id: str,
    *,
    round_name: str | None = "R1",
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    """Build the workflow-level R1 run contract without side effects."""

    task = build_ic_agent_task_dry_run(
        deal_id,
        profile_id,
        round_name=round_name,
        wiki_root=wiki_root,
    )
    return {
        "schema_version": WORKFLOW_R1_AGENT_RUN_DRY_RUN_SCHEMA,
        "deal_id": task["deal_id"],
        "agent_id": task["agent_id"],
        "round_name": task["round_name"],
        "workflow_action": "run-r1-agent",
        "dry_run": True,
        "queued": False,
        "job_id": None,
        "would_queue": bool(task.get("allowed")),
        "allowed": task.get("allowed"),
        "blocking_reasons": list(task.get("blocking_reasons") or []),
        "warnings": list(task.get("warnings") or []),
        "preflight_status": task.get("preflight_status"),
        "receipt": task.get("receipt"),
        "payload": task.get("payload"),
        "agent_task": task,
        "hermes_called": False,
        "report_written": False,
        "workflow_advanced": False,
    }


def build_workflow_r1_serial_run_dry_run(
    deal_id: str,
    *,
    round_name: str | None = "R1",
    max_agents: int | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    """Plan a strict serial R1 run without side effects."""

    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    normalized_round = _normalize_round_name(round_name)
    max_count = max(1, min(int(max_agents or R1_SERIAL_MAX_AGENTS), R1_SERIAL_MAX_AGENTS))
    workflow = deal_store.read_json(package_dir / "phases" / "workflow_state.json", {}) or {}
    preflight = deal_contracts.run_deal_preflight(normalized_deal_id, wiki_root=wiki_root)
    global_blocks = [
        *_workflow_global_blocks(workflow, normalized_round),
        *_preflight_blocks(preflight, warn_check_ids=R1_GLOBAL_BLOCKING_PREFLIGHT_WARN_IDS),
    ]
    warnings = _preflight_warnings(preflight)
    receipts = _receipt_agents(package_dir)
    submitted = _submitted_agents(workflow)
    virtual_submitted = set(submitted)
    profiles = {profile["id"]: profile for profile in ic_policy.list_ic_profiles(include_runtime=False)}

    planned: list[str] = []
    agents: list[dict[str, Any]] = []
    stop_reason: str | None = None
    for profile_id in ic_policy.R1_AGENT_SEQUENCE:
        profile = profiles.get(profile_id, {"id": profile_id, "label": profile_id, "role": profile_id})
        receipt = receipts.get(profile_id)
        index = ic_policy.R1_AGENT_SEQUENCE.index(profile_id)
        missing_previous = [agent_id for agent_id in ic_policy.R1_AGENT_SEQUENCE[:index] if agent_id not in virtual_submitted]
        blocking_reasons: list[str] = []
        action = "pending"
        would_run = False

        if profile_id in submitted:
            action = "skipped_submitted"
        else:
            if len(planned) >= max_count:
                action = "not_planned_max_agents"
                stop_reason = "max_agents_reached"
            else:
                blocking_reasons.extend(global_blocks)
                blocking_reasons.extend(_preflight_blocks(
                    preflight,
                    warn_check_ids=R1_AGENT_RECEIPT_PREFLIGHT_WARN_IDS,
                    current_agent_id=profile_id,
                ))
                if missing_previous:
                    blocking_reasons.append(f"r1_sequence_waiting_for:{','.join(missing_previous)}")
                if receipt is None:
                    blocking_reasons.append("startup_receipt_missing")
                if blocking_reasons:
                    action = "blocked"
                    stop_reason = blocking_reasons[0]
                else:
                    action = "would_run"
                    would_run = True
                    planned.append(profile_id)
                    virtual_submitted.add(profile_id)

        agents.append({
            "agent_id": profile_id,
            "role": profile.get("role"),
            "label": profile.get("label") or profile_id,
            "r1_sequence_index": profile.get("r1_sequence_index"),
            "round_name": normalized_round,
            "action": action,
            "would_run": would_run,
            "submitted": profile_id in submitted,
            "has_startup_receipt": receipt is not None,
            "startup_receipt_id": receipt.get("receipt_id") if isinstance(receipt, dict) else None,
            "blocking_reasons": blocking_reasons,
            "warnings": warnings,
            "dry_run": True,
            "hermes_called": False,
            "report_written": False,
            "workflow_advanced": False,
        })
        if action in {"blocked", "not_planned_max_agents"}:
            break

    all_submitted = all(agent_id in submitted for agent_id in ic_policy.R1_AGENT_SEQUENCE)
    top_blocking_reasons: list[str] = []
    if agents and agents[-1].get("action") == "blocked":
        top_blocking_reasons = list(agents[-1].get("blocking_reasons") or [])
    return deal_store.redact_public_payload({
        "schema_version": WORKFLOW_R1_SERIAL_RUN_DRY_RUN_SCHEMA,
        "deal_id": normalized_deal_id,
        "round_name": normalized_round,
        "workflow_action": "run-r1-serial",
        "dry_run": True,
        "allowed": bool(planned) or all_submitted,
        "would_run": bool(planned),
        "queued": False,
        "job_id": None,
        "current_phase": workflow.get("current_phase"),
        "workflow_status": workflow.get("status"),
        "preflight_status": preflight.get("status"),
        "planned_agent_ids": planned,
        "planned_count": len(planned),
        "submitted_agent_ids": [agent_id for agent_id in ic_policy.R1_AGENT_SEQUENCE if agent_id in submitted],
        "next_agent_id": planned[0] if planned else None,
        "stop_reason": stop_reason,
        "blocking_reasons": top_blocking_reasons,
        "agents": agents,
        "warnings": warnings,
        "hermes_called": False,
        "report_written": False,
        "workflow_advanced": False,
    })


async def run_workflow_r1_agent(
    deal_id: str,
    profile_id: str,
    *,
    round_name: str | None = "R1",
    created_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Run one R1 IC agent through Hermes and persist the report atomically after success."""

    task = build_ic_agent_task_dry_run(
        deal_id,
        profile_id,
        round_name=round_name,
        wiki_root=wiki_root,
    )
    if not task.get("allowed"):
        reasons = ", ".join(str(item) for item in task.get("blocking_reasons") or [])
        raise ValueError(f"R1 agent run blocked: {reasons or 'unknown'}")

    package_dir = _require_package_dir(task["deal_id"], wiki_root=wiki_root)
    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    prompt = _agent_prompt(payload)
    run_id = await hermes_client.create_run(
        prompt,
        [],
        profile=task["agent_id"],
        session_id=f"deal-{task['deal_id']}-{task['agent_id']}-{task['round_name']}",
    )
    output_text = await hermes_client.collect_run_result(
        run_id,
        profile=task["agent_id"],
        timeout=timeout,
    )

    parsed = _extract_json_object(output_text)
    try:
        parsed = _validate_r1_report_contract(
            package_dir=package_dir,
            task=task,
            parsed=parsed,
        )
    except ValueError as exc:
        deal_store.append_audit_event(
            task["deal_id"],
            {
                "event_type": "deal_r1_agent_run_rejected",
                "deal_id": task["deal_id"],
                "agent_id": task["agent_id"],
                "round_name": task["round_name"],
                "hermes_run_id": run_id,
                "reason": str(exc),
                "created_by": created_by,
            },
            wiki_root=wiki_root,
        )
        raise
    markdown_path = _write_markdown_report(
        package_dir,
        task=task,
        run_id=run_id,
        output_text=output_text,
    )
    report_entry = _build_report_entry(
        task=task,
        run_id=run_id,
        output_text=output_text,
        parsed=parsed,
        created_by=created_by,
    )
    report_entry["markdown_path"] = markdown_path
    report_entry["artifact_path"] = markdown_path
    json_path = _merge_r1_report(package_dir, task["agent_id"], report_entry)
    workflow = _advance_workflow_for_r1_report(package_dir, task["agent_id"])
    _touch_package_status(package_dir, str(workflow.get("status") or "r1_in_progress"))
    audit_event = deal_store.append_audit_event(
        task["deal_id"],
        {
            "event_type": "deal_r1_agent_run_completed",
            "deal_id": task["deal_id"],
            "agent_id": task["agent_id"],
            "round_name": task["round_name"],
            "hermes_run_id": run_id,
            "startup_receipt_id": payload.get("startup_receipt_id"),
            "markdown_path": markdown_path,
            "report_path": markdown_path,
            "json_path": json_path,
            "score": report_entry.get("score"),
            "recommendation": report_entry.get("recommendation"),
            "created_by": created_by,
        },
        wiki_root=wiki_root,
    )
    return deal_store.redact_public_payload({
        "schema_version": WORKFLOW_R1_AGENT_RUN_SCHEMA,
        "deal_id": task["deal_id"],
        "agent_id": task["agent_id"],
        "round_name": task["round_name"],
        "workflow_action": "run-r1-agent",
        "dry_run": False,
        "queued": False,
        "job_id": None,
        "allowed": True,
        "blocking_reasons": [],
        "warnings": list(task.get("warnings") or []),
        "preflight_status": task.get("preflight_status"),
        "receipt": task.get("receipt"),
        "payload": payload,
        "agent_task": task,
        "hermes_called": True,
        "hermes_run_id": run_id,
        "report_written": True,
        "workflow_advanced": True,
        "markdown_path": markdown_path,
        "json_path": json_path,
        "report": report_entry,
        "workflow": workflow,
        "audit_event": audit_event,
    })


async def run_workflow_r1_serial(
    deal_id: str,
    *,
    round_name: str | None = "R1",
    max_agents: int | None = None,
    created_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Run pending R1 agents strictly in policy order."""

    plan = build_workflow_r1_serial_run_dry_run(
        deal_id,
        round_name=round_name,
        max_agents=max_agents,
        wiki_root=wiki_root,
    )
    planned = list(plan.get("planned_agent_ids") or [])
    if not planned:
        if plan.get("allowed"):
            return {
                **plan,
                "schema_version": WORKFLOW_R1_SERIAL_RUN_SCHEMA,
                "dry_run": False,
                "would_run": False,
                "hermes_called": False,
                "report_written": False,
                "workflow_advanced": False,
                "executed_agent_ids": [],
                "agent_runs": [],
                "status": "no_op",
            }
        raise ValueError(f"R1 serial run blocked: {plan.get('stop_reason') or 'no runnable agents'}")

    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    agent_runs: list[dict[str, Any]] = []
    executed: list[str] = []
    try:
        for agent_id in planned:
            run = await run_workflow_r1_agent(
                normalized_deal_id,
                agent_id,
                round_name=round_name,
                created_by=created_by,
                wiki_root=wiki_root,
                timeout=timeout,
            )
            agent_runs.append(run)
            executed.append(agent_id)
    except Exception as exc:
        deal_store.append_audit_event(
            normalized_deal_id,
            {
                "event_type": "deal_r1_serial_run_failed",
                "deal_id": normalized_deal_id,
                "round_name": _normalize_round_name(round_name),
                "planned_agent_ids": planned,
                "executed_agent_ids": executed,
                "error": str(exc),
                "created_by": created_by,
            },
            wiki_root=wiki_root,
        )
        raise

    package_dir = _require_package_dir(normalized_deal_id, wiki_root=wiki_root)
    workflow = deal_store.read_json(package_dir / "phases" / "workflow_state.json", {}) or {}
    audit_event = deal_store.append_audit_event(
        normalized_deal_id,
        {
            "event_type": "deal_r1_serial_run_completed",
            "deal_id": normalized_deal_id,
            "round_name": _normalize_round_name(round_name),
            "planned_agent_ids": planned,
            "executed_agent_ids": executed,
            "created_by": created_by,
        },
        wiki_root=wiki_root,
    )
    return deal_store.redact_public_payload({
        "schema_version": WORKFLOW_R1_SERIAL_RUN_SCHEMA,
        "deal_id": normalized_deal_id,
        "round_name": _normalize_round_name(round_name),
        "workflow_action": "run-r1-serial",
        "dry_run": False,
        "allowed": True,
        "queued": False,
        "job_id": None,
        "planned_agent_ids": planned,
        "executed_agent_ids": executed,
        "planned_count": len(planned),
        "executed_count": len(executed),
        "agent_runs": agent_runs,
        "hermes_called": bool(executed),
        "report_written": bool(executed),
        "workflow_advanced": bool(executed),
        "workflow": workflow,
        "audit_event": audit_event,
    })
