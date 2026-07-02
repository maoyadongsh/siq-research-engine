import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

import app


def _task(task_id="source-route"):
    return {
        "task_id": task_id,
        "mineru_task_id": "mineru-source-route",
        "filename": "report.pdf",
        "file_size": 1,
        "pdf_page_count": 8,
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


def _source_client(tmp_path, monkeypatch, task_id="source-route"):
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


def test_source_table_route_returns_page_context_and_artifact_metadata(tmp_path, monkeypatch):
    client, task = _source_client(tmp_path, monkeypatch, task_id="source-table")
    markdown = "# Report\n\n<table><tr><td>Revenue</td></tr></table>\n"
    table_item = {
        "table_index": 3,
        "line": 12,
        "pdf_page_number": 5,
        "pdf_page_index": 4,
        "printed_page_number": "F-5",
        "bbox": [1, 2, 30, 40],
    }
    quality_report = {"schema_version": app.QUALITY_SCHEMA_VERSION, "table_index": [table_item]}
    calls = []

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("local markdown source route should not fetch upstream result")

    def fake_ensure_quality(task_arg, markdown_arg):
        calls.append(("quality", task_arg["task_id"], markdown_arg))
        return quality_report

    def fake_page_content(task_arg, page_number, *, report=None, focus_table=None):
        calls.append(("page_content", task_arg["task_id"], page_number, report, focus_table))
        return {"page_number": page_number, "focus_table": focus_table, "block_count": 1}

    def fake_page_bbox_extent(task_arg, page_index):
        calls.append(("bbox_extent", task_arg["task_id"], page_index))
        return {"width": 600, "height": 800}

    monkeypatch.setattr(app, "_read_markdown", lambda task_arg: markdown)
    monkeypatch.setattr(app, "_fetch_and_cache_result", fail_if_called)
    monkeypatch.setattr(app, "_ensure_quality_report", fake_ensure_quality)
    monkeypatch.setattr(app, "_table_html_by_index", lambda markdown_arg, table_index: "<table>target</table>")
    monkeypatch.setattr(app, "_markdown_excerpt", lambda markdown_arg, line, radius=12: f"line-{line}-r{radius}")
    monkeypatch.setattr(app, "_artifact_status", lambda task_arg: {"quality_report": {"exists": True}})
    monkeypatch.setattr(app, "_load_corrections", lambda task_arg: {"tables": {"3": {"status": "reviewed"}}})
    monkeypatch.setattr(app, "_page_content_payload", fake_page_content)
    monkeypatch.setattr(app, "_page_bbox_extent", fake_page_bbox_extent)

    response = client.get(f"/api/source/{task['task_id']}/table/3")

    assert response.status_code == 200
    assert response.get_json() == {
        "task_id": "source-table",
        "filename": "report.pdf",
        "table": table_item,
        "table_html": "<table>target</table>",
        "markdown_excerpt": "line-12-r14",
        "artifacts": {"quality_report": {"exists": True}},
        "correction": {"status": "reviewed"},
        "page_content": {"page_number": 5, "focus_table": 3, "block_count": 1},
        "pdf_page_image": {
            "url": "/api/pdf_page/source-table/5",
            "page_number": 5,
            "pdf_page_number": 5,
            "printed_page_number": "F-5",
            "page_count": 8,
            "bbox": [1, 2, 30, 40],
            "bbox_extent": {"width": 600, "height": 800},
        },
    }
    assert len(calls) == 3
    assert ("quality", "source-table", markdown) in calls
    assert ("page_content", "source-table", 5, quality_report, 3) in calls
    assert ("bbox_extent", "source-table", 4) in calls


def test_source_page_route_passes_focus_table_to_page_payload(tmp_path, monkeypatch):
    client, task = _source_client(tmp_path, monkeypatch, task_id="source-page")
    markdown = "# Report\n\ncontent"
    quality_report = {"schema_version": app.QUALITY_SCHEMA_VERSION, "table_index": []}
    calls = []

    def fake_page_content(task_arg, page_number, *, report=None, focus_table=None):
        calls.append((task_arg["task_id"], page_number, report, focus_table))
        return {"page_number": page_number, "focus_table": focus_table, "blocks": []}

    monkeypatch.setattr(app, "_read_markdown", lambda task_arg: markdown)
    monkeypatch.setattr(app, "_ensure_quality_report", lambda task_arg, markdown_arg: quality_report)
    monkeypatch.setattr(app, "_page_content_payload", fake_page_content)

    response = client.get(f"/api/source/{task['task_id']}/page/4?focus_table=11")

    assert response.status_code == 200
    assert response.get_json() == {"page_number": 4, "focus_table": 11, "blocks": []}
    assert calls == [("source-page", 4, quality_report, 11)]


def test_source_route_missing_task_returns_404_without_fetch(tmp_path, monkeypatch):
    client, _task = _source_client(tmp_path, monkeypatch)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("missing task must not fetch source artifacts")

    monkeypatch.setattr(app, "_fetch_and_cache_result", fail_if_called)

    response = client.get("/api/source/missing-source/table/1")

    assert response.status_code == 404
    assert response.get_json() == {"error": "Task not found"}


def test_source_route_upstream_error_short_circuits_source_payload(tmp_path, monkeypatch):
    client, task = _source_client(tmp_path, monkeypatch, task_id="source-upstream-error")
    calls = []

    def fake_fetch(task_arg):
        calls.append(("fetch", task_arg["task_id"]))
        return {"_error": True, "detail": "MinerU source unavailable"}

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("upstream errors must not build source payload")

    monkeypatch.setattr(app, "_read_markdown", lambda task_arg: None)
    monkeypatch.setattr(app, "_fetch_and_cache_result", fake_fetch)
    monkeypatch.setattr(app, "_ensure_quality_report", fail_if_called)
    monkeypatch.setattr(app, "_page_content_payload", fail_if_called)

    response = client.get(f"/api/source/{task['task_id']}/page/2")

    assert response.status_code == 502
    assert response.get_json() == {"error": "MinerU source unavailable"}
    assert calls == [("fetch", "source-upstream-error")]
