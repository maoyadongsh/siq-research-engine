from decimal import Decimal

from market_report_rules_service.load_plan import build_load_plan
from market_report_rules_service.models import (
    AccountingStandard,
    CheckStatus,
    EvidenceRef,
    ExtractedFact,
    ExtractionResult,
    FinancialStatement,
    Market,
    StatementType,
    ValidationResult,
)
from market_report_rules_service.quality_gate_adapter import apply_package_quality_gates


def _extraction() -> ExtractionResult:
    fact = ExtractedFact(
        canonical_name="total_assets",
        local_name="total_assets",
        statement_type=StatementType.BALANCE_SHEET,
        value=Decimal("1000"),
        raw_value="1000",
        unit="HKD million",
        currency="HKD",
        period_key="2025-12-31",
        period_end="2025-12-31",
        fiscal_year=2025,
        fiscal_period="FY",
        scale=Decimal("1"),
        market=Market.HK,
        accounting_standard=AccountingStandard.HKFRS,
        confidence=Decimal("0.95"),
        evidence=EvidenceRef(
            source_type="pdf_statement_table",
            source_id="table-1",
            table_index=1,
            quote_text="total_assets | 1000",
        ),
    )
    return ExtractionResult(
        rule_version="test",
        profile_id="test",
        artifact_id="hk-quality-gate-adapter",
        market=Market.HK,
        accounting_standard=AccountingStandard.HKFRS,
        company_id="HK:00001",
        ticker="00001",
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
                items=[fact],
            )
        ],
    )


def test_package_quality_gates_block_load_plan_candidates(monkeypatch, tmp_path):
    extraction = _extraction()
    validation = ValidationResult(
        rule_version="test",
        profile_id="test",
        artifact_id=extraction.artifact_id,
        market=extraction.market,
        overall_status=CheckStatus.PASS,
        summary={"pass": 1, "warning": 0, "fail": 0, "skipped": 0},
        checks=[],
    )
    decisions = {
        target: {
            "target": target,
            "promotion_target": target,
            "decision": "block" if target in {"canonical", "retrieval", "production"} else "allow",
            "severity": "hard" if target in {"canonical", "retrieval", "production"} else "observe",
            "rule_ids": ["package.evidence.unresolvable"],
            "review_rule_ids": [],
            "blocking_rule_ids": ["package.evidence.unresolvable"] if target in {"canonical", "retrieval", "production"} else [],
            "reasons": ["unresolvable evidence"],
        }
        for target in ("draft", "review", "canonical", "retrieval", "production")
    }

    monkeypatch.setattr(
        "market_report_rules_service.quality_gate_adapter.build_quality_gates",
        lambda _package_dir: {
            "gate_contract_version": "risk_calibrated_gate_v1",
            "overall_status": "fail",
            "canonical_decision": "block",
            "retrieval_decision": "block",
            "decisions_by_target": decisions,
            "gate_results": [
                {
                    "rule_id": "package.evidence.unresolvable",
                    "severity": "hard",
                    "decision": "block",
                    "target": "canonical",
                    "promotion_target": "canonical",
                    "reason": "unresolvable evidence",
                    "evidence_refs": [],
                }
            ],
            "evidence_resolvability_ratio": 0,
            "unresolvable_evidence_count": 1,
        },
    )

    updated = apply_package_quality_gates(validation, package_dir=tmp_path)
    plan = build_load_plan(extraction, updated)

    assert updated.overall_status == CheckStatus.FAIL
    assert any(check.rule_id == "package.quality_gates" for check in updated.checks)
    assert plan.can_import is False
    assert plan.can_vector_ingest is False
    assert plan.promotion_decisions["canonical"].decision == "block"
    assert any("package.evidence.unresolvable" in reason for reason in plan.blocked_reasons)
    assert not any(row.table == "financial_facts" for row in plan.rows)
    assert any(row.table == "financial_facts" for row in plan.quarantine_rows)
