"""Pure Agent-view parity helpers for market document_full gates."""

from __future__ import annotations

import json
from typing import Any

from document_fact_normalizer import (
    NormalizedFact,
    assertion_to_expected_fact,
    decimal_equal,
    has_reviewable_evidence,
    value_within_tolerance,
)


COMMON_CORE_METRICS = {
    "revenue",
    "gross_profit",
    "operating_profit",
    "profit_before_tax",
    "net_profit",
    "total_assets",
    "total_liabilities",
    "total_equity",
    "operating_cash_flow",
    "capex",
    "cash_and_equivalents",
    "cash_and_cash_equivalents",
    "basic_eps",
    "diluted_eps",
    "roe",
    "gross_margin",
    "current_assets",
}
AGENT_VIEW_REVIEWABLE_EVIDENCE_COLUMNS = (
    "evidence_page_number",
    "evidence_table_index",
    "evidence_bbox",
    "quote_text",
    "source_url",
    "wiki_package_path",
)


def has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, (dict, list, tuple, set)) and not value:
        return False
    return True


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def has_reviewable_agent_view_evidence(row: dict[str, Any]) -> bool:
    return any(has_value(row.get(field)) for field in AGENT_VIEW_REVIEWABLE_EVIDENCE_COLUMNS)


def normalize_currency_label(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text.startswith("ISO4217:"):
        text = text.split(":", 1)[1].strip()
    return {"RMB": "CNY", "CNH": "CNY"}.get(text, text)


def parity_diff(code: str, field: str, expected: Any, observed: Any, message: str) -> dict[str, Any]:
    return {
        "code": code,
        "field": field,
        "expected": json_safe(expected),
        "observed": json_safe(observed),
        "message": message,
    }


def agent_view_row_diffs(row: dict[str, Any], expected: dict[str, Any]) -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []
    tolerance_ratio = expected.get("tolerance_ratio")
    if "value" in expected:
        row_value = row.get("value")
        raw_value_matches = decimal_equal(row.get("raw_value"), expected.get("raw_value", expected["value"]))
        if row_value is None and raw_value_matches:
            pass
        elif tolerance_ratio is not None and not value_within_tolerance(row_value, expected["value"], tolerance_ratio):
            diffs.append(
                parity_diff(
                    "value_mismatch",
                    "value",
                    expected["value"],
                    row_value,
                    f"value expected {expected['value']!r} within {tolerance_ratio!r}, got {row_value!r}",
                )
            )
        elif tolerance_ratio is None and not decimal_equal(row_value, expected["value"]):
            diffs.append(
                parity_diff(
                    "value_mismatch",
                    "value",
                    expected["value"],
                    row_value,
                    f"value expected {expected['value']!r}, got {row_value!r}",
                )
            )
    for field in ("raw_value", "unit", "converted_value", "scale"):
        if field in expected and field in row and not decimal_equal(row.get(field), expected[field]):
            diffs.append(
                parity_diff(
                    "unit_display_diff",
                    field,
                    expected[field],
                    row.get(field),
                    f"{field} expected {expected[field]!r}, got {row.get(field)!r}",
                )
            )
    for field in ("currency", "fact_currency", "reporting_currency", "presentation_currency", "converted_currency"):
        if field in expected and field in row and str(row.get(field) or "").strip() != str(expected[field] or "").strip():
            diffs.append(
                parity_diff(
                    "currency_label_diff",
                    field,
                    expected[field],
                    row.get(field),
                    f"{field} expected {expected[field]!r}, got {row.get(field)!r}",
                )
            )
    if expected.get("required_evidence") is True and not has_reviewable_agent_view_evidence(row):
        diffs.append(
            parity_diff(
                "evidence_missing",
                "required_evidence",
                True,
                False,
                "expected reviewable evidence from agent view",
            )
        )
    return diffs


def agent_view_row_errors(row: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    return [diff["message"] for diff in agent_view_row_diffs(row, expected)]


def diff_codes(diffs: list[dict[str, Any]]) -> list[str]:
    return sorted({str(diff.get("code") or "") for diff in diffs if diff.get("code")})


def diff_code_counts(results: list[dict[str, Any]], *, source: str | None = None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        questions = result.get("questions") if isinstance(result, dict) else None
        if not isinstance(questions, list):
            continue
        for question in questions:
            if not isinstance(question, dict):
                continue
            if source == "errors" and question.get("passed"):
                continue
            if source == "warnings" and not result.get("passed"):
                continue
            for code in question.get("diff_codes") or []:
                counts[str(code)] = counts.get(str(code), 0) + 1
    return dict(sorted(counts.items()))


def period_alias_candidate(row: dict[str, Any], wiki_fact: NormalizedFact) -> bool:
    if not row:
        return False
    observed_value = row.get("value")
    if observed_value is None:
        observed_value = row.get("raw_value")
    return decimal_equal(observed_value, wiki_fact.value) or decimal_equal(row.get("raw_value"), wiki_fact.raw_value)


def fact_query_filter(fact: NormalizedFact, case: dict[str, Any]) -> dict[str, Any]:
    expected: dict[str, Any] = {}
    for field in ("statement_type", "period_key", "canonical_name", "concept"):
        value = getattr(fact, field, None)
        if value not in (None, ""):
            expected[field] = value
    if not any(expected.get(field) for field in ("canonical_name", "concept")):
        for field in ("name", "label"):
            value = getattr(fact, field, None)
            if value not in (None, ""):
                expected[field] = value
    expected.setdefault("period_key", case.get("period_key"))
    return expected


def fact_value_expectation(fact: NormalizedFact, *, require_evidence: bool) -> dict[str, Any]:
    expected: dict[str, Any] = {
        "value": fact.value,
    }
    for field in (
        "raw_value",
        "unit",
        "currency",
        "fact_currency",
        "reporting_currency",
        "presentation_currency",
        "converted_currency",
        "converted_value",
        "scale",
    ):
        value = getattr(fact, field, None)
        if value not in (None, ""):
            expected[field] = value
    if require_evidence:
        expected["required_evidence"] = True
    return expected


def explicit_assertion_value_fields(expected: dict[str, Any]) -> dict[str, Any]:
    return {
        field: expected[field]
        for field in (
            "value",
            "raw_value",
            "unit",
            "currency",
            "fact_currency",
            "reporting_currency",
            "presentation_currency",
            "converted_currency",
            "converted_value",
            "scale",
            "required_evidence",
        )
        if field in expected
    }


def generated_wiki_postgres_questions(
    facts: list[NormalizedFact],
    case: dict[str, Any],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    candidates: list[tuple[int, int, NormalizedFact]] = []
    for index, fact in enumerate(facts):
        if fact.value in (None, ""):
            continue
        filter_expected = fact_query_filter(fact, case)
        if not any(filter_expected.get(field) for field in ("canonical_name", "name", "label", "concept")):
            continue
        score = 0
        if fact.canonical_name in COMMON_CORE_METRICS:
            score += 5
        if fact.canonical_name:
            score += 3
        if fact.concept:
            score += 2
        if has_reviewable_evidence(fact.evidence or {}):
            score += 1
        candidates.append((-score, index, fact))
    candidates.sort(key=lambda item: (item[0], item[1]))
    questions: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for _score, index, fact in candidates:
        filter_expected = fact_query_filter(fact, case)
        key = (
            filter_expected.get("statement_type"),
            filter_expected.get("period_key"),
            filter_expected.get("canonical_name"),
            filter_expected.get("name"),
            filter_expected.get("label"),
            filter_expected.get("concept"),
        )
        if key in seen:
            continue
        seen.add(key)
        questions.append(
            {
                "question_id": f"{case.get('case_id')}:wiki_pg_parity:{index}",
                "expected_fact": filter_expected,
                "wiki_fact": fact,
                "explicit_expected": False,
            }
        )
        if len(questions) >= limit:
            break
    return questions


def case_agent_questions(case: dict[str, Any], facts: list[NormalizedFact]) -> list[dict[str, Any]]:
    questions = case.get("agent_questions")
    if isinstance(questions, list) and questions:
        return [question for question in questions if isinstance(question, dict)]
    assertions = [
        {
            "question_id": f"{case.get('case_id')}:wiki_pg_parity",
            "expected_fact": assertion_to_expected_fact(assertion, case),
            "explicit_expected": True,
        }
        for assertion in (case.get("assertions") or case.get("expected_facts") or [])
        if isinstance(assertion, dict)
    ]
    if assertions:
        return assertions
    return generated_wiki_postgres_questions(facts, case)


__all__ = [
    "AGENT_VIEW_REVIEWABLE_EVIDENCE_COLUMNS",
    "COMMON_CORE_METRICS",
    "agent_view_row_diffs",
    "agent_view_row_errors",
    "case_agent_questions",
    "diff_code_counts",
    "diff_codes",
    "explicit_assertion_value_fields",
    "fact_query_filter",
    "fact_value_expectation",
    "generated_wiki_postgres_questions",
    "has_reviewable_agent_view_evidence",
    "json_safe",
    "normalize_currency_label",
    "parity_diff",
    "period_alias_candidate",
]
