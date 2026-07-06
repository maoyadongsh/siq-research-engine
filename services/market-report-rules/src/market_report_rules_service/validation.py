from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

from .industry_profiles import get_industry_profile
from .models import (
    AccountingStandard,
    CheckStatus,
    EvidenceRef,
    ExtractedFact,
    ExtractionResult,
    Market,
    StatementType,
    ValidationCheck,
    ValidationResult,
)

GATE_CONTRACT_VERSION = "risk_calibrated_gate_v1"
GATE_SEVERITY_HARD = "hard"
GATE_SEVERITY_SOFT = "soft"
GATE_SEVERITY_OBSERVE = "observe"
GATE_MODE_ENFORCE = "enforce"
GATE_MODE_WARN = "warn"
GATE_MODE_OBSERVE = "observe"
GATE_DECISION_ALLOW = "allow"
GATE_DECISION_REVIEW = "review"
GATE_DECISION_BLOCK = "block"
PROMOTION_TARGETS = ("draft", "review", "canonical", "retrieval", "production")
_DECISION_RANK = {GATE_DECISION_ALLOW: 0, GATE_DECISION_REVIEW: 1, GATE_DECISION_BLOCK: 2}
_SEVERITY_RANK = {GATE_SEVERITY_OBSERVE: 0, GATE_SEVERITY_SOFT: 1, GATE_SEVERITY_HARD: 2}


def validate_extraction(extraction: ExtractionResult) -> ValidationResult:
    checks: list[ValidationCheck] = []
    facts = list(_all_facts(extraction))

    checks.extend(_required_metric_checks(extraction, facts))
    checks.extend(_required_statement_checks(extraction))
    checks.extend(_financial_bridge_checks(facts))
    checks.extend(_dimension_scope_checks(facts))
    checks.extend(_operating_metric_checks(extraction.operating_metrics))
    checks.extend(_evidence_checks(facts))
    checks.extend(_accounting_standard_checks(extraction, facts))
    checks = _attach_gate_contracts(checks)

    summary = {status.value: 0 for status in CheckStatus}
    for check in checks:
        summary[check.status.value] = summary.get(check.status.value, 0) + 1
    warnings = list(extraction.warnings)
    profile = get_industry_profile(extraction.industry_profile)
    warnings.extend(profile.validation_notes)
    overall = _overall_status_from_checks(checks, warnings)
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


def _attach_gate_contracts(checks: list[ValidationCheck]) -> list[ValidationCheck]:
    return [_with_gate_contract(check) for check in checks]


def _with_gate_contract(check: ValidationCheck) -> ValidationCheck:
    gate_results = _gate_results_for_check(check)
    if not gate_results:
        return check
    raw = dict(check.raw)
    raw["gate_contract_version"] = GATE_CONTRACT_VERSION
    raw["gate_results"] = gate_results
    raw["gate_decisions_by_target"] = _aggregate_gate_decisions(gate_results)
    raw["gate"] = next(gate for gate in gate_results if gate["target"] == "canonical")
    return check.model_copy(update={"raw": raw})


def _gate_results_for_check(check: ValidationCheck) -> list[dict[str, Any]]:
    severity = _gate_severity_for_check(check)
    if severity is None:
        return []
    decisions = _gate_decisions_for_severity(severity)
    mode = _gate_mode_for_severity(severity)
    reason = check.reason or check.status.value
    evidence_refs = [item.model_dump(mode="json", exclude_none=True) for item in check.evidence]
    return [
        {
            "rule_id": check.rule_id,
            "severity": severity,
            "mode": mode,
            "decision": decisions[target],
            "target": target,
            "promotion_target": target,
            "reason": reason,
            "evidence_refs": evidence_refs,
        }
        for target in PROMOTION_TARGETS
    ]


def _gate_severity_for_check(check: ValidationCheck) -> str | None:
    if check.status == CheckStatus.FAIL:
        return GATE_SEVERITY_HARD
    if check.status == CheckStatus.WARNING:
        if check.reason in {"dimension_specific_scope", "alternative_total_liabilities_and_equity_bridge_passed"}:
            return GATE_SEVERITY_OBSERVE
        return GATE_SEVERITY_SOFT
    if check.status == CheckStatus.SKIPPED:
        return GATE_SEVERITY_OBSERVE
    return None


