import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

import app


def _task(task_id="quality-financial-route"):
    return {
        "task_id": task_id,
        "mineru_task_id": "mineru-quality-financial",
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


def _route_client(tmp_path, monkeypatch, task_id="quality-financial-route"):
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


@pytest.mark.parametrize("endpoint", ["quality", "financial"])
def test_quality_financial_routes_missing_task_returns_404(tmp_path, monkeypatch, endpoint):
    client, _task = _route_client(tmp_path, monkeypatch)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("missing task must not fetch markdown")

    monkeypatch.setattr(app, "_fetch_and_cache_result", fail_if_called)

    response = client.get(f"/api/{endpoint}/missing-route-task")

    assert response.status_code == 404
    assert response.get_json() == {"error": "Task not found"}


@pytest.mark.parametrize(
    ("endpoint", "ensure_name"),
    [
        ("quality", "_ensure_quality_report"),
        ("financial", "_ensure_financial_artifacts"),
    ],
)
def test_quality_financial_routes_upstream_error_short_circuits(
    tmp_path,
    monkeypatch,
    endpoint,
    ensure_name,
):
    client, task = _route_client(tmp_path, monkeypatch, task_id=f"{endpoint}-upstream-error")

    def fake_fetch(task_arg):
        assert task_arg["task_id"] == task["task_id"]
        return {"_error": True, "detail": "MinerU result unavailable"}

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("upstream errors must not build derived artifacts")

    monkeypatch.setattr(app, "_read_markdown", lambda task_arg: None)
    monkeypatch.setattr(app, "_fetch_and_cache_result", fake_fetch)
    monkeypatch.setattr(app, ensure_name, fail_if_called)

    response = client.get(f"/api/{endpoint}/{task['task_id']}")

    assert response.status_code == 502
    assert response.get_json() == {"error": "MinerU result unavailable"}


@pytest.mark.parametrize("endpoint", ["quality", "financial"])
def test_quality_financial_routes_return_400_when_markdown_is_unavailable(
    tmp_path,
    monkeypatch,
    endpoint,
):
    client, task = _route_client(tmp_path, monkeypatch, task_id=f"{endpoint}-no-markdown")

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("missing markdown must not build derived artifacts")

    monkeypatch.setattr(app, "_read_markdown", lambda task_arg: None)
    monkeypatch.setattr(app, "_fetch_and_cache_result", lambda task_arg: None)
    monkeypatch.setattr(app, "_ensure_quality_report", fail_if_called)
    monkeypatch.setattr(app, "_ensure_financial_artifacts", fail_if_called)

    response = client.get(f"/api/{endpoint}/{task['task_id']}")

    assert response.status_code == 400
    assert response.get_json() == {"error": "No markdown available yet"}


def test_quality_route_returns_quality_report_from_local_markdown(tmp_path, monkeypatch):
    client, task = _route_client(tmp_path, monkeypatch, task_id="quality-success")
    markdown = "# Parsed quality report"
    quality_report = {"schema_version": app.QUALITY_SCHEMA_VERSION, "warnings": []}
    calls = []

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("local markdown route should not fetch upstream result")

    def fake_ensure_quality(task_arg, markdown_arg):
        calls.append((task_arg["task_id"], markdown_arg))
        return quality_report

    monkeypatch.setattr(app, "_read_markdown", lambda task_arg: markdown)
    monkeypatch.setattr(app, "_fetch_and_cache_result", fail_if_called)
    monkeypatch.setattr(app, "_ensure_quality_report", fake_ensure_quality)

    response = client.get(f"/api/quality/{task['task_id']}")

    assert response.status_code == 200
    assert response.get_json() == {"quality": quality_report}
    assert calls == [("quality-success", markdown)]


def test_financial_route_returns_financial_artifacts_from_local_markdown(tmp_path, monkeypatch):
    client, task = _route_client(tmp_path, monkeypatch, task_id="financial-success")
    markdown = "# Parsed financial report"
    financial_data = {"summary": {"statement_count": 1}}
    financial_checks = {"overall_status": "ok", "summary": {"fail": 0}}
    calls = []

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("local markdown route should not fetch upstream result")

    def fake_ensure_financial(task_arg, markdown_arg):
        calls.append((task_arg["task_id"], markdown_arg))
        return financial_data, financial_checks

    monkeypatch.setattr(app, "_read_markdown", lambda task_arg: markdown)
    monkeypatch.setattr(app, "_fetch_and_cache_result", fail_if_called)
    monkeypatch.setattr(app, "_ensure_financial_artifacts", fake_ensure_financial)

    response = client.get(f"/api/financial/{task['task_id']}")

    assert response.status_code == 200
    assert response.get_json() == {
        "financial_data": financial_data,
        "financial_checks": financial_checks,
    }
    assert calls == [("financial-success", markdown)]
