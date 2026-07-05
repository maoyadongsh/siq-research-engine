from decimal import Decimal

from market_report_rules_service.contracts import financial_data_contract
from market_report_rules_service.models import Market, ParsedArtifact, ParsedTable
from market_report_rules_service.pipeline import process_artifact


def test_jp_edinet_xbrl_extracts_structured_facts_to_jp_schema():
    artifact = ParsedArtifact(
        artifact_id="jp-7203-2025-annual",
        market=Market.JP,
        company_id="JP:E02144",
        ticker="7203",
        company_name="Toyota Motor Corporation",
        report_type="annual",
        report_form="有価証券報告書",
        fiscal_year=2025,
        period_end="2025-03-31",
        currency="JPY",
        source_url="https://disclosure2.edinet-fsa.go.jp/example",
        document_full={
            "edinet_facts": {
                "ifrs-full": {
                    "Assets": {"label": "Assets", "units": {"JPY": [{"val": 1000, "end": "2025-03-31", "fy": 2025, "fp": "FY", "doc_id": "S100TEST"}]}},
                    "Liabilities": {"label": "Liabilities", "units": {"JPY": [{"val": 600, "end": "2025-03-31", "fy": 2025, "fp": "FY", "doc_id": "S100TEST"}]}},
                    "Equity": {"label": "Equity", "units": {"JPY": [{"val": 400, "end": "2025-03-31", "fy": 2025, "fp": "FY", "doc_id": "S100TEST"}]}},
                    "Revenue": {"label": "Revenue", "units": {"JPY": [{"val": 3000, "start": "2024-04-01", "end": "2025-03-31", "fy": 2025, "fp": "FY", "doc_id": "S100TEST"}]}},
                    "ProfitLoss": {"label": "Profit", "units": {"JPY": [{"val": 100, "start": "2024-04-01", "end": "2025-03-31", "fy": 2025, "fp": "FY", "doc_id": "S100TEST"}]}},
                    "CashFlowsFromUsedInOperatingActivities": {"label": "OCF", "units": {"JPY": [{"val": 150, "start": "2024-04-01", "end": "2025-03-31", "fy": 2025, "fp": "FY", "doc_id": "S100TEST"}]}},
                }
            }
        },
    )

    result = process_artifact(artifact)
    data = financial_data_contract(result.extraction)

    assert result.load_plan.target_schema == "edinet_jp"
    assert data["market"] == "JP"
    assert data["summary"]["statement_count"] == 3
    assert any(row.table == "evidence_citations" for row in result.load_plan.rows)
    first_fact = result.extraction.statements[0].items[0]
    assert first_fact.evidence.source_type == "edinet_xbrl_fact"


def test_jp_pdf_financial_summary_extracts_mixed_statement_rows():
    artifact = ParsedArtifact(
        artifact_id="jp-7203-financial-summary",
        market=Market.JP,
        company_id="JP:7203",
        ticker="7203",
        company_name="Toyota Motor Corporation",
        report_type="integrated_report",
        fiscal_year=2025,
        period_end="2025-03-31",
        currency="JPY",
        tables=[
            ParsedTable(
                table_id="financial-summary",
                title="Financial Summary (Consolidated)",
                table_index=68,
                page_number=164,
                rows=[
                    ["Fiscal years ended March 31", "", "2024", "2025"],
                    ["Net revenues", "Sales revenues", "(Billions of yen)", "45,095.3", "48,036.7"],
                    ["Operating income", "Operating income", "(Billions of yen)", "5,352.9", "4,795.5"],
                    ["Income before income taxes", "Income before income taxes", "(Billions of yen)", "6,965.0", "6,414.5"],
                    ["Net income*1", "Net income attributable to Toyota Motor Corporation", "(Billions of yen)", "4,944.9", "4,765.0"],
                    ["Total assets", "(Billions of yen)", "90,114.2", "93,601.3"],
                ],
            )
        ],
    )

    result = process_artifact(artifact)
    data = financial_data_contract(result.extraction)

    assert data["summary"]["statement_count"] == 2
    facts = {
        (item.canonical_name, item.period_key): item
        for statement in result.extraction.statements
        for item in statement.items
    }
    assert facts[("operating_revenue", "2025-03-31")].value == Decimal("48036.7")
    assert facts[("operating_profit", "2025-03-31")].evidence.page_number == 164
    assert facts[("total_profit", "2025-03-31")].canonical_name == "total_profit"
    assert facts[("parent_net_profit", "2025-03-31")].value == Decimal("4765.0")
    assert facts[("total_assets", "2025-03-31")].statement_type == "balance_sheet"