def _gate_mode_for_severity(severity: str) -> str:
    if severity == GATE_SEVERITY_HARD:
        return GATE_MODE_ENFORCE
    if severity == GATE_SEVERITY_SOFT:
        return GATE_MODE_WARN
    return GATE_MODE_OBSERVE


def _gate_decisions_for_severity(severity: str) -> dict[str, str]:
    if severity == GATE_SEVERITY_HARD:
        return {
            "draft": GATE_DECISION_ALLOW,
            "review": GATE_DECISION_REVIEW,
            "canonical": GATE_DECISION_BLOCK,
            "retrieval": GATE_DECISION_BLOCK,
            "production": GATE_DECISION_BLOCK,
        }
    if severity == GATE_SEVERITY_SOFT:
        return {
            "draft": GATE_DECISION_ALLOW,
            "review": GATE_DECISION_REVIEW,
            "canonical": GATE_DECISION_REVIEW,
            "retrieval": GATE_DECISION_REVIEW,
            "production": GATE_DECISION_REVIEW,
        }
    return {target: GATE_DECISION_ALLOW for target in PROMOTION_TARGETS}


def _aggregate_gate_decisions(gate_results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    decisions: dict[str, dict[str, Any]] = {
        target: {
            "target": target,
            "promotion_target": target,
            "decision": GATE_DECISION_ALLOW,
            "severity": GATE_SEVERITY_OBSERVE,
            "rule_ids": [],
            "review_rule_ids": [],
            "blocking_rule_ids": [],
            "reasons": [],
        }
        for target in PROMOTION_TARGETS
    }
    for gate in gate_results:
        target = str(gate.get("target") or "")
        if target not in decisions:
            continue
        current = decisions[target]
        decision = str(gate.get("decision") or GATE_DECISION_ALLOW)
        severity = str(gate.get("severity") or GATE_SEVERITY_OBSERVE)
        if _DECISION_RANK.get(decision, 0) > _DECISION_RANK.get(str(current["decision"]), 0):
            current["decision"] = decision
        if _SEVERITY_RANK.get(severity, 0) > _SEVERITY_RANK.get(str(current["severity"]), 0):
            current["severity"] = severity
        rule_id = str(gate.get("rule_id") or "")
        if rule_id:
            current["rule_ids"].append(rule_id)
            if decision == GATE_DECISION_BLOCK:
                current["blocking_rule_ids"].append(rule_id)
            elif decision == GATE_DECISION_REVIEW:
                current["review_rule_ids"].append(rule_id)
        reason = str(gate.get("reason") or "")
        if reason:
            current["reasons"].append(reason)

    for payload in decisions.values():
        for key in ("rule_ids", "review_rule_ids", "blocking_rule_ids", "reasons"):
            seen: set[str] = set()
            payload[key] = [item for item in payload[key] if not (item in seen or seen.add(item))]
    return decisions


def _overall_status_from_checks(checks: list[ValidationCheck], warnings: list[str]) -> CheckStatus:
    if any(check.status == CheckStatus.FAIL for check in checks):
        return CheckStatus.FAIL

    required_statements_complete = _required_statements_complete(checks)
    if any(
        check.status == CheckStatus.WARNING
        and _is_blocking_validation_warning(check, required_statements_complete=required_statements_complete)
        for check in checks
    ):
        return CheckStatus.WARNING
    if _has_blocking_warning_text(warnings):
        return CheckStatus.WARNING
    if any(check.status == CheckStatus.PASS for check in checks):
        return CheckStatus.PASS
    if any(check.status == CheckStatus.WARNING for check in checks):
        return CheckStatus.WARNING
    return CheckStatus.SKIPPED


def _required_statements_complete(checks: list[ValidationCheck]) -> bool:
    required = [check for check in checks if check.rule_id.startswith("required.statement.")]
    return len(required) == 3 and all(check.status == CheckStatus.PASS for check in required)


def _is_blocking_validation_warning(check: ValidationCheck, *, required_statements_complete: bool) -> bool:
    if check.reason == "dimension_specific_scope":
        return False
    if required_statements_complete and check.reason in {
        "required_metric_missing_for_industry_profile",
        "alternative_total_liabilities_and_equity_bridge_passed",
    }:
        return False
    return True


def _has_blocking_warning_text(warnings: list[str]) -> bool:
    text = " ".join(str(item or "") for item in warnings).lower()
    return any(token in text for token in ("critical warning", "critical_warnings", "hash mismatch", "hash_mismatch"))


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
    checks: list[ValidationCheck] = []
    required = (StatementType.BALANCE_SHEET, StatementType.INCOME_STATEMENT, StatementType.CASH_FLOW_STATEMENT)
    for statement_type in required:
        present = _required_statement_present(extraction, statement_type)
        if present:
            status = CheckStatus.PASS
        else:
            status = _missing_statement_status_for_extraction(extraction, statement_type)
        checks.append(
            ValidationCheck(
                rule_id=f"required.statement.{statement_type.value}",
                rule_name=f"Required statement present: {statement_type.value}",
                statement_type="document",
                period=extraction.period_end.isoformat() if extraction.period_end else None,
                status=status,
                inputs=[statement_type.value],
                left={"statement_type": statement_type.value, "present": present},
                right={"report_type": extraction.report_type, "report_form": extraction.report_form},
                reason=None if status == CheckStatus.PASS else _missing_statement_reason(extraction, status, statement_type),
            )
        )
    return checks


def _required_statement_present(extraction: ExtractionResult, statement_type: StatementType) -> bool:
    statements = [statement for statement in extraction.statements if statement.statement_type == statement_type]
    if extraction.market != Market.JP:
        return bool(statements)
    report_text = f"{extraction.report_type or ''} {extraction.report_form or ''}".lower()
    if any(token in report_text for token in ("integrated", "highlights", "summary")):
        return bool(statements)
    for statement in statements:
        for fact in statement.items:
            if _jp_fact_is_formal_statement_fact(fact, statement_type):
                return True
    return False


def _jp_fact_is_formal_statement_fact(fact: ExtractedFact, statement_type: StatementType) -> bool:
    if fact.evidence.source_type in {"edinet_xbrl_fact", "xbrl_fact", "api_fact"}:
        return True
    raw = fact.evidence.raw if isinstance(fact.evidence.raw, dict) else {}
    detected = raw.get("detected_statement_type")
    if detected == statement_type.value and not raw.get("mixed_statement_summary"):
        return True
    return False


def _financial_bridge_checks(facts: list[ExtractedFact]) -> list[ValidationCheck]:
    by_period = _facts_by_period(facts)
    period_rows = _facts_list_by_period(facts)
    source_aware_bridge = _use_source_aware_bridge(facts)
    checks: list[ValidationCheck] = []
    for period, bucket in by_period.items():
        bs_components_bridge = _bridge_check_for_period(
            "bs.assets_eq_liabilities_plus_temporary_equity_plus_equity",
            "Assets = liabilities + temporary/redeemable equity + equity",
            StatementType.BALANCE_SHEET,
            period,
            bucket,
            period_rows.get(period, []),
            source_aware_bridge,
            "total_assets",
            ("total_liabilities", "redeemable_noncontrolling_interest", "total_equity"),
            missing_as_zero=("redeemable_noncontrolling_interest",),
        )
        bs_total_bridge = _bridge_check_for_period(
            "bs.assets_eq_liabilities_and_equity",
            "Assets = liabilities and equity total",
            StatementType.BALANCE_SHEET,
            period,
            bucket,
            period_rows.get(period, []),
            source_aware_bridge,
            "total_assets",
            ("total_liabilities_and_equity",),
        )
        if source_aware_bridge and bs_components_bridge.status == CheckStatus.FAIL and bs_total_bridge.status == CheckStatus.PASS:
            raw = dict(bs_components_bridge.raw)
            raw["downgraded_by"] = "bs.assets_eq_liabilities_and_equity"
            bs_components_bridge = bs_components_bridge.model_copy(
                update={
                    "status": CheckStatus.WARNING,
                    "reason": "alternative_total_liabilities_and_equity_bridge_passed",
                    "raw": raw,
                }
            )
        checks.append(bs_components_bridge)
        checks.append(bs_total_bridge)
        checks.append(
            _bridge_check_for_period(
                "bs.current_plus_non_current_assets",
                "Assets = current assets + non-current assets",
                StatementType.BALANCE_SHEET,
                period,
                bucket,
                period_rows.get(period, []),
                source_aware_bridge,
                "total_assets",
                ("current_assets", "non_current_assets"),
                optional=True,
            )
        )
        checks.append(
            _bridge_check_for_period(
                "bs.current_plus_non_current_liabilities",
                "Liabilities = current liabilities + non-current liabilities",
                StatementType.BALANCE_SHEET,
                period,
                bucket,
                period_rows.get(period, []),
                source_aware_bridge,
                "total_liabilities",
                ("current_liabilities", "non_current_liabilities"),
                optional=True,
            )
        )
        checks.append(
            _bridge_check_for_period(
                "bs.parent_equity_plus_nci",
                "Equity = parent equity + non-controlling interests",
                StatementType.BALANCE_SHEET,
                period,
                bucket,
                period_rows.get(period, []),
                source_aware_bridge,
                "total_equity",
                ("parent_equity", "nci_equity"),
                optional=True,
            )
        )
        checks.append(
            _bridge_check_for_period(
                "is.gross_profit_bridge",
                "Gross profit = revenue - cost of sales",
                StatementType.INCOME_STATEMENT,
                period,
                bucket,
                period_rows.get(period, []),
                source_aware_bridge,
                "gross_profit",
                ("operating_revenue", "cost_of_sales"),
                signs=(Decimal("1"), Decimal("-1")),
                optional=True,
            )
        )
        checks.append(
            _bridge_check_for_period(
                "is.net_profit_bridge",
                "Net profit = profit before tax - income tax",
                StatementType.INCOME_STATEMENT,
                period,
                bucket,
                period_rows.get(period, []),
                source_aware_bridge,
                "net_profit",
                ("total_profit", "income_tax_expense"),
                signs=(Decimal("1"), Decimal("-1")),
                optional=True,
            )
        )
        checks.append(
            _bridge_check_for_period(
                "is.net_profit_attribution",
                "Net profit = parent net profit + non-controlling interests profit",
                StatementType.INCOME_STATEMENT,
                period,
                bucket,
                period_rows.get(period, []),
                source_aware_bridge,
                "net_profit",
                ("parent_net_profit", "nci_profit"),
                optional=True,
            )
        )
        checks.append(
            _bridge_check_for_period(
                "cf.net_cash_change_bridge",
                "Net cash change = operating + investing + financing + FX",
                StatementType.CASH_FLOW_STATEMENT,
                period,
                bucket,
                period_rows.get(period, []),
                source_aware_bridge,
                "cash_equivalents_net_increase",
                ("operating_cash_flow_net", "investing_cash_flow_net", "financing_cash_flow_net", "fx_effect_cash"),
                optional=True,
                missing_as_zero=("fx_effect_cash",),
            )
        )
        checks.append(
            _bridge_check_for_period(
                "cf.ending_cash_bridge",
                "Ending cash = beginning cash + net cash change",
                StatementType.CASH_FLOW_STATEMENT,
                period,
                bucket,
                period_rows.get(period, []),
                source_aware_bridge,
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


def _facts_list_by_period(facts: list[ExtractedFact]) -> dict[str, list[ExtractedFact]]:
    by_period: dict[str, list[ExtractedFact]] = {}
    for fact in facts:
        if fact.statement_type == StatementType.OPERATING_METRICS:
            continue
        if _fact_dimensions(fact):
            continue
        by_period.setdefault(fact.period_key, []).append(fact)
    return by_period


def _use_source_aware_bridge(facts: list[ExtractedFact]) -> bool:
    markets = {fact.market for fact in facts}
    return bool(markets) and markets != {Market.CN}


def _bridge_check_for_period(
    rule_id: str,
    rule_name: str,
    statement_type: StatementType,
    period: str,
    fallback_bucket: dict[str, ExtractedFact],
    period_facts: list[ExtractedFact],
    source_aware_bridge: bool,
    left_name: str,
    right_names: tuple[str, ...],
    *,
    signs: tuple[Decimal, ...] | None = None,
    optional: bool = False,
    missing_as_zero: tuple[str, ...] = (),
) -> ValidationCheck:
    selected_bucket = fallback_bucket
    source_selection: dict[str, Any] | None = None
    if source_aware_bridge:
        candidate = _source_consistent_bridge_bucket(
            period_facts,
            statement_type,
            fallback_bucket,
            left_name,
            right_names,
            signs=signs,
            missing_as_zero=missing_as_zero,
        )
        if candidate is not None:
            selected_bucket, source_selection = candidate
    check = _bridge_check(
        rule_id,
        rule_name,
        statement_type,
        period,
        selected_bucket,
        left_name,
        right_names,
        signs=signs,
        optional=optional,
        missing_as_zero=missing_as_zero,
    )
    if source_selection:
        check.raw["source_selection"] = source_selection
    if source_aware_bridge and optional and check.status == CheckStatus.FAIL:
        raw = dict(check.raw)
        raw["downgraded_by"] = "optional_source_aware_bridge"
        return check.model_copy(
            update={
                "status": CheckStatus.WARNING,
                "reason": "optional_bridge_mismatch_for_market_profile",
                "raw": raw,
            }
        )
    return check


def _source_consistent_bridge_bucket(
    period_facts: list[ExtractedFact],
    statement_type: StatementType,
    fallback_bucket: dict[str, ExtractedFact],
    left_name: str,
    right_names: tuple[str, ...],
    *,
    signs: tuple[Decimal, ...] | None,
    missing_as_zero: tuple[str, ...],
) -> tuple[dict[str, ExtractedFact], dict[str, Any]] | None:
    required_names = {left_name, *(name for name in right_names if name not in missing_as_zero)}
    relevant = [
        fact
        for fact in period_facts
        if fact.statement_type == statement_type and fact.canonical_name in required_names
    ]
    if len(relevant) < 2:
        return None

    candidates: list[tuple[dict[str, ExtractedFact], dict[str, Any]]] = []
    grouped: dict[tuple[Any, ...], list[ExtractedFact]] = {}
    for fact in relevant:
        grouped.setdefault(_fact_source_group(fact), []).append(fact)
    for key, rows in grouped.items():
        candidates.append((_best_facts_by_name(rows), {"mode": "single_source", "group": list(key)}))

    table_rows = [fact for fact in relevant if fact.evidence and fact.evidence.table_index is not None]
    by_table: dict[int, list[ExtractedFact]] = {}
    for fact in table_rows:
        if fact.evidence.table_index is not None:
            by_table.setdefault(fact.evidence.table_index, []).append(fact)
    table_indexes = sorted(by_table)
    for table_index in table_indexes:
        window_indexes = [index for index in table_indexes if abs(index - table_index) <= 1]
        if len(window_indexes) < 2:
            continue
        rows: list[ExtractedFact] = []
        for index in window_indexes:
            rows.extend(by_table[index])
        candidates.append(
            (
                _best_facts_by_name(rows),
                {"mode": "adjacent_table_window", "table_indexes": window_indexes},
            )
        )

    best: tuple[tuple[Any, ...], dict[str, ExtractedFact], dict[str, Any]] | None = None
    for bucket, source_selection in candidates:
        score = _bridge_candidate_score(bucket, left_name, right_names, signs=signs, missing_as_zero=missing_as_zero)
        if score is None or not score[0]:
            continue
        if best is None or score > best[0]:
            best = (score, bucket, source_selection)
    if best is None:
        return None
    fallback_score = _bridge_candidate_score(fallback_bucket, left_name, right_names, signs=signs, missing_as_zero=missing_as_zero)
    if fallback_score is not None and fallback_score[0]:
        return None
    score, bucket, source_selection = best
    source_selection = {
        **source_selection,
        "reason": "source_consistent_bridge_candidate",
        "coverage": score[2],
    }
    return bucket, source_selection


def _best_facts_by_name(rows: list[ExtractedFact]) -> dict[str, ExtractedFact]:
    bucket: dict[str, ExtractedFact] = {}
    for fact in rows:
        current = bucket.get(fact.canonical_name)
        if current is None or _fact_selection_rank(fact) > _fact_selection_rank(current):
            bucket[fact.canonical_name] = fact
    return bucket


def _fact_selection_rank(fact: ExtractedFact) -> tuple[Decimal, int]:
    return (fact.confidence, _primary_source_rank(fact))


def _fact_source_group(fact: ExtractedFact) -> tuple[Any, ...]:
    evidence = fact.evidence
    source_type = str(evidence.source_type or "") if evidence else ""
    if "xbrl" in source_type.lower():
        return ("xbrl", source_type, fact.source_accession or evidence.accession_number if evidence else None)
    if evidence and evidence.table_index is not None:
        return ("table", evidence.table_index)
    return ("source", source_type, evidence.source_id if evidence else None)


def _bridge_candidate_score(
    bucket: dict[str, ExtractedFact],
    left_name: str,
    right_names: tuple[str, ...],
    *,
    signs: tuple[Decimal, ...] | None,
    missing_as_zero: tuple[str, ...],
) -> tuple[bool, int, int, Decimal, Decimal, int] | None:
    signs = signs or tuple(Decimal("1") for _ in right_names)
    left = bucket.get(left_name)
    required_right_names = [name for name in right_names if name not in missing_as_zero]
    coverage = sum(1 for name in (left_name, *required_right_names) if bucket.get(name) is not None)
    if left is None or coverage < len(required_right_names) + 1:
        return (False, 0, coverage, Decimal("-Infinity"), Decimal("0"), 0)
    right_value = Decimal("0")
    right_facts: list[ExtractedFact] = []
    for name, sign in zip(right_names, signs):
        fact = bucket.get(name)
        if fact is None:
            if name in missing_as_zero:
                continue
            return (False, 0, coverage, Decimal("-Infinity"), Decimal("0"), 0)
        right_facts.append(fact)
        right_value += _scaled_value(fact) * sign
    left_value = _scaled_value(left)
    tolerance = _tolerance([left_value, right_value], max(_scale_for_fact(left), *[_scale_for_fact(fact) for fact in right_facts]))
    diff = abs(left_value - right_value)
    denominator = max(abs(left_value), abs(right_value), Decimal("1"))
    primary_score = sum(_primary_source_rank(fact) for fact in [left, *right_facts])
    avg_confidence = sum((fact.confidence for fact in [left, *right_facts]), Decimal("0")) / Decimal(len(right_facts) + 1)
    return (diff <= tolerance, primary_score, coverage, -(diff / denominator), avg_confidence, -_table_span([left, *right_facts]))


def _primary_source_rank(fact: ExtractedFact) -> int:
    source_type = str(fact.evidence.source_type if fact.evidence else "").lower()
    if "xbrl" in source_type:
        return 4
    if "statement" in source_type:
        return 3
    if source_type in {"html_table", "parsed_financial_table"}:
        return 2
    if source_type == "derived_reported_metric":
        return 1
    return 0


def _table_span(facts: list[ExtractedFact]) -> int:
    indexes = [fact.evidence.table_index for fact in facts if fact.evidence and fact.evidence.table_index is not None]
    if not indexes:
        return 0
    return max(indexes) - min(indexes)


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
        right_value += _scaled_value(fact) * sign

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
            left=_check_side(left_name, left),
            right={"formula": " + ".join(right_names), "missing": missing},
            reason="missing_inputs",
            evidence=_evidence_list([left, *(term[1] for term in right_terms)]),
        )

    assert left is not None
    left_value = _scaled_value(left)
    diff = left_value - right_value
    tolerance = _tolerance([left_value, right_value], max(_scale_for_fact(left), *[_scale_for_fact(term[1]) for term in right_terms if term[1]]))
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
        left=_check_side(left_name, left),
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
            left=_check_side("cash_and_cash_equivalents", cash_bs),
            right=_check_side("cash_equivalents_ending", cash_cf),
            reason="missing_inputs",
            evidence=_evidence_list([cash_bs, cash_cf]),
        )
    cash_bs_value = _scaled_value(cash_bs)
    cash_cf_value = _scaled_value(cash_cf)
    diff = cash_bs_value - cash_cf_value
    tolerance = _tolerance([cash_bs_value, cash_cf_value], max(_scale_for_fact(cash_bs), _scale_for_fact(cash_cf)), soft=True)
    return ValidationCheck(
        rule_id="cross.cash_balance_vs_cash_flow_ending",
        rule_name="Balance sheet cash ~= cash flow ending cash",
        statement_type="cross",
        period=period,
        status=CheckStatus.PASS if abs(diff) <= tolerance else CheckStatus.WARNING,
        diff=diff,
        tolerance=tolerance,
        inputs=["cash_and_cash_equivalents", "cash_equivalents_ending"],
        left=_check_side("cash_and_cash_equivalents", cash_bs),
        right=_check_side("cash_equivalents_ending", cash_cf),
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
    left_value = _scaled_value(left)
    right_value = _scaled_value(right)
    passed = left_value <= right_value if left_lte_right else left_value >= right_value
    return ValidationCheck(
        rule_id=rule_id,
        rule_name=rule_name,
        statement_type=StatementType.OPERATING_METRICS,
        period=period,
        status=CheckStatus.PASS if passed else CheckStatus.WARNING,
        diff=left_value - right_value,
        tolerance=Decimal("0"),
        inputs=[left.canonical_name, right.canonical_name],
        left=_check_side(left.canonical_name, left),
        right=_check_side(right.canonical_name, right),
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


def _accounting_standard_checks(extraction: ExtractionResult, facts: list[ExtractedFact]) -> list[ValidationCheck]:
    if extraction.market not in {Market.JP, Market.KR}:
        return []
    unknown_facts = [fact for fact in facts if fact.accounting_standard == AccountingStandard.UNKNOWN]
    if extraction.accounting_standard != AccountingStandard.UNKNOWN and not unknown_facts:
        return []
    evidence = [fact.evidence for fact in unknown_facts[:5] if fact.evidence]
    return [
        ValidationCheck(
            rule_id="accounting.standard.known",
            rule_name="Accounting standard known before trusted promotion",
            statement_type="document",
            period=extraction.period_end.isoformat() if extraction.period_end else None,
            status=CheckStatus.WARNING,
            inputs=["accounting_standard"],
            left={
                "accounting_standard": extraction.accounting_standard.value,
                "unknown_fact_count": len(unknown_facts),
            },
            right={"allowed_for_draft": True, "canonical_requires_review": True},
            reason="accounting_standard_unknown",
            evidence=evidence,
        )
    ]


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


def _missing_statement_status(report_type: str | None, report_form: str | None, *, market: Market | None = None) -> CheckStatus:
    text = f"{report_type or ''} {report_form or ''}".lower()
    if market == Market.JP:
        if any(token in text for token in ("integrated", "highlights", "summary")):
            return CheckStatus.SKIPPED
    if any(token in text for token in ("annual", "10-k", "20-f", "year", "年报", "年度")):
        return CheckStatus.FAIL
    if any(token in text for token in ("interim", "half", "semi", "h1", "中期", "半年")):
        return CheckStatus.WARNING
    return CheckStatus.SKIPPED


def _missing_statement_status_for_extraction(extraction: ExtractionResult, statement_type: StatementType | None = None) -> CheckStatus:
    status = _missing_statement_status(extraction.report_type, extraction.report_form, market=extraction.market)
    if status != CheckStatus.FAIL:
        return status
    if extraction.market in {Market.EU, Market.KR} and _parser_coverage_incomplete_for_required_statements(extraction):
        return CheckStatus.WARNING
    return status


def _parser_coverage_incomplete_for_required_statements(extraction: ExtractionResult) -> bool:
    if extraction.statements:
        return False
    warning_text = " ".join(str(item or "") for item in extraction.warnings).lower()
    return any(
        token in warning_text
        for token in (
            "no mapped financial facts were extracted",
            "未确认完整结构化",
            "parser table quality",
            "parser coverage incomplete",
        )
    )


def _missing_statement_reason(extraction: ExtractionResult, status: CheckStatus, statement_type: StatementType | None = None) -> str:
    if extraction.market == Market.JP:
        if status == CheckStatus.SKIPPED:
            return "statement_not_required_for_jp_report_kind"
        if statement_type is not None and any(statement.statement_type == statement_type for statement in extraction.statements):
            return "statement_only_summary_or_note_facts_found_for_jp_annual_report"
        return "statement_not_extracted_or_not_located_for_jp_report"
    if extraction.market in {Market.EU, Market.KR} and status == CheckStatus.WARNING and _parser_coverage_incomplete_for_required_statements(extraction):
        return f"statement_not_extracted_or_parser_coverage_incomplete_for_{extraction.market.value.lower()}_report"
    return "statement_missing_for_report_type"


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


def _scaled_value(fact: ExtractedFact) -> Decimal:
    return fact.value * _scale_for_fact(fact)


def _check_side(name: str, fact: ExtractedFact | None) -> dict[str, Any]:
    if fact is None:
        return {"name": name, "value": None}
    scale = _scale_for_fact(fact)
    side: dict[str, Any] = {"name": name, "value": _decimal_text(_scaled_value(fact))}
    if scale != 1:
        side["raw_value"] = _decimal_text(fact.value)
        side["scale"] = _decimal_text(scale)
    return side


def _evidence_list(items: Iterable[ExtractedFact | None]) -> list[EvidenceRef]:
    return [item.evidence for item in items if item and item.evidence]


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)
