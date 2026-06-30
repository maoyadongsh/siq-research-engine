import base64
import json
import os

import pdf_parser_artifact_service as artifacts


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
