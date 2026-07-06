#!/usr/bin/env python3
"""
PDF to Markdown Web Application.
Flask backend for converting PDFs using the local MinerU API.
"""

from collections import Counter
import hashlib
import io
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, make_response, render_template, request, send_file

from artifact_manager import (
    cleanup_old_output_dirs as artifact_cleanup_old_output_dirs,
    cleanup_unreferenced_children as artifact_cleanup_unreferenced_children,
    safe_remove as artifact_safe_remove,
    safe_unlink as artifact_safe_unlink,
)
from financial_extractor import (
    FINANCIAL_CHECKS_SCHEMA_VERSION,
    FINANCIAL_DATA_SCHEMA_VERSION,
    FINANCIAL_RULE_VERSION,
    build_financial_checks,
    build_financial_data,
    parse_html_table as financial_parse_html_table,
)
from mineru_client import (
    check_service_health as mineru_check_service_health,
    friendly_submit_error as mineru_friendly_submit_error,
    json_request as mineru_json_request,
    stream_multipart_post as mineru_stream_multipart_post,
    submit_readiness as mineru_submit_readiness,
)
from path_config import resolve_app_paths
from pdf_source_viewer import (
    coerce_json_artifact as pdf_coerce_json_artifact,
    page_bbox_extent_from_content_list,
    page_content_payload_from_content_list,
    printed_page_numbers_by_pdf_page,
)
from pdf_parser_page_markers import (
    PDF_PAGE_MARKER_RE,
    _backfill_sparse_markdown_pages,
    _collect_text_fragments,
    _compact_text_fragment,
    _inject_pdf_page_markers,
    _page_anchor_candidates,
    _page_marker_line,
    _pdf_page_markers_by_line,
    _strip_page_markers,
)
import pdf_parser_artifact_service as artifact_service
import pdf_parser_artifact_orchestrator_service as artifact_orchestrator_service
import pdf_parser_content_list_enhanced_service as content_list_enhanced_service
import pdf_parser_document_full_service as document_full_service
import pdf_parser_financial_service as financial_service
import pdf_parser_mineru_result_service as mineru_result_service
import pdf_parser_quality_service as quality_service
import pdf_parser_response_service as response_service
import pdf_parser_source_service as source_service
import pdf_parser_task_lifecycle_service as task_lifecycle_service
import pdf_parser_task_repository as task_repository
from quality_engine import (
    candidate_confidence as quality_candidate_confidence,
    candidate_group as quality_candidate_group,
    detect_report_year as quality_detect_report_year,
)
from quality_report import (
    INDICATOR_TABLE_NAMES,
    KEY_SECTIONS,
    KEY_TABLE_DISPLAY_ORDER,
    QUALITY_SCHEMA_VERSION,
)
from table_merge import (
    TABLE_RELATION_RULESET_VERSION,
    build_table_relations as build_physical_table_relations,
)
from task_store import (
    CANCELLED,
    COMPLETED,
    COMPLETED_MISSING_ARTIFACT,
    FAILED,
    is_failed_status,
    is_success_status,
    is_terminal_status,
    missing_artifact_message,
)
from pdf_parser_request_utils import (
    ALLOWED_BACKENDS,
    ALLOWED_PARSE_METHODS,
    APP_ACCESS_TOKEN,
    MARKET_TOKEN_RE,
    SUPPORTED_MARKETS,
    _apply_task_market_fallback,
    _format_duration,
    _infer_market_from_text,
    _normalize_market,
    _parse_bool,
    _parse_page_id,
    _parse_submit_config,
    _request_has_valid_token,
    _safe_client_filename,
    _safe_download_name,
    _safe_task_id,
    _task_market_from_record,
)
from pdf_parser_runtime_utils import (
    FileCache,
    _looks_like_pdf,
    _now_iso,
    _read_json_cached as runtime_read_json_cached,
    _read_text_cached as runtime_read_text_cached,
    _safe_header_value,
    _safe_remove,
    _safe_unlink,
    _task_elapsed_seconds as runtime_task_elapsed_seconds,
    _utc_now,
)

