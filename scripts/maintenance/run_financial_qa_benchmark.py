#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKTEST_DIR = REPO_ROOT / "db" / "imports" / "backtests"
if str(BACKTEST_DIR) not in sys.path:
    sys.path.insert(0, str(BACKTEST_DIR))

from market_document_full_postgres_backtest import (  # noqa: E402
    NormalizedFact,
    decimal_equal,
    document_identity,
    has_reviewable_evidence,
    normalize_document_facts,
    read_json,
    value_within_tolerance,
)


DEFAULT_CASE_ROOT = REPO_ROOT / "datasets" / "eval" / "financial_qa_benchmark" / "v1"
DEFAULT_TRACE_LOG = DEFAULT_CASE_ROOT / "traces" / "p0_golden_traces.jsonl"
DEFAULT_OUTPUT = REPO_ROOT / "artifacts" / "eval-runs" / "financial-qa" / "financial_qa_benchmark.json"
DEFAULT_MARKDOWN = REPO_ROOT / "artifacts" / "eval-runs" / "financial-qa" / "financial_qa_benchmark.md"
P0_REQUIRED_RATE = 1.0
IMPLEMENTED_MODES = ("trace-offline", "wiki-static")
RESERVED_MODES = ("postgres-fallback",)
VALID_MODES = IMPLEMENTED_MODES + RESERVED_MODES
FIELD_ALIASES = {
    "quote_text": ("quote_text", "quote", "source_quote"),
    "quote": ("quote", "quote_text", "source_quote"),
    "source_page": ("source_page", "page", "page_number"),
    "page": ("page", "source_page", "page_number", "pdf_page", "pdf_page_number"),
    "page_number": ("page_number", "source_page", "page", "pdf_page", "pdf_page_number"),
    "pdf_page": ("pdf_page", "pdf_page_number", "source_page", "page", "page_number"),
}
NUMERIC_EQUIVALENT_FIELDS = {
    "column_index",
    "md_line",
    "page",
    "page_number",
    "pdf_page",
    "pdf_page_number",
    "row_index",
    "source_page",
    "table_index",
}
REQUIRED_CASE_FIELDS = ("case_id", "market", "question", "source_policy")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def repo_path(path: Path) -> Path:
    return path if path.is_absolute() else REPO_ROOT / path


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            raise ValueError(f"{path}:{line_number} is not a JSON object")
        rows.append(item)
    return rows


def load_cases(case_root: Path) -> list[dict[str, Any]]:
    case_root = repo_path(case_root)
    cases: list[dict[str, Any]] = []
    jsonl_path = case_root / "cases.jsonl"
    if jsonl_path.exists():
        for item in load_jsonl(jsonl_path):
            cases.append({**item, "_case_file": str(jsonl_path)})
    for path in sorted(case_root.glob("*_cases.json")):
        payload = read_json(path)
        raw_cases = payload.get("cases") if isinstance(payload, dict) else payload
        if not isinstance(raw_cases, list):
            continue
        for item in raw_cases:
            if isinstance(item, dict):
                cases.append({**item, "_case_file": str(path)})
    return cases


def load_trace_map(trace_log: Path) -> dict[str, dict[str, Any]]:
    traces = load_jsonl(repo_path(trace_log))
    return {str(item.get("question_id") or ""): item for item in traces if item.get("question_id")}


def case_modes(case: dict[str, Any]) -> tuple[str, ...]:
    """Return the implemented benchmark modes a case should run in.

    Missing ``modes`` means the case is part of every currently implemented
    deterministic mode. Reserved future modes must be declared only after their
    evaluator is implemented, otherwise PR gates could silently skip coverage.
    """
    raw = case.get("modes")
    if raw in (None, "", [], {}):
        return IMPLEMENTED_MODES
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, list):
        return tuple(str(item) for item in raw if str(item).strip())
    return ()


