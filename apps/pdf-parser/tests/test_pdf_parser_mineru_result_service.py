import json

import pdf_parser_mineru_result_service as mineru_result


def test_save_mineru_result_artifacts_writes_summary_and_payloads(tmp_path):
    task = {"task_id": "mineru-task"}
    writes = {}
    saved_images = []

    def result_dir(value):
        return str(tmp_path / value["task_id"])

    def write_json(path, payload):
        writes[path] = payload

    def save_images(images, images_dir):
        saved_images.append((images, images_dir))
        return 2

    payload = mineru_result.save_mineru_result_artifacts(
        task,
        {"backend": "mineru", "version": "1.0"},
        "result.md",
        {
            "middle_json": {"pages": 1},
            "model_output": {"ok": True},
            "content_list": [{"type": "text"}],
            "images": {"figure_1.png": "data:image/png;base64,aGVsbG8="},
        },
        result_dir=result_dir,
        write_json=write_json,
        save_images=save_images,
    )

    assert payload["summary"]["result_file"] == "result.md"
    assert payload["image_count"] == 2
    assert any(path.endswith("result_payload_summary.json") for path in writes)
    assert json.loads(json.dumps(writes[next(path for path in writes if path.endswith("middle.json"))])) == {"pages": 1}
    assert saved_images and saved_images[0][1].endswith("mineru-task/images")
