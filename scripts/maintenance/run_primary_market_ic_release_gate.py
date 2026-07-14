#!/usr/bin/env python3
"""Fail-closed offline release gate for primary-market IC behavior artifacts.

The gate is intentionally read-only with respect to the golden-case manifest.
Passing means that a bundle is eligible for a separate governed promotion; it
never changes a candidate case to ``quality_accepted`` by itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPO_ROOT / "agents" / "hermes" / "profiles" / "siq_ic_shared" / "golden_case_manifest.json"
DEFAULT_PROFILE_MATRIX = REPO_ROOT / "agents" / "hermes" / "profiles" / "siq_ic_shared" / "ic_profile_matrix.json"
CONTRACTS_DIR = REPO_ROOT / "agents" / "hermes" / "profiles" / "siq_ic_shared" / "contracts"
REPORT_SCHEMA = "siq_primary_market_ic_behavior_release_gate_v3"

REQUIRED_GOLDEN_CASES: dict[str, dict[str, Any]] = {
    "conditional_support": {
        "case_id": "GOLDEN-PMIC-CONDITIONAL-SUPPORT",
        "required_paths": {"R4", "factcheck", "human-confirmation"},
    },
    "material_risk": {
        "case_id": "GOLDEN-PMIC-MATERIAL-RISK",
        "required_paths": {"R3-full", "R4-non-pass"},
    },
    "insufficient_evidence": {
        "case_id": "GOLDEN-PMIC-INSUFFICIENT-EVIDENCE",
        "required_paths": {"claim-restriction", "R4-insufficient-evidence"},
    },
    "full_r3": {
        "case_id": "GOLDEN-PMIC-FULL-R3",
        "required_paths": {"R3-red", "R3-blue", "R3-rebuttal", "R3-verdict"},
    },
    "snapshot_stale": {
        "case_id": "GOLDEN-PMIC-SNAPSHOT-STALE",
        "required_paths": {"snapshot-change", "receipt-stale", "workflow-block"},
    },
    "seven_profile_kb_routing": {
        "case_id": "GOLDEN-PMIC-ROLE-ROUTING",
        "required_paths": {"shared-retrieval", "private-retrieval", "source-classification"},
    },
}
REQUIRED_INDEPENDENT_GOLDEN_CASE_IDS = {
    "GOLDEN-PMIC-CONDITIONAL-SUPPORT",
    "GOLDEN-PMIC-MATERIAL-RISK",
    "GOLDEN-PMIC-INSUFFICIENT-EVIDENCE",
    "GOLDEN-PMIC-FULL-R3",
    "GOLDEN-PMIC-SNAPSHOT-STALE",
}

REQUIRED_ARTIFACTS: dict[str, tuple[str, ...]] = {
    "manifest": ("manifest.json",),
    "evidence_index": ("evidence/evidence_index.json",),
    "evidence_snapshot": ("evidence/evidence_snapshot.json",),
    "workflow": ("phases/workflow_state.json",),
    "startup_receipts": ("phases/startup_receipts.json",),
    "r0": ("phases/r0_readiness.json",),
    "r1": ("phases/r1_reports.json",),
    "r1_5": ("phases/r1_5_disputes.json",),
    "r2": ("phases/r2_reports.json",),
    "r3": ("phases/r3_reports.json",),
    "r4": ("phases/r4_decision.json", "decision/decision_payload.json"),
    "quality": ("decision/report_quality.json",),
    "decision_markdown": ("decision/IC_DECISION_REPORT.md",),
    "workflow_runs": ("phases/ic_workflow_runs.json",),
    "tasks": ("phases/ic_agent_tasks.json",),
    "handoffs": ("phases/ic_agent_handoffs.json",),
    "factcheck_task": ("decision/factcheck_task.json",),
    "phase_audit": ("phases/audit_log.json",),
    "durable_audit": ("audit/audit_log.json",),
    "golden_bindings": ("release/golden_case_bindings.json",),
}
EXPECTED_CONTAINER_SCHEMAS: dict[str, str] = {
    "manifest": "siq_deal_manifest_v1",
    "evidence_index": "siq_deal_evidence_index_v1",
    "evidence_snapshot": "siq_deal_evidence_snapshot_v1",
    "workflow": "siq_deal_workflow_state_v1",
    "startup_receipts": "siq_ic_startup_receipts_v2",
    "r0": "siq_ic_r0_readiness_v1",
    "r3": "siq_ic_r3_debate_bundle_v2",
    "r4": "siq_ic_r4_decision_v2",
    "quality": "siq_ic_report_quality_v1",
    "workflow_runs": "siq_ic_workflow_runs_v1",
    "tasks": "siq_ic_agent_tasks_v1",
    "handoffs": "siq_ic_agent_handoffs_v1",
    "factcheck_task": "siq_ic_factcheck_task_v1",
    "golden_bindings": "siq_ic_golden_case_bindings_v1",
}
EXPERT_REPORT_SCHEMA = "siq_ic_expert_report_v2"
STARTUP_RECEIPT_SCHEMA = "siq_ic_startup_receipt_v2"
R1_5_CONTAINER_SCHEMA = "siq_ic_disputes_v1"
R1_5_RULING_SCHEMA = "siq_deal_r1_5_dispute_ruling_v1"
R1_5_DISPUTE_SCHEMA = "siq_ic_r1_5_dispute_v1"
R2_REVISION_SCHEMA = "siq_ic_r2_revision_v1"
R3_PLAN_SCHEMA = "siq_ic_r3_plan_v1"
R3_DEBATE_SCHEMA = "siq_ic_r3_debate_v1"
FACTCHECK_SCHEMA = "siq_ic_report_factcheck_v1"
MODEL_EXECUTION_AUDIT_SCHEMA = "siq_ic_model_execution_audit_v1"
RUN_RUNTIME_SCHEMA = "hermes.run_runtime.v1"
GOLDEN_CASE_RESULT_SCHEMA = "siq_ic_golden_case_result_v1"
GOLDEN_PATH_EVALUATION_SCHEMA = "siq_ic_golden_path_evaluation_v1"
GOLDEN_EVALUATOR = {
    "name": "primary-market-ic-golden-evaluator",
    "version": "v1",
    "deterministic_checks": True,
}
HUMAN_APPROVAL_SCHEMA = "siq_ic_human_methodology_approval_v3"
SNAPSHOT_RE = re.compile(r"^[a-fA-F0-9]{64}$")
DIGEST_RE = re.compile(r"^[a-f0-9]{64}$")
TASK_ID_RE = re.compile(r"^ICTASK-[A-F0-9]{24}$")
HANDOFF_ID_RE = re.compile(r"^ICHANDOFF-[A-F0-9]{24}$")
RUNTIME_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,159}$")
REQUIRED_MODEL_PHASES = {"R0", "R1A", "R1B", "R1.5", "R2", "R3", "R4"}
EXPORTED_CONTRACT_IDS = frozenset(
    {
        "siq_ic_agent_handoff_v2",
        "siq_ic_agent_task_v2",
        "siq_ic_claim_v1",
        "siq_ic_expert_report_v2",
        "siq_ic_r0_readiness_v1",
        "siq_ic_r1_5_chairman_rulings_v2",
        "siq_ic_r1_5_dispute_v1",
        "siq_ic_r2_revision_v1",
        "siq_ic_r3_debate_turn_v1",
        "siq_ic_r3_debate_v1",
        "siq_ic_r3_debate_verdict_v1",
        "siq_ic_r3_plan_v1",
        "siq_ic_r4_decision_v2",
        "siq_ic_workflow_run_identity_v1",
        "siq_ic_workflow_run_v1",
    }
)

FACTCHECK_PATHS = (
    "decision/factcheck.json",
    "decision/factcheck_report.json",
    "factcheck/factcheck.json",
)
REAL_SMOKE_PATH = "release/real_smoke.json"
HUMAN_APPROVAL_PATH = "release/human_methodology_approval.json"
REQUIRED_PROFILE_IDS = {
    "siq_ic_master_coordinator",
    "siq_ic_chairman",
    "siq_ic_strategist",
    "siq_ic_sector_expert",
    "siq_ic_finance_auditor",
    "siq_ic_legal_scanner",
    "siq_ic_risk_controller",
}
R1_AGENT_IDS = REQUIRED_PROFILE_IDS - {"siq_ic_master_coordinator"}
R2_AGENT_IDS = R1_AGENT_IDS - {"siq_ic_chairman"}

PLACEHOLDER_PATTERNS = (
    re.compile(r"\b(?:TODO|TBD|FIXME)\b", re.IGNORECASE),
    re.compile(r"待补充|占位(?:符|内容)?|请参见(?:其他|上述|相关)?(?:文件|报告|章节)?"),
    re.compile(r"\bSee\s+(?:the\s+)?R[0-4](?:\.|\s)", re.IGNORECASE),
    re.compile(r"No\s+.+\s+available", re.IGNORECASE),
)
INTERNAL_PATH_PATTERNS = (
    re.compile(r"(?:^|[\s('`\"])/(?:home|tmp|var)/"),
    re.compile(r"\bfile://", re.IGNORECASE),
    re.compile(r"(?:^|[\s('`\"])(?:data/wiki|artifacts|var|phases|discussion)/"),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> tuple[Any, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, "missing"
    except json.JSONDecodeError as exc:
        return None, f"invalid_json:{exc.lineno}:{exc.colno}"
    except OSError as exc:
        return None, f"read_error:{exc.__class__.__name__}"


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=1)
def _contract_validators() -> dict[str, Draft202012Validator]:
    schemas: dict[str, dict[str, Any]] = {}
    for path in sorted(CONTRACTS_DIR.glob("siq_ic_*.schema.json")):
        payload, error = _read_json(path)
        if error or not isinstance(payload, dict):
            raise ValueError(f"exported_contract_invalid:{path.name}:{error or 'not_object'}")
        schema_id = str(payload.get("$id") or "").strip()
        if not schema_id or schema_id in schemas:
            raise ValueError(f"exported_contract_id_invalid:{path.name}:{schema_id or 'missing'}")
        try:
            Draft202012Validator.check_schema(payload)
        except SchemaError as exc:
            raise ValueError(f"exported_contract_schema_error:{schema_id}:{exc.message}") from exc
        schemas[schema_id] = payload
    observed = frozenset(schemas)
    if observed != EXPORTED_CONTRACT_IDS:
        missing = ",".join(sorted(EXPORTED_CONTRACT_IDS - observed)) or "none"
        unexpected = ",".join(sorted(observed - EXPORTED_CONTRACT_IDS)) or "none"
        raise ValueError(f"exported_contract_registry_mismatch:missing={missing}:unexpected={unexpected}")
    checker = FormatChecker()
    return {schema_id: Draft202012Validator(schema, format_checker=checker) for schema_id, schema in schemas.items()}


def _contract_projection(
    validators: Mapping[str, Draft202012Validator],
    schema_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    schema = validators[schema_id].schema
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    return {key: payload[key] for key in properties if key in payload}


def _contract_instance_errors(
    validators: Mapping[str, Draft202012Validator],
    schema_id: str,
    payload: Any,
    *,
    label: str,
    project: bool = False,
) -> list[str]:
    if not isinstance(payload, Mapping):
        return [f"contract:{schema_id}:{label}:not_object"]
    candidate = _contract_projection(validators, schema_id, payload) if project else dict(payload)
    errors: list[str] = []
    for error in sorted(validators[schema_id].iter_errors(candidate), key=lambda item: list(item.absolute_path)):
        location = ".".join(str(item) for item in error.absolute_path) or "$"
        errors.append(f"contract:{schema_id}:{label}:{location}:{error.message}")
    return errors


def _contained_path(root: Path, relative: Any) -> Path | None:
    text = str(relative or "").strip()
    if not text or Path(text).is_absolute():
        return None
    root = root.resolve()
    candidate = (root / text).resolve()
    if candidate == root or root not in candidate.parents:
        return None
    return candidate


def _raw_output_path(
    bundle: Path,
    *,
    task_id: str,
    hermes_run_id: str,
    relative: str,
) -> Path | None:
    path = _contained_path(bundle, relative)
    expected_parent = (bundle / "audit" / "ic_agent_outputs" / task_id).resolve()
    if path is None or path.parent != expected_parent:
        return None
    if path.name == f"{hermes_run_id}.txt":
        return path
    if re.fullmatch(rf"{re.escape(hermes_run_id)}-repair-[1-9][0-9]*\.txt", path.name):
        return path
    return None


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _nonempty(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, (str, Sequence, Mapping)):
        return bool(value)
    return True


def _walk(value: Any, path: str = "$") -> Iterator[tuple[str, Any]]:
    yield path, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, f"{path}[{index}]")


def _strings(value: Any) -> list[str]:
    result: list[str] = []
    for item in _as_list(value):
        if isinstance(item, dict):
            candidate = item.get("evidence_id") or item.get("id") or item.get("ref_id")
        else:
            candidate = item
        text = str(candidate or "").strip()
        if text:
            result.append(text)
    return result


def _r1_5_canonical_artifact(
    dispute: Mapping[str, Any],
    ruling: Mapping[str, Any],
) -> dict[str, Any]:
    def split_value(key: str, *outer_aliases: str) -> Any:
        if key in ruling:
            return ruling.get(key)
        for candidate in (key, *outer_aliases):
            if candidate in dispute:
                return dispute.get(candidate)
        return None

    return {
        "schema_version": R1_5_DISPUTE_SCHEMA,
        "dispute_id": split_value("dispute_id"),
        "workflow_run_id": split_value("workflow_run_id"),
        "deal_id": split_value("deal_id"),
        "evidence_snapshot_hash": split_value("evidence_snapshot_hash"),
        "question": split_value("question", "topic"),
        "severity": split_value("severity"),
        "positions": split_value("positions"),
        "evidence_ids": split_value("evidence_ids"),
        "counter_evidence_ids": split_value("counter_evidence_ids"),
        "ruling": ruling.get("ruling") or ruling.get("decision") or dispute.get("ruling"),
        "rationale": split_value("rationale"),
        "accepted_claim_ids": split_value("accepted_claim_ids"),
        "rejected_claim_ids": split_value("rejected_claim_ids"),
        "required_followups": split_value("required_followups"),
        "decision_impact": split_value("decision_impact"),
        # The canonical model artifact and the submission record are created at
        # different lifecycle points. source_created_at preserves the former;
        # created_at remains the submission timestamp.
        "created_at": ruling.get("source_created_at")
        or dispute.get("source_created_at")
        or dispute.get("created_at")
        or ruling.get("created_at"),
    }


def validate_golden_manifest(payload: Any) -> dict[str, Any]:
    errors: list[str] = []
    coverage: dict[str, dict[str, Any]] = {}
    if not isinstance(payload, dict):
        return {"passed": False, "errors": ["manifest_not_object"], "coverage": {}}
    cases = payload.get("cases") if isinstance(payload.get("cases"), list) else []
    by_id = {
        str(case.get("case_id")): case for case in cases if isinstance(case, dict) and str(case.get("case_id") or "")
    }
    if payload.get("acceptance_status") != "candidates_only":
        errors.append("manifest_acceptance_status_must_be_candidates_only")
    if payload.get("quality_accepted") is not False:
        errors.append("manifest_quality_accepted_must_remain_false")

    for scenario, rule in REQUIRED_GOLDEN_CASES.items():
        case_id = rule["case_id"]
        case = by_id.get(case_id)
        missing_paths: list[str] = []
        case_errors: list[str] = []
        if case is None:
            case_errors.append("case_missing")
        else:
            paths = {str(item) for item in _as_list(case.get("required_paths"))}
            missing_paths = sorted(rule["required_paths"] - paths)
            if case.get("status") != "candidate":
                case_errors.append("status_not_candidate")
            if case.get("quality_accepted") is not False:
                case_errors.append("quality_accepted_must_remain_false")
            if not str(case.get("known_gap") or "").strip():
                case_errors.append("known_gap_missing")
            if missing_paths:
                case_errors.append("required_paths_missing")
        coverage[scenario] = {
            "case_id": case_id,
            "covered": not case_errors,
            "required_paths": sorted(paths) if case is not None else [],
            "missing_paths": missing_paths,
            "errors": case_errors,
        }
        errors.extend(f"{case_id}:{error}" for error in case_errors)

    return {
        "passed": not errors,
        "acceptance_status": payload.get("acceptance_status"),
        "quality_accepted": payload.get("quality_accepted"),
        "case_count": len(cases),
        "coverage": coverage,
        "errors": errors,
    }


def _resolve_artifact(bundle: Path, candidates: Iterable[str]) -> tuple[str | None, Path | None]:
    for relative in candidates:
        path = _contained_path(bundle, relative)
        if path is not None and path.is_file():
            return relative, path
    return None, None


def _load_artifacts(bundle: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    artifacts: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for name, candidates in REQUIRED_ARTIFACTS.items():
        relative, path = _resolve_artifact(bundle, candidates)
        item: dict[str, Any] = {
            "path": relative or candidates[0],
            "available": path is not None,
            "valid_json": None,
            "payload": None,
        }
        if path is None:
            errors.append(f"artifact_missing:{name}")
        elif path.suffix == ".json":
            payload, error = _read_json(path)
            item["payload"] = payload
            item["valid_json"] = error is None
            if error:
                item["error"] = error
                errors.append(f"artifact_invalid:{name}:{error}")
        artifacts[name] = item
    return artifacts, errors


def _report_objects(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    candidate = payload.get("reports") if isinstance(payload.get("reports"), (dict, list)) else payload
    if isinstance(candidate, dict):
        return [item for item in candidate.values() if isinstance(item, dict)]
    return [item for item in candidate if isinstance(item, dict)]


def _exported_contract_metric(artifacts: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    try:
        validators = _contract_validators()
    except ValueError as exc:
        return {
            "passed": False,
            "registry_count": 0,
            "instance_counts": {},
            "errors": [str(exc)],
        }

    counts = {schema_id: 0 for schema_id in EXPORTED_CONTRACT_IDS}
    errors: list[str] = []

    def validate(schema_id: str, payload: Any, *, label: str, project: bool = False) -> None:
        counts[schema_id] += 1
        errors.extend(
            _contract_instance_errors(
                validators,
                schema_id,
                payload,
                label=label,
                project=project,
            )
        )

    task_store = artifacts.get("tasks", {}).get("payload")
    tasks = (
        [item for item in _as_list(task_store.get("tasks")) if isinstance(item, dict)]
        if isinstance(task_store, dict)
        else []
    )
    for task in tasks:
        task_id = str(task.get("task_id") or "missing")
        validate("siq_ic_agent_task_v2", task, label=task_id)
        if task.get("status") != "succeeded" or not isinstance(task.get("validated_output"), dict):
            continue
        output_schema = str(task.get("output_schema") or "")
        validated_output = task["validated_output"]
        if output_schema == R2_REVISION_SCHEMA:
            revision = {
                "schema_version": validated_output.get("revision_contract_schema_version"),
                "report": validated_output,
                **{
                    key: validated_output.get(key)
                    for key in (
                        "r1_score",
                        "r2_score",
                        "score_change",
                        "changed_claims",
                        "unchanged_claims",
                        "accepted_rulings",
                        "challenged_rulings",
                        "new_evidence_ids",
                        "closed_questions",
                        "remaining_questions",
                        "revision_rationale",
                    )
                },
            }
            validate(R2_REVISION_SCHEMA, revision, label=f"{task_id}:validated_output")
        elif output_schema == "siq_ic_r1_5_chairman_rulings_v2":
            item_schema = validators[output_schema].schema["properties"]["rulings"]["items"]
            item_fields = set(item_schema.get("properties") or {})
            rulings = validated_output.get("rulings")
            if isinstance(rulings, list):
                for index, ruling in enumerate(rulings):
                    validate(
                        R1_5_DISPUTE_SCHEMA,
                        ruling,
                        label=f"{task_id}:validated_output:{index}",
                        project=True,
                    )
            projected_rulings = (
                [{key: ruling[key] for key in item_fields if key in ruling} for ruling in rulings]
                if isinstance(rulings, list) and all(isinstance(ruling, Mapping) for ruling in rulings)
                else rulings
            )
            validate(
                output_schema,
                {"rulings": projected_rulings},
                label=f"{task_id}:validated_output",
            )
        elif output_schema in {
            "siq_ic_r0_readiness_v1",
            EXPERT_REPORT_SCHEMA,
            "siq_ic_r3_debate_turn_v1",
            "siq_ic_r3_debate_verdict_v1",
            "siq_ic_r4_decision_v2",
        }:
            validate(
                output_schema,
                validated_output,
                label=f"{task_id}:validated_output",
                project=output_schema in {"siq_ic_r3_debate_turn_v1", "siq_ic_r3_debate_verdict_v1"},
            )

    handoff_store = artifacts.get("handoffs", {}).get("payload")
    handoffs = (
        [item for item in _as_list(handoff_store.get("handoffs")) if isinstance(item, dict)]
        if isinstance(handoff_store, dict)
        else []
    )
    for handoff in handoffs:
        validate(
            "siq_ic_agent_handoff_v2",
            handoff,
            label=str(handoff.get("handoff_id") or "missing"),
        )

    workflow_store = artifacts.get("workflow_runs", {}).get("payload")
    workflow_runs = (
        [item for item in _as_list(workflow_store.get("runs")) if isinstance(item, dict)]
        if isinstance(workflow_store, dict)
        else []
    )
    for run in workflow_runs:
        run_id = str(run.get("workflow_run_id") or "missing")
        validate("siq_ic_workflow_run_v1", run, label=run_id)
        matching_task = next(
            (task for task in tasks if task.get("workflow_run_id") == run.get("workflow_run_id")),
            None,
        )
        identity = {
            "schema_version": "siq_ic_workflow_run_identity_v1",
            "workflow_run_id": run.get("workflow_run_id"),
            "deal_id": run.get("deal_id"),
            "research_identity": dict((matching_task or {}).get("research_identity") or {}),
            "evidence_snapshot_hash": run.get("evidence_snapshot_hash"),
            "source_ids": _strings(run.get("source_ids")),
            "profile_contract_version": (matching_task or {}).get("profile_contract_version"),
            "started_at": (matching_task or {}).get("started_at")
            or (matching_task or {}).get("created_at")
            or run.get("created_at"),
        }
        validate("siq_ic_workflow_run_identity_v1", identity, label=run_id)

    r0 = artifacts.get("r0", {}).get("payload")
    if isinstance(r0, dict):
        validate("siq_ic_r0_readiness_v1", r0, label="r0", project=True)

    for artifact_name in ("r1", "r2"):
        for report in _report_objects(artifacts.get(artifact_name, {}).get("payload")):
            report_id = str(report.get("report_id") or f"{artifact_name}:missing")
            validate("siq_ic_expert_report_v2", report, label=report_id)
            for claim in _as_list(report.get("claims")):
                if isinstance(claim, dict):
                    validate(
                        "siq_ic_claim_v1",
                        claim,
                        label=f"{report_id}:{claim.get('claim_id') or 'missing'}",
                    )
            if artifact_name == "r2":
                revision = {
                    "schema_version": report.get("revision_contract_schema_version"),
                    "report": report,
                    **{
                        key: report.get(key)
                        for key in (
                            "r1_score",
                            "r2_score",
                            "score_change",
                            "changed_claims",
                            "unchanged_claims",
                            "accepted_rulings",
                            "challenged_rulings",
                            "new_evidence_ids",
                            "closed_questions",
                            "remaining_questions",
                            "revision_rationale",
                        )
                    },
                }
                validate("siq_ic_r2_revision_v1", revision, label=report_id)

    r1_5 = artifacts.get("r1_5", {}).get("payload")
    if isinstance(r1_5, dict):
        raw_disputes = r1_5.get("disputes")
        disputes = raw_disputes if isinstance(raw_disputes, list) else _as_list(r1_5.get("rulings"))
        chairman_items: list[dict[str, Any]] = []
        chairman_item_schema = validators["siq_ic_r1_5_chairman_rulings_v2"].schema["properties"]["rulings"]["items"]
        chairman_item_fields = set(chairman_item_schema.get("properties") or {})
        for dispute in disputes:
            if not isinstance(dispute, dict):
                continue
            ruling = dispute.get("chairman_ruling") if isinstance(dispute.get("chairman_ruling"), dict) else dispute
            merged = _r1_5_canonical_artifact(dispute, ruling)
            dispute_id = str(merged.get("dispute_id") or "missing")
            validate("siq_ic_r1_5_dispute_v1", merged, label=dispute_id, project=True)
            chairman_items.append({key: merged[key] for key in chairman_item_fields if key in merged})
        if chairman_items:
            validate(
                "siq_ic_r1_5_chairman_rulings_v2",
                {"rulings": chairman_items},
                label="r1_5",
            )

    r3 = artifacts.get("r3", {}).get("payload")
    if isinstance(r3, dict):
        if isinstance(r3.get("plan"), dict):
            validate("siq_ic_r3_plan_v1", r3["plan"], label="r3_plan")
        for debate in _as_list(r3.get("debates")):
            if isinstance(debate, dict):
                validate(
                    "siq_ic_r3_debate_v1",
                    debate,
                    label=str(debate.get("debate_id") or "missing"),
                )

    for task in tasks:
        output_schema = str(task.get("output_schema") or "")
        if task.get("status") == "succeeded" and output_schema in {
            "siq_ic_r3_debate_turn_v1",
            "siq_ic_r3_debate_verdict_v1",
        }:
            validate(
                output_schema,
                task.get("validated_output"),
                label=str(task.get("task_id") or "missing"),
                project=True,
            )

    r4 = artifacts.get("r4", {}).get("payload")
    if isinstance(r4, dict):
        validate("siq_ic_r4_decision_v2", r4, label=str(r4.get("report_id") or "r4"))
        for claim in _as_list(r4.get("claims")):
            if isinstance(claim, dict):
                validate(
                    "siq_ic_claim_v1",
                    claim,
                    label=f"r4:{claim.get('claim_id') or 'missing'}",
                )

    for schema_id, count in counts.items():
        if count == 0:
            errors.append(f"contract:{schema_id}:instance_missing")
    return {
        "passed": not errors,
        "registry_count": len(validators),
        "instance_counts": counts,
        "errors": errors,
    }


def _schema_metric(artifacts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    observed: dict[str, list[str]] = {}
    for name, expected in EXPECTED_CONTAINER_SCHEMAS.items():
        item = artifacts.get(name, {})
        if not item.get("available") or not item.get("valid_json"):
            continue
        payload = item.get("payload")
        top_level_version = str(payload.get("schema_version") or "") if isinstance(payload, dict) else ""
        observed[name] = [top_level_version] if top_level_version else []
        if top_level_version != expected:
            errors.append(f"{name}:schema_version_mismatch:{top_level_version or 'missing'}")

    startup = artifacts.get("startup_receipts", {}).get("payload")
    if isinstance(startup, dict):
        rows = _routing_rows(startup)
        history = startup.get("by_agent_phase")
        if not isinstance(history, dict):
            errors.append("startup_receipts:by_agent_phase_missing")
            history = {}
        for profile_id in sorted(REQUIRED_PROFILE_IDS):
            receipt = rows.get(profile_id)
            if not isinstance(receipt, dict):
                continue
            version = str(receipt.get("schema_version") or "")
            observed.setdefault("startup_receipt", []).append(version)
            if version != STARTUP_RECEIPT_SCHEMA:
                errors.append(f"startup_receipts:{profile_id}:receipt_schema_version_mismatch:{version or 'missing'}")
            phases = history.get(profile_id)
            if not isinstance(phases, dict) or not phases:
                errors.append(f"startup_receipts:{profile_id}:phase_history_missing")
            elif any(
                not isinstance(item, dict) or item.get("schema_version") != STARTUP_RECEIPT_SCHEMA
                for item in phases.values()
            ):
                errors.append(f"startup_receipts:{profile_id}:phase_history_schema_version_mismatch")

    for name, expected_agents in (("r1", R1_AGENT_IDS), ("r2", R2_AGENT_IDS)):
        payload = artifacts.get(name, {}).get("payload")
        reports = _report_objects(payload)
        report_agents = {str(report.get("agent_id") or "") for report in reports}
        missing_agents = sorted(expected_agents - report_agents)
        unexpected_agents = sorted(report_agents - expected_agents)
        if not reports:
            errors.append(f"{name}:reports_missing")
        if len(reports) != len(expected_agents):
            errors.append(f"{name}:report_cardinality_invalid")
        if missing_agents:
            errors.append(f"{name}:required_agents_missing:{','.join(missing_agents)}")
        if unexpected_agents:
            errors.append(f"{name}:unexpected_agents:{','.join(unexpected_agents)}")
        for field in ("report_id", "task_id"):
            values = [str(report.get(field) or "") for report in reports]
            if not all(values) or len(values) != len(set(values)):
                errors.append(f"{name}:{field}s_not_unique")
        for report in reports:
            version = str(report.get("schema_version") or "")
            observed.setdefault(name, []).append(version)
            if version != EXPERT_REPORT_SCHEMA:
                errors.append(f"{name}:report_schema_version_mismatch:{version or 'missing'}")
            if str(report.get("generation_mode") or "") != "model":
                errors.append(f"{name}:{report.get('agent_id')}:generation_mode_not_model")
            expected_phase = (
                "R2"
                if name == "r2"
                else "R1B"
                if report.get("agent_id") in {"siq_ic_risk_controller", "siq_ic_chairman"}
                else "R1A"
            )
            if report.get("phase") != expected_phase:
                errors.append(f"{name}:{report.get('agent_id')}:phase_mismatch")
            if name == "r2" and report.get("revision_contract_schema_version") != R2_REVISION_SCHEMA:
                errors.append(f"r2:{report.get('agent_id')}:revision_schema_version_mismatch")

    r1_5 = artifacts.get("r1_5", {}).get("payload")
    if isinstance(r1_5, dict):
        container_version = str(r1_5.get("schema_version") or "")
        observed["r1_5_container"] = [container_version] if container_version else []
        if container_version != R1_5_CONTAINER_SCHEMA:
            errors.append(f"r1_5:container_schema_version_mismatch:{container_version or 'missing'}")
        disputes = r1_5.get("disputes")
        if not isinstance(disputes, list):
            disputes = r1_5.get("rulings") if isinstance(r1_5.get("rulings"), list) else []
        if not disputes:
            errors.append("r1_5:disputes_missing")
        for dispute in disputes:
            ruling = dispute.get("chairman_ruling") if isinstance(dispute, dict) else None
            version = str(ruling.get("schema_version") or "") if isinstance(ruling, dict) else ""
            observed.setdefault("r1_5", []).append(version)
            if version != R1_5_RULING_SCHEMA:
                errors.append(f"r1_5:ruling_schema_version_mismatch:{version or 'missing'}")
            if isinstance(ruling, dict) and (
                ruling.get("generation_mode") != "model" or not str(ruling.get("hermes_run_id") or "").strip()
            ):
                errors.append(f"r1_5:{dispute.get('dispute_id')}:formal_model_ruling_required")

    r3 = artifacts.get("r3", {}).get("payload")
    if isinstance(r3, dict):
        plan = r3.get("plan")
        if not isinstance(plan, dict) or plan.get("schema_version") != R3_PLAN_SCHEMA:
            errors.append("r3:plan_schema_version_mismatch")
        debates = r3.get("debates") if isinstance(r3.get("debates"), list) else []
        if not debates:
            errors.append("r3:debates_missing")
        elif any(
            not isinstance(debate, dict) or debate.get("schema_version") != R3_DEBATE_SCHEMA for debate in debates
        ):
            errors.append("r3:debate_schema_version_mismatch")
        observed["r3_plan"] = [str(plan.get("schema_version") or "")] if isinstance(plan, dict) else []
        observed["r3_debate"] = sorted(
            {str(debate.get("schema_version") or "") for debate in debates if isinstance(debate, dict)}
        )

    r4 = artifacts.get("r4", {}).get("payload")
    if isinstance(r4, dict):
        if str(r4.get("generation_mode") or "") != "model" or r4.get("hermes_called") is not True:
            errors.append("r4:formal_model_execution_required")
        if str(r4.get("agent_id") or "") != "siq_ic_chairman":
            errors.append("r4:chairman_identity_missing")

    contract_metric = _exported_contract_metric(artifacts)
    errors.extend(contract_metric["errors"])
    observed = {name: sorted(set(values)) for name, values in observed.items()}
    return {
        "passed": not errors,
        "observed": observed,
        "exported_contracts": {key: value for key, value in contract_metric.items() if key != "errors"},
        "errors": errors,
    }


def _known_evidence_ids(bundle: Path, evidence_index: Any) -> tuple[set[str], list[str]]:
    known: set[str] = set()
    errors: list[str] = []
    if isinstance(evidence_index, dict):
        for _path, value in _walk(evidence_index.get("items") or []):
            if isinstance(value, dict):
                evidence_id = str(value.get("evidence_id") or value.get("id") or "").strip()
                if evidence_id:
                    known.add(evidence_id)
    ndjson_path = bundle / "evidence" / "evidence_items.ndjson"
    if not ndjson_path.is_file():
        errors.append("evidence_items_ndjson_missing")
    else:
        for line_number, raw in enumerate(ndjson_path.read_text(encoding="utf-8").splitlines(), start=1):
            if not raw.strip():
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                errors.append(f"evidence_items_ndjson_invalid:{line_number}")
                continue
            if not isinstance(item, dict):
                errors.append(f"evidence_items_ndjson_not_object:{line_number}")
                continue
            evidence_id = str(item.get("evidence_id") or item.get("id") or "").strip()
            if evidence_id:
                known.add(evidence_id)
    if not known:
        errors.append("known_evidence_ids_empty")
    return known, errors


def _object_evidence_ids(item: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for field in ("evidence_id", "evidence_ids", "counter_evidence_ids"):
        values.extend(_strings(item.get(field)))
    return sorted(set(values))


def _evidence_metric(
    artifacts: dict[str, dict[str, Any]], known: set[str], initial_errors: list[str]
) -> dict[str, Any]:
    references: set[str] = set()
    background_references: set[str] = set()
    for name, artifact in artifacts.items():
        if name in {"manifest", "evidence_index", "decision_markdown"}:
            continue
        for _path, value in _walk(artifact.get("payload")):
            if not isinstance(value, dict):
                continue
            refs = _object_evidence_ids(value)
            if str(value.get("source_class") or "").lower() == "background_knowledge":
                background_references.update(refs)
            else:
                references.update(refs)
    unknown = sorted(references - known)
    errors = list(initial_errors)
    if unknown:
        errors.append(f"unknown_evidence_ids:{len(unknown)}")
    if not references:
        errors.append("project_evidence_references_missing")
    return {
        "passed": not errors,
        "known_count": len(known),
        "project_reference_count": len(references),
        "background_reference_count": len(background_references),
        "unknown_count": len(unknown),
        "unknown_ids": unknown,
        "errors": errors,
    }


def _claim_metric(artifacts: dict[str, dict[str, Any]], known: set[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    claims: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for name in ("r1", "r2", "r4"):
        payload = artifacts.get(name, {}).get("payload")
        parents = _report_objects(payload) if name in {"r1", "r2"} else [payload] if isinstance(payload, dict) else []
        for parent in parents:
            for path, value in _walk(parent.get("claims") or []):
                if isinstance(value, dict) and str(value.get("claim_id") or "").strip():
                    claims.append((f"{name}:{path}", value, parent))

    critical: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for path, claim, parent in claims:
        impact = str(claim.get("decision_impact") or claim.get("severity") or "").lower()
        if impact in {"critical", "material", "high"}:
            critical.append((path, claim, parent))
    uncovered: list[str] = []
    for path, claim, _parent in critical:
        refs = set(_object_evidence_ids(claim))
        status = str(claim.get("status") or "").lower()
        if not refs or not refs <= known or status in {"missing", "assumed", "unverified"}:
            uncovered.append(str(claim.get("claim_id") or path))
    claim_errors: list[str] = []
    if not claims:
        claim_errors.append("claim_contracts_missing")
    if not critical:
        claim_errors.append("critical_claims_missing")
    if uncovered:
        claim_errors.append(f"critical_claims_without_valid_evidence:{len(uncovered)}")
    claim_metric = {
        "passed": not claim_errors,
        "total": len(claims),
        "critical_total": len(critical),
        "critical_covered": len(critical) - len(uncovered),
        "critical_coverage_ratio": (round((len(critical) - len(uncovered)) / len(critical), 4) if critical else 0.0),
        "uncovered_claim_ids": sorted(set(uncovered)),
        "errors": claim_errors,
    }

    numeric: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for path, claim, parent in claims:
        if any(field in claim for field in ("value", "amount", "numeric_value")):
            numeric.append((path, claim, parent))
    incomplete: list[dict[str, Any]] = []
    for path, claim, parent in numeric:
        missing = [field for field in ("period", "currency", "unit") if not _nonempty(claim.get(field))]
        refs = set(_object_evidence_ids(claim))
        traces = _as_list(claim.get("calculation_trace_ids")) or _as_list(parent.get("calculation_trace_ids"))
        if not ((refs and refs <= known) or traces):
            missing.append("structured_source_or_calculation_trace")
        if missing:
            incomplete.append(
                {
                    "claim_id": str(claim.get("claim_id") or path),
                    "missing": sorted(set(missing)),
                }
            )
    numeric_errors: list[str] = []
    if not numeric:
        numeric_errors.append("numeric_claims_missing")
    if incomplete:
        numeric_errors.append(f"numeric_trace_incomplete:{len(incomplete)}")
    numeric_metric = {
        "passed": not numeric_errors,
        "numeric_claims": len(numeric),
        "complete_traces": len(numeric) - len(incomplete),
        "coverage_ratio": round((len(numeric) - len(incomplete)) / len(numeric), 4) if numeric else 0.0,
        "incomplete": incomplete,
        "errors": numeric_errors,
    }
    return claim_metric, numeric_metric


def _dispute_metric(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"passed": False, "total": 0, "unresolved": 0, "errors": ["disputes_payload_missing"]}
    disputes = payload.get("disputes")
    if not isinstance(disputes, list):
        disputes = payload.get("rulings") if isinstance(payload.get("rulings"), list) else []
    unresolved: list[str] = []
    for index, dispute in enumerate(disputes):
        if not isinstance(dispute, dict):
            unresolved.append(f"index-{index}")
            continue
        ruling = dispute.get("chairman_ruling") if isinstance(dispute.get("chairman_ruling"), dict) else {}
        status = str(dispute.get("status") or ruling.get("status") or "").lower()
        resolved = dispute.get("resolved") is True or status in {"resolved", "closed", "adjudicated"}
        decision = ruling.get("decision") or ruling.get("verdict") or dispute.get("ruling") or dispute.get("decision")
        if not resolved or not _nonempty(decision):
            unresolved.append(str(dispute.get("dispute_id") or f"index-{index}"))
    errors = [f"unresolved_disputes:{len(unresolved)}"] if unresolved else []
    return {
        "passed": not errors,
        "total": len(disputes),
        "resolved": len(disputes) - len(unresolved),
        "unresolved": len(unresolved),
        "unresolved_ids": unresolved,
        "errors": errors,
    }


def _r3_metric(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"passed": False, "mode": "missing", "errors": ["r3_payload_missing"]}
    mode = str(payload.get("mode") or "").lower()
    text = json.dumps(payload, ensure_ascii=False).lower()
    required_stages = {"red": False, "blue": False, "rebuttal": False, "verdict": False}
    tokens = {
        "red": ("red", "red_team", "红方"),
        "blue": ("blue", "blue_team", "蓝方"),
        "rebuttal": ("rebuttal", "反驳"),
        "verdict": ("verdict", "裁定", "裁决"),
    }
    for stage, candidates in tokens.items():
        required_stages[stage] = any(token in text for token in candidates)
    errors: list[str] = []
    if mode == "skip":
        errors.append("r3_skipped")
    if mode == "full" and not all(required_stages.values()):
        errors.append("r3_full_stages_incomplete")
    if not mode:
        errors.append("r3_mode_missing")
    return {"passed": not errors, "mode": mode or "missing", "stages": required_stages, "errors": errors}


def _r4_claim_cross_reference_metric(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {
            "passed": False,
            "claim_count": 0,
            "dimension_count": 0,
            "referenced_claim_count": 0,
            "errors": ["r4_payload_missing"],
        }

    errors: list[str] = []
    claims = [item for item in _as_list(payload.get("claims")) if isinstance(item, Mapping)]
    claim_ids = [str(item.get("claim_id") or "").strip() for item in claims]
    known_claim_ids = {claim_id for claim_id in claim_ids if claim_id}
    if not claim_ids or any(not claim_id for claim_id in claim_ids):
        errors.append("r4_claim_ids_missing")
    if len(claim_ids) != len(set(claim_ids)):
        errors.append("r4_claim_ids_not_unique")

    scorecard = [
        item
        for item in _as_list(payload.get("six_dimension_scorecard"))
        if isinstance(item, Mapping)
    ]
    if len(scorecard) != 6:
        errors.append("six_dimension_scorecard_cardinality_invalid")
    dimensions = [str(item.get("dimension") or "").strip() for item in scorecard]
    if any(not dimension for dimension in dimensions):
        errors.append("six_dimension_scorecard_dimension_missing")
    if len(dimensions) != len(set(dimensions)):
        errors.append("six_dimension_scorecard_dimensions_not_unique")

    referenced_claim_ids: set[str] = set()
    for index, item in enumerate(scorecard):
        dimension = dimensions[index] or f"index-{index}"
        raw_claim_ids = item.get("claim_ids")
        dimension_claim_ids = _strings(raw_claim_ids) if isinstance(raw_claim_ids, list) else []
        if not dimension_claim_ids:
            errors.append(f"six_dimension_scorecard_claim_ids_missing:{dimension}")
            continue
        referenced_claim_ids.update(dimension_claim_ids)
    unknown_claim_ids = sorted(referenced_claim_ids - known_claim_ids)
    if unknown_claim_ids:
        errors.append("six_dimension_scorecard_unknown_claim_ids:" + ",".join(unknown_claim_ids))
    return {
        "passed": not errors,
        "claim_count": len(known_claim_ids),
        "dimension_count": len(scorecard),
        "referenced_claim_count": len(referenced_claim_ids),
        "unknown_claim_ids": unknown_claim_ids,
        "errors": errors,
    }


def _report_hygiene_metric(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"passed": False, "characters": 0, "errors": ["decision_report_missing"]}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"passed": False, "characters": 0, "errors": [f"decision_report_read_error:{exc.__class__.__name__}"]}
    placeholders = sorted({match.group(0) for pattern in PLACEHOLDER_PATTERNS for match in pattern.finditer(text)})
    internal_paths = sorted(
        {match.group(0).strip() for pattern in INTERNAL_PATH_PATTERNS for match in pattern.finditer(text)}
    )
    errors: list[str] = []
    if len(text.strip()) < 300:
        errors.append("decision_report_too_short")
    if placeholders:
        errors.append(f"placeholder_markers:{len(placeholders)}")
    if internal_paths:
        errors.append(f"internal_paths:{len(internal_paths)}")
    return {
        "passed": not errors,
        "characters": len(text.strip()),
        "placeholder_count": len(placeholders),
        "placeholders": placeholders,
        "internal_path_count": len(internal_paths),
        "internal_paths": internal_paths,
        "errors": errors,
    }


def _fallback_metric(artifacts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    markers: list[dict[str, str]] = []
    for name in ("r1_5", "r2", "r3", "r4"):
        for path, value in _walk(artifacts.get(name, {}).get("payload")):
            # Deterministic candidate-dispute identification is an allowed
            # pre-step; only the persisted chairman ruling must be model-run.
            if name == "r1_5" and ".chairman_ruling." not in path:
                continue
            if path.endswith(".generation_mode"):
                mode = str(value or "").lower()
                if "fallback" in mode or "deterministic" in mode:
                    markers.append({"artifact": name, "path": path, "value": str(value)})
            elif path.endswith(".fallback_used") and value is True:
                markers.append({"artifact": name, "path": path, "value": "true"})
            elif path.endswith(".hermes_called") and value is False:
                markers.append({"artifact": name, "path": path, "value": "false"})
    errors = [f"fallback_or_deterministic_artifacts:{len(markers)}"] if markers else []
    return {"passed": not errors, "detected": bool(markers), "markers": markers, "errors": errors}


def _status_passed(payload: Mapping[str, Any]) -> bool:
    status_values = [
        payload.get("status"),
        payload.get("result"),
        (payload.get("summary") or {}).get("status") if isinstance(payload.get("summary"), dict) else None,
    ]
    if any(str(value or "").lower() in {"pass", "passed", "ok", "completed", "approved"} for value in status_values):
        return True
    for field in ("validation_result", "result"):
        nested = payload.get(field)
        if isinstance(nested, dict) and nested.get("ok") is True:
            return True
    return payload.get("passed") is True


def _count(payload: Mapping[str, Any], *fields: str) -> int:
    counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
    observed: list[int] = []
    for field in fields:
        value = payload.get(field, counts.get(field))
        if isinstance(value, int) and not isinstance(value, bool):
            observed.append(value)
        elif isinstance(value, (list, tuple, set, dict)):
            observed.append(len(value))
    return max(observed, default=0)


def _factcheck_metric(
    payload: Any,
    *,
    path: str | None,
    expected_deal_id: str,
    expected_report_id: str,
    expected_snapshot_hash: str,
    expected_revision: int,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"passed": False, "path": path, "errors": ["factcheck_missing_or_invalid"]}
    verdicts = payload.get("claim_verdicts") if isinstance(payload.get("claim_verdicts"), list) else []
    checked = _count(payload, "checked_claims", "claims_checked", "claims", "claim_checks") or len(verdicts)
    critical = _count(payload, "critical_issues", "critical_failures")
    unknown = _count(payload, "unknown_evidence", "unknown_evidence_ids")
    errors: list[str] = []
    if payload.get("schema_version") != FACTCHECK_SCHEMA:
        errors.append("factcheck_schema_version_invalid")
    if payload.get("deal_id") is not None and str(payload.get("deal_id") or "") != expected_deal_id:
        errors.append("factcheck_deal_id_mismatch")
    if str(payload.get("report_id") or "") != expected_report_id:
        errors.append("factcheck_report_id_mismatch")
    if str(payload.get("evidence_snapshot_hash") or "") != expected_snapshot_hash:
        errors.append("factcheck_snapshot_mismatch")
    if payload.get("report_revision") != expected_revision:
        errors.append("factcheck_report_revision_mismatch")
    if not str(payload.get("checked_at") or "").strip():
        errors.append("factcheck_checked_at_missing")
    if not _status_passed(payload):
        errors.append("factcheck_not_passed")
    if checked <= 0:
        errors.append("factcheck_checked_claims_missing")
    if critical:
        errors.append(f"factcheck_critical_issues:{critical}")
    if unknown:
        errors.append(f"factcheck_unknown_evidence:{unknown}")
    return {
        "passed": not errors,
        "path": path,
        "checked_claims": checked,
        "critical_issues": critical,
        "unknown_evidence": unknown,
        "errors": errors,
    }


def _quality_metric(
    payload: Any,
    *,
    expected_deal_id: str,
    expected_report_id: str,
    expected_snapshot_hash: str,
    expected_revision: int,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"passed": False, "errors": ["report_quality_missing_or_invalid"]}
    errors: list[str] = []
    if payload.get("schema_version") != "siq_ic_report_quality_v1":
        errors.append("report_quality_schema_invalid")
    if str(payload.get("deal_id") or "") != expected_deal_id:
        errors.append("report_quality_deal_id_mismatch")
    if str(payload.get("report_id") or "") != expected_report_id:
        errors.append("report_quality_report_id_mismatch")
    if payload.get("report_revision") != expected_revision:
        errors.append("report_quality_report_revision_mismatch")
    if str(payload.get("evidence_snapshot_hash") or "") != expected_snapshot_hash:
        errors.append("report_quality_snapshot_mismatch")
    if str(payload.get("status") or "").lower() != "pass":
        errors.append("report_quality_not_passed")
    if payload.get("allowed_for_human_confirmation") is not True:
        errors.append("report_quality_human_confirmation_blocked")
    if _as_list(payload.get("blocking_reasons")):
        errors.append("report_quality_has_blocking_reasons")
    return {"passed": not errors, "errors": errors}


def _artifact_identity_metric(
    artifacts: dict[str, dict[str, Any]],
    *,
    expected_deal_id: str,
    expected_snapshot_hash: str,
) -> dict[str, Any]:
    errors: list[str] = []
    if not SNAPSHOT_RE.fullmatch(expected_snapshot_hash):
        errors.append("evidence_snapshot_hash_invalid")

    r4 = artifacts.get("r4", {}).get("payload")
    if isinstance(r4, dict):
        if str(r4.get("deal_id") or "") != expected_deal_id:
            errors.append("r4:deal_id_mismatch")
        if str(r4.get("evidence_snapshot_hash") or "") != expected_snapshot_hash:
            errors.append("r4:snapshot_mismatch")
        if not str(r4.get("report_id") or "").startswith("ICRPT-"):
            errors.append("r4:report_id_invalid")
        if not isinstance(r4.get("revision"), int) or r4.get("revision", 0) < 1:
            errors.append("r4:revision_invalid")

    for name in ("r1", "r2"):
        for report in _report_objects(artifacts.get(name, {}).get("payload")):
            agent_id = str(report.get("agent_id") or "missing")
            if str(report.get("deal_id") or "") != expected_deal_id:
                errors.append(f"{name}:{agent_id}:deal_id_mismatch")
            if str(report.get("evidence_snapshot_hash") or "") != expected_snapshot_hash:
                errors.append(f"{name}:{agent_id}:snapshot_mismatch")
            if not str(report.get("report_id") or "").startswith("ICRPT-"):
                errors.append(f"{name}:{agent_id}:report_id_invalid")

    startup = artifacts.get("startup_receipts", {}).get("payload")
    for profile_id, receipt in _routing_rows(startup).items():
        if str(receipt.get("deal_id") or "") != expected_deal_id:
            errors.append(f"startup_receipts:{profile_id}:deal_id_mismatch")
        if str(receipt.get("evidence_snapshot_hash") or "") != expected_snapshot_hash:
            errors.append(f"startup_receipts:{profile_id}:snapshot_mismatch")
        if str(receipt.get("readiness_status") or "").lower() != "current":
            errors.append(f"startup_receipts:{profile_id}:not_current")

    r1_5 = artifacts.get("r1_5", {}).get("payload")
    if isinstance(r1_5, dict):
        disputes = r1_5.get("disputes")
        if not isinstance(disputes, list):
            disputes = r1_5.get("rulings") if isinstance(r1_5.get("rulings"), list) else []
        for dispute in disputes:
            if not isinstance(dispute, dict):
                continue
            dispute_id = str(dispute.get("dispute_id") or "missing")
            ruling = dispute.get("chairman_ruling") if isinstance(dispute.get("chairman_ruling"), dict) else {}
            if str(ruling.get("deal_id") or r1_5.get("deal_id") or "") != expected_deal_id:
                errors.append(f"r1_5:{dispute_id}:deal_id_mismatch")
            if not str(ruling.get("workflow_run_id") or "").startswith("ICRUN-"):
                errors.append(f"r1_5:{dispute_id}:workflow_run_id_invalid")

    r3 = artifacts.get("r3", {}).get("payload")
    if isinstance(r3, dict):
        if str(r3.get("deal_id") or "") != expected_deal_id:
            errors.append("r3:deal_id_mismatch")
        if str(r3.get("evidence_snapshot_hash") or "") != expected_snapshot_hash:
            errors.append("r3:snapshot_mismatch")
        for debate in _as_list(r3.get("debates")):
            if not isinstance(debate, dict):
                continue
            debate_id = str(debate.get("debate_id") or "missing")
            if str(debate.get("deal_id") or "") != expected_deal_id:
                errors.append(f"r3:{debate_id}:deal_id_mismatch")
            if str(debate.get("evidence_snapshot_hash") or "") != expected_snapshot_hash:
                errors.append(f"r3:{debate_id}:snapshot_mismatch")

    return {
        "passed": not errors,
        "deal_id": expected_deal_id,
        "evidence_snapshot_hash": expected_snapshot_hash or None,
        "errors": errors,
    }


def _audit_events(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        return []
    return [dict(item) for item in payload["events"] if isinstance(item, dict)]


def _double_audit_metric(phase_payload: Any, durable_payload: Any) -> dict[str, Any]:
    errors: list[str] = []
    phase_events = _audit_events(phase_payload)
    durable_events = _audit_events(durable_payload)
    if not isinstance(phase_payload, dict) or not isinstance(phase_payload.get("events"), list):
        errors.append("phase_audit_invalid")
    if not isinstance(durable_payload, dict) or not isinstance(durable_payload.get("events"), list):
        errors.append("durable_audit_invalid")
    if phase_events != durable_events:
        errors.append("audit_logs_diverged")
    return {
        "passed": not errors,
        "event_count": len(phase_events),
        "audit_digest": _canonical_digest(phase_events) if phase_events else None,
        "errors": errors,
    }


def _ids_with_prefix(value: Any, pattern: re.Pattern[str]) -> set[str]:
    return {item for _path, item in _walk(value) if isinstance(item, str) and pattern.fullmatch(item)}


def _matching_audit_event(
    events: Iterable[Mapping[str, Any]],
    *,
    event_type: str,
    expected: Mapping[str, Any],
) -> dict[str, Any] | None:
    matches = _matching_audit_events(events, event_type=event_type, expected=expected)
    return matches[0] if matches else None


def _matching_audit_events(
    events: Iterable[Mapping[str, Any]],
    *,
    event_type: str,
    expected: Mapping[str, Any],
) -> list[dict[str, Any]]:
    return [
        dict(event)
        for event in events
        if event.get("event_type") == event_type and all(event.get(key) == value for key, value in expected.items())
    ]


def _phase_task_binding_errors(
    artifacts: Mapping[str, Mapping[str, Any]],
    tasks_by_id: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    errors: list[str] = []
    rules: tuple[tuple[str, set[str], set[str]], ...] = (
        ("r1", {"R1A", "R1B"}, R1_AGENT_IDS),
        ("r1_5", {"R1.5"}, {"siq_ic_chairman"}),
        ("r2", {"R2"}, R2_AGENT_IDS),
        ("r4", {"R4"}, {"siq_ic_chairman"}),
    )
    for artifact_name, phases, expected_agents in rules:
        payload = artifacts.get(artifact_name, {}).get("payload")
        referenced = _ids_with_prefix(payload, TASK_ID_RE)
        matched = {
            task_id: tasks_by_id[task_id]
            for task_id in referenced
            if task_id in tasks_by_id and str(tasks_by_id[task_id].get("phase") or "") in phases
        }
        observed_agents = {str(task.get("agent_id") or "") for task in matched.values()}
        missing = sorted(expected_agents - observed_agents)
        if missing:
            errors.append(f"{artifact_name}:task_bindings_missing_agents:{','.join(missing)}")

    r3_payload = artifacts.get("r3", {}).get("payload")
    r3_referenced = _ids_with_prefix(r3_payload, TASK_ID_RE)
    r3_tasks = {
        task_id: tasks_by_id[task_id]
        for task_id in r3_referenced
        if task_id in tasks_by_id and tasks_by_id[task_id].get("phase") == "R3"
    }
    expected_r3_agents = {"siq_ic_chairman"}
    if isinstance(r3_payload, dict):
        for debate in _as_list(r3_payload.get("debates")):
            if isinstance(debate, dict):
                expected_r3_agents.update(_strings(debate.get("red_team")))
                expected_r3_agents.update(_strings(debate.get("blue_team")))
        for topic in _as_list(r3_payload.get("topics")):
            if isinstance(topic, dict):
                expected_r3_agents.update(str(topic.get(key) or "") for key in ("red_agent_id", "blue_agent_id"))
    expected_r3_agents.discard("")
    observed_r3_agents = {str(task.get("agent_id") or "") for task in r3_tasks.values()}
    missing_r3 = sorted(expected_r3_agents - observed_r3_agents)
    if missing_r3:
        errors.append(f"r3:task_bindings_missing_agents:{','.join(missing_r3)}")
    errors.extend(_validated_output_artifact_errors(artifacts, tasks_by_id))
    return errors


def _validated_output_artifact_errors(
    artifacts: Mapping[str, Mapping[str, Any]],
    tasks_by_id: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    errors: list[str] = []

    def compare(label: str, task: Mapping[str, Any] | None, artifact: Any) -> None:
        if not isinstance(task, Mapping):
            errors.append(f"{label}:validated_output_task_missing")
            return
        validated = task.get("validated_output")
        if not isinstance(validated, dict) or not isinstance(artifact, Mapping):
            errors.append(f"{label}:validated_output_artifact_missing")
            return
        missing = sorted(key for key in validated if key not in artifact)
        projected = {key: artifact[key] for key in validated if key in artifact}
        if missing or _canonical_digest(projected) != _canonical_digest(validated):
            errors.append(f"{label}:validated_output_artifact_mismatch")

    def bind_identity(
        label: str,
        task: Mapping[str, Any] | None,
        artifact: Mapping[str, Any],
        *,
        expected_phase: str,
        expected_agent: str,
        compare_output: bool = True,
        expected_output_schema: str | None = None,
    ) -> None:
        if compare_output:
            compare(label, task, artifact)
        if not isinstance(task, Mapping):
            return
        for key in (
            "task_id",
            "workflow_run_id",
            "input_digest",
            "handoff_digest",
            "hermes_run_id",
        ):
            if artifact.get(key) != task.get(key):
                errors.append(f"{label}:{key}_mismatch")
        if task.get("phase") != expected_phase:
            errors.append(f"{label}:task_phase_mismatch")
        if task.get("agent_id") != expected_agent:
            errors.append(f"{label}:task_agent_mismatch")
        if expected_output_schema is not None and task.get("output_schema") != expected_output_schema:
            errors.append(f"{label}:task_output_schema_mismatch")
        if "phase" in artifact and artifact.get("phase") != expected_phase:
            errors.append(f"{label}:artifact_phase_mismatch")
        if "agent_id" in artifact and artifact.get("agent_id") != expected_agent:
            errors.append(f"{label}:artifact_agent_mismatch")

    r0 = artifacts.get("r0", {}).get("payload")
    r0_tasks = [
        task
        for task in tasks_by_id.values()
        if task.get("phase") == "R0" and task.get("agent_id") == "siq_ic_master_coordinator"
    ]
    if len(r0_tasks) != 1:
        errors.append("r0:validated_output_task_cardinality_invalid")
    elif isinstance(r0, Mapping):
        compare("r0", r0_tasks[0], r0)
        if r0_tasks[0].get("workflow_run_id") != r0.get("workflow_run_id"):
            errors.append("r0:workflow_run_id_mismatch")

    for artifact_name in ("r1", "r2"):
        for report in _report_objects(artifacts.get(artifact_name, {}).get("payload")):
            report_id = str(report.get("report_id") or "missing")
            task_id = str(report.get("task_id") or "")
            task = tasks_by_id.get(task_id)
            expected_phase = (
                "R2"
                if artifact_name == "r2"
                else "R1B"
                if report.get("agent_id") in {"siq_ic_risk_controller", "siq_ic_chairman"}
                else "R1A"
            )
            bind_identity(
                f"{artifact_name}:{report_id}",
                task,
                report,
                expected_phase=expected_phase,
                expected_agent=str(report.get("agent_id") or ""),
            )

    r1_5 = artifacts.get("r1_5", {}).get("payload")
    if isinstance(r1_5, Mapping):
        raw_disputes = r1_5.get("disputes")
        disputes = raw_disputes if isinstance(raw_disputes, list) else _as_list(r1_5.get("rulings"))
        rulings_by_task: dict[str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = {}
        for dispute in disputes:
            if not isinstance(dispute, Mapping):
                continue
            ruling = dispute.get("chairman_ruling")
            if not isinstance(ruling, Mapping):
                ruling = dispute
            task_id = str(ruling.get("task_id") or "")
            if task_id:
                rulings_by_task.setdefault(task_id, []).append((dispute, ruling))
            else:
                errors.append(f"r1_5:{ruling.get('dispute_id') or 'missing'}:task_id_missing")
        for task_id, persisted_items in rulings_by_task.items():
            task = tasks_by_id.get(task_id)
            validated = task.get("validated_output") if isinstance(task, Mapping) else None
            validated_rulings = validated.get("rulings") if isinstance(validated, dict) else None
            if not isinstance(validated_rulings, list):
                errors.append(f"r1_5:{task_id}:validated_output_artifact_missing")
                continue
            persisted_by_id = {
                str(ruling.get("dispute_id") or ""): (dispute, ruling) for dispute, ruling in persisted_items
            }
            validated_ids = [
                str(item.get("dispute_id") or "") for item in validated_rulings if isinstance(item, Mapping)
            ]
            if (
                len(persisted_by_id) != len(persisted_items)
                or len(validated_ids) != len(validated_rulings)
                or len(validated_ids) != len(set(validated_ids))
                or set(persisted_by_id) != set(validated_ids)
            ):
                errors.append(f"r1_5:{task_id}:ruling_identity_set_mismatch")
            canonical_fields = (
                "dispute_id",
                "workflow_run_id",
                "deal_id",
                "evidence_snapshot_hash",
                "question",
                "severity",
                "positions",
                "evidence_ids",
                "counter_evidence_ids",
                "ruling",
                "rationale",
                "accepted_claim_ids",
                "rejected_claim_ids",
                "required_followups",
                "decision_impact",
            )
            for validated_ruling in validated_rulings:
                if not isinstance(validated_ruling, dict):
                    errors.append(f"r1_5:{task_id}:validated_output_ruling_invalid")
                    continue
                persisted = persisted_by_id.get(str(validated_ruling.get("dispute_id") or ""))
                if not isinstance(persisted, tuple):
                    errors.append(f"r1_5:{task_id}:validated_output_artifact_missing")
                    continue
                dispute, ruling = persisted
                canonical = _r1_5_canonical_artifact(dispute, ruling)
                if validated_ruling.get("schema_version") != R1_5_DISPUTE_SCHEMA:
                    errors.append(f"r1_5:{task_id}:validated_output_schema_mismatch")
                for field in canonical_fields:
                    if _canonical_digest(validated_ruling.get(field)) != _canonical_digest(canonical.get(field)):
                        errors.append(
                            f"r1_5:{validated_ruling.get('dispute_id') or 'missing'}:canonical_artifact_mismatch:{field}"
                        )
                for field in ("decision", "resolved"):
                    if field in validated_ruling and validated_ruling.get(field) != ruling.get(field):
                        errors.append(
                            f"r1_5:{validated_ruling.get('dispute_id') or 'missing'}:canonical_artifact_mismatch:{field}"
                        )
                submission_schema = ruling.get("submission_schema_version")
                if submission_schema != validated_ruling.get("schema_version"):
                    errors.append(
                        f"r1_5:{validated_ruling.get('dispute_id') or 'missing'}:submission_schema_version_mismatch"
                    )
                if ruling.get("source_created_at") != validated_ruling.get("created_at"):
                    errors.append(f"r1_5:{validated_ruling.get('dispute_id') or 'missing'}:source_created_at_mismatch")
            for _dispute, ruling in persisted_items:
                bind_identity(
                    f"r1_5:{ruling.get('dispute_id') or 'missing'}",
                    task,
                    ruling,
                    expected_phase="R1.5",
                    expected_agent="siq_ic_chairman",
                    compare_output=False,
                )

    r3 = artifacts.get("r3", {}).get("payload")
    if isinstance(r3, Mapping):
        raw_debates = [item for item in _as_list(r3.get("debates")) if isinstance(item, Mapping)]
        debates = {str(item.get("debate_id") or ""): item for item in raw_debates}
        if len(debates) != len(raw_debates) or "" in debates:
            errors.append("r3:debate_ids_not_unique")
        topics = [item for item in _as_list(r3.get("topics")) if isinstance(item, Mapping)]
        if not topics:
            errors.append("r3:authoritative_topics_missing")
        topic_ids = [str(item.get("topic_id") or "") for item in topics]
        if not all(topic_ids) or len(topic_ids) != len(set(topic_ids)):
            errors.append("r3:topic_ids_not_unique")
        bound_task_ids: set[str] = set()
        bound_debate_ids: set[str] = set()
        for topic in topics:
            topic_id = str(topic.get("topic_id") or "missing")
            debate_contract = topic.get("debate_contract")
            debate_id = str(debate_contract.get("debate_id") or "") if isinstance(debate_contract, Mapping) else ""
            if not isinstance(debate_contract, Mapping) or not debate_id or debates.get(debate_id) != debate_contract:
                errors.append(f"r3:{topic_id}:debate_contract_mismatch")
            else:
                bound_debate_ids.add(debate_id)
                if _strings(debate_contract.get("red_team")) != [str(topic.get("red_agent_id") or "")]:
                    errors.append(f"r3:{topic_id}:red_team_identity_mismatch")
                if _strings(debate_contract.get("blue_team")) != [str(topic.get("blue_agent_id") or "")]:
                    errors.append(f"r3:{topic_id}:blue_team_identity_mismatch")

            arguments = [item for item in _as_list(topic.get("arguments")) if isinstance(item, Mapping)]
            if not arguments:
                errors.append(f"r3:{topic_id}:arguments_missing")
            for argument in arguments:
                task_id = str(argument.get("task_id") or "")
                if task_id in bound_task_ids:
                    errors.append(f"r3:{topic_id}:{task_id or 'missing'}:task_reused")
                bound_task_ids.add(task_id)
                turn_type = str(argument.get("turn_type") or "")
                expected_agent = str(argument.get("agent_id") or "")
                if turn_type.startswith("red_"):
                    expected_agent = str(topic.get("red_agent_id") or expected_agent)
                elif turn_type.startswith("blue_"):
                    expected_agent = str(topic.get("blue_agent_id") or expected_agent)
                bind_identity(
                    f"r3:{topic_id}:{task_id or 'missing'}:turn",
                    tasks_by_id.get(task_id),
                    argument,
                    expected_phase="R3",
                    expected_agent=expected_agent,
                    expected_output_schema="siq_ic_r3_debate_turn_v1",
                )

            verdict = topic.get("verdict")
            if not isinstance(verdict, Mapping):
                errors.append(f"r3:{topic_id}:verdict_missing")
                continue
            task_id = str(verdict.get("task_id") or "")
            if task_id in bound_task_ids:
                errors.append(f"r3:{topic_id}:{task_id or 'missing'}:task_reused")
            bound_task_ids.add(task_id)
            bind_identity(
                f"r3:{topic_id}:{task_id or 'missing'}:verdict",
                tasks_by_id.get(task_id),
                verdict,
                expected_phase="R3",
                expected_agent="siq_ic_chairman",
                expected_output_schema="siq_ic_r3_debate_verdict_v1",
            )
        if bound_debate_ids != set(debates):
            errors.append("r3:debate_topic_binding_set_mismatch")

    r4 = artifacts.get("r4", {}).get("payload")
    if isinstance(r4, Mapping):
        task_id = str(r4.get("task_id") or "")
        bind_identity(
            f"r4:{r4.get('report_id') or 'missing'}",
            tasks_by_id.get(task_id),
            r4,
            expected_phase="R4",
            expected_agent="siq_ic_chairman",
        )
    return errors


def _authoritative_phase_task_ids(
    artifacts: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    task_ids: set[str] = set()

    def add(value: Any) -> None:
        task_id = str(value or "").strip()
        if task_id:
            task_ids.add(task_id)

    r0 = artifacts.get("r0", {}).get("payload")
    if isinstance(r0, Mapping):
        add(r0.get("task_id"))

    for artifact_name in ("r1", "r2"):
        for report in _report_objects(artifacts.get(artifact_name, {}).get("payload")):
            add(report.get("task_id"))

    r1_5 = artifacts.get("r1_5", {}).get("payload")
    if isinstance(r1_5, Mapping):
        disputes = r1_5.get("disputes")
        if not isinstance(disputes, list):
            disputes = r1_5.get("rulings") if isinstance(r1_5.get("rulings"), list) else []
        for dispute in disputes:
            if not isinstance(dispute, Mapping):
                continue
            ruling = dispute.get("chairman_ruling")
            add(ruling.get("task_id") if isinstance(ruling, Mapping) else dispute.get("task_id"))

    r3 = artifacts.get("r3", {}).get("payload")
    if isinstance(r3, Mapping):
        for topic in _as_list(r3.get("topics")):
            if not isinstance(topic, Mapping):
                continue
            for argument in _as_list(topic.get("arguments")):
                if isinstance(argument, Mapping):
                    add(argument.get("task_id"))
            verdict = topic.get("verdict")
            if isinstance(verdict, Mapping):
                add(verdict.get("task_id"))

    r4 = artifacts.get("r4", {}).get("payload")
    if isinstance(r4, Mapping):
        add(r4.get("task_id"))
    return task_ids


def _terminal_task_audit_errors(
    bundle: Path,
    task: Mapping[str, Any],
    events: Iterable[Mapping[str, Any]],
) -> list[str]:
    errors: list[str] = []
    task_id = str(task.get("task_id") or "")
    digest = str(task.get("input_digest") or "")
    status = str(task.get("status") or "")
    terminal_statuses = {
        "succeeded",
        "failed",
        "cancelled",
        "interrupted",
        "timed_out",
        "stale_on_completion",
    }
    completed_statuses = {"succeeded", "stale_on_completion"}
    if status not in terminal_statuses:
        return ["status_not_terminal"]
    if task.get("schema_version") != "siq_ic_agent_task_v2":
        errors.append("schema_invalid")
    if not DIGEST_RE.fullmatch(digest) or task_id != f"ICTASK-{digest[:24].upper()}":
        errors.append("task_id_digest_mismatch")

    run_id = str(task.get("hermes_run_id") or "")
    run_ids = _strings(task.get("hermes_run_ids") or ([run_id] if run_id else []))
    if run_id and run_id not in run_ids:
        errors.append("hermes_run_identity_invalid")
    if status in completed_statuses and (not run_id or run_id not in run_ids):
        errors.append("completed_run_identity_missing")

    output_paths = _strings(task.get("output_artifact_paths"))
    output_hashes = task.get("output_artifact_hashes")
    if not isinstance(output_hashes, dict):
        output_hashes = {}
        errors.append("raw_output_hashes_invalid")
    if len(output_paths) != len(set(output_paths)) or set(output_paths) != set(output_hashes):
        errors.append("raw_output_manifest_mismatch")
    for relative in output_paths:
        matching_run_ids = [
            observed_run_id
            for observed_run_id in run_ids
            if _raw_output_path(
                bundle,
                task_id=task_id,
                hermes_run_id=observed_run_id,
                relative=relative,
            )
            is not None
        ]
        path = _contained_path(bundle, relative)
        expected_hash = str(output_hashes.get(relative) or "").lower()
        if (
            len(matching_run_ids) != 1
            or path is None
            or not path.is_file()
            or not DIGEST_RE.fullmatch(expected_hash)
            or _file_digest(path) != expected_hash
        ):
            errors.append(f"raw_output_invalid:{relative}")
    if status in completed_statuses:
        contract_validation = task.get("contract_validation")
        if (
            not isinstance(task.get("validated_output"), dict)
            or not isinstance(contract_validation, dict)
            or contract_validation.get("passed") is not True
        ):
            errors.append("completed_contract_validation_invalid")
        for observed_run_id in run_ids:
            matches = [
                relative
                for relative in output_paths
                if _raw_output_path(
                    bundle,
                    task_id=task_id,
                    hermes_run_id=observed_run_id,
                    relative=relative,
                )
                is not None
            ]
            if len(matches) != 1:
                errors.append(f"raw_output_cardinality_invalid:{observed_run_id}")

    event_type = "ic_phase_hermes_task_completed" if status in completed_statuses else "ic_phase_hermes_task_failed"
    terminal_events = _matching_audit_events(
        events,
        event_type=event_type,
        expected={
            "workflow_run_id": task.get("workflow_run_id"),
            "task_id": task_id,
            "phase": task.get("phase"),
            "agent_id": task.get("agent_id"),
            "input_digest": digest,
            "handoff_digest": task.get("handoff_digest"),
            "hermes_run_id": run_id or None,
            "evidence_snapshot_hash": task.get("evidence_snapshot_hash"),
            "prompt_contract_version": task.get("prompt_contract_version"),
            "profile_contract_version": task.get("profile_contract_version"),
            "output_schema": task.get("output_schema"),
            "output_artifact_hashes": output_hashes,
            "contract_validation": task.get("contract_validation"),
            "status": status,
        },
    )
    if not terminal_events:
        errors.append("terminal_audit_missing")
    elif len(terminal_events) != 1:
        errors.append("terminal_audit_cardinality_invalid")
    return errors


def _task_lineage_identity_errors(tasks: Iterable[Mapping[str, Any]]) -> list[str]:
    errors: list[str] = []
    run_owners: dict[str, str] = {}
    raw_path_owners: dict[str, str] = {}
    for task in tasks:
        task_id = str(task.get("task_id") or "missing")
        lineages: list[tuple[str, Mapping[str, Any]]] = [("terminal", task)]
        lineages.extend(
            (f"attempt:{index}", prior)
            for index, prior in enumerate(_as_list(task.get("attempt_history")), start=1)
            if isinstance(prior, Mapping)
        )
        for lineage_name, lineage in lineages:
            owner = f"{task_id}:{lineage_name}"
            run_ids = _strings(lineage.get("hermes_run_ids"))
            run_id = str(lineage.get("hermes_run_id") or "")
            if run_id and not run_ids:
                run_ids = [run_id]
            for observed_run_id in run_ids:
                prior_owner = run_owners.get(observed_run_id)
                if prior_owner is not None:
                    errors.append(f"hermes_run_id_reused:{observed_run_id}:{prior_owner}:{owner}")
                else:
                    run_owners[observed_run_id] = owner
            for relative in _strings(lineage.get("output_artifact_paths")):
                prior_owner = raw_path_owners.get(relative)
                if prior_owner is not None:
                    errors.append(f"raw_output_path_reused:{relative}:{prior_owner}:{owner}")
                else:
                    raw_path_owners[relative] = owner
    return errors


def _runtime_metadata_errors(value: Any) -> list[str]:
    if not isinstance(value, Mapping):
        return ["runtime_missing_or_invalid"]
    errors: list[str] = []
    expected_keys = {"schema_version", "requested_model", "configured", "effective", "fallback"}
    if set(value) != expected_keys:
        errors.append("runtime_fields_invalid")
    if value.get("schema_version") != RUN_RUNTIME_SCHEMA:
        errors.append("runtime_schema_version_invalid")

    def validate_label(label: str, candidate: Any) -> None:
        if (
            not isinstance(candidate, str)
            or not RUNTIME_LABEL_RE.fullmatch(candidate)
            or "://" in candidate
            or candidate.lower().startswith("bearer")
        ):
            errors.append(f"runtime_{label}_invalid")

    validate_label("requested_model", value.get("requested_model"))
    for section_name in ("configured", "effective"):
        section = value.get(section_name)
        if not isinstance(section, Mapping) or set(section) != {"provider", "model"}:
            errors.append(f"runtime_{section_name}_fields_invalid")
            continue
        validate_label(f"{section_name}_provider", section.get("provider"))
        validate_label(f"{section_name}_model", section.get("model"))
    fallback = value.get("fallback")
    if not isinstance(fallback, Mapping) or set(fallback) != {"activated"}:
        errors.append("runtime_fallback_fields_invalid")
    elif not isinstance(fallback.get("activated"), bool):
        errors.append("runtime_fallback_activated_invalid")
    return errors


def _model_execution_audit_errors(
    task: Mapping[str, Any],
    events: Iterable[Mapping[str, Any]],
    *,
    require_succeeded_terminal: bool = True,
) -> list[str]:
    audit = task.get("model_execution_audit")
    if not isinstance(audit, Mapping):
        return ["model_execution_audit_missing"]
    errors: list[str] = []
    expected_audit_keys = {
        "schema_version",
        "runtime_metadata_status",
        "attempt_count",
        "attempts",
        "final_hermes_run_id",
        "final_prompt_sha256",
        "final_runtime",
    }
    if set(audit) != expected_audit_keys:
        errors.append("model_execution_audit_fields_invalid")
    if audit.get("schema_version") != MODEL_EXECUTION_AUDIT_SCHEMA:
        errors.append("model_execution_audit_schema_invalid")

    attempts = audit.get("attempts")
    attempt_rows = [item for item in attempts if isinstance(item, Mapping)] if isinstance(attempts, list) else []
    if not isinstance(attempts, list) or len(attempt_rows) != len(attempts):
        errors.append("model_execution_attempts_invalid")
    attempt_count = audit.get("attempt_count")
    if isinstance(attempt_count, bool) or not isinstance(attempt_count, int) or attempt_count != len(attempt_rows):
        errors.append("model_execution_attempt_count_mismatch")

    task_run_ids = _strings(task.get("hermes_run_ids"))
    attempt_run_ids = [str(item.get("hermes_run_id") or "") for item in attempt_rows]
    if not task_run_ids or attempt_run_ids != task_run_ids:
        errors.append("model_execution_attempt_run_mapping_invalid")

    prompt_hashes: list[str] = []
    all_runtime_verified = bool(attempt_rows)
    for index, attempt in enumerate(attempt_rows):
        expected_attempt_keys = {
            "hermes_run_id",
            "purpose",
            "prompt_sha256",
            "terminal_status",
            "runtime_metadata_status",
            "runtime",
        }
        if set(attempt) != expected_attempt_keys:
            errors.append(f"model_execution_attempt_fields_invalid:{index}")
        expected_purpose = "generation" if index == 0 else "contract_repair"
        if attempt.get("purpose") != expected_purpose:
            errors.append(f"model_execution_attempt_purpose_invalid:{index}")
        prompt_sha256 = str(attempt.get("prompt_sha256") or "")
        prompt_hashes.append(prompt_sha256)
        if not DIGEST_RE.fullmatch(prompt_sha256):
            errors.append(f"model_execution_prompt_sha256_invalid:{index}")
        terminal_status = attempt.get("terminal_status")
        if require_succeeded_terminal and terminal_status != "succeeded":
            errors.append(f"model_execution_terminal_status_invalid:{index}")
        elif terminal_status not in {
            "succeeded",
            "failed",
            "cancelled",
            "timed_out",
            "protocol_eof",
            "unavailable",
        }:
            errors.append(f"model_execution_terminal_status_invalid:{index}")
        runtime_errors = _runtime_metadata_errors(attempt.get("runtime"))
        if runtime_errors:
            errors.extend(f"model_execution_attempt:{index}:{error}" for error in runtime_errors)
        derived_runtime_status = "verified" if not runtime_errors else "unverified"
        if attempt.get("runtime_metadata_status") != derived_runtime_status:
            errors.append(f"model_execution_runtime_status_mismatch:{index}")
        if derived_runtime_status != "verified":
            all_runtime_verified = False

        if expected_purpose == "contract_repair" and terminal_status == "succeeded":
            repair_event_type = (
                "ic_r4_factcheck_contract_repair_attempted"
                if task.get("agent_id") == "siq_factchecker"
                else "ic_phase_hermes_contract_repair_attempted"
            )
            repair_events = _matching_audit_events(
                events,
                event_type=repair_event_type,
                expected={
                    "workflow_run_id": task.get("workflow_run_id"),
                    "task_id": task.get("task_id"),
                    "phase": task.get("phase"),
                    "agent_id": task.get("agent_id"),
                    "original_hermes_run_id": task_run_ids[0] if task_run_ids else None,
                    "repair_hermes_run_id": attempt.get("hermes_run_id"),
                    "repair_prompt_sha256": prompt_sha256,
                    "model_execution_attempt": dict(attempt),
                },
            )
            if not repair_events:
                errors.append(f"model_execution_repair_audit_missing:{index}")
            elif len(repair_events) != 1:
                errors.append(f"model_execution_repair_audit_cardinality_invalid:{index}")

    if len(prompt_hashes) != len(set(prompt_hashes)):
        errors.append("model_execution_prompt_sha256_not_unique")
    derived_status = "verified" if all_runtime_verified else "unverified"
    if audit.get("runtime_metadata_status") != derived_status:
        errors.append("model_execution_runtime_status_mismatch")
    if derived_status != "verified":
        errors.append("model_execution_identity_unverified")

    final = attempt_rows[-1] if attempt_rows else {}
    if audit.get("final_hermes_run_id") != final.get("hermes_run_id"):
        errors.append("model_execution_final_run_id_mismatch")
    if audit.get("final_hermes_run_id") != task.get("hermes_run_id"):
        errors.append("model_execution_task_final_run_id_mismatch")
    if audit.get("final_prompt_sha256") != final.get("prompt_sha256"):
        errors.append("model_execution_final_prompt_sha256_mismatch")
    if _canonical_digest(audit.get("final_runtime")) != _canonical_digest(final.get("runtime")):
        errors.append("model_execution_final_runtime_mismatch")
    return errors


def _attempt_raw_output_cardinality_errors(
    bundle: Path,
    *,
    task_id: str,
    run_ids: Sequence[str],
    model_execution_audit: Any,
    output_paths: Sequence[str],
) -> list[str]:
    """Bind each raw artifact to the terminal status of its exact Hermes run."""
    if not isinstance(model_execution_audit, Mapping):
        return []
    attempts = model_execution_audit.get("attempts")
    if not isinstance(attempts, list):
        return []

    errors: list[str] = []
    for run_id in run_ids:
        matching_attempts = [
            attempt
            for attempt in attempts
            if isinstance(attempt, Mapping) and attempt.get("hermes_run_id") == run_id
        ]
        if len(matching_attempts) != 1:
            # The model-execution validator reports the authoritative mapping error.
            continue
        matching_paths = [
            relative
            for relative in output_paths
            if _raw_output_path(
                bundle,
                task_id=task_id,
                hermes_run_id=run_id,
                relative=relative,
            )
            is not None
        ]
        expected_count = 1 if matching_attempts[0].get("terminal_status") == "succeeded" else 0
        if len(matching_paths) != expected_count:
            errors.append(f"raw_output_cardinality_invalid:{run_id}")
    return errors


def _execution_chain_metric(
    bundle: Path,
    artifacts: Mapping[str, Mapping[str, Any]],
    *,
    expected_deal_id: str,
    expected_snapshot_hash: str,
    factcheck_payload: Any,
    real_smoke_payload: Any,
) -> dict[str, Any]:
    errors: list[str] = []
    task_store = artifacts.get("tasks", {}).get("payload")
    handoff_store = artifacts.get("handoffs", {}).get("payload")
    workflow_store = artifacts.get("workflow_runs", {}).get("payload")
    factcheck_task = artifacts.get("factcheck_task", {}).get("payload")
    events = _audit_events(artifacts.get("phase_audit", {}).get("payload"))

    raw_tasks = _as_list(task_store.get("tasks")) if isinstance(task_store, dict) else []
    all_tasks = [dict(item) for item in raw_tasks if isinstance(item, dict)]
    task_ids = [str(task.get("task_id") or "") for task in all_tasks]
    if len(task_ids) != len(set(task_ids)):
        errors.append("task_ids_not_unique")
    errors.extend(_task_lineage_identity_errors(all_tasks))
    tasks_by_id = {str(task.get("task_id") or ""): task for task in all_tasks}
    authoritative_task_ids = _authoritative_phase_task_ids(artifacts)
    current_tasks: list[dict[str, Any]] = []
    for task_id in sorted(authoritative_task_ids):
        task = tasks_by_id.get(task_id)
        if not isinstance(task, dict):
            errors.append(f"authoritative_task_missing:{task_id}")
            continue
        if task.get("status") != "succeeded":
            errors.append(f"authoritative_task_not_succeeded:{task_id}:{task.get('status') or 'missing'}")
            continue
        if task.get("deal_id") != expected_deal_id:
            errors.append(f"authoritative_task_deal_mismatch:{task_id}")
            continue
        if task.get("evidence_snapshot_hash") != expected_snapshot_hash:
            errors.append(f"authoritative_task_snapshot_mismatch:{task_id}")
            continue
        current_tasks.append(task)

    historical_tasks = [task for task in all_tasks if str(task.get("task_id") or "") not in authoritative_task_ids]
    historical_terminal_tasks = [
        task
        for task in historical_tasks
        if str(task.get("status") or "")
        in {"succeeded", "failed", "cancelled", "interrupted", "timed_out", "stale_on_completion"}
    ]
    for task in historical_terminal_tasks:
        historical_errors = _terminal_task_audit_errors(bundle, task, events)
        errors.extend(
            f"historical:{task.get('task_id') or 'missing'}:{error}"
            for error in historical_errors
        )
    raw_handoffs = _as_list(handoff_store.get("handoffs")) if isinstance(handoff_store, dict) else []
    handoffs = [dict(item) for item in raw_handoffs if isinstance(item, dict)]
    handoffs_by_id = {str(item.get("handoff_id") or ""): item for item in handoffs}
    if len(handoffs_by_id) != len(handoffs):
        errors.append("handoff_ids_not_unique")
    handoff_payloads = handoff_store.get("payloads") if isinstance(handoff_store, dict) else None
    if not isinstance(handoff_payloads, dict):
        handoff_payloads = {}
        errors.append("handoff_payloads_missing")

    runs = _as_list(workflow_store.get("runs")) if isinstance(workflow_store, dict) else []
    workflow_run_ids = [str(item.get("workflow_run_id") or "") for item in runs if isinstance(item, dict)]
    if len(workflow_run_ids) != len(set(workflow_run_ids)):
        errors.append("workflow_run_ids_not_unique")
    workflow_runs = {
        str(item.get("workflow_run_id") or ""): item
        for item in runs
        if isinstance(item, dict) and item.get("workflow_run_id")
    }
    observed_workflow_ids = {str(task.get("workflow_run_id") or "") for task in current_tasks}
    observed_workflow_ids.discard("")
    if len(observed_workflow_ids) != 1:
        errors.append("current_tasks_must_share_one_workflow_run")

    valid_tasks: dict[str, dict[str, Any]] = {}
    runtime_verified_task_count = 0
    prior_attempt_count = 0
    claimed_handoff_ids: set[str] = set()
    claimed_hermes_run_ids: set[str] = set()
    claimed_raw_paths: set[str] = set()
    for task in current_tasks:
        task_id = str(task.get("task_id") or "")
        task_errors: list[str] = []
        digest = str(task.get("input_digest") or "")
        if task.get("schema_version") != "siq_ic_agent_task_v2":
            task_errors.append("schema_invalid")
        if not TASK_ID_RE.fullmatch(task_id) or task_id != f"ICTASK-{digest[:24].upper()}":
            task_errors.append("task_id_digest_mismatch")
        if not DIGEST_RE.fullmatch(digest):
            task_errors.append("input_digest_invalid")
        if task.get("prompt_contract_version") != "siq_ic_phase_prompt_v5":
            task_errors.append("prompt_contract_version_not_v5")
        if task.get("profile_contract_version") != "hermes_profile_authority_v1":
            task_errors.append("profile_contract_version_invalid")
        methodology_refs = _as_list(task.get("methodology_refs"))
        if not methodology_refs:
            task_errors.append("methodology_refs_missing")
        elif any(
            not isinstance(ref, dict)
            or not str(ref.get("ref_id") or "").startswith("KBREF-")
            or ref.get("collection") != task.get("agent_id")
            or ref.get("usage") != "methodology"
            for ref in methodology_refs
        ):
            task_errors.append("methodology_refs_invalid")
        if task.get("hermes_called") is not True:
            task_errors.append("hermes_not_called")
        claim = task.get("task_claim")
        if not isinstance(claim, dict):
            task_errors.append("task_claim_missing")
            lease_attempt = 0
        else:
            try:
                lease_attempt = int(claim.get("attempt") or 0)
            except (TypeError, ValueError):
                lease_attempt = 0
            if lease_attempt < 1 or claim.get("status") != "succeeded":
                task_errors.append("task_claim_terminal_identity_invalid")
        attempt_history = _as_list(task.get("attempt_history"))
        prior_attempt_count += len(attempt_history)
        if lease_attempt > 1 and len(attempt_history) != lease_attempt - 1:
            task_errors.append("attempt_history_count_mismatch")
        elif lease_attempt == 1 and attempt_history:
            task_errors.append("attempt_history_unexpected_for_first_attempt")
        expected_attempt = 1
        for prior in attempt_history:
            if not isinstance(prior, dict):
                task_errors.append("attempt_history_entry_invalid")
                continue
            try:
                prior_attempt = int(prior.get("lease_attempt") or 0)
            except (TypeError, ValueError):
                prior_attempt = 0
            if prior_attempt != expected_attempt:
                task_errors.append("attempt_history_sequence_invalid")
            expected_attempt += 1
            prior_status = str(prior.get("terminal_status") or "")
            if prior_status not in {
                "succeeded",
                "failed",
                "cancelled",
                "interrupted",
                "timed_out",
                "stale_on_completion",
            }:
                task_errors.append("attempt_history_terminal_status_invalid")
            prior_run_ids = _strings(prior.get("hermes_run_ids"))
            prior_run_id = str(prior.get("hermes_run_id") or "")
            if prior_run_id and prior_run_id not in prior_run_ids:
                task_errors.append("attempt_history_run_identity_invalid")
            for observed_run_id in prior_run_ids:
                if observed_run_id in claimed_hermes_run_ids:
                    task_errors.append(f"attempt_history_run_id_not_unique:{observed_run_id}")
                claimed_hermes_run_ids.add(observed_run_id)
            prior_hashes = prior.get("output_artifact_hashes")
            if not isinstance(prior_hashes, dict):
                task_errors.append("attempt_history_output_hashes_invalid")
                prior_hashes = {}
            prior_paths = _strings(prior.get("output_artifact_paths"))
            if len(prior_paths) != len(set(prior_paths)) or set(prior_paths) != set(prior_hashes):
                task_errors.append("attempt_history_raw_output_manifest_mismatch")
            task_errors.extend(
                f"attempt_history_{error}"
                for error in _attempt_raw_output_cardinality_errors(
                    bundle,
                    task_id=task_id,
                    run_ids=prior_run_ids,
                    model_execution_audit=prior.get("model_execution_audit"),
                    output_paths=prior_paths,
                )
            )
            for relative in prior_paths:
                path = _contained_path(bundle, relative)
                expected_hash = str(prior_hashes.get(relative) or "").lower()
                if (
                    path is None
                    or f"/{task_id}/" not in path.as_posix()
                    or not path.is_file()
                    or not DIGEST_RE.fullmatch(expected_hash)
                    or _file_digest(path) != expected_hash
                ):
                    task_errors.append(f"attempt_history_raw_output_invalid:{relative}")
            prior_model_context = {
                **task,
                "hermes_run_id": prior_run_id or None,
                "hermes_run_ids": prior_run_ids,
                "model_execution_audit": prior.get("model_execution_audit"),
            }
            prior_model_errors = _model_execution_audit_errors(
                prior_model_context,
                events,
                require_succeeded_terminal=False,
            )
            task_errors.extend(
                f"attempt_history_model_execution:{prior_attempt}:{error}"
                for error in prior_model_errors
            )
            prior_event_type = (
                "ic_phase_hermes_task_completed"
                if prior_status in {"succeeded", "stale_on_completion"}
                else "ic_phase_hermes_task_failed"
            )
            prior_events = _matching_audit_events(
                events,
                event_type=prior_event_type,
                expected={
                    "workflow_run_id": task.get("workflow_run_id"),
                    "task_id": task_id,
                    "phase": task.get("phase"),
                    "agent_id": task.get("agent_id"),
                    "input_digest": digest,
                    "handoff_digest": task.get("handoff_digest"),
                    "hermes_run_id": prior_run_id or None,
                    "evidence_snapshot_hash": expected_snapshot_hash,
                    "prompt_contract_version": task.get("prompt_contract_version"),
                    "profile_contract_version": task.get("profile_contract_version"),
                    "output_schema": task.get("output_schema"),
                    "output_artifact_hashes": prior_hashes,
                    "contract_validation": prior.get("contract_validation"),
                    "model_execution_audit": prior.get("model_execution_audit"),
                    "status": prior_status,
                },
            )
            if not prior_events:
                task_errors.append("attempt_history_failure_audit_missing")
            elif len(prior_events) != 1:
                task_errors.append("attempt_history_audit_cardinality_invalid")
        run_id = str(task.get("hermes_run_id") or "")
        run_ids = _strings(task.get("hermes_run_ids") or [run_id])
        if not run_id or run_id not in run_ids:
            task_errors.append("hermes_run_identity_invalid")
        for observed_run_id in run_ids:
            if observed_run_id in claimed_hermes_run_ids:
                task_errors.append(f"hermes_run_id_not_unique:{observed_run_id}")
            claimed_hermes_run_ids.add(observed_run_id)
        model_execution_errors = _model_execution_audit_errors(task, events)
        task_errors.extend(model_execution_errors)
        if not model_execution_errors:
            runtime_verified_task_count += 1
        if not isinstance(task.get("validated_output"), dict):
            task_errors.append("validated_output_missing")
        output_schema = str(task.get("output_schema") or "")
        if not output_schema:
            task_errors.append("output_schema_missing")
        phase = str(task.get("phase") or "")
        expected_output_schemas = {
            "R0": {"siq_ic_r0_readiness_v1"},
            "R1A": {EXPERT_REPORT_SCHEMA},
            "R1B": {EXPERT_REPORT_SCHEMA},
            "R1.5": {"siq_ic_r1_5_chairman_rulings_v2"},
            "R2": {R2_REVISION_SCHEMA},
            "R3": {"siq_ic_r3_debate_turn_v1", "siq_ic_r3_debate_verdict_v1"},
            "R4": {"siq_ic_r4_decision_v2"},
        }.get(phase, set())
        if output_schema not in expected_output_schemas:
            task_errors.append("phase_output_schema_invalid")
        contract_validation = task.get("contract_validation")
        if (
            not isinstance(contract_validation, dict)
            or contract_validation.get("passed") is not True
            or contract_validation.get("validated_by") != "ic_phase_orchestrator"
            or contract_validation.get("output_schema") != task.get("output_schema")
        ):
            task_errors.append("contract_validation_invalid")
        expected_artifact_schema = {
            "R0": "siq_ic_r0_readiness_v1",
            "R1A": EXPERT_REPORT_SCHEMA,
            "R1B": EXPERT_REPORT_SCHEMA,
            "R1.5": None,
            "R2": EXPERT_REPORT_SCHEMA,
            "R3": output_schema,
            "R4": "siq_ic_r4_decision_v2",
        }.get(phase)
        if isinstance(contract_validation, dict) and (
            contract_validation.get("artifact_schema") != expected_artifact_schema
        ):
            task_errors.append("contract_artifact_schema_mismatch")
        workflow = workflow_runs.get(str(task.get("workflow_run_id") or ""))
        if not isinstance(workflow, dict):
            task_errors.append("workflow_run_missing")
        else:
            if workflow.get("deal_id") != expected_deal_id:
                task_errors.append("workflow_deal_id_mismatch")
            if workflow.get("evidence_snapshot_hash") != expected_snapshot_hash:
                task_errors.append("workflow_snapshot_mismatch")
            if str(workflow.get("status") or "") not in {"active", "completed", "succeeded"}:
                task_errors.append("workflow_status_invalid")

        handoff_id = str(task.get("handoff_id") or "")
        handoff_digest = str(task.get("handoff_digest") or "")
        if handoff_id in claimed_handoff_ids:
            task_errors.append("handoff_reused_by_current_task")
        claimed_handoff_ids.add(handoff_id)
        handoff = handoffs_by_id.get(handoff_id)
        if not isinstance(handoff, dict):
            task_errors.append("handoff_missing")
        else:
            if handoff.get("schema_version") != "siq_ic_agent_handoff_v2":
                task_errors.append("handoff_schema_invalid")
            if not HANDOFF_ID_RE.fullmatch(handoff_id) or handoff_id != f"ICHANDOFF-{handoff_digest[:24].upper()}":
                task_errors.append("handoff_id_digest_mismatch")
            if handoff.get("input_digest") != handoff_digest or not DIGEST_RE.fullmatch(handoff_digest):
                task_errors.append("handoff_digest_mismatch")
            handoff_body = {
                key: value
                for key, value in handoff.items()
                if key not in {"schema_version", "handoff_id", "input_digest", "created_at"}
            }
            if _canonical_digest(handoff_body) != handoff_digest:
                task_errors.append("handoff_content_digest_mismatch")
            for key, expected in (
                ("workflow_run_id", task.get("workflow_run_id")),
                ("deal_id", expected_deal_id),
                ("phase", task.get("phase")),
                ("to_agent_id", task.get("agent_id")),
                ("evidence_snapshot_hash", expected_snapshot_hash),
            ):
                if handoff.get(key) != expected:
                    task_errors.append(f"handoff_{key}_mismatch")
            sidecar = handoff_payloads.get(handoff_id)
            if not isinstance(sidecar, dict):
                task_errors.append("handoff_sidecar_missing")
            else:
                sidecar_body = {
                    "reports": _as_list(sidecar.get("reports")),
                    "payload": dict(sidecar.get("payload") or {}),
                    "project_evidence_ids": list(dict.fromkeys(_strings(sidecar.get("project_evidence_ids")))),
                    "source_ids": list(dict.fromkeys(_strings(sidecar.get("source_ids")))),
                    "background_knowledge": dict(sidecar.get("background_knowledge") or {}),
                }
                computed_sidecar_digest = _canonical_digest(sidecar_body)
                if (
                    sidecar.get("handoff_id") != handoff_id
                    or sidecar.get("content_digest") != computed_sidecar_digest
                    or handoff.get("sidecar_digest") != computed_sidecar_digest
                ):
                    task_errors.append("handoff_sidecar_binding_invalid")

        output_paths = _strings(task.get("output_artifact_paths"))
        output_hashes = task.get("output_artifact_hashes")
        if not isinstance(output_hashes, dict):
            output_hashes = {}
            task_errors.append("raw_output_hashes_missing")
        if not output_paths or len(output_paths) != len(set(output_paths)) or set(output_paths) != set(output_hashes):
            task_errors.append("raw_output_manifest_mismatch")
        for relative in output_paths:
            if relative in claimed_raw_paths:
                task_errors.append(f"raw_output_path_not_unique:{relative}")
            claimed_raw_paths.add(relative)
        for hermes_run_id in run_ids:
            matching_paths = [
                item
                for item in output_paths
                if _raw_output_path(
                    bundle,
                    task_id=task_id,
                    hermes_run_id=hermes_run_id,
                    relative=item,
                )
                is not None
            ]
            if len(matching_paths) != 1:
                task_errors.append(f"raw_output_cardinality_invalid:{hermes_run_id}")
                continue
            for relative in matching_paths:
                path = _contained_path(bundle, relative)
                if (
                    path is None
                    or f"/{task_id}/" not in path.as_posix()
                    or not path.is_file()
                    or path.stat().st_size <= 0
                ):
                    task_errors.append(f"raw_output_invalid:{relative}")
                else:
                    expected_hash = str(output_hashes.get(relative) or "").lower()
                    if not DIGEST_RE.fullmatch(expected_hash) or _file_digest(path) != expected_hash:
                        task_errors.append(f"raw_output_digest_mismatch:{relative}")

        completion_expected = {
            "workflow_run_id": task.get("workflow_run_id"),
            "task_id": task_id,
            "phase": task.get("phase"),
            "agent_id": task.get("agent_id"),
            "input_digest": digest,
            "handoff_digest": handoff_digest,
            "hermes_run_id": run_id,
            "evidence_snapshot_hash": expected_snapshot_hash,
            "prompt_contract_version": "siq_ic_phase_prompt_v5",
            "profile_contract_version": "hermes_profile_authority_v1",
            "output_schema": task.get("output_schema"),
            "output_artifact_hashes": output_hashes,
            "contract_validation": contract_validation,
            "model_execution_audit": task.get("model_execution_audit"),
            "status": "succeeded",
        }
        completion_matches = _matching_audit_events(
            events,
            event_type="ic_phase_hermes_task_completed",
            expected=completion_expected,
        )
        if not completion_matches:
            task_errors.append("completion_audit_missing")
        elif len(completion_matches) != 1:
            task_errors.append("completion_audit_cardinality_invalid")
        if task_errors:
            errors.extend(f"{task_id}:{error}" for error in task_errors)
        else:
            valid_tasks[task_id] = task

    observed_phases = {str(task.get("phase") or "") for task in valid_tasks.values()}
    missing_phases = sorted(REQUIRED_MODEL_PHASES - observed_phases)
    if missing_phases:
        errors.append(f"required_model_phases_missing:{','.join(missing_phases)}")
    observed_profiles = {str(task.get("agent_id") or "") for task in valid_tasks.values()}
    missing_profiles = sorted(REQUIRED_PROFILE_IDS - observed_profiles)
    if missing_profiles:
        errors.append(f"required_profiles_missing:{','.join(missing_profiles)}")
    for phase, agents in (
        ("R1A", R2_AGENT_IDS - {"siq_ic_risk_controller"}),
        ("R1B", {"siq_ic_risk_controller", "siq_ic_chairman"}),
        ("R2", R2_AGENT_IDS),
    ):
        observed = {str(task.get("agent_id") or "") for task in valid_tasks.values() if task.get("phase") == phase}
        missing = sorted(agents - observed)
        if missing:
            errors.append(f"{phase}:required_agents_missing:{','.join(missing)}")

    errors.extend(_phase_task_binding_errors(artifacts, valid_tasks))

    factcheck_errors: list[str] = []
    factcheck_runtime_verified = False
    factcheck_prior_attempt_count = 0
    if not isinstance(factcheck_task, dict):
        factcheck_errors.append("task_missing")
    else:
        fact_digest = str(factcheck_task.get("input_digest") or "")
        fact_task_id = str(factcheck_task.get("task_id") or "")
        fact_run_id = str(factcheck_task.get("hermes_run_id") or "")
        expected_fact_fields = {
            "schema_version": "siq_ic_factcheck_task_v1",
            "deal_id": expected_deal_id,
            "phase": "R4",
            "agent_id": "siq_factchecker",
            "report_id": str((factcheck_payload or {}).get("report_id") or ""),
            "report_revision": (factcheck_payload or {}).get("report_revision"),
            "evidence_snapshot_hash": expected_snapshot_hash,
            "prompt_contract_version": "siq_ic_phase_prompt_v5",
            "profile_contract_version": "hermes_profile_authority_v1",
            "output_schema": FACTCHECK_SCHEMA,
            "status": "succeeded",
            "generation_mode": "hermes_model",
            "hermes_called": True,
        }
        for key, expected in expected_fact_fields.items():
            if factcheck_task.get(key) != expected:
                factcheck_errors.append(f"{key}_mismatch")
        if not DIGEST_RE.fullmatch(fact_digest) or fact_task_id != f"ICFACT-{fact_digest[:24].upper()}":
            factcheck_errors.append("task_id_digest_mismatch")
        fact_claim = factcheck_task.get("task_claim")
        if not isinstance(fact_claim, Mapping):
            factcheck_errors.append("task_claim_missing")
            fact_lease_attempt = 0
        else:
            try:
                fact_lease_attempt = int(fact_claim.get("attempt") or 0)
            except (TypeError, ValueError):
                fact_lease_attempt = 0
            if fact_lease_attempt < 1 or fact_claim.get("status") != "succeeded":
                factcheck_errors.append("task_claim_terminal_identity_invalid")
        fact_attempt_history = _as_list(factcheck_task.get("attempt_history"))
        factcheck_prior_attempt_count = len(fact_attempt_history)
        if fact_lease_attempt > 1 and len(fact_attempt_history) != fact_lease_attempt - 1:
            factcheck_errors.append("attempt_history_count_mismatch")
        elif fact_lease_attempt == 1 and fact_attempt_history:
            factcheck_errors.append("attempt_history_unexpected_for_first_attempt")
        for expected_attempt, prior in enumerate(fact_attempt_history, start=1):
            if not isinstance(prior, Mapping):
                factcheck_errors.append("attempt_history_entry_invalid")
                continue
            try:
                prior_attempt = int(prior.get("lease_attempt") or 0)
            except (TypeError, ValueError):
                prior_attempt = 0
            if prior_attempt != expected_attempt:
                factcheck_errors.append("attempt_history_sequence_invalid")
            prior_status = str(prior.get("terminal_status") or "")
            if prior_status not in {
                "succeeded",
                "failed",
                "cancelled",
                "interrupted",
                "timed_out",
                "stale_on_completion",
            }:
                factcheck_errors.append("attempt_history_terminal_status_invalid")
            prior_run_id = str(prior.get("hermes_run_id") or "")
            prior_run_ids = _strings(prior.get("hermes_run_ids"))
            if prior_run_id and prior_run_id not in prior_run_ids:
                factcheck_errors.append("attempt_history_run_identity_invalid")
            for observed_run_id in prior_run_ids:
                if observed_run_id in claimed_hermes_run_ids:
                    factcheck_errors.append(f"attempt_history_run_id_not_unique:{observed_run_id}")
                claimed_hermes_run_ids.add(observed_run_id)

            prior_hashes = prior.get("output_artifact_hashes")
            if not isinstance(prior_hashes, Mapping):
                factcheck_errors.append("attempt_history_output_hashes_invalid")
                prior_hashes = {}
            else:
                prior_hashes = dict(prior_hashes)
            prior_paths = _strings(prior.get("output_artifact_paths"))
            if len(prior_paths) != len(set(prior_paths)) or set(prior_paths) != set(prior_hashes):
                factcheck_errors.append("attempt_history_raw_output_manifest_mismatch")
            factcheck_errors.extend(
                f"attempt_history_{error}"
                for error in _attempt_raw_output_cardinality_errors(
                    bundle,
                    task_id=fact_task_id,
                    run_ids=prior_run_ids,
                    model_execution_audit=prior.get("model_execution_audit"),
                    output_paths=prior_paths,
                )
            )
            for relative in prior_paths:
                if relative in claimed_raw_paths:
                    factcheck_errors.append(f"attempt_history_raw_output_path_not_unique:{relative}")
                claimed_raw_paths.add(relative)
                path = _contained_path(bundle, relative)
                expected_hash = str(prior_hashes.get(relative) or "").lower()
                if (
                    path is None
                    or f"/{fact_task_id}/" not in path.as_posix()
                    or not path.is_file()
                    or not DIGEST_RE.fullmatch(expected_hash)
                    or _file_digest(path) != expected_hash
                ):
                    factcheck_errors.append(f"attempt_history_raw_output_invalid:{relative}")

            prior_model_context = {
                **factcheck_task,
                "hermes_run_id": prior_run_id or None,
                "hermes_run_ids": prior_run_ids,
                "model_execution_audit": prior.get("model_execution_audit"),
            }
            prior_model_errors = _model_execution_audit_errors(
                prior_model_context,
                events,
                require_succeeded_terminal=False,
            )
            factcheck_errors.extend(
                f"attempt_history_model_execution:{prior_attempt}:{error}"
                for error in prior_model_errors
            )
            prior_event_type = (
                "ic_r4_factcheck_completed"
                if prior_status in {"succeeded", "stale_on_completion"}
                else "ic_r4_factcheck_failed"
            )
            prior_events = _matching_audit_events(
                events,
                event_type=prior_event_type,
                expected={
                    "workflow_run_id": factcheck_task.get("workflow_run_id"),
                    "task_id": fact_task_id,
                    "phase": "R4",
                    "agent_id": "siq_factchecker",
                    "report_id": factcheck_task.get("report_id"),
                    "report_revision": factcheck_task.get("report_revision"),
                    "input_digest": fact_digest,
                    "hermes_run_id": prior_run_id or None,
                    "evidence_snapshot_hash": expected_snapshot_hash,
                    "prompt_contract_version": "siq_ic_phase_prompt_v5",
                    "profile_contract_version": "hermes_profile_authority_v1",
                    "output_schema": FACTCHECK_SCHEMA,
                    "output_artifact_hashes": prior_hashes,
                    "contract_validation": prior.get("contract_validation"),
                    "model_execution_audit": prior.get("model_execution_audit"),
                    "status": prior_status,
                },
            )
            if not prior_events:
                factcheck_errors.append("attempt_history_terminal_audit_missing")
            elif len(prior_events) != 1:
                factcheck_errors.append("attempt_history_terminal_audit_cardinality_invalid")
        fact_run_ids = _strings(factcheck_task.get("hermes_run_ids") or [fact_run_id])
        if not fact_run_id or fact_run_id not in fact_run_ids:
            factcheck_errors.append("hermes_run_identity_invalid")
        for observed_run_id in fact_run_ids:
            if observed_run_id in claimed_hermes_run_ids:
                factcheck_errors.append(f"hermes_run_id_not_unique:{observed_run_id}")
            claimed_hermes_run_ids.add(observed_run_id)
        fact_model_execution_errors = _model_execution_audit_errors(factcheck_task, events)
        factcheck_errors.extend(fact_model_execution_errors)
        factcheck_runtime_verified = not fact_model_execution_errors
        if str(factcheck_task.get("workflow_run_id") or "") not in observed_workflow_ids:
            factcheck_errors.append("workflow_run_mismatch")
        fact_contract = factcheck_task.get("contract_validation")
        if (
            not isinstance(fact_contract, dict)
            or fact_contract.get("passed") is not True
            or fact_contract.get("output_schema") != FACTCHECK_SCHEMA
            or fact_contract.get("artifact_schema") != FACTCHECK_SCHEMA
            or fact_contract.get("validated_by") != "ic_phase_orchestrator"
        ):
            factcheck_errors.append("contract_validation_invalid")
        validated_factcheck = factcheck_task.get("validated_output")
        if not isinstance(validated_factcheck, dict) or validated_factcheck != factcheck_payload:
            factcheck_errors.append("validated_output_artifact_mismatch")
        fact_output_paths = _strings(factcheck_task.get("output_artifact_paths"))
        fact_output_hashes = factcheck_task.get("output_artifact_hashes")
        if not isinstance(fact_output_hashes, dict):
            fact_output_hashes = {}
            factcheck_errors.append("raw_output_hashes_missing")
        if (
            not fact_output_paths
            or len(fact_output_paths) != len(set(fact_output_paths))
            or set(fact_output_paths) != set(fact_output_hashes)
        ):
            factcheck_errors.append("raw_output_manifest_mismatch")
        for relative in fact_output_paths:
            if relative in claimed_raw_paths:
                factcheck_errors.append(f"raw_output_path_not_unique:{relative}")
            claimed_raw_paths.add(relative)
        for hermes_run_id in fact_run_ids:
            matching_paths = [
                item
                for item in fact_output_paths
                if _raw_output_path(
                    bundle,
                    task_id=fact_task_id,
                    hermes_run_id=hermes_run_id,
                    relative=item,
                )
                is not None
            ]
            if len(matching_paths) != 1:
                factcheck_errors.append(f"raw_output_cardinality_invalid:{hermes_run_id}")
                continue
            for relative in matching_paths:
                path = _contained_path(bundle, relative)
                if (
                    path is None
                    or f"/{fact_task_id}/" not in path.as_posix()
                    or not path.is_file()
                    or path.stat().st_size <= 0
                ):
                    factcheck_errors.append(f"raw_output_invalid:{relative}")
                else:
                    expected_hash = str(fact_output_hashes.get(relative) or "").lower()
                    if not DIGEST_RE.fullmatch(expected_hash) or _file_digest(path) != expected_hash:
                        factcheck_errors.append(f"raw_output_digest_mismatch:{relative}")
        fact_event_expected = {
            "workflow_run_id": factcheck_task.get("workflow_run_id"),
            "task_id": fact_task_id,
            "phase": "R4",
            "agent_id": "siq_factchecker",
            "report_id": factcheck_task.get("report_id"),
            "report_revision": factcheck_task.get("report_revision"),
            "input_digest": fact_digest,
            "hermes_run_id": fact_run_id,
            "evidence_snapshot_hash": expected_snapshot_hash,
            "prompt_contract_version": "siq_ic_phase_prompt_v5",
            "profile_contract_version": "hermes_profile_authority_v1",
            "output_schema": FACTCHECK_SCHEMA,
            "output_artifact_hashes": fact_output_hashes,
            "contract_validation": fact_contract,
            "model_execution_audit": factcheck_task.get("model_execution_audit"),
            "status": "succeeded",
            "factcheck_status": (factcheck_payload or {}).get("status"),
        }
        fact_events = _matching_audit_events(
            events,
            event_type="ic_r4_factcheck_completed",
            expected=fact_event_expected,
        )
        if not fact_events:
            factcheck_errors.append("completion_audit_missing")
        elif len(fact_events) != 1:
            factcheck_errors.append("completion_audit_cardinality_invalid")
    errors.extend(f"factcheck:{error}" for error in factcheck_errors)

    smoke_task_ids: set[str] = set()
    profile_tasks = real_smoke_payload.get("profile_tasks") if isinstance(real_smoke_payload, dict) else None
    if not isinstance(profile_tasks, dict) and isinstance(real_smoke_payload, dict):
        profile_results = real_smoke_payload.get("profile_results")
        if isinstance(profile_results, dict):
            profile_tasks = {
                str(profile_id): result.get("tasks")
                for profile_id, result in profile_results.items()
                if isinstance(result, dict)
            }
    if not isinstance(profile_tasks, dict):
        errors.append("real_smoke_profile_tasks_missing")
    else:
        for profile_id in sorted(REQUIRED_PROFILE_IDS):
            records = [item for item in _as_list(profile_tasks.get(profile_id)) if isinstance(item, dict)]
            if not records:
                errors.append(f"real_smoke:{profile_id}:tasks_missing")
            for record in records:
                task_id = str(record.get("task_id") or "")
                smoke_task_ids.add(task_id)
                stored = valid_tasks.get(task_id)
                if not isinstance(stored, dict):
                    errors.append(f"real_smoke:{task_id or 'missing'}:stored_task_missing")
                    continue
                bindings = {
                    "profile_id": stored.get("agent_id"),
                    "phase": stored.get("phase"),
                    "hermes_run_id": stored.get("hermes_run_id"),
                    "input_digest": stored.get("input_digest"),
                    "handoff_digest": stored.get("handoff_digest"),
                    "evidence_snapshot_hash": expected_snapshot_hash,
                    "status": "succeeded",
                }
                for key, expected in bindings.items():
                    if record.get(key) != expected:
                        errors.append(f"real_smoke:{task_id}:{key}_mismatch")
                contract_validation = record.get("contract_validation")
                if not isinstance(contract_validation, dict) or contract_validation.get("passed") is not True:
                    errors.append(f"real_smoke:{task_id}:contract_validation_not_passed")
                else:
                    stored_validation = stored.get("contract_validation")
                    expected_smoke_validation = {
                        "passed": True,
                        "validated_by": "ic_phase_orchestrator",
                        "artifact_schema": (
                            stored_validation.get("artifact_schema") if isinstance(stored_validation, dict) else None
                        ),
                    }
                    if any(
                        contract_validation.get(key) != expected for key, expected in expected_smoke_validation.items()
                    ):
                        errors.append(f"real_smoke:{task_id}:contract_validation_mismatch")
                record_model_audit = record.get("model_execution_audit")
                stored_model_audit = stored.get("model_execution_audit")
                if (
                    not isinstance(record_model_audit, Mapping)
                    or record_model_audit.get("runtime_metadata_status") != "verified"
                ):
                    errors.append(f"real_smoke:{task_id}:model_execution_identity_unverified")
                if _canonical_digest(record_model_audit) != _canonical_digest(stored_model_audit):
                    errors.append(f"real_smoke:{task_id}:model_execution_audit_mismatch")
    referenced_ids: set[str] = set()
    for name in ("r1", "r1_5", "r2", "r3", "r4"):
        referenced_ids.update(_ids_with_prefix(artifacts.get(name, {}).get("payload"), TASK_ID_RE))
    missing_smoke_bindings = sorted((referenced_ids & set(valid_tasks)) - smoke_task_ids)
    if missing_smoke_bindings:
        errors.append(f"real_smoke:authoritative_tasks_missing:{','.join(missing_smoke_bindings)}")

    return {
        "passed": not errors,
        "workflow_run_ids": sorted(observed_workflow_ids),
        "authoritative_task_count": len(authoritative_task_ids),
        "successful_task_count": len(current_tasks),
        "validated_task_count": len(valid_tasks),
        "runtime_verified_task_count": runtime_verified_task_count,
        "historical_terminal_task_count": len(historical_terminal_tasks),
        "historical_succeeded_task_count": sum(
            task.get("status") == "succeeded" for task in historical_terminal_tasks
        ),
        "historical_stale_task_count": sum(
            task.get("status") == "stale_on_completion" for task in historical_terminal_tasks
        ),
        "historical_failed_task_count": sum(
            task.get("status") in {"failed", "cancelled", "interrupted", "timed_out"}
            for task in historical_terminal_tasks
        ),
        "phase_count": len(observed_phases),
        "profile_count": len(observed_profiles & REQUIRED_PROFILE_IDS),
        "handoff_count": len(handoffs),
        "raw_hermes_run_count": sum(len(_strings(task.get("hermes_run_ids"))) for task in valid_tasks.values()),
        "prior_attempt_count": prior_attempt_count,
        "factcheck_task_validated": not factcheck_errors,
        "factcheck_runtime_verified": factcheck_runtime_verified,
        "factcheck_prior_attempt_count": factcheck_prior_attempt_count,
        "errors": errors,
    }


def _golden_case_binding_metric(
    bundle: Path,
    payload: Any,
    manifest_validation: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"passed": False, "suite_id": None, "case_count": 0, "errors": ["bindings_missing_or_invalid"]}
    errors: list[str] = []
    if payload.get("schema_version") != "siq_ic_golden_case_bindings_v1":
        errors.append("bindings_schema_invalid")
    suite_id = str(payload.get("suite_id") or "").strip()
    if not suite_id:
        errors.append("suite_id_missing")
    if str(payload.get("status") or "").lower() != "passed":
        errors.append("suite_not_passed")
    if payload.get("quality_accepted") is not False:
        errors.append("suite_quality_accepted_must_remain_false")
    if _parse_timestamp(payload.get("generated_at")) is None:
        errors.append("generated_at_invalid")

    bindings = [item for item in _as_list(payload.get("bindings")) if isinstance(item, dict)]
    by_case = {str(item.get("case_id") or ""): item for item in bindings if item.get("case_id")}
    if len(by_case) != len(bindings):
        errors.append("binding_case_ids_not_unique")
    unexpected_cases = sorted(set(by_case) - REQUIRED_INDEPENDENT_GOLDEN_CASE_IDS)
    if unexpected_cases:
        errors.append(f"unexpected_binding_cases:{','.join(unexpected_cases)}")
    run_ids: set[str] = set()
    result_ids: set[str] = set()
    deal_ids: set[str] = set()
    bundle_paths: set[str] = set()
    canonical_bundle_paths: set[Path] = set()
    canonical_result_paths: set[Path] = set()
    canonical_path_artifacts: set[Path] = set()
    results: list[dict[str, Any]] = []
    manifest_cases = {
        str(item.get("case_id") or ""): item
        for item in (manifest_validation.get("coverage") or {}).values()
        if isinstance(item, dict)
    }
    suite_root = bundle.parent.resolve()

    for case_id in sorted(REQUIRED_INDEPENDENT_GOLDEN_CASE_IDS):
        binding = by_case.get(case_id)
        case_errors: list[str] = []
        if not isinstance(binding, dict):
            errors.append(f"{case_id}:binding_missing")
            results.append({"case_id": case_id, "passed": False, "errors": ["binding_missing"]})
            continue
        run_id = str(binding.get("run_id") or "").strip()
        result_id = str(binding.get("result_id") or "").strip()
        deal_id = str(binding.get("deal_id") or "").strip()
        bundle_relative = str(binding.get("bundle_path") or "").strip()
        result_relative = str(binding.get("result_path") or "").strip()
        expected_result_digest = str(binding.get("result_sha256") or "").strip().lower()
        if not run_id:
            case_errors.append("run_id_missing")
        if not result_id:
            case_errors.append("result_id_missing")
        if not deal_id:
            case_errors.append("deal_id_missing")
        if run_id in run_ids:
            case_errors.append("run_id_not_independent")
        if result_id in result_ids:
            case_errors.append("result_id_not_independent")
        if deal_id in deal_ids:
            case_errors.append("deal_id_not_independent")
        if bundle_relative in bundle_paths:
            case_errors.append("bundle_path_not_independent")
        run_ids.add(run_id)
        result_ids.add(result_id)
        deal_ids.add(deal_id)
        bundle_paths.add(bundle_relative)

        case_bundle = _contained_path(suite_root, bundle_relative)
        if case_bundle is None or case_bundle == bundle.resolve() or not case_bundle.is_dir():
            case_errors.append("case_bundle_invalid")
            result_path = None
        else:
            if case_bundle in canonical_bundle_paths:
                case_errors.append("case_bundle_not_independent")
            canonical_bundle_paths.add(case_bundle)
            if case_bundle.relative_to(suite_root).as_posix() != bundle_relative:
                case_errors.append("case_bundle_path_not_canonical")
            if case_bundle.name != deal_id:
                case_errors.append("case_bundle_deal_id_mismatch")
            result_path = _contained_path(case_bundle, result_relative)
        result_payload: Any = None
        if result_path is None or not result_path.is_file():
            case_errors.append("result_missing")
        elif not DIGEST_RE.fullmatch(expected_result_digest):
            case_errors.append("result_digest_invalid")
        elif _file_digest(result_path) != expected_result_digest:
            case_errors.append("result_digest_mismatch")
        else:
            if result_path in canonical_result_paths:
                case_errors.append("result_path_not_independent")
            canonical_result_paths.add(result_path)
            if result_path.relative_to(case_bundle).as_posix() != result_relative:
                case_errors.append("result_path_not_canonical")
            result_payload, result_error = _read_json(result_path)
            if result_error:
                case_errors.append(f"result_{result_error}")

        if isinstance(result_payload, dict):
            expected_fields = {
                "schema_version": GOLDEN_CASE_RESULT_SCHEMA,
                "case_id": case_id,
                "run_id": run_id,
                "result_id": result_id,
                "deal_id": deal_id,
                "status": "passed",
            }
            for key, expected in expected_fields.items():
                if result_payload.get(key) != expected:
                    case_errors.append(f"result_{key}_mismatch")
            if _parse_timestamp(result_payload.get("evaluated_at")) is None:
                case_errors.append("result_evaluated_at_invalid")
            if result_payload.get("quality_accepted") is not False:
                case_errors.append("result_quality_accepted_must_remain_false")
            if _as_list(result_payload.get("errors")):
                case_errors.append("result_contains_errors")
            if not SNAPSHOT_RE.fullmatch(str(result_payload.get("evidence_snapshot_hash") or "")):
                case_errors.append("result_snapshot_invalid")
            evaluator = result_payload.get("evaluator")
            if evaluator != GOLDEN_EVALUATOR:
                case_errors.append("result_evaluator_invalid")
            path_results = result_payload.get("path_results")
            if not isinstance(path_results, dict):
                path_results = {}
                case_errors.append("result_path_results_invalid")
            required_paths = set((manifest_cases.get(case_id) or {}).get("required_paths") or [])
            if set(path_results) != required_paths:
                case_errors.append("result_required_paths_mismatch")
            for required_path in sorted(required_paths):
                path_result = path_results.get(required_path)
                if not isinstance(path_result, dict):
                    case_errors.append(f"path_missing:{required_path}")
                    continue
                if path_result.get("status") != "passed":
                    case_errors.append(f"path_not_passed:{required_path}")
                artifact_path = _contained_path(case_bundle, path_result.get("artifact_path")) if case_bundle else None
                artifact_digest = str(path_result.get("artifact_sha256") or "").lower()
                if artifact_path is None or artifact_path == result_path or not artifact_path.is_file():
                    case_errors.append(f"path_artifact_invalid:{required_path}")
                elif not DIGEST_RE.fullmatch(artifact_digest) or _file_digest(artifact_path) != artifact_digest:
                    case_errors.append(f"path_artifact_digest_mismatch:{required_path}")
                else:
                    if artifact_path.relative_to(case_bundle).as_posix() != str(path_result.get("artifact_path") or ""):
                        case_errors.append(f"path_artifact_path_not_canonical:{required_path}")
                    if artifact_path in canonical_path_artifacts:
                        case_errors.append(f"path_artifact_not_independent:{required_path}")
                    canonical_path_artifacts.add(artifact_path)
                    path_payload, path_error = _read_json(artifact_path)
                    if path_error or not isinstance(path_payload, dict):
                        case_errors.append(f"path_artifact_json_invalid:{required_path}")
                    else:
                        expected_path_fields = {
                            "schema_version": GOLDEN_PATH_EVALUATION_SCHEMA,
                            "case_id": case_id,
                            "required_path": required_path,
                            "deal_id": deal_id,
                            "run_id": run_id,
                            "evidence_snapshot_hash": result_payload.get("evidence_snapshot_hash"),
                            "status": "passed",
                            "quality_accepted": False,
                            "evaluator": GOLDEN_EVALUATOR,
                        }
                        for key, expected in expected_path_fields.items():
                            if path_payload.get(key) != expected:
                                case_errors.append(f"path_{key}_mismatch:{required_path}")
                        sources = path_payload.get("source_artifacts")
                        if not isinstance(sources, list) or not sources:
                            case_errors.append(f"path_source_artifacts_invalid:{required_path}")
                            sources = []
                        source_paths: set[str] = set()
                        for source in sources:
                            if not isinstance(source, dict):
                                case_errors.append(f"path_source_artifact_invalid:{required_path}")
                                continue
                            source_relative = str(source.get("path") or "").strip()
                            source_path = _contained_path(case_bundle, source_relative)
                            if source_relative in source_paths:
                                case_errors.append(f"path_source_artifact_duplicate:{required_path}")
                            source_paths.add(source_relative)
                            if source_path in {None, artifact_path, result_path}:
                                case_errors.append(f"path_source_artifact_invalid:{required_path}")
                                continue
                            if source_path.relative_to(case_bundle).as_posix() != source_relative:
                                case_errors.append(f"path_source_artifact_path_not_canonical:{required_path}")
                            source_exists = source.get("exists") is True
                            source_digest = str(source.get("sha256") or "").lower()
                            if source_exists:
                                if (
                                    not source_path.is_file()
                                    or not DIGEST_RE.fullmatch(source_digest)
                                    or _file_digest(source_path) != source_digest
                                ):
                                    case_errors.append(f"path_source_artifact_digest_mismatch:{required_path}")
                            elif source_path.exists() or source.get("sha256") not in {None, ""}:
                                case_errors.append(f"path_source_artifact_absence_mismatch:{required_path}")
                        assertions = _as_list(path_payload.get("assertions"))
                        if not assertions or any(
                            not isinstance(assertion, dict)
                            or not str(assertion.get("name") or "").strip()
                            or assertion.get("passed") is not True
                            or assertion.get("actual") != assertion.get("expected")
                            for assertion in assertions
                        ):
                            case_errors.append(f"path_assertions_invalid:{required_path}")
                        assertion_names = [
                            str(assertion.get("name") or "") for assertion in assertions if isinstance(assertion, dict)
                        ]
                        if len(assertion_names) != len(set(assertion_names)):
                            case_errors.append(f"path_assertion_names_not_unique:{required_path}")
                        source_observed = next(
                            (
                                assertion
                                for assertion in assertions
                                if isinstance(assertion, dict)
                                and assertion.get("name") == "path.source_artifacts_observed"
                            ),
                            None,
                        )
                        if not isinstance(source_observed, dict) or source_observed.get("actual") is not True:
                            case_errors.append(f"path_source_observation_missing:{required_path}")

        errors.extend(f"{case_id}:{error}" for error in case_errors)
        results.append(
            {
                "case_id": case_id,
                "run_id": run_id,
                "result_id": result_id,
                "deal_id": deal_id,
                "bundle_path": bundle_relative,
                "passed": not case_errors,
                "errors": case_errors,
            }
        )

    return {
        "passed": not errors,
        "suite_id": suite_id or None,
        "case_count": len(results),
        "distinct_run_count": len(run_ids),
        "distinct_deal_count": len(deal_ids),
        "cases": results,
        "errors": errors,
    }


def _named_actor(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    actor_id = str(value.get("id") or value.get("user_id") or "").strip()
    display_name = str(value.get("username") or value.get("name") or "").strip()
    return display_name if actor_id and display_name else ""


def _trusted_actor(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    actor_id = str(value.get("id") or value.get("user_id") or "").strip()
    username = str(value.get("username") or "").strip()
    if not actor_id or not username:
        return None
    return {"id": value.get("id", value.get("user_id")), "username": username}


def _human_confirmation_metric(
    r4_payload: Any,
    workflow_payload: Any,
    workflow_runs_payload: Any,
    audit_payload: Any,
    quality_payload: Any,
    factcheck_payload: Any,
) -> dict[str, Any]:
    confirmation = r4_payload.get("human_confirmation") if isinstance(r4_payload, dict) else None
    if not isinstance(confirmation, dict):
        return {"passed": False, "status": "missing", "errors": ["r4_human_confirmation_missing"]}
    status = str(confirmation.get("status") or "").lower()
    errors: list[str] = []
    if status not in {"confirmed", "approved"}:
        errors.append(f"r4_human_confirmation_not_confirmed:{status or 'missing'}")
    if confirmation.get("confirmed") is not True:
        errors.append("r4_human_confirmation_boolean_not_true")
    actor = _trusted_actor(confirmation.get("confirmed_by") or confirmation.get("approved_by"))
    if actor is None:
        errors.append("r4_human_confirmation_trusted_actor_missing")
    confirmed_at = str(confirmation.get("confirmed_at") or confirmation.get("approved_at") or "").strip()
    if _parse_timestamp(confirmed_at) is None:
        errors.append("r4_human_confirmation_time_invalid")

    decision_body = dict(r4_payload) if isinstance(r4_payload, dict) else {}
    decision_body.pop("human_confirmation", None)
    expected_attestation = {
        "attestation_schema_version": "siq_ic_human_confirmation_attestation_v1",
        "report_id": r4_payload.get("report_id") if isinstance(r4_payload, dict) else None,
        "report_revision": r4_payload.get("revision") if isinstance(r4_payload, dict) else None,
        "workflow_run_id": r4_payload.get("workflow_run_id") if isinstance(r4_payload, dict) else None,
        "evidence_snapshot_hash": (r4_payload.get("evidence_snapshot_hash") if isinstance(r4_payload, dict) else None),
        "decision_sha256": _canonical_digest(decision_body),
        "quality_sha256": _canonical_digest(quality_payload) if isinstance(quality_payload, dict) else None,
        "factcheck_sha256": _canonical_digest(factcheck_payload) if isinstance(factcheck_payload, dict) else None,
    }
    for key, expected in expected_attestation.items():
        if not expected or confirmation.get(key) != expected:
            errors.append(f"r4_human_confirmation_{key}_mismatch")

    workflow_confirmation = None
    if isinstance(workflow_payload, dict):
        phases = workflow_payload.get("phases") if isinstance(workflow_payload.get("phases"), dict) else {}
        r4_phase = phases.get("R4") if isinstance(phases.get("R4"), dict) else {}
        workflow_confirmation = r4_phase.get("human_confirmation")
        if r4_phase.get("human_confirmation_status") != status:
            errors.append("workflow_confirmation_status_mismatch")
    if workflow_confirmation != confirmation:
        errors.append("workflow_confirmation_payload_mismatch")

    workflow_runs = _as_list(workflow_runs_payload.get("runs")) if isinstance(workflow_runs_payload, dict) else []
    workflow_run = next(
        (
            item
            for item in workflow_runs
            if isinstance(item, dict) and item.get("workflow_run_id") == expected_attestation["workflow_run_id"]
        ),
        None,
    )
    if not isinstance(workflow_run, dict):
        errors.append("confirmed_workflow_run_missing")
    else:
        if workflow_run.get("status") != "completed":
            errors.append("confirmed_workflow_run_not_completed")
        if workflow_run.get("completed_at") != confirmed_at:
            errors.append("confirmed_workflow_run_completed_at_mismatch")
        completion = workflow_run.get("completion")
        expected_completion = {
            "status": status,
            "confirmed_by": actor,
            "confirmed_at": confirmed_at,
            **{key: value for key, value in expected_attestation.items() if key != "attestation_schema_version"},
        }
        if completion != expected_completion:
            errors.append("confirmed_workflow_run_attestation_mismatch")

    audit_events: list[dict[str, Any]] = []
    if actor is not None:
        audit_events = _matching_audit_events(
            _audit_events(audit_payload),
            event_type="r4_human_confirmation_updated",
            expected={
                "status": status,
                "confirmed_by": actor,
                **{key: value for key, value in expected_attestation.items() if key != "attestation_schema_version"},
            },
        )
    audit_event = audit_events[0] if audit_events else None
    if audit_event is None:
        errors.append("r4_human_confirmation_audit_missing")
    elif len(audit_events) != 1:
        errors.append("r4_human_confirmation_audit_cardinality_invalid")
    elif (
        _parse_timestamp(confirmed_at) is not None
        and _parse_timestamp(audit_event.get("created_at")) is not None
        and _parse_timestamp(audit_event.get("created_at")) < _parse_timestamp(confirmed_at)
    ):
        errors.append("r4_human_confirmation_audit_precedes_confirmation")
    return {
        "passed": not errors,
        "status": status or "missing",
        "actor": actor,
        "confirmed_at": confirmed_at or None,
        "audit_event_created_at": audit_event.get("created_at") if audit_event else None,
        "attestation": expected_attestation,
        "errors": errors,
    }


def _methodology_approval_metric(
    payload: Any,
    *,
    expected_deal_id: str,
    expected_report_id: str,
    expected_revision: int,
    expected_snapshot_hash: str,
    expected_golden_suite_id: str | None,
    expected_golden_bindings_sha256: str | None,
    human_confirmation: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"passed": False, "errors": ["human_methodology_approval_missing_or_invalid"]}
    errors: list[str] = []
    if payload.get("schema_version") != HUMAN_APPROVAL_SCHEMA:
        errors.append("human_methodology_approval_schema_invalid")
    if str(payload.get("deal_id") or "") != expected_deal_id:
        errors.append("human_methodology_approval_deal_id_mismatch")
    if str(payload.get("status") or "").lower() != "approved":
        errors.append("human_methodology_approval_not_approved")
    if not _named_actor(payload.get("approved_by")):
        errors.append("human_methodology_approval_actor_missing")
    approved_at = _parse_timestamp(payload.get("approved_at"))
    if approved_at is None:
        errors.append("human_methodology_approval_time_invalid")
    if not str(payload.get("methodology_version") or "").strip():
        errors.append("human_methodology_version_missing")
    if payload.get("scope") != "primary_market_ic_behavior_release":
        errors.append("human_methodology_approval_scope_invalid")
    if payload.get("golden_case_suite_id") != expected_golden_suite_id or not expected_golden_suite_id:
        errors.append("human_methodology_approval_golden_suite_mismatch")
    approved_bindings_sha256 = str(payload.get("golden_case_bindings_sha256") or "").strip().lower()
    if not DIGEST_RE.fullmatch(approved_bindings_sha256):
        errors.append("human_methodology_approval_golden_bindings_digest_invalid")
    elif approved_bindings_sha256 != expected_golden_bindings_sha256:
        errors.append("human_methodology_approval_golden_bindings_digest_mismatch")

    report_binding = payload.get("report_binding")
    expected_report_binding = {
        "report_id": expected_report_id,
        "revision": expected_revision,
        "evidence_snapshot_hash": expected_snapshot_hash,
    }
    if not isinstance(report_binding, dict):
        errors.append("human_methodology_approval_report_binding_missing")
    else:
        for key, expected in expected_report_binding.items():
            if report_binding.get(key) != expected:
                errors.append(f"human_methodology_approval_{key}_mismatch")

    confirmation_binding = payload.get("human_confirmation_binding")
    expected_confirmation_binding = {
        "status": human_confirmation.get("status"),
        "confirmed_by": human_confirmation.get("actor"),
        "confirmed_at": human_confirmation.get("confirmed_at"),
        "audit_event_created_at": human_confirmation.get("audit_event_created_at"),
        **dict(human_confirmation.get("attestation") or {}),
    }
    if not isinstance(confirmation_binding, dict):
        errors.append("human_methodology_approval_confirmation_binding_missing")
    else:
        for key, expected in expected_confirmation_binding.items():
            if not expected or confirmation_binding.get(key) != expected:
                errors.append(f"human_methodology_approval_confirmation_{key}_mismatch")
    confirmation_time = _parse_timestamp(human_confirmation.get("confirmed_at"))
    audit_time = _parse_timestamp(human_confirmation.get("audit_event_created_at"))
    if approved_at is not None and confirmation_time is not None and approved_at < confirmation_time:
        errors.append("human_methodology_approval_precedes_confirmation")
    if approved_at is not None and audit_time is not None and approved_at < audit_time:
        errors.append("human_methodology_approval_precedes_confirmation_audit")
    return {
        "passed": not errors,
        "report_id": expected_report_id,
        "revision": expected_revision,
        "golden_case_suite_id": expected_golden_suite_id,
        "golden_case_bindings_sha256": expected_golden_bindings_sha256,
        "errors": errors,
    }


def _profile_collections(profile_matrix: Any) -> dict[str, str]:
    if not isinstance(profile_matrix, dict):
        return {}
    result: dict[str, str] = {}
    for profile in _as_list(profile_matrix.get("profiles")):
        if not isinstance(profile, dict):
            continue
        retrieval = profile.get("retrieval") if isinstance(profile.get("retrieval"), dict) else {}
        profile_id = str(profile.get("id") or "")
        private = str(retrieval.get("private_collection") or "")
        if profile_id and private:
            result[profile_id] = private
    return result


def _routing_rows(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    candidate = value.get("agents") if isinstance(value.get("agents"), dict) else value
    rows: dict[str, dict[str, Any]] = {}
    if isinstance(candidate, dict):
        iterator = candidate.items()
    else:
        iterator = []
    for key, item in iterator:
        if isinstance(item, dict):
            profile_id = str(item.get("profile_id") or item.get("agent_id") or key)
            rows[profile_id] = item
    return rows


def _retrieval_routing_metric(payload: Any, expected: dict[str, str], *, source: str) -> dict[str, Any]:
    rows = _routing_rows(payload)
    errors: list[str] = []
    details: list[dict[str, Any]] = []
    if set(expected) != REQUIRED_PROFILE_IDS:
        errors.append("profile_matrix_must_define_exactly_seven_ic_profiles")
    for profile_id in sorted(REQUIRED_PROFILE_IDS):
        row = rows.get(profile_id)
        row_errors: list[str] = []
        expected_private = expected.get(profile_id)
        if row is None:
            row_errors.append("receipt_missing")
        else:
            if row.get("schema_version") != STARTUP_RECEIPT_SCHEMA:
                row_errors.append("receipt_schema_invalid")
            status = str(row.get("retrieval_status") or row.get("status") or "").lower()
            if status not in {"ready", "completed", "passed", "pass"}:
                row_errors.append("retrieval_not_ready")
            if str(row.get("readiness_status") or "").lower() != "current":
                row_errors.append("receipt_not_current")
            if row.get("milvus_used") is not True:
                row_errors.append("milvus_not_used")
            vector = row.get("vector_retrieval") if isinstance(row.get("vector_retrieval"), dict) else {}
            if vector.get("milvus_used") is not True or vector.get("status") != "completed":
                row_errors.append("vector_milvus_not_completed")
            gate = row.get("gate") if isinstance(row.get("gate"), dict) else {}
            if gate.get("allowed_to_speak") is not True or _as_list(gate.get("blocking_reasons")):
                row_errors.append("private_background_gate_blocked")
            private_hits = row.get("private_hits", row.get("background_knowledge_hit_count"))
            if not isinstance(private_hits, int) or isinstance(private_hits, bool) or private_hits <= 0:
                row_errors.append("private_hits_not_positive")
            physical = row.get("physical_collections") if isinstance(row.get("physical_collections"), dict) else {}
            observed_private = str(physical.get(profile_id) or row.get("private_collection") or "")
            if observed_private != expected_private:
                row_errors.append("private_collection_mismatch")
            observed_shared = str(
                physical.get("siq_deal_shared")
                or physical.get("ic_collaboration_shared")
                or row.get("shared_collection")
                or ""
            )
            if observed_shared not in {"siq_deal_shared", "ic_collaboration_shared"}:
                row_errors.append("shared_collection_mismatch")
            project_hits = _as_list(row.get("project_evidence_hits") or row.get("evidence_hits"))
            background_hits = _as_list(row.get("background_knowledge_hits"))
            background_refs = _as_list(row.get("background_knowledge_refs"))
            if not project_hits:
                row_errors.append("project_evidence_hits_missing")
            if not background_hits:
                row_errors.append("background_knowledge_hits_missing")
            if not background_refs:
                row_errors.append("background_knowledge_refs_missing")
            if any(not isinstance(hit, dict) or hit.get("source_class") != "project_evidence" for hit in project_hits):
                row_errors.append("project_evidence_source_class_invalid")
            if any(
                not isinstance(hit, dict) or hit.get("source_class") != "background_knowledge"
                for hit in background_hits
            ):
                row_errors.append("background_knowledge_source_class_invalid")
            if any(
                not isinstance(ref, dict)
                or ref.get("source_class") != "background_knowledge"
                or not str(ref.get("ref_id") or "").startswith("KBREF-")
                for ref in background_refs
            ):
                row_errors.append("background_knowledge_refs_invalid")
        if row_errors:
            errors.extend(f"{profile_id}:{error}" for error in row_errors)
        details.append({"profile_id": profile_id, "private_collection": expected_private, "errors": row_errors})
    distinct = {item["private_collection"] for item in details if item.get("private_collection")}
    if len(distinct) != 7:
        errors.append("private_collections_not_distinct")
    return {
        "passed": not errors,
        "source": source,
        "profile_count": len(rows),
        "required_profile_count": 7,
        "distinct_private_collections": len(distinct),
        "profiles": details,
        "errors": errors,
    }


def _real_smoke_metric(
    payload: Any,
    expected: dict[str, str],
    *,
    expected_deal_id: str,
    expected_snapshot_hash: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"passed": False, "routing": {}, "errors": ["real_smoke_missing_or_invalid"]}
    errors: list[str] = []
    if payload.get("schema_version") != "siq_ic_real_smoke_result_v1":
        errors.append("real_smoke_schema_invalid")
    if str(payload.get("deal_id") or "") != expected_deal_id:
        errors.append("real_smoke_deal_id_mismatch")
    if str(payload.get("evidence_snapshot_hash") or "") != expected_snapshot_hash:
        errors.append("real_smoke_snapshot_mismatch")
    if str(payload.get("execution_mode") or "").lower() not in {"real", "live"}:
        errors.append("real_smoke_execution_mode_not_real")
    if not _status_passed(payload):
        errors.append("real_smoke_not_passed")
    if payload.get("hermes_called") is not True:
        errors.append("real_smoke_hermes_not_called")
    if not str(payload.get("run_id") or "").strip():
        errors.append("real_smoke_run_id_missing")
    if not str(payload.get("completed_at") or "").strip():
        errors.append("real_smoke_completed_at_missing")
    phase_runs = payload.get("phase_runs") if isinstance(payload.get("phase_runs"), dict) else {}
    for phase in ("R0", "R1", "R1.5", "R2", "R3", "R4"):
        phase_run = phase_runs.get(phase)
        if not isinstance(phase_run, dict):
            errors.append(f"phase_run_missing:{phase}")
            continue
        if str(phase_run.get("status") or "").lower() != "passed":
            errors.append(f"phase_run_not_passed:{phase}")
        if phase_run.get("hermes_called") is not True:
            errors.append(f"phase_run_hermes_not_called:{phase}")
        if phase in {"R0", "R1.5", "R2", "R3", "R4"} and phase_run.get("workflow_advanced") is not True:
            errors.append(f"phase_run_workflow_not_advanced:{phase}")
    contract_validation = payload.get("contract_validation")
    if not isinstance(contract_validation, dict) or contract_validation.get("passed") is not True:
        errors.append("real_smoke_contract_validation_not_passed")
    retrievals = payload.get("agent_retrievals")
    if isinstance(retrievals, list):
        retrievals = {
            str(item.get("profile_id") or item.get("agent_id") or ""): item
            for item in retrievals
            if isinstance(item, dict)
        }
    routing = _retrieval_routing_metric(retrievals, expected, source="real_smoke")
    errors.extend(f"routing:{error}" for error in routing.get("errors", []))
    for profile_id, receipt in _routing_rows(retrievals).items():
        if str(receipt.get("deal_id") or "") != expected_deal_id:
            errors.append(f"routing:{profile_id}:deal_id_mismatch")
        if str(receipt.get("evidence_snapshot_hash") or "") != expected_snapshot_hash:
            errors.append(f"routing:{profile_id}:snapshot_mismatch")
    return {"passed": not errors, "routing": routing, "errors": errors}


def _blocking_reasons(metrics: Mapping[str, Mapping[str, Any]], artifact_errors: list[str]) -> list[str]:
    reasons = list(artifact_errors)
    for name, metric in metrics.items():
        if metric.get("passed") is not True:
            errors = metric.get("errors") if isinstance(metric.get("errors"), list) else ["failed"]
            reasons.extend(f"{name}:{error}" for error in errors)
    return sorted(set(reasons))


def inspect_bundle(
    bundle: Path,
    *,
    manifest_validation: dict[str, Any],
    profile_matrix: Any,
    factcheck_path: Path | None = None,
    real_smoke_path: Path | None = None,
    human_approval_path: Path | None = None,
) -> dict[str, Any]:
    bundle = bundle.resolve()
    artifacts, artifact_errors = _load_artifacts(bundle)
    expected_collections = _profile_collections(profile_matrix)

    known, evidence_errors = _known_evidence_ids(bundle, artifacts.get("evidence_index", {}).get("payload"))
    claims, numeric = _claim_metric(artifacts, known)
    decision_path = bundle / str(artifacts.get("decision_markdown", {}).get("path") or "")
    if not decision_path.is_file():
        decision_path = None

    if factcheck_path is None:
        fact_relative, resolved_factcheck = _resolve_artifact(bundle, FACTCHECK_PATHS)
    else:
        resolved_factcheck = factcheck_path
        fact_relative = factcheck_path.name
    factcheck_payload, _factcheck_error = _read_json(resolved_factcheck) if resolved_factcheck else (None, "missing")

    smoke_path = real_smoke_path or bundle / REAL_SMOKE_PATH
    approval_path = human_approval_path or bundle / HUMAN_APPROVAL_PATH
    smoke_payload, _smoke_error = _read_json(smoke_path)
    approval_payload, _approval_error = _read_json(approval_path)
    golden_bindings_path = _contained_path(
        bundle,
        artifacts.get("golden_bindings", {}).get("path"),
    )
    golden_bindings_sha256 = (
        _file_digest(golden_bindings_path)
        if golden_bindings_path is not None and golden_bindings_path.is_file()
        else None
    )
    r4_payload = artifacts.get("r4", {}).get("payload")
    manifest_payload = artifacts.get("manifest", {}).get("payload")
    deal_id = str(manifest_payload.get("deal_id") or bundle.name) if isinstance(manifest_payload, dict) else bundle.name
    decision = r4_payload if isinstance(r4_payload, dict) else {}
    report_id = str(decision.get("report_id") or "")
    snapshot_payload = artifacts.get("evidence_snapshot", {}).get("payload")
    snapshot_hash = str(snapshot_payload.get("snapshot_hash") or "") if isinstance(snapshot_payload, dict) else ""
    revision = decision.get("revision") if isinstance(decision.get("revision"), int) else 0
    double_audit = _double_audit_metric(
        artifacts.get("phase_audit", {}).get("payload"),
        artifacts.get("durable_audit", {}).get("payload"),
    )
    golden_case_bindings = _golden_case_binding_metric(
        bundle,
        artifacts.get("golden_bindings", {}).get("payload"),
        manifest_validation,
    )
    human_confirmation = _human_confirmation_metric(
        r4_payload,
        artifacts.get("workflow", {}).get("payload"),
        artifacts.get("workflow_runs", {}).get("payload"),
        artifacts.get("phase_audit", {}).get("payload"),
        artifacts.get("quality", {}).get("payload"),
        factcheck_payload,
    )

    metrics: dict[str, dict[str, Any]] = {
        "manifest_coverage": manifest_validation,
        "schemas": _schema_metric(artifacts),
        "evidence": _evidence_metric(artifacts, known, evidence_errors),
        "critical_claim_coverage": claims,
        "numeric_trace": numeric,
        "unresolved_disputes": _dispute_metric(artifacts.get("r1_5", {}).get("payload")),
        "r3_review": _r3_metric(artifacts.get("r3", {}).get("payload")),
        "r4_claim_cross_reference": _r4_claim_cross_reference_metric(
            artifacts.get("r4", {}).get("payload")
        ),
        "report_hygiene": _report_hygiene_metric(decision_path),
        "fallback": _fallback_metric(artifacts),
        "artifact_identity": _artifact_identity_metric(
            artifacts,
            expected_deal_id=deal_id,
            expected_snapshot_hash=snapshot_hash,
        ),
        "report_quality": _quality_metric(
            artifacts.get("quality", {}).get("payload"),
            expected_deal_id=deal_id,
            expected_report_id=report_id,
            expected_snapshot_hash=snapshot_hash,
            expected_revision=revision,
        ),
        "factcheck": _factcheck_metric(
            factcheck_payload,
            path=fact_relative,
            expected_deal_id=deal_id,
            expected_report_id=report_id,
            expected_snapshot_hash=snapshot_hash,
            expected_revision=revision,
        ),
        "double_audit": double_audit,
        "execution_chain": _execution_chain_metric(
            bundle,
            artifacts,
            expected_deal_id=deal_id,
            expected_snapshot_hash=snapshot_hash,
            factcheck_payload=factcheck_payload,
            real_smoke_payload=smoke_payload,
        ),
        "human_confirmation": human_confirmation,
        "startup_retrieval": _retrieval_routing_metric(
            artifacts.get("startup_receipts", {}).get("payload"),
            expected_collections,
            source="bundle_receipts",
        ),
        "real_smoke": _real_smoke_metric(
            smoke_payload,
            expected_collections,
            expected_deal_id=deal_id,
            expected_snapshot_hash=snapshot_hash,
        ),
        "golden_case_bindings": golden_case_bindings,
        "human_methodology_approval": _methodology_approval_metric(
            approval_payload,
            expected_deal_id=deal_id,
            expected_report_id=report_id,
            expected_revision=revision,
            expected_snapshot_hash=snapshot_hash,
            expected_golden_suite_id=golden_case_bindings.get("suite_id"),
            expected_golden_bindings_sha256=golden_bindings_sha256,
            human_confirmation=human_confirmation,
        ),
    }
    blockers = _blocking_reasons(metrics, artifact_errors)
    return {
        "deal_id": deal_id,
        "bundle_name": bundle.name,
        "passed": not blockers,
        "release_eligible": not blockers,
        "quality_accepted_written": False,
        "candidate_promotion_performed": False,
        "artifacts": {
            name: {key: value for key, value in item.items() if key != "payload"} for name, item in artifacts.items()
        },
        "metrics": metrics,
        "blocking_reasons": blockers,
    }


def build_report(
    *,
    bundle: Path,
    manifest_path: Path = DEFAULT_MANIFEST,
    profile_matrix_path: Path = DEFAULT_PROFILE_MATRIX,
    factcheck_path: Path | None = None,
    real_smoke_path: Path | None = None,
    human_approval_path: Path | None = None,
) -> dict[str, Any]:
    manifest_payload, manifest_error = _read_json(manifest_path)
    manifest_validation = validate_golden_manifest(manifest_payload)
    if manifest_error:
        manifest_validation["errors"] = [*manifest_validation.get("errors", []), manifest_error]
        manifest_validation["passed"] = False
    profile_matrix, matrix_error = _read_json(profile_matrix_path)
    bundle_report = inspect_bundle(
        bundle,
        manifest_validation=manifest_validation,
        profile_matrix=profile_matrix,
        factcheck_path=factcheck_path,
        real_smoke_path=real_smoke_path,
        human_approval_path=human_approval_path,
    )
    if matrix_error:
        bundle_report["blocking_reasons"].append(f"profile_matrix:{matrix_error}")
        bundle_report["blocking_reasons"] = sorted(set(bundle_report["blocking_reasons"]))
        bundle_report["passed"] = False
        bundle_report["release_eligible"] = False
    return {
        "schema_version": REPORT_SCHEMA,
        "generated_at": _now_iso(),
        "passed": bundle_report["passed"],
        "release_eligible": bundle_report["release_eligible"],
        "gate_policy": "fail_closed",
        "manifest_candidate_status_preserved": True,
        "quality_accepted_written": False,
        "candidate_promotion_performed": False,
        "manifest": {
            "name": manifest_path.name,
            **manifest_validation,
        },
        "bundle": bundle_report,
        "blocking_reasons": bundle_report["blocking_reasons"],
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    bundle = report.get("bundle") if isinstance(report.get("bundle"), dict) else {}
    metrics = bundle.get("metrics") if isinstance(bundle.get("metrics"), dict) else {}
    lines = [
        "# Primary Market IC Behavior Release Gate",
        "",
        f"- Result: `{'PASS' if report.get('passed') else 'FAIL'}`",
        f"- Release eligible: `{str(bool(report.get('release_eligible'))).lower()}`",
        f"- Deal: `{bundle.get('deal_id') or 'unknown'}`",
        "- Gate policy: `fail_closed`",
        "- Candidate promotion performed: `false`",
        "- quality_accepted written: `false`",
        "",
        "## Quality Metrics",
        "",
        "| Metric | Status | Key count |",
        "|---|---:|---:|",
    ]
    key_counts = {
        "manifest_coverage": len((metrics.get("manifest_coverage") or {}).get("coverage") or {}),
        "schemas": len((metrics.get("schemas") or {}).get("observed") or {}),
        "evidence": (metrics.get("evidence") or {}).get("unknown_count", 0),
        "critical_claim_coverage": (metrics.get("critical_claim_coverage") or {}).get("critical_covered", 0),
        "numeric_trace": (metrics.get("numeric_trace") or {}).get("complete_traces", 0),
        "unresolved_disputes": (metrics.get("unresolved_disputes") or {}).get("unresolved", 0),
        "r3_review": (metrics.get("r3_review") or {}).get("mode", "missing"),
        "report_hygiene": (metrics.get("report_hygiene") or {}).get("characters", 0),
        "fallback": len((metrics.get("fallback") or {}).get("markers") or []),
        "factcheck": (metrics.get("factcheck") or {}).get("checked_claims", 0),
        "double_audit": (metrics.get("double_audit") or {}).get("event_count", 0),
        "execution_chain": (metrics.get("execution_chain") or {}).get("validated_task_count", 0),
        "human_confirmation": (metrics.get("human_confirmation") or {}).get("status", "missing"),
        "startup_retrieval": (metrics.get("startup_retrieval") or {}).get("profile_count", 0),
        "real_smoke": (metrics.get("real_smoke") or {}).get("routing", {}).get("profile_count", 0),
        "golden_case_bindings": (metrics.get("golden_case_bindings") or {}).get("case_count", 0),
        "human_methodology_approval": "named"
        if (metrics.get("human_methodology_approval") or {}).get("passed")
        else "missing",
    }
    for name, metric in metrics.items():
        status = "PASS" if isinstance(metric, dict) and metric.get("passed") is True else "FAIL"
        lines.append(f"| `{name}` | {status} | `{key_counts.get(name, '')}` |")
    lines.extend(["", "## Blocking Reasons", ""])
    blockers = report.get("blocking_reasons") if isinstance(report.get("blocking_reasons"), list) else []
    lines.extend([f"- `{reason}`" for reason in blockers] or ["- None"])
    lines.extend(
        [
            "",
            "## Governance Note",
            "",
            "This gate does not mutate the golden-case manifest. A passing result only makes the bundle eligible for a separate reviewed promotion; it never marks a candidate as `quality_accepted`.",
            "",
        ]
    )
    return "\n".join(lines)


def _default_outputs(bundle: Path) -> tuple[Path, Path]:
    root = REPO_ROOT / "artifacts" / "eval-runs" / "primary-market-ic" / bundle.name
    return root / "release-gate.json", root / "release-gate.md"


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True, help="Deal artifact bundle directory")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--profile-matrix", type=Path, default=DEFAULT_PROFILE_MATRIX)
    parser.add_argument("--factcheck-report", type=Path)
    parser.add_argument("--real-smoke-report", type=Path)
    parser.add_argument("--human-approval", type=Path)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-markdown", type=Path)
    parser.add_argument("--stdout", action="store_true", help="Also print the JSON report")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.bundle.is_dir():
        print(f"bundle directory not found: {args.bundle}", file=sys.stderr)
        return 2
    report = build_report(
        bundle=args.bundle,
        manifest_path=args.manifest,
        profile_matrix_path=args.profile_matrix,
        factcheck_path=args.factcheck_report,
        real_smoke_path=args.real_smoke_report,
        human_approval_path=args.human_approval,
    )
    default_json, default_markdown = _default_outputs(args.bundle)
    json_path = args.output_json or default_json
    markdown_path = args.output_markdown or default_markdown
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    json_path.write_text(json_text, encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    if args.stdout:
        print(json_text, end="")
    else:
        print(f"Primary Market IC release gate: {'PASS' if report['passed'] else 'FAIL'}")
        print(f"JSON: {json_path}")
        print(f"Markdown: {markdown_path}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
