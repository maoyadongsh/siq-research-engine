import os
import json
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
OPENABLE_SUFFIXES = {".pdf", ".html", ".htm", ".xhtml", ".xml", ".xbrl", ".txt", ".zip"}

router = APIRouter(prefix="/downloads", tags=["downloads"])


def _safe_relative_path(value: str) -> Path:
    """安全的相对路径解析（使用security_utils增强）"""
    # 使用security_utils的safe_path_join
    full = safe_path_join(DOWNLOADS_ROOT, value)
    # 验证文件扩展名（如果是文件路径）
    if '.' in value:
        validate_file_extension(str(full), OPENABLE_SUFFIXES)
    return Path(full)


def _split_download_path(path: Path) -> tuple[str, str]:
    rel = path.relative_to(DOWNLOADS_ROOT)
    parts = rel.parts
    if len(parts) >= 6 and parts[0] == "EU":
        country = parts[1].strip()
        company = parts[2].strip()
        category = "/".join(part.strip() for part in parts[3:5] if part.strip())
        return company or "未分组", f"{country}/{category}" if country else category
    if len(parts) >= 5 and parts[0] in {"CN", "HK", "US", "KR", "JP"}:
        company = parts[1].strip()
        category = "/".join(part.strip() for part in parts[2:4] if part.strip())
        return company or "未分组", category or parts[0]
    company = parts[0].strip() if parts else ""
    category = parts[1].strip() if len(parts) > 2 else ""
    return company or "未分组", category or "PDF"


def _role_value(user: User) -> str:
    return str(user.role.value if hasattr(user.role, "value") else user.role)


def _is_admin(user: User) -> bool:
    return _role_value(user) in {"admin", "super_admin"}


def _metadata_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".metadata.json")


def _load_metadata(path: Path) -> dict:
    metadata_path = _metadata_path(path)
    if not metadata_path.is_file():
        return {}
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _metadata_content_type(path: Path) -> str | None:
    metadata_path = _metadata_path(path)
    if not metadata_path.is_file():
        return None
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    downloaded = payload.get("downloaded_file") if isinstance(payload, dict) else None
    content_type = downloaded.get("content_type") if isinstance(downloaded, dict) else None
    return str(content_type).split(";", 1)[0].strip().lower() or None


def _sniff_content_type(path: Path) -> str | None:
    try:
        with path.open("rb") as infile:
            head = infile.read(4096).lstrip()
    except OSError:
        return None
    lowered = head.lower()
    if lowered.startswith(b"%pdf-"):
        return "application/pdf"
    if lowered.startswith(b"<!doctype html") or lowered.startswith(b"<html"):
        return "text/html"
    if lowered.startswith(b"<?xml"):
        if b"<html" in lowered or b"xmlns:ix" in lowered or b"ix:header" in lowered:
            return "text/html"
        return "application/xml"
    if lowered.startswith(b"pk\x03\x04"):
        return "application/zip"
    return None


def _content_type_for_path(path: Path) -> str:
    sniffed = _sniff_content_type(path)
    if sniffed:
        return sniffed
    from_metadata = _metadata_content_type(path)
    if from_metadata:
        return from_metadata
    suffix = path.suffix.lower()
    return {
        ".pdf": "application/pdf",
        ".html": "text/html",
        ".htm": "text/html",
        ".xhtml": "text/html",
        ".xml": "application/xml",
        ".xbrl": "application/xml",
        ".txt": "text/plain",
        ".zip": "application/zip",
    }.get(suffix, "application/octet-stream")

