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
from services import market_report_proxy
from services import market_report_queueing
from services import market_report_status_service
from services.market_report_settings import (
    EU_ESEF_PACKAGE_BUILD_SCRIPT,
    MARKET_BUILD_SCRIPTS,
    MARKET_DATABASES,
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


def _content_type(headers: httpx.Headers) -> str:
    return market_report_proxy.content_type(headers)


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


def _payload_force_enabled(payload: dict[str, Any]) -> bool:
    value = payload.get("force")
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


_FORCE_REASON_KEYS = ("force_reason", "reason", "override_reason")
_FORCE_OPERATOR_KEYS = (
    "force_operator",
    "force_operator_id",
    "operator",
    "operator_id",
    "user",
    "user_id",
    "username",
    "requested_by",
)
_FORCE_TICKET_KEYS = (
    "force_ticket",
    "ticket",
    "change_id",
    "change_request",
    "approval_id",
)
_FORCE_EXPIRES_KEYS = ("force_expires_at", "expires_at", "expiry", "expires")
_FORCE_ONE_SHOT_KEYS = (
    "force_one_shot",
    "one_shot",
    "one_time",
    "one_time_use",
    "one_shot_id",
)


def _force_audit_sources(payload: dict[str, Any]) -> list[dict[str, Any]]:
    sources = [payload]
    for key in ("force_audit", "audit", "override"):
        value = payload.get(key)
        if isinstance(value, dict):
            sources.append(value)
    return sources


def _force_audit_text(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for source in _force_audit_sources(payload):
        for key in keys:
            value = source.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
    return None


def _force_one_shot_marker(payload: dict[str, Any]) -> str | None:
    false_values = {"0", "false", "no", "off", "none", "null"}
    for source in _force_audit_sources(payload):
        for key in _FORCE_ONE_SHOT_KEYS:
            if key not in source:
                continue
            value = source.get(key)
            if isinstance(value, bool):
                if value:
                    return "true"
                continue
            text = str(value or "").strip()
            if text and text.lower() not in false_values:
                return text
    return None


def _redact_audit_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value)
    text = re.sub(r"(?i)(password|passwd|secret|token|api[_-]?key)=\S+", r"\1=[redacted]", text)
    text = re.sub(r"://([^/\s:@]+):([^/\s@]+)@", "://[redacted]@", text)
    return text[:256]


def _validate_force_audit(payload: dict[str, Any]) -> dict[str, str | None]:
    reason = _force_audit_text(payload, _FORCE_REASON_KEYS)
    operator = _force_audit_text(payload, _FORCE_OPERATOR_KEYS)
    ticket = _force_audit_text(payload, _FORCE_TICKET_KEYS)
    expires_at = _force_audit_text(payload, _FORCE_EXPIRES_KEYS)
    one_shot = _force_one_shot_marker(payload)

    missing: list[str] = []
    invalid: list[str] = []
    if not reason:
        missing.append("reason")
    if not operator:
        missing.append("operator")
    if not ticket:
        missing.append("ticket_or_change_id")
    if not expires_at and not one_shot:
        missing.append("expires_at_or_one_shot")
    if expires_at:
        try:
            expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
        except ValueError:
            invalid.append("expires_at")
        else:
            if expires <= datetime.now(timezone.utc):
                invalid.append("expires_at")

    if missing or invalid:
        raise HTTPException(
            status_code=400,
            detail={
                "message": (
                    "force=true requires audit fields: reason, operator/user id, "
                    "ticket/change id, and expires_at or one_shot."
                ),
                "missing_fields": missing,
                "invalid_fields": invalid,
            },
        )

    return {
        "reason": reason,
        "operator": operator,
        "ticket": ticket,
        "expires_at": expires_at,
        "one_shot": one_shot,
    }


def _payload_with_force_operator(payload: dict[str, Any], user: User | None) -> dict[str, Any]:
    if not _payload_force_enabled(payload) or user is None:
        return payload
    if _force_audit_text(payload, _FORCE_OPERATOR_KEYS):
        return payload

    audit = dict(payload.get("force_audit")) if isinstance(payload.get("force_audit"), dict) else {}
    user_id = getattr(user, "id", None)
    username = getattr(user, "username", None) or getattr(user, "email", None)
    if user_id is not None:
        audit["operator_id"] = str(user_id)
    if username:
        audit["operator"] = str(username)
    return {**payload, "force_audit": audit}


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
    gates = _quality_gates_for_package(package_dir)
    blocked_key = "vector_ingest_blocked" if action == "vector_ingest" else "import_blocked"
    blocked = bool(gates.get(blocked_key))
    force_enabled = _payload_force_enabled(payload)
    if force_enabled:
        audit = _validate_force_audit(payload)
        _log_force_audit(
            action=action,
            package_dir=package_dir,
            gates=gates,
            audit=audit,
            blocked=blocked,
        )
    if not blocked:
        return
    if force_enabled and gates.get("force_allowed") is True:
        return
    if force_enabled:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "Quality gates contain hard blocks; force=true cannot run formal "
                    f"{action} and is limited to review/quarantine material."
                ),
                "action": action,
                "quality_gates": gates,
            },
        )
    if gates.get("force_allowed") is True:
        message = (
            "Quality gates block this action; force=true with required audit fields "
            "can request a soft-gate exception."
        )
    else:
        message = (
            "Quality gates contain hard blocks; fix the package or keep it in "
            "review/quarantine material before formal import or vector ingest."
        )
    raise HTTPException(
        status_code=409,
        detail={
            "message": message,
            "action": action,
            "quality_gates": gates,
        },
    )


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
    manifest = _read_json_file(package_dir / "manifest.json", {})
    quality = _read_json_file(package_dir / "qa" / "quality_report.json", {})
    financial_data = _read_json_file(package_dir / "metrics" / "financial_data.json", {})
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
        "quality_gates": _quality_gates_for_package(package_dir),
        "financial_data": financial_data,
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
    return market_report_commands.us_sec_case_set_ingest_result_payload(
        completed=completed,
        report=report,
        command=" ".join(args),
    )


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
) -> Response:
    return await market_report_proxy.proxy_request(
        base_url=base_url,
        upstream_path=upstream_path,
        request=request,
        timeout=timeout,
    )


