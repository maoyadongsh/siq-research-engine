from pathlib import Path
import sys
import types

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))


class _DummyFlask:
    def __init__(self, *args, **kwargs):
        self.config = {}

    def route(self, *args, **kwargs):
        def decorator(func):
            return func

        return decorator

    def before_request(self, func=None):
        def decorator(func):
            return func

        return decorator if func is None else func

    def errorhandler(self, *args, **kwargs):
        def decorator(func):
            return func

        return decorator


sys.modules.setdefault(
    "flask",
    types.SimpleNamespace(
        Flask=_DummyFlask,
        jsonify=lambda *args, **kwargs: None,
        make_response=lambda value: types.SimpleNamespace(
            value=value,
            headers={},
            set_cookie=lambda *args, **kwargs: None,
        ),
        render_template=lambda *args, **kwargs: "",
        request=types.SimpleNamespace(
            args={},
            files={},
            form={},
            headers={},
            cookies={},
            get_json=lambda silent=True: {},
        ),
        send_file=lambda *args, **kwargs: None,
    ),
)

import app


def test_table_structure_signals_detects_expanded_multi_level_header():
    table_html = (
        "<table>"
        "<tr><th rowspan='2'>项目</th><th colspan='2'>2025年度</th></tr>"
        "<tr><th>金额</th><th>比例</th></tr>"
        "<tr><td>营业收入</td><td>100</td><td>60%</td></tr>"
        "</table>"
    )

    signals = app._table_structure_signals(table_html)

    assert signals["expanded_rows"] == 3
    assert signals["expanded_columns"] == 3
    assert signals["header_row_count"] == 2
    assert signals["has_colspan"] is True
    assert signals["has_rowspan"] is True
    assert signals["multi_level_header_candidate"] is True
    assert signals["header_preview"][0] == "项目 | 2025年度 | 2025年度"


def test_matched_financial_table_names_uses_context_and_content_signals():
    context = {"heading": "第二节 公司简介和主要财务指标", "near_text": "主要会计数据和财务指标 单位：万元"}
    source = {"caption": ["主要会计数据"], "footnote": []}
    table_text = "项目 2025年 2024年 营业收入 100 90 净利润 20 18 资产总额 300 280 基本每股收益 1.20 1.10 净资产收益率 12% 10%"

    names = app._matched_financial_table_names(context, table_text, source)

    assert "主要会计数据" in names
    assert "主要财务指标" in names
    assert names.index("主要会计数据") < names.index("主要财务指标")


def test_matched_financial_table_names_filters_statement_date_noise():
    context = {"heading": "资产负债表日后事项", "near_text": "财务报表附注"}
    source = {"caption": ["资产负债表日后事项"], "footnote": []}

    names = app._matched_financial_table_names(context, "资产负债表日后事项说明", source)

    assert "资产负债表" not in names


def test_classify_table_semantics_splits_dimension_from_fact_tables():
    dimension = app._classify_table_semantics(
        {"heading": "公司信息", "near_text": "股票简称 股票代码 注册地址", "unit": ""},
        [],
        {"caption": [], "footnote": []},
        numeric_ratio=0,
        row_count=3,
    )
    fact = app._classify_table_semantics(
        {"heading": "合并利润表", "near_text": "", "unit": "单位：万元"},
        ["利润表"],
        {"caption": [], "footnote": []},
        numeric_ratio=0.5,
        row_count=4,
    )

    assert dimension["table_type"] == "dimension"
    assert dimension["year_binding_required"] is False
    assert "dimension_keyword" in dimension["classification_reasons"]
    assert fact["table_type"] == "fact"
    assert fact["year_binding_required"] is True
    assert "matched_financial_name" in fact["classification_reasons"]
    assert "numeric_density_high" in fact["classification_reasons"]


def test_build_table_index_enriches_page_source_semantics_and_suspect_reasons():
    table_html = (
        "<table>"
        "<tr><td>项目</td><td>2025年</td><td>2024年</td></tr>"
        "<tr><td>营业收入</td><td>100</td><td>90</td></tr>"
        "<tr><td>净利润</td><td>20</td><td>18</td></tr>"
        "<tr><td>资产总额</td><td>300</td><td>280</td></tr>"
        "<tr><td>基本每股收益</td><td>1.2</td><td>1.1</td></tr>"
        "<tr><td>净资产收益率</td><td>12%</td><td>10%</td></tr>"
        "</table>"
    )
    markdown = f"[PDF_PAGE: 7]\n# 主要会计数据和财务指标\n单位：万元\n{table_html}\n"
    content_list = [
        {"type": "page_number", "page_idx": 6, "text": "5"},
        {
            "type": "table",
            "page_idx": 6,
            "bbox": [10, 20, 300, 400],
            "table_body": table_html,
            "table_caption": ["主要会计数据"],
            "table_footnote": ["单位：万元"],
        },
    ]

    table_index = app._build_table_index(markdown, [table_html], content_list=content_list, report_year=2025)

    assert len(table_index) == 1
    item = table_index[0]
    assert item["table_index"] == 1
    assert item["pdf_page_number"] == 7
    assert item["printed_page_number"] == "5"
    assert item["bbox"] == [10.0, 20.0, 300.0, 400.0]
    assert item["table_type"] == "fact"
    assert item["year_binding_required"] is True
    assert item["fact_year"] == 2025
    assert item["matched_financial_names"] == ["主要会计数据", "主要财务指标"]
    assert item["suspect_reasons"] == []
    assert "matched_financial_name" in item["classification_reasons"]


