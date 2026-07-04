import jp_market_profile as jp


def test_detect_market_prefers_explicit_task_market_and_filename():
    assert jp.is_jp_market({"submit_config": {"market": "JP"}}, "anything.pdf")
    assert jp.is_jp_market({"market": "jp"}, "anything.pdf")
    assert jp.is_jp_market({}, "Toyota-Motor-Corporation_JP_7203_2025.pdf")
    assert not jp.is_jp_market({"submit_config": {"market": "CN"}}, "Toyota-Motor-Corporation_JP_7203_2025.pdf")


def test_detect_jp_report_kind_distinguishes_annual_securities_and_integrated_reports():
    assert jp.detect_jp_report_kind("[Document Filed] Annual Securities Report\nFinancial Instruments and Exchange Act") == "jp_annual_securities_report"
    assert jp.detect_jp_report_kind("有価証券報告書\n第一部【企業情報】") == "jp_annual_securities_report"
    assert jp.detect_jp_report_kind("Integrated Report\nValue Creation\nMateriality") == "jp_integrated_report"
    assert jp.detect_jp_report_kind("FINANCIAL HIGHLIGHTS\nNet sales Operating profit") == "jp_financial_highlights_only"


def test_jp_candidate_groups_find_financial_highlights_and_statements():
    table_index = [
        {"table_index": 1, "line": 10, "preview": "FINANCIAL HIGHLIGHTS Millions of yen 2025 2024 Net sales Operating profit Net income"},
        {"table_index": 2, "line": 30, "preview": "YEAR ENDED MARCH 20, 2025 ASSETS CURRENT ASSETS Cash and cash equivalents TOTAL ASSETS LIABILITIES AND NET ASSETS"},
        {"table_index": 3, "line": 50, "preview": "NET SALES COSTS AND EXPENSES Operating income Income before income taxes Net income"},
        {"table_index": 4, "line": 70, "preview": "CASH FLOWS FROM OPERATING ACTIVITIES Net cash provided by operating activities Cash and cash equivalents at end of year"},
        {"table_index": 5, "line": 90, "preview": "CONSOLIDATED STATEMENT OF CHANGES IN EQUITY Share capital Retained earnings"},
        {"table_index": 6, "line": 110, "preview": "SEGMENT INFORMATION Revenue Operating profit by segment"},
    ]

    candidates = jp.group_jp_key_table_candidates(table_index)

    assert candidates["Financial Highlights"][0]["table_index"] == 1
    assert candidates["Consolidated Statement of Financial Position"][0]["table_index"] == 2
    assert candidates["Consolidated Statement of Profit or Loss"][0]["table_index"] == 3
    assert candidates["Consolidated Statement of Cash Flows"][0]["table_index"] == 4
    assert candidates["Consolidated Statement of Changes in Equity"][0]["table_index"] == 5
    assert candidates["Segment Information"][0]["table_index"] == 6
    assert all(row["candidate_group"] in {"core", "indicator"} for rows in candidates.values() for row in rows)


def test_jp_quality_messages_do_not_use_a_share_core_table_warning_for_integrated_reports():
    warnings, info = jp.jp_quality_report_messages(
        report_kind="jp_integrated_report",
        table_count=10,
        single_row_table_count=0,
        image_ref_count=2,
        found_core_table_count=0,
        suspicious_table_count=0,
    )

    assert not any("三大表" in item or "财报核心表" in item for item in warnings)
    assert any("Integrated Report" in item for item in warnings)
    assert any("图片引用" in item for item in info)
