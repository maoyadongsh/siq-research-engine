import eu_market_profile as eu


def test_detect_market_prefers_explicit_task_market_and_filename():
    assert eu.EU_PROFILE_RULE_VERSION == "eu-pdf-profile-v7"
    assert eu.is_eu_market({"submit_config": {"market": "EU"}}, "anything.pdf")
    assert eu.is_eu_market({"market": "eu"}, "anything.pdf")
    assert eu.is_eu_market({}, "London-Stock-Exchange-Group-plc_EU_LSEG_2025-12-31_annual.pdf")
    assert eu.is_eu_market({}, "London-Stock-Exchange-Group-plc_issuer_annual_report.pdf")
    assert not eu.is_eu_market({"submit_config": {"market": "CN"}}, "Company_EU_2025.pdf")


def test_eu_candidate_groups_find_ifrs_sections_and_core_statements():
    markdown = """
    Strategic Report
    Governance
    Financial Statements
    Independent Auditor's Report
    Notes to the consolidated financial statements
    """
    table_index = [
        {
            "table_index": 1,
            "line": 10,
            "heading": "Group highlights",
            "preview": "Revenue 9,081 Operating profit 2,127 Basic earnings per share 238.4p Operating cash flow 3,622",
            "rows": 8,
            "numeric_ratio": 0.7,
        },
        {
            "table_index": 2,
            "line": 30,
            "heading": "Consolidated income statement",
            "preview": "Revenue Operating profit Profit before tax Income tax expense Profit for the year",
            "rows": 30,
            "numeric_ratio": 0.6,
        },
        {
            "table_index": 3,
            "line": 50,
            "heading": "Consolidated statement of comprehensive income",
            "preview": "Profit for the year Other comprehensive income Total comprehensive income",
            "rows": 22,
            "numeric_ratio": 0.4,
        },
        {
            "table_index": 4,
            "line": 70,
            "heading": "Consolidated balance sheet",
            "preview": "Assets Total assets Liabilities Total liabilities Net assets Equity Total equity",
            "rows": 52,
            "numeric_ratio": 0.55,
        },
        {
            "table_index": 5,
            "line": 90,
            "heading": "Attributable to equity holders",
            "preview": "Ordinary share capital Share premium Retained earnings Non-controlling interests Total equity",
            "rows": 29,
            "numeric_ratio": 0.48,
        },
        {
            "table_index": 6,
            "line": 110,
            "heading": "Consolidated cash flow statement",
            "preview": "Operating activities Net cash flows from operating activities Investing activities Financing activities Cash and cash equivalents at 31 December",
            "rows": 51,
            "numeric_ratio": 0.53,
        },
    ]

    candidates = eu.group_eu_key_table_candidates(table_index)

    found_sections = eu.found_sections(markdown, table_index)
    assert "Strategic Report" in found_sections
    assert "Governance" in found_sections
    assert "Financial Statements" in found_sections
    assert candidates["Financial Highlights"][0]["table_index"] == 1
    assert candidates["Consolidated Income Statement"][0]["table_index"] == 2
    assert candidates["Consolidated Statement of Comprehensive Income"][0]["table_index"] == 3
    assert candidates["Consolidated Statement of Financial Position"][0]["table_index"] == 4
    assert candidates["Consolidated Statement of Changes in Equity"][0]["table_index"] == 5
    assert candidates["Consolidated Statement of Cash Flows"][0]["table_index"] == 6
    assert all(row["_source"] == "eu_market_profile" for rows in candidates.values() for row in rows)


