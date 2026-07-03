from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from database import get_async_session
from services.auth_dependencies import require_permission
from services.auth_service import User
from services import deal_agents
from services import deal_contracts
from services import deal_audit
from services import deal_decision
from services import deal_documents
from services import deal_disputes
from services import deal_evidence
from services import deal_manifest
from services import deal_phase_artifacts
from services import deal_reports
from services import deal_status
from services import deal_store
from services import ic_agent_runtime
from services import ic_policy
from services import ic_startup_retrieval
from services.ic_openclaw_importer import DEFAULT_OPENCLAW_PROJECTS_ROOT, import_openclaw_project
from services.job_service import FileBackedJobService
from services.path_config import BACKEND_DATA_ROOT
from services.usage_service import UserArtifact


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


class DealDocumentBindParserTaskRequest(BaseModel):
    task_id: str = Field(..., min_length=2)
    artifact_path: str | None = None
    note: str = ""


class StartupRetrievalRequest(BaseModel):
    round_name: str = "R1"
    query: str | None = None
    limit: int = Field(default=10, ge=1, le=50)


class AgentTaskDryRunRequest(BaseModel):
    round_name: str = "R1"


class WorkflowRunR1AgentRequest(BaseModel):
    profile_id: str = Field(..., min_length=1)
    round_name: str = "R1"
    dry_run: bool = True


class WorkflowRunR1SerialRequest(BaseModel):
    round_name: str = "R1"
    dry_run: bool = True
    max_agents: int = Field(default=6, ge=1, le=6)


class WorkflowIdentifyDisputesRequest(BaseModel):
    dry_run: bool = True
    preserve_rulings: bool = True


class WorkflowDisputeRulingRequest(BaseModel):
    decision: str = Field(..., min_length=1)
    rationale: str = ""
    required_followups: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    resolved: bool = True
    overwrite: bool = False
    dry_run: bool = True


class WorkflowGenerateDisputeRulingsRequest(BaseModel):
    dry_run: bool = True
    overwrite: bool = False


class DealDecisionHumanConfirmationRequest(BaseModel):
    status: str = Field(..., min_length=3)
    override_reason: str | None = None
    override_decision: str | None = None
    override_score: float | str | None = None
    dry_run: bool = True


def _user_payload(user: User) -> dict[str, Any]:
    return {
        "id": getattr(user, "id", None),
        "username": getattr(user, "username", None),
    }


