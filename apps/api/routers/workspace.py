import json
import os
import re
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import quote, unquote

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from sqlmodel import Session, select

from database import get_session
from services.auth_dependencies import get_current_user
from services.auth_service import User
from services.path_config import REPORT_DOWNLOADS_ROOT, WIKI_ROOT as CONFIG_WIKI_ROOT
from services.usage_service import (
    AGENT_QUESTION_EVENT,
    DOCUMENT_PARSE_EVENT,
    PARSE_EVENT,
    UserArtifact,
    WorkspaceProject,
    ensure_within_quota,
    next_midnight_shanghai,
    record_usage,
    usage_response_payload,
)
from routers import source as source_proxy


router = APIRouter(prefix="/workspace", tags=["workspace"])
pdf_router = APIRouter(prefix="/pdf", tags=["pdf-proxy"])

PDF2MD_API_BASE = (os.environ.get("SIQ_PDF2MD_API_BASE") or os.environ.get("PDF2MD_API_BASE", "http://127.0.0.1:15000")).rstrip("/")
PDF2MD_ACCESS_TOKEN = os.environ.get("PDF2MD_ACCESS_TOKEN", "").strip()
DOWNLOADS_ROOT = REPORT_DOWNLOADS_ROOT
WIKI_ROOT = CONFIG_WIKI_ROOT
TERMINAL_FAILED = {"failed", "error", "failure", "cancelled"}
COMPANY_DIR_RE = re.compile(r"^(?P<code>[A-Za-z0-9]+)-(?P<name>.+)$")
REPORT_URL_RE = re.compile(
    r"(?:/api/wiki)?/companies/(?P<company>[^\s`'\"<>]+)/"
    r"(?P<section>analysis|factcheck|tracking|legal)/(?P<filename>[^\s`'\"<>]+\.html)"
)
REPORT_PATH_RE = re.compile(
    r"(?P<path>(?:/[^\s`'\"<>]+)?/wiki/companies/(?P<company>[^/\s`'\"<>]+)/"
    r"(?P<section>analysis|factcheck|tracking|legal)/(?P<filename>[^/\s`'\"<>]+\.html))"
)
REPORT_SOURCE_ROUTES = {
    "analysis": "/analysis",
    "factcheck": "/verify",
    "tracking": "/tracking",
    "legal": "/legal",
}


def _role_value(user: User) -> str:
    return str(user.role.value if hasattr(user.role, "value") else user.role)


def _is_admin(user: User) -> bool:
    return _role_value(user) in {"admin", "super_admin"}


def _quota_error_payload(event_type: str, limit: int, used: int) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail={
            "error": "daily_quota_exceeded",
            "type": event_type,
            "limit": limit,
            "used": used,
            "remaining": 0,
            "reset_at": next_midnight_shanghai().isoformat(),
            "resetAt": next_midnight_shanghai().isoformat(),
            "message": "今日额度已用完，明天 00:00 自动恢复。",
        },
    )


def _pdf2md_headers() -> dict[str, str]:
    return {"X-PDF2MD-Token": PDF2MD_ACCESS_TOKEN} if PDF2MD_ACCESS_TOKEN else {}


def _ensure_pdf_task_access(session: Session, current_user: User, task_id: str) -> None:
    if not source_proxy._user_has_task_access(session, current_user, task_id):
        raise HTTPException(status_code=403, detail="PDF task does not belong to current user")


def _parse_artifact_statement(task_id: str):
    return select(UserArtifact).where(
        UserArtifact.artifact_type == "parse",
        (UserArtifact.artifact_key == task_id) | (UserArtifact.global_artifact_id == task_id),
    )


async def _proxy_pdf_task(
    request: Request,
    task_id: str,
    upstream_path: str,
    *,
    current_user: User,
    session: Session,
    method: str | None = None,
) -> Response:
    _ensure_pdf_task_access(session, current_user, task_id)
    return await source_proxy._proxy_pdf2md(request, upstream_path, method=method)


