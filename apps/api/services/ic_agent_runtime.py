"""IC agent runtime contracts."""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

from services import (
    deal_contracts,
    deal_disputes,
    deal_reports,
    deal_store,
    hermes_client,
    ic_decision_report,
    ic_phase_orchestrator,
    ic_policy,
)
from services.ic_task_lease import (
    ICTaskAlreadyClaimedError,
    claim_ic_task,
    finish_ic_task,
    heartbeat_ic_task,
)

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
WORKFLOW_ADVANCE_NEXT_DRY_RUN_SCHEMA = "siq_ic_workflow_advance_next_dry_run_v1"
WORKFLOW_ADVANCE_NEXT_SCHEMA = "siq_ic_workflow_advance_next_v1"
R1_AGENT_READINESS_SCHEMA = "siq_ic_r1_agent_readiness_v1"
R1_AGENT_REPORT_SCHEMA = "siq_ic_r1_agent_report_v1"
R2_AGENT_REPORT_SCHEMA = "siq_ic_r2_agent_report_v1"
R3_REVIEW_SCHEMA = "siq_ic_r3_review_v1"
R3_AGENT_REVIEW_SCHEMA = "siq_ic_r3_agent_review_v1"
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
R1A_AGENT_IDS = ic_phase_orchestrator.R1A_AGENT_IDS
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
DEFAULT_R3_SKIP_REASON = "R2 已覆盖核心分歧，P0 留痕跳过。"
DEFAULT_IC_TASK_LEASE_SECONDS = 120
DEFAULT_IC_TASK_HEARTBEAT_SECONDS = 30
__all__ = ["ICTaskAlreadyClaimedError"]


class R1ReportContractError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("R1 Hermes output contract invalid: " + "; ".join(errors))


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
    result = _canonical_keyed_payload(agents)
    history = payload.get("by_agent_phase") if isinstance(payload, dict) and isinstance(payload.get("by_agent_phase"), dict) else {}
    for agent_id, phases in history.items():
        if isinstance(phases, dict) and isinstance(phases.get("R1"), dict):
            canonical = ic_policy.canonical_ic_profile_id(str(agent_id))
            result[canonical] = {**phases["R1"], "agent_id": canonical}
    return result


def _r1_reports(package_dir: Path) -> dict[str, dict[str, Any]]:
    return _canonical_keyed_payload(deal_store.read_json(package_dir / "phases" / "r1_reports.json", {}) or {})


def _evidence_identity(package_dir: Path) -> dict[str, Any]:
    snapshot = deal_store.read_json(package_dir / "evidence" / "evidence_snapshot.json", {}) or {}
    return {
        "source_ids": list(snapshot.get("source_ids") or []),
        "evidence_snapshot_hash": snapshot.get("snapshot_hash"),
        "active_sources": list(snapshot.get("active_sources") or []),
    }


MODEL_PHASE_AGENT_IDS: dict[str, tuple[str, ...]] = {
    "R0": (ic_phase_orchestrator.COORDINATOR_AGENT_ID,),
    "R1.5": (ic_phase_orchestrator.CHAIRMAN_AGENT_ID,),
    "R2": tuple(R2_AGENT_SEQUENCE),
    "R3": (*tuple(R2_AGENT_SEQUENCE), ic_phase_orchestrator.CHAIRMAN_AGENT_ID),
    "R4": (ic_phase_orchestrator.CHAIRMAN_AGENT_ID,),
}


