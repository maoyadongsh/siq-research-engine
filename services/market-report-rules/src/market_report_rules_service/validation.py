from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

from .industry_profiles import get_industry_profile
from .models import (
    CheckStatus,
    EvidenceRef,
    ExtractedFact,
    ExtractionResult,
    StatementType,
    ValidationCheck,
    ValidationResult,
)


def validate_extraction(extraction: ExtractionResult) -> ValidationResult:
    checks: list[ValidationCheck] = []
    facts = list(_all_facts(extraction))

    checks.extend(_required_metric_checks(extraction, facts))
    checks.extend(_required_statement_checks(extraction))
    checks.extend(_financial_bridge_checks(facts))
    checks.extend(_dimension_scope_checks(facts))
    checks.extend(_operating_metric_checks(extraction.operating_metrics))
    checks.extend(_evidence_checks(facts))

    summary = {status.value: 0 for status in CheckStatus}
    for check in checks:
        summary[check.status.value] = summary.get(check.status.value, 0) + 1
    overall = CheckStatus.FAIL if summary.get(CheckStatus.FAIL.value) else (
        CheckStatus.PASS if summary.get(CheckStatus.PASS.value) else CheckStatus.SKIPPED
    )
    warnings = list(extraction.warnings)
    profile = get_industry_profile(extraction.industry_profile)
    warnings.extend(profile.validation_notes)
    return ValidationResult(
        rule_version=extraction.rule_version,
        profile_id=extraction.profile_id,
        artifact_id=extraction.artifact_id,
        market=extraction.market,
        industry_profile=extraction.industry_profile,
        overall_status=overall,
        summary=summary,
        checks=checks,
        warnings=warnings,
    )


def _all_facts(extraction: ExtractionResult) -> Iterable[ExtractedFact]:
    for statement in extraction.statements:
        yield from statement.items
    yield from extraction.key_metrics
    yield from extraction.operating_metrics


def _required_metric_checks(extraction: ExtractionResult, facts: list[ExtractedFact]) -> list[ValidationCheck]:
    profile = get_industry_profile(extraction.industry_profile)
    present = {fact.canonical_name for fact in facts}
    checks: list[ValidationCheck] = []
    for canonical_name in profile.required_financial_metrics:
        missing_status = _missing_required_metric_status(extraction.report_type, extraction.report_form)
        checks.append(
            ValidationCheck(
                rule_id=f"required.{profile.profile_id}.{canonical_name}",
                rule_name=f"Required metric present: {canonical_name}",
                statement_type="document",
                period=extraction.period_end.isoformat() if extraction.period_end else None,
                status=CheckStatus.PASS if canonical_name in present else missing_status,
                inputs=[canonical_name],
                left={"metric": canonical_name, "present": canonical_name in present},
                right={"industry_profile": profile.profile_id},
                reason=None if canonical_name in present else "required_metric_missing_for_industry_profile",
            )
        )
    return checks


def _required_statement_checks(extraction: ExtractionResult) -> list[ValidationCheck]:
    present = {statement.statement_type for statement in extraction.statements}
    checks: list[ValidationCheck] = []
    required = (StatementType.BALANCE_SHEET, StatementType.INCOME_STATEMENT, StatementType.CASH_FLOW_STATEMENT)
    for statement_type in required:
        if statement_type in present:
            status = CheckStatus.PASS
        else:
            status = _missing_statement_status(extraction.report_type, extraction.report_form)
        checks.append(
            ValidationCheck(
                rule_id=f"required.statement.{statement_type.value}",
                rule_name=f"Required statement present: {statement_type.value}",
                statement_type="document",
                period=extraction.period_end.isoformat() if extraction.period_end else None,
                status=status,
                inputs=[statement_type.value],
                left={"statement_type": statement_type.value, "present": statement_type in present},
                right={"report_type": extraction.report_type, "report_form": extraction.report_form},
                reason=None if status == CheckStatus.PASS else "statement_missing_for_report_type",
            )
        )
    return checks


