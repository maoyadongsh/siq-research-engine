from __future__ import annotations

import pdf_parser_quality_service as quality


def test_merge_quality_candidates_uses_hk_statement_labels_for_hk_financial_data():
    report = {
        "report_kind": "annual_report",
        "key_table_candidates": {},
        "table_index": [
            {"table_index": 10, "line": 500, "pdf_page_number": 120, "heading": "Consolidated Statement of Financial Position", "rows": 20, "cells": 80, "preview": "Total assets Total liabilities Net assets"},
            {"table_index": 11, "line": 620, "pdf_page_number": 121, "heading": "Consolidated Statement of Profit or Loss", "rows": 18, "cells": 72, "preview": "Revenue Profit for the year"},
            {"table_index": 12, "line": 730, "pdf_page_number": 122, "heading": "Consolidated Statement of Cash Flows", "rows": 18, "cells": 72, "preview": "Net cash generated from operating activities"},
            {"table_index": 20, "line": 800, "pdf_page_number": 130, "heading": "Portfolio Occupancy", "rows": 8, "cells": 24, "preview": "Occupancy Rate 98.0%"},
        ],
    }
    financial_data = {
        "market": "HK",
        "accounting_standard": "HKFRS",
        "industry_profile": "real_estate",
        "report_kind": "annual_report",
        "report_year": 2026,
        "statements": [
            {"statement_type": "balance_sheet", "scope": "consolidated", "table_indexes": [10], "line_numbers": [500], "statement_name": "Balance Sheet", "unit": "HK$M"},
            {"statement_type": "income_statement", "scope": "consolidated", "table_indexes": [11], "line_numbers": [620], "statement_name": "Income Statement", "unit": "HK$M"},
            {"statement_type": "cash_flow_statement", "scope": "consolidated", "table_indexes": [12], "line_numbers": [730], "statement_name": "Cash Flow Statement", "unit": "HK$M"},
        ],
        "operating_metrics": [
            {"canonical_name": "occupancy_rate", "evidence": {"table_index": 20}, "unit": "%"},
        ],
        "summary": {"statement_count": 3, "key_metric_count": 0, "operating_metric_count": 1},
    }

    merged = quality.merge_quality_candidates_from_financial_data(report, financial_data)

    assert merged["market"] == "HK"
    assert merged["accounting_standard"] == "HKFRS"
    assert merged["industry_profile"] == "real_estate"
    names = [item["name"] for item in merged["core_financial_table_candidates"]]
    assert "Statement of Financial Position" in names
    assert "Statement of Profit or Loss" in names
    assert "Statement of Cash Flows" in names
    assert "资产负债表" not in names
    assert "主要会计数据" not in merged["key_table_candidates"]
    assert "Occupancy Rate" in merged["hk_key_table_candidates"]
    assert [item["name"] for item in merged["indicator_table_candidates"]] == ["Occupancy Rate"]


def test_merge_quality_candidates_falls_back_to_hk_table_locator_for_missing_core_tables():
    report = {
        "report_kind": "annual_report",
        "key_table_candidates": {},
        "table_index": [
            {
                "table_index": 20,
                "line": 1664,
                "pdf_page_number": 75,
                "heading": "Attributable to owners of the parent",
                "preview": "Notes Share capital RMB'000 Share premium account Treasury shares Capital reserve "
                "Retained profits Total At 1 January 2025 Profit for the year Other comprehensive income "
                "Dividends At 31 December 2025",
            },
            {
                "table_index": 22,
                "line": 1683,
                "pdf_page_number": 78,
                "heading": "Year ended 31 December 2025",
                "preview": "Notes 2025 RMB'000 2024 RMB'000 CASH FLOWS FROM OPERATING ACTIVITIES "
                "Profit before tax Adjustments for Finance costs Cash generated from operations",
            },
            {
                "table_index": 23,
                "line": 1690,
                "pdf_page_number": 79,
                "heading": "Year ended 31 December 2025",
                "preview": "Notes 2025 RMB'000 2024 RMB'000 CASH FLOWS FROM INVESTING ACTIVITIES "
                "Interest received Dividends received Purchase of items of property plant and equipment",
            },
        ],
    }
    financial_data = {
        "market": "HK",
        "accounting_standard": "HKFRS",
        "report_kind": "annual_report",
        "report_year": 2025,
        "statements": [],
    }

    merged = quality.merge_quality_candidates_from_financial_data(report, financial_data)
    by_name = {item["name"]: item for item in merged["core_financial_table_candidates"]}

    assert by_name["Statement of Cash Flows"]["status"] == "found"
    assert by_name["Statement of Cash Flows"]["table_index"] == 22
    assert by_name["Statement of Cash Flows"]["_source"] == "hk_table_locator"
    assert by_name["Statement of Changes in Equity"]["status"] == "found"
    assert by_name["Statement of Changes in Equity"]["table_index"] == 20
    assert by_name["Statement of Changes in Equity"]["_source"] == "hk_table_locator"
