"""SQLite task store for the generic document parser."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class TaskStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    document_kind TEXT DEFAULT 'unknown',
                    source_type TEXT DEFAULT 'upload',
                    source_url TEXT DEFAULT '',
                    status TEXT NOT NULL,
                    stage TEXT DEFAULT '',
                    progress_percent INTEGER DEFAULT 0,
                    file_size INTEGER DEFAULT 0,
                    file_sha256 TEXT DEFAULT '',
                    mime_type TEXT DEFAULT '',
                    parser_provider TEXT DEFAULT '',
                    quality_status TEXT DEFAULT '',
                    artifact_count INTEGER DEFAULT 0,
                    error TEXT DEFAULT '',
                    config_json TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    time TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL
                )
                """
            )

    def create_task(self, task: dict[str, Any]) -> None:
        now = now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    task_id, filename, document_kind, source_type, source_url, status, stage,
                    progress_percent, file_size, file_sha256, mime_type, parser_provider,
                    quality_status, artifact_count, error, config_json, created_at, updated_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task["task_id"],
                    task.get("filename") or task["task_id"],
                    task.get("document_kind", "unknown"),
                    task.get("source_type", "upload"),
                    task.get("source_url", ""),
                    task.get("status", "queued"),
                    task.get("stage", task.get("status", "queued")),
                    int(task.get("progress_percent", 0)),
                    int(task.get("file_size", 0)),
                    task.get("file_sha256", ""),
                    task.get("mime_type", ""),
                    task.get("parser_provider", ""),
                    task.get("quality_status", ""),
                    int(task.get("artifact_count", 0)),
                    task.get("error", ""),
                    json.dumps(task.get("config", {}), ensure_ascii=False),
                    now,
                    now,
                    task.get("completed_at", ""),
                ),
            )

    def update_task(self, task_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = now_iso()
        keys = []
        values = []
        for key, value in fields.items():
            if key == "config":
                key = "config_json"
                value = json.dumps(value, ensure_ascii=False)
            keys.append(f"{key} = ?")
            values.append(value)
        values.append(task_id)
        with self.connect() as conn:
            conn.execute(f"UPDATE tasks SET {', '.join(keys)} WHERE task_id = ?", values)

    def update_task_unless_cancelled(self, task_id: str, **fields: Any) -> bool:
        if not fields:
            return True
        fields["updated_at"] = now_iso()
        keys = []
        values = []
        for key, value in fields.items():
            if key == "config":
                key = "config_json"
                value = json.dumps(value, ensure_ascii=False)
            keys.append(f"{key} = ?")
            values.append(value)
        values.append(task_id)
        with self.connect() as conn:
            cursor = conn.execute(
                f"UPDATE tasks SET {', '.join(keys)} WHERE task_id = ? AND status != 'cancelled'",
                values,
            )
            return int(cursor.rowcount or 0) > 0

    def add_log(self, task_id: str, message: str, level: str = "info") -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO task_logs (task_id, time, level, message) VALUES (?, ?, ?, ?)",
                (task_id, now_iso(), level, message),
            )

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return self._row_to_task(row) if row else None

    def list_tasks(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def claim_next_queued_task(self) -> dict[str, Any] | None:
        """Atomically claim the oldest queued task for a local worker."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM tasks WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            task_id = row["task_id"]
            conn.execute(
                """
                UPDATE tasks
                SET status = 'running',
                    stage = 'running',
                    progress_percent = 10,
                    error = '',
                    updated_at = ?
                WHERE task_id = ? AND status = 'queued'
                """,
                (now_iso(), task_id),
            )
            conn.commit()
        claimed = self.get_task(task_id)
        return claimed

    def requeue_interrupted_tasks(self) -> int:
        """Return non-terminal tasks to queued on service startup."""
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET status = 'queued',
                    stage = 'queued',
                    progress_percent = 0,
                    updated_at = ?
                WHERE status IN ('uploaded', 'detecting_type', 'running', 'postprocessing')
                """,
                (now_iso(),),
            )
            return int(cursor.rowcount or 0)

    def get_logs(self, task_id: str, since: int = 0) -> tuple[list[dict[str, Any]], int]:
        since = max(0, int(since or 0))
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, time, level, message FROM task_logs WHERE task_id = ? AND id > ? ORDER BY id ASC",
                (task_id, since),
            ).fetchall()
            total = conn.execute(
                "SELECT COALESCE(MAX(id), 0) AS max_id FROM task_logs WHERE task_id = ?",
                (task_id,),
            ).fetchone()["max_id"]
        return [dict(row) for row in rows], int(total or 0)

    def delete_task(self, task_id: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM task_logs WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        try:
            item["config"] = json.loads(item.pop("config_json") or "{}")
        except json.JSONDecodeError:
            item["config"] = {}
        item["markdown_ready"] = item.get("status") in {"completed", "completed_with_warnings"}
        item["taskId"] = item.get("task_id")
        return item
