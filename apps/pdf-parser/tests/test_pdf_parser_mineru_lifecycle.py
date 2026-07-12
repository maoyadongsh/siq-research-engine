from __future__ import annotations

import io
import os
from types import SimpleNamespace

import app
from task_store import COMPLETED, COMPLETED_MISSING_ARTIFACT, FAILED


def _base_task(task_id="task-1", *, upload_path=None, status="queued", **updates):
    task = {
        "task_id": task_id,
        "mineru_task_id": None,
        "filename": f"{task_id}.pdf",
        "file_size": 10,
        "pdf_page_count": 1,
        "status": status,
        "stage": status,
        "created_at": "2026-05-01T00:00:00Z",
        "uploaded_at": "2026-05-01T00:00:00Z",
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
        "submit_config": {},
        "logs": [],
    }
    task.update(updates)
    return task


def _use_temp_app_paths(tmp_path, monkeypatch):
    db_path = str(tmp_path / "tasks.db")
    results_dir = str(tmp_path / "results")
    uploads_dir = str(tmp_path / "uploads")
    os.makedirs(results_dir, exist_ok=True)
    os.makedirs(uploads_dir, exist_ok=True)
    monkeypatch.setattr(app, "DB_PATH", db_path)
    monkeypatch.setattr(app, "RESULTS_FOLDER", results_dir)
    monkeypatch.setattr(app, "UPLOAD_FOLDER", uploads_dir)
    monkeypatch.setattr(app, "_now_iso", lambda: "2026-05-01T00:01:00Z")
    app._init_db()
    return db_path, results_dir, uploads_dir


def test_submit_persists_submitting_before_upstream_post_and_success_state(tmp_path, monkeypatch):
    _use_temp_app_paths(tmp_path, monkeypatch)
    upload_path = tmp_path / "uploads" / "task-1.pdf"
    upload_path.write_bytes(b"%PDF-1.4\n")
    task = _base_task(upload_path=str(upload_path), submit_config={"table_enable": True})
    app._save_task(task, allow_insert=True)

    def fake_stream_multipart_post(*_args, **kwargs):
        persisted = app._get_task("task-1")
        assert persisted["status"] == "submitting"
        assert persisted["stage"] == "submitting"
        assert persisted["mineru_task_id"] is None
        assert kwargs["fields"]["return_md"] == "true"
        assert kwargs["fields"]["table_enable"] == "true"
        return {"task_id": "mineru-123456789"}

    monkeypatch.setattr(app, "_stream_multipart_post", fake_stream_multipart_post)

    assert app._submit_task_to_mineru(task) is True

    persisted = app._get_task("task-1")
    assert persisted["mineru_task_id"] == "mineru-123456789"
    assert persisted["status"] == "pending"
    assert persisted["stage"] == "submitted"
    assert persisted["submitted_at"] == "2026-05-01T00:01:00Z"
    assert persisted["error"] is None


def test_parser_queue_capacity_response_is_structured_and_removes_spooled_uploads(tmp_path, monkeypatch):
    _use_temp_app_paths(tmp_path, monkeypatch)
    upload_path = tmp_path / "uploads" / "blocked.pdf"
    upload_path.write_bytes(b"%PDF-1.4\n")
    prepared = [
        {
            "task_id": "blocked",
            "filename": "blocked.pdf",
            "upload_path": str(upload_path),
            "file_size": upload_path.stat().st_size,
            "pdf_page_count": 1,
            "submit_config": {"market": "CN"},
            "file_sha256": "a" * 64,
            "owner_id": "user-1",
            "tenant_id": "tenant-1",
            "market_scope": "CN",
            "parse_config_hash": "config-1",
        }
    ]
    capacity = {
        "admitted": False,
        "exceeded": ["owner_tasks"],
        "global_active_tasks": 3,
        "global_active_bytes": 100,
        "owner_active_tasks": 2,
        "owner_active_bytes": 80,
        "requested_tasks": 1,
        "requested_bytes": upload_path.stat().st_size,
        "global_task_limit": 32,
        "owner_task_limit": 2,
        "global_bytes_limit": 1_000,
        "owner_bytes_limit": 100,
    }

    with app.app.test_request_context("/api/upload"):
        response = app._queue_capacity_response(prepared, capacity)

    assert response.status_code == 503
    assert response.headers["Retry-After"] == str(app.PARSER_QUEUE_RETRY_AFTER_SECONDS)
    payload = response.get_json()
    assert payload["error"] == "parser_queue_capacity_exceeded"
    assert payload["scope"] == ["owner_tasks"]
    assert payload["capacity"]["owner_active_tasks"] == 2
    assert not upload_path.exists()


