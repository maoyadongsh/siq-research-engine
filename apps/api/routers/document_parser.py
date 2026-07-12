import json
import os
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlparse

import httpx
from database import get_async_session
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from services.auth_dependencies import get_current_user, require_permission
from services.auth_service import User
from services.upload_proxy_limits import (
    UPLOAD_PROXY_LIMITER,
    buffer_upload_files,
    close_buffered_uploads,
    env_int,
    upload_proxy_timeout,
)
from services.usage_service import (
    DOCUMENT_PARSE_EVENT,
    UserArtifact,
    ensure_within_quota,
    ensure_within_quota_async,
    next_midnight_shanghai,
    record_usage_async,
    release_pending_quota_async,
    usage_response_payload_async,
)
from sqlmodel import Session, select
from sqlmodel.ext.asyncio.session import AsyncSession

router = APIRouter(prefix="/documents", tags=["document-parser"])

DOCUMENT_PARSER_API_BASE = (
    os.environ.get("SIQ_DOCUMENT_PARSER_API_BASE")
    or os.environ.get("DOCUMENT_PARSER_API_BASE")
    or "http://127.0.0.1:15010"
).rstrip("/")
DOCUMENT_PARSER_ACCESS_TOKEN = os.environ.get("SIQ_DOCUMENT_PARSER_ACCESS_TOKEN", "").strip()
DOCUMENT_PARSER_PROXY_TIMEOUT = float(os.environ.get("SIQ_DOCUMENT_PARSER_PROXY_TIMEOUT", "120"))
DOCUMENT_UPLOAD_MAX_FILE_BYTES = env_int("SIQ_DOCUMENT_UPLOAD_MAX_FILE_BYTES", 100 * 1024 * 1024)
DOCUMENT_UPLOAD_MAX_BATCH_BYTES = env_int("SIQ_DOCUMENT_UPLOAD_MAX_BATCH_BYTES", 200 * 1024 * 1024)
DOCUMENT_TASK_SUBMIT_TIMEOUT = upload_proxy_timeout(
    connect_env="SIQ_DOCUMENT_TASK_CONNECT_TIMEOUT",
    write_env="SIQ_DOCUMENT_TASK_WRITE_TIMEOUT",
    read_env="SIQ_DOCUMENT_TASK_READ_TIMEOUT",
    pool_env="SIQ_DOCUMENT_TASK_POOL_TIMEOUT",
    read_default=180.0,
)
SUPPORTED_DOCUMENT_MARKETS = {"CN", "HK", "US", "EU", "KR", "JP", "DOC"}


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