def test_eu_financial_highlights_key_figures_require_financial_context():
    table_index = [
        {
            "table_index": 24,
            "line": 2741,
            "heading": "Key Figures",
            "preview": "(In € million) 2025 2024 Revenue 73,420 69,230 EBIT Adjusted 7,128 5,354 EBIT (reported) 6,082 5,304 Net Income 5,221 4,232 Free Cash Flow 4,753 4,461",
            "rows": 8,
            "numeric_ratio": 0.7,
        },
        {
            "table_index": 63,
            "line": 4247,
            "heading": "Climate metrics",
            "preview": "Key figures Unit 2025 2024 Net revenue used to calculate GHG intensity million EUR 73,420 Total emissions tonnes 388,132",
            "rows": 6,
            "numeric_ratio": 0.6,
        },
        {
            "table_index": 42,
            "line": 6435,
            "heading": "Board of Management remuneration",
            "preview": "Key figures Net sales Net income CEO pay ratio performance shares",
            "rows": 5,
            "numeric_ratio": 0.5,
        },
    ]

    candidates = eu.group_eu_key_table_candidates(table_index)

    assert candidates["Financial Highlights"][0]["table_index"] == 24
    assert all(row["table_index"] not in {42, 63} for row in candidates["Financial Highlights"])


def test_eu_financial_highlights_accept_market_specific_summary_titles():
    table_index = [
        {
            "table_index": 15,
            "heading": "Deutsche Borse Group: five-year overview",
            "preview": "2021 2022 2023 2024 2025 Consolidated income statement Net revenue less treasury result from banking and similar business EURm 3,367 3,805 4,115 4,779 5,189 EBITDA 2,600",
            "rows": 12,
            "numeric_ratio": 0.7,
        },
        {
            "table_index": 2,
            "heading": "Consolidated results",
            "preview": "2025 2024 Change Profit or loss in EUR million Commercial net interest income 15,316 15,459 Other net interest income Net fee and commission income 4,602",
            "rows": 18,
            "numeric_ratio": 0.65,
        },
        {
            "table_index": 35,
            "heading": "Key elements of financial performance in 2025",
            "preview": "Reported $m Actual growth Core $m Gross profit 48,106 Total revenue 59,000 Operating profit 14,000",
            "rows": 10,
            "numeric_ratio": 0.55,
        },
    ]

    candidates = eu.group_eu_key_table_candidates(table_index)

    assert {row["table_index"] for row in candidates["Financial Highlights"][:3]} == {2, 15, 35}


def test_eu_financial_highlights_reject_note_segment_and_remuneration_tables():
    table_index = [
        {
            "table_index": 96,
            "heading": "3. Total income and contract liabilities continued",
            "preview": "During 2025 some revenue items were reallocated between business lines. The impact on previously reported 2024 results is revenue of GBP 158 million. Segment information continued.",
            "rows": 20,
            "numeric_ratio": 0.6,
        },
        {
            "table_index": 44,
            "heading": "LTI 2020 - Tranche 2022 - Performance Factor",
            "preview": "Financial performance factor Cloud revenue Total revenue Operating profit Final number of financial PSUs Stock Awards",
            "rows": 8,
            "numeric_ratio": 0.5,
        },
        {
            "table_index": 59,
            "heading": "Key figures for Volkswagen shares and market indices",
            "preview": "High Low Closing Ordinary share Price Preferred share Price DAX Price Dividend",
            "rows": 6,
            "numeric_ratio": 0.5,
        },
    ]

    candidates = eu.group_eu_key_table_candidates(table_index)

    assert "Financial Highlights" not in candidates