BASE_DIR = os.path.dirname(__file__)
APP_PATHS = resolve_app_paths(BASE_DIR)
DATA_DIR = APP_PATHS["data_dir"]
MINERU_API_BASE = os.environ.get("MINERU_API_URL", "http://127.0.0.1:8003")
VLM_API_BASE = os.environ.get("VLM_API_URL", "http://127.0.0.1:8002")
UPLOAD_FOLDER = APP_PATHS["uploads"]
RESULTS_FOLDER = APP_PATHS["results"]
FINANCIAL_LLM_CACHE_FOLDER = APP_PATHS["financial_llm_cache"]
OUTPUT_FOLDER = APP_PATHS["output"]
DB_PATH = APP_PATHS["db"]
MAX_FILE_SIZE = int(os.environ.get("MAX_FILE_SIZE", str(100 * 1024 * 1024)))  # 100 MB
MAX_FILES_PER_UPLOAD = int(os.environ.get("MAX_FILES_PER_UPLOAD", "5"))
MAX_BATCH_UPLOAD_SIZE = int(
    os.environ.get("MAX_BATCH_UPLOAD_SIZE", str(MAX_FILE_SIZE * MAX_FILES_PER_UPLOAD))
)
TASK_RETENTION_HOURS = int(os.environ.get("TASK_RETENTION_HOURS", "0"))
CLEANUP_INTERVAL_SECONDS = int(os.environ.get("CLEANUP_INTERVAL_SECONDS", "600"))
CLEANUP_ORPHAN_DATA = os.environ.get("CLEANUP_ORPHAN_DATA", "0") == "1"
CLEANUP_OUTPUT_FOLDER = os.environ.get("CLEANUP_OUTPUT_FOLDER", "0") == "1"
OUTPUT_RETENTION_HOURS = int(
    os.environ.get("OUTPUT_RETENTION_HOURS", "24" if TASK_RETENTION_HOURS <= 0 else str(TASK_RETENTION_HOURS))
)
PAGE_ESTIMATE_SECONDS = max(1.0, float(os.environ.get("PAGE_ESTIMATE_SECONDS", "18")))
STATUS_CACHE_SECONDS = float(os.environ.get("STATUS_CACHE_SECONDS", "1.5"))
UPLOAD_CHUNK_SIZE = 1024 * 1024
MINERU_SUBMIT_TIMEOUT_SECONDS = int(os.environ.get("MINERU_SUBMIT_TIMEOUT_SECONDS", "900"))
MINERU_STATUS_TIMEOUT_SECONDS = int(os.environ.get("MINERU_STATUS_TIMEOUT_SECONDS", "30"))
MINERU_STATUS_FAILURE_TOLERANCE = int(os.environ.get("MINERU_STATUS_FAILURE_TOLERANCE", "6"))
STALE_SUBMITTING_SECONDS = int(os.environ.get("STALE_SUBMITTING_SECONDS", "1800"))
APP_JS_VERSION = str(int(os.path.getmtime(os.path.join(BASE_DIR, "static", "app.js"))))
DOCUMENT_FULL_SCHEMA_VERSION = 3
CONTENT_LIST_ENHANCED_SCHEMA_VERSION = 10

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(RESULTS_FOLDER, exist_ok=True)
os.makedirs(FINANCIAL_LLM_CACHE_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)
os.makedirs(APP_PATHS["logs"], exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_BATCH_UPLOAD_SIZE
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

_db_lock = threading.Lock()
_last_cleanup_ts = 0.0
_queue_lock = threading.Lock()
_queue_wakeup = threading.Event()
_queue_worker_started = False
_app_init_lock = threading.Lock()
_app_initialized = False
QUEUE_POLL_SECONDS = float(os.environ.get("QUEUE_POLL_SECONDS", "3"))
FILE_CACHE_MAX_ITEMS = int(os.environ.get("PDF2MD_FILE_CACHE_MAX_ITEMS", "32"))
_file_cache = FileCache(max_items=FILE_CACHE_MAX_ITEMS)


def _wake_queue_worker():
    _queue_wakeup.set()


def _read_text_cached(path):
    return runtime_read_text_cached(path, _file_cache)


def _read_json_cached(path):
    return runtime_read_json_cached(path, _file_cache)


def _task_elapsed_seconds(task):
    return runtime_task_elapsed_seconds(task, now_factory=_utc_now)


def _refresh_markdown_page_markers(task, markdown, markdown_path, content_list=None):
    refreshed_markdown = _inject_pdf_page_markers(
        markdown,
        content_list,
        total_pages=task.get("pdf_page_count"),
    )
    refreshed_markdown, restored_pages = _backfill_sparse_markdown_pages(refreshed_markdown, content_list)
    if markdown_path and task.get("markdown_path") != markdown_path:
        task["markdown_path"] = markdown_path
        _persist_task(task)
    if refreshed_markdown == markdown:
        return markdown
    if markdown_path:
        os.makedirs(os.path.dirname(markdown_path), exist_ok=True)
        with open(markdown_path, "w", encoding="utf-8") as outfile:
            outfile.write(refreshed_markdown)
    if isinstance(content_list, list):
        _write_quality_artifacts(
            task,
            refreshed_markdown,
            file_name=task.get("filename"),
            content_list=content_list,
        )
    if restored_pages:
        _append_log(task, f"已从 content_list 回填 {len(restored_pages)} 个稀疏 Markdown 页", "info")
    return refreshed_markdown


def _db_conn():
    return task_repository.connect(DB_PATH)


def _task_exists(task_id):
    return task_repository.task_exists(DB_PATH, task_id)


def _init_db():
    task_repository.init_db(DB_PATH, lock=_db_lock)


def _row_to_task(row):
    return task_repository.row_to_task(row, normalize_task=_apply_task_market_fallback)


def _save_task(task, allow_insert=False):
    task_repository.save_task(DB_PATH, task, allow_insert=allow_insert, lock=_db_lock)


def _get_task(task_id):
    return task_repository.get_task(DB_PATH, task_id, normalize_task=_apply_task_market_fallback)


def _task_blocks_duplicate_upload(task):
    return task_repository.task_blocks_duplicate_upload(task)


def _find_duplicate_filename_task(filename):
    return task_repository.find_duplicate_filename_task(
        DB_PATH,
        filename,
        normalize_filename=_safe_client_filename,
        normalize_task=_apply_task_market_fallback,
    )


def _find_duplicate_file_hash_task(file_sha256):
    return task_repository.find_duplicate_file_hash_task(
        DB_PATH,
        file_sha256,
        normalize_task=_apply_task_market_fallback,
    )


def _task_duplicate_payload(task):
    return response_service.build_task_duplicate_payload(task, has_markdown_artifact=_has_markdown_artifact)


def _duplicate_task_response(error_code, filename, existing_task=None, message=None):
    payload = {
        "error": error_code,
        "message": message or "该文件已存在解析任务，请勿重复解析",
        "filename": filename,
        "existingTask": _task_duplicate_payload(existing_task),
    }
    return jsonify(payload), 409


def _duplicate_filename_response(filename, existing_task=None, message=None):
    return _duplicate_task_response("duplicate_filename", filename, existing_task, message)


def _duplicate_content_response(filename, existing_task=None, message=None):
    return _duplicate_task_response("duplicate_file_content", filename, existing_task, message)


def _list_recent_tasks(limit=100):
    tasks = task_repository.list_recent_tasks(DB_PATH, limit=limit, normalize_task=_apply_task_market_fallback)
    for task in tasks:
        if task.get("status") == COMPLETED and not _has_markdown_artifact(task):
            full_task = _get_task(task["task_id"])
            if full_task and not _has_markdown_artifact(full_task):
                _mark_completed_missing_artifact(full_task)
                task["status"] = COMPLETED_MISSING_ARTIFACT
                task["stage"] = COMPLETED_MISSING_ARTIFACT
    return response_service.normalize_recent_tasks(tasks, has_markdown_artifact=_has_markdown_artifact)


def _recent_task_list_limit():
    return response_service.clamp_recent_task_limit(os.environ.get("PDF_RECENT_TASK_LIMIT", "300"))


def _recent_tasks_payload():
    return response_service.build_recent_tasks_payload(
        _list_recent_tasks(limit=_recent_task_list_limit()),
        has_markdown_artifact=_has_markdown_artifact,
    )


def _refresh_recent_tasks(limit=50):
    for task_id in task_repository.task_ids_for_recent_refresh(DB_PATH, limit=limit):
        task = _get_task(task_id)
        if not task:
            continue
        try:
            _refresh_task_from_upstream(task)
        except RuntimeError:
            continue


def _append_log(task, message, level="info"):
    task.setdefault("logs", []).append(
        {"time": _now_iso(), "message": message, "level": level}
    )
    if len(task["logs"]) > 200:
        task["logs"] = task["logs"][-200:]


def _persist_task(task, allow_insert=False):
    _save_task(task, allow_insert=allow_insert)


def _has_active_upstream_task():
    return task_repository.has_active_upstream_task(DB_PATH)


def _next_queued_task():
    return task_repository.next_queued_task(DB_PATH, normalize_task=_apply_task_market_fallback)


def _claim_next_queued_task():
    return task_lifecycle_service.claim_next_queued_task(
        DB_PATH,
        normalize_task=_apply_task_market_fallback,
        lock=_db_lock,
    )


def _recover_stale_submitting_tasks():
    return task_lifecycle_service.recover_stale_submitting_tasks(
        DB_PATH,
        stale_seconds=STALE_SUBMITTING_SECONDS,
        now_factory=_utc_now,
        lock=_db_lock,
    )


def _local_queue_position(task_id):
    return task_repository.local_queue_position(DB_PATH, task_id)


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as infile:
        while True:
            chunk = infile.read(UPLOAD_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _cleanup_pending_uploads(prepared_uploads, current_path=None):
    if current_path:
        _safe_unlink(current_path)
    for item in prepared_uploads:
        _safe_unlink(item.get("upload_path"))


def _submit_task_to_mineru(task):
    upload_path = task.get("upload_path")
    submit_config = task.get("submit_config") or {}
    if not upload_path or not os.path.exists(upload_path):
        task["status"] = "failed"
        task["stage"] = "failed"
        task["completed_at"] = _now_iso()
        task["error"] = "本地上传文件不存在，无法提交到 MinerU"
        _append_log(task, task["error"], "error")
        _persist_task(task)
        return False

    fields = {
        "backend": submit_config.get("backend", "hybrid-http-client"),
        "parse_method": submit_config.get("parse_method", "auto"),
        "formula_enable": "true" if submit_config.get("formula_enable") else "false",
        "table_enable": "true" if submit_config.get("table_enable") else "false",
        "server_url": VLM_API_BASE,
        "return_md": "true",
        "return_middle_json": "true",
        "return_model_output": "true",
        "return_content_list": "true",
        "return_images": "true",
        "response_format_zip": "false",
        "return_original_file": "false",
        "lang_list": "ch",
    }
    if submit_config.get("start_page_id") not in (None, ""):
        fields["start_page_id"] = str(submit_config.get("start_page_id"))
    if submit_config.get("end_page_id") not in (None, ""):
        fields["end_page_id"] = str(submit_config.get("end_page_id"))

    task["status"] = "submitting"
    task["stage"] = "submitting"
    _append_log(task, "轮到当前任务，正在提交到 MinerU...", "info")
    _persist_task(task)

    result = _stream_multipart_post(
        f"{MINERU_API_BASE}/tasks",
        fields=fields,
        file_field_name="files",
        filename=task["filename"],
        file_path=upload_path,
        content_type="application/pdf",
        timeout=MINERU_SUBMIT_TIMEOUT_SECONDS,
    )

    if result.get("_error"):
        task["status"] = "failed"
        task["stage"] = "failed"
        task["completed_at"] = _now_iso()
        task["error"] = _friendly_submit_error(result.get("detail", "Unknown error"))
        _append_log(task, f"提交失败: {task['error']}", "error")
        _persist_task(task)
        return False

    mineru_task_id = result.get("task_id")
    if not mineru_task_id:
        task["status"] = "failed"
        task["stage"] = "failed"
        task["completed_at"] = _now_iso()
        task["error"] = "MinerU API did not return a task_id"
        _append_log(task, "MinerU API 未返回 task_id", "error")
        _persist_task(task)
        return False

    task["mineru_task_id"] = mineru_task_id
    task["status"] = "pending"
    task["stage"] = "submitted"
    task["submitted_at"] = _now_iso()
    task["error"] = None
    _append_log(task, f"任务已提交到 MinerU, task_id={mineru_task_id[:8]}...", "info")
    _persist_task(task)
    return True


def _maybe_submit_next_queued_task():
    readiness = _mineru_submit_readiness()
    if not readiness["submit_ready"]:
        return False
    if _has_active_upstream_task():
        return False
    with _queue_lock:
        if _has_active_upstream_task():
            return False
        task = _claim_next_queued_task()
        if not task:
            return False
        return _submit_task_to_mineru(task)


def _queue_worker_loop():
    while True:
        try:
            _recover_stale_submitting_tasks()
            _cleanup_old_data()
            _refresh_recent_tasks(limit=50)
            _maybe_submit_next_queued_task()
        except Exception:
            pass
        _queue_wakeup.wait(max(1.0, QUEUE_POLL_SECONDS))
        _queue_wakeup.clear()


def _start_queue_worker():
    global _queue_worker_started
    if _queue_worker_started:
        return
    _queue_worker_started = True
    thread = threading.Thread(target=_queue_worker_loop, name="pdf2md-local-queue", daemon=True)
    thread.start()


def initialize_app(start_worker=True):
    global _app_initialized
    with _app_init_lock:
        if not _app_initialized:
            _init_db()
            _recover_stale_submitting_tasks()
            _cleanup_old_data(force=True)
            _app_initialized = True
        if start_worker:
            _start_queue_worker()
            _wake_queue_worker()


def _delete_task_record(task_id):
    task_repository.delete_task_record(DB_PATH, task_id, lock=_db_lock)


def _referenced_task_paths():
    return task_repository.referenced_task_paths(DB_PATH, RESULTS_FOLDER)


def _cleanup_unreferenced_children(folder, referenced_paths, cutoff_ts):
    return artifact_cleanup_unreferenced_children(folder, referenced_paths, cutoff_ts, remove=_safe_remove)


def _cleanup_old_output_dirs(cutoff_ts):
    retention_hours = max(0.0, (time.time() - float(cutoff_ts)) / 3600.0)
    return artifact_cleanup_old_output_dirs(OUTPUT_FOLDER, retention_hours=retention_hours, remove=_safe_remove)


def _cleanup_output_artifacts():
    if not CLEANUP_OUTPUT_FOLDER:
        return 0
    return artifact_cleanup_old_output_dirs(
        OUTPUT_FOLDER,
        retention_hours=OUTPUT_RETENTION_HOURS,
        remove=_safe_remove,
    )


def _cleanup_old_data(force=False):
    global _last_cleanup_ts
    now = time.time()
    if not force and now - _last_cleanup_ts < CLEANUP_INTERVAL_SECONDS:
        return
    _last_cleanup_ts = now

    if TASK_RETENTION_HOURS <= 0:
        _cleanup_output_artifacts()
        return

    cutoff = (_utc_now() - timedelta(hours=TASK_RETENTION_HOURS)).replace(microsecond=0).isoformat() + "Z"
    conn = _db_conn()
    try:
        rows = conn.execute(
            """
            SELECT task_id, upload_path, markdown_path FROM tasks
            WHERE created_at < ?
              AND status IN ('completed', 'completed_missing_artifact', 'failed', 'cancelled')
            """,
            (cutoff,),
        ).fetchall()
        for row in rows:
            _safe_unlink(row["upload_path"])
            _safe_remove(row["markdown_path"])
            _safe_remove(os.path.join(RESULTS_FOLDER, row["task_id"]))
        conn.execute(
            """
            DELETE FROM tasks
            WHERE created_at < ?
              AND status IN ('completed', 'completed_missing_artifact', 'failed', 'cancelled')
            """,
            (cutoff,),
        )
        conn.commit()
    finally:
        conn.close()

    cutoff_ts = time.time() - TASK_RETENTION_HOURS * 3600
    if CLEANUP_ORPHAN_DATA:
        _, referenced_paths = _referenced_task_paths()
        _cleanup_unreferenced_children(UPLOAD_FOLDER, referenced_paths, cutoff_ts)
        _cleanup_unreferenced_children(RESULTS_FOLDER, referenced_paths, cutoff_ts)
    _cleanup_output_artifacts()


def _json_request(url, method="GET", data=None, headers=None, timeout=30):
    return mineru_json_request(url, method=method, data=data, headers=headers, timeout=timeout)


def _stream_multipart_post(url, fields, file_field_name, filename, file_path, content_type=None, timeout=300):
    return mineru_stream_multipart_post(
        url,
        fields=fields,
        file_field_name=file_field_name,
        filename=filename,
        file_path=file_path,
        content_type=content_type,
        timeout=timeout,
        chunk_size=UPLOAD_CHUNK_SIZE,
    )


def _friendly_submit_error(detail):
    return mineru_friendly_submit_error(detail)


def _check_service_health(url, timeout=5):
    return mineru_check_service_health(url, timeout=timeout)


def _mineru_submit_readiness():
    return mineru_submit_readiness(MINERU_API_BASE, VLM_API_BASE, timeout=5)


def _get_pdf_page_count(pdf_path):
    try:
        from pypdf import PdfReader

        reader = PdfReader(pdf_path)
        return len(reader.pages)
    except Exception:
        pass

    try:
        with open(pdf_path, "rb") as infile:
            text = infile.read(200000).decode("latin-1", errors="ignore")
        match = re.search(r"/Type\s*/Pages.*?/Count\s*(\d+)", text, re.DOTALL)
        if match:
            return int(match.group(1))
        match = re.search(r"/Count\s*(\d+)", text)
        if match:
            count = int(match.group(1))
            if 0 < count < 10000:
                return count
    except Exception:
        pass
    return None


def _calc_page_progress(task, elapsed):
    return task_lifecycle_service.calc_page_progress(
        task,
        elapsed,
        page_estimate_seconds=PAGE_ESTIMATE_SECONDS,
    )


def _calc_progress_percent(task, elapsed):
    return task_lifecycle_service.calc_progress_percent(
        task,
        elapsed,
        page_estimate_seconds=PAGE_ESTIMATE_SECONDS,
    )


def _legacy_markdown_path(task):
    return artifact_service.legacy_markdown_path(task, RESULTS_FOLDER)


def _canonical_markdown_path(task):
    return artifact_service.canonical_markdown_path(task, RESULTS_FOLDER)


def _markdown_artifact_path(task):
    return artifact_service.markdown_artifact_path(task, RESULTS_FOLDER)


def _has_markdown_artifact(task):
    return artifact_service.has_markdown_artifact(task, RESULTS_FOLDER)


def _mark_completed_missing_artifact(task, detail=None):
    previous_status = task.get("status")
    previous_error = task.get("error")
    message = detail or missing_artifact_message()
    task["status"] = COMPLETED_MISSING_ARTIFACT
    task["stage"] = COMPLETED_MISSING_ARTIFACT
    task["completed_at"] = task.get("completed_at") or _now_iso()
    task["error"] = message
    if previous_status != COMPLETED_MISSING_ARTIFACT or previous_error != message:
        _append_log(task, task["error"], "warn")
    _persist_task(task)
    return task


def _task_requires_markdown_artifact(task):
    return is_success_status(task.get("status")) or task.get("status") == COMPLETED_MISSING_ARTIFACT


def _read_markdown(task):
    markdown_path = _markdown_artifact_path(task)
    if markdown_path:
        if task.get("markdown_path") != markdown_path:
            task["markdown_path"] = markdown_path
            _persist_task(task)
        return _read_text_cached(markdown_path)
    return None


def _read_and_refresh_markdown(task):
    markdown_path = _markdown_artifact_path(task)
    if not markdown_path:
        return None
    markdown = _read_text_cached(markdown_path)
    return _refresh_markdown_page_markers(
        task,
        markdown,
        markdown_path,
        content_list=_load_json_artifact(task, "content_list.json"),
    )


def _result_dir(task):
    return artifact_service.result_dir(task, RESULTS_FOLDER)


def _write_json(path, payload):
    return artifact_service.write_json(path, payload)


def _corrections_path(task):
    return source_service.corrections_path(task, results_folder=RESULTS_FOLDER)


def _load_corrections(task):
    return source_service.load_corrections(task, results_folder=RESULTS_FOLDER)


def _save_table_correction(task, table_item, payload):
    return source_service.save_table_correction(
        task,
        table_item,
        payload,
        results_folder=RESULTS_FOLDER,
        now_iso=_now_iso,
    )


def _write_markdown(task, markdown):
    return artifact_service.write_markdown(task, markdown, results_folder=RESULTS_FOLDER)


def _decode_image_payload(payload):
    return artifact_service.decode_image_payload(payload)


def _save_images(images, images_dir):
    return artifact_service.save_images(images, images_dir)


def _count_table_rows(table_html):
    return len(re.findall(r"<tr\b", table_html, flags=re.IGNORECASE))


def _count_empty_cells(table_html):
    return len(re.findall(r"<td[^>]*>\s*</td>", table_html, flags=re.IGNORECASE))


def _strip_html(html):
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()


def _count_table_cells(table_html):
    return len(re.findall(r"<t[dh]\b", table_html, flags=re.IGNORECASE))


def _count_numeric_cells(table_html):
    cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", table_html, flags=re.IGNORECASE | re.DOTALL)
    numeric_cells = 0
    for cell in cells:
        text = _strip_html(cell)
        if re.search(r"[-(（]?\d[\d,，]*(?:\.\d+)?%?[)）]?", text):
            numeric_cells += 1
    return numeric_cells


def _table_context(markdown, start, end):
    before = markdown[max(0, start - 900):start]
    after = markdown[end:min(len(markdown), end + 220)]
    lines = [line.strip() for line in before.splitlines() if line.strip()]
    heading = ""
    for line in reversed(lines[-18:]):
        plain = re.sub(r"^#+\s*", "", line).strip()
        if not plain or "<table" in plain.lower() or "|" in plain:
            continue
        if line.startswith("#") or len(plain) <= 48:
            heading = re.sub(r"^#+\s*", "", line)
            break
    unit_match = re.search(r"单位[:：]\s*([^\n ]+)", before[-360:] + after[:120])
    return {
        "heading": heading,
        "unit": unit_match.group(1) if unit_match else "",
        "near_text": _strip_html((before[-260:] + " " + after[:100]))[:260],
    }


def _detect_report_year(markdown, file_name=None):
    return quality_detect_report_year(markdown, file_name=file_name)


def _detect_report_kind(markdown, filename=None):
    snapshot = build_financial_data(markdown or "", task_id=None, filename=filename, llm_judge=None)
    return snapshot.get("report_kind") or "annual_report"


def _compact_candidate_text(text):
    return quality_service.compact_candidate_text(text)


def _unique_preserve_order(items):
    return quality_service.unique_preserve_order(items)


def _candidate_signal_text(context, source, table_text):
    return quality_service.candidate_signal_text(context, source, table_text)


def _candidate_title_text(context, source):
    return quality_service.candidate_title_text(context, source)


def _table_item_text(table_item):
    return quality_service.table_item_text(table_item)


def _nearest_table_for_statement_lines(report, lines, statement_type):
    return quality_service.nearest_table_for_statement_lines(report, lines, statement_type)


def _statement_display_source(statement, report, statement_type):
    return quality_service.statement_display_source(statement, report, statement_type)


def _merge_quality_candidates_from_financial_data(report, financial_data):
    return quality_service.merge_quality_candidates_from_financial_data(report, financial_data)


def _sync_quality_profile_from_financial_data(report, financial_data, financial_checks=None):
    if not isinstance(report, dict) or not isinstance(financial_data, dict):
        return report
    for key in ("market", "market_profile", "accounting_standard", "currency", "unit"):
        if financial_data.get(key):
            report[key] = financial_data.get(key)
    if financial_data.get("profile_rule_version"):
        report["financial_profile_rule_version"] = financial_data.get("profile_rule_version")
    detected_currencies = (
        financial_data.get("detected_currencies")
        or (financial_data.get("summary") or {}).get("detected_currencies")
        or (financial_checks or {}).get("detected_currencies")
        or []
    )
    if detected_currencies:
        report["detected_currencies"] = detected_currencies
    return report


def _quality_report_warnings(report, financial_data=None):
    return quality_service.quality_report_warnings(report, financial_data)


def _has_formal_statement_signal(name, compact_direct):
    aliases = {
        "所有者权益变动表": ("所有者权益变动表", "股东权益变动表"),
    }.get(name, (name,))
    if not any(alias in compact_direct for alias in aliases):
        return False
    if name == "资产负债表" and "资产负债表日" in compact_direct:
        return False
    noise_terms = (
        "现金流量表补充资料",
        "财务报表附注",
        "报表附注",
    )
    if any(term in compact_direct for term in noise_terms):
        return False
    return True


def _looks_like_equity_change_table(compact):
    equity_header = any(
        term in compact
        for term in (
            "归属于母公司所有者权益",
            "归属于母公司股东权益",
            "归属于本行股东的权益",
            "归属于本行股东权益",
            "归属于银行股东的权益",
            "归属于普通股股东权益",
            "所有者权益合计",
            "股东权益合计",
        )
    ) or ("少数股东权益" in compact and "合计" in compact)
    capital_columns = (
        ("股本" in compact or "实收资本" in compact)
        and "资本公积" in compact
        and ("未分配利润" in compact or "未弥补亏损" in compact)
        and any(term in compact for term in ("盈余公积", "其他综合收益", "专项储备", "一般风险准备"))
    )
    period_rows = any(
        term in compact
        for term in (
            "上年年末余额",
            "上年期末余额",
            "本年年初余额",
            "本期期初余额",
            "年初余额",
            "期初余额",
            "1月1日余额",
            "2025年1月1日",
            "2024年1月1日",
            "12月31日余额",
            "本年增减变动",
            "本期增减变动",
            "年度增减变动额",
        )
    )
    movement_rows = any(
        term in compact
        for term in (
            "本年增减变动",
            "本期增减变动",
            "年度增减变动额",
            "所有者投入资本",
            "股东投入资本",
            "利润分配",
            "综合收益总额",
        )
    )
    return equity_header and capital_columns and period_rows and movement_rows


def _inferred_statement_names_from_content(table_text):
    compact = _compact_candidate_text(table_text[:6000])
    names = []
    if (
        "流动资产" in compact
        and "非流动资产" in compact
        and ("资产总计" in compact or "资产合计" in compact)
        and (
            "负债和所有者权益" in compact
            or "负债和股东权益" in compact
            or "所有者权益合计" in compact
            or "货币资金" in compact
        )
    ):
        names.append("资产负债表")
    if (
        ("营业总收入" in compact or "一、营业收入" in compact or "一营业收入" in compact)
        and ("营业总成本" in compact or "营业成本" in compact)
        and ("利润总额" in compact or "净利润" in compact)
        and "毛利率" not in compact[:260]
    ):
        names.append("利润表")
    if (
        "经营活动产生的现金流量" in compact
        and "经营活动现金流入小计" in compact
        and ("投资活动现金流入小计" in compact or "投资活动产生的现金流量" in compact)
        and ("筹资活动现金流入小计" in compact or "筹资活动产生的现金流量" in compact)
        and "现金流量净额" in compact
    ):
        names.append("现金流量表")
    if _looks_like_equity_change_table(compact):
        names.append("所有者权益变动表")
    return names


def _looks_like_main_accounting_data_table(compact_text):
    has_year_column = bool(re.search(r"20\d{2}年|本报告期|上年同期|上年末|本年末", compact_text))
    performance_terms = (
        "营业收入",
        "营业利润",
        "利润总额",
        "净利润",
        "归属于母公司股东的净利润",
        "归属于上市公司股东的净利润",
        "归属于本行股东的净利润",
        "归属于普通股股东的净利润",
    )
    scale_or_cash_terms = (
        "经营活动产生的现金流量净额",
        "资产总额",
        "负债总额",
        "股东权益",
        "所有者权益",
        "客户贷款和垫款总额",
        "发放贷款和垫款总额",
        "吸收存款",
    )
    performance_hits = sum(1 for term in performance_terms if term in compact_text)
    return has_year_column and performance_hits >= 2 and any(term in compact_text for term in scale_or_cash_terms)


def _looks_like_main_financial_indicator_table(compact_text):
    has_year_column = bool(re.search(r"20\d{2}年|本报告期|上年同期", compact_text))
    indicator_terms = (
        "基本每股收益",
        "稀释每股收益",
        "每股经营活动产生的现金流量净额",
        "加权平均净资产收益率",
        "平均总资产回报率",
        "平均净资产收益率",
        "净资产收益率",
        "总资产报酬率",
        "净利差",
        "净息差",
        "成本收入比",
        "资本充足率",
        "不良贷款率",
        "拨备覆盖率",
        "流动性覆盖率",
    )
    return has_year_column and sum(1 for term in indicator_terms if term in compact_text) >= 2


def _looks_like_bank_scale_indicator_table(compact_text):
    has_date_column = bool(re.search(r"20\d{2}年\d{1,2}月\d{1,2}日|20\d{2}年末|本年末|上年末", compact_text))
    scale_terms = (
        "总资产",
        "资产总额",
        "总负债",
        "负债总额",
        "客户贷款",
        "贷款及垫款总额",
        "客户存款",
        "存款总额",
        "归属于本行股东的权益总额",
        "归属于本行普通股股东的每股净资产",
    )
    return has_date_column and sum(1 for term in scale_terms if term in compact_text) >= 3


def _looks_like_nonrecurring_gain_loss_table(compact_text):
    if "计入非经常性损益" in compact_text[:260]:
        return False
    if "非经常性损益" in compact_text and any(term in compact_text for term in ("所得税影响", "合计", "净额")):
        return True
    detail_terms = (
        "非流动性资产处置损益",
        "非流动资产处置损益",
        "政府补助",
        "除上述各项之外的其他营业外收入和支出",
        "其他营业外收支",
        "所得税影响",
        "少数股东权益影响",
    )
    return sum(1 for term in detail_terms if term in compact_text) >= 3 and "合计" in compact_text


def _matched_financial_table_names(context, text, source):
    direct, broad = _candidate_signal_text(context, source, text)
    compact_title = _compact_candidate_text(_candidate_title_text(context, source))
    compact_direct = _compact_candidate_text(direct)
    compact_broad = _compact_candidate_text(broad)
    names = []

    for name in ("资产负债表", "利润表", "现金流量表", "所有者权益变动表"):
        if _has_formal_statement_signal(name, compact_title):
            names.append(name)
    if "股东权益变动表" in compact_title:
        names.append("所有者权益变动表")
    for name in _inferred_statement_names_from_content(text):
        names.append(name)

    head_compact = _compact_candidate_text(text[:260])
    context_compact = _compact_candidate_text(" ".join(filter(None, [context.get("heading") or "", context.get("near_text") or ""])))
    table_compact = _compact_candidate_text(text[:2000])
    accounting_data_alias_hit = any(
        term in compact_direct or term in context_compact or term in compact_broad
        for term in ("主要会计数据", "主要财务数据", "财务概要", "经营业绩")
    )
    if (
        accounting_data_alias_hit
        or "会计数据和财务指标" in context_compact
        or "会计数据和财务指标" in compact_broad
        or (
            _looks_like_main_accounting_data_table(table_compact)
            and any(term in context_compact or term in compact_broad for term in ("财务概要", "经营业绩", "主要财务数据"))
        )
        or (
            _looks_like_bank_scale_indicator_table(table_compact)
            and any(term in context_compact or term in compact_broad for term in ("财务概要", "规模指标", "主要财务数据"))
        )
    ):
        names.append("主要会计数据")
    financial_indicator_alias_hit = any(
        term in compact_direct or term in context_compact or term in compact_broad
        for term in ("主要财务指标", "盈利能力指标")
    )
    if (
        financial_indicator_alias_hit
        or "主要会计数据和财务指标" in compact_direct
        or "主要会计数据和财务指标" in context_compact
        or "会计数据和财务指标" in context_compact
        or "会计数据和财务指标" in compact_broad
        or (
            _looks_like_main_financial_indicator_table(table_compact)
            and any(term in context_compact or term in compact_broad for term in ("盈利能力指标", "财务指标", "主要财务指标"))
        )
        or (
            "财务指标" in compact_broad
            and any(term in head_compact for term in ("每股收益", "净资产收益率", "平均总资产回报率", "净利差", "成本收入比"))
        )
    ):
        names.append("主要财务指标")
    if (
        ("非经常性损益" in compact_title and "计入非经常性损益" not in compact_title)
        or (
            "非经常性损益" in context_compact
            and "计入非经常性损益" not in context_compact
            and (
                "非经常性损益明细表" in context_compact
                or "非经常性损益项目及金额" in context_compact
                or "当期非经常性损益" in context_compact
                or "项目金额说明" in head_compact
            )
        )
        or "非经常性损益项目" in head_compact
        or "非经常性损益明细表" in context_compact
        or _looks_like_nonrecurring_gain_loss_table(table_compact)
    ):
        names.append("非经常性损益")
    if "前十名股东" in compact_direct:
        names.append("前十名股东")

    segment_terms = ("分行业", "分产品", "分地区")
    if any(term in compact_broad for term in segment_terms):
        for term in segment_terms:
            if term in compact_broad:
                names.append(term)

    revenue_context_terms = (
        "营业收入构成",
        "营业收入情况",
        "营业收入和营业成本",
        "主营业务",
        "按行业",
        "按产品",
        "按地区",
        "分行业",
        "分产品",
        "分地区",
    )
    is_main_metric_table = any(name in names for name in ("主要会计数据", "主要财务指标"))
    if (
        not is_main_metric_table
        and "营业收入" in compact_broad
        and any(term in compact_broad for term in revenue_context_terms)
    ):
        names.append("营业收入")

    if any(term in compact_direct for term in ("研发投入", "研发人员", "研发项目")):
        names.append("研发投入")

    return [name for name in KEY_TABLE_DISPLAY_ORDER if name in set(names)]


def _candidate_group(name):
    return quality_candidate_group(name)


def _candidate_score(item, name):
    heading = item.get("heading") or ""
    caption = " ".join(item.get("source_caption") or [])
    preview = item.get("preview") or ""
    direct = _compact_candidate_text(" ".join([heading, caption, preview[:180]]))
    score = 35.0

    if name in _compact_candidate_text(" ".join([heading, caption])):
        score += 40
    elif name in direct:
        score += 24
    elif name in (item.get("matched_financial_names") or []):
        score += 24

    if item.get("table_type") == "fact":
        score += 12
    score += min(float(item.get("rows") or 0), 80.0) * 0.35
    score += min(float(item.get("numeric_ratio") or 0), 1.0) * 18

    if any(term in direct for term in ("合并", "合并及母公司")):
        score += 12
    if "母公司" in direct:
        score += 4
    if any(term in direct for term in ("续", "（续）", "(续)")):
        score -= 16
    if any(term in direct for term in ("财务报表附注", "报表附注", "补充资料", "项目注释", "明细", "变动原因")):
        score -= 22
    if (item.get("rows") or 0) <= 2:
        score -= 35
    if float(item.get("empty_ratio") or 0) >= 0.5:
        score -= 18

    if _candidate_group(name) == "indicator" and any(term in direct for term in ("主营业务", "分行业", "分产品", "分地区", "构成")):
        score += 16
    if _candidate_group(name) == "core" and name in (item.get("matched_financial_names") or []):
        score += 18
    if name == "主要会计数据":
        if any(term in direct for term in ("主要会计数据", "主要财务数据", "经营业绩", "规模指标")):
            score += 18
        if "盈利能力指标" in direct:
            score -= 14
    if name == "主要财务指标":
        if any(term in direct for term in ("主要财务指标", "盈利能力指标")):
            score += 24
        if any(term in direct for term in ("经营业绩", "规模指标")):
            score -= 18
    if name == "所有者权益变动表" and name in (item.get("matched_financial_names") or []):
        score += 16
    if name == "非经常性损益" and name in (item.get("matched_financial_names") or []):
        if any(term in direct for term in ("非经常性损益项目", "非经常性损益明细表", "项目金额说明", "本期发生额", "本期金额")):
            score += 14
    return round(score, 2)


def _candidate_confidence(score):
    return quality_candidate_confidence(score)


def _classify_table_semantics(context, matched_names, source, numeric_ratio=0, row_count=0):
    search_text = " ".join(
        filter(
            None,
            [
                context.get("heading") or "",
                context.get("near_text") or "",
                " ".join(matched_names or []),
                " ".join(source.get("caption") or []),
                " ".join(source.get("footnote") or []),
            ],
        )
    )
    dimension_keywords = [
        "公司信息",
        "联系人和联系方式",
        "信息披露及备置地点",
        "注册变更情况",
        "其他有关资料",
        "释义",
        "备查文件目录",
        "股票简称",
        "股票代码",
        "法定代表人",
        "注册地址",
        "办公地址",
        "电子信箱",
        "公司简介",
        "基本情况",
        "股本结构",
    ]
    fact_keywords = [
        "资产负债表",
        "利润表",
        "现金流量表",
        "所有者权益变动表",
        "非经常性损益",
        "主要会计数据",
        "主要财务指标",
        "分季度主要财务指标",
        "营业收入",
        "营业成本",
        "研发投入",
        "前十名股东",
        "股东情况",
        "分行业",
        "分产品",
        "分地区",
        "投资收益",
        "现金分红",
        "持股情况",
        "财务指标",
        "净利润",
        "资产总额",
        "负债总额",
    ]
    dimension_hits = [keyword for keyword in dimension_keywords if keyword in search_text]
    fact_hits = [keyword for keyword in fact_keywords if keyword in search_text]
    reasons = []
    if matched_names:
        reasons.append("matched_financial_name")
    if context.get("unit"):
        reasons.append("has_unit")
    if numeric_ratio >= 0.16 and row_count >= 2:
        reasons.append("numeric_density_high")

    strong_dimension = bool(dimension_hits)
    strong_fact = bool(matched_names or fact_hits or context.get("unit") or (numeric_ratio >= 0.16 and row_count >= 2))

    table_type = "dimension"
    if strong_dimension and not strong_fact:
        table_type = "dimension"
        reasons.append("dimension_keyword")
    elif strong_fact:
        table_type = "fact"
        reasons.append("fact_keyword")
    return {
        "table_type": table_type,
        "year_binding_required": table_type == "fact",
        "classification_reasons": sorted(set(reasons)),
    }


def _coerce_json_artifact(payload):
    return pdf_coerce_json_artifact(payload)


def _load_json_artifact(task, filename):
    return artifact_service.load_json_artifact(
        task,
        filename,
        results_folder=RESULTS_FOLDER,
        read_json_cached=_read_json_cached,
        coerce_json_artifact=_coerce_json_artifact,
    )


def _page_bbox_extent(task, page_index):
    return source_service.page_bbox_extent(
        task,
        page_index,
        load_json_artifact=_load_json_artifact,
        page_bbox_extent_from_content_list=page_bbox_extent_from_content_list,
    )


def _page_content_payload(task, page_number, report=None, focus_table=None):
    return source_service.page_content_payload(
        task,
        page_number,
        report=report,
        focus_table=focus_table,
        load_json_artifact=_load_json_artifact,
        page_content_payload_from_content_list=page_content_payload_from_content_list,
    )


def _pdf_page_image_path(task, page_number):
    return source_service.pdf_page_image_path(task, page_number, results_folder=RESULTS_FOLDER)


def _ensure_pdf_page_image(task, page_number):
    return source_service.ensure_pdf_page_image(task, page_number, results_folder=RESULTS_FOLDER)


def _content_table_sources(content_list):
    return content_list_enhanced_service.content_table_sources(content_list)


def _build_table_relations_artifact(task, markdown, enhanced=None, content_list=None):
    if enhanced is None:
        enhanced = _load_json_artifact(task, "content_list_enhanced.json")
    if content_list is None:
        content_list = _load_json_artifact(task, "content_list.json")
    return document_full_service.build_table_relations_artifact_payload(
        task,
        markdown,
        enhanced=enhanced,
        content_list=content_list,
        build_table_relations=build_physical_table_relations,
        now_iso=_now_iso,
        table_relation_ruleset_version=TABLE_RELATION_RULESET_VERSION,
    )


def _table_relations_path(task):
    return os.path.join(_result_dir(task), "table_relations.json")


def _write_table_relations_artifact(task, markdown, enhanced=None, content_list=None):
    result_dir = _result_dir(task)
    os.makedirs(result_dir, exist_ok=True)
    payload = _build_table_relations_artifact(task, markdown, enhanced=enhanced, content_list=content_list)
    _write_json(_table_relations_path(task), payload)
    return payload


def _ensure_table_relations_artifact(task, markdown, enhanced=None, content_list=None):
    path = _table_relations_path(task)
    existing = _load_json_artifact(task, "table_relations.json") if os.path.exists(path) else None
    if (
        isinstance(existing, dict)
        and existing.get("schema_version") == "document_table_relations_v1"
        and existing.get("ruleset_version") == TABLE_RELATION_RULESET_VERSION
    ):
        return existing
    return _write_table_relations_artifact(task, markdown, enhanced=enhanced, content_list=content_list)


def _normalized_table_html_for_match(table_html):
    return content_list_enhanced_service.normalized_table_html_for_match(table_html)


def _content_table_source_maps(table_sources):
    return content_list_enhanced_service.content_table_source_maps(table_sources)


def _pop_unused_content_table_source(table_html, exact_sources, normalized_sources, used_source_ids):
    return content_list_enhanced_service.pop_unused_content_table_source(
        table_html,
        exact_sources,
        normalized_sources,
        used_source_ids,
    )


def _pop_unused_source_from_bucket(bucket, used_source_ids):
    return content_list_enhanced_service._pop_unused_source_from_bucket(bucket, used_source_ids)


def _inferred_pdf_page_for_line(line, markers):
    return content_list_enhanced_service.inferred_pdf_page_for_line(line, markers)


def _table_source_confidence(source_name):
    return content_list_enhanced_service.table_source_confidence(source_name)


def _printed_page_numbers_by_pdf_page(content_list):
    return content_list_enhanced_service.printed_page_numbers_by_pdf_page_map(content_list)


SUPERSCRIPT_FOOTNOTE_REF_RE = content_list_enhanced_service.SUPERSCRIPT_FOOTNOTE_REF_RE
INLINE_FOOTNOTE_REF_RE = content_list_enhanced_service.INLINE_FOOTNOTE_REF_RE
FOOTNOTE_DEF_RE = content_list_enhanced_service.FOOTNOTE_DEF_RE
INLINE_FOOTNOTE_PREV_EXCLUDE = content_list_enhanced_service.INLINE_FOOTNOTE_PREV_EXCLUDE
INLINE_FOOTNOTE_NEXT_EXCLUDE = content_list_enhanced_service.INLINE_FOOTNOTE_NEXT_EXCLUDE
TOC_LINE_RE = content_list_enhanced_service.TOC_LINE_RE


def _table_structure_signals(table_html):
    try:
        grid = financial_parse_html_table(table_html)
    except Exception:
        grid = []
    row_count = len(grid)
    column_count = max((len(row) for row in grid), default=0)
    header_rows = 0
    header_signal_terms = (
        "项目",
        "本期",
        "上期",
        "本年",
        "上年",
        "期末",
        "期初",
        "年末",
        "年初",
        "金额",
        "比例",
        "名称",
        "单位",
        "合计",
        "年度",
        "月份",
    )
    for row_pos, row in enumerate(grid[:4]):
        row_text = _strip_html(" ".join(row))
        numeric_cells = sum(1 for cell in row if re.search(r"\d", str(cell or "")))
        nonempty_cells = sum(1 for cell in row if str(cell or "").strip())
        if not nonempty_cells:
            continue
        duplicate_cells = nonempty_cells - len({str(cell or "").strip() for cell in row if str(cell or "").strip()})
        has_header_terms = any(term in row_text for term in header_signal_terms)
        if row_pos == 0 and (has_header_terms or numeric_cells == 0):
            header_rows += 1
            continue
        if has_header_terms or duplicate_cells >= max(2, nonempty_cells // 2):
            header_rows += 1
            continue
        break
    has_colspan = bool(re.search(r"\bcolspan\s*=", table_html or "", flags=re.IGNORECASE))
    has_rowspan = bool(re.search(r"\browspan\s*=", table_html or "", flags=re.IGNORECASE))
    first_rows = [" | ".join(cell for cell in row[:8] if str(cell or "").strip())[:220] for row in grid[:3]]
    multi_level_header = has_colspan or has_rowspan or (column_count >= 3 and header_rows >= 2)
    return {
        "expanded_rows": row_count,
        "expanded_columns": column_count,
        "header_row_count": header_rows,
        "has_colspan": has_colspan,
        "has_rowspan": has_rowspan,
        "multi_level_header_candidate": multi_level_header,
        "header_preview": [row for row in first_rows if row],
    }


def _block_page_number(block):
    page_idx = block.get("page_idx") if isinstance(block, dict) else None
    return int(page_idx) + 1 if isinstance(page_idx, int) else None


def _build_enhanced_page_blocks(content_list):
    return content_list_enhanced_service.build_enhanced_page_blocks(content_list)


def _markdown_image_details(markdown):
    text = str(markdown or "")
    pattern = re.compile(
        r"!\[[^\]]*\]\((?P<path>[^)]+)\)"
        r"(?P<trailing>(?:[ \t]*\n|[ \t]{2,}\n|[ \t])*)"
        r"(?P<details><details>\s*<summary>(?P<summary>[^<]+)</summary>\s*(?P<body>.*?)</details>)?",
        flags=re.IGNORECASE | re.DOTALL,
    )
    details_by_path = {}
    order = 0
    for match in pattern.finditer(text):
        order += 1
        image_path = str(match.group("path") or "").strip()
        if not image_path:
            continue
        line = text.count("\n", 0, match.start()) + 1
        body = (match.group("body") or "").strip()
        summary = (match.group("summary") or "").strip()
        details_by_path.setdefault(image_path, []).append(
            {
                "markdown_image_order": order,
                "markdown_line": line,
                "summary_type": summary,
                "body": body,
                "body_preview": _compact_text_fragment(_strip_html(body), 320),
                "has_details": bool(match.group("details")),
            }
        )
    return details_by_path


def _image_semantic_kind(block_type, sub_type, detail_type):
    value = (detail_type or sub_type or block_type or "").lower()
    if "equation" in value or "formula" in value:
        return "formula"
    if value == "flowchart":
        return "flowchart"
    if value in {"bar", "pie", "line", "bar_line", "bar_stacked", "donut", "heatmap", "geo", "bubble"}:
        return "chart"
    if "chart" in value:
        return "chart"
    if value == "text_image":
        return "text_image"
    if value == "natural_image":
        return "natural_image"
    return "image"


def _image_semantic_confidence(block_type, sub_type, detail):
    if detail.get("has_details"):
        detail_type = str(detail.get("summary_type") or "").lower()
        if detail_type in {"bar", "pie", "line", "bar_line", "bar_stacked", "donut", "heatmap", "geo", "bubble", "flowchart"}:
            return "high"
        if detail_type in {"text_image", "natural_image"}:
            return "medium"
        return "medium"
    if block_type in {"chart", "equation"}:
        return "medium"
    if sub_type:
        return "low"
    return "low"


def _detect_text_language(text):
    plain = _strip_html(str(text or ""))
    zh = len(re.findall(r"[\u4e00-\u9fff]", plain))
    latin = len(re.findall(r"[A-Za-z]", plain))
    if zh and zh >= max(2, latin // 3):
        return "zh"
    if latin:
        return "en"
    return "unknown"


def _localize_markdown_table_headers_to_zh(text):
    lines = str(text or "").splitlines()
    replacements = {
        "Year": "年份",
        "Category": "类别",
        "Value": "数值",
        "Blue Bar Value": "蓝色柱数值",
        "Gold Bar Value": "金色柱数值",
    }
    localized = []
    for line in lines:
        if "|" not in line:
            localized.append(line)
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        new_cells = [replacements.get(cell, cell) for cell in cells]
        if len(new_cells) == len(cells):
            localized.append("| " + " | ".join(new_cells) + " |")
        else:
            localized.append(line)
    return "\n".join(localized)


def _localized_no_text_suffix(text):
    value = _compact_text_fragment(text, 600)
    if not value:
        return ""
    phrase_replacements = (
        ("no visible text or symbols visible", "未见可读文字或符号"),
        ("no visible text or symbols", "未见可读文字或符号"),
        ("no text or symbols visible", "未见可读文字或符号"),
        ("no text or symbols present", "未见文字或符号"),
        ("no text or symbols", "未见文字或符号"),
        ("no visible text", "未见可读文字"),
        ("no readable text in focus", "未见清晰可读文字"),
    )
    lowered = value.lower().rstrip(".")
    for source, target in phrase_replacements:
        lowered = lowered.replace(source, target)
    if lowered != value.lower().rstrip("."):
        match = re.search(r"(未见清晰可读文字|未见可读文字或符号|未见文字或符号|未见可读文字)", lowered)
        return match.group(1) if match else ""
    return ""


def _localize_plain_image_description_to_zh(text, semantic_kind="", detail_type=""):
    value = _compact_text_fragment(text, 600)
    if not value:
        return ""
    suffix = _localized_no_text_suffix(value)
    lowered = value.lower()
    if semantic_kind == "natural_image" or detail_type == "natural_image":
        if "portrait of" in lowered:
            return f"人物肖像图片，{suffix or '未见清晰可读文字'}。"
        if "line art illustration" in lowered or "illustration of" in lowered:
            return f"插图或线稿图片，{suffix or '未见清晰可读文字'}。"
        if "abstract" in lowered:
            return f"抽象装饰图片，{suffix or '未见清晰可读文字'}。"
        if "aerial view" in lowered:
            return f"航拍场景图片，{suffix or '未见清晰可读文字'}。"
        if "exterior view" in lowered:
            return f"建筑或场景外观图片，{suffix or '未见清晰可读文字'}。"
        if "interior" in lowered:
            return f"室内场景图片，{suffix or '未见清晰可读文字'}。"
        if "group photo" in lowered or "group of" in lowered:
            return f"人物合影或群体场景图片，{suffix or '未见清晰可读文字'}。"
        if suffix:
            return f"自然图片，{suffix}。"
        return "自然图片，原始英文描述已保留在 JSON 的 recognized_content 字段。"
    if semantic_kind == "text_image" or detail_type == "text_image":
        if _detect_text_language(value) == "en":
            return f"图片文字（英文原文）：{value}"
        return value
    if suffix:
        return f"图片描述：{suffix}。"
    return ""


def _normalized_image_content_zh(content, semantic_kind="", content_format="", detail_type=""):
    if not content:
        return ""
    if _detect_text_language(content) == "zh":
        return content
    if content_format == "markdown_table":
        return _localize_markdown_table_headers_to_zh(content)
    if content_format == "mermaid":
        # Mermaid structures often carry Chinese node labels already. Preserve
        # syntax exactly instead of translating graph code heuristically.
        return content
    if semantic_kind == "formula":
        return content
    if semantic_kind in {"natural_image", "text_image", "image"} or content_format == "plain_text":
        localized = _localize_plain_image_description_to_zh(
            content,
            semantic_kind=semantic_kind,
            detail_type=detail_type,
        )
        if localized:
            return localized
    return ""


def _markdown_table_to_records(markdown_table, max_rows=80):
    lines = []
    for raw_line in str(markdown_table or "").splitlines():
        line = raw_line.strip()
        if not line or "|" not in line or line.startswith("```"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells:
            continue
        is_separator = all(re.fullmatch(r":?-{2,}:?", cell or "") for cell in cells)
        if is_separator:
            continue
        lines.append(cells)
    if len(lines) < 2:
        return None
    headers = lines[0]
    normalized_headers = []
    seen = Counter()
    for index, header in enumerate(headers, start=1):
        name = header or f"列{index}"
        seen[name] += 1
        normalized_headers.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    rows = []
    for cells in lines[1 : max_rows + 1]:
        padded = cells + [""] * max(0, len(normalized_headers) - len(cells))
        rows.append({header: padded[idx] for idx, header in enumerate(normalized_headers)})
    return {
        "headers": normalized_headers,
        "rows": rows,
        "row_count": max(0, len(lines) - 1),
        "source": "markdown_table_in_image_details",
    }


def _strip_mermaid_fences(content):
    text = str(content or "").strip()
    text = re.sub(r"^```mermaid\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_mermaid_node_token(token):
    raw = str(token or "").strip().rstrip(";")
    raw = re.sub(r"\s+", " ", raw)
    if not raw:
        return "", ""
    node_match = re.match(r"([A-Za-z_][\w.-]*)", raw)
    node_id = node_match.group(1) if node_match else re.sub(r"\W+", "_", raw)[:32]
    label = ""
    quoted = re.search(r'["“](.+?)["”]', raw)
    if quoted:
        label = quoted.group(1).strip()
    else:
        bracket = re.search(r"[\[\(\{]([^\]\)\}]+)[\]\)\}]", raw)
        if bracket:
            label = bracket.group(1).strip().strip('"“”')
    return node_id, label or node_id


def _mermaid_to_nodes_edges(mermaid, max_edges=120):
    text = _strip_mermaid_fences(mermaid)
    if not text:
        return None
    nodes = {}
    edges = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("%") or line.startswith("%%"):
            continue
        if re.match(r"^(?:graph|flowchart|sequenceDiagram|classDiagram|stateDiagram|subgraph|end|style|classDef)\b", line):
            continue
        line = line.rstrip(";")
        edge_match = re.search(r"(.+?)\s*(-->|---|-.->|==>)\s*(.+)", line)
        if not edge_match:
            node_id, label = _parse_mermaid_node_token(line)
            if node_id:
                nodes.setdefault(node_id, {"id": node_id, "label": label})
            continue
        left = edge_match.group(1).strip()
        right = edge_match.group(3).strip()
        edge_label = ""
        if right.startswith("|"):
            label_match = re.match(r"\|([^|]+)\|\s*(.+)", right)
            if label_match:
                edge_label = label_match.group(1).strip()
                right = label_match.group(2).strip()
        source_id, source_label = _parse_mermaid_node_token(left)
        target_id, target_label = _parse_mermaid_node_token(right)
        if not source_id or not target_id:
            continue
        nodes.setdefault(source_id, {"id": source_id, "label": source_label})
        nodes.setdefault(target_id, {"id": target_id, "label": target_label})
        edges.append({"source": source_id, "target": target_id, "label": edge_label})
        if len(edges) >= max_edges:
            break
    if not nodes and not edges:
        return None
    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "source": "mermaid_in_image_details",
    }


def _image_bbox_area(bbox):
    if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
        return 0
    try:
        x0, y0, x1, y1 = [float(value) for value in bbox[:4]]
    except (TypeError, ValueError):
        return 0
    return max(0, x1 - x0) * max(0, y1 - y0)


def _image_ocr_vlm_candidate(block):
    confidence = block.get("confidence") or "low"
    has_content = bool(str(block.get("display_content") or block.get("recognized_content") or "").strip())
    area = _image_bbox_area(block.get("bbox"))
    kind = block.get("semantic_kind") or "image"
    needed = confidence == "low" and not has_content and area >= 50000
    if not needed:
        return {
            "needed": False,
            "priority": "none",
            "reason": "",
            "recommended_mode": "",
            "bbox_area": area,
        }
    priority = "high" if area >= 180000 or kind in {"chart", "flowchart", "text_image", "formula"} else "medium"
    return {
        "needed": True,
        "priority": priority,
        "reason": "低置信大图缺少可读文本或结构化内容，建议按需调用 OCR/VLM 二次识别。",
        "recommended_mode": "ocr_or_vlm_on_demand",
        "bbox_area": area,
    }


def _image_actionability(block, chart_data=None, flowchart_graph=None, ocr_vlm_candidate=None):
    kind = block.get("semantic_kind") or "image"
    display_content = str(block.get("display_content") or "").strip()
    if chart_data and chart_data.get("rows"):
        return "data_usable"
    if flowchart_graph and (flowchart_graph.get("nodes") or flowchart_graph.get("edges")):
        return "structure_usable"
    if kind == "formula" and display_content:
        return "formula_candidate"
    if kind == "text_image" and display_content:
        return "search_only"
    if kind == "chart" and display_content:
        return "search_only"
    if (ocr_vlm_candidate or {}).get("needed"):
        return "needs_ocr"
    return "visual_context_only"


def _should_show_image_block_in_complete(block):
    actionability = block.get("actionability") or ""
    if actionability in {"data_usable", "structure_usable", "formula_candidate", "search_only"}:
        return True
    return False


def _build_image_semantic_blocks(markdown, content_list=None):
    return content_list_enhanced_service.build_image_semantic_blocks(markdown, content_list=content_list)


def _markdown_line_offsets(markdown):
    return content_list_enhanced_service.markdown_line_offsets(markdown)


def _line_number_for_offset(offsets, offset):
    return content_list_enhanced_service.line_number_for_offset(offsets, offset)


def _build_enhanced_footnotes(markdown, content_list=None):
    return content_list_enhanced_service.build_enhanced_footnotes(
        markdown,
        content_list=content_list,
        pdf_page_markers_by_line=_pdf_page_markers_by_line,
        infer_pdf_page_for_line=content_list_enhanced_service.inferred_pdf_page_for_line,
    )


def _heading_level_from_text(text):
    return content_list_enhanced_service.heading_level_from_text(text)


def _build_enhanced_toc(markdown, content_list=None):
    return content_list_enhanced_service.build_enhanced_toc(
        markdown,
        content_list=content_list,
        pdf_page_markers_by_line=_pdf_page_markers_by_line,
        infer_pdf_page_for_line=content_list_enhanced_service.inferred_pdf_page_for_line,
    )


def _build_enhanced_quality_signals(tables, footnotes, toc, pages, financial_note_links=None, image_semantic_blocks=None):
    return content_list_enhanced_service.build_enhanced_quality_signals(
        tables,
        footnotes,
        toc,
        pages,
        financial_note_links=financial_note_links,
        image_semantic_blocks=image_semantic_blocks,
    )


FINANCIAL_NOTE_SECTION_RE = re.compile(
    r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:[一二三四五六七八九十]+(?:[、.．]|\s+))?"
    r"(?:合并|母公司|公司|本集团|集团)?财务报表(?:主要项目|项目)?(?:附注|注释)(?:\s*[（(]续[）)])?|"
    r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:[一二三四五六七八九十]+(?:[、.．]|\s+))?"
    r"(?:合并|母公司|公司|本集团|集团)?财务报表项目注释|"
    r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:七|八|九|十)[、.．]?\s*合并财务报表项目注释|"
    r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:[0-9]+[、.])?\s*(?:货币资金|应收账款|存货|固定资产|无形资产|短期借款|应付账款|营业收入|营业成本|投资收益|所得税费用|现金流量表补充资料)"
)
FINANCIAL_NOTE_ITEM_ALIASES = {
    "货币资金": ("货币资金", "现金及存放中央银行款项"),
    "交易性金融资产": ("交易性金融资产",),
    "衍生金融资产": ("衍生金融资产",),
    "应收账款": ("应收账款", "应收款项"),
    "预付款项": ("预付款项",),
    "其他应收款": ("其他应收款",),
    "存货": ("存货",),
    "长期股权投资": ("长期股权投资",),
    "固定资产": ("固定资产",),
    "在建工程": ("在建工程",),
    "无形资产": ("无形资产",),
    "商誉": ("商誉",),
    "短期借款": ("短期借款",),
    "应付账款": ("应付账款", "应付款项"),
    "合同负债": ("合同负债",),
    "长期借款": ("长期借款",),
    "吸收存款": ("吸收存款", "客户存款"),
    "发放贷款和垫款": ("发放贷款和垫款", "客户贷款及垫款", "贷款和垫款"),
    "拆出资金": ("拆出资金",),
    "拆入资金": ("拆入资金",),
    "买入返售金融资产": ("买入返售金融资产",),
    "卖出回购金融资产款": ("卖出回购金融资产款",),
    "融出资金": ("融出资金",),
    "代理买卖证券款": ("代理买卖证券款",),
    "应付债券": ("应付债券",),
    "保险合同负债": ("保险合同负债",),
    "投资资产": ("投资资产",),
    "营业收入": ("营业收入", "营业总收入"),
    "营业成本": ("营业成本", "营业总成本"),
    "利息净收入": ("利息净收入",),
    "手续费及佣金净收入": ("手续费及佣金净收入",),
    "保费收入": ("保险业务收入", "已赚保费", "保费收入"),
    "投资收益": ("投资收益",),
    "所得税费用": ("所得税费用",),
    "销售费用": ("销售费用",),
    "管理费用": ("管理费用",),
    "研发费用": ("研发费用",),
    "财务费用": ("财务费用",),
    "经营活动现金流量净额": ("经营活动产生的现金流量净额", "经营活动现金流量净额"),
}


CHINESE_NOTE_SECTION_NUMBERS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
    "十三": 13,
    "十四": 14,
    "十五": 15,
    "十六": 16,
    "十七": 17,
    "十八": 18,
    "十九": 19,
    "二十": 20,
}


