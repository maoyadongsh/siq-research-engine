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
from market_report_rules_service.load_plan import build_load_plan
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

    skipped_optional = next(
        check
        for check in validation.checks
        if check.rule_id == "bs.current_plus_non_current_assets"
    )
    assert skipped_optional.status == "skipped"
    assert skipped_optional.raw["gate"]["severity"] == "observe"
    assert skipped_optional.raw["gate_decisions_by_target"]["canonical"]["decision"] == "allow"


def test_cn_bridge_checks_keep_existing_period_best_selection():
    validation = validate_extraction(_extraction(Market.CN))
    bridge = next(
        check
        for check in validation.checks
        if check.rule_id == "bs.assets_eq_liabilities_plus_temporary_equity_plus_equity"
    )

    assert bridge.status == "fail"
    assert bridge.left["value"] == "1200"
    assert bridge.raw["gate_contract_version"] == "risk_calibrated_gate_v1"
    assert bridge.raw["gate_decisions_by_target"]["draft"]["decision"] == "allow"
    assert bridge.raw["gate_decisions_by_target"]["canonical"]["decision"] == "block"
    assert bridge.raw["gate_decisions_by_target"]["retrieval"]["decision"] == "block"
    canonical_gate = next(gate for gate in bridge.raw["gate_results"] if gate["target"] == "canonical")
    assert canonical_gate["severity"] == "hard"
    assert canonical_gate["decision"] == "block"
    assert {"rule_id", "severity", "reason", "target", "evidence_refs"} <= set(canonical_gate)


def test_load_plan_quarantines_canonical_rows_when_validation_blocks():
    extraction = _extraction(Market.CN)
    validation = validate_extraction(extraction)

    plan = build_load_plan(extraction, validation)

    assert plan.can_import is False
    assert plan.can_vector_ingest is False
    assert plan.promotion_decisions["canonical"].decision == "block"
    assert plan.promotion_decisions["retrieval"].decision == "block"
    assert any(reason.startswith("canonical:block:") for reason in plan.blocked_reasons)
    assert any(row.table == "financial_data_artifacts" for row in plan.rows)
    assert any(row.table == "financial_checks_artifacts" for row in plan.rows)
    assert not any(row.table == "financial_facts" for row in plan.rows)
    assert any(row.table == "financial_facts" for row in plan.quarantine_rows)
    assert any(row.table == "evidence_citations" for row in plan.quarantine_rows)


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


def test_eu_kr_required_statement_missing_is_warning_when_parser_coverage_incomplete():
    for market in (Market.EU, Market.KR):
        extraction = ExtractionResult(
            rule_version="test",
            profile_id="test",
            artifact_id=f"{market.value}-coverage-incomplete",
            market=market,
            accounting_standard=AccountingStandard.IFRS,
            company_id=f"{market.value}:TEST",
            ticker="TEST",
            report_type="annual",
            report_form="annual",
            fiscal_year=2025,
            fiscal_period="FY",
            period_end="2025-12-31",
            statements=[],
            warnings=["parser table quality: no mapped financial facts were extracted"],
        )

        validation = validate_extraction(extraction)
        required = [check for check in validation.checks if check.rule_id.startswith("required.statement.")]

        assert {check.status for check in required} == {"warning"}
        assert all("parser_coverage_incomplete" in (check.reason or "") for check in required)
        assert {check.raw["gate"]["severity"] for check in required} == {"soft"}
        assert {check.raw["gate_decisions_by_target"]["draft"]["decision"] for check in required} == {"allow"}
        assert {check.raw["gate_decisions_by_target"]["canonical"]["decision"] for check in required} == {"review"}
        assert validation.overall_status == "warning"
