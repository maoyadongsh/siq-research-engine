"""Product facade for Deal-scoped primary-market prospectus materials."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import httpx
from database import get_async_session
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from services.auth_dependencies import require_permission
from services.auth_service import User
from services.pdf_submission import PDFParseSubmissionResult, PDFSubmissionHooks, submit_pdf_parse
from services.usage_service import PARSE_EVENT, UserArtifact, record_usage_async, release_pending_quota_async
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from routers import deals, workspace
from services import primary_market_materials

router = APIRouter(prefix="/primary-market/projects", tags=["primary-market-materials"])
_REPORT_CREATE_DEPENDENCY = Depends(require_permission("report.create"))
_REPORT_VIEW_DEPENDENCY = Depends(require_permission("report.view"))
_ASYNC_SESSION_DEPENDENCY = Depends(get_async_session)
_PDF_UPLOAD = File(...)


class ReparseRequest(BaseModel):
    reason: str = "manual"
    parse_method: str = "auto"
    formula_enable: bool = True
    table_enable: bool = True


class SourceReviewRequest(BaseModel):
    decision: str
    capability_overrides: dict[str, str] = Field(default_factory=dict)
    note: str = Field(..., min_length=1, max_length=1000)


class SourceDisableRequest(BaseModel):
    note: str = Field(default="", max_length=1000)


class SupersedeRequest(BaseModel):
    superseding_document_id: str
    note: str = Field(default="", max_length=1000)


def _actor(user: User) -> dict[str, Any]:
    return {"id": getattr(user, "id", None), "username": getattr(user, "username", None)}


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "deal_or_material_not_found", "message": "Deal or material not found"},
    )


def _error_from_value(exc: ValueError) -> HTTPException:
    message = str(exc)
    if message.startswith("invalid_pdf"):
        return HTTPException(400, detail={"code": "invalid_pdf", "message": message.split(":", 1)[-1].strip()})
    if message.startswith("prospectus_too_large"):
        return HTTPException(413, detail={"code": "prospectus_too_large", "message": message})
    if message.startswith("material_state_conflict"):
        return HTTPException(409, detail={"code": "material_state_conflict", "message": message})
    if message.startswith("quality_review_invalid"):
        return HTTPException(422, detail={"code": "quality_review_invalid", "message": message})
    return HTTPException(400, detail={"code": "invalid_prospectus_metadata", "message": message})


def _require_access(deal_id: str, action: str, user: User) -> None:
    try:
        deals.require_deal_access(deal_id, action, user)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise _not_found() from exc
        raise


async def _record_material_artifact(
    session: AsyncSession,
    *,
    user: User,
    deal_id: str,
    document: dict[str, Any],
) -> None:
    user_id = int(user.id)
    artifact_key = str(document["document_id"])
    result = await session.exec(
        select(UserArtifact).where(
            UserArtifact.user_id == user_id,
            UserArtifact.artifact_type == "primary_market_material",
            UserArtifact.artifact_key == artifact_key,
        )
    )
    if result.first():
        return
    session.add(
        UserArtifact(
            user_id=user_id,
            artifact_type="primary_market_material",
            artifact_key=artifact_key,
            title=str(document.get("original_filename") or artifact_key),
            path=f"/api/primary-market/projects/{deal_id}/materials/{artifact_key}",
            source="prospectus_upload",
            global_artifact_id=f"{deal_id}:{artifact_key}",
        )
    )
    await session.commit()


def _quota_exception(exc: ValueError) -> HTTPException:
    parts = str(exc).split(":")
    if len(parts) == 4 and parts[0] == "daily_quota_exceeded":
        return HTTPException(
            429,
            detail={
                "code": "parse_quota_exceeded",
                "event_type": parts[1],
                "limit": int(parts[2]),
                "used": int(parts[3]),
            },
        )
    return HTTPException(429, detail={"code": "parse_quota_exceeded", "message": str(exc)})


def _build_submission_hooks(
    session: AsyncSession,
    *,
    user: User,
) -> PDFSubmissionHooks:
    user_id = int(user.id)
    headers = workspace._pdf2md_headers(current_user=user, market_scope="CN")

    async def lookup_tasks():
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{workspace.PDF2MD_API_BASE}/api/tasks", headers=headers)
            response.raise_for_status()
            payload = response.json()
        return payload.get("tasks") if isinstance(payload, dict) else []

    async def reserve_quota(count: int):
        try:
            await workspace.enforce_quota_or_429_async(session, user, PARSE_EVENT, increment=count)
        except HTTPException as exc:
            raise HTTPException(429, detail={"code": "parse_quota_exceeded", "message": exc.detail}) from exc
        except ValueError as exc:
            raise _quota_exception(exc) from exc

    async def release_quota():
        await release_pending_quota_async(session, user_id=user_id, event_type=PARSE_EVENT)

    async def record_usage(tasks: list[dict[str, Any]]):
        await record_usage_async(
            session,
            user_id=user_id,
            event_type=PARSE_EVENT,
            count=len(tasks),
            source="primary_market_prospectus_upload",
            metadata_json=json.dumps({"tasks": tasks}, ensure_ascii=False),
        )

    async def record_artifact(task: dict[str, Any], source: str):
        task_id = str(task.get("task_id") or "")
        if not task_id:
            return
        await workspace.record_user_artifact_async(
            session,
            user_id=user_id,
            artifact_type="parse",
            artifact_key=task_id,
            title=str(task.get("filename") or task_id),
            path=workspace._parse_result_artifact_path(task_id, str(task.get("market") or "CN")),
            source=source,
            global_artifact_id=task_id,
        )

    async def has_artifact(task_id: str):
        return await workspace._user_has_parse_artifact_async(session, user_id, task_id)

    return PDFSubmissionHooks(
        lookup_tasks=lookup_tasks,
        reserve_quota=reserve_quota,
        release_quota=release_quota,
        record_usage=record_usage,
        record_artifact=record_artifact,
        has_artifact=has_artifact,
    )


def _submission_task(result: PDFParseSubmissionResult) -> tuple[dict[str, Any] | None, bool]:
    if result.new_tasks:
        return dict(result.new_tasks[0]), False
    if result.reused_tasks:
        return dict(result.reused_tasks[0]), True
    payload = result.payload if isinstance(result.payload, dict) else {}
    tasks = payload.get("tasks") if isinstance(payload.get("tasks"), list) else []
    if tasks and isinstance(tasks[0], dict):
        return dict(tasks[0]), False
    existing = payload.get("existingTask") or payload.get("existing_task")
    return (dict(existing), True) if isinstance(existing, dict) else (None, False)


async def _submit_document_parse(
    *,
    deal_id: str,
    document: dict[str, Any],
    parse_run: dict[str, Any],
    user: User,
    session: AsyncSession,
    parse_method: str = "auto",
    formula_enable: bool = True,
    table_enable: bool = True,
) -> tuple[dict[str, Any], bool]:
    document_id = str(document["document_id"])
    raw_path = primary_market_materials.deal_raw_pdf_path(deal_id, document_id)
    handle = raw_path.open("rb")
    parser_upload = UploadFile(
        file=handle,
        filename=str(document.get("original_filename") or f"{document_id}.pdf"),
        headers={"content-type": "application/pdf"},
    )
    try:
        try:
            result = await submit_pdf_parse(
                files=[parser_upload],
                parser_api_base=workspace.PDF2MD_API_BASE,
                config={
                    "backend": "hybrid-http-client",
                    "parse_method": parse_method,
                    "market": "CN",
                    "formula_enable": str(formula_enable).lower(),
                    "table_enable": str(table_enable).lower(),
                },
                document_profile=primary_market_materials.CN_A_SHARE_PROSPECTUS_PROFILE,
                source_context={
                    "domain": "primary_market",
                    "deal_id": deal_id,
                    "document_id": document_id,
                    "source_type": "primary_market_prospectus",
                    "parse_run_id": str(parse_run["parse_run_id"]),
                    "origin": "primary_market_materials",
                },
                headers=workspace._pdf2md_headers(current_user=user, market_scope="CN"),
                hooks=_build_submission_hooks(session, user=user),
                parser_version=workspace.PDF_PARSE_CONFIG_VERSION,
                timeout=workspace.PDF_UPLOAD_TIMEOUT,
                max_file_bytes=workspace.PDF_UPLOAD_MAX_FILE_BYTES,
                max_batch_bytes=workspace.PDF_UPLOAD_MAX_BATCH_BYTES,
            )
        except Exception as exc:
            status_code = exc.status_code if isinstance(exc, HTTPException) else 502
            code = "parse_quota_exceeded" if status_code == 429 else "pdf_parser_submit_failed"
            public_message = (
                "PDF parser service is unavailable; the uploaded original was retained for retry"
                if status_code >= 500
                else str(getattr(exc, "detail", None) or exc)
            )
            primary_market_materials.update_parse_run_submission(
                deal_id,
                document_id,
                parse_run["parse_run_id"],
                status="failed",
                parser_version=workspace.PDF_PARSE_CONFIG_VERSION,
                failure_code=code,
                failure_message=public_message,
                actor=_actor(user),
            )
            if isinstance(exc, HTTPException) and status_code < 500:
                raise
            raise HTTPException(
                502,
                detail={"code": "pdf_parser_unavailable", "message": public_message},
            ) from exc
    finally:
        handle.close()

    task, reused = _submission_task(result)
    if not task or not task.get("task_id") or result.status_code >= 500:
        message = (
            "PDF parser service is unavailable; the uploaded original was retained for retry"
            if result.status_code >= 500
            else "PDF parser did not return a task"
        )
        if result.status_code < 500 and isinstance(result.payload, dict):
            message = str(result.payload.get("message") or result.payload.get("detail") or message)
        primary_market_materials.update_parse_run_submission(
            deal_id,
            document_id,
            parse_run["parse_run_id"],
            status="failed",
            parse_config_hash=result.parse_config_hash,
            parser_version=workspace.PDF_PARSE_CONFIG_VERSION,
            failure_code="pdf_parser_submit_failed",
            failure_message=message,
            actor=_actor(user),
        )
        raise HTTPException(502, detail={"code": "pdf_parser_unavailable", "message": message})

    updated = primary_market_materials.update_parse_run_submission(
        deal_id,
        document_id,
        parse_run["parse_run_id"],
        parser_task_id=str(task["task_id"]),
        status="queued",
        parse_config_hash=result.parse_config_hash,
        parser_version=str(task.get("parser_version") or workspace.PDF_PARSE_CONFIG_VERSION),
        actor=_actor(user),
    )
    return updated, reused or result.status_code == 409


async def _fetch_parser_task(
    task_id: str,
    user: User,
    *,
    submitted_by: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    headers = workspace._pdf2md_headers(current_user=None, market_scope="CN")
    owner_id = (submitted_by or {}).get("id")
    if owner_id not in (None, ""):
        headers["X-SIQ-User-Id"] = str(owner_id)
        headers["X-SIQ-User-Role"] = "analyst"
    elif getattr(user, "id", None) is not None:
        headers.update(workspace._pdf2md_headers(current_user=user, market_scope="CN"))
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{workspace.PDF2MD_API_BASE}/api/status/{quote(task_id, safe='')}",
                headers=headers,
            )
    except httpx.RequestError as exc:
        raise HTTPException(
            502,
            detail={"code": "pdf_parser_unavailable", "message": "Parser status service is unavailable"},
        ) from exc
    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        raise HTTPException(502, detail={"code": "pdf_parser_unavailable", "message": "Parser status request failed"})
    payload = response.json()
    if isinstance(payload, dict) and isinstance(payload.get("task"), dict):
        return payload["task"]
    return payload if isinstance(payload, dict) else None


@router.post("/{deal_id}/materials/prospectuses", status_code=202)
async def upload_prospectus(
    deal_id: str,
    file: UploadFile = _PDF_UPLOAD,
    exchange: str = Form(""),
    board: str = Form(""),
    filing_stage: str = Form(""),
    document_date: str = Form(""),
    issuer_name: str = Form(""),
    source_note: str = Form(""),
    supersedes_document_id: str = Form(""),
    current_user: User = _REPORT_CREATE_DEPENDENCY,
    async_session: AsyncSession = _ASYNC_SESSION_DEPENDENCY,
):
    _require_access(deal_id, "write", current_user)
    try:
        created = primary_market_materials.create_prospectus_document(
            deal_id=deal_id,
            filename=file.filename,
            content_type=file.content_type,
            stream=file.file,
            exchange=exchange,
            board=board,
            filing_stage=filing_stage,
            document_date=document_date,
            issuer_name=issuer_name,
            source_note=source_note,
            supersedes_document_id=supersedes_document_id,
            created_by=_actor(current_user),
        )
        document = created["document"]
        await _record_material_artifact(
            async_session, user=current_user, deal_id=deal_id, document=document
        )
        if created["reused"]:
            latest = primary_market_materials.read_material_parse_status(
                deal_id, document["document_id"]
            ).get("parse_run")
            return JSONResponse(status_code=200, content={
                "schema_version": primary_market_materials.PRIMARY_MARKET_UPLOAD_RESPONSE_SCHEMA,
                "document": document,
                "parse_run": latest,
                "status_url": f"/api/primary-market/projects/{deal_id}/materials/{document['document_id']}/parse-status",
                "reused": True,
            })
        parse_run = primary_market_materials.create_parse_run(
            deal_id,
            document["document_id"],
            submitted_by=_actor(current_user),
            parser_version=workspace.PDF_PARSE_CONFIG_VERSION,
        )
        parse_run, parser_reused = await _submit_document_parse(
            deal_id=deal_id,
            document=document,
            parse_run=parse_run,
            user=current_user,
            session=async_session,
        )
        return {
            "schema_version": primary_market_materials.PRIMARY_MARKET_UPLOAD_RESPONSE_SCHEMA,
            "document": primary_market_materials.get_primary_market_material(deal_id, document["document_id"]),
            "parse_run": parse_run,
            "status_url": f"/api/primary-market/projects/{deal_id}/materials/{document['document_id']}/parse-status",
            "reused": parser_reused,
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise _error_from_value(exc) from exc
    except FileNotFoundError as exc:
        raise _not_found() from exc
    finally:
        await file.close()


@router.get("/{deal_id}/materials")
def list_materials(
    deal_id: str,
    current_user: User = _REPORT_VIEW_DEPENDENCY,
):
    _require_access(deal_id, "view", current_user)
    try:
        return {
            "deal_id": deal_id,
            "materials": primary_market_materials.list_primary_market_materials(
                deal_id, include_all=True
            ),
            "analysis_sources": primary_market_materials.list_analysis_sources(deal_id),
        }
    except (ValueError, FileNotFoundError) as exc:
        raise _not_found() from exc


@router.get("/{deal_id}/materials/{document_id}")
def get_material(
    deal_id: str,
    document_id: str,
    current_user: User = _REPORT_VIEW_DEPENDENCY,
):
    _require_access(deal_id, "view", current_user)
    try:
        return primary_market_materials.read_material_detail(deal_id, document_id)
    except (ValueError, FileNotFoundError) as exc:
        raise _not_found() from exc


@router.get("/{deal_id}/materials/{document_id}/parse-status")
async def get_parse_status(
    deal_id: str,
    document_id: str,
    current_user: User = _REPORT_VIEW_DEPENDENCY,
):
    _require_access(deal_id, "view", current_user)
    try:
        current = primary_market_materials.read_material_parse_status(deal_id, document_id)
        run = current.get("parse_run") or {}
        if run.get("parser_task_id") and run.get("status") not in {"succeeded", "failed", "cancelled", "interrupted"}:
            parser_task = await _fetch_parser_task(
                str(run["parser_task_id"]),
                current_user,
                submitted_by=(run.get("submitted_by") if isinstance(run.get("submitted_by"), dict) else None),
            )
            return primary_market_materials.reconcile_parse_run(
                deal_id,
                document_id,
                parser_task=parser_task,
                reconciled_by=_actor(current_user),
            )
        return {**current, "reconciled": False}
    except HTTPException:
        raise
    except primary_market_materials.ArtifactPromotionError as exc:
        raise HTTPException(
            503,
            detail={"code": "artifact_promotion_unavailable", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise _error_from_value(exc) from exc
    except FileNotFoundError as exc:
        raise _not_found() from exc


@router.post("/{deal_id}/materials/{document_id}/reparse", status_code=202)
async def reparse_material(
    deal_id: str,
    document_id: str,
    payload: ReparseRequest,
    current_user: User = _REPORT_CREATE_DEPENDENCY,
    async_session: AsyncSession = _ASYNC_SESSION_DEPENDENCY,
):
    _require_access(deal_id, "write", current_user)
    if payload.reason not in {"parser_upgrade", "quality_retry", "manual"}:
        raise HTTPException(400, detail={"code": "invalid_prospectus_metadata", "message": "invalid reparse reason"})
    try:
        document = primary_market_materials.get_primary_market_material(deal_id, document_id)
        parse_run = primary_market_materials.create_parse_run(
            deal_id,
            document_id,
            submitted_by=_actor(current_user),
            parser_version=workspace.PDF_PARSE_CONFIG_VERSION,
        )
        parse_run, _ = await _submit_document_parse(
            deal_id=deal_id,
            document=document,
            parse_run=parse_run,
            user=current_user,
            session=async_session,
            parse_method=payload.parse_method,
            formula_enable=payload.formula_enable,
            table_enable=payload.table_enable,
        )
        return {
            "schema_version": primary_market_materials.PRIMARY_MARKET_UPLOAD_RESPONSE_SCHEMA,
            "document": primary_market_materials.get_primary_market_material(deal_id, document_id),
            "parse_run": parse_run,
            "status_url": f"/api/primary-market/projects/{deal_id}/materials/{document_id}/parse-status",
            "reused": False,
        }
    except ValueError as exc:
        raise _error_from_value(exc) from exc
    except FileNotFoundError as exc:
        raise _not_found() from exc


@router.post("/{deal_id}/materials/{document_id}/analysis-source/review")
def review_source(
    deal_id: str,
    document_id: str,
    payload: SourceReviewRequest,
    current_user: User = _REPORT_CREATE_DEPENDENCY,
):
    _require_access(deal_id, "write", current_user)
    try:
        return primary_market_materials.review_analysis_source(
            deal_id,
            document_id,
            decision=payload.decision,
            capability_overrides=payload.capability_overrides,
            note=payload.note,
            reviewer=_actor(current_user),
        )
    except ValueError as exc:
        raise _error_from_value(exc) from exc
    except FileNotFoundError as exc:
        raise _not_found() from exc


@router.post("/{deal_id}/materials/{document_id}/analysis-source/disable")
def disable_source(
    deal_id: str,
    document_id: str,
    payload: SourceDisableRequest,
    current_user: User = _REPORT_CREATE_DEPENDENCY,
):
    _require_access(deal_id, "write", current_user)
    try:
        return primary_market_materials.disable_analysis_source(
            deal_id,
            document_id,
            note=payload.note,
            disabled_by=_actor(current_user),
        )
    except ValueError as exc:
        raise _error_from_value(exc) from exc
    except FileNotFoundError as exc:
        raise _not_found() from exc


@router.post("/{deal_id}/materials/{document_id}/supersede")
def supersede(
    deal_id: str,
    document_id: str,
    payload: SupersedeRequest,
    current_user: User = _REPORT_CREATE_DEPENDENCY,
):
    _require_access(deal_id, "write", current_user)
    try:
        return primary_market_materials.supersede_material(
            deal_id,
            document_id,
            superseding_document_id=payload.superseding_document_id,
            note=payload.note,
            superseded_by=_actor(current_user),
        )
    except ValueError as exc:
        raise _error_from_value(exc) from exc
    except FileNotFoundError as exc:
        raise _not_found() from exc


@router.get("/{deal_id}/materials/{document_id}/artifacts/{artifact_name}")
def get_artifact(
    deal_id: str,
    document_id: str,
    artifact_name: str,
    current_user: User = _REPORT_VIEW_DEPENDENCY,
):
    _require_access(deal_id, "view", current_user)
    try:
        path = primary_market_materials.material_artifact_path(deal_id, document_id, artifact_name)
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid_artifact", "message": str(exc)}) from exc
    except FileNotFoundError as exc:
        raise _not_found() from exc
    media_type = "text/markdown" if path.suffix == ".md" else "application/json"
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.get("/{deal_id}/materials/{document_id}/original")
def get_original(
    deal_id: str,
    document_id: str,
    current_user: User = _REPORT_VIEW_DEPENDENCY,
):
    _require_access(deal_id, "view", current_user)
    try:
        document = primary_market_materials.get_primary_market_material(deal_id, document_id)
        path = primary_market_materials.material_original_path(deal_id, document_id)
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid_material", "message": str(exc)}) from exc
    except FileNotFoundError as exc:
        raise _not_found() from exc
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=str(document.get("original_filename") or f"{document_id}.pdf"),
    )


@router.get("/{deal_id}/materials/{document_id}/source/page/{page_number}")
def get_source_page(
    deal_id: str,
    document_id: str,
    page_number: int,
    current_user: User = _REPORT_VIEW_DEPENDENCY,
):
    _require_access(deal_id, "view", current_user)
    try:
        return primary_market_materials.material_source_page(
            deal_id, document_id, page_number
        )
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid_page", "message": str(exc)}) from exc
    except FileNotFoundError as exc:
        raise _not_found() from exc
