import base64
import json
import os

import pdf_parser_artifact_service as artifacts


def test_classify_open_artifact_images_download_and_listing_skip_sanitize(tmp_path):
    calls = []

    def sanitize(value):
        calls.append(value)
        return os.path.basename(value)

    result_dir = str(tmp_path / "task-raw")

    download = artifacts.classify_open_artifact_name(
        "task-raw",
        "images/download",
        result_dir,
        sanitize_filename=sanitize,
    )
    listing = artifacts.classify_open_artifact_name(
        "task-raw",
        "images",
        result_dir,
        sanitize_filename=sanitize,
    )

    assert download == {
        "kind": "images_download",
        "artifact": "images",
        "images_dir": os.path.join(result_dir, "images"),
        "download_name": "task-raw_images.zip",
    }
    assert listing == {
        "kind": "images_index",
        "artifact": "images",
        "images_dir": os.path.join(result_dir, "images"),
    }
    assert calls == []


def test_classify_open_artifact_image_file_uses_sanitized_name_and_mimetype(tmp_path):
    calls = []

    def sanitize(value):
        calls.append(value)
        return os.path.basename(value)

    result_dir = str(tmp_path / "task-images")

    descriptor = artifacts.classify_open_artifact_name(
        "task-images",
        "images/../Chart.PNG",
        result_dir,
        sanitize_filename=sanitize,
    )

    assert calls == ["../Chart.PNG"]
    assert descriptor == {
        "kind": "image_file",
        "artifact": "images",
        "image_name": "Chart.PNG",
        "path": os.path.join(result_dir, "images", "Chart.PNG"),
        "mimetype": "image/png",
    }


def test_classify_open_artifact_allowed_file_uses_sanitized_allowlist_name(tmp_path):
    calls = []

    def sanitize(value):
        calls.append(value)
        return os.path.basename(value)

    result_dir = str(tmp_path / "task-artifact")

    descriptor = artifacts.classify_open_artifact_name(
        "task-artifact",
        "../quality_report.json",
        result_dir,
        sanitize_filename=sanitize,
    )

    assert calls == ["../quality_report.json"]
    assert descriptor == {
        "kind": "artifact_file",
        "artifact_name": "quality_report.json",
        "path": os.path.join(result_dir, "quality_report.json"),
        "mimetype": "application/json; charset=utf-8",
        "binary": False,
    }

    assert artifacts.classify_open_artifact_name(
        "task-artifact",
        "quality_report.json",
        result_dir,
        sanitize_filename=sanitize,
        allowlist={},
    ) == {
        "kind": "forbidden",
        "artifact_name": "quality_report.json",
    }


def test_classify_open_artifact_forbidden_returns_sanitized_name(tmp_path):
    calls = []

    def sanitize(value):
        calls.append(value)
        return os.path.basename(value)

    descriptor = artifacts.classify_open_artifact_name(
        "task-secret",
        "../secret.txt",
        str(tmp_path / "task-secret"),
        sanitize_filename=sanitize,
    )

    assert calls == ["../secret.txt"]
    assert descriptor == {
        "kind": "forbidden",
        "artifact_name": "secret.txt",
    }


def test_markdown_artifact_prefers_existing_canonical_path(tmp_path):
    task = {"task_id": "task-1", "markdown_path": None}
    assert artifacts.markdown_artifact_path(task, str(tmp_path)) is None

    result_dir = tmp_path / "task-1"
    result_dir.mkdir()
    md_path = result_dir / "result.md"
    md_path.write_text("# ok\n", encoding="utf-8")

    assert artifacts.has_markdown_artifact(task, str(tmp_path))
    assert artifacts.markdown_artifact_path(task, str(tmp_path)) == str(md_path)


def test_write_json_is_readable_and_load_json_artifact_coerces(tmp_path):
    task = {"task_id": "task-json"}
    path = tmp_path / "task-json" / "content_list.json"

    artifacts.write_json(str(path), [{"type": "text"}])

    def read_json_cached(value):
        return path.read_text(encoding="utf-8") if value == str(path) else None

    payload = artifacts.load_json_artifact(
        task,
        "content_list.json",
        results_folder=str(tmp_path),
        read_json_cached=read_json_cached,
        coerce_json_artifact=json.loads,
    )

    assert payload == [{"type": "text"}]


def test_apply_table_corrections_replaces_only_fixed_tables():
    markdown = (
        "<table><tr><td>一</td></tr></table>\n"
        "<table><tr><td>二</td></tr></table>\n"
    )
    corrected, count = artifacts.apply_table_corrections(
        markdown,
        {
            "tables": {
                "1": {"review_status": "needs_fix", "table_markdown": "<table><tr><td>bad</td></tr></table>"},
                "2": {"review_status": "fixed", "table_markdown": "<table><tr><td>fixed</td></tr></table>"},
            }
        },
    )

    assert count == 1
    assert "<td>一</td>" in corrected
    assert "<td>fixed</td>" in corrected
    assert "<td>二</td>" not in corrected


def test_save_images_and_build_zip(tmp_path):
    image_bytes = b"image-bytes"
    images_dir = tmp_path / "images"
    saved = artifacts.save_images(
        {
            "../bad": {"data": base64.b64encode(image_bytes).decode("ascii")},
            "chart.png": "data:image/png;base64," + base64.b64encode(b"png").decode("ascii"),
        },
        str(images_dir),
    )

    assert saved == 2
    names = artifacts.image_artifact_names(str(images_dir))
    assert names == ["bad.jpg", "chart.png"]
    archive = artifacts.build_images_zip(str(images_dir), names)
    assert archive.getbuffer().nbytes > 0
