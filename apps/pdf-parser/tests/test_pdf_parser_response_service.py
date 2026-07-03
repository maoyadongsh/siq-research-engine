from task_store import COMPLETED, COMPLETED_MISSING_ARTIFACT

import pdf_parser_response_service as response


def test_build_task_duplicate_payload_returns_public_fields_and_markdown_ready():
    task = {
        "task_id": "task-1",
        "filename": "report.pdf",
        "market": "cn",
        "status": COMPLETED,
        "stage": COMPLETED,
        "created_at": "2026-01-01T00:00:00Z",
        "uploaded_at": "2026-01-01T00:00:01Z",
        "completed_at": "2026-01-01T00:01:00Z",
        "pdf_page_count": 42,
        "upload_path": "/tmp/private.pdf",
        "markdown_path": "/tmp/result.md",
    }

    payload = response.build_task_duplicate_payload(
        task,
        has_markdown_artifact=lambda current_task: current_task["task_id"] == "task-1",
    )

    assert payload == {
        "task_id": "task-1",
        "filename": "report.pdf",
        "market": "cn",
        "status": COMPLETED,
        "stage": COMPLETED,
        "created_at": "2026-01-01T00:00:00Z",
        "uploaded_at": "2026-01-01T00:00:01Z",
        "completed_at": "2026-01-01T00:01:00Z",
        "pdf_page_count": 42,
        "markdown_ready": True,
    }


def test_build_task_duplicate_payload_returns_none_without_task():
    assert response.build_task_duplicate_payload(None, has_markdown_artifact=lambda _task: True) is None


def test_build_status_response_payload_uses_injected_runtime_values():
    task = {
        "task_id": "task-status",
        "filename": "status.pdf",
        "status": "processing",
        "stage": "processing",
        "queue_position": 3,
        "file_size": 2048,
        "pdf_page_count": 12,
        "error": None,
        "logs": [{"message": "old"}, {"message": "new"}],
    }

    payload = response.build_status_response_payload(
        task,
        elapsed_seconds=45,
        page_progress={"total": 12, "processed": 5, "remaining": 7},
        progress_percent=41.7,
        markdown_ready=False,
        local_queue_position=2,
        logs_slice=[{"message": "new"}],
    )

    assert payload == {
        "task_id": "task-status",
        "status": "processing",
        "stage": "processing",
        "queue_position": 3,
        "local_queue_position": 2,
        "filename": "status.pdf",
        "file_size": 2048,
        "pdf_page_count": 12,
        "error": None,
        "elapsed_seconds": 45,
        "total_pages": 12,
        "processed_pages": 5,
        "progress_percent": 41.7,
        "markdown_ready": False,
        "log_count": 2,
        "logs": [{"message": "new"}],
    }


def test_build_status_response_payload_defaults_logs_and_completes_progress():
    task = {
        "task_id": "task-done",
        "filename": "done.pdf",
        "status": COMPLETED,
        "stage": COMPLETED,
        "pdf_page_count": 10,
        "logs": [{"message": "done"}],
    }
    page_progress = {"total": 10, "processed": 4, "remaining": 6}

    payload = response.build_status_response_payload(
        task,
        elapsed_seconds=90,
        page_progress=page_progress,
        progress_percent=40.0,
        markdown_ready=True,
        local_queue_position=None,
    )

    assert payload["processed_pages"] == 10
    assert payload["progress_percent"] == 100.0
    assert payload["markdown_ready"] is True
    assert payload["logs"] == []
    assert page_progress == {"total": 10, "processed": 4, "remaining": 6}


def test_build_status_response_payload_handles_missing_progress():
    payload = response.build_status_response_payload(
        {
            "task_id": "task-no-progress",
            "filename": "no-progress.pdf",
            "status": "queued",
            "stage": "queued",
            "pdf_page_count": None,
        },
        elapsed_seconds=None,
        page_progress=None,
        progress_percent=None,
        markdown_ready=False,
        local_queue_position=4,
    )

    assert payload["total_pages"] is None
    assert payload["processed_pages"] is None
    assert payload["progress_percent"] is None
    assert payload["local_queue_position"] == 4
    assert payload["logs"] == []


def test_result_quality_and_financial_payloads_keep_route_shapes():
    artifacts = {"markdown": {"exists": True, "path": "result.md"}}
    quality_report = {"warnings": [], "schema_version": "quality_v1"}
    financial_data = {"summary": {"statement_count": 1}}
    financial_checks = {"overall_status": "ok"}

    result_payload = response.build_result_response_payload("# report", artifacts)
    quality_payload = response.build_quality_response_payload(quality_report)
    financial_payload = response.build_financial_response_payload(financial_data, financial_checks)

    assert result_payload == {
        "markdown": "# report",
        "artifacts": {"markdown": {"exists": True, "path": "result.md"}},
    }
    assert quality_payload == {"quality": {"warnings": [], "schema_version": "quality_v1"}}
    assert financial_payload == {
        "financial_data": {"summary": {"statement_count": 1}},
        "financial_checks": {"overall_status": "ok"},
    }

    artifacts["extra"] = True
    quality_report["warnings"].append("late")
    financial_checks["overall_status"] = "changed"

    assert "extra" not in result_payload["artifacts"]
    assert quality_payload["quality"]["warnings"] == ["late"]
    assert financial_payload["financial_checks"]["overall_status"] == "ok"


def test_clamp_recent_task_limit_uses_default_and_bounds():
    assert response.clamp_recent_task_limit(None) == 300
    assert response.clamp_recent_task_limit("not-a-number") == 300
    assert response.clamp_recent_task_limit("42") == 100
    assert response.clamp_recent_task_limit("250") == 250
    assert response.clamp_recent_task_limit("2000") == 1000


def test_normalize_recent_task_marks_missing_artifact_and_drops_markdown_path():
    task = {
        "task_id": "task-missing",
        "filename": "missing.pdf",
        "status": COMPLETED,
        "stage": COMPLETED,
        "markdown_path": "/tmp/missing.md",
    }

    normalized = response.normalize_recent_task(
        task,
        has_markdown_artifact=lambda _task: False,
    )

    assert normalized["status"] == COMPLETED_MISSING_ARTIFACT
    assert normalized["stage"] == COMPLETED_MISSING_ARTIFACT
    assert normalized["markdown_ready"] is False
    assert "markdown_path" not in normalized
    assert task["status"] == COMPLETED


def test_build_recent_tasks_payload_normalizes_each_task():
    tasks = [
        {"task_id": "ready", "status": COMPLETED, "stage": COMPLETED, "markdown_path": "/tmp/ready.md"},
        {"task_id": "missing", "status": COMPLETED, "stage": COMPLETED, "markdown_path": "/tmp/missing.md"},
    ]

    payload = response.build_recent_tasks_payload(
        tasks,
        has_markdown_artifact=lambda task: task["task_id"] == "ready",
    )

    assert payload["tasks"][0]["status"] == COMPLETED
    assert payload["tasks"][0]["markdown_ready"] is True
    assert payload["tasks"][1]["status"] == COMPLETED_MISSING_ARTIFACT
    assert payload["tasks"][1]["stage"] == COMPLETED_MISSING_ARTIFACT
    assert payload["tasks"][1]["markdown_ready"] is False
    assert all("markdown_path" not in item for item in payload["tasks"])
