import json
import hashlib
import re
import subprocess
import uuid
import sys
import tempfile
from urllib.parse import urlencode
from datetime import datetime, timezone
from typing import Any
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from sqlmodel.ext.asyncio.session import AsyncSession

from database import get_async_session
from routers.workspace import record_user_artifact_async
from services.auth_dependencies import get_current_user
from services.auth_service import User
from services.auth_dependencies import require_permission
from services.hermes_client import collect_run_result, create_run
from services.command_runner import format_command, run_command
from services.job_service import market_report_job_service
from services.llm_settings import load_llm_settings
from services.hermes_model_control import infer_model_mode, set_all_profile_model_modes
from services import market_report_commands
from services import market_report_status_service
from services.market_report_settings import (
    EU_ESEF_PACKAGE_BUILD_SCRIPT,
    MARKET_BUILD_SCRIPTS,
    MARKET_INGESTION_EVAL_MARKDOWN_PATH,
    MARKET_INGESTION_EVAL_REPORT_PATH,
    MARKET_INGESTION_EVAL_SCRIPT,
    MARKET_IMPORT_SCRIPTS,
    MARKET_REPORT_ASSIST_TIMEOUT,
    MARKET_REPORT_PROXY_TIMEOUT,
    MARKET_RULES_BASE,
    MARKET_VECTOR_INGEST_SCRIPT,
    MARKET_WIKI_ROOTS,
    REPORT_FINDER_BASE,
    US_SEC_CASE_SET_PATH,
    US_SEC_INGEST_REPORT_PATH,
    US_SEC_INGEST_SCRIPT,
    US_SEC_PACKAGE_BUILD_SCRIPT,
    US_SEC_WIKI_ROOT,
)
from services.path_config import REPO_ROOT, REPORT_DOWNLOADS_ROOT
from services import market_package_repository as market_packages


router = APIRouter(tags=["market-reports"])
US_SEC_UPLOAD_SUFFIXES = {".pdf", ".html", ".htm", ".xhtml", ".xml", ".xbrl", ".zip"}


def _content_type(headers: httpx.Headers) -> str:
    return headers.get("content-type") or "application/octet-stream"


def _json_response(payload: dict[str, Any], status_code: int = 200) -> Response:
    return Response(
        content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        status_code=status_code,
        media_type="application/json",
    )


def _command_for_display(args: list[str]) -> str:
    return format_command(args)


def _job_created_by(user: User | None) -> dict[str, Any] | None:
    if user is None:
        return None
    return {
        "id": getattr(user, "id", None),
        "username": getattr(user, "username", None),
        "email": getattr(user, "email", None),
        "full_name": getattr(user, "full_name", None),
        "role": getattr(user, "role", None),
    }


def _read_json_file(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _safe_under(root: Path, path: Path) -> Path:
    root_resolved = root.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Path is outside the allowed evidence package root") from exc
    return resolved


def _market_code(value: str | None) -> str:
    market = str(value or "").upper()
    if market not in MARKET_WIKI_ROOTS:
        raise HTTPException(status_code=400, detail="market must be one of US/HK/JP/KR/EU")
    return market


def _safe_market_package_path(market: str, value: str | None) -> Path:
    if not value:
        raise HTTPException(status_code=400, detail="package_path is required")
    path = Path(value)
    if not path.is_absolute():
        path = REPO_ROOT / path
    package_dir = _safe_under(MARKET_WIKI_ROOTS[market], path)
    if not (package_dir / "manifest.json").is_file():
        raise HTTPException(status_code=404, detail="Market evidence package not found")
    return package_dir


def _safe_download_path(value: str | None) -> Path:
    if not value:
        raise HTTPException(status_code=400, detail="download_relative_path is required")
    relative = Path(str(value))
    if relative.is_absolute() or ".." in relative.parts:
        raise HTTPException(status_code=400, detail="Invalid download_relative_path")
    root = REPORT_DOWNLOADS_ROOT.resolve()
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="download_relative_path is outside downloads root") from exc
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="download_relative_path not found")
    return resolved


def _adjacent_metadata_path(path: Path) -> Path | None:
    metadata = path.with_suffix(path.suffix + ".metadata.json")
    return metadata if metadata.is_file() else None