def test_eu_financial_data_and_checks_extract_lseg_style_ifrs_tables():
    markdown = """
    # Consolidated income statement
    <table><tr><td>Year ended 31 December</td><td>Notes</td><td>2025 £m</td><td>2024 £m</td></tr>
    <tr><td>Revenue</td><td>2.1</td><td>9,081</td><td>8,579</td></tr>
    <tr><td>Operating profit</td><td></td><td>2,127</td><td>1,463</td></tr>
    <tr><td>Profit before tax</td><td></td><td>1,969</td><td>1,258</td></tr>
    <tr><td>Income tax expense</td><td></td><td>(463)</td><td>(337)</td></tr>
    <tr><td>Profit for the year</td><td></td><td>1,506</td><td>921</td></tr>
    <tr><td>Equity holders</td><td></td><td>1,249</td><td>685</td></tr>
    <tr><td>Non-controlling interests</td><td></td><td>257</td><td>236</td></tr></table>
    # Consolidated balance sheet
    <table><tr><td>At 31 December</td><td>Notes</td><td>2025£m</td><td>2024£m</td></tr>
    <tr><td>Total assets</td><td></td><td>796,704</td><td>732,819</td></tr>
    <tr><td>Total liabilities</td><td></td><td>774,536</td><td>707,666</td></tr>
    <tr><td>Net assets</td><td></td><td>22,168</td><td>25,153</td></tr>
    <tr><td>Total equity</td><td></td><td>22,168</td><td>25,153</td></tr></table>
    # Consolidated cash flow statement
    <table><tr><td>Year ended 31 December</td><td>Notes</td><td>2025£m</td><td>2024£m</td></tr>
    <tr><td>Net cash flows from operating activities</td><td></td><td>3,622</td><td>3,396</td></tr>
    <tr><td>Net cash flows used in investing activities</td><td></td><td>(2,046)</td><td>(1,279)</td></tr>
    <tr><td>Net cash flows used in financing activities</td><td></td><td>(1,061)</td><td>(2,164)</td></tr>
    <tr><td>Increase/(decrease) in cash and cash equivalents</td><td></td><td>515</td><td>(47)</td></tr>
    <tr><td>Foreign exchange translation</td><td></td><td>(41)</td><td>(58)</td></tr>
    <tr><td>Cash and cash equivalents at 1 January</td><td></td><td>3,475</td><td>3,580</td></tr>
    <tr><td>Cash and cash equivalents at 31 December</td><td></td><td>3,949</td><td>3,475</td></tr></table>
    """

    data = eu.build_eu_financial_data(
        markdown,
        task_id="eu-task",
        filename="London-Stock-Exchange-Group-plc_EU_LSEG_2025-12-31_annual.pdf",
    )
    checks = eu.build_eu_financial_checks(data)

    assert data["market"] == "EU"
    assert data["summary"]["statement_count"] == 3
    assert data["summary"]["key_metric_count"] >= 5
    assert checks["market"] == "EU"
    assert checks["overall_status"] == "pass"
    assert checks["summary"]["fail"] == 0
    assert checks["summary"]["pass"] >= 8
    assert not any("合并资产负债表" in item for item in checks["warnings"])


def test_eu_financial_data_records_report_currency_and_multi_country_units():
    markdown = """
    # Consolidated income statement
    <table><tr><td>Year ended 31 December</td><td>Notes</td><td>2025 €m</td><td>2024 €m</td></tr>
    <tr><td>Revenue</td><td></td><td>30,000</td><td>28,000</td></tr>
    <tr><td>Operating profit</td><td></td><td>4,000</td><td>3,800</td></tr>
    <tr><td>Profit before tax</td><td></td><td>3,800</td><td>3,600</td></tr>
    <tr><td>Income tax expense</td><td></td><td>(800)</td><td>(760)</td></tr>
    <tr><td>Profit for the year</td><td></td><td>3,000</td><td>2,840</td></tr></table>
    # Consolidated statement of financial position
    <table><tr><td>At 31 December</td><td>Notes</td><td>2025€m</td><td>2024€m</td></tr>
    <tr><td>Total assets</td><td></td><td>50,000</td><td>48,000</td></tr>
    <tr><td>Total liabilities</td><td></td><td>32,000</td><td>31,000</td></tr>
    <tr><td>Net assets</td><td></td><td>18,000</td><td>17,000</td></tr>
    <tr><td>Total equity</td><td></td><td>18,000</td><td>17,000</td></tr></table>
    # Consolidated statement of cash flows
    <table><tr><td>Year ended 31 December</td><td>Notes</td><td>2025€m</td><td>2024€m</td></tr>
    <tr><td>Net cash flows from operating activities</td><td></td><td>5,000</td><td>4,800</td></tr>
    <tr><td>Net cash flows used in investing activities</td><td></td><td>(2,000)</td><td>(1,900)</td></tr>
    <tr><td>Net cash flows used in financing activities</td><td></td><td>(1,000)</td><td>(950)</td></tr>
    <tr><td>Increase/(decrease) in cash and cash equivalents</td><td></td><td>2,000</td><td>1,950</td></tr>
    <tr><td>Cash and cash equivalents at 1 January</td><td></td><td>4,000</td><td>2,050</td></tr>
    <tr><td>Cash and cash equivalents at 31 December</td><td></td><td>6,000</td><td>4,000</td></tr></table>
    """

    data = eu.build_eu_financial_data(markdown, task_id="eu-eur", filename="LVMH_EU_MC_2025-12-31_annual.pdf")

    assert data["accounting_standard"] == "IFRS / EU local GAAP"
    assert data["currency"] == "EUR"
    assert data["unit"] == "EUR million"
    assert data["detected_currencies"] == ["EUR"]
    assert data["summary"]["detected_currencies"] == ["EUR"]
    assert data["summary"]["statement_units"] == ["EUR million"]