def _canonical_financial_note_ref(value, current_section=None):
    text = _strip_html(str(value or "")).strip()
    if not text or text in {"-", "—", "--", "无", "不适用"}:
        return None
    text = re.sub(r"\s+", "", text)
    text = text.replace("（", "(").replace("）", ")")
    text = re.sub(r"^(?:附注|注释|附注号|注释号|注|Note|note)", "", text)
    text = text.strip("：:、,.，。;；()[]【】")
    if not text:
        return None
    match = re.match(r"^([一二三四五六七八九十]{1,3})[、.．-]?(\d{1,3})(?:[、.．-](\d{1,3}))?$", text)
    if match:
        section = match.group(1)
        number = match.group(2)
        suffix = f".{match.group(3)}" if match.group(3) else ""
        return f"{section}、{int(number)}{suffix}"
    match = re.match(r"^([一二三四五六七八九十]{1,3})$", text)
    if match and current_section:
        return f"{current_section}、{CHINESE_NOTE_SECTION_NUMBERS.get(match.group(1), match.group(1))}"
    match = re.match(r"^(\d{1,3})(?:[、.．-](\d{1,3}))?$", text)
    if match:
        suffix = f".{int(match.group(2))}" if match.group(2) else ""
        return f"{current_section}、{int(match.group(1))}{suffix}" if current_section else f"{int(match.group(1))}{suffix}"
    return None


