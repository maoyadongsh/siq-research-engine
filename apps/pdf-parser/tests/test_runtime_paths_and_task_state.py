import os
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

try:
    import flask  # noqa: F401
except ModuleNotFoundError:
    class _DummyFlask:
        def __init__(self, *args, **kwargs):
            self.config = {}

        def route(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

        def before_request(self, func=None):
            def decorator(func):
                return func

            return decorator if func is None else func

        def errorhandler(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

    sys.modules.setdefault(
        "flask",
        types.SimpleNamespace(
            Flask=_DummyFlask,
            jsonify=lambda *args, **kwargs: None,
            make_response=lambda value: types.SimpleNamespace(
                value=value,
                set_cookie=lambda *args, **kwargs: None,
            ),
            render_template=lambda *args, **kwargs: "",
            request=types.SimpleNamespace(
                args={},
                files={},
                form={},
                headers={},
                cookies={},
                get_json=lambda silent=True: {},
            ),
            send_file=lambda *args, **kwargs: None,
        ),
    )

import app
from artifact_manager import cleanup_old_output_dirs
from path_config import resolve_app_paths
from task_store import CANCELLED, COMPLETED, COMPLETED_MISSING_ARTIFACT, FAILED, is_failed_status, is_success_status, is_terminal_status


class RuntimePathConfigTest(unittest.TestCase):
    def test_monorepo_data_layout_is_default(self):
        with patch.dict(os.environ, {}, clear=True):
            paths = resolve_app_paths("/tmp/project/apps/pdf-parser")

        self.assertTrue(paths["use_data_layout"])
        self.assertEqual(paths["uploads"], "/tmp/project/data/pdf-parser/uploads")
        self.assertEqual(paths["results"], "/tmp/project/data/pdf-parser/results")
        self.assertEqual(paths["output"], "/tmp/project/data/pdf-parser/output")
        self.assertEqual(paths["db"], "/tmp/project/data/pdf-parser/db/tasks.db")
        self.assertEqual(paths["financial_llm_cache"], "/tmp/project/data/pdf-parser/cache/financial_llm")

    def test_legacy_layout_can_be_requested(self):
        with patch.dict(os.environ, {"PDF2MD_USE_LEGACY_LAYOUT": "1"}, clear=True):
            paths = resolve_app_paths("/tmp/project/apps/pdf-parser")

        self.assertFalse(paths["use_data_layout"])
        self.assertEqual(paths["uploads"], "/tmp/project/apps/pdf-parser/uploads")
        self.assertEqual(paths["results"], "/tmp/project/apps/pdf-parser/results")
        self.assertEqual(paths["db"], "/tmp/project/apps/pdf-parser/tasks.db")


class TaskArtifactStateTest(unittest.TestCase):
    def test_task_state_helpers_cover_terminal_success_and_cancelled(self):
        self.assertTrue(is_success_status(COMPLETED))
        self.assertTrue(is_terminal_status(FAILED))
        self.assertTrue(is_terminal_status(CANCELLED))
        self.assertTrue(is_failed_status(FAILED))
        self.assertFalse(is_success_status(COMPLETED_MISSING_ARTIFACT))

    def test_completed_missing_artifact_is_terminal_failed_state(self):
        self.assertTrue(is_terminal_status(COMPLETED_MISSING_ARTIFACT))
        self.assertTrue(is_failed_status(COMPLETED_MISSING_ARTIFACT))

    def test_canonical_markdown_artifact_is_detected(self):
        old_results_folder = app.RESULTS_FOLDER
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                app.RESULTS_FOLDER = tmpdir
                task = {"task_id": "task-1", "markdown_path": None}
                self.assertFalse(app._has_markdown_artifact(task))

                result_dir = os.path.join(tmpdir, "task-1")
                os.makedirs(result_dir)
                with open(os.path.join(result_dir, "result.md"), "w", encoding="utf-8") as outfile:
                    outfile.write("# ok\n")

                self.assertTrue(app._has_markdown_artifact(task))
                self.assertEqual(app._markdown_artifact_path(task), os.path.join(result_dir, "result.md"))
        finally:
            app.RESULTS_FOLDER = old_results_folder

    def test_output_cleanup_removes_only_expired_children(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_dir = os.path.join(tmpdir, "old-output")
            new_dir = os.path.join(tmpdir, "new-output")
            os.makedirs(old_dir)
            os.makedirs(new_dir)
            now = time.time()
            os.utime(old_dir, (now - 3 * 3600, now - 3 * 3600))
            os.utime(new_dir, (now, now))

            removed = cleanup_old_output_dirs(tmpdir, retention_hours=1, now_ts=now)

            self.assertEqual(removed, 1)
            self.assertFalse(os.path.exists(old_dir))
            self.assertTrue(os.path.exists(new_dir))

    def test_cleanup_old_data_runs_output_cleanup_when_task_retention_disabled(self):
        old_output = app.OUTPUT_FOLDER
        old_task_retention = app.TASK_RETENTION_HOURS
        old_cleanup_output = app.CLEANUP_OUTPUT_FOLDER
        old_output_retention = app.OUTPUT_RETENTION_HOURS
        old_last_cleanup = app._last_cleanup_ts
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                old_dir = os.path.join(tmpdir, "old-output")
                os.makedirs(old_dir)
                stale = time.time() - 3 * 3600
                os.utime(old_dir, (stale, stale))
                app.OUTPUT_FOLDER = tmpdir
                app.TASK_RETENTION_HOURS = 0
                app.CLEANUP_OUTPUT_FOLDER = True
                app.OUTPUT_RETENTION_HOURS = 1
                app._last_cleanup_ts = 0

                app._cleanup_old_data(force=True)

                self.assertFalse(os.path.exists(old_dir))
        finally:
            app.OUTPUT_FOLDER = old_output
            app.TASK_RETENTION_HOURS = old_task_retention
            app.CLEANUP_OUTPUT_FOLDER = old_cleanup_output
            app.OUTPUT_RETENTION_HOURS = old_output_retention
            app._last_cleanup_ts = old_last_cleanup

    def test_refresh_recent_tasks_always_includes_active_upstream_tasks(self):
        old_db_path = app.DB_PATH
        refreshed = []
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                app.DB_PATH = os.path.join(tmpdir, "tasks.db")
                app._init_db()
                active = {
                    "task_id": "active-old",
                    "mineru_task_id": "mineru-active",
                    "filename": "active.pdf",
                    "file_size": 1,
                    "pdf_page_count": 10,
                    "status": "processing",
                    "stage": "processing",
                    "created_at": "2026-05-01T00:00:00Z",
                    "uploaded_at": "2026-05-01T00:00:00Z",
                    "submitted_at": "2026-05-01T00:01:00Z",
                    "started_at": "2026-05-01T00:01:00Z",
                    "completed_at": None,
                    "cancelled": False,
                    "error": None,
                    "markdown_path": None,
                    "upload_path": None,
                    "last_progress_log_time": None,
                    "last_status_payload": None,
                    "last_polled_at": None,
                    "consecutive_status_failures": 0,
                    "submit_config": {},
                    "logs": [],
                }
                app._save_task(active, allow_insert=True)
                for idx in range(60):
                    queued = dict(active)
                    queued.update(
                        {
                            "task_id": f"queued-{idx:02d}",
                            "mineru_task_id": None,
                            "filename": f"queued-{idx:02d}.pdf",
                            "status": "queued",
                            "stage": "queued",
                            "created_at": f"2026-05-01T00:{idx + 2:02d}:00Z",
                            "submitted_at": None,
                            "started_at": None,
                        }
                    )
                    app._save_task(queued, allow_insert=True)

                def fake_refresh(task):
                    refreshed.append(task["task_id"])
                    return task

                with patch.object(app, "_refresh_task_from_upstream", side_effect=fake_refresh):
                    app._refresh_recent_tasks(limit=50)

                self.assertIn("active-old", refreshed)
                self.assertIn("queued-59", refreshed)
        finally:
            app.DB_PATH = old_db_path

    def test_duplicate_filename_finds_existing_non_failed_task(self):
        old_db_path = app.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                app.DB_PATH = os.path.join(tmpdir, "tasks.db")
                app._init_db()
                base = {
                    "task_id": "completed-task",
                    "mineru_task_id": None,
                    "filename": "same.pdf",
                    "file_size": 1,
                    "pdf_page_count": 1,
                    "status": COMPLETED,
                    "stage": COMPLETED,
                    "created_at": "2026-05-01T00:00:00Z",
                    "uploaded_at": "2026-05-01T00:00:00Z",
                    "submitted_at": None,
                    "started_at": None,
                    "completed_at": "2026-05-01T00:01:00Z",
                    "cancelled": False,
                    "error": None,
                    "markdown_path": None,
                    "upload_path": None,
                    "last_progress_log_time": None,
                    "last_status_payload": None,
                    "last_polled_at": None,
                    "consecutive_status_failures": 0,
                    "submit_config": {},
                    "logs": [],
                }
                app._save_task(base, allow_insert=True)
                failed = dict(base)
                failed.update(
                    {
                        "task_id": "failed-newer-task",
                        "status": FAILED,
                        "stage": FAILED,
                        "created_at": "2026-05-01T00:02:00Z",
                        "completed_at": "2026-05-01T00:03:00Z",
                    }
                )
                app._save_task(failed, allow_insert=True)

                duplicate = app._find_duplicate_filename_task("same.pdf")

                self.assertIsNotNone(duplicate)
                self.assertEqual(duplicate["task_id"], "completed-task")
        finally:
            app.DB_PATH = old_db_path

    def test_duplicate_filename_ignores_failed_and_cancelled_tasks(self):
        old_db_path = app.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                app.DB_PATH = os.path.join(tmpdir, "tasks.db")
                app._init_db()
                base = {
                    "task_id": "failed-task",
                    "mineru_task_id": None,
                    "filename": "retry.pdf",
                    "file_size": 1,
                    "pdf_page_count": 1,
                    "status": FAILED,
                    "stage": FAILED,
                    "created_at": "2026-05-01T00:00:00Z",
                    "uploaded_at": "2026-05-01T00:00:00Z",
                    "submitted_at": None,
                    "started_at": None,
                    "completed_at": "2026-05-01T00:01:00Z",
                    "cancelled": False,
                    "error": None,
                    "markdown_path": None,
                    "upload_path": None,
                    "last_progress_log_time": None,
                    "last_status_payload": None,
                    "last_polled_at": None,
                    "consecutive_status_failures": 0,
                    "submit_config": {},
                    "logs": [],
                }
                app._save_task(base, allow_insert=True)
                cancelled = dict(base)
                cancelled.update(
                    {
                        "task_id": "cancelled-task",
                        "status": CANCELLED,
                        "stage": CANCELLED,
                        "created_at": "2026-05-01T00:02:00Z",
                        "cancelled": True,
                    }
                )
                app._save_task(cancelled, allow_insert=True)

                self.assertIsNone(app._find_duplicate_filename_task("retry.pdf"))
        finally:
            app.DB_PATH = old_db_path

    def test_get_task_requires_exact_task_id(self):
        old_db_path = app.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                app.DB_PATH = os.path.join(tmpdir, "tasks.db")
                app._init_db()
                task = {
                    "task_id": "abcdef12-3456-4789-8abc-def012345678",
                    "mineru_task_id": None,
                    "filename": "private.pdf",
                    "file_size": 1,
                    "pdf_page_count": 1,
                    "status": COMPLETED,
                    "stage": COMPLETED,
                    "created_at": "2026-05-01T00:00:00Z",
                    "uploaded_at": "2026-05-01T00:00:00Z",
                    "submitted_at": None,
                    "started_at": None,
                    "completed_at": "2026-05-01T00:01:00Z",
                    "cancelled": False,
                    "error": None,
                    "markdown_path": None,
                    "upload_path": None,
                    "last_progress_log_time": None,
                    "last_status_payload": None,
                    "last_polled_at": None,
                    "consecutive_status_failures": 0,
                    "submit_config": {},
                    "logs": [],
                }
                app._save_task(task, allow_insert=True)

                self.assertIsNone(app._get_task("abcdef12"))
                self.assertEqual(app._get_task(task["task_id"])["filename"], "private.pdf")
        finally:
            app.DB_PATH = old_db_path

    def test_recent_task_list_exposes_market_from_submit_config(self):
        old_db_path = app.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                app.DB_PATH = os.path.join(tmpdir, "tasks.db")
                app._init_db()
                task = {
                    "task_id": "hk-task",
                    "mineru_task_id": None,
                    "filename": "manual-hk-upload.pdf",
                    "file_size": 1,
                    "pdf_page_count": 1,
                    "status": COMPLETED,
                    "stage": COMPLETED,
                    "created_at": "2026-05-01T00:00:00Z",
                    "uploaded_at": "2026-05-01T00:00:00Z",
                    "submitted_at": None,
                    "started_at": None,
                    "completed_at": "2026-05-01T00:01:00Z",
                    "cancelled": False,
                    "error": None,
                    "markdown_path": None,
                    "upload_path": None,
                    "last_progress_log_time": None,
                    "last_status_payload": None,
                    "last_polled_at": None,
                    "consecutive_status_failures": 0,
                    "submit_config": {"market": "HK"},
                    "logs": [],
                }
                app._save_task(task, allow_insert=True)

                tasks = app._list_recent_tasks(limit=10)

                self.assertEqual(tasks[0]["market"], "HK")
                self.assertEqual(tasks[0]["submit_config"]["market"], "HK")
        finally:
            app.DB_PATH = old_db_path

    def test_recent_task_list_infers_market_from_filename_when_missing(self):
        old_db_path = app.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                app.DB_PATH = os.path.join(tmpdir, "tasks.db")
                app._init_db()
                task = {
                    "task_id": "eu-task",
                    "mineru_task_id": None,
                    "filename": "AstraZeneca-PLC_EU_AZN_2025-12-31_年报_2026-02-26_eu_direct_eb3a13dc.pdf",
                    "file_size": 1,
                    "pdf_page_count": 1,
                    "status": COMPLETED,
                    "stage": COMPLETED,
                    "created_at": "2026-05-01T00:00:00Z",
                    "uploaded_at": "2026-05-01T00:00:00Z",
                    "submitted_at": None,
                    "started_at": None,
                    "completed_at": "2026-05-01T00:01:00Z",
                    "cancelled": False,
                    "error": None,
                    "markdown_path": None,
                    "upload_path": None,
                    "last_progress_log_time": None,
                    "last_status_payload": None,
                    "last_polled_at": None,
                    "consecutive_status_failures": 0,
                    "submit_config": {},
                    "logs": [],
                }
                app._save_task(task, allow_insert=True)

                tasks = app._list_recent_tasks(limit=10)

                self.assertEqual(tasks[0]["market"], "EU")
                self.assertEqual(tasks[0]["submit_config"]["market"], "EU")
        finally:
            app.DB_PATH = old_db_path

    def test_task_market_from_record_prefers_filename_when_submit_config_missing(self):
        task = {
            "task_id": "jp-task",
            "filename": "Nintendo-Co.,-Ltd_JP_7974_2025-03-31_年报_2025-07-07_issuer_annual_report_3952349c.pdf",
            "submit_config": {},
        }

        self.assertEqual(app._task_market_from_record(task), "JP")


class ApiLayerTest(unittest.TestCase):
    def test_status_response_freezes_elapsed_time_for_completed_tasks(self):
        task = {
            "task_id": "task-1",
            "filename": "task.pdf",
            "status": "completed",
            "stage": "completed",
            "started_at": "2026-05-01T00:00:00Z",
            "completed_at": "2026-05-01T00:03:00Z",
            "logs": [],
        }

        with patch.object(app, "_utc_now", return_value=app.datetime(2026, 5, 1, 0, 10, 0)):
            payload = app._build_status_response(task)

        self.assertEqual(payload["elapsed_seconds"], 180)

    def test_health_endpoint_uses_submit_readiness_payload(self):
        if not hasattr(app.app, "test_client"):
            self.skipTest("Flask test client is unavailable in the lightweight import stub")
        readiness = {
            "mineru": True,
            "mineru_detail": "",
            "mineru_payload": {"status": "healthy"},
            "vlm": True,
            "vlm_detail": "",
            "submit_ready": True,
            "warning": "",
        }
        with patch.object(app, "initialize_app"), patch.object(app, "_mineru_submit_readiness", return_value=readiness):
            client = app.app.test_client()
            response = client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["flask"])
        self.assertTrue(payload["submit_ready"])
        self.assertEqual(payload["mineru_stats"], {"status": "healthy"})


if __name__ == "__main__":
    unittest.main()