def test_eu_financial_data_binds_short_fiscal_year_headers_without_using_change_column(tmp_path):
    markdown = """
    # Consolidated income statement
    <table><tr><td></td><td>FY251€m</td><td>FY24€m</td><td>Reported change %</td></tr>
    <tr><td>Revenue</td><td>37,448</td><td>36,717</td><td>2.0</td></tr>
    <tr><td>Gross profit</td><td>12,519</td><td>12,258</td><td>2.1</td></tr>
    <tr><td>Operating profit</td><td>(411)</td><td>3,665</td><td>(111.2)</td></tr>
    <tr><td>Profit before taxation</td><td>(1,478)</td><td>1,620</td><td></td></tr>
    <tr><td>Income tax expense</td><td>(2,246)</td><td>(50)</td><td></td></tr>
    <tr><td>Profit for the year</td><td>(3,746)</td><td>1,505</td><td></td></tr></table>
    """

    (tmp_path / "table_index.json").write_text(
        '[{"table_index": 1, "pdf_page_number": 21, "bbox": [10, 20, 30, 40]}]',
        encoding="utf-8",
    )
    data = eu.build_eu_financial_data(
        markdown,
        task_id="eu-vod",
        filename="Vodafone-Group-Plc_EU_VOD_2025-03-31_annual.pdf",
        result_dir_path=str(tmp_path),
    )

    income = next(statement for statement in data["statements"] if statement["statement_type"] == "income_statement")
    revenue = next(item for item in income["items"] if item["canonical_name"] == "operating_revenue")
    assert revenue["values"] == {"2025": 37448.0, "2024": 36717.0}
    assert revenue["raw_values"] == {"2025": "37,448", "2024": "36,717"}
    assert revenue["sources"]["2025"]["quote_text"] == "Revenue | 37,448 | 36,717 | 2.0"
    assert revenue["sources"]["2025"]["pdf_page_number"] == 21
    assert revenue["sources"]["2025"]["bbox"] == [10, 20, 30, 40]
    assert income["currency"] == "EUR"
    assert income["unit"] == "EUR million"


def test_eu_currency_detection_covers_non_uk_european_markets():
    for label, currency in (
        ("2025 CHF million", "CHF"),
        ("2025 SEKm", "SEK"),
        ("2025 DKKm", "DKK"),
        ("2025 NOKm", "NOK"),
        ("2025 PLN million", "PLN"),
    ):
        unit, detected = eu._infer_unit_and_currency([[label]], {"heading": "Consolidated statement of financial position"})
    assert detected == currency
    assert unit == f"{currency} million"


def test_eu_report_kind_detects_board_report_pdf_extract_with_split_financial_word():
    markdown = """
    Airbus SE
    Report of the Board of Directors 2025

    This document is an unaudited PDF format version of the Board Report and is not
    the original report included in the audited fi nancial report pursuant to Article 361.
    ESEF-compliant Annual Financial Report is available in XHTML format.

    Consolidated Statement of Financial Position
    """

    data = eu.build_eu_financial_data(markdown, task_id="eu-air", filename="Airbus-SE_EU_AIR_2025-12-31_annual.pdf")
    checks = eu.build_eu_financial_checks(data)

    assert data["report_kind"] == "eu_board_report"
    assert checks["summary"]["fail"] == 0
    assert any(item["status"] == "warning" for item in checks["checks"] if item["rule_id"].startswith("eu.presence."))


