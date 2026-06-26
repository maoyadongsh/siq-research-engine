"""Wiki file serving router."""
import json
import os
import glob
import time
from pathlib import Path
from datetime import datetime
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from services.auth_dependencies import get_current_user
from services.auth_service import User
from services.permissions import require_admin, require_user_permission
from services.path_config import WIKI_ROOT as CONFIG_WIKI_ROOT
from services.security_utils import safe_path_join, validate_company_dir, validate_file_extension

WIKI_ROOT = str(CONFIG_WIKI_ROOT)
WIKI_ROOT_PATH = CONFIG_WIKI_ROOT.resolve()
COMPANIES_DIR = os.path.join(WIKI_ROOT, "companies")
ALLOWED_EXT = {".html", ".json", ".md", ".csv", ".txt", ".png", ".jpg", ".jpeg", ".svg"}
LIST_CACHE_TTL_SECONDS = 5.0
RECENT_CACHE_TTL_SECONDS = 5.0
_companies_list_cache: tuple[float, dict] | None = None
_recent_results_cache: dict[int, tuple[float, dict]] = {}

router = APIRouter(prefix="/wiki", tags=["wiki"])

RESULT_TYPES = {
    "analysis": {
        "dir": "analysis",
        "label": "报告",
        "route": "/analysis",
        "url_part": "analysis",
    },
    "factcheck": {
        "dir": "factcheck",
        "label": "事实检验",
        "route": "/verify",
        "url_part": "factcheck",
    },
    "tracking": {
        "dir": "tracking",
        "label": "跟踪",
        "route": "/tracking",
        "url_part": "tracking",
    },
    "legal": {
        "dir": "legal",
        "label": "法务合规",
        "route": "/legal",
        "url_part": "legal",
    },
}


def _safe_path(requested: str) -> str:
    """安全的路径解析（使用security_utils增强）"""
    # 使用security_utils的safe_path_join
    return str(safe_path_join(WIKI_ROOT_PATH, requested))


def _split_company_dir(entry: str) -> tuple[str, str]:
    parts = entry.split("-", 1)
    code = parts[0] if parts else ""
    name = parts[1] if len(parts) > 1 else entry
    return code, name


def _clean_company_name(name: str) -> str:
    for marker in ("_CN_", "_SZSE_", "_SSE_"):
        if marker in name:
            return name.split(marker, 1)[0]
    return name


def _read_company_meta(company_path: str) -> dict:
    meta_path = os.path.join(company_path, "company.json")
    if not os.path.isfile(meta_path):
        return {}

    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _company_identity(entry: str, company_path: str) -> tuple[str, str]:
    code, name = _split_company_dir(entry)
    meta = _read_company_meta(company_path)
    meta_code = str(meta.get("stock_code") or "").strip()
    meta_name = str(meta.get("company_short_name") or meta.get("company_full_name") or "").strip()
    return meta_code or code, _clean_company_name(meta_name or name)


def _html_files(result_dir: str) -> list[str]:
    if not os.path.isdir(result_dir):
        return []
    return glob.glob(os.path.join(result_dir, "*.html"))


def _source_report_count(company_path: str) -> int:
    reports_dir = os.path.join(company_path, "reports")
    if not os.path.isdir(reports_dir):
        return 0

    count = 0
    for entry in os.listdir(reports_dir):
        report_dir = os.path.join(reports_dir, entry)
        if os.path.isdir(report_dir) and os.path.isfile(os.path.join(report_dir, "report.md")):
            count += 1
    return count


def _latest_mtime(paths: list[str]) -> float:
    latest = 0.0
    for path in paths:
      if os.path.isfile(path):
          latest = max(latest, os.stat(path).st_mtime)
          continue

      if not os.path.isdir(path):
          continue

      for root, dirs, files in os.walk(path):
          dirs[:] = [
              d for d in dirs
              if not d.startswith(".") and d not in {"images", "__pycache__"}
          ]
          for filename in files:
              if filename.startswith("."):
                  continue
              fp = os.path.join(root, filename)
              try:
                  latest = max(latest, os.stat(fp).st_mtime)
              except OSError:
                  continue
    return latest


def _wiki_result_url(company_dir: str, url_part: str, filename: str) -> str:
    return (
        f"/api/wiki/companies/{quote(company_dir, safe='')}/"
        f"{url_part}/{quote(filename, safe='')}"
    )


def _no_cache_file_response(path: str) -> FileResponse:
    response = FileResponse(path)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def _search_alias_text(text: str) -> str:
    aliases = {
        "赛力斯": ["塞力斯"],
    }
    expanded = [text]
    for canonical, alternatives in aliases.items():
        if canonical in text:
            expanded.extend(alternatives)
        if any(alias in text for alias in alternatives):
            expanded.append(canonical)
    return " ".join(expanded)


def _page_result_url(route: str, company_dir: str, filename: str) -> str:
    return (
        f"{route}?company={quote(company_dir, safe='')}"
        f"&result={quote(filename, safe='')}"
    )


