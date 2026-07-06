import jp_market_profile as jp


def test_detect_market_prefers_explicit_task_market_and_filename():
    assert jp.JP_PROFILE_RULE_VERSION == "jp-pdf-profile-v7"
    assert jp.is_jp_market({"submit_config": {"market": "JP"}}, "anything.pdf")
    assert jp.is_jp_market({"market": "jp"}, "anything.pdf")
    assert jp.is_jp_market({}, "Toyota-Motor-Corporation_JP_7203_2025.pdf")
    assert not jp.is_jp_market({"submit_config": {"market": "CN"}}, "Toyota-Motor-Corporation_JP_7203_2025.pdf")


def test_detect_jp_report_kind_distinguishes_annual_securities_and_integrated_reports():
    assert jp.detect_jp_report_kind("[Document Filed] Annual Securities Report\nFinancial Instruments and Exchange Act") == "jp_annual_securities_report"
    assert jp.detect_jp_report_kind("有価証券報告書\n第一部【企業情報】") == "jp_annual_securities_report"
    assert jp.detect_jp_report_kind("Integrated Report\nValue Creation\nMateriality") == "jp_integrated_report"
    assert jp.detect_jp_report_kind("FINANCIAL HIGHLIGHTS\nNet sales Operating profit") == "jp_financial_highlights_only"
    assert jp.detect_jp_report_kind("2025 Integrated Report\nWebsite: Annual Securities Report") == "jp_integrated_report"


def test_core_financial_table_names_are_report_kind_aware():
    assert jp.core_financial_table_names_for_report("jp_annual_securities_report") == jp.JP_FORMAL_CORE_FINANCIAL_TABLE_NAMES
    assert jp.core_financial_table_names_for_report("jp_integrated_report") == jp.JP_SUMMARY_CORE_FINANCIAL_TABLE_NAMES


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


def test_jp_candidate_groups_find_annual_securities_report_local_highlights():
    table_index = [
        {
            "table_index": 2,
            "line": 15,
            "pdf_page_number": 2,
            "heading": "（1）連結経営指標等",
            "signal_preview": (
                "回次 第117期 第118期 第119期 第120期 第121期 決算期 2021年3月 2022年3月 "
                "営業収益 (百万円) 27,214,594 31,379,507 "
                "税引前利益 (百万円) 2,932,354 3,990,532 "
                "親会社の所有者に帰属する当期利益 (百万円) 2,245,261 2,850,110 "
                "総資産額 (百万円) 62,267,140 67,688,771 1株当たり当期利益"
            ),
        },
        {
            "table_index": 12,
            "line": 479,
            "heading": "＜経営指標／長期経営計画2030ベース（2020年1月公表）＞",
            "signal_preview": "ROA ROE EPS 長計目標 2025年度業績予想",
        },
    ]

    candidates = jp.group_jp_key_table_candidates(table_index, report_kind="jp_annual_securities_report")

    assert candidates["Financial Highlights"][0]["table_index"] == 2
    assert candidates["Financial Highlights"][0]["confidence"] == "high"


def test_jp_formal_local_statement_titles_are_high_confidence():
    table_index = [
        {
            "table_index": 10,
            "heading": "連結財政状態計算書",
            "signal_preview": "資産 流動資産 非流動資産 資産合計 負債 資本合計",
        },
        {
            "table_index": 11,
            "heading": "連結損益計算書",
            "signal_preview": "売上収益 営業利益 税引前利益 当期利益",
        },
        {
            "table_index": 12,
            "heading": "連結包括利益計算書",
            "signal_preview": "当期利益 その他の包括利益 当期包括利益",
        },
        {
            "table_index": 13,
            "heading": "連結キャッシュ・フロー計算書",
            "signal_preview": "営業活動によるキャッシュ・フロー 投資活動によるキャッシュ・フロー 財務活動によるキャッシュ・フロー",
        },
    ]

    candidates = jp.group_jp_key_table_candidates(table_index, report_kind="jp_annual_securities_report")

    assert candidates["Consolidated Statement of Financial Position"][0]["confidence"] == "high"
    assert candidates["Consolidated Statement of Profit or Loss"][0]["confidence"] == "high"
    assert candidates["Consolidated Statement of Comprehensive Income"][0]["confidence"] == "high"
    assert candidates["Consolidated Statement of Cash Flows"][0]["confidence"] == "high"


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
    assert "Consolidated Statement of Financial Position" not in candidates
    assert candidates["Total Assets"][0]["table_index"] == 2


def test_jp_candidate_groups_do_not_treat_multi_year_metrics_as_profit_or_loss_statement():
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

    assert "Consolidated Statement of Profit or Loss" not in candidates
    assert candidates["Revenue"][0]["table_index"] == 1


def test_jp_candidate_groups_keep_bank_summary_data_out_of_core_statement_coverage():
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
    assert "Consolidated Statement of Financial Position" not in candidates
    assert {row["table_index"] for row in candidates["Total Assets"]} >= {2, 3}


