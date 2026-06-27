import json
import os
from urllib.parse import quote
from typing import Any

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlmodel import Session, select

from database import get_session
from services.auth_dependencies import get_current_user
from services.auth_service import User
from services.usage_service import (
    DOCUMENT_PARSE_EVENT,
    UserArtifact,
    ensure_within_quota,
    next_midnight_shanghai,
    record_usage,
    usage_response_payload,
)


router = APIRouter(prefix="/documents", tags=["document-parser"])

DOCUMENT_PARSER_API_BASE = (
    os.environ.get("SIQ_DOCUMENT_PARSER_API_BASE")
    or os.environ.get("DOCUMENT_PARSER_API_BASE")
    or "http://127.0.0.1:15010"
).rstrip("/")
DOCUMENT_PARSER_ACCESS_TOKEN = os.environ.get("SIQ_DOCUMENT_PARSER_ACCESS_TOKEN", "").strip()
DOCUMENT_PARSER_PROXY_TIMEOUT = float(os.environ.get("SIQ_DOCUMENT_PARSER_PROXY_TIMEOUT", "120"))


def _role_value(user: User) -> str:
    return str(user.role.value if hasattr(user.role, "value") else user.role)


def _is_admin(user: User) -> bool:
    return _role_value(user) in {"admin", "super_admin"}


def _quota_error_payload(event_type: str, limit: int, used: int) -> HTTPException:
    reset_at = next_midnight_shanghai().isoformat()
    return HTTPException(
        status_code=429,
        detail={
            "error": "daily_quota_exceeded",
            "type": event_type,
            "limit": limit,
            "used": used,
            "remaining": 0,
            "reset_at": reset_at,
            "resetAt": reset_at,
            "message": "今日文档解析额度已用完，明天 00:00 自动恢复。",
        },
    )


def _enforce_quota_or_429(session: Session, current_user: User, increment: int = 1) -> tuple[int, int | None]:
    try:
        return ensure_within_quota(
            session,
            user_id=int(current_user.id),
            user_role=_role_value(current_user),
            event_type=DOCUMENT_PARSE_EVENT,
            increment=increment,
        )
    except ValueError as exc:
        parts = str(exc).split(":")
        if len(parts) == 4 and parts[0] == "daily_quota_exceeded":
            raise _quota_error_payload(parts[1], int(parts[2]), int(parts[3])) from exc
        raise


def _document_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = dict(extra or {})
    if DOCUMENT_PARSER_ACCESS_TOKEN:
        headers.setdefault("X-Document-Parser-Token", DOCUMENT_PARSER_ACCESS_TOKEN)
    return headers


def _content_type(headers: httpx.Headers) -> str:
    return headers.get("content-type") or "application/octet-stream"


def _user_has_document_task_access(session: Session, current_user: User, task_id: str) -> bool:
    if _is_admin(current_user):
        return True
    item = session.exec(
        select(UserArtifact).where(
            UserArtifact.user_id == int(current_user.id),
            UserArtifact.artifact_type == "document_parse",
            UserArtifact.artifact_key == task_id,
        )
    ).first()
    if item:
        return True
    item = session.exec(
        select(UserArtifact).where(
            UserArtifact.user_id == int(current_user.id),
            UserArtifact.artifact_type == "document_parse",
            UserArtifact.global_artifact_id == task_id,
        )
    ).first()
    return item is not None


def _ensure_document_task_access(session: Session, current_user: User, task_id: str) -> None:
    if not _user_has_document_task_access(session, current_user, task_id):
        raise HTTPException(status_code=403, detail="Document task does not belong to current user")


def _artifact_statement(task_id: str):
    return select(UserArtifact).where(
        UserArtifact.artifact_type == "document_parse",
        (UserArtifact.artifact_key == task_id) | (UserArtifact.global_artifact_id == task_id),
    )


