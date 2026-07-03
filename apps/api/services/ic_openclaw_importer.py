"""Import OpenClaw investment-committee projects into SIQ deal packages."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Any

from services import deal_store


OPENCLAW_IMPORT_SCHEMA = "siq_openclaw_import_v1"
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
