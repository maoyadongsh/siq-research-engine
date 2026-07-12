"""SQLite task repository helpers for the PDF parser app."""

from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter
from contextlib import nullcontext

from task_store import CANCELLED, COMPLETED_MISSING_ARTIFACT, is_failed_status

DEFAULT_OWNER_ID = "system"
DEFAULT_TENANT_ID = "unknown"
DEFAULT_MARKET_SCOPE = "unknown"
DEFAULT_PARSE_CONFIG_HASH = "unknown"
ACTIVE_CAPACITY_STATUSES = ("queued", "submitting", "submitted", "pending", "processing")


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
                    file_sha256 TEXT,
                    owner_id TEXT NOT NULL DEFAULT 'system',
                    tenant_id TEXT NOT NULL DEFAULT 'unknown',
                    market_scope TEXT NOT NULL DEFAULT 'unknown',
                    parse_config_hash TEXT NOT NULL DEFAULT 'unknown',
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
            if "file_sha256" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN file_sha256 TEXT")
            if "owner_id" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'system'")
            if "tenant_id" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'unknown'")
            if "market_scope" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN market_scope TEXT NOT NULL DEFAULT 'unknown'")
            if "parse_config_hash" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN parse_config_hash TEXT NOT NULL DEFAULT 'unknown'")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_file_sha256 ON tasks(file_sha256)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_owner_created_at ON tasks(owner_id, tenant_id, created_at DESC)"
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tasks_owner_dedupe
                ON tasks(owner_id, tenant_id, market_scope, file_sha256, parse_config_hash)
                """
            )
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
    task["owner_id"] = task.get("owner_id") or DEFAULT_OWNER_ID
    task["tenant_id"] = task.get("tenant_id") or DEFAULT_TENANT_ID
    task["market_scope"] = task.get("market_scope") or DEFAULT_MARKET_SCOPE
    task["parse_config_hash"] = task.get("parse_config_hash") or DEFAULT_PARSE_CONFIG_HASH
    task["legacy_owner"] = (
        task["owner_id"] == DEFAULT_OWNER_ID
        and task["tenant_id"] == DEFAULT_TENANT_ID
        and task["market_scope"] == DEFAULT_MARKET_SCOPE
    )
    return normalize_task(task) if normalize_task else task


def _task_payload(task):
    payload = dict(task)
    logs = payload.pop("logs", [])
    submit_config = payload.pop("submit_config", {})
    payload.setdefault("file_sha256", None)
    payload["owner_id"] = payload.get("owner_id") or DEFAULT_OWNER_ID
    payload["tenant_id"] = payload.get("tenant_id") or DEFAULT_TENANT_ID
    payload["market_scope"] = payload.get("market_scope") or DEFAULT_MARKET_SCOPE
    payload["parse_config_hash"] = payload.get("parse_config_hash") or DEFAULT_PARSE_CONFIG_HASH
    last_status_payload = payload.get("last_status_payload")
    payload["logs_json"] = json.dumps(logs, ensure_ascii=False)
    payload["submit_config_json"] = json.dumps(submit_config or {}, ensure_ascii=False)
    payload["last_status_payload"] = (
        json.dumps(last_status_payload, ensure_ascii=False) if last_status_payload is not None else None
    )
    return payload


def _insert_task_with_connection(conn, task):
    payload = _task_payload(task)
    conn.execute(
        """
        INSERT INTO tasks (
            task_id, mineru_task_id, filename, file_sha256,
            owner_id, tenant_id, market_scope, parse_config_hash,
            file_size, pdf_page_count,
            status, stage, created_at, uploaded_at, submitted_at, started_at,
            completed_at, cancelled, error, markdown_path, upload_path,
            last_progress_log_time, last_status_payload, last_polled_at,
            consecutive_status_failures, submit_config_json, logs_json
        ) VALUES (
            :task_id, :mineru_task_id, :filename, :file_sha256,
            :owner_id, :tenant_id, :market_scope, :parse_config_hash,
            :file_size, :pdf_page_count,
            :status, :stage, :created_at, :uploaded_at, :submitted_at, :started_at,
            :completed_at, :cancelled, :error, :markdown_path, :upload_path,
            :last_progress_log_time, :last_status_payload, :last_polled_at,
            :consecutive_status_failures, :submit_config_json, :logs_json
        )
        """,
        payload,
    )


def _update_task_with_connection(conn, task):
    payload = _task_payload(task)
    return conn.execute(
        """
        UPDATE tasks SET
            mineru_task_id=:mineru_task_id,
            filename=:filename,
            file_sha256=:file_sha256,
            owner_id=:owner_id,
            tenant_id=:tenant_id,
            market_scope=:market_scope,
            parse_config_hash=:parse_config_hash,
            file_size=:file_size,
            pdf_page_count=:pdf_page_count,
            status=:status,
            stage=:stage,
            created_at=:created_at,
            uploaded_at=:uploaded_at,
            submitted_at=:submitted_at,
            started_at=:started_at,
            completed_at=:completed_at,
            cancelled=:cancelled,
            error=:error,
            markdown_path=:markdown_path,
            upload_path=:upload_path,
            last_progress_log_time=:last_progress_log_time,
            last_status_payload=:last_status_payload,
            last_polled_at=:last_polled_at,
            consecutive_status_failures=:consecutive_status_failures,
            submit_config_json=:submit_config_json,
            logs_json=:logs_json
        WHERE task_id=:task_id
        """,
        payload,
    ).rowcount


def save_task(db_path, task, *, allow_insert=False, lock=None):
    """Persist internal task state without ever turning an insert into an update."""
    with _lock_context(lock):
        conn = connect(db_path)
        try:
            if allow_insert:
                _insert_task_with_connection(conn, task)
                changed = 1
            else:
                changed = _update_task_with_connection(conn, task)
            conn.commit()
            return bool(changed)
        finally:
            conn.close()


def admit_tasks_if_capacity(
    db_path,
    tasks,
    *,
    global_task_limit,
    owner_task_limit,
    global_bytes_limit,
    owner_bytes_limit,
    lock=None,
):
    """Atomically insert a new task batch without conflicts or oversubscription."""
    batch = [dict(task) for task in tasks]
    if not batch:
        return {"admitted": True, "reason": "empty_batch"}
    owner_id = batch[0].get("owner_id") or DEFAULT_OWNER_ID
    tenant_id = batch[0].get("tenant_id") or DEFAULT_TENANT_ID
    if any(
        (task.get("owner_id") or DEFAULT_OWNER_ID) != owner_id
        or (task.get("tenant_id") or DEFAULT_TENANT_ID) != tenant_id
        for task in batch
    ):
        raise ValueError("capacity batch must use one owner/tenant scope")
    requested_tasks = len(batch)
    requested_bytes = sum(max(0, int(task.get("file_size") or 0)) for task in batch)
    task_ids = [str(task.get("task_id") or "") for task in batch]
    if any(not task_id for task_id in task_ids):
        raise ValueError("capacity batch requires non-empty task_id")
    task_id_counts = Counter(task_ids)
    placeholders = ",".join("?" for _ in ACTIVE_CAPACITY_STATUSES)
    with _lock_context(lock):
        conn = connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            duplicate_batch_ids = sorted(task_id for task_id, count in task_id_counts.items() if count > 1)
            conflict_rows = []
            if task_ids:
                id_placeholders = ",".join("?" for _ in task_ids)
                conflict_rows = conn.execute(
                    f"SELECT task_id FROM tasks WHERE task_id IN ({id_placeholders})",
                    task_ids,
                ).fetchall()
            conflict_task_ids = sorted(
                set(duplicate_batch_ids) | {str(row["task_id"]) for row in conflict_rows}
            )
            if conflict_task_ids:
                conn.rollback()
                return {
                    "admitted": False,
                    "reason": "task_id_conflict",
                    "conflict_task_ids": conflict_task_ids,
                    "requested_tasks": requested_tasks,
                    "requested_bytes": requested_bytes,
                }
            global_row = conn.execute(
                f"""
                SELECT COUNT(*) AS task_count, COALESCE(SUM(file_size), 0) AS byte_count
                FROM tasks
                WHERE cancelled = 0 AND status IN ({placeholders})
                """,
                ACTIVE_CAPACITY_STATUSES,
            ).fetchone()
            owner_row = conn.execute(
                f"""
                SELECT COUNT(*) AS task_count, COALESCE(SUM(file_size), 0) AS byte_count
                FROM tasks
                WHERE cancelled = 0 AND status IN ({placeholders})
                  AND owner_id = ? AND tenant_id = ?
                """,
                (*ACTIVE_CAPACITY_STATUSES, owner_id, tenant_id),
            ).fetchone()
            capacity = {
                "global_active_tasks": int(global_row["task_count"]),
                "global_active_bytes": int(global_row["byte_count"]),
                "owner_active_tasks": int(owner_row["task_count"]),
                "owner_active_bytes": int(owner_row["byte_count"]),
                "requested_tasks": requested_tasks,
                "requested_bytes": requested_bytes,
                "global_task_limit": int(global_task_limit),
                "owner_task_limit": int(owner_task_limit),
                "global_bytes_limit": int(global_bytes_limit),
                "owner_bytes_limit": int(owner_bytes_limit),
            }
            exceeded = [
                name
                for name, current, requested, limit in (
                    ("global_tasks", capacity["global_active_tasks"], requested_tasks, global_task_limit),
                    ("owner_tasks", capacity["owner_active_tasks"], requested_tasks, owner_task_limit),
                    ("global_bytes", capacity["global_active_bytes"], requested_bytes, global_bytes_limit),
                    ("owner_bytes", capacity["owner_active_bytes"], requested_bytes, owner_bytes_limit),
                )
                if int(limit) > 0 and int(current) + int(requested) > int(limit)
            ]
            if exceeded:
                conn.rollback()
                return {"admitted": False, "reason": "capacity_exceeded", "exceeded": exceeded, **capacity}
            for task in batch:
                _insert_task_with_connection(conn, task)
            conn.commit()
            return {"admitted": True, "reason": "capacity_available", **capacity}
        finally:
            conn.close()


def save_tasks_if_capacity(db_path, tasks, **kwargs):
    """Compatibility wrapper for callers migrating to insert-only admission."""
    return admit_tasks_if_capacity(db_path, tasks, **kwargs)


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


def find_duplicate_file_hash_task(db_path, file_sha256, *, normalize_task=None):
    digest = str(file_sha256 or "").strip().lower()
    if not digest:
        return None
    conn = connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT * FROM tasks
            WHERE file_sha256 = ?
            ORDER BY created_at DESC
            """,
            (digest,),
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        task = row_to_task(row, normalize_task=normalize_task)
        if task_blocks_duplicate_upload(task):
            return task
    return None