def test_eu_report_kind_detects_annual_review_without_finance_report_body():
    markdown = """
    Roche Annual Report 2025
    Annual Review 07

    The Finance Report contains the annual financial statements and the consolidated
    financial statements.
    """ + "\n".join(["Business review narrative"] * 12000) + """
    Our reporting consists of the actual Annual Report and of the Finance Report and
    contains the annual financial statements and the consolidated financial statements.
    """

    data = eu.build_eu_financial_data(markdown, task_id="eu-roche", filename="Roche-Holding-AG_EU_ROG_2025-12-31_annual.pdf")
    checks = eu.build_eu_financial_checks(data)

    assert data["report_kind"] == "eu_annual_review"
    assert checks["summary"]["fail"] == 0
    assert all(item["status"] != "fail" for item in checks["checks"] if item["rule_id"].startswith("eu.presence."))


def test_eu_financial_data_extracts_operations_and_plural_statement_titles():
    markdown = """
    # Consolidated statements of operations
    <table><tr><td>Year ended December 31</td><td>Notes</td><td>2024 €m</td><td>2025 €m</td></tr>
    <tr><td>Net system sales</td><td></td><td>21,768.7</td><td>24,474.3</td></tr>
    <tr><td>Total net sales</td><td></td><td>28,262.9</td><td>32,667.3</td></tr>
    <tr><td>Income before income taxes</td><td></td><td>8,900.0</td><td>11,300.0</td></tr>
    <tr><td>Income tax expense</td><td></td><td>(1,328.4)</td><td>(1,690.6)</td></tr>
    <tr><td>Net income</td><td></td><td>7,571.6</td><td>9,609.4</td></tr></table>
    # Consolidated balance sheets
    <table><tr><td>As of December 31</td><td>Notes</td><td>2024 €m</td><td>2025 €m</td></tr>
    <tr><td>Total assets</td><td></td><td>47,100.0</td><td>51,400.0</td></tr>
    <tr><td>Total liabilities</td><td></td><td>27,900.0</td><td>30,300.0</td></tr>
    <tr><td>Total equity</td><td></td><td>19,200.0</td><td>21,100.0</td></tr></table>
    # Consolidated statements of cash flows
    <table><tr><td>Year ended December 31</td><td>Notes</td><td>2024 €m</td><td>2025 €m</td></tr>
    <tr><td>Net cash flows from operating activities</td><td></td><td>11,166.2</td><td>12,658.5</td></tr>
    <tr><td>Net cash flows used in investing activities</td><td></td><td>(1,800.0)</td><td>(2,100.0)</td></tr>
    <tr><td>Net cash flows used in financing activities</td><td></td><td>(4,000.0)</td><td>(5,000.0)</td></tr>
    <tr><td>Increase in cash and cash equivalents</td><td></td><td>5,366.2</td><td>5,558.5</td></tr>
    <tr><td>Cash and cash equivalents at beginning of period</td><td></td><td>7,369.7</td><td>12,735.9</td></tr>
    <tr><td>Cash and cash equivalents at end of period</td><td></td><td>12,735.9</td><td>18,294.4</td></tr></table>
    """

    data = eu.build_eu_financial_data(markdown, task_id="eu-asml", filename="ASML-Holding-N.V_EU_ASML_2025-12-31_annual.pdf")
    checks = eu.build_eu_financial_checks(data)

    statement_types = {statement["statement_type"] for statement in data["statements"]}
    assert {"balance_sheet", "income_statement", "cash_flow_statement"}.issubset(statement_types)
    assert checks["summary"]["fail"] == 0