def _rel_or_abs(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _market_package_paths(package_dir: Path) -> dict[str, str]:
    return market_packages.market_package_paths(package_dir)


def _iter_market_packages(market: str) -> list[Path]:
    return market_packages.iter_market_packages(market, MARKET_WIKI_ROOTS)


def _read_market_package_summary(package_dir: Path) -> dict[str, Any]:
    return market_packages.read_market_package_summary(package_dir)


def _read_market_package_detail(package_dir: Path) -> dict[str, Any]:
    return market_packages.read_market_package_detail(package_dir)


def _markets_to_search(market: str | None) -> list[str]:
    return market_packages.markets_to_search(market, MARKET_WIKI_ROOTS)


def _find_market_package_by_filing_id(filing_id: str, market: str | None = None) -> tuple[str, Path]:
    return market_packages.find_market_package_by_filing_id(
        filing_id,
        market=market,
        market_wiki_roots=MARKET_WIKI_ROOTS,
    )


def _find_market_evidence(
    evidence_id: str,
    *,
    market: str | None = None,
    package_dir: Path | None = None,
) -> tuple[str, Path, dict[str, Any]]:
    return market_packages.find_market_evidence(
        evidence_id,
        market=market,
        package_dir=package_dir,
        market_wiki_roots=MARKET_WIKI_ROOTS,
    )


def _run_market_package_build(payload: dict[str, Any]) -> dict[str, Any]:
    market = _market_code(payload.get("market"))
    download_relative_path = payload.get("download_relative_path")
    source = payload.get("source_path") or payload.get("pdf_path")
    if download_relative_path:
        source_path = _safe_download_path(str(download_relative_path))
    else:
        source_path = Path(str(source)) if source else Path()
    if not source:
        if not download_relative_path:
            raise HTTPException(status_code=400, detail="source_path or download_relative_path is required")
    elif not source_path.is_absolute():
        source_path = REPO_ROOT / source_path
    if not source_path.is_file():
        raise HTTPException(status_code=404, detail="source_path not found")
    script = _market_build_script(market, source_path)
    if not script.is_file():
        raise HTTPException(status_code=404, detail=f"Missing package build script: {script}")
    metadata = payload.get("metadata_path")
    meta_path: Path | None = None
    if metadata:
        meta_path = Path(str(metadata))
        meta_path = meta_path if meta_path.is_absolute() else REPO_ROOT / meta_path
        if not meta_path.is_file():
            raise HTTPException(status_code=404, detail="metadata_path not found")
    else:
        meta_path = _adjacent_metadata_path(source_path)
    parser_result = payload.get("parser_result")
    if _market_build_requires_parser_result(market, source_path) and not parser_result:
        raise HTTPException(status_code=400, detail=f"parser_result is required for {market} package builds")
    parser_path: Path | None = None
    if parser_result and market in {"HK", "JP", "KR", "EU"} and _market_build_accepts_parser_result(market, script):
        parser_path = Path(str(parser_result))
        parser_path = parser_path if parser_path.is_absolute() else REPO_ROOT / parser_path
        if not parser_path.exists():
            raise HTTPException(status_code=404, detail="parser_result not found")
    args = market_report_commands.market_package_build_args(
        executable=sys.executable,
        script=script,
        source_path=source_path,
        output_root=MARKET_WIKI_ROOTS[market],
        metadata_path=meta_path,
        parser_result_path=parser_path,
        force=bool(payload.get("force")),
    )
    completed = run_command(args, cwd=REPO_ROOT, timeout=900)
    if completed.returncode != 0:
        return {"ok": False, "returncode": completed.returncode, "stdout": completed.stdout[-4000:], "stderr": completed.stderr[-4000:], "command": _command_for_display(args)}
    output_lines = (completed.stdout or "").strip().splitlines()
    if not output_lines:
        return {"ok": False, "returncode": completed.returncode, "stdout": completed.stdout[-4000:], "stderr": "Package build did not print a package path", "command": _command_for_display(args)}
    package_path = Path(output_lines[-1])
    detail = _read_package_detail(package_path) if market == "US" else _read_market_package_detail(package_path)
    return {"ok": True, "package": detail, "stdout": completed.stdout[-4000:], "stderr": completed.stderr[-4000:], "command": _command_for_display(args)}


def _market_build_script(market: str, source_path: Path) -> Path:
    if market == "EU" and source_path.suffix.lower() in {".zip", ".xhtml", ".html", ".htm", ".xml", ".xbrl"}:
        return EU_ESEF_PACKAGE_BUILD_SCRIPT
    return MARKET_BUILD_SCRIPTS[market]


def _market_build_requires_parser_result(market: str, source_path: Path) -> bool:
    if market == "EU":
        return _market_build_script(market, source_path) == MARKET_BUILD_SCRIPTS[market]
    return market == "HK"


def _market_build_accepts_parser_result(market: str, script: Path) -> bool:
    if market == "EU" and script == EU_ESEF_PACKAGE_BUILD_SCRIPT:
        return False
    return market in {"HK", "JP", "KR", "EU"}


def _run_market_package_import(payload: dict[str, Any]) -> dict[str, Any]:
    market = _market_code(payload.get("market"))
    package_dir = _safe_market_package_path(market, str(payload.get("package_path") or ""))
    script = MARKET_IMPORT_SCRIPTS[market]
    if not script.is_file():
        raise HTTPException(status_code=404, detail=f"Missing package import script: {script}")
    args = market_report_commands.market_package_import_args(
        executable=sys.executable,
        script=script,
        market=market,
        package_dir=package_dir,
        payload=payload,
    )
    completed = run_command(args, cwd=REPO_ROOT, timeout=900)
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "parse_run_id": completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() and completed.returncode == 0 else None,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
        "command": _command_for_display(args),
    }


def _run_market_vector_ingest(payload: dict[str, Any]) -> dict[str, Any]:
    market = _market_code(payload.get("market"))
    package_dir = _safe_market_package_path(market, str(payload.get("package_path") or ""))
    if not MARKET_VECTOR_INGEST_SCRIPT.is_file():
        raise HTTPException(status_code=404, detail=f"Missing vector ingest script: {MARKET_VECTOR_INGEST_SCRIPT}")
    args, dry_run = market_report_commands.market_vector_ingest_args(
        executable=sys.executable,
        script=MARKET_VECTOR_INGEST_SCRIPT,
        package_dir=package_dir,
        payload=payload,
    )
    completed = run_command(args, cwd=REPO_ROOT, timeout=1800)
    parsed: dict[str, Any] | None = None
    if completed.stdout:
        match = re.search(r"\{.*\}", completed.stdout, flags=re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except Exception:
                parsed = None
    return {
        "ok": completed.returncode == 0,
        "dry_run": dry_run,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-8000:],
        "stderr": completed.stderr[-8000:],
        "summary": parsed,
        "command": _command_for_display(args),
    }


def _run_market_ingestion_eval(payload: dict[str, Any]) -> dict[str, Any]:
    if not MARKET_INGESTION_EVAL_SCRIPT.is_file():
        raise HTTPException(status_code=404, detail=f"Missing eval script: {MARKET_INGESTION_EVAL_SCRIPT}")
    args, output, markdown = market_report_commands.market_ingestion_eval_args(
        executable=sys.executable,
        script=MARKET_INGESTION_EVAL_SCRIPT,
        payload=payload,
        repo_root=REPO_ROOT,
        default_output=MARKET_INGESTION_EVAL_REPORT_PATH,
        default_markdown=MARKET_INGESTION_EVAL_MARKDOWN_PATH,
    )
    completed = run_command(args, cwd=REPO_ROOT, timeout=900)
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-8000:],
        "stderr": completed.stderr[-8000:],
        "report": _read_json_file(output, {}),
        "markdown_path": _rel_or_abs(markdown),
        "command": _command_for_display(args),
    }


