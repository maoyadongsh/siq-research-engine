from services import agent_runtime_fallback_contexts as fallback


def test_postgres_row_helpers_prefer_top_level_fields():
    row = {
        "item_name": "营业收入",
        "raw_value": "123.45",
        "unit": "万元",
        "source_page_number": 42,
        "source_table_index": 7,
        "source_markdown_line": 88,
        "metric_payload": {
            "item_name": "payload-item",
            "raw_value": "payload-value",
            "unit": "payload-unit",
            "source": {
                "page_number": 12,
                "table_index": 3,
                "markdown_line": 66,
            },
        },
    }

    assert fallback._postgres_row_payload(row) == row["metric_payload"]
    assert fallback._postgres_row_source(row) == row["metric_payload"]["source"]
    assert fallback._postgres_row_metric_name(row) == "营业收入"
    assert fallback._postgres_row_value(row) == "123.45"
    assert fallback._postgres_row_unit(row) == "万元"
    assert fallback._postgres_row_pdf_page(row) == 42
    assert fallback._postgres_row_table_index(row) == 7
    assert fallback._postgres_row_md_line(row) == 88


def test_postgres_row_helpers_fall_back_to_payload_source():
    row = {
        "metric_key": "goodwill",
        "metric_payload": {
            "metric_name": "商誉",
            "value": "100",
            "unit": "元",
            "source": {
                "pdf_page_number": 18,
                "source_table_index": 5,
                "md_line": 120,
            },
        },
    }

    assert fallback._postgres_row_metric_name(row) == "商誉"
    assert fallback._postgres_row_value(row) == "100"
    assert fallback._postgres_row_unit(row) == "元"
    assert fallback._postgres_row_pdf_page(row) == 18
    assert fallback._postgres_row_table_index(row) == 5
    assert fallback._postgres_row_md_line(row) == 120


def test_postgres_row_helpers_handle_empty_payloads_and_markdown_cells():
    row = {"metric_payload": "not-a-dict"}

    assert fallback._postgres_row_payload(row) == {}
    assert fallback._postgres_row_source(row) == {}
    assert fallback._postgres_row_metric_name(row) == "未返回"
    assert fallback._postgres_row_value(row) is None
    assert fallback._postgres_row_unit(row) is None
    assert fallback._postgres_row_pdf_page(row) is None
    assert fallback._postgres_row_table_index(row) is None
    assert fallback._postgres_row_md_line(row) is None
    assert fallback._markdown_table_cell(None) == "未返回"
    assert fallback._markdown_table_cell("") == "未返回"
    assert fallback._markdown_table_cell("  A|B\nC  ") == "A\\|B C"
