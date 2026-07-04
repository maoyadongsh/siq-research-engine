import jp_market_profile as jp


def test_detect_market_prefers_explicit_task_market_and_filename():
    assert jp.JP_PROFILE_RULE_VERSION == "jp-pdf-profile-v3"
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


def test_jp_candidate_groups_use_long_signal_for_truncated_statement_previews():
    table_index = [
        {
            "table_index": 1,
            "heading": "</details>",
            "preview": "Millions of Yen 2025 2024 Net Sales Operating Income",
            "signal_preview": "Millions of Yen 2025 2024 Net Sales Operating Income Net Income Amounts Per Common Share Total Assets",
        },
        {
            "table_index": 2,
            "heading": "YEAR ENDED MARCH 20, 2025",
            "preview": "ASSETS: CURRENT ASSETS Cash and cash equivalents Time deposits Marketable securities",
            "signal_preview": "ASSETS: CURRENT ASSETS Cash and cash equivalents Time deposits Marketable securities Total assets LIABILITIES AND NET ASSETS Total liabilities Total equity",
        },
        {
            "table_index": 3,
            "heading": "OPERATING ACTIVITIES:",
            "preview": "Income before income taxes Adjustments for",
            "signal_preview": "OPERATING ACTIVITIES: Income before income taxes Adjustments for Income taxes paid Depreciation and amortization INVESTING ACTIVITIES FINANCING ACTIVITIES",
        },
        {
            "table_index": 4,
            "heading": "YEAR ENDED MARCH 20, 2025",
            "preview": "Outstanding number of shares of common stock Common stock Capital surplus Retained earnings Treasury stock",
            "signal_preview": "Outstanding number of shares of common stock Common stock Capital surplus Retained earnings Treasury stock Accumulated other comprehensive income Total equity",
        },
    ]

    candidates = jp.group_jp_key_table_candidates(table_index)

    assert candidates["Financial Highlights"][0]["table_index"] == 1
    assert candidates["Consolidated Statement of Financial Position"][0]["table_index"] == 2
    assert candidates["Consolidated Statement of Cash Flows"][0]["table_index"] == 3
    assert candidates["Consolidated Statement of Changes in Equity"][0]["table_index"] == 4


def test_jp_candidate_groups_find_integrated_report_financial_summary_tables():
    table_index = [
        {
            "table_index": 1,
            "heading": "For the year",
            "signal_preview": (
                "For the year Revenue 1,786,473 1,861,917 2,130,060 2,290,548 "
                "Business profit 162,041 184,034 252,459 265,166 "
                "Operating profit 127,292 176,414 236,212 257,636 "
                "Profit attributable to owners of the parent 48,052 119,280 154,811 162,578 "
                "Net cash generated by operating activities 98,755 212,168 176,403 300,505"
            ),
        },
        {
            "table_index": 2,
            "heading": "At year-end",
            "signal_preview": (
                "At year-end Total assets 1,238,119 1,388,486 1,953,466 2,010,558 "
                "Total equity 597,661 762,043 902,777 983,534 "
                "Interest-bearing debt 283,465 281,512 544,502 513,405"
            ),
        },
        {
            "table_index": 3,
            "heading": "Billions of JPY",
            "signal_preview": (
                "FY2015 FY2016 FY2017 FY2018 FY2019 FY2020 FY2021 FY2022 FY2023 FY2024 "
                "Financial results Revenue 986.4 955.1 960.2 929.7 981.8 962.5 1044.9 1278.5 1601.7 1886.3 "
                "Operating profit 130.4 88.9 76.3 83.7 138.8 63.8 73.0 120.6 211.6 331.9 "
                "Profit attributable to owners of the company 82.3 53.5 60.3 93.4 129.1 76.0 67.0 109.2 200.7 295.8"
            ),
        },
    ]

    candidates = jp.group_jp_key_table_candidates(table_index)

    assert candidates["Financial Highlights"][0]["table_index"] == 3
    assert {row["table_index"] for row in candidates["Financial Highlights"]} >= {1, 3}
    assert candidates["Consolidated Statement of Financial Position"][0]["table_index"] == 2


def test_jp_candidate_groups_treat_revenues_as_profit_or_loss_signal():
    table_index = [
        {
            "table_index": 1,
            "signal_preview": (
                "Million yen Fiscal year IFRS FY2015 FY2016 FY2017 FY2018 FY2019 FY2020 FY2021 FY2022 FY2023 FY2024 "
                "Revenues 10,034,305 9,162,264 9,368,614 9,480,619 8,767,263 8,729,196 10,264,602 10,881,150 9,728,716 9,783,370 "
                "Adjusted operating income 634,869 586,052 714,630 754,976 661,883 649,506 738,236 748,144 755,816 764,301 "
                "Net income attributable to stockholders 172,155 231,261 362,988 222,546 87,501 501,613 583,470 649,124 589,861 615,731"
            ),
        }
    ]

    candidates = jp.group_jp_key_table_candidates(table_index)

    assert candidates["Consolidated Statement of Profit or Loss"][0]["table_index"] == 1


def test_jp_candidate_groups_find_bank_summary_and_balance_sheet_data():
    table_index = [
        {
            "table_index": 1,
            "heading": "MUFG (consolidated)",
            "signal_preview": (
                "FY2023 FY2024 results results change Gross profits 4,732.5 4,819.3 86.7 "
                "Net operating profits 1,843.7 1,591.1 -252.5 "
                "Ordinary profits 2,127.9 2,669.4 541.5 "
                "Profits attributable to owners of parent 1,490.7 1,862.9 372.1"
            ),
        },
        {
            "table_index": 2,
            "heading": "Balance sheet data",
            "signal_preview": (
                "Balance sheet data Total assets Loans and bills discounted Securities "
                "Total liabilities Deposits Total net assets"
            ),
        },
        {
            "table_index": 3,
            "heading": "Financial indicators",
            "signal_preview": (
                "Financial indicators Total assets Total net assets Total equity "
                "Equity attributable to owners of the parent ratio Return on assets"
            ),
        },
    ]

    candidates = jp.group_jp_key_table_candidates(table_index)

    assert candidates["Financial Highlights"][0]["table_index"] == 1
    assert candidates["Consolidated Statement of Financial Position"][0]["table_index"] == 2
    assert {row["table_index"] for row in candidates["Consolidated Statement of Financial Position"]} >= {2, 3}


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
