from decimal import Decimal

from market_report_rules_service.contracts import financial_data_contract
from market_report_rules_service.markets.jp.rules import find_jp_label_rule
from market_report_rules_service.markets.kr.rules import find_kr_label_rule
from market_report_rules_service.models import AccountingStandard, Market, ParsedArtifact, ParsedTable
from market_report_rules_service.pipeline import process_artifact
from market_report_rules_service.statement_detection import detect_table_statement_type


def test_jp_label_rules_do_not_promote_detail_subtotals_to_statement_totals():
    assert find_jp_label_rule("資産合計").canonical_name == "total_assets"
    assert find_jp_label_rule("投資その他の資産合計") is None
    assert find_jp_label_rule("営業収益").canonical_name == "operating_revenue"
    assert find_jp_label_rule("営業外収益") is None


def test_jp_statement_detection_distinguishes_summary_and_formal_cash_flow_tables():
    summary_table = ParsedTable(
        table_id="summary",
        table_index=2,
        rows=[
            ["事業年度", "2020年度", "2021年度", "2022年度", "2023年度", "2024年度"],
            ["営業活動によるキャッシュ・フロー", "207,414", "280,090", "269,914", "307,249", "324,116"],
            ["投資活動によるキャッシュ・フロー", "△297,303", "△313,778", "△312,046", "△362,017", "△361,505"],
            ["財務活動によるキャッシュ・フロー", "82,888", "3,449", "26,572", "100,433", "12,871"],
        ],
    )
    formal_table = ParsedTable(
        table_id="formal-cf",
        table_index=93,
        rows=[
            ["", "前連結会計年度(自 2023年4月1日至 2024年3月31日)", "当連結会計年度(自 2024年4月1日至 2025年3月31日)"],
            ["営業活動によるキャッシュ・フロー", "307,249", "324,116"],
            ["投資活動によるキャッシュ・フロー", "△362,017", "△361,505"],
            ["財務活動によるキャッシュ・フロー", "100,433", "12,871"],
        ],
    )

    assert detect_table_statement_type(summary_table) is None
    assert detect_table_statement_type(formal_table) == "cash_flow_statement"


def test_eu_statement_detection_recognizes_operations_title_alias():
    table = ParsedTable(
        table_id="eu-ops",
        title="Consolidated statements of operations",
        rows=[
            ["Year ended December 31", "2024", "2025"],
            ["Total net sales", "28,262.9", "32,667.3"],
            ["Income before income taxes", "8,900.0", "11,300.0"],
            ["Net income", "7,571.6", "9,609.4"],
        ],
    )

    assert detect_table_statement_type(table) == "income_statement"


def test_kr_statement_detection_ignores_contents_and_recognizes_cash_flow_ocr():
    contents = ParsedTable(
        table_id="kr-contents",
        rows=[
            ["1. 연결재무상태표", "20"],
            ["2. 연결포괄손익계산서", "21"],
            ["3. 연결자본변동표", "22"],
            ["4. 연결한금흐를표", "24"],
        ],
        raw={"preview": "1. 연결재무상태표 20 2. 연결포괄손익계산서 21 3. 연결자본변동표 22 4. 연결한금흐를표 24"},
    )
    cash_flow = ParsedTable(
        table_id="kr-cf",
        rows=[
            ["과목", "제 49 기", "제 48 기"],
            ["III. 재무활동으로 대한 현금초를", "4,681,592", "1,065,666"],
            ["기말현금및현금성자산", "1,000", "900"],
        ],
        raw={"preview": "III. 재무활동으로 대한 현금초를 4,681,592 기말현금및현금성자산 1,000"},
    )

    assert detect_table_statement_type(contents) is None
    assert detect_table_statement_type(cash_flow) == "cash_flow_statement"
    assert find_kr_label_rule("III. 재무활동으로 대한 현금초를").canonical_name == "financing_cash_flow_net"