def _safe_package_path(value: str | None) -> Path:
    if not value:
        raise HTTPException(status_code=400, detail="package_path is required")
    path = Path(value)
    if not path.is_absolute():
        path = REPO_ROOT / path
    package_dir = _safe_under(US_SEC_WIKI_ROOT, path)
    if not (package_dir / "manifest.json").is_file():
        raise HTTPException(status_code=404, detail="US SEC package not found")
    return package_dir


def _latest_case_item_for_ticker(ticker: str) -> dict[str, Any] | None:
    ticker = ticker.strip().upper()
    case_set = _read_json_file(US_SEC_CASE_SET_PATH, {})
    items = case_set.get("items") if isinstance(case_set, dict) else []
    if not isinstance(items, list):
        return None
    candidates = [item for item in items if isinstance(item, dict) and str(item.get("ticker") or "").upper() == ticker]
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (str(item.get("filing_date") or ""), str(item.get("period_end") or "")), reverse=True)[0]


def _package_from_selector(payload: dict[str, Any]) -> Path:
    if payload.get("package_path"):
        return _safe_package_path(str(payload.get("package_path")))
    ticker = str(payload.get("ticker") or "").strip().upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker or package_path is required")
    item = _latest_case_item_for_ticker(ticker)
    if not item:
        raise HTTPException(status_code=404, detail=f"No package for ticker {ticker}")
    return _safe_package_path(str(item.get("package_path") or ""))


def _read_package_detail(package_dir: Path) -> dict[str, Any]:
    manifest = _read_json_file(package_dir / "manifest.json", {})
    quality = _read_json_file(package_dir / "qa" / "quality_report.json", {})
    financial_checks = _read_json_file(package_dir / "metrics" / "financial_checks.json", {})
    sections = (_read_json_file(package_dir / "sections.json", {}) or {}).get("sections") or []
    tables = (_read_json_file(package_dir / "tables" / "table_index.json", {}) or {}).get("tables") or []
    metrics = (_read_json_file(package_dir / "metrics" / "normalized_metrics.json", {}) or {}).get("metrics") or []
    source_map = (_read_json_file(package_dir / "qa" / "source_map.json", {}) or {}).get("entries") or []
    dimension_metrics = [item for item in metrics if isinstance(item, dict) and item.get("dimensions")]
    checks = financial_checks.get("checks") if isinstance(financial_checks, dict) else []
    if not isinstance(checks, list):
        checks = []
    bridge_checks = [
        check for check in checks
        if isinstance(check, dict) and (
            str(check.get("rule_id") or "").startswith(("bs.", "is.", "cf.", "cross."))
            or str(check.get("rule_name") or "").lower().find("cash") >= 0
        )
    ]
    bridge_summary: dict[str, int] = {}
    for check in bridge_checks:
        status = str(check.get("status") or "unknown")
        bridge_summary[status] = bridge_summary.get(status, 0) + 1
    return {
        "package_path": str(package_dir.relative_to(REPO_ROOT)) if package_dir.is_relative_to(REPO_ROOT) else str(package_dir),
        "manifest": manifest,
        "quality": quality,
        "financial_checks": financial_checks,
        "bridge_checks": {
            "overall_status": financial_checks.get("overall_status") if isinstance(financial_checks, dict) else None,
            "summary": bridge_summary,
            "checks": bridge_checks[:120],
        },
        "counts": {
            "sections": len(sections),
            "tables": len(tables),
            "metrics": len(metrics),
            "evidence": len(source_map),
            "dimension_metrics": len(dimension_metrics),
        },
        "sections": sections,
        "tables": tables[:200],
        "metrics": metrics[:300],
        "dimension_metrics": dimension_metrics[:80],
        "preview": {
            "raw_html": "raw/filing.htm" if (package_dir / "raw" / "filing.htm").is_file() else "",
            "default_markdown": f"sections/{sections[0].get('file')}" if sections else "",
        },
    }


def _media_type_for_file(path: Path) -> str:
    return {
        ".htm": "text/html; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".xhtml": "text/html; charset=utf-8",
        ".md": "text/markdown; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".txt": "text/plain; charset=utf-8",
        ".xml": "application/xml; charset=utf-8",
        ".xbrl": "application/xml; charset=utf-8",
        ".zip": "application/zip",
    }.get(path.suffix.lower(), "application/octet-stream")


def _safe_filename_part(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r'[\\/:*?"<>|\s]+', "-", text)
    text = re.sub(r"-{2,}", "-", text).strip(".-_")
    return text or "unknown"


def _file_suffix_from_content_type(content_type: str | None) -> str:
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    mapping = {
        "application/pdf": ".pdf",
        "text/html": ".html",
        "application/xhtml+xml": ".html",
        "application/xml": ".xml",
        "text/xml": ".xml",
        "text/plain": ".txt",
        "application/zip": ".zip",
        "application/x-zip-compressed": ".zip",
    }
    return mapping.get(normalized, "")


