from kr_quality_adapter import merge_kr_quality_candidates


def test_merge_kr_quality_candidates_promotes_medium_statement_with_financial_evidence():
    report = {
        "market": "KR",
        "report_kind": "kr_business_report",
        "table_index": [
            {
                "table_index": 197,
                "line": 3231,
                "pdf_page_number": 139,
                "heading": "연결 현금흐름표",
                "preview": "영업활동 현금흐름 투자활동 현금흐름 재무활동 현금흐름",
            }
        ],
        "key_table_candidates": {
            "Consolidated Statement of Cash Flows": [
                {
                    "name": "Consolidated Statement of Cash Flows",
                    "status": "found",
                    "table_index": 197,
                    "line": 3231,
                    "candidate_score": 86.0,
                    "confidence": "medium",
                    "candidate_group": "core",
                    "is_primary": True,
                    "_source": "kr_market_profile",
                }
            ]
        },
        "core_financial_table_candidates": [
            {"name": "Consolidated Statement of Cash Flows", "status": "found", "table_index": 197}
        ],
        "found_financial_tables": ["Consolidated Statement of Cash Flows"],
    }
    financial_data = {
        "market": "KR",
        "report_kind": "kr_business_report",
        "report_year": 2025,
        "statements": [
            {
                "statement_type": "cash_flow_statement",
                "table_indexes": [197],
                "items": [
                    {
                        "canonical_name": "cash_from_operating_activities",
                        "evidence": {
                            "table_index": 197,
                            "page_number": 139,
                            "raw": {
                                "table": {"table_index": 197, "line": 3231},
                            },
                        },
                    }
                ],
            }
        ],
    }

    merged = merge_kr_quality_candidates(report, financial_data)

    row = merged["key_table_candidates"]["Consolidated Statement of Cash Flows"][0]
    assert row["confidence"] == "high"
    assert row["_source"] == "financial_data_evidence"


def test_merge_kr_quality_candidates_preserves_nonblocking_summary_status():
    report = {
        "market": "KR",
        "report_kind": "kr_business_report",
        "table_index": [],
        "key_table_candidates": {},
        "core_financial_table_candidates": [
            {
                "name": "요약재무정보",
                "status": "not_applicable",
                "candidate_group": "core",
                "reason": "kr_summary_not_separately_presented",
                "display_note": "未单独定位到公司层面 요약재무정보；已排除附注、股东、子公司摘要。",
            },
            {"name": "Consolidated Statement of Cash Flows", "status": "missing", "candidate_group": "core"},
        ],
        "found_financial_tables": [],
    }
    financial_data = {
        "market": "KR",
        "report_kind": "kr_business_report",
        "report_year": 2025,
        "statements": [],
    }

    merged = merge_kr_quality_candidates(report, financial_data)

    by_name = {item["name"]: item for item in merged["core_financial_table_candidates"]}
    assert by_name["요약재무정보"]["status"] == "not_applicable"
    assert by_name["Consolidated Statement of Cash Flows"]["status"] == "missing"
