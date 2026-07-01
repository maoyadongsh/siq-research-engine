import json
from pathlib import Path

import pdf_parser_quality_service as quality


def test_quality_report_messages_cover_warning_and_info_rules():
    warnings, info_messages = quality.quality_report_messages(
        markdown_chars=100,
        pdf_page_count=10,
        table_count=5,
        single_row_table_count=2,
        image_ref_count=1,
        found_financial_table_count=1,
        suspicious_table_count=2,
    )

    assert any("Markdown 字符数相对页数偏少" in item for item in warnings)
    assert any("单行/空壳表格比例偏高" in item for item in warnings)
    assert any("财报核心表标题召回偏少" in item for item in warnings)
    assert any("发现 2 张可疑表样本" in item for item in warnings)
    assert info_messages == ["Markdown 包含图片引用，images 目录将作为 PDF 视觉元素与截图证据来源。"]


def test_quality_report_messages_keep_clean_inputs_quiet():
    warnings, info_messages = quality.quality_report_messages(
        markdown_chars=9000,
        pdf_page_count=10,
        table_count=5,
        single_row_table_count=1,
        image_ref_count=0,
        found_financial_table_count=3,
        suspicious_table_count=0,
    )

    assert warnings == []
    assert info_messages == []


def test_build_quality_report_payload_derives_counts_sections_and_warnings():
    tables = [
        "<table><tr><td>资产</td></tr></table>",
        "<table><tr><td>收入</td><td>100</td></tr><tr><td>利润</td><td>10</td></tr></table>",
    ]
    table_index = [
        {"table_index": 1, "table_type": "fact", "suspect_reasons": ["single_row"]},
        {"table_index": 2, "table_type": "dimension"},
    ]
    core_candidates = [
        {"name": "资产负债表", "status": "found"},
        {"name": "利润表", "status": "missing"},
        {"name": "现金流量表", "status": "missing"},
    ]
    payload = quality.build_quality_report_payload(
        task={"task_id": "quality-payload", "filename": "quality.pdf", "pdf_page_count": 10},
        filename="override.pdf",
        schema_version=7,
        report_kind="annual_report",
        report_year=2025,
        markdown_chars=100,
        tables=tables,
        table_index=table_index,
        single_row_tables=[tables[0]],
        empty_cell_count=3,
        image_refs=["images/chart.png"],
        found_sections=["董事会报告"],
        key_sections=["董事会报告", "财务报告"],
        key_table_candidates={"资产负债表": [{"table_index": 1}]},
        core_financial_table_candidates=core_candidates,
        indicator_table_candidates=[{"name": "营业收入", "status": "missing"}],
        suspicious_tables=[{"table_index": 1}],
        generated_at="2026-07-01T00:00:00Z",
    )

    assert payload["schema_version"] == 7
    assert payload["task_id"] == "quality-payload"
    assert payload["filename"] == "override.pdf"
    assert payload["report_kind"] == "annual_report"
    assert payload["report_year"] == 2025
    assert payload["markdown_chars"] == 100
    assert payload["table_count"] == 2
    assert payload["fact_table_count"] == 1
    assert payload["dimension_table_count"] == 1
    assert payload["single_row_table_count"] == 1
    assert payload["single_row_table_ratio"] == 0.5
    assert payload["empty_cell_count"] == 3
    assert payload["image_ref_count"] == 1
    assert payload["found_sections"] == ["董事会报告"]
    assert payload["missing_sections"] == ["财务报告"]
    assert payload["found_financial_tables"] == ["资产负债表"]
    assert payload["generated_at"] == "2026-07-01T00:00:00Z"
    assert any("Markdown 字符数相对页数偏少" in item for item in payload["warnings"])
    assert any("单行/空壳表格比例偏高" in item for item in payload["warnings"])
    assert any("财报核心表标题召回偏少" in item for item in payload["warnings"])
    assert any("优先复核表" in item for item in payload["warnings"])
    assert any("图片引用" in item for item in payload["info_messages"])