def build_model_phase_receipt_readiness(
    deal_id: str,
    phase: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_phase = str(phase or "").strip().upper()
    if normalized_phase not in MODEL_PHASE_AGENT_IDS:
        raise ValueError("phase must be R0, R1.5, R2, R3, or R4")
    raw = deal_store.read_json(package_dir / "phases" / "startup_receipts.json", {}) or {}
    history = raw.get("by_agent_phase") if isinstance(raw, dict) and isinstance(raw.get("by_agent_phase"), dict) else {}
    latest = raw.get("agents") if isinstance(raw, dict) and isinstance(raw.get("agents"), dict) else {}
    current_snapshot = str(_evidence_identity(package_dir).get("evidence_snapshot_hash") or "")
    agents: list[dict[str, Any]] = []
    blocking_reasons: list[str] = []
    for agent_id in MODEL_PHASE_AGENT_IDS[normalized_phase]:
        agent_history = history.get(agent_id) if isinstance(history.get(agent_id), dict) else {}
        receipt = agent_history.get(normalized_phase) if isinstance(agent_history, dict) else None
        if not isinstance(receipt, dict):
            candidate = latest.get(agent_id)
            if isinstance(candidate, dict) and str(candidate.get("round_name") or "").upper() == normalized_phase:
                receipt = candidate
        reasons: list[str] = []
        if not isinstance(receipt, dict):
            reasons.append("startup_receipt_missing")
        else:
            reasons.extend(_startup_receipt_gate_blocks(receipt))
            receipt_snapshot = str(receipt.get("evidence_snapshot_hash") or "")
            if not current_snapshot or receipt_snapshot != current_snapshot:
                reasons.append("startup_receipt_snapshot_mismatch")
        blocking_reasons.extend(f"{normalized_phase}:{agent_id}:{reason}" for reason in reasons)
        agents.append(
            {
                "agent_id": agent_id,
                "round_name": normalized_phase,
                "receipt_id": receipt.get("receipt_id") if isinstance(receipt, dict) else None,
                "ready": not reasons,
                "blocking_reasons": reasons,
                "private_hits": int(receipt.get("private_hits") or 0) if isinstance(receipt, dict) else 0,
                "milvus_used": bool(receipt.get("milvus_used")) if isinstance(receipt, dict) else False,
                "evidence_snapshot_hash": receipt.get("evidence_snapshot_hash") if isinstance(receipt, dict) else None,
            }
        )
    return {
        "schema_version": "siq_ic_model_phase_receipt_readiness_v1",
        "deal_id": deal_store.validate_deal_id(deal_id),
        "phase": normalized_phase,
        "evidence_snapshot_hash": current_snapshot or None,
        "allowed": not blocking_reasons,
        "blocking_reasons": _dedupe_strings(blocking_reasons),
        "agents": agents,
        "ready_count": sum(1 for item in agents if item["ready"]),
        "required_count": len(agents),
    }


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

    submitted_set = _submitted_agents(workflow)
    if profile_id == ic_phase_orchestrator.RISK_AGENT_ID:
        missing_previous = [agent_id for agent_id in R1A_AGENT_IDS if agent_id not in submitted_set]
        if missing_previous:
            reasons.append(f"r1a_reports_waiting_for:{','.join(missing_previous)}")
    elif profile_id == ic_phase_orchestrator.CHAIRMAN_AGENT_ID:
        dependencies = (*R1A_AGENT_IDS, ic_phase_orchestrator.RISK_AGENT_ID)
        missing_previous = [agent_id for agent_id in dependencies if agent_id not in submitted_set]
        if missing_previous:
            reasons.append(f"r1b_reports_waiting_for:{','.join(missing_previous)}")
    return not reasons, reasons


def _receipt_for(package_dir: Path, deal_id: str, profile_id: str) -> dict[str, Any] | None:
    del deal_id
    return _receipt_agents(package_dir).get(profile_id)


def _startup_receipt_gate_blocks(receipt: dict[str, Any] | None) -> list[str]:
    """Allow formal execution only from a current, private-KB-backed receipt.

    Legacy receipts remain readable for audit and migration, but they do not
    prove that the role's private Milvus collection was queried.  Treating a
    missing gate as executable would let R1 bypass the v2 knowledge contract.
    """

    if not isinstance(receipt, dict):
        return []
    gate = receipt.get("gate")
    if not isinstance(gate, dict):
        return ["startup_receipt_gate_blocked:receipt_gate_missing"]
    reasons = _dedupe_strings(_as_list(gate.get("blocking_reasons")))
    if gate.get("allowed_to_speak") is not True:
        return [f"startup_receipt_gate_blocked:{reason}" for reason in (reasons or ["not_ready"])]
    if int(receipt.get("private_hits") or 0) <= 0:
        return ["startup_receipt_gate_blocked:private_kb_empty"]
    if not receipt.get("milvus_used"):
        return ["startup_receipt_gate_blocked:milvus_not_used"]
    if not _as_list(receipt.get("background_knowledge_refs")):
        return ["startup_receipt_gate_blocked:background_knowledge_refs_missing"]
    return []


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
        "required_fields": ["score", "recommendation", "verified", "assumed", "open_questions", "evidence_ids"],
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
        "source_ids": payload.get("source_ids") or [],
        "evidence_snapshot_hash": payload.get("evidence_snapshot_hash"),
        "capability_restrictions": payload.get("capability_restrictions") or {},
        "research_identities": payload.get("research_identities") or [],
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
        value = parsed.get(field)
        if field not in parsed or value in (None, ""):
            errors.append(f"{field}_missing")
        elif not isinstance(value, list):
            errors.append(f"{field}_not_list")

    parsed_agent_id = str(parsed.get("agent_id") or "").strip()
    if parsed_agent_id:
        parsed_agent_id = ic_policy.canonical_ic_profile_id(parsed_agent_id)
        if parsed_agent_id != task.get("agent_id"):
            errors.append("agent_id_mismatch")

    parsed_round = str(parsed.get("round_name") or parsed.get("phase") or "").strip().upper()
    if parsed_round and parsed_round != "R1":
        errors.append("round_name_mismatch")

    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    expected_receipt_id = str(payload.get("startup_receipt_id") or "").strip()
    parsed_receipt_id = str(parsed.get("startup_receipt_id") or "").strip()
    if parsed_receipt_id and expected_receipt_id and parsed_receipt_id != expected_receipt_id:
        errors.append("startup_receipt_id_mismatch")

    known_ids = _known_evidence_ids(package_dir)
    evidence_ids = _report_evidence_ids(parsed)
    if not evidence_ids:
        errors.append("evidence_ids_missing")
    unknown_ids = sorted(set(evidence_ids) - known_ids)
    if unknown_ids:
        errors.append("evidence_ids_unknown:" + ",".join(unknown_ids))
    receipt = _receipt_for(package_dir, str(task.get("deal_id") or ""), str(task.get("agent_id") or ""))
    receipt_ids = _extract_evidence_ids(receipt.get("evidence_hits")) if isinstance(receipt, dict) else set()
    if receipt_ids and evidence_ids and not receipt_ids.intersection(evidence_ids):
        errors.append("evidence_ids_not_in_startup_receipt")

    if errors:
        raise R1ReportContractError(errors)

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

    def merge_report(current: Any) -> dict[str, Any]:
        reports = current or {}
        if not isinstance(reports, dict) or isinstance(reports.get("reports"), dict):
            reports = dict(reports.get("reports") or reports) if isinstance(reports, dict) else {}
        reports[profile_id] = report_entry
        return reports

    deal_store.update_json(path, merge_report, default={})
    return relative


def _advance_workflow_for_r1_report(package_dir: Path, profile_id: str) -> dict[str, Any]:
    workflow_path = package_dir / "phases" / "workflow_state.json"
    now = deal_store.utc_now_iso()

    def advance(current: Any) -> dict[str, Any]:
        workflow = current if isinstance(current, dict) else {}
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
        submitted = [
            ic_policy.canonical_ic_profile_id(str(item))
            for item in submitted
            if str(item or "").strip()
        ]
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
        return workflow

    return deal_store.update_json(workflow_path, advance, default={})


def _touch_package_status(package_dir: Path, status: str) -> None:
    now = deal_store.utc_now_iso()
    for relative in ("manifest.json", "project_meta.json"):
        path = package_dir / relative

        def touch(current: Any, *, document: str = relative) -> Any:
            if not isinstance(current, dict):
                return current
            current["updated_at"] = now
            if document == "project_meta.json":
                current["status"] = status
            return current

        deal_store.update_json(path, touch, default={})


def _update_project_decision(
    package_dir: Path,
    *,
    status: str,
    final_decision: str | None = None,
    final_score: float | None = None,
) -> None:
    path = package_dir / "project_meta.json"
    now = deal_store.utc_now_iso()

    def update_decision(current: Any) -> Any:
        if not isinstance(current, dict):
            return current
        current["updated_at"] = now
        current["status"] = status
        if final_decision is not None:
            current["final_decision"] = final_decision
        if final_score is not None:
            current["final_score"] = final_score
        return current

    deal_store.update_json(path, update_decision, default={})


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
    now = deal_store.utc_now_iso()

    def advance(current: Any) -> dict[str, Any]:
        workflow = current if isinstance(current, dict) else {}
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
        return workflow

    return deal_store.update_json(workflow_path, advance, default={})


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
        "evidence_identity": _evidence_identity(package_dir),
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
            "source_ids": plan.get("evidence_identity", {}).get("source_ids") or [],
            "evidence_snapshot_hash": plan.get("evidence_identity", {}).get("evidence_snapshot_hash"),
            "active_sources": plan.get("evidence_identity", {}).get("active_sources") or [],
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
        "evidence_identity": _evidence_identity(package_dir),
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
            "source_ids": plan.get("evidence_identity", {}).get("source_ids") or [],
            "evidence_snapshot_hash": plan.get("evidence_identity", {}).get("evidence_snapshot_hash"),
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
            "source_ids": plan.get("evidence_identity", {}).get("source_ids") or [],
            "evidence_snapshot_hash": plan.get("evidence_identity", {}).get("evidence_snapshot_hash"),
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
        "source_ids": plan.get("evidence_identity", {}).get("source_ids") or [],
        "evidence_snapshot_hash": plan.get("evidence_identity", {}).get("evidence_snapshot_hash"),
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
        "evidence_identity": _evidence_identity(package_dir),
        "blocking_reasons": _dedupe_strings(blocks),
        "warnings": _dedupe_strings(warnings),
        "counts": {
            "r2_reports": len(r2_reports),
            "required_r2_reports": len(R2_AGENT_SEQUENCE),
            "r3_reports": int((r3_summary.get("counts") or {}).get("reports") or 0) if isinstance(r3_summary.get("counts"), dict) else 0,
        },
    }


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
        payload = ic_decision_report.build_r4_decision_payload(plan)
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
            "markdown": ic_decision_report.R4_MARKDOWN_PATH,
            "html": ic_decision_report.R4_HTML_PATH,
            "decision_payload": ic_decision_report.R4_PAYLOAD_PATH,
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
    decision = ic_decision_report.build_r4_decision_payload(plan, created_by=created_by)
    json_path = "phases/r4_decision.json"
    deal_store.write_json(package_dir / json_path, decision)
    artifact_paths = ic_decision_report.write_r4_decision_artifacts(package_dir, decision)
    markdown_path = artifact_paths["markdown"]
    html_path = artifact_paths["html"]
    decision_payload_path = artifact_paths["decision_payload"]
    workflow = _advance_workflow_phase(
        package_dir,
        phase="R4",
        workflow_status="r4_completed",
        artifacts=[json_path, markdown_path, html_path, decision_payload_path],
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
            "decision_payload": decision_payload_path,
        },
        "decision": decision,
        "hermes_called": False,
        "report_written": True,
        "workflow_advanced": True,
        "workflow": workflow,
        "audit_event": audit_event,
    })