def _financial_bridge_checks(facts: list[ExtractedFact]) -> list[ValidationCheck]:
    by_period = _facts_by_period(facts)
    checks: list[ValidationCheck] = []
    for period, bucket in by_period.items():
        checks.append(
            _bridge_check(
                "bs.assets_eq_liabilities_plus_temporary_equity_plus_equity",
                "Assets = liabilities + temporary/redeemable equity + equity",
                StatementType.BALANCE_SHEET,
                period,
                bucket,
                "total_assets",
                ("total_liabilities", "redeemable_noncontrolling_interest", "total_equity"),
                missing_as_zero=("redeemable_noncontrolling_interest",),
            )
        )
        checks.append(
            _bridge_check(
                "bs.assets_eq_liabilities_and_equity",
                "Assets = liabilities and equity total",
                StatementType.BALANCE_SHEET,
                period,
                bucket,
                "total_assets",
                ("total_liabilities_and_equity",),
            )
        )
        checks.append(
            _bridge_check(
                "bs.current_plus_non_current_assets",
                "Assets = current assets + non-current assets",
                StatementType.BALANCE_SHEET,
                period,
                bucket,
                "total_assets",
                ("current_assets", "non_current_assets"),
                optional=True,
            )
        )
        checks.append(
            _bridge_check(
                "bs.current_plus_non_current_liabilities",
                "Liabilities = current liabilities + non-current liabilities",
                StatementType.BALANCE_SHEET,
                period,
                bucket,
                "total_liabilities",
                ("current_liabilities", "non_current_liabilities"),
                optional=True,
            )
        )
        checks.append(
            _bridge_check(
                "bs.parent_equity_plus_nci",
                "Equity = parent equity + non-controlling interests",
                StatementType.BALANCE_SHEET,
                period,
                bucket,
                "total_equity",
                ("parent_equity", "nci_equity"),
                optional=True,
            )
        )
        checks.append(
            _bridge_check(
                "is.gross_profit_bridge",
                "Gross profit = revenue - cost of sales",
                StatementType.INCOME_STATEMENT,
                period,
                bucket,
                "gross_profit",
                ("operating_revenue", "cost_of_sales"),
                signs=(Decimal("1"), Decimal("-1")),
                optional=True,
            )
        )
        checks.append(
            _bridge_check(
                "is.net_profit_bridge",
                "Net profit = profit before tax - income tax",
                StatementType.INCOME_STATEMENT,
                period,
                bucket,
                "net_profit",
                ("total_profit", "income_tax_expense"),
                signs=(Decimal("1"), Decimal("-1")),
                optional=True,
            )
        )
        checks.append(
            _bridge_check(
                "is.net_profit_attribution",
                "Net profit = parent net profit + non-controlling interests profit",
                StatementType.INCOME_STATEMENT,
                period,
                bucket,
                "net_profit",
                ("parent_net_profit", "nci_profit"),
                optional=True,
            )
        )
        checks.append(
            _bridge_check(
                "cf.net_cash_change_bridge",
                "Net cash change = operating + investing + financing + FX",
                StatementType.CASH_FLOW_STATEMENT,
                period,
                bucket,
                "cash_equivalents_net_increase",
                ("operating_cash_flow_net", "investing_cash_flow_net", "financing_cash_flow_net", "fx_effect_cash"),
                optional=True,
                missing_as_zero=("fx_effect_cash",),
            )
        )
        checks.append(
            _bridge_check(
                "cf.ending_cash_bridge",
                "Ending cash = beginning cash + net cash change",
                StatementType.CASH_FLOW_STATEMENT,
                period,
                bucket,
                "cash_equivalents_ending",
                ("cash_equivalents_beginning", "cash_equivalents_net_increase"),
                optional=True,
            )
        )
        checks.append(_cash_balance_soft_check(period, bucket))
    return checks


def _facts_by_period(facts: list[ExtractedFact]) -> dict[str, dict[str, ExtractedFact]]:
    by_period: dict[str, dict[str, ExtractedFact]] = {}
    for fact in facts:
        if fact.statement_type == StatementType.OPERATING_METRICS:
            continue
        if _fact_dimensions(fact):
            continue
        by_period.setdefault(fact.period_key, {})
        current = by_period[fact.period_key].get(fact.canonical_name)
        if current is None or fact.confidence > current.confidence:
            by_period[fact.period_key][fact.canonical_name] = fact
    return by_period


