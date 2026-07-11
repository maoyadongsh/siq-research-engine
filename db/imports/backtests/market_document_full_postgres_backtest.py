#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKTEST_DIR = Path(__file__).resolve().parent
if str(BACKTEST_DIR) not in sys.path:
    sys.path.insert(0, str(BACKTEST_DIR))
from document_fact_normalizer import (
    NormalizedFact,
    assertion_to_expected_fact,
    decimal_equal,
    document_identity,
    find_fact,
    has_evidence_value as _has_evidence_value,
    has_reviewable_evidence,
    normalize_document_facts,
    stable_json_hash,
    stable_row_list,
    stable_rows_hash,
    value_within_tolerance,
)
from contract_cases import (
    check_agent_case as _check_agent_case,
    check_case as _check_case,
    contract_assertion_stats,
)
from agent_query_gate import (
    AGENT_VIEW_EXPECTED_VALUE_COLUMNS,
    AGENT_VIEW_SELECT_COLUMNS,
    agent_view_fact_rows as _agent_view_fact_rows_impl,
    agent_view_filter_candidates as _agent_view_filter_candidates,
    check_production_agent_case as _check_production_agent_case,
    check_production_sample_agent_view_case as _check_production_sample_agent_view_case,
)
from agent_view_parity_helpers import (
    COMMON_CORE_METRICS,
    agent_view_row_diffs as _agent_view_row_diffs,
    agent_view_row_errors as _agent_view_row_errors,
    case_agent_questions as _case_agent_questions,
    diff_code_counts as _diff_code_counts,
    diff_codes as _diff_codes,
    explicit_assertion_value_fields as _explicit_assertion_value_fields,
    fact_query_filter as _fact_query_filter,
    fact_value_expectation as _fact_value_expectation,
    generated_wiki_postgres_questions as _generated_wiki_postgres_questions,
    json_safe,
    normalize_currency_label as _normalize_currency_label,
    parity_diff as _parity_diff,
    period_alias_candidate as _period_alias_candidate,
)
from production_sample_gate import (
    PRODUCTION_SAMPLE_MANIFEST_SCHEMA_VERSION,
    check_production_sample_db_coexistence as _check_production_sample_db_coexistence,
    production_sample_cases_from_manifest as _production_sample_cases_from_manifest,
    resolve_manifest_path as _resolve_manifest_path,
    validate_production_sample_manifest as _validate_production_sample_manifest,
)
from postgres_roundtrip_helpers import (
    DB_COUNT_TABLES,
    DB_DEFAULT_REQUIRED_FAMILIES,
    DB_FACT_EVIDENCE_TABLES,
    DB_REVIEWABLE_EVIDENCE_COLUMNS,
    DB_TABLE_FAMILIES,
    case_selector_values as _case_selector_values,
    check_count_expectation as _check_count_expectation,
    check_expected_counts,
    db_content_hashes,
    db_count,
    db_count_for_case,
    db_evidence_join_rows as _db_evidence_join_rows,
    db_fact_evidence_rows as _db_fact_evidence_rows,
    db_family_counts,
    db_required_evidence_check,
    db_table_counts,
    fact_filter_candidates as _fact_filter_candidates,
    has_reviewable_db_evidence,
    int_count as _int_count,
    relation_columns,
    relation_exists,
    safe_sql_ident,
    scoped_where_for_table,
    select_existing_columns as _select_existing_columns,
    simple_selector_column as _simple_selector_column,
    table_columns,
    table_exists,
    table_name_from_count_key as _table_name_from_count_key,
)
from postgres_roundtrip_gate import (
    check_db_case as _check_db_case,
    check_db_case_sequence as _check_db_case_sequence,
    db_selector_for_case,
    document_path_for_case,
    import_case_document_full as _default_import_case_document_full,
)
from report_writer import render_markdown_report as _render_markdown_report
from report_writer import write_report as _write_report
from wiki_postgres_parity_gate import check_wiki_postgres_parity_case as _check_wiki_postgres_parity_case

