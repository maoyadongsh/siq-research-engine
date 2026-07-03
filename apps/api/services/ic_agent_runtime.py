"""IC agent runtime contracts.

P1 starts with a dry-run task payload builder. It checks whether an agent would
be allowed to run and returns the structured payload that a later Hermes call
will receive. It does not call Hermes, write reports, or advance workflow state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from services import deal_contracts
from services import deal_store
from services import ic_policy


AGENT_TASK_SCHEMA = "siq_ic_agent_task_v1"
AGENT_TASK_DRY_RUN_SCHEMA = "siq_ic_agent_task_dry_run_v1"
WORKFLOW_R1_AGENT_RUN_DRY_RUN_SCHEMA = "siq_ic_workflow_r1_agent_run_dry_run_v1"
R1_AGENT_READINESS_SCHEMA = "siq_ic_r1_agent_readiness_v1"
SUPPORTED_ROUNDS = {"R1"}
REPORT_STEMS = {
    "siq_ic_strategist": "strategist",
    "siq_ic_sector_expert": "sector_expert",
    "siq_ic_finance_auditor": "finance_auditor",
    "siq_ic_legal_scanner": "legal_scanner",
    "siq_ic_risk_controller": "risk_controller",
    "siq_ic_chairman": "chairman",
}
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


def _output_contract(profile_id: str, round_name: str) -> dict[str, Any]:
    stem = REPORT_STEMS.get(profile_id, profile_id.removeprefix("siq_ic_"))
    return {
        "json_path": "phases/r1_reports.json",
        "json_key": profile_id,
        "markdown_path": f"discussion/01_R1_{stem}_report.md",
        "required_fields": ["score", "recommendation", "verified", "assumed", "open_questions"],
        "round_name": round_name,
    }


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
