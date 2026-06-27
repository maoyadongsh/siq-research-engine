import os
import json
import re
import uuid
import subprocess
import sys
import tempfile
import threading
from urllib.parse import urlencode
from datetime import datetime, timezone
from typing import Any
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response

from services.auth_dependencies import require_permission
from services.hermes_client import collect_run_result, create_run
from services.llm_settings import load_llm_settings
from services.hermes_model_control import infer_model_mode, set_all_profile_model_modes
from services.path_config import REPORT_DOWNLOADS_ROOT


router = APIRouter(tags=["market-reports"])

REPORT_FINDER_BASE = (
    os.environ.get("SIQ_REPORT_FINDER_BASE")
    or os.environ.get("REPORT_FINDER_BASE")
    or "http://127.0.0.1:18000"
).rstrip("/")
MARKET_RULES_BASE = (
    os.environ.get("SIQ_MARKET_REPORT_RULES_BASE")
    or os.environ.get("MARKET_REPORT_RULES_BASE")
    or "http://127.0.0.1:18020"
).rstrip("/")
MARKET_REPORT_PROXY_TIMEOUT = float(os.environ.get("SIQ_MARKET_REPORT_PROXY_TIMEOUT", "120"))
MARKET_REPORT_ASSIST_TIMEOUT = float(os.environ.get("SIQ_MARKET_REPORT_ASSIST_TIMEOUT", "45"))
REPO_ROOT = Path(__file__).resolve().parents[3]
US_SEC_CASE_SET_PATH = Path(
    os.environ.get("SIQ_US_SEC_CASE_SET_PATH", str(REPO_ROOT / "data" / "wiki" / "us_sec" / "case_set_50_us_10k.json"))
)
US_SEC_INGEST_REPORT_PATH = Path(
    os.environ.get("SIQ_US_SEC_INGEST_REPORT_PATH", str(REPO_ROOT / "data" / "wiki" / "us_sec" / "case_set_50_us_10k_ingest_report.json"))
)
US_SEC_INGEST_SCRIPT = Path(
    os.environ.get("SIQ_US_SEC_INGEST_SCRIPT", str(REPO_ROOT / "scripts" / "us-sec" / "ingest_sec_case_set.py"))
)
US_SEC_WIKI_ROOT = Path(
    os.environ.get("SIQ_US_SEC_WIKI_ROOT", str(REPO_ROOT / "data" / "wiki" / "us_sec"))
)
US_SEC_PACKAGE_BUILD_SCRIPT = Path(
    os.environ.get("SIQ_US_SEC_PACKAGE_BUILD_SCRIPT", str(REPO_ROOT / "scripts" / "us-sec" / "build_sec_evidence_package.py"))
)
MARKET_VECTOR_INGEST_SCRIPT = Path(
    os.environ.get(
        "SIQ_MARKET_VECTOR_INGEST_SCRIPT",
        str(REPO_ROOT / "scripts" / "vector-index" / "milvus-ingestion" / "ingest_market_evidence_chunks.py"),
    )
)
MARKET_INGESTION_EVAL_SCRIPT = Path(
    os.environ.get("SIQ_MARKET_INGESTION_EVAL_SCRIPT", str(REPO_ROOT / "scripts" / "maintenance" / "run_market_ingestion_eval.py"))
)
MARKET_INGESTION_EVAL_REPORT_PATH = Path(
    os.environ.get(
        "SIQ_MARKET_INGESTION_EVAL_REPORT_PATH",
        str(REPO_ROOT / "eval_datasets" / "market_ingestion_cases" / "market_ingestion_eval_report.json"),
    )
)
MARKET_INGESTION_EVAL_MARKDOWN_PATH = Path(
    os.environ.get(
        "SIQ_MARKET_INGESTION_EVAL_MARKDOWN_PATH",
        str(REPO_ROOT / "eval_datasets" / "market_ingestion_cases" / "market_ingestion_eval_report.md"),
    )
)
MARKET_WIKI_ROOTS = {
    "US": Path(os.environ.get("SIQ_US_SEC_WIKI_ROOT", str(REPO_ROOT / "data" / "wiki" / "us_sec"))),
    "HK": Path(os.environ.get("SIQ_HK_WIKI_ROOT", str(REPO_ROOT / "data" / "wiki" / "hk_reports"))),
    "JP": Path(os.environ.get("SIQ_JP_WIKI_ROOT", str(REPO_ROOT / "data" / "wiki" / "jp_reports"))),
    "KR": Path(os.environ.get("SIQ_KR_WIKI_ROOT", str(REPO_ROOT / "data" / "wiki" / "kr_reports"))),
    "EU": Path(os.environ.get("SIQ_EU_WIKI_ROOT", str(REPO_ROOT / "data" / "wiki" / "eu_reports"))),
}
MARKET_BUILD_SCRIPTS = {
    "US": US_SEC_PACKAGE_BUILD_SCRIPT,
    "HK": Path(os.environ.get("SIQ_HK_PACKAGE_BUILD_SCRIPT", str(REPO_ROOT / "scripts" / "hk" / "build_hk_evidence_package.py"))),
    "JP": Path(os.environ.get("SIQ_JP_PACKAGE_BUILD_SCRIPT", str(REPO_ROOT / "scripts" / "jp" / "build_jp_evidence_package.py"))),
    "KR": Path(os.environ.get("SIQ_KR_PACKAGE_BUILD_SCRIPT", str(REPO_ROOT / "scripts" / "kr" / "build_kr_evidence_package.py"))),
    "EU": Path(os.environ.get("SIQ_EU_PACKAGE_BUILD_SCRIPT", str(REPO_ROOT / "scripts" / "eu" / "build_eu_pdf_evidence_package.py"))),
}
EU_ESEF_PACKAGE_BUILD_SCRIPT = Path(
    os.environ.get("SIQ_EU_ESEF_PACKAGE_BUILD_SCRIPT", str(REPO_ROOT / "scripts" / "eu" / "build_eu_esef_evidence_package.py"))
)
MARKET_IMPORT_SCRIPTS = {
    "US": Path(os.environ.get("SIQ_US_IMPORT_SCRIPT", str(REPO_ROOT / "db" / "imports" / "import_sec_filing_to_postgres.py"))),
    "HK": Path(os.environ.get("SIQ_HK_IMPORT_SCRIPT", str(REPO_ROOT / "db" / "imports" / "import_hk_evidence_package_to_postgres.py"))),
    "JP": Path(os.environ.get("SIQ_JP_IMPORT_SCRIPT", str(REPO_ROOT / "db" / "imports" / "import_jp_evidence_package_to_postgres.py"))),
    "KR": Path(os.environ.get("SIQ_KR_IMPORT_SCRIPT", str(REPO_ROOT / "db" / "imports" / "import_kr_evidence_package_to_postgres.py"))),
    "EU": Path(os.environ.get("SIQ_EU_IMPORT_SCRIPT", str(REPO_ROOT / "db" / "imports" / "import_eu_evidence_package_to_postgres.py"))),
}
_job_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _snapshot_job(job: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in job.items()
        if key not in {"target"}
    }