def _note_ref_numeric_key(note_ref):
    match = re.search(r"(\d{1,3})(?:\.\d+)?$", str(note_ref or ""))
    return match.group(1) if match else ""


def _parse_financial_amount_cell(value):
    return content_list_enhanced_service.parse_financial_amount_cell(value)


def _financial_unit_scale_from_text(text):
    return content_list_enhanced_service.financial_unit_scale_from_text(text)


def _financial_unit_scale(unit):
    return content_list_enhanced_service.financial_unit_scale(unit)


def _financial_unit_scale_near(text, position):
    return content_list_enhanced_service.financial_unit_scale_near(text, position)


def _normalize_amount_for_compare(value, unit_scale=1.0):
    return content_list_enhanced_service.normalize_amount_for_compare(value, unit_scale)


def _amount_close(left, right):
    return content_list_enhanced_service.amount_close(left, right)


def _canonical_item_name_from_alias(text):
    compact = _strip_html(str(text or "")).strip()
    for canonical, aliases in FINANCIAL_NOTE_ITEM_ALIASES.items():
        if any(alias and alias in compact for alias in aliases):
            return canonical
    return None


def _clean_financial_note_title(text):
    title = _strip_html(str(text or "")).strip()
    title = re.sub(r"^#{1,6}\s*", "", title)
    title = re.sub(r"\s*[（(]\s*续\s*[）)]\s*$", "", title).strip()
    title = re.sub(r"\s+", " ", title)
    return title.strip(" ：:")


def _financial_note_title_line_hit(raw_line):
    raw_line = _strip_html(str(raw_line or "")).strip()
    if not raw_line or len(raw_line) > 120:
        return None
    is_markdown_heading = bool(re.match(r"^#{1,6}\s+", raw_line))
    line = re.sub(r"^#{1,6}\s*", "", raw_line).strip()
    if re.search(r"\.{2,}\s*\d{1,4}\s*$", line) or re.search(r"…+\s*\d{1,4}\s*$", line):
        return None
    match = re.match(
        r"^(?:[（(]\s*(\d{1,3})\s*[）)]|(\d{1,3})|([一二三四五六七八九十]{1,3}))"
        r"(?:[、.．)]|\s+)\s*(.+?)\s*$",
        line,
    )
    note_key = None
    title = line
    if match:
        note_key = match.group(1) or match.group(2) or match.group(3)
        title = match.group(4)
    elif not is_markdown_heading:
        return None
    title = _clean_financial_note_title(title)
    canonical = _canonical_item_name_from_alias(title)
    if not canonical:
        return None
    if not note_key:
        starts_with_alias = any(
            alias and title.startswith(alias)
            for alias in FINANCIAL_NOTE_ITEM_ALIASES.get(canonical, ())
        )
        if not starts_with_alias:
            return None
    return {
        "note_key": note_key,
        "canonical_name": canonical,
        "title": title,
    }


def _financial_statement_values_from_table_row(row, skip_columns=None, unit_scale=1.0):
    values = []
    skip_columns = set(skip_columns or [])
    for col_idx, cell in enumerate(row or []):
        if col_idx in skip_columns:
            continue
        amount = _parse_financial_amount_cell(cell)
        if amount is None:
            continue
        values.append(
            {
                "column_index": col_idx,
                "raw": _strip_html(str(cell or "")).strip(),
                "value": amount,
                "normalized_value": _normalize_amount_for_compare(amount, unit_scale),
                "unit_scale": unit_scale,
            }
        )
    return values[:8]


def _statement_table_row_hit_for_canonical(table_html, canonical):
    try:
        grid = financial_parse_html_table(table_html)
    except Exception:
        grid = []
    if len(grid) < 2:
        return None
    unit_scale = _financial_unit_scale_from_text(table_html)
    for row_idx, row in enumerate(grid[1:], start=1):
        first_nonempty = next((_strip_html(str(cell or "")).strip() for cell in row if _strip_html(str(cell or "")).strip()), "")
        if _canonical_item_name_from_alias(first_nonempty) != canonical:
            continue
        return {
            "row_index": row_idx,
            "matched_alias": first_nonempty,
            "statement_values": _financial_statement_values_from_table_row(row, unit_scale=unit_scale),
        }
    return None


def _financial_note_zone_start(markdown):
    text = str(markdown or "")
    explicit_pattern = re.compile(
        r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:[一二三四五六七八九十]+(?:[、.．]|\s+))?"
        r"(?:合并|母公司|公司|本集团|集团)?财务报表(?:主要项目|项目)?(?:附注|注释)(?:\s*[（(]续[）)])?|"
        r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:[一二三四五六七八九十]+(?:[、.．]|\s+))?"
        r"(?:合并|母公司|公司|本集团|集团)?财务报表项目注释",
    )
    explicit_matches = list(explicit_pattern.finditer(text))
    min_explicit_offset = int(len(text) * 0.2)
    for match in explicit_matches:
        if match.start() >= min_explicit_offset:
            return match.start()
    if explicit_matches:
        # The first occurrence is often only a table-of-contents line. Prefer a
        # later explicit heading if one exists; otherwise fall back to title
        # heuristics below so the statement section is not accidentally cut off.
        later = explicit_matches[-1]
        if len(explicit_matches) > 1 and later.start() > explicit_matches[0].start():
            return later.start()

    # Fallback: only short heading-like note titles should start the note zone.
    # Long narrative lines such as "营业收入变动原因说明" belong to MD&A, not notes.
    offset = 0
    min_offset = int(len(text) * 0.35)
    for line in text.splitlines(True):
        raw_line = _strip_html(line).strip()
        if offset >= min_offset and _financial_note_title_line_hit(raw_line):
            return offset
        offset += len(line)
    return max(0, int(len(text) * 0.45))


def _financial_statement_item_hits(markdown):
    text = str(markdown or "")
    note_start = _financial_note_zone_start(text)
    statement_part = text[:note_start]
    hits = {}
    for canonical, aliases in FINANCIAL_NOTE_ITEM_ALIASES.items():
        best_pos = None
        best_alias = None
        for alias in aliases:
            pos = statement_part.find(alias)
            if pos >= 0 and (best_pos is None or pos < best_pos):
                best_pos = pos
                best_alias = alias
        if best_pos is None:
            continue
        line = statement_part.count("\n", 0, best_pos) + 1
        table_index = None
        statement_values = []
        row_index = None
        for idx, match in enumerate(re.finditer(r"<table\b.*?</table>", statement_part, flags=re.IGNORECASE | re.DOTALL), start=1):
            if match.start() <= best_pos <= match.end():
                table_index = idx
                row_hit = _statement_table_row_hit_for_canonical(match.group(0), canonical)
                if row_hit:
                    statement_values = row_hit.get("statement_values") or []
                    row_index = row_hit.get("row_index")
                break
        hits[canonical] = {
            "canonical_name": canonical,
            "matched_alias": best_alias,
            "line": line,
            "table_index": table_index,
            "source": "statement_text_alias",
            "row_index": row_index,
            "statement_values": statement_values,
        }
    for canonical, hit in _financial_statement_table_alias_hits(markdown, note_start).items():
        hits.setdefault(canonical, hit)
    hits.update(_financial_statement_note_ref_hits(markdown, note_start))
    return list(hits.values())


