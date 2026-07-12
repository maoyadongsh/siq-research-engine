import asyncio
import hashlib
import json
import logging
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from database import get_async_session
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from services.auth_dependencies import get_current_user, require_permission
from services.auth_service import User
from services.command_runner import format_command, run_command
from services.hermes_client import collect_run_result, create_run, hermes_profile_config
from services.hermes_model_control import set_all_profile_model_modes
from services.job_service import market_report_job_service
from services.llm_settings import load_llm_settings
from services.market_report_settings import (
    EU_ESEF_PACKAGE_BUILD_SCRIPT,
    MARKET_BUILD_SCRIPTS,
    MARKET_DATABASES,
    MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS,
    MARKET_DOCUMENT_FULL_ROOTS,
    MARKET_IMPORT_SCRIPTS,
    MARKET_INGESTION_EVAL_MARKDOWN_PATH,
    MARKET_INGESTION_EVAL_REPORT_PATH,
    MARKET_INGESTION_EVAL_SCRIPT,
    MARKET_REPORT_ASSIST_TIMEOUT,
    MARKET_REPORT_PROXY_TIMEOUT,
    MARKET_RULES_BASE,
    MARKET_VECTOR_COLLECTIONS,
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
from services.upload_proxy_limits import (
    DEFAULT_CHUNK_BYTES,
    DEFAULT_MAX_BATCH_BYTES,
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_MAX_FILES,
    UPLOAD_PROXY_LIMITER,
    BufferedUpload,
    buffer_upload_files,
    close_buffered_uploads,
    env_int,
)
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.concurrency import run_in_threadpool

from routers.workspace import record_user_artifact_async
from services import (
    market_document_full_postgres_status,
    market_document_identity,
    market_package_repository as market_packages,
    market_report_assist_service,
    market_report_commands,
    market_report_eval_service,
    market_report_package_service,
    market_report_postgres_service,
    market_report_proxy,
    market_report_queueing,
    market_report_status_service,
    observability,
)

router = APIRouter(tags=["market-reports"])
logger = logging.getLogger(__name__)
US_SEC_UPLOAD_SUFFIXES = {".pdf", ".html", ".htm", ".xhtml", ".xml", ".xbrl", ".zip"}
US_SEC_UPLOAD_MAX_FILES = env_int("SIQ_US_SEC_UPLOAD_MAX_FILES", DEFAULT_MAX_FILES)
US_SEC_UPLOAD_MAX_FILE_BYTES = env_int("SIQ_US_SEC_UPLOAD_MAX_FILE_BYTES", DEFAULT_MAX_FILE_BYTES)
US_SEC_UPLOAD_MAX_BATCH_BYTES = env_int("SIQ_US_SEC_UPLOAD_MAX_BATCH_BYTES", DEFAULT_MAX_BATCH_BYTES)
MARKET_WIKISET_ROOT = REPO_ROOT / "scripts" / "wiki" / "market_wikiset"
MARKET_RULE_SEMANTIC_SCRIPT = MARKET_WIKISET_ROOT / "run_market_rule_semantics.py"
MARKET_LLM_SEMANTIC_SCRIPT = MARKET_WIKISET_ROOT / "run_market_llm_semantics.py"
MARKET_DOCUMENT_FULL_SCHEMAS = {
    **market_document_full_postgres_status.MARKET_DOCUMENT_FULL_SCHEMAS,
}
MARKET_ALIASES = market_document_identity.MARKET_ALIASES
FINDER_PROXY_ALLOWED_ROUTES = {
    "/v1/company/resolve": frozenset({"POST"}),
    "/v1/resolve": frozenset({"POST"}),
    "/v1/sources": frozenset({"GET"}),
    "/v1/reports/latest": frozenset({"POST"}),
    "/v1/reports/recent": frozenset({"POST"}),
    "/v1/reports/assist": frozenset({"POST"}),
    "/v1/reports/curated-annuals": frozenset({"GET"}),
    "/v1/reports/select-download": frozenset({"POST"}),
    "/v1/reports/batch-download": frozenset({"POST"}),
    "/v1/reports/download": frozenset({"POST"}),
    "/v1/reports/direct-download": frozenset({"POST"}),
}


def _content_type(headers: httpx.Headers) -> str:
    return market_report_proxy.content_type(headers)


def _service_token(env_name: str) -> str | None:
    token = os.environ.get(env_name, "").strip()
    return token or None


def _finder_service_token() -> str | None:
    return _service_token("SIQ_MARKET_REPORT_FINDER_TOKEN")


def _market_rules_service_token() -> str | None:
    return _service_token("SIQ_MARKET_REPORT_RULES_TOKEN")


def _finder_proxy_path_allowed(path: str, method: str) -> bool:
    allowed_methods = FINDER_PROXY_ALLOWED_ROUTES.get(path)
    return bool(allowed_methods and method.upper() in allowed_methods)


def _json_response(payload: dict[str, Any], status_code: int = 200) -> Response:
    return Response(
        content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        status_code=status_code,
        media_type="application/json",
    )


def _command_for_display(args: list[str]) -> str:
    return format_command(args)


def _run_or_queue_market_report_job(
    *,
    wait: bool,
    kind: str,
    target,
    created_by: User | None = None,
) -> dict[str, Any]:
    return market_report_queueing.run_or_queue_market_report_job(
        wait=wait,
        job_service=market_report_job_service,
        kind=kind,
        target=target,
        created_by=created_by,
    )


def _read_json_file(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _safe_under(root: Path, path: Path) -> Path:
    return market_packages.safe_under(root, path)


def _market_code(value: str | None) -> str:
    return market_packages.market_code(value, MARKET_WIKI_ROOTS)


def _normalize_market_code(value: str | None) -> str:
    return market_document_identity.normalize_market_code(value)


def _safe_market_package_path(market: str, value: str | None) -> Path:
    if market == "US" and value:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = REPO_ROOT / candidate
        us_sec_roots = [US_SEC_WIKI_ROOT, REPO_ROOT / "data" / "wiki" / "us_sec"]
        seen_roots: set[Path] = set()
        for us_sec_root in us_sec_roots:
            resolved_root = us_sec_root.resolve()
            if resolved_root in seen_roots:
                continue
            seen_roots.add(resolved_root)
            try:
                market_packages.safe_under(us_sec_root, candidate)
            except HTTPException:
                continue
            return market_packages.safe_us_sec_package_path(
                value,
                repo_root=REPO_ROOT,
                us_sec_wiki_root=us_sec_root,
            )
    return market_packages.safe_market_package_path(
        market,
        value,
        repo_root=REPO_ROOT,
        market_wiki_roots=MARKET_WIKI_ROOTS,
    )


def _safe_download_path(value: str | None) -> Path:
    return market_packages.safe_download_path(value, downloads_root=REPORT_DOWNLOADS_ROOT)


def _safe_market_document_full_path(market: str, value: str | None) -> Path:
    market = _normalize_market_code(market)
    if not value:
        raise HTTPException(status_code=400, detail="document_full_path is required")
    root = MARKET_DOCUMENT_FULL_ROOTS[market]
    path = Path(value)
    candidates = [path] if path.is_absolute() else [REPO_ROOT / path, root / path]
    last_error: HTTPException | None = None
    for candidate in candidates:
        try:
            return _safe_under(root, candidate)
        except HTTPException as exc:
            last_error = exc
            continue
    try:
        raise last_error or HTTPException(status_code=400, detail="Invalid document_full_path")
    except HTTPException as exc:
        raise HTTPException(status_code=400, detail="document_full_path is outside the allowed market root") from exc


def _market_document_full_path_keys(market: str, value: str | None) -> list[str]:
    return market_document_full_postgres_status.market_document_full_path_keys(
        market,
        value,
        repo_root=REPO_ROOT,
        market_document_full_roots=MARKET_DOCUMENT_FULL_ROOTS,
        safe_market_document_full_path=_safe_market_document_full_path,
    )


def _truthy_payload_flag(payload: dict[str, Any], *keys: str) -> bool:
    return market_report_package_service.truthy_payload_flag(payload, *keys)


def _enforce_legacy_market_package_import(payload: dict[str, Any]) -> None:
    try:
        market_report_package_service.require_legacy_market_package_import(payload)
    except market_report_commands.MarketPackagePlanError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _market_document_full_db_status(
    market: str,
    *,
    parse_run_id: str | None = None,
    filing_id: str | None = None,
    document_full_path: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    return market_document_full_postgres_status.market_document_full_db_status(
        market,
        repo_root=REPO_ROOT,
        market_document_full_roots=MARKET_DOCUMENT_FULL_ROOTS,
        safe_market_document_full_path=_safe_market_document_full_path,
        market_databases=MARKET_DATABASES,
        schemas=MARKET_DOCUMENT_FULL_SCHEMAS,
        parse_run_id=parse_run_id,
        filing_id=filing_id,
        document_full_path=document_full_path,
        task_id=task_id,
    )


def _adjacent_metadata_path(path: Path) -> Path | None:
    metadata = path.with_suffix(path.suffix + ".metadata.json")
    return metadata if metadata.is_file() else None


def _rel_or_abs(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _iter_market_packages(market: str) -> list[Path]:
    return market_packages.iter_market_packages(market, MARKET_WIKI_ROOTS)


def _read_market_package_summary(package_dir: Path) -> dict[str, Any]:
    return market_packages.read_market_package_summary(package_dir)


def _read_market_package_detail(package_dir: Path) -> dict[str, Any]:
    return market_packages.read_market_package_detail(package_dir)


def _quality_gates_for_package(package_dir: Path) -> dict[str, Any]:
    return market_packages.build_quality_gates(package_dir)


def _load_plan_for_package(package_dir: Path) -> dict[str, Any]:
    return market_report_package_service.load_plan_for_package(
        package_dir,
        read_json_file=_read_json_file,
    )


def _quality_gates_with_load_plan(package_dir: Path) -> dict[str, Any]:
    return market_report_package_service.quality_gates_with_load_plan(
        package_dir,
        quality_gates_for_package=_quality_gates_for_package,
        load_plan_for_package=_load_plan_for_package,
    )


def _payload_force_enabled(payload: dict[str, Any]) -> bool:
    return market_report_commands.payload_force_enabled(payload)


def _force_audit_sources(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(source) for source in market_report_commands.force_audit_sources(payload)]


def _force_audit_text(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    return market_report_commands.force_audit_text(payload, keys)


def _force_one_shot_marker(payload: dict[str, Any]) -> str | None:
    return market_report_commands.force_one_shot_marker(payload)


def _redact_audit_text(value: str | None) -> str | None:
    return market_report_commands.redact_audit_text(value)


def _validate_force_audit(payload: dict[str, Any]) -> dict[str, str | None]:
    try:
        return market_report_commands.validate_force_audit(payload)
    except market_report_commands.MarketPackagePlanError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _payload_with_force_operator(payload: dict[str, Any], user: User | None) -> dict[str, Any]:
    if user is None:
        return payload
    user_id = getattr(user, "id", None)
    username = getattr(user, "username", None) or getattr(user, "email", None)
    return market_report_commands.payload_with_force_operator(payload, user_id=user_id, username=username)


def _log_force_audit(
    *,
    action: str,
    package_dir: Path,
    gates: dict[str, Any],
    audit: dict[str, str | None],
    blocked: bool,
) -> None:
    market_report_package_service._log_force_audit(
        logger=logger,
        action=action,
        package_dir=package_dir,
        gates=gates,
        audit=audit,
        blocked=blocked,
        rel_or_abs=_rel_or_abs,
    )


def _enforce_market_package_quality_gate(
    *,
    package_dir: Path,
    payload: dict[str, Any],
    action: str,
) -> None:
    try:
        market_report_package_service.enforce_market_package_quality_gate(
            package_dir=package_dir,
            payload=payload,
            action=action,
            quality_gates_with_load_plan=_quality_gates_with_load_plan,
            rel_or_abs=_rel_or_abs,
            logger=logger,
        )
    except market_report_commands.MarketPackagePlanError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


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
    try:
        return market_report_package_service.run_market_package_build(
            payload=payload,
            executable=sys.executable,
            repo_root=REPO_ROOT,
            market=market,
            market_wiki_roots=MARKET_WIKI_ROOTS,
            market_build_scripts=MARKET_BUILD_SCRIPTS,
            eu_esef_package_build_script=EU_ESEF_PACKAGE_BUILD_SCRIPT,
            safe_download_path=_safe_download_path,
            adjacent_metadata_path=_adjacent_metadata_path,
            run_command=run_command,
            command_for_display=_command_for_display,
            read_market_package_detail=_read_market_package_detail,
            read_us_sec_package_detail=_read_package_detail,
        )
    except market_report_commands.MarketPackageBuildPlanError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _market_build_script(market: str, source_path: Path) -> Path:
    return market_report_commands.select_market_build_script(
        market=market,
        source_path=source_path,
        market_build_scripts=MARKET_BUILD_SCRIPTS,
        eu_esef_package_build_script=EU_ESEF_PACKAGE_BUILD_SCRIPT,
    )


def _market_build_requires_parser_result(market: str, source_path: Path) -> bool:
    return market_report_commands.market_build_requires_parser_result(
        market=market,
        source_path=source_path,
        market_build_scripts=MARKET_BUILD_SCRIPTS,
        eu_esef_package_build_script=EU_ESEF_PACKAGE_BUILD_SCRIPT,
    )


def _market_build_accepts_parser_result(market: str, script: Path) -> bool:
    return market_report_commands.market_build_accepts_parser_result(
        market=market,
        script=script,
        eu_esef_package_build_script=EU_ESEF_PACKAGE_BUILD_SCRIPT,
    )


def _run_market_package_import(payload: dict[str, Any]) -> dict[str, Any]:
    market = _market_code(payload.get("market"))
    try:
        return market_report_package_service.run_market_package_import(
            payload=payload,
            executable=sys.executable,
            repo_root=REPO_ROOT,
            market=market,
            market_databases=MARKET_DATABASES,
            market_import_scripts=MARKET_IMPORT_SCRIPTS,
            safe_market_package_path=_safe_market_package_path,
            quality_gates_with_load_plan=_quality_gates_with_load_plan,
            run_command=run_command,
            command_for_display=_command_for_display,
            rel_or_abs=_rel_or_abs,
            logger=logger,
            base_env=os.environ,
        )
    except market_report_commands.MarketPackagePlanError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _run_market_document_full_import(payload: dict[str, Any]) -> dict[str, Any]:
    market = _market_code(payload.get("market"))
    try:
        return market_report_postgres_service.run_market_document_full_import(
            payload=payload,
            executable=sys.executable,
            repo_root=REPO_ROOT,
            market=market,
            market_document_full_import_scripts=MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS,
            market_document_full_roots=MARKET_DOCUMENT_FULL_ROOTS,
            market_databases=MARKET_DATABASES,
            safe_market_document_full_path=_safe_market_document_full_path,
            run_command=run_command,
            command_for_display=_command_for_display,
            record_pipeline_failure=observability.record_frontend_pipeline_job_failure,
            record_ingestion_duration=observability.record_ingestion_duration,
            base_env=os.environ,
        )
    except market_report_postgres_service.MarketReportPostgresError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _run_market_vector_ingest(payload: dict[str, Any]) -> dict[str, Any]:
    market = _market_code(payload.get("market"))
    try:
        return market_report_package_service.run_market_vector_ingest(
            payload=payload,
            executable=sys.executable,
            repo_root=REPO_ROOT,
            market=market,
            market_vector_collections=MARKET_VECTOR_COLLECTIONS,
            vector_ingest_script=MARKET_VECTOR_INGEST_SCRIPT,
            safe_market_package_path=_safe_market_package_path,
            quality_gates_with_load_plan=_quality_gates_with_load_plan,
            run_command=run_command,
            command_for_display=_command_for_display,
            rel_or_abs=_rel_or_abs,
            logger=logger,
        )
    except market_report_commands.MarketPackagePlanError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _run_market_ingestion_eval(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return market_report_eval_service.run_market_ingestion_eval(
            payload=payload,
            repo_root=REPO_ROOT,
            eval_script=MARKET_INGESTION_EVAL_SCRIPT,
            default_output=MARKET_INGESTION_EVAL_REPORT_PATH,
            default_markdown=MARKET_INGESTION_EVAL_MARKDOWN_PATH,
            executable=sys.executable,
            run_command=run_command,
            command_for_display=_command_for_display,
            read_json_file=_read_json_file,
            rel_or_abs=_rel_or_abs,
        )
    except market_report_eval_service.MarketReportEvalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _safe_package_path(value: str | None) -> Path:
    return market_packages.safe_us_sec_package_path(
        value,
        repo_root=REPO_ROOT,
        us_sec_wiki_root=US_SEC_WIKI_ROOT,
    )


def _latest_case_item_for_ticker(ticker: str) -> dict[str, Any] | None:
    return market_report_package_service.latest_us_sec_case_item_for_ticker(
        ticker,
        case_set_path=US_SEC_CASE_SET_PATH,
        read_json_file=_read_json_file,
    )


def _package_from_selector(payload: dict[str, Any]) -> Path:
    try:
        return market_report_package_service.package_from_us_sec_selector(
            payload,
            latest_case_item_for_ticker=_latest_case_item_for_ticker,
            safe_package_path=_safe_package_path,
        )
    except market_report_package_service.MarketReportPackageError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _read_package_detail(package_dir: Path) -> dict[str, Any]:
    return market_report_status_service.us_sec_package_detail_response(
        package_dir,
        rel_or_abs=_rel_or_abs,
        read_json_file=_read_json_file,
        quality_gates_for_package=_quality_gates_for_package,
    )


def _us_sec_semantic_status_for_case_item(item: dict[str, Any]) -> dict[str, Any]:
    return market_report_package_service.us_sec_semantic_status_for_case_item(
        item,
        safe_package_path=_safe_package_path,
        read_json_file=_read_json_file,
    )


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
    return market_report_commands.safe_filename_part(value)


def _file_suffix_from_content_type(content_type: str | None) -> str:
    return market_report_commands.file_suffix_from_content_type(content_type)


def _us_sec_upload_dir() -> Path:
    target = REPORT_DOWNLOADS_ROOT / "US"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _us_sec_upload_metadata_payload(
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
) -> dict[str, Any]:
    del fiscal_year
    return market_report_commands.us_sec_upload_metadata_payload(
        file_path=file_path,
        original_name=original_name,
        content_type=content_type,
        digest=digest,
        size_bytes=size_bytes,
        ticker=ticker,
        company_name=company_name,
        report_type=report_type,
        report_family=report_family,
        period_end=period_end,
        filing_date=filing_date,
        fallback_published_at=datetime.now(timezone.utc).date().isoformat(),
    )


def _validate_us_sec_upload_files(files: list[UploadFile]) -> None:
    for file in files:
        raw_name = file.filename or "upload"
        if Path(raw_name).suffix.lower() not in US_SEC_UPLOAD_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail="Only PDF, HTML, XHTML, XML, XBRL and ZIP uploads are supported",
            )


def _copy_buffered_upload_to_path(item: BufferedUpload, destination: Path) -> None:
    item.file.seek(0)
    digest = hashlib.sha256()
    size_bytes = 0
    with destination.open("wb") as target:
        while chunk := item.file.read(DEFAULT_CHUNK_BYTES):
            target.write(chunk)
            digest.update(chunk)
            size_bytes += len(chunk)
        target.flush()
        os.fsync(target.fileno())
    if size_bytes != item.size_bytes or digest.hexdigest() != item.sha256:
        raise OSError(f"Buffered upload integrity check failed: {item.filename}")


def _write_us_sec_metadata_temp(path: Path, payload: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as target:
        json.dump(payload, target, ensure_ascii=False, indent=2)
        target.flush()
        os.fsync(target.fileno())


def _new_us_sec_temp_path(parent: Path) -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix=".siq-upload-", dir=parent)
    os.close(descriptor)
    return Path(raw_path)


def _persist_us_sec_upload_batch(
    files: list[BufferedUpload],
    *,
    ticker: str | None,
    company_name: str | None,
    report_type: str | None,
    fiscal_year: int | None,
    period_end: str | None,
    filing_date: str | None,
) -> list[dict[str, Any]]:
    upload_dir = _us_sec_upload_dir()
    now = datetime.now(timezone.utc)
    stamp_part = now.strftime("%Y%m%dT%H%M%SZ")
    report_type_text = str(report_type or "file").strip()
    report_part = _safe_filename_part(report_type_text)
    ticker_part = _safe_filename_part(ticker or "manual")
    if report_type_text.lower() in {"10-k", "20-f", "annual", "annual-report"}:
        folder = "年报"
        report_family = "annual"
    else:
        folder = "财报"
        report_family = "quarterly"
    year_text = str(
        fiscal_year
        or (period_end[:4] if period_end else "")
        or (filing_date[:4] if filing_date else "")
        or now.year
    )

    plans: list[dict[str, Any]] = []
    planned_paths: set[Path] = set()
    for item in files:
        raw_name = item.filename or "upload"
        suffix = Path(raw_name).suffix.lower()
        company_part = _safe_filename_part(company_name or Path(raw_name).stem)
        effective_suffix = _file_suffix_from_content_type(item.content_type) or suffix or ".bin"
        target_dir = upload_dir / company_part / year_text / folder
        target_dir.mkdir(parents=True, exist_ok=True)
        target_name = (
            f"{company_part}_US_{ticker_part}_{report_part}_{stamp_part}_{item.sha256[:10]}{effective_suffix}"
        )
        target_path = target_dir / target_name
        metadata_path = target_path.with_suffix(target_path.suffix + ".metadata.json")
        if target_path in planned_paths or target_path.exists() or metadata_path.exists():
            raise HTTPException(status_code=409, detail="A file with the same generated name already exists")
        planned_paths.add(target_path)
        content_type = item.content_type or _media_type_for_file(target_path)
        metadata = _us_sec_upload_metadata_payload(
            target_path,
            original_name=raw_name,
            content_type=content_type,
            digest=item.sha256,
            size_bytes=item.size_bytes,
            ticker=ticker,
            company_name=company_name,
            report_type=report_type_text,
            report_family=report_family,
            fiscal_year=int(year_text) if year_text.isdigit() else None,
            period_end=period_end,
            filing_date=filing_date,
        )
        plans.append(
            {
                "item": item,
                "target_path": target_path,
                "metadata_path": metadata_path,
                "metadata": metadata,
                "result": {
                    "file_name": target_path.name,
                    "saved_path": str(target_path.resolve()),
                    "size_bytes": item.size_bytes,
                    "content_type": content_type,
                    "cache_hit": False,
                    "deduplicated": False,
                    "content_sha256": item.sha256,
                    "metadata_path": str(metadata_path.resolve()),
                    "relative_path": str(target_path.relative_to(REPORT_DOWNLOADS_ROOT)),
                },
            }
        )

    staged: list[tuple[Path, Path]] = []
    published: list[tuple[Path, Path]] = []
    try:
        for plan in plans:
            data_temp = _new_us_sec_temp_path(plan["target_path"].parent)
            staged.append((data_temp, plan["target_path"]))
            _copy_buffered_upload_to_path(plan["item"], data_temp)

            metadata_temp = _new_us_sec_temp_path(plan["metadata_path"].parent)
            staged.append((metadata_temp, plan["metadata_path"]))
            _write_us_sec_metadata_temp(metadata_temp, plan["metadata"])

        for temporary_path, final_path in staged:
            try:
                os.link(temporary_path, final_path)
            except FileExistsError as exc:
                raise HTTPException(status_code=409, detail="A file with the same generated name already exists") from exc
            published.append((temporary_path, final_path))
        return [plan["result"] for plan in plans]
    except BaseException:
        for temporary_path, final_path in reversed(published):
            try:
                if temporary_path.samefile(final_path):
                    final_path.unlink()
            except FileNotFoundError:
                pass
        raise
    finally:
        for temporary_path, _final_path in staged:
            temporary_path.unlink(missing_ok=True)


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
    _validate_us_sec_upload_files(files)
    upload_fields = {
        "ticker": ticker.strip().upper() or None,
        "company_name": company_name.strip() or None,
        "report_type": report_type.strip() or None,
        "fiscal_year": int(fiscal_year) if str(fiscal_year).strip().isdigit() else None,
        "period_end": period_end.strip() or None,
        "filing_date": filing_date.strip() or None,
    }
    async with UPLOAD_PROXY_LIMITER.slot():
        buffered = await buffer_upload_files(
            files,
            max_files=US_SEC_UPLOAD_MAX_FILES,
            max_file_bytes=US_SEC_UPLOAD_MAX_FILE_BYTES,
            max_batch_bytes=US_SEC_UPLOAD_MAX_BATCH_BYTES,
            default_filename="upload",
            default_content_type="",
            reject_empty=True,
        )
        try:
            uploaded = await run_in_threadpool(
                _persist_us_sec_upload_batch,
                buffered,
                **upload_fields,
            )
        finally:
            close_buffered_uploads(buffered)

    for item, result in zip(files, uploaded, strict=True):
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
    try:
        return market_report_package_service.us_sec_ingest_args_for_payload(
            payload,
            executable=sys.executable,
            ingest_script=US_SEC_INGEST_SCRIPT,
            case_set_path=US_SEC_CASE_SET_PATH,
            report_path=US_SEC_INGEST_REPORT_PATH,
        )
    except market_report_commands.MarketPackagePlanError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _run_us_sec_case_set_ingest(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return market_report_package_service.run_us_sec_case_set_ingest(
            payload,
            executable=sys.executable,
            repo_root=REPO_ROOT,
            ingest_script=US_SEC_INGEST_SCRIPT,
            case_set_path=US_SEC_CASE_SET_PATH,
            report_path=US_SEC_INGEST_REPORT_PATH,
            semantic_prestep=_run_us_sec_semantic_prestep,
            run_command=run_command,
            command_for_display=_command_for_display,
            read_json_file=_read_json_file,
        )
    except market_report_commands.MarketPackagePlanError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except market_report_package_service.MarketReportPackageError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _run_us_sec_rebuild_package(ticker: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return market_report_package_service.run_us_sec_rebuild_package(
            ticker=ticker,
            payload=payload,
            executable=sys.executable,
            repo_root=REPO_ROOT,
            latest_case_item=_latest_case_item_for_ticker,
            safe_package_path=_safe_package_path,
            read_json_file=_read_json_file,
            safe_under=_safe_under,
            package_build_script=US_SEC_PACKAGE_BUILD_SCRIPT,
            output_root=US_SEC_WIKI_ROOT,
            run_command=run_command,
            read_package_detail=_read_package_detail,
        )
    except market_report_commands.MarketPackagePlanError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except market_report_package_service.MarketReportPackageError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


async def _proxy_request(
    *,
    base_url: str,
    upstream_path: str,
    request: Request,
    timeout: float = MARKET_REPORT_PROXY_TIMEOUT,
    service_token: str | None = None,
) -> Response:
    return await market_report_proxy.proxy_request(
        base_url=base_url,
        upstream_path=upstream_path,
        request=request,
        timeout=timeout,
        service_token=service_token,
    )


async def _finder_assist(payload: dict[str, Any]) -> dict[str, Any]:
    return await market_report_proxy.finder_assist(
        report_finder_base=REPORT_FINDER_BASE,
        payload=payload,
        timeout=MARKET_REPORT_PROXY_TIMEOUT,
        service_token=_finder_service_token(),
    )


def _active_llm_provider() -> tuple[str, dict[str, Any] | None]:
    settings = load_llm_settings(include_secrets=True)
    providers = settings.get("providers") or {}
    active = str(settings.get("activeProvider") or "local")
    provider = providers.get(active)
    if isinstance(provider, dict) and provider.get("enabled", True):
        return active, provider
    for fallback_key in ("local", "cloud"):
        fallback = providers.get(fallback_key)
        if isinstance(fallback, dict) and fallback.get("enabled", True):
            return fallback_key, fallback
    return str(active), None


def _llm_semantic_env() -> dict[str, str]:
    env = dict(os.environ)
    provider_key, provider = _active_llm_provider()
    if not isinstance(provider, dict):
        return env
    base_url = str(provider.get("baseUrl") or "").strip()
    model = str(provider.get("model") or "").strip()
    api_key = str(provider.get("apiKey") or "").strip()
    env["SIQ_LLM_SEMANTIC_PROVIDER"] = provider_key
    env["FINSIGHT_LLM_SEMANTIC_PROVIDER"] = provider_key
    if base_url:
        env["SIQ_LLM_SEMANTIC_PROVIDER_BASE_URL"] = base_url
        env["FINSIGHT_LLM_SEMANTIC_PROVIDER_BASE_URL"] = base_url
        env["SIQ_LOCAL_LLM_BASE_URL"] = base_url
        env["FINSIGHT_LOCAL_LLM_BASE_URL"] = base_url
    if model:
        env["SIQ_LLM_SEMANTIC_MODEL"] = model
        env["FINSIGHT_LLM_SEMANTIC_MODEL"] = model
        env["SIQ_LOCAL_LLM_MODEL"] = model
        env["FINSIGHT_LOCAL_LLM_MODEL"] = model
    if api_key:
        env["SIQ_LLM_SEMANTIC_API_KEY"] = api_key
        env["FINSIGHT_LLM_SEMANTIC_API_KEY"] = api_key
        env["SIQ_LOCAL_LLM_API_KEY"] = api_key
        env["FINSIGHT_LOCAL_LLM_API_KEY"] = api_key
    if provider.get("timeoutSeconds"):
        env["SIQ_LLM_SEMANTIC_TIMEOUT"] = str(provider.get("timeoutSeconds"))
        env["FINSIGHT_LLM_SEMANTIC_TIMEOUT"] = str(provider.get("timeoutSeconds"))
    if provider.get("maxTokens"):
        env["SIQ_LLM_SEMANTIC_MAX_TOKENS"] = str(provider.get("maxTokens"))
        env["FINSIGHT_LLM_SEMANTIC_MAX_TOKENS"] = str(provider.get("maxTokens"))
    if provider.get("temperature") is not None:
        env["SIQ_LLM_SEMANTIC_TEMPERATURE"] = str(provider.get("temperature"))
        env["FINSIGHT_LLM_SEMANTIC_TEMPERATURE"] = str(provider.get("temperature"))
    if isinstance(provider.get("chatTemplateKwargs"), dict):
        chat_template_kwargs = json.dumps(provider["chatTemplateKwargs"], ensure_ascii=False)
        env["SIQ_LLM_SEMANTIC_CHAT_TEMPLATE_KWARGS"] = chat_template_kwargs
        env["FINSIGHT_LLM_SEMANTIC_CHAT_TEMPLATE_KWARGS"] = chat_template_kwargs
    hermes_mode = _hermes_mode_for_provider(provider)
    if base_url.startswith("hermes://") or hermes_mode:
        profile = "siq_analysis"
        try:
            profile_config = hermes_profile_config(profile)
        except Exception:
            profile_config = {}
        runs_url = str(profile_config.get("base") or "").rstrip("/")
        model = str(profile_config.get("model") or "").strip()
        env["SIQ_LLM_SEMANTIC_HERMES_PROFILE"] = profile
        env["FINSIGHT_LLM_SEMANTIC_HERMES_PROFILE"] = profile
        if runs_url:
            env["SIQ_LLM_SEMANTIC_HERMES_RUNS_URL"] = runs_url
            env["FINSIGHT_LLM_SEMANTIC_HERMES_RUNS_URL"] = runs_url
        if model:
            env["SIQ_LLM_SEMANTIC_MODEL"] = model
            env["FINSIGHT_LLM_SEMANTIC_MODEL"] = model
        env["SIQ_LLM_SEMANTIC_HERMES_MODE"] = str(hermes_mode or provider.get("hermesMode") or "")
        env["FINSIGHT_LLM_SEMANTIC_HERMES_MODE"] = str(hermes_mode or provider.get("hermesMode") or "")
    return env


def _us_sec_company_dirs_from_payload(payload: dict[str, Any]) -> list[str]:
    return market_report_package_service.us_sec_company_dirs_from_payload(
        payload,
        latest_case_item=_latest_case_item_for_ticker,
        safe_package_path=_safe_package_path,
    )


def _run_us_sec_semantic_prestep(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return market_report_package_service.run_us_sec_semantic_prestep(
        payload,
        executable=sys.executable,
        repo_root=REPO_ROOT,
        rule_semantic_script=MARKET_RULE_SEMANTIC_SCRIPT,
        llm_semantic_script=MARKET_LLM_SEMANTIC_SCRIPT,
        company_dirs_from_payload=_us_sec_company_dirs_from_payload,
        llm_semantic_env=_llm_semantic_env,
        run_command=run_command,
        command_for_display=_command_for_display,
    )


def _extract_json_object(text: str) -> dict[str, Any] | None:
    return market_report_assist_service.extract_json_object(text)


def _compact_assist_candidates(request_payload: dict[str, Any]) -> list[dict[str, Any]]:
    return market_report_assist_service.compact_assist_candidates(request_payload)


def _assist_system_prompt() -> str:
    return market_report_assist_service.assist_system_prompt()


def _assist_user_payload(request_payload: dict[str, Any], base_assist: dict[str, Any]) -> dict[str, Any]:
    return market_report_assist_service.assist_user_payload(request_payload, base_assist)


def _assist_retry_user_payload(request_payload: dict[str, Any], base_assist: dict[str, Any]) -> dict[str, Any]:
    return market_report_assist_service.assist_retry_user_payload(request_payload, base_assist)


def _hermes_mode_for_provider(provider: dict[str, Any]) -> str | None:
    return market_report_assist_service.hermes_mode_for_provider(provider)


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
    headers = {"Content-Type": "application/json"}
    if provider.get("apiKey"):
        headers["Authorization"] = f"Bearer {provider['apiKey']}"

    async def _attempt(user_payload: dict[str, Any], retry_index: int) -> dict[str, Any] | None:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
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
            if retry_index == 1:
                return None
            await asyncio.sleep(0.1)
            return None
        if not isinstance(data, dict):
            return None
        choices = data.get("choices") or []
        if not choices:
            return None
        message = choices[0].get("message") or {}
        return _extract_json_object(str(message.get("content") or choices[0].get("text") or ""))

    parsed = await _attempt(_assist_user_payload(request_payload, base_assist), 0)
    if not parsed:
        parsed = await _attempt(_assist_retry_user_payload(request_payload, base_assist), 1)
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

    async def _attempt(user_payload: dict[str, Any]) -> dict[str, Any] | None:
        prompt = "\n".join(
            [
                _assist_system_prompt(),
                "只返回 JSON，不要输出 Markdown 代码块，不要调用工具，不要访问外部网页。",
                "输入如下：",
                json.dumps(user_payload, ensure_ascii=False),
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
        return _extract_json_object(text)

    parsed = await _attempt(_assist_user_payload(request_payload, base_assist))
    if not parsed:
        parsed = await _attempt(_assist_retry_user_payload(request_payload, base_assist))
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
    return market_report_assist_service.merge_assist(base_assist, llm_assist)


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
    finder_path = f"/v1/{upstream_path}"
    if not _finder_proxy_path_allowed(finder_path, request.method):
        raise HTTPException(status_code=404, detail="Market report finder path is not allowed")
    return await _proxy_request(
        base_url=REPORT_FINDER_BASE,
        upstream_path=finder_path,
        request=request,
        service_token=_finder_service_token(),
    )


@router.get("/markets")
async def market_modules() -> Response:
    return await market_report_proxy.proxy_rules_get(
        market_rules_base=MARKET_RULES_BASE,
        upstream_path="/markets",
        service_token=_market_rules_service_token(),
    )


@router.get("/markets/cn/rules")
async def cn_market_rules() -> Response:
    return await market_report_proxy.proxy_rules_get(
        market_rules_base=MARKET_RULES_BASE,
        upstream_path="/markets/cn/rules",
        service_token=_market_rules_service_token(),
    )


@router.get("/market-report-health")
async def market_report_health() -> dict[str, Any]:
    return await market_report_proxy.market_report_health(
        report_finder_base=REPORT_FINDER_BASE,
        market_rules_base=MARKET_RULES_BASE,
    )


@router.get("/market-reports/packages")
async def list_market_packages(market: str | None = None, q: str = "", limit: int = 80) -> dict[str, Any]:
    return market_report_package_service.market_package_list_payload(
        market=market,
        query=q,
        limit=limit,
        market_wiki_roots=MARKET_WIKI_ROOTS,
        markets_to_search=_markets_to_search,
        iter_market_packages=_iter_market_packages,
        read_market_package_summary=_read_market_package_summary,
        rel_or_abs=_rel_or_abs,
    )


@router.get("/market-reports/package")
async def market_package_detail_by_path(market: str, package_path: str) -> dict[str, Any]:
    return market_report_package_service.market_package_detail_by_path_payload(
        market=market,
        package_path=package_path,
        market_code=_market_code,
        safe_market_package_path=_safe_market_package_path,
        read_market_package_detail=_read_market_package_detail,
    )


@router.get("/market-reports/package/quality")
async def market_package_quality_by_path(market: str, package_path: str) -> dict[str, Any]:
    return market_report_package_service.market_package_quality_payload_for_path(
        market=market,
        package_path=package_path,
        market_code=_market_code,
        safe_market_package_path=_safe_market_package_path,
        rel_or_abs=_rel_or_abs,
        read_json_file=_read_json_file,
        load_plan_for_package=_load_plan_for_package,
        quality_gates_with_load_plan=_quality_gates_with_load_plan,
        include_source_map_summary=True,
    )


@router.get("/market-reports/package-file")
async def market_package_file(market: str, package_path: str, file: str, inline: bool = True) -> Response:
    try:
        target = market_report_package_service.market_package_file_target(
            market=market,
            package_path=package_path,
            file_path=file,
            market_code=_market_code,
            safe_market_package_path=_safe_market_package_path,
            safe_under=_safe_under,
        )
    except market_report_package_service.MarketReportPackageError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
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
    return _run_or_queue_market_report_job(
        wait=wait,
        kind="market-package-build",
        target=lambda: _run_market_package_build(payload),
        created_by=_ops_user,
    )


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
    return _run_or_queue_market_report_job(
        wait=wait,
        kind="eu-market-report-parse",
        target=lambda: _run_market_package_build(payload),
        created_by=_ops_user,
    )


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
    payload = _payload_with_force_operator(payload, _ops_user)
    _enforce_legacy_market_package_import(payload)
    return _run_or_queue_market_report_job(
        wait=wait,
        kind="market-package-import",
        target=lambda: _run_market_package_import(payload),
        created_by=_ops_user,
    )


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
    payload = _payload_with_force_operator(payload, _ops_user)
    return _run_or_queue_market_report_job(
        wait=wait,
        kind="market-vector-ingest",
        target=lambda: _run_market_vector_ingest(payload),
        created_by=_ops_user,
    )


@router.get("/market-reports/document-full/status")
@router.get("/market-reports/document-full/import/status")
async def market_document_full_import_status(
    market: str | None = None,
    parse_run_id: str | None = None,
    filing_id: str | None = None,
    document_full_path: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    try:
        return market_report_postgres_service.market_document_full_import_status(
            market=market,
            parse_run_id=parse_run_id,
            filing_id=filing_id,
            document_full_path=document_full_path,
            task_id=task_id,
            markets_to_search=_markets_to_search,
            document_full_path_keys=_market_document_full_path_keys,
            document_full_roots=MARKET_DOCUMENT_FULL_ROOTS,
            import_scripts=MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS,
            market_databases=MARKET_DATABASES,
            schemas=MARKET_DOCUMENT_FULL_SCHEMAS,
            rel_or_abs=_rel_or_abs,
            db_status_for_market=_market_document_full_db_status,
            record_fact_counts=lambda code, db_status: observability.record_ingestion_fact_counts(
                market=code,
                counts=db_status,
            ),
        )
    except market_report_postgres_service.MarketReportPostgresError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/market-reports/document-full/import")
async def import_market_document_full(
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
    return _run_or_queue_market_report_job(
        wait=wait,
        kind="market-document-full-import",
        target=lambda: _run_market_document_full_import(payload),
        created_by=_ops_user,
    )


@router.get("/market-reports/eval")
async def market_ingestion_eval_report(include_markdown: bool = False) -> dict[str, Any]:
    report = _read_json_file(MARKET_INGESTION_EVAL_REPORT_PATH, {})
    observability.record_wiki_postgres_parity_summary(report if isinstance(report, dict) else None)
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
    return _run_or_queue_market_report_job(
        wait=wait,
        kind="market-ingestion-eval",
        target=lambda: _run_market_ingestion_eval(payload),
        created_by=_ops_user,
    )


@router.get("/market-reports/packages/{filing_id}")
async def market_package_detail_by_filing_id(filing_id: str, market: str | None = None) -> dict[str, Any]:
    return market_report_package_service.market_package_detail_by_filing_id_payload(
        filing_id=filing_id,
        market=market,
        find_market_package_by_filing_id=_find_market_package_by_filing_id,
        read_market_package_detail=_read_market_package_detail,
    )


@router.get("/market-reports/packages/{filing_id}/quality")
async def market_package_quality_by_filing_id(filing_id: str, market: str | None = None) -> dict[str, Any]:
    return market_report_package_service.market_package_quality_payload_for_filing_id(
        filing_id=filing_id,
        market=market,
        find_market_package_by_filing_id=_find_market_package_by_filing_id,
        rel_or_abs=_rel_or_abs,
        read_json_file=_read_json_file,
        load_plan_for_package=_load_plan_for_package,
        quality_gates_with_load_plan=_quality_gates_with_load_plan,
    )


@router.get("/market-reports/evidence/{evidence_id}")
async def market_evidence_detail(
    evidence_id: str,
    market: str | None = None,
    package_path: str | None = None,
) -> dict[str, Any]:
    return market_report_package_service.market_evidence_detail_payload(
        evidence_id=evidence_id,
        market=market,
        package_path=package_path,
        market_code=_market_code,
        safe_market_package_path=_safe_market_package_path,
        find_market_evidence=_find_market_evidence,
        rel_or_abs=_rel_or_abs,
    )


@router.get("/us-sec/case-set")
async def us_sec_case_set_status() -> dict[str, Any]:
    return market_report_package_service.us_sec_case_set_status_payload(
        case_set_path=US_SEC_CASE_SET_PATH,
        ingest_report_path=US_SEC_INGEST_REPORT_PATH,
        read_json_file=_read_json_file,
        semantic_status_for_item=_us_sec_semantic_status_for_case_item,
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
    return _run_or_queue_market_report_job(
        wait=wait,
        kind="us-sec-ingest",
        target=lambda: _run_us_sec_case_set_ingest(payload),
        created_by=_ops_user,
    )


@router.get("/us-sec/packages/{ticker}")
async def us_sec_package_detail(ticker: str) -> dict[str, Any]:
    try:
        return market_report_package_service.us_sec_package_detail_by_ticker_payload(
            ticker,
            latest_case_item_for_ticker=_latest_case_item_for_ticker,
            safe_package_path=_safe_package_path,
            read_package_detail=_read_package_detail,
        )
    except market_report_package_service.MarketReportPackageError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/us-sec/package-file")
async def us_sec_package_file(package_path: str, file: str, inline: bool = True) -> Response:
    package_dir = _safe_package_path(package_path)
    try:
        target = market_report_package_service.package_file_target(
            package_dir=package_dir,
            file_path=file,
            safe_under=_safe_under,
        )
    except market_report_package_service.MarketReportPackageError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
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
    return _run_or_queue_market_report_job(
        wait=wait,
        kind="us-sec-rebuild",
        target=lambda: _run_us_sec_rebuild_package(ticker, payload),
        created_by=_ops_user,
    )


@router.get("/jobs/{job_id}")
async def market_report_job_status(
    job_id: str,
    _ops_user=Depends(require_permission("system.config")),
) -> dict[str, Any]:
    try:
        return market_report_queueing.market_report_job_status(
            job_service=market_report_job_service,
            job_id=job_id,
        )
    except market_report_queueing.MarketReportJobError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
