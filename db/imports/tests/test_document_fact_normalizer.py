import importlib.util
import sys
from pathlib import Path


def _load_module():
    source = Path(__file__).resolve().parents[1] / "backtests" / "document_fact_normalizer.py"
    spec = importlib.util.spec_from_file_location("document_fact_normalizer_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_normalizes_financial_data_values_and_enriches_table_evidence():
    module = _load_module()
    document_full = {
        "financial_data": {
            "market": "HK",
            "company_id": "HK:00005",
            "reporting_currency": "HKD",
            "statements": [
                {
                    "statement_type": "income_statement",
                    "unit": "million",
                    "currency": "HKD",
                    "scale": "1e6",
                    "items": [
                        {
                            "canonical_name": "revenue",
                            "name": "Revenue",
                            "values": {"FY2025": "100"},
                            "raw_values": {"FY2025": "100.0"},
                            "sources": {"FY2025": {"table_index": 7}},
                        },
                        {
                            "canonical_name": "net_profit",
                            "period_key": "FY2025",
                            "value": "10",
                            "raw_value": "10.0",
                            "evidence": {"table_index": 7, "quote_text": "profit"},
                        },
                    ],
                }
            ],
        },
        "content_list_enhanced": {
            "tables": [
                {
                    "table_index": 7,
                    "page_number": 12,
                    "bbox": [1, 2, 3, 4],
                }
            ]
        },
    }

    facts = module.normalize_document_facts(document_full)

    assert len(facts) == 2
    assert facts[0].canonical_name == "revenue"
    assert facts[0].period_key == "FY2025"
    assert facts[0].currency == "HKD"
    assert facts[0].reporting_currency == "HKD"
    assert facts[0].presentation_currency == "HKD"
    assert facts[0].scale == "1e6"
    assert facts[0].evidence["page_number"] == 12
    assert facts[0].evidence["bbox"] == [1, 2, 3, 4]
    assert module.has_reviewable_evidence(facts[1].evidence)


def test_normalizes_sec_facts_and_us_cik_identity():
    module = _load_module()
    document_full = {
        "filing": {
            "market": "US",
            "filing_id": "US:1773751:0001773751-25-000001",
            "ticker": "HIMS",
            "period_end": "2025-12-31",
            "form": "10-K",
            "fiscal_year": 2025,
        },
        "source": {"source_url": "https://www.sec.gov/Archives/edgar/data/1773751/report.htm"},
        "facts": [
            {
                "concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                "label": "Revenue",
                "value_numeric": 123,
                "value_text": "123",
                "unit": "USD",
                "currency": "USD",
                "context_ref": "CY2025",
                "html_anchor": "#revenue",
            }
        ],
    }

    identity = module.document_identity(document_full)
    facts = module.normalize_document_facts(document_full)

    assert identity["company_id"] == "US:CIK0001773751"
    assert facts[0].statement_type == "xbrl_fact"
    assert facts[0].period_key == "CY2025"
    assert facts[0].value == 123
    assert facts[0].raw_value == "123"
    assert facts[0].fact_currency == "USD"
    assert facts[0].evidence["html_anchor"] == "#revenue"
    assert facts[0].evidence["source_url"].endswith("report.htm")


def test_recovers_currency_and_unit_from_noisy_pdf_table_headers():
    module = _load_module()
    document_full = {
        "financial_data": {
            "statements": [
                {
                    "statement_type": "income_statement",
                    "currency": "HKD",
                    "unit": "2025RMB’Million 2024RMB’Million Revenues",
                    "scale": "1000000",
                    "items": [
                        {
                            "canonical_name": "operating_revenue",
                            "period_key": "2025-12-31",
                            "value": "751766",
                            "unit": "2025RMB’Million 2024RMB’Million Revenues",
                            "currency": "HKD",
                            "scale": "1000000",
                        }
                    ],
                },
                {
                    "statement_type": "income_statement",
                    "currency": "JPY",
                    "unit": "(百万円)",
                    "scale": "1000000",
                    "items": [
                        {
                            "canonical_name": "operating_revenue",
                            "period_key": "2025-03-31",
                            "value": "48036704",
                            "unit": "金額：百万円 3月31日に終了した1年間",
                            "currency": "JPY",
                            "scale": "1000000",
                        }
                    ],
                },
            ]
        }
    }

    facts = module.normalize_document_facts(document_full)

    assert facts[0].unit == "RMB million"
    assert facts[0].currency == "RMB"
    assert facts[0].fact_currency == "RMB"
    assert facts[0].reporting_currency == "RMB"
    assert facts[0].presentation_currency == "RMB"
    assert facts[1].unit == "JPY million"
    assert facts[1].currency == "JPY"


def test_recovers_explicit_million_scale_and_pdf_page_locator():
    module = _load_module()
    document_full = {
        "financial_data": {
            "market": "EU",
            "statements": [
                {
                    "statement_type": "balance_sheet",
                    "unit": "EUR million",
                    "currency": "EUR",
                    "scale": 1.0,
                    "items": [
                        {
                            "canonical_name": "total_assets",
                            "values": {"2025-12-31": 50566.6},
                            "raw_values": {"2025-12-31": "50,566.6"},
                            "sources": {"2025-12-31": {"table_index": 88}},
                        }
                    ],
                }
            ],
        },
        "content_list_enhanced": {
            "tables": [
                {
                    "table_index": 88,
                    "pdf_page_number": 278,
                    "bbox": [6, 207, 494, 846],
                }
            ]
        },
    }

    fact = module.normalize_document_facts(document_full)[0]

    assert fact.unit == "EUR million"
    assert fact.scale == 1000000
    assert fact.evidence["page_number"] == 278
    assert fact.evidence["bbox"] == [6, 207, 494, 846]


def test_fact_matching_hash_and_numeric_helpers_are_stable():
    module = _load_module()
    fact = module.NormalizedFact(
        statement_type="balance_sheet",
        period_key="FY2025",
        canonical_name="total_assets",
        value="100.00",
        raw_value="100.00",
        unit="million",
        currency="CNY",
        evidence={"page_number": 3},
    )

    expected = module.assertion_to_expected_fact(
        {"statement_type": "balance_sheet", "canonical_name": "total_assets", "expected_value": "100"},
        {"period_key": "FY2025"},
    )

    assert module.find_fact([fact], expected) is fact
    assert module.decimal_equal("100.00", "100")
    assert module.decimal_equal("566,942,110", "566942110")
    assert module.value_within_tolerance("101", "100", "0.02")
    assert module.fact_content_hash([fact]) == module.fact_content_hash([fact])
    assert module.stable_rows_hash([{"b": 2, "a": 1}, {"a": 0}]) == module.stable_rows_hash(
        [{"a": 0}, {"a": 1, "b": 2}]
    )
