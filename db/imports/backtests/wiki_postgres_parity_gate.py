"""Offline Wiki/document_full versus PostgreSQL Agent-view parity gate."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_query_gate import agent_view_fact_rows
from agent_view_parity_helpers import (
    agent_view_row_diffs,
    case_agent_questions,
    diff_code_counts,
    diff_codes,
    explicit_assertion_value_fields,
    fact_value_expectation,
    json_safe,
    parity_diff,
    period_alias_candidate,
)
from document_fact_normalizer import (
    NormalizedFact,
    assertion_to_expected_fact,
    find_fact,
    has_reviewable_evidence,
    normalize_document_facts,
)


DatabaseUrlForMarket = Callable[[str, str | None], str]
DbSelectorForCase = Callable[[dict[str, Any]], tuple[str, tuple[Any, ...]]]
DocumentPathForCase = Callable[[dict[str, Any], Path], Path]
ReadJson = Callable[[Path], Any]


def check_wiki_postgres_parity_case(
    case: dict[str, Any],
    cases_path: Path,
    *,
    market_schemas: dict[str, str],
    database_url_for_market: DatabaseUrlForMarket,
    db_selector_for_case: DbSelectorForCase,
    document_path_for_case: DocumentPathForCase,
    read_json: ReadJson,
    database_url: str | None = None,
    db_result: dict[str, Any] | None = None,
    generated_limit: int = 5,
    connect: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    market = str(case.get("market") or "").upper()
    mode = "wiki_postgres_query_parity"
    if market not in market_schemas:
        return {
            "case_id": case.get("case_id"),
            "market": market,
            "passed": True,
            "skipped": True,
            "reason": "legacy_or_unsupported_market",
            "mode": mode,
        }
    if db_result and db_result.get("skipped"):
        return {
            "case_id": case.get("case_id"),
            "market": market,
            "passed": True,
            "skipped": True,
            "reason": db_result.get("reason") or "db case skipped",
            "mode": mode,
        }
    if db_result and not db_result.get("passed"):
        return {
            "case_id": case.get("case_id"),
            "market": market,
            "passed": False,
            "errors": ["PostgreSQL result failed; wiki/PG parity not trusted"],
            "mode": mode,
        }
    if connect is None:
        try:
            import psycopg
        except Exception as exc:
            return {
                "case_id": case.get("case_id"),
                "market": market,
                "passed": False,
                "errors": [f"psycopg unavailable: {exc}"],
                "mode": mode,
            }
        connect = psycopg.connect

    document_full = read_json(document_path_for_case(case, cases_path))
    facts = normalize_document_facts(document_full)
    questions = case_agent_questions(case, facts)
    if not (case.get("agent_questions") or case.get("assertions") or case.get("expected_facts")):
        questions = questions[:generated_limit]
    if not questions:
        return {
            "case_id": case.get("case_id"),
            "market": market,
            "passed": True,
            "skipped": True,
            "reason": "no comparable Wiki/document_full facts",
            "mode": mode,
        }
    parse_run_id = str((db_result or {}).get("parse_run_id") or "")
    if parse_run_id:
        where_sql, params = "parse_run_id = %s", (parse_run_id,)
    else:
        where_sql, params = db_selector_for_case(case)

    schema = market_schemas[market]
    errors: list[str] = []
    checked = 0
    passed_checks = 0
    checked_questions: list[dict[str, Any]] = []
    explicit_parity = any(bool(question.get("explicit_expected")) for question in questions if isinstance(question, dict))
    try:
        with connect(database_url_for_market(market, database_url)) as conn:
            for question in questions:
                expected = question.get("expected_fact") if isinstance(question.get("expected_fact"), dict) else question
                expected = assertion_to_expected_fact(expected, case)
                question_id = str(question.get("question_id") or f"{case.get('case_id')}:wiki_pg_parity")
                wiki_fact = question.get("wiki_fact") if isinstance(question.get("wiki_fact"), NormalizedFact) else None
                if wiki_fact is None:
                    wiki_fact = find_fact(facts, expected)
                checked += 1
                if wiki_fact is None:
                    diff = parity_diff(
                        "wiki_missing",
                        "wiki_fact",
                        expected,
                        None,
                        f"{question_id}: missing Wiki/document_full fact {expected}",
                    )
                    errors.append(diff["message"])
                    checked_questions.append(
                        {
                            "question_id": question_id,
                            "passed": False,
                            "reason": "wiki_fact_missing",
                            "parity_diffs": [diff],
                            "diff_codes": diff_codes([diff]),
                        }
                    )
                    continue
                query_expected = dict(expected)
                query_expected.pop("value", None)
                query_expected.pop("raw_value", None)
                query_expected.pop("unit", None)
                query_expected.pop("currency", None)
                query_expected.pop("scale", None)
                query_expected.pop("required_evidence", None)
                rows, reason = agent_view_fact_rows(conn, schema, case, where_sql, params, query_expected)
                if not rows:
                    diffs: list[dict[str, Any]] = []
                    relaxed_rows: list[dict[str, Any]] = []
                    if query_expected.get("period_key"):
                        relaxed_expected = dict(query_expected)
                        expected_period = relaxed_expected.pop("period_key", None)
                        relaxed_rows, _relaxed_reason = agent_view_fact_rows(
                            conn,
                            schema,
                            case,
                            where_sql,
                            params,
                            relaxed_expected,
                        )
                        alias_row = next(
                            (row for row in relaxed_rows if period_alias_candidate(row, wiki_fact)),
                            None,
                        )
                        if alias_row is not None:
                            observed_period = (
                                alias_row.get("period_key")
                                or alias_row.get("period_end")
                                or alias_row.get("filing_period_end")
                                or alias_row.get("context_ref")
                            )
                            diffs.append(
                                parity_diff(
                                    "period_alias_diff",
                                    "period_key",
                                    expected_period,
                                    observed_period,
                                    f"{question_id}: PostgreSQL agent view matched metric/value with a different period alias",
                                )
                            )
                    if not diffs:
                        diffs.append(
                            parity_diff(
                                "postgres_missing",
                                "agent_view_fact",
                                query_expected,
                                None,
                                f"{question_id}: PostgreSQL agent view missing comparable fact: {reason or query_expected}",
                            )
                        )
                    errors.append("; ".join(diff["message"] for diff in diffs))
                    checked_questions.append(
                        {
                            "question_id": question_id,
                            "passed": False,
                            "row_count": len(relaxed_rows) if relaxed_rows else 0,
                            "reason": reason,
                            "parity_diffs": diffs,
                            "diff_codes": diff_codes(diffs),
                            **({"first_postgres_candidate": json_safe(relaxed_rows[0])} if relaxed_rows else {}),
                        }
                    )
                    continue
                if question.get("explicit_expected"):
                    value_expected = explicit_assertion_value_fields(expected)
                    if "value" not in value_expected:
                        value_expected["value"] = wiki_fact.value
                else:
                    value_expected = fact_value_expectation(
                        wiki_fact,
                        require_evidence=bool(expected.get("required_evidence") or has_reviewable_evidence(wiki_fact.evidence or {})),
                    )
                parity_expected = {**query_expected, **value_expected}
                row_diffs_by_candidate = [agent_view_row_diffs(row, parity_expected) for row in rows]
                row_errors_by_candidate = [[diff["message"] for diff in row_diffs] for row_diffs in row_diffs_by_candidate]
                match_index = next((index for index, row_diffs in enumerate(row_diffs_by_candidate) if not row_diffs), None)
                if match_index is None:
                    first_errors = row_errors_by_candidate[0] if row_errors_by_candidate else ["missing comparable fact"]
                    first_diffs = row_diffs_by_candidate[0] if row_diffs_by_candidate else [
                        parity_diff("postgres_missing", "agent_view_fact", parity_expected, None, "missing comparable fact")
                    ]
                    errors.append(f"{question_id}: {'; '.join(first_errors)}")
                    checked_questions.append(
                        {
                            "question_id": question_id,
                            "passed": False,
                            "row_count": len(rows),
                            "candidate_errors": first_errors,
                            "parity_diffs": first_diffs,
                            "diff_codes": diff_codes(first_diffs),
                            "wiki_fact": json_safe(wiki_fact.__dict__),
                            "first_postgres_candidate": json_safe(rows[0]),
                        }
                    )
                    continue
                passed_checks += 1
                checked_questions.append(
                    {
                        "question_id": question_id,
                        "passed": True,
                        "row_count": len(rows),
                        "diff_codes": [],
                        "parity_diffs": [],
                        "wiki_fact": json_safe(wiki_fact.__dict__),
                        "matched_postgres_fact": json_safe(rows[match_index]),
                    }
                )
    except Exception as exc:
        errors.append(str(exc))

    warnings: list[str] = []
    passed = checked > 0 and not errors
    minimum_generated_passes = min(2, checked)
    if not explicit_parity and checked > 0 and passed_checks >= minimum_generated_passes:
        warnings = errors
        errors = []
        passed = True
    result = {
        "case_id": case.get("case_id"),
        "market": market,
        "passed": passed,
        "checked": checked,
        "passed_checks": passed_checks,
        "errors": errors,
        "warnings": warnings,
        "minimum_generated_passes": minimum_generated_passes if not explicit_parity else None,
        "schema": schema,
        "view": f"{schema}.v_agent_financial_facts",
        "mode": mode,
        "questions": checked_questions,
    }
    result["diff_code_counts"] = diff_code_counts([result])
    result["warning_diff_code_counts"] = result["diff_code_counts"] if warnings else {}
    result["error_diff_code_counts"] = result["diff_code_counts"] if errors else {}
    return result


__all__ = ["check_wiki_postgres_parity_case"]