DEFAULT_CASES_PATH = REPO_ROOT / "eval_datasets" / "market_document_full_postgres" / "cases.json"
DEFAULT_LOCAL_REPORT_DIR = REPO_ROOT / "artifacts" / "eval-runs" / "local"
DEFAULT_OUTPUT_PATH = DEFAULT_LOCAL_REPORT_DIR / "market_document_full_postgres_backtest.json"
DEFAULT_MARKDOWN_PATH = DEFAULT_LOCAL_REPORT_DIR / "market_document_full_postgres_backtest.md"
DEFAULT_PRODUCTION_SAMPLE_MANIFEST_PATH = (
    REPO_ROOT / "eval_datasets" / "market_document_full_postgres" / "production_sample_manifest.json"
)
IMPORTS_DIR = REPO_ROOT / "db" / "imports"
if str(IMPORTS_DIR) not in sys.path:
    sys.path.insert(0, str(IMPORTS_DIR))
from market_ingestion_contract import database_url as market_database_url

MARKET_DATABASES = {
    "HK": "siq_hk",
    "JP": "siq_jp",
    "KR": "siq_kr",
    "EU": "siq_eu",
    "US": "siq_us",
}
MARKET_SCHEMAS = {
    "HK": "pdf2md_hk",
    "JP": "edinet_jp",
    "KR": "dart_kr",
    "EU": "eu_ifrs",
    "US": "sec_us",
}
def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_manifest_path(path: str | Path) -> Path:
    return _resolve_manifest_path(path, repo_root=REPO_ROOT)


def validate_production_sample_manifest(path: Path | None, *, require_existing: bool = True) -> dict[str, Any]:
    return _validate_production_sample_manifest(
        path,
        repo_root=REPO_ROOT,
        market_databases=MARKET_DATABASES,
        require_existing=require_existing,
    )


def production_sample_cases_from_manifest(sample_manifest_result: dict[str, Any]) -> list[dict[str, Any]]:
    return _production_sample_cases_from_manifest(sample_manifest_result, market_databases=MARKET_DATABASES)


def ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def database_url_for_market(market: str, explicit_url: str | None = None) -> str:
    return market_database_url(explicit_url, market)


