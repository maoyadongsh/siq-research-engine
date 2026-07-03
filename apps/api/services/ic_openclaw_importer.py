"""Import OpenClaw investment-committee projects into SIQ deal packages."""

from __future__ import annotations

import copy
import hashlib
import os
import shutil
from pathlib import Path
from typing import Any

from services import deal_store
from services import ic_policy


OPENCLAW_IMPORT_SCHEMA = "siq_openclaw_import_v1"
R4_DECISION_SCHEMA = "siq_ic_r4_decision_v1"
STARTUP_RECEIPTS_SCHEMA = "siq_ic_startup_receipts_v1"
DEFAULT_R4_ARTIFACT_PATHS = {
    "markdown": "decision/IC_DECISION_REPORT.md",
    "html": "decision/IC_DECISION_REPORT.html",
}
DEFAULT_WORKSPACE_RULES_READ = ["SOUL.md", "AGENTS.md"]
LEGACY_STARTUP_RECEIPTS_META_KEYS = {
    "compatibility",
    "created_at",
    "deal_id",
    "generated_at",
    "project_id",
    "project_tag",
    "schema_version",
    "source",
    "summary",
    "total",
    "total_count",
    "updated_at",
}
DEFAULT_OPENCLAW_PROJECTS_ROOT = Path(
    os.environ.get(
        "SIQ_OPENCLAW_PROJECTS_ROOT",
        "/home/maoyd/.openclaw/workspace/ic_master_coordinator_workspace/shared/projects",
    )
).expanduser().resolve()


def _safe_under(root: Path, path: Path) -> Path:
    root_resolved = root.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"OpenClaw source must be under {root_resolved}") from exc
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_is_safe_file(source_root: Path, source: Path) -> tuple[bool, str | None]:
    try:
        source.relative_to(source_root)
    except ValueError:
        return False, "source file escapes project root"
    current = source_root
    try:
        relative_parts = source.relative_to(source_root).parts
    except ValueError:
        return False, "source file escapes project root"
    for part in relative_parts:
        current = current / part
        if current.is_symlink():
            return False, "source symlink is not allowed"
    if not source.is_file():
        return False, "source file not found"
    try:
        source.resolve().relative_to(source_root.resolve())
    except ValueError:
        return False, "resolved source escapes project root"
    return True, None


def _copy_if_exists(source_root: Path, package_dir: Path, source_relative: str, target_relative: str) -> dict[str, Any]:
    source = source_root / source_relative
    target = package_dir / target_relative
    safe, reason = _source_is_safe_file(source_root, source)
    if not safe:
        status = "rejected" if reason and "symlink" in reason else "missing"
        return {
            "source": source_relative,
            "target": target_relative,
            "status": status,
            "reason": reason or "source file not found",
        }
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return {
        "source": source_relative,
        "target": target_relative,
        "status": "imported",
        "sha256": _sha256(target),
    }


def _copy_existing_directory_files(source_root: Path, package_dir: Path, source_relative: str, target_relative: str) -> list[dict[str, Any]]:
    source_dir = source_root / source_relative
    if source_dir.is_symlink():
        return [{
            "source": source_relative,
            "target": target_relative,
            "status": "rejected",
            "reason": "source symlink is not allowed",
        }]
    if not source_dir.is_dir():
        return []
    results: list[dict[str, Any]] = []
    for source in sorted(path for path in source_dir.rglob("*") if path.is_file() or path.is_symlink()):
        relative = source.relative_to(source_dir).as_posix()
        target = package_dir / target_relative / relative
        safe, reason = _source_is_safe_file(source_root, source)
        if not safe:
            results.append({
                "source": f"{source_relative}/{relative}",
                "target": f"{target_relative}/{relative}",
                "status": "rejected",
                "reason": reason or "unsafe source file",
            })
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        results.append({
            "source": f"{source_relative}/{relative}",
            "target": f"{target_relative}/{relative}",
            "status": "imported",
            "sha256": _sha256(target),
        })
    return results