async def _proxy_pdf2md_health(request: Request) -> Response:
    return await source_proxy._proxy_pdf2md(request, "/api/health")


def enforce_quota_or_429(session: Session, current_user: User, event_type: str, increment: int = 1) -> tuple[int, int | None]:
    try:
        return ensure_within_quota(
            session,
            user_id=int(current_user.id),
            user_role=_role_value(current_user),
            event_type=event_type,
            increment=increment,
        )
    except ValueError as exc:
        parts = str(exc).split(":")
        if len(parts) == 4 and parts[0] == "daily_quota_exceeded":
            raise _quota_error_payload(parts[1], int(parts[2]), int(parts[3])) from exc
        raise


def record_user_artifact(
    session: Session,
    *,
    user_id: int,
    artifact_type: str,
    artifact_key: str,
    title: str,
    path: str,
    source: str,
    global_artifact_id: str | None = None,
    company_code: str | None = None,
    company_name: str | None = None,
    company_dir: str | None = None,
) -> UserArtifact:
    _upsert_workspace_project(
        session,
        user_id=user_id,
        company_code=company_code,
        company_name=company_name,
        company_dir=company_dir,
        fallback_name=title,
    )
    existing = session.exec(
        select(UserArtifact).where(
            UserArtifact.user_id == user_id,
            UserArtifact.artifact_type == artifact_type,
            UserArtifact.artifact_key == artifact_key,
        )
    ).first()
    if existing:
        changed = False
        for field, value in {
            "title": title,
            "path": path,
            "source": source,
            "global_artifact_id": global_artifact_id,
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
        artifact_type=artifact_type,
        artifact_key=artifact_key,
        title=title,
        path=path,
        source=source,
        global_artifact_id=global_artifact_id,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def _split_company_dir(company_dir: str | None) -> tuple[str | None, str | None]:
    text = str(company_dir or "").strip()
    if not text:
        return None, None
    match = COMPANY_DIR_RE.match(text)
    if not match:
        return None, text
    return match.group("code").strip() or None, match.group("name").strip() or None


def _read_company_json(company_dir: str | None) -> dict:
    if not company_dir:
        return {}
    try:
        safe = (WIKI_ROOT / "companies" / company_dir / "company.json").resolve()
        safe.relative_to(WIKI_ROOT / "companies")
    except Exception:
        return {}
    if not safe.is_file():
        return {}
    try:
        data = json.loads(safe.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def company_identity_from_dir(company_dir: str | None) -> dict:
    code, name = _split_company_dir(company_dir)
    meta = _read_company_json(company_dir)
    return {
        "company_dir": company_dir or "",
        "company_code": str(meta.get("stock_code") or code or "").strip(),
        "company_name": str(meta.get("company_short_name") or meta.get("company_full_name") or name or "").strip(),
    }


def _upsert_workspace_project(
    session: Session,
    *,
    user_id: int,
    company_code: str | None = None,
    company_name: str | None = None,
    company_dir: str | None = None,
    fallback_name: str | None = None,
) -> WorkspaceProject | None:
    identity = company_identity_from_dir(company_dir)
    code = (company_code or identity.get("company_code") or "").strip()
    name = (company_name or identity.get("company_name") or "").strip()
    if not code and not name:
        return None

    candidates = []
    if code:
        candidates.append(WorkspaceProject.company_code == code)
    if name:
        candidates.append(WorkspaceProject.company_name == name)

    existing = None
    if candidates:
        statement = select(WorkspaceProject).where(WorkspaceProject.user_id == user_id)
        statement = statement.where(candidates[0] if len(candidates) == 1 else candidates[0] | candidates[1])
        existing = session.exec(statement).first()

    now = datetime.now(timezone.utc)
    project_name = name or fallback_name or code
    if existing:
        changed = False
        if code and existing.company_code != code:
            existing.company_code = code
            changed = True
        if name and existing.company_name != name:
            existing.company_name = name
            changed = True
        if project_name and existing.name != project_name:
            existing.name = project_name
            changed = True
        existing.updated_at = now
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing

    project = WorkspaceProject(
        user_id=user_id,
        name=project_name,
        company_code=code or None,
        company_name=name or None,
        updated_at=now,
    )
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


def _report_route(section: str, company_dir: str, filename: str | None = None) -> str:
    route = REPORT_SOURCE_ROUTES.get(section, "/analysis")
    query = f"?company={quote(company_dir, safe='')}"
    if filename:
        query += f"&result={quote(filename, safe='')}"
    return f"{route}{query}"


def _report_key(section: str, company_dir: str, filename: str) -> str:
    return f"wiki:{section}:{company_dir}:{filename}"


def _report_title(section: str, company_name: str, filename: str) -> str:
    labels = {
        "analysis": "智能分析",
        "factcheck": "事实核查",
        "tracking": "持续跟踪",
        "legal": "法务合规",
    }
    label = labels.get(section, section)
    return f"{company_name or filename} · {label}"


def _url_text(value: str | None) -> str:
    return unquote(str(value or "").strip())


def extract_report_artifact_from_text(text: str | None, fallback_section: str | None = None) -> dict | None:
    source = text or ""
    matches = list(REPORT_PATH_RE.finditer(source))
    if matches:
        match = matches[-1]
        company_dir = _url_text(match.group("company"))
        section = match.group("section")
        filename = _url_text(match.group("filename"))
        identity = company_identity_from_dir(company_dir)
        return {
            **identity,
            "section": section,
            "filename": filename,
            "source_path": match.group("path"),
            "page_path": _report_route(section, company_dir, filename),
            "artifact_key": _report_key(section, company_dir, filename),
        }

    url_matches = list(REPORT_URL_RE.finditer(source))
    if url_matches:
        match = url_matches[-1]
        company_dir = _url_text(match.group("company"))
        section = match.group("section")
        filename = _url_text(match.group("filename"))
        identity = company_identity_from_dir(company_dir)
        return {
            **identity,
            "section": section,
            "filename": filename,
            "source_path": f"/api/wiki/companies/{company_dir}/{section}/{filename}",
            "page_path": _report_route(section, company_dir, filename),
            "artifact_key": _report_key(section, company_dir, filename),
        }

    return None


def _project_payload(item: WorkspaceProject) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "company_code": item.company_code,
        "company_name": item.company_name,
        "status": item.status,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _artifact_payload(item: UserArtifact) -> dict:
    return {
        "id": item.id,
        "type": item.artifact_type,
        "key": item.artifact_key,
        "title": item.title,
        "path": item.path,
        "source": item.source,
        "globalArtifactId": item.global_artifact_id,
        "createdAt": item.created_at,
        "created_at": item.created_at,
    }


def _download_path_for_relative_path(relative_path: str) -> Path:
    try:
        safe = (DOWNLOADS_ROOT / relative_path).resolve()
        safe.relative_to(DOWNLOADS_ROOT)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="文件路径不合法") from exc
    if not safe.is_file() or safe.suffix.lower() != ".pdf":
        raise HTTPException(status_code=404, detail="PDF not found")
    return safe


def _download_path_from_payload(payload: dict) -> Path:
    relative_path = str(payload.get("relativePath") or payload.get("relative_path") or "").strip()
    saved_path = str(payload.get("savedPath") or payload.get("saved_path") or "").strip()
    file_name = str(payload.get("fileName") or payload.get("file_name") or "").strip()
    company_name = str(payload.get("companyName") or payload.get("company_name") or "").strip().lower()

    if relative_path:
        return _download_path_for_relative_path(relative_path)

    if saved_path:
        try:
            safe = Path(saved_path).resolve()
            safe.relative_to(DOWNLOADS_ROOT)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="文件路径不合法") from exc
        if not safe.is_file() or safe.suffix.lower() != ".pdf":
            raise HTTPException(status_code=404, detail="PDF not found")
        return safe

    if file_name:
        matches = []
        for path in DOWNLOADS_ROOT.rglob(file_name):
            if not path.is_file() or path.suffix.lower() != ".pdf":
                continue
            rel_text = path.relative_to(DOWNLOADS_ROOT).as_posix().lower()
            if company_name and company_name not in rel_text:
                continue
            matches.append(path)
        if matches:
            matches.sort(key=lambda item: item.stat().st_mtime, reverse=True)
            return matches[0].resolve()

    raise HTTPException(status_code=404, detail="PDF not found")


