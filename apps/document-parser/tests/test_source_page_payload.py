import pytest

from source_page_payload import build_source_page_payload


def test_build_source_page_payload_filters_blocks_and_uses_page_metadata():
    blocks = [
        {"block_id": "p1-default"},
        {"block_id": "p2-a", "page_number": 2},
        {"block_id": "p2-b", "page_number": "2"},
        {"block_id": "p3", "page_number": 3},
    ]
    layout = {
        "pages": [
            {"page_number": 1, "width": 100, "height": 200},
            {"page_number": 2, "width": 595, "height": 842, "page_size": [595, 842], "bbox_unit": "pt"},
        ]
    }

    payload = build_source_page_payload("task-a", 2, blocks, layout)

    assert payload == {
        "task_id": "task-a",
        "page_number": 2,
        "page": {
            "page_number": 2,
            "page_index": 1,
            "width": 595,
            "height": 842,
            "page_size": [595, 842],
            "bbox_unit": "pt",
        },
        "blocks": [
            {"block_id": "p2-a", "page_number": 2},
            {"block_id": "p2-b", "page_number": "2"},
        ],
        "block_count": 2,
        "page_image_url": "/api/source/task-a/page-image/2",
    }


def test_build_source_page_payload_keeps_existing_fallbacks_for_missing_metadata():
    payload = build_source_page_payload("task-a", 1, [{"block_id": "default-page"}], {"pages": []})

    assert payload["page"] == {
        "page_number": 1,
        "page_index": 0,
        "width": 0,
        "height": 0,
        "page_size": [],
        "bbox_unit": "none",
    }
    assert payload["blocks"] == [{"block_id": "default-page"}]
    assert payload["block_count"] == 1


def test_build_source_page_payload_preserves_invalid_page_number_errors():
    with pytest.raises(ValueError):
        build_source_page_payload("task-a", 1, [{"block_id": "bad", "page_number": "bad"}], {})
