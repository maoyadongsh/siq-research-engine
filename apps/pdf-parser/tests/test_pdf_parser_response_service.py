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