def _created_by_payload(created_by: dict[str, Any] | None) -> dict[str, Any] | None:
    if not created_by:
        return None
    return {key: value for key, value in created_by.items() if value is not None}


def _import_metadata_payload(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    if not metadata:
        return None
    redacted = deal_store.redact_public_payload(metadata)
    return redacted if isinstance(redacted, dict) else None


def _non_negative_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(parsed, 0)


def _first_present_count(payload: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        if key in payload and payload.get(key) is not None:
            return _non_negative_int(payload.get(key))
    return None


def _is_legacy_startup_summary(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and not value.get("receipt_id")
        and ("count" in value or isinstance(value.get("types"), dict))
    )


def _legacy_startup_agents(payload: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], bool]:
    if isinstance(payload.get("agents"), dict):
        raw_agents = payload["agents"]
    else:
        raw_agents = {
            key: value
            for key, value in payload.items()
            if key not in LEGACY_STARTUP_RECEIPTS_META_KEYS
        }

    agents: dict[str, dict[str, Any]] = {}
    found_legacy = False
    for key, value in raw_agents.items():
        if not isinstance(value, dict):
            continue
        profile_id = ic_policy.canonical_ic_profile_id(str(value.get("agent_id") or key))
        if _is_legacy_startup_summary(value):
            found_legacy = True
        agents[profile_id] = value
    return agents, found_legacy


def _legacy_startup_hit_counts(item: dict[str, Any]) -> tuple[int, int]:
    types = item.get("types") if isinstance(item.get("types"), dict) else {}
    shared = _first_present_count(
        item,
        ("shared_hits", "shared_count", "shared"),
    )
    private = _first_present_count(
        item,
        ("private_hits", "private_count", "private"),
    )
    if shared is None:
        shared = _first_present_count(types, ("shared", "shared_hits", "public", "workspace"))
    if private is None:
        private = _first_present_count(types, ("private", "private_hits", "knowledge_base", "local"))

    total = _non_negative_int(item.get("count"), default=0)
    if shared is None and private is None:
        return total, 0
    if shared is None:
        shared = max(total - (private or 0), 0)
    if private is None:
        private = max(total - shared, 0) if total else 0
    return shared, private


def _legacy_startup_receipt_query(
    payload: dict[str, Any],
    item: dict[str, Any],
    *,
    company_name: str,
    industry: str,
    stage: str,
    deal_id: str,
) -> str:
    for candidate in (item.get("query"), item.get("search_query"), payload.get("query")):
        if candidate:
            return str(candidate)
    parts = [company_name, industry, stage]
    query = " ".join(str(part).strip() for part in parts if str(part or "").strip())
    return query or deal_id


def _legacy_startup_evidence_hits(item: dict[str, Any]) -> list[dict[str, Any]]:
    evidence_hits = item.get("evidence_hits")
    if isinstance(evidence_hits, list):
        return [hit for hit in evidence_hits if isinstance(hit, dict)]
    evidence_ids = item.get("evidence_ids")
    if isinstance(evidence_ids, list):
        return [
            {"evidence_id": evidence_id}
            for evidence_id in evidence_ids
            if isinstance(evidence_id, str) and evidence_id
        ]
    return []


def _normalize_legacy_startup_receipt(
    payload: dict[str, Any],
    profile_id: str,
    item: dict[str, Any],
    *,
    deal_id: str,
    company_name: str,
    industry: str,
    stage: str,
) -> dict[str, Any]:
    round_name = str(item.get("round_name") or item.get("round") or payload.get("round_name") or "R1")
    shared_hits, private_hits = _legacy_startup_hit_counts(item)
    workspace_rules = item.get("workspace_rules_read") or payload.get("workspace_rules_read")
    if not isinstance(workspace_rules, list) or not workspace_rules:
        workspace_rules = list(DEFAULT_WORKSPACE_RULES_READ)
    gaps = item.get("gaps")
    if not isinstance(gaps, list):
        gaps = []
    created_at = (
        item.get("created_at")
        or item.get("generated_at")
        or payload.get("created_at")
        or payload.get("generated_at")
        or deal_store.utc_now_iso()
    )

    return {
        "agent_id": profile_id,
        "receipt_id": item.get("receipt_id") or f"startup-{profile_id}-{round_name}-001",
        "round_name": round_name,
        "query": _legacy_startup_receipt_query(
            payload,
            item,
            company_name=company_name,
            industry=industry,
            stage=stage,
            deal_id=deal_id,
        ),
        "project_tag": item.get("project_tag") or payload.get("project_tag") or deal_id,
        "shared_hits": shared_hits,
        "private_hits": private_hits,
        "workspace_rules_read": workspace_rules,
        "gaps": gaps,
        "evidence_hits": _legacy_startup_evidence_hits(item),
        "created_at": created_at,
        "compatibility": {
            "source": "openclaw_legacy_startup_summary",
            "openclaw_legacy_summary": copy.deepcopy(item),
        },
    }


def _normalize_startup_receipts_contract(
    package_dir: Path,
    *,
    deal_id: str,
    company_name: str,
    industry: str,
    stage: str,
) -> None:
    """Backfill SIQ startup receipt fields from legacy OpenClaw count summaries."""

    path = package_dir / "phases" / "startup_receipts.json"
    payload = deal_store.read_json(path, None)
    if not isinstance(payload, dict):
        return

    agents, found_legacy = _legacy_startup_agents(payload)
    if not found_legacy:
        return

    normalized_agents: dict[str, dict[str, Any]] = {}
    for profile_id, item in agents.items():
        if _is_legacy_startup_summary(item):
            normalized_agents[profile_id] = _normalize_legacy_startup_receipt(
                payload,
                profile_id,
                item,
                deal_id=deal_id,
                company_name=company_name,
                industry=industry,
                stage=stage,
            )
        else:
            normalized_agents[profile_id] = copy.deepcopy(item)

    compatibility = (
        copy.deepcopy(payload.get("compatibility"))
        if isinstance(payload.get("compatibility"), dict)
        else {}
    )
    compatibility.setdefault("source", "openclaw_legacy_startup_receipts")
    compatibility["openclaw_legacy_summary"] = copy.deepcopy(payload)

    normalized: dict[str, Any] = {
        "schema_version": STARTUP_RECEIPTS_SCHEMA,
        "deal_id": deal_id,
        "agents": normalized_agents,
        "updated_at": deal_store.utc_now_iso(),
        "compatibility": compatibility,
    }
    if payload.get("generated_at") is not None:
        normalized["generated_at"] = payload.get("generated_at")

    deal_store.write_json(path, normalized)


def _normalize_r4_decision_contract(package_dir: Path, *, deal_id: str) -> None:
    """Backfill SIQ R4 contract fields from legacy OpenClaw decision payloads."""

    path = package_dir / "phases" / "r4_decision.json"
    decision = deal_store.read_json(path, None)
    if not isinstance(decision, dict):
        return

    changed = False
    breakdown = decision.get("breakdown") if isinstance(decision.get("breakdown"), dict) else {}
    chairman = (
        breakdown.get("siq_ic_chairman")
        or breakdown.get("ic_chairman")
        or breakdown.get("chairman")
        or {}
    )
    if not isinstance(chairman, dict):
        chairman = {}

    if decision.get("schema_version") is None:
        decision["schema_version"] = R4_DECISION_SCHEMA
        changed = True
    if decision.get("deal_id") is None:
        decision["deal_id"] = deal_id
        changed = True
    if decision.get("conditions") is None:
        decision["conditions"] = []
        changed = True
    if decision.get("monitoring_metrics") is None:
        decision["monitoring_metrics"] = []
        changed = True
    if decision.get("artifact_paths") is None:
        decision["artifact_paths"] = dict(DEFAULT_R4_ARTIFACT_PATHS)
        changed = True
    if decision.get("human_confirmation") is None:
        decision["human_confirmation"] = {
            "status": "pending",
            "confirmed_by": None,
            "confirmed_at": None,
            "override_reason": None,
        }
        changed = True
    if decision.get("weighted_agent_score") is None and decision.get("final_score") is not None:
        decision["weighted_agent_score"] = decision.get("final_score")
        changed = True
    if decision.get("chairman_dimension_score") is None:
        chairman_score = chairman.get("raw_score") or chairman.get("score") or chairman.get("weighted_score")
        if chairman_score is not None:
            decision["chairman_dimension_score"] = chairman_score
            changed = True
    if decision.get("chairman_qualitative_decision") is None:
        qualitative = decision.get("decision_text") or decision.get("decision") or decision.get("final_decision")
        if qualitative is not None:
            decision["chairman_qualitative_decision"] = qualitative
            changed = True
    if decision.get("threshold_result") is None:
        threshold_result = decision.get("decision") or decision.get("final_decision")
        if threshold_result is not None:
            decision["threshold_result"] = threshold_result
            changed = True
    if changed:
        compatibility = decision.setdefault("compatibility", {})
        if isinstance(compatibility, dict):
            compatibility.setdefault("source", "openclaw_legacy_r4_decision")
            compatibility.setdefault("normalized_at", deal_store.utc_now_iso())
        decision["updated_at"] = deal_store.utc_now_iso()
        deal_store.write_json(path, decision)


def import_openclaw_project(
    *,
    source_root: str | Path,
    deal_id: str,
    created_by: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    wiki_root: str | Path | None = None,
    openclaw_projects_root: str | Path | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    allowed_root = Path(openclaw_projects_root).expanduser().resolve() if openclaw_projects_root else DEFAULT_OPENCLAW_PROJECTS_ROOT
    source = _safe_under(allowed_root, Path(source_root).expanduser().resolve())
    if not source.is_dir():
        raise FileNotFoundError(f"OpenClaw project source not found: {source}")

    legacy_project_id = source.name
    source_project_meta = deal_store.read_json(source / "project_meta.json", {}) or {}
    source_workflow = deal_store.read_json(source / "phases" / "workflow_state.json", {}) or {}
    company_name = (
        source_project_meta.get("company_name")
        or source_workflow.get("company_name")
        or legacy_project_id
    )
    industry = source_project_meta.get("industry") or source_workflow.get("industry") or ""
    stage = source_project_meta.get("stage") or source_workflow.get("stage") or ""
    import_metadata = _import_metadata_payload(metadata)

    summary = deal_store.create_deal_package(
        deal_id=deal_id,
        legacy_project_id=legacy_project_id,
        company_name=company_name,
        industry=industry,
        stage=stage,
        source="openclaw_import",
        created_by=_created_by_payload(created_by),
        wiki_root=wiki_root,
        overwrite=overwrite,
    )
    package_dir = deal_store.safe_deal_dir(deal_id, wiki_root=wiki_root)

    file_results: list[dict[str, Any]] = []
    for source_relative, target_relative in (
        ("project_meta.json", "project_meta.json"),
        ("artifact_map.json", "artifact_map.json"),
        ("phases/workflow_state.json", "phases/workflow_state.json"),
        ("phases/r1_reports.json", "phases/r1_reports.json"),
        ("phases/r1_5_disputes.json", "phases/r1_5_disputes.json"),
        ("phases/r2_reports.json", "phases/r2_reports.json"),
        ("phases/r3_reports.json", "phases/r3_reports.json"),
        ("phases/r4_decision.json", "phases/r4_decision.json"),
        ("phases/startup_receipts.json", "phases/startup_receipts.json"),
        ("phases/round_context_receipts.json", "phases/round_context_receipts.json"),
        ("phases/audit_log.json", "phases/audit_log.json"),
        ("40_decision/IC_DECISION_REPORT.md", "decision/IC_DECISION_REPORT.md"),
        ("archive_manifest.json", "audit/legacy_archive_manifest.json"),
    ):
        file_results.append(_copy_if_exists(source, package_dir, source_relative, target_relative))

    file_results.extend(_copy_existing_directory_files(source, package_dir, "discussion", "discussion"))
    file_results.extend(_copy_existing_directory_files(source, package_dir, "90_audit", "audit/legacy_90_audit"))

    project_meta = deal_store.read_json(package_dir / "project_meta.json", {}) or {}
    project_meta.update({
        "schema_version": deal_store.DEAL_PROJECT_SCHEMA,
        "deal_id": deal_id,
        "legacy_project_id": legacy_project_id,
        "company_name": project_meta.get("company_name") or company_name,
        "industry": project_meta.get("industry") or industry,
        "stage": project_meta.get("stage") or stage,
        "source": "openclaw_import",
        "updated_at": deal_store.utc_now_iso(),
    })
    project_meta.setdefault("created_by", _created_by_payload(created_by))
    if import_metadata:
        project_meta["import_metadata"] = import_metadata
    deal_store.write_json(package_dir / "project_meta.json", project_meta)

    workflow = deal_store.read_json(package_dir / "phases" / "workflow_state.json", {}) or {}
    workflow.update({
        "schema_version": deal_store.DEAL_WORKFLOW_SCHEMA,
        "deal_id": deal_id,
        "legacy_project_id": legacy_project_id,
        "company_name": workflow.get("company_name") or company_name,
        "industry": workflow.get("industry") or industry,
        "stage": workflow.get("stage") or stage,
        "updated_at": deal_store.utc_now_iso(),
    })
    if workflow.get("final_decision") and not workflow.get("current_phase"):
        workflow["current_phase"] = "R4"
    if workflow.get("final_decision") and workflow.get("status") in (None, "", "draft"):
        workflow["status"] = "r4_completed"
    workflow.setdefault("current_phase", "R0")
    deal_store.write_json(package_dir / "phases" / "workflow_state.json", workflow)
    _normalize_r4_decision_contract(package_dir, deal_id=deal_id)
    _normalize_startup_receipts_contract(
        package_dir,
        deal_id=deal_id,
        company_name=company_name,
        industry=industry,
        stage=stage,
    )

    imported_count = len([item for item in file_results if item.get("status") == "imported"])
    audit_event = deal_store.append_audit_event(
        deal_id,
        {
            "event_type": "openclaw_imported",
            "legacy_project_id": legacy_project_id,
            "source_root": source.name,
            "file_count": imported_count,
            "created_by": _created_by_payload(created_by),
        },
        wiki_root=wiki_root,
    )

    for item in file_results:
        if item.get("status") == "imported":
            target = package_dir / str(item.get("target") or "")
            if target.is_file():
                item["sha256"] = _sha256(target)

    manifest = deal_store.read_json(package_dir / "manifest.json", {}) or {}
    manifest.update({
        "schema_version": deal_store.DEAL_MANIFEST_SCHEMA,
        "deal_id": deal_id,
        "legacy_project_id": legacy_project_id,
        "company_name": project_meta.get("company_name") or company_name,
        "updated_at": deal_store.utc_now_iso(),
    })
    manifest["hashes"] = {
        item["target"]: item["sha256"]
        for item in file_results
        if item.get("status") == "imported" and item.get("sha256")
    }
    manifest["openclaw_import"] = {
        "schema_version": OPENCLAW_IMPORT_SCHEMA,
        "source_root": source.name,
        "legacy_project_id": legacy_project_id,
        "imported_at": deal_store.utc_now_iso(),
        "file_count": imported_count,
        "files": file_results,
    }
    if import_metadata:
        manifest["openclaw_import"]["metadata"] = import_metadata
    deal_store.write_json(package_dir / "manifest.json", manifest)

    archive_manifest = {
        "schema_version": OPENCLAW_IMPORT_SCHEMA,
        "deal_id": deal_id,
        "legacy_project_id": legacy_project_id,
        "source_root": source.name,
        "imported_at": deal_store.utc_now_iso(),
        "file_count": manifest["openclaw_import"]["file_count"],
        "files": file_results,
    }
    deal_store.write_json(package_dir / "audit" / "archive_manifest.json", archive_manifest)
    return {
        "deal": deal_store.read_deal_detail(deal_id, wiki_root=wiki_root),
        "summary": summary,
        "archive_manifest": archive_manifest,
        "audit_event": audit_event,
    }
