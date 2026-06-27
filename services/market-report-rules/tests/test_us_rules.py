from market_report_rules_service.contracts import financial_data_contract
from market_report_rules_service.models import Market, ParsedArtifact
from market_report_rules_service.pipeline import process_artifact


def test_us_companyfacts_extracts_three_statement_metrics_and_sec_evidence():
    artifact = ParsedArtifact(
        artifact_id="us-aapl-2025-10k",
        market=Market.US,
        company_id="US:AAPL",
        ticker="AAPL",
        company_name="Apple Inc.",
        report_type="annual",
        report_form="10-K",
        fiscal_year=2025,
        period_end="2025-09-27",
        source_url="https://www.sec.gov/Archives/edgar/data/320193/example.htm",
        document_full={
            "sec_companyfacts": {
                "us-gaap": {
                    "Assets": {"label": "Assets", "units": {"USD": [{"val": 1000, "end": "2025-09-27", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-31", "accn": "a1"}]}},
                    "Liabilities": {"label": "Liabilities", "units": {"USD": [{"val": 600, "end": "2025-09-27", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-31", "accn": "a1"}]}},
                    "StockholdersEquity": {"label": "Equity", "units": {"USD": [{"val": 400, "end": "2025-09-27", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-31", "accn": "a1"}]}},
                    "RevenueFromContractWithCustomerExcludingAssessedTax": {"label": "Revenue", "units": {"USD": [{"val": 3900, "end": "2025-09-27", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-31", "accn": "a1"}]}},
                    "NetIncomeLoss": {"label": "Net income", "units": {"USD": [{"val": 100, "end": "2025-09-27", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-31", "accn": "a1"}]}},
                    "NetCashProvidedByUsedInOperatingActivities": {"label": "Operating cash flow", "units": {"USD": [{"val": 120, "end": "2025-09-27", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-31", "accn": "a1"}]}},
                }
            }
        },
    )

    result = process_artifact(artifact)
    data = financial_data_contract(result.extraction)

    assert result.load_plan.target_database == "siq"
    assert result.load_plan.target_schema == "sec_us"
    assert data["summary"]["statement_count"] == 3
    assert any(statement["statement_type"] == "balance_sheet" for statement in data["statements"])
    first_evidence = data["statements"][0]["items"][0]["evidence_targets"]
    assert first_evidence
    assert result.validation.overall_status in {"pass", "warning"}
    assert any(row.table == "evidence_citations" for row in result.load_plan.rows)


def test_us_10q_keeps_qtd_and_ytd_duration_types_separate():
    artifact = ParsedArtifact(
        artifact_id="us-demo-2025-q3",
        market=Market.US,
        company_id="US:DEMO",
        ticker="DEMO",
        report_type="quarterly",
        report_form="10-Q",
        fiscal_year=2025,
        fiscal_period="Q3",
        period_end="2025-09-30",
        document_full={
            "sec_companyfacts": {
                "us-gaap": {
                    "RevenueFromContractWithCustomerExcludingAssessedTax": {
                        "label": "Revenue",
                        "units": {
                            "USD": [
                                {"val": 90, "start": "2025-07-01", "end": "2025-09-30", "fy": 2025, "fp": "Q3", "form": "10-Q", "filed": "2025-11-01", "accn": "q3"},
                                {"val": 250, "start": "2025-01-01", "end": "2025-09-30", "fy": 2025, "fp": "Q3", "form": "10-Q", "filed": "2025-11-01", "accn": "q3"},
                            ]
                        },
                    }
                }
            }
        },
    )

    result = process_artifact(artifact, include_load_plan=False)
    revenue_items = [
        item
        for statement in result.extraction.statements
        for item in statement.items
        if item.canonical_name == "operating_revenue"
    ]

    assert {item.qtd_ytd_type for item in revenue_items} == {"qtd", "ytd"}
    assert {item.value for item in revenue_items} == {90, 250}
