from decimal import Decimal

from market_report_rules_service.models import (
    AccountingStandard,
    EvidenceRef,
    ExtractedFact,
    ExtractionResult,
    FinancialStatement,
    Market,
    StatementType,
)
from market_report_rules_service.validation import validate_extraction


def _balance_fact(
    market: Market,
    canonical_name: str,
    value: str,
    *,
    confidence: str,
    table_index: int,
) -> ExtractedFact:
    return ExtractedFact(
        canonical_name=canonical_name,
        local_name=canonical_name,
        statement_type=StatementType.BALANCE_SHEET,
        value=Decimal(value),
        raw_value=value,
        unit="EUR million" if market == Market.EU else "CNY",
        currency="EUR" if market == Market.EU else "CNY",
        period_key="2025-12-31",
        period_end="2025-12-31",
        fiscal_year=2025,
        fiscal_period="FY",
        scale=Decimal("1"),
        market=market,
        accounting_standard=AccountingStandard.IFRS if market == Market.EU else AccountingStandard.CASBE,
        confidence=Decimal(confidence),
        evidence=EvidenceRef(
            source_type="pdf_statement_table",
            source_id=f"table-{table_index}",
            table_index=table_index,
            quote_text=f"{canonical_name} | {value}",
        ),
    )


def _extraction(market: Market) -> ExtractionResult:
    facts = [
        _balance_fact(market, "total_assets", "1000", confidence="0.80", table_index=1),
        _balance_fact(market, "total_liabilities", "600", confidence="0.80", table_index=1),
        _balance_fact(market, "total_equity", "400", confidence="0.80", table_index=1),
        _balance_fact(market, "total_assets", "1200", confidence="0.95", table_index=20),
    ]
    return ExtractionResult(
        rule_version="test",
        profile_id="test",
        artifact_id=f"{market.value}-source-selection",
        market=market,
        accounting_standard=AccountingStandard.IFRS if market == Market.EU else AccountingStandard.CASBE,
        company_id=f"{market.value}:TEST",
        ticker="TEST",
        report_type="annual",
        report_form="annual",
        fiscal_year=2025,
        fiscal_period="FY",
        period_end="2025-12-31",
        statements=[
            FinancialStatement(
                statement_id="balance_sheet",
                statement_type=StatementType.BALANCE_SHEET,
                statement_name="Balance Sheet",
                items=facts,
            )
        ],
    )


def test_non_cn_bridge_checks_prefer_source_consistent_statement_group():
    validation = validate_extraction(_extraction(Market.EU))
    bridge = next(
        check
        for check in validation.checks
        if check.rule_id == "bs.assets_eq_liabilities_plus_temporary_equity_plus_equity"
    )

    assert bridge.status == "pass"
    assert bridge.left["value"] == "1000"
    assert bridge.raw["source_selection"]["reason"] == "source_consistent_bridge_candidate"


def test_cn_bridge_checks_keep_existing_period_best_selection():
    validation = validate_extraction(_extraction(Market.CN))
    bridge = next(
        check
        for check in validation.checks
        if check.rule_id == "bs.assets_eq_liabilities_plus_temporary_equity_plus_equity"
    )

    assert bridge.status == "fail"
    assert bridge.left["value"] == "1200"


def test_non_cn_component_balance_bridge_downgrades_when_total_bridge_passes():
    facts = [
        _balance_fact(Market.EU, "total_assets", "1000", confidence="0.80", table_index=1),
        _balance_fact(Market.EU, "total_liabilities", "200", confidence="0.80", table_index=1),
        _balance_fact(Market.EU, "total_equity", "300", confidence="0.80", table_index=1),
        _balance_fact(Market.EU, "total_liabilities_and_equity", "1000", confidence="0.80", table_index=1),
    ]
    extraction = _extraction(Market.EU).model_copy(
        update={
            "statements": [
                FinancialStatement(
                    statement_id="balance_sheet",
                    statement_type=StatementType.BALANCE_SHEET,
                    statement_name="Balance Sheet",
                    items=facts,
                )
            ]
        }
    )

    validation = validate_extraction(extraction)
    component_bridge = next(
        check
        for check in validation.checks
        if check.rule_id == "bs.assets_eq_liabilities_plus_temporary_equity_plus_equity"
    )
    total_bridge = next(
        check
        for check in validation.checks
        if check.rule_id == "bs.assets_eq_liabilities_and_equity"
    )

    assert component_bridge.status == "warning"
    assert component_bridge.reason == "alternative_total_liabilities_and_equity_bridge_passed"
    assert total_bridge.status == "pass"