def test_build_quality_report_payload_handles_empty_tables_without_ratio_warning():
    payload = quality.build_quality_report_payload(
        task={"task_id": "empty-quality", "filename": "empty.pdf"},
        filename="empty.pdf",
        schema_version=7,
        report_kind="document",
        report_year=None,
        markdown_chars=0,
        tables=[],
        table_index=[],
        single_row_tables=[],
        empty_cell_count=0,
        image_refs=[],
        found_sections=[],
        key_sections=["财务报告"],
        key_table_candidates={},
        core_financial_table_candidates=[],
        indicator_table_candidates=[],
        suspicious_tables=[],
        generated_at="2026-07-01T00:00:00Z",
    )

    assert payload["table_count"] == 0
    assert payload["single_row_table_ratio"] == 0
    assert payload["warnings"] == ["财报核心表标题召回偏少，建议检查目录、财务报告章节或启用局部重解析。"]


def test_merge_quality_candidates_from_financial_data_uses_nearby_statement_table():
    report = {
        "report_kind": "annual_report",
        "key_table_candidates": {},
        "table_index": [
            {
                "table_index": 1,
                "line": 98,
                "pdf_page_number": 12,
                "pdf_page_source": "content_list",
                "pdf_page_inference_reason": "source_map",
                "bbox": [1, 2, 3, 4],
                "rows": 8,
                "cells": 32,
                "empty_ratio": 0,
                "numeric_ratio": 0.5,
                "heading": "合并资产负债表",
                "unit": "元",
                "table_type": "fact",
                "year_binding_required": True,
                "report_year": 2025,
                "preview": "流动资产 非流动资产 资产总计",
            }
        ],
    }
    financial_data = {
        "report_kind": "annual_report",
        "report_year": 2025,
        "statements": [
            {
                "statement_type": "balance_sheet",
                "scope": "consolidated",
                "line_numbers": [102],
                "table_indexes": [],
                "title": "合并资产负债表",
                "unit": "元",
            }
        ],
        "summary": {"statement_count": 1, "key_metric_count": 0},
    }

    merged = quality.merge_quality_candidates_from_financial_data(report, financial_data)
    balance_sheet = next(
        item
        for item in merged["core_financial_table_candidates"]
        if item["name"] == "资产负债表"
    )

    assert balance_sheet["status"] == "found"
    assert balance_sheet["table_index"] == 1
    assert balance_sheet["pdf_page_number"] == 12
    assert "资产负债表" in merged["found_financial_tables"]


def test_merge_quality_candidates_from_financial_data_keeps_existing_found_candidate():
    report = {
        "report_kind": "annual_report",
        "key_table_candidates": {
            "资产负债表": [
                {
                    "name": "资产负债表",
                    "status": "found",
                    "table_index": 9,
                    "line": 300,
                    "candidate_group": "core",
                    "candidate_score": 88.0,
                    "confidence": "medium",
                    "preview": "人工确认资产负债表",
                }
            ]
        },
        "table_index": [
            {"table_index": 1, "line": 98, "heading": "合并资产负债表"},
            {"table_index": 9, "line": 300, "heading": "人工确认资产负债表"},
        ],
    }
    financial_data = {
        "report_kind": "annual_report",
        "report_year": 2025,
        "statements": [
            {
                "statement_type": "balance_sheet",
                "scope": "consolidated",
                "table_indexes": [1],
                "line_numbers": [98],
                "title": "合并资产负债表",
            }
        ],
        "summary": {"statement_count": 1, "key_metric_count": 0},
    }

    merged = quality.merge_quality_candidates_from_financial_data(report, financial_data)
    balance_rows = merged["key_table_candidates"]["资产负债表"]
    balance_sheet = next(
        item
        for item in merged["core_financial_table_candidates"]
        if item["name"] == "资产负债表"
    )

    assert balance_rows == report["key_table_candidates"]["资产负债表"]
    assert balance_sheet["status"] == "found"
    assert balance_sheet["table_index"] == 9
    assert balance_sheet["line"] == 300
    assert balance_sheet["confidence"] == "medium"
    assert "资产负债表" in merged["found_financial_tables"]


def test_statement_table_index_without_line_uses_table_line_and_counts_found_table():
    report = {
        "report_kind": "annual_report",
        "key_table_candidates": {},
        "table_index": [
            {
                "table_index": 1,
                "line": 12,
                "heading": "合并利润表",
                "preview": "营业收入 营业利润 利润总额 净利润",
            }
        ],
    }
    statement = {
        "statement_type": "income_statement",
        "scope": "consolidated",
        "table_indexes": [1],
        "line_numbers": [],
        "title": "合并利润表",
        "unit": "元",
    }
    financial_data = {
        "report_kind": "annual_report",
        "report_year": 2025,
        "statements": [statement],
        "summary": {"statement_count": 1, "key_metric_count": 0},
    }

    display_source = quality.statement_display_source(statement, report, "income_statement")
    merged = quality.merge_quality_candidates_from_financial_data(report, financial_data)
    income_statement = next(
        item
        for item in merged["core_financial_table_candidates"]
        if item["name"] == "利润表"
    )

    assert display_source["line"] == 12
    assert income_statement["line"] == 12
    assert "利润表" in merged["found_financial_tables"]