def _remember_job(job: dict[str, Any]) -> None:
    with _job_lock:
        _jobs[job["job_id"]] = job
        if len(_jobs) > 200:
            old_ids = sorted(_jobs, key=lambda item: _jobs[item].get("created_at", ""))[:-200]
            for old_id in old_ids:
                _jobs.pop(old_id, None)


def _start_background_job(kind: str, target, *, created_by: str | None = None) -> dict[str, Any]:
    job_id = f"{kind}-{uuid.uuid4().hex[:12]}"
    job: dict[str, Any] = {
        "job_id": job_id,
        "kind": kind,
        "status": "queued",
        "created_at": _now_iso(),
        "started_at": None,
        "finished_at": None,
        "created_by": created_by,
        "result": None,
        "error": None,
    }
    _remember_job(job)

    def runner() -> None:
        with _job_lock:
            job["status"] = "running"
            job["started_at"] = _now_iso()
        try:
            result = target()
            with _job_lock:
                job["status"] = "succeeded" if result.get("ok", True) else "failed"
                job["result"] = result
                job["finished_at"] = _now_iso()
        except Exception as exc:
            with _job_lock:
                job["status"] = "failed"
                job["error"] = str(exc)
                job["finished_at"] = _now_iso()

    thread = threading.Thread(target=runner, name=f"siq-{job_id}", daemon=True)
    thread.start()
    return _snapshot_job(job)


