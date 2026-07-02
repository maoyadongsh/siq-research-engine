import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

import app


def _task(task_id="result-route"):
    return {
        "task_id": task_id,
        "mineru_task_id": "mineru-result-route",
        "filename": "report.pdf",
        "file_size": 1,
        "pdf_page_count": 1,
        "status": "completed",
        "stage": "completed",
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


def _result_client(tmp_path, monkeypatch, task_id="result-route"):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    monkeypatch.setattr(app, "RESULTS_FOLDER", str(results_dir))
    monkeypatch.setattr(app, "DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setattr(app, "initialize_app", lambda start_worker=True: None)
    app.app.config["TESTING"] = True
    app._init_db()
    task = _task(task_id)
    app._save_task(task, allow_insert=True)
    return app.app.test_client(), task


def test_result_route_missing_task_returns_404_without_fetch(tmp_path, monkeypatch):
    client, _task = _result_client(tmp_path, monkeypatch)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("missing task must not fetch result artifacts")

    monkeypatch.setattr(app, "_fetch_and_cache_result", fail_if_called)

    response = client.get("/api/result/missing-result-task")

    assert response.status_code == 404
    assert response.get_json() == {"error": "Task not found"}


def test_result_route_upstream_error_returns_502_without_artifact_generation(tmp_path, monkeypatch):
    client, task = _result_client(tmp_path, monkeypatch, task_id="upstream-error")
    calls = []

    def fake_fetch(task_arg):
        calls.append(("fetch", task_arg["task_id"]))
        return {"_error": True, "detail": "MinerU result unavailable"}

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("upstream fetch errors must short-circuit artifact generation")

    monkeypatch.setattr(app, "_fetch_and_cache_result", fake_fetch)
    monkeypatch.setattr(app, "_ensure_quality_report", fail_if_called)
    monkeypatch.setattr(app, "_ensure_document_full_artifact", fail_if_called)
    monkeypatch.setattr(app, "_artifact_status", fail_if_called)

    response = client.get(f"/api/result/{task['task_id']}")

    assert response.status_code == 502
    assert response.get_json() == {"error": "MinerU result unavailable"}
    assert calls == [("fetch", "upstream-error")]


def test_result_route_success_builds_quality_and_document_full_before_artifact_status(
    tmp_path,
    monkeypatch,
):
    client, task = _result_client(tmp_path, monkeypatch, task_id="success-result")
    markdown = "# Parsed report\n\n| item | value |\n| --- | ---: |\n| revenue | 1 |\n"
    quality_report = {"schema_version": app.QUALITY_SCHEMA_VERSION, "warnings": []}
    calls = []

    def fake_fetch(task_arg):
        calls.append(("fetch", task_arg["task_id"]))
        return markdown

    def fake_ensure_quality(task_arg, markdown_arg):
        calls.append(("quality", task_arg["task_id"], markdown_arg))
        return quality_report

    def fake_ensure_document_full(task_arg, markdown_arg, *, report=None):
        calls.append(("document_full", task_arg["task_id"], markdown_arg, report))
        return str(tmp_path / task_arg["task_id"] / "document_full.json")

    def fake_artifact_status(task_arg):
        calls.append(("artifacts", task_arg["task_id"]))
        return {
            "markdown": {"exists": True, "path": "result.md"},
            "document_full": {"exists": True, "path": "document_full.json"},
        }

    monkeypatch.setattr(app, "_fetch_and_cache_result", fake_fetch)
    monkeypatch.setattr(app, "_ensure_quality_report", fake_ensure_quality)
    monkeypatch.setattr(app, "_ensure_document_full_artifact", fake_ensure_document_full)
    monkeypatch.setattr(app, "_artifact_status", fake_artifact_status)

    response = client.get(f"/api/result/{task['task_id']}")

    assert response.status_code == 200
    assert response.get_json() == {
        "markdown": markdown,
        "artifacts": {
            "markdown": {"exists": True, "path": "result.md"},
            "document_full": {"exists": True, "path": "document_full.json"},
        },
    }
    assert calls == [
        ("fetch", "success-result"),
        ("quality", "success-result", markdown),
        ("document_full", "success-result", markdown, quality_report),
        ("artifacts", "success-result"),
    ]