def test_merge_quality_candidates_from_financial_data_backfills_equity_statement():
    report = {
        "report_kind": "annual_report",
        "key_table_candidates": {},
        "table_index": [
            {
                "table_index": 7,
                "line": 220,
                "pdf_page_number": 88,
                "pdf_page_source": "content_list",
                "pdf_page_inference_reason": "source_map",
                "bbox": [10, 20, 30, 40],
                "rows": 14,
                "cells": 98,
                "empty_ratio": 0.05,
                "numeric_ratio": 0.62,
                "heading": "合并所有者权益变动表",
                "unit": "元",
                "table_type": "fact",
                "preview": "归属于母公司所有者权益 少数股东权益 所有者权益合计",
            }
        ],
    }
    financial_data = {
        "report_kind": "annual_report",
        "report_year": 2025,
        "statements": [
            {
                "statement_type": "equity_statement",
                "scope": "consolidated",
                "line_numbers": [220],
                "table_indexes": [7],
                "title": "合并所有者权益变动表",
                "unit": "元",
            }
        ],
        "summary": {"statement_count": 4, "key_metric_count": 0},
    }

    merged = quality.merge_quality_candidates_from_financial_data(report, financial_data)
    equity_statement = next(
        item
        for item in merged["core_financial_table_candidates"]
        if item["name"] == "所有者权益变动表"
    )

    assert equity_statement["status"] == "found"
    assert equity_statement["table_index"] == 7
    assert equity_statement["line"] == 220
    assert equity_statement["pdf_page_number"] == 88
    assert equity_statement["_source"] == "financial_data"
    assert "所有者权益变动表" in merged["found_financial_tables"]


def test_merge_quality_candidates_from_financial_metrics_keeps_table_metadata():
    report = {
        "report_kind": "annual_report",
        "key_table_candidates": {},
        "table_index": [
            {
                "table_index": 3,
                "line": 56,
                "pdf_page_number": 18,
                "pdf_page_source": "content_list",
                "pdf_page_inference_reason": "source_map",
                "bbox": [5, 6, 7, 8],
                "rows": 6,
                "cells": 42,
                "empty_ratio": 0.02,
                "numeric_ratio": 0.7,
                "heading": "主要会计数据和财务指标",
                "unit": "元",
                "table_type": "fact",
                "preview": "营业收入 归属于上市公司股东的净利润 经营活动现金流量净额",
            }
        ],
    }
    financial_data = {
        "report_kind": "annual_report",
        "report_year": 2025,
        "statements": [],
        "key_metrics": [
            {
                "name": "营业收入",
                "canonical_name": "operating_revenue",
                "unit": "元",
                "sources": {
                    "2025": {
                        "table_index": 3,
                        "line": 57,
                    }
                },
            }
        ],
        "summary": {"statement_count": 0, "key_metric_count": 1},
    }

    merged = quality.merge_quality_candidates_from_financial_data(report, financial_data)
    accounting_data = next(
        item
        for item in merged["core_financial_table_candidates"]
        if item["name"] == "主要会计数据"
    )

    assert accounting_data["status"] == "found"
    assert accounting_data["table_index"] == 3
    assert accounting_data["line"] == 57
    assert accounting_data["pdf_page_number"] == 18
    assert accounting_data["bbox"] == [5, 6, 7, 8]
    assert accounting_data["rows"] == 6
    assert accounting_data["cells"] == 42
    assert accounting_data["heading"] == "主要会计数据和财务指标"
    assert accounting_data["preview"].startswith("营业收入")