def find_duplicate_scoped_file_hash_task(
    db_path,
    file_sha256,
    *,
    owner_id,
    tenant_id,
    market_scope,
    parse_config_hash,
    normalize_task=None,
):
    digest = str(file_sha256 or "").strip().lower()
    if not digest:
        return None
    conn = connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT * FROM tasks
            WHERE file_sha256 = ?
              AND owner_id = ?
              AND tenant_id = ?
              AND market_scope = ?
              AND parse_config_hash = ?
            ORDER BY created_at DESC
            """,
            (
                digest,
                owner_id or DEFAULT_OWNER_ID,
                tenant_id or DEFAULT_TENANT_ID,
                market_scope or DEFAULT_MARKET_SCOPE,
                parse_config_hash or DEFAULT_PARSE_CONFIG_HASH,
            ),
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        task = row_to_task(row, normalize_task=normalize_task)
        if task_blocks_duplicate_upload(task):
            return task
    return None


def list_recent_tasks(db_path, limit=100, *, normalize_task=None, owner_scope=None):
    conn = connect(db_path)
    try:
        where = ""
        params = []
        if owner_scope and not owner_scope.get("is_admin"):
            if owner_scope.get("allow_legacy_task"):
                where = (
                    "WHERE ((owner_id = ? AND tenant_id = ?) "
                    "OR (owner_id = 'system' AND tenant_id = 'unknown' AND market_scope = 'unknown'))"
                )
            else:
                where = "WHERE owner_id = ? AND tenant_id = ?"
            params.extend([
                owner_scope.get("owner_id") or DEFAULT_OWNER_ID,
                owner_scope.get("tenant_id") or DEFAULT_TENANT_ID,
            ])
            market_scope = owner_scope.get("market_scope")
            if market_scope and market_scope != DEFAULT_MARKET_SCOPE:
                where += " AND market_scope = ?"
                params.append(market_scope)
        rows = conn.execute(
            f"""
            SELECT task_id, filename, file_sha256, owner_id, tenant_id, market_scope,
                   parse_config_hash, status, stage, created_at, markdown_path, submit_config_json
            FROM tasks
            {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            tuple(params + [limit]),
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


def capacity_snapshot(db_path):
    placeholders = ",".join("?" for _ in ACTIVE_CAPACITY_STATUSES)
    conn = connect(db_path)
    try:
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS active_tasks, COALESCE(SUM(file_size), 0) AS active_bytes
            FROM tasks
            WHERE cancelled = 0 AND status IN ({placeholders})
            """,
            ACTIVE_CAPACITY_STATUSES,
        ).fetchone()
        queued = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE cancelled = 0 AND status = 'queued'"
        ).fetchone()[0]
    finally:
        conn.close()
    return {
        "active_tasks": int(row["active_tasks"]),
        "active_bytes": int(row["active_bytes"]),
        "queued_tasks": int(queued),
    }


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


def claim_next_queued_task(db_path, *, normalize_task=None, lock=None):
    with _lock_context(lock):
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
            return row_to_task(row, normalize_task=normalize_task)
        finally:
            conn.close()


def recover_stale_submitting_tasks(db_path, cutoff, *, lock=None):
    with _lock_context(lock):
        conn = connect(db_path)
        try:
            cursor = conn.execute(
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
            return cursor.rowcount
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
