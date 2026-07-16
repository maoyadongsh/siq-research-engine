from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import httpx
from database import get_async_session
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from services.auth_dependencies import require_permission
from services.auth_service import User
from services.job_service import create_job_service
from services.path_config import BACKEND_DATA_ROOT
from services.usage_service import UserArtifact
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from services import (
    agent_memory_service,
    deal_agents,
    deal_audit,
    deal_contracts,
    deal_decision,
    deal_discussion,
    deal_disputes,
    deal_documents,
    deal_evidence,
    deal_evidence_milvus,
    deal_manifest,
    deal_phase_artifacts,
    deal_reports,
    deal_status,
    deal_store,
    ic_agent_runtime,
    ic_intake,
    ic_policy,
    ic_report_submission,
    ic_startup_retrieval,
    ic_workflow,
    primary_market_wiki,
)

router = APIRouter(prefix="/deals", tags=["deals"])
deal_job_service = create_job_service(store_path=BACKEND_DATA_ROOT / "deals" / "jobs.json")
_REPORT_CREATE_DEPENDENCY = Depends(require_permission("report.create"))


class DealCreateRequest(BaseModel):
    deal_id: str = Field(..., min_length=3)
    company_name: str = Field(..., min_length=1)
    industry: str = ""
    stage: str = ""
    deal_type: str = ""
    source: str = "manual"


class DealDocumentBindParserTaskRequest(BaseModel):
    task_id: str = Field(..., min_length=2)
    artifact_path: str | None = None
    note: str = ""


class StartupRetrievalRequest(BaseModel):
    round_name: str = "R1"
    query: str | None = None
    limit: int = Field(default=10, ge=1, le=50)
    include_external: bool = False
    external_providers: list[str] | None = None
    include_vector: bool = True
    include_rerank: bool = True
    vector_collections: list[str] | None = None


class AgentTaskDryRunRequest(BaseModel):
    round_name: str = "R1"


class WorkflowRunR1AgentRequest(BaseModel):
    profile_id: str = Field(..., min_length=1)
    round_name: str = "R1"
    dry_run: bool = True
    expected_evidence_snapshot_hash: str | None = Field(default=None, min_length=64, max_length=64)


class WorkflowSubmitR1ReportRequest(BaseModel):
    agent_id: str | None = None
    profile_id: str | None = None
    report: dict[str, Any] = Field(default_factory=dict)
    overwrite: bool = False
    dry_run: bool = True


class WorkflowRunR0IntakeRequest(BaseModel):
    search_key: str | None = None
    task_description: dict[str, Any] = Field(default_factory=dict)
    include_external: bool = False
    external_providers: list[str] | None = None
    max_results: int = Field(default=5, ge=1, le=20)
    dry_run: bool = False


class WorkflowRunR1SerialRequest(BaseModel):
    round_name: str = "R1"
    dry_run: bool = True
    max_agents: int = Field(default=6, ge=1, le=6)
    expected_evidence_snapshot_hash: str | None = Field(default=None, min_length=64, max_length=64)


class WorkflowRunR0CoordinatorRequest(BaseModel):
    dry_run: bool = True
    mode: Literal["model", "deterministic_fallback"] = "model"
    timeout: float | None = Field(default=None, ge=1, le=14400)
    expected_evidence_snapshot_hash: str | None = Field(default=None, min_length=64, max_length=64)


class WorkflowIdentifyDisputesRequest(BaseModel):
    dry_run: bool = True
    preserve_rulings: bool = True


class WorkflowDisputeRulingRequest(BaseModel):
    decision: str = Field(..., min_length=1)
    rationale: str = Field(..., min_length=1)
    required_followups: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    resolved: bool
    overwrite: bool = False
    dry_run: bool = True


class WorkflowSubmitChairmanRulingsRequest(BaseModel):
    rulings: list[dict[str, Any]] = Field(default_factory=list)
    overwrite: bool = False
    dry_run: bool = True


class WorkflowGenerateDisputeRulingsRequest(BaseModel):
    dry_run: bool = True
    overwrite: bool = False