def test_new_upload_path_retries_collision_without_touching_existing_file(tmp_path, monkeypatch):
    _use_temp_app_paths(tmp_path, monkeypatch)
    collision_path = tmp_path / "uploads" / "collision.pdf"
    collision_path.write_bytes(b"existing-bytes")
    values = iter([SimpleNamespace(hex="collision"), SimpleNamespace(hex="allocated")])
    monkeypatch.setattr(app.uuid, "uuid4", lambda: next(values))

    upload_path = app._new_upload_path()

    assert upload_path == str(tmp_path / "uploads" / "allocated.pdf")
    assert collision_path.read_bytes() == b"existing-bytes"
    assert os.path.exists(upload_path)
    assert os.path.getsize(upload_path) == 0


def test_upload_task_id_conflict_preserves_existing_task_and_pdf(tmp_path, monkeypatch):
    _use_temp_app_paths(tmp_path, monkeypatch)
    original_path = tmp_path / "uploads" / "original.pdf"
    original_bytes = b"%PDF-1.4\noriginal-owner-content"
    original_path.write_bytes(original_bytes)
    existing = _base_task(
        "client-task-id",
        upload_path=str(original_path),
        status="processing",
        owner_id="owner-a",
        tenant_id="tenant-a",
        market_scope="HK",
        file_size=len(original_bytes),
        file_sha256="a" * 64,
    )
    app._save_task(existing, allow_insert=True)
    before = app._get_task("client-task-id")
    monkeypatch.setattr(app, "initialize_app", lambda start_worker=True: None)
    monkeypatch.setattr(app, "_cleanup_old_data", lambda: None)
    monkeypatch.setattr(app, "_wake_queue_worker", lambda: None)
    monkeypatch.setattr(app, "_looks_like_pdf", lambda _path: True)
    monkeypatch.setattr(app, "_get_pdf_page_count", lambda _path: 1)

    response = app.app.test_client().post(
        "/api/upload",
        data={
            "task_id": "client-task-id",
            "files": [(io.BytesIO(b"%PDF-1.4\nreplacement"), "replacement.pdf")],
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "parser_task_id_conflict",
        "message": "任务 ID 已存在，请使用新的任务 ID。",
        "task_ids": ["client-task-id"],
    }
    assert app._get_task("client-task-id") == before
    assert original_path.read_bytes() == original_bytes
    assert list((tmp_path / "uploads").iterdir()) == [original_path]


def test_reparse_capacity_rejection_removes_copy_and_keeps_source(tmp_path, monkeypatch):
    _use_temp_app_paths(tmp_path, monkeypatch)
    source_path = tmp_path / "uploads" / "source.pdf"
    source_bytes = b"%PDF-1.4\nsource-for-reparse"
    source_path.write_bytes(source_bytes)
    source = _base_task(
        "source-task",
        upload_path=str(source_path),
        status="completed",
        completed_at="2026-05-01T00:01:00Z",
        file_size=len(source_bytes),
        file_sha256="b" * 64,
    )
    app._save_task(source, allow_insert=True)
    monkeypatch.setattr(app, "initialize_app", lambda start_worker=True: None)
    monkeypatch.setattr(app, "_wake_queue_worker", lambda: None)
    monkeypatch.setattr(app, "PARSER_QUEUE_GLOBAL_BYTES_LIMIT", len(source_bytes) - 1)
    monkeypatch.setattr(app, "PARSER_QUEUE_OWNER_BYTES_LIMIT", len(source_bytes) - 1)

    response = app.app.test_client().post("/api/reparse/source-task")

    assert response.status_code == 503
    assert response.get_json()["error"] == "parser_queue_capacity_exceeded"
    assert app._get_task("source-task")["status"] == "completed"
    assert source_path.read_bytes() == source_bytes
    assert list((tmp_path / "uploads").iterdir()) == [source_path]
    assert app.task_repository.capacity_snapshot(app.DB_PATH)["active_tasks"] == 0


def test_reparse_success_uses_atomic_admission_and_accounts_bytes(tmp_path, monkeypatch):
    _use_temp_app_paths(tmp_path, monkeypatch)
    source_path = tmp_path / "uploads" / "source-success.pdf"
    source_bytes = b"%PDF-1.4\nsource-success"
    source_path.write_bytes(source_bytes)
    source = _base_task(
        "source-success",
        upload_path=str(source_path),
        status="completed",
        completed_at="2026-05-01T00:01:00Z",
        file_size=len(source_bytes),
        file_sha256="c" * 64,
    )
    app._save_task(source, allow_insert=True)
    monkeypatch.setattr(app, "initialize_app", lambda start_worker=True: None)
    monkeypatch.setattr(app, "_wake_queue_worker", lambda: None)

    response = app.app.test_client().post("/api/reparse/source-success")

    assert response.status_code == 200
    created = app._get_task(response.get_json()["task_id"])
    assert created["status"] == "queued"
    assert created["file_size"] == len(source_bytes)
    assert created["file_sha256"] == app._sha256_file(created["upload_path"])
    assert created["file_sha256"] != source["file_sha256"]
    assert created["upload_path"] != str(source_path)
    with open(created["upload_path"], "rb") as created_file:
        assert created_file.read() == source_bytes
    capacity = app.task_repository.capacity_snapshot(app.DB_PATH)
    assert capacity["active_tasks"] == 1
    assert capacity["active_bytes"] == len(source_bytes)


def test_submit_missing_upload_marks_task_failed(tmp_path, monkeypatch):
    _use_temp_app_paths(tmp_path, monkeypatch)
    task = _base_task(upload_path=str(tmp_path / "missing.pdf"))
    app._save_task(task, allow_insert=True)

    assert app._submit_task_to_mineru(task) is False

    persisted = app._get_task("task-1")
    assert persisted["status"] == FAILED
    assert persisted["stage"] == FAILED
    assert persisted["completed_at"] == "2026-05-01T00:01:00Z"
    assert "本地上传文件不存在" in persisted["error"]


def test_refresh_upstream_404_marks_task_failed(tmp_path, monkeypatch):
    _use_temp_app_paths(tmp_path, monkeypatch)
    task = _base_task(
        status="pending",
        stage="submitted",
        mineru_task_id="mineru-missing",
        submitted_at="2026-05-01T00:00:30Z",
    )
    app._save_task(task, allow_insert=True)
    monkeypatch.setattr(app, "_json_request", lambda *_args, **_kwargs: {"_error": True, "status": 404})

    refreshed = app._refresh_task_from_upstream(task)

    assert refreshed["status"] == FAILED
    assert refreshed["stage"] == FAILED
    assert refreshed["completed_at"] == "2026-05-01T00:01:00Z"
    assert "上游任务不存在" in refreshed["error"]
    assert app._get_task("task-1")["status"] == FAILED


def test_refresh_completed_status_fetches_artifacts_before_final_persist(tmp_path, monkeypatch):
    _use_temp_app_paths(tmp_path, monkeypatch)
    task = _base_task(
        status="pending",
        stage="submitted",
        mineru_task_id="mineru-completed",
        submitted_at="2026-05-01T00:00:30Z",
    )
    app._save_task(task, allow_insert=True)
    calls = []

    monkeypatch.setattr(app, "_json_request", lambda *_args, **_kwargs: {"status": COMPLETED})
    monkeypatch.setattr(app, "_has_markdown_artifact", lambda _task: False)

    def fake_fetch_and_cache_result(fetch_task, force=False):
        calls.append(("fetch", fetch_task["status"], fetch_task["stage"], force))
        assert fetch_task["status"] == COMPLETED
        assert fetch_task["stage"] == COMPLETED
        return "# parsed\n"

    def fake_persist_task(persist_task, allow_insert=False):
        calls.append(("persist", persist_task["status"], persist_task["stage"], allow_insert))
        app._save_task(persist_task, allow_insert=allow_insert)

    monkeypatch.setattr(app, "_fetch_and_cache_result", fake_fetch_and_cache_result)
    monkeypatch.setattr(app, "_persist_task", fake_persist_task)

    refreshed = app._refresh_task_from_upstream(task)

    assert refreshed["status"] == COMPLETED
    assert calls[0][0] == "fetch"
    assert calls[-1] == ("persist", COMPLETED, COMPLETED, False)


def test_fetch_completed_task_without_markdown_marks_completed_missing_artifact(tmp_path, monkeypatch):
    _use_temp_app_paths(tmp_path, monkeypatch)
    task = _base_task(status=COMPLETED, stage=COMPLETED, completed_at="2026-05-01T00:00:30Z")
    app._save_task(task, allow_insert=True)

    result = app._fetch_and_cache_result(task)

    persisted = app._get_task("task-1")
    assert result["_error"] is True
    assert persisted["status"] == COMPLETED_MISSING_ARTIFACT
    assert persisted["stage"] == COMPLETED_MISSING_ARTIFACT
    assert persisted["completed_at"] == "2026-05-01T00:00:30Z"
    assert "Markdown" in persisted["error"]


def test_fetch_result_preserves_non_404_upstream_error_detail(tmp_path, monkeypatch):
    _use_temp_app_paths(tmp_path, monkeypatch)
    task = _base_task(
        status=COMPLETED,
        stage=COMPLETED,
        mineru_task_id="mineru-error",
        completed_at="2026-05-01T00:00:30Z",
    )
    app._save_task(task, allow_insert=True)
    monkeypatch.setattr(
        app,
        "_json_request",
        lambda *_args, **_kwargs: {"_error": True, "status": 502, "detail": "MinerU gateway unavailable"},
    )

    result = app._fetch_and_cache_result(task, force=True)

    persisted = app._get_task("task-1")
    assert result == {"_error": True, "detail": "MinerU gateway unavailable"}
    assert persisted["status"] == COMPLETED_MISSING_ARTIFACT
    assert persisted["stage"] == COMPLETED_MISSING_ARTIFACT
    assert persisted["error"] == "MinerU gateway unavailable"


def test_fetch_result_without_results_marks_completed_missing_artifact(tmp_path, monkeypatch):
    _use_temp_app_paths(tmp_path, monkeypatch)
    task = _base_task(
        status=COMPLETED,
        stage=COMPLETED,
        mineru_task_id="mineru-empty",
        completed_at="2026-05-01T00:00:30Z",
    )
    app._save_task(task, allow_insert=True)
    monkeypatch.setattr(app, "_json_request", lambda *_args, **_kwargs: {"status": COMPLETED})

    result = app._fetch_and_cache_result(task, force=True)

    persisted = app._get_task("task-1")
    assert result["_error"] is True
    assert result["detail"] == "任务已完成，但 MinerU 结果中没有可用的 Markdown 内容。"
    assert persisted["status"] == COMPLETED_MISSING_ARTIFACT
    assert persisted["error"] == result["detail"]


def test_fetch_result_without_md_content_marks_completed_missing_artifact(tmp_path, monkeypatch):
    _use_temp_app_paths(tmp_path, monkeypatch)
    task = _base_task(
        status=COMPLETED,
        stage=COMPLETED,
        mineru_task_id="mineru-no-md",
        completed_at="2026-05-01T00:00:30Z",
    )
    app._save_task(task, allow_insert=True)
    monkeypatch.setattr(
        app,
        "_json_request",
        lambda *_args, **_kwargs: {"results": {"task-1.pdf": {"content_list": []}}},
    )

    result = app._fetch_and_cache_result(task, force=True)

    persisted = app._get_task("task-1")
    assert result["_error"] is True
    assert result["detail"] == "任务已完成，但 MinerU 结果中没有可用的 Markdown 内容。"
    assert persisted["status"] == COMPLETED_MISSING_ARTIFACT
    assert persisted["error"] == result["detail"]


def test_fetch_force_returns_local_markdown_when_no_mineru_task_id(tmp_path, monkeypatch):
    _use_temp_app_paths(tmp_path, monkeypatch)
    task = _base_task(status=COMPLETED, stage=COMPLETED, completed_at="2026-05-01T00:00:30Z")
    app._write_markdown(task, "# local\n")
    app._save_task(task, allow_insert=True)

    def fail_json_request(*_args, **_kwargs):
        raise AssertionError("local-only force fetch should not call MinerU")

    monkeypatch.setattr(app, "_json_request", fail_json_request)

    assert app._fetch_and_cache_result(task, force=True) == "# local\n"


def test_fetch_force_with_local_markdown_refreshes_from_mineru_and_logs_quality_first(tmp_path, monkeypatch):
    _use_temp_app_paths(tmp_path, monkeypatch)
    task = _base_task(
        status=COMPLETED,
        stage=COMPLETED,
        mineru_task_id="mineru-refresh",
        completed_at="2026-05-01T00:00:30Z",
    )
    app._write_markdown(task, "# stale\n")
    app._save_task(task, allow_insert=True)
    calls = []

    monkeypatch.setattr(
        app,
        "_json_request",
        lambda *_args, **_kwargs: {
            "results": {
                "task-1.pdf": {
                    "md_content": "# fresh\n",
                    "content_list": [],
                }
            }
        },
    )
    monkeypatch.setattr(app, "_inject_pdf_page_markers", lambda markdown, *_args, **_kwargs: markdown)
    monkeypatch.setattr(app, "_backfill_sparse_markdown_pages", lambda markdown, *_args, **_kwargs: (markdown, []))

    def fake_save_mineru_artifacts(*_args, **_kwargs):
        calls.append("save_artifacts")
        return {"table_count": 2, "single_row_table_count": 1}

    def fake_append_log(_task, message, level="info"):
        calls.append(("log", level, message))

    monkeypatch.setattr(app, "_save_mineru_artifacts", fake_save_mineru_artifacts)
    monkeypatch.setattr(app, "_append_log", fake_append_log)

    assert app._fetch_and_cache_result(task, force=True) == "# fresh\n"

    persisted = app._get_task("task-1")
    assert persisted["status"] == COMPLETED
    assert app._read_markdown(persisted) == "# fresh\n"
    assert calls[0] == "save_artifacts"
    assert calls[1][0:2] == ("log", "info")
    assert calls[1][2].startswith("质量报告已生成")
    assert calls[2][0:2] == ("log", "success")
    assert calls[2][2].startswith("Markdown 结果已获取")
