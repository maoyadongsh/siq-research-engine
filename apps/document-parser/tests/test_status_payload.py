from status_payload import build_task_status_payload


def test_build_task_status_payload_adds_logs_count_and_artifact_readiness():
    task = {"task_id": "task-1", "status": "completed", "filename": "demo.pdf"}
    logs = [{"message": "done", "level": "success"}]

    payload = build_task_status_payload(task, logs, 7)

    assert payload == {
        "task_id": "task-1",
        "status": "completed",
        "filename": "demo.pdf",
        "logs": logs,
        "log_count": 7,
        "artifacts_ready": True,
    }
    assert "logs" not in task


def test_build_task_status_payload_marks_non_completed_tasks_not_ready_and_copies_logs():
    logs = [{"message": "running", "level": "info"}]

    payload = build_task_status_payload({"task_id": "task-2", "status": "running"}, logs, 1)
    logs.append({"message": "late", "level": "warn"})

    assert payload["artifacts_ready"] is False
    assert payload["logs"] == [{"message": "running", "level": "info"}]
