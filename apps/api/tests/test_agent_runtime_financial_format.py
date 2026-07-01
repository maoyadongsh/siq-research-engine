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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("-", None),
        ("未返回", None),
        ("N/A", None),
        ("null", None),
        ("€1,234.50", 1234.5),
        ("-12.3%", -12.3),
        ("approx. 8,765 people", 8765.0),
        ("not-a-number", None),
    ],
)
def test_parse_number_handles_empty_and_mixed_numeric_text(value, expected):
    assert fmt._parse_number(value) == expected


def test_row_numeric_values_skips_label_empty_refs_and_preserves_mixed_numbers():
    row = ["Sales revenue", "", "[1]", "€68,449 million", "-5.6%", "N/A", "approx. 42"]

    assert fmt._row_numeric_values(row) == [68449.0, -5.6, 42.0]
    assert fmt._row_numeric_values(None) == []


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


def test_table_trace_uses_source_fallbacks_and_optional_links():
    trace = fmt._table_trace(
        2,
        source_type="wiki_report_table",
        file="reports/2025-annual/report.md",
        metric="营业收入",
        report_id="2025-annual",
        task_id="task-1",
        table={"pdf_page": 12, "table_index": "", "markdown_line": 345},
        links="[查看表格](https://example.test/table)",
    )

    assert trace == (
        "[H2] source_type=wiki_report_table, file=reports/2025-annual/report.md, metric=营业收入, "
        "period=2025-annual, task_id=task-1, pdf_page=12, table_index=未返回, md_line=345，"
        "[查看表格](https://example.test/table)"
    )


def test_table_trace_falls_back_to_missing_values_without_links():
    assert fmt._table_trace(
        1,
        source_type="wiki_metrics",
        file="metrics/three_statements.json",
        metric="净利润",
        report_id="2025-annual",
        task_id=None,
        table={},
    ) == (
        "[H1] source_type=wiki_metrics, file=metrics/three_statements.json, metric=净利润, "
        "period=2025-annual, task_id=未返回, pdf_page=未返回, table_index=未返回, md_line=未返回"
    )


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
    assert runtime._parse_number("€1,234.50") == fmt._parse_number("€1,234.50")
    assert runtime._row_numeric_values(["label", "[1]", "1,000"]) == fmt._row_numeric_values(["label", "[1]", "1,000"])
    assert runtime._calculator_per_capita_display(payload) == fmt._calculator_per_capita_display(payload)
    assert runtime._calculator_formula_text(payload) == fmt._calculator_formula_text(payload)
    assert runtime._statement_row_table(row) == fmt._statement_row_table(row)
    assert runtime._table_trace(
        1,
        source_type="wiki_metrics",
        file="metrics/three_statements.json",
        metric="营业收入",
        report_id="2025-annual",
        task_id=None,
        table=row,
    ) == fmt._table_trace(
        1,
        source_type="wiki_metrics",
        file="metrics/three_statements.json",
        metric="营业收入",
        report_id="2025-annual",
        task_id=None,
        table=fmt._statement_row_table(row),
    )
