from __future__ import annotations

from hk_evidence_lib import (
    _extraction_from_financial_data_contract,
    _infer_unit,
    infer_hk_reporting_currency,
    parsed_tables_from_document_full,
)
from market_report_rules_service.models import AccountingStandard, Market, ParsedArtifact


def test_parsed_tables_from_document_full_reads_nested_enhanced_raw_preview():
    enhanced = {
        "tables": [
            {
                "table_id": "hk_table_0001",
                "table_index": 1,
                "title": None,
                "raw": {
                    "pdf_page_number": 42,
                    "structure": {
                        "header_preview": [
                            "Note | 2026HK$M | 2025HK$M",
                            "Revenue | 5 | 13,938 | 14,223",
                            "Total assets | 209,556 | 220,413",
                        ]
                    },
                    "preview": "Revenue 13,938 14,223",
                },
            }
        ]
    }

    tables = parsed_tables_from_document_full({"content_list": []}, enhanced)

    assert len(tables) == 1
    assert tables[0].page_number == 42
    assert tables[0].rows[1] == ["Revenue", "5", "13,938", "14,223"]


def test_parser_contract_rmb_unit_repairs_stale_hkd_currency():
    artifact = ParsedArtifact(
        artifact_id="hk-contract-rmb",
        market=Market.HK,
        company_id="HK:00700",
        ticker="00700",
        report_type="annual",
        fiscal_year=2025,
        fiscal_period="FY",
        period_end="2025-12-31",
        accounting_standard=AccountingStandard.HKFRS,
        currency="HKD",
    )
    financial_data = {
        "statements": [
            {
                "statement_id": "income",
                "statement_type": "income_statement",
                "unit": "RMB million",
                "currency": "HKD",
                "items": [
                    {
                        "canonical_name": "operating_revenue",
                        "name": "Revenue",
                        "unit": "RMB million",
                        "currency": "HKD",
                        "values": {"2025-12-31": "751766"},
                    }
                ],
            }
        ]
    }

    extraction = _extraction_from_financial_data_contract(financial_data, artifact)
    statement = extraction.statements[0]

    assert statement.currency == "CNY"
    assert statement.items[0].currency == "CNY"
    assert statement.items[0].unit == "RMB million"


def test_infer_unit_recognizes_chinese_reporting_currency_units():
    unit = _infer_unit("合并损益表", [["", "2025 人民币千元"], ["收入", "100"]])

    assert unit is not None
    assert "人民币" in unit


def test_hk_reporting_currency_requires_explicit_presentation_evidence():
    assert infer_hk_reporting_currency({}, "The currency we report in is US dollars.") == "USD"
    assert infer_hk_reporting_currency({}, "The consolidated financial statements are presented in Hong Kong dollars.") == "HKD"
    assert infer_hk_reporting_currency({}, "The consolidated financial statements are presented in Renminbi (RMB).") == "CNY"
    assert infer_hk_reporting_currency({}, "The report contains $m amounts but no presentation declaration.") is None
    assert infer_hk_reporting_currency({}, "The consolidated financial statements are presented in US dollars. The presentation currency is Hong Kong dollars.") is None


def test_hk_unit_parser_separates_bare_dollar_scale_from_narrative_amount():
    assert _infer_unit("Summary consolidated income statement", [["", "2025$m", "2024$m"]]) == "million"
    assert _infer_unit("Consolidated income statement", [["", "2025RMB’Million", "2024RMB’Million"]]) == "RMB million"
    assert _infer_unit("Highlights", [["Profit before tax US$1.55bn (FY24 US$1.5bn)", "10"]]) is None