def _get_job_or_404(job_id: str) -> dict[str, Any]:
    with _job_lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return _snapshot_job(job)


def _content_type(headers: httpx.Headers) -> str:
    return headers.get("content-type") or "application/octet-stream"


def _json_response(payload: dict[str, Any], status_code: int = 200) -> Response:
    return Response(
        content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        status_code=status_code,
        media_type="application/json",
    )


def _command_for_display(args: list[str]) -> str:
    redacted: list[str] = []
    hide_next = False
    for arg in args:
        if hide_next:
            redacted.append("***")
            hide_next = False
            continue
        redacted.append(arg)
        if arg in {"--database-url"}:
            hide_next = True
    return " ".join(redacted)


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
    files = {
        "manifest": package_dir / "manifest.json",
        "quality_report": package_dir / "qa" / "quality_report.json",
        "source_map": package_dir / "qa" / "source_map.json",
        "financial_data": package_dir / "metrics" / "financial_data.json",
        "financial_checks": package_dir / "metrics" / "financial_checks.json",
        "normalized_metrics": package_dir / "metrics" / "normalized_metrics.json",
        "table_index": package_dir / "tables" / "table_index.json",
    }
    return {key: str(path.relative_to(package_dir)) for key, path in files.items() if path.exists()}


def _iter_market_packages(market: str) -> list[Path]:
    root = MARKET_WIKI_ROOTS[market]
    if not root.exists():
        return []
    patterns = ("*/*/*/*/manifest.json",) if market == "EU" else ("*/*/*/manifest.json",)
    package_dirs: list[Path] = []
    for pattern in patterns:
        package_dirs.extend(path.parent for path in root.glob(pattern))
    return sorted(package_dirs, key=lambda path: path.stat().st_mtime, reverse=True)


def _read_market_package_summary(package_dir: Path) -> dict[str, Any]:
    manifest = _read_json_file(package_dir / "manifest.json", {})
    quality = _read_json_file(package_dir / "qa" / "quality_report.json", {})
    metrics = (_read_json_file(package_dir / "metrics" / "normalized_metrics.json", {}) or {}).get("metrics") or []
    source_map = (_read_json_file(package_dir / "qa" / "source_map.json", {}) or {}).get("entries") or []
    return {
        "package_path": _rel_or_abs(package_dir),
        "paths": _market_package_paths(package_dir),
        "market": manifest.get("market"),
        "country": manifest.get("country"),
        "document_format": manifest.get("document_format"),
        "filing_id": manifest.get("filing_id"),
        "parse_run_id": manifest.get("parse_run_id"),
        "ticker": manifest.get("ticker"),
        "company_name": manifest.get("company_name"),
        "form": manifest.get("form"),
        "report_type": manifest.get("report_type"),
        "fiscal_year": manifest.get("fiscal_year"),
        "fiscal_period": manifest.get("fiscal_period"),
        "period_end": manifest.get("period_end"),
        "published_at": manifest.get("published_at") or manifest.get("filing_date"),
        "quality_status": quality.get("overall_status") or manifest.get("quality_status"),
        "counts": {
            "sections": quality.get("section_count") or (quality.get("summary") or {}).get("section_count"),
            "tables": quality.get("table_count") or (quality.get("summary") or {}).get("table_count"),
            "raw_facts": quality.get("raw_fact_count") or (quality.get("summary") or {}).get("xbrl_fact_count"),
            "metrics": quality.get("normalized_metric_count") or len(metrics),
            "evidence": len(source_map),
        },
    }


