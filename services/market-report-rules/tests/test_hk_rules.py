from market_report_rules_service.contracts import financial_data_contract
from market_report_rules_service.markets.hk.rules import find_hk_rule
from market_report_rules_service.models import AccountingStandard, Market, ParsedArtifact, ParsedTable
from market_report_rules_service.pipeline import process_artifact


def test_hk_rule_specificity_filters_broad_revenue_matches():
    assert find_hk_rule("銷售成本 Cost of sales").canonical_name == "cost_of_sales"
    assert find_hk_rule("除所得税前溢利 Profit before income tax").canonical_name == "total_profit"
    assert find_hk_rule("Net cash inflow from operating activities").canonical_name == "operating_cash_flow_net"
    assert find_hk_rule("Net cash flow (used in)/from operating activities").canonical_name == "operating_cash_flow_net"
    assert find_hk_rule("其他收入及收益 Other income and gains") is None
    assert find_hk_rule("年內溢利 For profit for the year") is None
    assert find_hk_rule("Total comprehensive income for the year") is None
    assert find_hk_rule("Reserves") is None
    assert find_hk_rule("Net assets of Shandong Jingzhi Baijiu") is None


def test_hk_pdf_tables_detect_statement_type_and_period_columns():
    artifact = ParsedArtifact(
        artifact_id="hk-00700-2025-annual",
        market=Market.HK,
        company_id="HK:00700",
        ticker="00700",
        company_name="TENCENT",
        report_type="annual",
        fiscal_year=2025,
        period_end="2025-12-31",
        accounting_standard=AccountingStandard.HKFRS,
        currency="HKD",
        unit="HK$ million",
        source_url="https://www1.hkexnews.hk/example.pdf",
        tables=[
            ParsedTable(
                table_id="bs1",
                title="Consolidated Statement of Financial Position",
                table_index=1,
                page_number=88,
                unit="HK$ million",
                rows=[
                    ["", "2025", "2024"],
                    ["Total assets", "1,000", "900"],
                    ["Total liabilities", "600", "550"],
                    ["Total equity", "400", "350"],
                    ["Cash and cash equivalents", "120", "100"],
                ],
            ),
            ParsedTable(
                table_id="is1",
                title="Consolidated Statement of Profit or Loss",
                table_index=2,
                page_number=89,
                unit="HK$ million",
                rows=[
                    ["", "2025", "2024"],
                    ["Revenue", "700", "650"],
                    ["Cost of sales", "300", "290"],
                    ["Gross profit", "400", "360"],
                    ["Profit before tax", "130", "120"],
                    ["Taxation", "30", "25"],
                    ["Profit for the year", "100", "95"],
                ],
            ),
            ParsedTable(
                table_id="cf1",
                title="Consolidated Statement of Cash Flows",
                table_index=3,
                page_number=90,
                unit="HK$ million",
                rows=[
                    ["", "2025", "2024"],
                    ["Net cash generated from operating activities", "150", "130"],
                    ["Net cash used in investing activities", "-20", "-10"],
                    ["Net cash used in financing activities", "-10", "-8"],
                    ["Net increase in cash and cash equivalents", "120", "112"],
                ],
            ),
        ],
    )

    result = process_artifact(artifact)
    data = financial_data_contract(result.extraction)

    assert result.load_plan.target_database == "siq"
    assert result.load_plan.target_schema == "pdf2md_hk"
    assert data["summary"]["statement_count"] == 3
    balance = next(statement for statement in data["statements"] if statement["statement_type"] == "balance_sheet")
    total_assets = next(item for item in balance["items"] if item["canonical_name"] == "total_assets")
    assert total_assets["values"]["2025-12-31"] == "1000"
    assert total_assets["values"]["2024-12-31"] == "900"
    assert result.validation.summary["pass"] > 0


def test_hk_operating_metrics_are_separate_from_statement_tables():
    artifact = ParsedArtifact(
        artifact_id="hk-platform-kpi",
        market=Market.HK,
        company_id="HK:9999",
        ticker="9999",
        report_type="annual",
        fiscal_year=2025,
        period_end="2025-12-31",
        industry_profile="internet_platform",
        tables=[
            ParsedTable(
                table_id="kpi1",
                title="Operating Highlights",
                table_index=5,
                page_number=12,
                rows=[
                    ["", "2025"],
                    ["Monthly active users", "100"],
                    ["Daily active users", "50"],
                    ["GMV", "2000"],
                ],
            )
        ],
    )

    result = process_artifact(artifact)

    assert {item.canonical_name for item in result.extraction.operating_metrics} >= {
        "monthly_active_users",
        "daily_active_users",
        "gmv",
    }
    assert all(item.statement_type == "operating_metrics" for item in result.extraction.operating_metrics)