def _record_document_artifact(
    session: Session,
    *,
    user_id: int,
    task_id: str,
    filename: str,
    source: str,
) -> UserArtifact:
    existing = session.exec(
        select(UserArtifact).where(
            UserArtifact.user_id == user_id,
            UserArtifact.artifact_type == "document_parse",
            UserArtifact.artifact_key == task_id,
        )
    ).first()
    path = f"/documents?task={quote(task_id, safe='')}"
    if existing:
        changed = False
        for field, value in {
            "title": filename or task_id,
            "path": path,
            "source": source,
            "global_artifact_id": task_id,
        }.items():
            if value and getattr(existing, field) != value:
                setattr(existing, field, value)
                changed = True
        if changed:
            session.add(existing)
            session.commit()
            session.refresh(existing)
        return existing
    item = UserArtifact(
        user_id=user_id,
        artifact_type="document_parse",
        artifact_key=task_id,
        title=filename or task_id,
        path=path,
        source=source,
        global_artifact_id=task_id,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


async def _proxy_document_parser(
    request: Request,
    upstream_path: str,
    *,
    method: str | None = None,
    json_body: Any | None = None,
) -> Response:
    request_method = method or request.method
    url = f"{DOCUMENT_PARSER_API_BASE}{upstream_path}"
    kwargs: dict[str, Any] = {
        "headers": _document_headers(),
        "params": list(request.query_params.multi_items()),
    }
    if json_body is not None:
        kwargs["json"] = json_body
    elif request_method in {"POST", "PUT", "PATCH"}:
        kwargs["content"] = await request.body()
        content_type = request.headers.get("content-type")
        if content_type:
            kwargs["headers"] = _document_headers({"content-type": content_type})
    try:
        async with httpx.AsyncClient(timeout=DOCUMENT_PARSER_PROXY_TIMEOUT) as client:
            upstream = await client.request(request_method, url, **kwargs)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"文档解析服务不可用: {exc}") from exc
    return Response(
        content=b"" if request.method == "HEAD" else upstream.content,
        status_code=upstream.status_code,
        media_type=_content_type(upstream.headers),
    )


@router.get("/quota")
def document_parse_quota(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return usage_response_payload(
        session,
        user_id=int(current_user.id),
        user_role=_role_value(current_user),
        event_type=DOCUMENT_PARSE_EVENT,
    )


@router.get("/health")
async def document_parser_health(request: Request):
    return await _proxy_document_parser(request, "/api/health")


@router.post("/tasks")
async def create_document_tasks(
    request: Request,
    files: list[UploadFile] | None = File(default=None),
    source_type: str = Form("upload"),
    model_version: str = Form("auto"),
    ocr: str = Form("auto"),
    enable_formula: str = Form("true"),
    enable_table: str = Form("true"),
    language: str = Form("auto"),
    page_ranges: str = Form(""),
    extra_formats: str = Form(""),
    no_cache: str = Form("false"),
    data_id: str = Form(""),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    content_type = request.headers.get("content-type", "")
    is_multipart = "multipart/form-data" in content_type.lower()
    if is_multipart:
        upload_files = files or []
        if not upload_files:
            raise HTTPException(status_code=400, detail="请上传文件")
        _enforce_quota_or_429(session, current_user, increment=len(upload_files))
        form = {
            "source_type": source_type,
            "model_version": model_version,
            "ocr": ocr,
            "enable_formula": enable_formula,
            "enable_table": enable_table,
            "language": language,
            "page_ranges": page_ranges,
            "extra_formats": extra_formats,
            "no_cache": no_cache,
            "data_id": data_id,
        }
        multipart = []
        for item in upload_files:
            content = await item.read()
            multipart.append(("files", (item.filename or "document", content, item.content_type or "application/octet-stream")))
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                response = await client.post(
                    f"{DOCUMENT_PARSER_API_BASE}/api/tasks",
                    data=form,
                    files=multipart,
                    headers=_document_headers(),
                )
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"文档解析服务不可用: {exc}") from exc
    else:
        payload = await request.json()
        _enforce_quota_or_429(session, current_user, increment=1)
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                response = await client.post(
                    f"{DOCUMENT_PARSER_API_BASE}/api/tasks",
                    json=payload,
                    headers=_document_headers(),
                )
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"文档解析服务不可用: {exc}") from exc

    content_type = response.headers.get("content-type", "application/json")
    try:
        payload = response.json()
    except ValueError:
        return Response(content=response.content, status_code=response.status_code, media_type=content_type)

    if 200 <= response.status_code < 300:
        created_tasks = payload.get("tasks") if isinstance(payload, dict) else []
        task_count = len(created_tasks or [])
        if task_count:
            record_usage(
                session,
                user_id=int(current_user.id),
                event_type=DOCUMENT_PARSE_EVENT,
                count=task_count,
                source="document_upload" if is_multipart else "document_url",
                metadata_json=json.dumps({"tasks": created_tasks}, ensure_ascii=False),
            )
            for task in created_tasks:
                task_id = str(task.get("task_id") or "")
                filename = str(task.get("filename") or task_id or "文档解析任务")
                if task_id:
                    _record_document_artifact(
                        session,
                        user_id=int(current_user.id),
                        task_id=task_id,
                        filename=filename,
                        source="document_upload" if is_multipart else "document_url",
                    )
        return payload

    return Response(
        content=json.dumps(payload, ensure_ascii=False),
        status_code=response.status_code,
        media_type=content_type,
    )