def test_eu_financial_data_extracts_statement_of_income_caption_and_positive_tax_charge():
    markdown = """
    306 Note 36 Post-balance sheet events

    Consolidated Statement of Income for the year ended December 31, 2025
    <table><tr><td></td><td>Notes</td><td>2025</td><td>2024</td><td>$ million 2023</td></tr>
    <tr><td>Revenue</td><td>8</td><td>266,886</td><td>284,312</td><td>316,620</td></tr>
    <tr><td>Income before taxation</td><td></td><td>29,756</td><td>29,922</td><td>32,627</td></tr>
    <tr><td>Taxation charge</td><td>23</td><td>11,637</td><td>13,401</td><td>12,991</td></tr>
    <tr><td>Income for the period</td><td>7</td><td>18,119</td><td>16,521</td><td>19,636</td></tr>
    <tr><td>Income attributable to non-controlling interest</td><td></td><td>282</td><td>427</td><td>277</td></tr>
    <tr><td>Income attributable to Shell plc shareholders</td><td></td><td>17,837</td><td>16,094</td><td>19,359</td></tr></table>

    Consolidated Balance Sheet as at December 31, 2025
    <table><tr><td></td><td>Notes</td><td>Dec 31, 2025</td><td>$ million Dec 31, 2024</td></tr>
    <tr><td>Total assets</td><td></td><td>370,350</td><td>387,609</td></tr>
    <tr><td>Total liabilities</td><td></td><td>195,031</td><td>207,441</td></tr>
    <tr><td>Total equity</td><td></td><td>175,319</td><td>180,168</td></tr></table>

    Consolidated Statement of Cash Flows for the year ended December 31, 2025
    <table><tr><td></td><td>Notes</td><td>2025</td><td>2024</td><td>$ million 2023</td></tr>
    <tr><td>Cash flow from operating activities</td><td></td><td>42,863</td><td>54,687</td><td>54,191</td></tr>
    <tr><td>Cash flow from investing activities</td><td></td><td>(16,811)</td><td>(15,155)</td><td>(17,734)</td></tr>
    <tr><td>Cash flow from financing activities</td><td></td><td>(35,812)</td><td>(38,435)</td><td>(38,235)</td></tr>
    <tr><td>Effects of exchange rate changes on cash and cash equivalents</td><td></td><td>866</td><td>(761)</td><td>306</td></tr>
    <tr><td>(Decrease)/increase in cash and cash equivalents</td><td></td><td>(8,894)</td><td>336</td><td>(1,472)</td></tr>
    <tr><td>Cash and cash equivalents at January 1</td><td></td><td>39,110</td><td>38,774</td><td>40,246</td></tr>
    <tr><td>Cash and cash equivalents at December 31</td><td></td><td>30,216</td><td>39,110</td><td>38,774</td></tr></table>
    """

    data = eu.build_eu_financial_data(markdown, task_id="eu-shell", filename="Shell-plc_EU_SHELL_2025-12-31_annual.pdf")
    checks = eu.build_eu_financial_checks(data)

    income = next(statement for statement in data["statements"] if statement["statement_type"] == "income_statement")
    cash = next(statement for statement in data["statements"] if statement["statement_type"] == "cash_flow_statement")
    income_items = {item["canonical_name"] for item in income["items"]}
    cash_items = {item["canonical_name"] for item in cash["items"]}

    assert income["title"] == "Consolidated Statement of Income for the year ended December 31, 2025"
    assert {"profit_before_tax", "income_tax_expense", "net_profit", "parent_net_profit", "minority_profit_loss"}.issubset(income_items)
    assert {"operating_cash_flow_net", "investing_cash_flow_net", "financing_cash_flow_net", "fx_effect_cash"}.issubset(cash_items)
    assert checks["summary"]["fail"] == 0
    assert all(item["status"] != "warning" for item in checks["checks"] if item["rule_id"] == "eu.is.profit_before_tax_less_tax_eq_profit_for_year")
    assert all(item["status"] != "warning" for item in checks["checks"] if item["rule_id"].startswith("eu.cf."))