async def _enforce_quota_or_429_async(
    async_session: AsyncSession,
    current_user: User,
    increment: int = 1,
) -> tuple[int, int | None]:
    try:
        return await ensure_within_quota_async(
            async_session,
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


def _document_headers(
    extra: dict[str, str] | None = None,
    *,
    current_user: User | None = None,
    market_scope: str | None = None,
    allow_legacy: bool = False,
) -> dict[str, str]:
    headers = dict(extra or {})
    if current_user is not None:
        headers["X-SIQ-User-Id"] = str(getattr(current_user, "id", "") or "")
        headers["X-SIQ-User-Role"] = _role_value(current_user)
        tenant_id = str(getattr(current_user, "tenant_id", "") or getattr(current_user, "tenant", "") or "").strip()
        if tenant_id:
            headers["X-SIQ-Tenant-Id"] = tenant_id
    if market_scope:
        headers["X-SIQ-Market-Scope"] = str(market_scope)
    if allow_legacy:
        headers["X-SIQ-Allow-Legacy-Task"] = "1"
    if DOCUMENT_PARSER_ACCESS_TOKEN:
        headers.setdefault("X-Document-Parser-Token", DOCUMENT_PARSER_ACCESS_TOKEN)
    return headers


def _content_type(headers: httpx.Headers) -> str:
    return headers.get("content-type") or "application/octet-stream"


def _normalize_market(value: Any) -> str:
    market = str(value or "").strip().upper()
    return market if market in SUPPORTED_DOCUMENT_MARKETS else ""


def _market_from_filename(filename: str) -> str:
    text = str(filename or "").upper()
    for market in ("CN", "HK", "US", "EU", "KR", "JP", "DOC"):
        if f"_{market}_" in text:
            return market
    return ""


def _document_task_path(task_id: str, market: str | None = None) -> str:
    query = {"task": task_id}
    normalized_market = _normalize_market(market)
    if normalized_market:
        query["market"] = normalized_market
    return f"/documents?{urlencode(query)}"


def _market_from_artifact(item: UserArtifact | None) -> str:
    if item is None:
        return ""
    path = str(getattr(item, "path", "") or "")
    if path:
        try:
            query = parse_qs(urlparse(path).query)
        except Exception:
            query = {}
        market = _normalize_market((query.get("market") or [""])[0])
        if market:
            return market
    return _market_from_filename(str(getattr(item, "title", "") or ""))


def _enrich_document_task_market(task: dict[str, Any], item: UserArtifact | None = None) -> dict[str, Any]:
    enriched = dict(task)
    market = _normalize_market(enriched.get("market")) or _market_from_artifact(item) or _market_from_filename(str(enriched.get("filename") or ""))
    if market:
        enriched["market"] = market
    return enriched


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


async def _user_has_document_task_access_async(
    async_session: AsyncSession,
    current_user: User,
    task_id: str,
) -> bool:
    if _is_admin(current_user):
        return True
    result = await async_session.exec(
        select(UserArtifact).where(
            UserArtifact.user_id == int(current_user.id),
            UserArtifact.artifact_type == "document_parse",
            UserArtifact.artifact_key == task_id,
        )
    )
    if result.first():
        return True
    result = await async_session.exec(
        select(UserArtifact).where(
            UserArtifact.user_id == int(current_user.id),
            UserArtifact.artifact_type == "document_parse",
            UserArtifact.global_artifact_id == task_id,
        )
    )
    return result.first() is not None


async def _ensure_document_task_access_async(
    async_session: AsyncSession,
    current_user: User,
    task_id: str,
) -> None:
    if not await _user_has_document_task_access_async(async_session, current_user, task_id):
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
    market: str | None = None,
) -> UserArtifact:
    existing = session.exec(
        select(UserArtifact).where(
            UserArtifact.user_id == user_id,
            UserArtifact.artifact_type == "document_parse",
            UserArtifact.artifact_key == task_id,
        )
    ).first()
    path = _document_task_path(task_id, market)
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


async def _record_document_artifact_async(
    async_session: AsyncSession,
    *,
    user_id: int,
    task_id: str,
    filename: str,
    source: str,
    market: str | None = None,
) -> UserArtifact:
    result = await async_session.exec(
        select(UserArtifact).where(
            UserArtifact.user_id == user_id,
            UserArtifact.artifact_type == "document_parse",
            UserArtifact.artifact_key == task_id,
        )
    )
    existing = result.first()
    path = _document_task_path(task_id, market)
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
            async_session.add(existing)
            await async_session.commit()
            await async_session.refresh(existing)
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
    async_session.add(item)
    await async_session.commit()
    await async_session.refresh(item)
    return item