async def _finder_assist(payload: dict[str, Any]) -> dict[str, Any]:
    return await market_report_proxy.finder_assist(
        report_finder_base=REPORT_FINDER_BASE,
        payload=payload,
        timeout=MARKET_REPORT_PROXY_TIMEOUT,
    )


def _active_llm_provider() -> tuple[str, dict[str, Any] | None]:
    settings = load_llm_settings(include_secrets=True)
    providers = settings.get("providers") or {}
    cloud_provider = providers.get("cloud")
    if (
        isinstance(cloud_provider, dict)
        and cloud_provider.get("enabled", True)
        and (
            _hermes_mode_for_provider(cloud_provider) in {"minimax", "stepfun"}
            or (
                str(cloud_provider.get("baseUrl") or "").strip()
                and not str(cloud_provider.get("baseUrl") or "").strip().startswith("hermes://")
                and str(cloud_provider.get("model") or "").strip()
            )
        )
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


def _assist_retry_user_payload(request_payload: dict[str, Any], base_assist: dict[str, Any]) -> dict[str, Any]:
    payload = _assist_user_payload(request_payload, base_assist)
    payload["retry_hint"] = (
        "上一次增强没有得到可用 JSON。请优先补全 intent，"
        "尤其是把中文境外公司名映射为当地上市主体官方名称与代码；"
        "若没有候选列表，也只返回 intent。"
    )
    return payload


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
    return await market_report_proxy.proxy_rules_get(market_rules_base=MARKET_RULES_BASE, upstream_path="/markets")


@router.get("/markets/cn/rules")
async def cn_market_rules() -> Response:
    return await market_report_proxy.proxy_rules_get(market_rules_base=MARKET_RULES_BASE, upstream_path="/markets/cn/rules")


@router.get("/market-report-health")
async def market_report_health() -> dict[str, Any]:
    return await market_report_proxy.market_report_health(
        report_finder_base=REPORT_FINDER_BASE,
        market_rules_base=MARKET_RULES_BASE,
    )


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
    return market_report_status_service.market_package_quality_payload(
        package_path=_rel_or_abs(package_dir),
        manifest=_read_json_file(package_dir / "manifest.json", {}),
        quality=_read_json_file(package_dir / "qa" / "quality_report.json", {}),
        financial_checks=_read_json_file(package_dir / "metrics" / "financial_checks.json", {}),
        source_map=_read_json_file(package_dir / "qa" / "source_map.json", {}),
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
    return market_report_status_service.market_package_quality_payload(
        package_path=_rel_or_abs(package_dir),
        manifest=_read_json_file(package_dir / "manifest.json", {}),
        quality=_read_json_file(package_dir / "qa" / "quality_report.json", {}),
        financial_checks=_read_json_file(package_dir / "metrics" / "financial_checks.json", {}),
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