def test_merge_quality_candidates_from_financial_metrics_keeps_existing_found_candidate():
    existing_rows = [
        {
            "name": "主要会计数据",
            "status": "found",
            "table_index": 9,
            "line": 300,
            "candidate_group": "core",
            "candidate_score": 86.0,
            "confidence": "medium",
            "preview": "人工确认主要会计数据",
        }
    ]
    report = {
        "report_kind": "annual_report",
        "key_table_candidates": {"主要会计数据": existing_rows},
        "table_index": [
            {"table_index": 3, "line": 56, "heading": "主要会计数据和财务指标"},
            {"table_index": 9, "line": 300, "heading": "人工确认主要会计数据"},
        ],
    }
    financial_data = {
        "report_kind": "annual_report",
        "report_year": 2025,
        "statements": [],
        "key_metrics": [
            {
                "name": "营业收入",
                "canonical_name": "operating_revenue",
                "sources": {"2025": {"table_index": 3, "line": 57}},
            }
        ],
        "summary": {"statement_count": 0, "key_metric_count": 1},
    }

    merged = quality.merge_quality_candidates_from_financial_data(report, financial_data)
    accounting_data = next(
        item
        for item in merged["core_financial_table_candidates"]
        if item["name"] == "主要会计数据"
    )

    assert merged["key_table_candidates"]["主要会计数据"] == existing_rows
    assert accounting_data["status"] == "found"
    assert accounting_data["table_index"] == 9
    assert accounting_data["line"] == 300
    assert accounting_data["confidence"] == "medium"


def test_balance_sheet_nearby_table_skips_average_balance_noise():
    report = {
        "table_index": [
            {
                "table_index": 1,
                "line": 90,
                "heading": "平均余额和平均收益率",
                "preview": "平均余额 平均收益率 生息资产 利息收入/支出",
            },
            {
                "table_index": 2,
                "line": 98,
                "heading": "合并资产负债表",
                "preview": "流动资产 非流动资产 资产总计",
            },
        ]
    }

    matched = quality.nearest_table_for_statement_lines(report, [100], "balance_sheet")

    assert matched["table_index"] == 2
    assert matched["line"] == 100


def test_statement_display_source_uses_nearby_balance_table_when_index_is_noise():
    report = {
        "table_index": [
            {
                "table_index": 1,
                "line": 90,
                "heading": "平均余额和平均收益率",
                "preview": "平均余额 平均收益率 生息资产 利息收入/支出",
            },
            {
                "table_index": 2,
                "line": 98,
                "heading": "合并资产负债表",
                "preview": "流动资产 非流动资产 资产总计",
            },
        ]
    }
    statement = {
        "statement_type": "balance_sheet",
        "scope": "consolidated",
        "table_indexes": [1],
        "line_numbers": [100],
        "title": "合并资产负债表",
    }

    display_source = quality.statement_display_source(statement, report, "balance_sheet")

    assert display_source["table_index"] == 2
    assert display_source["line"] == 100
    assert display_source["table_item"]["heading"] == "合并资产负债表"


def test_statement_display_source_keeps_valid_balance_index_over_nearby_table():
    report = {
        "table_index": [
            {
                "table_index": 1,
                "line": 100,
                "heading": "合并资产负债表",
                "preview": "流动资产 非流动资产 资产总计",
            },
            {
                "table_index": 2,
                "line": 118,
                "heading": "银行平均余额和收益率",
                "preview": "平均余额 平均收益率 生息资产 利息收入/支出",
            },
        ]
    }
    statement = {
        "statement_type": "balance_sheet",
        "scope": "consolidated",
        "table_indexes": [1],
        "line_numbers": [119],
        "title": "合并资产负债表",
    }

    display_source = quality.statement_display_source(statement, report, "balance_sheet")

    assert display_source["table_index"] == 1
    assert display_source["line"] == 119
    assert display_source["table_item"]["heading"] == "合并资产负债表"


def test_nearest_table_for_statement_lines_ignores_non_numeric_lines():
    report = {
        "table_index": [
            {
                "table_index": 2,
                "line": 98,
                "heading": "合并资产负债表",
                "preview": "流动资产 非流动资产 资产总计",
            },
        ]
    }

    assert quality.nearest_table_for_statement_lines(report, ["not-a-line"], "balance_sheet") is None


def test_required_core_financial_table_names_excludes_equity_for_quarterly_reports():
    quarterly_names = quality.required_core_financial_table_names("quarterly_report")
    annual_names = quality.required_core_financial_table_names("annual_report")

    assert "所有者权益变动表" not in quarterly_names
    assert "所有者权益变动表" in annual_names


