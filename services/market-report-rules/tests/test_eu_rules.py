from market_report_rules_service.contracts import financial_data_contract
from market_report_rules_service.markets.eu.rules import find_eu_label_rule
from market_report_rules_service.models import AccountingStandard, Market, ParsedArtifact, ParsedTable
from market_report_rules_service.pipeline import process_artifact


def test_eu_rule_specificity_filters_broad_revenue_matches():
    assert find_eu_label_rule("Revenue").canonical_name == "operating_revenue"
    assert find_eu_label_rule("Cost of sales").canonical_name == "cost_of_sales"
    assert find_eu_label_rule("Profit before tax").canonical_name == "total_profit"
    assert find_eu_label_rule("Net cash from operating activities").canonical_name == "operating_cash_flow_net"
    assert find_eu_label_rule("Other operating income") is None
    assert find_eu_label_rule("Adjusted EBITDA") is None
    assert find_eu_label_rule("Basic earnings per share").canonical_name == "basic_eps"


def test_eu_pdf_tables_extract_ifrs_core_metrics_to_eu_schema():
    artifact = ParsedArtifact(
        artifact_id="eu-nl-asml-2025-annual",
        market=Market.EU,
        company_id="NL:ASML",
        ticker="ASML",
        company_name="ASML Holding N.V.",
        report_type="annual",
        report_form="annual",
        fiscal_year=2025,
        fiscal_period="FY",
        period_end="2025-12-31",
        accounting_standard=AccountingStandard.IFRS,
        industry_profile="semiconductor",
        currency="EUR",
        unit="EUR million",
        source_url="https://example.test/asml.pdf",
        metadata={"country": "NL", "document_format": "pdf"},
        tables=[
            ParsedTable(
                table_id="bs",
                title="Consolidated Statement of Financial Position",
                table_index=1,
                page_number=184,
                unit="EUR million",
                rows=[
                    ["", "2025", "2024"],
                    ["Total assets", "50,000", "45,000"],
                    ["Total liabilities", "20,000", "18,000"],
                    ["Total equity", "30,000", "27,000"],
                    ["Cash and cash equivalents", "7,000", "6,000"],
                    ["Inventories", "9,000", "8,000"],
                    ["Property, plant and equipment", "12,000", "11,000"],
                ],
            ),
            ParsedTable(
                table_id="is",
                title="Consolidated Statement of Profit or Loss",
                table_index=2,
                page_number=185,
                unit="EUR million",
                rows=[
                    ["", "2025", "2024"],
                    ["Revenue", "30,000", "28,000"],
                    ["Cost of sales", "(14,000)", "(13,000)"],
                    ["Gross profit", "16,000", "15,000"],
                    ["Operating profit", "8,000", "7,500"],
                    ["Profit before tax", "7,500", "7,000"],
                    ["Income tax expense", "(1,500)", "(1,400)"],
                    ["Profit for the year", "6,000", "5,600"],
                ],
            ),
            ParsedTable(
                table_id="cf",
                title="Consolidated Statement of Cash Flows",
                table_index=3,
                page_number=186,
                unit="EUR million",
                rows=[
                    ["", "2025", "2024"],
                    ["Net cash from operating activities", "7,200", "6,800"],
                    ["Net cash used in investing activities", "(2,000)", "(1,900)"],
                    ["Net cash used in financing activities", "(1,000)", "(900)"],
                    ["Net increase in cash and cash equivalents", "4,200", "4,000"],
                ],
            ),
        ],
    )

    result = process_artifact(artifact)
    data = financial_data_contract(result.extraction)

    assert result.load_plan.target_schema == "eu_ifrs"
    assert data["market"] == "EU"
    assert data["summary"]["statement_count"] == 3
    balance = next(statement for statement in data["statements"] if statement["statement_type"] == "balance_sheet")
    total_assets = next(item for item in balance["items"] if item["canonical_name"] == "total_assets")
    assert total_assets["values"]["2025-12-31"] == "50000"
    assert total_assets["sources"]["2025-12-31"]["page_number"] == 184
    cash_flow = next(statement for statement in data["statements"] if statement["statement_type"] == "cash_flow_statement")
    operating_cash_flow = next(item for item in cash_flow["items"] if item["canonical_name"] == "operating_cash_flow_net")
    assert operating_cash_flow["values"]["2025-12-31"] == "7200"