def _us_sec_upload_dir() -> Path:
    target = REPORT_DOWNLOADS_ROOT / "US"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _us_sec_upload_metadata(
    file_path: Path,
    *,
    original_name: str,
    content_type: str | None,
    digest: str,
    size_bytes: int,
    ticker: str | None,
    company_name: str | None,
    report_type: str | None,
    report_family: str | None,
    fiscal_year: int | None,
    period_end: str | None,
    filing_date: str | None,
) -> Path:
    metadata_path = file_path.with_suffix(file_path.suffix + ".metadata.json")
    effective_report_type = (report_type or file_path.suffix.lower().lstrip(".") or "file").strip()
    effective_form = effective_report_type.upper() if effective_report_type not in {"file", ""} else "FILE"
    candidate = {
        "source_id": "manual_upload",
        "source_name": "US SEC Manual Upload",
        "source_domain": "local",
        "market": "US",
        "company_id": "manual",
        "ticker": ticker,
        "company_name": company_name or file_path.parent.parent.parent.name or "Manual Upload",
        "report_type": report_family or effective_report_type,
        "report_family": report_family or "current",
        "form": effective_form,
        "title": original_name,
        "accession_number": "manual-upload",
        "primary_document": original_name,
        "report_end": period_end,
        "published_at": filing_date or datetime.now(timezone.utc).date().isoformat(),
        "accepted_at": None,
        "document_url": f"file://{file_path.resolve()}",
        "landing_url": f"file://{file_path.resolve()}",
        "file_format": file_path.suffix.lower().lstrip(".") or "bin",
        "language": None,
        "inline_xbrl": None,
        "metadata": {
            "uploaded_filename": original_name,
            "content_type": content_type,
        },
    }
    payload = {
        "candidate": candidate,
        "downloaded_file": {
            "file_name": file_path.name,
            "saved_path": str(file_path.resolve()),
            "size_bytes": size_bytes,
            "content_type": content_type,
            "content_sha256": digest,
        },
    }
    metadata_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata_path


def _persist_us_sec_upload(
    file: UploadFile,
    *,
    ticker: str | None,
    company_name: str | None,
    report_type: str | None,
    fiscal_year: int | None,
    period_end: str | None,
    filing_date: str | None,
) -> dict[str, Any]:
    upload_dir = _us_sec_upload_dir()
    raw_name = file.filename or "upload"
    suffix = Path(raw_name).suffix.lower()
    if suffix not in US_SEC_UPLOAD_SUFFIXES:
        raise HTTPException(status_code=400, detail="Only PDF, HTML, XHTML, XML, XBRL and ZIP uploads are supported")

    content = file.file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    digest = hashlib.sha256(content).hexdigest()
    ticker_part = _safe_filename_part(ticker or "manual")
    company_part = _safe_filename_part(company_name or Path(raw_name).stem)
    report_type_text = str(report_type or "file").strip()
    report_part = _safe_filename_part(report_type_text)
    stamp_part = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest_part = digest[:10]
    effective_suffix = _file_suffix_from_content_type(file.content_type) or suffix or ".bin"
    if report_type_text.lower() in {"10-k", "20-f", "annual", "annual-report"}:
        folder = "年报"
        report_family = "annual"
    else:
        folder = "财报"
        report_family = "quarterly"
    year_text = str(fiscal_year or (period_end[:4] if period_end else "") or (filing_date[:4] if filing_date else "") or datetime.now(timezone.utc).year)
    target_dir = upload_dir / company_part / year_text / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    target_name = f"{company_part}_US_{ticker_part}_{report_part}_{stamp_part}_{digest_part}{effective_suffix}"
    target_path = target_dir / target_name
    if target_path.exists():
        raise HTTPException(status_code=409, detail="A file with the same generated name already exists")
    target_path.write_bytes(content)
    metadata_path = _us_sec_upload_metadata(
        target_path,
        original_name=raw_name,
        content_type=file.content_type or _media_type_for_file(target_path),
        digest=digest,
        size_bytes=len(content),
        ticker=ticker,
        company_name=company_name,
        report_type=report_type_text,
        report_family=report_family,
        fiscal_year=int(year_text) if str(year_text).isdigit() else None,
        period_end=period_end,
        filing_date=filing_date,
    )
    return {
        "file_name": target_path.name,
        "saved_path": str(target_path.resolve()),
        "size_bytes": len(content),
        "content_type": file.content_type or _media_type_for_file(target_path),
        "cache_hit": False,
        "deduplicated": False,
        "content_sha256": digest,
        "metadata_path": str(metadata_path.resolve()),
        "relative_path": str(target_path.relative_to(REPORT_DOWNLOADS_ROOT)),
    }


@router.post("/us-sec/uploads")
async def us_sec_upload_files(
    request: Request,
    files: list[UploadFile] = File(...),
    ticker: str = Form(""),
    company_name: str = Form(""),
    report_type: str = Form(""),
    fiscal_year: str = Form(""),
    period_end: str = Form(""),
    filing_date: str = Form(""),
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    del request
    if not files:
        raise HTTPException(status_code=400, detail="请上传文件")
    uploaded: list[dict[str, Any]] = []
    for item in files:
        result = _persist_us_sec_upload(
            item,
            ticker=ticker.strip().upper() or None,
            company_name=company_name.strip() or None,
            report_type=report_type.strip() or None,
            fiscal_year=int(fiscal_year) if str(fiscal_year).strip().isdigit() else None,
            period_end=period_end.strip() or None,
            filing_date=filing_date.strip() or None,
        )
        uploaded.append(result)
        try:
            # best effort: keep uploads visible in personal workspace
            await record_user_artifact_async(
                async_session,
                user_id=int(current_user.id),
                artifact_type="download",
                artifact_key=str(result["relative_path"]),
                title=str(item.filename or result["file_name"]),
                path=str(result["relative_path"]),
                source="us-sec-upload",
                global_artifact_id=str(result["relative_path"]),
            )
        except Exception:
            pass
    return {"ok": True, "count": len(uploaded), "files": uploaded}


def _safe_ingest_args(payload: dict[str, Any]) -> list[str]:
    tickers = str(payload.get("tickers") or "").strip().upper()
    if tickers:
        if not re.fullmatch(r"[A-Z0-9.,_-]{1,240}", tickers):
            raise HTTPException(status_code=400, detail="Invalid tickers")
    batch_tag = str(payload.get("batch_tag") or "").strip()
    if batch_tag:
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", batch_tag):
            raise HTTPException(status_code=400, detail="Invalid batch_tag")
    return market_report_commands.us_sec_ingest_args(
        executable=sys.executable,
        script=US_SEC_INGEST_SCRIPT,
        case_set_path=US_SEC_CASE_SET_PATH,
        report_path=US_SEC_INGEST_REPORT_PATH,
        payload=payload,
        tickers=tickers,
        batch_tag=batch_tag,
    )


def _run_us_sec_case_set_ingest(payload: dict[str, Any]) -> dict[str, Any]:
    if not US_SEC_INGEST_SCRIPT.is_file():
        raise HTTPException(status_code=404, detail=f"Missing ingest script: {US_SEC_INGEST_SCRIPT}")
    args = _safe_ingest_args(payload)
    try:
        completed = run_command(args, cwd=REPO_ROOT, timeout=1800)
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail=f"US SEC ingest timed out: {exc}") from exc
    report = _read_json_file(US_SEC_INGEST_REPORT_PATH, {})
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "command": " ".join(args),
        "stdout": completed.stdout[-8000:],
        "stderr": completed.stderr[-8000:],
        "report": report,
    }