def _bridge_check(
    rule_id: str,
    rule_name: str,
    statement_type: StatementType,
    period: str,
    bucket: dict[str, ExtractedFact],
    left_name: str,
    right_names: tuple[str, ...],
    *,
    signs: tuple[Decimal, ...] | None = None,
    optional: bool = False,
    missing_as_zero: tuple[str, ...] = (),
) -> ValidationCheck:
    left = bucket.get(left_name)
    signs = signs or tuple(Decimal("1") for _ in right_names)
    right_terms: list[tuple[str, ExtractedFact | None, Decimal]] = []
    missing: list[str] = []
    right_value = Decimal("0")
    for name, sign in zip(right_names, signs):
        fact = bucket.get(name)
        if fact is None:
            if name in missing_as_zero:
                continue
            missing.append(name)
            right_terms.append((name, None, sign))
            continue
        right_terms.append((name, fact, sign))
        right_value += fact.value * sign

    if left is None:
        missing.append(left_name)
    if missing:
        return ValidationCheck(
            rule_id=rule_id,
            rule_name=rule_name,
            statement_type=statement_type,
            period=period,
            status=CheckStatus.SKIPPED if optional else CheckStatus.WARNING,
            inputs=[left_name, *right_names],
            left={"name": left_name, "value": _decimal_text(left.value) if left else None},
            right={"formula": " + ".join(right_names), "missing": missing},
            reason="missing_inputs",
            evidence=_evidence_list([left, *(term[1] for term in right_terms)]),
        )

    assert left is not None
    diff = left.value - right_value
    tolerance = _tolerance([left.value, right_value], max(_scale_for_fact(left), *[_scale_for_fact(term[1]) for term in right_terms if term[1]]))
    if abs(diff) <= tolerance:
        status = CheckStatus.PASS
    else:
        status = CheckStatus.WARNING if optional else CheckStatus.FAIL
    return ValidationCheck(
        rule_id=rule_id,
        rule_name=rule_name,
        statement_type=statement_type,
        period=period,
        status=status,
        diff=diff,
        tolerance=tolerance,
        inputs=[left_name, *right_names],
        left={"name": left_name, "value": _decimal_text(left.value)},
        right={
            "formula": " + ".join(f"{sign}*{name}" for name, _, sign in right_terms),
            "value": _decimal_text(right_value),
        },
        reason=None if status == CheckStatus.PASS else "outside_tolerance",
        evidence=_evidence_list([left, *(term[1] for term in right_terms)]),
    )


def _cash_balance_soft_check(period: str, bucket: dict[str, ExtractedFact]) -> ValidationCheck:
    cash_bs = bucket.get("cash_and_cash_equivalents")
    cash_cf = bucket.get("cash_equivalents_ending")
    if not cash_bs or not cash_cf:
        return ValidationCheck(
            rule_id="cross.cash_balance_vs_cash_flow_ending",
            rule_name="Balance sheet cash ~= cash flow ending cash",
            statement_type="cross",
            period=period,
            status=CheckStatus.SKIPPED,
            inputs=["cash_and_cash_equivalents", "cash_equivalents_ending"],
            left={"name": "cash_and_cash_equivalents", "value": _decimal_text(cash_bs.value) if cash_bs else None},
            right={"name": "cash_equivalents_ending", "value": _decimal_text(cash_cf.value) if cash_cf else None},
            reason="missing_inputs",
            evidence=_evidence_list([cash_bs, cash_cf]),
        )
    diff = cash_bs.value - cash_cf.value
    tolerance = _tolerance([cash_bs.value, cash_cf.value], max(_scale_for_fact(cash_bs), _scale_for_fact(cash_cf)), soft=True)
    return ValidationCheck(
        rule_id="cross.cash_balance_vs_cash_flow_ending",
        rule_name="Balance sheet cash ~= cash flow ending cash",
        statement_type="cross",
        period=period,
        status=CheckStatus.PASS if abs(diff) <= tolerance else CheckStatus.WARNING,
        diff=diff,
        tolerance=tolerance,
        inputs=["cash_and_cash_equivalents", "cash_equivalents_ending"],
        left={"name": "cash_and_cash_equivalents", "value": _decimal_text(cash_bs.value)},
        right={"name": "cash_equivalents_ending", "value": _decimal_text(cash_cf.value)},
        reason=None if abs(diff) <= tolerance else "cash_definition_may_include_restricted_cash_or_time_deposits",
        evidence=_evidence_list([cash_bs, cash_cf]),
    )