def _financial_statement_note_ref_hits(markdown, note_start):
    statement_part = str(markdown or "")[:note_start]
    hits = {}
    table_iter = list(re.finditer(r"<table\b.*?</table>", statement_part, flags=re.IGNORECASE | re.DOTALL))
    for table_index, match in enumerate(table_iter, start=1):
        table_html = match.group(0)
        try:
            grid = financial_parse_html_table(table_html)
        except Exception:
            grid = []
        if len(grid) < 2:
            continue
        header_rows = min(4, len(grid))
        note_columns = []
        for row_pos, row in enumerate(grid[:header_rows]):
            for col_idx, cell in enumerate(row):
                cell_text = _strip_html(str(cell or "")).strip()
                if cell_text in {"附注", "注释", "附注号", "注释号", "注"} or re.fullmatch(r"附注[一二三四五六七八九十]+", cell_text):
                    note_columns.append((col_idx, _canonical_financial_note_ref(cell_text)))
        if not note_columns:
            continue
        note_col_indexes = sorted({col for col, _section in note_columns})
        row_line_base = statement_part.count("\n", 0, match.start()) + 1
        for row_idx, row in enumerate(grid[1:], start=1):
            first_nonempty = next((_strip_html(str(cell or "")).strip() for cell in row if _strip_html(str(cell or "")).strip()), "")
            canonical = _canonical_item_name_from_alias(first_nonempty)
            if not canonical:
                continue
            note_ref = None
            note_alias = ""
            for col_idx in note_col_indexes:
                if col_idx >= len(row):
                    continue
                candidate = _canonical_financial_note_ref(row[col_idx])
                if candidate:
                    note_ref = candidate
                    note_alias = _strip_html(str(row[col_idx] or "")).strip()
                    break
            if not note_ref:
                continue
            table_unit_scale = _financial_unit_scale_from_text(table_html)
            statement_values = _financial_statement_values_from_table_row(
                row,
                skip_columns=note_col_indexes,
                unit_scale=table_unit_scale,
            )
            hits[canonical] = {
                "canonical_name": canonical,
                "matched_alias": first_nonempty,
                "line": row_line_base,
                "table_index": table_index,
                "note_ref": note_ref,
                "note_ref_raw": note_alias,
                "source": "statement_note_column",
                "row_index": row_idx,
                "statement_values": statement_values[:8],
            }
    return hits


def _financial_statement_table_alias_hits(markdown, note_start):
    statement_part = str(markdown or "")[:note_start]
    hits = {}
    statement_heading_re = re.compile(
        r"(合并资产负债表|资产负债表|合并利润表|利润表|合并现金流量表|现金流量表|"
        r"CONSOLIDATED\\s+STATEMENT|STATEMENT\\s+OF\\s+FINANCIAL\\s+POSITION)",
        flags=re.IGNORECASE,
    )
    table_iter = list(re.finditer(r"<table\b.*?</table>", statement_part, flags=re.IGNORECASE | re.DOTALL))
    for table_index, match in enumerate(table_iter, start=1):
        before = _strip_html(statement_part[max(0, match.start() - 900) : match.start()])
        after = _strip_html(statement_part[match.end() : min(len(statement_part), match.end() + 160)])
        table_text = _strip_html(match.group(0))
        has_statement_context = bool(statement_heading_re.search(before))
        has_period = bool(re.search(r"20\d{2}年|20\d{2}|12月31日|本年|上年|本期|上期", table_text[:1200]))
        if not has_statement_context or not has_period:
            continue
        if re.search(r"(附注|注释|项目注释|项目附注|财务报表附注)", after[:120]):
            # The next table after a note heading belongs to notes, not the
            # statement area, even if the previous heading is still nearby.
            continue
        try:
            grid = financial_parse_html_table(match.group(0))
        except Exception:
            grid = []
        if len(grid) < 2:
            continue
        unit_scale = _financial_unit_scale_from_text(match.group(0))
        row_line_base = statement_part.count("\n", 0, match.start()) + 1
        for row_idx, row in enumerate(grid[1:], start=1):
            first_nonempty = next((_strip_html(str(cell or "")).strip() for cell in row if _strip_html(str(cell or "")).strip()), "")
            canonical = _canonical_item_name_from_alias(first_nonempty)
            if not canonical or canonical in hits:
                continue
            statement_values = _financial_statement_values_from_table_row(row, unit_scale=unit_scale)
            hits[canonical] = {
                "canonical_name": canonical,
                "matched_alias": first_nonempty,
                "line": row_line_base,
                "table_index": table_index,
                "source": "statement_table_alias",
                "row_index": row_idx,
                "statement_values": statement_values[:8],
            }
    return hits


def _financial_note_title_hits(markdown):
    text = str(markdown or "")
    note_start = _financial_note_zone_start(text)
    note_part = text[note_start:]
    hits = {}
    offset = 0
    for raw_line in note_part.splitlines(True):
        hit = _financial_note_title_line_hit(raw_line)
        if not hit:
            offset += len(raw_line)
            continue
        canonical = hit["canonical_name"]
        if canonical not in hits:
            absolute_pos = note_start + offset
            hits[canonical] = {
                "canonical_name": canonical,
                "matched_alias": hit["title"],
                "title": _compact_text_fragment(hit["title"], 160),
                "line": text.count("\n", 0, absolute_pos) + 1,
            }
        offset += len(raw_line)
    return hits


def _financial_note_title_tree(markdown):
    text = str(markdown or "")
    page_markers = _pdf_page_markers_by_line(text)
    note_start = _financial_note_zone_start(text)
    lines = text.splitlines()
    tree = {}
    current_section = None
    current_scope = ""
    for line_number, line in enumerate(lines, start=1):
        if line_number < text.count("\n", 0, note_start) + 1:
            continue
        raw_line = _strip_html(line).strip()
        if not raw_line:
            continue
        section_match = re.match(
            r"^(?:#{1,6}\s*)?([一二三四五六七八九十]{1,3})(?:[、.．]|\s+)"
            r"\s*(.*(?:财务报表|报表).*(?:附注|注释).*)$",
            raw_line,
        )
        if section_match:
            current_section = section_match.group(1)
            section_title = section_match.group(2)
            if "母公司" in section_title or "公司财务报表" in section_title:
                current_scope = "parent_company"
            elif "合并" in section_title or "集团" in section_title:
                current_scope = "consolidated"
            else:
                current_scope = ""
            continue
        if len(raw_line) > 100:
            continue
        line_hit = _financial_note_title_line_hit(raw_line)
        if not line_hit:
            continue
        title = line_hit["title"]
        canonical = line_hit["canonical_name"]
        note_ref = _canonical_financial_note_ref(line_hit.get("note_key"), current_section=current_section)
        if not note_ref:
            continue
        page_number, reason = _inferred_pdf_page_for_line(line_number, page_markers)
        tree[note_ref] = {
            "note_ref": note_ref,
            "numeric_key": _note_ref_numeric_key(note_ref),
            "section": current_section,
            "scope": current_scope,
            "canonical_name": canonical,
            "title": title,
            "line": line_number,
            "pdf_page_number": page_number,
            "pdf_page_source": "markdown_marker_inferred" if page_number else "",
            "pdf_page_inference_reason": reason if page_number else "",
            "source": "markdown_note_title_tree",
        }
    return tree


def _financial_note_slice(markdown, note):
    lines = str(markdown or "").splitlines()
    start_line = int((note or {}).get("line") or 0)
    if start_line <= 0 or start_line > len(lines):
        return ""
    end_line = len(lines) + 1
    for line_number in range(start_line + 1, len(lines) + 1):
        raw_line = _strip_html(lines[line_number - 1]).strip()
        if len(raw_line) > 100:
            continue
        if _financial_note_title_line_hit(raw_line):
            end_line = line_number
            break
        if re.match(
            r"^(?:#{1,6}\s*)?[一二三四五六七八九十]{1,3}(?:[、.．]|\s+)\s*.*(?:财务报表|报表).*(?:附注|注释)",
            raw_line,
        ):
            end_line = line_number
            break
    # Large annual-report notes can span many pages. Use a generous local
    # evidence window so multi-page note tables are covered without scanning
    # unrelated later notes indefinitely.
    return "\n".join(lines[start_line - 1 : min(end_line - 1, start_line + 420)])