def validate_case(case: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_guardrail = case.get("expected_guardrail") if isinstance(case.get("expected_guardrail"), dict) else {}
    should_answer = expected_guardrail.get("should_answer", True)
    modes = case_modes(case)
    if not modes:
        errors.append("case.modes must be a string or non-empty array")
    unknown_modes = [mode for mode in modes if mode not in IMPLEMENTED_MODES]
    if unknown_modes:
        errors.append(f"case.modes contains unsupported modes: {unknown_modes!r}")
    for field in REQUIRED_CASE_FIELDS:
        if case.get(field) in (None, "", [], {}):
            errors.append(f"case.{field} missing")
    if "source_policy" in case and not isinstance(case.get("source_policy"), dict):
        errors.append("case.source_policy must be an object")
    expected_facts = case.get("expected_facts")
    if expected_facts in (None, ""):
        if should_answer:
            errors.append("case.expected_facts missing")
    elif not isinstance(expected_facts, list):
        errors.append("case.expected_facts must be an array")
    elif isinstance(expected_facts, list):
        if not expected_facts and should_answer:
            errors.append("case.expected_facts missing")
        for index, fact in enumerate(expected_facts, start=1):
            if not isinstance(fact, dict):
                errors.append(f"case.expected_facts[{index}] must be an object")
                continue
            if not any(fact.get(key) not in (None, "") for key in ("canonical_name", "metric_name", "name", "label", "concept")):
                errors.append(f"case.expected_facts[{index}] missing metric identifier")
            if fact.get("value") in (None, ""):
                errors.append(f"case.expected_facts[{index}].value missing")
    expected_calculations = case.get("expected_calculations")
    if expected_calculations is not None and not isinstance(expected_calculations, list):
        errors.append("case.expected_calculations must be an array")
    elif isinstance(expected_calculations, list):
        for index, calculation in enumerate(expected_calculations, start=1):
            if not isinstance(calculation, dict):
                errors.append(f"case.expected_calculations[{index}] must be an object")
                continue
            if calculation.get("operation") in (None, ""):
                errors.append(f"case.expected_calculations[{index}].operation missing")
            if not any(calculation.get(key) not in (None, "") for key in ("result", "value", "output")):
                errors.append(f"case.expected_calculations[{index}].result missing")
    return errors


def invalid_case_result(case: dict[str, Any], mode: str, errors: list[str]) -> dict[str, Any]:
    return {
        "case_id": case.get("case_id"),
        "market": case.get("market"),
        "tier": case.get("tier", "P0"),
        "mode": mode,
        "passed": False,
        "facts": [],
        "errors": errors,
    }


def resolve_case_document_path(case: dict[str, Any]) -> Path:
    raw = Path(str(case.get("document_full_path") or ""))
    if raw.is_absolute():
        return raw
    case_file = Path(str(case.get("_case_file") or ""))
    base = case_file.parent if case_file else DEFAULT_CASE_ROOT
    return (base / raw).resolve()


def fact_key(expected: dict[str, Any]) -> dict[str, Any]:
    return {
        "statement_type": expected.get("statement_type"),
        "period": expected.get("period") or expected.get("period_key"),
        "canonical_name": expected.get("canonical_name"),
        "name": expected.get("name"),
        "label": expected.get("label"),
        "concept": expected.get("concept"),
    }


def trace_fact_matches(fact: dict[str, Any], expected: dict[str, Any]) -> bool:
    key = fact_key(expected)
    for field, value in key.items():
        if value in (None, ""):
            continue
        if field == "period":
            observed = fact.get("period") or fact.get("period_key")
        else:
            observed = fact.get(field)
        if observed != value:
            return False
    return True


def wiki_fact_matches(fact: NormalizedFact, expected: dict[str, Any]) -> bool:
    key = fact_key(expected)
    fields = {
        "statement_type": fact.statement_type,
        "period": fact.period_key,
        "canonical_name": fact.canonical_name,
        "name": fact.name,
        "label": fact.label,
        "concept": fact.concept,
    }
    return all(value in (None, "") or fields[field] == value for field, value in key.items())


def find_trace_fact(trace: dict[str, Any], expected: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    facts = trace.get("wiki_facts") if isinstance(trace.get("wiki_facts"), list) else []
    for fact in facts:
        if isinstance(fact, dict) and trace_fact_matches(fact, expected):
            return fact, "wiki_facts"
    facts = trace.get("postgres_facts") if isinstance(trace.get("postgres_facts"), list) else []
    for fact in facts:
        if isinstance(fact, dict) and trace_fact_matches(fact, expected):
            return fact, "postgres_facts"
    return None, ""


def find_wiki_fact(facts: list[NormalizedFact], expected: dict[str, Any]) -> NormalizedFact | None:
    for fact in facts:
        if wiki_fact_matches(fact, expected):
            return fact
    return None


def check_value(observed: Any, expected: dict[str, Any]) -> tuple[bool, str]:
    expected_value = expected.get("value")
    tolerance_ratio = expected.get("tolerance_ratio")
    if expected_value in (None, ""):
        return True, ""
    if tolerance_ratio is not None:
        passed = value_within_tolerance(observed, expected_value, tolerance_ratio)
        return passed, "" if passed else f"value expected {expected_value!r} within {tolerance_ratio!r}, got {observed!r}"
    passed = decimal_equal(observed, expected_value)
    return passed, "" if passed else f"value expected {expected_value!r}, got {observed!r}"


def observed_field(observed: dict[str, Any], field: str) -> Any:
    for candidate in FIELD_ALIASES.get(field, (field,)):
        value = observed.get(candidate)
        if value not in (None, "", [], {}):
            return value
    return observed.get(field)


def check_expected_fields(observed: dict[str, Any], expected: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    errors: list[str] = []
    for field in fields:
        value = observed_field(observed, field)
        if field in expected and field in NUMERIC_EQUIVALENT_FIELDS | {"scale"} and decimal_equal(value, expected[field]):
            continue
        if field in expected and value != expected[field]:
            errors.append(f"{field} expected {expected[field]!r}, got {value!r}")
    return errors


def evidence_fields_present(observed: dict[str, Any], required: list[Any]) -> list[str]:
    errors: list[str] = []
    for field in required:
        if not isinstance(field, str):
            continue
        if observed_field(observed, field) in (None, "", [], {}):
            errors.append(f"evidence.{field} missing")
    return errors


def expected_required_evidence(expected: dict[str, Any]) -> list[str]:
    value = expected.get("required_evidence") or []
    return value if isinstance(value, list) else []


def expected_evidence_for_fact(case: dict[str, Any], expected_fact: dict[str, Any], index: int) -> dict[str, Any]:
    expected: dict[str, Any] = {}
    case_evidence = case.get("required_evidence")
    if isinstance(case_evidence, list) and case_evidence:
        if index - 1 < len(case_evidence) and isinstance(case_evidence[index - 1], dict):
            expected.update(case_evidence[index - 1])
        elif len(case_evidence) == 1 and isinstance(case_evidence[0], dict):
            expected.update(case_evidence[0])
    if isinstance(expected_fact.get("evidence"), dict):
        expected.update(expected_fact["evidence"])
    return expected


def expected_trace_value(case: dict[str, Any], field: str) -> Any:
    expected_trace = case.get("expected_trace") if isinstance(case.get("expected_trace"), dict) else {}
    return expected_trace.get(field)


def expected_trace_has(case: dict[str, Any], field: str) -> bool:
    expected_trace = case.get("expected_trace") if isinstance(case.get("expected_trace"), dict) else {}
    return field in expected_trace


def policy_allows_postgres_fallback(policy: dict[str, Any]) -> bool:
    return policy.get("allow_postgres_fallback", True) is not False


def check_trace_identity(case: dict[str, Any], trace: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    company = trace.get("resolved_company") if isinstance(trace.get("resolved_company"), dict) else {}
    period = trace.get("resolved_period") if isinstance(trace.get("resolved_period"), dict) else {}
    if case.get("market") and company.get("market") != case.get("market"):
        errors.append(f"resolved_company.market expected {case.get('market')!r}, got {company.get('market')!r}")
    company_id = company.get("id") or company.get("company_id")
    if case.get("company_id") and company_id != case.get("company_id"):
        errors.append(f"resolved_company.id expected {case.get('company_id')!r}, got {company_id!r}")
    if case.get("filing_id") and period.get("filing_id") != case.get("filing_id"):
        errors.append(f"resolved_period.filing_id expected {case.get('filing_id')!r}, got {period.get('filing_id')!r}")
    if case.get("report_id") and period.get("report_id") != case.get("report_id"):
        errors.append(f"resolved_period.report_id expected {case.get('report_id')!r}, got {period.get('report_id')!r}")
    period_value = period.get("period") or period.get("period_end")
    if case.get("period") and period_value != case.get("period"):
        errors.append(f"resolved_period.period expected {case.get('period')!r}, got {period_value!r}")
    return errors


def check_evidence_expectations(
    observed: dict[str, Any],
    expected_fact: dict[str, Any],
    case: dict[str, Any],
    index: int,
) -> list[str]:
    errors = evidence_fields_present(observed, expected_required_evidence(expected_fact))
    expected_evidence = expected_evidence_for_fact(case, expected_fact, index)
    for field, expected_value in expected_evidence.items():
        if field == "page_number_required":
            if expected_value and observed_field(observed, "page_number") in (None, "", [], {}):
                errors.append("evidence.page_number missing")
            continue
        if field == "bbox_required":
            if expected_value and observed_field(observed, "bbox") in (None, "", [], {}):
                errors.append("evidence.bbox missing")
            continue
        if expected_value in (None, "") or isinstance(expected_value, bool):
            continue
        observed_value = observed_field(observed, field)
        if field in NUMERIC_EQUIVALENT_FIELDS and decimal_equal(observed_value, expected_value):
            continue
        if observed_value != expected_value:
            errors.append(f"evidence.{field} expected {expected_value!r}, got {observed_value!r}")
    return errors


def calculation_value(expected: dict[str, Any]) -> Any:
    for key in ("result", "value", "output"):
        if expected.get(key) not in (None, ""):
            return expected.get(key)
    return None


def calculation_matches(run: dict[str, Any], expected: dict[str, Any]) -> bool:
    if expected.get("operation") and run.get("operation") != expected.get("operation"):
        return False
    expected_result = calculation_value(expected)
    if expected_result not in (None, ""):
        observed = run.get("result") if run.get("result") not in (None, "") else run.get("value") or run.get("output")
        tolerance_ratio = expected.get("tolerance_ratio")
        if tolerance_ratio is not None:
            if not value_within_tolerance(observed, expected_result, tolerance_ratio):
                return False
        elif not decimal_equal(observed, expected_result):
            return False
    for field in ("numerator", "denominator", "unit", "currency", "formula"):
        if field in expected and str(run.get(field) or "") != str(expected[field]):
            return False
    return True


def evaluate_expected_calculations(case: dict[str, Any], trace: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    expected_calculations = case.get("expected_calculations") or []
    if not isinstance(expected_calculations, list) or not expected_calculations:
        return [], []
    runs = trace.get("calculator_runs") if isinstance(trace.get("calculator_runs"), list) else []
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, expected in enumerate(expected_calculations, start=1):
        matched = None
        if isinstance(expected, dict):
            for run in runs:
                if isinstance(run, dict) and calculation_matches(run, expected):
                    matched = run
                    break
        if matched is None:
            operation = expected.get("operation") if isinstance(expected, dict) else None
            expected_result = calculation_value(expected) if isinstance(expected, dict) else None
            message = f"missing calculator_run[{index}] operation={operation!r} result={expected_result!r}"
            errors.append(message)
            results.append({"index": index, "passed": False, "operation": operation, "errors": [message]})
        else:
            results.append(
                {
                    "index": index,
                    "passed": True,
                    "operation": matched.get("operation"),
                    "result": matched.get("result") if matched.get("result") not in (None, "") else matched.get("value"),
                    "errors": [],
                }
            )
    return results, errors


def evaluate_trace_case(case: dict[str, Any], trace: dict[str, Any] | None) -> dict[str, Any]:
    errors: list[str] = []
    fact_results: list[dict[str, Any]] = []
    if trace is None:
        return {
            "case_id": case.get("case_id"),
            "market": case.get("market"),
            "tier": case.get("tier", "P0"),
            "mode": "trace-offline",
            "passed": False,
            "facts": [],
            "errors": ["missing answer_audit_trace"],
        }

    policy = case.get("source_policy") if isinstance(case.get("source_policy"), dict) else {}
    fallback_reason = trace.get("fallback_reason")
    errors.extend(check_trace_identity(case, trace))
    postgres_facts = trace.get("postgres_facts") if isinstance(trace.get("postgres_facts"), list) else []
    postgres_fallback_allowed = policy_allows_postgres_fallback(policy)
    if postgres_facts and not postgres_fallback_allowed:
        errors.append("postgres_facts present but source_policy.allow_postgres_fallback is false")
    if postgres_facts and not fallback_reason:
        errors.append("postgres_facts present without fallback_reason")
    if expected_trace_has(case, "fallback_reason") and fallback_reason != expected_trace_value(case, "fallback_reason"):
        errors.append(
            f"fallback_reason expected {expected_trace_value(case, 'fallback_reason')!r}, got {fallback_reason!r}"
        )
    allowed_reasons = set(policy.get("allowed_fallback_reasons") or [])
    if fallback_reason and allowed_reasons and fallback_reason not in allowed_reasons:
        errors.append(f"fallback_reason {fallback_reason!r} is not allowed")

    guardrail = trace.get("guardrail_result") if isinstance(trace.get("guardrail_result"), dict) else {}
    expected_guardrail = case.get("expected_guardrail") if isinstance(case.get("expected_guardrail"), dict) else {}
    should_answer = expected_guardrail.get("should_answer", True)
    if should_answer and guardrail.get("blocked") is True:
        errors.append("guardrail blocked an answer that should answer")
    if not should_answer and guardrail.get("blocked") is not True:
        errors.append("guardrail should block this answer")
    wiki_facts = trace.get("wiki_facts") if isinstance(trace.get("wiki_facts"), list) else []
    if expected_trace_value(case, "must_have_wiki_facts") and not wiki_facts:
        errors.append("expected wiki_facts in answer_audit_trace")

    for index, expected in enumerate(case.get("expected_facts") or [], start=1):
        fact, bucket = find_trace_fact(trace, expected)
        fact_errors: list[str] = []
        if fact is None:
            fact_results.append(
                {
                    "index": index,
                    "passed": False,
                    "source_bucket": None,
                    "key_fact_passed": False,
                    "period_passed": False,
                    "unit_currency_passed": False,
                    "evidence_passed": False,
                    "source_policy_passed": False,
                    "calculator_input_ready": False,
                    "errors": [f"missing trace fact: {fact_key(expected)}"],
                }
            )
            continue
        source_type = str(fact.get("source_type") or "")
        source_policy_passed = True
        if bucket == "wiki_facts":
            allowed = set(expected.get("required_source_types") or [])
            if allowed and source_type not in allowed:
                source_policy_passed = False
                fact_errors.append(f"source_type {source_type!r} is not in required_source_types")
        elif bucket == "postgres_facts":
            if not postgres_fallback_allowed:
                source_policy_passed = False
                fact_errors.append("postgres fallback is forbidden by source_policy.allow_postgres_fallback")
            allowed = set(expected.get("fallback_source_types") or [])
            if allowed and source_type not in allowed:
                source_policy_passed = False
                fact_errors.append(f"source_type {source_type!r} is not in fallback_source_types")
            if not fallback_reason:
                source_policy_passed = False
                fact_errors.append("postgres fallback fact has no fallback_reason")
        if policy.get("forbid_semantic_numeric_source") and source_type.startswith("semantic"):
            source_policy_passed = False
            fact_errors.append("semantic source is not allowed for numeric fact")

        value_passed, value_error = check_value(fact.get("value"), expected)
        if value_error:
            fact_errors.append(value_error)
        raw_errors = check_expected_fields(fact, expected, ("raw_value",))
        unit_currency_errors = check_expected_fields(
            fact,
            expected,
            ("unit", "currency", "fact_currency", "reporting_currency", "presentation_currency", "scale"),
        )
        fact_errors.extend(raw_errors)
        fact_errors.extend(unit_currency_errors)
        period = fact.get("period") or fact.get("period_key")
        expected_period = expected.get("period") or expected.get("period_key")
        period_passed = expected_period in (None, "") or period == expected_period
        if not period_passed:
            fact_errors.append(f"period expected {expected_period!r}, got {period!r}")
        evidence_errors = check_evidence_expectations(fact, expected, case, index)
        evidence_passed = not evidence_errors
        fact_errors.extend(evidence_errors)
        unit_currency_passed = not unit_currency_errors
        key_fact_passed = value_passed and not raw_errors
        calculator_input_ready = key_fact_passed and period_passed and unit_currency_passed and evidence_passed
        fact_results.append(
            {
                "index": index,
                "passed": calculator_input_ready and source_policy_passed,
                "source_bucket": bucket,
                "source_type": source_type,
                "key_fact_passed": key_fact_passed,
                "period_passed": period_passed,
                "unit_currency_passed": unit_currency_passed,
                "evidence_passed": evidence_passed,
                "source_policy_passed": source_policy_passed,
                "calculator_input_ready": calculator_input_ready,
                "errors": fact_errors,
            }
        )

    calculation_results, calculation_errors = evaluate_expected_calculations(case, trace)
    errors.extend(error for fact in fact_results for error in fact.get("errors") or [])
    errors.extend(calculation_errors)
    return {
        "case_id": case.get("case_id"),
        "market": case.get("market"),
        "tier": case.get("tier", "P0"),
        "mode": "trace-offline",
        "passed": not errors and (bool(fact_results) or not should_answer),
        "facts": fact_results,
        "calculations": calculation_results,
        "fallback_reason": fallback_reason,
        "guardrail_blocked": guardrail.get("blocked") is True,
        "errors": errors,
    }


def evaluate_wiki_static_case(case: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    fact_results: list[dict[str, Any]] = []
    document_path = resolve_case_document_path(case)
    if not document_path.exists():
        return {
            "case_id": case.get("case_id"),
            "market": case.get("market"),
            "tier": case.get("tier", "P0"),
            "mode": "wiki-static",
            "passed": False,
            "document_full_path": str(document_path),
            "facts": [],
            "errors": [f"document_full_path not found: {document_path}"],
        }
    document_full = read_json(document_path)
    identity = document_identity(document_full, fallback_market=case.get("market"))
    for field in ("market", "company_id"):
        if case.get(field) and identity.get(field) != case.get(field):
            errors.append(f"identity.{field} expected {case.get(field)!r}, got {identity.get(field)!r}")
    if case.get("filing_id") and identity.get("filing_id") != case.get("filing_id"):
        errors.append(f"identity.filing_id expected {case.get('filing_id')!r}, got {identity.get('filing_id')!r}")

    facts = normalize_document_facts(document_full)
    for index, expected in enumerate(case.get("expected_facts") or [], start=1):
        fact = find_wiki_fact(facts, expected)
        fact_errors: list[str] = []
        if fact is None:
            fact_results.append(
                {
                    "index": index,
                    "passed": False,
                    "key_fact_passed": False,
                    "period_passed": False,
                    "unit_currency_passed": False,
                    "evidence_passed": False,
                    "source_policy_passed": True,
                    "calculator_input_ready": False,
                    "errors": [f"missing document_full fact: {fact_key(expected)}"],
                }
            )
            continue
        value_passed, value_error = check_value(fact.value, expected)
        if value_error:
            fact_errors.append(value_error)
        observed = {
            "raw_value": fact.raw_value,
            "unit": fact.unit,
            "currency": fact.currency,
            "fact_currency": fact.fact_currency,
            "reporting_currency": fact.reporting_currency,
            "presentation_currency": fact.presentation_currency,
            "scale": fact.scale,
        }
        raw_errors = check_expected_fields(observed, expected, ("raw_value",))
        unit_currency_errors = check_expected_fields(
            observed,
            expected,
            ("unit", "currency", "fact_currency", "reporting_currency", "presentation_currency", "scale"),
        )
        fact_errors.extend(raw_errors)
        fact_errors.extend(unit_currency_errors)
        expected_period = expected.get("period") or expected.get("period_key")
        period_passed = expected_period in (None, "") or fact.period_key == expected_period
        if not period_passed:
            fact_errors.append(f"period expected {expected_period!r}, got {fact.period_key!r}")
        evidence = fact.evidence or {}
        evidence_errors = check_evidence_expectations(evidence, expected, case, index)
        if not evidence_errors and not has_reviewable_evidence(evidence):
            evidence_errors.append(f"expected reviewable evidence, got {evidence!r}")
        fact_errors.extend(evidence_errors)
        key_fact_passed = value_passed and not raw_errors
        unit_currency_passed = not unit_currency_errors
        evidence_passed = not evidence_errors
        calculator_input_ready = key_fact_passed and period_passed and unit_currency_passed and evidence_passed
        fact_results.append(
            {
                "index": index,
                "passed": calculator_input_ready,
                "key_fact_passed": key_fact_passed,
                "period_passed": period_passed,
                "unit_currency_passed": unit_currency_passed,
                "evidence_passed": evidence_passed,
                "source_policy_passed": True,
                "calculator_input_ready": calculator_input_ready,
                "errors": fact_errors,
            }
        )
    errors.extend(error for fact in fact_results for error in fact.get("errors") or [])
    return {
        "case_id": case.get("case_id"),
        "market": case.get("market"),
        "tier": case.get("tier", "P0"),
        "mode": "wiki-static",
        "passed": not errors and bool(fact_results),
        "document_full_path": str(document_path),
        "identity": identity,
        "facts": fact_results,
        "errors": errors,
    }


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    fact_results = [fact for result in results for fact in result.get("facts") or []]
    calculation_results = [calculation for result in results for calculation in result.get("calculations") or []]
    total = len(fact_results)
    total_calculations = len(calculation_results)
    summary = {
        "cases": len(results),
        "passed_cases": sum(1 for result in results if result.get("passed")),
        "facts": total,
        "calculations": total_calculations,
        "key_fact_accuracy": _rate(sum(1 for fact in fact_results if fact.get("key_fact_passed")), total),
        "period_unit_currency_accuracy": _rate(
            sum(1 for fact in fact_results if fact.get("period_passed") and fact.get("unit_currency_passed")),
            total,
        ),
        "evidence_coverage_rate": _rate(sum(1 for fact in fact_results if fact.get("evidence_passed")), total),
        "source_policy_pass_rate": _rate(sum(1 for fact in fact_results if fact.get("source_policy_passed")), total),
        "calculator_input_ready_rate": _rate(sum(1 for fact in fact_results if fact.get("calculator_input_ready")), total),
        "calculator_run_accuracy": _rate(
            sum(1 for calculation in calculation_results if calculation.get("passed")),
            total_calculations,
        )
        if total_calculations
        else 1.0,
        "guardrail_block_count": sum(1 for result in results if result.get("guardrail_blocked") is True),
    }
    summary["p0_gate_passed"] = all(
        summary[key] >= P0_REQUIRED_RATE
        for key in (
            "key_fact_accuracy",
            "period_unit_currency_accuracy",
            "evidence_coverage_rate",
            "source_policy_pass_rate",
            "calculator_input_ready_rate",
            "calculator_run_accuracy",
        )
    )
    return summary


def run_benchmark(
    *,
    case_root: Path = DEFAULT_CASE_ROOT,
    trace_log: Path = DEFAULT_TRACE_LOG,
    mode: str = "trace-offline",
) -> dict[str, Any]:
    if mode == "postgres-fallback":
        raise ValueError("postgres-fallback benchmark mode is reserved for the offline PostgreSQL release gate")
    if mode not in VALID_MODES:
        raise ValueError(f"unsupported mode: {mode}")
    cases = [case for case in load_cases(case_root) if mode in case_modes(case)]
    traces = load_trace_map(trace_log) if mode == "trace-offline" else {}
    validation_errors = {id(case): validate_case(case) for case in cases}
    if mode == "trace-offline":
        results = [
            invalid_case_result(case, mode, validation_errors[id(case)])
            if validation_errors[id(case)]
            else evaluate_trace_case(case, traces.get(str(case.get("case_id"))))
            for case in cases
        ]
    else:
        results = [
            invalid_case_result(case, mode, validation_errors[id(case)])
            if validation_errors[id(case)]
            else evaluate_wiki_static_case(case)
            for case in cases
        ]
    summary = summarize(results)
    return {
        "schema_version": "siq_financial_qa_benchmark_report_v1",
        "created_at": now_iso(),
        "mode": mode,
        "case_root": str(repo_path(case_root)),
        "trace_log": str(repo_path(trace_log)) if mode == "trace-offline" else None,
        "passed": bool(cases) and summary["p0_gate_passed"] and all(result.get("passed") for result in results),
        "summary": summary,
        "results": results,
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Financial QA Benchmark",
        "",
        f"Mode: `{report.get('mode')}`",
        f"Status: **{'PASS' if report.get('passed') else 'FAIL'}**",
        "",
        f"- Cases: {summary.get('passed_cases', 0)}/{summary.get('cases', 0)}",
        f"- Facts: {summary.get('facts', 0)}",
        f"- Key fact accuracy: {summary.get('key_fact_accuracy', 0):.3f}",
        f"- Period/unit/currency accuracy: {summary.get('period_unit_currency_accuracy', 0):.3f}",
        f"- Evidence coverage rate: {summary.get('evidence_coverage_rate', 0):.3f}",
        f"- Source policy pass rate: {summary.get('source_policy_pass_rate', 0):.3f}",
        f"- Calculator input ready rate: {summary.get('calculator_input_ready_rate', 0):.3f}",
        f"- Calculator run accuracy: {summary.get('calculator_run_accuracy', 0):.3f}",
        "",
        "| Case | Market | Status | Facts |",
        "| --- | --- | --- | ---: |",
    ]
    for result in report.get("results") or []:
        status = "PASS" if result.get("passed") else "FAIL"
        lines.append(f"| {result.get('case_id')} | {result.get('market')} | {status} | {len(result.get('facts') or [])} |")
        for error in result.get("errors") or []:
            lines.append(f"| {result.get('case_id')} error | {result.get('market')} | `{error}` |  |")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run deterministic SIQ financial QA benchmark.")
    parser.add_argument("--mode", choices=IMPLEMENTED_MODES, default="trace-offline")
    parser.add_argument("--case-root", type=Path, default=DEFAULT_CASE_ROOT)
    parser.add_argument("--trace-log", type=Path, default=DEFAULT_TRACE_LOG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--json", action="store_true", help="Print full JSON report.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_benchmark(case_root=args.case_root, trace_log=args.trace_log, mode=args.mode)
    output = repo_path(args.output)
    markdown = repo_path(args.markdown)
    write_json(output, report)
    markdown.parent.mkdir(parents=True, exist_ok=True)
    markdown.write_text(render_markdown(report), encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"{'PASS' if report.get('passed') else 'FAIL'} financial QA benchmark mode={args.mode}")
        print(f"JSON: {output}")
        print(f"Markdown: {markdown}")
        print(f"Key fact accuracy: {report['summary'].get('key_fact_accuracy', 0):.3f}")
        print(f"Evidence coverage rate: {report['summary'].get('evidence_coverage_rate', 0):.3f}")
    return 0 if report.get("passed") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