def _operating_metric_checks(metrics: list[ExtractedFact]) -> list[ValidationCheck]:
    checks: list[ValidationCheck] = []
    by_period = _facts_by_period_for_operating(metrics)
    for metric in metrics:
        status = CheckStatus.PASS
        reason = None
        if metric.value < 0 and metric.canonical_name not in {"same_store_sales_growth", "net_revenue_retention"}:
            status = CheckStatus.WARNING
            reason = "negative_operating_metric"
        if metric.canonical_name.endswith("_rate") or metric.canonical_name in {
            "net_revenue_retention",
            "same_store_sales_growth",
            "net_interest_margin",
            "npl_ratio",
            "combined_ratio",
        }:
            if abs(metric.value) > Decimal("300"):
                status = CheckStatus.WARNING
                reason = "ratio_outside_reasonable_range"
        checks.append(
            ValidationCheck(
                rule_id=f"op.basic.{metric.canonical_name}",
                rule_name=f"Operating metric basic validation: {metric.canonical_name}",
                statement_type=StatementType.OPERATING_METRICS,
                period=metric.period_key,
                status=status,
                inputs=[metric.canonical_name],
                left={"name": metric.canonical_name, "value": _decimal_text(metric.value)},
                right={"rule": "non_negative_or_reasonable_ratio"},
                reason=reason,
                evidence=[metric.evidence],
            )
        )

    for period, bucket in by_period.items():
        dau = bucket.get("daily_active_users")
        mau = bucket.get("monthly_active_users")
        if dau and mau:
            checks.append(
                _comparison_check(
                    "op.internet.dau_le_mau",
                    "DAU <= MAU",
                    period,
                    dau,
                    mau,
                    left_lte_right=True,
                )
            )
        paid = bucket.get("paid_subscribers")
        active = bucket.get("active_customers")
        if paid and active:
            checks.append(
                _comparison_check(
                    "op.saas.paid_customers_le_active_customers",
                    "Paid subscribers/customers <= active customers",
                    period,
                    paid,
                    active,
                    left_lte_right=True,
                )
            )
    return checks


def _facts_by_period_for_operating(facts: list[ExtractedFact]) -> dict[str, dict[str, ExtractedFact]]:
    by_period: dict[str, dict[str, ExtractedFact]] = {}
    for fact in facts:
        by_period.setdefault(fact.period_key, {})
        by_period[fact.period_key][fact.canonical_name] = fact
    return by_period


def _comparison_check(
    rule_id: str,
    rule_name: str,
    period: str,
    left: ExtractedFact,
    right: ExtractedFact,
    *,
    left_lte_right: bool,
) -> ValidationCheck:
    passed = left.value <= right.value if left_lte_right else left.value >= right.value
    return ValidationCheck(
        rule_id=rule_id,
        rule_name=rule_name,
        statement_type=StatementType.OPERATING_METRICS,
        period=period,
        status=CheckStatus.PASS if passed else CheckStatus.WARNING,
        diff=left.value - right.value,
        tolerance=Decimal("0"),
        inputs=[left.canonical_name, right.canonical_name],
        left={"name": left.canonical_name, "value": _decimal_text(left.value)},
        right={"name": right.canonical_name, "value": _decimal_text(right.value)},
        reason=None if passed else "operating_metric_relationship_failed",
        evidence=[left.evidence, right.evidence],
    )