def test_hk_bilingual_statement_tables_align_note_and_period_columns():
    artifact = ParsedArtifact(
        artifact_id="hk-bilingual-2025-annual",
        market=Market.HK,
        company_id="HK:09633",
        ticker="09633",
        company_name="NONGFU SPRING",
        report_type="annual",
        fiscal_year=2025,
        period_end="2025-12-31",
        accounting_standard=AccountingStandard.HKFRS,
        currency="CNY",
        unit="RMB'000",
        tables=[
            ParsedTable(
                table_id="is-bilingual",
                table_index=1,
                page_number=115,
                unit="RMB'000",
                rows=[
                    ["", "附註Notes", "2025年 2025 人民幣千元 RMB'000", "2024年 2024 人民幣千元 RMB'000"],
                    ["收益", "REVENUE", "5", "52,552,910", "42,895,992"],
                    ["除稅前溢利", "PROFIT BEFORE TAX", "7", "20,917,593", "15,787,858"],
                    ["所得稅開支", "Income tax expense", "11", "(5,049,319)", "(3,664,554)"],
                    ["年內溢利", "PROFIT FOR THE YEAR", "", "15,868,274", "12,123,304"],
                ],
            ),
            ParsedTable(
                table_id="cf-bilingual",
                table_index=2,
                page_number=121,
                unit="RMB'000",
                rows=[
                    ["", "附註Notes", "2025年 2025 人民幣千元 RMB'000", "2024年 2024 人民幣千元 RMB'000"],
                    ["經營活動所得現金流量", "CASH FLOWS FROM OPERATING ACTIVITIES", "", "", ""],
                    ["除稅前溢利", "Profit before tax", "", "20,917,593", "15,787,858"],
                    ["經營活動所得現金流量淨額", "Net cash flows from operating activities", "", "21,141,652", "11,022,144"],
                    ["投資活動所用現金流量淨額", "Net cash flows used in investing activities", "", "(11,494,415)", "(4,501,597)"],
                    ["融資活動所用現金流量淨額", "Net cash flows used in financing activities", "", "(8,058,287)", "(8,061,776)"],
                ],
            ),
        ],
    )

    result = process_artifact(artifact)
    data = financial_data_contract(result.extraction)

    income = next(statement for statement in data["statements"] if statement["statement_type"] == "income_statement")
    revenue = next(item for item in income["items"] if item["canonical_name"] == "operating_revenue")
    net_profit = next(item for item in income["items"] if item["canonical_name"] == "net_profit")
    assert revenue["values"]["2025-12-31"] == "52552910"
    assert net_profit["values"]["2025-12-31"] == "15868274"

    cash_flow = next(statement for statement in data["statements"] if statement["statement_type"] == "cash_flow_statement")
    operating_cash_flow = next(item for item in cash_flow["items"] if item["canonical_name"] == "operating_cash_flow_net")
    assert operating_cash_flow["values"]["2025-12-31"] == "21141652"


def test_hk_mixed_summary_allows_exact_balance_totals():
    artifact = ParsedArtifact(
        artifact_id="hk-mixed-summary",
        market=Market.HK,
        company_id="HK:01177",
        ticker="01177",
        report_type="annual",
        fiscal_year=2025,
        period_end="2025-12-31",
        accounting_standard=AccountingStandard.HKFRS,
        currency="CNY",
        unit="RMB'000",
        tables=[
            ParsedTable(
                table_id="summary",
                table_index=1,
                page_number=6,
                unit="RMB'000",
                rows=[
                    ["", "2025RMB'000", "2024RMB'000"],
                    ["REVENUE", "31,834,488", "28,866,159"],
                    ["PROFIT FOR THE YEAR", "5,314,529", "6,364,682"],
                    ["TOTAL ASSETS", "76,009,821", "65,408,069"],
                    ["TOTAL LIABILITIES", "(33,923,894)", "(22,633,999)"],
                    ["NET ASSETS", "42,085,927", "42,774,070"],
                ],
            )
        ],
    )

    result = process_artifact(artifact)
    data = financial_data_contract(result.extraction)

    balance = next(statement for statement in data["statements"] if statement["statement_type"] == "balance_sheet")
    total_assets = next(item for item in balance["items"] if item["canonical_name"] == "total_assets")
    total_liabilities = next(item for item in balance["items"] if item["canonical_name"] == "total_liabilities")
    total_equity = next(item for item in balance["items"] if item["canonical_name"] == "total_equity")
    assert total_assets["values"]["2025-12-31"] == "76009821"
    assert total_liabilities["values"]["2025-12-31"] == "-33923894"
    assert total_equity["values"]["2025-12-31"] == "42085927"