def _read_market_package_detail(package_dir: Path) -> dict[str, Any]:
    summary = _read_market_package_summary(package_dir)
    return {
        **summary,
        "manifest": _read_json_file(package_dir / "manifest.json", {}),
        "quality": _read_json_file(package_dir / "qa" / "quality_report.json", {}),
        "financial_data": _read_json_file(package_dir / "metrics" / "financial_data.json", {}),
        "financial_checks": _read_json_file(package_dir / "metrics" / "financial_checks.json", {}),
        "metrics": (_read_json_file(package_dir / "metrics" / "normalized_metrics.json", {}) or {}).get("metrics") or [],
        "source_map": (_read_json_file(package_dir / "qa" / "source_map.json", {}) or {}).get("entries") or [],
        "tables": (_read_json_file(package_dir / "tables" / "table_index.json", {}) or {}).get("tables") or [],
    }


def _markets_to_search(market: str | None) -> list[str]:
    if market:
        return [_market_code(market)]
    return list(MARKET_WIKI_ROOTS)


def _find_market_package_by_filing_id(filing_id: str, market: str | None = None) -> tuple[str, Path]:
    target = str(filing_id or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="filing_id is required")
    for code in _markets_to_search(market):
        for package_dir in _iter_market_packages(code):
            manifest = _read_json_file(package_dir / "manifest.json", {})
            if str(manifest.get("filing_id") or "") == target:
                return code, package_dir
    raise HTTPException(status_code=404, detail="Market evidence package not found")


def _find_market_evidence(
    evidence_id: str,
    *,
    market: str | None = None,
    package_dir: Path | None = None,
) -> tuple[str, Path, dict[str, Any]]:
    target = str(evidence_id or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="evidence_id is required")
    if package_dir is not None:
        packages = [(str((_read_json_file(package_dir / "manifest.json", {}) or {}).get("market") or market or "").upper(), package_dir)]
    else:
        packages = [(code, path) for code in _markets_to_search(market) for path in _iter_market_packages(code)]
    for code, path in packages:
        source_map = _read_json_file(path / "qa" / "source_map.json", {})
        for entry in source_map.get("entries") or []:
            if isinstance(entry, dict) and str(entry.get("evidence_id") or "") == target:
                return code, path, entry
    raise HTTPException(status_code=404, detail="Evidence not found")


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
    args = [sys.executable, str(script), str(source_path)]
    metadata = payload.get("metadata_path")
    meta_path: Path | None = None
    if metadata:
        meta_path = Path(str(metadata))
        meta_path = meta_path if meta_path.is_absolute() else REPO_ROOT / meta_path
        if not meta_path.is_file():
            raise HTTPException(status_code=404, detail="metadata_path not found")
    else:
        meta_path = _adjacent_metadata_path(source_path)
    if meta_path:
        args.extend(["--metadata", str(meta_path)])
    parser_result = payload.get("parser_result")
    if _market_build_requires_parser_result(market, source_path) and not parser_result:
        raise HTTPException(status_code=400, detail=f"parser_result is required for {market} package builds")
    if parser_result and market in {"HK", "JP", "KR", "EU"} and _market_build_accepts_parser_result(market, script):
        parser_path = Path(str(parser_result))
        parser_path = parser_path if parser_path.is_absolute() else REPO_ROOT / parser_path
        if not parser_path.exists():
            raise HTTPException(status_code=404, detail="parser_result not found")
        args.extend(["--parser-result", str(parser_path)])
    args.extend(["--output-root", str(MARKET_WIKI_ROOTS[market])])
    if payload.get("force"):
        args.append("--force")
    completed = subprocess.run(args, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=900, check=False)
    if completed.returncode != 0:
        return {"ok": False, "returncode": completed.returncode, "stdout": completed.stdout[-4000:], "stderr": completed.stderr[-4000:], "command": _command_for_display(args)}
    output_lines = (completed.stdout or "").strip().splitlines()
    if not output_lines:
        return {"ok": False, "returncode": completed.returncode, "stdout": completed.stdout[-4000:], "stderr": "Package build did not print a package path", "command": _command_for_display(args)}
    package_path = Path(output_lines[-1])
    return {"ok": True, "package": _read_market_package_detail(package_path), "stdout": completed.stdout[-4000:], "stderr": completed.stderr[-4000:], "command": _command_for_display(args)}


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
    args = [sys.executable, str(script)]
    if market == "US":
        args.extend(["--package", str(package_dir)])
    else:
        args.append(str(package_dir))
    database_url = payload.get("database_url")
    if database_url:
        args.extend(["--database-url", str(database_url)])
    if payload.get("ddl") or payload.get("run_ddl"):
        args.append("--ddl")
    completed = subprocess.run(args, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=900, check=False)
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
    args = [
        sys.executable,
        str(MARKET_VECTOR_INGEST_SCRIPT),
        "--package",
        str(package_dir),
        "--batch-tag",
        str(payload.get("batch_tag") or "market-evidence"),
    ]
    for key, flag in (("collection", "--collection"), ("embed_url", "--embed-url"), ("embed_model", "--embed-model"), ("vector_dim", "--vector-dim")):
        value = payload.get(key)
        if value not in (None, ""):
            args.extend([flag, str(value)])
    dry_run = bool(payload.get("dry_run", True))
    if dry_run:
        args.append("--dry-run")
    completed = subprocess.run(args, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=1800, check=False)
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
    output = Path(str(payload.get("output") or MARKET_INGESTION_EVAL_REPORT_PATH))
    markdown = Path(str(payload.get("markdown") or MARKET_INGESTION_EVAL_MARKDOWN_PATH))
    if not output.is_absolute():
        output = REPO_ROOT / output
    if not markdown.is_absolute():
        markdown = REPO_ROOT / markdown
    args = [sys.executable, str(MARKET_INGESTION_EVAL_SCRIPT), "--output", str(output), "--markdown", str(markdown)]
    completed = subprocess.run(args, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=900, check=False)
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
        ".md": "text/markdown; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".txt": "text/plain; charset=utf-8",
    }.get(path.suffix.lower(), "application/octet-stream")


