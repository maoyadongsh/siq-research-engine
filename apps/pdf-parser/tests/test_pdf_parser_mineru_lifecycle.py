from __future__ import annotations

import os

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