def _amount_candidates_from_note_slice(note_slice):
    text = str(note_slice or "")[:360000]
    unit_scale = _financial_unit_scale_from_text(text)
    candidates = []
    table_iter = list(re.finditer(r"<table\b.*?</table>", text, flags=re.IGNORECASE | re.DOTALL))
    max_candidates = 240
    for table_pos, match in enumerate(table_iter[:24], start=1):
        try:
            grid = financial_parse_html_table(match.group(0))
        except Exception:
            grid = []
        table_unit_scale = _financial_unit_scale_from_text(match.group(0))
        if table_unit_scale == 1.0:
            table_unit_scale = _financial_unit_scale_near(text, match.start())
        if table_unit_scale == 1.0:
            table_unit_scale = unit_scale
        for row_idx, row in enumerate(grid):
            row_label = _compact_text_fragment(" ".join(_strip_html(str(cell or "")) for cell in row[:2]), 80)
            for col_idx, cell in enumerate(row):
                amount = _parse_financial_amount_cell(cell)
                if amount is None:
                    continue
                candidates.append(
                    {
                        "source": "note_table",
                        "table_position": table_pos,
                        "row_index": row_idx,
                        "column_index": col_idx,
                        "row_label": row_label,
                        "raw": _strip_html(str(cell or "")).strip(),
                        "value": amount,
                        "normalized_value": _normalize_amount_for_compare(amount, table_unit_scale),
                        "unit_scale": table_unit_scale,
                    }
                )
                if len(candidates) >= max_candidates:
                    break
            if len(candidates) >= max_candidates:
                break
        if len(candidates) >= max_candidates:
            break
    without_tables = re.sub(r"<table\b.*?</table>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    for match in re.finditer(r"[（(]?-?\d{1,3}(?:[,，]\d{3})*(?:\.\d+)?[）)]?|-?\d+(?:\.\d+)?", without_tables):
        amount = _parse_financial_amount_cell(match.group(0))
        if amount is None:
            continue
        candidates.append(
            {
                "source": "note_text",
                "raw": match.group(0),
                "value": amount,
                "normalized_value": _normalize_amount_for_compare(amount, unit_scale),
                "unit_scale": unit_scale,
            }
        )
        if len(candidates) >= max_candidates:
            break
    return candidates[:max_candidates]


def _build_financial_note_amount_check(item, note, markdown):
    statement_values = item.get("statement_values") or []
    if not statement_values:
        return {
            "status": "no_statement_amount",
            "confidence": "none",
            "statement_values": [],
            "note_candidates": [],
            "matched": None,
        }
    note_slice = _financial_note_slice(markdown, note)
    candidates = _amount_candidates_from_note_slice(note_slice)
    sample_statement_values = statement_values[:4]
    sample_note_candidates = candidates[:12]
    for candidate_source, confidence in (("note_table", "high"), ("note_text", "medium")):
        scoped_candidates = [candidate for candidate in candidates if candidate.get("source") == candidate_source]
        for statement_value in statement_values:
            left = statement_value.get("normalized_value")
            for candidate in scoped_candidates:
                matched, detail = _amount_close(left, candidate.get("normalized_value"))
                if not matched:
                    continue
                return {
                    "status": "verified",
                    "confidence": confidence,
                    "statement_values": sample_statement_values,
                    "note_candidates": sample_note_candidates,
                    "matched": {
                        "statement": statement_value,
                        "note": candidate,
                        **(detail or {}),
                    },
                }
    return {
        "status": "unverified",
        "confidence": "low",
        "statement_values": sample_statement_values,
        "note_candidates": sample_note_candidates,
        "matched": None,
    }


def _financial_note_link_precision(link):
    if link.get("confidence") != "high":
        return link.get("confidence") or "medium"
    amount_check = link.get("amount_check") or {}
    if amount_check.get("status") == "verified" and amount_check.get("confidence") == "high":
        return "audit_ready_navigation"
    if amount_check.get("status") == "verified":
        return "high_with_amount_text_match"
    return "high_navigation_unverified_amount"


def _financial_note_amount_summary(links):
    checks = [item.get("amount_check") or {} for item in links]
    return {
        "amount_check_count": len([item for item in checks if item.get("status")]),
        "amount_verified_count": sum(1 for item in checks if item.get("status") == "verified"),
        "amount_verified_table_count": sum(
            1
            for item in checks
            if item.get("status") == "verified" and item.get("confidence") == "high"
        ),
        "amount_unverified_count": sum(1 for item in checks if item.get("status") == "unverified"),
        "amount_no_statement_count": sum(1 for item in checks if item.get("status") == "no_statement_amount"),
    }


def _build_financial_note_links(markdown, tables, page_markers):
    return content_list_enhanced_service.build_financial_note_links(markdown, tables, page_markers)



def _complete_markdown_appendix(enhanced):
    return content_list_enhanced_service.complete_markdown_appendix(enhanced)


def _complete_markdown_content(markdown, enhanced, corrections=None):
    return content_list_enhanced_service.complete_markdown_content(
        markdown,
        enhanced,
        corrections=corrections,
        apply_table_corrections=_apply_table_corrections,
    )


def _write_complete_markdown_artifact(task, markdown, enhanced, corrections=None):
    return content_list_enhanced_service.write_complete_markdown_artifact(
        task,
        markdown,
        enhanced,
        corrections=corrections,
        result_dir=_result_dir,
        apply_table_corrections=_apply_table_corrections,
    )


def _file_reference_payload(path, url=None, kind=None):
    return document_full_service.file_reference_payload(path, url, kind)


def _image_resource_index(task):
    return document_full_service.image_resource_index(task, _result_dir)


def _pdf_page_resource_index(task):
    return document_full_service.pdf_page_resource_index(task, _result_dir)


def _markdown_page_index(markdown, content_list=None):
    text = str(markdown or "")
    markers = _pdf_page_markers_by_line(text)
    if not markers:
        return []
    printed_pages = _printed_page_numbers_by_pdf_page(content_list)
    lines = text.splitlines()
    pages = []
    for idx, marker in enumerate(markers):
        start_line = int(marker.get("line") or 1)
        end_line = int(markers[idx + 1].get("line") or start_line) - 1 if idx + 1 < len(markers) else len(lines)
        page_text = "\n".join(lines[start_line:end_line]).strip()
        pages.append(
            {
                "page_number": marker.get("page_number"),
                "pdf_page_number": marker.get("page_number"),
                "printed_page_number": printed_pages.get(marker.get("page_number")),
                "start_line": start_line,
                "end_line": max(start_line, end_line),
                "text_chars": len(page_text),
                "preview": _compact_text_fragment(_strip_html(page_text), 240),
            }
        )
    return pages


def _build_document_full_json(task, markdown, enhanced, quality_report, financial_data=None, financial_checks=None, table_relations=None):
    return document_full_service.build_document_full_json(
        task,
        markdown,
        enhanced,
        quality_report,
        financial_data=financial_data,
        financial_checks=financial_checks,
        table_relations=table_relations,
        result_dir=_result_dir,
        load_json_artifact=_load_json_artifact,
        artifact_status=_artifact_status,
        markdown_page_index=_markdown_page_index,
        now_iso=_now_iso,
        document_full_schema_version=DOCUMENT_FULL_SCHEMA_VERSION,
    )


def _write_document_full_artifact(task, markdown, enhanced, quality_report, financial_data=None, financial_checks=None, table_relations=None):
    if markdown is None or not isinstance(enhanced, dict):
        return None
    result_dir = _result_dir(task)
    os.makedirs(result_dir, exist_ok=True)
    if table_relations is None:
        table_relations = _ensure_table_relations_artifact(
            task,
            markdown,
            enhanced=enhanced,
            content_list=_load_json_artifact(task, "content_list.json"),
        )
    path = os.path.join(result_dir, "document_full.json")
    payload = _build_document_full_json(
        task,
        markdown,
        enhanced,
        quality_report,
        financial_data=financial_data,
        financial_checks=financial_checks,
        table_relations=table_relations,
    )
    payload.setdefault("artifacts", {})["document_full.json"] = {
        "exists": True,
        "path": path,
        "url": f"/api/artifact/{task['task_id']}/document_full.json",
    }
    payload.setdefault("artifacts", {})["table_relations.json"] = {
        "exists": True,
        "path": _table_relations_path(task),
        "url": f"/api/artifact/{task['task_id']}/table_relations.json",
    }
    _write_json(path, payload)
    return path


def _ensure_content_list_enhanced_artifact(task, markdown):
    result_dir = _result_dir(task)
    path = os.path.join(result_dir, "content_list_enhanced.json")
    enhanced = _load_json_artifact(task, "content_list_enhanced.json")
    if isinstance(enhanced, dict) and int(enhanced.get("schema_version") or 0) >= CONTENT_LIST_ENHANCED_SCHEMA_VERSION:
        return enhanced

    previous_version = int(enhanced.get("schema_version") or 0) if isinstance(enhanced, dict) else 0
    document_full = _load_json_artifact(task, "document_full.json")
    document_enhanced = (document_full or {}).get("content_list_enhanced") if isinstance(document_full, dict) else None
    if isinstance(document_enhanced, dict) and int(document_enhanced.get("schema_version") or 0) >= CONTENT_LIST_ENHANCED_SCHEMA_VERSION:
        enhanced = document_enhanced
    else:
        content_list = _load_json_artifact(task, "content_list.json")
        enhanced = _build_content_list_enhanced(
            markdown,
            content_list=content_list,
            report_year=_detect_report_year(markdown, file_name=task.get("filename")),
        )
    enhanced.update(
        {
            "task_id": task["task_id"],
            "filename": task.get("filename"),
            "generated_at": enhanced.get("generated_at") or _now_iso(),
        }
    )
    _write_json(path, enhanced)
    _write_complete_markdown_artifact(task, markdown, enhanced, corrections=_load_corrections(task))
    table_relations = _ensure_table_relations_artifact(
        task,
        markdown,
        enhanced=enhanced,
        content_list=_load_json_artifact(task, "content_list.json"),
    )
    if isinstance(document_full, dict):
        complete_path = os.path.join(result_dir, "result_complete.md")
        document_full = document_full_service.apply_content_list_enhanced_update_to_document_full(
            document_full,
            task_id=task["task_id"],
            enhanced=enhanced,
            table_relations=table_relations,
            content_list_enhanced_path=path,
            table_relations_path=_table_relations_path(task),
            complete_markdown_path=complete_path,
            complete_markdown_exists=os.path.exists(complete_path),
        )
        _write_json(os.path.join(result_dir, "document_full.json"), document_full)
    elif previous_version:
        financial_data, financial_checks = _ensure_financial_artifacts(task, markdown)
        report = _read_quality_report(task) or _build_quality_report(
            markdown,
            task,
            file_name=task.get("filename"),
            content_list=_load_json_artifact(task, "content_list.json"),
        )
        _write_document_full_artifact(
            task,
            markdown,
            enhanced,
            report,
            financial_data=financial_data,
            financial_checks=financial_checks,
        )
    return enhanced


def _ensure_document_full_artifact(task, markdown, report=None):
    result_dir = _result_dir(task)
    path = os.path.join(result_dir, "document_full.json")
    if os.path.exists(path):
        _ensure_content_list_enhanced_artifact(task, markdown)
        return path
    enhanced = _ensure_content_list_enhanced_artifact(task, markdown)
    financial_data, financial_checks = _ensure_financial_artifacts(task, markdown)
    if report is None:
        report = _read_quality_report(task) or _build_quality_report(
            markdown,
            task,
            file_name=task.get("filename"),
            content_list=_load_json_artifact(task, "content_list.json"),
        )
    return _write_document_full_artifact(
        task,
        markdown,
        enhanced,
        report,
        financial_data=financial_data,
        financial_checks=financial_checks,
    )


def _build_content_list_enhanced(markdown, content_list=None, report_year=None):
    return content_list_enhanced_service.build_content_list_enhanced_payload(
        markdown,
        schema_version=CONTENT_LIST_ENHANCED_SCHEMA_VERSION,
        content_table_sources=content_list_enhanced_service.content_table_sources,
        content_table_source_maps=content_list_enhanced_service.content_table_source_maps,
        pop_unused_content_table_source=content_list_enhanced_service.pop_unused_content_table_source,
        pdf_page_markers_by_line=_pdf_page_markers_by_line,
        printed_page_numbers_by_pdf_page=content_list_enhanced_service.printed_page_numbers_by_pdf_page_map,
        inferred_pdf_page_for_line=content_list_enhanced_service.inferred_pdf_page_for_line,
        strip_html=_strip_html,
        table_structure_signals=_table_structure_signals,
        table_source_confidence=content_list_enhanced_service.table_source_confidence,
        count_table_rows=_count_table_rows,
        count_table_cells=_count_table_cells,
        build_enhanced_page_blocks=_build_enhanced_page_blocks,
        build_enhanced_footnotes=_build_enhanced_footnotes,
        build_enhanced_toc=_build_enhanced_toc,
        build_financial_note_links=_build_financial_note_links,
        build_image_semantic_blocks=_build_image_semantic_blocks,
        build_enhanced_quality_signals=content_list_enhanced_service.build_enhanced_quality_signals,
        content_list=content_list,
        report_year=report_year,
    )


def _build_table_index(markdown, tables, content_list=None, report_year=None):
    table_index = []
    enhanced = _build_content_list_enhanced(markdown, content_list=content_list, report_year=report_year)
    enhanced_by_index = {item.get("table_index"): item for item in enhanced.get("tables", [])}

    for idx, match in enumerate(re.finditer(r"<table\b.*?</table>", markdown, flags=re.IGNORECASE | re.DOTALL), start=1):
        table_html = match.group(0)
        line = markdown.count("\n", 0, match.start()) + 1
        row_count = _count_table_rows(table_html)
        cell_count = _count_table_cells(table_html)
        empty_cells = _count_empty_cells(table_html)
        numeric_cells = _count_numeric_cells(table_html)
        text = _strip_html(table_html)
        context = _table_context(markdown, match.start(), match.end())
        enhanced_source = enhanced_by_index.get(idx) or {}
        pdf_page_source = enhanced_source.get("source") if enhanced_source.get("source") != "unresolved" else ""
        source = {
            "pdf_page_number": enhanced_source.get("pdf_page_number"),
            "pdf_page_index": enhanced_source.get("pdf_page_index"),
            "printed_page_number": enhanced_source.get("printed_page_number"),
            "bbox": enhanced_source.get("bbox") or [],
            "image_path": enhanced_source.get("source_image_path") or "",
            "caption": enhanced_source.get("source_caption") or [],
            "footnote": enhanced_source.get("source_footnote") or [],
        }
        matched_names = _matched_financial_table_names(context, text, source)
        empty_ratio = round(empty_cells / cell_count, 4) if cell_count else 0
        numeric_ratio = round(numeric_cells / cell_count, 4) if cell_count else 0

        reasons = []
        if row_count <= 1:
            reasons.append("single_row")
        if cell_count >= 6 and empty_ratio >= 0.5:
            reasons.append("many_empty_cells")
        if cell_count >= 12 and numeric_ratio < 0.1:
            reasons.append("low_numeric_density")
        if matched_names and row_count <= 2:
            reasons.append("key_table_too_short")

        semantics = _classify_table_semantics(
            context,
            matched_names,
            source,
            numeric_ratio=numeric_ratio,
            row_count=row_count,
        )

        table_index.append(
            {
                "table_index": idx,
                "line": line,
                "rows": row_count,
                "cells": cell_count,
                "empty_cells": empty_cells,
                "empty_ratio": empty_ratio,
                "numeric_cells": numeric_cells,
                "numeric_ratio": numeric_ratio,
                "matched_financial_names": matched_names,
                "heading": context["heading"],
                "unit": context["unit"],
                "pdf_page_index": enhanced_source.get("pdf_page_index"),
                "pdf_page_number": enhanced_source.get("pdf_page_number"),
                "printed_page_number": enhanced_source.get("printed_page_number"),
                "pdf_page_source": pdf_page_source,
                "pdf_page_inference_reason": enhanced_source.get("pdf_page_inference_reason") or "",
                "source_confidence": enhanced_source.get("confidence"),
                "content_table_source_id": enhanced_source.get("content_table_source_id"),
                "bbox": source.get("bbox") or [],
                "source_image_path": source.get("image_path") or "",
                "source_caption": source.get("caption") or [],
                "source_footnote": source.get("footnote") or [],
                "table_type": semantics["table_type"],
                "year_binding_required": semantics["year_binding_required"],
                "report_year": report_year,
                "fact_year": report_year if semantics["table_type"] == "fact" else None,
                "classification_reasons": semantics["classification_reasons"],
                "suspect_reasons": reasons,
                "preview": text[:220],
            }
        )

    return table_index


def _group_key_table_candidates(table_index):
    grouped = {}
    for item in table_index:
        for name in item.get("matched_financial_names", []):
            score = _candidate_score(item, name)
            grouped.setdefault(name, []).append(
                {
                    "table_index": item["table_index"],
                    "line": item["line"],
                    "pdf_page_number": item.get("pdf_page_number"),
                    "pdf_page_source": item.get("pdf_page_source"),
                    "pdf_page_inference_reason": item.get("pdf_page_inference_reason"),
                    "bbox": item.get("bbox") or [],
                    "rows": item["rows"],
                    "cells": item["cells"],
                    "empty_ratio": item["empty_ratio"],
                    "numeric_ratio": item["numeric_ratio"],
                    "heading": item["heading"],
                    "unit": item["unit"],
                    "table_type": item.get("table_type"),
                    "year_binding_required": item.get("year_binding_required"),
                    "report_year": item.get("report_year"),
                    "candidate_group": _candidate_group(name),
                    "candidate_score": score,
                    "confidence": _candidate_confidence(score),
                    "preview": item["preview"],
                    "matched_financial_names": item.get("matched_financial_names", []),
                }
            )

    ordered = {}
    names = [name for name in KEY_TABLE_DISPLAY_ORDER if name in grouped]
    names.extend(sorted(name for name in grouped if name not in set(names)))
    for name in names:
        rows = sorted(
            grouped[name],
            key=lambda row: (-(row.get("candidate_score") or 0), row.get("table_index") or 0),
        )[:5]
        for idx, row in enumerate(rows):
            row["is_primary"] = idx == 0
        ordered[name] = rows
    return ordered


def _candidate_summary_list(key_table_candidates, names):
    return quality_service.candidate_summary_list(key_table_candidates, names)


def _required_core_financial_table_names(report_kind):
    return quality_service.required_core_financial_table_names(report_kind)


def _priority_review_tables(table_index, core_candidates, key_table_candidates):
    return quality_service.priority_review_tables(table_index, core_candidates, key_table_candidates)


def _market_quality_profile(market, markdown, filename, table_index, report_year, report_kind):
    market = str(market or "").upper()
    if market == "JP":
        import jp_market_profile as jp

        profile_report_kind = jp.detect_jp_report_kind(markdown, filename=filename)
        financial_tables = jp.core_financial_table_names_for_report(profile_report_kind)
        return {
            "market": "JP",
            "market_profile": "JP",
            "profile_rule_version": getattr(jp, "JP_PROFILE_RULE_VERSION", "jp-pdf-profile"),
            "report_kind": profile_report_kind,
            "report_year": report_year,
            "key_sections": jp.JP_KEY_SECTIONS,
            "financial_tables": financial_tables,
            "indicator_tables": jp.JP_INDICATOR_TABLE_NAMES,
            "found_sections": jp.found_sections(markdown, table_index),
            "key_table_candidates": jp.group_jp_key_table_candidates(table_index, report_kind=profile_report_kind),
            "candidate_summary_list": jp.candidate_summary_list,
            "quality_messages": lambda **kwargs: jp.jp_quality_report_messages(
                report_kind=profile_report_kind,
                **kwargs,
            ),
        }
    if market == "KR":
        import kr_market_profile as kr

        return {
            "market": "KR",
            "market_profile": "KR",
            "profile_rule_version": kr.KR_PROFILE_RULE_VERSION,
            "report_kind": kr.detect_kr_report_kind(markdown, filename=filename),
            "report_year": report_year,
            "key_sections": kr.KR_KEY_SECTIONS,
            "financial_tables": kr.KR_CORE_FINANCIAL_TABLE_NAMES,
            "indicator_tables": kr.KR_INDICATOR_TABLE_NAMES,
            "found_sections": kr.found_sections(markdown, table_index),
            "key_table_candidates": kr.group_kr_key_table_candidates(table_index),
            "candidate_summary_list": kr.candidate_summary_list,
            "quality_messages": kr.kr_quality_report_messages,
        }
    if market == "EU":
        import eu_market_profile as eu

        profile_report_year = eu.detect_eu_report_year(markdown, filename=filename) or report_year
        profile_report_kind = eu.detect_eu_report_kind(markdown, filename=filename)
        return {
            "market": "EU",
            "market_profile": "EU",
            "profile_rule_version": eu.EU_PROFILE_RULE_VERSION,
            "accounting_standard": eu.EU_DEFAULT_ACCOUNTING_STANDARD,
            "report_kind": profile_report_kind,
            "report_year": profile_report_year,
            "key_sections": eu.EU_KEY_SECTIONS,
            "financial_tables": eu.EU_CORE_FINANCIAL_TABLE_NAMES,
            "indicator_tables": eu.EU_INDICATOR_TABLE_NAMES,
            "found_sections": eu.found_sections(markdown, table_index),
            "key_table_candidates": eu.group_eu_key_table_candidates(table_index),
            "candidate_summary_list": eu.candidate_summary_list,
            "quality_messages": lambda **kwargs: eu.eu_quality_report_messages(
                report_kind=profile_report_kind,
                **kwargs,
            ),
        }
    if market == "US":
        import us_market_profile as us

        profile_report_kind = us.detect_us_report_kind(markdown, filename=filename)
        return {
            "market": "US",
            "market_profile": "US",
            "profile_rule_version": us.US_PROFILE_RULE_VERSION,
            "accounting_standard": us.US_DEFAULT_ACCOUNTING_STANDARD,
            "report_kind": profile_report_kind,
            "report_year": report_year,
            "key_sections": us.US_KEY_SECTIONS,
            "financial_tables": us.US_CORE_FINANCIAL_TABLE_NAMES,
            "indicator_tables": us.US_INDICATOR_TABLE_NAMES,
            "found_sections": us.found_sections(markdown, table_index),
            "key_table_candidates": us.group_us_key_table_candidates(table_index),
            "candidate_summary_list": us.candidate_summary_list,
            "quality_messages": lambda **kwargs: us.us_quality_report_messages(
                report_kind=profile_report_kind,
                **kwargs,
            ),
        }
    return None


def _build_quality_report(markdown, task, file_name=None, content_list=None):
    markdown = markdown or ""
    tables = re.findall(r"<table\b.*?</table>", markdown, flags=re.IGNORECASE | re.DOTALL)
    resolved_filename = file_name or task.get("filename")
    market = financial_service.detect_market(task, resolved_filename)
    report_year = _detect_report_year(markdown, file_name=resolved_filename)
    if market == "EU":
        import eu_market_profile as eu

        report_year = eu.detect_eu_report_year(markdown, filename=resolved_filename) or report_year
    table_index = _build_table_index(markdown, tables, content_list=content_list, report_year=report_year)
    single_row_tables = [table for table in tables if _count_table_rows(table) <= 1]
    empty_cell_count = sum(_count_empty_cells(table) for table in tables)

    report_kind = _detect_report_kind(markdown, filename=resolved_filename)
    profile = _market_quality_profile(market, markdown, resolved_filename, table_index, report_year, report_kind)
    if profile:
        report_kind = profile["report_kind"]
        report_year = profile["report_year"]
        financial_tables = profile["financial_tables"]
        found_sections = profile["found_sections"]
        key_sections = profile["key_sections"]
        key_table_candidates = profile["key_table_candidates"]
        core_financial_table_candidates = profile["candidate_summary_list"](key_table_candidates, financial_tables)
        indicator_table_candidates = profile["candidate_summary_list"](
            key_table_candidates,
            profile["indicator_tables"],
        )
    else:
        financial_tables = _required_core_financial_table_names(report_kind)
        found_sections = [section for section in KEY_SECTIONS if section in markdown]
        key_sections = KEY_SECTIONS
        key_table_candidates = _group_key_table_candidates(table_index)
        core_financial_table_candidates = _candidate_summary_list(key_table_candidates, financial_tables)
        indicator_table_candidates = _candidate_summary_list(key_table_candidates, INDICATOR_TABLE_NAMES)
    suspicious_tables = _priority_review_tables(
        table_index,
        core_financial_table_candidates,
        key_table_candidates,
    )

    image_refs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", markdown)
    report = quality_service.build_quality_report_payload(
        task=task,
        filename=resolved_filename,
        schema_version=QUALITY_SCHEMA_VERSION,
        report_kind=report_kind,
        report_year=report_year,
        markdown_chars=len(markdown),
        tables=tables,
        table_index=table_index,
        single_row_tables=single_row_tables,
        empty_cell_count=empty_cell_count,
        image_refs=image_refs,
        found_sections=found_sections,
        key_sections=key_sections,
        key_table_candidates=key_table_candidates,
        core_financial_table_candidates=core_financial_table_candidates,
        indicator_table_candidates=indicator_table_candidates,
        suspicious_tables=suspicious_tables,
        generated_at=_now_iso(),
    )
    if profile:
        found_core_table_count = len(
            [item for item in core_financial_table_candidates if item.get("status") == "found"]
        )
        warnings, info_messages = profile["quality_messages"](
            table_count=len(tables),
            single_row_table_count=len(single_row_tables),
            image_ref_count=len(image_refs),
            found_core_table_count=found_core_table_count,
            suspicious_table_count=len(suspicious_tables),
        )
        report["market"] = profile["market"]
        report["market_profile"] = profile["market_profile"]
        report["profile_rule_version"] = profile["profile_rule_version"]
        if profile.get("accounting_standard"):
            report["accounting_standard"] = profile["accounting_standard"]
        report["warnings"] = warnings
        report["info_messages"] = _unique_preserve_order(list(report.get("info_messages") or []) + info_messages)
    return report


def _write_quality_artifacts(task, markdown, file_name=None, content_list=None, saved_image_count=None):
    result_dir = _result_dir(task)
    os.makedirs(result_dir, exist_ok=True)
    report_year = _detect_report_year(markdown, file_name=file_name or task.get("filename"))
    enhanced_content_list = _build_content_list_enhanced(
        markdown,
        content_list=content_list,
        report_year=report_year,
    )
    enhanced_content_list.update(
        {
            "task_id": task["task_id"],
            "filename": file_name or task.get("filename"),
            "generated_at": _now_iso(),
        }
    )
    report = _build_quality_report(
        markdown,
        task,
        file_name=file_name or task.get("filename"),
        content_list=content_list,
    )
    if saved_image_count is not None:
        report["saved_image_count"] = saved_image_count
    try:
        financial_data, financial_checks = _write_financial_artifacts(
            task,
            markdown,
            file_name=file_name or task.get("filename"),
        )
        report = _merge_quality_candidates_from_financial_data(report, financial_data)
        report = _sync_quality_profile_from_financial_data(report, financial_data, financial_checks)
        report["financial_summary"] = financial_checks.get("summary", {})
        report["financial_overall_status"] = financial_checks.get("overall_status")
        report["financial_statement_count"] = financial_data.get("summary", {}).get("statement_count", 0)
        report["financial_key_metric_count"] = financial_data.get("summary", {}).get("key_metric_count", 0)
        report["warnings"] = _quality_report_warnings(report, financial_data)
    except Exception as exc:
        report["financial_summary"] = {"error": str(exc)}
        report["financial_overall_status"] = "error"
        financial_data = None
        financial_checks = None
    _write_json(os.path.join(result_dir, "content_list_enhanced.json"), enhanced_content_list)
    _write_complete_markdown_artifact(task, markdown, enhanced_content_list)
    quality_service.write_quality_report_files(task, report, _result_dir, _write_json)
    table_relations = _write_table_relations_artifact(
        task,
        markdown,
        enhanced=enhanced_content_list,
        content_list=content_list,
    )
    _write_document_full_artifact(
        task,
        markdown,
        enhanced_content_list,
        report,
        financial_data=financial_data,
        financial_checks=financial_checks,
        table_relations=table_relations,
    )
    return report


def _financial_data_path(task):
    return financial_service.financial_data_path(task, _result_dir)


def _financial_checks_path(task):
    return financial_service.financial_checks_path(task, _result_dir)


def _read_financial_artifacts(task):
    return financial_service.read_financial_artifacts(task, _result_dir)


def _financial_artifacts_are_current(financial_data, financial_checks):
    return financial_service.financial_artifacts_are_current(financial_data, financial_checks)


def _write_financial_artifacts(task, markdown, file_name=None):
    return financial_service.write_financial_artifacts(
        task,
        markdown,
        result_dir=_result_dir,
        write_json=_write_json,
        financial_llm_cache_folder=FINANCIAL_LLM_CACHE_FOLDER,
        file_name=file_name,
    )


def _ensure_financial_artifacts(task, markdown):
    return financial_service.ensure_financial_artifacts(
        task,
        markdown,
        result_dir=_result_dir,
        write_json=_write_json,
        financial_llm_cache_folder=FINANCIAL_LLM_CACHE_FOLDER,
        file_name=task.get("filename"),
    )


def _save_mineru_artifacts(task, upstream_response, file_name, file_data, markdown):
    result = mineru_result_service.save_mineru_result_artifacts(
        task,
        upstream_response,
        file_name,
        file_data,
        result_dir=_result_dir,
        write_json=_write_json,
        save_images=_save_images,
    )
    content_list = file_data.get("content_list") if isinstance(file_data, dict) else None
    quality_report = _write_quality_artifacts(
        task,
        markdown,
        file_name=file_name,
        content_list=content_list,
        saved_image_count=result["image_count"],
    )
    return quality_report


def _quality_report_path(task):
    return quality_service.quality_report_path(task, _result_dir)


def _read_quality_report(task):
    return quality_service.read_quality_report(task, _result_dir, _read_json_cached)


def _market_profile_candidate_names(market):
    market = str(market or "").upper()
    if market == "HK":
        import hk_quality_adapter as hk

        return set(hk.HK_STATEMENT_LABELS.values())
    if market == "JP":
        import jp_market_profile as jp

        return set(jp.JP_CORE_FINANCIAL_TABLE_NAMES)
    if market == "KR":
        import kr_market_profile as kr

        return set(kr.KR_CORE_FINANCIAL_TABLE_NAMES)
    if market == "EU":
        import eu_market_profile as eu

        return set(eu.EU_CORE_FINANCIAL_TABLE_NAMES)
    if market == "US":
        import us_market_profile as us

        return set(us.US_CORE_FINANCIAL_TABLE_NAMES)
    return set()


def _market_profile_rule_version(market):
    market = str(market or "").upper()
    if market == "JP":
        import jp_market_profile as jp

        return getattr(jp, "JP_PROFILE_RULE_VERSION", "")
    if market == "EU":
        import eu_market_profile as eu

        return getattr(eu, "EU_PROFILE_RULE_VERSION", "")
    if market == "US":
        import us_market_profile as us

        return getattr(us, "US_PROFILE_RULE_VERSION", "")
    if market == "KR":
        import kr_market_profile as kr

        return getattr(kr, "KR_PROFILE_RULE_VERSION", "")
    return ""


def _quality_report_matches_market_profile(report, expected_market):
    expected_market = str(expected_market or "").upper()
    if expected_market not in {"HK", "JP", "KR", "EU", "US"}:
        return True
    if not isinstance(report, dict):
        return False
    expected_rule_version = _market_profile_rule_version(expected_market)
    if expected_rule_version and report.get("profile_rule_version") != expected_rule_version:
        return False
    expected_names = _market_profile_candidate_names(expected_market)
    if not expected_names:
        return True
    names = {
        str(item.get("name") or "")
        for item in (report.get("core_financial_table_candidates") or [])
        if isinstance(item, dict)
    }
    return bool(names) and names.issubset(expected_names)


def _ensure_quality_report(task, markdown):
    financial_data, financial_checks = _ensure_financial_artifacts(task, markdown)
    report = _read_quality_report(task)
    expected_market = financial_service.detect_market(task, task.get("filename"))
    cached_market = str(report.get("market") or report.get("market_profile") or "").upper() if isinstance(report, dict) else ""
    market_profile_matches = expected_market not in {"HK", "JP", "KR", "EU", "US"} or cached_market == expected_market
    candidate_profile_matches = _quality_report_matches_market_profile(report, expected_market)
    if (
        isinstance(report, dict)
        and report.get("schema_version") == QUALITY_SCHEMA_VERSION
        and market_profile_matches
        and candidate_profile_matches
    ):
        original_fields = {
            "found_financial_tables": report.get("found_financial_tables"),
            "core_financial_table_candidates": report.get("core_financial_table_candidates"),
            "financial_summary": report.get("financial_summary"),
            "financial_overall_status": report.get("financial_overall_status"),
            "financial_statement_count": report.get("financial_statement_count"),
            "financial_key_metric_count": report.get("financial_key_metric_count"),
            "profile_rule_version": report.get("profile_rule_version"),
            "accounting_standard": report.get("accounting_standard"),
            "detected_currencies": report.get("detected_currencies"),
            "currency": report.get("currency"),
            "unit": report.get("unit"),
            "warnings": report.get("warnings"),
        }
        report = _merge_quality_candidates_from_financial_data(report, financial_data)
        report = _sync_quality_profile_from_financial_data(report, financial_data, financial_checks)
        report["financial_summary"] = financial_checks.get("summary", {})
        report["financial_overall_status"] = financial_checks.get("overall_status")
        report["financial_statement_count"] = financial_data.get("summary", {}).get("statement_count", 0)
        report["financial_key_metric_count"] = financial_data.get("summary", {}).get("key_metric_count", 0)
        report["warnings"] = _quality_report_warnings(report, financial_data)
        refreshed_fields = {
            "found_financial_tables": report.get("found_financial_tables"),
            "core_financial_table_candidates": report.get("core_financial_table_candidates"),
            "financial_summary": report.get("financial_summary"),
            "financial_overall_status": report.get("financial_overall_status"),
            "financial_statement_count": report.get("financial_statement_count"),
            "financial_key_metric_count": report.get("financial_key_metric_count"),
            "profile_rule_version": report.get("profile_rule_version"),
            "accounting_standard": report.get("accounting_standard"),
            "detected_currencies": report.get("detected_currencies"),
            "currency": report.get("currency"),
            "unit": report.get("unit"),
            "warnings": report.get("warnings"),
        }
        if refreshed_fields != original_fields:
            quality_service.write_quality_report_files(task, report, _result_dir, _write_json)
        return report
    report = _build_quality_report(
        markdown,
        task,
        file_name=task.get("filename"),
        content_list=_load_json_artifact(task, "content_list.json"),
    )
    report = _merge_quality_candidates_from_financial_data(report, financial_data)
    report = _sync_quality_profile_from_financial_data(report, financial_data, financial_checks)
    report["financial_summary"] = financial_checks.get("summary", {})
    report["financial_overall_status"] = financial_checks.get("overall_status")
    report["financial_statement_count"] = financial_data.get("summary", {}).get("statement_count", 0)
    report["financial_key_metric_count"] = financial_data.get("summary", {}).get("key_metric_count", 0)
    report["warnings"] = _quality_report_warnings(report, financial_data)
    quality_service.write_quality_report_files(task, report, _result_dir, _write_json)
    return report


def _artifact_status(task):
    return artifact_service.artifact_status(task, results_folder=RESULTS_FOLDER)


ARTIFACT_OPEN_ALLOWLIST = artifact_service.ARTIFACT_OPEN_ALLOWLIST


def _artifact_file_response(path, mimetype):
    response = send_file(path, mimetype=mimetype, as_attachment=False)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _image_artifact_names(images_dir):
    return artifact_service.image_artifact_names(images_dir)


def _markdown_excerpt(markdown, line, radius=12):
    return artifact_service.markdown_excerpt(markdown, line, radius=radius)


def _table_html_by_index(markdown, table_index):
    return artifact_service.table_html_by_index(markdown, table_index)


def _apply_table_corrections(markdown, corrections):
    return artifact_service.apply_table_corrections(markdown, corrections)


def _fetch_and_cache_result(task, force=False):
    local_markdown = _read_markdown(task)
    if local_markdown is not None and (not force or not task.get("mineru_task_id")):
        return local_markdown

    mineru_task_id = task.get("mineru_task_id")
    if not mineru_task_id:
        if _task_requires_markdown_artifact(task) and local_markdown is None:
            return artifact_orchestrator_service.completed_missing_local_markdown(
                task,
                mark_completed_missing_artifact=_mark_completed_missing_artifact,
            )
        return None

    result_url = f"{MINERU_API_BASE}/tasks/{mineru_task_id}/result"
    resp = _json_request(result_url, timeout=30)
    if resp.get("_error"):
        if _task_requires_markdown_artifact(task) and local_markdown is None:
            return artifact_orchestrator_service.missing_local_markdown_error(
                task,
                resp,
                mark_completed_missing_artifact=_mark_completed_missing_artifact,
            )
        return {"_error": True, "detail": resp.get("detail", "Failed to fetch result")}

    return artifact_orchestrator_service.cache_mineru_result_artifacts(
        task,
        resp,
        local_markdown=local_markdown,
        task_requires_markdown_artifact=_task_requires_markdown_artifact,
        mark_completed_missing_artifact=_mark_completed_missing_artifact,
        inject_pdf_page_markers=_inject_pdf_page_markers,
        backfill_sparse_markdown_pages=_backfill_sparse_markdown_pages,
        write_markdown=_write_markdown,
        save_mineru_artifacts=_save_mineru_artifacts,
        append_log=_append_log,
        now_iso=_now_iso,
        persist_task=_persist_task,
    )


def _build_status_response(task, logs_slice=None):
    elapsed = _task_elapsed_seconds(task)
    page_progress = _calc_page_progress(task, elapsed)
    progress_percent = _calc_progress_percent(task, elapsed)
    return response_service.build_status_response_payload(
        task,
        elapsed_seconds=elapsed,
        page_progress=page_progress,
        progress_percent=progress_percent,
        markdown_ready=_has_markdown_artifact(task),
        local_queue_position=_local_queue_position(task["task_id"]),
        logs_slice=logs_slice,
    )


def _refresh_task_from_upstream(task):
    if task.get("cancelled"):
        return task
    if task.get("status") == COMPLETED:
        if not _has_markdown_artifact(task):
            _fetch_and_cache_result(task)
        return task
    if task.get("status") == COMPLETED_MISSING_ARTIFACT:
        return task

    mineru_task_id = task.get("mineru_task_id")
    if not mineru_task_id:
        return task

    now = time.time()
    if task.get("last_status_payload") and task.get("last_polled_at"):
        if now - float(task["last_polled_at"]) < STATUS_CACHE_SECONDS:
            payload = task["last_status_payload"]
            task["queue_position"] = payload.get("queued_ahead")
            return task

    status_url = f"{MINERU_API_BASE}/tasks/{mineru_task_id}"
    resp = _json_request(status_url, timeout=MINERU_STATUS_TIMEOUT_SECONDS)
    if resp.get("_error") and resp.get("status") == 404:
        task["status"] = FAILED
        task["stage"] = FAILED
        task["completed_at"] = task.get("completed_at") or _now_iso()
        task["error"] = "上游任务不存在，可能已因 MinerU 重启或清理而失效"
        _append_log(task, task["error"], "warn")
        _persist_task(task)
        return task
    if resp.get("_error"):
        task["last_status_payload"] = None
        raise RuntimeError(resp.get("detail", "Unknown upstream status error"))

    task["consecutive_status_failures"] = 0
    task["last_status_payload"] = resp
    task["last_polled_at"] = now
    task["queue_position"] = resp.get("queued_ahead")

    raw_status = resp.get("status") or resp.get("state") or resp.get("task_status", "unknown")
    new_status = raw_status.lower() if isinstance(raw_status, str) else "unknown"
    old_status = task.get("status")

    if new_status != old_status:
        task["status"] = new_status
        if new_status == "processing" and old_status in ("pending", "submitted", "uploaded"):
            task["stage"] = "processing"
            task["started_at"] = task.get("started_at") or task.get("submitted_at") or _now_iso()
            _append_log(task, "MinerU 开始处理 PDF", "info")
        elif new_status == COMPLETED:
            task["stage"] = COMPLETED
            task["completed_at"] = _now_iso()
            _append_log(task, "PDF 解析完成", "success")
        elif new_status in ("failed", "error", "failure"):
            task["stage"] = "failed"
            task["error"] = resp.get("error") or resp.get("message") or resp.get("detail", "Unknown error")
            _append_log(task, f"解析失败: {task['error']}", "error")

    if new_status == "processing":
        task["stage"] = "processing"
        if not task.get("started_at"):
            task["started_at"] = task.get("submitted_at") or _now_iso()
            _append_log(task, "已恢复处理中任务，继续同步进度", "info")

    if task.get("status") == "processing" and task.get("started_at"):
        elapsed = _task_elapsed_seconds(task)
        if elapsed is not None:
            try:
                last_log_time = task.get("last_progress_log_time")
                if last_log_time:
                    last_logged = datetime.fromisoformat(last_log_time.replace("Z", "+00:00"))
                    seconds_since_last = int((_utc_now() - last_logged.replace(tzinfo=None)).total_seconds())
                else:
                    seconds_since_last = elapsed + 1

                if seconds_since_last >= 60:
                    task["last_progress_log_time"] = _now_iso()
                    page_info = _calc_page_progress(task, elapsed)
                    if page_info:
                        _append_log(
                            task,
                            f"处理中... 已耗时 {_format_duration(elapsed)}, 已完成 {page_info['processed']}/{page_info['total']} 页, 还剩 {page_info['remaining']} 页",
                            "info",
                        )
                    else:
                        _append_log(task, f"处理中... 已耗时 {_format_duration(elapsed)}", "info")
            except Exception:
                pass

    if task.get("status") == COMPLETED and not _has_markdown_artifact(task):
        _fetch_and_cache_result(task)

    _persist_task(task)
    return task


@app.before_request
def _prepare_request():
    initialize_app(start_worker=True)
    if not _request_has_valid_token(APP_ACCESS_TOKEN):
        return jsonify({"error": "Unauthorized"}), 401
    return None


@app.errorhandler(413)
def _request_too_large(_error):
    return jsonify({"error": "上传请求过大"}), 413


@app.route("/")
def index():
    _cleanup_old_data()
    response = make_response(render_template("index.html", app_js_version=APP_JS_VERSION))
    if APP_ACCESS_TOKEN and request.args.get("token") == APP_ACCESS_TOKEN:
        response.set_cookie("pdf2md_token", APP_ACCESS_TOKEN, httponly=True, samesite="Lax")
    return response


@app.route("/api/health", methods=["GET"])
def health():
    readiness = _mineru_submit_readiness()
    return jsonify(
        {
            "flask": True,
            "quality_schema_version": QUALITY_SCHEMA_VERSION,
            "content_list_enhanced_schema_version": CONTENT_LIST_ENHANCED_SCHEMA_VERSION,
            "document_full_schema_version": DOCUMENT_FULL_SCHEMA_VERSION,
            "financial_data_schema_version": FINANCIAL_DATA_SCHEMA_VERSION,
            "financial_checks_schema_version": FINANCIAL_CHECKS_SCHEMA_VERSION,
            "financial_rule_version": FINANCIAL_RULE_VERSION,
            "mineru": readiness["mineru"],
            "mineru_detail": readiness["mineru_detail"],
            "mineru_stats": readiness["mineru_payload"] or {},
            "vlm": readiness["vlm"],
            "vlm_detail": readiness["vlm_detail"],
            "submit_ready": readiness["submit_ready"],
            "warning": readiness["warning"],
        }
    )


@app.route("/api/upload", methods=["POST"])
def upload():
    _cleanup_old_data()
    files = request.files.getlist("files")
    if not files:
        single_file = request.files.get("file")
        if single_file:
            files = [single_file]
    files = [file for file in files if file and file.filename]
    if not files:
        return jsonify({"error": "No file provided"}), 400
    if len(files) > MAX_FILES_PER_UPLOAD:
        return jsonify({"error": f"一次最多上传 {MAX_FILES_PER_UPLOAD} 个 PDF"}), 400

    try:
        submit_config = _parse_submit_config(request.form)
        requested_task_id = _safe_task_id(request.form.get("task_id") or request.form.get("taskId"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    display_filenames = []
    seen_filenames = set()
    for file in files:
        display_filename = _safe_client_filename(file.filename)
        if not display_filename.lower().endswith(".pdf"):
            return jsonify({"error": f"仅支持 PDF 文件: {display_filename}"}), 400
        if display_filename in seen_filenames:
            return _duplicate_filename_response(
                display_filename,
                message=f"本次上传中包含重复文件名，请勿重复解析: {display_filename}",
            )
        seen_filenames.add(display_filename)
        if not requested_task_id:
            duplicate_task = _find_duplicate_filename_task(display_filename)
            if duplicate_task:
                duplicate_status = str(duplicate_task.get("status") or "").lower()
                if duplicate_status in {"queued", "uploaded", "submitting", "submitted", "pending", "processing"}:
                    message = f"该文件正在解析或排队中，请勿重复提交: {display_filename}"
                else:
                    message = f"该文件已存在解析任务，请查看已有结果: {display_filename}"
                return _duplicate_filename_response(display_filename, duplicate_task, message=message)
        display_filenames.append(display_filename)

    created_tasks = []
    prepared_uploads = []
    seen_hashes = set()
    for file, display_filename in zip(files, display_filenames):
        local_task_id = requested_task_id or str(uuid.uuid4())
        skip_duplicate_lookup = bool(requested_task_id)
        requested_task_id = None
        upload_path = os.path.join(UPLOAD_FOLDER, f"{local_task_id}.pdf")
        total_size = 0
        digest = hashlib.sha256()

        with open(upload_path, "wb") as outfile:
            while True:
                chunk = file.stream.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > MAX_FILE_SIZE:
                    _cleanup_pending_uploads(prepared_uploads, upload_path)
                    return jsonify({"error": f"文件超过 {MAX_FILE_SIZE // 1024 // 1024} MB 限制: {display_filename}"}), 400
                digest.update(chunk)
                outfile.write(chunk)

        if total_size == 0:
            _cleanup_pending_uploads(prepared_uploads, upload_path)
            return jsonify({"error": f"空文件: {display_filename}"}), 400
        if not _looks_like_pdf(upload_path):
            _cleanup_pending_uploads(prepared_uploads, upload_path)
            return jsonify({"error": f"文件内容不是有效 PDF: {display_filename}"}), 400

        pdf_page_count = _get_pdf_page_count(upload_path)
        if pdf_page_count and submit_config.get("end_page_id") not in (None, ""):
            if int(submit_config["end_page_id"]) >= int(pdf_page_count):
                _cleanup_pending_uploads(prepared_uploads, upload_path)
                return jsonify({"error": f"结束页码超出 PDF 页数: {display_filename} 共 {pdf_page_count} 页"}), 400
        file_sha256 = digest.hexdigest()
        if file_sha256 in seen_hashes:
            _cleanup_pending_uploads(prepared_uploads, upload_path)
            return _duplicate_content_response(
                display_filename,
                message=f"本次上传中包含重复文档内容，请勿重复解析: {display_filename}",
            )
        if not skip_duplicate_lookup:
            duplicate_task = _find_duplicate_file_hash_task(file_sha256)
            if duplicate_task:
                duplicate_status = str(duplicate_task.get("status") or "").lower()
                _cleanup_pending_uploads(prepared_uploads, upload_path)
                if duplicate_status in {"queued", "uploaded", "submitting", "submitted", "pending", "processing"}:
                    message = f"该文档内容正在解析或排队中，请勿重复提交: {display_filename}"
                else:
                    message = f"该文档内容已存在解析任务，请查看已有结果: {display_filename}"
                return _duplicate_content_response(display_filename, duplicate_task, message=message)
        seen_hashes.add(file_sha256)
        prepared_uploads.append(
            {
                "task_id": local_task_id,
                "filename": display_filename,
                "upload_path": upload_path,
                "file_size": total_size,
                "pdf_page_count": pdf_page_count,
                "submit_config": dict(submit_config),
                "file_sha256": file_sha256,
            }
        )

    for prepared in prepared_uploads:
        task = {
            "task_id": prepared["task_id"],
            "mineru_task_id": None,
            "filename": prepared["filename"],
            "file_sha256": prepared["file_sha256"],
            "file_size": prepared["file_size"],
            "pdf_page_count": prepared["pdf_page_count"],
            "status": "queued",
            "stage": "queued",
            "created_at": _now_iso(),
            "uploaded_at": _now_iso(),
            "submitted_at": None,
            "started_at": None,
            "completed_at": None,
            "cancelled": False,
            "error": None,
            "markdown_path": None,
            "upload_path": prepared["upload_path"],
            "last_progress_log_time": None,
            "last_status_payload": None,
            "last_polled_at": None,
            "consecutive_status_failures": 0,
            "queue_position": None,
            "submit_config": prepared["submit_config"],
            "logs": [],
        }
        _append_log(task, f"文件上传成功: {prepared['filename']} ({prepared['file_size'] // 1024 // 1024}MB)", "info")
        _append_log(task, "已加入本地解析队列，等待轮到当前任务。", "info")
        _persist_task(task, allow_insert=True)
        created_tasks.append(
            {
                "task_id": prepared["task_id"],
                "filename": prepared["filename"],
                "pdf_page_count": prepared["pdf_page_count"],
            }
        )

    _wake_queue_worker()
    return jsonify(
        {
            "tasks": created_tasks,
            "task_id": created_tasks[0]["task_id"],
            "batch_count": len(created_tasks),
        }
    )


@app.route("/api/cancel/<task_id>", methods=["POST"])
def cancel_task(task_id):
    task = _get_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    if is_terminal_status(task.get("status")):
        return jsonify({"error": "Task already finished"}), 400

    upstream_cancelled = False
    mineru_task_id = task.get("mineru_task_id")
    if mineru_task_id:
        cancel_resp = _json_request(f"{MINERU_API_BASE}/tasks/{mineru_task_id}", method="DELETE", timeout=10)
        upstream_cancelled = not cancel_resp.get("_error")

    update = task_lifecycle_service.build_cancel_task_update(
        task,
        upstream_cancelled=upstream_cancelled,
        now_iso=_now_iso(),
    )
    task.update(update["patch"])
    _append_log(task, update["log"]["message"], update["log"]["level"])
    _persist_task(task)
    _wake_queue_worker()
    return jsonify(
        {
            "success": True,
            "message": "Stopped tracking this task",
            "upstream_cancelled": upstream_cancelled,
        }
    )


@app.route("/api/refetch/<task_id>", methods=["POST"])
def refetch_result(task_id):
    task = _get_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    markdown = _fetch_and_cache_result(task, force=True)
    refreshed_task = _get_task(task_id) or task
    if isinstance(markdown, dict) and markdown.get("_error"):
        return jsonify(
            {
                "error": markdown["detail"],
                "status": refreshed_task.get("status"),
                "markdown_ready": _has_markdown_artifact(refreshed_task),
            }
        ), 502
    if markdown is None:
        return jsonify(
            {
                "error": "No markdown available yet",
                "status": refreshed_task.get("status"),
                "markdown_ready": _has_markdown_artifact(refreshed_task),
            }
        ), 400

    return jsonify(
        {
            "success": True,
            "status": refreshed_task.get("status"),
            "markdown_ready": _has_markdown_artifact(refreshed_task),
            "markdown_chars": len(markdown),
        }
    )


@app.route("/api/reparse/<task_id>", methods=["POST"])
def reparse_task(task_id):
    source_task = _get_task(task_id)
    if not source_task:
        return jsonify({"error": "Task not found"}), 404

    source_upload_path = source_task.get("upload_path")
    if not source_upload_path or not os.path.exists(source_upload_path):
        return jsonify({"error": "原始 PDF 不存在，无法重新解析"}), 400

    local_task_id = str(uuid.uuid4())
    upload_path = os.path.join(UPLOAD_FOLDER, f"{local_task_id}.pdf")
    shutil.copy2(source_upload_path, upload_path)
    total_size = os.path.getsize(upload_path)
    display_filename = _safe_client_filename(source_task.get("filename") or f"{task_id}.pdf")
    submit_config = dict(source_task.get("submit_config") or {})
    pdf_page_count = source_task.get("pdf_page_count") or _get_pdf_page_count(upload_path)

    task = {
        "task_id": local_task_id,
        "mineru_task_id": None,
        "filename": display_filename,
        "file_sha256": source_task.get("file_sha256") or _sha256_file(upload_path),
        "file_size": total_size,
        "pdf_page_count": pdf_page_count,
        "status": "queued",
        "stage": "queued",
        "created_at": _now_iso(),
        "uploaded_at": _now_iso(),
        "submitted_at": None,
        "started_at": None,
        "completed_at": None,
        "cancelled": False,
        "error": None,
        "markdown_path": None,
        "upload_path": upload_path,
        "last_progress_log_time": None,
        "last_status_payload": None,
        "last_polled_at": None,
        "consecutive_status_failures": 0,
        "queue_position": None,
        "submit_config": submit_config,
        "logs": [],
    }
    _append_log(task, f"从任务 {task_id[:8]} 复制原始 PDF，已创建重新解析任务。", "info")
    _append_log(task, "已加入本地解析队列，等待轮到当前任务。", "info")
    _persist_task(task, allow_insert=True)
    _wake_queue_worker()

    return jsonify(
        {
            "success": True,
            "task_id": local_task_id,
            "filename": display_filename,
            "pdf_page_count": pdf_page_count,
        }
    )


@app.route("/api/status/<task_id>", methods=["GET"])
def status(task_id):
    task = _get_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    since_index = task_lifecycle_service.status_log_since_index(request.args.get("since", "0"))

    if task_lifecycle_service.should_refresh_task_from_upstream(task):
        try:
            task = _refresh_task_from_upstream(task)
        except RuntimeError as exc:
            update = task_lifecycle_service.build_status_failure_update(
                task,
                error_detail=str(exc),
                tolerance=MINERU_STATUS_FAILURE_TOLERANCE,
                now_iso=_now_iso(),
            )
            task.update(update["patch"])
            _append_log(task, update["log"]["message"], update["log"]["level"])
            _persist_task(task)

    _wake_queue_worker()
    logs = task.get("logs", [])
    return jsonify(_build_status_response(task, logs_slice=logs[since_index:]))


@app.route("/api/result/<task_id>", methods=["GET"])
def result(task_id):
    task = _get_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    markdown = _fetch_and_cache_result(task)
    if isinstance(markdown, dict) and markdown.get("_error"):
        return jsonify({"error": markdown["detail"]}), 502
    if markdown is not None:
        report = _ensure_quality_report(task, markdown)
        _ensure_document_full_artifact(task, markdown, report=report)
    return jsonify(response_service.build_result_response_payload(markdown, _artifact_status(task)))


@app.route("/api/quality/<task_id>", methods=["GET"])
def quality(task_id):
    task = _get_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    markdown = _read_markdown(task)
    if markdown is None:
        markdown = _fetch_and_cache_result(task)
        if isinstance(markdown, dict) and markdown.get("_error"):
            return jsonify({"error": markdown["detail"]}), 502
    if markdown is None:
        return jsonify({"error": "No markdown available yet"}), 400
    return jsonify(response_service.build_quality_response_payload(_ensure_quality_report(task, markdown)))


@app.route("/api/financial/<task_id>", methods=["GET"])
def financial(task_id):
    task = _get_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    markdown = _read_markdown(task)
    if markdown is None:
        markdown = _fetch_and_cache_result(task)
        if isinstance(markdown, dict) and markdown.get("_error"):
            return jsonify({"error": markdown["detail"]}), 502
    if markdown is None:
        return jsonify({"error": "No markdown available yet"}), 400
    financial_data, financial_checks = _ensure_financial_artifacts(task, markdown)
    return jsonify(response_service.build_financial_response_payload(financial_data, financial_checks))


@app.route("/api/artifact/<task_id>/<path:artifact_name>", methods=["GET"])
def open_artifact(task_id, artifact_name):
    task = _get_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    result_dir = _result_dir(task)
    artifact_descriptor = artifact_service.classify_open_artifact_name(
        task_id,
        artifact_name,
        result_dir,
        sanitize_filename=_safe_client_filename,
        allowlist=ARTIFACT_OPEN_ALLOWLIST,
    )
    kind = artifact_descriptor["kind"]
    if kind == "images_download":
        images_dir = artifact_descriptor["images_dir"]
        if not os.path.isdir(images_dir):
            return jsonify({"error": "Images artifact not found"}), 404
        image_names = _image_artifact_names(images_dir)
        if not image_names:
            return jsonify({"error": "No downloadable images found"}), 404
        archive = artifact_service.build_images_zip(images_dir, image_names)
        filename = _safe_download_name(artifact_descriptor["download_name"])
        response = send_file(
            archive,
            mimetype="application/zip",
            as_attachment=True,
            download_name=filename,
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    if kind == "images_index":
        images_dir = artifact_descriptor["images_dir"]
        if not os.path.isdir(images_dir):
            return jsonify({"error": "Images artifact not found"}), 404
        image_names = _image_artifact_names(images_dir)
        return jsonify(artifact_service.build_images_index_payload(task_id, image_names))
    if kind == "image_file":
        image_path = artifact_descriptor["path"]
        if not os.path.exists(image_path):
            return jsonify({"error": "Image artifact not found"}), 404
        return _artifact_file_response(image_path, artifact_descriptor["mimetype"])
    if kind == "forbidden":
        return jsonify({"error": "Artifact is not openable"}), 403
    path = artifact_descriptor["path"]
    if not os.path.exists(path):
        return jsonify({"error": "Artifact not found"}), 404
    return _artifact_file_response(path, artifact_descriptor["mimetype"])


@app.route("/api/source/<task_id>/table/<int:table_index>", methods=["GET"])
def table_source(task_id, table_index):
    task = _get_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    markdown = _read_markdown(task)
    if markdown is None:
        markdown = _fetch_and_cache_result(task)
        if isinstance(markdown, dict) and markdown.get("_error"):
            return jsonify({"error": markdown["detail"]}), 502
    if markdown is None:
        return jsonify({"error": "No markdown available yet"}), 400

    report = _ensure_quality_report(task, markdown)
    table_item = source_service.find_source_table(report, table_index)
    if table_item is None:
        return jsonify({"error": "Table source not found"}), 404

    page_content = _page_content_payload(
        task,
        table_item.get("pdf_page_number") or 1,
        report=report,
        focus_table=table_index,
    )
    bbox_extent = _page_bbox_extent(task, table_item.get("pdf_page_index"))
    return jsonify(
        source_service.source_table_payload(
            task_id=task_id,
            task=task,
            table_item=table_item,
            table_html=_table_html_by_index(markdown, table_index),
            markdown_excerpt=_markdown_excerpt(markdown, table_item.get("line"), radius=14),
            artifacts=_artifact_status(task),
            correction=_load_corrections(task).get("tables", {}).get(str(table_index)),
            page_content=page_content,
            bbox_extent=bbox_extent,
        )
    )


@app.route("/api/source/<task_id>/page/<int:page_number>", methods=["GET"])
def source_page(task_id, page_number):
    task = _get_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    markdown = _read_markdown(task)
    if markdown is None:
        markdown = _fetch_and_cache_result(task)
        if isinstance(markdown, dict) and markdown.get("_error"):
            return jsonify({"error": markdown["detail"]}), 502
    if markdown is None:
        return jsonify({"error": "No markdown available yet"}), 400

    focus_table = request.args.get("focus_table", type=int)
    report = _ensure_quality_report(task, markdown)
    try:
        page_content = _page_content_payload(task, page_number, report=report, focus_table=focus_table)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(page_content)


@app.route("/api/pdf_page/<task_id>/<int:page_number>", methods=["GET"])
def pdf_page_image(task_id, page_number):
    task = _get_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    try:
        image_path = _ensure_pdf_page_image(task, page_number)
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except (ValueError, subprocess.SubprocessError, subprocess.TimeoutExpired) as exc:
        return jsonify({"error": f"PDF page render failed: {exc}"}), 500
    return send_file(image_path, mimetype="image/png")


@app.route("/api/source/<task_id>/table/<int:table_index>/correction", methods=["POST"])
def save_table_correction(task_id, table_index):
    task = _get_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    markdown = _read_markdown(task)
    if markdown is None:
        markdown = _fetch_and_cache_result(task)
        if isinstance(markdown, dict) and markdown.get("_error"):
            return jsonify({"error": markdown["detail"]}), 502
    if markdown is None:
        return jsonify({"error": "No markdown available yet"}), 400

    report = _ensure_quality_report(task, markdown)
    table_item = None
    for item in report.get("table_index", []):
        if int(item.get("table_index") or 0) == table_index:
            table_item = item
            break
    if table_item is None:
        return jsonify({"error": "Table source not found"}), 404

    payload = request.get_json(silent=True) or {}
    correction = _save_table_correction(task, table_item, payload)
    enhanced = _load_json_artifact(task, "content_list_enhanced.json")
    if isinstance(enhanced, dict):
        _write_complete_markdown_artifact(task, markdown, enhanced, corrections=_load_corrections(task))
    return jsonify(
        {
            "success": True,
            "correction": correction,
            "path": _corrections_path(task),
        }
    )


@app.route("/api/download/<task_id>", methods=["GET"])
def download(task_id):
    task = _get_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    markdown = _read_markdown(task)
    if markdown is None:
        markdown = _fetch_and_cache_result(task)
        if isinstance(markdown, dict) and markdown.get("_error"):
            return jsonify({"error": markdown["detail"]}), 502

    markdown_path = task.get("markdown_path")
    if not markdown_path or not os.path.exists(markdown_path):
        return jsonify({"error": "No markdown available yet"}), 400

    filename = _safe_download_name(os.path.splitext(task["filename"])[0] + ".md")
    return send_file(
        markdown_path,
        mimetype="text/markdown",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/api/download_complete/<task_id>", methods=["GET"])
def download_complete(task_id):
    task = _get_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    markdown = _read_markdown(task)
    if markdown is None:
        markdown = _fetch_and_cache_result(task)
        if isinstance(markdown, dict) and markdown.get("_error"):
            return jsonify({"error": markdown["detail"]}), 502
    if markdown is None:
        return jsonify({"error": "No markdown available yet"}), 400

    result_dir = _result_dir(task)
    complete_path = os.path.join(result_dir, "result_complete.md")
    enhanced = _load_json_artifact(task, "content_list_enhanced.json")
    if not isinstance(enhanced, dict) or int(enhanced.get("schema_version") or 0) < CONTENT_LIST_ENHANCED_SCHEMA_VERSION:
        enhanced = _ensure_content_list_enhanced_artifact(task, markdown)
    corrections = _load_corrections(task)
    complete_needs_refresh = True
    if os.path.exists(complete_path):
        try:
            complete_needs_refresh = os.path.getmtime(complete_path) < os.path.getmtime(os.path.join(result_dir, "content_list_enhanced.json"))
        except OSError:
            complete_needs_refresh = True
    if corrections.get("tables"):
        _write_complete_markdown_artifact(task, markdown, enhanced, corrections=corrections)
    elif complete_needs_refresh:
        _write_complete_markdown_artifact(task, markdown, enhanced)
    if not os.path.exists(complete_path):
        return jsonify({"error": "No complete markdown available yet"}), 400

    filename = _safe_download_name(os.path.splitext(task["filename"])[0] + ".complete.md")
    return send_file(
        complete_path,
        mimetype="text/markdown",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/api/download_corrected/<task_id>", methods=["GET"])
def download_corrected(task_id):
    task = _get_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    markdown = _read_markdown(task)
    if markdown is None:
        markdown = _fetch_and_cache_result(task)
        if isinstance(markdown, dict) and markdown.get("_error"):
            return jsonify({"error": markdown["detail"]}), 502
    if markdown is None:
        return jsonify({"error": "No markdown available yet"}), 400

    corrected_markdown, replaced_count = _apply_table_corrections(markdown, _load_corrections(task))
    result_dir = _result_dir(task)
    os.makedirs(result_dir, exist_ok=True)
    corrected_path = os.path.join(result_dir, "corrected_result.md")
    with open(corrected_path, "w", encoding="utf-8") as outfile:
        outfile.write(corrected_markdown)

    filename = _safe_download_name(os.path.splitext(task["filename"])[0] + ".corrected.md")
    download = io.BytesIO(corrected_markdown.encode("utf-8"))
    download.seek(0)
    response = send_file(
        download,
        mimetype="text/markdown",
        as_attachment=True,
        download_name=filename,
    )
    response.headers["X-Corrections-Applied"] = str(replaced_count)
    response.headers["X-Corrected-Markdown-Path"] = corrected_path
    return response


@app.route("/api/tasks", methods=["GET"])
def list_tasks():
    _cleanup_old_data()
    return jsonify(_recent_tasks_payload())


@app.route("/api/tasks/<task_id>", methods=["DELETE"])
def delete_task(task_id):
    task = _get_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    upstream_cancelled = False
    mineru_task_id = task.get("mineru_task_id")
    if not is_terminal_status(task.get("status")) and mineru_task_id:
        cancel_resp = _json_request(f"{MINERU_API_BASE}/tasks/{mineru_task_id}", method="DELETE", timeout=5)
        upstream_cancelled = not cancel_resp.get("_error")

    _safe_unlink(task.get("upload_path"))
    _safe_remove(task.get("markdown_path"))
    _safe_remove(os.path.join(RESULTS_FOLDER, task["task_id"]))
    _safe_unlink(os.path.join(RESULTS_FOLDER, f"{task['task_id']}.md"))
    _delete_task_record(task_id)
    _wake_queue_worker()
    return jsonify({"success": True, "upstream_cancelled": upstream_cancelled})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 15000))
    host = os.environ.get("HOST", "127.0.0.1")
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    if os.environ.get("FLASK_ENV", "").lower() == "production":
        raise RuntimeError("Do not run PDF parser with Flask app.run in production. Use a WSGI server such as gunicorn.")
    initialize_app(start_worker=True)
    app.run(host=host, port=port, debug=debug)