def test_candidate_summary_list_preserves_primary_candidate_fields_and_missing_group():
    summary = quality.candidate_summary_list(
        {
            "资产负债表": [
                {
                    "table_index": 2,
                    "line": 98,
                    "candidate_group": "core",
                    "candidate_score": 98.5,
                    "confidence": "high",
                    "preview": "流动资产 非流动资产 资产总计",
                },
                {
                    "table_index": 3,
                    "line": 110,
                    "candidate_group": "core",
                    "candidate_score": 80.0,
                },
            ]
        },
        ["资产负债表", "利润表"],
    )

    assert summary[0] == {
        "name": "资产负债表",
        "status": "found",
        "table_index": 2,
        "line": 98,
        "candidate_group": "core",
        "candidate_score": 98.5,
        "confidence": "high",
        "preview": "流动资产 非流动资产 资产总计",
        "candidate_count": 2,
    }
    assert summary[1] == {
        "name": "利润表",
        "status": "missing",
        "candidate_group": "core",
    }


def test_priority_review_tables_dedupes_reasons_and_truncates_to_thirty():
    table_index = [
        {"table_index": 1, "heading": "合并资产负债表", "suspect_reasons": ["empty_rows"]},
        {"table_index": 2, "heading": "合并利润表"},
        *[
            {"table_index": value, "heading": f"可疑表 {value}", "suspect_reasons": ["thin_table"]}
            for value in range(3, 36)
        ],
    ]
    core_candidates = [
        {"status": "found", "table_index": 1, "confidence": "low"},
        {"status": "found", "table_index": 2, "confidence": "medium"},
        {"status": "missing", "table_index": 3, "confidence": "low"},
    ]
    key_table_candidates = {
        "资产负债表": [{"table_index": 1}],
        "现金流量表": [{"table_index": 3}],
    }

    priority = quality.priority_review_tables(table_index, core_candidates, key_table_candidates)

    assert len(priority) == 30
    assert [item["table_index"] for item in priority[:3]] == [1, 2, 3]
    assert priority[0]["suspect_reasons"] == ["empty_rows", "low_confidence_core_candidate"]
    assert priority[1]["suspect_reasons"] == ["medium_confidence_core_candidate"]
    assert priority[2]["suspect_reasons"] == ["thin_table"]
    assert 35 not in {item["table_index"] for item in priority}


def test_quality_report_warnings_filters_summary_core_table_noise():
    report = {
        "report_kind": "annual_report_summary",
        "warnings": [
            "财报核心表标题召回偏少，建议检查目录、财务报告章节或启用局部重解析。",
            "三大表缺失。",
            "其他提示。",
        ],
    }
    financial_data = {
        "key_metrics": [{"canonical_name": "operating_revenue"}],
        "summary": {"statement_count": 0},
    }

    warnings = quality.quality_report_warnings(report, financial_data)

    assert "其他提示。" in warnings
    assert not any(item.startswith("财报核心表标题召回偏少") for item in warnings)
    assert "三大表缺失。" not in warnings
    assert any("摘要模式" in item for item in warnings)
    assert any("摘要文件不提供完整三大表" in item for item in warnings)


def test_quality_report_warnings_filters_full_report_core_table_noise_when_statements_found():
    report = {
        "report_kind": "annual_report",
        "warnings": [
            "财报核心表标题召回偏少，建议检查目录、财务报告章节或启用局部重解析。",
            "核心表缺失。",
            "其他提示。",
        ],
    }
    financial_data = {
        "summary": {"statement_count": 3},
    }

    warnings = quality.quality_report_warnings(report, financial_data)

    assert warnings == ["其他提示。"]


def test_write_and_read_quality_report_files_round_trip(tmp_path):
    task = {"task_id": "task-quality"}
    report = {
        "schema_version": 11,
        "task_id": "task-quality",
        "table_index": [{"table_index": 1}],
    }

    def result_dir(value):
        return str(tmp_path / value["task_id"])

    def write_json(path, payload):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def read_json(path):
        path = Path(path)
        return json.loads(path.read_text(encoding="utf-8"))

    quality.write_quality_report_files(task, report, result_dir, write_json)

    assert quality.read_quality_report(task, result_dir, read_json) == report
    table_index = json.loads((tmp_path / "task-quality" / "table_index.json").read_text(encoding="utf-8"))
    assert table_index == [{"table_index": 1}]