class WorkflowRunR15ChairmanRequest(BaseModel):
    dry_run: bool = True
    mode: Literal["model", "deterministic_fallback"] = "model"
    overwrite: bool = False
    timeout: float | None = Field(default=None, ge=1, le=14400)
    expected_evidence_snapshot_hash: str | None = Field(default=None, min_length=64, max_length=64)


class WorkflowRunR2Request(BaseModel):
    dry_run: bool = True
    mode: Literal["model", "deterministic_fallback"] = "model"
    timeout: float | None = Field(default=None, ge=1, le=14400)
    expected_evidence_snapshot_hash: str | None = Field(default=None, min_length=64, max_length=64)


class WorkflowRunR3Request(BaseModel):
    dry_run: bool = True
    mode: Literal["model", "deterministic_fallback"] = "model"
    skip: bool = False
    skip_reason: str | None = None
    timeout: float | None = Field(default=None, ge=1, le=14400)
    expected_evidence_snapshot_hash: str | None = Field(default=None, min_length=64, max_length=64)


class WorkflowFinalizeR4Request(BaseModel):
    dry_run: bool = True
    mode: Literal["model", "deterministic_fallback"] = "model"
    overwrite: bool = False
    timeout: float | None = Field(default=None, ge=1, le=14400)
    expected_evidence_snapshot_hash: str | None = Field(default=None, min_length=64, max_length=64)


class WorkflowAdvanceNextRequest(BaseModel):
    dry_run: bool = True
    allow_hermes: bool = False
    max_agents: int = Field(default=1, ge=1, le=6)
    r3_skip: bool = False
    r3_skip_reason: str | None = None
    r4_overwrite: bool = False
    expected_evidence_snapshot_hash: str | None = Field(default=None, min_length=64, max_length=64)


class WorkflowStateSnapshotRequest(BaseModel):
    allow_hermes: bool = False
    max_agents: int = Field(default=1, ge=1, le=6)
    r3_skip: bool = False
    r3_skip_reason: str | None = None
    r4_overwrite: bool = False


class DealDiscussionBuildRequest(BaseModel):
    dry_run: bool = True
    overwrite: bool = False
    phases: list[str] | str | None = None


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


def _require_expected_evidence_snapshot(deal_id: str, expected_hash: str | None) -> None:
    expected = str(expected_hash or "").strip()
    if not expected:
        return
    current = ic_startup_retrieval.current_evidence_identity(deal_id)
    actual = str(current.get("evidence_snapshot_hash") or "").strip()
    if actual != expected:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "evidence_snapshot_mismatch",
                "expected_evidence_snapshot_hash": expected,
                "current_evidence_snapshot_hash": actual or None,
            },
        )


def _report_memory_text(report: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, label in [
        ("summary", "summary"),
        ("recommendation", "recommendation"),
        ("rationale", "rationale"),
        ("risk_flags", "risk_flags"),
        ("open_questions", "open_questions"),
    ]:
        value = report.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (list, dict)):
            rendered = json_dumps_safe(value)
        else:
            rendered = str(value)
        parts.append(f"{label}: {rendered}")
    return "\n".join(parts).strip()


def json_dumps_safe(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)


async def _remember_deal_r1_report(
    async_session: AsyncSession,
    *,
    current_user: User,
    deal_id: str,
    profile_id: str | None,
    report: dict[str, Any] | None,
    source_id: str | None,
) -> None:
    if not report:
        return
    content = _report_memory_text(report)
    if not content:
        return
    user_id = getattr(current_user, "id", None)
    if user_id is None:
        return
    profile = agent_memory_service.normalize_profile(profile_id or str(report.get("agent_id") or "siq_ic_master_coordinator"))
    context = agent_memory_service.MemoryRequestContext(
        tenant_id=agent_memory_service.DEFAULT_TENANT_ID,
        user_id=int(user_id),
        profile=profile,
        agent_group=agent_memory_service.infer_agent_group(profile),
        session_id=f"deal-{deal_id}-{profile}-r1-memory",
        deal_id=deal_id,
        visibility="project_shared",
    )
    try:
        await agent_memory_service.record_project_access_binding(
            async_session,
            tenant_id=context.tenant_id,
            resource_type="deal",
            resource_id=deal_id,
            principal_type="user",
            principal_id=int(user_id),
            role="owner",
            commit=False,
        )
        await agent_memory_service.promote_memory_item(
            async_session,
            context,
            title=f"{deal_id} R1 {profile} report",
            content=content,
            memory_type="project_fact",
            source_type="deal_r1_report",
            source_id=source_id or f"{deal_id}:{profile}:r1",
            confidence=float(report.get("confidence") or 0.75) if isinstance(report.get("confidence"), (int, float)) else 0.75,
            importance=0.85,
            metadata={
                "deal_id": deal_id,
                "profile_id": profile,
                "score": report.get("score"),
                "recommendation": report.get("recommendation"),
            },
            status="active",
            commit=False,
        )
        await async_session.commit()
    except Exception as exc:
        await async_session.rollback()
        print(f"[agent-memory] failed to remember R1 report for deal {deal_id}: {exc}")


