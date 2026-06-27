#!/usr/bin/env python3
"""Generic document parsing service."""

from __future__ import annotations

import hmac
import ipaddress
import json
import os
import shutil
import socket
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from flask import Flask, Response, jsonify, request, send_file

from artifacts import artifact_summary, build_artifacts, read_json, write_json
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
from path_config import resolve_app_paths
from provider_router import parse_source
from providers.simple import parse_json_schema_excerpt
from task_store import TaskStore, now_iso


BASE_DIR = Path(__file__).resolve().parent
APP_PATHS = resolve_app_paths(BASE_DIR)
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
    if not APP_ACCESS_TOKEN:
        return None
    if request.path == "/api/health":
        return None
    provided = request.headers.get("X-Document-Parser-Token", "")
    if not hmac.compare_digest(provided, APP_ACCESS_TOKEN):
        return jsonify({"error": "unauthorized"}), 401
    return None


def _parse_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


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


def _create_task_record(task_id: str, source: SourceFile, config: ParseConfig, document_kind: str) -> dict:
    store.create_task(
        {
            "task_id": task_id,
            "filename": source.filename,
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


def _enqueue_task(source: SourceFile, config: ParseConfig, task_id: str | None = None) -> dict:
    task_id = task_id or str(uuid.uuid4())
    document_kind = document_kind_for_extension(source.extension)
    return _create_task_record(task_id, source, config, document_kind)


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
        output = parse_source(task_id, source, config, document_kind)
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
        status = COMPLETED if manifest.get("quality_status") == "pass" else COMPLETED_WITH_WARNINGS
        artifacts = artifact_summary(task_id, _task_result_dir(task_id))
        store.update_task_unless_cancelled(
            task_id,
            status=status,
            stage=status,
            progress_percent=100,
            parser_provider=manifest.get("parser_provider", ""),
            quality_status=manifest.get("quality_status", ""),
            artifact_count=sum(1 for item in artifacts.values() if item.get("exists")),
            completed_at=now_iso(),
        )
        store.add_log(task_id, "解析产物已生成")
    except Exception as exc:
        store.update_task(task_id, status=FAILED, stage=FAILED, progress_percent=0, error=str(exc), completed_at=now_iso())
        store.add_log(task_id, f"解析失败: {exc}", level="error")
    return store.get_task(task_id) or {"task_id": task_id, "status": FAILED}


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


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "version": APP_VERSION,
        "data_dir": str(DATA_DIR),
        "providers": {
            "simple_text_parser": True,
            "html_reader": True,
            "pypdf_text_parser": True,
            "spreadsheet_parser": True,
            "office_local": True,
            "image_local": True,
            "cloud_mineru_enabled": os.environ.get("SIQ_DOCUMENT_PARSE_CLOUD_ENABLED", "false").lower() in {"1", "true", "yes"},
        },
        "max_file_mb": MAX_FILE_SIZE // 1024 // 1024,
        "max_files_per_upload": MAX_FILES_PER_UPLOAD,
    }


@app.route("/api/tasks", methods=["POST"])
def create_tasks():
    tasks = []
    config = _parse_config(request.form)
    files = request.files.getlist("files")
    if files:
        if len(files) > MAX_FILES_PER_UPLOAD:
            return jsonify({"error": "too_many_files", "message": f"一次最多上传 {MAX_FILES_PER_UPLOAD} 个文件"}), 400
        for file_storage in files:
            task_id = str(uuid.uuid4())
            source = _save_upload(task_id, file_storage)
            tasks.append(_enqueue_task(source, config, task_id=task_id))
        return jsonify({"tasks": tasks})

    payload = request.get_json(silent=True) or {}
    if str(payload.get("source_type") or payload.get("sourceType") or "").lower() == "url" or payload.get("url"):
        config = _parse_config(payload=payload)
        task_id = str(uuid.uuid4())
        source = _download_url(task_id, str(payload.get("url") or "").strip())
        tasks.append(_enqueue_task(source, config, task_id=task_id))
        return jsonify({"tasks": tasks})

    return jsonify({"error": "no_source", "message": "请上传文件或提供 URL"}), 400


@app.get("/api/tasks")
def list_tasks():
    limit = int(request.args.get("limit") or 200)
    return jsonify({"tasks": store.list_tasks(limit=limit)})


@app.get("/api/tasks/<task_id>")
def get_task(task_id: str):
    task = store.get_task(task_id)
    if not task:
        return jsonify({"error": "not_found"}), 404
    return jsonify(task)


@app.get("/api/status/<task_id>")
def task_status(task_id: str):
    task = store.get_task(task_id)
    if not task:
        return jsonify({"error": "not_found"}), 404
    logs, log_count = store.get_logs(task_id, since=int(request.args.get("since") or 0))
    payload = dict(task)
    payload["logs"] = logs
    payload["log_count"] = log_count
    payload["artifacts_ready"] = payload.get("status") in {COMPLETED, COMPLETED_WITH_WARNINGS}
    return jsonify(payload)


@app.post("/api/cancel/<task_id>")
def cancel_task(task_id: str):
    task = store.get_task(task_id)
    if not task:
        return jsonify({"error": "not_found"}), 404
    if task.get("status") not in TERMINAL_STATUSES:
        store.update_task(task_id, status=CANCELLED, stage=CANCELLED, completed_at=now_iso())
        store.add_log(task_id, "任务已取消")
    return jsonify({"success": True, "task_id": task_id})


@app.post("/api/retry/<task_id>")
def retry_task(task_id: str):
    task = store.get_task(task_id)
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
    store.delete_task(task_id)
    shutil.rmtree(_task_result_dir(task_id), ignore_errors=True)
    return jsonify(_enqueue_task(source, config, task_id=task_id))


@app.delete("/api/tasks/<task_id>")
def delete_task(task_id: str):
    store.delete_task(task_id)
    shutil.rmtree(_task_upload_dir(task_id), ignore_errors=True)
    shutil.rmtree(_task_result_dir(task_id), ignore_errors=True)
    return jsonify({"success": True, "task_id": task_id})


@app.get("/api/result/<task_id>")
def result(task_id: str):
    task = store.get_task(task_id)
    if not task:
        return jsonify({"error": "not_found"}), 404
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
    result_dir = _task_result_dir(task_id)
    normalized = artifact.strip().replace("\\", "/").rstrip("/")
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
    if normalized not in ARTIFACT_ALLOWLIST and not normalized.startswith(("images/original/", "images/crops/", "images/page_previews/", "exports/")):
        return jsonify({"error": "artifact_not_allowed"}), 403
    try:
        path = safe_artifact_path(result_dir, normalized)
    except ValueError:
        return jsonify({"error": "invalid_artifact"}), 400
    if not path.exists() or not path.is_file():
        return jsonify({"error": "not_found"}), 404
    as_attachment = request.args.get("download") in {"1", "true", "yes"} or normalized.startswith("exports/")
    return send_file(path, as_attachment=as_attachment, download_name=path.name if as_attachment else None)


@app.get("/api/download/<task_id>")
def download_full(task_id: str):
    return artifact(task_id, "exports/full.zip")


@app.get("/api/figures/<task_id>")
def list_figures(task_id: str):
    path = _task_result_dir(task_id) / "figures.json"
    if not path.exists():
        return jsonify({"error": "not_found"}), 404
    return jsonify(read_json(path))


@app.get("/api/figures/<task_id>/<image_id>")
def get_figure(task_id: str, image_id: str):
    figures_path = _task_result_dir(task_id) / "figures.json"
    if not figures_path.exists():
        return jsonify({"error": "not_found"}), 404
    figures = read_json(figures_path).get("figures") or []
    for figure in figures:
        if figure.get("image_id") == image_id:
            return jsonify({"figure": figure})
    return jsonify({"error": "not_found"}), 404


@app.get("/api/source/<task_id>/page/<int:page_number>")
def source_page(task_id: str, page_number: int):
    blocks_path = _task_result_dir(task_id) / "blocks.json"
    if not blocks_path.exists():
        return jsonify({"error": "not_found"}), 404
    blocks = read_json(blocks_path).get("blocks") or []
    page_blocks = [block for block in blocks if int(block.get("page_number") or 1) == page_number]
    return jsonify({"task_id": task_id, "page_number": page_number, "blocks": page_blocks, "block_count": len(page_blocks)})


@app.get("/api/source/<task_id>/block/<block_id>")
def source_block(task_id: str, block_id: str):
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
    return get_figure(task_id, image_id)


@app.get("/api/table-relations/<task_id>")
def table_relations(task_id: str):
    path = _task_result_dir(task_id) / "table_relations.json"
    if not path.exists():
        return jsonify({"error": "not_found"}), 404
    return jsonify(read_json(path))


@app.post("/api/table-relations/<task_id>/<relation_id>/review")
def review_table_relation(task_id: str, relation_id: str):
    result_dir = _task_result_dir(task_id)
    corrections_path = result_dir / "table_merge_corrections.json"
    corrections = read_json(corrections_path) if corrections_path.exists() else {"schema_version": "document_table_merge_corrections_v1", "task_id": task_id, "relations": {}, "manual_logical_tables": []}
    payload = request.get_json(silent=True) or {}
    corrections.setdefault("relations", {})[relation_id] = {
        "review_status": payload.get("review_status") or payload.get("reviewStatus") or "accepted",
        "note": payload.get("note") or "",
        "updated_at": now_iso(),
    }
    write_json(corrections_path, corrections)
    return jsonify({"success": True, "corrections": corrections})


@app.post("/api/logical-tables/<task_id>/<logical_table_id>/split")
def split_logical_table(task_id: str, logical_table_id: str):
    return jsonify({"success": False, "message": "P0 provider has no merged logical tables to split", "logical_table_id": logical_table_id})


@app.post("/api/logical-tables/<task_id>/merge")
def merge_logical_tables(task_id: str):
    return jsonify({"success": False, "message": "P0 provider does not support manual logical table merge yet", "task_id": task_id})


@app.post("/api/extract/<task_id>")
def extract(task_id: str):
    result_dir = _task_result_dir(task_id)
    markdown_path = result_dir / "document.md"
    if not markdown_path.exists():
        return jsonify({"error": "not_found"}), 404
    payload = request.get_json(silent=True) or {}
    schema = payload.get("schema") or {}
    result_data = parse_json_schema_excerpt(schema, markdown_path.read_text(encoding="utf-8"))
    extract_id = str(uuid.uuid4())
    evidence_map = {key: [] for key in result_data}
    validation = {
        "schema_valid": True,
        "evidence_coverage_ratio": 0.0,
        "warnings": [
            {
                "code": "rule_based_excerpt_only",
                "severity": "warning",
                "message": "P0 抽取仅执行字段名冒号匹配，未调用 LLM。",
            }
        ],
    }
    write_json(result_dir / "extraction" / "schema.json", {"schema_version": "document_extraction_schema_v1", "task_id": task_id, "schema": schema})
    write_json(result_dir / "extraction" / "result.json", {"schema_version": "document_extraction_result_v1", "task_id": task_id, "extract_id": extract_id, "status": "completed", "result": result_data})
    write_json(result_dir / "extraction" / "evidence_map.json", {"schema_version": "document_extraction_evidence_v1", "task_id": task_id, "extract_id": extract_id, "evidence_map": evidence_map})
    write_json(result_dir / "extraction" / "validation_report.json", {"schema_version": "document_extraction_validation_v1", "task_id": task_id, **validation})
    return jsonify({"extract_id": extract_id, "status": "completed", "result": result_data, "evidence_map": evidence_map, "validation_report": validation})


@app.get("/api/extract/<task_id>/<extract_id>")
def extract_result(task_id: str, extract_id: str):
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
