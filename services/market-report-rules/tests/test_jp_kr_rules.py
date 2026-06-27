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
