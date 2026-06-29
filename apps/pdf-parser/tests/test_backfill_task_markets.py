import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "backfill_task_markets.py"
SPEC = importlib.util.spec_from_file_location("backfill_task_markets", SCRIPT_PATH)
backfill_task_markets = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(backfill_task_markets)


class BackfillTaskMarketsTest(unittest.TestCase):
    def test_main_backfills_only_missing_markets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "tasks.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE tasks (
                        task_id TEXT PRIMARY KEY,
                        filename TEXT NOT NULL,
                        submit_config_json TEXT,
                        created_at TEXT
                    )
                    """
                )
                conn.executemany(
                    "INSERT INTO tasks (task_id, filename, submit_config_json, created_at) VALUES (?, ?, ?, ?)",
                    [
                        (
                            "cn-task",
                            "美的集团：2025年年度报告.pdf",
                            json.dumps({}, ensure_ascii=False),
                            "2026-05-01T00:00:00Z",
                        ),
                        (
                            "hk-task",
                            "HK_0001_annual.pdf",
                            json.dumps({"market": "HK"}, ensure_ascii=False),
                            "2026-05-01T00:01:00Z",
                        ),
                    ],
                )
                conn.commit()

            code = backfill_task_markets.main(["--db", str(db_path)])
            self.assertEqual(code, 0)

            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT task_id, submit_config_json FROM tasks ORDER BY task_id").fetchall()

            cn_cfg = json.loads(rows[0]["submit_config_json"])
            hk_cfg = json.loads(rows[1]["submit_config_json"])
            self.assertEqual(cn_cfg["market"], "CN")
            self.assertEqual(hk_cfg["market"], "HK")

    def test_dry_run_leaves_database_untouched(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "tasks.db"
            original = json.dumps({}, ensure_ascii=False)
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "CREATE TABLE tasks (task_id TEXT PRIMARY KEY, filename TEXT NOT NULL, submit_config_json TEXT, created_at TEXT)"
                )
                conn.execute(
                    "INSERT INTO tasks (task_id, filename, submit_config_json, created_at) VALUES (?, ?, ?, ?)",
                    ("cn-task", "美的集团：2025年年度报告.pdf", original, "2026-05-01T00:00:00Z"),
                )
                conn.commit()

            code = backfill_task_markets.main(["--db", str(db_path), "--dry-run"])
            self.assertEqual(code, 0)

            with sqlite3.connect(db_path) as conn:
                submit_config_json = conn.execute(
                    "SELECT submit_config_json FROM tasks WHERE task_id = ?",
                    ("cn-task",),
                ).fetchone()[0]

            self.assertEqual(submit_config_json, original)


if __name__ == "__main__":
    unittest.main()
