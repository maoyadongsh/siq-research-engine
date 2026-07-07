#!/usr/bin/env python3
"""Generic document parsing service."""

from __future__ import annotations

import hmac
import hashlib
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import uuid
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request as UrlRequest
from urllib.request import urlopen
import zipfile

from flask import Flask, jsonify, request, send_file

from artifacts import artifact_summary, build_artifacts, read_json, write_json
from batch_download_payload import (
    MAX_BATCH_DOWNLOAD_TASKS,
    build_batch_download_manifest,
    requested_batch_download_task_ids,
)
from contracts import (
    ARTIFACT_ALLOWLIST,
    APP_VERSION,
    CANCELLED,
    COMPLETED,
    COMPLETED_WITH_WARNINGS,
    DETECTING_TYPE,
    FAILED,
    ParseConfig,
    SourceFile,
    POSTPROCESSING,
    QUEUED,
    RUNNING,
    TERMINAL_STATUSES,
)
from file_utils import (
    document_kind_for_extension,
    guess_mime_type,
    safe_artifact_path,
    safe_client_filename,
    sha256_file,
    validate_extension,
)
from mineru_import import copy_mineru_images_to_result, parse_mineru_output_dir, rewrite_image_paths_to_result
from mineru_candidates_payload import build_mineru_import_candidates_payload
from page_metadata import load_mineru_page_metadata, merge_layout_page_metadata
from path_config import resolve_app_paths
from provider_router import parse_source
from request_args import parse_int_arg, query_flag_enabled
from source_image_payload import build_source_image_payload, find_figure_by_image_id
from source_page_payload import build_source_page_payload
from status_payload import build_task_status_payload
from table_relations_payload import build_table_relations_response_payload
from providers.simple import (
    _bridge_task_id,
    _json_request as pdf_parser_json_request,
    _pdf_parser_api_base,
    _pdf_parser_headers,
    _pdf_parser_result_dir,
    _result_dir_looks_ready,
    cleanup_pdf_parser_bridge_output,
)
from extraction import list_extraction_templates, run_extraction
from table_merge import TABLE_RELATION_RULESET_VERSION, build_logical_tables, build_table_relations
from task_store import DEFAULT_MARKET_SCOPE, DEFAULT_OWNER_ID, DEFAULT_TENANT_ID, TaskStore, now_iso


BASE_DIR = Path(__file__).resolve().parent
APP_PATHS = resolve_app_paths(BASE_DIR)
PROJECT_ROOT = APP_PATHS["project_root"]
DATA_DIR = APP_PATHS["data_dir"]
UPLOAD_FOLDER = APP_PATHS["uploads"]
RESULTS_FOLDER = APP_PATHS["results"]
OUTPUT_FOLDER = APP_PATHS["output"]
DB_PATH = APP_PATHS["db"]
LOG_DIR = APP_PATHS["logs"]
CACHE_DIR = APP_PATHS["cache"]
MAX_FILE_SIZE = int(os.environ.get("SIQ_DOCUMENT_PARSE_MAX_FILE_MB", "200")) * 1024 * 1024
MAX_FILES_PER_UPLOAD = int(os.environ.get("SIQ_DOCUMENT_PARSE_MAX_FILES_PER_UPLOAD", "50"))
APP_ACCESS_TOKEN = os.environ.get("SIQ_DOCUMENT_PARSER_ACCESS_TOKEN", "").strip()
WORKER_POLL_SECONDS = float(os.environ.get("SIQ_DOCUMENT_PARSE_WORKER_POLL_SECONDS", "0.5"))
WORKER_AUTOSTART = os.environ.get("SIQ_DOCUMENT_PARSE_WORKER_AUTOSTART", "true").lower() not in {"0", "false", "no", "off"}
SUPPORTED_MARKET_SCOPES = {"CN", "HK", "US", "EU", "KR", "JP", "DOC"}
ADMIN_ROLES = {"admin", "super_admin", "system"}
SCOPE_VALUE_RE = re.compile(r"[^A-Za-z0-9_.@:-]+")
PROFILE_ENV_NAMES = ("SIQ_DEPLOYMENT_PROFILE", "SIQ_ENV", "APP_ENV", "ENVIRONMENT", "FLASK_ENV")
TOKEN_REQUIRED_PROFILES = {"prod", "production", "docker"}
LOCAL_DEV_PROFILES = {"local", "dev", "development"}
LOGGER = logging.getLogger(__name__)
_local_no_token_warning_logged = False


def _profile_values() -> list[str]:
    return [os.environ.get(name, "").strip().lower() for name in PROFILE_ENV_NAMES if os.environ.get(name, "").strip()]


def _token_required_profile_enabled() -> bool:
    return any(value in TOKEN_REQUIRED_PROFILES for value in _profile_values())


def _explicit_local_dev_profile_enabled() -> bool:
    values = _profile_values()
    return bool(values) and any(value in LOCAL_DEV_PROFILES for value in values)


def _production_profile_enabled() -> bool:
    return _token_required_profile_enabled()


def _log_local_no_token_warning_once() -> None:
    global _local_no_token_warning_logged
    if _local_no_token_warning_logged:
        return
    _local_no_token_warning_logged = True
    LOGGER.warning(
        "Document parser internal token is not configured because an explicit local/dev profile is active; "
        "X-SIQ identity headers will be ignored unless a valid token is provided."
    )


def _configured_access_token(access_token: str | None = None) -> str:
    return APP_ACCESS_TOKEN if access_token is None else str(access_token or "").strip()


def _request_has_valid_token(access_token: str | None = None) -> bool:
    access_token = _configured_access_token(access_token)
    if not access_token:
        return False
    provided = request.headers.get("X-Document-Parser-Token", "")
    return hmac.compare_digest(provided, access_token)


def _request_is_authorized(access_token: str | None = None) -> bool:
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
    raise RuntimeError("SIQ_DOCUMENT_PARSER_ACCESS_TOKEN is required in production/docker profile.")
if not APP_ACCESS_TOKEN and _explicit_local_dev_profile_enabled():
    _log_local_no_token_warning_once()

for folder in (UPLOAD_FOLDER, RESULTS_FOLDER, OUTPUT_FOLDER, LOG_DIR, CACHE_DIR, DB_PATH.parent):
    folder.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE * MAX_FILES_PER_UPLOAD
store = TaskStore(DB_PATH)
worker_stop_event = threading.Event()
worker_thread: threading.Thread | None = None
worker_lock = threading.Lock()
store.requeue_interrupted_tasks()


@app.before_request
def require_access_token():
    ensure_worker_started()
    if request.path == "/api/health":
        return None
    if not _request_is_authorized(APP_ACCESS_TOKEN):
        return jsonify({"error": "unauthorized"}), 401
    return None


def _parse_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_market_scope(value: object) -> str:
    market = str(value or "").strip().upper()
    return market if market in SUPPORTED_MARKET_SCOPES else ""


