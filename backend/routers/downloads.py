import os
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

DOWNLOADS_ROOT = Path(os.environ.get("REPORT_DOWNLOADS_ROOT", "/home/maoyd/report-finder-service/downloads")).resolve()

router = APIRouter(prefix="/downloads", tags=["downloads"])


def _safe_relative_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise HTTPException(403, "Path traversal denied")
    full = (DOWNLOADS_ROOT / path).resolve()
    if DOWNLOADS_ROOT not in full.parents and full != DOWNLOADS_ROOT:
        raise HTTPException(403, "Path traversal denied")
    return full


def _split_download_path(path: Path) -> tuple[str, str]:
    rel = path.relative_to(DOWNLOADS_ROOT)
    parts = rel.parts
    company = parts[0].strip() if parts else ""
    category = parts[1].strip() if len(parts) > 2 else ""
    return company or "未分组", category or "PDF"


@router.get("/reports")
def list_downloaded_reports(q: str = "", limit: int = Query(default=80, ge=1, le=300)):
    if not DOWNLOADS_ROOT.is_dir():
        return {"root": str(DOWNLOADS_ROOT), "reports": []}

    query = q.strip().lower()
    reports = []
    for path in DOWNLOADS_ROOT.rglob("*.pdf"):
        if not path.is_file():
            continue
        rel = path.relative_to(DOWNLOADS_ROOT)
        search_text = " ".join(rel.parts).lower()
        if query and query not in search_text:
            continue
        stat = path.stat()
        company, category = _split_download_path(path)
        rel_text = rel.as_posix()
        reports.append({
            "id": quote(rel_text, safe=""),
            "company": company,
            "category": category,
            "filename": path.name,
            "relativePath": rel_text,
            "size": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "url": f"/api/downloads/report-file?path={quote(rel_text, safe='')}",
        })

    reports.sort(key=lambda item: item["mtime"], reverse=True)
    return {"root": str(DOWNLOADS_ROOT), "reports": reports[:limit]}


@router.get("/report-file")
def open_downloaded_report(path: str):
    safe = _safe_relative_path(path)
    if not safe.is_file() or safe.suffix.lower() != ".pdf":
        raise HTTPException(404, "PDF not found")
    return FileResponse(safe, media_type="application/pdf", filename=safe.name)


@router.delete("/report-file")
def delete_downloaded_report(path: str):
    safe = _safe_relative_path(path)
    if not safe.is_file() or safe.suffix.lower() != ".pdf":
        raise HTTPException(404, "PDF not found")
    size = safe.stat().st_size
    filename = safe.name
    relative_path = safe.relative_to(DOWNLOADS_ROOT).as_posix()
    safe.unlink()
    return {
        "deleted": True,
        "filename": filename,
        "relativePath": relative_path,
        "size": size,
    }
