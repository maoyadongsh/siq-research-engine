from jp_quality_adapter import merge_jp_quality_candidates


def test_merge_jp_quality_candidates_backfills_statement_locations_from_financial_data():
    report = {
        "market": "JP",
        "report_kind": "jp_annual_securities_report",
        "table_index": [
            {
                "table_index": 85,
                "line": 2225,
                "pdf_page_number": 100,
                "heading": "①【連結財政状態計算書】",
                "preview": "資産合計 負債合計 資本合計",
            }
        ],
        "key_table_candidates": {},
        "core_financial_table_candidates": [
            {"name": "Financial Highlights", "status": "missing", "candidate_group": "core"},
            {"name": "Consolidated Statement of Financial Position", "status": "missing", "candidate_group": "core"},
        ],
        "found_financial_tables": [],
    }
    financial_data = {
        "market": "JP",
        "report_kind": "jp_annual_securities_report",
        "report_year": 2025,
        "statements": [
            {
                "statement_type": "balance_sheet",
                "table_indexes": [85],
                "items": [
                    {
                        "canonical_name": "total_assets",
                        "evidence": {
                            "table_index": 85,
                            "page_number": 100,
                            "raw": {
                                "detected_statement_type": "balance_sheet",
                                "table": {"table_index": 85, "line": 2225, "source": "edinet_pdf_statement_table"},
                            },
                        },
                    }
                ],
            }
        ],
    }

    merged = merge_jp_quality_candidates(report, financial_data)

    by_name = {item["name"]: item for item in merged["core_financial_table_candidates"]}
    assert by_name["Financial Highlights"]["status"] == "missing"
    assert by_name["Consolidated Statement of Financial Position"]["status"] == "found"
    assert by_name["Consolidated Statement of Financial Position"]["table_index"] == 85
    assert merged["key_table_candidates"]["Consolidated Statement of Financial Position"][0]["_source"] == "financial_data_evidence"
