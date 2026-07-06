import io
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

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
import pdf_parser_document_full_service as document_full_service
import pdf_parser_response_service as response_service
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

    def test_runtime_and_artifacts_roots_are_opt_in(self):
        with patch.dict(
            os.environ,
            {
                "SIQ_DATA_ROOT": "/tmp/state/data",
                "SIQ_RUNTIME_ROOT": "/tmp/state/runtime",
                "SIQ_ARTIFACTS_ROOT": "/tmp/state/artifacts",
            },
            clear=True,
        ):
            paths = resolve_app_paths("/tmp/project/apps/pdf-parser")

        self.assertEqual(paths["data_root"], "/tmp/state/data")
        self.assertEqual(paths["runtime_root"], "/tmp/state/runtime")
        self.assertEqual(paths["artifacts_root"], "/tmp/state/artifacts")
        self.assertEqual(paths["data_dir"], "/tmp/state/runtime/pdf-parser")
        self.assertEqual(paths["uploads"], "/tmp/state/runtime/pdf-parser/uploads")
        self.assertEqual(paths["db"], "/tmp/state/runtime/pdf-parser/db/tasks.db")
        self.assertEqual(paths["results"], "/tmp/state/artifacts/pdf-parser/results")
        self.assertEqual(paths["output"], "/tmp/state/artifacts/pdf-parser/output")
        self.assertIn("/tmp/project/data/pdf-parser/results", paths["results_candidates"])
        self.assertIn("/tmp/project/data/pdf-parser/output", paths["output_candidates"])

    def test_legacy_env_overrides_generic_runtime_roots(self):
        with patch.dict(
            os.environ,
            {
                "SIQ_RUNTIME_ROOT": "/tmp/state/runtime",
                "SIQ_ARTIFACTS_ROOT": "/tmp/state/artifacts",
                "PDF2MD_DATA_DIR": "/tmp/legacy/pdf-data",
                "RESULTS_FOLDER": "/tmp/legacy/results",
            },
            clear=True,
        ):
            paths = resolve_app_paths("/tmp/project/apps/pdf-parser")

        self.assertEqual(paths["data_dir"], "/tmp/legacy/pdf-data")
        self.assertEqual(paths["uploads"], "/tmp/legacy/pdf-data/uploads")
        self.assertEqual(paths["results"], "/tmp/legacy/results")

    def test_resolver_does_not_create_or_migrate_legacy_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "project"
            base_dir = project_root / "apps" / "pdf-parser"
            legacy_result = project_root / "data" / "pdf-parser" / "results" / "legacy-task" / "result.md"
            base_dir.mkdir(parents=True)
            legacy_result.parent.mkdir(parents=True)
            legacy_result.write_text("# legacy\n", encoding="utf-8")

            state_root = Path(tmpdir) / "state"
            with patch.dict(
                os.environ,
                {
                    "SIQ_RUNTIME_ROOT": str(state_root / "runtime"),
                    "SIQ_ARTIFACTS_ROOT": str(state_root / "artifacts"),
                },
                clear=True,
            ):
                paths = resolve_app_paths(str(base_dir))

            self.assertTrue(legacy_result.is_file())
            self.assertFalse((state_root / "runtime").exists())
            self.assertFalse((state_root / "artifacts").exists())
            self.assertIn(str(project_root / "data" / "pdf-parser" / "results"), paths["results_candidates"])


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

    def test_duplicate_file_hash_finds_existing_non_failed_task(self):
        old_db_path = app.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                app.DB_PATH = os.path.join(tmpdir, "tasks.db")
                app._init_db()
                base = {
                    "task_id": "completed-task",
                    "mineru_task_id": None,
                    "filename": "original.pdf",
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
                    "file_sha256": "same-hash",
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

                duplicate = app._find_duplicate_file_hash_task("same-hash")

                self.assertIsNotNone(duplicate)
                self.assertEqual(duplicate["task_id"], "completed-task")
        finally:
            app.DB_PATH = old_db_path

    def test_duplicate_file_hash_is_scoped_by_owner_market_and_parse_config(self):
        old_db_path = app.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                app.DB_PATH = os.path.join(tmpdir, "tasks.db")
                app._init_db()
                base = {
                    "task_id": "alice-cn-auto",
                    "mineru_task_id": None,
                    "filename": "same-content.pdf",
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
                    "submit_config": {"market": "CN", "backend": "hybrid-http-client", "parse_method": "auto"},
                    "logs": [],
                    "file_sha256": "same-hash",
                    "owner_id": "alice",
                    "tenant_id": "unknown",
                    "market_scope": "CN",
                    "parse_config_hash": "hash-cn-auto",
                }
                app._save_task(base, allow_insert=True)
                other_owner = dict(base)
                other_owner.update(
                    {
                        "task_id": "bob-cn-auto",
                        "owner_id": "bob",
                        "created_at": "2026-05-01T00:02:00Z",
                    }
                )
                app._save_task(other_owner, allow_insert=True)

                alice_scope = {"owner_id": "alice", "tenant_id": "unknown", "market_scope": "CN"}
                bob_scope = {"owner_id": "bob", "tenant_id": "unknown", "market_scope": "CN"}
                eu_scope = {"owner_id": "alice", "tenant_id": "unknown", "market_scope": "EU"}

                self.assertEqual(
                    app._find_duplicate_file_hash_task(
                        "same-hash",
                        owner_scope=alice_scope,
                        parse_config_hash="hash-cn-auto",
                    )["task_id"],
                    "alice-cn-auto",
                )
                self.assertEqual(
                    app._find_duplicate_file_hash_task(
                        "same-hash",
                        owner_scope=bob_scope,
                        parse_config_hash="hash-cn-auto",
                    )["task_id"],
                    "bob-cn-auto",
                )
                self.assertIsNone(
                    app._find_duplicate_file_hash_task(
                        "same-hash",
                        owner_scope=eu_scope,
                        parse_config_hash="hash-cn-auto",
                    )
                )
                self.assertIsNone(
                    app._find_duplicate_file_hash_task(
                        "same-hash",
                        owner_scope=alice_scope,
                        parse_config_hash="hash-cn-ocr",
                    )
                )
        finally:
            app.DB_PATH = old_db_path

    def test_task_status_route_rejects_non_owner(self):
        old_db_path = app.DB_PATH
        tmpdir = tempfile.mkdtemp()
        try:
            app.DB_PATH = os.path.join(tmpdir, "tasks.db")
            app._init_db()
            task = {
                "task_id": "owned-task",
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
                "submit_config": {"market": "HK"},
                "logs": [],
                "owner_id": "alice",
                "tenant_id": "unknown",
                "market_scope": "HK",
                "parse_config_hash": "hash-hk",
            }
            app._save_task(task, allow_insert=True)
            client = app.app.test_client()

            blocked = client.get(
                "/api/status/owned-task",
                headers={"X-SIQ-User-Id": "bob", "X-SIQ-Market-Scope": "HK"},
            )
            allowed = client.get(
                "/api/status/owned-task",
                headers={"X-SIQ-User-Id": "alice", "X-SIQ-Market-Scope": "HK"},
            )

            self.assertEqual(blocked.status_code, 404)
            self.assertEqual(allowed.status_code, 200)
            self.assertEqual(allowed.get_json()["task_id"], "owned-task")
        finally:
            app.DB_PATH = old_db_path
            shutil.rmtree(tmpdir, ignore_errors=True)

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


class PdfParserResponseServiceTest(unittest.TestCase):
    def test_duplicate_payload_builder_uses_injected_markdown_checker(self):
        task = {
            "task_id": "task-dup",
            "filename": "dup.pdf",
            "market": "US",
            "status": COMPLETED,
            "stage": COMPLETED,
            "created_at": "2026-05-01T00:00:00Z",
            "uploaded_at": "2026-05-01T00:00:30Z",
            "completed_at": "2026-05-01T00:02:00Z",
            "pdf_page_count": 12,
        }

        payload = response_service.build_task_duplicate_payload(
            task,
            has_markdown_artifact=lambda _task: True,
        )

        self.assertEqual(payload["task_id"], "task-dup")
        self.assertTrue(payload["markdown_ready"])
        self.assertEqual(set(payload), {
            "task_id",
            "filename",
            "market",
            "status",
            "stage",
            "created_at",
            "uploaded_at",
            "completed_at",
            "pdf_page_count",
            "markdown_ready",
        })

    def test_recent_task_limit_clamps_invalid_and_out_of_range_values(self):
        self.assertEqual(response_service.clamp_recent_task_limit(None), 300)
        self.assertEqual(response_service.clamp_recent_task_limit("nope"), 300)
        self.assertEqual(response_service.clamp_recent_task_limit("99"), 100)
        self.assertEqual(response_service.clamp_recent_task_limit("100"), 100)
        self.assertEqual(response_service.clamp_recent_task_limit(1000), 1000)
        self.assertEqual(response_service.clamp_recent_task_limit("1001"), 1000)

    def test_recent_task_normalization_preserves_input_and_marks_missing_artifact(self):
        task = {
            "task_id": "task-normalize",
            "filename": "normalize.pdf",
            "status": COMPLETED,
            "stage": COMPLETED,
            "created_at": "2026-05-01T00:00:00Z",
            "markdown_path": "/tmp/normalize/result.md",
        }

        normalized = response_service.normalize_recent_task(
            task,
            has_markdown_artifact=lambda _task: False,
        )

        self.assertEqual(task["status"], COMPLETED)
        self.assertEqual(normalized["status"], COMPLETED_MISSING_ARTIFACT)
        self.assertEqual(normalized["stage"], COMPLETED_MISSING_ARTIFACT)
        self.assertFalse(normalized["markdown_ready"])
        self.assertNotIn("markdown_path", normalized)

    def test_recent_task_list_normalization_keeps_order_and_uses_ready_injection(self):
        tasks = [
            {
                "task_id": "task-a",
                "filename": "a.pdf",
                "status": COMPLETED,
                "stage": COMPLETED,
                "created_at": "2026-05-01T00:00:00Z",
                "markdown_path": "/tmp/a/result.md",
            },
            {
                "task_id": "task-b",
                "filename": "b.pdf",
                "status": "queued",
                "stage": "queued",
                "created_at": "2026-05-01T00:01:00Z",
                "markdown_path": "/tmp/b/result.md",
            },
        ]

        normalized = response_service.normalize_recent_tasks(
            tasks,
            has_markdown_artifact=lambda task: task["task_id"] == "task-b",
        )

        self.assertEqual([task["task_id"] for task in normalized], ["task-a", "task-b"])
        self.assertEqual(normalized[0]["status"], COMPLETED_MISSING_ARTIFACT)
        self.assertFalse(normalized[0]["markdown_ready"])
        self.assertEqual(normalized[1]["status"], "queued")
        self.assertTrue(normalized[1]["markdown_ready"])
        self.assertNotIn("markdown_path", normalized[0])
        self.assertNotIn("markdown_path", normalized[1])
        self.assertIn("markdown_path", tasks[0])
        self.assertIn("markdown_path", tasks[1])

    def test_recent_tasks_payload_wraps_normalized_list(self):
        tasks = [
            {
                "task_id": "task-a",
                "filename": "a.pdf",
                "status": COMPLETED,
                "stage": COMPLETED,
                "created_at": "2026-05-01T00:00:00Z",
            }
        ]

        payload = response_service.build_recent_tasks_payload(
            tasks,
            has_markdown_artifact=lambda _task: False,
        )

        self.assertIn("tasks", payload)
        self.assertEqual(len(payload["tasks"]), 1)
        self.assertEqual(payload["tasks"][0]["status"], COMPLETED_MISSING_ARTIFACT)
        self.assertFalse(payload["tasks"][0]["markdown_ready"])


class PdfParserDocumentFullServiceTest(unittest.TestCase):
    def test_table_relations_payload_normalizes_tables_blocks_and_relation_metadata(self):
        task = {"task_id": "task-rel", "filename": "report.pdf"}
        enhanced = {
            "tables": [
                {
                    "table_id": "table-main",
                    "table_index": 7,
                    "pdf_page_number": 1,
                    "bbox": [10, 20, 110, 80],
                    "heading": "Revenue",
                    "structure": {"expanded_rows": 3, "expanded_columns": 2},
                    "table_html": "<table><tr><td>A</td><td>B</td></tr></table>",
                }
            ]
        }
        content_list = [
            {"type": "page_number", "page_idx": 0, "text": "12"},
            {
                "type": "table",
                "page_idx": 0,
                "bbox": [10, 20, 110, 80],
                "table_body": "<table><tr><td>A</td><td>B</td></tr><tr><td>1</td><td>2</td></tr></table>",
            },
            {"type": "list", "page_idx": 0, "list_items": ["first", "second"]},
        ]
        captured = {}

        def build_table_relations(task_id, tables, *, blocks, markdown):
            captured["task_id"] = task_id
            captured["tables"] = tables
            captured["blocks"] = blocks
            captured["markdown"] = markdown
            return {
                "relations": [
                    {
                        "from_table_id": "table-main",
                        "to_table_id": "table-main",
                    }
                ]
            }

        payload = document_full_service.build_table_relations_artifact_payload(
            task,
            "# Report",
            enhanced=enhanced,
            content_list=content_list,
            build_table_relations=build_table_relations,
            now_iso=lambda: "2026-05-01T00:00:00Z",
            table_relation_ruleset_version="rules-v-test",
        )

        self.assertEqual(captured["task_id"], "task-rel")
        self.assertEqual(captured["markdown"], "# Report")
        self.assertEqual(len(captured["tables"]), 1)
        self.assertEqual(captured["tables"][0]["table_id"], "table-main")
        self.assertEqual(captured["tables"][0]["quality"], {"row_count": 3, "column_count": 2})
        self.assertEqual(captured["tables"][0]["content_table_source_id"], 1)
        self.assertEqual([block["type"] for block in captured["blocks"]], ["page_number", "table", "list"])
        self.assertEqual(captured["blocks"][1]["text"], "A B 1 2")
        self.assertEqual(captured["blocks"][2]["markdown"], "first second")
        self.assertEqual(payload["schema_version"], "document_table_relations_v1")
        self.assertEqual(payload["ruleset_version"], "rules-v-test")
        self.assertEqual(payload["task_id"], "task-rel")
        self.assertEqual(payload["filename"], "report.pdf")
        self.assertEqual(payload["generated_at"], "2026-05-01T00:00:00Z")
        self.assertEqual(payload["physical_table_count"], 1)
        self.assertEqual(payload["relations"][0]["from_table_index"], 7)
        self.assertEqual(payload["relations"][0]["from_bbox"], [10.0, 20.0, 110.0, 80.0])
        self.assertEqual(payload["relations"][0]["from_page_number"], 1)
        self.assertEqual(payload["relations"][0]["to_table_index"], 7)
        self.assertEqual(payload["relations"][0]["to_bbox"], [10.0, 20.0, 110.0, 80.0])
        self.assertEqual(payload["relations"][0]["to_page_number"], 1)

    def test_content_list_enhanced_update_adds_artifacts_without_mutating_original(self):
        original = {
            "artifacts": {"existing.json": {"exists": True}},
            "source_files": {"pdf": {"path": "/tmp/report.pdf"}},
        }
        enhanced = {"schema_version": 10}
        table_relations = {"relations": []}

        updated = document_full_service.apply_content_list_enhanced_update_to_document_full(
            original,
            task_id="task-update",
            enhanced=enhanced,
            table_relations=table_relations,
            content_list_enhanced_path="/tmp/content_list_enhanced.json",
            table_relations_path="/tmp/table_relations.json",
            complete_markdown_path="/tmp/result_complete.md",
            complete_markdown_exists=True,
        )

        self.assertIsNot(updated, original)
        self.assertNotIn("content_list_enhanced", original)
        self.assertNotIn("content_list_enhanced.json", original["artifacts"])
        self.assertEqual(updated["content_list_enhanced"], enhanced)
        self.assertEqual(updated["table_relations"], table_relations)
        self.assertEqual(updated["artifacts"]["existing.json"], {"exists": True})
        self.assertEqual(
            updated["artifacts"]["content_list_enhanced.json"],
            {
                "exists": True,
                "path": "/tmp/content_list_enhanced.json",
                "url": "/api/artifact/task-update/content_list_enhanced.json",
            },
        )
        self.assertEqual(
            updated["artifacts"]["table_relations.json"],
            {
                "exists": True,
                "path": "/tmp/table_relations.json",
                "url": "/api/artifact/task-update/table_relations.json",
            },
        )
        self.assertEqual(
            updated["source_files"]["complete_markdown"],
            {
                "path": "/tmp/result_complete.md",
                "exists": True,
                "url": "/api/artifact/task-update/result_complete.md",
            },
        )


class AppWrapperCompatibilityTest(unittest.TestCase):
    def test_task_duplicate_payload_wrapper_uses_response_service(self):
        task = {
            "task_id": "task-wrapper",
            "filename": "wrapper.pdf",
            "status": COMPLETED,
            "stage": COMPLETED,
            "created_at": "2026-05-01T00:00:00Z",
            "uploaded_at": "2026-05-01T00:01:00Z",
            "completed_at": "2026-05-01T00:02:00Z",
            "pdf_page_count": 4,
        }

        with patch.object(app, "_has_markdown_artifact", return_value=False):
            payload = app._task_duplicate_payload(task)

        self.assertFalse(payload["markdown_ready"])
        self.assertEqual(payload["task_id"], "task-wrapper")

    def test_recent_task_limit_wrapper_clamps_env(self):
        with patch.dict(os.environ, {"PDF_RECENT_TASK_LIMIT": "42"}):
            self.assertEqual(app._recent_task_list_limit(), 100)
        with patch.dict(os.environ, {"PDF_RECENT_TASK_LIMIT": "1001"}):
            self.assertEqual(app._recent_task_list_limit(), 1000)
        with patch.dict(os.environ, {"PDF_RECENT_TASK_LIMIT": "invalid"}):
            self.assertEqual(app._recent_task_list_limit(), 300)

    def test_recent_tasks_payload_wrapper_uses_response_service(self):
        tasks = [
            {
                "task_id": "task-wrapper",
                "filename": "wrapper.pdf",
                "status": COMPLETED,
                "stage": COMPLETED,
                "created_at": "2026-05-01T00:00:00Z",
            }
        ]

        with patch.object(app, "_list_recent_tasks", return_value=tasks), patch.object(
            app, "_has_markdown_artifact", return_value=False
        ), patch.object(app, "_recent_task_list_limit", return_value=1):
            payload = app._recent_tasks_payload()

        self.assertEqual(len(payload["tasks"]), 1)
        self.assertEqual(payload["tasks"][0]["task_id"], "task-wrapper")
        self.assertEqual(payload["tasks"][0]["status"], COMPLETED_MISSING_ARTIFACT)
        self.assertFalse(payload["tasks"][0]["markdown_ready"])

    def test_table_relations_artifact_wrapper_uses_document_full_service(self):
        task = {"task_id": "task-wrapper", "filename": "wrapper.pdf"}
        enhanced = {"tables": []}
        content_list = []

        def load_json_artifact(_task, name):
            return {
                "content_list_enhanced.json": enhanced,
                "content_list.json": content_list,
            }[name]

        with patch.object(app, "_load_json_artifact", side_effect=load_json_artifact), patch.object(
            app.document_full_service,
            "build_table_relations_artifact_payload",
            return_value={"ok": True},
        ) as build_payload:
            payload = app._build_table_relations_artifact(task, "# Report")

        self.assertEqual(payload, {"ok": True})
        build_payload.assert_called_once()
        args, kwargs = build_payload.call_args
        self.assertEqual(args, (task, "# Report"))
        self.assertIs(kwargs["enhanced"], enhanced)
        self.assertIs(kwargs["content_list"], content_list)
        self.assertIs(kwargs["build_table_relations"], app.build_physical_table_relations)
        self.assertIs(kwargs["now_iso"], app._now_iso)
        self.assertEqual(kwargs["table_relation_ruleset_version"], app.TABLE_RELATION_RULESET_VERSION)


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

    def test_status_response_wrapper_injects_runtime_values(self):
        task = {
            "task_id": "task-wrapper",
            "filename": "wrapper.pdf",
            "status": "processing",
            "stage": "processing",
            "logs": [{"message": "one"}, {"message": "two"}],
        }
        calls = {}

        def build_payload(current_task, **kwargs):
            calls["task"] = current_task
            calls["kwargs"] = kwargs
            return {"ok": True}

        with (
            patch.object(app, "_task_elapsed_seconds", return_value=30),
            patch.object(app, "_calc_page_progress", return_value={"total": 5, "processed": 2, "remaining": 3}),
            patch.object(app, "_calc_progress_percent", return_value=40.0),
            patch.object(app, "_has_markdown_artifact", return_value=True),
            patch.object(app, "_local_queue_position", return_value=7),
            patch.object(response_service, "build_status_response_payload", side_effect=build_payload),
        ):
            payload = app._build_status_response(task, logs_slice=[{"message": "two"}])

        self.assertEqual(payload, {"ok": True})
        self.assertIs(calls["task"], task)
        self.assertEqual(
            calls["kwargs"],
            {
                "elapsed_seconds": 30,
                "page_progress": {"total": 5, "processed": 2, "remaining": 3},
                "progress_percent": 40.0,
                "markdown_ready": True,
                "local_queue_position": 7,
                "logs_slice": [{"message": "two"}],
            },
        )

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

    def test_upload_endpoint_rejects_same_content_different_filenames(self):
        if not hasattr(app.app, "test_client"):
            self.skipTest("Flask test client is unavailable in the lightweight import stub")
        old_db_path = app.DB_PATH
        old_upload_folder = app.UPLOAD_FOLDER
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                app.DB_PATH = os.path.join(tmpdir, "tasks.db")
                app.UPLOAD_FOLDER = os.path.join(tmpdir, "uploads")
                os.makedirs(app.UPLOAD_FOLDER, exist_ok=True)
                app._init_db()
                pdf_bytes = b"%PDF-1.4\nsame document"

                with patch.object(app, "initialize_app"), patch.object(
                    app, "_request_has_valid_token", return_value=True
                ), patch.object(app, "_cleanup_old_data"), patch.object(
                    app, "_wake_queue_worker"
                ), patch.object(
                    app, "_looks_like_pdf", return_value=True
                ), patch.object(
                    app, "_get_pdf_page_count", return_value=1
                ):
                    client = app.app.test_client()
                    first = client.post(
                        "/api/upload",
                        data={"files": [(io.BytesIO(pdf_bytes), "first.pdf")]},
                        content_type="multipart/form-data",
                    )
                    second = client.post(
                        "/api/upload",
                        data={"files": [(io.BytesIO(pdf_bytes), "second.pdf")]},
                        content_type="multipart/form-data",
                    )

                self.assertEqual(first.status_code, 200)
                self.assertEqual(second.status_code, 409)
                payload = second.get_json()
                self.assertEqual(payload["error"], "duplicate_file_content")
                self.assertEqual(payload["filename"], "second.pdf")
                self.assertEqual(payload["existingTask"]["filename"], "first.pdf")
        finally:
            app.DB_PATH = old_db_path
            app.UPLOAD_FOLDER = old_upload_folder


if __name__ == "__main__":
    unittest.main()
