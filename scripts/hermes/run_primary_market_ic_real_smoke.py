#!/usr/bin/env python3
"""Run an isolated, resumable real smoke across primary-market IC profiles.

The default is a non-mutating dry run. ``--real`` is required before the
script copies the fixture, performs Deal OS startup retrieval, or calls a
Hermes gateway. The live path only reads Milvus through the normal startup
retrieval service; it never ingests or updates a collection.
"""

# The API service imports below intentionally follow the local sys.path setup.
# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = PROJECT_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services import (
    deal_contracts,  # noqa: E402
    deal_store,  # noqa: E402
    hermes_client,  # noqa: E402
    ic_agent_runtime,  # noqa: E402
    ic_phase_orchestrator,  # noqa: E402
    ic_policy,  # noqa: E402
    ic_startup_retrieval,  # noqa: E402
)

REPORT_SCHEMA = "siq_ic_real_smoke_result_v1"
STATE_SCHEMA = "siq_ic_real_smoke_state_v1"
PHASE_ORDER = ("R0", "R1", "R1.5", "R2", "R3", "R4")
DEFAULT_PHASES = ("R0", "R1")
CREATED_BY = {"id": "primary-market-ic-real-smoke", "username": "real-smoke-runner"}
WORKFLOW_ADVANCE_PHASES = frozenset({"R1.5", "R2", "R3", "R4"})