@router.get("/companies/list")
def list_companies(current_user: User = Depends(get_current_user)):
    require_user_permission(current_user, "company.view")
    global _companies_list_cache
    now = time.monotonic()
    if _companies_list_cache and now - _companies_list_cache[0] <= LIST_CACHE_TTL_SECONDS:
        return _companies_list_cache[1]

    if not os.path.isdir(COMPANIES_DIR):
        return {"companies": []}
    result = []
    for entry in sorted(os.listdir(COMPANIES_DIR)):
        full = os.path.join(COMPANIES_DIR, entry)
        if not os.path.isdir(full):
            continue
        code, name = _company_identity(entry, full)
        analysis_dir = os.path.join(full, "analysis")
        factcheck_dir = os.path.join(full, "factcheck")
        tracking_dir = os.path.join(full, "tracking")
        legal_dir = os.path.join(full, "legal")
        htmls = _html_files(analysis_dir)
        fc_htmls = _html_files(factcheck_dir)
        tr_htmls = _html_files(tracking_dir)
        legal_htmls = _html_files(legal_dir)
        latest_result_mtime = _latest_mtime(htmls + fc_htmls + tr_htmls + legal_htmls)
        latest_wiki_mtime = _latest_mtime([
            os.path.join(full, "company.json"),
            os.path.join(full, "analysis"),
            os.path.join(full, "factcheck"),
            os.path.join(full, "tracking"),
            os.path.join(full, "legal"),
            os.path.join(full, "reports"),
            os.path.join(full, "metrics"),
            os.path.join(full, "semantic"),
            os.path.join(full, "evidence"),
            os.path.join(full, "graph"),
        ])
        result.append({
            "code": code,
            "name": name,
            "dir": entry,
            "hasReport": len(htmls) > 0,
            "reportCount": len(htmls),
            "hasFactcheck": len(fc_htmls) > 0,
            "factcheckCount": len(fc_htmls),
            "hasTracking": len(tr_htmls) > 0,
            "trackingCount": len(tr_htmls),
            "hasLegal": len(legal_htmls) > 0,
            "legalCount": len(legal_htmls),
            "sourceReportCount": _source_report_count(full),
            "latestResultAt": datetime.fromtimestamp(latest_result_mtime).isoformat() if latest_result_mtime else None,
            "latestWikiAt": datetime.fromtimestamp(latest_wiki_mtime).isoformat() if latest_wiki_mtime else None,
        })
    payload = {"companies": result}
    _companies_list_cache = (now, payload)
    return payload


@router.get("/companies/recent-results")
def list_recent_results(
    limit: int = 8,
    current_user: User = Depends(get_current_user),
):
    require_admin(current_user)
    safe_limit = max(1, min(limit, 50))
    now = time.monotonic()
    cached = _recent_results_cache.get(safe_limit)
    if cached and now - cached[0] <= RECENT_CACHE_TTL_SECONDS:
        return cached[1]

    results = _iter_generated_results()
    results.sort(key=lambda item: item["mtimeTs"], reverse=True)
    for item in results:
        item.pop("mtimeTs", None)

    payload = {"results": results[:safe_limit]}
    _recent_results_cache[safe_limit] = (now, payload)
    return payload



def _iter_generated_results():
    if not os.path.isdir(COMPANIES_DIR):
        return []

    results = []
    for entry in sorted(os.listdir(COMPANIES_DIR)):
        company_path = os.path.join(COMPANIES_DIR, entry)
        if not os.path.isdir(company_path):
            continue

        code, name = _company_identity(entry, company_path)
        for result_type, config in RESULT_TYPES.items():
            result_dir = os.path.join(company_path, config["dir"])
            if not os.path.isdir(result_dir):
                continue

            for filename in os.listdir(result_dir):
                if not filename.endswith(".html"):
                    continue

                if result_type == "analysis" and "factcheck" in filename.lower():
                    continue

                fp = os.path.join(result_dir, filename)
                st = os.stat(fp)
                results.append({
                    "id": f"{entry}:{result_type}:{filename}",
                    "type": result_type,
                    "typeLabel": config["label"],
                    "code": code,
                    "name": name,
                    "companyDir": entry,
                    "filename": filename,
                    "url": _wiki_result_url(entry, config["url_part"], filename),
                    "pageUrl": _page_result_url(config["route"], entry, filename),
                    "size": st.st_size,
                    "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(),
                    "mtimeTs": st.st_mtime,
                })
    return results


def _result_search_text(item: dict) -> str:
    return _search_alias_text(" ".join([
        item.get("code", ""),
        item.get("name", ""),
        item.get("companyDir", ""),
        item.get("filename", ""),
        item.get("typeLabel", ""),
        item.get("type", ""),
    ])).lower()


@router.get("/reports/search")
def search_generated_reports(
    q: str = "",
    limit: int = 10,
    current_user: User = Depends(get_current_user),
):
    require_user_permission(current_user, "report.view")
    query = q.strip().lower()
    safe_limit = max(1, min(limit, 30))
    results = _iter_generated_results()

    if query:
        terms = [term for term in query.split() if term]
        results = [
            item for item in results
            if all(term in _result_search_text(item) for term in terms)
        ]

    results.sort(key=lambda item: item["mtimeTs"], reverse=True)
    for item in results:
        item.pop("mtimeTs", None)

    return {"results": results[:safe_limit]}