def _safe_ingest_args(payload: dict[str, Any]) -> list[str]:
    args = [
        sys.executable,
        str(US_SEC_INGEST_SCRIPT),
        "--case-set",
        str(US_SEC_CASE_SET_PATH),
        "--report",
        str(US_SEC_INGEST_REPORT_PATH),
    ]
    if payload.get("include_fail"):
        args.append("--include-fail")
    if payload.get("postgres"):
        args.append("--postgres")
    if payload.get("milvus"):
        args.append("--milvus")
    if payload.get("ddl"):
        args.append("--ddl")
    if payload.get("dry_run", True):
        args.append("--dry-run")
    tickers = str(payload.get("tickers") or "").strip().upper()
    if tickers:
        if not re.fullmatch(r"[A-Z0-9.,_-]{1,240}", tickers):
            raise HTTPException(status_code=400, detail="Invalid tickers")
        args.extend(["--tickers", tickers])
    batch_tag = str(payload.get("batch_tag") or "").strip()
    if batch_tag:
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", batch_tag):
            raise HTTPException(status_code=400, detail="Invalid batch_tag")
        args.extend(["--batch-tag", batch_tag])
    return args


def _run_us_sec_case_set_ingest(payload: dict[str, Any]) -> dict[str, Any]:
    if not US_SEC_INGEST_SCRIPT.is_file():
        raise HTTPException(status_code=404, detail=f"Missing ingest script: {US_SEC_INGEST_SCRIPT}")
    args = _safe_ingest_args(payload)
    try:
        completed = subprocess.run(
            args,
            cwd=str(REPO_ROOT),
            check=False,
            capture_output=True,
            text=True,
            timeout=1800,
        )
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
        args = [sys.executable, str(US_SEC_PACKAGE_BUILD_SCRIPT), str(tmp_source), "--force"]
        if tmp_metadata:
            args.extend(["--metadata", str(tmp_metadata)])
        args.extend(["--output-root", str(US_SEC_WIKI_ROOT)])
        try:
            completed = subprocess.run(
                args,
                cwd=str(REPO_ROOT),
                check=False,
                capture_output=True,
                text=True,
                timeout=900,
            )
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
    job = _start_background_job("market-package-build", lambda: _run_market_package_build(payload))
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
    job = _start_background_job("eu-market-report-parse", lambda: _run_market_package_build(payload))
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
    job = _start_background_job("market-package-import", lambda: _run_market_package_import(payload))
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
    job = _start_background_job("market-vector-ingest", lambda: _run_market_vector_ingest(payload))
    return {"ok": True, "queued": True, **job}