@router.post("/import/mineru")
async def import_document_from_mineru(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    payload = await request.json()
    _enforce_quota_or_429(session, current_user, increment=1)
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.post(
                f"{DOCUMENT_PARSER_API_BASE}/api/import/mineru",
                json=payload,
                headers=_document_headers(),
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"文档解析服务不可用: {exc}") from exc

    content_type = response.headers.get("content-type", "application/json")
    try:
        upstream_payload = response.json()
    except ValueError:
        return Response(content=response.content, status_code=response.status_code, media_type=content_type)

    if 200 <= response.status_code < 300 and isinstance(upstream_payload, dict):
        task = upstream_payload.get("task") if isinstance(upstream_payload.get("task"), dict) else {}
        task_id = str(task.get("task_id") or "")
        filename = str(task.get("filename") or task_id or "MinerU 导入任务")
        if task_id:
            record_usage(
                session,
                user_id=int(current_user.id),
                event_type=DOCUMENT_PARSE_EVENT,
                count=1,
                source="document_mineru_import",
                metadata_json=json.dumps(
                    {
                        "task": task,
                        "source_dir": payload.get("source_dir") or payload.get("sourceDir") or "",
                    },
                    ensure_ascii=False,
                ),
            )
            _record_document_artifact(
                session,
                user_id=int(current_user.id),
                task_id=task_id,
                filename=filename,
                source="document_mineru_import",
            )
        return upstream_payload

    return Response(
        content=json.dumps(upstream_payload, ensure_ascii=False),
        status_code=response.status_code,
        media_type=content_type,
    )


@router.get("/import/mineru/candidates")
async def list_mineru_import_candidates(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    return await _proxy_document_parser(request, "/api/import/mineru/candidates")


@router.get("/tasks")
async def list_document_tasks(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{DOCUMENT_PARSER_API_BASE}/api/tasks", headers=_document_headers())
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"文档解析任务服务不可用: {exc}") from exc
    tasks = payload.get("tasks") if isinstance(payload, dict) else []
    if _is_admin(current_user) and os.environ.get("SIQ_DOCUMENT_TASK_LIST_WORKSPACE_ONLY", "").lower() not in {"1", "true", "yes"}:
        return {"tasks": tasks or [], "scope": "system"}
    links = session.exec(
        select(UserArtifact).where(
            UserArtifact.user_id == int(current_user.id),
            UserArtifact.artifact_type == "document_parse",
        )
    ).all()
    allowed = {
        value
        for item in links
        for value in [item.artifact_key, item.global_artifact_id]
        if value
    }
    visible_tasks = [task for task in (tasks or []) if str(task.get("task_id") or "") in allowed]
    return {"tasks": visible_tasks, "scope": "workspace"}


@router.get("/tasks/{task_id}")
async def get_document_task(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _ensure_document_task_access(session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/tasks/{quote(task_id, safe='')}")


@router.get("/status/{task_id}")
async def get_document_status(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _ensure_document_task_access(session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/status/{quote(task_id, safe='')}")


@router.get("/result/{task_id}")
async def get_document_result(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _ensure_document_task_access(session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/result/{quote(task_id, safe='')}")


@router.post("/cancel/{task_id}")
async def cancel_document_task(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _ensure_document_task_access(session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/cancel/{quote(task_id, safe='')}", method="POST")


@router.post("/retry/{task_id}")
async def retry_document_task(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _ensure_document_task_access(session, current_user, task_id)
    _enforce_quota_or_429(session, current_user, increment=1)
    response = await _proxy_document_parser(request, f"/api/retry/{quote(task_id, safe='')}", method="POST")
    if 200 <= response.status_code < 300:
        record_usage(
            session,
            user_id=int(current_user.id),
            event_type=DOCUMENT_PARSE_EVENT,
            count=1,
            source="document_retry",
            metadata_json=json.dumps({"task_id": task_id}, ensure_ascii=False),
        )
    return response


@router.delete("/tasks/{task_id}")
async def delete_document_task(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _ensure_document_task_access(session, current_user, task_id)
    user_links = session.exec(
        select(UserArtifact).where(
            UserArtifact.user_id == int(current_user.id),
            UserArtifact.artifact_type == "document_parse",
            (UserArtifact.artifact_key == task_id) | (UserArtifact.global_artifact_id == task_id),
        )
    ).all()
    for item in user_links:
        session.delete(item)
    if user_links:
        session.commit()
    remaining_links = session.exec(_artifact_statement(task_id)).all()
    if remaining_links and not _is_admin(current_user):
        return {"success": True, "upstream_deleted": False, "scope": "workspace"}
    return await _proxy_document_parser(request, f"/api/tasks/{quote(task_id, safe='')}", method="DELETE")


@router.get("/artifact/{task_id}/{artifact:path}")
async def get_document_artifact(
    request: Request,
    task_id: str,
    artifact: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _ensure_document_task_access(session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/artifact/{quote(task_id, safe='')}/{artifact}")


@router.get("/download/{task_id}")
async def download_document_package(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _ensure_document_task_access(session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/download/{quote(task_id, safe='')}")


@router.post("/download/batch")
async def download_document_batch(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    body = await request.json()
    raw_task_ids = body.get("task_ids") or body.get("taskIds") or []
    if not isinstance(raw_task_ids, list):
        raise HTTPException(status_code=400, detail="task_ids must be a list")
    allowed_task_ids = []
    for raw_task_id in raw_task_ids:
        task_id = str(raw_task_id or "").strip()
        if not task_id or task_id in allowed_task_ids:
            continue
        if _user_has_document_task_access(session, current_user, task_id):
            allowed_task_ids.append(task_id)
    if not allowed_task_ids:
        raise HTTPException(status_code=403, detail="No selected document tasks are accessible")
    return await _proxy_document_parser(
        request,
        "/api/download/batch",
        method="POST",
        json_body={"task_ids": allowed_task_ids},
    )


@router.get("/source/{task_id}/page/{page_number}")
async def source_page(
    request: Request,
    task_id: str,
    page_number: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _ensure_document_task_access(session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/source/{quote(task_id, safe='')}/page/{page_number}")


@router.get("/source/{task_id}/block/{block_id}")
async def source_block(
    request: Request,
    task_id: str,
    block_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _ensure_document_task_access(session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/source/{quote(task_id, safe='')}/block/{quote(block_id, safe='')}")


@router.get("/source/{task_id}/table/{table_id}")
async def source_table(
    request: Request,
    task_id: str,
    table_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _ensure_document_task_access(session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/source/{quote(task_id, safe='')}/table/{quote(table_id, safe='')}")


@router.get("/source/{task_id}/image/{image_id}")
async def source_image(
    request: Request,
    task_id: str,
    image_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _ensure_document_task_access(session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/source/{quote(task_id, safe='')}/image/{quote(image_id, safe='')}")


@router.get("/figures/{task_id}")
async def document_figures(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _ensure_document_task_access(session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/figures/{quote(task_id, safe='')}")


@router.get("/figures/{task_id}/{image_id}")
async def document_figure(
    request: Request,
    task_id: str,
    image_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _ensure_document_task_access(session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/figures/{quote(task_id, safe='')}/{quote(image_id, safe='')}")


@router.get("/table-relations/{task_id}")
async def document_table_relations(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _ensure_document_task_access(session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/table-relations/{quote(task_id, safe='')}")


@router.post("/table-relations/{task_id}/{relation_id}/review")
async def review_document_table_relation(
    request: Request,
    task_id: str,
    relation_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _ensure_document_task_access(session, current_user, task_id)
    body = await request.json()
    return await _proxy_document_parser(
        request,
        f"/api/table-relations/{quote(task_id, safe='')}/{quote(relation_id, safe='')}/review",
        method="POST",
        json_body=body,
    )


@router.post("/logical-tables/{task_id}/{logical_table_id}/split")
async def split_document_logical_table(
    request: Request,
    task_id: str,
    logical_table_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _ensure_document_task_access(session, current_user, task_id)
    body = await request.json()
    return await _proxy_document_parser(
        request,
        f"/api/logical-tables/{quote(task_id, safe='')}/{quote(logical_table_id, safe='')}/split",
        method="POST",
        json_body=body,
    )


@router.post("/logical-tables/{task_id}/merge")
async def merge_document_logical_tables(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _ensure_document_task_access(session, current_user, task_id)
    body = await request.json()
    return await _proxy_document_parser(
        request,
        f"/api/logical-tables/{quote(task_id, safe='')}/merge",
        method="POST",
        json_body=body,
    )


@router.get("/extraction/templates")
async def document_extraction_templates(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    return await _proxy_document_parser(request, "/api/extraction/templates")


@router.post("/extract/{task_id}")
async def extract_document_schema(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _ensure_document_task_access(session, current_user, task_id)
    body = await request.json()
    return await _proxy_document_parser(request, f"/api/extract/{quote(task_id, safe='')}", method="POST", json_body=body)


@router.get("/extract/{task_id}/{extract_id}")
async def get_document_extraction(
    request: Request,
    task_id: str,
    extract_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _ensure_document_task_access(session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/extract/{quote(task_id, safe='')}/{quote(extract_id, safe='')}")