def test_jp_annual_required_statements_require_formal_statement_sources():
    artifact = ParsedArtifact(
        artifact_id="jp-summary-only-annual",
        market=Market.JP,
        company_id="JP:8802",
        ticker="8802",
        company_name="Summary Only Co.",
        report_type="annual_securities_report",
        report_form="有価証券報告書",
        fiscal_year=2025,
        period_end="2025-03-31",
        currency="JPY",
        tables=[
            ParsedTable(
                table_id="summary",
                table_index=2,
                page_number=2,
                rows=[
                    ["事業年度", "2023年度", "2024年度"],
                    ["営業収益", "(百万円)", "1,504,687", "1,579,812"],
                    ["親会社株主に帰属する当期純利益", "(百万円)", "168,432", "189,356"],
                    ["総資産", "(百万円)", "7,583,748", "7,996,591"],
                    ["営業活動によるキャッシュ・フロー", "(百万円)", "307,249", "324,116"],
                ],
            )
        ],
    )

    result = process_artifact(artifact)
    required = [
        check
        for check in result.validation.checks
        if check.rule_id.startswith("required.statement.")
    ]

    assert {check.status for check in required} == {"fail"}
    assert {check.reason for check in required} == {"statement_only_summary_or_note_facts_found_for_jp_annual_report"}


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
    candidate_rows = result.load_plan.rows if result.load_plan.can_import else result.load_plan.quarantine_rows
    assert any(row.table == "evidence_citations" for row in candidate_rows)
    if result.load_plan.can_import:
        assert result.load_plan.can_vector_ingest is True
        assert result.load_plan.quarantine_rows == []
        assert result.load_plan.promotion_decisions["canonical"].decision == "allow"
        assert result.load_plan.promotion_decisions["retrieval"].decision == "allow"
    else:
        assert result.load_plan.promotion_decisions["canonical"].decision in {"review", "block"}
        assert result.load_plan.blocked_reasons
    first_fact = result.extraction.statements[0].items[0]
    assert first_fact.evidence.source_type == "edinet_xbrl_fact"


def test_jp_accounting_standard_resolves_ifrs_and_jgaap_without_ifrs_default():
    ifrs_artifact = ParsedArtifact(
        artifact_id="jp-ifrs-standard",
        market=Market.JP,
        company_id="JP:7203",
        ticker="7203",
        company_name="IFRS Co.",
        report_type="annual",
        report_form="有価証券報告書",
        fiscal_year=2025,
        period_end="2025-03-31",
        currency="JPY",
        document_full={
            "edinet_facts": {
                "ifrs-full": {
                    "Assets": {"label": "Assets", "units": {"JPY": [{"val": 1000, "end": "2025-03-31"}]}},
                    "Revenue": {"label": "Revenue", "units": {"JPY": [{"val": 3000, "end": "2025-03-31"}]}},
                }
            }
        },
    )
    jgaap_artifact = ParsedArtifact(
        artifact_id="jp-jgaap-standard",
        market=Market.JP,
        company_id="JP:8802",
        ticker="8802",
        company_name="JGAAP Co.",
        report_type="annual",
        report_form="有価証券報告書",
        fiscal_year=2025,
        period_end="2025-03-31",
        currency="JPY",
        document_full={
            "edinet_facts": {
                "jpcrp_cor": {
                    "Assets": {"label": "資産合計", "units": {"JPY": [{"val": 1000, "end": "2025-03-31"}]}},
                    "NetSales": {"label": "売上高", "units": {"JPY": [{"val": 3000, "end": "2025-03-31"}]}},
                }
            }
        },
    )

    ifrs_result = process_artifact(ifrs_artifact)
    jgaap_result = process_artifact(jgaap_artifact)

    assert ifrs_result.extraction.accounting_standard == AccountingStandard.IFRS
    assert jgaap_result.extraction.accounting_standard == AccountingStandard.JGAAP
    assert {
        item.accounting_standard
        for statement in ifrs_result.extraction.statements
        for item in statement.items
    } == {AccountingStandard.IFRS}
    assert {
        item.accounting_standard
        for statement in jgaap_result.extraction.statements
        for item in statement.items
    } == {AccountingStandard.JGAAP}