async def _proxy_document_parser(
    request: Request,
    upstream_path: str,
    *,
    method: str | None = None,
    json_body: Any | None = None,
    current_user: User | None = None,
    market_scope: str | None = None,
    allow_legacy: bool = False,
) -> Response:
    request_method = method or request.method
    url = f"{DOCUMENT_PARSER_API_BASE}{upstream_path}"
    kwargs: dict[str, Any] = {
        "headers": _document_headers(
            current_user=current_user,
            market_scope=market_scope,
            allow_legacy=allow_legacy,
        ),
        "params": list(request.query_params.multi_items()),
    }
    if json_body is not None:
        kwargs["json"] = json_body
    elif request_method in {"POST", "PUT", "PATCH"}:
        kwargs["content"] = await request.body()
        content_type = request.headers.get("content-type")
        if content_type:
            kwargs["headers"] = _document_headers(
                {"content-type": content_type},
                current_user=current_user,
                market_scope=market_scope,
                allow_legacy=allow_legacy,
            )
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
async def document_parse_quota(
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
):
    return await usage_response_payload_async(
        async_session,
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
    market: str = Form(""),
    model_version: str = Form("auto"),
    ocr: str = Form("auto"),
    enable_formula: str = Form("true"),
    enable_table: str = Form("true"),
    language: str = Form("auto"),
    page_ranges: str = Form(""),
    extra_formats: str = Form(""),
    no_cache: str = Form("false"),
    data_id: str = Form(""),
    current_user: User = Depends(require_permission("report.create")),
    async_session: AsyncSession = Depends(get_async_session),
):
    requested_market = ""
    content_type = request.headers.get("content-type", "")
    is_multipart = "multipart/form-data" in content_type.lower()
    if is_multipart:
        upload_files = files or []
        if not upload_files:
            raise HTTPException(status_code=400, detail="请上传文件")
        await _enforce_quota_or_429_async(async_session, current_user, increment=len(upload_files))
        requested_market = _normalize_market(market)
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
        async with UPLOAD_PROXY_LIMITER.slot():
            buffered_uploads = await buffer_upload_files(
                upload_files,
                max_file_bytes=DOCUMENT_UPLOAD_MAX_FILE_BYTES,
                max_batch_bytes=DOCUMENT_UPLOAD_MAX_BATCH_BYTES,
                default_filename="document",
                default_content_type="application/octet-stream",
            )
            try:
                multipart = [
                    ("files", (item.filename or "document", item.file, item.content_type or "application/octet-stream"))
                    for item in buffered_uploads
                ]
                async with httpx.AsyncClient(timeout=DOCUMENT_TASK_SUBMIT_TIMEOUT) as client:
                    response = await client.post(
                        f"{DOCUMENT_PARSER_API_BASE}/api/tasks",
                        data=form,
                        files=multipart,
                        headers=_document_headers(current_user=current_user, market_scope=requested_market),
                    )
            except httpx.RequestError as exc:
                await release_pending_quota_async(
                    async_session,
                    user_id=int(current_user.id),
                    event_type=DOCUMENT_PARSE_EVENT,
                )
                raise HTTPException(status_code=502, detail=f"文档解析服务不可用: {exc}") from exc
            finally:
                close_buffered_uploads(buffered_uploads)
    else:
        payload = await request.json()
        requested_market = _normalize_market(payload.get("market")) if isinstance(payload, dict) else ""
        await _enforce_quota_or_429_async(async_session, current_user, increment=1)
        try:
            async with UPLOAD_PROXY_LIMITER.slot():
                async with httpx.AsyncClient(timeout=DOCUMENT_TASK_SUBMIT_TIMEOUT) as client:
                    response = await client.post(
                        f"{DOCUMENT_PARSER_API_BASE}/api/tasks",
                        json=payload,
                        headers=_document_headers(current_user=current_user, market_scope=requested_market),
                    )
        except httpx.RequestError as exc:
            await release_pending_quota_async(
                async_session,
                user_id=int(current_user.id),
                event_type=DOCUMENT_PARSE_EVENT,
            )
            raise HTTPException(status_code=502, detail=f"文档解析服务不可用: {exc}") from exc

    content_type = response.headers.get("content-type", "application/json")
    try:
        payload = response.json()
    except ValueError:
        await release_pending_quota_async(
            async_session,
            user_id=int(current_user.id),
            event_type=DOCUMENT_PARSE_EVENT,
        )
        return Response(content=response.content, status_code=response.status_code, media_type=content_type)

    if 200 <= response.status_code < 300:
        created_tasks = payload.get("tasks") if isinstance(payload, dict) else []
        task_count = len(created_tasks or [])
        if task_count:
            enriched_tasks = []
            for task in created_tasks:
                base_task = dict(task)
                if requested_market and not _normalize_market(base_task.get("market")):
                    base_task["market"] = requested_market
                enriched_tasks.append(_enrich_document_task_market(base_task))
            if isinstance(payload, dict):
                payload["tasks"] = enriched_tasks
            await record_usage_async(
                async_session,
                user_id=int(current_user.id),
                event_type=DOCUMENT_PARSE_EVENT,
                count=task_count,
                source="document_upload" if is_multipart else "document_url",
                metadata_json=json.dumps({"tasks": payload.get("tasks")}, ensure_ascii=False),
            )
            for task in payload.get("tasks") or []:
                task_id = str(task.get("task_id") or "")
                filename = str(task.get("filename") or task_id or "文档解析任务")
                if task_id:
                    await _record_document_artifact_async(
                        async_session,
                        user_id=int(current_user.id),
                        task_id=task_id,
                        filename=filename,
                        source="document_upload" if is_multipart else "document_url",
                        market=task.get("market"),
                    )
        else:
            await release_pending_quota_async(
                async_session,
                user_id=int(current_user.id),
                event_type=DOCUMENT_PARSE_EVENT,
            )
        return payload

    await release_pending_quota_async(
        async_session,
        user_id=int(current_user.id),
        event_type=DOCUMENT_PARSE_EVENT,
    )
    return Response(
        content=json.dumps(payload, ensure_ascii=False),
        status_code=response.status_code,
        media_type=content_type,
    )


