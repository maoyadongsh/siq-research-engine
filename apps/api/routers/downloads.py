import os
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from services.security_utils import safe_path_join, validate_file_extension
from database import get_session
from services.auth_dependencies import get_current_user
from services.auth_service import User
from services.path_config import REPORT_DOWNLOADS_ROOT
from services.usage_service import UserArtifact

DOWNLOADS_ROOT = REPORT_DOWNLOADS_ROOT

router = APIRouter(prefix="/downloads", tags=["downloads"])


def _safe_relative_path(value: str) -> Path:
    """安全的相对路径解析（使用security_utils增强）"""
    # 使用security_utils的safe_path_join
    full = safe_path_join(DOWNLOADS_ROOT, value)
    # 验证文件扩展名（如果是文件路径）
    if '.' in value:
        validate_file_extension(str(full), {'.pdf'})
    return Path(full)


def _split_download_path(path: Path) -> tuple[str, str]:
    rel = path.relative_to(DOWNLOADS_ROOT)
    parts = rel.parts
    company = parts[0].strip() if parts else ""
    category = parts[1].strip() if len(parts) > 2 else ""
    return company or "未分组", category or "PDF"


def _role_value(user: User) -> str:
    return str(user.role.value if hasattr(user.role, "value") else user.role)


def _is_admin(user: User) -> bool:
    return _role_value(user) in {"admin", "super_admin"}


def _report_payload(path: Path) -> dict:
    rel = path.relative_to(DOWNLOADS_ROOT)
    stat = path.stat()
    company, category = _split_download_path(path)
    rel_text = rel.as_posix()
    return {
        "id": quote(rel_text, safe=""),
        "company": company,
        "category": category,
        "filename": path.name,
        "relativePath": rel_text,
        "size": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "url": f"/api/downloads/report-file?path={quote(rel_text, safe='')}",
    }


def _user_has_download_link(session: Session, user_id: int, relative_path: str) -> bool:
    item = session.exec(
        select(UserArtifact).where(
            UserArtifact.user_id == user_id,
            UserArtifact.artifact_type == "download",
            UserArtifact.artifact_key == relative_path,
        )
    ).first()
    return item is not None


@router.get("/reports")
def list_downloaded_reports(
    q: str = "",
    limit: int = Query(default=80, ge=1, le=300),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not DOWNLOADS_ROOT.is_dir():
        return {"root": str(DOWNLOADS_ROOT), "reports": []}

    query = q.strip().lower()
    reports = []

    if _is_admin(current_user):
        candidate_paths = [path for path in DOWNLOADS_ROOT.rglob("*.pdf") if path.is_file()]
    else:
        links = session.exec(
            select(UserArtifact).where(
                UserArtifact.user_id == int(current_user.id),
                UserArtifact.artifact_type == "download",
            )
        ).all()
        candidate_paths = []
        for item in links:
            try:
                path = _safe_relative_path(item.artifact_key)
            except HTTPException:
                continue
            if path.is_file():
                candidate_paths.append(path)

    for path in candidate_paths:
        if not path.is_file():
            continue
        rel = path.relative_to(DOWNLOADS_ROOT)
        search_text = " ".join(rel.parts).lower()
        if query and query not in search_text:
            continue
        reports.append(_report_payload(path))

    reports.sort(key=lambda item: item["mtime"], reverse=True)
    return {"root": str(DOWNLOADS_ROOT), "reports": reports[:limit]}


@router.get("/report-file")
def open_downloaded_report(
    path: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    safe = _safe_relative_path(path)
    if not safe.is_file() or safe.suffix.lower() != ".pdf":
        raise HTTPException(404, "PDF not found")
    relative_path = safe.relative_to(DOWNLOADS_ROOT).as_posix()
    if not _is_admin(current_user) and not _user_has_download_link(session, int(current_user.id), relative_path):
        raise HTTPException(403, "PDF not in current user's workspace")
    return FileResponse(safe, media_type="application/pdf", filename=safe.name)


@router.delete("/report-file")
def delete_downloaded_report(
    path: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    safe = _safe_relative_path(path)
    if not safe.is_file() or safe.suffix.lower() != ".pdf":
        raise HTTPException(404, "PDF not found")
    size = safe.stat().st_size
    filename = safe.name
    relative_path = safe.relative_to(DOWNLOADS_ROOT).as_posix()
    if not _is_admin(current_user):
        link = session.exec(
            select(UserArtifact).where(
                UserArtifact.user_id == int(current_user.id),
                UserArtifact.artifact_type == "download",
                UserArtifact.artifact_key == relative_path,
            )
        ).first()
        if not link:
            raise HTTPException(403, "PDF not in current user's workspace")
        session.delete(link)
        session.commit()
        return {
            "deleted": False,
            "unlinked": True,
            "filename": filename,
            "relativePath": relative_path,
            "size": size,
        }
    safe.unlink()
    return {
        "deleted": True,
        "filename": filename,
        "relativePath": relative_path,
        "size": size,
    }