def _role_value(user: User) -> str:
    role = getattr(user, "role", "")
    return str(role.value if hasattr(role, "value") else role)


def _is_admin_user(user: User) -> bool:
    return _role_value(user) in {"admin", "super_admin"}


def _record_deal_access_decision(
    deal_id: str,
    *,
    action: str,
    decision: str,
    current_user: User,
    reason: str | None = None,
) -> None:
    try:
        deal_store.append_access_decision(
            deal_id,
            action=action,
            decision=decision,
            actor=_user_payload(current_user),
            reason=reason,
        )
    except Exception:
        # Access checks must not become unavailable because the append-only audit
        # sidecar cannot be written. The route still returns the auth decision.
        return


def require_deal_access(deal_id: str, action: str, current_user: User) -> None:
    try:
        allowed = deal_store.user_can_access_deal(deal_id, current_user, action=action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc
    if not allowed:
        _record_deal_access_decision(
            deal_id,
            action=action,
            decision="denied",
            current_user=current_user,
            reason="deal_object_access_denied",
        )
        raise _not_found(deal_id)
    if action not in {"view", "read", "list"}:
        _record_deal_access_decision(
            deal_id,
            action=action,
            decision="allowed",
            current_user=current_user,
        )


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
    deals = deal_store.filter_deals_for_user(deal_store.list_deals(), current_user)
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
async def create_deal(
    payload: DealCreateRequest,
    current_user: User = Depends(require_permission("report.create")),
    async_session: AsyncSession = Depends(get_async_session),
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
    try:
        await agent_memory_service.record_project_access_binding(
            async_session,
            tenant_id=agent_memory_service.DEFAULT_TENANT_ID,
            resource_type="deal",
            resource_id=payload.deal_id,
            principal_type="user",
            principal_id=int(current_user.id),
            role="owner",
            commit=True,
        )
    except Exception as exc:
        await async_session.rollback()
        print(f"[agent-memory] failed to bind deal memory access for deal {payload.deal_id}: {exc}")
    return {"deal": deal}


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
    require_deal_access(deal_id, "view", current_user)
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
    require_deal_access(deal_id, "view", current_user)
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
    require_deal_access(deal_id, "view", current_user)
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
        require_deal_access(deal_id, "write", current_user)
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
    require_deal_access(deal_id, "view", current_user)
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
        require_deal_access(deal_id, "write", current_user)
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
        require_deal_access(deal_id, "write", current_user)
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
        require_deal_access(deal_id, "write", current_user)
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
    require_deal_access(deal_id, "view", current_user)
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
    require_deal_access(deal_id, "view", current_user)
    try:
        return deal_store.redact_public_payload(deal_evidence.read_deal_evidence_quality(deal_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.post("/{deal_id}/evidence/index-milvus")
def index_deal_evidence_milvus(
    deal_id: str,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    try:
        require_deal_access(deal_id, "write", current_user)
        receipt = deal_evidence_milvus.index_deal_evidence_milvus(
            deal_id,
            created_by=_user_payload(current_user),
        )
        return {"milvus_index": deal_store.redact_public_payload(receipt)}
    except deal_evidence_milvus.DealEvidenceMilvusIndexError as exc:
        detail = exc.receipt or {"status": "failed", "error": str(exc)[:300]}
        raise HTTPException(
            status_code=502,
            detail=deal_store.redact_public_payload(detail),
        ) from exc
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
        require_deal_access(deal_id, "write", current_user)
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
    require_deal_access(deal_id, "view", current_user)
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
    require_deal_access(deal_id, "view", current_user)
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
    require_deal_access(deal_id, "view", current_user)
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
    require_deal_access(deal_id, "view", current_user)
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
    require_deal_access(deal_id, "view", current_user)
    try:
        return deal_store.redact_public_payload(deal_reports.summarize_r3_review(deal_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.post("/{deal_id}/reports/discussion/build")
def post_deal_discussion_build(
    deal_id: str,
    payload: DealDiscussionBuildRequest | None = None,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    request = payload or DealDiscussionBuildRequest()
    try:
        require_deal_access(deal_id, "write", current_user)
        return deal_store.redact_public_payload(
            deal_discussion.build_deal_discussion(
                deal_id,
                dry_run=request.dry_run,
                overwrite=request.overwrite,
                phases=request.phases,
                created_by=_user_payload(current_user),
            )
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    require_deal_access(deal_id, "view", current_user)
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
    require_deal_access(deal_id, "view", current_user)
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
    require_deal_access(deal_id, "view", current_user)
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
        require_deal_access(deal_id, "write", current_user)
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


@router.get("/{deal_id}/workflow/disputes/chairman-task")
def get_workflow_dispute_chairman_task(
    deal_id: str,
    only_unresolved: bool = Query(default=True),
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    require_deal_access(deal_id, "view", current_user)
    try:
        return deal_disputes.build_chairman_ruling_task(
            deal_id,
            only_unresolved=only_unresolved,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.post("/{deal_id}/workflow/disputes/chairman-rulings")
def post_workflow_submit_chairman_rulings(
    deal_id: str,
    payload: WorkflowSubmitChairmanRulingsRequest,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    try:
        require_deal_access(deal_id, "write", current_user)
        return deal_disputes.submit_chairman_rulings(
            deal_id,
            rulings=payload.rulings,
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


@router.post("/{deal_id}/workflow/disputes/{dispute_id}/ruling")
def post_workflow_dispute_ruling(
    deal_id: str,
    dispute_id: str,
    payload: WorkflowDisputeRulingRequest,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    try:
        require_deal_access(deal_id, "write", current_user)
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
        require_deal_access(deal_id, "write", current_user)
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
    require_deal_access(deal_id, "view", current_user)
    try:
        return deal_phase_artifacts.summarize_deal_phase_artifacts(deal_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.get("/{deal_id}/wiki")
def get_primary_market_wiki(
    deal_id: str,
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    require_deal_access(deal_id, "view", current_user)
    try:
        return primary_market_wiki.read_primary_market_wiki(deal_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.post("/{deal_id}/wiki/rebuild")
def rebuild_primary_market_wiki(
    deal_id: str,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    require_deal_access(deal_id, "write", current_user)
    try:
        return primary_market_wiki.rebuild_primary_market_wiki(
            deal_id,
            created_by=_user_payload(current_user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.post("/{deal_id}/workflow/run-r0-intake")
def post_workflow_run_r0_intake(
    deal_id: str,
    payload: WorkflowRunR0IntakeRequest | None = None,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    request = payload or WorkflowRunR0IntakeRequest()
    try:
        require_deal_access(deal_id, "write", current_user)
        return deal_store.redact_public_payload(
            ic_intake.run_r0_intake(
                deal_id,
                search_key=request.search_key,
                task_description=request.task_description,
                include_external=request.include_external,
                external_providers=request.external_providers,
                max_results=request.max_results,
                dry_run=request.dry_run,
                created_by=_user_payload(current_user),
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.get("/{deal_id}/workflow/r0-intake")
def get_workflow_r0_intake(
    deal_id: str,
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    require_deal_access(deal_id, "view", current_user)
    try:
        return deal_store.redact_public_payload(ic_intake.read_r0_intake(deal_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.get("/{deal_id}/workflow/state")
def get_workflow_state(
    deal_id: str,
    allow_hermes: bool = Query(default=False),
    max_agents: int = Query(default=1, ge=1, le=6),
    r3_skip: bool = Query(default=False),
    r3_skip_reason: str | None = Query(default=None),
    r4_overwrite: bool = Query(default=False),
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    require_deal_access(deal_id, "view", current_user)
    try:
        return ic_workflow.summarize_workflow_state(
            deal_id,
            allow_hermes=allow_hermes,
            max_agents=max_agents,
            r3_skip=r3_skip,
            r3_skip_reason=r3_skip_reason,
            r4_overwrite=r4_overwrite,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.post("/{deal_id}/workflow/state/snapshot")
def write_workflow_state_snapshot(
    deal_id: str,
    payload: WorkflowStateSnapshotRequest | None = None,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    request = payload or WorkflowStateSnapshotRequest()
    try:
        require_deal_access(deal_id, "write", current_user)
        return ic_workflow.summarize_workflow_state(
            deal_id,
            allow_hermes=request.allow_hermes,
            max_agents=request.max_agents,
            r3_skip=request.r3_skip,
            r3_skip_reason=request.r3_skip_reason,
            r4_overwrite=request.r4_overwrite,
            write_snapshot=True,
            created_by=_user_payload(current_user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.get("/{deal_id}/workflow/state/snapshot")
def get_workflow_state_snapshot(
    deal_id: str,
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    require_deal_access(deal_id, "view", current_user)
    try:
        return ic_workflow.read_workflow_state_snapshot(deal_id)
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
        require_deal_access(deal_id, "write", current_user)
        receipt = ic_startup_retrieval.generate_startup_retrieval_receipt(
            deal_id,
            profile_id,
            round_name=request.round_name,
            query=request.query,
            limit=request.limit,
            include_external=request.include_external,
            external_providers=request.external_providers,
            include_vector=request.include_vector,
            include_rerank=request.include_rerank,
            vector_collections=request.vector_collections,
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
    round_name: str | None = Query(default=None),
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    require_deal_access(deal_id, "view", current_user)
    try:
        return deal_store.redact_public_payload(
            ic_startup_retrieval.read_startup_retrieval_receipt(
                deal_id,
                profile_id,
                round_name=round_name,
            )
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
    require_deal_access(deal_id, "view", current_user)
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
    request = payload or AgentTaskDryRunRequest()
    try:
        require_deal_access(deal_id, "write", current_user)
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


@router.post("/{deal_id}/workflow/submit-r1-report")
async def post_workflow_submit_r1_report(
    deal_id: str,
    payload: WorkflowSubmitR1ReportRequest,
    current_user: User = Depends(require_permission("report.create")),
    async_session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    try:
        require_deal_access(deal_id, "write", current_user)
        result = deal_store.redact_public_payload(
            ic_report_submission.submit_r1_expert_report(
                deal_id,
                agent_id=payload.agent_id,
                profile_id=payload.profile_id,
                report_payload=payload.report,
                dry_run=payload.dry_run,
                overwrite=payload.overwrite,
                created_by=_user_payload(current_user),
            )
        )
        if not payload.dry_run:
            report = result.get("report") if isinstance(result, dict) else None
            await _remember_deal_r1_report(
                async_session,
                current_user=current_user,
                deal_id=deal_id,
                profile_id=payload.profile_id or payload.agent_id,
                report=report if isinstance(report, dict) else payload.report,
                source_id=f"{deal_id}:{payload.profile_id or payload.agent_id or 'r1'}:manual",
            )
        return result
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.post("/{deal_id}/workflow/run-r1-agent")
async def post_workflow_run_r1_agent(
    deal_id: str,
    payload: WorkflowRunR1AgentRequest,
    current_user: User = Depends(require_permission("report.create")),
    async_session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    try:
        require_deal_access(deal_id, "write", current_user)
        _require_expected_evidence_snapshot(deal_id, payload.expected_evidence_snapshot_hash)
        if not payload.dry_run:
            result = deal_store.redact_public_payload(
                await ic_agent_runtime.run_workflow_r1_agent(
                    deal_id,
                    payload.profile_id,
                    round_name=payload.round_name,
                    created_by=_user_payload(current_user),
                )
            )
            report = result.get("report") if isinstance(result, dict) else None
            await _remember_deal_r1_report(
                async_session,
                current_user=current_user,
                deal_id=deal_id,
                profile_id=payload.profile_id,
                report=report if isinstance(report, dict) else None,
                source_id=f"{deal_id}:{payload.profile_id}:run-r1-agent",
            )
            return result
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
    except ic_agent_runtime.ICTaskAlreadyClaimedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=f"Hermes R1 agent run failed: {exc}") from exc


@router.post("/{deal_id}/workflow/run-r0-coordinator")
async def post_workflow_run_r0_coordinator(
    deal_id: str,
    payload: WorkflowRunR0CoordinatorRequest | None = None,
    current_user: User = _REPORT_CREATE_DEPENDENCY,
) -> dict[str, Any]:
    request = payload or WorkflowRunR0CoordinatorRequest()
    try:
        require_deal_access(deal_id, "write", current_user)
        _require_expected_evidence_snapshot(deal_id, request.expected_evidence_snapshot_hash)
        if request.mode == "deterministic_fallback":
            preflight = deal_contracts.run_deal_preflight(deal_id)
            return deal_store.redact_public_payload({
                "schema_version": "siq_ic_workflow_r0_deterministic_fallback_v1",
                "deal_id": deal_id,
                "phase": "R0",
                "dry_run": request.dry_run,
                "generation_mode": "deterministic_fallback",
                "fallback": True,
                "hermes_called": False,
                "report_written": False,
                "workflow_advanced": False,
                "preflight": preflight,
            })
        readiness = ic_agent_runtime.build_model_phase_receipt_readiness(deal_id, "R0")
        if request.dry_run:
            return deal_store.redact_public_payload({
                "schema_version": "siq_ic_workflow_r0_model_dry_run_v1",
                "deal_id": deal_id,
                "phase": "R0",
                "dry_run": True,
                "mode": "model",
                "allowed": readiness["allowed"],
                "blocking_reasons": readiness["blocking_reasons"],
                "receipt_readiness": readiness,
                "hermes_called": False,
                "report_written": False,
                "workflow_advanced": False,
            })
        return deal_store.redact_public_payload(
            await ic_agent_runtime.run_workflow_r0_model(
                deal_id,
                timeout=request.timeout,
                created_by=_user_payload(current_user),
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc
    except ic_agent_runtime.ICTaskAlreadyClaimedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=f"Hermes R0 coordinator run failed: {exc}") from exc


@router.post("/{deal_id}/workflow/run-r1-serial")
async def post_workflow_run_r1_serial(
    deal_id: str,
    payload: WorkflowRunR1SerialRequest | None = None,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    request = payload or WorkflowRunR1SerialRequest()
    try:
        require_deal_access(deal_id, "write", current_user)
        _require_expected_evidence_snapshot(deal_id, request.expected_evidence_snapshot_hash)
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
    except ic_agent_runtime.ICTaskAlreadyClaimedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=f"Hermes R1 serial run failed: {exc}") from exc


@router.post("/{deal_id}/workflow/run-r1-5-chairman")
async def post_workflow_run_r1_5_chairman(
    deal_id: str,
    payload: WorkflowRunR15ChairmanRequest | None = None,
    current_user: User = _REPORT_CREATE_DEPENDENCY,
) -> dict[str, Any]:
    request = payload or WorkflowRunR15ChairmanRequest()
    try:
        require_deal_access(deal_id, "write", current_user)
        _require_expected_evidence_snapshot(deal_id, request.expected_evidence_snapshot_hash)
        if request.dry_run:
            task = deal_disputes.build_chairman_ruling_task(deal_id, only_unresolved=True)
            readiness = (
                ic_agent_runtime.build_model_phase_receipt_readiness(deal_id, "R1.5")
                if request.mode == "model"
                else None
            )
            return deal_store.redact_public_payload({
                "schema_version": "siq_ic_workflow_r1_5_model_dry_run_v1",
                "deal_id": deal_id,
                "phase": "R1.5",
                "mode": request.mode,
                "allowed": bool(task.get("disputes")) and (readiness is None or readiness["allowed"]),
                "blocking_reasons": readiness["blocking_reasons"] if readiness else [],
                "receipt_readiness": readiness,
                "hermes_called": False,
                "workflow_advanced": False,
                "chairman_task": task,
            })
        if request.mode == "deterministic_fallback":
            result = deal_disputes.generate_deal_dispute_rulings(
                deal_id,
                dry_run=False,
                overwrite=request.overwrite,
                created_by=_user_payload(current_user),
            )
            result["generation_mode"] = "deterministic_fallback"
            result["fallback"] = True
            result["hermes_called"] = False
            return deal_store.redact_public_payload(result)
        return deal_store.redact_public_payload(
            await ic_agent_runtime.run_workflow_r1_5_model(
                deal_id,
                overwrite=request.overwrite,
                timeout=request.timeout,
                created_by=_user_payload(current_user),
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc
    except ic_agent_runtime.ICTaskAlreadyClaimedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=f"Hermes R1.5 chairman run failed: {exc}") from exc


@router.post("/{deal_id}/workflow/run-r2")
async def post_workflow_run_r2(
    deal_id: str,
    payload: WorkflowRunR2Request | None = None,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    request = payload or WorkflowRunR2Request()
    try:
        require_deal_access(deal_id, "write", current_user)
        _require_expected_evidence_snapshot(deal_id, request.expected_evidence_snapshot_hash)
        if not request.dry_run:
            return deal_store.redact_public_payload(
                await ic_agent_runtime.run_workflow_r2_async(
                    deal_id,
                    mode=request.mode,
                    timeout=request.timeout,
                    created_by=_user_payload(current_user),
                )
            )
        result = ic_agent_runtime.build_workflow_r2_run_dry_run(deal_id)
        result["mode"] = request.mode
        if request.mode == "model":
            readiness = ic_agent_runtime.build_model_phase_receipt_readiness(deal_id, "R2")
            result["receipt_readiness"] = readiness
            result["blocking_reasons"] = list(dict.fromkeys([
                *result.get("blocking_reasons", []),
                *readiness["blocking_reasons"],
            ]))
            result["allowed"] = not result["blocking_reasons"]
            result["would_write"] = result["allowed"]
        return deal_store.redact_public_payload(result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc
    except ic_agent_runtime.ICTaskAlreadyClaimedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=f"Hermes R2 run failed: {exc}") from exc


@router.post("/{deal_id}/workflow/run-r3")
async def post_workflow_run_r3(
    deal_id: str,
    payload: WorkflowRunR3Request | None = None,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    request = payload or WorkflowRunR3Request()
    try:
        require_deal_access(deal_id, "write", current_user)
        _require_expected_evidence_snapshot(deal_id, request.expected_evidence_snapshot_hash)
        if not request.dry_run:
            return deal_store.redact_public_payload(
                await ic_agent_runtime.run_workflow_r3_async(
                    deal_id,
                    mode=request.mode,
                    skip=request.skip,
                    skip_reason=request.skip_reason,
                    timeout=request.timeout,
                    created_by=_user_payload(current_user),
                )
            )
        result = ic_agent_runtime.build_workflow_r3_run_dry_run(
            deal_id,
            skip=request.skip,
            skip_reason=request.skip_reason,
        )
        result["execution_mode"] = request.mode
        if request.mode == "model":
            readiness = ic_agent_runtime.build_model_phase_receipt_readiness(deal_id, "R3")
            result["receipt_readiness"] = readiness
            result["blocking_reasons"] = list(dict.fromkeys([
                *result.get("blocking_reasons", []),
                *readiness["blocking_reasons"],
            ]))
            result["allowed"] = not result["blocking_reasons"]
            result["would_write"] = result["allowed"]
        return deal_store.redact_public_payload(result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc
    except ic_agent_runtime.ICTaskAlreadyClaimedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=f"Hermes R3 run failed: {exc}") from exc


@router.post("/{deal_id}/workflow/finalize-r4")
async def post_workflow_finalize_r4(
    deal_id: str,
    payload: WorkflowFinalizeR4Request | None = None,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    request = payload or WorkflowFinalizeR4Request()
    try:
        require_deal_access(deal_id, "write", current_user)
        _require_expected_evidence_snapshot(deal_id, request.expected_evidence_snapshot_hash)
        if not request.dry_run:
            return deal_store.redact_public_payload(
                await ic_agent_runtime.finalize_workflow_r4_async(
                    deal_id,
                    mode=request.mode,
                    overwrite=request.overwrite,
                    timeout=request.timeout,
                    created_by=_user_payload(current_user),
                )
            )
        result = ic_agent_runtime.build_workflow_r4_finalize_dry_run(
            deal_id,
            overwrite=request.overwrite,
        )
        result["mode"] = request.mode
        if request.mode == "model":
            readiness = ic_agent_runtime.build_model_phase_receipt_readiness(deal_id, "R4")
            result["receipt_readiness"] = readiness
            result["blocking_reasons"] = list(dict.fromkeys([
                *result.get("blocking_reasons", []),
                *readiness["blocking_reasons"],
            ]))
            result["allowed"] = not result["blocking_reasons"]
            result["would_write"] = result["allowed"]
        return deal_store.redact_public_payload(result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc
    except ic_agent_runtime.ICTaskAlreadyClaimedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=f"Hermes R4 finalize failed: {exc}") from exc


@router.post("/{deal_id}/workflow/advance-next")
async def post_workflow_advance_next(
    deal_id: str,
    payload: WorkflowAdvanceNextRequest | None = None,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    request = payload or WorkflowAdvanceNextRequest()
    try:
        require_deal_access(deal_id, "write", current_user)
        _require_expected_evidence_snapshot(deal_id, request.expected_evidence_snapshot_hash)
        if not request.dry_run:
            return deal_store.redact_public_payload(
                await ic_agent_runtime.run_workflow_advance_next(
                    deal_id,
                    allow_hermes=request.allow_hermes,
                    max_agents=request.max_agents,
                    r3_skip=request.r3_skip,
                    r3_skip_reason=request.r3_skip_reason,
                    r4_overwrite=request.r4_overwrite,
                    created_by=_user_payload(current_user),
                )
            )
        return deal_store.redact_public_payload(
            ic_agent_runtime.build_workflow_advance_next_dry_run(
                deal_id,
                allow_hermes=request.allow_hermes,
                max_agents=request.max_agents,
                r3_skip=request.r3_skip,
                r3_skip_reason=request.r3_skip_reason,
                r4_overwrite=request.r4_overwrite,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc
    except ic_agent_runtime.ICTaskAlreadyClaimedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=f"Workflow advance-next failed: {exc}") from exc


@router.get("/{deal_id}/evidence/{evidence_id}")
def get_deal_evidence_item(
    deal_id: str,
    evidence_id: str,
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    require_deal_access(deal_id, "view", current_user)
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
    require_deal_access(deal_id, "view", current_user)
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
    require_deal_access(deal_id, "view", current_user)
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
    require_deal_access(deal_id, "view", current_user)
    try:
        package_dir = deal_store.safe_deal_dir(deal_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    decision_json = deal_store.read_json(package_dir / "phases" / "r4_decision.json", {}) or {}
    report_quality = deal_store.read_json(package_dir / "decision" / "report_quality.json", None)
    factcheck = deal_store.read_json(package_dir / "decision" / "factcheck.json", None)
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
    return deal_store.redact_public_payload({
        "decision": decision_json,
        "report_markdown": report_markdown,
        "report_path": "decision/IC_DECISION_REPORT.md" if report_markdown else None,
        "contract": contract,
        "quality": report_quality if isinstance(report_quality, dict) else None,
        "factcheck": factcheck if isinstance(factcheck, dict) else None,
    })


@router.post("/{deal_id}/decision/human-confirmation")
def post_deal_decision_human_confirmation(
    deal_id: str,
    payload: DealDecisionHumanConfirmationRequest,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    try:
        require_deal_access(deal_id, "write", current_user)
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
    require_deal_access(deal_id, "view", current_user)
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
    require_deal_access(deal_id, "view", current_user)
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