def _run_us_sec_rebuild_package(ticker: str, payload: dict[str, Any]) -> dict[str, Any]:
    item = _latest_case_item_for_ticker(ticker)
    if not item:
        raise HTTPException(status_code=404, detail=f"No package for ticker {ticker}")
    package_dir = _safe_package_path(str(item.get("package_path") or ""))
    manifest = _read_json_file(package_dir / "manifest.json", {})
    source = package_dir / str(manifest.get("local_source_path") or "raw/filing.htm")
    source = _safe_under(package_dir, source)
    if not source.is_file():
        raise HTTPException(status_code=404, detail="Raw SEC filing source not found in package")
    metadata = package_dir / "raw" / "filing.metadata.json"
    with tempfile.TemporaryDirectory(prefix="siq-sec-rebuild-") as tmp_dir:
        tmp_source = Path(tmp_dir) / "filing.htm"
        tmp_source.write_bytes(source.read_bytes())
        tmp_metadata = None
        if metadata.is_file():
            tmp_metadata = Path(tmp_dir) / "filing.metadata.json"
            tmp_metadata.write_bytes(metadata.read_bytes())
        args = market_report_commands.us_sec_rebuild_package_args(
            executable=sys.executable,
            script=US_SEC_PACKAGE_BUILD_SCRIPT,
            source_path=tmp_source,
            output_root=US_SEC_WIKI_ROOT,
            metadata_path=tmp_metadata,
            force=True,
        )
        try:
            completed = run_command(args, cwd=REPO_ROOT, timeout=900)
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(status_code=504, detail=f"US SEC package rebuild timed out: {exc}") from exc
    if completed.returncode != 0:
        raise HTTPException(status_code=500, detail=(completed.stderr or completed.stdout)[-2000:])
    rebuilt_path = Path((completed.stdout or "").strip().splitlines()[-1])
    detail = _read_package_detail(_safe_package_path(str(rebuilt_path)))
    return {
        "ok": True,
        "ticker": ticker.upper(),
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
        "package": detail,
    }


