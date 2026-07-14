from market_report_rules_service.contracts import financial_checks_contract, financial_data_contract
from market_report_rules_service.markets.eu.rules import find_eu_label_rule
from market_report_rules_service.models import AccountingStandard, Market, ParsedArtifact, ParsedTable
from market_report_rules_service.pipeline import process_artifact


def test_eu_rule_specificity_filters_broad_revenue_matches():
    assert find_eu_label_rule("Revenue").canonical_name == "operating_revenue"
    assert find_eu_label_rule("Cost of sales").canonical_name == "cost_of_sales"
    assert find_eu_label_rule("Profit before tax").canonical_name == "total_profit"
    assert find_eu_label_rule("Net cash from operating activities").canonical_name == "operating_cash_flow_net"
    assert find_eu_label_rule("Cash inflow/outflow from operating activities").canonical_name == "operating_cash_flow_net"
    assert find_eu_label_rule("Net cash provided by/(used in) operating activities").canonical_name == "operating_cash_flow_net"
    assert find_eu_label_rule("Net change in cash and cash equivalents").canonical_name == "cash_equivalents_net_increase"
    assert find_eu_label_rule("Other operating income") is None
    assert find_eu_label_rule("Adjusted EBITDA") is None
    assert find_eu_label_rule("Total assets less current liabilities") is None
    assert find_eu_label_rule("SUB-TOTAL ASSETS") is None
    assert find_eu_label_rule("Average of (total equity + net debt)") is None
    assert find_eu_label_rule("TOTAL LIABILITIES AT FAIR VALUE") is None
    assert find_eu_label_rule("Total liabilities (excluding shareholders' equity)") is None
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
                    ["Total liabilities", "(20,000)", "(18,000)"],
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
    total_liabilities = next(item for item in balance["items"] if item["canonical_name"] == "total_liabilities")
    assert total_assets["values"]["2025-12-31"] == "50000"
    assert total_liabilities["values"]["2025-12-31"] == "20000"
    assert total_assets["sources"]["2025-12-31"]["page_number"] == 184
    cash_flow = next(statement for statement in data["statements"] if statement["statement_type"] == "cash_flow_statement")
    operating_cash_flow = next(item for item in cash_flow["items"] if item["canonical_name"] == "operating_cash_flow_net")
    assert operating_cash_flow["values"]["2025-12-31"] == "7200"


def test_eu_split_balance_sheet_uses_equity_liabilities_context_and_shifted_header():
    artifact = ParsedArtifact(
        artifact_id="eu-ch-zurn-2025-annual",
        market=Market.EU,
        company_id="CH:ZURN",
        ticker="ZURN",
        company_name="Zurich Insurance Group AG",
        report_type="annual",
        report_form="annual",
        fiscal_year=2025,
        fiscal_period="FY",
        period_end="2025-12-31",
        accounting_standard=AccountingStandard.IFRS,
        industry_profile="insurance",
        currency="USD",
        unit="USD million",
        metadata={"country": "CH", "document_format": "pdf"},
        tables=[
            ParsedTable(
                table_id="assets",
                title="Consolidated balance sheets",
                table_index=1,
                page_number=276,
                unit="USD million",
                rows=[
                    ["Assets", "in USD millions, as of December 31", "Notes", "2025", "2024"],
                    ["Total assets", "", "407,211", "358,005"],
                ],
            ),
            ParsedTable(
                table_id="liabilities",
                title="Liabilities and equity",
                table_index=2,
                page_number=277,
                unit="USD million",
                rows=[
                    ["in USD millions, as of December 31", "Notes", "2025", "2024"],
                    ["Total liabilities", "", "377,045", "331,067"],
                    ["Equity", "", "30,166", "26,938"],
                    ["TOTAL", "", "407,211", "358,005"],
                ],
            ),
            ParsedTable(
                table_id="cash",
                title="Consolidated statements of cash flows",
                table_index=3,
                page_number=278,
                unit="USD million",
                rows=[
                    ["in USD millions", "2025", "2024"],
                    ["Net cash provided by/(used in) operating activities", "10,000", "9,000"],
                ],
            ),
            ParsedTable(
                table_id="income",
                title="Consolidated income statements",
                table_index=4,
                page_number=273,
                unit="USD million",
                rows=[
                    ["in USD millions", "2025", "2024"],
                    ["Revenue", "80,000", "75,000"],
                    ["Profit for the year", "6,000", "5,000"],
                ],
            ),
        ],
    )

    result = process_artifact(artifact)
    data = financial_data_contract(result.extraction)
    checks = financial_checks_contract(result.validation)

    assert checks["overall_status"] == "pass"
    assert checks["warnings"] == [
        "EU bank/insurance profile: cash-flow statement coverage may be partial and should be reviewed manually."
    ]
    assert checks["advisories"] == [
        "IFRS 17/HKFRS 17 changes revenue and liability presentation; use insurance profile rules."
    ]
    balance = next(statement for statement in data["statements"] if statement["statement_type"] == "balance_sheet")
    total_assets = next(item for item in balance["items"] if item["canonical_name"] == "total_assets")
    total_equity = next(item for item in balance["items"] if item["canonical_name"] == "total_equity")
    total_liabilities_and_equity = next(
        item for item in balance["items"] if item["canonical_name"] == "total_liabilities_and_equity"
    )
    assert total_assets["values"]["2025-12-31"] == "407211"
    assert total_assets["values"]["2024-12-31"] == "358005"
    assert total_equity["values"]["2025-12-31"] == "30166"
    assert total_liabilities_and_equity["values"]["2025-12-31"] == "407211"


