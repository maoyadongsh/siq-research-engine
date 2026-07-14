#!/usr/bin/env python3
"""Build and verify fail-closed primary-market IC golden-case candidates.

This tool does not promote golden cases.  It derives every path assertion from
persisted Deal artifacts, records their byte digests, and always leaves
``quality_accepted`` false.  The release gate remains the promotion boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPO_ROOT / "agents/hermes/profiles/siq_ic_shared/golden_case_manifest.json"
RESULT_PATH = Path("release/golden_case_result.json")
BINDINGS_PATH = Path("release/golden_case_bindings.json")
RESULT_SCHEMA = "siq_ic_golden_case_result_v1"
PATH_SCHEMA = "siq_ic_golden_path_evaluation_v1"
BINDINGS_SCHEMA = "siq_ic_golden_case_bindings_v1"
EVALUATOR_NAME = "primary-market-ic-golden-evaluator"
EVALUATOR_VERSION = "v1"
PROMPT_VERSION = "siq_ic_phase_prompt_v5"
DIGEST_RE = re.compile(r"^[a-f0-9]{64}$")
EVIDENCE_RE = re.compile(r"^EVID-[A-Za-z0-9][A-Za-z0-9:_-]{2,190}$")

INDEPENDENT_CASE_IDS = {
    "GOLDEN-PMIC-CONDITIONAL-SUPPORT",
    "GOLDEN-PMIC-MATERIAL-RISK",
    "GOLDEN-PMIC-INSUFFICIENT-EVIDENCE",
    "GOLDEN-PMIC-FULL-R3",
    "GOLDEN-PMIC-SNAPSHOT-STALE",
}
PROFILE_IDS = {
    "siq_ic_master_coordinator",
    "siq_ic_chairman",
    "siq_ic_strategist",
    "siq_ic_sector_expert",
    "siq_ic_finance_auditor",
    "siq_ic_legal_scanner",
    "siq_ic_risk_controller",
}
R1A_ROLES = {
    "siq_ic_strategist",
    "siq_ic_sector_expert",
    "siq_ic_finance_auditor",
    "siq_ic_legal_scanner",
}
R1B_ROLES = {"siq_ic_risk_controller", "siq_ic_chairman"}
R2_ROLES = R1A_ROLES | {"siq_ic_risk_controller"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _valid_timestamp(value: Any) -> bool:
    text = _text(value)
    if not text:
        return False
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()


def _payload_digest(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _contained_path(root: Path, relative: Any) -> Path | None:
    text = str(relative or "").strip()
    if not text or Path(text).is_absolute():
        return None
    candidate = (root / text).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _ids(value: Any) -> set[str]:
    return {_text(item) for item in _as_list(value) if _text(item)}


def _assertion(name: str, actual: Any, expected: Any = True) -> dict[str, Any]:
    return {"name": name, "expected": expected, "actual": actual, "passed": actual == expected}


@dataclass
class EvaluationContext:
    bundle: Path
    case_id: str
    deal_id: str
    run_id: str
    snapshot_hash: str
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)

    def json(self, relative: str) -> Any:
        path = _contained_path(self.bundle, relative)
        exists = bool(path and path.is_file())
        self.sources[relative] = {
            "path": relative,
            "exists": exists,
            "sha256": _file_digest(path) if exists and path else None,
        }
        return _read_json(path) if exists and path else None

    def file(self, relative: str) -> Path | None:
        path = _contained_path(self.bundle, relative)
        exists = bool(path and path.is_file())
        self.sources[relative] = {
            "path": relative,
            "exists": exists,
            "sha256": _file_digest(path) if exists and path else None,
        }
        return path if exists else None

    def source_rows(self) -> list[dict[str, Any]]:
        return [self.sources[key] for key in sorted(self.sources)]


PathEvaluator = Callable[[EvaluationContext], list[dict[str, Any]]]


def _task_store(ctx: EvaluationContext) -> list[dict[str, Any]]:
    payload = _as_dict(ctx.json("phases/ic_agent_tasks.json"))
    if payload.get("schema_version") != "siq_ic_agent_tasks_v1":
        return []
    return [item for item in _as_list(payload.get("tasks")) if isinstance(item, dict)]


def _task_output_digests_valid(ctx: EvaluationContext, task: Mapping[str, Any]) -> bool:
    hashes = task.get("output_artifact_hashes")
    if not isinstance(hashes, dict) or not hashes:
        return False
    paths = [_text(item) for item in _as_list(task.get("output_artifact_paths")) if _text(item)]
    if not paths or len(paths) != len(set(paths)) or set(paths) != {_text(item) for item in hashes}:
        return False
    for relative, expected in hashes.items():
        path = ctx.file(str(relative))
        if path is None or not DIGEST_RE.fullmatch(_text(expected)) or _file_digest(path) != expected:
            return False
    return True


def _valid_tasks(ctx: EvaluationContext, phase: str) -> list[dict[str, Any]]:
    valid: list[dict[str, Any]] = []
    for task in _task_store(ctx):
        if task.get("phase") != phase or task.get("deal_id") != ctx.deal_id:
            continue
        if task.get("status") != "succeeded" or task.get("hermes_called") is not True:
            continue
        if task.get("prompt_contract_version") != PROMPT_VERSION:
            continue
        if task.get("evidence_snapshot_hash") != ctx.snapshot_hash:
            continue
        hermes_run_id = _text(task.get("hermes_run_id"))
        if (
            not _text(task.get("task_id"))
            or not _text(task.get("workflow_run_id"))
            or not hermes_run_id
            or hermes_run_id not in _ids(task.get("hermes_run_ids"))
        ):
            continue
        if _as_dict(task.get("contract_validation")).get("passed") is not True:
            continue
        if not _as_list(task.get("methodology_refs")):
            continue
        if not _task_output_digests_valid(ctx, task):
            continue
        valid.append(task)
    return valid


def _task_role_assertions(ctx: EvaluationContext, phase: str, roles: set[str]) -> list[dict[str, Any]]:
    tasks = _valid_tasks(ctx, phase)
    actual_roles = [_text(task.get("agent_id")) for task in tasks]
    role_counts = {role: actual_roles.count(role) for role in roles}
    return [
        _assertion(f"{phase}.validated_task_roles", sorted(actual_roles), sorted(roles)),
        _assertion(
            f"{phase}.one_validated_task_per_role",
            role_counts,
            {role: 1 for role in sorted(roles)},
        ),
        _assertion(f"{phase}.task_ids_unique", len({_text(task.get("task_id")) for task in tasks}), len(tasks)),
    ]


def _artifact_is_bound_to_task(
    ctx: EvaluationContext,
    artifact: Mapping[str, Any],
    *,
    phase: str,
    role: str,
) -> bool:
    task_id = _text(artifact.get("task_id"))
    hermes_run_id = _text(artifact.get("hermes_run_id"))
    workflow_run_id = _text(artifact.get("workflow_run_id"))
    if not task_id or not hermes_run_id:
        return False
    matches = [
        task
        for task in _valid_tasks(ctx, phase)
        if task.get("agent_id") == role
        and task.get("task_id") == task_id
        and task.get("hermes_run_id") == hermes_run_id
        and (not workflow_run_id or task.get("workflow_run_id") == workflow_run_id)
    ]
    return len(matches) == 1


def _report_is_current(report: Mapping[str, Any], ctx: EvaluationContext, *, phase: str, role: str) -> bool:
    return (
        report.get("schema_version") == "siq_ic_expert_report_v2"
        and report.get("deal_id") == ctx.deal_id
        and report.get("agent_id") == role
        and report.get("phase") == phase
        and report.get("evidence_snapshot_hash") == ctx.snapshot_hash
        and report.get("generation_mode") == "model"
        and bool(_text(report.get("task_id")))
        and bool(_text(report.get("hermes_run_id")))
        and bool(_as_list(report.get("methodology_refs")))
        and _artifact_is_bound_to_task(ctx, report, phase=phase, role=role)
    )


def _report_role_assertions(
    ctx: EvaluationContext,
    *,
    relative: str,
    phase: str,
    roles: set[str],
) -> list[dict[str, Any]]:
    reports = _as_dict(ctx.json(relative))
    current = {
        role
        for role in roles
        if isinstance(reports.get(role), dict) and _report_is_current(reports[role], ctx, phase=phase, role=role)
    }
    return [_assertion(f"{phase}.current_model_reports", sorted(current), sorted(roles))]


def _path_r0(ctx: EvaluationContext) -> list[dict[str, Any]]:
    readiness = _as_dict(ctx.json("phases/r0_readiness.json"))
    assertions = [
        _assertion("R0.schema", readiness.get("schema_version"), "siq_ic_r0_readiness_v1"),
        _assertion("R0.deal", readiness.get("deal_id"), ctx.deal_id),
        _assertion("R0.snapshot", readiness.get("evidence_snapshot_hash"), ctx.snapshot_hash),
        _assertion("R0.model", readiness.get("generation_mode"), "model"),
        _assertion("R0.ready", readiness.get("readiness"), "ready"),
        _assertion("R0.no_blockers", _as_list(readiness.get("blocking_reasons")), []),
        _assertion(
            "R0.task_binding",
            _artifact_is_bound_to_task(
                ctx,
                readiness,
                phase="R0",
                role="siq_ic_master_coordinator",
            ),
        ),
    ]
    return assertions + _task_role_assertions(ctx, "R0", {"siq_ic_master_coordinator"})


def _path_r1a(ctx: EvaluationContext) -> list[dict[str, Any]]:
    return _report_role_assertions(
        ctx, relative="phases/r1_reports.json", phase="R1A", roles=R1A_ROLES
    ) + _task_role_assertions(ctx, "R1A", R1A_ROLES)


def _path_r1b(ctx: EvaluationContext) -> list[dict[str, Any]]:
    return _report_role_assertions(
        ctx, relative="phases/r1_reports.json", phase="R1B", roles=R1B_ROLES
    ) + _task_role_assertions(ctx, "R1B", R1B_ROLES)


def _path_r1_5(ctx: EvaluationContext) -> list[dict[str, Any]]:
    disputes = _as_dict(ctx.json("phases/r1_5_disputes.json"))
    return [
        _assertion("R1.5.schema", disputes.get("schema_version"), "siq_ic_disputes_v1"),
        _assertion("R1.5.deal", disputes.get("deal_id"), ctx.deal_id),
        *_task_role_assertions(ctx, "R1.5", {"siq_ic_chairman"}),
    ]


def _path_r2(ctx: EvaluationContext) -> list[dict[str, Any]]:
    assertions = _report_role_assertions(
        ctx, relative="phases/r2_reports.json", phase="R2", roles=R2_ROLES
    ) + _task_role_assertions(ctx, "R2", R2_ROLES)
    reports = _as_dict(ctx.json("phases/r2_reports.json"))
    revisions = {
        role
        for role in R2_ROLES
        if _as_dict(reports.get(role)).get("revision_contract_schema_version") == "siq_ic_r2_revision_v1"
    }
    assertions.append(_assertion("R2.revision_contract_roles", sorted(revisions), sorted(R2_ROLES)))
    return assertions


def _r3(ctx: EvaluationContext) -> dict[str, Any]:
    return _as_dict(ctx.json("phases/r3_reports.json"))


def _r3_debates(ctx: EvaluationContext) -> list[dict[str, Any]]:
    return [item for item in _as_list(_r3(ctx).get("debates")) if isinstance(item, dict)]


def _path_r3_short_or_full(ctx: EvaluationContext) -> list[dict[str, Any]]:
    r3 = _r3(ctx)
    debates = _r3_debates(ctx)
    return [
        _assertion("R3.schema", r3.get("schema_version"), "siq_ic_r3_debate_bundle_v2"),
        _assertion("R3.deal", r3.get("deal_id"), ctx.deal_id),
        _assertion("R3.snapshot", r3.get("evidence_snapshot_hash"), ctx.snapshot_hash),
        _assertion("R3.mode_allowed", r3.get("mode") in {"short", "full"}),
        _assertion("R3.real_model", r3.get("hermes_called") is True),
        _assertion("R3.debate_count", len(debates) > 0),
        _assertion("R3.validated_tasks", len(_valid_tasks(ctx, "R3")) > 0),
    ]


def _r4(ctx: EvaluationContext) -> dict[str, Any]:
    payload = ctx.json("phases/r4_decision.json")
    if not isinstance(payload, dict):
        payload = ctx.json("decision/decision_payload.json")
    return _as_dict(payload)


def _path_r4(ctx: EvaluationContext) -> list[dict[str, Any]]:
    r4 = _r4(ctx)
    current = (
        r4.get("schema_version") == "siq_ic_r4_decision_v2"
        and r4.get("deal_id") == ctx.deal_id
        and r4.get("evidence_snapshot_hash") == ctx.snapshot_hash
        and r4.get("generation_mode") == "model"
        and bool(_text(r4.get("report_id")))
        and isinstance(r4.get("revision"), int)
        and _artifact_is_bound_to_task(
            ctx,
            r4,
            phase="R4",
            role="siq_ic_chairman",
        )
    )
    return [
        _assertion("R4.current_model_decision", current),
        *_task_role_assertions(ctx, "R4", {"siq_ic_chairman"}),
    ]


def _factcheck(ctx: EvaluationContext) -> dict[str, Any]:
    for relative in ("decision/factcheck.json", "decision/factcheck_report.json", "factcheck/factcheck.json"):
        value = ctx.json(relative)
        if isinstance(value, dict):
            return value
    return {}


def _quality(ctx: EvaluationContext) -> dict[str, Any]:
    return _as_dict(ctx.json("decision/report_quality.json"))


def _path_factcheck(ctx: EvaluationContext) -> list[dict[str, Any]]:
    r4 = _r4(ctx)
    factcheck = _factcheck(ctx)
    task = _as_dict(ctx.json("decision/factcheck_task.json"))
    return [
        _assertion("factcheck.schema", factcheck.get("schema_version"), "siq_ic_report_factcheck_v1"),
        _assertion("factcheck.status", factcheck.get("status"), "pass"),
        _assertion("factcheck.report", factcheck.get("report_id"), r4.get("report_id")),
        _assertion("factcheck.revision", factcheck.get("report_revision"), r4.get("revision")),
        _assertion("factcheck.snapshot", factcheck.get("evidence_snapshot_hash"), ctx.snapshot_hash),
        _assertion("factcheck.unsupported", _as_list(factcheck.get("unsupported_claims")), []),
        _assertion("factcheck.repairs", _as_list(factcheck.get("required_repairs")), []),
        _assertion("factcheck.task_schema", task.get("schema_version"), "siq_ic_factcheck_task_v1"),
        _assertion("factcheck.task_status", task.get("status"), "succeeded"),
        _assertion("factcheck.task_contract", _as_dict(task.get("contract_validation")).get("passed"), True),
        _assertion("factcheck.task_output_hashes", bool(_as_dict(task.get("output_artifact_hashes")))),
        _assertion("factcheck.task_raw_digests", _task_output_digests_valid(ctx, task)),
    ]


def _path_human_confirmation(ctx: EvaluationContext) -> list[dict[str, Any]]:
    r4 = _r4(ctx)
    quality = _quality(ctx)
    factcheck = _factcheck(ctx)
    confirmation = _as_dict(r4.get("human_confirmation"))
    reviewed = dict(r4)
    reviewed.pop("human_confirmation", None)
    expected = {
        "attestation_schema_version": "siq_ic_human_confirmation_attestation_v1",
        "report_id": r4.get("report_id"),
        "report_revision": r4.get("revision"),
        "workflow_run_id": r4.get("workflow_run_id"),
        "evidence_snapshot_hash": ctx.snapshot_hash,
        "decision_sha256": _payload_digest(reviewed),
        "quality_sha256": _payload_digest(quality),
        "factcheck_sha256": _payload_digest(factcheck),
    }
    actor = _as_dict(confirmation.get("confirmed_by"))
    runs = _as_list(_as_dict(ctx.json("phases/ic_workflow_runs.json")).get("runs"))
    completed = next(
        (
            item
            for item in runs
            if isinstance(item, dict)
            and item.get("workflow_run_id") == r4.get("workflow_run_id")
            and item.get("status") == "completed"
        ),
        None,
    )
    return [
        _assertion("human.status", confirmation.get("status") in {"confirmed", "overridden"}),
        _assertion("human.named_actor", bool(_text(actor.get("id")) and _text(actor.get("username")))),
        *[
            _assertion(f"human.{key}", confirmation.get(key), value)
            for key, value in expected.items()
        ],
        _assertion("human.workflow_completed", completed is not None),
        _assertion("human.workflow_completion", _as_dict(completed).get("completion"), confirmation),
    ]


def _path_material_risk(ctx: EvaluationContext) -> list[dict[str, Any]]:
    reports = _as_dict(ctx.json("phases/r1_reports.json"))
    material_roles: list[str] = []
    for role in ("siq_ic_finance_auditor", "siq_ic_legal_scanner"):
        report = _as_dict(reports.get(role))
        red_flags = _as_list(report.get("red_flags"))
        has_material_flag = any(
            isinstance(item, dict)
            and (
                _text(item.get("severity")).lower() in {"high", "critical", "material"}
                or _text(item.get("decision_impact")).lower() in {"critical", "material"}
                or item.get("blocking") is True
            )
            for item in red_flags
        )
        if report.get("recommendation") in {"review", "reject", "insufficient_evidence"} or has_material_flag:
            material_roles.append(role)
    return [
        _assertion("material_risk.finance_or_legal", bool(material_roles)),
        _assertion(
            "material_risk.current_reports",
            all(_report_is_current(reports[role], ctx, phase="R1A", role=role) for role in material_roles),
        ),
    ]


def _path_r3_full(ctx: EvaluationContext) -> list[dict[str, Any]]:
    r3 = _r3(ctx)
    plan = _as_dict(r3.get("plan"))
    return [
        _assertion("R3.full_mode", r3.get("mode"), "full"),
        _assertion("R3.full_plan", plan.get("mode"), "full"),
        _assertion("R3.full_debates", len(_r3_debates(ctx)) > 0),
        _assertion("R3.full_model", r3.get("hermes_called") is True),
        _assertion("R3.full_tasks", len(_valid_tasks(ctx, "R3")) > 0),
    ]


def _path_r4_non_pass(ctx: EvaluationContext) -> list[dict[str, Any]]:
    r4 = _r4(ctx)
    return [
        *_path_r4(ctx),
        _assertion(
            "R4.non_pass_outcome",
            r4.get("recommendation") in {"review", "reject", "insufficient_evidence"}
            or r4.get("decision") in {"review", "reject", "insufficient_evidence"},
        ),
    ]


def _evidence_ids(ctx: EvaluationContext) -> set[str]:
    index = _as_dict(ctx.json("evidence/evidence_index.json"))
    return {
        _text(item.get("evidence_id"))
        for item in _as_list(index.get("items"))
        if isinstance(item, dict) and EVIDENCE_RE.fullmatch(_text(item.get("evidence_id")))
    }


def _all_reports(ctx: EvaluationContext) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for relative in ("phases/r1_reports.json", "phases/r2_reports.json"):
        rows.extend(item for item in _as_dict(ctx.json(relative)).values() if isinstance(item, dict))
    r4 = _r4(ctx)
    if r4:
        rows.append(r4)
    return rows


def _known_critical_gaps(ctx: EvaluationContext) -> list[str]:
    quality = _as_dict(ctx.json("evidence/evidence_quality_report.json"))
    if _text(quality.get("critical_fact_status")).lower() != "incomplete":
        return []
    return sorted(_ids(quality.get("known_critical_fact_gaps")))


def _workflow_phase(workflow: Mapping[str, Any], phase: str) -> dict[str, Any]:
    return _as_dict(_as_dict(workflow.get("phases")).get(phase))


def _needs_more_evidence_disputes(ctx: EvaluationContext) -> list[dict[str, Any]]:
    disputes = _as_list(_as_dict(ctx.json("phases/r1_5_disputes.json")).get("disputes"))
    result: list[dict[str, Any]] = []
    for item in disputes:
        if not isinstance(item, dict) or _text(item.get("severity")).lower() not in {"high", "critical"}:
            continue
        ruling = _text(item.get("ruling") or _as_dict(item.get("chairman_ruling")).get("decision")).lower()
        status = _text(item.get("status")).lower()
        if item.get("resolved") is False and (
            ruling in {"needs_more_evidence", "unresolved", "insufficient_evidence"}
            or status in {"open", "unresolved", "needs_more_evidence", "blocked"}
        ):
            result.append(item)
    return result


def _r1_5_terminal_lineage_errors(ctx: EvaluationContext) -> list[str]:
    unresolved = _needs_more_evidence_disputes(ctx)
    return _r1_5_ruling_lineage_errors(ctx, unresolved)


def _r1_5_ruling_lineage_errors(
    ctx: EvaluationContext,
    disputes: Sequence[Mapping[str, Any]],
) -> list[str]:
    tasks = _valid_tasks(ctx, "R1.5")
    errors: list[str] = []
    if len(tasks) != 1:
        errors.append("r1_5_task_cardinality_invalid")
        return errors
    task = tasks[0]
    expected = {
        "task_id": task.get("task_id"),
        "workflow_run_id": task.get("workflow_run_id"),
        "hermes_run_id": task.get("hermes_run_id"),
        "evidence_snapshot_hash": ctx.snapshot_hash,
    }
    for dispute in disputes:
        dispute_id = _text(dispute.get("dispute_id")) or "<missing>"
        ruling = _as_dict(dispute.get("chairman_ruling"))
        if ruling.get("schema_version") != "siq_deal_r1_5_dispute_ruling_v1":
            errors.append(f"{dispute_id}:ruling_schema_invalid")
        if ruling.get("deal_id") != ctx.deal_id or ruling.get("dispute_id") != dispute_id:
            errors.append(f"{dispute_id}:ruling_identity_mismatch")
        if ruling.get("agent_id") != "siq_ic_chairman" or ruling.get("generation_mode") != "model":
            errors.append(f"{dispute_id}:ruling_authority_invalid")
        for identity_field, value in expected.items():
            if ruling.get(identity_field) != value:
                errors.append(f"{dispute_id}:ruling_{identity_field}_mismatch")
        if dispute.get("evidence_snapshot_hash") != ctx.snapshot_hash:
            errors.append(f"{dispute_id}:dispute_snapshot_mismatch")
        matches = [
            candidate
            for candidate in tasks
            if candidate.get("task_id") == ruling.get("task_id")
            and candidate.get("workflow_run_id") == ruling.get("workflow_run_id")
            and candidate.get("hermes_run_id") == ruling.get("hermes_run_id")
            and candidate.get("evidence_snapshot_hash") == ruling.get("evidence_snapshot_hash")
        ]
        if len(matches) != 1:
            errors.append(f"{dispute_id}:ruling_task_binding_invalid")
    return sorted(set(errors))


def _current_report_roles(ctx: EvaluationContext, *, phase: str, roles: set[str]) -> set[str]:
    relative = "phases/r1_reports.json" if phase in {"R1A", "R1B"} else "phases/r2_reports.json"
    reports = _as_dict(ctx.json(relative))
    return {
        role
        for role in roles
        if isinstance(reports.get(role), dict) and _report_is_current(reports[role], ctx, phase=phase, role=role)
    }


def _early_insufficient_terminal_route(ctx: EvaluationContext) -> str:
    readiness = _as_dict(ctx.json("phases/r0_readiness.json"))
    workflow = _as_dict(ctx.json("phases/workflow_state.json"))
    smoke = _as_dict(ctx.json("release/real_smoke.json"))
    r1_5_payload = _as_dict(ctx.json("phases/r1_5_disputes.json"))
    smoke_phases = _as_dict(smoke.get("phase_runs"))
    r0_phase = _workflow_phase(workflow, "R0")
    smoke_r0 = _as_dict(smoke_phases.get("R0"))
    r0_blocked = (
        readiness.get("generation_mode") == "model"
        and readiness.get("readiness") in {"blocked", "needs_more_evidence"}
        and bool(_as_list(readiness.get("blocking_reasons")))
        and r0_phase.get("status") == "blocked"
        and smoke.get("status") == "blocked"
        and smoke_r0.get("status") == "blocked"
        and smoke_r0.get("task_validated") is True
        and smoke_r0.get("workflow_advanced") is False
        and len(_valid_tasks(ctx, "R0")) == 1
    )
    if r0_blocked:
        return "r0_blocked"

    r1_5_phase = _workflow_phase(workflow, "R1.5")
    smoke_r1_5 = _as_dict(smoke_phases.get("R1.5"))
    r1a_tasks = _valid_tasks(ctx, "R1A")
    r1b_tasks = _valid_tasks(ctx, "R1B")
    r1_5_tasks = _valid_tasks(ctx, "R1.5")
    r1_5_lineage_errors = _r1_5_terminal_lineage_errors(ctx)
    r1_5_blocked = (
        readiness.get("generation_mode") == "model"
        and readiness.get("readiness") == "ready"
        and _workflow_phase(workflow, "R0").get("status") == "completed"
        and _current_report_roles(ctx, phase="R1A", roles=R1A_ROLES) == R1A_ROLES
        and _current_report_roles(ctx, phase="R1B", roles=R1B_ROLES) == R1B_ROLES
        and len(r1a_tasks) == len(R1A_ROLES)
        and {_text(task.get("agent_id")) for task in r1a_tasks} == R1A_ROLES
        and len(r1b_tasks) == len(R1B_ROLES)
        and {_text(task.get("agent_id")) for task in r1b_tasks} == R1B_ROLES
        and r1_5_payload.get("schema_version") == "siq_ic_disputes_v1"
        and r1_5_payload.get("deal_id") == ctx.deal_id
        and bool(_needs_more_evidence_disputes(ctx))
        and not r1_5_lineage_errors
        and r1_5_phase.get("status") == "blocked"
        and smoke.get("status") == "blocked"
        and smoke_r1_5.get("status") == "blocked"
        and smoke_r1_5.get("task_validated") is True
        and smoke_r1_5.get("workflow_advanced") is False
        and len(r1_5_tasks) == 1
        and {_text(task.get("agent_id")) for task in r1_5_tasks} == {"siq_ic_chairman"}
    )
    return "r1_5_blocked" if r1_5_blocked else ""


def _files_under(ctx: EvaluationContext, relative: str) -> list[str]:
    root = _contained_path(ctx.bundle, relative)
    if root is None or not root.exists():
        ctx.file(relative)
        return []
    if not root.is_dir():
        ctx.file(relative)
        return [relative]
    result: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        child = path.relative_to(ctx.bundle).as_posix()
        ctx.file(child)
        result.append(child)
    return result


def _unexpected_downstream_outputs(ctx: EvaluationContext, route: str) -> tuple[list[str], list[str]]:
    r1_artifacts = (
        "discussion/01_R1_尽调汇总.md",
        "discussion/01_R1_strategist_report.md",
        "discussion/01_R1_sector_expert_report.md",
        "discussion/01_R1_finance_auditor_report.md",
        "discussion/01_R1_legal_scanner_report.md",
        "discussion/01_R1_risk_controller_report.md",
        "discussion/01_R1_chairman_report.md",
        "discussion/02_R1.5_裁决记录.md",
    )
    r2_r4_artifacts = (
        "discussion/03_R2_观点完善汇总.md",
        "discussion/03_R2_观点完善.md",
        "discussion/04_R3_红蓝对抗.md",
    )
    if route == "r0_blocked":
        allowed_phases = {"R0"}
        relatives = (
            "phases/r1_reports.json",
            "phases/r1_5_disputes.json",
            "phases/r2_reports.json",
            "phases/r3_reports.json",
            "phases/r4_decision.json",
            *r1_artifacts,
            *r2_r4_artifacts,
        )
        allowed_discussion_prefixes = ("00_",)
    else:
        allowed_phases = {"R0", "R1A", "R1B", "R1.5"}
        relatives = (
            "phases/r2_reports.json",
            "phases/r3_reports.json",
            "phases/r4_decision.json",
            *r2_r4_artifacts,
        )
        allowed_discussion_prefixes = ("00_", "01_", "02_")
    artifacts = [relative for relative in relatives if ctx.file(relative) is not None]
    for root in ("decision", "factcheck"):
        root_path = _contained_path(ctx.bundle, root)
        if root_path is not None and root_path.exists():
            artifacts.append(root)
        artifacts.extend(_files_under(ctx, root))
    for relative in _files_under(ctx, "discussion"):
        if not Path(relative).name.startswith(allowed_discussion_prefixes):
            artifacts.append(relative)

    task_store = _task_store(ctx)
    tasks = [
        _text(task.get("task_id")) or "<missing>"
        for task in task_store
        if _text(task.get("phase")) not in allowed_phases
    ]
    task_ids = {_text(task.get("task_id")) for task in task_store if _text(task.get("task_id"))}
    lease_store = _as_dict(ctx.json("phases/ic_task_leases.json"))
    for claim in _as_list(lease_store.get("claims")):
        task_key = _text(_as_dict(claim).get("task_key"))
        if not task_key or not any(f":{task_id}:" in task_key for task_id in task_ids):
            tasks.append(f"lease:{task_key or '<missing>'}")

    handoff_store = ctx.json("phases/ic_agent_handoffs.json")
    if handoff_store is not None:
        handoff_store = _as_dict(handoff_store)
        handoffs = [item for item in _as_list(handoff_store.get("handoffs")) if isinstance(item, dict)]
        handoff_ids = {_text(item.get("handoff_id")) for item in handoffs if _text(item.get("handoff_id"))}
        if handoff_store.get("schema_version") != "siq_ic_agent_handoffs_v1":
            artifacts.append("phases/ic_agent_handoffs.json#invalid-schema")
        artifacts.extend(
            f"phases/ic_agent_handoffs.json#{_text(item.get('handoff_id')) or '<missing>'}"
            for item in handoffs
            if _text(item.get("phase")) not in allowed_phases
        )
        payloads = handoff_store.get("payloads")
        if not isinstance(payloads, dict):
            artifacts.append("phases/ic_agent_handoffs.json#invalid-payloads")
        else:
            artifacts.extend(
                f"phases/ic_agent_handoffs.json#orphan-payload:{handoff_id}"
                for handoff_id in payloads
                if _text(handoff_id) not in handoff_ids
            )
    return sorted(set(artifacts)), sorted(set(tasks))


def _path_r0_blocked(ctx: EvaluationContext) -> list[dict[str, Any]]:
    readiness = _as_dict(ctx.json("phases/r0_readiness.json"))
    workflow = _as_dict(ctx.json("phases/workflow_state.json"))
    smoke = _as_dict(ctx.json("release/real_smoke.json"))
    gaps = _known_critical_gaps(ctx)
    route = _early_insufficient_terminal_route(ctx)
    r0_phase = _workflow_phase(workflow, "R0")
    smoke_r0 = _as_dict(_as_dict(smoke.get("phase_runs")).get("R0"))
    admitted_with_limits = (
        readiness.get("readiness") == "ready"
        and not _as_list(readiness.get("blocking_reasons"))
        and bool(_as_list(readiness.get("evidence_gaps")))
        and bool(gaps)
        and r0_phase.get("status") == "completed"
        and smoke_r0.get("status") == "passed"
        and smoke_r0.get("workflow_advanced") is True
    )
    return [
        _assertion("R0.limited_schema", readiness.get("schema_version"), "siq_ic_r0_readiness_v1"),
        _assertion("R0.limited_snapshot", readiness.get("evidence_snapshot_hash"), ctx.snapshot_hash),
        _assertion("R0.limited_model", readiness.get("generation_mode"), "model"),
        _assertion(
            "R0.limited_task_binding",
            _artifact_is_bound_to_task(ctx, readiness, phase="R0", role="siq_ic_master_coordinator"),
        ),
        _assertion("R0.explicit_critical_gaps", bool(gaps)),
        _assertion("R0.blocked_or_admitted_with_limits", route == "r0_blocked" or admitted_with_limits),
        *_task_role_assertions(ctx, "R0", {"siq_ic_master_coordinator"}),
    ]


def _path_claim_restriction(ctx: EvaluationContext) -> list[dict[str, Any]]:
    known = _evidence_ids(ctx)
    reports = _all_reports(ctx)
    claims = [claim for report in reports for claim in _as_list(report.get("claims")) if isinstance(claim, dict)]
    invalid: list[str] = []
    restricted: list[str] = []
    for claim in claims:
        claim_id = _text(claim.get("claim_id")) or "<missing>"
        status = _text(claim.get("status"))
        evidence = _ids(claim.get("evidence_ids"))
        if status in {"verified", "derived"} and (not evidence or not evidence <= known):
            invalid.append(claim_id)
        if status == "derived" and not _as_list(claim.get("calculation_trace_ids")):
            invalid.append(claim_id)
        if status in {"missing", "assumed"}:
            restricted.append(claim_id)
    recommendations = {_text(report.get("recommendation")) for report in reports}
    early_route = _early_insufficient_terminal_route(ctx)
    r1_5_lineage_errors = _r1_5_terminal_lineage_errors(ctx) if _needs_more_evidence_disputes(ctx) else []
    readiness = _as_dict(ctx.json("phases/r0_readiness.json"))
    explicit_restriction = bool(restricted) or (
        early_route == "r0_blocked" and bool(_as_list(readiness.get("evidence_gaps")))
    )
    unexpected_artifacts, unexpected_tasks = _unexpected_downstream_outputs(ctx, early_route) if early_route else ([], [])
    return [
        _assertion("claim_restriction.no_unbound_material_claims", sorted(set(invalid)), []),
        _assertion("claim_restriction.explicit_restriction", explicit_restriction),
        _assertion("claim_restriction.r1_5_terminal_lineage", r1_5_lineage_errors, []),
        _assertion(
            "claim_restriction.insufficient_terminal",
            "insufficient_evidence" in recommendations or early_route in {"r0_blocked", "r1_5_blocked"},
        ),
        _assertion("claim_restriction.no_illegal_downstream_artifacts", unexpected_artifacts, []),
        _assertion("claim_restriction.no_illegal_downstream_tasks", unexpected_tasks, []),
    ]


def _path_r4_insufficient(ctx: EvaluationContext) -> list[dict[str, Any]]:
    # Keep the published path id, but never manufacture R4 after R0/R1.5 has
    # lawfully returned the workflow to the Evidence loop.
    r4 = _r4(ctx)
    if r4:
        return [
            *_path_r2(ctx),
            *_path_r3_short_or_full(ctx),
            *_path_r4(ctx),
            _assertion("R4.insufficient_recommendation", r4.get("recommendation"), "insufficient_evidence"),
            _assertion("R4.insufficient_decision", r4.get("decision"), "insufficient_evidence"),
        ]
    early_route = _early_insufficient_terminal_route(ctx)
    unexpected_artifacts, unexpected_tasks = _unexpected_downstream_outputs(ctx, early_route) if early_route else ([], [])
    return [
        _assertion("R4.early_terminal_route", early_route in {"r0_blocked", "r1_5_blocked"}),
        _assertion("R4.absent_after_early_terminal", bool(r4), False),
        _assertion("R4.no_illegal_downstream_artifacts", unexpected_artifacts, []),
        _assertion("R4.no_illegal_downstream_tasks", unexpected_tasks, []),
    ]


def _recommendation_bucket(value: Any) -> str:
    recommendation = _text(value).lower()
    if recommendation in {"support", "pass", "conditional_pass", "conditional_support", "go"}:
        return "positive"
    if recommendation in {"reject", "no_go", "pass_on", "caution", "insufficient_evidence"}:
        return "negative"
    if recommendation in {"review", "hold", "needs_review", "revise"}:
        return "review"
    return ""


def _position_score(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(_text(value))
    except ValueError:
        return None


def _resolved_contested_disputes(ctx: EvaluationContext) -> list[dict[str, Any]]:
    disputes = _as_list(_as_dict(ctx.json("phases/r1_5_disputes.json")).get("disputes"))
    contested: list[dict[str, Any]] = []
    for dispute in disputes:
        if not isinstance(dispute, dict):
            continue
        if _text(dispute.get("severity")).lower() not in {"high", "critical"}:
            continue
        ruling = _as_dict(dispute.get("chairman_ruling"))
        ruling_value = _text(ruling.get("ruling") or ruling.get("decision")).lower()
        if (
            dispute.get("resolved") is not True
            or ruling.get("resolved") is not True
            or ruling_value in {"needs_more_evidence", "unresolved", "insufficient_evidence"}
            or not _text(ruling.get("rationale"))
        ):
            continue
        positions = [item for item in _as_list(dispute.get("positions")) if isinstance(item, dict)]
        agents = {_text(item.get("agent_id")) for item in positions if _text(item.get("agent_id"))}
        if len(positions) < 2 or len(agents) < 2:
            continue
        detection_rules = _ids(dispute.get("detection_rules"))
        recommendation_buckets = {
            bucket
            for item in positions
            if (bucket := _recommendation_bucket(item.get("recommendation")))
        }
        scores = [score for item in positions if (score := _position_score(item.get("score"))) is not None]
        recommendation_tradeoff = (
            "recommendation_bucket_divergence" in detection_rules and len(recommendation_buckets) > 1
        )
        score_tradeoff = (
            "score_spread_threshold" in detection_rules
            and len(scores) >= 2
            and max(scores) - min(scores) >= 20
        )
        if recommendation_tradeoff or score_tradeoff:
            contested.append(dispute)
    return contested


def _path_r1_5_resolved_contested(ctx: EvaluationContext) -> list[dict[str, Any]]:
    disputes_payload = _as_dict(ctx.json("phases/r1_5_disputes.json"))
    disputes = [item for item in _as_list(disputes_payload.get("disputes")) if isinstance(item, dict)]
    contested = _resolved_contested_disputes(ctx)
    contested_ids = [_text(item.get("dispute_id")) for item in contested]
    unresolved_ids = [_text(item.get("dispute_id")) or "<missing>" for item in disputes if item.get("resolved") is not True]
    invalid_resolved_rulings = []
    for item in disputes:
        ruling = _as_dict(item.get("chairman_ruling"))
        ruling_value = _text(ruling.get("ruling") or ruling.get("decision")).lower()
        if (
            ruling.get("resolved") is not True
            or ruling_value in {"needs_more_evidence", "unresolved", "insufficient_evidence"}
            or not _text(ruling.get("rationale"))
        ):
            invalid_resolved_rulings.append(_text(item.get("dispute_id")) or "<missing>")
    known_evidence = _evidence_ids(ctx)
    unbound_rulings = [
        _text(item.get("dispute_id")) or "<missing>"
        for item in contested
        if not _ids(_as_dict(item.get("chairman_ruling")).get("evidence_ids"))
        or not _ids(_as_dict(item.get("chairman_ruling")).get("evidence_ids")) <= known_evidence
    ]
    workflow = _as_dict(ctx.json("phases/workflow_state.json"))
    smoke = _as_dict(ctx.json("release/real_smoke.json"))
    smoke_phases = _as_dict(smoke.get("phase_runs"))
    r3 = _as_dict(ctx.json("phases/r3_reports.json"))
    return [
        *_path_r1_5(ctx),
        _assertion("R1.5.material_resolved_contested", bool(contested_ids)),
        _assertion("R1.5.all_disputes_resolved", unresolved_ids, []),
        _assertion("R1.5.all_resolved_rulings_formal", invalid_resolved_rulings, []),
        _assertion("R1.5.all_ruling_lineage", _r1_5_ruling_lineage_errors(ctx, disputes), []),
        _assertion("R1.5.contested_ruling_evidence", unbound_rulings, []),
        _assertion("R1.5.workflow_completed", _workflow_phase(workflow, "R1.5").get("status"), "completed"),
        _assertion("R1.5.smoke_advanced", _as_dict(smoke_phases.get("R1.5")).get("workflow_advanced"), True),
        _assertion("R1.5.R2_completed", _workflow_phase(workflow, "R2").get("status"), "completed"),
        _assertion("R1.5.R2_smoke_advanced", _as_dict(smoke_phases.get("R2")).get("workflow_advanced"), True),
        _assertion("R1.5.R3_completed", _workflow_phase(workflow, "R3").get("status"), "completed"),
        _assertion("R1.5.R3_smoke_advanced", _as_dict(smoke_phases.get("R3")).get("workflow_advanced"), True),
        _assertion("R1.5.R3_full", r3.get("mode"), "full"),
    ]


def _path_r2_delta(ctx: EvaluationContext) -> list[dict[str, Any]]:
    reports = _as_dict(ctx.json("phases/r2_reports.json"))
    contract_roles: set[str] = set()
    delta_roles: set[str] = set()
    for role in R2_ROLES:
        report = _as_dict(reports.get(role))
        if report.get("revision_contract_schema_version") == "siq_ic_r2_revision_v1":
            contract_roles.add(role)
        if isinstance(report.get("score_change"), (int, float)) and any(
            isinstance(report.get(key), list)
            for key in ("changed_claims", "unchanged_claims", "new_evidence_ids", "remaining_questions")
        ):
            delta_roles.add(role)
    return [
        *_path_r2(ctx),
        _assertion("R2.delta_contract_roles", sorted(contract_roles), sorted(R2_ROLES)),
        _assertion("R2.delta_fields_roles", sorted(delta_roles), sorted(R2_ROLES)),
    ]


def _debate_turns(ctx: EvaluationContext) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return [
        (debate, turn)
        for debate in _r3_debates(ctx)
        for turn in _as_list(debate.get("rounds"))
        if isinstance(turn, dict)
    ]


def _valid_turn(turn: Mapping[str, Any], known_evidence: set[str]) -> bool:
    evidence = _ids(turn.get("evidence_ids"))
    return (
        bool(_text(turn.get("argument_id")))
        and isinstance(turn.get("round"), int)
        and bool(_text(turn.get("speaker")))
        and bool(_text(turn.get("argument")))
        and bool(evidence)
        and evidence <= known_evidence
    )


def _path_r3_team(ctx: EvaluationContext, team: str) -> list[dict[str, Any]]:
    known = _evidence_ids(ctx)
    turns = [
        turn
        for debate, turn in _debate_turns(ctx)
        if _text(turn.get("speaker")) in _ids(debate.get(team)) and _valid_turn(turn, known)
    ]
    return [
        *_path_r3_full(ctx),
        _assertion(f"R3.{team}.evidence_bound_turn", len(turns) > 0),
    ]


def _path_r3_red(ctx: EvaluationContext) -> list[dict[str, Any]]:
    return _path_r3_team(ctx, "red_team")


def _path_r3_blue(ctx: EvaluationContext) -> list[dict[str, Any]]:
    return _path_r3_team(ctx, "blue_team")


def _path_r3_rebuttal(ctx: EvaluationContext) -> list[dict[str, Any]]:
    known = _evidence_ids(ctx)
    turns = [turn for _, turn in _debate_turns(ctx)]
    ids = {_text(turn.get("argument_id")) for turn in turns}
    rebuttals = [
        turn
        for turn in turns
        if _valid_turn(turn, known)
        and bool(_ids(turn.get("responds_to_argument_ids")))
        and _ids(turn.get("responds_to_argument_ids")) <= ids
    ]
    return [
        *_path_r3_full(ctx),
        _assertion("R3.rebuttal.linked_turn", len(rebuttals) > 0),
    ]


def _path_r3_verdict(ctx: EvaluationContext) -> list[dict[str, Any]]:
    valid = 0
    for debate in _r3_debates(ctx):
        argument_ids = {_text(turn.get("argument_id")) for turn in _as_list(debate.get("rounds")) if isinstance(turn, dict)}
        verdict = _as_dict(debate.get("chairman_verdict"))
        ruled = _ids(verdict.get("accepted_argument_ids")) | _ids(verdict.get("rejected_argument_ids"))
        if (
            bool(_text(verdict.get("ruling")))
            and bool(_text(verdict.get("rationale")))
            and bool(_text(verdict.get("decision_impact")))
            and bool(ruled)
            and ruled <= argument_ids
        ):
            valid += 1
    return [
        *_path_r3_full(ctx),
        _assertion("R3.verdict.bound_arguments", valid, len(_r3_debates(ctx))),
    ]


def _snapshot_change_event(ctx: EvaluationContext) -> dict[str, Any]:
    for relative in ("audit/audit_log.json", "phases/audit_log.json"):
        events = _as_list(_as_dict(ctx.json(relative)).get("events"))
        for event in reversed(events):
            if (
                isinstance(event, dict)
                and event.get("event_type") == "deal_evidence_snapshot_changed"
                and event.get("snapshot_hash") == ctx.snapshot_hash
                and DIGEST_RE.fullmatch(_text(event.get("previous_snapshot_hash")))
                and event.get("previous_snapshot_hash") != ctx.snapshot_hash
            ):
                return event
    return {}


def _path_source_activation(ctx: EvaluationContext) -> list[dict[str, Any]]:
    snapshot = _as_dict(ctx.json("evidence/evidence_snapshot.json"))
    registry = _as_dict(ctx.json("sources/analysis_sources.json"))
    active = _ids(snapshot.get("source_ids"))
    registered = {
        _text(source.get("source_id"))
        for source in _as_list(registry.get("sources"))
        if isinstance(source, dict) and _text(source.get("status")) not in {"disabled", "inactive", "failed"}
    }
    return [
        _assertion("source_activation.current_sources", bool(active)),
        _assertion("source_activation.registry_contains_snapshot", active <= registered),
    ]


def _path_snapshot_change(ctx: EvaluationContext) -> list[dict[str, Any]]:
    event = _snapshot_change_event(ctx)
    return [
        _assertion("snapshot_change.audited", bool(event)),
        _assertion("snapshot_change.current_hash", event.get("snapshot_hash"), ctx.snapshot_hash),
        _assertion("snapshot_change.distinct_previous", event.get("previous_snapshot_hash") != ctx.snapshot_hash),
    ]


def _stale_receipts(ctx: EvaluationContext) -> list[dict[str, Any]]:
    receipts = _as_dict(_as_dict(ctx.json("phases/startup_receipts.json")).get("agents"))
    return [
        receipt
        for receipt in receipts.values()
        if isinstance(receipt, dict)
        and receipt.get("readiness_status") == "stale"
        and receipt.get("current_evidence_snapshot_hash") == ctx.snapshot_hash
        and receipt.get("evidence_snapshot_hash") != ctx.snapshot_hash
        and receipt.get("stale_reason") == "evidence_snapshot_changed"
    ]


def _path_receipt_stale(ctx: EvaluationContext) -> list[dict[str, Any]]:
    return [_assertion("receipt_stale.explicit_stale_receipt", len(_stale_receipts(ctx)) > 0)]


def _path_workflow_block(ctx: EvaluationContext) -> list[dict[str, Any]]:
    workflow = _as_dict(ctx.json("phases/workflow_state.json"))
    return [
        _assertion("workflow_block.status", workflow.get("status"), "decision_review_required"),
        _assertion("workflow_block.flag", workflow.get("decision_review_required"), True),
        _assertion("workflow_block.reason", workflow.get("decision_review_reason"), "evidence_snapshot_changed"),
        _assertion("workflow_block.current_snapshot", workflow.get("current_evidence_snapshot_hash"), ctx.snapshot_hash),
    ]


def _path_decision_review_required(ctx: EvaluationContext) -> list[dict[str, Any]]:
    workflow = _as_dict(ctx.json("phases/workflow_state.json"))
    project = _as_dict(ctx.json("project_meta.json"))
    r4 = _r4(ctx)
    previous = workflow.get("confirmed_decision_snapshot_hash")
    return [
        *_path_workflow_block(ctx),
        _assertion("decision_review.project_flag", project.get("decision_review_required"), True),
        _assertion("decision_review.project_reason", project.get("decision_review_reason"), "evidence_snapshot_changed"),
        _assertion("decision_review.previous_snapshot", previous, r4.get("evidence_snapshot_hash")),
        _assertion("decision_review.snapshot_changed", previous != ctx.snapshot_hash),
        _assertion(
            "decision_review.prior_confirmation",
            _as_dict(r4.get("human_confirmation")).get("status") in {"confirmed", "overridden", "approved"},
        ),
    ]


def _role_receipts(ctx: EvaluationContext) -> dict[str, dict[str, Any]]:
    payload = _as_dict(ctx.json("phases/startup_receipts.json"))
    agents = _as_dict(payload.get("agents"))
    return {key: value for key, value in agents.items() if key in PROFILE_IDS and isinstance(value, dict)}


def _path_shared_retrieval(ctx: EvaluationContext) -> list[dict[str, Any]]:
    receipts = _role_receipts(ctx)
    valid = {
        role
        for role, receipt in receipts.items()
        if receipt.get("schema_version") == "siq_ic_startup_receipt_v2"
        and receipt.get("evidence_snapshot_hash") == ctx.snapshot_hash
        and bool(_as_list(receipt.get("project_evidence_hits")))
        and bool(_text(receipt.get("shared_collection")))
    }
    return [_assertion("routing.shared_roles", sorted(valid), sorted(PROFILE_IDS))]


def _path_private_retrieval(ctx: EvaluationContext) -> list[dict[str, Any]]:
    receipts = _role_receipts(ctx)
    valid = {
        role
        for role, receipt in receipts.items()
        if bool(_text(receipt.get("private_collection"))) and bool(_as_list(receipt.get("methodology_refs")))
    }
    return [_assertion("routing.private_roles", sorted(valid), sorted(PROFILE_IDS))]


def _path_source_classification(ctx: EvaluationContext) -> list[dict[str, Any]]:
    receipts = _role_receipts(ctx)
    valid = set()
    for role, receipt in receipts.items():
        evidence = _as_list(receipt.get("project_evidence_hits"))
        methodology = _as_list(receipt.get("methodology_refs"))
        if evidence and methodology and all(
            isinstance(item, dict) and item.get("source_class") == "project_evidence" for item in evidence
        ) and all(
            isinstance(item, dict)
            and item.get("source_class") == "background_knowledge"
            and item.get("usage") == "methodology"
            for item in methodology
        ):
            valid.add(role)
    return [_assertion("routing.classified_roles", sorted(valid), sorted(PROFILE_IDS))]


def _path_degraded_reasons(ctx: EvaluationContext) -> list[dict[str, Any]]:
    receipts = _role_receipts(ctx)
    explicit = set()
    consistent = set()
    for role, receipt in receipts.items():
        gate = _as_dict(receipt.get("gate"))
        degraded = receipt.get("degraded_reasons")
        blockers = gate.get("blocking_reasons")
        if isinstance(degraded, list) and isinstance(blockers, list):
            explicit.add(role)
            if gate.get("allowed_to_speak") is (not bool(blockers)):
                consistent.add(role)
    return [
        _assertion("routing.explicit_degraded_reasons", sorted(explicit), sorted(PROFILE_IDS)),
        _assertion("routing.gate_reason_consistency", sorted(consistent), sorted(PROFILE_IDS)),
    ]


PATH_EVALUATORS: dict[str, PathEvaluator] = {
    "R0": _path_r0,
    "R1A": _path_r1a,
    "R1B": _path_r1b,
    "R1.5": _path_r1_5,
    "R2": _path_r2,
    "R3-short-or-full": _path_r3_short_or_full,
    "R4": _path_r4,
    "factcheck": _path_factcheck,
    "human-confirmation": _path_human_confirmation,
    "R1-finance-or-legal": _path_material_risk,
    "R3-full": _path_r3_full,
    "R4-non-pass": _path_r4_non_pass,
    "R0-block-or-degraded": _path_r0_blocked,
    "claim-restriction": _path_claim_restriction,
    "R4-insufficient-evidence": _path_r4_insufficient,
    "R1.5-resolved-contested": _path_r1_5_resolved_contested,
    "R2-delta": _path_r2_delta,
    "R3-red": _path_r3_red,
    "R3-blue": _path_r3_blue,
    "R3-rebuttal": _path_r3_rebuttal,
    "R3-verdict": _path_r3_verdict,
    "source-activation": _path_source_activation,
    "snapshot-change": _path_snapshot_change,
    "receipt-stale": _path_receipt_stale,
    "workflow-block": _path_workflow_block,
    "decision-review-required": _path_decision_review_required,
    "shared-retrieval": _path_shared_retrieval,
    "private-retrieval": _path_private_retrieval,
    "source-classification": _path_source_classification,
    "degraded-and-block-reasons": _path_degraded_reasons,
}


def _manifest_case(manifest_path: Path, case_id: str) -> tuple[dict[str, Any], list[str]]:
    manifest = _as_dict(_read_json(manifest_path))
    errors: list[str] = []
    if manifest.get("schema_version") != "siq_ic_golden_case_manifest_v1":
        errors.append("manifest_schema_invalid")
    if manifest.get("acceptance_status") != "candidates_only":
        errors.append("manifest_acceptance_status_invalid")
    if manifest.get("quality_accepted") is not False:
        errors.append("manifest_quality_accepted_must_remain_false")
    cases = [item for item in _as_list(manifest.get("cases")) if isinstance(item, dict)]
    case_ids = [_text(item.get("case_id")) for item in cases]
    if not case_ids or any(not value for value in case_ids) or len(case_ids) != len(set(case_ids)):
        errors.append("manifest_case_ids_invalid")
    matching = [item for item in cases if item.get("case_id") == case_id]
    case = matching[0] if len(matching) == 1 else {}
    if len(matching) != 1:
        errors.append("manifest_case_missing")
    elif case.get("status") != "candidate" or case.get("quality_accepted") is not False:
        errors.append("manifest_case_not_candidate")
    if case and not _text(case.get("known_gap")):
        errors.append("manifest_case_known_gap_missing")
    paths = [_text(item) for item in _as_list(case.get("required_paths")) if _text(item)]
    if not paths or len(paths) != len(set(paths)):
        errors.append("manifest_required_paths_invalid")
    unsupported = sorted(set(paths) - set(PATH_EVALUATORS))
    if unsupported:
        errors.append(f"unsupported_paths:{','.join(unsupported)}")
    return case, errors


def _identity(bundle: Path, case_id: str) -> tuple[dict[str, str], list[str]]:
    errors: list[str] = []
    if not bundle.is_dir():
        errors.append("bundle_not_directory")
    manifest = _as_dict(_read_json(bundle / "manifest.json"))
    snapshot = _as_dict(_read_json(bundle / "evidence/evidence_snapshot.json"))
    smoke = _as_dict(_read_json(bundle / "release/real_smoke.json"))
    deal_id = _text(manifest.get("deal_id"))
    snapshot_hash = _text(snapshot.get("snapshot_hash")).lower()
    run_id = _text(smoke.get("run_id"))
    if manifest.get("schema_version") != "siq_deal_manifest_v1" or not deal_id:
        errors.append("deal_manifest_invalid")
    if bundle.name != deal_id:
        errors.append("bundle_deal_id_mismatch")
    if snapshot.get("schema_version") != "siq_deal_evidence_snapshot_v1":
        errors.append("evidence_snapshot_schema_invalid")
    if snapshot.get("deal_id") != deal_id or not DIGEST_RE.fullmatch(snapshot_hash):
        errors.append("evidence_snapshot_identity_invalid")
    if smoke.get("schema_version") != "siq_ic_real_smoke_result_v1":
        errors.append("real_smoke_schema_invalid")
    if smoke.get("deal_id") != deal_id or smoke.get("execution_mode") != "real" or smoke.get("hermes_called") is not True:
        errors.append("real_smoke_not_real_execution")
    if not run_id:
        errors.append("real_smoke_run_id_missing")
    if case_id == "GOLDEN-PMIC-SNAPSHOT-STALE":
        smoke_snapshot = _text(smoke.get("evidence_snapshot_hash")).lower()
        if not DIGEST_RE.fullmatch(smoke_snapshot) or smoke_snapshot == snapshot_hash:
            errors.append("stale_case_requires_prior_smoke_snapshot")
    elif _text(smoke.get("evidence_snapshot_hash")).lower() != snapshot_hash:
        errors.append("real_smoke_snapshot_mismatch")
    return {"deal_id": deal_id, "snapshot_hash": snapshot_hash, "run_id": run_id}, errors


def _path_filename(required_path: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", required_path).strip("-").lower()
    return f"evaluation/golden/{slug}.json"


def _result_id(case_id: str, deal_id: str, run_id: str, snapshot_hash: str) -> str:
    digest = _payload_digest(
        {
            "case_id": case_id,
            "deal_id": deal_id,
            "run_id": run_id,
            "snapshot_hash": snapshot_hash,
            "evaluator": EVALUATOR_VERSION,
        }
    )
    return f"GOLDEN-RESULT-{digest[:24].upper()}"


def _compute_path(ctx: EvaluationContext, required_path: str) -> dict[str, Any]:
    ctx.sources.clear()
    evaluator = PATH_EVALUATORS.get(required_path)
    assertions = evaluator(ctx) if evaluator else [_assertion("path_supported", False)]
    assertions.append(_assertion("path.source_artifacts_observed", bool(ctx.sources)))
    passed = bool(assertions) and all(item.get("passed") is True for item in assertions)
    return {
        "schema_version": PATH_SCHEMA,
        "case_id": ctx.case_id,
        "required_path": required_path,
        "deal_id": ctx.deal_id,
        "run_id": ctx.run_id,
        "evidence_snapshot_hash": ctx.snapshot_hash,
        "status": "passed" if passed else "failed",
        "quality_accepted": False,
        "evaluator": {
            "name": EVALUATOR_NAME,
            "version": EVALUATOR_VERSION,
            "deterministic_checks": True,
        },
        "source_artifacts": ctx.source_rows(),
        "assertions": assertions,
    }


def evaluate_case(
    bundle: Path,
    case_id: str,
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    write: bool = True,
) -> dict[str, Any]:
    bundle = bundle.resolve()
    case, errors = _manifest_case(manifest_path, case_id)
    identity, identity_errors = _identity(bundle, case_id)
    errors.extend(identity_errors)
    ctx = EvaluationContext(bundle=bundle, case_id=case_id, **identity)
    effective_write = write and bundle.is_dir()
    path_results: dict[str, dict[str, Any]] = {}
    path_payloads: dict[str, dict[str, Any]] = {}
    for required_path in _as_list(case.get("required_paths")):
        required_path = _text(required_path)
        payload = _compute_path(ctx, required_path)
        relative = _path_filename(required_path)
        path_payloads[required_path] = payload
        if effective_write:
            _write_json(bundle / relative, payload)
            digest = _file_digest(bundle / relative)
        else:
            digest = _payload_digest(payload)
        path_results[required_path] = {
            "status": payload["status"],
            "artifact_path": relative,
            "artifact_sha256": digest,
        }
        if payload["status"] != "passed":
            errors.append(f"path_failed:{required_path}")
    result = {
        "schema_version": RESULT_SCHEMA,
        "case_id": case_id,
        "run_id": identity["run_id"],
        "result_id": _result_id(case_id, **identity),
        "deal_id": identity["deal_id"],
        "status": "passed" if not errors else "failed",
        "quality_accepted": False,
        "evidence_snapshot_hash": identity["snapshot_hash"],
        "evaluated_at": _utc_now(),
        "evaluator": {
            "name": EVALUATOR_NAME,
            "version": EVALUATOR_VERSION,
            "deterministic_checks": True,
        },
        "path_results": path_results,
        "errors": sorted(set(errors)),
    }
    if effective_write:
        _write_json(bundle / RESULT_PATH, result)
    return {"passed": not errors, "result": result, "path_payloads": path_payloads, "errors": result["errors"]}


def validate_candidate_result(
    bundle: Path,
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    result_path: Path | None = None,
) -> dict[str, Any]:
    bundle = bundle.resolve()
    errors: list[str] = []
    path = result_path.resolve() if result_path else (bundle / RESULT_PATH).resolve()
    try:
        path.relative_to(bundle)
    except ValueError:
        errors.append("result_path_outside_bundle")
        result: dict[str, Any] = {}
    else:
        result = _as_dict(_read_json(path))
        if not path.is_file():
            errors.append("result_missing_or_invalid")
    case_id = _text(result.get("case_id"))
    case, manifest_errors = _manifest_case(manifest_path, case_id)
    identity, identity_errors = _identity(bundle, case_id)
    errors.extend(manifest_errors + identity_errors)
    expected_fields = {
        "schema_version": RESULT_SCHEMA,
        "deal_id": identity["deal_id"],
        "run_id": identity["run_id"],
        "evidence_snapshot_hash": identity["snapshot_hash"],
        "result_id": _result_id(case_id, **identity),
        "quality_accepted": False,
    }
    for key, expected in expected_fields.items():
        if result.get(key) != expected:
            errors.append(f"result_{key}_mismatch")
    evaluator = _as_dict(result.get("evaluator"))
    if evaluator != {"name": EVALUATOR_NAME, "version": EVALUATOR_VERSION, "deterministic_checks": True}:
        errors.append("result_evaluator_invalid")
    if not _valid_timestamp(result.get("evaluated_at")):
        errors.append("result_evaluated_at_invalid")
    if _as_list(result.get("errors")):
        errors.append("result_contains_errors")
    expected_paths = {_text(item) for item in _as_list(case.get("required_paths"))}
    observed_paths = set(_as_dict(result.get("path_results")))
    if observed_paths != expected_paths:
        errors.append("result_required_paths_mismatch")
    ctx = EvaluationContext(bundle=bundle, case_id=case_id, **identity)
    for required_path in sorted(expected_paths):
        row = _as_dict(_as_dict(result.get("path_results")).get(required_path))
        expected_relative = _path_filename(required_path)
        artifact = _contained_path(bundle, row.get("artifact_path"))
        if row.get("artifact_path") != expected_relative or artifact is None or not artifact.is_file():
            errors.append(f"path_artifact_invalid:{required_path}")
            continue
        digest = _text(row.get("artifact_sha256"))
        if not DIGEST_RE.fullmatch(digest) or _file_digest(artifact) != digest:
            errors.append(f"path_artifact_digest_mismatch:{required_path}")
            continue
        stored = _as_dict(_read_json(artifact))
        fresh = _compute_path(ctx, required_path)
        for key in (
            "schema_version",
            "case_id",
            "required_path",
            "deal_id",
            "run_id",
            "evidence_snapshot_hash",
            "status",
            "quality_accepted",
            "evaluator",
            "source_artifacts",
            "assertions",
        ):
            if stored.get(key) != fresh.get(key):
                errors.append(f"path_recompute_mismatch:{required_path}:{key}")
        if row.get("status") != fresh.get("status") or fresh.get("status") != "passed":
            errors.append(f"path_not_passed:{required_path}")
    if result.get("status") != "passed" or result.get("quality_accepted") is not False:
        errors.append("result_not_passed_candidate")
    return {
        "passed": not errors,
        "case_id": case_id,
        "deal_id": identity["deal_id"],
        "run_id": identity["run_id"],
        "result_id": result.get("result_id"),
        "result_path": path,
        "result": result,
        "errors": sorted(set(errors)),
    }


def build_bindings(
    release_bundle: Path,
    case_bundles: Sequence[Path],
    *,
    suite_id: str,
    manifest_path: Path = DEFAULT_MANIFEST,
    output_path: Path | None = None,
) -> dict[str, Any]:
    release_bundle = release_bundle.resolve()
    suite_root = release_bundle.parent.resolve()
    errors: list[str] = []
    if not release_bundle.is_dir():
        errors.append("release_bundle_not_directory")
    validated: dict[str, dict[str, Any]] = {}
    for raw_bundle in case_bundles:
        bundle = raw_bundle.resolve()
        try:
            bundle_relative = bundle.relative_to(suite_root).as_posix()
        except ValueError:
            errors.append(f"case_bundle_outside_suite:{bundle}")
            continue
        if not bundle.is_dir():
            errors.append(f"case_bundle_invalid:{bundle_relative}")
            continue
        check = validate_candidate_result(bundle, manifest_path=manifest_path)
        case_id = _text(check.get("case_id"))
        if not check["passed"]:
            errors.extend(f"{case_id or bundle_relative}:{item}" for item in check["errors"])
            continue
        if case_id in validated:
            errors.append(f"duplicate_case:{case_id}")
            continue
        validated[case_id] = {**check, "bundle": bundle, "bundle_relative": bundle_relative}
    missing = sorted(INDEPENDENT_CASE_IDS - set(validated))
    extra = sorted(set(validated) - INDEPENDENT_CASE_IDS)
    errors.extend(f"required_case_missing:{case_id}" for case_id in missing)
    errors.extend(f"non_independent_case:{case_id}" for case_id in extra)
    for field_name in ("deal_id", "run_id", "result_id", "bundle_relative"):
        values = [_text(item.get(field_name)) for item in validated.values()]
        if len(values) != len(set(values)):
            errors.append(f"{field_name}_not_independent")
    if not _text(suite_id):
        errors.append("suite_id_missing")
    bindings = []
    for case_id in sorted(INDEPENDENT_CASE_IDS & set(validated)):
        item = validated[case_id]
        result_path = Path(item["result_path"])
        bindings.append(
            {
                "case_id": case_id,
                "run_id": item["run_id"],
                "result_id": item["result_id"],
                "deal_id": item["deal_id"],
                "bundle_path": item["bundle_relative"],
                "result_path": result_path.relative_to(item["bundle"]).as_posix(),
                "result_sha256": _file_digest(result_path),
            }
        )
    result_digests = [_text(item.get("result_sha256")) for item in bindings]
    if len(result_digests) != len(set(result_digests)):
        errors.append("result_sha256_not_independent")
    payload = {
        "schema_version": BINDINGS_SCHEMA,
        "suite_id": _text(suite_id),
        "status": "passed" if not errors else "failed",
        "quality_accepted": False,
        "generated_at": _utc_now(),
        "bindings": bindings,
        "errors": sorted(set(errors)),
    }
    canonical_destination = (release_bundle / BINDINGS_PATH).resolve()
    destination = output_path.resolve() if output_path else canonical_destination
    if destination != canonical_destination:
        payload["status"] = "failed"
        payload["errors"] = sorted(set([*payload["errors"], "bindings_output_must_be_canonical"]))
        destination = canonical_destination
    if release_bundle.is_dir():
        _write_json(destination, payload)
    errors = list(payload["errors"])
    return {"passed": not errors, "bindings": payload, "output_path": destination, "errors": payload["errors"]}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    subparsers = parser.add_subparsers(dest="command", required=True)

    evaluate = subparsers.add_parser("evaluate", help="evaluate one candidate Deal bundle")
    evaluate.add_argument("--bundle", type=Path, required=True)
    evaluate.add_argument("--case-id", required=True)

    validate = subparsers.add_parser("validate", help="recompute and validate one candidate result")
    validate.add_argument("--bundle", type=Path, required=True)
    validate.add_argument("--result", type=Path)

    bind = subparsers.add_parser("bind", help="bind five independent, validated candidate results")
    bind.add_argument("--release-bundle", type=Path, required=True)
    bind.add_argument("--case-bundle", type=Path, action="append", default=[])
    bind.add_argument("--suite-id", required=True)
    bind.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "evaluate":
        report = evaluate_case(args.bundle, args.case_id, manifest_path=args.manifest)
    elif args.command == "validate":
        report = validate_candidate_result(args.bundle, manifest_path=args.manifest, result_path=args.result)
    else:
        report = build_bindings(
            args.release_bundle,
            args.case_bundle,
            suite_id=args.suite_id,
            manifest_path=args.manifest,
            output_path=args.output,
        )
    printable = {key: value for key, value in report.items() if key not in {"path_payloads", "result"}}
    for key, value in list(printable.items()):
        if isinstance(value, Path):
            printable[key] = str(value)
    print(json.dumps(printable, ensure_ascii=False, indent=2, default=str))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
