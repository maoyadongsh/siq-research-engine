from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from services.auth_dependencies import require_permission
from services.auth_service import User
from services import deal_contracts
from services import deal_store
from services import ic_policy
from services.ic_openclaw_importer import DEFAULT_OPENCLAW_PROJECTS_ROOT, import_openclaw_project
from services.job_service import FileBackedJobService
from services.path_config import BACKEND_DATA_ROOT


router = APIRouter(prefix="/deals", tags=["deals"])
deal_job_service = FileBackedJobService(store_path=BACKEND_DATA_ROOT / "deals" / "jobs.json")


class DealCreateRequest(BaseModel):
    deal_id: str = Field(..., min_length=3)
    company_name: str = Field(..., min_length=1)
    industry: str = ""
    stage: str = ""
    deal_type: str = ""
    source: str = "manual"


class OpenClawImportRequest(BaseModel):
    source_root: str | None = None
    project_id: str | None = None
    deal_id: str = Field(..., min_length=3)
    overwrite: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


def _user_payload(user: User) -> dict[str, Any]:
    return {
        "id": getattr(user, "id", None),
        "username": getattr(user, "username", None),
    }


def _not_found(deal_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Deal not found: {deal_id}")


def _resolve_openclaw_source(payload: OpenClawImportRequest) -> str:
    if payload.source_root:
        return payload.source_root
    project_id = str(payload.project_id or "").strip()
    if not project_id:
        raise ValueError("source_root or project_id is required")
    project_path = Path(project_id)
    if project_path.is_absolute() or project_path.name != project_id or project_id in {".", ".."}:
        raise ValueError("project_id must be a single OpenClaw project directory name")
    return str(DEFAULT_OPENCLAW_PROJECTS_ROOT / project_id)


def _compact_import_result(result: dict[str, Any], deal_id: str) -> dict[str, Any]:
    deal = result.get("deal") if isinstance(result, dict) else {}
    archive_manifest = result.get("archive_manifest") if isinstance(result, dict) else {}
    summary = deal.get("summary") if isinstance(deal, dict) else None
    if not isinstance(summary, dict):
        summary = result.get("summary") if isinstance(result, dict) and isinstance(result.get("summary"), dict) else {}
    manifest = deal.get("manifest") if isinstance(deal, dict) else {}
    openclaw_import = manifest.get("openclaw_import") if isinstance(manifest, dict) else {}
    return deal_store.redact_public_payload({
        "ok": True,
        "deal_id": deal_id,
        "summary": summary,
        "legacy_project_id": (
            archive_manifest.get("legacy_project_id")
            if isinstance(archive_manifest, dict)
            else openclaw_import.get("legacy_project_id")
            if isinstance(openclaw_import, dict)
            else None
        ),
        "archive_manifest": {
            "schema_version": archive_manifest.get("schema_version") if isinstance(archive_manifest, dict) else None,
            "file_count": archive_manifest.get("file_count") if isinstance(archive_manifest, dict) else None,
        },
    })


def _safe_import_error(exc: Exception) -> str:
    if isinstance(exc, FileExistsError):
        return str(exc)
    if isinstance(exc, FileNotFoundError):
        return "OpenClaw project source not found"
    if isinstance(exc, ValueError):
        message = str(exc)
        if "source" in message.lower() and "under" in message.lower():
            return "OpenClaw source must be under the configured projects root"
        return message
    return "OpenClaw import failed"


def _raise_import_error(exc: Exception) -> None:
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=400, detail=_safe_import_error(exc)) from exc
    if isinstance(exc, FileExistsError):
        raise HTTPException(status_code=409, detail=_safe_import_error(exc)) from exc
    if isinstance(exc, FileNotFoundError):
        raise HTTPException(status_code=404, detail=_safe_import_error(exc)) from exc
    raise HTTPException(status_code=500, detail=_safe_import_error(exc)) from exc


def _run_openclaw_import(payload: OpenClawImportRequest, created_by: dict[str, Any]) -> dict[str, Any]:
    return import_openclaw_project(
        source_root=_resolve_openclaw_source(payload),
        deal_id=payload.deal_id,
        created_by=created_by,
        overwrite=payload.overwrite,
    )


def _run_openclaw_import_job(payload: OpenClawImportRequest, created_by: dict[str, Any]) -> dict[str, Any]:
    try:
        result = _run_openclaw_import(payload, created_by)
        return _compact_import_result(result, payload.deal_id)
    except Exception as exc:
        return {
            "ok": False,
            "deal_id": payload.deal_id,
            "error": _safe_import_error(exc),
        }