async def _proxy_request(
    *,
    base_url: str,
    upstream_path: str,
    request: Request,
    timeout: float = MARKET_REPORT_PROXY_TIMEOUT,
) -> Response:
    method = request.method
    params = list(request.query_params.multi_items())
    body = await request.body() if method in {"POST", "PUT", "PATCH", "DELETE"} else None
    headers: dict[str, str] = {}
    content_type = request.headers.get("content-type")
    if content_type:
        headers["content-type"] = content_type
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            upstream = await client.request(
                method,
                f"{base_url}{upstream_path}",
                params=params,
                content=body,
                headers=headers,
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Market report upstream unavailable: {exc}") from exc
    return Response(
        content=b"" if method == "HEAD" else upstream.content,
        status_code=upstream.status_code,
        media_type=_content_type(upstream.headers),
    )


async def _finder_assist(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=MARKET_REPORT_PROXY_TIMEOUT) as client:
            upstream = await client.post(f"{REPORT_FINDER_BASE}/v1/reports/assist", json=payload)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Market report assist upstream unavailable: {exc}") from exc
    if upstream.status_code >= 400:
        raise HTTPException(status_code=upstream.status_code, detail=upstream.text[:1000])
    return upstream.json() if upstream.content else {}


def _active_llm_provider() -> tuple[str, dict[str, Any] | None]:
    settings = load_llm_settings(include_secrets=True)
    providers = settings.get("providers") or {}
    cloud_provider = providers.get("cloud")
    if (
        isinstance(cloud_provider, dict)
        and cloud_provider.get("enabled", True)
        and _hermes_mode_for_provider(cloud_provider) == "minimax"
    ):
        return "cloud", cloud_provider

    active = settings.get("activeProvider") or "local"
    provider = providers.get(active)
    if not isinstance(provider, dict) or not provider.get("enabled", True):
        return active, None
    return str(active), provider


def _extract_json_object(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _compact_assist_candidates(request_payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = request_payload.get("candidates") or []
    return [
        {
            "document_url": item.get("document_url"),
            "title": item.get("title"),
            "report_type": item.get("report_type"),
            "report_end": item.get("report_end"),
            "published_at": item.get("published_at"),
        }
        for item in candidates[:30]
        if isinstance(item, dict)
    ]


def _assist_system_prompt() -> str:
    return (
        "你是财报下载助手。只能解释用户给定的官方候选列表，不要生成或修改下载 URL。"
        "请输出严格 JSON：{\"intent\":{...},\"candidate_explanations\":[...] }。"
        "candidate_explanations 每项必须包含 document_url、title_zh、report_type_zh、period_zh、recommendation、recommended、warnings。"
        "韩语和日语标题要翻译成中文；推荐项必须与年份、报告类型和官方候选匹配。"
        "如果候选像修订版、摘要、非完整报告或标题/报告期不匹配，请写入 warnings。"
    )


def _assist_user_payload(request_payload: dict[str, Any], base_assist: dict[str, Any]) -> dict[str, Any]:
    return {
        "prompt": request_payload.get("prompt"),
        "request": {
            key: request_payload.get(key)
            for key in ("market", "company_name", "ticker", "company_id", "report_year", "report_types")
        },
        "base_assist": base_assist,
        "official_candidates": _compact_assist_candidates(request_payload),
    }


def _hermes_mode_for_provider(provider: dict[str, Any]) -> str | None:
    return infer_model_mode(
        provider_name=str(provider.get("providerName") or ""),
        provider=str(provider.get("provider") or ""),
        model=str(provider.get("model") or ""),
        base_url=str(provider.get("baseUrl") or ""),
    )


async def _openai_compatible_enhance_assist(
    *,
    active: str,
    provider: dict[str, Any],
    request_payload: dict[str, Any],
    base_assist: dict[str, Any],
) -> dict[str, Any] | None:
    base_url = str(provider.get("baseUrl") or "").strip().rstrip("/")
    if not base_url or base_url.startswith("hermes://"):
        return None
    model = str(provider.get("model") or "").strip()
    if not model:
        return None

    system = _assist_system_prompt()
    user = _assist_user_payload(request_payload, base_assist)
    headers = {"Content-Type": "application/json"}
    if provider.get("apiKey"):
        headers["Authorization"] = f"Bearer {provider['apiKey']}"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
        "temperature": min(float(provider.get("temperature", 0.2)), 0.3),
        "max_tokens": min(int(provider.get("maxTokens", 4096)), 4096),
        "stream": False,
    }
    if isinstance(provider.get("chatTemplateKwargs"), dict):
        payload["chat_template_kwargs"] = provider["chatTemplateKwargs"]
    try:
        async with httpx.AsyncClient(timeout=MARKET_REPORT_ASSIST_TIMEOUT) as client:
            resp = await client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return None
    choices = data.get("choices") or []
    if not choices:
        return None
    message = choices[0].get("message") or {}
    parsed = _extract_json_object(str(message.get("content") or choices[0].get("text") or ""))
    if not parsed:
        return None
    parsed["assistant_mode"] = f"llm:{active}:{model}"
    return parsed


async def _hermes_enhance_assist(
    *,
    active: str,
    provider: dict[str, Any],
    request_payload: dict[str, Any],
    base_assist: dict[str, Any],
) -> dict[str, Any] | None:
    base_url = str(provider.get("baseUrl") or "").strip()
    if not base_url.startswith("hermes://"):
        return None
    model = str(provider.get("model") or "").strip()
    mode = _hermes_mode_for_provider(provider)
    if mode:
        try:
            set_all_profile_model_modes(mode)
        except Exception:
            pass

    prompt = "\n".join(
        [
            _assist_system_prompt(),
            "只返回 JSON，不要输出 Markdown 代码块，不要调用工具，不要访问外部网页。",
            "输入如下：",
            json.dumps(_assist_user_payload(request_payload, base_assist), ensure_ascii=False),
        ]
    )
    try:
        run_id = await create_run(
            prompt,
            [],
            profile="siq_assistant",
            session_id=f"market-report-assist-{uuid.uuid4().hex[:12]}",
        )
        text = await collect_run_result(
            run_id,
            profile="siq_assistant",
            timeout=httpx.Timeout(MARKET_REPORT_ASSIST_TIMEOUT, connect=10.0),
        )
    except Exception:
        return None
    parsed = _extract_json_object(text)
    if not parsed:
        return None
    parsed["assistant_mode"] = f"llm:{active}:hermes:{mode or model or base_url.removeprefix('hermes://')}"
    return parsed


async def _llm_enhance_assist(request_payload: dict[str, Any], base_assist: dict[str, Any]) -> dict[str, Any] | None:
    try:
        active, provider = _active_llm_provider()
    except Exception:
        return None
    if not provider:
        return None
    if str(provider.get("baseUrl") or "").strip().startswith("hermes://"):
        return await _hermes_enhance_assist(
            active=active,
            provider=provider,
            request_payload=request_payload,
            base_assist=base_assist,
        )
    return await _openai_compatible_enhance_assist(
        active=active,
        provider=provider,
        request_payload=request_payload,
        base_assist=base_assist,
    )


def _merge_assist(base_assist: dict[str, Any], llm_assist: dict[str, Any] | None) -> dict[str, Any]:
    if not llm_assist:
        base_assist.setdefault("assistant_mode", "rules")
        return base_assist
    merged = dict(base_assist)
    if isinstance(llm_assist.get("intent"), dict):
        base_intent = dict(merged.get("intent") or {})
        base_intent.update({k: v for k, v in llm_assist["intent"].items() if v not in (None, "", [])})
        merged["intent"] = base_intent
    by_url = {
        item.get("document_url"): item
        for item in merged.get("candidate_explanations", [])
        if isinstance(item, dict) and item.get("document_url")
    }
    for item in llm_assist.get("candidate_explanations") or []:
        if not isinstance(item, dict) or not item.get("document_url"):
            continue
        original = by_url.get(item["document_url"], {})
        original.update({k: v for k, v in item.items() if k != "document_url" and v not in (None, "", [])})
        original["document_url"] = item["document_url"]
        by_url[item["document_url"]] = original
    if by_url:
        ordered_urls = [
            item.get("document_url")
            for item in merged.get("candidate_explanations", [])
            if isinstance(item, dict)
        ]
        merged["candidate_explanations"] = [by_url[url] for url in ordered_urls if url in by_url]
    merged["assistant_mode"] = llm_assist.get("assistant_mode") or "llm"
    return merged


@router.post("/v1/reports/assist")
async def assist_market_reports(request: Request) -> Response:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object payload is required")
    base_assist = await _finder_assist(payload)
    llm_assist = await _llm_enhance_assist(payload, base_assist)
    return _json_response(_merge_assist(base_assist, llm_assist))


@router.api_route("/v1/{upstream_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
async def proxy_market_report_finder(upstream_path: str, request: Request) -> Response:
    return await _proxy_request(
        base_url=REPORT_FINDER_BASE,
        upstream_path=f"/v1/{upstream_path}",
        request=request,
    )


@router.get("/markets")
async def market_modules() -> Response:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            upstream = await client.get(f"{MARKET_RULES_BASE}/markets")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Market rules service unavailable: {exc}") from exc
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=_content_type(upstream.headers),
    )


@router.get("/markets/cn/rules")
async def cn_market_rules() -> Response:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            upstream = await client.get(f"{MARKET_RULES_BASE}/markets/cn/rules")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Market rules service unavailable: {exc}") from exc
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=_content_type(upstream.headers),
    )


@router.get("/market-report-health")
async def market_report_health() -> dict[str, Any]:
    result: dict[str, Any] = {
        "report_finder_base": REPORT_FINDER_BASE,
        "market_rules_base": MARKET_RULES_BASE,
        "report_finder": {"status": "unknown"},
        "market_rules": {"status": "unknown"},
    }
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            finder = await client.get(f"{REPORT_FINDER_BASE}/health")
            finder_payload: dict[str, Any] = {}
            try:
                parsed = finder.json()
                if isinstance(parsed, dict):
                    finder_payload = parsed
            except Exception:
                finder_payload = {}
            result["report_finder"] = {
                "status": "ok" if finder.status_code < 400 else "error",
                "code": finder.status_code,
                "config": finder_payload.get("config") or {},
                "markets": finder_payload.get("markets") or {},
            }
        except httpx.RequestError as exc:
            result["report_finder"] = {"status": "error", "error": str(exc)}
        try:
            rules = await client.get(f"{MARKET_RULES_BASE}/healthz")
            result["market_rules"] = {"status": "ok" if rules.status_code < 400 else "error", "code": rules.status_code}
        except httpx.RequestError as exc:
            result["market_rules"] = {"status": "error", "error": str(exc)}
    return result


@router.get("/market-reports/packages")
async def list_market_packages(market: str | None = None, q: str = "", limit: int = 80) -> dict[str, Any]:
    codes = _markets_to_search(market)
    limit = max(1, min(int(limit or 80), 500))
    query = str(q or "").strip().lower()
    packages: list[dict[str, Any]] = []
    for code in codes:
        for package_dir in _iter_market_packages(code):
            summary = _read_market_package_summary(package_dir)
            haystack = " ".join(
                str(summary.get(key) or "")
                for key in ("package_path", "market", "filing_id", "ticker", "company_name", "form", "report_type", "fiscal_year")
            ).lower()
            if query and query not in haystack:
                continue
            packages.append(summary)
    packages.sort(key=lambda item: str(item.get("published_at") or item.get("period_end") or ""), reverse=True)
    return {
        "ok": True,
        "market": codes[0] if len(codes) == 1 else None,
        "markets": codes,
        "roots": {code: _rel_or_abs(MARKET_WIKI_ROOTS[code]) for code in codes},
        "count": len(packages[:limit]),
        "packages": packages[:limit],
    }


@router.get("/market-reports/package")
async def market_package_detail_by_path(market: str, package_path: str) -> dict[str, Any]:
    code = _market_code(market)
    return _read_market_package_detail(_safe_market_package_path(code, package_path))


@router.get("/market-reports/package/quality")
async def market_package_quality_by_path(market: str, package_path: str) -> dict[str, Any]:
    code = _market_code(market)
    package_dir = _safe_market_package_path(code, package_path)
    return {
        "ok": True,
        "package_path": _rel_or_abs(package_dir),
        "manifest": _read_json_file(package_dir / "manifest.json", {}),
        "quality": _read_json_file(package_dir / "qa" / "quality_report.json", {}),
        "financial_checks": _read_json_file(package_dir / "metrics" / "financial_checks.json", {}),
        "source_map_summary": {
            "evidence": len((_read_json_file(package_dir / "qa" / "source_map.json", {}) or {}).get("entries") or []),
        },
    }


@router.get("/market-reports/package-file")
async def market_package_file(market: str, package_path: str, file: str, inline: bool = True) -> Response:
    code = _market_code(market)
    package_dir = _safe_market_package_path(code, package_path)
    if not file or file.startswith("/") or ".." in Path(file).parts:
        raise HTTPException(status_code=400, detail="Invalid file path")
    target = _safe_under(package_dir, package_dir / file)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Package file not found")
    if inline:
        return FileResponse(target, media_type=_media_type_for_file(target), headers={"Content-Disposition": "inline"})
    return FileResponse(target, media_type=_media_type_for_file(target))


@router.post("/market-reports/packages/build")
async def build_market_package(
    request: Request,
    wait: bool = False,
    _ops_user=Depends(require_permission("system.config")),
) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object payload is required")
    if wait:
        return _run_market_package_build(payload)
    job = market_report_job_service.start(
        "market-package-build",
        lambda: _run_market_package_build(payload),
        created_by=_job_created_by(_ops_user),
    )
    return {"ok": True, "queued": True, **job}


@router.post("/market-reports/eu/parse")
async def parse_eu_market_report(
    request: Request,
    wait: bool = False,
    _ops_user=Depends(require_permission("system.config")),
) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object payload is required")
    payload = {**payload, "market": "EU"}
    if wait:
        return _run_market_package_build(payload)
    job = market_report_job_service.start(
        "eu-market-report-parse",
        lambda: _run_market_package_build(payload),
        created_by=_job_created_by(_ops_user),
    )
    return {"ok": True, "queued": True, **job}


@router.post("/market-reports/packages/import")
async def import_market_package(
    request: Request,
    wait: bool = False,
    _ops_user=Depends(require_permission("system.config")),
) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object payload is required")
    if wait:
        return _run_market_package_import(payload)
    job = market_report_job_service.start(
        "market-package-import",
        lambda: _run_market_package_import(payload),
        created_by=_job_created_by(_ops_user),
    )
    return {"ok": True, "queued": True, **job}


@router.post("/market-reports/packages/vector-ingest")
async def vector_ingest_market_package(
    request: Request,
    wait: bool = False,
    _ops_user=Depends(require_permission("system.config")),
) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object payload is required")
    if wait:
        return _run_market_vector_ingest(payload)
    job = market_report_job_service.start(
        "market-vector-ingest",
        lambda: _run_market_vector_ingest(payload),
        created_by=_job_created_by(_ops_user),
    )
    return {"ok": True, "queued": True, **job}


@router.get("/market-reports/eval")
async def market_ingestion_eval_report(include_markdown: bool = False) -> dict[str, Any]:
    report = _read_json_file(MARKET_INGESTION_EVAL_REPORT_PATH, {})
    markdown = None
    if include_markdown and MARKET_INGESTION_EVAL_MARKDOWN_PATH.is_file():
        markdown = MARKET_INGESTION_EVAL_MARKDOWN_PATH.read_text(encoding="utf-8")
    return market_report_status_service.market_ingestion_eval_report_payload(
        report=report,
        report_path=_rel_or_abs(MARKET_INGESTION_EVAL_REPORT_PATH),
        markdown_path=_rel_or_abs(MARKET_INGESTION_EVAL_MARKDOWN_PATH),
        markdown=markdown,
    )


@router.post("/market-reports/eval/run")
async def run_market_ingestion_eval(
    request: Request,
    wait: bool = False,
    _ops_user=Depends(require_permission("system.config")),
) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object payload is required")
    if wait:
        return _run_market_ingestion_eval(payload)
    job = market_report_job_service.start(
        "market-ingestion-eval",
        lambda: _run_market_ingestion_eval(payload),
        created_by=_job_created_by(_ops_user),
    )
    return {"ok": True, "queued": True, **job}


@router.get("/market-reports/packages/{filing_id}")
async def market_package_detail_by_filing_id(filing_id: str, market: str | None = None) -> dict[str, Any]:
    _code, package_dir = _find_market_package_by_filing_id(filing_id, market)
    return _read_market_package_detail(package_dir)


@router.get("/market-reports/packages/{filing_id}/quality")
async def market_package_quality_by_filing_id(filing_id: str, market: str | None = None) -> dict[str, Any]:
    _code, package_dir = _find_market_package_by_filing_id(filing_id, market)
    return {
        "ok": True,
        "package_path": _rel_or_abs(package_dir),
        "manifest": _read_json_file(package_dir / "manifest.json", {}),
        "quality": _read_json_file(package_dir / "qa" / "quality_report.json", {}),
        "financial_checks": _read_json_file(package_dir / "metrics" / "financial_checks.json", {}),
    }


@router.get("/market-reports/evidence/{evidence_id}")
async def market_evidence_detail(
    evidence_id: str,
    market: str | None = None,
    package_path: str | None = None,
) -> dict[str, Any]:
    package_dir = _safe_market_package_path(_market_code(market), package_path) if market and package_path else None
    code, found_package, entry = _find_market_evidence(evidence_id, market=market, package_dir=package_dir)
    file_path = entry.get("local_path")
    file_url = None
    if file_path:
        file_url = f"/api/market-reports/package-file?{urlencode({'market': code, 'package_path': _rel_or_abs(found_package), 'file': str(file_path)})}"
    return {
        "ok": True,
        "market": code,
        "package_path": _rel_or_abs(found_package),
        "evidence": entry,
        "file_url": file_url,
    }


@router.get("/us-sec/case-set")
async def us_sec_case_set_status() -> dict[str, Any]:
    case_set = _read_json_file(US_SEC_CASE_SET_PATH, {})
    ingest_report = _read_json_file(US_SEC_INGEST_REPORT_PATH, {})
    return market_report_status_service.us_sec_case_set_status_payload(
        case_set=case_set,
        ingest_report=ingest_report,
        case_set_path=str(US_SEC_CASE_SET_PATH),
        ingest_report_path=str(US_SEC_INGEST_REPORT_PATH),
    )


@router.post("/us-sec/case-set/ingest")
async def us_sec_case_set_ingest(
    request: Request,
    wait: bool = False,
    _ops_user=Depends(require_permission("system.config")),
) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object payload is required")
    if wait:
        return _run_us_sec_case_set_ingest(payload)
    job = market_report_job_service.start(
        "us-sec-ingest",
        lambda: _run_us_sec_case_set_ingest(payload),
        created_by=_job_created_by(_ops_user),
    )
    return {"ok": True, "queued": True, **job}


@router.get("/us-sec/packages/{ticker}")
async def us_sec_package_detail(ticker: str) -> dict[str, Any]:
    item = _latest_case_item_for_ticker(ticker)
    if not item:
        raise HTTPException(status_code=404, detail=f"No package for ticker {ticker}")
    return _read_package_detail(_safe_package_path(str(item.get("package_path") or "")))


@router.get("/us-sec/package-file")
async def us_sec_package_file(package_path: str, file: str, inline: bool = True) -> Response:
    package_dir = _safe_package_path(package_path)
    if not file or file.startswith("/") or ".." in Path(file).parts:
        raise HTTPException(status_code=400, detail="Invalid file path")
    target = _safe_under(package_dir, package_dir / file)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Package file not found")
    if inline:
        return FileResponse(target, media_type=_media_type_for_file(target), headers={"Content-Disposition": "inline"})
    return FileResponse(target, media_type=_media_type_for_file(target))


@router.post("/us-sec/packages/{ticker}/rebuild")
async def us_sec_rebuild_package(
    ticker: str,
    request: Request,
    wait: bool = False,
    _ops_user=Depends(require_permission("system.config")),
) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if wait:
        return _run_us_sec_rebuild_package(ticker, payload)
    job = market_report_job_service.start(
        "us-sec-rebuild",
        lambda: _run_us_sec_rebuild_package(ticker, payload),
        created_by=_job_created_by(_ops_user),
    )
    return {"ok": True, "queued": True, **job}


@router.get("/jobs/{job_id}")
async def market_report_job_status(
    job_id: str,
    _ops_user=Depends(require_permission("system.config")),
) -> dict[str, Any]:
    job = market_report_job_service.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
