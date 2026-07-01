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


def test_wiki_fulltext_html_and_search_text_helpers_normalize_content():
    html = "<table><tr><th>项目</th><th>金额</th></tr><tr><td>商誉&nbsp;</td><td>100</td></tr></table>"

    text = fallback._html_to_text(html)

    assert "项目 | 金额" in text
    assert "商誉" in text
    assert "100 |" in text
    assert fallback._normalize_search_text(" 商誉（账面_净额）/增长率：10% ") == "商誉账面净额增长率10%"
    assert fallback._specific_fulltext_terms(
        ["报告", "市场占有率", " 数据 ", "", "商誉"],
        {"报告", "数据"},
    ) == ["市场占有率", "商誉"]


def test_wiki_fulltext_company_alias_helpers_extract_and_strip_long_aliases_first():
    company = {
        "company_id": "600104-上汽集团",
        "stock_code": "600104",
        "company_short_name": "上汽",
        "company_full_name": "上汽集团股份有限公司",
        "aliases": ["上汽集团", "SAIC"],
    }

    aliases = fallback._company_aliases("600104-上汽集团", company)
    text = fallback._remove_company_aliases("请问上汽集团股份有限公司和上汽集团的市场占有率", aliases)

    assert aliases == [
        "600104-上汽集团",
        "600104",
        "上汽",
        "上汽集团股份有限公司",
        "上汽集团",
        "SAIC",
    ]
    assert "上汽集团" not in text
    assert "上汽" not in text
    assert "市场占有率" in text


def test_wiki_fulltext_fallback_search_terms_clean_aliases_noise_and_dedupe():
    company = {
        "company_id": "300383-光环新网",
        "stock_code": "300383",
        "company_short_name": "光环新网",
        "company_full_name": "北京光环新网科技股份有限公司",
    }
    aliases = fallback._company_aliases("300383-光环新网", company)

    terms = fallback._fallback_search_terms(
        "帮我看看光环新网2025年年度报告中的商誉情况，以及商誉减值准备。",
        aliases,
        ("商誉", "商誉减值准备", "报告"),
    )

    assert terms == ["商誉减值准备", "商誉", "报告"]


def test_wiki_fulltext_line_scoring_matches_terms_and_boosts_tables():
    plain_score = fallback._line_match_score("市场占有率稳定提升", ["市场占有率"])
    table_score = fallback._line_match_score("<table><tr><td>市场占有率</td><td>13.1%</td></tr></table>", ["市场占有率"])

    assert plain_score == 25
    assert table_score == 36
    assert fallback._line_matches_any_term("市场 占有率：13.1%", ["市场占有率"])
    assert not fallback._line_matches_any_term("营业收入 100", ["市场占有率"])


def test_wiki_fulltext_snippet_window_strips_html_and_truncates():
    lines = [
        "[PDF_PAGE: 8]",
        "<p>上文</p>",
        "<table><tr><td>市场占有率</td><td>13.1%</td></tr></table>",
        "<p>下文带有很长的说明内容</p>",
    ]

    snippet = fallback._snippet_window(lines, 3, radius=1, snippet_chars=24)

    assert "上文" in snippet
    assert "市场占有率 | 13.1%" in snippet
    assert snippet.endswith("...")
    assert "<table" not in snippet


def test_wiki_fulltext_nearest_pdf_page_searches_backward_and_clamps():
    lines = [
        "[PDF_PAGE: 3]",
        "第一页内容",
        "继续",
        "[PDF_PAGE: 8]",
        "目标行",
    ]

    assert fallback._nearest_report_pdf_page(lines, 5) == 8
    assert fallback._nearest_report_pdf_page(lines, 3) == 3
    assert fallback._nearest_report_pdf_page(lines, 999) == 8
    assert fallback._nearest_report_pdf_page(lines, None) is None


def test_wiki_fulltext_nearest_table_meta_prefers_distance_then_table_index():
    tables = [
        {"table_index": 8, "line": 99},
        {"table_index": 2, "markdown_line": "101"},
        {"table_index": 5, "md_line": 101},
        {"table_index": 1, "line": "bad"},
        {"table_index": 3, "line": 110},
    ]

    assert fallback._nearest_table_meta(tables, 100) == tables[1]
    assert fallback._nearest_table_meta(tables, 101) == tables[1]
    assert fallback._nearest_table_meta(tables, 100, max_distance=0) is None
