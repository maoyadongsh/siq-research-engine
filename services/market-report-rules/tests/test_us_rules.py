from market_report_rules_service.contracts import financial_checks_contract, financial_data_contract
from market_report_rules_service.evidence_package import (
    build_quality_gates,
    build_quality_report,
    source_map_from_financial_data,
)
from market_report_rules_service.models import Market, ParsedArtifact
from market_report_rules_service.pipeline import process_artifact


def test_us_companyfacts_extracts_three_statement_metrics_and_sec_evidence(tmp_path):
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
                    "LiabilitiesAndStockholdersEquity": {"label": "Liabilities and equity", "units": {"USD": [{"val": 1000, "end": "2025-09-27", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-31", "accn": "a1"}]}},
                    "RevenueFromContractWithCustomerExcludingAssessedTax": {"label": "Revenue", "units": {"USD": [{"val": 3900, "end": "2025-09-27", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-31", "accn": "a1"}]}},
                    "NetIncomeLoss": {"label": "Net income", "units": {"USD": [{"val": 100, "end": "2025-09-27", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-31", "accn": "a1"}]}},
                    "NetCashProvidedByUsedInOperatingActivities": {"label": "Operating cash flow", "units": {"USD": [{"val": 120, "end": "2025-09-27", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-31", "accn": "a1"}]}},
                }
            }
        },
    )

    result = process_artifact(artifact)
    data = financial_data_contract(result.extraction)
    checks = financial_checks_contract(result.validation)

    assert result.load_plan.target_database == "siq"
    assert result.load_plan.target_schema == "sec_us"
    assert data["summary"]["statement_count"] == 3
    assert any(statement["statement_type"] == "balance_sheet" for statement in data["statements"])
    first_evidence = data["statements"][0]["items"][0]["evidence_targets"]
    assert first_evidence
    assert result.validation.overall_status == "pass"
    assert checks["warnings"] == []
    assert checks["advisories"] == ["Use standard three-statement bridge checks."]

    manifest = {
        "market": "US",
        "filing_id": artifact.artifact_id,
        "quality_status": checks["overall_status"],
        "artifact_hashes": {},
    }
    source_map = source_map_from_financial_data(
        manifest=manifest,
        financial_data=data,
        package_dir=tmp_path,
    )
    quality = build_quality_report(
        manifest=manifest,
        financial_data=data,
        financial_checks=checks,
        section_count=1,
        table_count=0,
        raw_fact_count=7,
        source_map=source_map,
    )
    gates = build_quality_gates(
        tmp_path,
        manifest=manifest,
        quality=quality,
        financial_data=data,
        financial_checks=checks,
    )

    assert quality["rule_warnings"] == []
    assert quality["rule_advisories"] == ["Use standard three-statement bridge checks."]
    assert gates["rule_advisories"] == quality["rule_advisories"]
    assert "package.parser_or_rule_warnings.present" not in {
        gate["rule_id"] for gate in gates["gate_results"]
    }
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