def test_jp_unknown_accounting_standard_is_draft_only_and_requires_canonical_review():
    artifact = ParsedArtifact(
        artifact_id="jp-unknown-standard",
        market=Market.JP,
        company_id="JP:0000",
        ticker="0000",
        company_name="Unknown Standard Co.",
        report_type="integrated_report",
        fiscal_year=2025,
        period_end="2025-03-31",
        currency="JPY",
        tables=[
            ParsedTable(
                table_id="financial-summary",
                title="Financial Summary",
                table_index=1,
                page_number=12,
                rows=[
                    ["", "2025"],
                    ["Total assets", "1000"],
                    ["Total liabilities", "600"],
                    ["Total equity", "400"],
                    ["Revenue", "3000"],
                    ["Net income", "100"],
                    ["Cash flows from operating activities", "150"],
                ],
            )
        ],
    )

    result = process_artifact(artifact)
    check = next(check for check in result.validation.checks if check.rule_id == "accounting.standard.known")

    assert result.extraction.accounting_standard == AccountingStandard.UNKNOWN
    assert {
        item.accounting_standard
        for statement in result.extraction.statements
        for item in statement.items
    } == {AccountingStandard.UNKNOWN}
    assert result.validation.overall_status == "warning"
    assert check.status == "warning"
    assert check.raw["gate_decisions_by_target"]["draft"]["decision"] == "allow"
    assert check.raw["gate_decisions_by_target"]["canonical"]["decision"] == "review"
    assert check.raw["gate_decisions_by_target"]["retrieval"]["decision"] == "review"
    assert result.load_plan.can_import is False
    assert result.load_plan.can_vector_ingest is False
    assert result.load_plan.promotion_decisions["canonical"].decision == "review"
    assert result.load_plan.promotion_decisions["retrieval"].decision == "review"
    assert any("accounting.standard.known" in reason for reason in result.load_plan.blocked_reasons)
    assert not any(row.table in {"financial_statements", "financial_facts", "operating_metric_facts", "evidence_citations"} for row in result.load_plan.rows)
    assert any(row.table == "financial_facts" for row in result.load_plan.quarantine_rows)


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
    assert data["accounting_standard"] == "KIFRS"
    assert data["summary"]["statement_count"] == 3
    assert {
        item.accounting_standard
        for statement in result.extraction.statements
        for item in statement.items
    } == {AccountingStandard.KIFRS}
    assert not any(check.rule_id == "accounting.standard.known" for check in result.validation.checks)
    assert any(item.evidence.source_type == "dart_pdf_statement_table" for statement in result.extraction.statements for item in statement.items)
    if result.load_plan.can_import:
        assert result.load_plan.can_vector_ingest is True
        assert result.load_plan.quarantine_rows == []
        assert result.load_plan.promotion_decisions["canonical"].decision == "allow"
        assert result.load_plan.promotion_decisions["retrieval"].decision == "allow"
    else:
        assert result.load_plan.promotion_decisions["canonical"].decision in {"review", "block"}
        assert result.load_plan.blocked_reasons


def test_kr_explicit_unknown_accounting_standard_requires_review():
    artifact = ParsedArtifact(
        artifact_id="kr-unknown-standard",
        market=Market.KR,
        company_id="KR:00000000",
        ticker="000000",
        company_name="Unknown KR Co.",
        report_type="annual",
        fiscal_year=2025,
        period_end="2025-12-31",
        currency="KRW",
        metadata={"accounting_standard": "UNKNOWN"},
        tables=[
            ParsedTable(
                table_id="bs",
                title="연결재무상태표",
                table_index=1,
                page_number=120,
                unit="KRW million",
                rows=[
                    ["", "2025"],
                    ["자산총계", "1000"],
                    ["부채총계", "600"],
                    ["자본총계", "400"],
                ],
            )
        ],
    )

    result = process_artifact(artifact)
    check = next(check for check in result.validation.checks if check.rule_id == "accounting.standard.known")

    assert result.extraction.accounting_standard == AccountingStandard.UNKNOWN
    assert check.status == "warning"
    assert check.raw["gate_decisions_by_target"]["draft"]["decision"] == "allow"
    assert check.raw["gate_decisions_by_target"]["canonical"]["decision"] == "review"
    assert result.load_plan.can_import is False
    assert result.load_plan.can_vector_ingest is False
    assert result.load_plan.promotion_decisions["canonical"].decision in {"review", "block"}
    assert result.load_plan.promotion_decisions["retrieval"].decision in {"review", "block"}
    assert "accounting.standard.known" in result.load_plan.promotion_decisions["canonical"].review_rule_ids
    assert not any(row.table in {"financial_statements", "financial_facts", "operating_metric_facts", "evidence_citations"} for row in result.load_plan.rows)
    assert any(row.table == "financial_facts" for row in result.load_plan.quarantine_rows)


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
