"""Product facade for Deal-scoped primary-market prospectus materials."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping
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

from routers import deals, document_parser, workspace
from services import (
    deal_documents,
    deal_evidence,
    deal_evidence_milvus,
    deal_store,
    document_parser_artifact_transport,
    primary_market_materials,
    primary_market_wiki,
)

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


def _parser_owner_scope(user: User) -> dict[str, str]:
    role = document_parser._role_value(user) or "analyst"
    tenant_id = str(
        getattr(user, "tenant_id", "") or getattr(user, "tenant", "") or "unknown"
    ).strip()
    return {
        "owner_id": str(getattr(user, "id", "") or "system"),
        "tenant_id": tenant_id or "unknown",
        "market_scope": "CN",
        "user_role": role,
    }


def _document_parser_headers_for_run(
    run: Mapping[str, Any],
) -> dict[str, str]:
    return document_parser_artifact_transport.parser_owner_headers(
        run,
        access_token=document_parser.DOCUMENT_PARSER_ACCESS_TOKEN,
    )


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
    actor = _actor(user)
    submission_headers = workspace._pdf2md_headers(current_user=user, market_scope="CN")
    submission_hooks = _build_submission_hooks(session, user=user)
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
                headers=submission_headers,
                hooks=submission_hooks,
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
                actor=actor,
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
            actor=actor,
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
        actor=actor,
    )
    return updated, reused or result.status_code == 409


def _generic_raw_path(deal_id: str, document: dict[str, Any]) -> Path:
    package_dir = deal_store.safe_deal_dir(deal_id)
    storage_path = str(document.get("storage_path") or "").strip().replace("\\", "/")
    candidate = (package_dir / storage_path).resolve()
    try:
        candidate.relative_to((package_dir / "data_room" / "raw").resolve())
    except ValueError as exc:
        raise ValueError("material raw path escapes Deal data room") from exc
    if not candidate.is_file():
        raise FileNotFoundError(candidate)
    return candidate


async def _submit_generic_document_parse(
    *,
    deal_id: str,
    document: dict[str, Any],
    parse_run: dict[str, Any],
    user: User,
    session: AsyncSession,
) -> tuple[dict[str, Any], bool]:
    document_id = str(document["document_id"])
    user_id = int(user.id)
    actor = _actor(user)
    submission_headers = _document_parser_headers_for_run(parse_run)
    raw_path = _generic_raw_path(deal_id, document)
    handle = raw_path.open("rb")
    filename = str(document.get("original_filename") or document.get("filename") or document_id)
    content_type = str(document.get("content_type") or "application/octet-stream")
    await document_parser._enforce_quota_or_429_async(session, user, increment=1)
    try:
        try:
            async with httpx.AsyncClient(timeout=document_parser.DOCUMENT_TASK_SUBMIT_TIMEOUT) as client:
                response = await client.post(
                    f"{document_parser.DOCUMENT_PARSER_API_BASE}/api/tasks",
                    data={
                        "source_type": "upload",
                        "market": "CN",
                        "model_version": "auto",
                        "ocr": "auto",
                        "enable_formula": "true",
                        "enable_table": "true",
                        "language": "auto",
                        "data_id": f"primary_market:{deal_id}:{document_id}:{parse_run['parse_run_id']}",
                    },
                    files=[("files", (filename, handle, content_type))],
                    headers=submission_headers,
                )
        except Exception as exc:
            status_code = exc.status_code if isinstance(exc, HTTPException) else 502
            await release_pending_quota_async(
                session,
                user_id=user_id,
                event_type=document_parser.DOCUMENT_PARSE_EVENT,
            )
            primary_market_materials.update_parse_run_submission(
                deal_id,
                document_id,
                parse_run["parse_run_id"],
                status="failed",
                parser_version="document_parser_v1",
                failure_code="document_parser_submit_failed",
                failure_message=str(getattr(exc, "detail", None) or exc),
                actor=actor,
            )
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(
                status_code,
                detail={"code": "document_parser_unavailable", "message": "Document parser service is unavailable"},
            ) from exc
    finally:
        handle.close()

    try:
        payload = response.json()
    except ValueError:
        payload = {}
    tasks = payload.get("tasks") if isinstance(payload, dict) and isinstance(payload.get("tasks"), list) else []
    task = dict(tasks[0]) if tasks and isinstance(tasks[0], dict) else None
    if response.status_code >= 400 or not task or not task.get("task_id"):
        await release_pending_quota_async(
            session,
            user_id=user_id,
            event_type=document_parser.DOCUMENT_PARSE_EVENT,
        )
        message = str(
            (payload.get("message") or payload.get("error") or payload.get("detail"))
            if isinstance(payload, dict)
            else ""
        ) or "Document parser did not return a task"
        primary_market_materials.update_parse_run_submission(
            deal_id,
            document_id,
            parse_run["parse_run_id"],
            status="failed",
            parser_version="document_parser_v1",
            failure_code="document_parser_submit_failed",
            failure_message=message,
            actor=actor,
        )
        raise HTTPException(
            response.status_code if response.status_code >= 400 else 502,
            detail={
                "code": "document_parser_submit_failed",
                "message": message,
                "retryable": response.status_code >= 500,
            },
        )

    task_id = str(task["task_id"])
    await record_usage_async(
        session,
        user_id=user_id,
        event_type=document_parser.DOCUMENT_PARSE_EVENT,
        count=1,
        source="primary_market_material_upload",
        metadata_json=json.dumps({"tasks": [task]}, ensure_ascii=False),
    )
    await document_parser._record_document_artifact_async(
        session,
        user_id=user_id,
        task_id=task_id,
        filename=filename,
        source="primary_market_material_upload",
        market="CN",
    )
    updated = primary_market_materials.update_parse_run_submission(
        deal_id,
        document_id,
        parse_run["parse_run_id"],
        parser_task_id=task_id,
        status="queued",
        parser_version=str(task.get("parser_version") or "document_parser_v1"),
        actor=actor,
    )
    deal_documents.bind_parser_task(
        deal_id,
        document_id,
        task_id=task_id,
        note="primary_market_auto_parse",
        bound_by=actor,
    )
    return updated, False


def _material_pipeline_response(
    deal_id: str,
    document_id: str,
    *,
    base: dict[str, Any] | None = None,
    wiki: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    milvus_index: dict[str, Any] | None = None,
    reused: bool | None = None,
) -> dict[str, Any]:
    current = primary_market_materials.read_material_parse_status(deal_id, document_id)
    document = current.get("document") if isinstance(current.get("document"), dict) else {}
    package_dir = deal_store.safe_deal_dir(deal_id)
    if wiki is None:
        if document.get("wiki_status") == "failed" and document.get("wiki_receipt_path"):
            wiki = deal_store.read_json(package_dir / str(document["wiki_receipt_path"]), None)
        else:
            company_index = deal_store.read_json(
                package_dir / primary_market_wiki.COMPANY_WIKI_INDEX_PATH,
                {},
            ) or {}
            projections = company_index.get("documents") if isinstance(company_index.get("documents"), dict) else {}
            wiki = projections.get(document_id) if isinstance(projections, dict) else None
    snapshot = deal_store.read_json(package_dir / "evidence" / "evidence_snapshot.json", {}) or {}
    quality = deal_store.read_json(package_dir / "evidence" / "evidence_quality_report.json", {}) or {}
    if isinstance(evidence, dict):
        snapshot = evidence.get("evidence_snapshot") or snapshot
        quality = evidence.get("quality_report") or evidence.get("quality") or quality
        milvus_index = milvus_index or evidence.get("milvus_index")
    if milvus_index is None:
        milvus_index = deal_store.read_json(
            package_dir / deal_evidence_milvus.MILVUS_INDEX_RECEIPT_PATH,
            None,
        )
    milvus_index = milvus_index if isinstance(milvus_index, dict) else {"status": "not_requested"}
    quality_documents = quality.get("documents") if isinstance(quality.get("documents"), list) else []
    evidence_document = next(
        (
            item
            for item in quality_documents
            if isinstance(item, dict) and item.get("document_id") == document_id
        ),
        {},
    )
    document_evidence_items = int(evidence_document.get("items") or 0)
    snapshot_hash = str(snapshot.get("snapshot_hash") or "")
    receipt_snapshot_hash = str(milvus_index.get("snapshot_hash") or "")
    global_milvus_status = str(milvus_index.get("status") or "not_requested")
    if global_milvus_status == "failed":
        document_milvus_status = "failed"
    elif document_evidence_items <= 0:
        document_milvus_status = "pending"
    elif not snapshot_hash or receipt_snapshot_hash != snapshot_hash:
        document_milvus_status = "stale"
    elif global_milvus_status in {"indexed", "unchanged"}:
        document_milvus_status = global_milvus_status
    else:
        document_milvus_status = "pending"
    evidence_payload = evidence if isinstance(evidence, dict) else {
        "quality_report": quality,
        "evidence_snapshot": snapshot,
    }
    parse_run = current.get("parse_run") if isinstance(current.get("parse_run"), dict) else {}
    archive_receipt = (
        parse_run.get("archive_receipt")
        if isinstance(parse_run.get("archive_receipt"), dict)
        else {}
    )
    pipeline = {
        "schema_version": "siq_primary_market_material_pipeline_v1",
        "deal_id": deal_id,
        "document_id": document_id,
        "stages": {
            "upload": {"status": "ready", "sha256": document.get("sha256")},
            "parse": {
                "status": document.get("parse_status") or "not_started",
                "task_id": document.get("parse_task_id"),
                "run_id": parse_run.get("parse_run_id"),
                "artifact_transport": archive_receipt.get("transport"),
                "archive_status": archive_receipt.get("status") or "pending",
                "artifact_contract_version": archive_receipt.get(
                    "artifact_contract_version"
                ),
                "bundle_sha256": archive_receipt.get("bundle_sha256"),
            },
            "wiki": {
                "status": (wiki or {}).get("status") or document.get("wiki_status") or "pending",
                "path": (wiki or {}).get("wiki_path") or document.get("wiki_path"),
                "sha256": (wiki or {}).get("wiki_sha256") or document.get("wiki_sha256"),
                "retryable": (wiki or {}).get("retryable"),
                "error": (wiki or {}).get("error") or document.get("wiki_error"),
            },
            "evidence": {
                "status": evidence_document.get("status") or quality.get("status") or "pending",
                "snapshot_hash": snapshot_hash or None,
                "items": document_evidence_items,
                "document": evidence_document,
            },
            "milvus": {
                "status": document_milvus_status,
                "project_status": global_milvus_status,
                "snapshot_hash": milvus_index.get("snapshot_hash"),
                "physical_collection": milvus_index.get("physical_collection"),
                "counts": {
                    "document_items": document_evidence_items,
                    "project_items": (milvus_index.get("counts") or {}).get("items"),
                },
                "error": milvus_index.get("error"),
            },
        },
    }
    response = {
        **(base or {}),
        **current,
        "schema_version": "siq_primary_market_material_pipeline_response_v1",
        "document": document,
        "material": document,
        "wiki": wiki,
        "evidence": evidence_payload,
        "evidence_snapshot": snapshot,
        "milvus_index": milvus_index,
        "pipeline": pipeline,
        "status_url": f"/api/primary-market/projects/{deal_id}/materials/{document_id}/parse-status",
    }
    if reused is not None:
        response["reused"] = reused
    return response


async def _fetch_parser_task(
    task_id: str,
    *,
    parse_run: Mapping[str, Any],
) -> dict[str, Any] | None:
    headers = workspace._pdf2md_headers(current_user=None, market_scope="CN")
    try:
        headers.update(document_parser_artifact_transport.parser_owner_headers(parse_run))
    except document_parser_artifact_transport.DocumentArtifactTransportError as exc:
        raise HTTPException(
            409,
            detail={
                "code": "pdf_parser_identity_scope_invalid",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc
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


async def _fetch_generic_parser_task(
    task_id: str,
    *,
    parse_run: Mapping[str, Any],
) -> dict[str, Any] | None:
    headers = _document_parser_headers_for_run(parse_run)
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{document_parser.DOCUMENT_PARSER_API_BASE}/api/status/{quote(task_id, safe='')}",
                headers=headers,
            )
    except httpx.RequestError as exc:
        raise HTTPException(
            502,
            detail={"code": "document_parser_unavailable", "message": "Parser status service is unavailable"},
        ) from exc
    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        raise HTTPException(
            503,
            detail={
                "code": "document_parser_poll_degraded",
                "message": "Parser status request is temporarily unavailable",
                "retryable": True,
                "upstream_status": response.status_code,
            },
        )
    payload = response.json()
    if isinstance(payload, dict) and isinstance(payload.get("task"), dict):
        return payload["task"]
    return payload if isinstance(payload, dict) else None


def _latest_or_bound_generic_run(
    deal_id: str,
    document_id: str,
    *,
    user: User,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current = primary_market_materials.read_material_parse_status(deal_id, document_id)
    document = current.get("document") if isinstance(current.get("document"), dict) else {}
    run = current.get("parse_run") if isinstance(current.get("parse_run"), dict) else None
    task_id = str(document.get("parse_task_id") or "")
    if run is None and task_id:
        run = primary_market_materials.create_parse_run(
            deal_id,
            document_id,
            submitted_by=_actor(user),
            parser_owner_scope=_parser_owner_scope(user),
            parser_version="document_parser_v1",
        )
        run = primary_market_materials.update_parse_run_submission(
            deal_id,
            document_id,
            run["parse_run_id"],
            parser_task_id=task_id,
            status="queued",
            parser_version="document_parser_v1",
            actor=_actor(user),
        )
        current = primary_market_materials.read_material_parse_status(deal_id, document_id)
    return current, run or {}


async def _reconcile_generic_material(
    deal_id: str,
    document_id: str,
    *,
    user: User,
) -> dict[str, Any]:
    current, run = _latest_or_bound_generic_run(deal_id, document_id, user=user)
    document = current.get("document") if isinstance(current.get("document"), dict) else {}
    task_id = str(run.get("parser_task_id") or document.get("parse_task_id") or "")
    if not task_id:
        return _material_pipeline_response(deal_id, document_id, base=current)

    try:
        parser_task = await _fetch_generic_parser_task(
            task_id,
            parse_run=run,
        )
    except (
        HTTPException,
        document_parser_artifact_transport.DocumentArtifactTransportError,
    ) as exc:
        detail = exc.detail if isinstance(exc, HTTPException) else {
            "code": "document_parser_identity_scope_invalid",
            "message": str(exc),
            "retryable": False,
        }
        response = _material_pipeline_response(
            deal_id,
            document_id,
            base={"parser_poll": {"status": "degraded", "detail": detail}},
        )
        response["pipeline"]["stages"]["parse"]["poll_status"] = "degraded"
        response["pipeline"]["stages"]["parse"]["retryable"] = isinstance(
            exc, HTTPException
        )
        return response
    if not isinstance(parser_task, dict):
        if run.get("parse_run_id"):
            primary_market_materials.update_parse_run_submission(
                deal_id,
                document_id,
                run["parse_run_id"],
                parser_task_id=task_id,
                status="interrupted",
                failure_code="parser_task_missing",
                failure_message="Parser task is missing during reconciliation",
                actor=_actor(user),
            )
        return _material_pipeline_response(deal_id, document_id)

    parser_status = str(parser_task.get("status") or parser_task.get("stage") or "").strip().lower()
    if parser_status in primary_market_materials.PARSER_SUCCESS_STATUSES:
        parse_run_id = str(run.get("parse_run_id") or "")
        if not parse_run_id:
            error = ValueError("document_parser_completed_without_parse_run_identity")
            wiki = primary_market_wiki.record_company_wiki_failure(
                deal_id,
                document_id,
                error,
                parse_task_id=task_id,
                parse_run_id=None,
                projected_by=_actor(user),
            )
            return _material_pipeline_response(deal_id, document_id, wiki=wiki)

        primary_market_materials.update_parse_run_submission(
            deal_id,
            document_id,
            parse_run_id,
            parser_task_id=task_id,
            status="archiving",
            actor=_actor(user),
        )
        try:
            archived = await document_parser_artifact_transport.archive_document_parser_result(
                deal_id=deal_id,
                document_id=document_id,
                parse_run_id=parse_run_id,
                parser_task_id=task_id,
                target_dir=primary_market_materials.deal_parse_run_dir(
                    deal_id,
                    document_id,
                    parse_run_id,
                ),
                api_base=document_parser.DOCUMENT_PARSER_API_BASE,
                headers=_document_parser_headers_for_run(run),
                shared_results_root=deal_documents.DOCUMENT_PARSER_RESULTS_ROOT,
                raw_sha256=str(run.get("raw_sha256") or "") or None,
                parse_config_hash=str(run.get("parse_config_hash") or "") or None,
            )
        except document_parser_artifact_transport.DocumentArtifactTransportUnavailable as exc:
            response = _material_pipeline_response(
                deal_id,
                document_id,
                base={
                    "artifact_archive": {
                        "status": "degraded",
                        "retryable": True,
                        "message": str(exc),
                    }
                },
            )
            response["pipeline"]["stages"]["parse"]["archive_status"] = "degraded"
            response["pipeline"]["stages"]["parse"]["retryable"] = True
            return response
        except document_parser_artifact_transport.DocumentArtifactTransportError as exc:
            wiki = primary_market_wiki.record_company_wiki_failure(
                deal_id,
                document_id,
                exc,
                parse_task_id=task_id,
                parse_run_id=parse_run_id,
                projected_by=_actor(user),
            )
            primary_market_materials.update_parse_run_submission(
                deal_id,
                document_id,
                parse_run_id,
                parser_task_id=task_id,
                status="failed",
                failure_code="parser_artifact_archive_failed",
                failure_message=str(exc),
                actor=_actor(user),
            )
            return _material_pipeline_response(deal_id, document_id, wiki=wiki)

        markdown_path = Path(archived["document_path"])
        wiki = primary_market_wiki.project_material_to_company_wiki_safe(
            deal_id,
            document_id,
            source_path=markdown_path,
            structured_artifact_dir=Path(archived["archive_dir"]),
            parse_task_id=task_id,
            parse_run_id=parse_run_id,
            projected_by=_actor(user),
        )
        primary_market_materials.update_parse_run_submission(
            deal_id,
            document_id,
            parse_run_id,
            parser_task_id=task_id,
            status="succeeded",
            artifact_root=(
                f"parsed_documents/{document_id}/runs/{parse_run_id}"
            ),
            archive_receipt={
                "status": archived.get("status"),
                "transport": archived.get("transport"),
                "artifact_contract_version": (
                    archived.get("archive_manifest") or {}
                ).get("artifact_contract_version"),
                "bundle_sha256": (
                    archived.get("archive_manifest") or {}
                ).get("bundle_sha256"),
            },
            actor=_actor(user),
        )
        if wiki.get("status") == "failed":
            return _material_pipeline_response(deal_id, document_id, wiki=wiki)
        try:
            evidence = deal_evidence.build_deal_evidence_package(
                deal_id,
                built_by=_actor(user),
            )
        except Exception as exc:
            evidence = {
                "status": "failed",
                "quality_report": {
                    "status": "fail",
                    "errors": [f"{type(exc).__name__}: {str(exc)[:300]}"],
                },
            }
        return _material_pipeline_response(
            deal_id,
            document_id,
            wiki=wiki,
            evidence=evidence,
            milvus_index=evidence.get("milvus_index") if isinstance(evidence, dict) else None,
        )

    if parser_status in primary_market_materials.PARSER_FAILURE_STATUSES:
        status = "failed"
    elif parser_status in primary_market_materials.PARSER_CANCELLED_STATUSES:
        status = "cancelled"
    elif parser_status in primary_market_materials.PARSER_PROCESSING_STATUSES:
        status = "parsing"
    else:
        status = "queued"
    if run.get("parse_run_id") and status != run.get("status"):
        primary_market_materials.update_parse_run_submission(
            deal_id,
            document_id,
            run["parse_run_id"],
            parser_task_id=task_id,
            status=status,
            failure_code=str(parser_task.get("error_code") or "") if status == "failed" else None,
            failure_message=str(parser_task.get("error") or parser_task.get("message") or "") if status == "failed" else None,
            actor=_actor(user),
        )
    return _material_pipeline_response(deal_id, document_id)


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
            return JSONResponse(
                status_code=200,
                content=_material_pipeline_response(
                    deal_id,
                    document["document_id"],
                    base={
                        "legacy_schema_version": primary_market_materials.PRIMARY_MARKET_UPLOAD_RESPONSE_SCHEMA,
                        "parse_run": latest,
                    },
                    reused=True,
                ),
            )
        parse_run = primary_market_materials.create_parse_run(
            deal_id,
            document["document_id"],
            submitted_by=_actor(current_user),
            parser_owner_scope=_parser_owner_scope(current_user),
            parser_version=workspace.PDF_PARSE_CONFIG_VERSION,
        )
        parse_run, parser_reused = await _submit_document_parse(
            deal_id=deal_id,
            document=document,
            parse_run=parse_run,
            user=current_user,
            session=async_session,
        )
        return _material_pipeline_response(
            deal_id,
            document["document_id"],
            base={
                "legacy_schema_version": primary_market_materials.PRIMARY_MARKET_UPLOAD_RESPONSE_SCHEMA,
                "parse_run": parse_run,
            },
            reused=parser_reused,
        )
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


@router.post("/{deal_id}/materials/{document_id}/parse", status_code=202)
async def start_material_parse(
    deal_id: str,
    document_id: str,
    current_user: User = _REPORT_CREATE_DEPENDENCY,
    async_session: AsyncSession = _ASYNC_SESSION_DEPENDENCY,
):
    _require_access(deal_id, "write", current_user)
    try:
        current = primary_market_materials.read_material_parse_status(deal_id, document_id)
        latest = current.get("parse_run") if isinstance(current.get("parse_run"), dict) else {}
        if latest.get("status") in {"submitting", "queued", "parsing", "archiving"}:
            return _material_pipeline_response(deal_id, document_id, base=current, reused=True)
        document = primary_market_materials.get_primary_market_material(deal_id, document_id)
        parse_run = primary_market_materials.create_parse_run(
            deal_id,
            document_id,
            submitted_by=_actor(current_user),
            parser_owner_scope=_parser_owner_scope(current_user),
            parser_version=workspace.PDF_PARSE_CONFIG_VERSION
            if document.get("document_type") == primary_market_materials.PROSPECTUS_DOCUMENT_TYPE
            else "document_parser_v1",
        )
        if document.get("document_type") == primary_market_materials.PROSPECTUS_DOCUMENT_TYPE:
            parse_run, parser_reused = await _submit_document_parse(
                deal_id=deal_id,
                document=document,
                parse_run=parse_run,
                user=current_user,
                session=async_session,
            )
        else:
            parse_run, parser_reused = await _submit_generic_document_parse(
                deal_id=deal_id,
                document=document,
                parse_run=parse_run,
                user=current_user,
                session=async_session,
            )
        return _material_pipeline_response(
            deal_id,
            document_id,
            base={"parse_run": parse_run},
            reused=parser_reused,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise _error_from_value(exc) from exc
    except FileNotFoundError as exc:
        raise _not_found() from exc


@router.get("/{deal_id}/materials/{document_id}/parse-status")
async def get_parse_status(
    deal_id: str,
    document_id: str,
    current_user: User = _REPORT_VIEW_DEPENDENCY,
):
    _require_access(deal_id, "view", current_user)
    try:
        material = primary_market_materials.get_primary_market_material(deal_id, document_id)
        if material.get("document_type") != primary_market_materials.PROSPECTUS_DOCUMENT_TYPE:
            return await _reconcile_generic_material(
                deal_id,
                document_id,
                user=current_user,
            )
        current = primary_market_materials.read_material_parse_status(deal_id, document_id)
        run = current.get("parse_run") or {}
        if run.get("parser_task_id") and run.get("status") not in {"succeeded", "failed", "cancelled", "interrupted"}:
            parser_task = await _fetch_parser_task(
                str(run["parser_task_id"]),
                parse_run=run,
            )
            reconciled = primary_market_materials.reconcile_parse_run(
                deal_id,
                document_id,
                parser_task=parser_task,
                reconciled_by=_actor(current_user),
            )
            promotion = reconciled.get("promotion") if isinstance(reconciled.get("promotion"), dict) else {}
            evidence = promotion.get("evidence") if isinstance(promotion.get("evidence"), dict) else None
            return _material_pipeline_response(
                deal_id,
                document_id,
                base=reconciled,
                wiki=promotion.get("wiki") if isinstance(promotion.get("wiki"), dict) else None,
                evidence=evidence,
                milvus_index=evidence.get("milvus_index") if isinstance(evidence, dict) else None,
            )
        return _material_pipeline_response(
            deal_id,
            document_id,
            base={**current, "reconciled": False},
        )
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
            parser_owner_scope=_parser_owner_scope(current_user),
            parser_version=workspace.PDF_PARSE_CONFIG_VERSION
            if document.get("document_type") == primary_market_materials.PROSPECTUS_DOCUMENT_TYPE
            else "document_parser_v1",
        )
        if document.get("document_type") == primary_market_materials.PROSPECTUS_DOCUMENT_TYPE:
            parse_run, parser_reused = await _submit_document_parse(
                deal_id=deal_id,
                document=document,
                parse_run=parse_run,
                user=current_user,
                session=async_session,
                parse_method=payload.parse_method,
                formula_enable=payload.formula_enable,
                table_enable=payload.table_enable,
            )
        else:
            parse_run, parser_reused = await _submit_generic_document_parse(
                deal_id=deal_id,
                document=document,
                parse_run=parse_run,
                user=current_user,
                session=async_session,
            )
        return _material_pipeline_response(
            deal_id,
            document_id,
            base={
                "legacy_schema_version": primary_market_materials.PRIMARY_MARKET_UPLOAD_RESPONSE_SCHEMA,
                "parse_run": parse_run,
            },
            reused=parser_reused,
        )
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
        media_type=str(document.get("content_type") or "application/octet-stream"),
        filename=str(document.get("original_filename") or document.get("filename") or document_id),
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
