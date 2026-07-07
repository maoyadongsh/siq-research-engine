"""Request and task parsing helpers for the PDF parser app."""

from __future__ import annotations

import hmac
import hashlib
import json
import logging
import os
import re

from flask import request

ALLOWED_BACKENDS = {"hybrid-http-client", "pipeline", "vlm-http-client"}
ALLOWED_PARSE_METHODS = {"auto", "txt", "ocr"}
SUPPORTED_MARKETS = {"CN", "HK", "US", "JP", "KR", "EU", "DOC"}
MARKET_TOKEN_RE = re.compile(r"(?:^|[_\W])(CN|HK|US|JP|KR|EU|DOC)(?:[_\W]|$)", re.IGNORECASE)
APP_ACCESS_TOKEN = os.environ.get("PDF2MD_ACCESS_TOKEN", "").strip()
PARSER_CONFIG_VERSION = os.environ.get("SIQ_PDF_PARSE_CONFIG_VERSION", "pdf_parser_v1").strip() or "pdf_parser_v1"
DEFAULT_OWNER_ID = "system"
DEFAULT_TENANT_ID = "unknown"
DEFAULT_MARKET_SCOPE = "unknown"
ADMIN_ROLES = {"admin", "super_admin", "system"}
SCOPE_VALUE_RE = re.compile(r"[^A-Za-z0-9_.@:-]+")
PROFILE_ENV_NAMES = ("SIQ_DEPLOYMENT_PROFILE", "SIQ_ENV", "APP_ENV", "ENVIRONMENT", "FLASK_ENV")
TOKEN_REQUIRED_PROFILES = {"prod", "production", "docker"}
LOCAL_DEV_PROFILES = {"local", "dev", "development"}
LOGGER = logging.getLogger(__name__)
_local_no_token_warning_logged = False


def _profile_values():
    return [os.environ.get(name, "").strip().lower() for name in PROFILE_ENV_NAMES if os.environ.get(name, "").strip()]


def _token_required_profile_enabled():
    return any(value in TOKEN_REQUIRED_PROFILES for value in _profile_values())


def _explicit_local_dev_profile_enabled():
    values = _profile_values()
    return bool(values) and any(value in LOCAL_DEV_PROFILES for value in values)


def _production_profile_enabled():
    return _token_required_profile_enabled()


def _log_local_no_token_warning_once():
    global _local_no_token_warning_logged
    if _local_no_token_warning_logged:
        return
    _local_no_token_warning_logged = True
    LOGGER.warning(
        "PDF parser internal token is not configured because an explicit local/dev profile is active; "
        "X-SIQ identity headers will be ignored unless a valid token is provided."
    )


def _configured_access_token(access_token=None):
    return APP_ACCESS_TOKEN if access_token is None else str(access_token or "").strip()


def _request_token_value():
    return (
        request.headers.get("X-PDF2MD-Token")
        or request.args.get("token")
        or request.cookies.get("pdf2md_token")
    )


def _request_has_valid_token(access_token=None):
    access_token = _configured_access_token(access_token)
    if not access_token:
        return False
    return hmac.compare_digest(str(_request_token_value() or ""), access_token)


def _request_is_authorized(access_token=None):
    access_token = _configured_access_token(access_token)
    if _request_has_valid_token(access_token):
        return True
    if access_token or _token_required_profile_enabled():
        return False
    if _explicit_local_dev_profile_enabled():
        _log_local_no_token_warning_once()
        return True
    return False


if _production_profile_enabled() and not APP_ACCESS_TOKEN:
    raise RuntimeError("PDF2MD_ACCESS_TOKEN is required in production/docker profile.")
if not APP_ACCESS_TOKEN and _explicit_local_dev_profile_enabled():
    _log_local_no_token_warning_once()


def _safe_client_filename(filename):
    name = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = re.sub(r"[\r\n\x00]", "_", name)
    return name or "upload.pdf"


def _safe_download_name(filename):
    name = _safe_client_filename(filename)
    return re.sub(r"[/\\]+", "_", name) or "download.md"


