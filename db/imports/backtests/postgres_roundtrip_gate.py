"""DB import/roundtrip/idempotency orchestration for document_full gates."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

from postgres_roundtrip_helpers import (
    DB_DEFAULT_REQUIRED_FAMILIES,
    check_expected_counts,
    db_content_hashes,
    db_family_counts,
    db_required_evidence_check,
    db_scope_issues,
    db_table_counts,
)

DatabaseUrlForMarket = Callable[[str, str | None], str]
DocumentImporter = Callable[[dict[str, Any]], str]


def db_selector_for_case(case: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    if case.get("parse_run_id"):
        return "parse_run_id = %s", (case["parse_run_id"],)
    filing_id = (case.get("expected_identity") or {}).get("filing_id") or case.get("filing_id")
    if filing_id:
        return "filing_id = %s", (filing_id,)
    return "parse_run_id = %s", ("__missing_parse_run__",)


def document_path_for_case(case: dict[str, Any], cases_path: Path) -> Path:
    return (cases_path.parent / str(case["document_full_path"])).resolve()


def import_case_document_full(
    case: dict[str, Any],
    *,
    cases_path: Path,
    imports_dir: Path,
    database_url: str | None = None,
    run_ddl: bool = True,
) -> str:
    if str(imports_dir) not in sys.path:
        sys.path.insert(0, str(imports_dir))
    from import_market_document_full_to_postgres import import_document_full

    return import_document_full(
        document_path_for_case(case, cases_path),
        market=str(case.get("market") or ""),
        database_url_value=database_url,
        run_ddl_flag=run_ddl,
    )


def check_db_case(
    case: dict[str, Any],
    *,
    cases_path: Path,
    market_schemas: dict[str, str],
    database_url_for_market: DatabaseUrlForMarket,
    database_url: str | None = None,
    import_before_check: bool = False,
    idempotency: bool = False,
    run_ddl_before_import: bool = True,
    import_case_document_full: Callable[..., str] | None = None,
) -> dict[str, Any]:
    market = str(case.get("market") or "").upper()
    if market not in market_schemas:
        return {
            "case_id": case.get("case_id"),
            "market": market,
            "passed": True,
            "skipped": True,
            "reason": "legacy_or_unsupported_market",
        }
    try:
        import psycopg
    except Exception as exc:
        return {"case_id": case.get("case_id"), "market": market, "passed": False, "errors": [f"psycopg unavailable: {exc}"]}

    importer = import_case_document_full
    if importer is None:
        raise ValueError("import_case_document_full is required")

    schema = market_schemas[market]
    imported_parse_run_id = ""
    errors: list[str] = []
    counts: dict[str, int] = {}
    table_counts: dict[str, int] = {}
    second_counts: dict[str, int] = {}
    second_table_counts: dict[str, int] = {}
    content_hashes: dict[str, str] = {}
    second_content_hashes: dict[str, str] = {}
    required_evidence_checks: list[dict[str, Any]] = []
    scope_issues: list[dict[str, Any]] = []
    try:
        if idempotency and not import_before_check:
            errors.append("--idempotency requires --import-before-db-check")
            raise RuntimeError(errors[-1])
        if import_before_check:
            imported_parse_run_id = importer(
                case,
                cases_path=cases_path,
                database_url=database_url,
                run_ddl=run_ddl_before_import,
            )
        if imported_parse_run_id:
            where_sql, params = "parse_run_id = %s", (imported_parse_run_id,)
        else:
            where_sql, params = db_selector_for_case(case)
        with psycopg.connect(database_url_for_market(market, database_url)) as conn:
            expected_table_counts = case.get("expected_table_counts") if isinstance(case.get("expected_table_counts"), dict) else {}
            counts = db_family_counts(conn, schema, case, where_sql, params)
            table_counts = db_table_counts(conn, schema, case, where_sql, params, expected_table_counts)
            scope_issues = db_scope_issues(conn, schema, case, where_sql, params, expected_table_counts)
            errors.extend(str(issue.get("message")) for issue in scope_issues if issue.get("message"))
            content_hashes = db_content_hashes(conn, schema, where_sql, params)
            for assertion in case.get("assertions") or case.get("expected_facts") or []:
                if isinstance(assertion, dict) and assertion.get("required_evidence") is True:
                    evidence_check = db_required_evidence_check(conn, schema, case, where_sql, params, assertion)
                    required_evidence_checks.append(evidence_check)
                    if not evidence_check.get("passed"):
                        errors.append(f"{evidence_check.get('label')}: {evidence_check.get('reason')}")
        for family, error_message in DB_DEFAULT_REQUIRED_FAMILIES.items():
            if counts.get(family, 0) < 1:
                errors.append(error_message)
        check_expected_counts(errors, family_counts=counts, table_counts=table_counts, case=case)
        expected_hashes = case.get("expected_db_content_hashes") if isinstance(case.get("expected_db_content_hashes"), dict) else {}
        for key, expected_hash in expected_hashes.items():
            observed_hash = content_hashes.get(str(key))
            if observed_hash != expected_hash:
                errors.append(f"{key}: expected content hash {expected_hash!r}, got {observed_hash!r}")
        if import_before_check and idempotency:
            imported_again = importer(case, cases_path=cases_path, database_url=database_url, run_ddl=False)
            if imported_again != imported_parse_run_id:
                errors.append(f"idempotency parse_run_id changed: {imported_parse_run_id!r} -> {imported_again!r}")
            with psycopg.connect(database_url_for_market(market, database_url)) as conn:
                expected_table_counts = case.get("expected_table_counts") if isinstance(case.get("expected_table_counts"), dict) else {}
                second_counts = db_family_counts(conn, schema, case, where_sql, params)
                second_table_counts = db_table_counts(conn, schema, case, where_sql, params, expected_table_counts)
                second_content_hashes = db_content_hashes(conn, schema, where_sql, params)
            if second_counts != counts:
                errors.append(f"idempotency row counts changed: {counts!r} -> {second_counts!r}")
            if second_table_counts != table_counts:
                errors.append(f"idempotency table counts changed: {table_counts!r} -> {second_table_counts!r}")
            if second_content_hashes != content_hashes:
                errors.append(f"idempotency content hashes changed: {content_hashes!r} -> {second_content_hashes!r}")
    except Exception as exc:
        if not errors or str(exc) != errors[-1]:
            errors.append(str(exc))

    return {
        "case_id": case.get("case_id"),
        "market": market,
        "passed": not errors,
        "errors": errors,
        "counts": counts,
        "table_counts": table_counts,
        "second_counts": second_counts,
        "second_table_counts": second_table_counts,
        "content_hashes": content_hashes,
        "second_content_hashes": second_content_hashes,
        "required_evidence_checks": required_evidence_checks,
        "scope_issues": scope_issues,
        "schema": schema,
        "parse_run_id": imported_parse_run_id,
        "imported_before_check": import_before_check,
        "run_ddl_before_import": bool(import_before_check and run_ddl_before_import),
        "idempotency_checked": idempotency,
        "check_type": "import_idempotency"
        if import_before_check and idempotency
        else "import_roundtrip"
        if import_before_check
        else "existing_row_check",
    }


def check_db_case_sequence(
    cases: list[dict[str, Any]],
    *,
    cases_path: Path,
    market_schemas: dict[str, str],
    database_url_for_market: DatabaseUrlForMarket,
    database_url: str | None = None,
    import_before_check: bool = False,
    idempotency: bool = False,
    import_case_document_full: Callable[..., str] | None = None,
) -> list[dict[str, Any]]:
    initialized_markets: set[str] = set()
    results: list[dict[str, Any]] = []
    for case in cases:
        market = str(case.get("market") or "").upper()
        run_ddl_before_import = import_before_check and market in market_schemas and market not in initialized_markets
        result = check_db_case(
            case,
            cases_path=cases_path,
            market_schemas=market_schemas,
            database_url_for_market=database_url_for_market,
            database_url=database_url,
            import_before_check=import_before_check,
            idempotency=idempotency,
            run_ddl_before_import=run_ddl_before_import,
            import_case_document_full=import_case_document_full,
        )
        results.append(result)
        if run_ddl_before_import and market in market_schemas:
            initialized_markets.add(market)
    return results


__all__ = [
    "check_db_case",
    "check_db_case_sequence",
    "db_selector_for_case",
    "document_path_for_case",
    "import_case_document_full",
]