def test_eu_non_primary_fair_value_table_does_not_override_balance_bridge():
    artifact = ParsedArtifact(
        artifact_id="eu-fr-or-2025-annual",
        market=Market.EU,
        company_id="FR:OR",
        ticker="OR",
        company_name="L'Oreal S.A.",
        report_type="annual",
        report_form="annual",
        fiscal_year=2025,
        fiscal_period="FY",
        period_end="2025-12-31",
        accounting_standard=AccountingStandard.IFRS,
        industry_profile="general",
        currency="EUR",
        unit="EUR million",
        metadata={"country": "FR", "document_format": "pdf"},
        tables=[
            ParsedTable(
                table_id="assets",
                title="Assets",
                table_index=1,
                page_number=294,
                unit="EUR million",
                rows=[
                    ["EUR millions", "Notes", "31.12.2025", "31.12.2024"],
                    ["TOTAL ASSETS", "", "61,821.2", "56,353.4"],
                ],
            ),
            ParsedTable(
                table_id="equity_liabilities",
                title="Equity & liabilities",
                table_index=2,
                page_number=294,
                unit="EUR million",
                rows=[
                    ["EUR millions", "Notes", "31.12.2025", "31.12.2024"],
                    ["Equity", "11", "35,003.8", "33,137.8"],
                    ["TOTAL", "", "61,821.2", "56,353.4"],
                ],
            ),
            ParsedTable(
                table_id="fair_value",
                title="EUR millions",
                table_index=3,
                page_number=337,
                unit="EUR million",
                rows=[
                    ["31 December 2025", "Level 1", "Level 2", "Level 3", "Total fair value"],
                    ["TOTAL LIABILITIES AT FAIR VALUE", "-", "172.2", "-", "172.2"],
                ],
            ),
            ParsedTable(
                table_id="is",
                title="Consolidated income statement",
                table_index=4,
                page_number=293,
                unit="EUR million",
                rows=[
                    ["EUR millions", "2025", "2024"],
                    ["Sales", "44,052.0", "43,486.8"],
                    ["Profit for the year", "6,133.7", "6,416.5"],
                ],
            ),
            ParsedTable(
                table_id="cf",
                title="Statements of cash flows",
                table_index=5,
                page_number=295,
                unit="EUR million",
                rows=[
                    ["EUR millions", "2025", "2024"],
                    ["Net cash provided by/(used in) operating activities", "8,329.5", "8,512.6"],
                ],
            ),
        ],
    )

    result = process_artifact(artifact)
    checks = financial_checks_contract(result.validation)

    assert checks["overall_status"] == "pass"