def test_jp_candidate_groups_do_not_promote_integrated_financial_highlights_to_core_statements():
    table_index = [
        {
            "table_index": 55,
            "heading": "IFRS",
            "signal_preview": (
                "2014 2015 2016 2017 2018 For the year: Revenue [Net sales] "
                "Operating profit Profit before tax Profit attributable to owners of parent "
                "Net cash provided by operating activities Net cash used in investing activities "
                "Free cash flow At year-end: Total assets Total equity [Net assets] "
                "Interest-bearing liabilities Ratios: Operating profit margin ROE"
            ),
        },
    ]

    candidates = jp.group_jp_key_table_candidates(table_index)

    assert candidates["Financial Highlights"][0]["table_index"] == 55
    assert candidates["Revenue"][0]["table_index"] == 55
    assert "Consolidated Statement of Financial Position" not in candidates
    assert "Consolidated Statement of Profit or Loss" not in candidates
    assert "Consolidated Statement of Cash Flows" not in candidates


def test_jp_candidate_groups_do_not_promote_multi_year_cash_flow_summary_to_cash_flow_statement():
    table_index = [
        {
            "table_index": 17,
            "heading": "",
            "signal_preview": (
                "Cash Flows Japanese GAAP IFRS Unit 2014 2015 2016 2017 2018 2019 2020 2021 2022 2023 2024 "
                "Cash flows from operating activities Cash flows from investing activities Free cash flow"
            ),
        }
    ]

    candidates = jp.group_jp_key_table_candidates(table_index)

    assert "Consolidated Statement of Cash Flows" not in candidates


def test_jp_candidate_groups_find_local_changes_in_equity_table_without_english_title():
    table_index = [
        {
            "table_index": 120,
            "line": 2814,
            "pdf_page_number": 149,
            "heading": "（単位：百万円）",
            "signal_preview": (
                "親会社の所有者に帰属する持分 資本金 資本剩余金 自己株式 利益剩余金 "
                "その他の資本の構成要素 非支配持分 資本合計 2024年4月1日残高 "
                "当期利益 その他の包括利益 当期包括利益 その他の変動"
            ),
        },
        {
            "table_index": 91,
            "line": 1923,
            "heading": "（4）【取得自己株式の処理状況及び保有状況】",
            "signal_preview": "区分 当事業年度 当期間 株式数 処分価額の総額 保有自己株式数",
        },
    ]

    candidates = jp.group_jp_key_table_candidates(table_index, report_kind="jp_annual_securities_report")

    assert candidates["Consolidated Statement of Changes in Equity"][0]["table_index"] == 120
    assert candidates["Consolidated Statement of Changes in Equity"][0]["confidence"] == "high"


def test_jp_candidate_groups_find_consolidated_operating_results_as_financial_highlights():
    table_index = [
        {
            "table_index": 14,
            "heading": "Fiscal years ended December 31",
            "signal_preview": (
                "Consolidated Operating Results Japanese GAAP IFRS Unit 2014 2015 2016 2017 2018 2019 2020 2021 2022 2023 2024 "
                "Revenue Billion JPY"
            ),
        }
    ]

    candidates = jp.group_jp_key_table_candidates(table_index)

    assert candidates["Financial Highlights"][0]["table_index"] == 14
    assert "Consolidated Statement of Profit or Loss" not in candidates


def test_jp_candidate_groups_do_not_promote_strategy_capital_tables_to_core_statements():
    table_index = [
        {
            "table_index": 13,
            "heading": "The Six Capitals and Measures to Strengthen Them",
            "signal_preview": (
                "Human capital Financial capital Key monitoring indicators "
                "Implementing balance sheet-driven management Improving cash flows from operating activities "
                "ROE ROIC Cash generation Debt to equity ratio"
            ),
        },
        {
            "table_index": 24,
            "heading": "Ideal Vision",
            "signal_preview": (
                "Target Actual Financial Targets Consolidated revenue Operating profit margin "
                "ROE Bonds and borrowings to total assets"
            ),
        },
    ]

    candidates = jp.group_jp_key_table_candidates(table_index)

    assert "Consolidated Statement of Financial Position" not in candidates
    assert "Consolidated Statement of Profit or Loss" not in candidates
    assert "Consolidated Statement of Cash Flows" not in candidates


def test_jp_candidate_groups_do_not_promote_shareholder_return_footnotes_to_comprehensive_income():
    table_index = [
        {
            "table_index": 34,
            "heading": "</details>",
            "signal_preview": (
                "Past 10 years Past 5 years Past 3 years Past 1 year Total Shareholder Return "
                "Mitsubishi Electric 217.7% TOPIX 217.4% "
                "Adjusted dividend on equity ratio excludes accumulated other comprehensive income (loss)"
            ),
        }
    ]

    candidates = jp.group_jp_key_table_candidates(table_index)

    assert "Consolidated Statement of Comprehensive Income" not in candidates


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