def test_build_table_index_flags_short_key_table_candidate_as_suspect():
    table_html = "<table><tr><td>资产负债表</td><td>2025年</td></tr></table>"
    markdown = f"# 合并资产负债表\n{table_html}\n"

    table_index = app._build_table_index(markdown, [table_html], report_year=2025)

    assert table_index[0]["matched_financial_names"] == ["资产负债表"]
    assert "single_row" in table_index[0]["suspect_reasons"]
    assert "key_table_too_short" in table_index[0]["suspect_reasons"]


def test_group_key_table_candidates_orders_primary_by_score_and_display_order():
    table_index = [
        {
            "table_index": 1,
            "line": 4,
            "pdf_page_number": 10,
            "pdf_page_source": "markdown_marker_inferred",
            "pdf_page_inference_reason": "marker",
            "bbox": [1, 2, 3, 4],
            "rows": 2,
            "cells": 6,
            "empty_ratio": 0,
            "numeric_ratio": 0.2,
            "heading": "资产负债表附注",
            "unit": "单位：万元",
            "table_type": "fact",
            "year_binding_required": True,
            "report_year": 2025,
            "preview": "资产负债表补充资料",
            "matched_financial_names": ["资产负债表"],
        },
        {
            "table_index": 2,
            "line": 20,
            "pdf_page_number": 11,
            "pdf_page_source": "content_list",
            "pdf_page_inference_reason": "exact_table",
            "bbox": [5, 6, 7, 8],
            "rows": 8,
            "cells": 24,
            "empty_ratio": 0.05,
            "numeric_ratio": 0.6,
            "heading": "合并资产负债表",
            "unit": "单位：万元",
            "table_type": "fact",
            "year_binding_required": True,
            "report_year": 2025,
            "preview": "流动资产 非流动资产 资产总计 负债和所有者权益",
            "matched_financial_names": ["资产负债表"],
        },
        {
            "table_index": 3,
            "line": 40,
            "pdf_page_number": 12,
            "pdf_page_source": "content_list",
            "pdf_page_inference_reason": "exact_table",
            "bbox": [],
            "rows": 5,
            "cells": 15,
            "empty_ratio": 0,
            "numeric_ratio": 0.5,
            "heading": "主要财务指标",
            "unit": "",
            "table_type": "fact",
            "year_binding_required": True,
            "report_year": 2025,
            "preview": "基本每股收益 净资产收益率",
            "matched_financial_names": ["主要财务指标"],
        },
    ]

    grouped = app._group_key_table_candidates(table_index)

    assert list(grouped)[:2] == ["主要财务指标", "资产负债表"]
    assert grouped["资产负债表"][0]["table_index"] == 2
    assert grouped["资产负债表"][0]["is_primary"] is True
    assert grouped["资产负债表"][1]["is_primary"] is False
    assert grouped["资产负债表"][0]["candidate_score"] > grouped["资产负债表"][1]["candidate_score"]
    assert grouped["主要财务指标"][0]["candidate_group"] == app._candidate_group("主要财务指标")
    assert grouped["主要财务指标"][0]["confidence"] == app._candidate_confidence(
        grouped["主要财务指标"][0]["candidate_score"]
    )


def test_markdown_page_index_tracks_marker_ranges_previews_and_printed_pages():
    markdown = (
        "[PDF_PAGE: 3]\n"
        "# 第一页\n"
        "<table><tr><td>营业收入</td><td>100</td></tr></table>\n"
        "[PDF_PAGE: 4]\n"
        "第二页正文\n"
        "<!-- PDF_PAGE: 5 -->\n"
        "第三页正文\n"
    )
    content_list = [
        {"type": "page_number", "page_idx": 2, "text": "1"},
        {"type": "page_number", "page_idx": 3, "text": "2"},
    ]

    page_index = app._markdown_page_index(markdown, content_list=content_list)

    assert [page["pdf_page_number"] for page in page_index] == [3, 4, 5]
    assert [page["printed_page_number"] for page in page_index] == ["1", "2", None]
    assert page_index[0]["start_line"] == 1
    assert page_index[0]["end_line"] == 3
    assert page_index[0]["preview"] == "# 第一页 营业收入 100"
    assert page_index[1]["start_line"] == 4
    assert page_index[1]["end_line"] == 5
    assert page_index[2]["start_line"] == 6
    assert page_index[2]["end_line"] == 7


def test_markdown_page_index_returns_empty_without_markers():
    assert app._markdown_page_index("# 无页码\n正文") == []
