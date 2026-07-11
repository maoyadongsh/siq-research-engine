import asyncio
import json
import hashlib
import logging
import os
import re
import subprocess
import uuid
import sys
import tempfile
import time
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
from services.hermes_client import collect_run_result, create_run, hermes_profile_config
from services.command_runner import format_command, run_command
from services.job_service import market_report_job_service
from services.llm_settings import load_llm_settings
from services.hermes_model_control import set_all_profile_model_modes
from services import market_report_assist_service
from services import market_report_commands
from services import market_document_identity
from services import market_report_proxy
from services import market_report_queueing
from services import market_report_status_service
from services import market_document_full_postgres_status
from services import observability
from services.market_report_settings import (
    EU_ESEF_PACKAGE_BUILD_SCRIPT,
    MARKET_BUILD_SCRIPTS,
    MARKET_DATABASES,
    MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS,
    MARKET_DOCUMENT_FULL_ROOTS,
    MARKET_INGESTION_EVAL_MARKDOWN_PATH,
    MARKET_INGESTION_EVAL_REPORT_PATH,
    MARKET_INGESTION_EVAL_SCRIPT,
    MARKET_IMPORT_SCRIPTS,
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
from services import market_package_repository as market_packages


router = APIRouter(tags=["market-reports"])
logger = logging.getLogger(__name__)
US_SEC_UPLOAD_SUFFIXES = {".pdf", ".html", ".htm", ".xhtml", ".xml", ".xbrl", ".zip"}
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


def _job_created_by(user: User | None) -> dict[str, Any] | None:
    return market_report_queueing.job_created_by(user)


def _queue_market_report_job(kind: str, target, *, created_by: User | None = None) -> dict[str, Any]:
    return market_report_queueing.queue_market_report_job(
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
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            if value:
                return True
            continue
        if str(value or "").strip().lower() in {"1", "true", "yes", "on"}:
            return True
    return False


def _enforce_legacy_market_package_import(payload: dict[str, Any]) -> None:
    if _truthy_payload_flag(payload, "legacy_package_import", "legacyPackageImport"):
        return
    raise HTTPException(
        status_code=422,
        detail=(
            "Package PostgreSQL import is legacy-only. "
            "Use /market-reports/document-full/import, or pass legacy_package_import=true for an explicit compatibility import."
        ),
    )


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


def _market_package_paths(package_dir: Path) -> dict[str, str]:
    return market_packages.market_package_paths(package_dir)


def _iter_market_packages(market: str) -> list[Path]:
    return market_packages.iter_market_packages(market, MARKET_WIKI_ROOTS)


def _read_market_package_summary(package_dir: Path) -> dict[str, Any]:
    return market_packages.read_market_package_summary(package_dir)


def _read_market_package_detail(package_dir: Path) -> dict[str, Any]:
    return market_packages.read_market_package_detail(package_dir)


def _quality_gates_for_package(package_dir: Path) -> dict[str, Any]:
    return market_packages.build_quality_gates(package_dir)


def _load_plan_for_package(package_dir: Path) -> dict[str, Any]:
    payload = _read_json_file(package_dir / "metrics" / "load_plan.json", {})
    return payload if isinstance(payload, dict) else {}


def _load_plan_summary(load_plan: dict[str, Any]) -> dict[str, Any]:
    return market_report_status_service.load_plan_summary(load_plan)


def _merge_load_plan_decision_into_gates(gates: dict[str, Any], load_plan: dict[str, Any]) -> dict[str, Any]:
    return market_report_status_service.merge_load_plan_decision_into_gates(gates, load_plan)


def _quality_gates_with_load_plan(package_dir: Path) -> dict[str, Any]:
    gates = _quality_gates_for_package(package_dir)
    return _merge_load_plan_decision_into_gates(gates, _load_plan_for_package(package_dir))


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
    logger.info(
        (
            "market package force requested action=%s package=%s operator=%s ticket=%s "
            "reason=%s expires_at=%s one_shot=%s blocked=%s force_allowed=%s "
            "hard_gate_rule_ids=%s soft_gate_rule_ids=%s"
        ),
        action,
        _redact_audit_text(_rel_or_abs(package_dir)),
        _redact_audit_text(audit.get("operator")),
        _redact_audit_text(audit.get("ticket")),
        _redact_audit_text(audit.get("reason")),
        _redact_audit_text(audit.get("expires_at")),
        _redact_audit_text(audit.get("one_shot")),
        blocked,
        bool(gates.get("force_allowed")),
        gates.get("hard_gate_rule_ids") or [],
        gates.get("soft_gate_rule_ids") or [],
    )


def _enforce_market_package_quality_gate(
    *,
    package_dir: Path,
    payload: dict[str, Any],
    action: str,
) -> None:
    gates = _quality_gates_with_load_plan(package_dir)
    try:
        decision = market_report_commands.market_package_quality_gate_decision(
            gates=gates,
            payload=payload,
            action=action,
        )
    except market_report_commands.MarketPackagePlanError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    if decision.audit is not None:
        _log_force_audit(
            action=action,
            package_dir=package_dir,
            gates=gates,
            audit=decision.audit,
            blocked=decision.blocked,
        )
    if decision.error_status_code is not None:
        raise HTTPException(status_code=decision.error_status_code, detail=decision.error_detail)
    if not decision.blocked:
        return


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
        plan = market_report_commands.build_market_package_build_plan(
            payload=payload,
            market=market,
            repo_root=REPO_ROOT,
            market_wiki_roots=MARKET_WIKI_ROOTS,
            market_build_scripts=MARKET_BUILD_SCRIPTS,
            eu_esef_package_build_script=EU_ESEF_PACKAGE_BUILD_SCRIPT,
            safe_download_path=_safe_download_path,
            adjacent_metadata_path=_adjacent_metadata_path,
        )
    except market_report_commands.MarketPackageBuildPlanError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    args = market_report_commands.market_package_build_args(
        executable=sys.executable,
        script=plan.script,
        source_path=plan.source_path,
        output_root=plan.output_root,
        metadata_path=plan.metadata_path,
        parser_result_path=plan.parser_result_path,
        force=plan.force,
    )
    completed = run_command(args, cwd=REPO_ROOT, timeout=900)
    output_lines = (completed.stdout or "").strip().splitlines()
    detail = None
    if completed.returncode == 0 and output_lines:
        package_path = Path(output_lines[-1])
        detail = _read_package_detail(package_path) if plan.market == "US" else _read_market_package_detail(package_path)
    return market_report_commands.market_package_build_result_payload(
        completed=completed,
        package=detail,
        command=_command_for_display(args),
    )


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
    _enforce_legacy_market_package_import(payload)
    market = _market_code(payload.get("market"))
    try:
        plan = market_report_commands.build_market_package_import_plan(
            payload=payload,
            market=market,
            market_import_scripts=MARKET_IMPORT_SCRIPTS,
            safe_market_package_path=_safe_market_package_path,
        )
    except market_report_commands.MarketPackagePlanError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    _enforce_market_package_quality_gate(
        package_dir=plan.package_dir,
        payload=payload,
        action="import",
    )
    args = market_report_commands.market_package_import_args(
        executable=sys.executable,
        script=plan.script,
        market=plan.market,
        package_dir=plan.package_dir,
        payload=payload,
    )
    run_kwargs: dict[str, Any] = {"cwd": REPO_ROOT, "timeout": 900}
    import_env = market_report_commands.market_package_import_env(
        plan.market,
        MARKET_DATABASES,
        base_env=os.environ,
        database_url=payload.get("database_url"),
    )
    if import_env:
        run_kwargs["env"] = import_env
    completed = run_command(args, **run_kwargs)
    return market_report_commands.market_package_import_result_payload(
        completed=completed,
        command=_command_for_display(args),
    )


def _run_market_document_full_import(payload: dict[str, Any]) -> dict[str, Any]:
    market = _market_code(payload.get("market"))
    started = time.perf_counter()
    metric_status = "failure"
    try:
        plan = market_report_commands.build_market_document_full_import_plan(
            payload=payload,
            market=market,
            market_document_full_import_scripts=MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS,
            safe_market_document_full_path=_safe_market_document_full_path,
            repo_root=REPO_ROOT,
            market_document_full_roots=MARKET_DOCUMENT_FULL_ROOTS,
        )
    except market_report_commands.MarketPackagePlanError as exc:
        observability.record_frontend_pipeline_job_failure(
            market=market,
            action="postgres",
            reason=f"plan_error_{exc.status_code}",
        )
        observability.record_ingestion_duration(
            market=market,
            stage="postgres_import",
            status=metric_status,
            duration_seconds=time.perf_counter() - started,
        )
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    try:
        args = market_report_commands.market_document_full_import_args(
            executable=sys.executable,
            script=plan.script,
            market=plan.market,
            document_full_path=plan.document_full_path,
            payload=payload,
        )
        import_env = market_report_commands.market_document_full_import_env(
            market,
            MARKET_DATABASES,
            base_env=os.environ,
            database_url=str(payload.get("database_url") or "").strip() or None,
        )
        run_kwargs: dict[str, Any] = {"cwd": REPO_ROOT, "timeout": 900}
        if import_env:
            run_kwargs["env"] = import_env
        completed = run_command(args, **run_kwargs)
        result = market_report_commands.market_document_full_import_result_payload(
            completed=completed,
            command=_command_for_display(args),
        )
        result["selector"] = dict(plan.selector)
        result["identity"] = {
            "market": plan.identity.market,
            **plan.identity.selector_payload(),
            "path_keys": list(plan.identity.path_keys),
        }
        metric_status = "success" if result.get("ok") else "failure"
        if not result.get("ok"):
            observability.record_frontend_pipeline_job_failure(
                market=plan.market,
                action="postgres",
                reason=f"returncode_{result.get('returncode')}",
            )
        return result
    except Exception:
        observability.record_frontend_pipeline_job_failure(
            market=market,
            action="postgres",
            reason="exception",
        )
        raise
    finally:
        observability.record_ingestion_duration(
            market=market,
            stage="postgres_import",
            status=metric_status,
            duration_seconds=time.perf_counter() - started,
        )


def _run_market_vector_ingest(payload: dict[str, Any]) -> dict[str, Any]:
    market = _market_code(payload.get("market"))
    try:
        plan = market_report_commands.build_market_vector_ingest_plan(
            payload=payload,
            market=market,
            vector_ingest_script=MARKET_VECTOR_INGEST_SCRIPT,
            safe_market_package_path=_safe_market_package_path,
        )
    except market_report_commands.MarketPackagePlanError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    _enforce_market_package_quality_gate(
        package_dir=plan.package_dir,
        payload=payload,
        action="vector_ingest",
    )
    args, _dry_run = market_report_commands.market_vector_ingest_args(
        executable=sys.executable,
        script=plan.script,
        package_dir=plan.package_dir,
        payload=payload,
        market=plan.market,
        market_vector_collections=MARKET_VECTOR_COLLECTIONS,
    )
    completed = run_command(args, cwd=REPO_ROOT, timeout=1800)
    return market_report_commands.market_vector_ingest_result_payload(
        completed=completed,
        dry_run=plan.dry_run,
        command=_command_for_display(args),
    )


def _run_market_ingestion_eval(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        plan = market_report_commands.build_market_ingestion_eval_plan(
            payload=payload,
            eval_script=MARKET_INGESTION_EVAL_SCRIPT,
            repo_root=REPO_ROOT,
            default_output=MARKET_INGESTION_EVAL_REPORT_PATH,
            default_markdown=MARKET_INGESTION_EVAL_MARKDOWN_PATH,
        )
    except market_report_commands.MarketPackagePlanError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    args, _output, _markdown = market_report_commands.market_ingestion_eval_args(
        executable=sys.executable,
        script=plan.script,
        payload={},
        repo_root=REPO_ROOT,
        default_output=plan.output_path,
        default_markdown=plan.markdown_path,
    )
    completed = run_command(args, cwd=REPO_ROOT, timeout=900)
    return market_report_commands.market_ingestion_eval_result_payload(
        completed=completed,
        report=_read_json_file(plan.output_path, {}),
        markdown_path=_rel_or_abs(plan.markdown_path),
        command=_command_for_display(args),
    )


def _safe_package_path(value: str | None) -> Path:
    return market_packages.safe_us_sec_package_path(
        value,
        repo_root=REPO_ROOT,
        us_sec_wiki_root=US_SEC_WIKI_ROOT,
    )


def _latest_case_item_for_ticker(ticker: str) -> dict[str, Any] | None:
    case_set = _read_json_file(US_SEC_CASE_SET_PATH, {})
    return market_report_status_service.latest_case_item_for_ticker(case_set, ticker)


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
    return market_report_status_service.us_sec_package_detail_response(
        package_dir,
        rel_or_abs=_rel_or_abs,
        read_json_file=_read_json_file,
        quality_gates_for_package=_quality_gates_for_package,
    )


def _us_sec_semantic_status_for_case_item(item: dict[str, Any]) -> dict[str, Any]:
    try:
        package_path = str(item.get("package_path") or "")
        if not package_path:
            return {}
        return market_report_status_service.us_sec_semantic_status_for_package(
            _safe_package_path(package_path),
            read_json_file=_read_json_file,
        )
    except Exception:
        return {}


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
    payload = market_report_commands.us_sec_upload_metadata_payload(
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
    try:
        tickers, batch_tag = market_report_commands.normalize_us_sec_ingest_filters(payload)
    except market_report_commands.MarketPackagePlanError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return market_report_commands.us_sec_ingest_args(
        executable=sys.executable,
        script=US_SEC_INGEST_SCRIPT,
        case_set_path=US_SEC_CASE_SET_PATH,
        report_path=US_SEC_INGEST_REPORT_PATH,
        payload={**payload, "milvus": False},
        tickers=tickers,
        batch_tag=batch_tag,
    )


def _run_us_sec_case_set_ingest(payload: dict[str, Any]) -> dict[str, Any]:
    if not US_SEC_INGEST_SCRIPT.is_file():
        raise HTTPException(status_code=404, detail=f"Missing ingest script: {US_SEC_INGEST_SCRIPT}")
    semantic_prestep = _run_us_sec_semantic_prestep(payload)
    args = _safe_ingest_args(payload)
    try:
        completed = run_command(args, cwd=REPO_ROOT, timeout=1800)
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail=f"US SEC ingest timed out: {exc}") from exc
    report = _read_json_file(US_SEC_INGEST_REPORT_PATH, {})
    result = market_report_commands.us_sec_case_set_ingest_result_payload(
        completed=completed,
        report=report,
        command=" ".join(args),
    )
    result["semantic_prestep"] = semantic_prestep
    return result


def _run_us_sec_rebuild_package(ticker: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        plan = market_report_commands.build_us_sec_rebuild_package_plan(
            ticker=ticker,
            latest_case_item=_latest_case_item_for_ticker,
            safe_package_path=_safe_package_path,
            read_json_file=_read_json_file,
            safe_under=_safe_under,
            package_build_script=US_SEC_PACKAGE_BUILD_SCRIPT,
            output_root=US_SEC_WIKI_ROOT,
        )
    except market_report_commands.MarketPackagePlanError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    with tempfile.TemporaryDirectory(prefix="siq-sec-rebuild-") as tmp_dir:
        tmp_source = Path(tmp_dir) / "filing.htm"
        tmp_source.write_bytes(plan.source_path.read_bytes())
        tmp_metadata = None
        if plan.metadata_path is not None:
            tmp_metadata = Path(tmp_dir) / "filing.metadata.json"
            tmp_metadata.write_bytes(plan.metadata_path.read_bytes())
        args = market_report_commands.us_sec_rebuild_package_args(
            executable=sys.executable,
            script=plan.script,
            source_path=tmp_source,
            output_root=plan.output_root,
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
    return market_report_commands.us_sec_rebuild_package_result_payload(
        completed=completed,
        ticker=plan.ticker,
        package=detail,
    )


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
    tickers = str(payload.get("tickers") or "").strip().upper()
    values = [item for item in re.split(r"[,\\s]+", tickers) if item] if tickers else []
    if not values and payload.get("ticker"):
        values = [str(payload.get("ticker") or "").strip().upper()]
    company_dirs: list[str] = []
    for ticker in values:
        item = _latest_case_item_for_ticker(ticker)
        package_path = str((item or {}).get("package_path") or "")
        if not package_path:
            continue
        try:
            package_dir = _safe_package_path(package_path)
        except HTTPException:
            continue
        if package_dir.parent.name == "reports":
            company_dirs.append(package_dir.parent.parent.name)
    return sorted(set(company_dirs))


def _run_us_sec_semantic_prestep(payload: dict[str, Any]) -> list[dict[str, Any]]:
    semantic_requested = bool(payload.get("semantic") or payload.get("llm_semantic") or payload.get("wiki_semantic"))
    if not semantic_requested or payload.get("dry_run", True):
        return []
    if not MARKET_RULE_SEMANTIC_SCRIPT.is_file() or not MARKET_LLM_SEMANTIC_SCRIPT.is_file():
        return [{
            "stage": "us_sec_semantic",
            "status": "skipped",
            "reason": "market semantic scripts missing",
        }]
    company_dirs = _us_sec_company_dirs_from_payload(payload)
    if not company_dirs:
        return [{
            "stage": "us_sec_semantic",
            "status": "skipped",
            "reason": "no company dirs resolved from payload",
        }]
    results = []
    for company_dir in company_dirs:
        rule_args = [
            sys.executable,
            str(MARKET_RULE_SEMANTIC_SCRIPT),
            "--market",
            "US",
            "--company",
            company_dir,
        ]
        llm_args = [
            sys.executable,
            str(MARKET_LLM_SEMANTIC_SCRIPT),
            "--market",
            "US",
            "--company",
            company_dir,
            "--allow-failures",
        ]
        rule_completed = run_command(rule_args, cwd=REPO_ROOT, timeout=900)
        llm_completed = run_command(llm_args, cwd=REPO_ROOT, timeout=1800, env=_llm_semantic_env())
        results.append({
            "companyDir": company_dir,
            "rule": {
                "returncode": rule_completed.returncode,
                "stdout": rule_completed.stdout[-4000:],
                "stderr": rule_completed.stderr[-4000:],
                "command": _command_for_display(rule_args),
            },
            "llm": {
                "returncode": llm_completed.returncode,
                "stdout": llm_completed.stdout[-4000:],
                "stderr": llm_completed.stderr[-4000:],
                "command": _command_for_display(llm_args),
            },
        })
    return results


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
    codes = _markets_to_search(market)
    package_summaries: list[dict[str, Any]] = []
    for code in codes:
        for package_dir in _iter_market_packages(code):
            package_summaries.append(_read_market_package_summary(package_dir))
    return market_report_status_service.market_package_list_payload(
        market_codes=codes,
        package_summaries=package_summaries,
        roots={code: _rel_or_abs(MARKET_WIKI_ROOTS[code]) for code in codes},
        query=q,
        limit=limit,
    )


@router.get("/market-reports/package")
async def market_package_detail_by_path(market: str, package_path: str) -> dict[str, Any]:
    code = _market_code(market)
    return _read_market_package_detail(_safe_market_package_path(code, package_path))


@router.get("/market-reports/package/quality")
async def market_package_quality_by_path(market: str, package_path: str) -> dict[str, Any]:
    code = _market_code(market)
    package_dir = _safe_market_package_path(code, package_path)
    return market_report_status_service.market_package_quality_response(
        package_dir,
        rel_or_abs=_rel_or_abs,
        read_json_file=_read_json_file,
        load_plan_for_package=_load_plan_for_package,
        quality_gates_with_load_plan=_quality_gates_with_load_plan,
        include_source_map_summary=True,
    )


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
    return _queue_market_report_job(
        "market-package-build",
        lambda: _run_market_package_build(payload),
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
    if wait:
        return _run_market_package_build(payload)
    return _queue_market_report_job(
        "eu-market-report-parse",
        lambda: _run_market_package_build(payload),
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
    if wait:
        return _run_market_package_import(payload)
    return _queue_market_report_job(
        "market-package-import",
        lambda: _run_market_package_import(payload),
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
    if wait:
        return _run_market_vector_ingest(payload)
    return _queue_market_report_job(
        "market-vector-ingest",
        lambda: _run_market_vector_ingest(payload),
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
    if document_full_path and not market:
        raise HTTPException(status_code=400, detail="market is required when document_full_path is provided")
    codes = _markets_to_search(market)
    if document_full_path and market:
        for code in codes:
            _market_document_full_path_keys(code, document_full_path)
    return market_report_status_service.market_document_full_status_payload(
        market_codes=codes,
        document_full_roots=MARKET_DOCUMENT_FULL_ROOTS,
        import_scripts=MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS,
        market_databases=MARKET_DATABASES,
        schemas=MARKET_DOCUMENT_FULL_SCHEMAS,
        rel_or_abs=_rel_or_abs,
        db_status_for_market=lambda code: _market_document_full_db_status(
            code,
            parse_run_id=parse_run_id,
            filing_id=filing_id,
            document_full_path=document_full_path,
            task_id=task_id,
        ),
        record_fact_counts=lambda code, db_status: observability.record_ingestion_fact_counts(
            market=code,
            counts=db_status,
        ),
    )


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
    if wait:
        return _run_market_document_full_import(payload)
    return _queue_market_report_job(
        "market-document-full-import",
        lambda: _run_market_document_full_import(payload),
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
    if wait:
        return _run_market_ingestion_eval(payload)
    return _queue_market_report_job(
        "market-ingestion-eval",
        lambda: _run_market_ingestion_eval(payload),
        created_by=_ops_user,
    )


@router.get("/market-reports/packages/{filing_id}")
async def market_package_detail_by_filing_id(filing_id: str, market: str | None = None) -> dict[str, Any]:
    _code, package_dir = _find_market_package_by_filing_id(filing_id, market)
    return _read_market_package_detail(package_dir)


@router.get("/market-reports/packages/{filing_id}/quality")
async def market_package_quality_by_filing_id(filing_id: str, market: str | None = None) -> dict[str, Any]:
    _code, package_dir = _find_market_package_by_filing_id(filing_id, market)
    return market_report_status_service.market_package_quality_response(
        package_dir,
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
    if wait:
        return _run_us_sec_case_set_ingest(payload)
    return _queue_market_report_job(
        "us-sec-ingest",
        lambda: _run_us_sec_case_set_ingest(payload),
        created_by=_ops_user,
    )


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
    return _queue_market_report_job(
        "us-sec-rebuild",
        lambda: _run_us_sec_rebuild_package(ticker, payload),
        created_by=_ops_user,
    )


@router.get("/jobs/{job_id}")
async def market_report_job_status(
    job_id: str,
    _ops_user=Depends(require_permission("system.config")),
) -> dict[str, Any]:
    job = market_report_queueing.get_market_report_job(
        job_service=market_report_job_service,
        job_id=job_id,
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