def _not_found(deal_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Deal not found: {deal_id}")


def _role_value(user: User) -> str:
    role = getattr(user, "role", "")
    return str(role.value if hasattr(role, "value") else role)


def _is_admin_user(user: User) -> bool:
    return _role_value(user) in {"admin", "super_admin"}


async def _user_has_document_task_access(
    async_session: AsyncSession,
    current_user: User,
    task_id: str,
) -> bool:
    if _is_admin_user(current_user):
        return True
    user_id = getattr(current_user, "id", None)
    if user_id is None:
        return False
    result = await async_session.exec(
        select(UserArtifact).where(
            UserArtifact.user_id == int(user_id),
            UserArtifact.artifact_type == "document_parse",
            UserArtifact.artifact_key == task_id,
        )
    )
    if result.first():
        return True
    result = await async_session.exec(
        select(UserArtifact).where(
            UserArtifact.user_id == int(user_id),
            UserArtifact.artifact_type == "document_parse",
            UserArtifact.global_artifact_id == task_id,
        )
    )
    return result.first() is not None


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
        metadata=payload.metadata,
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


def _read_r1_agent_readiness(package_dir: Path) -> dict[str, Any]:
    try:
        wiki_root = package_dir.parent.parent if package_dir.parent.name == "deals" else None
        return ic_agent_runtime.build_r1_agent_readiness(package_dir.name, wiki_root=wiki_root)
    except (FileNotFoundError, ValueError):
        agents = []
        profiles = {profile["id"]: profile for profile in ic_policy.list_ic_profiles(include_runtime=False)}
        for profile_id in ic_policy.R1_AGENT_SEQUENCE:
            profile = profiles.get(profile_id, {"id": profile_id, "label": profile_id, "role": profile_id})
            agents.append({
                "agent_id": profile_id,
                "role": profile.get("role"),
                "label": profile.get("label") or profile_id,
                "r1_sequence_index": profile.get("r1_sequence_index"),
                "round_name": "R1",
                "allowed": False,
                "would_queue": False,
                "blocking_reasons": ["readiness_unavailable"],
                "warnings": [],
                "preflight_status": "unavailable",
                "has_startup_receipt": False,
                "startup_receipt_id": None,
                "has_report": False,
                "submitted": False,
                "dry_run": True,
                "hermes_called": False,
                "report_written": False,
                "workflow_advanced": False,
            })
        return {
            "schema_version": "siq_ic_r1_agent_readiness_v1",
            "deal_id": package_dir.name,
            "round_name": "R1",
            "workflow_action": "run-r1-agent",
            "dry_run": True,
            "current_phase": None,
            "workflow_status": None,
            "preflight_status": "unavailable",
            "next_agent_id": None,
            "ready_count": 0,
            "blocked_count": len(agents),
            "agents": agents,
            "hermes_called": False,
            "report_written": False,
            "workflow_advanced": False,
        }


def _read_deal_workflow_artifacts(package_dir: Path) -> dict[str, Any]:
    reports = _canonical_keyed_payload(
        deal_store.redact_public_payload(deal_store.read_json(package_dir / "phases" / "r1_reports.json", {}) or {})
    )
    receipts = _receipt_agents(
        deal_store.redact_public_payload(deal_store.read_json(package_dir / "phases" / "startup_receipts.json", {}) or {})
    )
    disputes_summary = deal_disputes.summarize_deal_disputes_package(package_dir)
    dispute_summaries = disputes_summary.get("disputes") if isinstance(disputes_summary.get("disputes"), list) else []

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

    return {
        "r1_agent_sequence": list(ic_policy.R1_AGENT_SEQUENCE),
        "agent_reports": agent_reports,
        "r1_agent_readiness": _read_r1_agent_readiness(package_dir),
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


@router.get("/ic/script-migration")
def get_ic_script_migration(
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    del current_user
    try:
        return {"matrix": ic_policy.public_openclaw_script_migration_matrix()}
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{deal_id}/status")
def get_deal_status(
    deal_id: str,
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    del current_user
    try:
        return deal_status.summarize_deal_status(deal_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


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


@router.get("/{deal_id}/documents")
def list_deal_documents(
    deal_id: str,
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    del current_user
    try:
        return {"documents": deal_documents.list_deal_documents(deal_id)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.post("/{deal_id}/documents")
def upload_deal_document(
    deal_id: str,
    file: UploadFile = File(...),
    document_type: str = Form(""),
    source_note: str = Form(""),
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    try:
        document = deal_documents.create_deal_document(
            deal_id=deal_id,
            filename=file.filename,
            content_type=file.content_type,
            stream=file.file,
            document_type=document_type,
            source_note=source_note,
            created_by=_user_payload(current_user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc
    finally:
        file.file.close()
    return {"document": document}


@router.get("/{deal_id}/documents/{document_id}")
def get_deal_document(
    deal_id: str,
    document_id: str,
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    del current_user
    try:
        return {"document": deal_documents.get_deal_document(deal_id, document_id)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Deal document not found: {document_id}") from exc


@router.delete("/{deal_id}/documents/{document_id}")
def delete_deal_document(
    deal_id: str,
    document_id: str,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    try:
        return deal_documents.delete_deal_document(
            deal_id,
            document_id,
            deleted_by=_user_payload(current_user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Deal document not found: {document_id}") from exc


@router.post("/{deal_id}/documents/{document_id}/bind-parser-task")
async def bind_deal_document_parser_task(
    deal_id: str,
    document_id: str,
    payload: DealDocumentBindParserTaskRequest,
    current_user: User = Depends(require_permission("report.create")),
    async_session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    try:
        task_id = deal_documents.validate_parser_task_id(payload.task_id)
        if not await _user_has_document_task_access(async_session, current_user, task_id):
            raise HTTPException(status_code=403, detail="Document parser task does not belong to current user")
        document = deal_documents.bind_parser_task(
            deal_id,
            document_id,
            task_id=task_id,
            artifact_path=payload.artifact_path,
            note=payload.note,
            bound_by=_user_payload(current_user),
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Deal document not found: {document_id}") from exc
    return {"document": document}


@router.post("/{deal_id}/evidence/build")
def build_deal_evidence(
    deal_id: str,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    try:
        return deal_store.redact_public_payload(deal_evidence.build_deal_evidence_package(
            deal_id,
            built_by=_user_payload(current_user),
        ))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.get("/{deal_id}/evidence")
def get_deal_evidence(
    deal_id: str,
    q: str | None = Query(default=None, max_length=300),
    dimension: str | None = Query(default=None, max_length=80),
    document_id: str | None = Query(default=None, max_length=80),
    source_url: str | None = Query(default=None, max_length=300),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    del current_user
    try:
        return deal_store.redact_public_payload(deal_evidence.read_deal_evidence_package(
            deal_id,
            preview_limit=limit,
            q=q,
            dimension=dimension,
            document_id=document_id,
            source_url=source_url,
        ))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.get("/{deal_id}/evidence/quality")
def get_deal_evidence_quality(
    deal_id: str,
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    del current_user
    try:
        return deal_store.redact_public_payload(deal_evidence.read_deal_evidence_quality(deal_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.post("/{deal_id}/evidence/ingest/dry-run")
@router.post("/{deal_id}/evidence/ingest-dry-run")
def build_deal_evidence_ingest_dry_run(
    deal_id: str,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    try:
        return {"ingest_dry_run": deal_store.redact_public_payload(
            deal_evidence.build_deal_evidence_ingest_dry_run(
                deal_id,
                created_by=_user_payload(current_user),
            )
        )}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.get("/{deal_id}/evidence/ingest/dry-run")
@router.get("/{deal_id}/evidence/ingest-dry-run")
def get_deal_evidence_ingest_dry_run(
    deal_id: str,
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    del current_user
    try:
        return deal_store.redact_public_payload(deal_evidence.read_deal_evidence_ingest_dry_run(deal_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.get("/{deal_id}/reports")
def list_deal_reports(
    deal_id: str,
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    del current_user
    try:
        return deal_store.redact_public_payload(deal_reports.list_deal_reports(deal_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.get("/{deal_id}/reports/r1-agents")
def list_deal_r1_agent_reports(
    deal_id: str,
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    del current_user
    try:
        return deal_store.redact_public_payload(deal_reports.list_r1_agent_reports(deal_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.get("/{deal_id}/reports/r2-agents")
def list_deal_r2_agent_reports(
    deal_id: str,
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    del current_user
    try:
        return deal_store.redact_public_payload(deal_reports.list_r2_agent_reports(deal_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.get("/{deal_id}/reports/r3-review")
def get_deal_r3_review(
    deal_id: str,
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    del current_user
    try:
        return deal_store.redact_public_payload(deal_reports.summarize_r3_review(deal_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.get("/{deal_id}/reports/{report_path:path}")
def get_deal_report(
    deal_id: str,
    report_path: str,
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    del current_user
    try:
        return deal_store.redact_public_payload(deal_reports.read_deal_report(deal_id, report_path))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Deal report not found: {report_path}") from exc


@router.get("/{deal_id}/agents")
def list_deal_agents(
    deal_id: str,
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    del current_user
    try:
        return deal_agents.summarize_deal_agents(deal_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.get("/{deal_id}/disputes")
def get_deal_disputes(
    deal_id: str,
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    del current_user
    try:
        return deal_disputes.summarize_deal_disputes(deal_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.post("/{deal_id}/workflow/identify-disputes")
def post_workflow_identify_disputes(
    deal_id: str,
    payload: WorkflowIdentifyDisputesRequest | None = None,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    request = payload or WorkflowIdentifyDisputesRequest()
    try:
        return deal_disputes.identify_deal_disputes(
            deal_id,
            dry_run=request.dry_run,
            preserve_rulings=request.preserve_rulings,
            created_by=_user_payload(current_user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.post("/{deal_id}/workflow/disputes/{dispute_id}/ruling")
def post_workflow_dispute_ruling(
    deal_id: str,
    dispute_id: str,
    payload: WorkflowDisputeRulingRequest,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    try:
        return deal_disputes.rule_deal_dispute(
            deal_id,
            dispute_id,
            decision=payload.decision,
            rationale=payload.rationale,
            required_followups=payload.required_followups,
            evidence_ids=payload.evidence_ids,
            resolved=payload.resolved,
            overwrite=payload.overwrite,
            dry_run=payload.dry_run,
            created_by=_user_payload(current_user),
        )
    except ValueError as exc:
        detail = str(exc)
        if detail.startswith("Dispute not found:"):
            raise HTTPException(status_code=404, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.post("/{deal_id}/workflow/generate-dispute-rulings")
def post_workflow_generate_dispute_rulings(
    deal_id: str,
    payload: WorkflowGenerateDisputeRulingsRequest | None = None,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    request = payload or WorkflowGenerateDisputeRulingsRequest()
    try:
        return deal_disputes.generate_deal_dispute_rulings(
            deal_id,
            dry_run=request.dry_run,
            overwrite=request.overwrite,
            created_by=_user_payload(current_user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.get("/{deal_id}/phase-artifacts")
def get_deal_phase_artifacts(
    deal_id: str,
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    del current_user
    try:
        return deal_phase_artifacts.summarize_deal_phase_artifacts(deal_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.post("/{deal_id}/agents/{profile_id}/startup-retrieval")
def generate_agent_startup_retrieval(
    deal_id: str,
    profile_id: str,
    payload: StartupRetrievalRequest | None = None,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    request = payload or StartupRetrievalRequest()
    try:
        receipt = ic_startup_retrieval.generate_startup_retrieval_receipt(
            deal_id,
            profile_id,
            round_name=request.round_name,
            query=request.query,
            limit=request.limit,
            created_by=_user_payload(current_user),
        )
        redacted = deal_store.redact_public_payload(receipt)
        return {
            "deal_id": deal_store.validate_deal_id(deal_id),
            "agent_id": redacted.get("agent_id") if isinstance(redacted, dict) else profile_id,
            "receipt": redacted,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.get("/{deal_id}/agents/{profile_id}/startup-retrieval")
def get_agent_startup_retrieval(
    deal_id: str,
    profile_id: str,
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    del current_user
    try:
        return deal_store.redact_public_payload(
            ic_startup_retrieval.read_startup_retrieval_receipt(deal_id, profile_id)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.get("/{deal_id}/agents/{profile_id}/task-payload")
def get_agent_task_payload_dry_run(
    deal_id: str,
    profile_id: str,
    round_name: str = Query(default="R1"),
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    del current_user
    try:
        return deal_store.redact_public_payload(
            ic_agent_runtime.build_ic_agent_task_dry_run(
                deal_id,
                profile_id,
                round_name=round_name,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.post("/{deal_id}/agents/{profile_id}/dry-run")
def post_agent_task_dry_run(
    deal_id: str,
    profile_id: str,
    payload: AgentTaskDryRunRequest | None = None,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    del current_user
    request = payload or AgentTaskDryRunRequest()
    try:
        return deal_store.redact_public_payload(
            ic_agent_runtime.build_ic_agent_task_dry_run(
                deal_id,
                profile_id,
                round_name=request.round_name,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.post("/{deal_id}/workflow/run-r1-agent")
async def post_workflow_run_r1_agent(
    deal_id: str,
    payload: WorkflowRunR1AgentRequest,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    try:
        if not payload.dry_run:
            return deal_store.redact_public_payload(
                await ic_agent_runtime.run_workflow_r1_agent(
                    deal_id,
                    payload.profile_id,
                    round_name=payload.round_name,
                    created_by=_user_payload(current_user),
                )
            )
        return deal_store.redact_public_payload(
            ic_agent_runtime.build_workflow_r1_agent_run_dry_run(
                deal_id,
                payload.profile_id,
                round_name=payload.round_name,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc
    except (RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=f"Hermes R1 agent run failed: {exc}") from exc


@router.post("/{deal_id}/workflow/run-r1-serial")
async def post_workflow_run_r1_serial(
    deal_id: str,
    payload: WorkflowRunR1SerialRequest | None = None,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    request = payload or WorkflowRunR1SerialRequest()
    try:
        if not request.dry_run:
            return deal_store.redact_public_payload(
                await ic_agent_runtime.run_workflow_r1_serial(
                    deal_id,
                    round_name=request.round_name,
                    max_agents=request.max_agents,
                    created_by=_user_payload(current_user),
                )
            )
        return deal_store.redact_public_payload(
            ic_agent_runtime.build_workflow_r1_serial_run_dry_run(
                deal_id,
                round_name=request.round_name,
                max_agents=request.max_agents,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc
    except (RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=f"Hermes R1 serial run failed: {exc}") from exc


@router.get("/{deal_id}/evidence/{evidence_id}")
def get_deal_evidence_item(
    deal_id: str,
    evidence_id: str,
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    del current_user
    try:
        return deal_store.redact_public_payload(deal_evidence.get_deal_evidence_item(deal_id, evidence_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Deal evidence not found: {evidence_id}") from exc


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
    try:
        contract = deal_reports.summarize_r4_decision(deal_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc
    return {
        "decision": decision_json,
        "report_markdown": report_markdown,
        "report_path": "decision/IC_DECISION_REPORT.md" if report_markdown else None,
        "contract": contract,
    }


@router.post("/{deal_id}/decision/human-confirmation")
def post_deal_decision_human_confirmation(
    deal_id: str,
    payload: DealDecisionHumanConfirmationRequest,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    try:
        return deal_decision.update_human_confirmation(
            deal_id,
            status=payload.status,
            confirmed_by=_user_payload(current_user),
            override_reason=payload.override_reason,
            override_decision=payload.override_decision,
            override_score=payload.override_score,
            dry_run=payload.dry_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


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
    try:
        summary = deal_audit.summarize_deal_audit(deal_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc
    return {
        "audit": deal_store.redact_public_payload(audit),
        "summary": summary,
    }


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
    try:
        summary = deal_manifest.summarize_deal_manifest(deal_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc
    return {
        "manifest": deal_store.redact_public_payload(manifest),
        "summary": summary,
    }