def _parse_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_page_id(value, field_name):
    value = str(value or "").strip()
    if value == "":
        return ""
    try:
        page_id = int(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须是非负整数") from exc
    if page_id < 0:
        raise ValueError(f"{field_name} 必须是非负整数")
    return str(page_id)


def _parse_submit_config(form):
    backend = str(form.get("backend", "hybrid-http-client")).strip()
    parse_method = str(form.get("parse_method", "auto")).strip()
    market = str(form.get("market", "CN")).strip().upper()
    if backend not in ALLOWED_BACKENDS:
        raise ValueError("不支持的后端模式")
    if parse_method not in ALLOWED_PARSE_METHODS:
        raise ValueError("不支持的解析方式")
    if market not in SUPPORTED_MARKETS:
        raise ValueError("不支持的市场类型")

    start_page_id = _parse_page_id(form.get("start_page_id", ""), "起始页码")
    end_page_id = _parse_page_id(form.get("end_page_id", ""), "结束页码")
    if start_page_id != "" and end_page_id != "" and int(start_page_id) > int(end_page_id):
        raise ValueError("起始页码不能大于结束页码")

    return {
        "backend": backend,
        "parse_method": parse_method,
        "market": market,
        "start_page_id": start_page_id,
        "end_page_id": end_page_id,
        "formula_enable": _parse_bool(form.get("formula_enable"), default=True),
        "table_enable": _parse_bool(form.get("table_enable"), default=True),
    }


def _canonical_parse_config(config):
    source = dict(config or {})
    market = _normalize_market(source.get("market")) or "CN"
    return {
        "parser_version": PARSER_CONFIG_VERSION,
        "market": market,
        "backend": str(source.get("backend") or "hybrid-http-client").strip(),
        "parse_method": str(source.get("parse_method") or "auto").strip(),
        "start_page_id": str(source.get("start_page_id") or ""),
        "end_page_id": str(source.get("end_page_id") or ""),
        "formula_enable": bool(source.get("formula_enable", True)),
        "table_enable": bool(source.get("table_enable", True)),
    }


def _parse_config_hash(config):
    canonical = _canonical_parse_config(config)
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_market(value):
    market = str(value or "").strip().upper()
    return market if market in SUPPORTED_MARKETS else None


def _clean_scope_value(value, default):
    text = str(value or "").strip()
    if not text:
        return default
    text = SCOPE_VALUE_RE.sub("_", text)[:120].strip("._:-")
    return text or default


def _request_owner_scope(default_market=None):
    identity_headers_trusted = _request_has_valid_token(APP_ACCESS_TOKEN)
    owner_header = request.headers.get("X-SIQ-User-Id") if identity_headers_trusted else None
    role = _clean_scope_value(request.headers.get("X-SIQ-User-Role"), "") if identity_headers_trusted else ""
    tenant_header = None
    if identity_headers_trusted:
        tenant_header = (
            request.headers.get("X-SIQ-Tenant-Id")
            or request.headers.get("X-SIQ-Tenant-ID")
            or request.headers.get("X-SIQ-Workspace-Id")
            or request.headers.get("X-SIQ-Workspace-ID")
        )
    tenant = _clean_scope_value(tenant_header, DEFAULT_TENANT_ID)
    market = (
        (_normalize_market(request.headers.get("X-SIQ-Market-Scope")) if identity_headers_trusted else None)
        or _normalize_market(default_market)
        or DEFAULT_MARKET_SCOPE
    )
    owner = _clean_scope_value(owner_header, DEFAULT_OWNER_ID)
    role_lower = role.lower()
    allow_legacy_task = False
    if identity_headers_trusted:
        allow_legacy_task = str(request.headers.get("X-SIQ-Allow-Legacy-Task") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    return {
        "owner_id": owner,
        "tenant_id": tenant,
        "market_scope": market,
        "user_role": role,
        "is_admin": role_lower in ADMIN_ROLES,
        "is_legacy_request": not bool(str(owner_header or "").strip()),
        "allow_legacy_task": allow_legacy_task,
    }


def _infer_market_from_text(value):
    text = str(value or "").strip()
    if not text:
        return None
    match = MARKET_TOKEN_RE.search(text)
    if match:
        return match.group(1).upper()
    if re.search(r"[\u4e00-\u9fff]", text):
        return "CN"
    return None


def _task_market_from_record(task):
    if not isinstance(task, dict):
        return None

    submit_config = task.get("submit_config")
    if isinstance(submit_config, dict):
        market = _normalize_market(submit_config.get("market"))
        if market:
            return market

    market = _normalize_market(task.get("market"))
    if market:
        return market

    for key in ("filename", "upload_path", "markdown_path", "task_id"):
        market = _infer_market_from_text(task.get(key))
        if market:
            return market
    return None


def _apply_task_market_fallback(task):
    market = _task_market_from_record(task)
    if not market:
        task["market"] = None
        return task

    task["market"] = market
    submit_config = task.get("submit_config")
    if isinstance(submit_config, dict) and not _normalize_market(submit_config.get("market")):
        submit_config["market"] = market
    return task


def _safe_task_id(value):
    task_id = str(value or "").strip()
    if not task_id:
        return None
    if any(char in task_id for char in "/\\") or task_id in {".", ".."} or len(task_id) > 120:
        raise ValueError("invalid task_id")
    return task_id


def _format_duration(seconds):
    if seconds is None or seconds < 0:
        return "--"
    minutes = int(seconds) // 60
    remainder = int(seconds) % 60
    if minutes > 0:
        return f"{minutes}分{remainder}秒"
    return f"{remainder}秒"