def _require_phase_model_enabled(env_name: str) -> None:
    raw = os.getenv(env_name)
    if raw is not None and raw.strip().lower() not in {"1", "true", "yes", "on"}:
        raise ValueError(f"Hermes model phase is disabled by {env_name}")


async def run_workflow_r0_model(
    deal_id: str,
    *,
    created_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    _require_phase_model_enabled("SIQ_IC_R0_MODEL_ENABLED")
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    receipt_readiness = build_model_phase_receipt_readiness(deal_id, "R0", wiki_root=wiki_root)
    if not receipt_readiness["allowed"]:
        raise ValueError("R0 model run blocked: " + ", ".join(receipt_readiness["blocking_reasons"]))
    return deal_store.redact_public_payload(
        await ic_phase_orchestrator.run_r0_model(
            package_dir,
            created_by=created_by,
            timeout=timeout,
        )
    )


async def run_workflow_r1_5_model(
    deal_id: str,
    *,
    created_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
    timeout: float | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    _require_phase_model_enabled("SIQ_IC_R15_MODEL_ENABLED")
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    receipt_readiness = build_model_phase_receipt_readiness(deal_id, "R1.5", wiki_root=wiki_root)
    if not receipt_readiness["allowed"]:
        raise ValueError("R1.5 model run blocked: " + ", ".join(receipt_readiness["blocking_reasons"]))
    return deal_store.redact_public_payload(await ic_phase_orchestrator.run_r15_model(
        package_dir,
        created_by=created_by,
        timeout=timeout,
        overwrite=overwrite,
    ))


async def run_workflow_r2_async(
    deal_id: str,
    *,
    mode: str = "model",
    created_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    normalized_mode = str(mode or "model").strip().lower()
    if normalized_mode == "deterministic_fallback":
        fallback = run_workflow_r2(deal_id, created_by=created_by, wiki_root=wiki_root)
        fallback["generation_mode"] = "deterministic_fallback"
        fallback["fallback"] = True
        fallback["hermes_called"] = False
        return fallback
    if normalized_mode != "model":
        raise ValueError("R2 mode must be model or deterministic_fallback")
    _require_phase_model_enabled("SIQ_IC_R2_MODEL_ENABLED")
    receipt_readiness = build_model_phase_receipt_readiness(deal_id, "R2", wiki_root=wiki_root)
    if not receipt_readiness["allowed"]:
        raise ValueError("R2 model run blocked: " + ", ".join(receipt_readiness["blocking_reasons"]))
    plan = _r2_gate(deal_id, wiki_root=wiki_root)
    if plan["blocking_reasons"]:
        raise ValueError(f"R2 model run blocked: {', '.join(plan['blocking_reasons'])}")
    return deal_store.redact_public_payload(await ic_phase_orchestrator.run_r2_model(
        plan["package_dir"],
        created_by=created_by,
        timeout=timeout,
    ))


async def run_workflow_r3_async(
    deal_id: str,
    *,
    mode: str = "model",
    skip: bool = False,
    skip_reason: str | None = None,
    created_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    normalized_mode = str(mode or "model").strip().lower()
    if normalized_mode == "deterministic_fallback":
        fallback = run_workflow_r3(
            deal_id,
            skip=skip,
            skip_reason=skip_reason,
            created_by=created_by,
            wiki_root=wiki_root,
        )
        fallback["generation_mode"] = "deterministic_fallback"
        fallback["fallback"] = True
        fallback["hermes_called"] = False
        return fallback
    if normalized_mode != "model":
        raise ValueError("R3 mode must be model or deterministic_fallback")
    if skip:
        raise ValueError("R3 model mode uses the dynamic planner; use deterministic_fallback for an explicit manual skip")
    _require_phase_model_enabled("SIQ_IC_R3_MODEL_ENABLED")
    receipt_readiness = build_model_phase_receipt_readiness(deal_id, "R3", wiki_root=wiki_root)
    if not receipt_readiness["allowed"]:
        raise ValueError("R3 model run blocked: " + ", ".join(receipt_readiness["blocking_reasons"]))
    plan = _r3_gate(deal_id, skip=False, wiki_root=wiki_root)
    if plan["blocking_reasons"]:
        raise ValueError(f"R3 model run blocked: {', '.join(plan['blocking_reasons'])}")
    return deal_store.redact_public_payload(await ic_phase_orchestrator.run_r3_model(
        plan["package_dir"],
        created_by=created_by,
        timeout=timeout,
        allow_skip=True,
    ))


async def finalize_workflow_r4_async(
    deal_id: str,
    *,
    mode: str = "model",
    overwrite: bool = False,
    created_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    normalized_mode = str(mode or "model").strip().lower()
    if normalized_mode == "deterministic_fallback":
        fallback = finalize_workflow_r4(
            deal_id,
            overwrite=overwrite,
            created_by=created_by,
            wiki_root=wiki_root,
        )
        fallback["generation_mode"] = "deterministic_fallback"
        fallback["fallback"] = True
        fallback["hermes_called"] = False
        return fallback
    if normalized_mode != "model":
        raise ValueError("R4 mode must be model or deterministic_fallback")
    _require_phase_model_enabled("SIQ_IC_R4_MODEL_ENABLED")
    receipt_readiness = build_model_phase_receipt_readiness(deal_id, "R4", wiki_root=wiki_root)
    if not receipt_readiness["allowed"]:
        raise ValueError("R4 model finalize blocked: " + ", ".join(receipt_readiness["blocking_reasons"]))
    plan = _r4_gate(deal_id, overwrite=overwrite, wiki_root=wiki_root)
    if plan["blocking_reasons"]:
        raise ValueError(f"R4 model finalize blocked: {', '.join(plan['blocking_reasons'])}")
    return deal_store.redact_public_payload(await ic_phase_orchestrator.run_r4_model(
        plan["package_dir"],
        created_by=created_by,
        timeout=timeout,
        overwrite=overwrite,
    ))


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
        "source_ids": receipt.get("source_ids", []) if isinstance(receipt, dict) else [],
        "evidence_snapshot_hash": receipt.get("evidence_snapshot_hash") if isinstance(receipt, dict) else None,
        "capability_restrictions": receipt.get("capability_restrictions", {}) if isinstance(receipt, dict) else {},
        "research_identities": receipt.get("research_identities", []) if isinstance(receipt, dict) else [],
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
            "所有一级市场引用必须匹配 payload 的 source_ids 和 evidence_snapshot_hash",
            "financial_facts 受限时不得生成 verified 数字 claim，只能标记 assumed/contested/insufficient_evidence",
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
    else:
        blocking_reasons.extend(_startup_receipt_gate_blocks(receipt))

    payload = _task_payload(
        package_dir=package_dir,
        deal_id=normalized_deal_id,
        profile_id=canonical_profile,
        round_name=normalized_round,
        workflow=workflow,
        receipt=receipt,
    )
    phase_task: dict[str, Any] | None = None
    handoff: dict[str, Any] | None = None
    if (
        not blocking_reasons
        and isinstance(receipt, dict)
        and str(receipt.get("schema_version") or "").endswith("_v2")
        and receipt.get("evidence_snapshot_hash")
    ):
        phase_task, handoff = ic_phase_orchestrator.build_r1_task_envelope(
            package_dir,
            agent_id=canonical_profile,
            receipt=receipt,
            persist=False,
        )
        payload["phase_task_envelope"] = phase_task
        payload["agent_handoff"] = handoff
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
        "phase_task_envelope": deal_store.redact_public_payload(phase_task),
        "agent_handoff": deal_store.redact_public_payload(handoff),
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
        else:
            blocking_reasons.extend(_startup_receipt_gate_blocks(receipt))
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
        if profile_id in R1A_AGENT_IDS:
            dependencies: tuple[str, ...] = ()
        elif profile_id == ic_phase_orchestrator.RISK_AGENT_ID:
            dependencies = tuple(R1A_AGENT_IDS)
        else:
            dependencies = (*R1A_AGENT_IDS, ic_phase_orchestrator.RISK_AGENT_ID)
        missing_previous = [agent_id for agent_id in dependencies if agent_id not in virtual_submitted]
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
                    blocking_reasons.append(f"r1_hybrid_dag_waiting_for:{','.join(missing_previous)}")
                if receipt is None:
                    blocking_reasons.append("startup_receipt_missing")
                else:
                    blocking_reasons.extend(_startup_receipt_gate_blocks(receipt))
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


async def _run_workflow_r1_agent_without_claim(
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
    receipt = _receipt_for(package_dir, task["deal_id"], task["agent_id"])
    if not (
        isinstance(receipt, dict)
        and str(receipt.get("schema_version") or "").endswith("_v2")
        and receipt.get("evidence_snapshot_hash")
    ):
        raise ValueError("R1 formal model run requires a current startup receipt v2")
    model_result = await ic_phase_orchestrator.run_r1_model_task(
        package_dir,
        agent_id=task["agent_id"],
        receipt=receipt,
        created_by=created_by,
        timeout=timeout,
    )
    phase_task = model_result["task"]
    handoff = model_result["handoff"]
    execution = model_result["execution"]
    run_id = str(execution.get("hermes_run_id") or "")
    payload["phase_task_envelope"] = phase_task
    payload["agent_handoff"] = handoff
    payload["handoff_payload"] = ic_phase_orchestrator.read_handoff_payload(
        package_dir,
        handoff.get("handoff_id"),
    )
    task["payload"] = payload
    if model_result["stale_on_completion"]:
        audit_event = deal_store.append_audit_event(
            task["deal_id"],
            {
                "event_type": "deal_r1_agent_run_stale_on_completion",
                "deal_id": task["deal_id"],
                "agent_id": task["agent_id"],
                "hermes_run_id": run_id,
                "expected_evidence_snapshot_hash": phase_task.get("evidence_snapshot_hash"),
                "current_evidence_snapshot_hash": phase_task.get("current_evidence_snapshot_hash"),
                "task_id": phase_task.get("task_id"),
                "input_digest": phase_task.get("input_digest"),
                "report_written": False,
                "workflow_advanced": False,
                "created_by": created_by,
            },
            wiki_root=wiki_root,
        )
        return deal_store.redact_public_payload({
            "schema_version": WORKFLOW_R1_AGENT_RUN_SCHEMA,
            "deal_id": task["deal_id"],
            "agent_id": task["agent_id"],
            "round_name": task["round_name"],
            "status": "stale_on_completion",
            "hermes_called": True,
            "hermes_run_id": run_id,
            "phase_task_envelope": phase_task,
            "report_written": False,
            "workflow_advanced": False,
            "audit_event": audit_event,
        })
    report_entry = dict(model_result["report"])
    output_contract = payload.get("output_contract") if isinstance(payload.get("output_contract"), dict) else {}
    markdown_path = str(
        output_contract.get("markdown_path")
        or _output_contract(task["agent_id"], task["round_name"])["markdown_path"]
    )
    markdown_file = package_dir / markdown_path
    markdown_file.parent.mkdir(parents=True, exist_ok=True)
    markdown_file.write_text(str(model_result["markdown"]), encoding="utf-8")
    report_entry["markdown_path"] = markdown_path
    report_entry["artifact_path"] = markdown_path
    json_path = _merge_r1_report(package_dir, task["agent_id"], report_entry)
    workflow = _advance_workflow_for_r1_report(package_dir, task["agent_id"])
    persisted_handoffs = ic_phase_orchestrator.persist_available_r1_handoffs(
        package_dir,
        created_by=created_by,
    )
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
        "phase_task_envelope": phase_task,
        "agent_handoff": handoff,
        "persisted_handoffs": persisted_handoffs,
        "workflow": workflow,
        "audit_event": audit_event,
    })


def _positive_int_env(name: str, default: int) -> int:
    try:
        return max(1, int(str(os.getenv(name, default)).strip()))
    except (TypeError, ValueError):
        return default


def _ic_task_claim_public(claim: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(claim, dict):
        return {}
    return {
        key: claim.get(key)
        for key in (
            "task_key",
            "status",
            "attempt",
            "claimed_at",
            "heartbeat_at",
            "lease_expires_at",
            "finished_at",
            "failure_reason",
            "recovery_reason",
        )
    }


async def _finish_ic_task_off_thread(
    store_path: Path,
    *,
    task_key: str,
    owner: str,
    now: str,
    status: str,
    failure_reason: str | None = None,
) -> dict[str, Any] | None:
    return await asyncio.to_thread(
        finish_ic_task,
        store_path,
        task_key=task_key,
        owner=owner,
        now=now,
        status=status,
        failure_reason=failure_reason,
    )


async def run_workflow_r1_agent(
    deal_id: str,
    profile_id: str,
    *,
    round_name: str | None = "R1",
    created_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Atomically claim and run one R1 IC agent through Hermes."""

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
    store_path = package_dir / "phases" / "ic_task_leases.json"
    task_key = f"{task['deal_id']}:{task['round_name']}:{task['agent_id']}"
    owner = f"ic-worker-{os.getpid()}-{uuid.uuid4().hex[:12]}"
    lease_seconds = _positive_int_env("SIQ_IC_TASK_LEASE_SECONDS", DEFAULT_IC_TASK_LEASE_SECONDS)
    heartbeat_seconds = min(
        _positive_int_env("SIQ_IC_TASK_HEARTBEAT_SECONDS", DEFAULT_IC_TASK_HEARTBEAT_SECONDS),
        max(1, lease_seconds // 3),
    )
    await asyncio.to_thread(
        claim_ic_task,
        store_path,
        task_key=task_key,
        owner=owner,
        now=deal_store.utc_now_iso(),
        lease_seconds=lease_seconds,
    )
    stop_heartbeat = asyncio.Event()
    lease_lost = asyncio.Event()

    async def heartbeat_loop() -> None:
        while True:
            try:
                await asyncio.wait_for(stop_heartbeat.wait(), timeout=heartbeat_seconds)
                return
            except TimeoutError:
                renewed = await asyncio.to_thread(
                    heartbeat_ic_task,
                    store_path,
                    task_key=task_key,
                    owner=owner,
                    now=deal_store.utc_now_iso(),
                    lease_seconds=lease_seconds,
                )
                if renewed is None:
                    lease_lost.set()
                    return
            except Exception:
                lease_lost.set()
                return

    heartbeat_task = asyncio.create_task(heartbeat_loop())
    try:
        result = await _run_workflow_r1_agent_without_claim(
            deal_id,
            profile_id,
            round_name=round_name,
            created_by=created_by,
            wiki_root=wiki_root,
            timeout=timeout,
        )
        if lease_lost.is_set():
            raise RuntimeError("IC task lease ownership was lost before completion")
    except BaseException as exc:
        if isinstance(exc, asyncio.CancelledError):
            terminal_status = "cancelled"
        elif isinstance(exc, hermes_client.RunTerminalError) and exc.result.status == "timed_out":
            terminal_status = "timed_out"
        else:
            terminal_status = "failed"
        await _finish_ic_task_off_thread(
            store_path,
            task_key=task_key,
            owner=owner,
            now=deal_store.utc_now_iso(),
            status=terminal_status,
            failure_reason=type(exc).__name__,
        )
        raise
    else:
        terminal_status = "stale_on_completion" if result.get("status") == "stale_on_completion" else "succeeded"
        finished = await _finish_ic_task_off_thread(
            store_path,
            task_key=task_key,
            owner=owner,
            now=deal_store.utc_now_iso(),
            status=terminal_status,
        )
        if finished is None:
            raise RuntimeError("IC task completion rejected because lease ownership changed")
        result["task_claim"] = _ic_task_claim_public(finished)
        return result
    finally:
        stop_heartbeat.set()
        await heartbeat_task


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


def build_workflow_advance_next_dry_run(
    deal_id: str,
    *,
    allow_hermes: bool = False,
    max_agents: int | None = 1,
    r3_skip: bool = False,
    r3_skip_reason: str | None = None,
    r4_overwrite: bool = False,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    """Plan the next safe workflow step without side effects."""

    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    workflow = deal_store.read_json(package_dir / "phases" / "workflow_state.json", {}) or {}
    blocking_reasons: list[str] = []
    selected_action = "no-op"
    action_dry_run: dict[str, Any] = {}
    requires_hermes = False

    r0_readiness = deal_store.read_json(package_dir / "phases" / "r0_readiness.json", {}) or {}
    current_snapshot = str(_evidence_identity(package_dir).get("evidence_snapshot_hash") or "")
    r0_snapshot = str(r0_readiness.get("evidence_snapshot_hash") or "") if isinstance(r0_readiness, dict) else ""
    if not r0_readiness or r0_snapshot != current_snapshot:
        selected_action = "run-r0-coordinator"
        receipt_readiness = build_model_phase_receipt_readiness(
            normalized_deal_id,
            "R0",
            wiki_root=wiki_root,
        )
        action_dry_run = {
            "schema_version": "siq_ic_workflow_r0_model_dry_run_v1",
            "deal_id": normalized_deal_id,
            "phase": "R0",
            "allowed": receipt_readiness["allowed"],
            "receipt_readiness": receipt_readiness,
            "existing_readiness_stale": bool(r0_readiness),
        }
        blocking_reasons.extend(receipt_readiness["blocking_reasons"])
        requires_hermes = True
        if not allow_hermes:
            blocking_reasons.append("r0_coordinator_requires_allow_hermes")
    elif str(r0_readiness.get("readiness") or "") != "ready":
        selected_action = "run-r0-coordinator"
        action_dry_run = {
            "schema_version": "siq_ic_workflow_r0_model_dry_run_v1",
            "deal_id": normalized_deal_id,
            "phase": "R0",
            "allowed": False,
            "blocking_reasons": ["r0_readiness_requires_new_evidence"],
            "readiness": r0_readiness,
        }
        blocking_reasons.append("r0_readiness_requires_new_evidence")
        requires_hermes = True

    r1_reports = _r1_reports(package_dir)
    r1_reports_complete = all(agent_id in r1_reports for agent_id in ic_policy.R1_AGENT_SEQUENCE)
    if selected_action == "no-op" and not r1_reports_complete:
        r1_plan = build_workflow_r1_serial_run_dry_run(
            normalized_deal_id,
            max_agents=max_agents,
            wiki_root=wiki_root,
        )
        if r1_plan.get("would_run"):
            selected_action = "run-r1-serial"
            action_dry_run = r1_plan
            requires_hermes = True
            if not allow_hermes:
                blocking_reasons.append("r1_serial_requires_allow_hermes")
        elif not r1_plan.get("allowed") and r1_plan.get("blocking_reasons"):
            selected_action = "run-r1-serial"
            action_dry_run = r1_plan
            requires_hermes = True
            blocking_reasons.extend(str(item) for item in r1_plan.get("blocking_reasons") or [])
    if selected_action == "no-op":
        disputes_path = package_dir / deal_disputes.DISPUTES_JSON_PATH
        disputes_summary = deal_disputes.summarize_deal_disputes(normalized_deal_id, wiki_root=wiki_root)
        dispute_counts = disputes_summary.get("counts") if isinstance(disputes_summary.get("counts"), dict) else {}
        unresolved = int(dispute_counts.get("unresolved") or 0)
        if not disputes_path.is_file() or str(disputes_summary.get("status") or "") == "missing":
            selected_action = "identify-disputes"
            action_dry_run = deal_disputes.identify_deal_disputes(
                normalized_deal_id,
                dry_run=True,
                preserve_rulings=True,
                wiki_root=wiki_root,
            )
        elif unresolved > 0:
            selected_action = "run-r1-5-chairman"
            chairman_task = deal_disputes.build_chairman_ruling_task(
                normalized_deal_id,
                only_unresolved=True,
                wiki_root=wiki_root,
            )
            action_dry_run = {
                "schema_version": "siq_ic_workflow_r1_5_model_dry_run_v1",
                "deal_id": normalized_deal_id,
                "phase": "R1.5",
                "allowed": bool(chairman_task.get("disputes")),
                "chairman_task": chairman_task,
            }
            receipt_readiness = build_model_phase_receipt_readiness(
                normalized_deal_id,
                "R1.5",
                wiki_root=wiki_root,
            )
            action_dry_run["receipt_readiness"] = receipt_readiness
            blocking_reasons.extend(receipt_readiness["blocking_reasons"])
            requires_hermes = True
            if not allow_hermes:
                blocking_reasons.append("r1_5_chairman_requires_allow_hermes")
        elif not (package_dir / "phases" / "r2_reports.json").is_file():
            selected_action = "run-r2"
            requires_hermes = True
            if not allow_hermes:
                blocking_reasons.append("r2_model_requires_allow_hermes")
            action_dry_run = build_workflow_r2_run_dry_run(normalized_deal_id, wiki_root=wiki_root)
            receipt_readiness = build_model_phase_receipt_readiness(
                normalized_deal_id,
                "R2",
                wiki_root=wiki_root,
            )
            action_dry_run["receipt_readiness"] = receipt_readiness
            blocking_reasons.extend(receipt_readiness["blocking_reasons"])
            blocking_reasons.extend(str(item) for item in action_dry_run.get("blocking_reasons") or [])
        elif not (package_dir / "phases" / "r3_reports.json").is_file():
            selected_action = "run-r3"
            requires_hermes = True
            if not allow_hermes:
                blocking_reasons.append("r3_model_requires_allow_hermes")
            action_dry_run = build_workflow_r3_run_dry_run(
                normalized_deal_id,
                skip=r3_skip,
                skip_reason=r3_skip_reason,
                wiki_root=wiki_root,
            )
            receipt_readiness = build_model_phase_receipt_readiness(
                normalized_deal_id,
                "R3",
                wiki_root=wiki_root,
            )
            action_dry_run["receipt_readiness"] = receipt_readiness
            blocking_reasons.extend(receipt_readiness["blocking_reasons"])
            blocking_reasons.extend(str(item) for item in action_dry_run.get("blocking_reasons") or [])
        elif r4_overwrite or not (package_dir / "phases" / "r4_decision.json").is_file():
            selected_action = "finalize-r4"
            requires_hermes = True
            if not allow_hermes:
                blocking_reasons.append("r4_model_requires_allow_hermes")
            action_dry_run = build_workflow_r4_finalize_dry_run(
                normalized_deal_id,
                overwrite=r4_overwrite,
                wiki_root=wiki_root,
            )
            receipt_readiness = build_model_phase_receipt_readiness(
                normalized_deal_id,
                "R4",
                wiki_root=wiki_root,
            )
            action_dry_run["receipt_readiness"] = receipt_readiness
            blocking_reasons.extend(receipt_readiness["blocking_reasons"])
            blocking_reasons.extend(str(item) for item in action_dry_run.get("blocking_reasons") or [])

    if action_dry_run and action_dry_run.get("allowed") is False and not blocking_reasons:
        blocking_reasons.extend(str(item) for item in action_dry_run.get("blocking_reasons") or [])
    allowed = selected_action != "no-op" and not blocking_reasons
    return deal_store.redact_public_payload({
        "schema_version": WORKFLOW_ADVANCE_NEXT_DRY_RUN_SCHEMA,
        "deal_id": normalized_deal_id,
        "workflow_action": "advance-next",
        "dry_run": True,
        "allowed": allowed,
        "would_write": allowed,
        "selected_action": selected_action,
        "requires_hermes": requires_hermes,
        "allow_hermes": bool(allow_hermes),
        "blocking_reasons": _dedupe_strings(blocking_reasons),
        "current_phase": workflow.get("current_phase"),
        "workflow_status": workflow.get("status"),
        "action_dry_run": action_dry_run,
        "hermes_called": False,
        "report_written": False,
        "workflow_advanced": False,
    })


async def run_workflow_advance_next(
    deal_id: str,
    *,
    allow_hermes: bool = False,
    max_agents: int | None = 1,
    r3_skip: bool = False,
    r3_skip_reason: str | None = None,
    r4_overwrite: bool = False,
    created_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Execute exactly one planned workflow step."""

    plan = build_workflow_advance_next_dry_run(
        deal_id,
        allow_hermes=allow_hermes,
        max_agents=max_agents,
        r3_skip=r3_skip,
        r3_skip_reason=r3_skip_reason,
        r4_overwrite=r4_overwrite,
        wiki_root=wiki_root,
    )
    selected_action = str(plan.get("selected_action") or "no-op")
    if selected_action == "no-op":
        return {
            **plan,
            "schema_version": WORKFLOW_ADVANCE_NEXT_SCHEMA,
            "dry_run": False,
            "status": "no_op",
            "would_write": False,
            "action_result": {},
        }
    if not plan.get("allowed"):
        reasons = ", ".join(str(item) for item in plan.get("blocking_reasons") or [])
        raise ValueError(f"Workflow advance-next blocked: {reasons or selected_action}")

    if selected_action == "run-r0-coordinator":
        result = await run_workflow_r0_model(
            deal_id,
            created_by=created_by,
            wiki_root=wiki_root,
            timeout=timeout,
        )
    elif selected_action == "run-r1-serial":
        result = await run_workflow_r1_serial(
            deal_id,
            max_agents=max_agents,
            created_by=created_by,
            wiki_root=wiki_root,
            timeout=timeout,
        )
    elif selected_action == "identify-disputes":
        result = deal_disputes.identify_deal_disputes(
            deal_id,
            dry_run=False,
            preserve_rulings=True,
            created_by=created_by,
            wiki_root=wiki_root,
        )
    elif selected_action == "run-r1-5-chairman":
        result = await run_workflow_r1_5_model(
            deal_id,
            created_by=created_by,
            wiki_root=wiki_root,
            timeout=timeout,
        )
    elif selected_action == "run-r2":
        result = await run_workflow_r2_async(deal_id, mode="model", created_by=created_by, wiki_root=wiki_root, timeout=timeout)
    elif selected_action == "run-r3":
        result = await run_workflow_r3_async(
            deal_id,
            mode="model",
            skip=r3_skip,
            skip_reason=r3_skip_reason,
            created_by=created_by,
            wiki_root=wiki_root,
            timeout=timeout,
        )
    elif selected_action == "finalize-r4":
        result = await finalize_workflow_r4_async(
            deal_id,
            mode="model",
            overwrite=r4_overwrite,
            created_by=created_by,
            wiki_root=wiki_root,
            timeout=timeout,
        )
    else:
        raise ValueError(f"Unknown workflow advance-next action: {selected_action}")

    return deal_store.redact_public_payload({
        "schema_version": WORKFLOW_ADVANCE_NEXT_SCHEMA,
        "deal_id": deal_store.validate_deal_id(deal_id),
        "workflow_action": "advance-next",
        "dry_run": False,
        "allowed": True,
        "would_write": True,
        "selected_action": selected_action,
        "requires_hermes": bool(plan.get("requires_hermes")),
        "allow_hermes": bool(allow_hermes),
        "blocking_reasons": [],
        "action_result": result,
        "hermes_called": bool(result.get("hermes_called")),
        "report_written": bool(result.get("report_written") or result.get("written")),
        "workflow_advanced": bool(result.get("workflow_advanced")),
    })
