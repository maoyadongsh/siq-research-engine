import json
from pathlib import Path

import pdf_parser_quality_service as quality


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
