from __future__ import annotations

import os
from datetime import datetime

import app
import pdf_parser_task_lifecycle_service as lifecycle
import pdf_parser_task_repository as repository


def _base_task(task_id, *, status="queued", stage=None, created_at="2026-05-01T00:00:00Z", **updates):
    task = {
        "task_id": task_id,
        "mineru_task_id": None,
        "filename": f"{task_id}.pdf",
        "file_size": 1,
        "pdf_page_count": 1,
        "status": status,
        "stage": stage or status,
        "created_at": created_at,
        "uploaded_at": created_at,
        "submitted_at": None,
        "started_at": None,
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
    task.update(updates)
    return task


def _save(db_path, *tasks):
    for task in tasks:
        repository.save_task(db_path, task, allow_insert=True)


def test_claim_next_queued_task_claims_oldest_eligible_task(tmp_path):
    db_path = str(tmp_path / "tasks.db")
    repository.init_db(db_path)
    _save(
        db_path,
        _base_task("newer", created_at="2026-05-01T00:02:00Z", submit_config={"market": "HK"}),
        _base_task("older", created_at="2026-05-01T00:01:00Z", submit_config={"market": "US"}),
    )

    claimed = lifecycle.claim_next_queued_task(
        db_path,
        normalize_task=lambda task: task.update({"normalized": True}) or task,
    )

    assert claimed["task_id"] == "older"
    assert claimed["status"] == "submitting"
    assert claimed["stage"] == "submitting"
    assert claimed["normalized"] is True
    assert repository.get_task(db_path, "older")["status"] == "submitting"
    assert repository.get_task(db_path, "newer")["status"] == "queued"


def test_claim_next_queued_task_skips_cancelled_and_upstream_tasks(tmp_path):
    db_path = str(tmp_path / "tasks.db")
    repository.init_db(db_path)
    _save(
        db_path,
        _base_task("cancelled-old", created_at="2026-05-01T00:00:00Z", cancelled=True),
        _base_task("already-submitted", created_at="2026-05-01T00:01:00Z", mineru_task_id="mineru-1"),
        _base_task("eligible", created_at="2026-05-01T00:02:00Z"),
    )

    claimed = lifecycle.claim_next_queued_task(db_path)

    assert claimed["task_id"] == "eligible"
    assert repository.get_task(db_path, "cancelled-old")["status"] == "queued"
    assert repository.get_task(db_path, "already-submitted")["status"] == "queued"


def test_claim_next_queued_task_returns_none_without_eligible_tasks(tmp_path):
    db_path = str(tmp_path / "tasks.db")
    repository.init_db(db_path)
    _save(
        db_path,
        _base_task("submitting", status="submitting"),
        _base_task("cancelled", cancelled=True),
        _base_task("upstream", mineru_task_id="mineru-1"),
    )

    assert lifecycle.claim_next_queued_task(db_path) is None


def test_recover_stale_submitting_tasks_only_recovers_local_stale_claims(tmp_path):
    db_path = str(tmp_path / "tasks.db")
    repository.init_db(db_path)
    _save(
        db_path,
        _base_task("stale-uploaded", status="submitting", uploaded_at="2026-05-01T00:00:00Z"),
        _base_task("stale-created", status="submitting", uploaded_at=None, created_at="2026-05-01T00:00:00Z"),
        _base_task("fresh", status="submitting", uploaded_at="2026-05-01T00:29:30Z"),
        _base_task("with-upstream", status="submitting", uploaded_at="2026-05-01T00:00:00Z", mineru_task_id="mineru-1"),
        _base_task("cancelled", status="submitting", uploaded_at="2026-05-01T00:00:00Z", cancelled=True),
        _base_task("queued", status="queued", uploaded_at="2026-05-01T00:00:00Z"),
    )

    recovered = lifecycle.recover_stale_submitting_tasks(
        db_path,
        stale_seconds=1800,
        now_factory=lambda: datetime(2026, 5, 1, 0, 30, 1),
    )

    assert recovered == 2
    assert repository.get_task(db_path, "stale-uploaded")["status"] == "queued"
    assert repository.get_task(db_path, "stale-uploaded")["stage"] == "queued"
    assert repository.get_task(db_path, "stale-created")["status"] == "queued"
    assert repository.get_task(db_path, "fresh")["status"] == "submitting"
    assert repository.get_task(db_path, "with-upstream")["status"] == "submitting"
    assert repository.get_task(db_path, "cancelled")["status"] == "submitting"
    assert repository.get_task(db_path, "queued")["status"] == "queued"


def test_recover_stale_submitting_tasks_uses_strict_cutoff(tmp_path):
    db_path = str(tmp_path / "tasks.db")
    repository.init_db(db_path)
    _save(
        db_path,
        _base_task("at-cutoff", status="submitting", uploaded_at="2026-05-01T00:00:00Z"),
        _base_task("before-cutoff", status="submitting", uploaded_at="2026-04-30T23:59:59Z"),
    )

    recovered = lifecycle.recover_stale_submitting_tasks(
        db_path,
        stale_seconds=1800,
        now_factory=lambda: datetime(2026, 5, 1, 0, 30, 0),
    )

    assert recovered == 1
    assert repository.get_task(db_path, "at-cutoff")["status"] == "submitting"
    assert repository.get_task(db_path, "before-cutoff")["status"] == "queued"


def test_calc_page_progress_handles_missing_elapsed_and_clamps_processed_pages():
    assert lifecycle.calc_page_progress({"pdf_page_count": 5}, None, page_estimate_seconds=10) is None
    assert lifecycle.calc_page_progress({"pdf_page_count": 0}, 30, page_estimate_seconds=10) is None

    progress = lifecycle.calc_page_progress(
        {"pdf_page_count": 5},
        80,
        page_estimate_seconds=10,
    )

    assert progress == {"total": 5, "processed": 5, "remaining": 0}


def test_calc_progress_percent_rounds_and_clamps_to_100():
    assert lifecycle.calc_progress_percent({"pdf_page_count": 5}, 0, page_estimate_seconds=10) is None
    assert lifecycle.calc_progress_percent({"pdf_page_count": None}, 30, page_estimate_seconds=10) is None

    assert lifecycle.calc_progress_percent({"pdf_page_count": 4}, 15, page_estimate_seconds=10) == 37.5
    assert lifecycle.calc_progress_percent({"pdf_page_count": 4}, 80, page_estimate_seconds=10) == 100.0


def test_status_log_since_index_normalizes_query_value():
    assert lifecycle.status_log_since_index(" 7 ") == 7
    assert lifecycle.status_log_since_index("-3") == 0
    assert lifecycle.status_log_since_index("not-a-number") == 0
    assert lifecycle.status_log_since_index(None) == 0


def test_should_refresh_task_from_upstream_skips_cancelled_tasks():
    assert lifecycle.should_refresh_task_from_upstream({"cancelled": True}) is False
    assert lifecycle.should_refresh_task_from_upstream({"cancelled": False}) is True
    assert lifecycle.should_refresh_task_from_upstream({}) is True


def test_build_cancel_task_update_preserves_completed_at_and_log_variants():
    now = "2026-05-01T00:00:00Z"

    upstream = lifecycle.build_cancel_task_update(
        {"completed_at": "done", "mineru_task_id": "mineru-1"},
        upstream_cancelled=True,
        now_iso=now,
    )
    upstream_failed = lifecycle.build_cancel_task_update(
        {"mineru_task_id": "mineru-1"},
        upstream_cancelled=False,
        now_iso=now,
    )
    local_only = lifecycle.build_cancel_task_update(
        {},
        upstream_cancelled=False,
        now_iso=now,
    )

    assert upstream["patch"] == {
        "cancelled": True,
        "status": "cancelled",
        "stage": "cancelled",
        "completed_at": "done",
    }
    assert upstream["log"] == {"message": "任务已取消，已通知 MinerU 停止处理。", "level": "warn"}
    assert upstream_failed["patch"]["completed_at"] == now
    assert upstream_failed["log"] == {"message": "已停止本地查看；MinerU 后端可能仍在处理。", "level": "warn"}
    assert local_only["log"] == {"message": "任务已从本地排队队列中移除。", "level": "warn"}


def test_build_status_failure_update_warns_then_fails_at_tolerance():
    now = "2026-05-01T00:00:00Z"

    waiting = lifecycle.build_status_failure_update(
        {"status": "processing", "stage": "processing", "consecutive_status_failures": 1},
        error_detail="timeout",
        tolerance=3,
        now_iso=now,
    )
    failed = lifecycle.build_status_failure_update(
        {"status": "processing", "stage": "processing", "consecutive_status_failures": 2, "completed_at": "done"},
        error_detail="timeout",
        tolerance=3,
        now_iso=now,
    )

    assert waiting["patch"] == {
        "consecutive_status_failures": 2,
        "error": "任务状态查询失败: timeout",
    }
    assert waiting["log"] == {"message": "状态查询超时，第 2/3 次，继续等待...", "level": "warn"}
    assert failed["patch"] == {
        "consecutive_status_failures": 3,
        "error": "任务状态查询失败: timeout",
        "status": "failed",
        "stage": "failed",
        "completed_at": "done",
    }
    assert failed["log"] == {"message": "任务状态查询失败: timeout", "level": "error"}


def test_app_claim_and_recover_wrappers_keep_compatible_db_behavior(tmp_path, monkeypatch):
    db_path = str(tmp_path / "tasks.db")
    old_db_path = app.DB_PATH
    old_stale_seconds = app.STALE_SUBMITTING_SECONDS
    try:
        app.DB_PATH = db_path
        app.STALE_SUBMITTING_SECONDS = 1800
        monkeypatch.setattr(app, "_utc_now", lambda: datetime(2026, 5, 1, 0, 30, 1))
        app._init_db()
        app._save_task(_base_task("stale", status="submitting", uploaded_at="2026-05-01T00:00:00Z"), allow_insert=True)
        app._save_task(_base_task("queued", created_at="2026-05-01T00:01:00Z"), allow_insert=True)

        assert app._recover_stale_submitting_tasks() == 1
        claimed = app._claim_next_queued_task()

        assert claimed["task_id"] == "stale"
        assert claimed["status"] == "submitting"
        assert app._get_task("queued")["status"] == "queued"
    finally:
        app.DB_PATH = old_db_path
        app.STALE_SUBMITTING_SECONDS = old_stale_seconds
        if os.path.exists(db_path):
            os.remove(db_path)
