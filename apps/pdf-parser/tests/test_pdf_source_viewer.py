import json

import pytest

from pdf_source_viewer import (
    page_bbox_extent_from_content_list,
    printed_page_numbers_by_pdf_page,
)


def test_page_bbox_extent_coerces_json_and_uses_valid_bboxes_for_target_page_only():
    content_list = [
        {"page_idx": 2, "bbox": [1, 2, 30.5, 40.25]},
        {"page_idx": 2, "bbox": ["4", "5", "18", "22"]},
        {"page_idx": 3, "bbox": [1, 2, 999, 999]},
        {"page_idx": 2, "bbox": [1, 2, 3]},
        {"page_idx": 2, "bbox": [1, 2, 3, 4, 5]},
        {"page_idx": 2, "bbox": [1, 2, "not-a-number", 4]},
        {"page_idx": 2, "bbox": None},
        "not-a-dict",
    ]

    assert page_bbox_extent_from_content_list(json.dumps(content_list), 2) == {
        "width": 30.5,
        "height": 40.25,
    }


@pytest.mark.parametrize(
    ("content_list", "page_index"),
    [
        ("not-json", 0),
        ({"page_idx": 0, "bbox": [1, 2, 3, 4]}, 0),
        ([{"page_idx": 0, "bbox": [1, 2, 3, 4]}], None),
        ([{"page_idx": 0, "bbox": [1, 2, 3]}], 0),
        ([{"page_idx": 0, "bbox": [0, 2, 0, 4]}], 0),
        ([{"page_idx": 0, "bbox": [1, 0, 3, 0]}], 0),
        ([{"page_idx": 1, "bbox": [1, 2, 3, 4]}], 0),
    ],
)
def test_page_bbox_extent_returns_none_for_invalid_inputs(content_list, page_index):
    assert page_bbox_extent_from_content_list(content_list, page_index) is None


def test_printed_page_numbers_only_uses_page_number_blocks_with_int_page_idx_and_text():
    content_list = [
        {"type": "page_number", "page_idx": 0, "text": "  iv  "},
        {"type": "page_number", "page_idx": 2, "text": 12},
        {"type": "text", "page_idx": 1, "text": "ignored"},
        {"type": "page_number", "page_idx": "1", "text": "ignored"},
        {"type": "page_number", "page_idx": 3, "text": "   "},
        {"type": "page_number", "page_idx": None, "text": "ignored"},
        "not-a-dict",
    ]

    assert printed_page_numbers_by_pdf_page(json.dumps(content_list)) == {
        1: "iv",
        3: "12",
    }