def _clean_scope_value(value: object, default: str) -> str:
    text = str(value or "").strip()
    if not text:
        return default
    text = SCOPE_VALUE_RE.sub("_", text)[:120].strip("._:-")
    return text or default


def _request_owner_scope(default_market: object = None) -> dict:
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
    market_scope = (
        (_normalize_market_scope(request.headers.get("X-SIQ-Market-Scope")) if identity_headers_trusted else "")
        or _normalize_market_scope(default_market)
        or DEFAULT_MARKET_SCOPE
    )
    allow_legacy_task = False
    if identity_headers_trusted:
        allow_legacy_task = str(request.headers.get("X-SIQ-Allow-Legacy-Task") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    return {
        "owner_id": _clean_scope_value(owner_header, DEFAULT_OWNER_ID),
        "tenant_id": _clean_scope_value(tenant_header, DEFAULT_TENANT_ID),
        "market_scope": market_scope,
        "user_role": role,
        "is_admin": role.lower() in ADMIN_ROLES,
        "is_legacy_request": not bool(str(owner_header or "").strip()),
        "allow_legacy_task": allow_legacy_task,
    }


def _task_has_legacy_owner(task: dict | None) -> bool:
    if not task:
        return False
    return (
        (task.get("owner_id") or DEFAULT_OWNER_ID) == DEFAULT_OWNER_ID
        and (task.get("tenant_id") or DEFAULT_TENANT_ID) == DEFAULT_TENANT_ID
        and (task.get("market_scope") or DEFAULT_MARKET_SCOPE) == DEFAULT_MARKET_SCOPE
    )


def _scope_can_access_task(task: dict | None, owner_scope: dict | None) -> bool:
    if not task:
        return False
    if not owner_scope or owner_scope.get("is_admin"):
        return True
    if (
        (task.get("owner_id") or DEFAULT_OWNER_ID) == owner_scope.get("owner_id")
        and (task.get("tenant_id") or DEFAULT_TENANT_ID) == owner_scope.get("tenant_id")
    ):
        scope_market = owner_scope.get("market_scope")
        task_market = task.get("market_scope") or DEFAULT_MARKET_SCOPE
        return scope_market in {None, "", DEFAULT_MARKET_SCOPE, task_market} or task_market == DEFAULT_MARKET_SCOPE
    return bool(owner_scope.get("allow_legacy_task") and _task_has_legacy_owner(task))


def _get_visible_task(task_id: str, owner_scope: dict | None = None) -> dict | None:
    scope = owner_scope or _request_owner_scope()
    task = store.get_task(task_id)
    return task if _scope_can_access_task(task, scope) else None


def _market_from_request_payload(form: dict | None = None, payload: dict | None = None) -> str:
    data = dict(form or {})
    data.update(payload or {})
    return _normalize_market_scope(data.get("market") or data.get("market_scope") or data.get("marketScope"))


def _parse_config_hash(config: ParseConfig, market_scope: str) -> str:
    payload = {
        "parser_version": APP_VERSION,
        "market_scope": market_scope or DEFAULT_MARKET_SCOPE,
        "config": config.to_manifest(),
        "data_id": config.data_id,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _parse_config(form: dict | None = None, payload: dict | None = None) -> ParseConfig:
    data = dict(form or {})
    data.update(payload or {})
    extra_formats = data.get("extra_formats") or data.get("extraFormats") or []
    if isinstance(extra_formats, str):
        extra_formats = [item.strip() for item in extra_formats.split(",") if item.strip()]
    return ParseConfig(
        model_version=str(data.get("model_version") or data.get("modelVersion") or "auto"),
        ocr=str(data.get("ocr") or "auto"),
        enable_formula=_parse_bool(data.get("enable_formula", data.get("enableFormula")), True),
        enable_table=_parse_bool(data.get("enable_table", data.get("enableTable")), True),
        language=str(data.get("language") or "auto"),
        page_ranges=str(data.get("page_ranges") or data.get("pageRanges") or ""),
        extra_formats=list(extra_formats or []),
        no_cache=_parse_bool(data.get("no_cache", data.get("noCache")), False),
        data_id=str(data.get("data_id") or data.get("dataId") or ""),
    )


def _task_upload_dir(task_id: str) -> Path:
    return UPLOAD_FOLDER / task_id


def _task_result_dir(task_id: str) -> Path:
    return RESULTS_FOLDER / task_id


def _pdf_parser_upload_task_id(task_id: str) -> str:
    return task_id


def _find_source_pdf(result_dir: Path) -> Path | None:
    for folder in (result_dir / "raw" / "original", result_dir / "raw" / "mineru"):
        if not folder.exists():
            continue
        for candidate in sorted(folder.rglob("*.pdf")):
            if candidate.is_file():
                return candidate
    return None


def _page_preview_path(result_dir: Path, page_number: int) -> Path:
    page_dir = result_dir / "images" / "page_previews"
    page_dir.mkdir(parents=True, exist_ok=True)
    return page_dir / f"page_{int(page_number):04d}.png"


def _ensure_source_page_image(task_id: str, page_number: int) -> Path:
    page_number = int(page_number)
    if page_number <= 0:
        raise ValueError("Invalid page number")
    result_dir = _task_result_dir(task_id)
    image_path = _page_preview_path(result_dir, page_number)
    if image_path.exists() and image_path.stat().st_size > 0:
        return image_path
    source_pdf = _find_source_pdf(result_dir)
    if not source_pdf:
        raise FileNotFoundError("Source PDF is not available for page preview")
    prefix = image_path.with_suffix("")
    subprocess.run(
        [
            "pdftoppm",
            "-f",
            str(page_number),
            "-l",
            str(page_number),
            "-png",
            "-r",
            os.environ.get("SIQ_DOCUMENT_PARSE_PAGE_RENDER_DPI", "144"),
            str(source_pdf),
            str(prefix),
        ],
        check=True,
        capture_output=True,
        timeout=int(os.environ.get("SIQ_DOCUMENT_PARSE_PAGE_RENDER_TIMEOUT", "30")),
    )
    generated = prefix.with_name(f"{prefix.name}-{page_number}").with_suffix(".png")
    if generated.exists() and generated != image_path:
        generated.replace(image_path)
    if not image_path.exists():
        generated_candidates = sorted(image_path.parent.glob(f"{prefix.name}-*.png"))
        if generated_candidates:
            generated_candidates[0].replace(image_path)
    if not image_path.exists() or image_path.stat().st_size <= 0:
        raise FileNotFoundError("PDF page renderer did not generate an image")
    return image_path


def _load_page_metadata(result_dir: Path) -> list[dict]:
    return load_mineru_page_metadata(result_dir / "raw" / "mineru")


def _read_layout_blocks_with_page_metadata(result_dir: Path) -> dict:
    layout_path = result_dir / "layout_blocks.json"
    payload = read_json(layout_path) if layout_path.exists() else {"pages": []}
    return merge_layout_page_metadata(payload, _load_page_metadata(result_dir))


def _table_relations_need_refresh(payload: dict) -> bool:
    return payload.get("ruleset_version") != TABLE_RELATION_RULESET_VERSION


def _rebuild_full_zip(result_dir: Path) -> None:
    zip_path = result_dir / "exports" / "full.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in result_dir.rglob("*"):
            if not path.is_file() or path == zip_path:
                continue
            archive.write(path, path.relative_to(result_dir).as_posix())


def _read_table_relations_payload(task_id: str, result_dir: Path) -> dict:
    path = result_dir / "table_relations.json"
    if not path.exists():
        return {}
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {}
    if not _table_relations_need_refresh(payload):
        return payload

    tables_path = result_dir / "tables.json"
    blocks_path = result_dir / "blocks.json"
    markdown_path = result_dir / "document.md"
    if not tables_path.exists() or not blocks_path.exists() or not markdown_path.exists():
        return payload

    tables_payload = read_json(tables_path)
    blocks_payload = read_json(blocks_path)
    tables = tables_payload.get("physical_tables") or tables_payload.get("tables") or []
    blocks = blocks_payload.get("blocks") or []
    markdown = markdown_path.read_text(encoding="utf-8")
    refreshed = build_table_relations(task_id, tables, blocks=blocks, markdown=markdown)
    write_json(path, refreshed)
    write_json(result_dir / "logical_tables.json", build_logical_tables(task_id, tables, refreshed.get("relations") or []))
    _rebuild_full_zip(result_dir)
    return refreshed


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _allowed_mineru_import_roots() -> list[Path]:
    roots = [DATA_DIR.resolve(), (PROJECT_ROOT / "data").resolve()]
    extra_roots = os.environ.get("SIQ_DOCUMENT_PARSE_IMPORT_ROOTS", "")
    for item in extra_roots.replace(",", os.pathsep).split(os.pathsep):
        text = item.strip()
        if text:
            roots.append(Path(text).expanduser().resolve())
    deduped: list[Path] = []
    for root in roots:
        if root not in deduped:
            deduped.append(root)
    return deduped


def _looks_like_mineru_result_dir(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    has_structured = (path / "content_list.json").exists() or (path / "middle.json").exists()
    has_markdown = (path / "result.md").exists() or (path / "result_complete.md").exists()
    return has_structured and has_markdown


def _resolve_mineru_import_dir(raw_path: str) -> Path:
    if not raw_path or not raw_path.strip():
        raise ValueError("source_dir is required")
    source_dir = Path(raw_path.strip()).expanduser()
    if not source_dir.is_absolute():
        source_dir = PROJECT_ROOT / source_dir
    source_dir = source_dir.resolve()
    allowed_roots = _allowed_mineru_import_roots()
    if not any(_path_is_relative_to(source_dir, root) for root in allowed_roots):
        raise ValueError("source_dir is outside allowed import roots")
    if not _looks_like_mineru_result_dir(source_dir):
        raise ValueError("source_dir does not look like a MinerU output directory")
    return source_dir


def _safe_task_id(value: str | None = None) -> str:
    if not value:
        return str(uuid.uuid4())
    task_id = str(value).strip()
    if not task_id or any(char in task_id for char in "/\\"):
        raise ValueError("invalid task_id")
    if task_id in {".", ".."} or len(task_id) > 120:
        raise ValueError("invalid task_id")
    return task_id


def _save_upload(task_id: str, file_storage) -> SourceFile:
    filename = safe_client_filename(file_storage.filename)
    extension = validate_extension(filename)
    task_upload_dir = _task_upload_dir(task_id)
    task_upload_dir.mkdir(parents=True, exist_ok=True)
    path = task_upload_dir / filename
    file_storage.save(path)
    size = path.stat().st_size
    if size > MAX_FILE_SIZE:
        path.unlink(missing_ok=True)
        raise ValueError(f"File exceeds max size: {filename}")
    return SourceFile(
        path=path,
        filename=filename,
        mime_type=file_storage.mimetype or guess_mime_type(filename),
        extension=extension,
        file_size=size,
        sha256=sha256_file(path),
        source_type="upload",
    )


def _is_public_hostname(hostname: str) -> bool:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip_text = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            return False
    return True


def _download_url(task_id: str, url: str) -> SourceFile:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only http/https URLs are supported")
    if not _is_public_hostname(parsed.hostname):
        raise ValueError("URL host is not allowed")
    filename = safe_client_filename(Path(parsed.path).name or "web-document.html")
    if "." not in filename:
        filename += ".html"
    extension = validate_extension(filename)
    task_upload_dir = _task_upload_dir(task_id)
    task_upload_dir.mkdir(parents=True, exist_ok=True)
    path = task_upload_dir / filename
    req = UrlRequest(url, headers={"User-Agent": f"SIQDocumentParser/{APP_VERSION}"})
    size = 0
    with urlopen(req, timeout=30) as response, path.open("wb") as outfile:  # nosec - URL is validated above.
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_FILE_SIZE:
                raise ValueError("Downloaded URL exceeds max size")
            outfile.write(chunk)
        content_type = response.headers.get("content-type") or guess_mime_type(filename)
    return SourceFile(
        path=path,
        filename=filename,
        mime_type=content_type,
        extension=extension,
        file_size=path.stat().st_size,
        sha256=sha256_file(path),
        source_type="url",
        source_url=url,
    )


def _source_file_from_task(task: dict) -> SourceFile:
    upload_dir = _task_upload_dir(str(task["task_id"]))
    files = [path for path in upload_dir.iterdir() if path.is_file()] if upload_dir.exists() else []
    if not files:
        raise FileNotFoundError("source file is missing")
    source_path = files[0]
    filename = safe_client_filename(task.get("filename") or source_path.name)
    return SourceFile(
        path=source_path,
        filename=filename,
        mime_type=str(task.get("mime_type") or guess_mime_type(filename)),
        extension=validate_extension(filename),
        file_size=int(task.get("file_size") or source_path.stat().st_size),
        sha256=str(task.get("file_sha256") or sha256_file(source_path)),
        source_type=str(task.get("source_type") or "upload"),
        source_url=str(task.get("source_url") or ""),
    )


def _create_task_record(
    task_id: str,
    source: SourceFile,
    config: ParseConfig,
    document_kind: str,
    *,
    owner_scope: dict | None = None,
    market_scope: str | None = None,
) -> dict:
    scope = owner_scope or _request_owner_scope(default_market=market_scope)
    task_market_scope = market_scope or scope.get("market_scope") or DEFAULT_MARKET_SCOPE
    store.create_task(
        {
            "task_id": task_id,
            "filename": source.filename,
            "owner_id": scope.get("owner_id") or DEFAULT_OWNER_ID,
            "tenant_id": scope.get("tenant_id") or DEFAULT_TENANT_ID,
            "market_scope": task_market_scope,
            "parse_config_hash": _parse_config_hash(config, task_market_scope),
            "document_kind": document_kind,
            "source_type": source.source_type,
            "source_url": source.source_url,
            "status": QUEUED,
            "stage": QUEUED,
            "progress_percent": 0,
            "file_size": source.file_size,
            "file_sha256": source.sha256,
            "mime_type": source.mime_type,
            "config": config.to_manifest(),
        }
    )
    store.add_log(task_id, f"已接收文档并进入解析队列: {source.filename}")
    return store.get_task(task_id) or {"task_id": task_id, "status": QUEUED}


def _enqueue_task(
    source: SourceFile,
    config: ParseConfig,
    task_id: str | None = None,
    *,
    owner_scope: dict | None = None,
    market_scope: str | None = None,
) -> dict:
    task_id = task_id or str(uuid.uuid4())
    document_kind = document_kind_for_extension(source.extension)
    return _create_task_record(
        task_id,
        source,
        config,
        document_kind,
        owner_scope=owner_scope,
        market_scope=market_scope,
    )


def _progress_from_upstream_status(status: dict) -> int:
    raw_progress = status.get("progress_percent")
    if raw_progress is not None:
        try:
            return max(0, min(99, int(float(raw_progress))))
        except (TypeError, ValueError):
            pass
    total = int(status.get("total_pages") or 0)
    processed = int(status.get("processed_pages") or 0)
    if total > 0 and processed >= 0:
        return max(0, min(99, int((processed / total) * 100)))
    value = str(status.get("status") or status.get("stage") or "").lower()
    if value in {"submitted", "pending"}:
        return 5
    if value == "processing":
        return 8
    return 40


def _sync_pdf_bridge_status(task_id: str, status: dict) -> None:
    upstream_status = str(status.get("status") or status.get("stage") or "running").lower()
    upstream_task_id = str(status.get("task_id") or status.get("upstream_task_id") or "")
    previous = store.get_task(task_id) or {}
    previous_upstream_status = str(previous.get("upstream_status") or "")
    progress = _progress_from_upstream_status(status)
    update_fields = {
        "status": RUNNING,
        "stage": upstream_status,
        "progress_percent": progress,
        "parser_provider": previous.get("parser_provider") or "pdf_parser_bridge",
        "upstream_task_id": upstream_task_id,
        "upstream_status": upstream_status,
        "queue_position": status.get("queue_position"),
        "local_queue_position": status.get("local_queue_position"),
        "elapsed_seconds": status.get("elapsed_seconds"),
        "total_pages": status.get("total_pages"),
        "processed_pages": status.get("processed_pages"),
        "error": "",
    }
    store.update_task_unless_cancelled(task_id, **update_fields)

    if upstream_status != previous_upstream_status:
        labels = {
            "submitted": "已提交到 PDF 解析器",
            "pending": "PDF 解析器排队中",
            "processing": "PDF 解析器正在解析",
            "completed": "PDF 解析器已完成，正在整理文档产物",
        }
        store.add_log(task_id, labels.get(upstream_status, f"PDF 解析器状态: {upstream_status}"))
        return

    elapsed = status.get("elapsed_seconds")
    total = status.get("total_pages")
    processed = status.get("processed_pages")
    if upstream_status == "processing" and total and processed is not None:
        previous_processed = previous.get("processed_pages")
        try:
            should_log = int(processed) != int(previous_processed or -1) and int(processed) % 5 == 0
        except (TypeError, ValueError):
            should_log = False
        if should_log:
            store.add_log(
                task_id,
                f"处理中... 已耗时 {elapsed or 0} 秒，已完成 {processed}/{total} 页",
            )


def _finalize_from_pdf_bridge_result(task: dict, upstream_task_id: str, config: ParseConfig) -> dict | None:
    task_id = str(task["task_id"])
    source_dir = _pdf_parser_result_dir(upstream_task_id)
    if not source_dir.is_dir() or not (source_dir / "document_full.json").is_file():
        return None
    try:
        source = _source_file_from_task(task)
    except Exception:
        source, _ = parse_mineru_output_dir(task_id, source_dir, config)
    _source_file, output = parse_mineru_output_dir(task_id, source_dir, config)
    rewrite_image_paths_to_result(output)
    output.provider_name = output.provider_name or "pdf_parser_bridge"
    output.upstream_task_id = upstream_task_id
    manifest = build_artifacts(
        task_id=task_id,
        result_dir=_task_result_dir(task_id),
        source=source,
        config=config,
        output=output,
        source_type=source.source_type,
        source_url=source.source_url,
    )
    cleanup_message = cleanup_pdf_parser_bridge_output(output)
    if cleanup_message:
        store.add_log(task_id, cleanup_message)
    artifacts = artifact_summary(task_id, _task_result_dir(task_id))
    status = COMPLETED if manifest.get("quality_status") == "pass" else COMPLETED_WITH_WARNINGS
    store.update_task(
        task_id,
        status=status,
        stage=status,
        progress_percent=100,
        parser_provider=manifest.get("parser_provider", ""),
        upstream_task_id=upstream_task_id,
        upstream_status=COMPLETED,
        quality_status=manifest.get("quality_status", ""),
        artifact_count=sum(1 for item in artifacts.values() if item.get("exists")),
        error="",
        completed_at=now_iso(),
    )
    store.add_log(task_id, "已从仍在运行的 PDF bridge 任务补生成文档解析产物")
    return store.get_task(task_id)


def _recover_pdf_bridge_task(task: dict) -> dict:
    task_id = str(task.get("task_id") or "")
    if not task_id or str(task.get("document_kind") or "") not in {"pdf", "image", "word", "ppt", "excel"}:
        return task
    upstream_task_id = str(task.get("upstream_task_id") or "") or _bridge_task_id(task_id)
    config = _parse_config(payload=task.get("config") or {})
    if _result_dir_looks_ready(_pdf_parser_result_dir(upstream_task_id)):
        return _finalize_from_pdf_bridge_result(task, upstream_task_id, config) or task

    status = pdf_parser_json_request(
        f"{_pdf_parser_api_base()}/api/status/{upstream_task_id}",
        headers=_pdf_parser_headers(),
        timeout=15,
    )
    if _result_dir_looks_ready(_pdf_parser_result_dir(upstream_task_id)):
        return _finalize_from_pdf_bridge_result(task, upstream_task_id, config) or task
    if status.get("_error"):
        return task
    status = {**status, "task_id": upstream_task_id}
    upstream_status = str(status.get("status") or "").lower()
    if upstream_status in {COMPLETED, "completed_with_warnings"}:
        return _finalize_from_pdf_bridge_result(task, upstream_task_id, config) or task
    if upstream_status in {"queued", "uploaded", "submitting", "submitted", "pending", "processing"}:
        _sync_pdf_bridge_status(task_id, status)
        return store.get_task(task_id) or task
    return task


def _process_task(task_id: str, source: SourceFile, config: ParseConfig, document_kind: str | None = None) -> dict:
    document_kind = document_kind or document_kind_for_extension(source.extension)
    try:
        if not store.update_task_unless_cancelled(task_id, status=DETECTING_TYPE, stage=DETECTING_TYPE, progress_percent=15, document_kind=document_kind):
            store.add_log(task_id, "任务已取消，跳过解析")
            return store.get_task(task_id) or {"task_id": task_id, "status": CANCELLED}
        store.add_log(task_id, f"识别文件类型: {document_kind}")
        if not store.update_task_unless_cancelled(task_id, status=RUNNING, stage=RUNNING, progress_percent=40):
            store.add_log(task_id, "任务已取消，跳过解析")
            return store.get_task(task_id) or {"task_id": task_id, "status": CANCELLED}
        output = parse_source(task_id, source, config, document_kind, on_status=lambda payload: _sync_pdf_bridge_status(task_id, payload))
        store.add_log(task_id, f"解析 provider: {output.provider_name}")
        if (store.get_task(task_id) or {}).get("status") == CANCELLED:
            store.add_log(task_id, "任务已取消，跳过产物生成")
            return store.get_task(task_id) or {"task_id": task_id, "status": CANCELLED}
        if not store.update_task_unless_cancelled(task_id, status=POSTPROCESSING, stage=POSTPROCESSING, progress_percent=82, parser_provider=output.provider_name):
            store.add_log(task_id, "任务已取消，跳过产物生成")
            return store.get_task(task_id) or {"task_id": task_id, "status": CANCELLED}
        manifest = build_artifacts(
            task_id=task_id,
            result_dir=_task_result_dir(task_id),
            source=source,
            config=config,
            output=output,
            source_type=source.source_type,
            source_url=source.source_url,
        )
        cleanup_message = cleanup_pdf_parser_bridge_output(output)
        if cleanup_message:
            store.add_log(task_id, cleanup_message)
        status = COMPLETED if manifest.get("quality_status") == "pass" else COMPLETED_WITH_WARNINGS
        artifacts = artifact_summary(task_id, _task_result_dir(task_id))
        store.update_task_unless_cancelled(
            task_id,
            status=status,
            stage=status,
            progress_percent=100,
            parser_provider=manifest.get("parser_provider", ""),
            upstream_task_id=output.upstream_task_id,
            upstream_status=COMPLETED,
            quality_status=manifest.get("quality_status", ""),
            artifact_count=sum(1 for item in artifacts.values() if item.get("exists")),
            completed_at=now_iso(),
        )
        store.add_log(task_id, "解析产物已生成")
    except Exception as exc:
        store.update_task(task_id, status=FAILED, stage=FAILED, progress_percent=0, error=str(exc), completed_at=now_iso())
        store.add_log(task_id, f"解析失败: {exc}", level="error")
    return store.get_task(task_id) or {"task_id": task_id, "status": FAILED}


def _import_mineru_result_dir(
    task_id: str,
    source_dir: Path,
    config: ParseConfig | None = None,
    *,
    owner_scope: dict | None = None,
    market_scope: str | None = None,
) -> dict:
    config = config or ParseConfig()
    scope = owner_scope or _request_owner_scope(default_market=market_scope)
    task_market_scope = market_scope or scope.get("market_scope") or DEFAULT_MARKET_SCOPE
    source, output = parse_mineru_output_dir(task_id, source_dir, config)
    result_dir = _task_result_dir(task_id)
    rewrite_image_paths_to_result(output)
    if store.get_task(task_id):
        raise ValueError("task_id already exists")
    store.create_task(
        {
            "task_id": task_id,
            "filename": source.filename,
            "owner_id": scope.get("owner_id") or DEFAULT_OWNER_ID,
            "tenant_id": scope.get("tenant_id") or DEFAULT_TENANT_ID,
            "market_scope": task_market_scope,
            "parse_config_hash": _parse_config_hash(config, task_market_scope),
            "document_kind": "pdf",
            "source_type": "mineru_import",
            "source_url": str(source_dir),
            "status": POSTPROCESSING,
            "stage": POSTPROCESSING,
            "progress_percent": 80,
            "file_size": source.file_size,
            "file_sha256": source.sha256,
            "mime_type": source.mime_type,
            "config": config.to_manifest(),
        }
    )
    store.add_log(task_id, f"导入已有 MinerU 产物目录: {source_dir}")
    copy_mineru_images_to_result(source_dir, result_dir)
    manifest = build_artifacts(
        task_id=task_id,
        result_dir=result_dir,
        source=source,
        config=config,
        output=output,
        source_type="mineru_import",
        source_url=str(source_dir),
    )
    artifacts = artifact_summary(task_id, result_dir)
    status = COMPLETED if manifest.get("quality_status") == "pass" else COMPLETED_WITH_WARNINGS
    store.update_task(
        task_id,
        status=status,
        stage=status,
        progress_percent=100,
        parser_provider=manifest.get("parser_provider", ""),
        quality_status=manifest.get("quality_status", ""),
        artifact_count=sum(1 for item in artifacts.values() if item.get("exists")),
        completed_at=now_iso(),
    )
    store.add_log(task_id, "已有 MinerU 产物已归一为通用文档产物")
    return store.get_task(task_id) or {"task_id": task_id, "status": status}


def _worker_loop() -> None:
    while not worker_stop_event.is_set():
        task = store.claim_next_queued_task()
        if not task:
            worker_stop_event.wait(WORKER_POLL_SECONDS)
            continue
        task_id = str(task["task_id"])
        try:
            source = _source_file_from_task(task)
            config = _parse_config(payload=task.get("config") or {})
            store.add_log(task_id, "后台 worker 开始解析")
            _process_task(task_id, source, config, document_kind=str(task.get("document_kind") or "") or None)
        except Exception as exc:
            store.update_task(task_id, status=FAILED, stage=FAILED, progress_percent=0, error=str(exc), completed_at=now_iso())
            store.add_log(task_id, f"后台 worker 处理失败: {exc}", level="error")


def ensure_worker_started() -> None:
    global worker_thread
    if not WORKER_AUTOSTART:
        return
    with worker_lock:
        if worker_thread and worker_thread.is_alive():
            return
        worker_stop_event.clear()
        worker_thread = threading.Thread(target=_worker_loop, name="document-parser-worker", daemon=True)
        worker_thread.start()


def stop_worker(timeout: float = 2.0) -> None:
    worker_stop_event.set()
    if worker_thread and worker_thread.is_alive():
        worker_thread.join(timeout=timeout)


def _mineru_candidate_entry(path: Path) -> dict[str, object]:
    result_md = path / "result_complete.md"
    if not result_md.exists():
        result_md = path / "result.md"
    content_list = path / "content_list.json"
    middle = path / "middle.json"
    mtime = max(
        candidate.stat().st_mtime
        for candidate in (result_md, content_list, middle)
        if candidate.exists()
    )
    title = path.name
    if result_md.exists():
        first_line = result_md.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
        if first_line:
            title = first_line[0].strip("# ").strip() or title
    return {
        "source_dir": str(path),
        "title": title,
        "result_markdown": result_md.name if result_md.exists() else "",
        "has_content_list": content_list.exists(),
        "has_middle": middle.exists(),
        "updated_at": int(mtime),
    }


def _list_mineru_import_candidates(limit: int = 50) -> list[dict[str, object]]:
    limit = max(1, min(limit, 200))
    candidates: list[dict[str, object]] = []
    seen: set[Path] = set()

    def visit_marker(marker_path: Path) -> bool:
        source_dir = marker_path.parent.resolve()
        if source_dir in seen or not _looks_like_mineru_result_dir(source_dir):
            return False
        seen.add(source_dir)
        try:
            candidates.append(_mineru_candidate_entry(source_dir))
        except OSError:
            return False
        return len(candidates) >= limit * 2

    for root in _allowed_mineru_import_roots():
        if not root.exists():
            continue
        for marker_name in ("content_list.json", "middle.json"):
            try:
                marker_iter = root.rglob(marker_name)
                for marker_path in marker_iter:
                    if visit_marker(marker_path):
                        break
            except OSError:
                continue
            if len(candidates) >= limit * 2:
                break
        if len(candidates) >= limit * 2:
            break
    candidates.sort(key=lambda item: int(item.get("updated_at") or 0), reverse=True)
    return candidates[:limit]


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "version": APP_VERSION,
        "data_dir": str(DATA_DIR),
        "providers": {
            "pdf_parser_bridge": True,
            "mineru_import": True,
            "simple_text_parser": True,
            "html_reader": True,
            "image_to_pdf_bridge": True,
            "office_to_pdf_bridge": True,
            "spreadsheet_to_pdf_bridge": True,
            "pypdf_text_parser": False,
            "spreadsheet_parser": False,
            "office_local": False,
            "image_local": False,
            "local_mineru_pdf": False,
            "archives_to_document_parser": True,
            "cloud_mineru_enabled": os.environ.get("SIQ_DOCUMENT_PARSE_CLOUD_ENABLED", "false").lower() in {"1", "true", "yes"},
        },
        "parser_engine": {
            "service": "apps/pdf-parser",
            "provider": "MinerU/PDF bridge",
            "final_artifact_root": str(RESULTS_FOLDER),
            "temporary_upstream_results": "data/pdf-parser/results/doc-<task_id>",
        },
        "conversion_pipeline": {
            "pdf": "pdf_parser_bridge",
            "image": "image_to_pdf -> pdf_parser_bridge",
            "word": "libreoffice_to_pdf -> pdf_parser_bridge",
            "ppt": "libreoffice_to_pdf -> pdf_parser_bridge",
            "excel": "libreoffice_to_pdf -> pdf_parser_bridge",
        },
        "max_file_mb": MAX_FILE_SIZE // 1024 // 1024,
        "max_files_per_upload": MAX_FILES_PER_UPLOAD,
    }


@app.route("/api/tasks", methods=["POST"])
def create_tasks():
    tasks = []
    config = _parse_config(request.form)
    market_scope = _market_from_request_payload(request.form)
    owner_scope = _request_owner_scope(default_market=market_scope)
    task_market_scope = owner_scope.get("market_scope") or market_scope or DEFAULT_MARKET_SCOPE
    files = request.files.getlist("files")
    if files:
        if len(files) > MAX_FILES_PER_UPLOAD:
            return jsonify({"error": "too_many_files", "message": f"一次最多上传 {MAX_FILES_PER_UPLOAD} 个文件"}), 400
        for file_storage in files:
            task_id = str(uuid.uuid4())
            source = _save_upload(task_id, file_storage)
            tasks.append(_enqueue_task(source, config, task_id=task_id, owner_scope=owner_scope, market_scope=task_market_scope))
        return jsonify({"tasks": tasks})

    payload = request.get_json(silent=True) or {}
    if str(payload.get("source_type") or payload.get("sourceType") or "").lower() == "url" or payload.get("url"):
        config = _parse_config(payload=payload)
        market_scope = _market_from_request_payload(payload=payload)
        owner_scope = _request_owner_scope(default_market=market_scope)
        task_market_scope = owner_scope.get("market_scope") or market_scope or DEFAULT_MARKET_SCOPE
        task_id = str(uuid.uuid4())
        source = _download_url(task_id, str(payload.get("url") or "").strip())
        tasks.append(_enqueue_task(source, config, task_id=task_id, owner_scope=owner_scope, market_scope=task_market_scope))
        return jsonify({"tasks": tasks})

    return jsonify({"error": "no_source", "message": "请上传文件或提供 URL"}), 400


@app.post("/api/import/mineru")
def import_mineru_result():
    payload = request.get_json(silent=True) or {}
    try:
        source_dir = _resolve_mineru_import_dir(str(payload.get("source_dir") or payload.get("sourceDir") or ""))
        task_id = _safe_task_id(payload.get("task_id") or payload.get("taskId"))
        config = _parse_config(payload=payload)
        market_scope = _market_from_request_payload(payload=payload)
        owner_scope = _request_owner_scope(default_market=market_scope)
        task = _import_mineru_result_dir(
            task_id,
            source_dir,
            config=config,
            owner_scope=owner_scope,
            market_scope=owner_scope.get("market_scope") or market_scope or DEFAULT_MARKET_SCOPE,
        )
        return jsonify({"task": task})
    except ValueError as exc:
        return jsonify({"error": "invalid_mineru_import", "message": str(exc)}), 400


@app.get("/api/import/mineru/candidates")
def mineru_import_candidates():
    limit = parse_int_arg(request.args, "limit", 50, invalid_default=50)
    return jsonify(build_mineru_import_candidates_payload(
        _allowed_mineru_import_roots(),
        _list_mineru_import_candidates(limit=limit),
    ))


@app.get("/api/tasks")
def list_tasks():
    limit = parse_int_arg(request.args, "limit", 200)
    tasks = []
    for task in store.list_tasks(limit=limit, owner_scope=_request_owner_scope()):
        if task.get("status") == FAILED:
            task = _recover_pdf_bridge_task(task)
        tasks.append(task)
    return jsonify({"tasks": tasks})


@app.get("/api/tasks/<task_id>")
def get_task(task_id: str):
    task = _get_visible_task(task_id)
    if not task:
        return jsonify({"error": "not_found"}), 404
    return jsonify(task)


@app.get("/api/status/<task_id>")
def task_status(task_id: str):
    task = _get_visible_task(task_id)
    if not task:
        return jsonify({"error": "not_found"}), 404
    if task.get("status") == FAILED:
        task = _recover_pdf_bridge_task(task)
    logs, log_count = store.get_logs(task_id, since=parse_int_arg(request.args, "since", 0))
    return jsonify(build_task_status_payload(task, logs, log_count))


@app.post("/api/cancel/<task_id>")
def cancel_task(task_id: str):
    task = _get_visible_task(task_id)
    if not task:
        return jsonify({"error": "not_found"}), 404
    if task.get("status") not in TERMINAL_STATUSES:
        store.update_task(task_id, status=CANCELLED, stage=CANCELLED, completed_at=now_iso())
        store.add_log(task_id, "任务已取消")
    return jsonify({"success": True, "task_id": task_id})


@app.post("/api/retry/<task_id>")
def retry_task(task_id: str):
    task = _get_visible_task(task_id)
    if not task:
        return jsonify({"error": "not_found"}), 404
    upload_dir = _task_upload_dir(task_id)
    files = [path for path in upload_dir.iterdir() if path.is_file()] if upload_dir.exists() else []
    if not files:
        return jsonify({"error": "missing_source"}), 404
    source_path = files[0]
    filename = safe_client_filename(source_path.name)
    config_payload = task.get("config") or {}
    config = _parse_config(payload=config_payload)
    source = SourceFile(
        path=source_path,
        filename=filename,
        mime_type=guess_mime_type(filename),
        extension=validate_extension(filename),
        file_size=source_path.stat().st_size,
        sha256=sha256_file(source_path),
        source_type=task.get("source_type") or "upload",
        source_url=task.get("source_url") or "",
    )
    owner_scope = _request_owner_scope(default_market=task.get("market_scope"))
    market_scope = owner_scope.get("market_scope") or task.get("market_scope") or DEFAULT_MARKET_SCOPE
    store.delete_task(task_id)
    shutil.rmtree(_task_result_dir(task_id), ignore_errors=True)
    return jsonify(_enqueue_task(source, config, task_id=task_id, owner_scope=owner_scope, market_scope=market_scope))


@app.delete("/api/tasks/<task_id>")
def delete_task(task_id: str):
    if not _get_visible_task(task_id):
        return jsonify({"error": "not_found"}), 404
    store.delete_task(task_id)
    shutil.rmtree(_task_upload_dir(task_id), ignore_errors=True)
    shutil.rmtree(_task_result_dir(task_id), ignore_errors=True)
    return jsonify({"success": True, "task_id": task_id})


@app.get("/api/result/<task_id>")
def result(task_id: str):
    task = _get_visible_task(task_id)
    if not task:
        return jsonify({"error": "not_found"}), 404
    if task.get("status") == FAILED:
        task = _recover_pdf_bridge_task(task)
    result_dir = _task_result_dir(task_id)
    markdown_path = result_dir / "document.md"
    manifest_path = result_dir / "manifest.json"
    if not markdown_path.exists() or not manifest_path.exists():
        return jsonify({"error": "missing_artifact", "task": task}), 404
    return jsonify(
        {
            "task": task,
            "manifest": read_json(manifest_path),
            "markdown": markdown_path.read_text(encoding="utf-8"),
            "artifacts": artifact_summary(task_id, result_dir),
        }
    )


@app.get("/api/artifact/<task_id>/<path:artifact>")
def artifact(task_id: str, artifact: str):
    if not _get_visible_task(task_id):
        return jsonify({"error": "not_found"}), 404
    result_dir = _task_result_dir(task_id)
    normalized = artifact.strip().replace("\\", "/").rstrip("/")
    download_requested = query_flag_enabled(request.args, "download")
    if normalized == "images":
        zip_path = result_dir / "exports" / "images.zip"
        with __import__("zipfile").ZipFile(zip_path, "w") as archive:
            images_dir = result_dir / "images"
            if images_dir.exists():
                for path in images_dir.rglob("*"):
                    if path.is_file():
                        archive.write(path, path.relative_to(result_dir).as_posix())
        return send_file(zip_path, as_attachment=True, download_name="images.zip")
    if normalized == "images/download":
        return artifact(task_id, "images")
    if normalized not in ARTIFACT_ALLOWLIST and not normalized.startswith(("images/original/", "images/crops/", "images/page_previews/", "exports/", "raw/mineru/")):
        return jsonify({"error": "artifact_not_allowed"}), 403
    try:
        path = safe_artifact_path(result_dir, normalized)
    except ValueError:
        return jsonify({"error": "invalid_artifact"}), 400
    if not path.exists() or not path.is_file():
        return jsonify({"error": "not_found"}), 404
    if normalized == "layout_blocks.json" and not download_requested:
        return jsonify(_read_layout_blocks_with_page_metadata(result_dir))
    if normalized == "table_relations.json":
        _read_table_relations_payload(task_id, result_dir)
        if not path.exists() or not path.is_file():
            return jsonify({"error": "not_found"}), 404
        if not download_requested:
            payload = read_json(path)
            return jsonify(payload)
    as_attachment = download_requested or normalized.startswith("exports/")
    return send_file(path, as_attachment=as_attachment, download_name=path.name if as_attachment else None)


@app.get("/api/download/<task_id>")
def download_full(task_id: str):
    return artifact(task_id, "exports/full.zip")


@app.post("/api/download/batch")
def download_batch():
    payload = request.get_json(silent=True) or {}
    normalized_ids = requested_batch_download_task_ids(payload)
    if normalized_ids is None:
        return jsonify({"error": "invalid_task_ids"}), 400
    if not normalized_ids:
        return jsonify({"error": "no_tasks"}), 400
    if len(normalized_ids) > MAX_BATCH_DOWNLOAD_TASKS:
        return jsonify({"error": "too_many_tasks", "message": f"一次最多下载 {MAX_BATCH_DOWNLOAD_TASKS} 个任务"}), 400

    batch_id = uuid.uuid4().hex[:12]
    zip_path = OUTPUT_FOLDER / f"document-parser-batch-{batch_id}.zip"
    included = []
    missing = []
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for task_id in normalized_ids:
            task = _get_visible_task(task_id)
            result_dir = _task_result_dir(task_id)
            full_zip = result_dir / "exports" / "full.zip"
            if not task or not full_zip.exists():
                missing.append(task_id)
                continue
            filename = safe_client_filename(task.get("filename") or task_id)
            archive_dir = safe_client_filename(task_id)
            archive.write(full_zip, f"{archive_dir}/{filename}.zip")
            manifest_path = result_dir / "manifest.json"
            if manifest_path.exists():
                archive.write(manifest_path, f"{archive_dir}/manifest.json")
            included.append({"task_id": task_id, "filename": task.get("filename") or task_id})
        archive.writestr(
            "batch_manifest.json",
            json.dumps(
                build_batch_download_manifest(
                    batch_id=batch_id,
                    requested_task_ids=normalized_ids,
                    included=included,
                    missing=missing,
                ),
                ensure_ascii=False,
                indent=2,
            ),
        )
    if not included:
        zip_path.unlink(missing_ok=True)
        return jsonify({"error": "no_downloadable_tasks", "missing": missing}), 404
    return send_file(zip_path, as_attachment=True, download_name=f"document-parser-batch-{batch_id}.zip")


@app.get("/api/figures/<task_id>")
def list_figures(task_id: str):
    if not _get_visible_task(task_id):
        return jsonify({"error": "not_found"}), 404
    path = _task_result_dir(task_id) / "figures.json"
    if not path.exists():
        return jsonify({"error": "not_found"}), 404
    return jsonify(read_json(path))


@app.get("/api/figures/<task_id>/<image_id>")
def get_figure(task_id: str, image_id: str):
    if not _get_visible_task(task_id):
        return jsonify({"error": "not_found"}), 404
    figures_path = _task_result_dir(task_id) / "figures.json"
    if not figures_path.exists():
        return jsonify({"error": "not_found"}), 404
    figures = read_json(figures_path).get("figures") or []
    figure = find_figure_by_image_id(figures, image_id)
    if figure:
        return jsonify({"figure": figure})
    return jsonify({"error": "not_found"}), 404


@app.get("/api/source/<task_id>/page/<int:page_number>")
def source_page(task_id: str, page_number: int):
    if not _get_visible_task(task_id):
        return jsonify({"error": "not_found"}), 404
    result_dir = _task_result_dir(task_id)
    blocks_path = result_dir / "blocks.json"
    if not blocks_path.exists():
        return jsonify({"error": "not_found"}), 404
    blocks = read_json(blocks_path).get("blocks") or []
    layout = _read_layout_blocks_with_page_metadata(result_dir)
    return jsonify(build_source_page_payload(task_id, page_number, blocks, layout))


@app.get("/api/source/<task_id>/page-image/<int:page_number>")
def source_page_image(task_id: str, page_number: int):
    task = _get_visible_task(task_id)
    if not task:
        return jsonify({"error": "not_found"}), 404
    try:
        image_path = _ensure_source_page_image(task_id, page_number)
    except FileNotFoundError as exc:
        return jsonify({"error": "source_page_image_unavailable", "message": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": "invalid_page", "message": str(exc)}), 400
    except (subprocess.SubprocessError, subprocess.TimeoutExpired) as exc:
        return jsonify({"error": "source_page_render_failed", "message": str(exc)}), 500
    return send_file(image_path, mimetype="image/png")


@app.get("/api/source/<task_id>/block/<block_id>")
def source_block(task_id: str, block_id: str):
    if not _get_visible_task(task_id):
        return jsonify({"error": "not_found"}), 404
    blocks_path = _task_result_dir(task_id) / "blocks.json"
    if not blocks_path.exists():
        return jsonify({"error": "not_found"}), 404
    blocks = read_json(blocks_path).get("blocks") or []
    for block in blocks:
        if block.get("block_id") == block_id:
            return jsonify({"task_id": task_id, "block": block})
    return jsonify({"error": "not_found"}), 404


@app.get("/api/source/<task_id>/table/<table_id>")
def source_table(task_id: str, table_id: str):
    if not _get_visible_task(task_id):
        return jsonify({"error": "not_found"}), 404
    tables_path = _task_result_dir(task_id) / "tables.json"
    if not tables_path.exists():
        return jsonify({"error": "not_found"}), 404
    tables = read_json(tables_path).get("physical_tables") or read_json(tables_path).get("tables") or []
    for table in tables:
        if str(table.get("table_id")) == table_id:
            return jsonify({"task_id": task_id, "table": table})
    return jsonify({"error": "not_found"}), 404


@app.get("/api/source/<task_id>/image/<image_id>")
def source_image(task_id: str, image_id: str):
    if not _get_visible_task(task_id):
        return jsonify({"error": "not_found"}), 404
    figures_path = _task_result_dir(task_id) / "figures.json"
    if not figures_path.exists():
        return jsonify({"error": "not_found"}), 404
    figures = read_json(figures_path).get("figures") or []
    figure = find_figure_by_image_id(figures, image_id)
    if figure:
        return jsonify(build_source_image_payload(task_id, image_id, figure))
    return jsonify({"error": "not_found"}), 404


@app.get("/api/table-relations/<task_id>")
def table_relations(task_id: str):
    if not _get_visible_task(task_id):
        return jsonify({"error": "not_found"}), 404
    result_dir = _task_result_dir(task_id)
    path = result_dir / "table_relations.json"
    if not path.exists():
        return jsonify({"error": "not_found"}), 404
    payload = _read_table_relations_payload(task_id, result_dir)
    if not payload:
        return jsonify({"error": "not_found"}), 404
    corrections_path = result_dir / "table_merge_corrections.json"
    corrections = read_json(corrections_path) if corrections_path.exists() else {}
    return jsonify(build_table_relations_response_payload(payload, corrections))


@app.post("/api/table-relations/<task_id>/<relation_id>/review")
def review_table_relation(task_id: str, relation_id: str):
    if not _get_visible_task(task_id):
        return jsonify({"error": "not_found"}), 404
    result_dir = _task_result_dir(task_id)
    corrections_path = result_dir / "table_merge_corrections.json"
    corrections = read_json(corrections_path) if corrections_path.exists() else {"schema_version": "document_table_merge_corrections_v1", "task_id": task_id, "relations": {}, "manual_logical_tables": []}
    payload = request.get_json(silent=True) or {}
    review_status = payload.get("review_status") or payload.get("reviewStatus") or "accepted"
    if review_status not in {"accepted", "rejected", "needs_review"}:
        return jsonify({"error": "invalid_review_status"}), 400
    corrections.setdefault("relations", {})[relation_id] = {
        "review_status": review_status,
        "note": payload.get("note") or "",
        "updated_at": now_iso(),
    }
    write_json(corrections_path, corrections)
    return jsonify({"success": True, "corrections": corrections})


@app.post("/api/logical-tables/<task_id>/<logical_table_id>/split")
def split_logical_table(task_id: str, logical_table_id: str):
    if not _get_visible_task(task_id):
        return jsonify({"error": "not_found"}), 404
    return jsonify({"success": False, "message": "P0 provider has no merged logical tables to split", "logical_table_id": logical_table_id})


@app.post("/api/logical-tables/<task_id>/merge")
def merge_logical_tables(task_id: str):
    if not _get_visible_task(task_id):
        return jsonify({"error": "not_found"}), 404
    return jsonify({"success": False, "message": "P0 provider does not support manual logical table merge yet", "task_id": task_id})


@app.get("/api/extraction/templates")
def extraction_templates():
    return jsonify({"schema_version": "document_extraction_templates_v1", "templates": list_extraction_templates()})


@app.post("/api/extract/<task_id>")
def extract(task_id: str):
    if not _get_visible_task(task_id):
        return jsonify({"error": "not_found"}), 404
    result_dir = _task_result_dir(task_id)
    markdown_path = result_dir / "document.md"
    if not markdown_path.exists():
        return jsonify({"error": "not_found"}), 404
    payload = request.get_json(silent=True) or {}
    return jsonify(run_extraction(task_id, result_dir, payload))


@app.get("/api/extract/<task_id>/<extract_id>")
def extract_result(task_id: str, extract_id: str):
    if not _get_visible_task(task_id):
        return jsonify({"error": "not_found"}), 404
    path = _task_result_dir(task_id) / "extraction" / "result.json"
    if not path.exists():
        return jsonify({"error": "not_found"}), 404
    payload = read_json(path)
    if payload.get("extract_id") not in {"", None, extract_id}:
        return jsonify({"error": "not_found"}), 404
    return jsonify(payload)


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "15010"))
    app.run(host=host, port=port, debug=False, threaded=True)