@router.get("/companies/{company_dir}/reports")
def list_reports(company_dir: str, current_user: User = Depends(get_current_user)):
    require_user_permission(current_user, "report.view")
    company_dir = validate_company_dir(company_dir)
    safe = _safe_path(os.path.join("companies", company_dir))
    analysis_dir = os.path.join(safe, "analysis")
    if not os.path.isdir(analysis_dir):
        return {"reports": []}
    reports = []
    for f in os.listdir(analysis_dir):
        if not f.endswith(".html"):
            continue
        fp = os.path.join(analysis_dir, f)
        st = os.stat(fp)
        reports.append({
            "filename": f,
            "url": f"/api/wiki/companies/{company_dir}/analysis/{f}",
            "size": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(),
        })
    reports.sort(key=lambda item: item["mtime"], reverse=True)
    return {"reports": reports}


@router.get("/companies/{company_dir}/factchecks")
def list_factchecks(company_dir: str, current_user: User = Depends(get_current_user)):
    require_user_permission(current_user, "report.view")
    company_dir = validate_company_dir(company_dir)
    safe = _safe_path(os.path.join("companies", company_dir))
    factcheck_dir = os.path.join(safe, "factcheck")
    if not os.path.isdir(factcheck_dir):
        return {"factchecks": []}
    factchecks = []
    for f in sorted(os.listdir(factcheck_dir)):
        if not f.endswith(".html"):
            continue
        fp = os.path.join(factcheck_dir, f)
        st = os.stat(fp)
        factchecks.append({
            "filename": f,
            "url": f"/api/wiki/companies/{company_dir}/factcheck/{f}",
            "size": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(),
        })
    return {"factchecks": factchecks}


@router.get("/companies/{company_dir}/trackings")
def list_trackings(company_dir: str, current_user: User = Depends(get_current_user)):
    require_user_permission(current_user, "report.view")
    company_dir = validate_company_dir(company_dir)
    safe = _safe_path(os.path.join("companies", company_dir))
    tracking_dir = os.path.join(safe, "tracking")
    if not os.path.isdir(tracking_dir):
        return {"trackings": []}
    trackings = []
    for f in sorted(os.listdir(tracking_dir), reverse=True):
        if not f.endswith(".html"):
            continue
        fp = os.path.join(tracking_dir, f)
        st = os.stat(fp)
        trackings.append({
            "filename": f,
            "url": f"/api/wiki/companies/{company_dir}/tracking/{f}",
            "size": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(),
        })
    return {"trackings": trackings}


@router.get("/companies/{company_dir}/legals")
def list_legals(company_dir: str, current_user: User = Depends(get_current_user)):
    require_user_permission(current_user, "report.view")
    company_dir = validate_company_dir(company_dir)
    safe = _safe_path(os.path.join("companies", company_dir))
    legal_dir = os.path.join(safe, "legal")
    if not os.path.isdir(legal_dir):
        return {"legals": []}
    legals = []
    for f in sorted(os.listdir(legal_dir), reverse=True):
        if not f.endswith(".html"):
            continue
        fp = os.path.join(legal_dir, f)
        st = os.stat(fp)
        legals.append({
            "filename": f,
            "url": f"/api/wiki/companies/{company_dir}/legal/{f}",
            "size": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(),
        })
    return {"legals": legals}


@router.delete("/companies/{company_dir}/{result_type}/{filename}", dependencies=[Depends(get_current_user)])
def delete_generated_report(
    company_dir: str,
    result_type: str,
    filename: str,
    current_user: User = Depends(get_current_user),
):
    require_user_permission(current_user, "report.delete")
    company_dir = validate_company_dir(company_dir)
    allowed_dirs = {config["url_part"]: config["dir"] for config in RESULT_TYPES.values()}
    if result_type not in allowed_dirs:
        raise HTTPException(404, "Report type not found")
    if not filename.endswith(".html"):
        raise HTTPException(403, "Only generated HTML reports can be deleted")

    safe = _safe_path(os.path.join("companies", company_dir, allowed_dirs[result_type], filename))
    if not os.path.isfile(safe):
        raise HTTPException(404, "Report not found")
    os.remove(safe)
    return {
        "deleted": True,
        "companyDir": company_dir,
        "type": result_type,
        "filename": filename,
    }


@router.get("/companies/{path:path}")
def serve_file(path: str, current_user: User = Depends(get_current_user)):
    require_user_permission(current_user, "report.view")
    safe = _safe_path(os.path.join("companies", path))
    if not os.path.isfile(safe):
        raise HTTPException(404, "File not found")
    _, ext = os.path.splitext(safe)
    if ext.lower() not in ALLOWED_EXT:
        raise HTTPException(403, f"File type {ext} not allowed")
    return _no_cache_file_response(safe)