def test_jp_summary_header_starting_with_year_does_not_map_change_percent_to_current_year():
    artifact = ParsedArtifact(
        artifact_id="jp-canon-summary",
        market=Market.JP,
        company_id="JP:7751",
        ticker="7751",
        company_name="Canon Inc.",
        report_type="integrated_report",
        fiscal_year=2025,
        period_end="2025-12-31",
        currency="JPY",
        tables=[
            ParsedTable(
                table_id="summary",
                table_index=2,
                page_number=3,
                rows=[
                    ["", "Millions of yen", "", "Thousands of U.S. dollars"],
                    ["2025", "2024", "Change (%)", "2025"],
                    ["Net sales", "¥ 4,624,727", "¥ 4,509,821", "+ 2.5", "$ 29,456,860"],
                    ["Total assets", "¥ 6,135,044", "¥ 5,766,246", "+6.4", "$ 39,076,713"],
                ],
            )
        ],
    )

    result = process_artifact(artifact)
    facts = {
        (item.canonical_name, item.period_key): item
        for statement in result.extraction.statements
        for item in statement.items
    }

    assert facts[("total_assets", "2025-12-31")].value == Decimal("6135044")
    assert facts[("total_assets", "2024-12-31")].value == Decimal("5766246")


def test_jp_hybrid_skips_subsidiary_impact_statement_tables():
    artifact = ParsedArtifact(
        artifact_id="jp-sbg-subsidiary-impact",
        market=Market.JP,
        company_id="JP:9984",
        ticker="9984",
        report_type="integrated_report",
        fiscal_year=2025,
        period_end="2025-03-31",
        currency="JPY",
        tables=[
            ParsedTable(
                table_id="primary-bs",
                title="Consolidated Statement of Financial Position",
                table_index=1,
                page_number=80,
                rows=[
                    ["", "March 31, 2025"],
                    ["Total assets", "1000"],
                    ["Total liabilities", "600"],
                    ["Total equity", "400"],
                ],
            ),
            ParsedTable(
                table_id="subsidiary-impact",
                title="Impact of the asset management subsidiaries on the company’s consolidated statement of financial position",
                table_index=34,
                page_number=92,
                rows=[
                    ["", "March 31, 2025"],
                    ["Total assets", "1145394"],
                    ["Total liabilities", "31852"],
                    ["Equity", "1113542"],
                ],
            ),
        ],
    )

    result = process_artifact(artifact)
    data = financial_data_contract(result.extraction)
    balance = next(statement for statement in data["statements"] if statement["statement_type"] == "balance_sheet")
    total_assets = next(item for item in balance["items"] if item["canonical_name"] == "total_assets")

    assert total_assets["values"]["2025-03-31"] == "1000"
    assert 34 not in balance["table_indexes"]


def test_jp_pdf_summary_reuses_previous_header_table_periods():
    artifact = ParsedArtifact(
        artifact_id="jp-9983-summary",
        market=Market.JP,
        company_id="JP:9983",
        ticker="9983",
        company_name="Fast Retailing Co., Ltd.",
        report_type="integrated_report",
        fiscal_year=2025,
        period_end="2025-08-31",
        currency="JPY",
        tables=[
            ParsedTable(
                table_id="year-header",
                table_index=5,
                page_number=88,
                rows=[["", "2022", "2023", "2024", "2025"]],
            ),
            ParsedTable(
                table_id="for-the-year",
                title="For the year",
                table_index=6,
                page_number=88,
                rows=[
                    ["Revenue", "¥1,786,473", "¥1,861,917", "¥2,130,060", "¥2,290,548"],
                    ["Operating profit", "127,292", "176,414", "236,212", "257,636"],
                    ["Profit before income taxes", "90,237", "193,398", "242,678", "252,447"],
                    ["Profit attributable to owners of the Parent", "48,052", "119,280", "154,811", "162,578"],
                    ["Net cash generated by operating activities", "98,755", "212,168", "176,403", "300,505"],
                    ["Net cash (used in)/generated by investing activities", "(245,939)", "122,790", "(57,180)", "(78,756)"],
                ],
            ),
        ],
    )

    result = process_artifact(artifact)
    facts = {
        (item.canonical_name, item.period_key): item
        for statement in result.extraction.statements
        for item in statement.items
    }

    assert facts[("operating_revenue", "2025-08-31")].value == 2290548
    assert facts[("operating_profit", "2025-08-31")].evidence.column_index == 4
    assert facts[("total_profit", "2025-08-31")].value == 252447
    assert facts[("parent_net_profit", "2025-08-31")].value == 162578
    assert facts[("operating_cash_flow_net", "2025-08-31")].value == 300505
    assert facts[("investing_cash_flow_net", "2025-08-31")].value == -78756