async def _pdf_tasks_by_filename() -> dict[str, dict]:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{PDF2MD_API_BASE}/api/tasks", headers=_pdf2md_headers())
            response.raise_for_status()
            tasks = response.json().get("tasks") or []
    except Exception:
        return {}

    by_filename: dict[str, dict] = {}
    for task in tasks:
        filename = str(task.get("filename") or "").strip()
        if not filename:
            continue
        status = str(task.get("status") or "").lower()
        if status in TERMINAL_FAILED:
            continue
        by_filename.setdefault(filename, task)
    return by_filename


@router.get("/summary")
def workspace_summary(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    user_id = int(current_user.id)
    projects = session.exec(select(WorkspaceProject).where(WorkspaceProject.user_id == user_id)).all()
    artifacts = session.exec(select(UserArtifact).where(UserArtifact.user_id == user_id)).all()
    counts: dict[str, int] = {}
    for item in artifacts:
        counts[item.artifact_type] = counts.get(item.artifact_type, 0) + 1

    recent = sorted(artifacts, key=lambda item: item.created_at, reverse=True)[:8]
    projects_sorted = sorted(projects, key=lambda item: item.updated_at, reverse=True)
    artifacts_sorted = sorted(artifacts, key=lambda item: item.created_at, reverse=True)
    return {
        "user": {
            "id": current_user.id,
            "username": current_user.username,
            "email": current_user.email,
            "full_name": current_user.full_name,
            "role": current_user.role,
            "approval_status": getattr(current_user, "approval_status", "approved"),
            "created_at": current_user.created_at,
            "last_login": current_user.last_login,
        },
        "quotas": {
            "agentQuestion": usage_response_payload(session, user_id=user_id, user_role=_role_value(current_user), event_type=AGENT_QUESTION_EVENT),
            "parseJob": usage_response_payload(session, user_id=user_id, user_role=_role_value(current_user), event_type=PARSE_EVENT),
            "documentParse": usage_response_payload(session, user_id=user_id, user_role=_role_value(current_user), event_type=DOCUMENT_PARSE_EVENT),
        },
        "stats": {
            "projects": len(projects),
            "artifacts": len(artifacts),
            "downloads": counts.get("download", 0),
            "parses": counts.get("parse", 0),
            "documentParses": counts.get("document_parse", 0),
            "reports": counts.get("report", 0),
        },
        "recentArtifacts": [
            _artifact_payload(item)
            for item in recent
        ],
        "projects": [_project_payload(item) for item in projects_sorted],
        "artifacts": [_artifact_payload(item) for item in artifacts_sorted],
    }


@router.get("/artifacts")
def list_workspace_artifacts(
    artifact_type: str | None = None,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    statement = select(UserArtifact).where(UserArtifact.user_id == int(current_user.id))
    if artifact_type:
        statement = statement.where(UserArtifact.artifact_type == artifact_type)
    items = session.exec(statement).all()
    items = sorted(items, key=lambda item: item.created_at, reverse=True)
    return {"artifacts": [_artifact_payload(item) for item in items]}


@router.get("/projects")
def list_workspace_projects(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    items = session.exec(
        select(WorkspaceProject).where(WorkspaceProject.user_id == int(current_user.id))
        .order_by(WorkspaceProject.updated_at.desc())
    ).all()
    return {"projects": [_project_payload(item) for item in items]}


@router.post("/projects")
def create_workspace_project(
    payload: dict,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="项目名称不能为空")
    project = WorkspaceProject(
        user_id=int(current_user.id),
        name=name,
        company_code=str(payload.get("company_code") or "").strip() or None,
        company_name=str(payload.get("company_name") or "").strip() or None,
    )
    session.add(project)
    session.commit()
    session.refresh(project)
    return {
        "id": project.id,
        "name": project.name,
        "company_code": project.company_code,
        "company_name": project.company_name,
        "status": project.status,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }


@router.post("/downloads/link")
def link_download_to_workspace(
    payload: dict,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    safe = _download_path_from_payload(payload)
    rel_text = safe.relative_to(DOWNLOADS_ROOT).as_posix()
    item = record_user_artifact(
        session,
        user_id=int(current_user.id),
        artifact_type="download",
        artifact_key=rel_text,
        title=safe.name,
        path=rel_text,
        source=str(payload.get("source") or "reused_download"),
        global_artifact_id=rel_text,
    )
    return {
        "linked": True,
        "artifact": {
            "id": item.id,
            "type": item.artifact_type,
            "key": item.artifact_key,
            "title": item.title,
            "path": item.path,
            "source": item.source,
        },
    }


@pdf_router.post("/upload")
async def authenticated_pdf_upload(
    files: list[UploadFile] = File(...),
    backend: str = Form("hybrid-http-client"),
    parse_method: str = Form("auto"),
    start_page_id: str = Form(""),
    end_page_id: str = Form(""),
    formula_enable: str = Form("true"),
    table_enable: str = Form("true"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    form = {
        "backend": backend,
        "parse_method": parse_method,
        "start_page_id": start_page_id,
        "end_page_id": end_page_id,
        "formula_enable": formula_enable,
        "table_enable": table_enable,
    }
    multipart = []
    filenames: list[str] = []
    for item in files:
        content = await item.read()
        filename = item.filename or "upload.pdf"
        filenames.append(filename)
        multipart.append(("files", (filename, content, item.content_type or "application/pdf")))

    existing_tasks = await _pdf_tasks_by_filename()
    new_parse_count = sum(1 for filename in filenames if filename not in existing_tasks)
    if new_parse_count:
        enforce_quota_or_429(session, current_user, PARSE_EVENT, increment=new_parse_count)

    async with httpx.AsyncClient(timeout=None) as client:
        response = await client.post(f"{PDF2MD_API_BASE}/api/upload", data=form, files=multipart, headers=_pdf2md_headers())

    content_type = response.headers.get("content-type", "application/json")
    try:
        payload = response.json()
    except ValueError:
        return Response(content=response.content, status_code=response.status_code, media_type=content_type)

    if response.status_code == 409 and isinstance(payload, dict) and payload.get("error") == "duplicate_filename":
        existing = payload.get("existingTask") or payload.get("existing_task") or {}
        task_id = str(existing.get("task_id") or "")
        filename = str(payload.get("filename") or existing.get("filename") or "已有解析任务")
        if task_id:
            record_user_artifact(
                session,
                user_id=int(current_user.id),
                artifact_type="parse",
                artifact_key=task_id,
                title=filename,
                path=f"{PDF2MD_API_BASE}/api/result/{quote(task_id, safe='')}",
                source="reused_parse",
                global_artifact_id=task_id,
            )
        return JSONResponse(content=payload, status_code=409)

    if 200 <= response.status_code < 300:
        created_tasks = payload.get("tasks") if isinstance(payload, dict) else []
        task_count = len(created_tasks or [])
        if task_count:
            record_usage(
                session,
                user_id=int(current_user.id),
                event_type=PARSE_EVENT,
                count=task_count,
                source="pdf_upload",
                metadata_json=json.dumps({"tasks": created_tasks}, ensure_ascii=False),
            )
            for task in created_tasks:
                task_id = str(task.get("task_id") or "")
                filename = str(task.get("filename") or task_id or "解析任务")
                if task_id:
                    record_user_artifact(
                        session,
                        user_id=int(current_user.id),
                        artifact_type="parse",
                        artifact_key=task_id,
                        title=filename,
                        path=f"{PDF2MD_API_BASE}/api/result/{quote(task_id, safe='')}",
                        source="new_parse",
                        global_artifact_id=task_id,
                    )
        return payload

    return Response(
        content=json.dumps(payload, ensure_ascii=False),
        status_code=response.status_code,
        media_type=content_type,
    )


@pdf_router.get("/quota")
def pdf_quota(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return usage_response_payload(
        session,
        user_id=int(current_user.id),
        user_role=_role_value(current_user),
        event_type=PARSE_EVENT,
    )


@pdf_router.get("/health")
async def pdf_health(request: Request):
    return await _proxy_pdf2md_health(request)


@pdf_router.get("/tasks")
async def list_my_pdf_tasks(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{PDF2MD_API_BASE}/api/tasks", headers=_pdf2md_headers())
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"解析任务服务不可用: {exc}") from exc

    tasks = payload.get("tasks") if isinstance(payload, dict) else []
    # The pdf-parser queue is a local system-level runtime shared by ops
    # scripts and the UI. Return the queue to authenticated users; per-task
    # result/source access remains protected by the existing task access checks.
    if os.environ.get("SIQ_PDF_TASK_LIST_WORKSPACE_ONLY", "").strip().lower() not in {"1", "true", "yes"}:
        return {"tasks": tasks or [], "scope": "system"}

    parse_links = session.exec(
        select(UserArtifact).where(
            UserArtifact.user_id == int(current_user.id),
            UserArtifact.artifact_type == "parse",
        )
    ).all()
    allowed_task_ids = {item.artifact_key for item in parse_links if item.artifact_key}
    if not allowed_task_ids:
        return {"tasks": [], "scope": "workspace"}

    visible_tasks = [
        task for task in (tasks or [])
        if str(task.get("task_id") or "") in allowed_task_ids
    ]
    return {"tasks": visible_tasks, "scope": "workspace"}


@pdf_router.get("/status/{task_id}")
async def pdf_task_status(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return await _proxy_pdf_task(
        request,
        task_id,
        f"/api/status/{quote(task_id, safe='')}",
        current_user=current_user,
        session=session,
    )


@pdf_router.get("/result/{task_id}")
async def pdf_task_result(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return await _proxy_pdf_task(
        request,
        task_id,
        f"/api/result/{quote(task_id, safe='')}",
        current_user=current_user,
        session=session,
    )


@pdf_router.get("/quality/{task_id}")
async def pdf_task_quality(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return await _proxy_pdf_task(
        request,
        task_id,
        f"/api/quality/{quote(task_id, safe='')}",
        current_user=current_user,
        session=session,
    )


@pdf_router.get("/financial/{task_id}")
async def pdf_task_financial(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return await _proxy_pdf_task(
        request,
        task_id,
        f"/api/financial/{quote(task_id, safe='')}",
        current_user=current_user,
        session=session,
    )


@pdf_router.post("/cancel/{task_id}")
async def pdf_task_cancel(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return await _proxy_pdf_task(
        request,
        task_id,
        f"/api/cancel/{quote(task_id, safe='')}",
        current_user=current_user,
        session=session,
        method="POST",
    )


@pdf_router.post("/refetch/{task_id}")
async def pdf_task_refetch(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return await _proxy_pdf_task(
        request,
        task_id,
        f"/api/refetch/{quote(task_id, safe='')}",
        current_user=current_user,
        session=session,
        method="POST",
    )


@pdf_router.post("/reparse/{task_id}")
async def pdf_task_reparse(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return await _proxy_pdf_task(
        request,
        task_id,
        f"/api/reparse/{quote(task_id, safe='')}",
        current_user=current_user,
        session=session,
        method="POST",
    )


@pdf_router.get("/artifact/{task_id}/{artifact_name:path}")
async def pdf_task_artifact(
    request: Request,
    task_id: str,
    artifact_name: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return await _proxy_pdf_task(
        request,
        task_id,
        f"/api/artifact/{quote(task_id, safe='')}/{artifact_name}",
        current_user=current_user,
        session=session,
    )


@pdf_router.get("/download/{task_id}")
async def pdf_task_download(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return await _proxy_pdf_task(
        request,
        task_id,
        f"/api/download/{quote(task_id, safe='')}",
        current_user=current_user,
        session=session,
    )


@pdf_router.get("/download_complete/{task_id}")
async def pdf_task_download_complete(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return await _proxy_pdf_task(
        request,
        task_id,
        f"/api/download_complete/{quote(task_id, safe='')}",
        current_user=current_user,
        session=session,
    )


@pdf_router.get("/download_corrected/{task_id}")
async def pdf_task_download_corrected(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return await _proxy_pdf_task(
        request,
        task_id,
        f"/api/download_corrected/{quote(task_id, safe='')}",
        current_user=current_user,
        session=session,
    )


@pdf_router.get("/source/{task_id}/table/{table_index}")
async def pdf_task_source_table(
    request: Request,
    task_id: str,
    table_index: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return await _proxy_pdf_task(
        request,
        task_id,
        f"/api/source/{quote(task_id, safe='')}/table/{table_index}",
        current_user=current_user,
        session=session,
    )


@pdf_router.get("/source/{task_id}/page/{page_number}")
async def pdf_task_source_page(
    request: Request,
    task_id: str,
    page_number: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return await _proxy_pdf_task(
        request,
        task_id,
        f"/api/source/{quote(task_id, safe='')}/page/{page_number}",
        current_user=current_user,
        session=session,
    )


@pdf_router.post("/source/{task_id}/table/{table_index}/correction")
async def pdf_task_source_correction(
    request: Request,
    task_id: str,
    table_index: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return await _proxy_pdf_task(
        request,
        task_id,
        f"/api/source/{quote(task_id, safe='')}/table/{table_index}/correction",
        current_user=current_user,
        session=session,
        method="POST",
    )


@pdf_router.get("/pdf_page/{task_id}/{page_number}")
async def pdf_task_page_image(
    request: Request,
    task_id: str,
    page_number: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return await _proxy_pdf_task(
        request,
        task_id,
        f"/api/pdf_page/{quote(task_id, safe='')}/{page_number}",
        current_user=current_user,
        session=session,
    )


@pdf_router.delete("/tasks/{task_id}")
async def delete_my_pdf_task(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _ensure_pdf_task_access(session, current_user, task_id)
    user_links = session.exec(
        select(UserArtifact).where(
            UserArtifact.user_id == int(current_user.id),
            UserArtifact.artifact_type == "parse",
            (UserArtifact.artifact_key == task_id) | (UserArtifact.global_artifact_id == task_id),
        )
    ).all()
    for item in user_links:
        session.delete(item)
    if user_links:
        session.commit()

    remaining_links = session.exec(_parse_artifact_statement(task_id)).all()
    if remaining_links and not source_proxy._is_admin(current_user):
        return {"success": True, "upstream_deleted": False, "scope": "workspace"}

    response = await source_proxy._proxy_pdf2md(
        request,
        f"/api/tasks/{quote(task_id, safe='')}",
        method="DELETE",
    )
    response.headers["X-SIQ-Workspace-Unlinked"] = "1"
    return response


@router.get("/me")
def workspace_me(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return workspace_summary(current_user=current_user, session=session)