def _report_payload(path: Path) -> dict:
    rel = path.relative_to(DOWNLOADS_ROOT)
    stat = path.stat()
    company, category = _split_download_path(path)
    rel_text = rel.as_posix()
    content_type = _content_type_for_path(path)
    metadata = _load_metadata(path)
    candidate = metadata.get("candidate") if isinstance(metadata.get("candidate"), dict) else {}
    downloaded = metadata.get("downloaded_file") if isinstance(metadata.get("downloaded_file"), dict) else {}
    company_name = str(candidate.get("company_name") or company or "未分组").strip() or "未分组"
    ticker = str(candidate.get("ticker") or "").strip() or None
    form = str(candidate.get("form") or "").strip() or None
    report_type = str(candidate.get("report_type") or "").strip() or None
    report_family = str(candidate.get("report_family") or "").strip() or None
    report_end = str(candidate.get("report_end") or candidate.get("period_end") or "").strip() or None
    published_at = str(candidate.get("published_at") or candidate.get("filing_date") or "").strip() or None
    return {
        "id": quote(rel_text, safe=""),
        "company": company_name,
        "companyName": company_name,
        "ticker": ticker,
        "category": category,
        "form": form,
        "reportType": report_type,
        "reportFamily": report_family,
        "reportEnd": report_end,
        "publishedAt": published_at,
        "accessionNumber": str(candidate.get("accession_number") or "").strip() or None,
        "sourceId": str(candidate.get("source_id") or "").strip() or None,
        "filename": path.name,
        "relativePath": rel_text,
        "size": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "contentType": content_type,
        "isPdf": content_type == "application/pdf",
        "url": f"/api/downloads/report-file?path={quote(rel_text, safe='')}",
        "metadataPath": metadata_path.as_posix() if (metadata_path := _metadata_path(path)).is_file() else None,
        "downloadedFile": downloaded or None,
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
    market: str = "",
    limit: int = Query(default=80, ge=1, le=300),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not DOWNLOADS_ROOT.is_dir():
        return {"root": str(DOWNLOADS_ROOT), "reports": []}

    query = q.strip().lower()
    market_filter = market.strip().upper()
    if market_filter and market_filter not in {"CN", "HK", "US", "EU", "KR", "JP"}:
        raise HTTPException(400, "Unsupported market")
    reports = []

    system_downloads_visible = os.environ.get("SIQ_DOWNLOAD_LIST_WORKSPACE_ONLY", "").strip().lower() not in {"1", "true", "yes"}
    if _is_admin(current_user):
        candidate_paths = [
            path
            for path in DOWNLOADS_ROOT.rglob("*")
            if path.is_file() and path.suffix.lower() in OPENABLE_SUFFIXES
        ]
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
        rel_text = rel.as_posix()
        if market_filter and (not rel.parts or rel.parts[0].upper() != market_filter):
            continue
        search_text = " ".join(rel.parts).lower()
        if query and query not in search_text:
            continue
        payload = _report_payload(path)
        payload["accessible"] = _is_admin(current_user) or _user_has_download_link(session, int(current_user.id), rel_text)
        if system_downloads_visible or payload["accessible"]:
            reports.append(payload)

    reports.sort(key=lambda item: item["mtime"], reverse=True)
    return {"root": str(DOWNLOADS_ROOT), "reports": reports[:limit]}


@router.get("/report-file")
def open_downloaded_report(
    path: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    safe = _safe_relative_path(path)
    if not safe.is_file() or safe.suffix.lower() not in OPENABLE_SUFFIXES:
        raise HTTPException(404, "Downloaded file not found")
    relative_path = safe.relative_to(DOWNLOADS_ROOT).as_posix()
    if not _is_admin(current_user) and not _user_has_download_link(session, int(current_user.id), relative_path):
        raise HTTPException(403, "File not in current user's workspace")
    disposition = f"inline; filename*=UTF-8''{quote(safe.name)}"
    return FileResponse(safe, media_type=_content_type_for_path(safe), headers={"Content-Disposition": disposition})


@router.delete("/report-file")
def delete_downloaded_report(
    path: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    safe = _safe_relative_path(path)
    if not safe.is_file() or safe.suffix.lower() not in OPENABLE_SUFFIXES:
        raise HTTPException(404, "Downloaded file not found")
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