PHASE_PROFILES: dict[str, tuple[str, ...]] = {
    "R0": (ic_phase_orchestrator.COORDINATOR_AGENT_ID,),
    "R1": tuple(ic_policy.R1_AGENT_SEQUENCE),
    "R1.5": (ic_phase_orchestrator.CHAIRMAN_AGENT_ID,),
    "R2": tuple(ic_phase_orchestrator.R2_AGENT_IDS),
    "R3": (*tuple(ic_phase_orchestrator.R2_AGENT_IDS), ic_phase_orchestrator.CHAIRMAN_AGENT_ID),
    "R4": (ic_phase_orchestrator.CHAIRMAN_AGENT_ID,),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"SMOKE-{stamp}-{uuid.uuid4().hex[:10].upper()}"


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if value and value not in result:
            result.append(value)
    return result


def _ordered_phases(values: Sequence[str] | None) -> list[str]:
    selected = {str(item).strip().upper() for item in (values or DEFAULT_PHASES)}
    unknown = selected - set(PHASE_ORDER)
    if unknown:
        raise ValueError("unknown phase(s): " + ", ".join(sorted(unknown)))
    return [phase for phase in PHASE_ORDER if phase in selected]


def _ordered_profiles(values: Sequence[str] | None) -> list[str]:
    if not values:
        return list(ic_policy.IC_PROFILE_IDS)
    selected: set[str] = set()
    for raw in values:
        profile_id = ic_policy.canonical_ic_profile_id(raw)
        if profile_id not in ic_policy.IC_PROFILE_IDS:
            raise ValueError(f"unknown IC profile: {raw}")
        selected.add(profile_id)
    return [profile_id for profile_id in ic_policy.IC_PROFILE_IDS if profile_id in selected]


def _workflow_blocked(phase: str, phase_state: Mapping[str, Any]) -> bool:
    if phase not in WORKFLOW_ADVANCE_PHASES:
        return False
    if phase_state.get("workflow_blocked") is True:
        return True
    return (
        str(phase_state.get("status") or "") in {"passed", "blocked"}
        and phase_state.get("workflow_advanced") is not True
    )


def _fixture_identity(fixture: Path) -> tuple[Path, str]:
    source = fixture.expanduser().resolve()
    if not source.is_dir():
        raise ValueError("fixture must be a Deal OS package directory")
    manifest = deal_store.read_json(source / "manifest.json", {}) or {}
    project_meta = deal_store.read_json(source / "project_meta.json", {}) or {}
    deal_id = str(manifest.get("deal_id") or project_meta.get("deal_id") or source.name)
    deal_store.validate_deal_id(deal_id)
    required = (
        "manifest.json",
        "project_meta.json",
        "phases/workflow_state.json",
        "evidence/evidence_items.ndjson",
        "evidence/evidence_snapshot.json",
    )
    missing = [relative for relative in required if not (source / relative).is_file()]
    if missing:
        raise ValueError("fixture is missing required artifacts: " + ", ".join(missing))
    manifest_deal_id = str(manifest.get("deal_id") or "")
    if manifest_deal_id and manifest_deal_id != deal_id:
        raise ValueError("fixture manifest deal_id mismatch")
    return source, deal_id


def _safe_gateway(profile_id: str) -> dict[str, str]:
    config = hermes_client.hermes_profile_config(profile_id)
    parsed = urlsplit(str(config.get("base") or ""))
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    endpoint = urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    return {
        "profile_id": profile_id,
        "endpoint": endpoint,
        "model": str(config.get("model") or profile_id),
    }


def _project_hit(hit: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": str(hit.get("evidence_id") or hit.get("id") or ""),
        "source_class": "project_evidence",
    }


def _background_hit(hit: Mapping[str, Any], *, private_collection: str) -> dict[str, Any]:
    return {
        "ref_id": str(
            hit.get("ref_id")
            or hit.get("source_id")
            or hit.get("id")
            or hit.get("chunk_id")
            or ""
        ),
        "collection": str(hit.get("collection") or private_collection),
        "source_class": "background_knowledge",
    }


def sanitize_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Keep routing proof while excluding retrieved text, prompts, and users."""

    profile_id = str(receipt.get("agent_id") or "")
    physical = receipt.get("physical_collections")
    physical_collections = {
        str(key): str(value)
        for key, value in (physical.items() if isinstance(physical, Mapping) else [])
        if str(key or "") and str(value or "")
    }
    private_collection = str(
        physical_collections.get(profile_id)
        or receipt.get("private_collection")
        or profile_id
    )
    project_hits = [
        _project_hit(item)
        for item in receipt.get("project_evidence_hits") or receipt.get("evidence_hits") or []
        if isinstance(item, Mapping) and (item.get("evidence_id") or item.get("id"))
    ]
    background_hits = [
        _background_hit(item, private_collection=private_collection)
        for item in receipt.get("background_knowledge_hits") or []
        if isinstance(item, Mapping)
        and (item.get("ref_id") or item.get("source_id") or item.get("id") or item.get("chunk_id"))
    ]
    background_refs = [
        _background_hit(item, private_collection=private_collection)
        for item in receipt.get("background_knowledge_refs") or []
        if isinstance(item, Mapping) and (item.get("ref_id") or item.get("id") or item.get("chunk_id"))
    ]
    vector = receipt.get("vector_retrieval") if isinstance(receipt.get("vector_retrieval"), Mapping) else {}
    gate = receipt.get("gate") if isinstance(receipt.get("gate"), Mapping) else {}
    return {
        "schema_version": str(receipt.get("schema_version") or ""),
        "receipt_id": str(receipt.get("receipt_id") or ""),
        "deal_id": str(receipt.get("deal_id") or ""),
        "agent_id": profile_id,
        "phase": str(receipt.get("phase") or receipt.get("round_name") or ""),
        "round_name": str(receipt.get("round_name") or ""),
        "retrieval_status": str(receipt.get("retrieval_status") or ""),
        "readiness_status": str(receipt.get("readiness_status") or ""),
        "milvus_used": receipt.get("milvus_used") is True,
        "shared_collection": str(receipt.get("shared_collection") or ""),
        "private_collection": str(receipt.get("private_collection") or profile_id),
        "physical_collections": physical_collections,
        "private_hits": len(background_hits),
        "project_evidence_hits": project_hits,
        "background_knowledge_hits": background_hits,
        "background_knowledge_refs": background_refs,
        "vector_retrieval": {
            "status": str(vector.get("status") or ""),
            "milvus_used": vector.get("milvus_used") is True,
            "collections": _dedupe(vector.get("collections") or []),
            "physical_collections": physical_collections,
        },
        "source_ids": _dedupe(receipt.get("source_ids") or []),
        "evidence_snapshot_hash": str(receipt.get("evidence_snapshot_hash") or ""),
        "gate": {
            "allowed_to_speak": gate.get("allowed_to_speak") is True,
            "blocking_reasons": _dedupe(gate.get("blocking_reasons") or []),
        },
    }


def _receipt_errors(receipt: Mapping[str, Any], *, profile_id: str, snapshot_hash: str) -> list[str]:
    errors: list[str] = []
    if receipt.get("agent_id") != profile_id:
        errors.append("profile_mismatch")
    if receipt.get("retrieval_status") != "ready":
        errors.append("retrieval_not_ready")
    if receipt.get("readiness_status") != "current":
        errors.append("receipt_not_current")
    if receipt.get("milvus_used") is not True:
        errors.append("milvus_not_used")
    vector = receipt.get("vector_retrieval") if isinstance(receipt.get("vector_retrieval"), Mapping) else {}
    if vector.get("milvus_used") is not True or vector.get("status") != "completed":
        errors.append("vector_milvus_not_completed")
    if not receipt.get("project_evidence_hits"):
        errors.append("project_evidence_missing")
    if not receipt.get("background_knowledge_hits") or int(receipt.get("private_hits") or 0) <= 0:
        errors.append("private_kb_empty")
    if not receipt.get("background_knowledge_refs"):
        errors.append("private_kb_refs_missing")
    gate = receipt.get("gate") if isinstance(receipt.get("gate"), Mapping) else {}
    if gate.get("allowed_to_speak") is not True:
        errors.append("receipt_gate_blocked")
    if not snapshot_hash or receipt.get("evidence_snapshot_hash") != snapshot_hash:
        errors.append("snapshot_mismatch")
    return errors


def _task_records(result: Any, *, allowed_profiles: set[str]) -> list[dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            task_id = str(value.get("task_id") or "")
            agent_id = str(value.get("agent_id") or "")
            if (
                task_id
                and agent_id in allowed_profiles
                and value.get("hermes_called") is True
                and str(value.get("status") or "") == "succeeded"
                and str(value.get("hermes_run_id") or "")
                and str(value.get("output_schema") or "")
            ):
                raw_validation = value.get("contract_validation")
                validation = raw_validation if isinstance(raw_validation, Mapping) else {}
                contract_validation = {
                    "passed": validation.get("passed") is True,
                    "validated_by": validation.get("validated_by"),
                    "output_schema": validation.get("output_schema"),
                    "artifact_schema": validation.get("artifact_schema"),
                }
                if validation.get("error_type") is not None:
                    contract_validation["error_type"] = validation.get("error_type")
                candidates[task_id] = {
                    "task_id": task_id,
                    "profile_id": agent_id,
                    "phase": str(value.get("phase") or value.get("round_name") or ""),
                    "round_name": str(value.get("round_name") or ""),
                    "gateway_profile": agent_id,
                    "hermes_run_id": str(value.get("hermes_run_id") or ""),
                    "hermes_run_ids": _dedupe(value.get("hermes_run_ids") or [value.get("hermes_run_id")]),
                    "receipt_id": str(
                        (value.get("startup_retrieval_gate") or {}).get("receipt_id")
                        if isinstance(value.get("startup_retrieval_gate"), Mapping)
                        else ""
                    ),
                    "output_schema": str(value.get("output_schema") or ""),
                    "input_digest": str(value.get("input_digest") or ""),
                    "handoff_digest": str(value.get("handoff_digest") or ""),
                    "evidence_snapshot_hash": str(value.get("evidence_snapshot_hash") or ""),
                    "contract_validation": contract_validation,
                    "model_execution_audit": (
                        deepcopy(value.get("model_execution_audit"))
                        if isinstance(value.get("model_execution_audit"), Mapping)
                        else None
                    ),
                    "status": "succeeded",
                }
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(result)
    return list(candidates.values())


def _r1_records_for_profile(records: Iterable[Mapping[str, Any]], profile_id: str) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in records
        if item.get("profile_id") == profile_id
        and item.get("round_name") == "R1"
        and item.get("phase") in {"R1A", "R1B"}
    ]


def _merge_profile_tasks(state: dict[str, Any], profile_id: str, records: Iterable[Mapping[str, Any]]) -> None:
    rows = state.setdefault("profile_tasks", {}).setdefault(profile_id, [])
    by_task_id = {
        str(item.get("task_id") or ""): dict(item)
        for item in rows
        if isinstance(item, Mapping) and item.get("task_id")
    }
    for item in records:
        task_id = str(item.get("task_id") or "")
        if task_id:
            by_task_id[task_id] = dict(item)
    state["profile_tasks"][profile_id] = list(by_task_id.values())


def _recover_persisted_r1_tasks(package_dir: Path, profiles: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
    raw_reports = deal_store.read_json(package_dir / "phases" / "r1_reports.json", {}) or {}
    reports = raw_reports.get("reports") if isinstance(raw_reports, Mapping) else None
    if not isinstance(reports, Mapping):
        reports = raw_reports if isinstance(raw_reports, Mapping) else {}
    reports_by_profile = {
        ic_policy.canonical_ic_profile_id(str(item.get("agent_id") or key)): item
        for key, item in reports.items()
        if isinstance(item, Mapping)
    }
    task_store = deal_store.read_json(package_dir / ic_phase_orchestrator.TASK_STORE_PATH, {}) or {}
    records = _task_records(task_store, allowed_profiles=set(profiles))
    snapshot = deal_store.read_json(package_dir / "evidence" / "evidence_snapshot.json", {}) or {}
    snapshot_hash = str(snapshot.get("snapshot_hash") or "")
    recovered: dict[str, list[dict[str, Any]]] = {}
    for profile_id in profiles:
        report = reports_by_profile.get(profile_id)
        if not isinstance(report, Mapping):
            continue
        report_task_id = str(report.get("task_id") or "")
        if (
            not report_task_id
            or report.get("status") != "completed"
            or report.get("hermes_called") is not True
            or str(report.get("evidence_snapshot_hash") or "") != snapshot_hash
        ):
            continue
        matches = [
            item
            for item in _r1_records_for_profile(records, profile_id)
            if item.get("task_id") == report_task_id
            and item.get("evidence_snapshot_hash") == snapshot_hash
            and (item.get("contract_validation") or {}).get("passed") is True
        ]
        if matches:
            recovered[profile_id] = matches
    return recovered


def _empty_state(*, run_id: str, deal_id: str, phases: list[str], profiles: list[str]) -> dict[str, Any]:
    now = _now_iso()
    return {
        "schema_version": STATE_SCHEMA,
        "run_id": run_id,
        "deal_id": deal_id,
        "execution_mode": "real",
        "status": "running",
        "isolated_fixture": True,
        "selected_phases": phases,
        "selected_profiles": profiles,
        "phase_runs": {},
        "agent_retrievals": {},
        "profile_tasks": {},
        "errors": [],
        "started_at": now,
        "updated_at": now,
    }


def _write_state(package_dir: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = _now_iso()
    deal_store.write_json(package_dir / "release" / "real_smoke_state.json", state)


def _load_or_create_state(
    package_dir: Path,
    *,
    deal_id: str,
    phases: list[str],
    profiles: list[str],
    resume: bool,
) -> dict[str, Any]:
    path = package_dir / "release" / "real_smoke_state.json"
    existing = deal_store.read_json(path, {}) or {}
    if resume:
        if existing.get("schema_version") != STATE_SCHEMA:
            raise ValueError("resume state missing or invalid")
        if existing.get("deal_id") != deal_id:
            raise ValueError("resume deal_id mismatch")
        existing["selected_phases"] = _dedupe([*existing.get("selected_phases", []), *phases])
        existing["selected_profiles"] = _dedupe([*existing.get("selected_profiles", []), *profiles])
        existing["status"] = "running"
        return existing
    if existing:
        raise ValueError("smoke state already exists; use --resume")
    return _empty_state(run_id=_new_run_id(), deal_id=deal_id, phases=phases, profiles=profiles)


def _phase_selected_profiles(phase: str, selected_profiles: Sequence[str]) -> list[str]:
    selected = set(selected_profiles)
    required = PHASE_PROFILES[phase]
    phase_profiles = [profile_id for profile_id in required if profile_id in selected]
    if not phase_profiles:
        raise ValueError(f"phase {phase} has no selected profile")
    if phase in {"R2", "R3"} and set(phase_profiles) != set(required):
        raise ValueError(f"phase {phase} requires all profiles: {', '.join(required)}")
    return phase_profiles


def _prepare_receipt(
    *,
    deal_id: str,
    profile_id: str,
    phase: str,
    wiki_root: Path,
    limit: int,
) -> dict[str, Any]:
    raw = ic_startup_retrieval.generate_startup_retrieval_receipt(
        deal_id,
        profile_id,
        round_name=phase,
        limit=limit,
        include_external=False,
        include_vector=True,
        include_rerank=False,
        created_by=CREATED_BY,
        wiki_root=wiki_root,
    )
    receipt = sanitize_receipt(raw)
    identity = ic_startup_retrieval.current_evidence_identity(deal_id, wiki_root=wiki_root)
    errors = _receipt_errors(
        receipt,
        profile_id=profile_id,
        snapshot_hash=str(identity.get("evidence_snapshot_hash") or ""),
    )
    if errors:
        raise ValueError(f"startup retrieval blocked for {profile_id}: {', '.join(errors)}")
    return receipt


async def _execute_phase(
    phase: str,
    *,
    deal_id: str,
    profiles: list[str],
    wiki_root: Path,
    timeout: float,
    state: dict[str, Any],
    package_dir: Path,
    retrieval_limit: int,
) -> None:
    phase_state = state.setdefault("phase_runs", {}).setdefault(phase, {})
    if phase_state.get("status") == "passed":
        if _workflow_blocked(phase, phase_state):
            phase_state.update(
                {
                    "status": "blocked",
                    "task_validated": True,
                    "workflow_blocked": True,
                }
            )
            _write_state(package_dir, state)
        return
    if phase_state.get("status") == "blocked" and phase_state.get("task_validated") is True:
        return
    resuming_phase = bool(phase_state.get("started_at"))
    state["errors"] = [
        item
        for item in state.get("errors") or []
        if not isinstance(item, Mapping) or item.get("phase") != phase
    ]
    phase_state.update({"status": "running", "started_at": phase_state.get("started_at") or _now_iso()})
    _write_state(package_dir, state)

    if phase == "R1":
        completed_profiles = set(phase_state.get("completed_profiles") or [])
        if resuming_phase:
            recovered = _recover_persisted_r1_tasks(package_dir, profiles)
            for profile_id, records in recovered.items():
                _merge_profile_tasks(state, profile_id, records)
                completed_profiles.add(profile_id)
            if recovered:
                phase_state["completed_profiles"] = [item for item in profiles if item in completed_profiles]
                _write_state(package_dir, state)
        for profile_id in profiles:
            if profile_id in completed_profiles:
                continue
            receipt = _prepare_receipt(
                deal_id=deal_id,
                profile_id=profile_id,
                phase=phase,
                wiki_root=wiki_root,
                limit=retrieval_limit,
            )
            state["agent_retrievals"][profile_id] = receipt
            result = await ic_agent_runtime.run_workflow_r1_agent(
                deal_id,
                profile_id,
                round_name="R1",
                created_by=CREATED_BY,
                wiki_root=wiki_root,
                timeout=timeout,
            )
            records = _task_records(result, allowed_profiles=set(ic_policy.IC_PROFILE_IDS))
            own_records = _r1_records_for_profile(records, profile_id)
            if not result.get("hermes_called") or not result.get("report_written") or not own_records:
                raise RuntimeError(f"R1 did not complete a validated real task for {profile_id}")
            _merge_profile_tasks(state, profile_id, own_records)
            completed_profiles.add(profile_id)
            phase_state["completed_profiles"] = [item for item in profiles if item in completed_profiles]
            _write_state(package_dir, state)
        phase_state.update({"status": "passed", "completed_at": _now_iso(), "hermes_called": True})
        _write_state(package_dir, state)
        return

    for profile_id in profiles:
        state["agent_retrievals"][profile_id] = _prepare_receipt(
            deal_id=deal_id,
            profile_id=profile_id,
            phase=phase,
            wiki_root=wiki_root,
            limit=retrieval_limit,
        )
    _write_state(package_dir, state)

    if phase == "R0":
        result = await ic_agent_runtime.run_workflow_r0_model(
            deal_id, created_by=CREATED_BY, wiki_root=wiki_root, timeout=timeout
        )
    elif phase == "R1.5":
        result = await ic_agent_runtime.run_workflow_r1_5_model(
            deal_id, created_by=CREATED_BY, wiki_root=wiki_root, timeout=timeout
        )
    elif phase == "R2":
        result = await ic_agent_runtime.run_workflow_r2_async(
            deal_id, mode="model", created_by=CREATED_BY, wiki_root=wiki_root, timeout=timeout
        )
    elif phase == "R3":
        result = await ic_agent_runtime.run_workflow_r3_async(
            deal_id,
            mode="model",
            skip=False,
            created_by=CREATED_BY,
            wiki_root=wiki_root,
            timeout=timeout,
        )
    elif phase == "R4":
        result = await ic_agent_runtime.finalize_workflow_r4_async(
            deal_id,
            mode="model",
            created_by=CREATED_BY,
            wiki_root=wiki_root,
            timeout=timeout,
        )
    else:  # pragma: no cover - guarded by parser and _ordered_phases
        raise ValueError(f"unsupported phase: {phase}")

    records = _task_records(result, allowed_profiles=set(ic_policy.IC_PROFILE_IDS))
    expected_executors = set(profiles)
    actual_executors = {item["profile_id"] for item in records}
    if phase == "R3":
        missing = sorted({ic_phase_orchestrator.CHAIRMAN_AGENT_ID} - actual_executors)
        if not actual_executors.intersection(ic_phase_orchestrator.R2_AGENT_IDS):
            missing.append("dynamic_red_or_blue_expert")
    else:
        missing = sorted(expected_executors - actual_executors)
    if result.get("hermes_called") is not True or missing:
        raise RuntimeError(f"{phase} did not complete validated real tasks: {', '.join(missing) or 'Hermes not called'}")
    if phase == "R0" and result.get("status") != "completed":
        raise RuntimeError(f"R0 readiness did not pass: {result.get('status') or 'unknown'}")
    if str(result.get("status") or "") in {"stale_on_completion", "quality_blocked", "factcheck_blocked"}:
        raise RuntimeError(f"{phase} returned {result.get('status')}")
    for record in records:
        state.setdefault("profile_tasks", {}).setdefault(record["profile_id"], []).append(record)
    task_validated = all(
        (record.get("contract_validation") or {}).get("passed") is True
        for record in records
    )
    if not task_validated:
        raise RuntimeError(f"{phase} returned a task that failed contract validation")
    workflow_blocked = phase in WORKFLOW_ADVANCE_PHASES and result.get("workflow_advanced") is not True
    phase_state.update(
        {
            "status": "blocked" if workflow_blocked else "passed",
            "completed_at": _now_iso(),
            "hermes_called": True,
            "task_validated": task_validated,
            "result_status": str(result.get("status") or "completed"),
            "workflow_advanced": result.get("workflow_advanced") is True,
            "workflow_blocked": workflow_blocked,
        }
    )
    _write_state(package_dir, state)


def _profile_results(state: Mapping[str, Any]) -> dict[str, Any]:
    results: dict[str, Any] = {}
    tasks = state.get("profile_tasks") if isinstance(state.get("profile_tasks"), Mapping) else {}
    for profile_id in ic_policy.IC_PROFILE_IDS:
        rows = [dict(item) for item in tasks.get(profile_id, []) if isinstance(item, Mapping)]
        unique = {str(item.get("task_id") or ""): item for item in rows if item.get("task_id")}
        rows = list(unique.values())
        results[profile_id] = {
            "status": "passed" if rows else "missing",
            "gateway": _safe_gateway(profile_id),
            "task_count": len(rows),
            "phases": _dedupe(item.get("phase") for item in rows),
            "tasks": rows,
        }
    return results


def build_report(state: Mapping[str, Any], *, execution_mode: str) -> dict[str, Any]:
    retrievals = state.get("agent_retrievals") if isinstance(state.get("agent_retrievals"), Mapping) else {}
    profile_results = _profile_results(state)
    snapshots = {
        str(item.get("evidence_snapshot_hash") or "")
        for item in retrievals.values()
        if isinstance(item, Mapping) and item.get("evidence_snapshot_hash")
    }
    source_ids = _dedupe(
        source_id
        for item in retrievals.values()
        if isinstance(item, Mapping)
        for source_id in item.get("source_ids") or []
    )
    validation_errors: list[str] = []
    if execution_mode == "real":
        for phase in state.get("selected_phases") or []:
            phase_state = state.get("phase_runs", {}).get(phase) or {}
            phase_status = "blocked" if _workflow_blocked(phase, phase_state) else str(
                phase_state.get("status") or "missing"
            )
            if phase_status != "passed":
                validation_errors.append(f"phase:{phase}:{phase_status}")
        if set(retrievals) != set(ic_policy.IC_PROFILE_IDS):
            validation_errors.append("seven_profile_receipts_incomplete")
        if len(snapshots) != 1:
            validation_errors.append("snapshot_identity_not_unique")
        for profile_id in ic_policy.IC_PROFILE_IDS:
            receipt = retrievals.get(profile_id)
            if not isinstance(receipt, Mapping):
                validation_errors.append(f"{profile_id}:receipt_missing")
                continue
            validation_errors.extend(
                f"{profile_id}:{error}"
                for error in _receipt_errors(
                    receipt,
                    profile_id=profile_id,
                    snapshot_hash=next(iter(snapshots), ""),
                )
            )
            tasks = profile_results[profile_id]["tasks"]
            if not tasks:
                validation_errors.append(f"{profile_id}:real_task_missing")
            for task in tasks:
                if task.get("evidence_snapshot_hash") not in snapshots:
                    validation_errors.append(f"{profile_id}:task_snapshot_mismatch")
                if not (task.get("contract_validation") or {}).get("passed"):
                    validation_errors.append(f"{profile_id}:contract_not_validated")
                model_audit = task.get("model_execution_audit")
                if (
                    not isinstance(model_audit, Mapping)
                    or model_audit.get("runtime_metadata_status") != "verified"
                ):
                    validation_errors.append(f"{profile_id}:model_execution_identity_unverified")
    state_errors = [dict(item) for item in state.get("errors") or [] if isinstance(item, Mapping)]
    validation_errors.extend(
        f"execution:{item.get('phase') or 'unknown'}:{item.get('error_type') or 'failed'}"
        for item in state_errors
    )
    passed = execution_mode == "real" and not validation_errors
    blocked = any(
        str((state.get("phase_runs", {}).get(phase) or {}).get("status") or "") == "blocked"
        or _workflow_blocked(phase, state.get("phase_runs", {}).get(phase) or {})
        for phase in state.get("selected_phases") or []
    )
    status = (
        "passed"
        if passed
        else "dry_run"
        if execution_mode == "dry_run"
        else "blocked"
        if blocked
        else "failed"
    )
    hermes_called = any(
        isinstance(item, Mapping) and bool(item.get("tasks"))
        for item in profile_results.values()
    )
    return {
        "schema_version": REPORT_SCHEMA,
        "deal_id": str(state.get("deal_id") or ""),
        "execution_mode": execution_mode,
        "status": status,
        "workflow_blocked": blocked,
        "hermes_called": hermes_called,
        "run_id": str(state.get("run_id") or ""),
        "completed_at": _now_iso(),
        "evidence_snapshot_hash": next(iter(snapshots), "") if len(snapshots) == 1 else "",
        "isolated_fixture": True,
        "selected_phases": list(state.get("selected_phases") or []),
        "selected_profiles": list(state.get("selected_profiles") or []),
        "snapshot_identity": {
            "evidence_snapshot_hash": next(iter(snapshots), "") if len(snapshots) == 1 else "",
            "source_ids": source_ids,
        },
        "agent_retrievals": {key: dict(value) for key, value in retrievals.items() if isinstance(value, Mapping)},
        "profile_results": profile_results,
        "contract_validation": {
            "passed": passed,
            "required_profile_count": len(ic_policy.IC_PROFILE_IDS),
            "validated_profile_count": sum(1 for item in profile_results.values() if item["status"] == "passed"),
            "errors": _dedupe(validation_errors),
        },
        "phase_runs": {
            str(key): {
                **{
                    field: value
                    for field, value in item.items()
                    if field
                    in {
                        "status",
                        "started_at",
                        "completed_at",
                        "hermes_called",
                        "task_validated",
                        "result_status",
                        "workflow_advanced",
                        "workflow_blocked",
                        "completed_profiles",
                    }
                },
                **(
                    {"status": "blocked", "workflow_blocked": True}
                    if _workflow_blocked(str(key), item)
                    else {}
                ),
            }
            for key, item in (state.get("phase_runs") or {}).items()
            if isinstance(item, Mapping)
        },
        "errors": state_errors,
    }


def _dry_run_report(*, deal_id: str, phases: list[str], profiles: list[str]) -> dict[str, Any]:
    state = _empty_state(run_id=_new_run_id(), deal_id=deal_id, phases=phases, profiles=profiles)
    state["execution_mode"] = "dry_run"
    state["status"] = "dry_run"
    return build_report(state, execution_mode="dry_run")


async def run_real(
    *,
    fixture: Path,
    run_root: Path,
    phases: list[str],
    profiles: list[str],
    resume: bool,
    timeout: float,
    retrieval_limit: int,
) -> tuple[dict[str, Any], Path]:
    source, deal_id = _fixture_identity(fixture)
    wiki_root = run_root / "wiki"
    package_dir = deal_store.safe_deal_dir(deal_id, wiki_root=wiki_root)
    if resume:
        if not package_dir.is_dir():
            raise ValueError("resume package is missing")
    else:
        if package_dir.exists():
            raise ValueError("isolated package already exists; use --resume")
        package_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, package_dir)
    preflight = deal_contracts.run_deal_preflight(deal_id, wiki_root=wiki_root)
    deal_store.write_json(package_dir / "phases" / "preflight.json", preflight)
    state = _load_or_create_state(
        package_dir,
        deal_id=deal_id,
        phases=phases,
        profiles=profiles,
        resume=resume,
    )
    _write_state(package_dir, state)
    try:
        for phase in phases:
            phase_profiles = _phase_selected_profiles(phase, profiles)
            await _execute_phase(
                phase,
                deal_id=deal_id,
                profiles=phase_profiles,
                wiki_root=wiki_root,
                timeout=timeout,
                state=state,
                package_dir=package_dir,
                retrieval_limit=retrieval_limit,
            )
            if (state.get("phase_runs", {}).get(phase) or {}).get("status") == "blocked":
                break
    except BaseException as exc:
        if isinstance(exc, asyncio.CancelledError):
            raise
        failed_phase = next(
            (phase for phase in phases if (state.get("phase_runs", {}).get(phase) or {}).get("status") == "running"),
            "setup",
        )
        phase_state = state.setdefault("phase_runs", {}).setdefault(failed_phase, {})
        phase_state.update({"status": "failed", "completed_at": _now_iso()})
        error = {
            "phase": failed_phase,
            "error_type": type(exc).__name__,
            "message": (str(exc) or type(exc).__name__)[:500],
        }
        if error not in state["errors"]:
            state["errors"].append(error)
        state["status"] = "failed"
        _write_state(package_dir, state)
    report = build_report(state, execution_mode="real")
    state["status"] = report["status"]
    if report["status"] == "passed":
        state["completed_at"] = report["completed_at"]
    _write_state(package_dir, state)
    report_path = package_dir / "release" / "real_smoke.json"
    deal_store.write_json(report_path, report)
    return report, report_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True, help="Source Deal OS package; copied only in --real mode")
    parser.add_argument("--run-root", type=Path, help="Isolated run root; required with --resume")
    parser.add_argument("--phase", action="append", choices=PHASE_ORDER, help="Phase to execute; repeatable")
    parser.add_argument("--profile", action="append", help="IC profile to execute; repeatable")
    parser.add_argument("--timeout", type=float, default=1200.0, help="Per Hermes task timeout in seconds")
    parser.add_argument("--retrieval-limit", type=int, default=10, help="Startup retrieval evidence limit")
    parser.add_argument("--resume", action="store_true", help="Resume the state under --run-root")
    parser.add_argument("--real", action="store_true", help="Copy fixture, query startup retrieval, and call Hermes")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        fixture, deal_id = _fixture_identity(args.fixture)
        phases = _ordered_phases(args.phase)
        profiles = _ordered_profiles(args.profile)
        for phase in phases:
            _phase_selected_profiles(phase, profiles)
        if args.timeout <= 0:
            raise ValueError("timeout must be positive")
        if not 1 <= args.retrieval_limit <= 50:
            raise ValueError("retrieval-limit must be between 1 and 50")
        if args.resume and not args.run_root:
            raise ValueError("--resume requires --run-root")
        run_root = (
            args.run_root.expanduser().resolve()
            if args.run_root
            else Path(tempfile.mkdtemp(prefix="siq-primary-market-ic-real-smoke-")).resolve()
        )
        if not args.real:
            report = _dry_run_report(deal_id=deal_id, phases=phases, profiles=profiles)
            output = run_root / "release" / "real_smoke.json"
            deal_store.write_json(output, report)
            print(json.dumps({"report": report, "report_path": str(output)}, ensure_ascii=False, indent=2))
            return 0
        report, output = asyncio.run(
            run_real(
                fixture=fixture,
                run_root=run_root,
                phases=phases,
                profiles=profiles,
                resume=args.resume,
                timeout=args.timeout,
                retrieval_limit=args.retrieval_limit,
            )
        )
        print(json.dumps({"report": report, "report_path": str(output)}, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "passed" else 1
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
