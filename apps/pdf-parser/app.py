#!/usr/bin/env python3
"""
PDF to Markdown Web Application.
Flask backend for converting PDFs using the local MinerU API.
"""

import base64
from collections import Counter
from collections import OrderedDict
import hmac
import io
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import threading
import time
import uuid
import zipfile
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
from quality_engine import (
    candidate_confidence as quality_candidate_confidence,
    candidate_group as quality_candidate_group,
    detect_report_year as quality_detect_report_year,
)
from quality_report import (
    CORE_FINANCIAL_TABLE_NAMES,
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
ALLOWED_BACKENDS = {"hybrid-http-client", "pipeline", "vlm-http-client"}
ALLOWED_PARSE_METHODS = {"auto", "txt", "ocr"}
SUPPORTED_MARKETS = {"CN", "HK", "US", "JP", "KR", "EU", "DOC"}
MARKET_TOKEN_RE = re.compile(r"(?:^|[_\W])(CN|HK|US|JP|KR|EU|DOC)(?:[_\W]|$)", re.IGNORECASE)
APP_ACCESS_TOKEN = os.environ.get("PDF2MD_ACCESS_TOKEN", "").strip()
APP_JS_VERSION = str(int(os.path.getmtime(os.path.join(BASE_DIR, "static", "app.js"))))
PDF_PAGE_MARKER_RE = re.compile(
    r"(?m)^[ \t]*(?:<!--\s*PDF_PAGE:\s*(\d+)\s*-->|\[PDF_PAGE:\s*(\d+)\])\s*\n?"
)
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
_file_cache_lock = threading.Lock()
_file_cache = OrderedDict()


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_task_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.replace(tzinfo=None)


def _task_elapsed_seconds(task):
    start = _parse_task_datetime(task.get("started_at"))
    if start is None:
        return None
    end = None
    if is_terminal_status(task.get("status")):
        end = _parse_task_datetime(task.get("completed_at"))
    if end is None:
        end = _utc_now()
    return max(0, int((end - start).total_seconds()))


def _safe_unlink(path):
    artifact_safe_unlink(path)


def _safe_remove(path):
    artifact_safe_remove(path)


def _wake_queue_worker():
    _queue_wakeup.set()


def _safe_client_filename(filename):
    name = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = re.sub(r"[\r\n\x00]", "_", name)
    return name or "upload.pdf"


def _safe_header_value(value):
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\r", "_").replace("\n", "_")


def _safe_download_name(filename):
    name = _safe_client_filename(filename)
    return re.sub(r"[/\\]+", "_", name) or "download.md"


def _looks_like_pdf(path):
    try:
        with open(path, "rb") as infile:
            return infile.read(5) == b"%PDF-"
    except OSError:
        return False


def _file_cache_get(path, loader):
    if not path or not os.path.exists(path):
        return None
    stat = os.stat(path)
    cache_key = (os.path.abspath(path), stat.st_mtime_ns, stat.st_size)
    with _file_cache_lock:
        cached = _file_cache.get(cache_key)
        if cached is not None:
            _file_cache.move_to_end(cache_key)
            return cached
    value = loader(path)
    with _file_cache_lock:
        stale_keys = [key for key in _file_cache if key[0] == cache_key[0] and key != cache_key]
        for key in stale_keys:
            _file_cache.pop(key, None)
        _file_cache[cache_key] = value
        _file_cache.move_to_end(cache_key)
        while len(_file_cache) > FILE_CACHE_MAX_ITEMS:
            _file_cache.popitem(last=False)
    return value


def _read_text_cached(path):
    def loader(filename):
        with open(filename, "r", encoding="utf-8") as infile:
            return infile.read()

    return _file_cache_get(path, loader)


def _read_json_cached(path):
    def loader(filename):
        with open(filename, "r", encoding="utf-8") as infile:
            return json.load(infile)

    return _file_cache_get(path, loader)


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


def _normalize_market(value):
    market = str(value or "").strip().upper()
    return market if market in SUPPORTED_MARKETS else None


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


def _request_has_valid_token():
    if not APP_ACCESS_TOKEN:
        return True
    token = (
        request.headers.get("X-PDF2MD-Token")
        or request.args.get("token")
        or request.cookies.get("pdf2md_token")
    )
    return hmac.compare_digest(str(token or ""), APP_ACCESS_TOKEN)


def _format_duration(seconds):
    if seconds is None or seconds < 0:
        return "--"
    minutes = int(seconds) // 60
    remainder = int(seconds) % 60
    if minutes > 0:
        return f"{minutes}分{remainder}秒"
    return f"{remainder}秒"


def _page_marker_line(page_number):
    return f"[PDF_PAGE: {int(page_number)}]"


def _strip_page_markers(markdown):
    return PDF_PAGE_MARKER_RE.sub("", str(markdown or ""))


def _page_body_is_sparse(markdown):
    body = str(markdown or "").strip()
    if not body:
        return True
    if "<table" in body.lower() or "![](" in body or "<details>" in body.lower():
        return False
    normalized = _normalized_anchor_text(body)
    nonempty_lines = [line for line in body.splitlines() if line.strip()]
    return len(normalized) < 40 and len(nonempty_lines) <= 2


def _markdown_from_page_payload(page_payload):
    if not isinstance(page_payload, dict):
        return ""
    blocks = page_payload.get("blocks") or []
    lines = []

    def append_line(text):
        text = str(text or "").strip()
        if not text:
            return
        if lines and lines[-1] == text:
            return
        lines.append(text)

    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = str(block.get("text") or "").strip()
            if not text:
                continue
            level = block.get("text_level")
            if isinstance(level, int) and level > 0 and len(text) <= 80:
                append_line(f"{'#' * min(level, 6)} {text}")
            else:
                append_line(text)
        elif block_type == "list":
            for item in block.get("list_items") or []:
                append_line(str(item or "").strip())
        elif block_type == "table":
            for item in block.get("caption") or []:
                append_line(item)
            table_html = str(block.get("table_html") or "").strip()
            if table_html:
                append_line(table_html)
            for item in block.get("footnote") or []:
                append_line(item)
        elif block_type == "image":
            image_path = str(block.get("image_path") or "").strip()
            if image_path:
                append_line(f"![]({image_path})")
            for item in block.get("caption") or []:
                append_line(item)
            for item in block.get("footnote") or []:
                append_line(item)

    return "\n\n".join(lines).strip()


def _backfill_sparse_markdown_pages(markdown, content_list):
    content_list = _coerce_json_artifact(content_list)
    text = str(markdown or "")
    if not text or not isinstance(content_list, list):
        return text, []

    matches = list(re.finditer(r"(?m)^\[PDF_PAGE:\s*(\d+)\]\s*$", text))
    if not matches:
        return text, []

    rebuilt = []
    restored_pages = []
    for index, match in enumerate(matches):
        page_number = int(match.group(1))
        marker_line = match.group(0)
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[body_start:body_end]
        if _page_body_is_sparse(body):
            payload = page_content_payload_from_content_list(content_list, page_number)
            rebuilt_body = _markdown_from_page_payload(payload)
            if rebuilt_body:
                rebuilt.append(marker_line + "\n" + rebuilt_body.strip() + "\n")
                restored_pages.append(page_number)
                continue
        rebuilt.append(marker_line + body)
    return "".join(rebuilt), restored_pages


def _normalized_anchor_text(text):
    normalized = []
    for ch in str(text or ""):
        if ch.isalnum():
            normalized.append(ch.lower())
    return "".join(normalized)


def _normalized_text_with_map(text):
    normalized = []
    raw_index_map = []
    for idx, ch in enumerate(str(text or "")):
        if ch.isalnum():
            lowered = ch.lower()
            normalized.append(lowered)
            raw_index_map.extend([idx] * len(lowered))
    return "".join(normalized), raw_index_map


def _compact_text_fragment(text, max_length=80):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= max_length:
        return text
    return text[:max_length].rstrip()


def _collect_text_fragments(payload):
    fragments = []
    if isinstance(payload, str):
        fragments.append(payload)
    elif isinstance(payload, list):
        for item in payload[:8]:
            fragments.extend(_collect_text_fragments(item))
    elif isinstance(payload, dict):
        for key in ("text", "content", "title", "caption"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                fragments.append(value)
                break
    return fragments


def _append_unique_fragment(bucket, seen, text):
    fragment = _compact_text_fragment(text)
    normalized = _normalized_anchor_text(fragment)
    if len(normalized) < 2 or normalized.isdigit() or normalized in seen:
        return
    seen.add(normalized)
    bucket.append(fragment)


def _page_anchor_candidates(content_list):
    content_list = _coerce_json_artifact(content_list)
    if not isinstance(content_list, list):
        return []

    pages = {}
    for item in content_list:
        if not isinstance(item, dict):
            continue
        page_idx = item.get("page_idx")
        if not isinstance(page_idx, int):
            continue
        page = pages.setdefault(
            page_idx,
            {
                "primary": [],
                "secondary": [],
                "primary_seen": set(),
                "secondary_seen": set(),
            },
        )
        item_type = item.get("type")
        if item_type == "page_number":
            continue

        primary_fragments = []
        secondary_fragments = []
        if item_type == "text":
            primary_fragments.extend(_collect_text_fragments(item.get("text")))
        elif item_type == "list":
            primary_fragments.extend(_collect_text_fragments(item.get("list_items")))
        elif item_type == "table":
            primary_fragments.extend(_collect_text_fragments(item.get("table_caption")))
            primary_fragments.extend(_collect_text_fragments(item.get("table_footnote")))
        elif item_type == "image":
            if item.get("img_path"):
                primary_fragments.append(item.get("img_path"))
            primary_fragments.extend(_collect_text_fragments(item.get("image_caption")))
            primary_fragments.extend(_collect_text_fragments(item.get("image_footnote")))
        elif item_type == "header":
            secondary_fragments.extend(_collect_text_fragments(item.get("text")))
        else:
            primary_fragments.extend(_collect_text_fragments(item.get("text")))

        for fragment in primary_fragments:
            _append_unique_fragment(page["primary"], page["primary_seen"], fragment)
        for fragment in secondary_fragments:
            _append_unique_fragment(page["secondary"], page["secondary_seen"], fragment)

    ordered_pages = []
    for page_idx in sorted(pages):
        fragments = (pages[page_idx]["primary"] + pages[page_idx]["secondary"])[:8]
        if not fragments:
            continue
        candidates = []
        seen = set()
        max_join = min(4, len(fragments))
        for size in range(max_join, 0, -1):
            combined = "".join(fragments[:size])
            normalized = _normalized_anchor_text(combined)
            if len(normalized) < 2 or normalized in seen:
                continue
            seen.add(normalized)
            candidates.append({"text": combined, "normalized": normalized})
        for fragment in fragments[:6]:
            normalized = _normalized_anchor_text(fragment)
            if len(normalized) < 2 or normalized in seen:
                continue
            seen.add(normalized)
            candidates.append({"text": fragment, "normalized": normalized})
        if candidates:
            ordered_pages.append({"page_number": page_idx + 1, "candidates": candidates})
    return ordered_pages


def _candidate_is_unique(markdown_normalized, candidate_normalized, uniqueness_cache):
    cached = uniqueness_cache.get(candidate_normalized)
    if cached is not None:
        return cached
    first = markdown_normalized.find(candidate_normalized)
    unique = first != -1 and markdown_normalized.find(candidate_normalized, first + 1) == -1
    uniqueness_cache[candidate_normalized] = unique
    return unique


def _select_page_anchor_match(markdown_normalized, page_candidates, start_pos, uniqueness_cache):
    best_match = None
    for order, candidate in enumerate(page_candidates):
        normalized = candidate.get("normalized") or ""
        if len(normalized) < 2:
            continue
        pos = markdown_normalized.find(normalized, start_pos)
        if pos == -1:
            continue
        unique = _candidate_is_unique(markdown_normalized, normalized, uniqueness_cache)
        score = (0 if unique else 1, order, pos, -len(normalized))
        if best_match is None or score < best_match["score"]:
            best_match = {
                "pos": pos,
                "length": len(normalized),
                "score": score,
            }
            if unique and order == 0:
                break
    return best_match


def _apply_page_marker_insertions(markdown, insertions):
    if not insertions:
        return markdown
    output = []
    last_index = 0
    for insert_at, marker, _page_number in sorted(insertions, key=lambda item: (item[0], item[2])):
        insert_at = max(last_index, insert_at)
        output.append(markdown[last_index:insert_at])
        output.append(marker)
        last_index = insert_at
    output.append(markdown[last_index:])
    return "".join(output)


def _page_marker_line_start(markdown, position):
    position = max(0, min(int(position), len(markdown)))
    if position >= len(markdown):
        return len(markdown)
    return markdown.rfind("\n", 0, position) + 1


def _fill_missing_page_marker_insertions(insertions, total_pages, markdown):
    if not insertions:
        return insertions

    page_to_pos = {int(page_number): int(insert_at) for insert_at, _marker, page_number in insertions}
    if not page_to_pos:
        return insertions

    known_pages = sorted(page_to_pos)
    max_known_page = known_pages[-1]
    total_pages = max(int(total_pages or 0), max_known_page)
    if total_pages <= 0:
        return insertions

    markdown_length = len(markdown)
    for page_number in range(1, total_pages + 1):
        if page_number in page_to_pos:
            continue
        prev_known_page = next((page for page in reversed(known_pages) if page < page_number), None)
        next_known_page = next((page for page in known_pages if page > page_number), None)
        if prev_known_page is not None and next_known_page is not None:
            prev_pos = page_to_pos[prev_known_page]
            next_pos = page_to_pos[next_known_page]
            page_span = next_known_page - prev_known_page
            if page_span > 0 and next_pos > prev_pos:
                ratio = (page_number - prev_known_page) / page_span
                insert_at = int(prev_pos + (next_pos - prev_pos) * ratio)
            else:
                insert_at = next_pos
        elif prev_known_page is not None:
            insert_at = markdown_length
        else:
            insert_at = page_to_pos[next_known_page] if next_known_page is not None else markdown_length
        insert_at = _page_marker_line_start(markdown, insert_at)
        insertions.append((insert_at, _page_marker_line(page_number) + "\n", page_number))
        page_to_pos[page_number] = insert_at
    return insertions


def _inject_pdf_page_markers(markdown, content_list, total_pages=None):
    original_markdown = str(markdown or "")
    if not original_markdown:
        return original_markdown

    page_candidates = _page_anchor_candidates(content_list)
    if not page_candidates and not total_pages:
        return original_markdown

    base_markdown = _strip_page_markers(original_markdown)
    markdown_normalized, raw_index_map = _normalized_text_with_map(base_markdown)
    if not markdown_normalized or not raw_index_map:
        return original_markdown

    if not page_candidates:
        insertions = [(0, _page_marker_line(1) + "\n", 1)]
        insertions = _fill_missing_page_marker_insertions(insertions, total_pages, base_markdown)
        if len(insertions) <= 1 and PDF_PAGE_MARKER_RE.search(original_markdown):
            return original_markdown
        return _apply_page_marker_insertions(base_markdown, insertions)

    insertions = [(0, _page_marker_line(page_candidates[0]["page_number"]) + "\n", page_candidates[0]["page_number"])]
    occupied_lines = {0}
    uniqueness_cache = {}
    last_norm_pos = 0

    first_match = _select_page_anchor_match(
        markdown_normalized,
        page_candidates[0]["candidates"],
        last_norm_pos,
        uniqueness_cache,
    )
    if first_match is not None:
        last_norm_pos = first_match["pos"] + first_match["length"]

    for page in page_candidates[1:]:
        match = _select_page_anchor_match(
            markdown_normalized,
            page["candidates"],
            last_norm_pos,
            uniqueness_cache,
        )
        if match is None:
            continue
        raw_pos = raw_index_map[match["pos"]]
        line_start = base_markdown.rfind("\n", 0, raw_pos) + 1
        if line_start in occupied_lines:
            last_norm_pos = match["pos"] + match["length"]
            continue
        occupied_lines.add(line_start)
        insertions.append((line_start, _page_marker_line(page["page_number"]) + "\n", page["page_number"]))
        last_norm_pos = match["pos"] + match["length"]

    insertions = _fill_missing_page_marker_insertions(insertions, total_pages, base_markdown)
    if len(insertions) <= 1 and PDF_PAGE_MARKER_RE.search(original_markdown):
        return original_markdown
    return _apply_page_marker_insertions(base_markdown, insertions)


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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _task_exists(task_id):
    conn = _db_conn()
    try:
        row = conn.execute("SELECT 1 FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return row is not None
    finally:
        conn.close()


def _init_db():
    with _db_lock:
        conn = _db_conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    mineru_task_id TEXT,
                    filename TEXT NOT NULL,
                    file_size INTEGER,
                    pdf_page_count INTEGER,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    uploaded_at TEXT,
                    submitted_at TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    cancelled INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    markdown_path TEXT,
                    upload_path TEXT,
                    last_progress_log_time TEXT,
                    last_status_payload TEXT,
                    last_polled_at REAL,
                    consecutive_status_failures INTEGER NOT NULL DEFAULT 0,
                    submit_config_json TEXT,
                    logs_json TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at DESC)"
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
            if "consecutive_status_failures" not in columns:
                conn.execute(
                    "ALTER TABLE tasks ADD COLUMN consecutive_status_failures INTEGER NOT NULL DEFAULT 0"
                )
            if "submit_config_json" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN submit_config_json TEXT")
            conn.commit()
        finally:
            conn.close()


def _row_to_task(row):
    if row is None:
        return None
    task = dict(row)
    task["cancelled"] = bool(task.get("cancelled"))
    try:
        task["logs"] = json.loads(task.pop("logs_json") or "[]")
    except json.JSONDecodeError:
        task["logs"] = []
    try:
        task["submit_config"] = json.loads(task.pop("submit_config_json") or "{}")
    except json.JSONDecodeError:
        task["submit_config"] = {}
    try:
        task["last_status_payload"] = json.loads(task["last_status_payload"]) if task.get("last_status_payload") else None
    except json.JSONDecodeError:
        task["last_status_payload"] = None
    return _apply_task_market_fallback(task)


def _save_task(task, allow_insert=False):
    payload = dict(task)
    logs = payload.pop("logs", [])
    submit_config = payload.pop("submit_config", {})
    last_status_payload = payload.get("last_status_payload")
    payload["logs_json"] = json.dumps(logs, ensure_ascii=False)
    payload["submit_config_json"] = json.dumps(submit_config or {}, ensure_ascii=False)
    payload["last_status_payload"] = (
        json.dumps(last_status_payload, ensure_ascii=False) if last_status_payload is not None else None
    )
    if not allow_insert and not _task_exists(payload["task_id"]):
        return
    with _db_lock:
        conn = _db_conn()
        try:
            conn.execute(
                """
                INSERT INTO tasks (
                    task_id, mineru_task_id, filename, file_size, pdf_page_count,
                    status, stage, created_at, uploaded_at, submitted_at, started_at,
                    completed_at, cancelled, error, markdown_path, upload_path,
                    last_progress_log_time, last_status_payload, last_polled_at,
                    consecutive_status_failures, submit_config_json, logs_json
                ) VALUES (
                    :task_id, :mineru_task_id, :filename, :file_size, :pdf_page_count,
                    :status, :stage, :created_at, :uploaded_at, :submitted_at, :started_at,
                    :completed_at, :cancelled, :error, :markdown_path, :upload_path,
                    :last_progress_log_time, :last_status_payload, :last_polled_at,
                    :consecutive_status_failures, :submit_config_json, :logs_json
                )
                ON CONFLICT(task_id) DO UPDATE SET
                    mineru_task_id=excluded.mineru_task_id,
                    filename=excluded.filename,
                    file_size=excluded.file_size,
                    pdf_page_count=excluded.pdf_page_count,
                    status=excluded.status,
                    stage=excluded.stage,
                    created_at=excluded.created_at,
                    uploaded_at=excluded.uploaded_at,
                    submitted_at=excluded.submitted_at,
                    started_at=excluded.started_at,
                    completed_at=excluded.completed_at,
                    cancelled=excluded.cancelled,
                    error=excluded.error,
                    markdown_path=excluded.markdown_path,
                    upload_path=excluded.upload_path,
                    last_progress_log_time=excluded.last_progress_log_time,
                    last_status_payload=excluded.last_status_payload,
                    last_polled_at=excluded.last_polled_at,
                    consecutive_status_failures=excluded.consecutive_status_failures,
                    submit_config_json=excluded.submit_config_json,
                    logs_json=excluded.logs_json
                """,
                payload,
            )
            conn.commit()
        finally:
            conn.close()


def _get_task(task_id):
    conn = _db_conn()
    try:
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row:
            return _row_to_task(row)
        return None
    finally:
        conn.close()


def _task_blocks_duplicate_upload(task):
    if not task:
        return False
    status = str(task.get("status") or "").lower()
    if task.get("cancelled") or status == CANCELLED:
        return False
    if is_failed_status(status):
        return False
    return True


def _find_duplicate_filename_task(filename):
    display_filename = _safe_client_filename(filename)
    conn = _db_conn()
    try:
        rows = conn.execute(
            """
            SELECT * FROM tasks
            WHERE filename = ?
            ORDER BY created_at DESC
            """,
            (display_filename,),
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        task = _row_to_task(row)
        if _task_blocks_duplicate_upload(task):
            return task
    return None


def _task_duplicate_payload(task):
    if not task:
        return None
    return {
        "task_id": task.get("task_id"),
        "filename": task.get("filename"),
        "market": task.get("market"),
        "status": task.get("status"),
        "stage": task.get("stage"),
        "created_at": task.get("created_at"),
        "uploaded_at": task.get("uploaded_at"),
        "completed_at": task.get("completed_at"),
        "pdf_page_count": task.get("pdf_page_count"),
        "markdown_ready": _has_markdown_artifact(task),
    }


def _duplicate_filename_response(filename, existing_task=None, message=None):
    payload = {
        "error": "duplicate_filename",
        "message": message or "该文件已存在解析任务，请勿重复解析",
        "filename": filename,
        "existingTask": _task_duplicate_payload(existing_task),
    }
    return jsonify(payload), 409


def _list_recent_tasks(limit=100):
    conn = _db_conn()
    try:
        rows = conn.execute(
            "SELECT task_id, filename, status, stage, created_at, markdown_path, submit_config_json FROM tasks ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        tasks = [dict(row) for row in rows]
        queued_rows = conn.execute(
            """
            SELECT task_id FROM tasks
            WHERE cancelled = 0 AND mineru_task_id IS NULL AND status = 'queued'
            ORDER BY created_at ASC
            """
        ).fetchall()
        queued_order = {row["task_id"]: idx + 1 for idx, row in enumerate(queued_rows)}
    finally:
        conn.close()

    for task in tasks:
        try:
            task["submit_config"] = json.loads(task.pop("submit_config_json") or "{}")
        except json.JSONDecodeError:
            task["submit_config"] = {}
        task = _apply_task_market_fallback(task)
        task["local_queue_position"] = queued_order.get(task["task_id"])
        if task.get("status") == COMPLETED and not _has_markdown_artifact(task):
            full_task = _get_task(task["task_id"])
            if full_task and not _has_markdown_artifact(full_task):
                _mark_completed_missing_artifact(full_task)
                task["status"] = COMPLETED_MISSING_ARTIFACT
                task["stage"] = COMPLETED_MISSING_ARTIFACT
        task["markdown_ready"] = _has_markdown_artifact(task)
        task.pop("markdown_path", None)
    return tasks


def _recent_task_list_limit():
    raw = os.environ.get("PDF_RECENT_TASK_LIMIT", "300")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 300
    return max(100, min(value, 1000))


def _refresh_recent_tasks(limit=50):
    conn = _db_conn()
    try:
        upstream_rows = conn.execute(
            """
            SELECT task_id FROM tasks
            WHERE cancelled = 0
              AND mineru_task_id IS NOT NULL
              AND status IN ('submitted', 'pending', 'processing')
            ORDER BY COALESCE(submitted_at, created_at) ASC
            """
        ).fetchall()
        recent_rows = conn.execute(
            """
            SELECT task_id FROM tasks
            WHERE status NOT IN ('completed', 'completed_missing_artifact', 'failed', 'cancelled')
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    task_ids = []
    seen = set()
    for row in list(upstream_rows) + list(recent_rows):
        task_id = row["task_id"]
        if task_id in seen:
            continue
        seen.add(task_id)
        task_ids.append(task_id)

    for task_id in task_ids:
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
    conn = _db_conn()
    try:
        row = conn.execute(
            """
            SELECT 1 FROM tasks
            WHERE cancelled = 0
              AND (
                status = 'submitting'
                OR (
                  mineru_task_id IS NOT NULL
                  AND status IN ('submitted', 'pending', 'processing')
                )
              )
            LIMIT 1
            """
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _next_queued_task():
    conn = _db_conn()
    try:
        row = conn.execute(
            """
            SELECT * FROM tasks
            WHERE cancelled = 0
              AND mineru_task_id IS NULL
              AND status = 'queued'
            ORDER BY created_at ASC
            LIMIT 1
            """
        ).fetchone()
        return _row_to_task(row)
    finally:
        conn.close()


def _claim_next_queued_task():
    with _db_lock:
        conn = _db_conn()
        try:
            row = conn.execute(
                """
                SELECT * FROM tasks
                WHERE cancelled = 0
                  AND mineru_task_id IS NULL
                  AND status = 'queued'
                ORDER BY created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            task_id = row["task_id"]
            conn.execute(
                """
                UPDATE tasks
                SET status = 'submitting', stage = 'submitting'
                WHERE task_id = ? AND status = 'queued' AND mineru_task_id IS NULL
                """,
                (task_id,),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            return _row_to_task(row)
        finally:
            conn.close()


def _recover_stale_submitting_tasks():
    cutoff = (_utc_now() - timedelta(seconds=STALE_SUBMITTING_SECONDS)).replace(microsecond=0).isoformat() + "Z"
    with _db_lock:
        conn = _db_conn()
        try:
            conn.execute(
                """
                UPDATE tasks
                SET status = 'queued', stage = 'queued'
                WHERE cancelled = 0
                  AND mineru_task_id IS NULL
                  AND status = 'submitting'
                  AND COALESCE(uploaded_at, created_at) < ?
                """,
                (cutoff,),
            )
            conn.commit()
        finally:
            conn.close()


def _local_queue_position(task_id):
    conn = _db_conn()
    try:
        rows = conn.execute(
            """
            SELECT task_id FROM tasks
            WHERE cancelled = 0
              AND mineru_task_id IS NULL
              AND status = 'queued'
            ORDER BY created_at ASC
            """
        ).fetchall()
        for idx, row in enumerate(rows, start=1):
            if row["task_id"] == task_id:
                return idx
        return None
    finally:
        conn.close()


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
    with _db_lock:
        conn = _db_conn()
        try:
            conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
            conn.commit()
        finally:
            conn.close()


def _referenced_task_paths():
    conn = _db_conn()
    try:
        rows = conn.execute("SELECT task_id, upload_path, markdown_path FROM tasks").fetchall()
        paths = set()
        task_ids = set()
        for row in rows:
            task_ids.add(row["task_id"])
            for key in ("upload_path", "markdown_path"):
                if row[key]:
                    paths.add(os.path.abspath(row[key]))
            paths.add(os.path.abspath(os.path.join(RESULTS_FOLDER, row["task_id"])))
            paths.add(os.path.abspath(os.path.join(RESULTS_FOLDER, f"{row['task_id']}.md")))
        return task_ids, paths
    finally:
        conn.close()


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
    if elapsed is None or elapsed <= 0:
        return None
    total = task.get("pdf_page_count")
    if not total or total <= 0:
        return None
    processed = min(total, max(0, int(elapsed / PAGE_ESTIMATE_SECONDS)))
    remaining = max(0, total - processed)
    return {"total": total, "processed": processed, "remaining": remaining}


def _calc_progress_percent(task, elapsed):
    total = task.get("pdf_page_count")
    if not total or total <= 0 or elapsed is None or elapsed <= 0:
        return None
    estimated_pages = min(float(total), max(0.0, elapsed / PAGE_ESTIMATE_SECONDS))
    if total <= 0:
        return None
    return round((estimated_pages / float(total)) * 100, 1)


def _legacy_markdown_path(task):
    return os.path.join(RESULTS_FOLDER, f"{task['task_id']}.md")


def _canonical_markdown_path(task):
    return os.path.join(RESULTS_FOLDER, task["task_id"], "result.md")


def _markdown_artifact_path(task):
    candidates = []
    if task.get("markdown_path"):
        candidates.append(task["markdown_path"])
    candidates.append(_canonical_markdown_path(task))
    candidates.append(_legacy_markdown_path(task))

    seen = set()
    for path in candidates:
        if not path or path in seen:
            continue
        seen.add(path)
        if os.path.exists(path):
            return path
    return None


def _has_markdown_artifact(task):
    return _markdown_artifact_path(task) is not None


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
    return os.path.join(RESULTS_FOLDER, task["task_id"])


def _write_json(path, payload):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp_path = f"{path}.tmp.{os.getpid()}.{threading.get_ident()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as outfile:
            json.dump(payload, outfile, ensure_ascii=False, indent=2)
            outfile.write("\n")
            outfile.flush()
            os.fsync(outfile.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def _corrections_path(task):
    return os.path.join(_result_dir(task), "corrections.json")


def _load_corrections(task):
    path = _corrections_path(task)
    if not os.path.exists(path):
        return {
            "schema_version": 1,
            "task_id": task["task_id"],
            "filename": task.get("filename"),
            "tables": {},
            "updated_at": None,
        }
    with open(path, "r", encoding="utf-8") as infile:
        payload = json.load(infile)
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("schema_version", 1)
    payload.setdefault("task_id", task["task_id"])
    payload.setdefault("filename", task.get("filename"))
    payload.setdefault("tables", {})
    return payload


def _save_table_correction(task, table_item, payload):
    corrections = _load_corrections(task)
    tables = corrections.setdefault("tables", {})
    table_key = str(table_item["table_index"])
    review_status = str(payload.get("review_status") or "needs_fix")
    if review_status not in {"unreviewed", "correct", "needs_fix", "fixed", "ignored"}:
        review_status = "needs_fix"

    record = {
        "table_index": table_item["table_index"],
        "markdown_line": table_item.get("line"),
        "pdf_page_number": table_item.get("pdf_page_number"),
        "bbox": table_item.get("bbox"),
        "suspect_reasons": table_item.get("suspect_reasons", []),
        "review_status": review_status,
        "table_markdown": str(payload.get("table_markdown") or "")[:1000000],
        "note": str(payload.get("note") or "")[:20000],
        "updated_at": _now_iso(),
    }
    tables[table_key] = record
    corrections["updated_at"] = record["updated_at"]
    os.makedirs(_result_dir(task), exist_ok=True)
    _write_json(_corrections_path(task), corrections)
    return record


def _write_markdown(task, markdown):
    if markdown is None:
        return None
    result_dir = _result_dir(task)
    os.makedirs(result_dir, exist_ok=True)
    markdown_path = os.path.join(result_dir, "result.md")
    with open(markdown_path, "w", encoding="utf-8") as outfile:
        outfile.write(markdown)
    task["markdown_path"] = markdown_path
    return markdown_path


def _decode_image_payload(payload):
    if isinstance(payload, dict):
        payload = payload.get("data") or payload.get("content") or payload.get("base64")
    if not isinstance(payload, str):
        return None
    if "," in payload and payload.lower().startswith("data:"):
        payload = payload.split(",", 1)[1]
    try:
        return base64.b64decode(payload, validate=False)
    except Exception:
        return None


def _save_images(images, images_dir):
    if not isinstance(images, dict):
        return 0
    os.makedirs(images_dir, exist_ok=True)
    saved = 0
    for name, payload in images.items():
        image_bytes = _decode_image_payload(payload)
        if not image_bytes:
            continue
        safe_name = os.path.basename(str(name)) or f"image_{saved + 1}.jpg"
        if not os.path.splitext(safe_name)[1]:
            safe_name += ".jpg"
        with open(os.path.join(images_dir, safe_name), "wb") as outfile:
            outfile.write(image_bytes)
        saved += 1
    return saved


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
    return re.sub(r"\s+", "", str(text or ""))


def _unique_preserve_order(items):
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _candidate_signal_text(context, source, table_text):
    caption = " ".join(source.get("caption") or [])
    footnote = " ".join(source.get("footnote") or [])
    heading = context.get("heading") or ""
    near_text = context.get("near_text") or ""
    preview = table_text[:600]
    direct = " ".join(filter(None, [heading, caption, footnote, table_text[:180]]))
    broad = " ".join(filter(None, [direct, near_text[:220], preview]))
    return direct, broad


def _candidate_title_text(context, source):
    caption = " ".join(source.get("caption") or [])
    footnote = " ".join(source.get("footnote") or [])
    heading = context.get("heading") or ""
    return " ".join(filter(None, [heading, caption, footnote]))


def _table_item_text(table_item):
    return _compact_candidate_text(
        " ".join(
            str(part or "")
            for part in (
                table_item.get("heading"),
                table_item.get("preview"),
                table_item.get("text_preview"),
            )
        )
    )


def _nearest_table_for_statement_lines(report, lines, statement_type):
    if not lines:
        return None
    table_items = [
        item
        for item in (report.get("table_index") or [])
        if isinstance(item, dict) and item.get("table_index") and item.get("line")
    ]
    if not table_items:
        return None

    bad_balance_terms = (
        "平均余额",
        "平均收益率",
        "平均成本率",
        "利息收入/支出",
        "生息资产",
        "计息负债",
    )
    best = None
    min_line = min(
        int(line)
        for line in lines
        if isinstance(line, int) or (isinstance(line, str) and line.isdigit())
    )
    for line in lines:
        try:
            source_line = int(line)
        except (TypeError, ValueError):
            continue
        for table_item in table_items:
            try:
                table_line = int(table_item.get("line") or 0)
            except (TypeError, ValueError):
                continue
            if table_line <= 0:
                continue
            table_text = _table_item_text(table_item)
            if statement_type == "balance_sheet" and any(term in table_text for term in bad_balance_terms):
                continue
            distance = abs(source_line - table_line)
            if distance > 40:
                continue
            if statement_type == "balance_sheet":
                has_asset_heading = "资产" in table_text and not any(term in table_text for term in ("负债", "股东权益", "所有者权益"))
                starts_before_first_total = table_line <= min_line
                score = (0 if has_asset_heading else 1, 0 if starts_before_first_total else 1, abs(min_line - table_line), table_line)
            else:
                # Prefer the table that starts immediately before the verified total row.
                direction_penalty = 0 if table_line <= source_line else 1
                score = (distance, direction_penalty, table_line)
            if best is None or score < best["score"]:
                best = {
                    "score": score,
                    "table_index": table_item.get("table_index"),
                    "line": min_line if statement_type == "balance_sheet" else source_line,
                    "table_item": table_item,
                }
    return best


def _statement_display_source(statement, report, statement_type):
    indexes = statement.get("table_indexes") or []
    lines = statement.get("line_numbers") or []
    table_lookup = {
        item.get("table_index"): item
        for item in (report.get("table_index") or [])
        if isinstance(item, dict) and item.get("table_index")
    }
    bad_balance_terms = (
        "平均余额",
        "平均收益率",
        "平均成本率",
        "利息收入/支出",
        "生息资产",
        "计息负债",
    )
    fallback = {
        "table_index": indexes[0] if indexes else None,
        "line": lines[0] if lines else None,
        "table_item": table_lookup.get(indexes[0]) if indexes else None,
    }
    if not indexes and lines:
        nearby_table = _nearest_table_for_statement_lines(report, lines, statement_type)
        if nearby_table:
            fallback = nearby_table
    for pos, table_index in enumerate(indexes):
        table_item = table_lookup.get(table_index) or {}
        table_text = _table_item_text(table_item)
        display_text = _compact_candidate_text(
            " ".join(
                str(part or "")
                for part in (
                    table_text,
                    statement.get("title"),
                    statement.get("statement_name"),
                )
            )
        )
        if statement_type == "balance_sheet" and any(term in table_text for term in bad_balance_terms):
            continue
        return {
            "table_index": table_index,
            "line": lines[pos] if pos < len(lines) else None,
            "table_item": table_item,
        }
    return fallback


def _merge_quality_candidates_from_financial_data(report, financial_data):
    if not isinstance(report, dict) or not isinstance(financial_data, dict):
        return report
    statements = financial_data.get("statements") or []
    metrics = financial_data.get("key_metrics") or []
    if not statements:
        statements = []

    existing = report.get("key_table_candidates") or {}
    by_name = {}
    for statement in statements:
        statement_type = statement.get("statement_type")
        scope = statement.get("scope")
        if statement_type == "balance_sheet":
            by_name.setdefault("资产负债表", []).append(statement)
            if scope == "consolidated":
                by_name.setdefault("合并资产负债表", []).append(statement)
            elif scope == "parent_company":
                by_name.setdefault("公司资产负债表", []).append(statement)
        elif statement_type == "income_statement":
            by_name.setdefault("利润表", []).append(statement)
            if scope == "consolidated":
                by_name.setdefault("合并利润表", []).append(statement)
            elif scope == "parent_company":
                by_name.setdefault("公司利润表", []).append(statement)
        elif statement_type == "cash_flow_statement":
            by_name.setdefault("现金流量表", []).append(statement)
            if scope == "consolidated":
                by_name.setdefault("合并现金流量表", []).append(statement)
            elif scope == "parent_company":
                by_name.setdefault("公司现金流量表", []).append(statement)

    if metrics:
        def _metric_source_for(canonical_names):
            for canonical_name in canonical_names:
                for item in metrics:
                    if item.get("canonical_name") != canonical_name:
                        continue
                    sources = item.get("sources") or {}
                    if sources:
                        return item, next(iter(sources.values()))
            return None, None

        metric_sources = {
            "主要会计数据": _metric_source_for(
                (
                    "operating_revenue",
                    "operating_profit",
                    "total_profit",
                    "parent_net_profit",
                    "operating_cash_flow_net",
                    "total_assets",
                    "total_liabilities",
                    "equity_attributable_parent",
                )
            ),
            "主要财务指标": _metric_source_for(
                (
                    "weighted_avg_roe",
                    "deducted_weighted_avg_roe",
                    "parent_nav_per_share",
                    "basic_eps",
                    "diluted_eps",
                    "deducted_basic_eps",
                )
            ),
        }
        for name, (metric_item, metric_source) in metric_sources.items():
            if metric_source is not None:
                by_name.setdefault(name, []).append(
                    {
                        "name": name,
                        "status": "found",
                        "table_index": metric_source.get("table_index"),
                        "line": metric_source.get("line"),
                        "pdf_page_number": None,
                        "pdf_page_source": "",
                        "pdf_page_inference_reason": "",
                        "bbox": [],
                        "rows": None,
                        "cells": None,
                        "empty_ratio": None,
                        "numeric_ratio": None,
                        "heading": metric_item.get("name") if metric_item else name,
                        "unit": metric_item.get("unit") if metric_item else "",
                        "table_type": "fact",
                        "year_binding_required": True,
                        "report_year": financial_data.get("report_year"),
                        "candidate_group": quality_candidate_group(name),
                        "candidate_score": 99.0,
                        "confidence": "high",
                        "preview": metric_item.get("name") if metric_item else name,
                        "is_primary": True,
                        "_source": "financial_data",
                    }
                )

    for name in ("所有者权益变动表",):
        if any(statement.get("statement_type") == "equity_statement" for statement in statements):
            by_name.setdefault(name, [])

    for name, statement_rows in by_name.items():
        existing_rows = existing.get(name) or []
        if any(
            item.get("status") == "found" and item.get("table_index") and item.get("line")
            for item in existing_rows
        ):
            continue
        fallback_rows = []
        for idx, statement in enumerate(statement_rows, start=1):
            statement_type = statement.get("statement_type")
            display_source = _statement_display_source(statement, report, statement_type)
            display_table = display_source.get("table_item") or {}
            if isinstance(statement, dict) and statement.get("status") == "found" and (
                statement.get("table_index") or statement.get("line")
            ):
                fallback = dict(statement)
                fallback.setdefault("name", name)
                fallback.setdefault("candidate_group", quality_candidate_group(name))
                fallback.setdefault("candidate_score", 100.0 - idx)
                fallback.setdefault("confidence", "high")
                fallback.setdefault("is_primary", idx == 1)
                fallback_rows.append(fallback)
                continue
            fallback_rows.append(
                {
                    "name": name,
                    "status": "found",
                    "table_index": display_source.get("table_index"),
                    "line": display_source.get("line"),
                    "pdf_page_number": display_table.get("pdf_page_number"),
                    "pdf_page_source": display_table.get("pdf_page_source"),
                    "pdf_page_inference_reason": display_table.get("pdf_page_inference_reason"),
                    "bbox": display_table.get("bbox") or [],
                    "rows": display_table.get("rows"),
                    "cells": display_table.get("cells"),
                    "empty_ratio": display_table.get("empty_ratio"),
                    "numeric_ratio": display_table.get("numeric_ratio"),
                    "heading": display_table.get("heading") or statement.get("title") or statement.get("statement_name") or name,
                    "unit": statement.get("unit") or "",
                    "table_type": "fact",
                    "year_binding_required": True,
                    "report_year": financial_data.get("report_year"),
                    "candidate_group": "core",
                    "candidate_score": 100.0 - idx,
                    "confidence": "high",
                    "preview": display_table.get("preview") or statement.get("title") or statement.get("statement_name") or name,
                    "is_primary": idx == 1,
                    "_source": "financial_data",
                }
            )
        if fallback_rows:
            existing[name] = fallback_rows

    report["key_table_candidates"] = existing

    financial_names = []
    financial_rows = {}
    for name in CORE_FINANCIAL_TABLE_NAMES:
        rows = existing.get(name) or []
        found_row = next(
            (item for item in rows if item.get("table_index") and item.get("line")),
            None,
        )
        if found_row:
            financial_names.append(name)
            financial_rows[name] = found_row
    report["found_financial_tables"] = financial_names
    core_candidates = []
    for name in CORE_FINANCIAL_TABLE_NAMES:
        row = financial_rows.get(name) or {}
        item = {
            "name": name,
            "status": "found" if name in financial_names else "missing",
            "candidate_group": quality_candidate_group(name),
        }
        if row:
            for key in (
                "table_index",
                "line",
                "pdf_page_number",
                "pdf_page_source",
                "pdf_page_inference_reason",
                "bbox",
                "rows",
                "cells",
                "empty_ratio",
                "numeric_ratio",
                "heading",
                "unit",
                "table_type",
                "year_binding_required",
                "report_year",
                "candidate_score",
                "confidence",
                "preview",
                "_source",
            ):
                if key in row:
                    item[key] = row.get(key)
        core_candidates.append(item)
    report["core_financial_table_candidates"] = core_candidates
    report["report_kind"] = financial_data.get("report_kind") or report.get("report_kind")
    return report


def _quality_report_warnings(report, financial_data=None):
    warnings = list(report.get("warnings") or [])
    if financial_data and financial_data.get("summary", {}).get("statement_count", 0) >= 3:
        warnings = [
            item
            for item in warnings
            if "财报核心表标题召回偏少" not in item and "核心表" not in item
        ]
    if report.get("report_kind") in {"annual_report_summary", "interim_report_summary"}:
        warnings = [item for item in warnings if "三大表" not in item and "核心表" not in item]
        if financial_data and financial_data.get("key_metrics"):
            warnings.append("当前文件为报告摘要，已按摘要模式处理主要会计数据/财务指标。")
            if financial_data.get("summary", {}).get("statement_count", 0) == 0:
                warnings.append("摘要文件不提供完整三大表；如需勾稽校验，请切换到年度报告全文。")
    return warnings


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
    path = os.path.join(_result_dir(task), filename)
    if not os.path.exists(path):
        return None
    return _coerce_json_artifact(_read_json_cached(path))


def _page_bbox_extent(task, page_index):
    content_list = _load_json_artifact(task, "content_list.json")
    return page_bbox_extent_from_content_list(content_list, page_index)


def _page_content_payload(task, page_number, report=None, focus_table=None):
    page_number = int(page_number)
    if page_number <= 0:
        raise ValueError("Invalid page number")
    content_list = _load_json_artifact(task, "content_list.json")
    return page_content_payload_from_content_list(content_list, page_number, report=report, focus_table=focus_table)


def _pdf_page_image_path(task, page_number):
    page_number = int(page_number)
    page_dir = os.path.join(_result_dir(task), "pdf_pages")
    os.makedirs(page_dir, exist_ok=True)
    return os.path.join(page_dir, f"page_{page_number:04d}.png")


def _ensure_pdf_page_image(task, page_number):
    page_number = int(page_number)
    if page_number <= 0:
        raise ValueError("Invalid page number")
    image_path = _pdf_page_image_path(task, page_number)
    if os.path.exists(image_path) and os.path.getsize(image_path) > 0:
        return image_path

    upload_path = task.get("upload_path")
    if not upload_path or not os.path.exists(upload_path):
        raise FileNotFoundError("Original PDF not found")

    prefix = os.path.join(os.path.dirname(image_path), f"page_{page_number:04d}")
    subprocess.run(
        [
            "pdftoppm",
            "-f",
            str(page_number),
            "-l",
            str(page_number),
            "-png",
            "-r",
            "144",
            upload_path,
            prefix,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    generated_path = f"{prefix}-{page_number}.png"
    if not os.path.exists(generated_path):
        generated_candidates = [
            os.path.join(os.path.dirname(image_path), name)
            for name in os.listdir(os.path.dirname(image_path))
            if name.startswith(os.path.basename(prefix) + "-") and name.endswith(".png")
        ]
        if generated_candidates:
            generated_path = generated_candidates[0]
    if generated_path != image_path and os.path.exists(generated_path):
        os.replace(generated_path, image_path)
    if not os.path.exists(image_path):
        raise FileNotFoundError("Rendered page image not found")
    return image_path


def _content_table_sources(content_list):
    content_list = _coerce_json_artifact(content_list)
    if not isinstance(content_list, list):
        return []
    printed_pages = _printed_page_numbers_by_pdf_page(content_list)
    sources = []
    table_ordinal = 0
    for item in content_list:
        if not isinstance(item, dict) or item.get("type") != "table":
            continue
        table_body = item.get("table_body") or ""
        if not table_body:
            continue
        table_ordinal += 1
        sources.append(
            {
                "source_id": table_ordinal,
                "table_body": str(table_body).strip(),
                "pdf_page_index": item.get("page_idx"),
                "pdf_page_number": int(item["page_idx"]) + 1 if isinstance(item.get("page_idx"), int) else None,
                "printed_page_number": printed_pages.get(int(item["page_idx"]) + 1) if isinstance(item.get("page_idx"), int) else None,
                "bbox": item.get("bbox") or [],
                "image_path": item.get("img_path") or "",
                "caption": item.get("table_caption") or [],
                "footnote": item.get("table_footnote") or [],
            }
        )
    return sources


def _coerce_bbox(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return []
    try:
        bbox = [float(item) for item in value]
    except (TypeError, ValueError):
        return []
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return []
    return bbox


def _table_relation_column_count(table):
    structure = table.get("structure") if isinstance(table.get("structure"), dict) else {}
    for value in (
        structure.get("expanded_columns"),
        structure.get("column_count"),
        table.get("column_count"),
    ):
        try:
            parsed = int(value or 0)
        except (TypeError, ValueError):
            parsed = 0
        if parsed > 0:
            return parsed
    return 0


def _table_relation_row_count(table):
    structure = table.get("structure") if isinstance(table.get("structure"), dict) else {}
    for value in (
        table.get("rows"),
        structure.get("expanded_rows"),
        structure.get("row_count"),
    ):
        try:
            parsed = int(value or 0)
        except (TypeError, ValueError):
            parsed = 0
        if parsed > 0:
            return parsed
    return 0


def _table_relation_title(table):
    return table.get("heading") or table.get("preview") or table.get("unit") or ""


def _table_relation_table_id(page_number, bbox, fallback):
    bbox_part = "-".join(str(int(round(float(item)))) for item in bbox)
    return f"pt-p{int(page_number or 1):04d}-{bbox_part or fallback}"


def _normalize_enhanced_table_for_relations(table, fallback_index=0):
    if not isinstance(table, dict):
        return None
    bbox = _coerce_bbox(table.get("bbox"))
    if not bbox:
        return None
    page_number = table.get("pdf_page_number") or table.get("page_number")
    try:
        page_number = int(page_number or 0)
    except (TypeError, ValueError):
        page_number = 0
    if page_number <= 0:
        return None
    table_index = table.get("table_index")
    source_id = table.get("content_table_source_id") or table.get("source_table_index")
    table_id = table.get("table_id") or f"pt-{int(table_index or fallback_index or 0):06d}"
    if not table_index:
        table_id = _table_relation_table_id(page_number, bbox, f"e{fallback_index}")
    row_count = _table_relation_row_count(table)
    column_count = _table_relation_column_count(table)
    return {
        "table_id": table_id,
        "table_index": table_index,
        "content_table_source_id": source_id,
        "page_number": page_number,
        "pdf_page_number": page_number,
        "printed_page_number": table.get("printed_page_number"),
        "bbox": bbox,
        "title": _table_relation_title(table),
        "caption": _table_relation_title(table),
        "html": table.get("table_html") or table.get("html") or "",
        "markdown": table.get("markdown") or "",
        "text": table.get("preview") or "",
        "quality": {
            "row_count": row_count,
            "column_count": column_count,
        },
        "missing_body": bool(table.get("missing_body") or not (table.get("table_html") or table.get("html") or table.get("markdown") or table.get("preview"))),
        "source": table.get("source") or "enhanced_table",
    }


def _normalize_content_table_block_for_relations(item, table_ordinal, printed_pages):
    if not isinstance(item, dict) or item.get("type") != "table":
        return None
    bbox = _coerce_bbox(item.get("bbox"))
    if not bbox:
        return None
    page_idx = item.get("page_idx")
    if not isinstance(page_idx, int):
        return None
    page_number = page_idx + 1
    table_body = str(item.get("table_body") or "").strip()
    row_count = _count_table_rows(table_body) if table_body else 0
    column_count = _table_structure_signals(table_body).get("expanded_columns") if table_body else 0
    return {
        "table_id": _table_relation_table_id(page_number, bbox, f"c{table_ordinal}"),
        "table_index": None,
        "content_table_source_id": table_ordinal if table_body else None,
        "page_number": page_number,
        "pdf_page_number": page_number,
        "printed_page_number": printed_pages.get(page_number),
        "bbox": bbox,
        "title": "",
        "caption": "",
        "html": table_body,
        "markdown": "",
        "text": _strip_html(table_body) if table_body else "",
        "quality": {
            "row_count": row_count,
            "column_count": int(column_count or 0),
        },
        "missing_body": not bool(table_body),
        "source": "content_list_table_block",
    }


def _relation_merge_key(table):
    page_number = int(table.get("page_number") or 0)
    bbox = _coerce_bbox(table.get("bbox"))
    if bbox:
        return (page_number, tuple(round(value, 2) for value in bbox))
    return (page_number, table.get("table_id") or "")


def _merge_relation_table(existing, incoming):
    merged = dict(incoming)
    merged.update(existing)
    existing_quality = existing.get("quality") if isinstance(existing.get("quality"), dict) else {}
    incoming_quality = incoming.get("quality") if isinstance(incoming.get("quality"), dict) else {}
    merged["quality"] = {
        "row_count": existing_quality.get("row_count") or incoming_quality.get("row_count") or 0,
        "column_count": existing_quality.get("column_count") or incoming_quality.get("column_count") or 0,
    }
    merged["html"] = existing.get("html") or incoming.get("html") or ""
    merged["markdown"] = existing.get("markdown") or incoming.get("markdown") or ""
    merged["text"] = existing.get("text") or incoming.get("text") or ""
    merged["title"] = existing.get("title") or incoming.get("title") or ""
    merged["caption"] = existing.get("caption") or incoming.get("caption") or ""
    merged["table_index"] = existing.get("table_index") or incoming.get("table_index")
    merged["content_table_source_id"] = existing.get("content_table_source_id") or incoming.get("content_table_source_id")
    merged["missing_body"] = bool(existing.get("missing_body") and incoming.get("missing_body"))
    return merged


def _relation_blocks_from_content_list(content_list):
    content_list = _coerce_json_artifact(content_list)
    if not isinstance(content_list, list):
        return []
    blocks = []
    for index, item in enumerate(content_list, start=1):
        if not isinstance(item, dict):
            continue
        page_idx = item.get("page_idx")
        page_number = int(page_idx) + 1 if isinstance(page_idx, int) else 1
        block_type = item.get("type") or "unknown"
        block = {
            "block_id": item.get("block_id") or f"pb-{index:06d}",
            "type": block_type,
            "page_number": page_number,
            "bbox": item.get("bbox") or [],
            "text": item.get("text") or "",
            "markdown": item.get("text") or "",
            "sub_type": item.get("sub_type") or "",
            "reading_order": index,
        }
        if block_type == "table":
            block["text"] = _strip_html(item.get("table_body") or "")
            block["markdown"] = block["text"]
        elif block_type == "list":
            block["text"] = " ".join(str(value or "") for value in item.get("list_items") or [])
            block["markdown"] = block["text"]
        blocks.append(block)
    return blocks


def _relation_tables_from_artifacts(enhanced, content_list):
    merged = {}
    for index, table in enumerate((enhanced or {}).get("tables") or [], start=1):
        normalized = _normalize_enhanced_table_for_relations(table, fallback_index=index)
        if not normalized:
            continue
        key = _relation_merge_key(normalized)
        merged[key] = _merge_relation_table(merged[key], normalized) if key in merged else normalized

    content_list = _coerce_json_artifact(content_list)
    printed_pages = _printed_page_numbers_by_pdf_page(content_list)
    table_ordinal = 0
    if isinstance(content_list, list):
        for item in content_list:
            if isinstance(item, dict) and item.get("type") == "table" and item.get("table_body"):
                table_ordinal += 1
            normalized = _normalize_content_table_block_for_relations(item, table_ordinal, printed_pages)
            if not normalized:
                continue
            key = _relation_merge_key(normalized)
            merged[key] = _merge_relation_table(merged[key], normalized) if key in merged else normalized

    return sorted(
        merged.values(),
        key=lambda item: (
            int(item.get("page_number") or 0),
            _coerce_bbox(item.get("bbox"))[1] if _coerce_bbox(item.get("bbox")) else 0,
            _coerce_bbox(item.get("bbox"))[0] if _coerce_bbox(item.get("bbox")) else 0,
        ),
    )


def _augment_table_relations(relations_payload, relation_tables):
    table_by_id = {str(table.get("table_id") or ""): table for table in relation_tables}
    relations = relations_payload.get("relations") if isinstance(relations_payload, dict) else []
    if not isinstance(relations, list):
        return relations_payload
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        from_table = table_by_id.get(str(relation.get("from_table_id") or relation.get("source_table_id") or ""))
        to_table = table_by_id.get(str(relation.get("to_table_id") or relation.get("target_table_id") or ""))
        if from_table:
            relation["from_table_index"] = from_table.get("table_index")
            relation["from_bbox"] = from_table.get("bbox") or []
            relation["from_page_number"] = from_table.get("page_number")
        if to_table:
            relation["to_table_index"] = to_table.get("table_index")
            relation["to_bbox"] = to_table.get("bbox") or []
            relation["to_page_number"] = to_table.get("page_number")
    return relations_payload


def _build_table_relations_artifact(task, markdown, enhanced=None, content_list=None):
    task_id = task.get("task_id") or ""
    if enhanced is None:
        enhanced = _load_json_artifact(task, "content_list_enhanced.json")
    if content_list is None:
        content_list = _load_json_artifact(task, "content_list.json")
    relation_tables = _relation_tables_from_artifacts(enhanced if isinstance(enhanced, dict) else {}, content_list)
    blocks = _relation_blocks_from_content_list(content_list)
    payload = build_physical_table_relations(task_id, relation_tables, blocks=blocks, markdown=markdown or "")
    payload = _augment_table_relations(payload, relation_tables)
    payload.update(
        {
            "schema_version": "document_table_relations_v1",
            "ruleset_version": payload.get("ruleset_version") or TABLE_RELATION_RULESET_VERSION,
            "task_id": task_id,
            "filename": task.get("filename"),
            "generated_at": _now_iso(),
            "physical_table_count": len(relation_tables),
        }
    )
    return payload


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
    return re.sub(r"\s+", "", str(table_html or "")).strip()


def _content_table_source_maps(table_sources):
    exact = {}
    normalized = {}
    for source in table_sources:
        table_body = str(source.get("table_body") or "").strip()
        if not table_body:
            continue
        exact.setdefault(table_body, []).append(source)
        normalized_body = _normalized_table_html_for_match(table_body)
        if normalized_body and normalized_body != table_body:
            normalized.setdefault(normalized_body, []).append(source)
    return exact, normalized


def _pop_unused_content_table_source(table_html, exact_sources, normalized_sources, used_source_ids):
    table_html = str(table_html or "").strip()
    source = _pop_unused_source_from_bucket(exact_sources.get(table_html), used_source_ids)
    if source:
        source = dict(source)
        source["source_match"] = "content_list_body_exact"
        return source

    normalized_html = _normalized_table_html_for_match(table_html)
    source = _pop_unused_source_from_bucket(normalized_sources.get(normalized_html), used_source_ids)
    if source:
        source = dict(source)
        source["source_match"] = "content_list_body_normalized"
        return source
    return {}


def _pop_unused_source_from_bucket(bucket, used_source_ids):
    if not bucket:
        return None
    while bucket:
        source = bucket.pop(0)
        source_id = source.get("source_id")
        if source_id in used_source_ids:
            continue
        used_source_ids.add(source_id)
        return source
    return None


def _pdf_page_markers_by_line(markdown):
    markers = []
    for match in PDF_PAGE_MARKER_RE.finditer(str(markdown or "")):
        page_text = match.group(1) or match.group(2)
        try:
            page_number = int(page_text)
        except (TypeError, ValueError):
            continue
        markers.append(
            {
                "line": str(markdown or "").count("\n", 0, match.start()) + 1,
                "page_number": page_number,
            }
        )
    return markers


def _inferred_pdf_page_for_line(line, markers):
    if not line or not markers:
        return None, ""
    previous_marker = None
    next_marker = None
    for marker in markers:
        if marker["line"] <= line:
            previous_marker = marker
            continue
        next_marker = marker
        break
    if previous_marker and next_marker:
        previous_distance = line - previous_marker["line"]
        next_distance = next_marker["line"] - line
        if next_marker["page_number"] >= previous_marker["page_number"] and previous_distance <= 220:
            return previous_marker["page_number"], "between_ordered_markers"
        if previous_distance <= 80:
            return previous_marker["page_number"], "near_previous_marker"
        if next_distance <= 80:
            return next_marker["page_number"], "near_next_marker"
        return None, "ambiguous_marker_distance"
    if previous_marker and line - previous_marker["line"] <= 220:
        return previous_marker["page_number"], "tail_near_previous_marker"
    return None, "no_safe_marker"


def _table_source_confidence(source_name):
    if source_name in {"content_list_body_exact", "content_list_body_normalized"}:
        return "high"
    if source_name == "markdown_marker_inferred":
        return "medium"
    return "low"


def _printed_page_numbers_by_pdf_page(content_list):
    content_list = _coerce_json_artifact(content_list)
    if not isinstance(content_list, list):
        return {}
    pages = {}
    for item in content_list:
        if not isinstance(item, dict) or item.get("type") != "page_number":
            continue
        page_idx = item.get("page_idx")
        if not isinstance(page_idx, int):
            continue
        text = str(item.get("text") or "").strip()
        if text:
            pages[page_idx + 1] = text
    return pages


SUPERSCRIPT_FOOTNOTE_REF_RE = re.compile(r"[\u00b9\u00b2\u00b3\u2070-\u2079]")
INLINE_FOOTNOTE_REF_RE = re.compile(r"(?<=[\u4e00-\u9fffA-Za-z])[1-9](?=[\u4e00-\u9fff])")
FOOTNOTE_DEF_RE = re.compile(r"^\s*(?:注|注释|说明)?\s*(?:[\u00b9\u00b2\u00b3\u2070-\u2079]|[1-9][\.、）)])\s*")
INLINE_FOOTNOTE_PREV_EXCLUDE = set("第表图附注")
INLINE_FOOTNOTE_NEXT_EXCLUDE = set("页章节条款项年月日号个亿万元股倍")
TOC_LINE_RE = re.compile(r"^(?P<title>第[一二三四五六七八九十百]+[章节篇部][^.\n]{0,80}?|[一二三四五六七八九十]+、[^.\n]{1,80}|[0-9]+(?:\.[0-9]+)*[、. ]+[^.\n]{1,80}?)[\s.·…-]*(?P<page>\d{1,4})?$")


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
    content_list = _coerce_json_artifact(content_list)
    if not isinstance(content_list, list):
        return []
    printed_pages = _printed_page_numbers_by_pdf_page(content_list)
    pages = {}
    for block in content_list:
        if not isinstance(block, dict):
            continue
        page_number = _block_page_number(block)
        if not page_number:
            continue
        payload = pages.setdefault(
            page_number,
            {
                "page_number": page_number,
                "pdf_page_number": page_number,
                "printed_page_number": printed_pages.get(page_number),
                "block_count": 0,
                "block_types": Counter(),
                "table_count": 0,
                "text_chars": 0,
                "footnote_texts": [],
            },
        )
        block_type = str(block.get("type") or "unknown")
        payload["block_count"] += 1
        payload["block_types"][block_type] += 1
        if block_type == "table":
            payload["table_count"] += 1
            for footnote in block.get("table_footnote") or []:
                if str(footnote or "").strip():
                    payload["footnote_texts"].append(str(footnote).strip())
        text = " ".join(_collect_text_fragments(block))
        payload["text_chars"] += len(text)
    return [
        {
            **{key: value for key, value in page.items() if key != "block_types"},
            "block_types": dict(page["block_types"]),
        }
        for _page_number, page in sorted(pages.items())
    ]


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
    content_list = _coerce_json_artifact(content_list)
    if not isinstance(content_list, list):
        content_list = []
    details_by_path = _markdown_image_details(markdown)
    path_offsets = Counter()
    blocks = []
    for source_id, block in enumerate(content_list, start=1):
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or block.get("category") or block.get("block_type") or "").lower()
        sub_type = str(block.get("sub_type") or block.get("subtype") or "").lower()
        image_path = (
            block.get("img_path")
            or block.get("image_path")
            or block.get("source_image_path")
            or block.get("image")
            or ""
        )
        is_semantic_image = block_type in {"image", "chart", "equation"} or bool(image_path and block_type != "table")
        if not is_semantic_image or not image_path:
            continue
        image_path = str(image_path)
        candidates = details_by_path.get(image_path) or []
        detail_index = path_offsets[image_path]
        detail = candidates[detail_index] if detail_index < len(candidates) else {}
        if candidates:
            path_offsets[image_path] += 1
        detail_type = detail.get("summary_type") or ""
        semantic_kind = _image_semantic_kind(block_type, sub_type, detail_type)
        body = detail.get("body") or ""
        content_format = ""
        if body:
            if "```mermaid" in body:
                content_format = "mermaid"
            elif re.search(r"^\s*\|.+\|\s*$", body, flags=re.MULTILINE):
                content_format = "markdown_table"
            elif "$$" in body or block_type == "equation":
                content_format = "latex_or_text"
            else:
                content_format = "plain_text"
        recognized_language = _detect_text_language(body)
        normalized_content_zh = _normalized_image_content_zh(
            body,
            semantic_kind=semantic_kind,
            content_format=content_format,
            detail_type=detail_type,
        )
        display_content = normalized_content_zh or body
        display_preview = _compact_text_fragment(_strip_html(display_content), 320)
        item = {
            "image_index": len(blocks) + 1,
            "content_source_id": source_id,
            "type": block_type or "image",
            "sub_type": sub_type,
            "semantic_kind": semantic_kind,
            "image_path": image_path,
            "pdf_page_index": block.get("page_idx"),
            "pdf_page_number": _block_page_number(block),
            "bbox": block.get("bbox") or [],
            "caption": block.get("image_caption") or block.get("caption") or [],
            "footnote": block.get("image_footnote") or block.get("footnote") or [],
            "markdown_line": detail.get("markdown_line"),
            "markdown_image_order": detail.get("markdown_image_order"),
            "detail_type": detail_type,
            "recognized_content": body,
            "recognized_language": recognized_language,
            "normalized_content_zh": normalized_content_zh,
            "display_content": display_content,
            "recognized_preview": detail.get("body_preview") or "",
            "display_preview": display_preview,
            "content_format": content_format,
            "confidence": _image_semantic_confidence(block_type, sub_type, detail),
            "source": "markdown_details_with_content_list" if detail else "content_list_image_block",
            "evidence": [
                value
                for value in (
                    "content_list_block",
                    "markdown_details" if detail else "",
                    "bbox" if block.get("bbox") else "",
                )
                if value
            ],
        }
        chart_data = _markdown_table_to_records(display_content) if content_format == "markdown_table" else None
        flowchart_graph = _mermaid_to_nodes_edges(body) if content_format == "mermaid" else None
        if chart_data:
            item["chart_data"] = chart_data
        if flowchart_graph:
            item["flowchart_graph"] = flowchart_graph
        ocr_vlm_candidate = _image_ocr_vlm_candidate(item)
        actionability = _image_actionability(
            item,
            chart_data=chart_data,
            flowchart_graph=flowchart_graph,
            ocr_vlm_candidate=ocr_vlm_candidate,
        )
        item["ocr_vlm_candidate"] = ocr_vlm_candidate
        item["actionability"] = actionability
        item["show_in_complete"] = _should_show_image_block_in_complete(item)
        blocks.append(item)
    return blocks


def _markdown_line_offsets(markdown):
    offsets = []
    pos = 0
    for line in str(markdown or "").splitlines(True):
        offsets.append(pos)
        pos += len(line)
    if not offsets:
        offsets.append(0)
    return offsets


def _line_number_for_offset(offsets, offset):
    best = 1
    for idx, start in enumerate(offsets, start=1):
        if start > offset:
            break
        best = idx
    return best


def _build_enhanced_footnotes(markdown, content_list=None):
    text = str(markdown or "")
    offsets = _markdown_line_offsets(text)
    page_markers = _pdf_page_markers_by_line(text)
    references = []
    for match in SUPERSCRIPT_FOOTNOTE_REF_RE.finditer(text):
        line = _line_number_for_offset(offsets, match.start())
        line_text = text.splitlines()[line - 1] if 0 <= line - 1 < len(text.splitlines()) else ""
        if FOOTNOTE_DEF_RE.search(line_text):
            continue
        page_number, reason = _inferred_pdf_page_for_line(line, page_markers)
        references.append(
            {
                "marker": match.group(0),
                "line": line,
                "pdf_page_number": page_number,
                "pdf_page_source": "markdown_marker_inferred" if page_number else "",
                "pdf_page_inference_reason": reason if page_number else "",
                "context": _compact_text_fragment(text[max(0, match.start() - 40) : match.end() + 60], 120),
                "source": "markdown_superscript",
            }
        )
    inline_refs = []
    for match in INLINE_FOOTNOTE_REF_RE.finditer(text):
        line = _line_number_for_offset(offsets, match.start())
        line_text = text.splitlines()[line - 1] if 0 <= line - 1 < len(text.splitlines()) else ""
        if FOOTNOTE_DEF_RE.search(line_text):
            continue
        prev_char = text[match.start() - 1] if match.start() > 0 else ""
        next_char = text[match.end()] if match.end() < len(text) else ""
        if prev_char in INLINE_FOOTNOTE_PREV_EXCLUDE or next_char in INLINE_FOOTNOTE_NEXT_EXCLUDE:
            continue
        inline_refs.append(match)
    if len(inline_refs) <= 80:
        for match in inline_refs:
            line = _line_number_for_offset(offsets, match.start())
            page_number, reason = _inferred_pdf_page_for_line(line, page_markers)
            references.append(
                {
                    "marker": match.group(0),
                    "line": line,
                    "pdf_page_number": page_number,
                    "pdf_page_source": "markdown_marker_inferred" if page_number else "",
                    "pdf_page_inference_reason": reason if page_number else "",
                    "context": _compact_text_fragment(text[max(0, match.start() - 40) : match.end() + 60], 120),
                    "source": "markdown_inline_digit",
                }
            )

    definitions = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not FOOTNOTE_DEF_RE.search(line):
            continue
        page_number, reason = _inferred_pdf_page_for_line(line_number, page_markers)
        definitions.append(
            {
                "line": line_number,
                "pdf_page_number": page_number,
                "pdf_page_source": "markdown_marker_inferred" if page_number else "",
                "pdf_page_inference_reason": reason if page_number else "",
                "text": _compact_text_fragment(line, 220),
                "source": "markdown_line",
            }
        )

    content_list = _coerce_json_artifact(content_list)
    if isinstance(content_list, list):
        for block in content_list:
            if not isinstance(block, dict):
                continue
            page_number = _block_page_number(block)
            footnotes = []
            if block.get("type") == "table":
                footnotes.extend(block.get("table_footnote") or [])
            if block.get("type") == "image":
                footnotes.extend(block.get("image_footnote") or [])
            for footnote in footnotes:
                footnote_text = str(footnote or "").strip()
                if not footnote_text:
                    continue
                definitions.append(
                    {
                        "line": None,
                        "pdf_page_number": page_number,
                        "pdf_page_source": "content_list",
                        "pdf_page_inference_reason": "",
                        "text": _compact_text_fragment(footnote_text, 220),
                        "source": "content_list_footnote",
                    }
                )

    def ref_key(item):
        return str(item.get("marker") or "")

    definition_by_marker = {}
    for definition in definitions:
        marker_match = re.search(r"[\u00b9\u00b2\u00b3\u2070-\u2079]|[1-9]", definition.get("text") or "")
        if marker_match:
            definition_by_marker.setdefault(marker_match.group(0), definition)
    bindings = []
    for ref in references:
        definition = definition_by_marker.get(ref_key(ref))
        bindings.append(
            {
                "marker": ref.get("marker"),
                "reference_line": ref.get("line"),
                "definition_line": definition.get("line") if definition else None,
                "reference_page": ref.get("pdf_page_number"),
                "definition_page": definition.get("pdf_page_number") if definition else None,
                "status": "bound" if definition else "unbound",
            }
        )
    return {
        "references": references[:500],
        "definitions": definitions[:500],
        "bindings": bindings[:500],
        "summary": {
            "reference_count": len(references),
            "definition_count": len(definitions),
            "bound_count": sum(1 for item in bindings if item.get("status") == "bound"),
            "unbound_count": sum(1 for item in bindings if item.get("status") == "unbound"),
            "inline_digit_refs_suppressed": len(inline_refs) > 80,
        },
    }


def _heading_level_from_text(text):
    title = str(text or "").strip()
    if re.match(r"^第[一二三四五六七八九十百]+[章节篇部]", title):
        return 1
    if re.match(r"^[一二三四五六七八九十]+、", title):
        return 2
    if re.match(r"^[0-9]+(?:\.[0-9]+)+", title):
        return min(6, title.count(".") + 1)
    return 3


def _build_enhanced_toc(markdown, content_list=None):
    text = str(markdown or "")
    page_markers = _pdf_page_markers_by_line(text)
    headings = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            continue
        title = _strip_html(match.group(2)).strip()
        if not title:
            continue
        page_number, reason = _inferred_pdf_page_for_line(line_number, page_markers)
        headings.append(
            {
                "title": title,
                "level": len(match.group(1)),
                "line": line_number,
                "pdf_page_number": page_number,
                "pdf_page_source": "markdown_marker_inferred" if page_number else "",
                "pdf_page_inference_reason": reason if page_number else "",
                "source": "markdown_heading",
            }
        )

    toc_candidates = []
    toc_zone_lines = set()
    lines = text.splitlines()
    for idx, line in enumerate(lines, start=1):
        cleaned = _strip_html(line).strip()
        if cleaned in {"目录", "目 录", "目次"} or cleaned.startswith("# 目录"):
            for target in range(idx, min(len(lines), idx + 180) + 1):
                toc_zone_lines.add(target)
    for line_number, line in enumerate(text.splitlines(), start=1):
        if toc_zone_lines and line_number not in toc_zone_lines:
            continue
        cleaned = _strip_html(line).strip()
        if len(cleaned) < 4 or len(cleaned) > 120:
            continue
        match = TOC_LINE_RE.match(cleaned)
        if not match:
            continue
        title = (match.group("title") or "").strip(" .·…-")
        page_text = match.group("page")
        if not title:
            continue
        page_number, reason = _inferred_pdf_page_for_line(line_number, page_markers)
        toc_candidates.append(
            {
                "title": title,
                "level": _heading_level_from_text(title),
                "line": line_number,
                "target_page_number": int(page_text) if page_text else None,
                "pdf_page_number": page_number,
                "pdf_page_source": "markdown_marker_inferred" if page_number else "",
                "pdf_page_inference_reason": reason if page_number else "",
                "source": "markdown_toc_candidate",
            }
        )

    content_headings = []
    content_list = _coerce_json_artifact(content_list)
    if isinstance(content_list, list):
        for block in content_list:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            title = str(block.get("text") or "").strip()
            level = block.get("text_level")
            if not isinstance(level, int) or level <= 0 or not title or len(title) > 120:
                continue
            content_headings.append(
                {
                    "title": title,
                    "level": min(level, 6),
                    "line": None,
                    "pdf_page_number": _block_page_number(block),
                    "pdf_page_source": "content_list",
                    "pdf_page_inference_reason": "",
                    "source": "content_list_text_level",
                }
            )

    return {
        "headings": headings[:500],
        "toc_candidates": toc_candidates[:500],
        "content_headings": content_headings[:500],
        "summary": {
            "heading_count": len(headings),
            "toc_candidate_count": len(toc_candidates),
            "content_heading_count": len(content_headings),
            "headings_with_page": sum(1 for item in headings if item.get("pdf_page_number")),
            "toc_candidates_with_target_page": sum(1 for item in toc_candidates if item.get("target_page_number")),
        },
    }


def _build_enhanced_quality_signals(tables, footnotes, toc, pages, financial_note_links=None, image_semantic_blocks=None):
    source_counts = Counter(item.get("source") or "unresolved" for item in tables)
    table_count = len(tables)
    exact = source_counts.get("content_list_body_exact", 0) + source_counts.get("content_list_body_normalized", 0)
    inferred = source_counts.get("markdown_marker_inferred", 0)
    missing_page = sum(1 for item in tables if not item.get("pdf_page_number"))
    multi_header = sum(1 for item in tables if (item.get("structure") or {}).get("multi_level_header_candidate"))
    foot_summary = footnotes.get("summary") or {}
    toc_summary = toc.get("summary") or {}
    note_link_summary = (financial_note_links or {}).get("summary") or {}
    image_blocks = image_semantic_blocks or []
    image_kind_counts = Counter(item.get("semantic_kind") or "image" for item in image_blocks)
    image_actionability_counts = Counter(item.get("actionability") or "unknown" for item in image_blocks)
    image_with_recognition = sum(1 for item in image_blocks if item.get("recognized_content"))
    image_with_display = sum(1 for item in image_blocks if item.get("display_content"))
    image_show_count = sum(1 for item in image_blocks if item.get("show_in_complete"))
    image_ocr_candidate_count = sum(1 for item in image_blocks if (item.get("ocr_vlm_candidate") or {}).get("needed"))
    return {
        "table_exact_rate": round(exact / table_count, 4) if table_count else 0,
        "table_inferred_rate": round(inferred / table_count, 4) if table_count else 0,
        "table_missing_page_count": missing_page,
        "multi_level_header_table_count": multi_header,
        "footnote_reference_count": foot_summary.get("reference_count", 0),
        "footnote_definition_count": foot_summary.get("definition_count", 0),
        "footnote_unbound_count": foot_summary.get("unbound_count", 0),
        "toc_heading_count": toc_summary.get("heading_count", 0),
        "toc_candidate_count": toc_summary.get("toc_candidate_count", 0),
        "content_heading_count": toc_summary.get("content_heading_count", 0),
        "page_count_with_content_blocks": len(pages),
        "financial_note_link_count": note_link_summary.get("linked_item_count", 0),
        "image_semantic_block_count": len(image_blocks),
        "image_semantic_kind_counts": dict(image_kind_counts),
        "image_semantic_actionability_counts": dict(image_actionability_counts),
        "image_semantic_recognized_count": image_with_recognition,
        "image_semantic_display_count": image_with_display,
        "image_semantic_show_count": image_show_count,
        "image_semantic_ocr_candidate_count": image_ocr_candidate_count,
    }


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
    raw = _strip_html(str(value or "")).strip()
    if not raw or raw in {"-", "—", "--", "－", "不适用", "无"}:
        return None
    normalized = raw.replace(",", "").replace("，", "").replace(" ", "")
    normalized = normalized.replace("人民币", "")
    negative = False
    if re.fullmatch(r"[（(].+[）)]", normalized):
        negative = True
        normalized = normalized[1:-1]
    normalized = normalized.replace("%", "")
    normalized = re.sub(r"(?:元|万元|千元|百万元|百万|亿元|万|千|亿)$", "", normalized)
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", normalized):
        return None
    number = float(normalized)
    return -abs(number) if negative else number


def _financial_unit_scale_from_text(text):
    compact = str(text or "")
    unit_matches = re.findall(r"(?:单位|金额单位)\s*[：:]?\s*(?:人民币)?\s*(亿元|百万元|万元|千元|元)", compact)
    if unit_matches:
        return _financial_unit_scale(unit_matches[-1])
    if "亿元" in compact:
        return 100000000.0
    if "百万元" in compact or "百万" in compact:
        return 1000000.0
    if "万元" in compact or "人民币万元" in compact:
        return 10000.0
    if "千元" in compact:
        return 1000.0
    return 1.0


def _financial_unit_scale(unit):
    unit = str(unit or "")
    if unit == "亿元":
        return 100000000.0
    if unit == "百万元" or unit == "百万":
        return 1000000.0
    if unit == "万元":
        return 10000.0
    if unit == "千元":
        return 1000.0
    return 1.0


def _financial_unit_scale_near(text, position):
    text = str(text or "")
    position = max(0, min(int(position or 0), len(text)))
    context = text[max(0, position - 1000) : min(len(text), position + 200)]
    unit_matches = list(
        re.finditer(r"(?:单位|金额单位)\s*[：:]?\s*(?:人民币)?\s*(亿元|百万元|万元|千元|元)", context)
    )
    if unit_matches:
        return _financial_unit_scale(unit_matches[-1].group(1))
    return 1.0


def _normalize_amount_for_compare(value, unit_scale=1.0):
    if value is None:
        return None
    try:
        return float(value) * float(unit_scale or 1.0)
    except (TypeError, ValueError):
        return None


def _amount_close(left, right):
    if left is None or right is None:
        return False, None
    diff = abs(float(left) - float(right))
    tolerance = max(1.0, abs(float(left)) * 0.0001, abs(float(right)) * 0.0001)
    return diff <= tolerance, {"difference": diff, "tolerance": tolerance}


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
    statement_items = _financial_statement_item_hits(markdown)
    note_titles = _financial_note_title_hits(markdown)
    note_tree = _financial_note_title_tree(markdown)
    note_tree_by_numeric = {}
    for note in note_tree.values():
        note_tree_by_numeric.setdefault(note.get("numeric_key"), []).append(note)
    table_by_index = {item.get("table_index"): item for item in tables}
    links = []
    for item in statement_items:
        note = None
        method = "statement_item_to_note_title_alias"
        confidence = "medium"
        evidence = []
        note_ref = item.get("note_ref")
        if note_ref:
            direct = note_tree.get(note_ref)
            numeric_matches = note_tree_by_numeric.get(_note_ref_numeric_key(note_ref)) or []
            if direct:
                note = direct
                evidence.append("note_ref_exact")
            elif len(numeric_matches) == 1:
                note = numeric_matches[0]
                evidence.append("note_ref_numeric_unique")
            elif numeric_matches:
                same_name = [candidate for candidate in numeric_matches if candidate.get("canonical_name") == item["canonical_name"]]
                if len(same_name) == 1:
                    note = same_name[0]
                    evidence.append("note_ref_numeric_and_title")
            if note:
                if note.get("canonical_name") == item["canonical_name"]:
                    method = "statement_note_ref_to_note_title"
                    evidence.append("title_match")
                    confidence = (
                        "high"
                        if "note_ref_exact" in evidence and "title_match" in evidence
                        else "medium"
                    )
                else:
                    # Financial reports may reuse local note numbers across
                    # sections. A numeric ref without title agreement is useful
                    # as a candidate, but not safe enough to link.
                    note = None
                    evidence.append("note_ref_title_mismatch")
        if note is None:
            note = note_titles.get(item["canonical_name"])
            evidence.append("title_alias_match")
        if not note:
            continue
        amount_check = _build_financial_note_amount_check(item, note, markdown)
        statement_page, statement_reason = _inferred_pdf_page_for_line(item["line"], page_markers)
        note_page, note_reason = _inferred_pdf_page_for_line(note["line"], page_markers)
        table = table_by_index.get(item.get("table_index")) or {}
        link = {
            "statement_item": item["canonical_name"],
            "statement_alias": item.get("matched_alias"),
            "statement_line": item.get("line"),
            "statement_table_index": item.get("table_index"),
            "statement_note_ref": note_ref,
            "statement_note_ref_raw": item.get("note_ref_raw"),
            "statement_page_number": table.get("pdf_page_number") or statement_page,
            "statement_page_source": table.get("source") if table.get("pdf_page_number") else ("markdown_marker_inferred" if statement_page else ""),
            "statement_page_inference_reason": table.get("pdf_page_inference_reason") or statement_reason,
            "note_title": note.get("title"),
            "note_alias": note.get("matched_alias"),
            "note_ref": note.get("note_ref"),
            "note_scope": note.get("scope"),
            "note_line": note.get("line"),
            "note_page_number": note.get("pdf_page_number") or note_page,
            "note_page_source": note.get("pdf_page_source") or ("markdown_marker_inferred" if note_page else ""),
            "note_page_inference_reason": note.get("pdf_page_inference_reason") or (note_reason if note_page else ""),
            "confidence": confidence,
            "method": method,
            "evidence": evidence,
            "amount_check": amount_check,
        }
        link["precision_level"] = _financial_note_link_precision(link)
        links.append(link)
    amount_summary = _financial_note_amount_summary(links)
    return {
        "links": links[:500],
        "note_title_tree": list(note_tree.values())[:500],
        "summary": {
            "statement_item_count": len(statement_items),
            "note_title_count": len(note_titles),
            "note_title_tree_count": len(note_tree),
            "linked_item_count": len(links),
            "high_confidence_link_count": sum(1 for item in links if item.get("confidence") == "high"),
            "audit_ready_navigation_count": sum(
                1 for item in links if item.get("precision_level") == "audit_ready_navigation"
            ),
            **amount_summary,
        },
    }



def _complete_markdown_appendix(enhanced):
    signals = enhanced.get("quality_signals") or {}
    toc = enhanced.get("toc") or {}
    footnotes = enhanced.get("footnotes") or {}
    note_links = enhanced.get("financial_note_links") or {}
    image_blocks = enhanced.get("image_semantic_blocks") or []
    tables = enhanced.get("tables") or []
    lines = [
        "",
        "",
        "---",
        "",
        "# PDF 可恢复信息附录",
        "",
        "> 本附录由解析产物自动生成，用于补足 Markdown 难以表达的 PDF 结构信息；不改写原文和财务数字。",
        "",
        "## 解析溯源摘要",
        "",
        f"- 表格总数：{enhanced.get('table_count', 0)}",
        f"- content_list 精确表格：{(enhanced.get('source_counts') or {}).get('content_list_body_exact', 0)}",
        f"- Markdown 页码推断表格：{(enhanced.get('source_counts') or {}).get('markdown_marker_inferred', 0)}",
        f"- 缺页码表格：{signals.get('table_missing_page_count', 0)}",
        f"- 多级表头候选表：{signals.get('multi_level_header_table_count', 0)}",
        f"- 脚注引用：{signals.get('footnote_reference_count', 0)}",
        f"- 脚注定义：{signals.get('footnote_definition_count', 0)}",
        f"- 目录候选：{signals.get('toc_candidate_count', 0)}",
        f"- 财报项目附注关联：{(note_links.get('summary') or {}).get('linked_item_count', 0)}",
        f"- 图片/图表/公式语义块：{signals.get('image_semantic_block_count', 0)}",
        f"- 已带识别内容的图片语义块：{signals.get('image_semantic_recognized_count', 0)}",
        f"- 可展示图片增强块：{signals.get('image_semantic_show_count', 0)}",
        f"- 按需 OCR/VLM 候选图像：{signals.get('image_semantic_ocr_candidate_count', 0)}",
        "",
    ]
    toc_candidates = toc.get("toc_candidates") or []
    if toc_candidates:
        lines.extend(["## 目录候选索引", ""])
        for item in toc_candidates[:300]:
            page = item.get("target_page_number") or item.get("pdf_page_number") or "--"
            lines.append(f"- 第 {page} 页：{item.get('title')}")
        if len(toc_candidates) > 300:
            lines.append(f"- ... 其余 {len(toc_candidates) - 300} 条见 content_list_enhanced.json")
        lines.append("")
    definitions = footnotes.get("definitions") or []
    if definitions:
        lines.extend(["## 脚注与注释", "", "### 脚注定义"])
        for item in definitions[:200]:
            page = item.get("pdf_page_number") or "--"
            line = item.get("line") or "--"
            lines.append(f"- PDF {page} 页 / MD 行 {line}：{item.get('text')}")
        if len(definitions) > 200:
            lines.append(f"- ... 其余 {len(definitions) - 200} 条见 content_list_enhanced.json")
        lines.append("")
    unbound = [item for item in (footnotes.get("bindings") or []) if item.get("status") == "unbound"]
    if unbound:
        lines.extend(["### 未绑定脚注引用"])
        for item in unbound[:100]:
            lines.append(
                f"- 标记 {item.get('marker')} / PDF {item.get('reference_page') or '--'} 页 / MD 行 {item.get('reference_line') or '--'}"
            )
        if len(unbound) > 100:
            lines.append(f"- ... 其余 {len(unbound) - 100} 条见 content_list_enhanced.json")
        lines.append("")
    links = note_links.get("links") or []
    if links:
        lines.extend(["## 财报项目附注关联", ""])
        for item in links[:200]:
            amount_check = item.get("amount_check") or {}
            amount_status = amount_check.get("status") or "未校验"
            amount_confidence = amount_check.get("confidence") or ""
            note_ref = item.get("statement_note_ref") or item.get("note_ref") or "--"
            precision = item.get("precision_level") or item.get("confidence") or "--"
            lines.append(
                f"- {item.get('statement_item')} [{precision}] "
                f"附注 {note_ref} -> {item.get('note_title')} "
                f"(附注页 {item.get('note_page_number') or '--'} / 主表 {item.get('statement_table_index') or '--'} / "
                f"金额校验 {amount_status}{('/' + amount_confidence) if amount_confidence else ''})"
            )
        if len(links) > 200:
            lines.append(f"- ... 其余 {len(links) - 200} 条见 content_list_enhanced.json")
        lines.append("")
    recognized_image_blocks = [item for item in image_blocks if item.get("show_in_complete")]
    if recognized_image_blocks:
        lines.extend(["## 图片、图表与公式增强识别", ""])
        lines.append("仅展示有数据、结构、公式或可检索文字价值的增强块；自然图片等视觉上下文保留在 `content_list_enhanced.json`。")
        lines.append("")
        for item in recognized_image_blocks[:120]:
            page = item.get("pdf_page_number") or "--"
            line = item.get("markdown_line") or "--"
            kind = item.get("semantic_kind") or item.get("type") or "image"
            detail_type = item.get("detail_type") or item.get("sub_type") or "--"
            confidence = item.get("confidence") or "--"
            actionability = item.get("actionability") or "--"
            lines.append(
                f"- 图像 {item.get('image_index')} / {kind} / {detail_type} / "
                f"PDF {page} 页 / MD 行 {line} / 置信度 {confidence} / 可用性 {actionability} / {item.get('image_path')}"
            )
            preview = item.get("display_preview") or item.get("recognized_preview") or ""
            if preview:
                lines.append(f"  - 识别预览：{preview}")
            chart_data = item.get("chart_data") or {}
            if chart_data.get("rows"):
                lines.append(
                    f"  - 图表数据：{chart_data.get('row_count', len(chart_data.get('rows') or []))} 行，字段："
                    f"{'、'.join((chart_data.get('headers') or [])[:8])}"
                )
            flowchart_graph = item.get("flowchart_graph") or {}
            if flowchart_graph.get("nodes") or flowchart_graph.get("edges"):
                lines.append(
                    f"  - 流程结构：{flowchart_graph.get('node_count', len(flowchart_graph.get('nodes') or []))} 个节点，"
                    f"{flowchart_graph.get('edge_count', len(flowchart_graph.get('edges') or []))} 条关系"
                )
        if len(recognized_image_blocks) > 120:
            lines.append(f"- ... 其余 {len(recognized_image_blocks) - 120} 个图片语义块见 content_list_enhanced.json")
        lines.append("")
    ocr_candidates = [item for item in image_blocks if (item.get("ocr_vlm_candidate") or {}).get("needed")]
    if ocr_candidates:
        lines.extend(["## 按需 OCR/VLM 候选图像", ""])
        lines.append("这些图像面积较大但当前缺少可靠文字或结构化内容，建议在人工复核或智能体分析需要时再二次识别。")
        lines.append("")
        for item in ocr_candidates[:60]:
            candidate = item.get("ocr_vlm_candidate") or {}
            page = item.get("pdf_page_number") or "--"
            kind = item.get("semantic_kind") or item.get("type") or "image"
            lines.append(
                f"- 图像 {item.get('image_index')} / {kind} / PDF {page} 页 / "
                f"优先级 {candidate.get('priority') or '--'} / 面积 {round(candidate.get('bbox_area') or 0, 2)} / "
                f"{item.get('image_path')}"
            )
        if len(ocr_candidates) > 60:
            lines.append(f"- ... 其余 {len(ocr_candidates) - 60} 个候选图像见 content_list_enhanced.json")
        lines.append("")
    multi_header_tables = [
        table for table in tables if (table.get("structure") or {}).get("multi_level_header_candidate")
    ]
    if multi_header_tables:
        lines.extend(["## 多级表头候选表", ""])
        lines.append("完整表格结构请查看同目录 `content_list_enhanced.json` 的 `tables[].structure` 字段。")
        lines.append("")
        for table in multi_header_tables[:80]:
            structure = table.get("structure") or {}
            page = table.get("pdf_page_number") or "--"
            line = table.get("line") or "--"
            lines.append(
                f"- 表 {table.get('table_index')} / PDF {page} 页 / MD 行 {line} / "
                f"{structure.get('expanded_rows', 0)} 行 x {structure.get('expanded_columns', 0)} 列 / "
                f"表头候选 {structure.get('header_row_count', 0)} 行"
            )
            for preview in (structure.get("header_preview") or [])[:1]:
                lines.append(f"  - 表头预览：{preview}")
        if len(multi_header_tables) > 80:
            lines.append(f"- ... 其余 {len(multi_header_tables) - 80} 张表见 content_list_enhanced.json")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _complete_markdown_content(markdown, enhanced, corrections=None):
    base_markdown = str(markdown or "")
    if corrections is not None:
        base_markdown, _replaced_count = _apply_table_corrections(base_markdown, corrections)
    return base_markdown.rstrip() + _complete_markdown_appendix(enhanced)


def _write_complete_markdown_artifact(task, markdown, enhanced, corrections=None):
    if markdown is None or not isinstance(enhanced, dict):
        return None
    result_dir = _result_dir(task)
    os.makedirs(result_dir, exist_ok=True)
    complete_path = os.path.join(result_dir, "result_complete.md")
    complete_markdown = _complete_markdown_content(markdown, enhanced, corrections=corrections)
    with open(complete_path, "w", encoding="utf-8") as outfile:
        outfile.write(complete_markdown)
    return complete_path


def _file_reference_payload(path, url=None, kind=None):
    if not path:
        return None
    exists = os.path.exists(path)
    payload = {
        "path": path if exists else "",
        "exists": exists,
        "url": url or "",
    }
    if kind:
        payload["kind"] = kind
    if exists and os.path.isfile(path):
        payload["size_bytes"] = os.path.getsize(path)
        payload["mtime"] = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc).isoformat()
    return payload


def _image_resource_index(task):
    result_dir = _result_dir(task)
    images_dir = os.path.join(result_dir, "images")
    resources = []
    if not os.path.isdir(images_dir):
        return {
            "directory": _file_reference_payload(images_dir, f"/api/artifact/{task['task_id']}/images", kind="directory"),
            "items": [],
            "summary": {"count": 0, "total_size_bytes": 0},
        }
    total_size = 0
    for name in sorted(os.listdir(images_dir)):
        if not name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            continue
        path = os.path.join(images_dir, name)
        size = os.path.getsize(path) if os.path.isfile(path) else 0
        total_size += size
        resources.append(
            {
                "name": name,
                "path": path,
                "url": f"/api/artifact/{task['task_id']}/images/{name}",
                "size_bytes": size,
            }
        )
    return {
        "directory": _file_reference_payload(images_dir, f"/api/artifact/{task['task_id']}/images", kind="directory"),
        "items": resources,
        "summary": {"count": len(resources), "total_size_bytes": total_size},
    }


def _pdf_page_resource_index(task):
    result_dir = _result_dir(task)
    page_dir = os.path.join(result_dir, "pdf_pages")
    resources = []
    if os.path.isdir(page_dir):
        for name in sorted(os.listdir(page_dir)):
            if not name.lower().endswith(".png"):
                continue
            path = os.path.join(page_dir, name)
            match = re.search(r"page_(\d+)\.png$", name)
            resources.append(
                {
                    "page_number": int(match.group(1)) if match else None,
                    "name": name,
                    "path": path,
                    "url": f"/api/pdf_page/{task['task_id']}/{int(match.group(1))}" if match else "",
                    "size_bytes": os.path.getsize(path) if os.path.isfile(path) else 0,
                }
            )
    return {
        "directory": _file_reference_payload(page_dir, kind="directory"),
        "items": resources,
        "summary": {"rendered_page_count": len(resources), "total_size_bytes": sum(item.get("size_bytes") or 0 for item in resources)},
    }


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
    result_dir = _result_dir(task)
    content_list = _load_json_artifact(task, "content_list.json")
    middle_json = _load_json_artifact(task, "middle.json")
    model_output = _load_json_artifact(task, "model_output.json")
    payload_summary = _load_json_artifact(task, "result_payload_summary.json")
    markdown_path = task.get("markdown_path") or os.path.join(result_dir, "result.md")
    complete_path = os.path.join(result_dir, "result_complete.md")
    return {
        "schema_version": DOCUMENT_FULL_SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "task": {
            "task_id": task.get("task_id"),
            "mineru_task_id": task.get("mineru_task_id"),
            "filename": task.get("filename"),
            "status": task.get("status"),
            "stage": task.get("stage"),
            "created_at": task.get("created_at"),
            "completed_at": task.get("completed_at"),
            "pdf_page_count": task.get("pdf_page_count"),
            "submit_config": task.get("submit_config") or {},
        },
        "source_files": {
            "pdf": _file_reference_payload(task.get("upload_path"), kind="pdf"),
            "markdown": _file_reference_payload(markdown_path, f"/api/artifact/{task['task_id']}/result.md", kind="markdown"),
            "complete_markdown": _file_reference_payload(complete_path, f"/api/artifact/{task['task_id']}/result_complete.md", kind="markdown"),
        },
        "markdown": {
            "content": markdown or "",
            "chars": len(markdown or ""),
            "line_count": len(str(markdown or "").splitlines()),
            "pages": _markdown_page_index(markdown, content_list=content_list),
        },
        "content_list": content_list,
        "content_list_enhanced": enhanced,
        "middle_json": middle_json,
        "model_output": model_output,
        "result_payload_summary": payload_summary,
        "quality_report": quality_report,
        "table_relations": table_relations,
        "financial_data": financial_data,
        "financial_checks": financial_checks,
        "resources": {
            "images": _image_resource_index(task),
            "pdf_pages": _pdf_page_resource_index(task),
        },
        "artifacts": _artifact_status(task),
        "notes": [
            "本 JSON 保存 PDF 的完整解析信息、结构化索引和证据引用。",
            "为控制体积并保持可浏览性，PDF 原文件、页面截图和图片资源以 path/url 引用，不以内嵌 base64 保存。",
        ],
    }


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
        document_full["content_list_enhanced"] = enhanced
        document_full["table_relations"] = table_relations
        document_full.setdefault("artifacts", {})["content_list_enhanced.json"] = {
            "exists": True,
            "path": path,
            "url": f"/api/artifact/{task['task_id']}/content_list_enhanced.json",
        }
        document_full.setdefault("artifacts", {})["table_relations.json"] = {
            "exists": True,
            "path": _table_relations_path(task),
            "url": f"/api/artifact/{task['task_id']}/table_relations.json",
        }
        complete_path = os.path.join(result_dir, "result_complete.md")
        document_full.setdefault("source_files", {}).setdefault("complete_markdown", {})["path"] = complete_path
        document_full.setdefault("source_files", {}).setdefault("complete_markdown", {})["exists"] = os.path.exists(complete_path)
        document_full.setdefault("source_files", {}).setdefault("complete_markdown", {})["url"] = f"/api/artifact/{task['task_id']}/result_complete.md"
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
    markdown = str(markdown or "")
    table_sources = _content_table_sources(content_list)
    exact_table_sources, normalized_table_sources = _content_table_source_maps(table_sources)
    used_source_ids = set()
    page_markers = _pdf_page_markers_by_line(markdown)
    printed_pages = _printed_page_numbers_by_pdf_page(content_list)
    tables = []

    for idx, match in enumerate(re.finditer(r"<table\b.*?</table>", markdown, flags=re.IGNORECASE | re.DOTALL), start=1):
        table_html = match.group(0)
        line = markdown.count("\n", 0, match.start()) + 1
        source = _pop_unused_content_table_source(
            table_html,
            exact_table_sources,
            normalized_table_sources,
            used_source_ids,
        )
        pdf_page_number = source.get("pdf_page_number")
        pdf_page_index = source.get("pdf_page_index")
        printed_page_number = source.get("printed_page_number")
        source_name = source.get("source_match") if pdf_page_number else ""
        inferred_reason = ""
        if not pdf_page_number:
            inferred_page, inferred_reason = _inferred_pdf_page_for_line(line, page_markers)
            if inferred_page:
                pdf_page_number = inferred_page
                pdf_page_index = inferred_page - 1
                printed_page_number = printed_pages.get(inferred_page)
                source_name = "markdown_marker_inferred"

        table_html_text = _strip_html(table_html)
        structure = _table_structure_signals(table_html)
        tables.append(
            {
                "table_index": idx,
                "line": line,
                "source": source_name or "unresolved",
                "confidence": _table_source_confidence(source_name),
                "pdf_page_index": pdf_page_index,
                "pdf_page_number": pdf_page_number,
                "printed_page_number": printed_page_number,
                "pdf_page_inference_reason": inferred_reason if source_name == "markdown_marker_inferred" else "",
                "bbox": source.get("bbox") or [],
                "source_image_path": source.get("image_path") or "",
                "source_caption": source.get("caption") or [],
                "source_footnote": source.get("footnote") or [],
                "content_table_source_id": source.get("source_id"),
                "rows": _count_table_rows(table_html),
                "cells": _count_table_cells(table_html),
                "structure": structure,
                "preview": table_html_text[:220],
                "report_year": report_year,
            }
        )

    source_counts = Counter(item["source"] for item in tables)
    pages = _build_enhanced_page_blocks(content_list)
    footnotes = _build_enhanced_footnotes(markdown, content_list=content_list)
    toc = _build_enhanced_toc(markdown, content_list=content_list)
    financial_note_links = _build_financial_note_links(markdown, tables, page_markers)
    image_semantic_blocks = _build_image_semantic_blocks(markdown, content_list=content_list)
    return {
        "schema_version": CONTENT_LIST_ENHANCED_SCHEMA_VERSION,
        "report_year": report_year,
        "table_count": len(tables),
        "content_table_body_count": len(table_sources),
        "source_counts": dict(source_counts),
        "tables": tables,
        "pages": pages,
        "footnotes": footnotes,
        "toc": toc,
        "financial_note_links": financial_note_links,
        "image_semantic_blocks": image_semantic_blocks,
        "quality_signals": _build_enhanced_quality_signals(
            tables,
            footnotes,
            toc,
            pages,
            financial_note_links=financial_note_links,
            image_semantic_blocks=image_semantic_blocks,
        ),
    }


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
    summary = []
    for name in names:
        rows = key_table_candidates.get(name) or []
        if not rows:
            summary.append({"name": name, "status": "missing", "candidate_group": _candidate_group(name)})
            continue
        primary = dict(rows[0])
        primary["name"] = name
        primary["status"] = "found"
        primary["candidate_count"] = len(rows)
        summary.append(primary)
    return summary


def _required_core_financial_table_names(report_kind):
    if report_kind == "quarterly_report":
        return [name for name in CORE_FINANCIAL_TABLE_NAMES if name != "所有者权益变动表"]
    return list(CORE_FINANCIAL_TABLE_NAMES)


def _priority_review_tables(table_index, core_candidates, key_table_candidates):
    lookup = {item.get("table_index"): item for item in table_index}
    priority = []
    seen = set()

    def add_table(table_index_value, extra_reason=None):
        if not table_index_value or table_index_value in seen:
            return
        source = lookup.get(table_index_value)
        if not source:
            return
        item = dict(source)
        reasons = list(item.get("suspect_reasons") or [])
        if extra_reason and extra_reason not in reasons:
            reasons.append(extra_reason)
        if not reasons:
            return
        item["suspect_reasons"] = reasons
        priority.append(item)
        seen.add(table_index_value)

    for candidate in core_candidates:
        if candidate.get("status") != "found":
            continue
        reason = None
        if candidate.get("confidence") == "low":
            reason = "low_confidence_core_candidate"
        elif candidate.get("confidence") == "medium":
            reason = "medium_confidence_core_candidate"
        table_item = lookup.get(candidate.get("table_index"))
        if reason or (table_item and table_item.get("suspect_reasons")):
            add_table(candidate.get("table_index"), reason)

    for rows in key_table_candidates.values():
        for candidate in rows:
            table_item = lookup.get(candidate.get("table_index"))
            if table_item and table_item.get("suspect_reasons"):
                add_table(candidate.get("table_index"))

    for item in table_index:
        if item.get("suspect_reasons"):
            add_table(item.get("table_index"))

    return priority[:30]


def _build_quality_report(markdown, task, file_name=None, content_list=None):
    markdown = markdown or ""
    tables = re.findall(r"<table\b.*?</table>", markdown, flags=re.IGNORECASE | re.DOTALL)
    report_year = _detect_report_year(markdown, file_name=file_name or task.get("filename"))
    table_index = _build_table_index(markdown, tables, content_list=content_list, report_year=report_year)
    single_row_tables = [table for table in tables if _count_table_rows(table) <= 1]
    empty_cell_count = sum(_count_empty_cells(table) for table in tables)

    report_kind = _detect_report_kind(markdown, filename=file_name or task.get("filename"))
    financial_tables = _required_core_financial_table_names(report_kind)
    found_sections = [section for section in KEY_SECTIONS if section in markdown]
    key_table_candidates = _group_key_table_candidates(table_index)
    core_financial_table_candidates = _candidate_summary_list(key_table_candidates, financial_tables)
    indicator_table_candidates = _candidate_summary_list(key_table_candidates, INDICATOR_TABLE_NAMES)
    found_financial_tables = [
        item["name"] for item in core_financial_table_candidates
        if item.get("status") == "found"
    ]
    suspicious_tables = _priority_review_tables(
        table_index,
        core_financial_table_candidates,
        key_table_candidates,
    )

    image_refs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", markdown)
    warnings = []
    info_messages = []
    if task.get("pdf_page_count") and len(markdown) < int(task["pdf_page_count"]) * 800:
        warnings.append("Markdown 字符数相对页数偏少，建议检查是否有页面漏解析。")
    if tables:
        single_row_ratio = len(single_row_tables) / len(tables)
        if single_row_ratio > 0.2:
            warnings.append("单行/空壳表格比例偏高，建议用中间 JSON 和页面截图复核表格漏识别。")
    if image_refs:
        info_messages.append("Markdown 包含图片引用，images 目录将作为 PDF 视觉元素与截图证据来源。")
    if len(found_financial_tables) < 3:
        warnings.append("财报核心表标题召回偏少，建议检查目录、财务报告章节或启用局部重解析。")
    if suspicious_tables:
        warnings.append(f"发现 {len(suspicious_tables)} 张可疑表样本，建议在前端“优先复核表”中逐项打开可视化溯源。")

    return {
        "schema_version": QUALITY_SCHEMA_VERSION,
        "task_id": task["task_id"],
        "filename": file_name or task.get("filename"),
        "report_kind": report_kind,
        "report_year": report_year,
        "pdf_page_count": task.get("pdf_page_count"),
        "markdown_chars": len(markdown),
        "table_count": len(tables),
        "fact_table_count": len([item for item in table_index if item.get("table_type") == "fact"]),
        "dimension_table_count": len([item for item in table_index if item.get("table_type") == "dimension"]),
        "single_row_table_count": len(single_row_tables),
        "single_row_table_ratio": round(len(single_row_tables) / len(tables), 4) if tables else 0,
        "empty_cell_count": empty_cell_count,
        "image_ref_count": len(image_refs),
        "info_messages": info_messages,
        "found_sections": found_sections,
        "missing_sections": [section for section in KEY_SECTIONS if section not in found_sections],
        "found_financial_tables": found_financial_tables,
        "core_financial_table_candidates": core_financial_table_candidates,
        "indicator_table_candidates": indicator_table_candidates,
        "key_table_candidates": key_table_candidates,
        "suspicious_tables": suspicious_tables,
        "table_index": table_index,
        "warnings": warnings,
        "generated_at": _now_iso(),
    }


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
    _write_json(os.path.join(result_dir, "quality_report.json"), report)
    _write_json(os.path.join(result_dir, "table_index.json"), report.get("table_index", []))
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
    return os.path.join(_result_dir(task), "financial_data.json")


def _financial_checks_path(task):
    return os.path.join(_result_dir(task), "financial_checks.json")


def _read_financial_artifacts(task):
    data_path = _financial_data_path(task)
    checks_path = _financial_checks_path(task)
    if not os.path.exists(data_path) or not os.path.exists(checks_path):
        return None, None
    with open(data_path, "r", encoding="utf-8") as infile:
        data = json.load(infile)
    with open(checks_path, "r", encoding="utf-8") as infile:
        checks = json.load(infile)
    return data, checks


def _financial_artifacts_are_current(financial_data, financial_checks):
    return (
        isinstance(financial_data, dict)
        and isinstance(financial_checks, dict)
        and financial_data.get("schema_version") == FINANCIAL_DATA_SCHEMA_VERSION
        and financial_checks.get("schema_version") == FINANCIAL_CHECKS_SCHEMA_VERSION
        and financial_data.get("rule_version") == FINANCIAL_RULE_VERSION
        and financial_checks.get("rule_version") == FINANCIAL_RULE_VERSION
    )


def _write_financial_artifacts(task, markdown, file_name=None):
    result_dir = _result_dir(task)
    os.makedirs(result_dir, exist_ok=True)
    financial_data = build_financial_data(
        markdown,
        task_id=task.get("task_id"),
        filename=file_name or task.get("filename"),
        llm_cache_dir=os.path.join(FINANCIAL_LLM_CACHE_FOLDER, task.get("task_id") or "unknown"),
    )
    financial_checks = build_financial_checks(financial_data)
    _write_json(_financial_data_path(task), financial_data)
    _write_json(_financial_checks_path(task), financial_checks)
    return financial_data, financial_checks


def _ensure_financial_artifacts(task, markdown):
    financial_data, financial_checks = _read_financial_artifacts(task)
    if _financial_artifacts_are_current(financial_data, financial_checks):
        return financial_data, financial_checks
    return _write_financial_artifacts(task, markdown, file_name=task.get("filename"))


def _save_mineru_artifacts(task, upstream_response, file_name, file_data, markdown):
    result_dir = _result_dir(task)
    os.makedirs(result_dir, exist_ok=True)

    _write_json(
        os.path.join(result_dir, "result_payload_summary.json"),
        {
            "backend": upstream_response.get("backend"),
            "version": upstream_response.get("version"),
            "result_file": file_name,
            "file_keys": sorted(file_data.keys()) if isinstance(file_data, dict) else [],
        },
    )

    artifact_map = {
        "middle_json": "middle.json",
        "model_output": "model_output.json",
        "content_list": "content_list.json",
    }
    for key, filename in artifact_map.items():
        if isinstance(file_data, dict) and key in file_data:
            _write_json(os.path.join(result_dir, filename), file_data[key])

    image_count = 0
    if isinstance(file_data, dict) and "images" in file_data:
        image_count = _save_images(file_data["images"], os.path.join(result_dir, "images"))

    content_list = file_data.get("content_list") if isinstance(file_data, dict) else None
    quality_report = _write_quality_artifacts(
        task,
        markdown,
        file_name=file_name,
        content_list=content_list,
        saved_image_count=image_count,
    )
    return quality_report


def _quality_report_path(task):
    return os.path.join(_result_dir(task), "quality_report.json")


def _read_quality_report(task):
    report_path = _quality_report_path(task)
    if os.path.exists(report_path):
        return _read_json_cached(report_path)
    return None


def _ensure_quality_report(task, markdown):
    financial_data, financial_checks = _ensure_financial_artifacts(task, markdown)
    report = _read_quality_report(task)
    if report is not None and report.get("schema_version") == QUALITY_SCHEMA_VERSION:
        original_fields = {
            "found_financial_tables": report.get("found_financial_tables"),
            "core_financial_table_candidates": report.get("core_financial_table_candidates"),
            "financial_summary": report.get("financial_summary"),
            "financial_overall_status": report.get("financial_overall_status"),
            "financial_statement_count": report.get("financial_statement_count"),
            "financial_key_metric_count": report.get("financial_key_metric_count"),
            "warnings": report.get("warnings"),
        }
        report = _merge_quality_candidates_from_financial_data(report, financial_data)
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
            "warnings": report.get("warnings"),
        }
        if refreshed_fields != original_fields:
            _write_json(_quality_report_path(task), report)
            _write_json(os.path.join(_result_dir(task), "table_index.json"), report.get("table_index", []))
        return report
    report = _write_quality_artifacts(
        task,
        markdown,
        file_name=task.get("filename"),
        content_list=_load_json_artifact(task, "content_list.json"),
    )
    return report


def _artifact_status(task):
    result_dir = _result_dir(task)
    artifacts = {}
    for name in (
        "result.md",
        "result_complete.md",
        "document_full.json",
        "content_list_enhanced.json",
        "quality_report.json",
        "table_relations.json",
        "table_index.json",
        "financial_data.json",
        "financial_checks.json",
        "middle.json",
        "content_list.json",
        "model_output.json",
    ):
        path = os.path.join(result_dir, name)
        artifacts[name] = {
            "exists": os.path.exists(path),
            "path": path if os.path.exists(path) else "",
            "url": f"/api/artifact/{task['task_id']}/{name}" if os.path.exists(path) else "",
        }
    images_dir = os.path.join(result_dir, "images")
    artifacts["images"] = {
        "exists": os.path.isdir(images_dir),
        "path": images_dir if os.path.isdir(images_dir) else "",
        "url": f"/api/artifact/{task['task_id']}/images" if os.path.isdir(images_dir) else "",
    }
    return artifacts


ARTIFACT_OPEN_ALLOWLIST = {
    "result.md": ("text/markdown; charset=utf-8", False),
    "result_complete.md": ("text/markdown; charset=utf-8", False),
    "document_full.json": ("application/json; charset=utf-8", False),
    "quality_report.json": ("application/json; charset=utf-8", False),
    "table_relations.json": ("application/json; charset=utf-8", False),
    "table_index.json": ("application/json; charset=utf-8", False),
    "financial_data.json": ("application/json; charset=utf-8", False),
    "financial_checks.json": ("application/json; charset=utf-8", False),
    "middle.json": ("application/json; charset=utf-8", False),
    "content_list.json": ("application/json; charset=utf-8", False),
    "content_list_enhanced.json": ("application/json; charset=utf-8", False),
    "model_output.json": ("application/json; charset=utf-8", False),
}


def _artifact_file_response(path, mimetype):
    response = send_file(path, mimetype=mimetype, as_attachment=False)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _image_artifact_names(images_dir):
    return [
        name
        for name in sorted(os.listdir(images_dir))
        if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
        and os.path.isfile(os.path.join(images_dir, name))
    ]


def _markdown_excerpt(markdown, line, radius=12):
    lines = markdown.splitlines()
    if not lines:
        return []
    line = max(1, min(int(line or 1), len(lines)))
    start = max(1, line - radius)
    end = min(len(lines), line + radius)
    return [
        {
            "line": idx,
            "text": lines[idx - 1],
            "focus": idx == line,
        }
        for idx in range(start, end + 1)
    ]


def _table_html_by_index(markdown, table_index):
    for idx, match in enumerate(
        re.finditer(r"<table\b.*?</table>", markdown, flags=re.IGNORECASE | re.DOTALL),
        start=1,
    ):
        if idx == table_index:
            return match.group(0)
    return ""


def _apply_table_corrections(markdown, corrections):
    tables = corrections.get("tables", {}) if isinstance(corrections, dict) else {}
    replacements = {}
    for key, item in tables.items():
        if not isinstance(item, dict):
            continue
        if item.get("review_status") != "fixed":
            continue
        table_markdown = item.get("table_markdown")
        if not table_markdown:
            continue
        try:
            table_index = int(item.get("table_index") or key)
        except (TypeError, ValueError):
            continue
        replacements[table_index] = str(table_markdown)

    if not replacements:
        return markdown, 0

    replaced_count = 0

    def replace_match(match):
        nonlocal replaced_count
        replace_match.table_index += 1
        corrected = replacements.get(replace_match.table_index)
        if corrected is None:
            return match.group(0)
        replaced_count += 1
        return corrected

    replace_match.table_index = 0
    corrected_markdown = re.sub(
        r"<table\b.*?</table>",
        replace_match,
        markdown,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return corrected_markdown, replaced_count


def _fetch_and_cache_result(task, force=False):
    local_markdown = _read_markdown(task)
    if local_markdown is not None and (not force or not task.get("mineru_task_id")):
        return local_markdown

    mineru_task_id = task.get("mineru_task_id")
    if not mineru_task_id:
        if _task_requires_markdown_artifact(task) and local_markdown is None:
            _mark_completed_missing_artifact(task)
            return {"_error": True, "detail": task.get("error") or missing_artifact_message()}
        return None

    result_url = f"{MINERU_API_BASE}/tasks/{mineru_task_id}/result"
    resp = _json_request(result_url, timeout=30)
    if resp.get("_error"):
        detail = resp.get("detail", "Failed to fetch result")
        if _task_requires_markdown_artifact(task) and local_markdown is None:
            if resp.get("status") == 404:
                detail = "任务已完成，但本地 Markdown 结果不存在，且上游 MinerU 结果已不可拉取。"
            _mark_completed_missing_artifact(task, detail)
        return {"_error": True, "detail": detail}

    markdown = None
    selected_file_name = None
    selected_file_data = None
    results = resp.get("results")
    if isinstance(results, dict):
        for file_name, file_data in results.items():
            if isinstance(file_data, dict) and "md_content" in file_data:
                markdown = file_data["md_content"]
                selected_file_name = file_name
                selected_file_data = file_data
                break

    if markdown is not None:
        markdown = _inject_pdf_page_markers(
            markdown,
            selected_file_data.get("content_list") if isinstance(selected_file_data, dict) else None,
            total_pages=task.get("pdf_page_count"),
        )
        markdown, restored_pages = _backfill_sparse_markdown_pages(
            markdown,
            selected_file_data.get("content_list") if isinstance(selected_file_data, dict) else None,
        )
        _write_markdown(task, markdown)
        if selected_file_data is not None:
            quality_report = _save_mineru_artifacts(
                task, resp, selected_file_name, selected_file_data, markdown
            )
            _append_log(
                task,
                f"质量报告已生成: {quality_report['table_count']} 个表格, {quality_report['single_row_table_count']} 个单行/空壳表",
                "info",
            )
        _append_log(task, f"Markdown 结果已获取 ({len(markdown)} 字符)", "success")
        if restored_pages:
            _append_log(task, f"已从 content_list 回填 {len(restored_pages)} 个稀疏 Markdown 页", "info")
        task["status"] = COMPLETED
        task["stage"] = COMPLETED
        task["error"] = None
        task["completed_at"] = task.get("completed_at") or _now_iso()
        _persist_task(task)
    elif _task_requires_markdown_artifact(task) and local_markdown is None:
        detail = "任务已完成，但 MinerU 结果中没有可用的 Markdown 内容。"
        _mark_completed_missing_artifact(task, detail)
        return {"_error": True, "detail": detail}
    return markdown


def _build_status_response(task, logs_slice=None):
    elapsed = _task_elapsed_seconds(task)

    page_progress = _calc_page_progress(task, elapsed)
    progress_percent = _calc_progress_percent(task, elapsed)

    # If task is actually completed, override estimates to show 100%.
    if task.get("status") == COMPLETED and page_progress:
        page_progress["processed"] = page_progress["total"]
        page_progress["remaining"] = 0
        progress_percent = 100.0

    markdown_ready = _has_markdown_artifact(task)

    return {
        "task_id": task["task_id"],
        "status": task["status"],
        "stage": task["stage"],
        "queue_position": task.get("queue_position"),
        "local_queue_position": _local_queue_position(task["task_id"]),
        "filename": task["filename"],
        "file_size": task.get("file_size"),
        "pdf_page_count": task.get("pdf_page_count"),
        "error": task.get("error"),
        "elapsed_seconds": elapsed,
        "total_pages": task.get("pdf_page_count"),
        "processed_pages": page_progress["processed"] if page_progress else None,
        "progress_percent": progress_percent,
        "markdown_ready": markdown_ready,
        "log_count": len(task.get("logs", [])),
        "logs": logs_slice if logs_slice is not None else [],
    }


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
    if not _request_has_valid_token():
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
    for file, display_filename in zip(files, display_filenames):
        local_task_id = requested_task_id or str(uuid.uuid4())
        requested_task_id = None
        upload_path = os.path.join(UPLOAD_FOLDER, f"{local_task_id}.pdf")
        total_size = 0

        with open(upload_path, "wb") as outfile:
            while True:
                chunk = file.stream.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > MAX_FILE_SIZE:
                    _safe_unlink(upload_path)
                    return jsonify({"error": f"文件超过 {MAX_FILE_SIZE // 1024 // 1024} MB 限制: {display_filename}"}), 400
                outfile.write(chunk)

        if total_size == 0:
            _safe_unlink(upload_path)
            return jsonify({"error": f"空文件: {display_filename}"}), 400
        if not _looks_like_pdf(upload_path):
            _safe_unlink(upload_path)
            return jsonify({"error": f"文件内容不是有效 PDF: {display_filename}"}), 400

        pdf_page_count = _get_pdf_page_count(upload_path)
        if pdf_page_count and submit_config.get("end_page_id") not in (None, ""):
            if int(submit_config["end_page_id"]) >= int(pdf_page_count):
                _safe_unlink(upload_path)
                return jsonify({"error": f"结束页码超出 PDF 页数: {display_filename} 共 {pdf_page_count} 页"}), 400
        task = {
            "task_id": local_task_id,
            "mineru_task_id": None,
            "filename": display_filename,
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
        _append_log(task, f"文件上传成功: {display_filename} ({total_size // 1024 // 1024}MB)", "info")
        _append_log(task, "已加入本地解析队列，等待轮到当前任务。", "info")
        _persist_task(task, allow_insert=True)
        created_tasks.append(
            {
                "task_id": local_task_id,
                "filename": display_filename,
                "pdf_page_count": pdf_page_count,
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

    task["cancelled"] = True
    task["status"] = "cancelled"
    task["stage"] = "cancelled"
    task["completed_at"] = task.get("completed_at") or _now_iso()
    if upstream_cancelled:
        _append_log(task, "任务已取消，已通知 MinerU 停止处理。", "warn")
    else:
        if mineru_task_id:
            _append_log(task, "已停止本地查看；MinerU 后端可能仍在处理。", "warn")
        else:
            _append_log(task, "任务已从本地排队队列中移除。", "warn")
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

    since = request.args.get("since", "0").strip()
    try:
        since_index = max(0, int(since))
    except ValueError:
        since_index = 0

    if not task.get("cancelled"):
        try:
            task = _refresh_task_from_upstream(task)
        except RuntimeError as exc:
            task["consecutive_status_failures"] = int(task.get("consecutive_status_failures") or 0) + 1
            task["error"] = f"任务状态查询失败: {exc}"
            if task["consecutive_status_failures"] >= MINERU_STATUS_FAILURE_TOLERANCE:
                task["status"] = "failed"
                task["stage"] = "failed"
                task["completed_at"] = task.get("completed_at") or _now_iso()
                _append_log(task, task["error"], "error")
            else:
                _append_log(
                    task,
                    f"状态查询超时，第 {task['consecutive_status_failures']}/{MINERU_STATUS_FAILURE_TOLERANCE} 次，继续等待...",
                    "warn",
            )
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
    return jsonify({"markdown": markdown, "artifacts": _artifact_status(task)})


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
    return jsonify({"quality": _ensure_quality_report(task, markdown)})


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
    return jsonify(
        {
            "financial_data": financial_data,
            "financial_checks": financial_checks,
        }
    )


@app.route("/api/artifact/<task_id>/<path:artifact_name>", methods=["GET"])
def open_artifact(task_id, artifact_name):
    task = _get_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    result_dir = _result_dir(task)
    if artifact_name == "images/download":
        images_dir = os.path.join(result_dir, "images")
        if not os.path.isdir(images_dir):
            return jsonify({"error": "Images artifact not found"}), 404
        image_names = _image_artifact_names(images_dir)
        if not image_names:
            return jsonify({"error": "No downloadable images found"}), 404
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
            for name in image_names:
                zip_file.write(os.path.join(images_dir, name), arcname=name)
        archive.seek(0)
        filename = _safe_download_name(f"{task_id}_images.zip")
        response = send_file(
            archive,
            mimetype="application/zip",
            as_attachment=True,
            download_name=filename,
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    if artifact_name == "images":
        images_dir = os.path.join(result_dir, "images")
        if not os.path.isdir(images_dir):
            return jsonify({"error": "Images artifact not found"}), 404
        images = [
            {
                "name": name,
                "url": f"/api/artifact/{task_id}/images/{name}",
            }
            for name in _image_artifact_names(images_dir)
        ]
        return jsonify({"task_id": task_id, "artifact": "images", "count": len(images), "images": images})
    if artifact_name.startswith("images/"):
        image_name = _safe_client_filename(artifact_name.split("/", 1)[1])
        image_path = os.path.join(result_dir, "images", image_name)
        if not os.path.exists(image_path):
            return jsonify({"error": "Image artifact not found"}), 404
        mimetype = "image/png" if image_name.lower().endswith(".png") else "image/jpeg"
        return _artifact_file_response(image_path, mimetype)
    artifact_name = _safe_client_filename(artifact_name)
    if artifact_name not in ARTIFACT_OPEN_ALLOWLIST:
        return jsonify({"error": "Artifact is not openable"}), 403
    path = os.path.join(result_dir, artifact_name)
    if not os.path.exists(path):
        return jsonify({"error": "Artifact not found"}), 404
    mimetype, _binary = ARTIFACT_OPEN_ALLOWLIST[artifact_name]
    return _artifact_file_response(path, mimetype)


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
    table_item = None
    for item in report.get("table_index", []):
        if int(item.get("table_index") or 0) == table_index:
            table_item = item
            break
    if table_item is None:
        return jsonify({"error": "Table source not found"}), 404

    return jsonify(
        {
            "task_id": task_id,
            "filename": task.get("filename"),
            "table": table_item,
            "table_html": _table_html_by_index(markdown, table_index),
            "markdown_excerpt": _markdown_excerpt(markdown, table_item.get("line"), radius=14),
            "artifacts": _artifact_status(task),
            "correction": _load_corrections(task).get("tables", {}).get(str(table_index)),
            "page_content": _page_content_payload(
                task,
                table_item.get("pdf_page_number") or 1,
                report=report,
                focus_table=table_index,
            ),
            "pdf_page_image": {
                "url": (
                    f"/api/pdf_page/{task_id}/{table_item.get('pdf_page_number')}"
                    if table_item.get("pdf_page_number") else ""
                ),
                "page_number": table_item.get("pdf_page_number"),
                "pdf_page_number": table_item.get("pdf_page_number"),
                "printed_page_number": table_item.get("printed_page_number"),
                "page_count": task.get("pdf_page_count"),
                "bbox": table_item.get("bbox") or [],
                "bbox_extent": _page_bbox_extent(task, table_item.get("pdf_page_index")),
            },
        }
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
    return jsonify({"tasks": _list_recent_tasks(limit=_recent_task_list_limit())})


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