@router.get("/market-reports/eval")
async def market_ingestion_eval_report(include_markdown: bool = False) -> dict[str, Any]:
    report = _read_json_file(MARKET_INGESTION_EVAL_REPORT_PATH, {})
    result: dict[str, Any] = {
        "ok": bool(report),
        "report_path": _rel_or_abs(MARKET_INGESTION_EVAL_REPORT_PATH),
        "markdown_path": _rel_or_abs(MARKET_INGESTION_EVAL_MARKDOWN_PATH),
        "report": report,
    }
    if include_markdown and MARKET_INGESTION_EVAL_MARKDOWN_PATH.is_file():
        result["markdown"] = MARKET_INGESTION_EVAL_MARKDOWN_PATH.read_text(encoding="utf-8")
    return result


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
    job = _start_background_job("market-ingestion-eval", lambda: _run_market_ingestion_eval(payload))
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
    items = case_set.get("items") if isinstance(case_set, dict) else []
    if not isinstance(items, list):
        items = []
    quality: dict[str, int] = {}
    total_counts = {
        "xbrl_fact_count": 0,
        "normalized_metric_count": 0,
        "section_count": 0,
        "table_count": 0,
    }
    by_ticker = []
    for item in items:
        if not isinstance(item, dict):
            continue
        status = str(item.get("quality_status") or "unknown")
        quality[status] = quality.get(status, 0) + 1
        summary = item.get("quality_summary") if isinstance(item.get("quality_summary"), dict) else {}
        total_counts["xbrl_fact_count"] += int(summary.get("xbrl_fact_count") or 0)
        total_counts["normalized_metric_count"] += int(summary.get("normalized_metric_count") or 0)
        total_counts["section_count"] += int(summary.get("section_count") or 0)
        total_counts["table_count"] += int(summary.get("table_count") or 0)
        by_ticker.append({
            "ticker": item.get("ticker"),
            "company_name": item.get("company_name"),
            "fiscal_year": item.get("fiscal_year"),
            "period_end": item.get("period_end"),
            "filing_date": item.get("filing_date"),
            "quality_status": status,
            "quality_summary": summary,
            "package_path": item.get("package_path"),
        })
    relationship = {}
    if isinstance(ingest_report, dict):
        relationship = {
            "generated_at": ingest_report.get("generated_at"),
            "summary": ingest_report.get("summary") or {},
            "package_count": ingest_report.get("package_count"),
            "collection": ingest_report.get("collection"),
            "batch_tag": ingest_report.get("batch_tag"),
        }
    return {
        "case_set_path": str(US_SEC_CASE_SET_PATH),
        "ingest_report_path": str(US_SEC_INGEST_REPORT_PATH),
        "company_count": len(by_ticker),
        "quality": quality,
        "counts": total_counts,
        "items": by_ticker,
        "ingest_report": relationship,
    }


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
    job = _start_background_job("us-sec-ingest", lambda: _run_us_sec_case_set_ingest(payload))
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
    job = _start_background_job("us-sec-rebuild", lambda: _run_us_sec_rebuild_package(ticker, payload))
    return {"ok": True, "queued": True, **job}


@router.get("/jobs/{job_id}")
async def market_report_job_status(
    job_id: str,
    _ops_user=Depends(require_permission("system.config")),
) -> dict[str, Any]:
    return _get_job_or_404(job_id)
