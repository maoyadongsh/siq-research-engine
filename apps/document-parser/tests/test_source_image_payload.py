from source_image_payload import build_source_image_payload, find_figure_by_image_id


def test_find_figure_by_image_id_returns_first_matching_figure():
    first = {"image_id": "img-1", "image_path": "images/a.png"}
    second = {"image_id": "img-2", "image_path": "images/b.png"}

    assert find_figure_by_image_id([first, second], "img-1") is first
    assert find_figure_by_image_id([first, second], "missing") is None


def test_build_source_image_payload_uses_image_paths_and_metadata():
    figure = {
        "image_id": "img-1",
        "page_number": 3,
        "bbox": [1, 2, 3, 4],
        "bbox_unit": "pt",
        "caption": "Chart caption",
        "ocr_text": "Revenue",
        "image_path": "images/original/chart.png",
        "crop_path": "images/crops/chart.png",
        "thumbnail_path": "images/thumbs/chart.png",
    }

    assert build_source_image_payload("task-a", "img-1", figure) == {
        "task_id": "task-a",
        "image_id": "img-1",
        "page_number": 3,
        "bbox": [1, 2, 3, 4],
        "bbox_unit": "pt",
        "caption": "Chart caption",
        "ocr_text": "Revenue",
        "figure": figure,
        "image_url": "/api/artifact/task-a/images/original/chart.png",
        "crop_url": "/api/artifact/task-a/images/crops/chart.png",
        "thumbnail_url": "/api/artifact/task-a/images/thumbs/chart.png",
        "open_artifact_url": "/api/documents/artifact/task-a/images/original/chart.png",
    }


def test_build_source_image_payload_keeps_existing_fallbacks():
    figure = {
        "image_id": "img-1",
        "alt_text": "Alt caption",
        "image_path": "images/original/chart.png",
    }

    payload = build_source_image_payload("task-a", "img-1", figure)

    assert payload["page_number"] == 1
    assert payload["bbox"] == []
    assert payload["bbox_unit"] == "none"
    assert payload["caption"] == "Alt caption"
    assert payload["ocr_text"] == ""
    assert payload["crop_url"] == "/api/artifact/task-a/images/original/chart.png"
    assert payload["thumbnail_url"] == ""


def test_build_source_image_payload_allows_missing_image_path():
    payload = build_source_image_payload("task-a", "img-1", {"image_id": "img-1"})

    assert payload["image_url"] == ""
    assert payload["crop_url"] == ""
    assert payload["open_artifact_url"] == ""
