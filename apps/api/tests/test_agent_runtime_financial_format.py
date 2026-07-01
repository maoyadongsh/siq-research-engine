import pytest

from services import agent_runtime_financial_format as fmt


class StringOnlyValue:
    def __str__(self) -> str:
        return "not-a-number"


@pytest.mark.parametrize(
    ("value", "digits", "expected"),
    [
        (None, 1, "未返回"),
        (1234, 1, "1,234"),
        (1234.0, 2, "1,234"),
        (1234.567, 1, "1,234.6"),
        (1234.567, 2, "1,234.57"),
        ("12.30", 1, "12.30"),
        (StringOnlyValue(), 1, "not-a-number"),
    ],
)
def test_fmt_number_preserves_integer_decimal_and_fallback_formatting(value, digits, expected):
    assert fmt._fmt_number(value, digits) == expected


def test_calculator_per_capita_display_formats_preferred_values_and_unknown_preference_falls_back():
    payload = {
        "status": "ok",
        "result": {
            "cny_10k_per": "12.34567",
            "cny_10k_per_unit": "万元/人",
            "native_10k_per": "8.76543",
            "native_10k_per_unit": "万欧元/人",
            "native_per": "87654.321",
            "native_per_unit": "欧元/人",
        },
    }

    assert fmt._calculator_per_capita_display(payload) == "12.3457万元/人"
    assert fmt._calculator_per_capita_display(payload, preferred="native_10k") == "8.7654万欧元/人"
    assert fmt._calculator_per_capita_display(payload, preferred="native_per") == "87,654.32欧元/人"
    assert fmt._calculator_per_capita_display(payload, preferred="unexpected") == "12.35万元/人"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (None, "未返回"),
        ({}, "未返回"),
        ({"status": "not_applicable", "result": {"cny_10k_per": "1", "cny_10k_per_unit": "万元/人"}}, "未返回"),
        ({"status": "ok", "result": {"cny_10k_per": "", "cny_10k_per_unit": "万元/人"}}, "未返回"),
        ({"status": "ok", "result": {"cny_10k_per": "1.2"}}, "未返回"),
        ({"status": "ok", "result": {"cny_10k_per": "approx. 1.2", "cny_10k_per_unit": "万元/人"}}, "approx. 1.2万元/人"),
    ],
)
def test_calculator_per_capita_display_handles_payload_boundaries(payload, expected):
    assert fmt._calculator_per_capita_display(payload) == expected


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (None, "未返回"),
        ({}, "未返回"),
        ({"formula": []}, "未返回"),
        ({"formula": ["100 / 20", "unit: 元/人", 5]}, "100 / 20；unit: 元/人；5"),
    ],
)
def test_calculator_formula_text_joins_formula_items(payload, expected):
    assert fmt._calculator_formula_text(payload) == expected


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (None, {"pdf_page_number": None, "table_index": None, "line": None}),
        ({}, {"pdf_page_number": None, "table_index": None, "line": None}),
        (
            {"pdf_page": 0, "table_index": 0, "md_line": 0, "ignored": "x"},
            {"pdf_page_number": 0, "table_index": 0, "line": 0},
        ),
    ],
)
def test_statement_row_table_maps_payload_boundaries(row, expected):
    assert fmt._statement_row_table(row) == expected


def test_agent_chat_runtime_financial_format_wrappers_preserve_compatibility():
    pytest.importorskip("sqlmodel")
    from services import agent_chat_runtime as runtime

    payload = {
        "status": "ok",
        "result": {
            "cny_10k_per": "12.34567",
            "cny_10k_per_unit": "万元/人",
        },
        "formula": ["123 / 10"],
    }
    row = {"pdf_page": 7, "table_index": 2, "md_line": 99}

    assert runtime._fmt_number(1234.567, 2) == fmt._fmt_number(1234.567, 2)
    assert runtime._calculator_per_capita_display(payload) == fmt._calculator_per_capita_display(payload)
    assert runtime._calculator_formula_text(payload) == fmt._calculator_formula_text(payload)
    assert runtime._statement_row_table(row) == fmt._statement_row_table(row)