def _evidence_checks(facts: list[ExtractedFact]) -> list[ValidationCheck]:
    checks = []
    for fact in facts:
        has_evidence = bool(fact.evidence and (fact.evidence.source_type or fact.evidence.quote_text or fact.evidence.xbrl_tag))
        checks.append(
            ValidationCheck(
                rule_id=f"evidence.required.{fact.canonical_name}.{fact.period_key}",
                rule_name=f"Evidence required: {fact.canonical_name}",
                statement_type=fact.statement_type,
                period=fact.period_key,
                status=CheckStatus.PASS if has_evidence else CheckStatus.WARNING,
                inputs=[fact.canonical_name],
                left={"metric": fact.canonical_name, "has_evidence": has_evidence},
                right={"required": True},
                reason=None if has_evidence else "missing_evidence",
                evidence=[fact.evidence] if fact.evidence else [],
            )
        )
    return checks


def _dimension_scope_checks(facts: list[ExtractedFact]) -> list[ValidationCheck]:
    checks: list[ValidationCheck] = []
    grouped: dict[tuple[str, str], list[ExtractedFact]] = {}
    for fact in facts:
        dimensions = _fact_dimensions(fact)
        if not dimensions:
            continue
        grouped.setdefault((fact.period_key, _dimension_key(dimensions)), []).append(fact)
    for (period, dimension_key), rows in grouped.items():
        sample = rows[0]
        checks.append(
            ValidationCheck(
                rule_id=f"scope.dimension_specific.{period}.{dimension_key[:12]}",
                rule_name="Dimension-specific facts separated from consolidated bridge checks",
                statement_type="document",
                period=period,
                status=CheckStatus.WARNING,
                inputs=sorted({row.canonical_name for row in rows}),
                left={"dimension_key": dimension_key, "fact_count": len(rows)},
                right={"rule": "not_used_for_consolidated_financial_bridge"},
                reason="dimension_specific_scope",
                evidence=_evidence_list([sample]),
            )
        )
    return checks


def _fact_dimensions(fact: ExtractedFact) -> dict[str, Any]:
    raw = fact.raw if isinstance(fact.raw, dict) else {}
    for candidate in (
        raw.get("dimensions"),
        (raw.get("raw") or {}).get("dimensions") if isinstance(raw.get("raw"), dict) else None,
    ):
        if isinstance(candidate, dict) and candidate:
            return {str(k): v for k, v in candidate.items()}
    return {}


def _dimension_key(dimensions: dict[str, Any]) -> str:
    return "|".join(f"{key}={dimensions[key]}" for key in sorted(dimensions))


def _missing_statement_status(report_type: str | None, report_form: str | None) -> CheckStatus:
    text = f"{report_type or ''} {report_form or ''}".lower()
    if any(token in text for token in ("annual", "10-k", "20-f", "year", "年报", "年度")):
        return CheckStatus.FAIL
    if any(token in text for token in ("interim", "half", "semi", "h1", "中期", "半年")):
        return CheckStatus.WARNING
    return CheckStatus.SKIPPED


def _missing_required_metric_status(report_type: str | None, report_form: str | None) -> CheckStatus:
    text = f"{report_type or ''} {report_form or ''}".lower()
    if any(token in text for token in ("annual", "10-k", "20-f", "year", "年报", "年度")):
        return CheckStatus.WARNING
    if any(token in text for token in ("quarter", "q1", "q2", "q3", "q4", "6-k", "季度")):
        return CheckStatus.SKIPPED
    return CheckStatus.WARNING


def _tolerance(values: list[Decimal], scale: Decimal, *, soft: bool = False) -> Decimal:
    max_abs = max([abs(value) for value in values if value is not None] or [Decimal("0")])
    pct = Decimal("0.01") if soft else Decimal("0.005")
    multiplier = Decimal("5") if soft else Decimal("2")
    return max(max_abs * pct, scale * multiplier)


def _scale_for_fact(fact: ExtractedFact | None) -> Decimal:
    if fact is None:
        return Decimal("1")
    return max(fact.scale, Decimal("1"))


def _evidence_list(items: Iterable[ExtractedFact | None]) -> list[EvidenceRef]:
    return [item.evidence for item in items if item and item.evidence]


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)
