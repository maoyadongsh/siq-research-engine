"""SIQ-native IC workflow state snapshots and transition planning."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from services import deal_disputes
from services import deal_reports
from services import deal_store
from services import ic_agent_runtime
from services import ic_policy


IC_WORKFLOW_STATE_SCHEMA = "siq_ic_workflow_state_v1"
ROUND_STATE_PATH = "phases/round_state.json"
PHASE_ORDER = ("R0", "R1", "R1.5", "R2", "R3", "R4")


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


def _workflow_phase(workflow: dict[str, Any], phase: str) -> dict[str, Any]:
    phases = workflow.get("phases") if isinstance(workflow.get("phases"), dict) else {}
    payload = phases.get(phase) if isinstance(phases.get(phase), dict) else {}
    return payload if isinstance(payload, dict) else {}


def _canonical_keyed_payload(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    raw = value.get("reports") if isinstance(value.get("reports"), dict) else value
    payload: dict[str, dict[str, Any]] = {}
    for key, item in raw.items():
        if not isinstance(item, dict):
            continue
        agent_id = ic_policy.canonical_ic_profile_id(str(item.get("agent_id") or key))
        normalized = dict(item)
        normalized["agent_id"] = agent_id
        payload[agent_id] = normalized
    return payload


def _startup_receipts(package_dir: Path) -> dict[str, dict[str, Any]]:
    raw = deal_store.read_json(package_dir / "phases" / "startup_receipts.json", {}) or {}
    agents = raw.get("agents", raw) if isinstance(raw, dict) else {}
    return _canonical_keyed_payload(agents)


def _submitted_agents(workflow: dict[str, Any], reports: dict[str, dict[str, Any]]) -> list[str]:
    r1_state = _workflow_phase(workflow, "R1")
    raw = r1_state.get("submitted_agents") if isinstance(r1_state.get("submitted_agents"), list) else []
    submitted = [
        ic_policy.canonical_ic_profile_id(str(item))
        for item in raw
        if str(item or "").strip()
    ]
    for agent_id in ic_policy.R1_AGENT_SEQUENCE:
        if agent_id in reports and agent_id not in submitted:
            submitted.append(agent_id)
    return [agent_id for agent_id in ic_policy.R1_AGENT_SEQUENCE if agent_id in set(submitted)]


def _phase_artifacts(package_dir: Path) -> dict[str, list[dict[str, Any]]]:
    return {
        "R0": [
            _artifact(package_dir, "project_meta.json"),
            _artifact(package_dir, "phases/r0_intake.json"),
            _artifact(package_dir, "discussion/00_\u9879\u76ee\u4fe1\u606f_R0.md"),
        ],
        "R1": [
            _artifact(package_dir, "phases/startup_receipts.json"),
            _artifact(package_dir, "phases/r1_reports.json"),
        ],
        "R1.5": [
            _artifact(package_dir, deal_disputes.DISPUTES_JSON_PATH),
            _artifact(package_dir, deal_disputes.DISPUTES_MARKDOWN_PATH),
        ],
        "R2": [
            _artifact(package_dir, "phases/r2_reports.json"),
            _artifact(package_dir, deal_reports.R2_REPORT_ARTIFACT_PATH),
        ],
        "R3": [
            _artifact(package_dir, "phases/r3_reports.json"),
            _artifact(package_dir, deal_reports.R3_REVIEW_ARTIFACT_PATH),
        ],
        "R4": [
            _artifact(package_dir, "phases/r4_decision.json"),
            _artifact(package_dir, "decision/IC_DECISION_REPORT.md"),
        ],
    }


def _phase_status(phase_state: dict[str, Any], artifacts: list[dict[str, Any]]) -> str:
    explicit = str(phase_state.get("status") or "").strip()
    if explicit:
        return explicit
    if any(item.get("available") for item in artifacts):
        return "available"
    return "pending"


def _phase_summary(package_dir: Path, workflow: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts_by_phase = _phase_artifacts(package_dir)
    phases: list[dict[str, Any]] = []
    for phase in PHASE_ORDER:
        phase_state = _workflow_phase(workflow, phase)
        artifacts = artifacts_by_phase.get(phase, [])
        phases.append({
            "phase": phase,
            "status": _phase_status(phase_state, artifacts),
            "started_at": phase_state.get("started_at"),
            "completed_at": phase_state.get("completed_at"),
            "updated_at": phase_state.get("updated_at"),
            "artifacts": artifacts,
        })
    return phases


def _r1_round_state(package_dir: Path, workflow: dict[str, Any]) -> dict[str, Any]:
    receipts = _startup_receipts(package_dir)
    reports = _canonical_keyed_payload(deal_store.read_json(package_dir / "phases" / "r1_reports.json", {}) or {})
    submitted = _submitted_agents(workflow, reports)
    missing_agents = [agent_id for agent_id in ic_policy.R1_AGENT_SEQUENCE if agent_id not in set(submitted)]
    active_agent = missing_agents[0] if missing_agents else None
    agents: list[dict[str, Any]] = []
    for index, agent_id in enumerate(ic_policy.R1_AGENT_SEQUENCE, start=1):
        receipt = receipts.get(agent_id)
        report = reports.get(agent_id)
        agents.append({
            "agent_id": agent_id,
            "sequence_index": index,
            "submitted": agent_id in set(submitted),
            "has_startup_receipt": receipt is not None,
            "startup_receipt_id": receipt.get("receipt_id") if isinstance(receipt, dict) else None,
            "has_report": report is not None,
            "report_status": report.get("status") if isinstance(report, dict) else None,
            "score": report.get("score") if isinstance(report, dict) else None,
            "recommendation": report.get("recommendation") if isinstance(report, dict) else None,
        })
    complete = not missing_agents
    return {
        "round_name": "R1",
        "mode": "sequential",
        "status": "completed" if complete else "in_progress" if submitted else "pending",
        "required_agents": list(ic_policy.R1_AGENT_SEQUENCE),
        "submitted_agents": submitted,
        "missing_agents": missing_agents,
        "active_agent": active_agent,
        "next_agent_id": active_agent,
        "completed": complete,
        "counts": {
            "required_agents": len(ic_policy.R1_AGENT_SEQUENCE),
            "submitted_agents": len(submitted),
            "startup_receipts": sum(1 for agent_id in ic_policy.R1_AGENT_SEQUENCE if agent_id in receipts),
            "reports": sum(1 for agent_id in ic_policy.R1_AGENT_SEQUENCE if agent_id in reports),
        },
        "agents": agents,
    }


def _rounds(package_dir: Path, workflow: dict[str, Any]) -> dict[str, Any]:
    r2_reports = _canonical_keyed_payload(deal_store.read_json(package_dir / "phases" / "r2_reports.json", {}) or {})
    r3_payload = deal_store.read_json(package_dir / "phases" / "r3_reports.json", {}) or {}
    r4_payload = deal_store.read_json(package_dir / "phases" / "r4_decision.json", {}) or {}
    r1 = _r1_round_state(package_dir, workflow)
    return {
        "R0": {
            "round_name": "R0",
            "status": _workflow_phase(workflow, "R0").get("status") or ("completed" if (package_dir / "phases" / "r0_intake.json").is_file() else "pending"),
            "artifact_path": "phases/r0_intake.json",
        },
        "R1": r1,
        "R1.5": {
            "round_name": "R1.5",
            "status": _workflow_phase(workflow, "R1.5").get("status") or ("available" if (package_dir / deal_disputes.DISPUTES_JSON_PATH).is_file() else "pending"),
            "artifact_path": deal_disputes.DISPUTES_JSON_PATH,
        },
        "R2": {
            "round_name": "R2",
            "status": _workflow_phase(workflow, "R2").get("status") or ("completed" if r2_reports else "pending"),
            "required_agents": list(ic_agent_runtime.R2_AGENT_SEQUENCE),
            "submitted_agents": [agent_id for agent_id in ic_agent_runtime.R2_AGENT_SEQUENCE if agent_id in r2_reports],
            "missing_agents": [agent_id for agent_id in ic_agent_runtime.R2_AGENT_SEQUENCE if agent_id not in r2_reports],
            "counts": {"reports": len(r2_reports), "required_reports": len(ic_agent_runtime.R2_AGENT_SEQUENCE)},
        },
        "R3": {
            "round_name": "R3",
            "status": _workflow_phase(workflow, "R3").get("status") or (str(r3_payload.get("mode")) if isinstance(r3_payload, dict) and r3_payload else "pending"),
            "mode": r3_payload.get("mode") if isinstance(r3_payload, dict) else None,
            "skip_reason": r3_payload.get("skip_reason") if isinstance(r3_payload, dict) else None,
        },
        "R4": {
            "round_name": "R4",
            "status": _workflow_phase(workflow, "R4").get("status") or ("completed" if isinstance(r4_payload, dict) and r4_payload else "pending"),
            "decision": r4_payload.get("decision") if isinstance(r4_payload, dict) else None,
            "final_score": r4_payload.get("final_score") if isinstance(r4_payload, dict) else None,
            "human_confirmation_status": (
                (_workflow_phase(workflow, "R4").get("human_confirmation_status"))
                or ((r4_payload.get("human_confirmation") or {}).get("status") if isinstance(r4_payload, dict) and isinstance(r4_payload.get("human_confirmation"), dict) else None)
            ),
        },
    }


def _transition_plan(
    deal_id: str,
    *,
    allow_hermes: bool,
    max_agents: int,
    r3_skip: bool,
    r3_skip_reason: str | None,
    r4_overwrite: bool,
    wiki_root: Path | str | None,
) -> dict[str, Any]:
    try:
        return ic_agent_runtime.build_workflow_advance_next_dry_run(
            deal_id,
            allow_hermes=allow_hermes,
            max_agents=max_agents,
            r3_skip=r3_skip,
            r3_skip_reason=r3_skip_reason,
            r4_overwrite=r4_overwrite,
            wiki_root=wiki_root,
        )
    except (FileNotFoundError, ValueError):
        raise
    except Exception as exc:  # defensive: state snapshots should remain readable.
        return {
            "schema_version": "siq_ic_workflow_transition_plan_error_v1",
            "deal_id": deal_store.validate_deal_id(deal_id),
            "allowed": False,
            "selected_action": "unknown",
            "blocking_reasons": ["transition_plan_failed"],
            "error": str(exc)[:300],
        }


def summarize_workflow_state(
    deal_id: str,
    *,
    allow_hermes: bool = False,
    max_agents: int = 1,
    r3_skip: bool = False,
    r3_skip_reason: str | None = None,
    r4_overwrite: bool = False,
    write_snapshot: bool = False,
    created_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    workflow = deal_store.read_json(package_dir / "phases" / "workflow_state.json", {}) or {}
    project_meta = deal_store.read_json(package_dir / "project_meta.json", {}) or {}
    generated_at = deal_store.utc_now_iso()
    transition_plan = _transition_plan(
        normalized_deal_id,
        allow_hermes=allow_hermes,
        max_agents=max_agents,
        r3_skip=r3_skip,
        r3_skip_reason=r3_skip_reason,
        r4_overwrite=r4_overwrite,
        wiki_root=wiki_root,
    )
    payload = deal_store.redact_public_payload({
        "schema_version": IC_WORKFLOW_STATE_SCHEMA,
        "deal_id": normalized_deal_id,
        "company_name": project_meta.get("company_name") or workflow.get("company_name"),
        "current_phase": workflow.get("current_phase") or "R0",
        "workflow_status": workflow.get("status") or project_meta.get("status"),
        "generated_at": generated_at,
        "phase_order": list(PHASE_ORDER),
        "phases": _phase_summary(package_dir, workflow),
        "rounds": _rounds(package_dir, workflow),
        "transition_plan": transition_plan,
        "round_state_path": ROUND_STATE_PATH,
        "written": False,
        "created_by": created_by,
    })
    if write_snapshot:
        payload["written"] = True
        deal_store.write_json(package_dir / ROUND_STATE_PATH, payload)
        deal_store.append_audit_event(
            normalized_deal_id,
            {
                "event_type": "deal_workflow_round_state_snapshot_written",
                "deal_id": normalized_deal_id,
                "round_state_path": ROUND_STATE_PATH,
                "selected_action": transition_plan.get("selected_action"),
                "created_by": created_by,
            },
            wiki_root=wiki_root,
        )
    return payload


def read_workflow_state_snapshot(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    payload = deal_store.read_json(package_dir / ROUND_STATE_PATH, None)
    return {
        "deal_id": deal_store.validate_deal_id(deal_id),
        "round_state_path": ROUND_STATE_PATH,
        "state": payload if isinstance(payload, dict) else None,
    }