@router.post("/import/mineru")
async def import_document_from_mineru(
    request: Request,
    current_user: User = Depends(require_permission("report.create")),
    async_session: AsyncSession = Depends(get_async_session),
):
    payload = await request.json()
    await _enforce_quota_or_429_async(async_session, current_user, increment=1)
    try:
        async with UPLOAD_PROXY_LIMITER.slot():
            async with httpx.AsyncClient(timeout=DOCUMENT_TASK_SUBMIT_TIMEOUT) as client:
                response = await client.post(
                    f"{DOCUMENT_PARSER_API_BASE}/api/import/mineru",
                    json=payload,
                    headers=_document_headers(
                        current_user=current_user,
                        market_scope=_normalize_market(payload.get("market")) if isinstance(payload, dict) else "",
                    ),
                )
    except httpx.RequestError as exc:
        await release_pending_quota_async(
            async_session,
            user_id=int(current_user.id),
            event_type=DOCUMENT_PARSE_EVENT,
        )
        raise HTTPException(status_code=502, detail=f"文档解析服务不可用: {exc}") from exc

    content_type = response.headers.get("content-type", "application/json")
    try:
        upstream_payload = response.json()
    except ValueError:
        await release_pending_quota_async(
            async_session,
            user_id=int(current_user.id),
            event_type=DOCUMENT_PARSE_EVENT,
        )
        return Response(content=response.content, status_code=response.status_code, media_type=content_type)

    if 200 <= response.status_code < 300 and isinstance(upstream_payload, dict):
        task = upstream_payload.get("task") if isinstance(upstream_payload.get("task"), dict) else {}
        task_id = str(task.get("task_id") or "")
        filename = str(task.get("filename") or task_id or "MinerU 导入任务")
        if task_id:
            await record_usage_async(
                async_session,
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
            await _record_document_artifact_async(
                async_session,
                user_id=int(current_user.id),
                task_id=task_id,
                filename=filename,
                source="document_mineru_import",
            )
    elif response.status_code >= 300:
        await release_pending_quota_async(
            async_session,
            user_id=int(current_user.id),
            event_type=DOCUMENT_PARSE_EVENT,
        )
    if 200 <= response.status_code < 300:
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
    return await _proxy_document_parser(request, "/api/import/mineru/candidates", current_user=current_user)


@router.get("/tasks")
async def list_document_tasks(
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
):
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{DOCUMENT_PARSER_API_BASE}/api/tasks",
                headers=_document_headers(current_user=current_user, allow_legacy=True),
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"文档解析任务服务不可用: {exc}") from exc
    tasks = payload.get("tasks") if isinstance(payload, dict) else []
    if _is_admin(current_user) and os.environ.get("SIQ_DOCUMENT_TASK_LIST_WORKSPACE_ONLY", "").lower() not in {"1", "true", "yes"}:
        result = await async_session.exec(select(UserArtifact).where(UserArtifact.artifact_type == "document_parse"))
        artifacts = result.all()
        by_task_id = {
            value: item
            for item in artifacts
            for value in (item.artifact_key, item.global_artifact_id)
            if value
        }
        return {
            "tasks": [_enrich_document_task_market(task, by_task_id.get(str(task.get("task_id") or ""))) for task in (tasks or [])],
            "scope": "system",
        }
    result = await async_session.exec(
        select(UserArtifact).where(
            UserArtifact.user_id == int(current_user.id),
            UserArtifact.artifact_type == "document_parse",
        )
    )
    links = result.all()
    allowed = {
        value
        for item in links
        for value in [item.artifact_key, item.global_artifact_id]
        if value
    }
    by_task_id = {
        value: item
        for item in links
        for value in (item.artifact_key, item.global_artifact_id)
        if value
    }
    visible_tasks = [
        _enrich_document_task_market(task, by_task_id.get(str(task.get("task_id") or "")))
        for task in (tasks or [])
        if str(task.get("task_id") or "") in allowed
    ]
    return {"tasks": visible_tasks, "scope": "workspace"}