def test_kr_dart_pdf_tables_are_fallback_for_local_language_rows():
    artifact = ParsedArtifact(
        artifact_id="kr-005930-2025-annual",
        market=Market.KR,
        company_id="KR:00126380",
        ticker="005930",
        company_name="Samsung Electronics",
        report_type="annual",
        fiscal_year=2025,
        period_end="2025-12-31",
        currency="KRW",
        tables=[
            ParsedTable(
                table_id="bs",
                title="연결재무상태표",
                table_index=1,
                page_number=120,
                unit="KRW million",
                rows=[
                    ["", "2025", "2024"],
                    ["자산총계", "1000", "900"],
                    ["부채총계", "450", "410"],
                    ["자본총계", "550", "490"],
                ],
            ),
            ParsedTable(
                table_id="is",
                title="연결손익계산서",
                table_index=2,
                page_number=121,
                unit="KRW million",
                rows=[
                    ["", "2025"],
                    ["매출액", "3000"],
                    ["영업이익", "250"],
                    ["당기순이익", "180"],
                ],
            ),
            ParsedTable(
                table_id="cf",
                title="연결현금흐름표",
                table_index=3,
                page_number=122,
                unit="KRW million",
                rows=[
                    ["", "2025"],
                    ["영업활동현금흐름", "300"],
                    ["투자활동현금흐름", "-100"],
                    ["재무활동현금흐름", "-50"],
                ],
            ),
        ],
    )

    result = process_artifact(artifact)
    data = financial_data_contract(result.extraction)

    assert result.load_plan.target_schema == "dart_kr"
    assert data["market"] == "KR"
    assert data["summary"]["statement_count"] == 3
    assert any(item.evidence.source_type == "dart_pdf_statement_table" for statement in result.extraction.statements for item in statement.items)


def test_kr_bridge_checks_compare_scaled_table_values_with_xbrl_amounts():
    artifact = ParsedArtifact(
        artifact_id="kr-scaled-bridge",
        market=Market.KR,
        company_id="KR:005930",
        ticker="005930",
        company_name="Samsung Electronics",
        report_type="annual",
        fiscal_year=2025,
        period_end="2025-12-31",
        currency="KRW",
        document_full={
            "dart_facts": {
                "ifrs-full": {
                    "Assets": {
                        "label": "Assets",
                        "units": {"KRW": [{"val": 1000000000, "end": "2025-12-31", "fy": 2025, "fp": "FY"}]},
                    }
                }
            }
        },
        tables=[
            ParsedTable(
                table_id="bs",
                title="연결재무상태표",
                table_index=1,
                page_number=120,
                unit="KRW million",
                rows=[
                    ["", "2025"],
                    ["부채총계", "600"],
                    ["자본총계", "400"],
                ],
            )
        ],
    )

    result = process_artifact(artifact)
    bridge = next(
        check
        for check in result.validation.checks
        if check.rule_id == "bs.assets_eq_liabilities_plus_temporary_equity_plus_equity"
    )

    assert bridge.status == "pass"
    assert bridge.left["value"] == "1000000000"
    assert bridge.right["value"] == "1000000000"


def test_kr_derives_total_equity_from_parent_and_nci_in_summary_table():
    artifact = ParsedArtifact(
        artifact_id="kr-derived-equity",
        market=Market.KR,
        company_id="KR:006400",
        ticker="006400",
        company_name="Samsung SDI",
        report_type="annual",
        fiscal_year=2025,
        period_end="2025-12-31",
        currency="KRW",
        tables=[
            ParsedTable(
                table_id="summary-bs",
                title="연결재무상태표",
                table_index=57,
                page_number=38,
                unit="KRW million",
                rows=[
                    ["", "2025"],
                    ["자산총계", "42,255,339"],
                    ["부채총계", "18,685,226"],
                    ["[지배기업지분]", "21,442,874"],
                    ["[비지배지분]", "2,127,239"],
                ],
            )
        ],
    )

    result = process_artifact(artifact)
    bridge = next(
        check
        for check in result.validation.checks
        if check.rule_id == "bs.assets_eq_liabilities_plus_temporary_equity_plus_equity"
    )

    assert bridge.status == "pass"
    assert any(
        item.canonical_name == "total_equity" and item.gaap_status == "derived_from_reported_components"
        for statement in result.extraction.statements
        for item in statement.items
    )