def _agent_view_fact_rows(
    conn: Any,
    schema: str,
    case: dict[str, Any],
    where_sql: str,
    params: tuple[Any, ...],
    expected: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    return _agent_view_fact_rows_impl(conn, schema, case, where_sql, params, expected)

def check_wiki_postgres_parity_case(
    case: dict[str, Any],
    cases_path: Path,
    *,
    database_url: str | None = None,
    db_result: dict[str, Any] | None = None,
    generated_limit: int = 5,
) -> dict[str, Any]:
    return _check_wiki_postgres_parity_case(
        case,
        cases_path,
        market_schemas=MARKET_SCHEMAS,
        database_url_for_market=database_url_for_market,
        db_selector_for_case=db_selector_for_case,
        document_path_for_case=document_path_for_case,
        read_json=read_json,
        database_url=database_url,
        db_result=db_result,
        generated_limit=generated_limit,
    )


def check_production_agent_case(
    case: dict[str, Any],
    *,
    database_url: str | None = None,
    db_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _check_production_agent_case(
        case,
        market_schemas=MARKET_SCHEMAS,
        database_url_for_market=database_url_for_market,
        db_selector_for_case=db_selector_for_case,
        database_url=database_url,
        db_result=db_result,
    )


def check_production_sample_agent_view_case(
    case: dict[str, Any],
    *,
    database_url: str | None = None,
    db_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _check_production_sample_agent_view_case(
        case,
        market_schemas=MARKET_SCHEMAS,
        database_url_for_market=database_url_for_market,
        database_url=database_url,
        db_result=db_result,
    )


def check_case(case: dict[str, Any], cases_path: Path) -> dict[str, Any]:
    return _check_case(case, cases_path, read_json=read_json)


def check_agent_case(case: dict[str, Any], cases_path: Path) -> dict[str, Any]:
    return _check_agent_case(case, cases_path, read_json=read_json)


def _import_case_document_full(
    case: dict[str, Any],
    *,
    cases_path: Path,
    database_url: str | None = None,
    run_ddl: bool = True,
) -> str:
    return _default_import_case_document_full(
        case,
        cases_path=cases_path,
        imports_dir=IMPORTS_DIR,
        database_url=database_url,
        run_ddl=run_ddl,
    )


def check_db_case(
    case: dict[str, Any],
    *,
    cases_path: Path,
    database_url: str | None = None,
    import_before_check: bool = False,
    idempotency: bool = False,
    run_ddl_before_import: bool = True,
) -> dict[str, Any]:
    return _check_db_case(
        case,
        cases_path=cases_path,
        market_schemas=MARKET_SCHEMAS,
        database_url_for_market=database_url_for_market,
        database_url=database_url,
        import_before_check=import_before_check,
        idempotency=idempotency,
        run_ddl_before_import=run_ddl_before_import,
        import_case_document_full=_import_case_document_full,
    )


def check_db_case_sequence(
    cases: list[dict[str, Any]],
    *,
    cases_path: Path,
    database_url: str | None = None,
    import_before_check: bool = False,
    idempotency: bool = False,
) -> list[dict[str, Any]]:
    return _check_db_case_sequence(
        cases,
        cases_path=cases_path,
        market_schemas=MARKET_SCHEMAS,
        database_url_for_market=database_url_for_market,
        database_url=database_url,
        import_before_check=import_before_check,
        idempotency=idempotency,
        import_case_document_full=_import_case_document_full,
    )


def check_production_sample_db_coexistence(
    production_sample_db_results: list[dict[str, Any]],
    *,
    database_url: str | None = None,
) -> list[dict[str, Any]]:
    return _check_production_sample_db_coexistence(
        production_sample_db_results,
        market_schemas=MARKET_SCHEMAS,
        database_url_for_market=database_url_for_market,
        relation_exists=relation_exists,
        safe_sql_ident=safe_sql_ident,
        database_url=database_url,
    )


def run_cases(
    cases_path: Path = DEFAULT_CASES_PATH,
    *,
    verify_db: bool = False,
    database_url: str | None = None,
    import_before_db_check: bool = False,
    idempotency: bool = False,
    production_sample_manifest_path: Path | None = DEFAULT_PRODUCTION_SAMPLE_MANIFEST_PATH,
    require_production_sample_files: bool = True,
    production_sample_db: bool = False,
    production_agent_query: bool = False,
) -> dict[str, Any]:
    if idempotency and not import_before_db_check:
        raise ValueError("--idempotency requires --import-before-db-check")
    if production_agent_query and not verify_db:
        raise ValueError("--production-agent-query requires --db")
    if production_sample_db and not (verify_db and import_before_db_check):
        raise ValueError("--production-sample-db requires --db --import-before-db-check")
    if production_sample_db and not idempotency:
        raise ValueError("--production-sample-db requires --idempotency")
    if production_sample_db and not require_production_sample_files:
        raise ValueError("--production-sample-db requires real sample files")
    payload = read_json(cases_path)
    cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(cases, list):
        raise ValueError(f"{cases_path} does not contain a cases list")
    sample_manifest_result = validate_production_sample_manifest(
        production_sample_manifest_path,
        require_existing=require_production_sample_files,
    )
    production_sample_cases = production_sample_cases_from_manifest(sample_manifest_result)
    results = [check_case(case, cases_path) for case in cases]
    agent_results = [check_agent_case(case, cases_path) for case in cases]
    assertion_stats = contract_assertion_stats(cases)
    market_counts: dict[str, int] = {}
    for result in results:
        market = str(result.get("market") or "")
        market_counts[market] = market_counts.get(market, 0) + 1
    db_results = check_db_case_sequence(
        cases,
        cases_path=cases_path,
        database_url=database_url,
        import_before_check=import_before_db_check,
        idempotency=idempotency,
    ) if verify_db else []
    db_result_by_case = {result.get("case_id"): result for result in db_results if isinstance(result, dict)}
    fixture_production_agent_results = [
        check_production_agent_case(
            case,
            database_url=database_url,
            db_result=db_result_by_case.get(case.get("case_id")),
        )
        for case in cases
    ] if verify_db and production_agent_query else []
    wiki_postgres_parity_results = [
        check_wiki_postgres_parity_case(
            case,
            cases_path,
            database_url=database_url,
            db_result=db_result_by_case.get(case.get("case_id")),
        )
        for case in cases
    ] if verify_db and import_before_db_check else []

    production_sample_db_results = check_db_case_sequence(
        production_sample_cases,
        cases_path=production_sample_manifest_path or DEFAULT_PRODUCTION_SAMPLE_MANIFEST_PATH,
        database_url=database_url,
        import_before_check=True,
        idempotency=idempotency,
    ) if production_sample_db else []
    production_sample_db_coexistence_results = check_production_sample_db_coexistence(
        production_sample_db_results,
        database_url=database_url,
    ) if production_sample_db else []
    production_sample_db_result_by_case = {
        result.get("case_id"): result for result in production_sample_db_results if isinstance(result, dict)
    }
    production_sample_agent_results = [
        check_production_sample_agent_view_case(
            case,
            database_url=database_url,
            db_result=production_sample_db_result_by_case.get(case.get("case_id")),
        )
        for case in production_sample_cases
    ] if verify_db and production_agent_query and production_sample_db else []
    production_agent_results = fixture_production_agent_results + production_sample_agent_results
    production_sample_wiki_postgres_parity_results = [
        check_wiki_postgres_parity_case(
            case,
            production_sample_manifest_path or DEFAULT_PRODUCTION_SAMPLE_MANIFEST_PATH,
            database_url=database_url,
            db_result=production_sample_db_result_by_case.get(case.get("case_id")),
        )
        for case in production_sample_cases
    ] if verify_db and import_before_db_check and production_sample_db else []
    all_wiki_postgres_parity_results = wiki_postgres_parity_results + production_sample_wiki_postgres_parity_results
    db_verified = bool(db_results) and all(result.get("passed") or result.get("skipped") for result in db_results)
    db_passed_count = sum(1 for result in db_results if result.get("passed") and not result.get("skipped"))
    db_case_count = sum(1 for result in db_results if not result.get("skipped"))
    production_sample_db_coexistence_verified = bool(production_sample_db_coexistence_results) and all(
        result.get("passed") for result in production_sample_db_coexistence_results
    )
    production_sample_db_verified = bool(production_sample_db_results) and all(
        result.get("passed") and not result.get("skipped") for result in production_sample_db_results
    ) and production_sample_db_coexistence_verified
    production_sample_db_passed_count = sum(
        1 for result in production_sample_db_results if result.get("passed") and not result.get("skipped")
    )
    production_agent_non_skipped = [result for result in production_agent_results if not result.get("skipped")]
    production_agent_query_verified = bool(production_agent_non_skipped) and all(
        result.get("passed") for result in production_agent_non_skipped
    )
    production_sample_agent_non_skipped = [
        result for result in production_sample_agent_results if not result.get("skipped")
    ]
    production_sample_agent_view_verified = bool(production_sample_agent_non_skipped) and all(
        result.get("passed") for result in production_sample_agent_non_skipped
    )
    wiki_postgres_parity_non_skipped = [
        result for result in all_wiki_postgres_parity_results if not result.get("skipped")
    ]
    wiki_postgres_parity_verified = bool(wiki_postgres_parity_non_skipped) and all(
        result.get("passed") for result in wiki_postgres_parity_non_skipped
    )
    db_required_evidence_checks = [
        check
        for result in db_results
        for check in (result.get("required_evidence_checks") or [])
        if isinstance(check, dict)
    ]
    db_table_count_check_count = sum(
        len(result.get("table_counts") or {})
        for result in db_results
        if not result.get("skipped")
    )
    agent_verified = bool(agent_results) and all(result["passed"] for result in agent_results)
    all_passed = (
        all(result["passed"] for result in results)
        and agent_verified
        and (db_verified if verify_db else True)
        and (production_sample_db_verified if production_sample_db else True)
        and (production_agent_query_verified if production_agent_query else True)
        and (wiki_postgres_parity_verified if verify_db and import_before_db_check else True)
    )
    evidence_passed_count = sum(int(result.get("required_evidence_passed_count") or 0) for result in results)
    evidence_checked_count = sum(int(result.get("required_evidence_checked_count") or 0) for result in results)
    unit_currency_passed_count = sum(int(result.get("unit_currency_passed_count") or 0) for result in results)
    unit_currency_explainability_checked_count = sum(
        int(result.get("unit_currency_checked_count") or 0) for result in results
    )
    postgres_idempotency_verified = bool(verify_db and import_before_db_check and idempotency and db_verified)
    postgres_required_evidence_verified = bool(
        db_required_evidence_checks and all(check.get("passed") for check in db_required_evidence_checks)
    )
    real_sample_minimum_met = bool(sample_manifest_result.get("passed"))
    acceptance_requirements = {
        "fixture_contract": all(result["passed"] for result in results),
        "fixture_agent_fact_lookup": agent_verified,
        "postgres_import_idempotency": postgres_idempotency_verified,
        "postgres_required_evidence": postgres_required_evidence_verified,
        "real_sample_minimum": real_sample_minimum_met,
        "real_sample_postgres_roundtrip": production_sample_db_verified,
        "real_sample_postgres_coexistence": production_sample_db_coexistence_verified,
        "real_sample_agent_view_query": production_sample_agent_view_verified,
        "wiki_postgres_query_parity": wiki_postgres_parity_verified,
        "production_agent_query": production_agent_query_verified,
    }
    acceptance_passed = all(acceptance_requirements.values())
    return {
        "schema_version": "market_document_full_postgres_backtest_results_v1",
        "cases_path": str(cases_path),
        "mode": (
            "document_full_fixture_contract+postgres_import_idempotency"
            if verify_db and import_before_db_check and idempotency
            else "document_full_fixture_contract+postgres_import_roundtrip"
            if verify_db and import_before_db_check
            else "document_full_fixture_contract+postgres_existing_row_check"
            if verify_db
            else "document_full_fixture_contract"
        ),
        "note": "This runner validates document_full row-shape, identity, value, unit/currency, evidence contracts, fixed fixture fact-lookups, and the real-sample manifest. With --db it checks PostgreSQL table-family/table counts and required-evidence facts; --import-before-db-check first imports document_full fixtures; --idempotency repeats the import and compares row/table counts plus content hashes; --production-agent-query validates fixed Agent questions and real-sample parse_run probes through v_agent_financial_facts; DB import mode also compares Wiki/document_full facts against PostgreSQL Agent-view facts for the same metrics.",
        "passed": all_passed,
        "acceptance_passed": acceptance_passed,
        "acceptance_requirements": acceptance_requirements,
        "passed_count": sum(1 for result in results if result["passed"]),
        "case_count": len(results),
        "market_counts": market_counts,
        "summary": {
            "assertion_count": assertion_stats["assertion_count"],
            "common_core_assertion_count": assertion_stats["common_core_assertion_count"],
            "required_evidence_assertion_count": assertion_stats["required_evidence_assertion_count"],
            "evidence_coverage_ratio": ratio(evidence_passed_count, evidence_checked_count),
            "required_evidence_passed_count": evidence_passed_count,
            "required_evidence_checked_count": evidence_checked_count,
            "unit_checked_assertion_count": assertion_stats["unit_checked_assertion_count"],
            "currency_checked_assertion_count": assertion_stats["currency_checked_assertion_count"],
            "fact_currency_checked_assertion_count": assertion_stats["fact_currency_checked_assertion_count"],
            "unit_currency_explainability_ratio": ratio(
                unit_currency_passed_count,
                unit_currency_explainability_checked_count,
            ),
            "unit_currency_explainability_passed_count": unit_currency_passed_count,
            "unit_currency_explainability_checked_count": unit_currency_explainability_checked_count,
            "postgres_existing_row_check_verified": bool(verify_db and not import_before_db_check and db_verified),
            "postgres_roundtrip_verified": bool(verify_db and import_before_db_check and db_verified),
            "postgres_import_executed": bool(verify_db and import_before_db_check),
            "postgres_idempotency_verified": postgres_idempotency_verified,
            "postgres_roundtrip_passed_count": db_passed_count,
            "postgres_roundtrip_case_count": db_case_count,
            "postgres_family_count_checked_count": db_case_count * len(DB_TABLE_FAMILIES) if verify_db else 0,
            "postgres_table_count_checked_count": db_table_count_check_count,
            "postgres_required_evidence_verified": postgres_required_evidence_verified,
            "postgres_required_evidence_passed_count": sum(1 for check in db_required_evidence_checks if check.get("passed")),
            "postgres_required_evidence_checked_count": len(db_required_evidence_checks),
            "agent_query_verified": agent_verified,
            "agent_query_mode": "fixture_fact_lookup",
            "agent_query_passed_count": sum(1 for result in agent_results if result["passed"]),
            "agent_query_case_count": len(agent_results),
            "production_sample_manifest_path": sample_manifest_result.get("path"),
            "production_sample_require_existing": sample_manifest_result.get("require_existing"),
            "production_sample_goal_per_market": sample_manifest_result.get("sample_goal_per_market"),
            "production_sample_manifest_counts": sample_manifest_result.get("market_counts"),
            "production_sample_existing_counts": sample_manifest_result.get("existing_counts"),
            "production_sample_missing_count": sum(len(items) for items in (sample_manifest_result.get("missing") or {}).values()),
            "real_sample_minimum_met": real_sample_minimum_met,
            "production_sample_db_executed": bool(production_sample_db),
            "production_sample_db_verified": production_sample_db_verified,
            "production_sample_db_passed_count": production_sample_db_passed_count,
            "production_sample_db_case_count": len(production_sample_db_results),
            "production_sample_db_coexistence_verified": production_sample_db_coexistence_verified,
            "production_sample_db_coexistence_passed_count": sum(
                1 for result in production_sample_db_coexistence_results if result.get("passed")
            ),
            "production_sample_db_coexistence_market_count": len(production_sample_db_coexistence_results),
            "production_agent_query_executed": bool(production_agent_query),
            "production_agent_query_verified": production_agent_query_verified,
            "production_agent_query_passed_count": sum(
                1 for result in production_agent_non_skipped if result.get("passed")
            ),
            "production_agent_query_case_count": len(production_agent_non_skipped),
            "production_sample_agent_view_verified": production_sample_agent_view_verified,
            "production_sample_agent_view_passed_count": sum(
                1 for result in production_sample_agent_non_skipped if result.get("passed")
            ),
            "production_sample_agent_view_case_count": len(production_sample_agent_non_skipped),
            "wiki_postgres_query_parity_verified": wiki_postgres_parity_verified,
            "wiki_postgres_query_parity_passed_count": sum(
                1 for result in wiki_postgres_parity_non_skipped if result.get("passed")
            ),
            "wiki_postgres_query_parity_case_count": len(wiki_postgres_parity_non_skipped),
            "wiki_postgres_query_parity_warning_count": sum(
                len(result.get("warnings") or []) for result in wiki_postgres_parity_non_skipped
            ),
            "wiki_postgres_query_parity_diff_code_counts": _diff_code_counts(wiki_postgres_parity_non_skipped),
            "wiki_postgres_query_parity_warning_code_counts": _diff_code_counts(
                wiki_postgres_parity_non_skipped,
                source="warnings",
            ),
            "wiki_postgres_query_parity_error_code_counts": _diff_code_counts(
                wiki_postgres_parity_non_skipped,
                source="errors",
            ),
            "production_sample_wiki_postgres_query_parity_passed_count": sum(
                1 for result in production_sample_wiki_postgres_parity_results if result.get("passed") and not result.get("skipped")
            ),
            "production_sample_wiki_postgres_query_parity_case_count": sum(
                1 for result in production_sample_wiki_postgres_parity_results if not result.get("skipped")
            ),
            "production_sample_wiki_postgres_query_parity_warning_count": sum(
                len(result.get("warnings") or [])
                for result in production_sample_wiki_postgres_parity_results
                if not result.get("skipped")
            ),
            "production_sample_wiki_postgres_query_parity_diff_code_counts": _diff_code_counts(
                [result for result in production_sample_wiki_postgres_parity_results if not result.get("skipped")]
            ),
            "production_sample_wiki_postgres_query_parity_warning_code_counts": _diff_code_counts(
                [result for result in production_sample_wiki_postgres_parity_results if not result.get("skipped")],
                source="warnings",
            ),
            "production_sample_wiki_postgres_query_parity_error_code_counts": _diff_code_counts(
                [result for result in production_sample_wiki_postgres_parity_results if not result.get("skipped")],
                source="errors",
            ),
        },
        "results": results,
        "agent_results": agent_results,
        "db_results": db_results,
        "production_sample_manifest": sample_manifest_result,
        "production_sample_db_results": production_sample_db_results,
        "production_sample_db_coexistence_results": production_sample_db_coexistence_results,
        "production_sample_agent_results": production_sample_agent_results,
        "production_agent_results": production_agent_results,
        "wiki_postgres_parity_results": wiki_postgres_parity_results,
        "production_sample_wiki_postgres_parity_results": production_sample_wiki_postgres_parity_results,
    }

def write_report(summary: dict[str, Any], output_path: Path | None, markdown_path: Path | None) -> None:
    _write_report(summary, output_path, markdown_path)


def render_markdown_report(summary: dict[str, Any]) -> str:
    return _render_markdown_report(summary)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run minimal market document_full PostgreSQL backtest cases.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH, help="Path to backtest cases.json.")
    parser.add_argument("--json", action="store_true", help="Print full JSON results.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="Write JSON report.")
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN_PATH, help="Write Markdown report.")
    parser.add_argument("--no-write", action="store_true", help="Do not write report files.")
    parser.add_argument("--db", action="store_true", help="Also verify PostgreSQL row counts for cases with market schemas.")
    parser.add_argument("--import-before-db-check", action="store_true", help="With --db, run the document_full importer before checking row counts.")
    parser.add_argument("--idempotency", action="store_true", help="With --db --import-before-db-check, import each case twice and compare row counts.")
    parser.add_argument("--database-url", default=None, help="Override PostgreSQL URL for --db checks.")
    parser.add_argument(
        "--production-sample-manifest",
        type=Path,
        default=DEFAULT_PRODUCTION_SAMPLE_MANIFEST_PATH,
        help="Path to real document_full sample manifest used for the real-sample acceptance gate.",
    )
    parser.add_argument(
        "--skip-production-sample-manifest",
        action="store_true",
        help="Disable the real-sample manifest acceptance gate.",
    )
    parser.add_argument(
        "--production-sample-db",
        action="store_true",
        help="With --db --import-before-db-check --idempotency, import all real samples from the manifest and verify idempotency.",
    )
    parser.add_argument(
        "--production-agent-query",
        action="store_true",
        help="With --db, validate fixed Agent questions against each market v_agent_financial_facts view.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.idempotency and not args.import_before_db_check:
        raise SystemExit("--idempotency requires --import-before-db-check")
    if args.production_agent_query and not args.db:
        raise SystemExit("--production-agent-query requires --db")
    if args.production_sample_db and not (args.db and args.import_before_db_check and args.idempotency):
        raise SystemExit("--production-sample-db requires --db --import-before-db-check --idempotency")
    production_sample_manifest_path = None if args.skip_production_sample_manifest else args.production_sample_manifest
    summary = run_cases(
        args.cases,
        verify_db=args.db,
        database_url=args.database_url,
        import_before_db_check=args.import_before_db_check,
        idempotency=args.idempotency,
        production_sample_manifest_path=production_sample_manifest_path,
        production_sample_db=args.production_sample_db,
        production_agent_query=args.production_agent_query,
    )
    if not args.no_write:
        write_report(summary, args.output, args.markdown)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(
            f"{'PASS' if summary['passed'] else 'FAIL'} "
            f"{summary['passed_count']}/{summary['case_count']} market document_full PostgreSQL backtest cases"
        )
        for result in summary["results"]:
            status = "PASS" if result["passed"] else "FAIL"
            print(f"{status} {result['case_id']} facts={result['fact_count']}")
            for error in result["errors"]:
                print(f"  - {error}")
    return 0 if summary["acceptance_passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
