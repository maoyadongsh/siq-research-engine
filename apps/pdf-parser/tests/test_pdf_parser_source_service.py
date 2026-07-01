import json
import os

import pytest

import pdf_parser_source_service as source


def test_load_and_save_corrections_defaults_and_sanitizes(tmp_path):
    task = {"task_id": "task-1", "filename": "report.pdf"}
    default = source.load_corrections(task, results_folder=str(tmp_path))

    assert default["schema_version"] == 1
    assert default["tables"] == {}

    record = source.save_table_correction(
        task,
        {
            "table_index": 3,
            "line": 88,
            "pdf_page_number": 9,
            "bbox": [1, 2, 3, 4],
            "suspect_reasons": ["single_row"],
        },
        {
            "review_status": "not-valid",
            "table_markdown": "x" * 20,
            "note": "n" * 20,
        },
        results_folder=str(tmp_path),
        now_iso=lambda: "2026-06-30T12:00:00Z",
    )

    assert record["review_status"] == "needs_fix"
    assert record["updated_at"] == "2026-06-30T12:00:00Z"
    path = source.corrections_path(task, results_folder=str(tmp_path))
    with open(path, "r", encoding="utf-8") as infile:
        saved = json.load(infile)
    assert saved["tables"]["3"]["markdown_line"] == 88


def test_page_content_payload_coerces_page_and_uses_injected_loader():
    task = {"task_id": "task-1"}
    content_list = [
        {"type": "text", "text": "hello", "page_idx": 0, "bbox": [1, 2, 3, 4]},
        {"type": "page_number", "text": "1", "page_idx": 0},
    ]
    report = {"table_index": [{"table_index": 3}]}

    payload = source.page_content_payload(
        task,
        "2",
        report=report,
        load_json_artifact=lambda _task, name: content_list if name == "content_list.json" else None,
        page_content_payload_from_content_list=lambda content, page, report=None, focus_table=None: {
            "content": content,
            "page": page,
            "report": report,
            "focus_table": focus_table,
        },
        focus_table=5,
    )

    assert payload["content"] == content_list
    assert payload["page"] == 2
    assert payload["report"] == report
    assert payload["focus_table"] == 5


@pytest.mark.parametrize("page_number", ["0", 0, -1])
def test_page_content_payload_rejects_invalid_page_before_loading(page_number):
    def fail_loader(_task, _name):
        raise AssertionError("loader should not be called")

    with pytest.raises(ValueError, match="Invalid page number"):
        source.page_content_payload(
            {"task_id": "task-1"},
            page_number,
            load_json_artifact=fail_loader,
            page_content_payload_from_content_list=lambda *_args, **_kwargs: {},
        )


def test_ensure_pdf_page_image_returns_existing_cache(tmp_path):
    task = {"task_id": "task-1", "upload_path": ""}
    image_path = source.pdf_page_image_path(task, 2, results_folder=str(tmp_path))
    with open(image_path, "wb") as outfile:
        outfile.write(b"png")

    assert source.ensure_pdf_page_image(task, 2, results_folder=str(tmp_path)) == image_path


def test_ensure_pdf_page_image_requires_original_pdf(tmp_path):
    task = {"task_id": "task-1", "upload_path": os.path.join(str(tmp_path), "missing.pdf")}

    with pytest.raises(FileNotFoundError):
        source.ensure_pdf_page_image(task, 1, results_folder=str(tmp_path))