@router.get("/tasks/{task_id}")
async def get_document_task(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
):
    await _ensure_document_task_access_async(async_session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/tasks/{quote(task_id, safe='')}", current_user=current_user, allow_legacy=True)


@router.get("/status/{task_id}")
async def get_document_status(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
):
    await _ensure_document_task_access_async(async_session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/status/{quote(task_id, safe='')}", current_user=current_user, allow_legacy=True)


@router.get("/result/{task_id}")
async def get_document_result(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
):
    await _ensure_document_task_access_async(async_session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/result/{quote(task_id, safe='')}", current_user=current_user, allow_legacy=True)


@router.post("/cancel/{task_id}")
async def cancel_document_task(
    request: Request,
    task_id: str,
    current_user: User = Depends(require_permission("report.edit")),
    async_session: AsyncSession = Depends(get_async_session),
):
    await _ensure_document_task_access_async(async_session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/cancel/{quote(task_id, safe='')}", method="POST", current_user=current_user, allow_legacy=True)


@router.post("/retry/{task_id}")
async def retry_document_task(
    request: Request,
    task_id: str,
    current_user: User = Depends(require_permission("report.create")),
    async_session: AsyncSession = Depends(get_async_session),
):
    await _ensure_document_task_access_async(async_session, current_user, task_id)
    await _enforce_quota_or_429_async(async_session, current_user, increment=1)
    response = await _proxy_document_parser(request, f"/api/retry/{quote(task_id, safe='')}", method="POST", current_user=current_user, allow_legacy=True)
    if 200 <= response.status_code < 300:
        await record_usage_async(
            async_session,
            user_id=int(current_user.id),
            event_type=DOCUMENT_PARSE_EVENT,
            count=1,
            source="document_retry",
            metadata_json=json.dumps({"task_id": task_id}, ensure_ascii=False),
        )
    else:
        await release_pending_quota_async(
            async_session,
            user_id=int(current_user.id),
            event_type=DOCUMENT_PARSE_EVENT,
        )
    return response


@router.delete("/tasks/{task_id}")
async def delete_document_task(
    request: Request,
    task_id: str,
    current_user: User = Depends(require_permission("report.edit")),
    async_session: AsyncSession = Depends(get_async_session),
):
    await _ensure_document_task_access_async(async_session, current_user, task_id)
    result = await async_session.exec(
        select(UserArtifact).where(
            UserArtifact.user_id == int(current_user.id),
            UserArtifact.artifact_type == "document_parse",
            (UserArtifact.artifact_key == task_id) | (UserArtifact.global_artifact_id == task_id),
        )
    )
    user_links = result.all()
    for item in user_links:
        await async_session.delete(item)
    if user_links:
        await async_session.commit()
    result = await async_session.exec(_artifact_statement(task_id))
    remaining_links = result.all()
    if remaining_links and not _is_admin(current_user):
        return {"success": True, "upstream_deleted": False, "scope": "workspace"}
    return await _proxy_document_parser(request, f"/api/tasks/{quote(task_id, safe='')}", method="DELETE", current_user=current_user, allow_legacy=True)


@router.get("/artifact/{task_id}/{artifact:path}")
async def get_document_artifact(
    request: Request,
    task_id: str,
    artifact: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
):
    await _ensure_document_task_access_async(async_session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/artifact/{quote(task_id, safe='')}/{artifact}", current_user=current_user, allow_legacy=True)


@router.get("/download/{task_id}")
async def download_document_package(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
):
    await _ensure_document_task_access_async(async_session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/download/{quote(task_id, safe='')}", current_user=current_user, allow_legacy=True)


@router.post("/download/batch")
async def download_document_batch(
    request: Request,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
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
        if await _user_has_document_task_access_async(async_session, current_user, task_id):
            allowed_task_ids.append(task_id)
    if not allowed_task_ids:
        raise HTTPException(status_code=403, detail="No selected document tasks are accessible")
    return await _proxy_document_parser(
        request,
        "/api/download/batch",
        method="POST",
        json_body={"task_ids": allowed_task_ids},
        current_user=current_user,
        allow_legacy=True,
    )


@router.get("/source/{task_id}/page/{page_number}")
async def source_page(
    request: Request,
    task_id: str,
    page_number: int,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
):
    await _ensure_document_task_access_async(async_session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/source/{quote(task_id, safe='')}/page/{page_number}", current_user=current_user, allow_legacy=True)


@router.get("/source/{task_id}/page-image/{page_number}")
async def source_page_image(
    request: Request,
    task_id: str,
    page_number: int,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
):
    await _ensure_document_task_access_async(async_session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/source/{quote(task_id, safe='')}/page-image/{page_number}", current_user=current_user, allow_legacy=True)


@router.get("/source/{task_id}/block/{block_id}")
async def source_block(
    request: Request,
    task_id: str,
    block_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
):
    await _ensure_document_task_access_async(async_session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/source/{quote(task_id, safe='')}/block/{quote(block_id, safe='')}", current_user=current_user, allow_legacy=True)


@router.get("/source/{task_id}/table/{table_id}")
async def source_table(
    request: Request,
    task_id: str,
    table_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
):
    await _ensure_document_task_access_async(async_session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/source/{quote(task_id, safe='')}/table/{quote(table_id, safe='')}", current_user=current_user, allow_legacy=True)


@router.get("/source/{task_id}/image/{image_id}")
async def source_image(
    request: Request,
    task_id: str,
    image_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
):
    await _ensure_document_task_access_async(async_session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/source/{quote(task_id, safe='')}/image/{quote(image_id, safe='')}", current_user=current_user, allow_legacy=True)


@router.get("/figures/{task_id}")
async def document_figures(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
):
    await _ensure_document_task_access_async(async_session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/figures/{quote(task_id, safe='')}", current_user=current_user, allow_legacy=True)


@router.get("/figures/{task_id}/{image_id}")
async def document_figure(
    request: Request,
    task_id: str,
    image_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
):
    await _ensure_document_task_access_async(async_session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/figures/{quote(task_id, safe='')}/{quote(image_id, safe='')}", current_user=current_user, allow_legacy=True)


@router.get("/table-relations/{task_id}")
async def document_table_relations(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
):
    await _ensure_document_task_access_async(async_session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/table-relations/{quote(task_id, safe='')}", current_user=current_user, allow_legacy=True)


@router.post("/table-relations/{task_id}/{relation_id}/review")
async def review_document_table_relation(
    request: Request,
    task_id: str,
    relation_id: str,
    current_user: User = Depends(require_permission("report.edit")),
    async_session: AsyncSession = Depends(get_async_session),
):
    await _ensure_document_task_access_async(async_session, current_user, task_id)
    body = await request.json()
    return await _proxy_document_parser(
        request,
        f"/api/table-relations/{quote(task_id, safe='')}/{quote(relation_id, safe='')}/review",
        method="POST",
        json_body=body,
        current_user=current_user,
        allow_legacy=True,
    )


@router.post("/logical-tables/{task_id}/{logical_table_id}/split")
async def split_document_logical_table(
    request: Request,
    task_id: str,
    logical_table_id: str,
    current_user: User = Depends(require_permission("report.edit")),
    async_session: AsyncSession = Depends(get_async_session),
):
    await _ensure_document_task_access_async(async_session, current_user, task_id)
    body = await request.json()
    return await _proxy_document_parser(
        request,
        f"/api/logical-tables/{quote(task_id, safe='')}/{quote(logical_table_id, safe='')}/split",
        method="POST",
        json_body=body,
        current_user=current_user,
        allow_legacy=True,
    )


@router.post("/logical-tables/{task_id}/merge")
async def merge_document_logical_tables(
    request: Request,
    task_id: str,
    current_user: User = Depends(require_permission("report.edit")),
    async_session: AsyncSession = Depends(get_async_session),
):
    await _ensure_document_task_access_async(async_session, current_user, task_id)
    body = await request.json()
    return await _proxy_document_parser(
        request,
        f"/api/logical-tables/{quote(task_id, safe='')}/merge",
        method="POST",
        json_body=body,
        current_user=current_user,
        allow_legacy=True,
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
    current_user: User = Depends(require_permission("report.create")),
    async_session: AsyncSession = Depends(get_async_session),
):
    await _ensure_document_task_access_async(async_session, current_user, task_id)
    body = await request.json()
    return await _proxy_document_parser(request, f"/api/extract/{quote(task_id, safe='')}", method="POST", json_body=body, current_user=current_user, allow_legacy=True)


@router.get("/extract/{task_id}/{extract_id}")
async def get_document_extraction(
    request: Request,
    task_id: str,
    extract_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
):
    await _ensure_document_task_access_async(async_session, current_user, task_id)
    return await _proxy_document_parser(request, f"/api/extract/{quote(task_id, safe='')}/{quote(extract_id, safe='')}", current_user=current_user, allow_legacy=True)
