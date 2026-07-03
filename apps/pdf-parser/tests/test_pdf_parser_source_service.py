import json
import os

import pytest

from pdf_source_viewer import page_content_payload_from_content_list
from pdf_source_viewer import page_bbox_extent_from_content_list
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


@pytest.mark.parametrize("content_list", [None, {"content": None}, "{not-json"])
def test_page_content_payload_wrapper_handles_missing_non_list_and_invalid_json_content(content_list):
    payload = source.page_content_payload(
        {"task_id": "task-1"},
        3,
        load_json_artifact=lambda _task, name: content_list if name == "content_list.json" else None,
        page_content_payload_from_content_list=page_content_payload_from_content_list,
    )

    assert payload == {
        "page_number": 3,
        "pdf_page_number": 3,
        "printed_page_number": None,
        "page_index": 2,
        "block_count": 0,
        "table_count": 0,
        "page_tables": [],
        "blocks": [],
    }


def test_page_content_payload_wrapper_keeps_report_page_tables_when_content_list_is_missing():
    report = {
        "table_index": [
            {
                "table_index": 8,
                "content_table_source_id": 1,
                "line": 80,
                "pdf_page_number": 2,
                "heading": "Report fallback",
            }
        ]
    }

    payload = source.page_content_payload(
        {"task_id": "task-1"},
        2,
        report=report,
        load_json_artifact=lambda _task, name: None if name == "content_list.json" else [],
        page_content_payload_from_content_list=page_content_payload_from_content_list,
    )

    assert payload["blocks"] == []
    assert payload["page_tables"] == [
        {
            "table_index": 8,
            "source_table_index": 1,
            "line": 80,
            "heading": "Report fallback",
            "printed_page_number": None,
            "matched_financial_names": [],
        }
    ]


@pytest.mark.parametrize("content_list", [None, "{not-json"])
def test_page_bbox_extent_wrapper_uses_content_list_loader_and_handles_bad_payload(content_list):
    requested_names = []

    def load_json_artifact(_task, name):
        requested_names.append(name)
        return content_list

    payload = source.page_bbox_extent(
        {"task_id": "task-1"},
        0,
        load_json_artifact=load_json_artifact,
        page_bbox_extent_from_content_list=page_bbox_extent_from_content_list,
    )

    assert payload is None
    assert requested_names == ["content_list.json"]


def test_find_source_table_matches_numeric_and_string_indexes():
    table_3 = {"table_index": "3", "heading": "target"}
    report = {
        "table_index": [
            {"table_index": 1},
            "not-a-table",
            table_3,
        ]
    }

    assert source.find_source_table(report, 3) is table_3
    assert source.find_source_table(report, 9) is None
    assert source.find_source_table({}, 3) is None


def test_find_source_table_ignores_malformed_report_entries():
    table_3 = {"table_index": 3, "heading": "target"}
    report = {
        "table_index": [
            {"table_index": "not-a-number"},
            {"table_index": None},
            table_3,
        ]
    }

    assert source.find_source_table(report, 3) is table_3
    assert source.find_source_table({"table_index": {"3": table_3}}, 3) is None
    assert source.find_source_table("not-a-report", 3) is None


def test_source_table_pdf_page_image_payload_handles_page_and_empty_bbox():
    payload = source.source_table_pdf_page_image_payload(
        task_id="source-table",
        task={"pdf_page_count": 8},
        table_item={
            "pdf_page_number": 5,
            "printed_page_number": "F-5",
        },
        bbox_extent={"width": 600, "height": 800},
    )

    assert payload == {
        "url": "/api/pdf_page/source-table/5",
        "page_number": 5,
        "pdf_page_number": 5,
        "printed_page_number": "F-5",
        "page_count": 8,
        "bbox": [],
        "bbox_extent": {"width": 600, "height": 800},
    }


def test_source_table_pdf_page_image_payload_handles_missing_page():
    payload = source.source_table_pdf_page_image_payload(
        task_id="source-table",
        task={"pdf_page_count": 8},
        table_item={"bbox": [1, 2, 3, 4]},
        bbox_extent=None,
    )

    assert payload == {
        "url": "",
        "page_number": None,
        "pdf_page_number": None,
        "printed_page_number": None,
        "page_count": 8,
        "bbox": [1, 2, 3, 4],
        "bbox_extent": None,
    }


def test_source_table_pdf_page_image_payload_coerces_page_and_bbox_shape():
    payload = source.source_table_pdf_page_image_payload(
        task_id="source-table",
        task={"pdf_page_count": 8},
        table_item={
            "pdf_page_number": "5",
            "printed_page_number": "F-5",
            "bbox": (1, 2, 30, 40),
        },
        bbox_extent=None,
    )

    assert payload == {
        "url": "/api/pdf_page/source-table/5",
        "page_number": 5,
        "pdf_page_number": 5,
        "printed_page_number": "F-5",
        "page_count": 8,
        "bbox": [1, 2, 30, 40],
        "bbox_extent": None,
    }

    bad_payload = source.source_table_pdf_page_image_payload(
        task_id="source-table",
        task={"pdf_page_count": 8},
        table_item={"pdf_page_number": "bad", "bbox": "bad-bbox"},
        bbox_extent=None,
    )
    assert bad_payload["url"] == ""
    assert bad_payload["page_number"] is None
    assert bad_payload["bbox"] == []


