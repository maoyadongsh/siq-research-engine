from batch_download_payload import (
    MAX_BATCH_DOWNLOAD_TASKS,
    build_batch_download_manifest,
    requested_batch_download_task_ids,
)


def test_requested_batch_download_task_ids_accepts_snake_case_and_dedupes():
    payload = {"task_ids": [" first ", "second", "first", "", None, 123]}

    assert requested_batch_download_task_ids(payload) == ["first", "second", "123"]


def test_requested_batch_download_task_ids_accepts_camel_case_fallback():
    payload = {"task_ids": [], "taskIds": ["from-camel"]}

    assert requested_batch_download_task_ids(payload) == ["from-camel"]


def test_requested_batch_download_task_ids_rejects_non_list_payload():
    assert requested_batch_download_task_ids({"task_ids": "task-a"}) is None


def test_build_batch_download_manifest_copies_lists_and_counts_included_items():
    included = [{"task_id": "task-a", "filename": "a.pdf"}]
    missing = ["missing-task"]

    manifest = build_batch_download_manifest(
        batch_id="batch-1",
        requested_task_ids=["task-a", "missing-task"],
        included=included,
        missing=missing,
    )
    included.append({"task_id": "late", "filename": "late.pdf"})
    missing.append("late-missing")

    assert manifest == {
        "schema_version": "document_parse_batch_download_v1",
        "batch_id": "batch-1",
        "requested_task_ids": ["task-a", "missing-task"],
        "included": [{"task_id": "task-a", "filename": "a.pdf"}],
        "missing": ["missing-task"],
        "task_count": 1,
    }
    assert MAX_BATCH_DOWNLOAD_TASKS == 50
