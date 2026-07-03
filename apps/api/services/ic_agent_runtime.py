"""IC agent runtime contracts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from services import deal_contracts
from services import deal_store
from services import hermes_client
from services import ic_policy


AGENT_TASK_SCHEMA = "siq_ic_agent_task_v1"
AGENT_TASK_DRY_RUN_SCHEMA = "siq_ic_agent_task_dry_run_v1"
WORKFLOW_R1_AGENT_RUN_DRY_RUN_SCHEMA = "siq_ic_workflow_r1_agent_run_dry_run_v1"
WORKFLOW_R1_AGENT_RUN_SCHEMA = "siq_ic_workflow_r1_agent_run_v1"
WORKFLOW_R1_SERIAL_RUN_DRY_RUN_SCHEMA = "siq_ic_workflow_r1_serial_run_dry_run_v1"
WORKFLOW_R1_SERIAL_RUN_SCHEMA = "siq_ic_workflow_r1_serial_run_v1"
R1_AGENT_READINESS_SCHEMA = "siq_ic_r1_agent_readiness_v1"
R1_AGENT_REPORT_SCHEMA = "siq_ic_r1_agent_report_v1"
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


def _preflight_blocks(preflight: dict[str, Any]) -> list[str]:
    blocks: list[str] = []
    for check in preflight.get("checks", []):
        if not isinstance(check, dict):
            continue
        if check.get("status") == "fail":
            blocks.append(f"preflight_fail:{check.get('id')}")
    return blocks


def _preflight_warnings(preflight: dict[str, Any]) -> list[str]:
    return [
        f"preflight:{check.get('id')}:{check.get('status')}"
        for check in preflight.get("checks", [])
        if isinstance(check, dict) and check.get("status") in {"warn", "info"}
    ]


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
        "输出应先给可读 Markdown 报告；如可以，请在报告末尾附上一个 JSON 摘要，"
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
    blocking_reasons.extend(_preflight_blocks(preflight))
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
    preflight_blocks = _preflight_blocks(preflight)
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
    global_blocks = [*_workflow_global_blocks(workflow, normalized_round), *_preflight_blocks(preflight)]
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
