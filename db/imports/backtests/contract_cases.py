"""Fixture document_full contract checks for market backtest gates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_view_parity_helpers import COMMON_CORE_METRICS
from document_fact_normalizer import (
    assertion_to_expected_fact,
    decimal_equal,
    document_identity,
    fact_content_hash,
    find_fact,
    has_reviewable_evidence,
    normalize_document_facts,
    value_within_tolerance,
)


def check_case(case: dict[str, Any], cases_path: Path, *, read_json: Any) -> dict[str, Any]:
    errors: list[str] = []
    required_evidence_checked = 0
    required_evidence_passed = 0
    unit_currency_checked = 0
    unit_currency_passed = 0
    document_path = cases_path.parent / str(case["document_full_path"])
    document_full = read_json(document_path)
    identity = document_identity(document_full, fallback_market=case.get("market"))
    facts = normalize_document_facts(document_full)

    for field in ("market", "company_id", "report_year"):
        if field in case and identity.get(field) != case[field]:
            errors.append(f"identity.{field}: expected {case[field]!r}, got {identity.get(field)!r}")
    for field, expected_value in (case.get("expected_identity") or {}).items():
        if identity.get(field) != expected_value:
            errors.append(f"identity.{field}: expected {expected_value!r}, got {identity.get(field)!r}")

    expected_facts = case.get("assertions") or case.get("expected_facts") or []
    for assertion in expected_facts:
        expected = assertion_to_expected_fact(assertion, case)
        if expected.get("required_evidence") is True:
            required_evidence_checked += 1
        unit_currency_fields = tuple(
            field
            for field in (
                "unit",
                "currency",
                "fact_currency",
                "reporting_currency",
                "presentation_currency",
                "scale",
            )
            if field in expected
        )
        if unit_currency_fields:
            unit_currency_checked += 1
        fact = find_fact(facts, expected)
        if fact is None:
            errors.append(f"missing fact match: {expected}")
            continue
        tolerance_ratio = expected.get("tolerance_ratio")
        if "value" in expected and tolerance_ratio is not None and not value_within_tolerance(
            fact.value,
            expected["value"],
            tolerance_ratio,
        ):
            errors.append(
                f"{expected}: value expected {expected['value']!r} within {tolerance_ratio!r}, got {fact.value!r}"
            )
        elif "value" in expected and tolerance_ratio is None and not decimal_equal(fact.value, expected["value"]):
            errors.append(f"{expected}: value expected {expected['value']!r}, got {fact.value!r}")
        unit_currency_matches = True
        for field in ("raw_value", "unit", "currency", "fact_currency", "reporting_currency", "presentation_currency", "scale"):
            if field in expected and getattr(fact, field) != expected[field]:
                errors.append(f"{expected}: {field} expected {expected[field]!r}, got {getattr(fact, field)!r}")
                if field in unit_currency_fields:
                    unit_currency_matches = False
        if unit_currency_fields and unit_currency_matches:
            unit_currency_passed += 1
        expected_evidence = expected.get("evidence") if isinstance(expected.get("evidence"), dict) else {}
        observed_evidence = fact.evidence or {}
        if expected.get("required_evidence") is True and not has_reviewable_evidence(observed_evidence):
            errors.append(f"{expected}: expected reviewable evidence, got {observed_evidence!r}")
        elif expected.get("required_evidence") is True:
            required_evidence_passed += 1
        for field, expected_value in expected_evidence.items():
            if observed_evidence.get(field) != expected_value:
                errors.append(
                    f"{expected}: evidence.{field} expected {expected_value!r}, got {observed_evidence.get(field)!r}"
                )
    for flag in case.get("expected_flags") or []:
        if flag == "eu_multi_currency_document":
            currencies = {fact.fact_currency or fact.currency for fact in facts if fact.fact_currency or fact.currency}
            if len(currencies) <= 1:
                errors.append(f"expected multi-currency document, got currencies={sorted(currencies)}")
        else:
            errors.append(f"unsupported expected flag: {flag}")
    expected_content_hash = case.get("expected_content_hash")
    observed_content_hash = fact_content_hash(facts)
    if expected_content_hash and observed_content_hash != expected_content_hash:
        errors.append(f"content hash expected {expected_content_hash!r}, got {observed_content_hash!r}")

    return {
        "case_id": case.get("case_id"),
        "market": case.get("market"),
        "passed": not errors,
        "errors": errors,
        "fact_count": len(facts),
        "content_hash": observed_content_hash,
        "document_full_path": str(document_path),
        "required_evidence_checked_count": required_evidence_checked,
        "required_evidence_passed_count": required_evidence_passed,
        "unit_currency_checked_count": unit_currency_checked,
        "unit_currency_passed_count": unit_currency_passed,
    }


def check_agent_case(case: dict[str, Any], cases_path: Path, *, read_json: Any) -> dict[str, Any]:
    errors: list[str] = []
    document_path = cases_path.parent / str(case["document_full_path"])
    document_full = read_json(document_path)
    facts = normalize_document_facts(document_full)
    questions = case.get("agent_questions")
    if not isinstance(questions, list) or not questions:
        questions = [
            {
                "question_id": f"{case.get('case_id')}:fixture_fact_lookup",
                "expected_fact": assertion_to_expected_fact(assertion, case),
            }
            for assertion in (case.get("assertions") or case.get("expected_facts") or [])
            if isinstance(assertion, dict)
        ]
    checked = 0
    for question in questions:
        if not isinstance(question, dict):
            continue
        expected = question.get("expected_fact") if isinstance(question.get("expected_fact"), dict) else question
        expected = assertion_to_expected_fact(expected, case)
        fact = find_fact(facts, expected)
        checked += 1
        if fact is None:
            errors.append(f"{question.get('question_id') or 'agent_question'}: missing fact match {expected}")
            continue
        if "value" in expected and not decimal_equal(fact.value, expected["value"]):
            errors.append(
                f"{question.get('question_id') or 'agent_question'}: value expected {expected['value']!r}, got {fact.value!r}"
            )
        if expected.get("required_evidence") is True and not has_reviewable_evidence(fact.evidence or {}):
            errors.append(
                f"{question.get('question_id') or 'agent_question'}: expected reviewable evidence, got {fact.evidence!r}"
            )
    return {
        "case_id": case.get("case_id"),
        "market": case.get("market"),
        "passed": checked > 0 and not errors,
        "checked": checked,
        "errors": errors,
        "mode": "fixture_fact_lookup",
    }


def contract_assertion_stats(cases: list[dict[str, Any]]) -> dict[str, int]:
    assertions = [
        assertion
        for case in cases
        for assertion in (case.get("assertions") or case.get("expected_facts") or [])
        if isinstance(assertion, dict)
    ]
    return {
        "assertion_count": len(assertions),
        "common_core_assertion_count": sum(1 for assertion in assertions if assertion.get("canonical_name") in COMMON_CORE_METRICS),
        "required_evidence_assertion_count": sum(1 for assertion in assertions if assertion.get("required_evidence") is True),
        "unit_checked_assertion_count": sum(1 for assertion in assertions if "unit" in assertion),
        "currency_checked_assertion_count": sum(1 for assertion in assertions if "currency" in assertion),
        "fact_currency_checked_assertion_count": sum(1 for assertion in assertions if "fact_currency" in assertion),
    }


__all__ = ["check_agent_case", "check_case", "contract_assertion_stats"]
