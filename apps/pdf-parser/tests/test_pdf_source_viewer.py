import json

import pytest

from pdf_source_viewer import (
    page_bbox_extent_from_content_list,
    page_content_payload_from_content_list,
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


def test_page_content_payload_coerces_json_string_and_builds_block_payloads():
    content_list = [
        {"type": "page_number", "page_idx": 0, "text": " A-1 "},
        {
            "type": "image",
            "page_idx": 0,
            "bbox": [1, 2, 3, 4],
            "img_path": "images/page_1/chart.png",
            "sub_type": "chart",
            "image_caption": ["Revenue chart"],
            "image_footnote": ["Source: filing"],
        },
        {
            "type": "list",
            "page_idx": 0,
            "bbox": [5, 6, 7, 8],
            "list_items": ["first", "second"],
            "sub_type": "ordered",
        },
        {"type": "diagram", "page_idx": 0, "bbox": [9, 10, 11, 12], "payload": {"raw": True}},
        {"type": "text", "page_idx": 1, "text": "wrong page"},
    ]

    payload = page_content_payload_from_content_list(json.dumps(content_list), 1)

    assert payload["page_number"] == 1
    assert payload["page_index"] == 0
    assert payload["printed_page_number"] == "A-1"
    assert payload["block_count"] == 4
    assert payload["table_count"] == 0
    assert [block["type"] for block in payload["blocks"]] == ["page_number", "image", "list", "diagram"]
    assert payload["blocks"][0]["text"] == " A-1 "
    assert payload["blocks"][1] == {
        "block_id": "b000002",
        "type": "image",
        "bbox": [1, 2, 3, 4],
        "page_number": 1,
        "pdf_page_number": 1,
        "reading_order": 2,
        "image_path": "images/page_1/chart.png",
        "sub_type": "chart",
        "caption": ["Revenue chart"],
        "footnote": ["Source: filing"],
    }
    assert payload["blocks"][2]["list_items"] == ["first", "second"]
    assert payload["blocks"][2]["sub_type"] == "ordered"
    assert payload["blocks"][3]["raw"] == content_list[3]


@pytest.mark.parametrize("page_number", [0, -1])
def test_page_content_payload_rejects_invalid_page(page_number):
    with pytest.raises(ValueError, match="Invalid page number"):
        page_content_payload_from_content_list([], page_number)


def test_page_content_payload_returns_empty_payload_for_non_list_content():
    payload = page_content_payload_from_content_list({"not": "a-list"}, 3)

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


def test_page_content_payload_matches_tables_by_source_id_and_focus_table():
    first_table = "<table><tr><td>Revenue</td></tr></table>"
    second_table = "<table><tr><td>Assets</td></tr></table>"
    content_list = [
        {"type": "table", "page_idx": 0, "bbox": [1, 2, 3, 4], "table_body": first_table},
        {"type": "text", "page_idx": 0, "text": "between tables"},
        {"type": "table", "page_idx": 0, "bbox": [5, 6, 7, 8], "table_body": second_table},
        {"type": "page_number", "page_idx": 0, "text": "i"},
    ]
    report = {
        "table_index": [
            {
                "table_index": 10,
                "content_table_source_id": 1,
                "line": 100,
                "pdf_page_number": 1,
                "heading": "Revenue table",
                "printed_page_number": "R-1",
                "matched_financial_names": ["revenue"],
            },
            {
                "table_index": 11,
                "content_table_source_id": 2,
                "line": 110,
                "pdf_page_number": 1,
                "heading": "Assets table",
                "matched_financial_names": ["assets"],
            },
        ]
    }

    payload = page_content_payload_from_content_list(content_list, 1, report=report, focus_table=11)
    tables = [block for block in payload["blocks"] if block["type"] == "table"]

    assert payload["table_count"] == 2
    assert payload["page_tables"] == [
        {
            "table_index": 10,
            "source_table_index": 1,
            "line": 100,
            "heading": "Revenue table",
            "printed_page_number": "R-1",
            "matched_financial_names": ["revenue"],
        },
        {
            "table_index": 11,
            "source_table_index": 2,
            "line": 110,
            "heading": "Assets table",
            "printed_page_number": "i",
            "matched_financial_names": ["assets"],
        },
    ]
    assert [table["table_index"] for table in tables] == [10, 11]
    assert [table["source_table_index"] for table in tables] == [1, 2]
    assert tables[0]["heading"] == "Revenue table"
    assert tables[0]["line"] == 100
    assert tables[0]["printed_page_number"] == "R-1"
    assert tables[0]["is_focus_table"] is False
    assert tables[1]["is_focus_table"] is True
    assert tables[1]["matched_financial_names"] == ["assets"]


def test_page_content_payload_matches_table_by_bbox_when_source_id_is_missing():
    table_html = "<table><tr><td>Cash</td></tr></table>"
    content_list = [
        {
            "type": "table",
            "page_idx": 1,
            "bbox": ["10", "20", "30", "40"],
            "table_body": table_html,
            "table_caption": ["Content caption"],
            "table_footnote": ["Content footnote"],
        },
    ]
    report = {
        "table_index": [
            {
                "table_index": 7,
                "pdf_page_number": 2,
                "bbox": [10.0, 20.0, 30.0, 40.0],
                "line": 70,
                "heading": "Cash table",
                "source_caption": ["Report caption"],
                "source_footnote": ["Report footnote"],
            }
        ]
    }

    payload = page_content_payload_from_content_list(content_list, 2, report=report)
    table = payload["blocks"][0]

    assert table["table_index"] == 7
    assert table["source_table_index"] == 1
    assert table["heading"] == "Cash table"
    assert table["line"] == 70
    assert table["caption"] == ["Content caption"]
    assert table["footnote"] == ["Content footnote"]
    assert table["missing_body"] is False


def test_page_content_payload_uses_report_caption_and_footnote_when_content_omits_them():
    table_html = "<table><tr><td>Inventory</td></tr></table>"
    content_list = [
        {
            "type": "table",
            "page_idx": 0,
            "bbox": [1, 2, 3, 4],
            "table_body": table_html,
            "table_caption": [],
            "table_footnote": [],
        },
    ]
    report = {
        "table_index": [
            {
                "table_index": 12,
                "content_table_source_id": 1,
                "pdf_page_number": 1,
                "source_caption": ["Report caption"],
                "source_footnote": ["Report footnote"],
            }
        ]
    }

    payload = page_content_payload_from_content_list(content_list, 1, report=report)
    table = payload["blocks"][0]

    assert table["table_index"] == 12
    assert table["caption"] == ["Report caption"]
    assert table["footnote"] == ["Report footnote"]


def test_page_content_payload_matches_source_id_zero_to_first_table_body():
    content_list = [
        {"type": "text", "page_idx": 0, "text": "before table"},
        {
            "type": "table",
            "page_idx": 0,
            "bbox": [1, 2, 3, 4],
            "table_body": "<table><tr><td>Zero source id</td></tr></table>",
        },
    ]
    report = {
        "table_index": [
            {
                "table_index": 21,
                "content_table_source_id": 0,
                "line": 210,
                "pdf_page_number": 1,
                "heading": "First table via zero source id",
            }
        ]
    }

    payload = page_content_payload_from_content_list(content_list, 1, report=report)
    table = next(block for block in payload["blocks"] if block["type"] == "table")

    assert table["table_index"] == 21
    assert table["source_table_index"] == 1
    assert table["heading"] == "First table via zero source id"
    assert table["line"] == 210
    assert payload["page_tables"] == [
        {
            "table_index": 21,
            "source_table_index": 0,
            "line": 210,
            "heading": "First table via zero source id",
            "printed_page_number": None,
            "matched_financial_names": [],
        }
    ]


def test_page_content_payload_ignores_invalid_report_table_rows():
    content_list = [
        {
            "type": "table",
            "page_idx": 0,
            "bbox": [10, 20, 30, 40],
            "table_body": "<table><tr><td>Clean table</td></tr></table>",
        }
    ]
    report = {
        "table_index": [
            {
                "table_index": "not-a-number",
                "content_table_source_id": 1,
                "line": 999,
                "pdf_page_number": 1,
                "heading": "Invalid table index",
            },
            {
                "table_index": 22,
                "content_table_source_id": 1,
                "line": 220,
                "pdf_page_number": "not-a-number",
                "heading": "Invalid page number",
            },
        ]
    }

    payload = page_content_payload_from_content_list(content_list, 1, report=report)
    table = payload["blocks"][0]

    assert table["table_index"] == 1
    assert table["source_table_index"] == 1
    assert table["heading"] == ""
    assert table["line"] is None
    assert payload["page_tables"] == []


def test_page_content_payload_raises_for_non_numeric_focus_table():
    content_list = [
        {
            "type": "table",
            "page_idx": 0,
            "bbox": [1, 2, 3, 4],
            "table_body": "<table><tr><td>Focus candidate</td></tr></table>",
        }
    ]

    with pytest.raises(ValueError):
        page_content_payload_from_content_list(content_list, 1, focus_table="not-a-number")