def test_source_table_payload_keeps_route_shape_without_loading_artifacts():
    table_item = {
        "table_index": 3,
        "line": 12,
        "pdf_page_number": 5,
        "printed_page_number": "F-5",
        "bbox": [1, 2, 30, 40],
    }

    payload = source.source_table_payload(
        task_id="source-table",
        task={"filename": "report.pdf", "pdf_page_count": 8},
        table_item=table_item,
        table_html="<table>target</table>",
        markdown_excerpt="line-12-r14",
        artifacts={"quality_report": {"exists": True}},
        correction=None,
        page_content={"page_number": 5, "focus_table": 3},
        bbox_extent={"width": 600, "height": 800},
    )

    assert payload == {
        "task_id": "source-table",
        "filename": "report.pdf",
        "table": table_item,
        "table_html": "<table>target</table>",
        "markdown_excerpt": "line-12-r14",
        "artifacts": {"quality_report": {"exists": True}},
        "correction": None,
        "page_content": {"page_number": 5, "focus_table": 3},
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


def test_source_table_payload_falls_back_when_page_content_is_malformed():
    table_item = {
        "table_index": 3,
        "line": 12,
        "pdf_page_number": "5",
        "pdf_page_index": "4",
        "printed_page_number": "F-5",
        "bbox": "bad-bbox",
    }

    payload = source.source_table_payload(
        task_id="source-table",
        task={"filename": "report.pdf", "pdf_page_count": 8},
        table_item=table_item,
        table_html="<table>target</table>",
        markdown_excerpt="line-12-r14",
        artifacts=["not", "a", "dict"],
        correction=None,
        page_content=None,
        bbox_extent=None,
    )

    assert payload["artifacts"] == {}
    assert payload["page_content"] == {
        "page_number": 5,
        "pdf_page_number": 5,
        "printed_page_number": "F-5",
        "page_index": 4,
        "block_count": 0,
        "table_count": 0,
        "page_tables": [],
        "blocks": [],
    }
    assert payload["pdf_page_image"]["bbox"] == []


def test_ensure_pdf_page_image_returns_existing_cache(tmp_path):
    task = {"task_id": "task-1", "upload_path": ""}
    image_path = source.pdf_page_image_path(task, 2, results_folder=str(tmp_path))
    with open(image_path, "wb") as outfile:
        outfile.write(b"png")

    assert source.ensure_pdf_page_image(task, 2, results_folder=str(tmp_path)) == image_path


def test_ensure_pdf_page_image_rerenders_empty_cache(tmp_path, monkeypatch):
    upload_path = tmp_path / "source.pdf"
    upload_path.write_bytes(b"%PDF-1.4")
    task = {"task_id": "task-empty-cache", "upload_path": str(upload_path)}
    image_path = source.pdf_page_image_path(task, 4, results_folder=str(tmp_path))
    open(image_path, "wb").close()
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        prefix = args[-1]
        with open(f"{prefix}-4.png", "wb") as outfile:
            outfile.write(b"rerendered")

    monkeypatch.setattr(source.subprocess, "run", fake_run)

    assert source.ensure_pdf_page_image(task, 4, results_folder=str(tmp_path)) == image_path
    assert len(calls) == 1
    with open(image_path, "rb") as infile:
        assert infile.read() == b"rerendered"


def test_ensure_pdf_page_image_renders_and_moves_generated_page(tmp_path, monkeypatch):
    upload_path = tmp_path / "source.pdf"
    upload_path.write_bytes(b"%PDF-1.4")
    task = {"task_id": "task-render", "upload_path": str(upload_path)}
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        prefix = args[-1]
        with open(f"{prefix}-3.png", "wb") as outfile:
            outfile.write(b"rendered")

    monkeypatch.setattr(source.subprocess, "run", fake_run)

    image_path = source.ensure_pdf_page_image(
        task,
        3,
        results_folder=str(tmp_path),
    )

    assert image_path == source.pdf_page_image_path(task, 3, results_folder=str(tmp_path))
    assert os.path.exists(image_path)
    with open(image_path, "rb") as infile:
        assert infile.read() == b"rendered"
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[:8] == ["pdftoppm", "-f", "3", "-l", "3", "-png", "-r", "144"]
    assert args[8] == str(upload_path)
    assert args[9].endswith(os.path.join("pdf_pages", "page_0003"))
    assert kwargs["check"] is True
    assert kwargs["stdout"] is source.subprocess.DEVNULL
    assert kwargs["stderr"] is source.subprocess.PIPE
    assert kwargs["timeout"] == 60


@pytest.mark.parametrize("page_number", ["not-a-number", "0", 0, -1])
def test_ensure_pdf_page_image_rejects_invalid_page_before_touching_files(tmp_path, monkeypatch, page_number):
    def fail_run(*_args, **_kwargs):
        raise AssertionError("pdftoppm should not be called")

    monkeypatch.setattr(source.subprocess, "run", fail_run)
    task = {"task_id": "task-invalid-page", "upload_path": os.path.join(str(tmp_path), "missing.pdf")}

    with pytest.raises(ValueError):
        source.ensure_pdf_page_image(task, page_number, results_folder=str(tmp_path))

    assert not os.path.exists(os.path.join(str(tmp_path), "task-invalid-page"))


def test_ensure_pdf_page_image_requires_original_pdf(tmp_path):
    task = {"task_id": "task-1", "upload_path": os.path.join(str(tmp_path), "missing.pdf")}

    with pytest.raises(FileNotFoundError):
        source.ensure_pdf_page_image(task, 1, results_folder=str(tmp_path))