def _coerce_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _canonical_keyed_payload(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    payload: dict[str, dict[str, Any]] = {}
    for key, item in value.items():
        if not isinstance(item, dict):
            continue
        agent_id = ic_policy.canonical_ic_profile_id(str(item.get("agent_id") or key))
        normalized = dict(item)
        normalized["agent_id"] = agent_id
        payload[agent_id] = normalized
    return payload


def _receipt_agents(value: Any) -> dict[str, dict[str, Any]]:
    payload = _coerce_dict(value)
    agents = payload.get("agents", payload)
    return _canonical_keyed_payload(agents)


def _limited_list_count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _limited_list(value: Any, limit: int = 5) -> list[Any]:
    if not isinstance(value, list):
        return []
    return value[:limit]


def _read_deal_workflow_artifacts(package_dir: Path) -> dict[str, Any]:
    reports = _canonical_keyed_payload(
        deal_store.redact_public_payload(deal_store.read_json(package_dir / "phases" / "r1_reports.json", {}) or {})
    )
    receipts = _receipt_agents(
        deal_store.redact_public_payload(deal_store.read_json(package_dir / "phases" / "startup_receipts.json", {}) or {})
    )
    raw_disputes = deal_store.redact_public_payload(
        deal_store.read_json(package_dir / "phases" / "r1_5_disputes.json", {}) or {}
    )
    dispute_items = raw_disputes.get("disputes") if isinstance(raw_disputes, dict) else raw_disputes
    if not isinstance(dispute_items, list):
        dispute_items = []

    profiles = {profile["id"]: profile for profile in ic_policy.list_ic_profiles(include_runtime=False)}
    agent_reports: list[dict[str, Any]] = []
    for profile_id in ic_policy.R1_AGENT_SEQUENCE:
        profile = profiles.get(profile_id, {"id": profile_id, "label": profile_id, "role": profile_id})
        report = reports.get(profile_id, {})
        receipt = receipts.get(profile_id, {})
        agent_reports.append({
            "agent_id": profile_id,
            "role": profile.get("role"),
            "label": profile.get("label"),
            "r1_sequence_index": profile.get("r1_sequence_index"),
            "has_report": bool(report),
            "has_startup_receipt": bool(receipt),
            "score": report.get("score"),
            "recommendation": report.get("recommendation"),
            "confidence": report.get("confidence"),
            "summary": report.get("summary"),
            "verified_count": _limited_list_count(report.get("verified")),
            "assumed_count": _limited_list_count(report.get("assumed")),
            "open_questions": _limited_list(report.get("open_questions")),
            "risk_flags": _limited_list(report.get("risk_flags")),
            "artifact_path": report.get("artifact_path"),
            "startup_receipt_id": report.get("startup_receipt_id") or receipt.get("receipt_id"),
            "created_at": report.get("created_at"),
        })

    dispute_summaries: list[dict[str, Any]] = []
    for index, dispute in enumerate(dispute_items, start=1):
        if not isinstance(dispute, dict):
            continue
        positions = dispute.get("positions")
        ruling = dispute.get("chairman_ruling")
        dispute_summaries.append({
            "dispute_id": dispute.get("dispute_id") or f"DISP-{index:03d}",
            "topic": dispute.get("topic"),
            "dimension": dispute.get("dimension"),
            "severity": dispute.get("severity"),
            "resolved": bool(dispute.get("resolved")),
            "position_count": len(positions) if isinstance(positions, list) else 0,
            "chairman_ruling": ruling if isinstance(ruling, dict) else None,
        })

    return {
        "r1_agent_sequence": list(ic_policy.R1_AGENT_SEQUENCE),
        "agent_reports": agent_reports,
        "startup_receipts": {
            "count": len(receipts),
            "agents": sorted(receipts.keys()),
        },
        "disputes": dispute_summaries,
        "artifact_status": {
            "r1_reports": bool(reports),
            "startup_receipts": bool(receipts),
            "r1_5_disputes": bool(dispute_summaries),
        },
    }


@router.get("")
def list_deals(
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    del current_user
    deals = deal_store.list_deals()
    if q:
        needle = q.strip().lower()
        deals = [
            item for item in deals
            if needle in str(item.get("deal_id") or "").lower()
            or needle in str(item.get("company_name") or "").lower()
            or needle in str(item.get("legacy_project_id") or "").lower()
        ]
    if status:
        deals = [item for item in deals if str(item.get("status") or "") == status]
    stats = {
        "total": len(deals),
        "active": sum(1 for item in deals if item.get("status") not in {"r4_completed", "archived", "closed"}),
        "diligence": sum(1 for item in deals if str(item.get("status") or "").startswith("r1")),
        "highRisk": 0,
    }
    return {"deals": deals, "stats": stats}


@router.post("")
def create_deal(
    payload: DealCreateRequest,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    try:
        deal = deal_store.create_deal_package(
            deal_id=payload.deal_id,
            company_name=payload.company_name,
            industry=payload.industry,
            stage=payload.stage,
            deal_type=payload.deal_type,
            source=payload.source,
            created_by=_user_payload(current_user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    deal_store.append_audit_event(
        payload.deal_id,
        {
            "event_type": "deal_created",
            "created_by": _user_payload(current_user),
        },
    )
    return {"deal": deal}


@router.post("/import/openclaw")
def import_openclaw_deal(
    payload: OpenClawImportRequest,
    wait: bool = Query(default=False),
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    created_by = _user_payload(current_user)
    if not wait:
        job = deal_job_service.start(
            "deal-openclaw-import",
            lambda: _run_openclaw_import_job(payload, created_by),
            created_by=created_by,
        )
        return {"ok": True, "queued": True, **job}
    try:
        return _run_openclaw_import(payload, created_by)
    except Exception as exc:
        _raise_import_error(exc)


@router.get("/jobs/{job_id}")
def get_deal_job_status(
    job_id: str,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    del current_user
    job = deal_job_service.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Deal job not found")
    return deal_store.redact_public_payload(job)


@router.get("/ic/profiles")
def get_ic_profiles(
    runtime: bool = Query(default=False),
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    del current_user
    try:
        return {"profiles": ic_policy.list_ic_profiles(include_runtime=runtime)}
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/ic/policy")
def get_ic_policy(
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    del current_user
    try:
        return {"policy": ic_policy.public_ic_workflow_policy()}
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{deal_id}")
def get_deal(
    deal_id: str,
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    del current_user
    try:
        return deal_store.read_deal_detail(deal_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.get("/{deal_id}/workflow")
def get_deal_workflow(
    deal_id: str,
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    del current_user
    try:
        package_dir = deal_store.safe_deal_dir(deal_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    workflow = deal_store.read_json(package_dir / "phases" / "workflow_state.json", None)
    if workflow is None:
        raise _not_found(deal_id)
    return {
        "workflow": deal_store.redact_public_payload(workflow),
        **_read_deal_workflow_artifacts(package_dir),
    }


@router.get("/{deal_id}/preflight")
def get_deal_preflight(
    deal_id: str,
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    del current_user
    try:
        return {"preflight": deal_store.redact_public_payload(deal_contracts.run_deal_preflight(deal_id))}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.get("/{deal_id}/decision")
def get_deal_decision(
    deal_id: str,
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    del current_user
    try:
        package_dir = deal_store.safe_deal_dir(deal_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    decision_json = deal_store.read_json(package_dir / "phases" / "r4_decision.json", {}) or {}
    report_path = package_dir / "decision" / "IC_DECISION_REPORT.md"
    report_markdown = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
    if not decision_json and not report_markdown:
        raise _not_found(deal_id)
    return {
        "decision": decision_json,
        "report_markdown": report_markdown,
        "report_path": "decision/IC_DECISION_REPORT.md" if report_markdown else None,
    }


@router.get("/{deal_id}/audit")
def get_deal_audit(
    deal_id: str,
    current_user: User = Depends(require_permission("audit.view")),
) -> dict[str, Any]:
    del current_user
    try:
        package_dir = deal_store.safe_deal_dir(deal_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit = deal_store.read_json(package_dir / "audit" / "audit_log.json", None)
    if audit is None:
        audit = deal_store.read_json(package_dir / "phases" / "audit_log.json", None)
    if audit is None:
        raise _not_found(deal_id)
    return {"audit": deal_store.redact_public_payload(audit)}


@router.get("/{deal_id}/manifest")
def get_deal_manifest(
    deal_id: str,
    current_user: User = Depends(require_permission("audit.view")),
) -> dict[str, Any]:
    del current_user
    try:
        package_dir = deal_store.safe_deal_dir(deal_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    manifest = deal_store.read_json(package_dir / "manifest.json", None)
    if manifest is None:
        raise _not_found(deal_id)
    return {"manifest": deal_store.redact_public_payload(manifest)}
