"""SQLite task repository helpers for the PDF parser app."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import nullcontext

from task_store import CANCELLED, COMPLETED_MISSING_ARTIFACT, is_failed_status


def _lock_context(lock):
    return lock if lock is not None else nullcontext()


def connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def task_exists(db_path, task_id):
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT 1 FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return row is not None
    finally:
        conn.close()


def init_db(db_path, lock=None):
    with _lock_context(lock):
        conn = connect(db_path)
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at DESC)")
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
            if "consecutive_status_failures" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN consecutive_status_failures INTEGER NOT NULL DEFAULT 0")
            if "submit_config_json" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN submit_config_json TEXT")
            conn.commit()
        finally:
            conn.close()


def row_to_task(row, *, normalize_task=None):
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
    return normalize_task(task) if normalize_task else task


def save_task(db_path, task, *, allow_insert=False, lock=None):
    payload = dict(task)
    logs = payload.pop("logs", [])
    submit_config = payload.pop("submit_config", {})
    last_status_payload = payload.get("last_status_payload")
    payload["logs_json"] = json.dumps(logs, ensure_ascii=False)
    payload["submit_config_json"] = json.dumps(submit_config or {}, ensure_ascii=False)
    payload["last_status_payload"] = (
        json.dumps(last_status_payload, ensure_ascii=False) if last_status_payload is not None else None
    )
    if not allow_insert and not task_exists(db_path, payload["task_id"]):
        return
    with _lock_context(lock):
        conn = connect(db_path)
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


def get_task(db_path, task_id, *, normalize_task=None):
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return row_to_task(row, normalize_task=normalize_task) if row else None
    finally:
        conn.close()


def task_blocks_duplicate_upload(task):
    if not task:
        return False
    status = str(task.get("status") or "").lower()
    if task.get("cancelled") or status == CANCELLED:
        return False
    if is_failed_status(status):
        return False
    return True


def find_duplicate_filename_task(db_path, filename, *, normalize_filename=None, normalize_task=None):
    display_filename = normalize_filename(filename) if normalize_filename else str(filename or "").strip()
    conn = connect(db_path)
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
        task = row_to_task(row, normalize_task=normalize_task)
        if task_blocks_duplicate_upload(task):
            return task
    return None


def list_recent_tasks(db_path, limit=100, *, normalize_task=None):
    conn = connect(db_path)
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
        if normalize_task:
            normalize_task(task)
        task["local_queue_position"] = queued_order.get(task["task_id"])
    return tasks


def task_ids_for_recent_refresh(db_path, limit=50):
    conn = connect(db_path)
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
    return task_ids


def has_active_upstream_task(db_path):
    conn = connect(db_path)
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


def next_queued_task(db_path, *, normalize_task=None):
    conn = connect(db_path)
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
        return row_to_task(row, normalize_task=normalize_task)
    finally:
        conn.close()


def local_queue_position(db_path, task_id):
    conn = connect(db_path)
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


def delete_task_record(db_path, task_id, lock=None):
    with _lock_context(lock):
        conn = connect(db_path)
        try:
            conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
            conn.commit()
        finally:
            conn.close()


def referenced_task_paths(db_path, results_folder):
    conn = connect(db_path)
    try:
        rows = conn.execute("SELECT task_id, upload_path, markdown_path FROM tasks").fetchall()
        paths = set()
        task_ids = set()
        for row in rows:
            task_ids.add(row["task_id"])
            for key in ("upload_path", "markdown_path"):
                if row[key]:
                    paths.add(os.path.abspath(row[key]))
            paths.add(os.path.abspath(os.path.join(results_folder, row["task_id"])))
            paths.add(os.path.abspath(os.path.join(results_folder, f"{row['task_id']}.md")))
        return task_ids, paths
    finally:
        conn.close()
