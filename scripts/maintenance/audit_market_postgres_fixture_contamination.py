#!/usr/bin/env python3
"""Read-only audit for committed document_full fixtures in fixed market databases."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
IMPORTS_DIR = REPO_ROOT / "db" / "imports"
if str(IMPORTS_DIR) not in sys.path:
    sys.path.insert(0, str(IMPORTS_DIR))

from market_ingestion_contract import (  # noqa: E402
    MARKET_TARGETS,
    database_url,
    quote_ident,
)

SCHEMA_VERSION = "market_postgres_fixture_contamination_audit_v2"
FIXTURE_PATH_MARKER = "eval_datasets/market_document_full_postgres/examples/"
CASES_PATH = REPO_ROOT / "eval_datasets" / "market_document_full_postgres" / "cases.json"
DSN_RE = re.compile(r"postgres(?:ql)?(?:\+[a-z0-9_]+)?://\S+", re.IGNORECASE)
LEGACY_REAL_IDENTITY_FIXTURES = {
    "hk_row_period_document_full.json": {
        "fixture_version": "legacy_real_identity_v1",
        "case_id": "hk_row_period_currency_evidence",
        "market": "HK",
        "company_id": "HK:00700",
        "filing_id": "HK:00700:2025-annual",
        "parse_run_id": "parse_ab6f710544effd640be32294",
        "document_full_sha256": "ef98213e9272c87c076bcbd81b97c69b16f569dcc9e24c920d3db9680cf8c353",
        "task_id": "fixture-hk-00700",
    },
    "jp_row_period_document_full.json": {
        "fixture_version": "legacy_real_identity_v1",
        "case_id": "jp_row_period_local_language",
        "market": "JP",
        "company_id": "JP:7203",
        "filing_id": "JP:7203:2025-annual-securities-report",
        "parse_run_id": "parse_02d1bc8bb3300a75c7bf2167",
        "document_full_sha256": "2c7b045d9069c52f22776d0163520619973e7d8fedf9732e91c38d35df091db5",
        "task_id": "fixture-jp-7203",
    },
    "kr_row_period_document_full.json": {
        "fixture_version": "legacy_real_identity_v1",
        "case_id": "kr_row_period_kifrs",
        "market": "KR",
        "company_id": "KR:005930",
        "filing_id": "KR:005930:2025-annual",
        "parse_run_id": "parse_38a69501ae5adfa16872d12c",
        "document_full_sha256": "cab04a1f0f3dcd304cb376b617cf0de0d46ea335c45b71f70763862e50ae93f0",
        "task_id": "fixture-kr-005930",
    },
    "eu_period_map_document_full.json": {
        "fixture_version": "legacy_real_identity_v1",
        "case_id": "eu_period_map_country_currency",
        "market": "EU",
        "company_id": "EU:NL:ASML:NL0010273215",
        "filing_id": "EU:NL:ASML:2025-annual",
        "parse_run_id": "parse_b2d241997ca7effadcaa2abc",
        "document_full_sha256": "200147aad0517cb1662248b93ca02f91c368277e4748fdcf9b48335dab7c58fc",
        "task_id": "fixture-eu-asml",
    },
    "eu_multi_currency_document_full.json": {
        "fixture_version": "legacy_real_identity_v1",
        "case_id": "eu_multi_currency_original_fact_currency",
        "market": "EU",
        "company_id": "EU:GB:VOD:GB00BH4HKS39",
        "filing_id": "EU:GB:VOD:2025-annual",
        "parse_run_id": "parse_b9fa9d022bfc8d989e086ffe",
        "document_full_sha256": "84dd0f9bc824902576e38fd02e6c1ab658dfde566d8c1812560ec1f59b06b6c8",
        "task_id": "fixture-eu-vodafone-multi-currency",
    },
    "us_sec_document_full.json": {
        "fixture_version": "legacy_real_identity_v1",
        "case_id": "us_sec_ixbrl_fact_anchor",
        "market": "US",
        "company_id": "US:0000320193",
        "filing_id": "US:0000320193:0000320193-25-000079",
        "parse_run_id": "parse_a73b6a5ecfcda6ad7cea3800",
        "document_full_sha256": "7b822a20513e6c0e8716d2dca3e49b220f68c99cd5be69086e147fb5b273ee0b",
        "task_id": None,
    },
}


def _git_output(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_wiki_package_path(value: Any, *, marker: str = FIXTURE_PATH_MARKER) -> str:
    text = str(value or "").replace("\\", "/")
    marker_index = text.find(marker)
    if marker_index >= 0:
        return text[marker_index:]
    path = Path(text)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
        except ValueError:
            return "[external]"
    return path.as_posix()


def _safe_error(value: Any) -> str:
    return DSN_RE.sub("[redacted-dsn]", str(value)).replace(str(REPO_ROOT), "[repo]")


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _fixture_catalog(marker: str) -> dict[str, dict[str, Any]]:
    payload = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    catalog: dict[str, dict[str, Any]] = {}
    for case in payload.get("cases") or []:
        if not isinstance(case, dict) or str(case.get("market") or "").upper() not in MARKET_TARGETS:
            continue
        relative_path = Path(str(case.get("document_full_path") or ""))
        fixture_path = (CASES_PATH.parent / relative_path).resolve()
        if not fixture_path.is_file():
            continue
        document = json.loads(fixture_path.read_text(encoding="utf-8"))
        task = document.get("task") if isinstance(document, dict) else None
        task_id = task.get("task_id") if isinstance(task, dict) else None
        safe_path = marker + fixture_path.name
        current = {
            "fixture_version": "synthetic_identity_v2",
            "case_id": case.get("case_id"),
            "market": str(case.get("market") or "").upper(),
            "company_id": case.get("company_id"),
            "filing_id": (case.get("expected_identity") or {}).get("filing_id"),
            "document_full_sha256": _sha256(fixture_path),
            "task_id": str(task_id) if task_id not in (None, "") else None,
        }
        known_versions = [current]
        legacy = LEGACY_REAL_IDENTITY_FIXTURES.get(fixture_path.name)
        if legacy and legacy["document_full_sha256"] != current["document_full_sha256"]:
            known_versions.append(dict(legacy))
        catalog[safe_path] = {**current, "known_versions": known_versions}
    return catalog


def _matching_fixture_version(
    expected: dict[str, Any],
    *,
    filing_id: Any,
    document_full_sha256: Any,
    task_id: Any,
) -> dict[str, Any] | None:
    for version in expected.get("known_versions") or [expected]:
        if (
            str(version.get("filing_id") or "") == str(filing_id or "")
            and version.get("document_full_sha256") == document_full_sha256
            and version.get("task_id") == task_id
        ):
            return version
    return None


def _scalar(row: Any, index: int = 0) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()), None)
    return row[index]


def _connect_factory() -> Callable[[str], Any]:
    try:
        import psycopg
    except Exception as exc:  # pragma: no cover - environment dependent
        raise SystemExit(f"psycopg unavailable: {exc}") from exc
    return psycopg.connect


def _audit_market(
    market: str,
    *,
    marker: str,
    connect: Callable[[str], Any],
    url_for_market: Callable[[str | None, str], str],
    fixture_catalog: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    target = MARKET_TARGETS[market]
    schema = quote_ident(target.schema)
    conn = None
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    database_name = ""
    transaction_read_only = ""
    relation_present = False
    rolled_back = False
    try:
        conn = connect(url_for_market(None, market))
        conn.execute("set transaction read only")
        identity = conn.execute(
            "select current_database(), current_setting('transaction_read_only')"
        ).fetchone()
        if isinstance(identity, dict):
            database_name = str(identity.get("current_database") or identity.get("database_name") or "")
            transaction_read_only = str(identity.get("transaction_read_only") or "")
        elif identity:
            database_name = str(identity[0])
            transaction_read_only = str(identity[1])
        if database_name != target.database:
            errors.append(
                f"database identity mismatch: expected {target.database}, got {database_name or '<missing>'}"
            )
        if transaction_read_only != "on":
            errors.append(
                f"transaction is not read-only: {transaction_read_only or '<missing>'}"
            )
        relation_present = bool(
            _scalar(conn.execute("select to_regclass(%s)", (f"{target.schema}.parse_runs",)).fetchone())
        )
        if not relation_present:
            errors.append(f"required relation missing: {target.schema}.parse_runs")
        if not errors:
            result_rows = conn.execute(
                f"""
                select pr.parse_run_id, pr.filing_id, pr.wiki_package_path, pr.status,
                       pr.completed_at, pr.artifact_hashes, pr.raw, f.company_id
                from {schema}.parse_runs pr
                join {schema}.filings f on f.filing_id = pr.filing_id
                where position(%s in replace(pr.wiki_package_path, chr(92), '/')) > 0
                order by pr.parse_run_id
                """,
                (marker,),
            ).fetchall()
            for row in result_rows:
                safe_path = _safe_wiki_package_path(row[2], marker=marker)
                expected = fixture_catalog.get(safe_path) or {}
                artifact_hashes = _json_object(row[5])
                raw = _json_object(row[6])
                raw_task = raw.get("task") if isinstance(raw.get("task"), dict) else {}
                observed_sha256 = artifact_hashes.get("document_full.json")
                observed_task_id = raw_task.get("task_id")
                observed_company_id = row[7]
                matched_version = _matching_fixture_version(
                    expected,
                    filing_id=row[1],
                    document_full_sha256=observed_sha256,
                    task_id=observed_task_id,
                )
                reference = matched_version or expected
                sha256_match = bool(
                    reference.get("document_full_sha256")
                    and observed_sha256 == reference.get("document_full_sha256")
                )
                task_id_match = bool(expected) and (
                    observed_task_id == reference.get("task_id")
                )
                filing_id_match = bool(expected) and str(row[1]) == str(
                    reference.get("filing_id") or ""
                )
                parse_run_id_match = bool(expected) and (
                    not reference.get("parse_run_id")
                    or str(row[0]) == str(reference.get("parse_run_id"))
                )
                company_id_match = bool(expected) and str(observed_company_id) == str(
                    reference.get("company_id") or ""
                )
                exact_match = bool(
                    matched_version
                    and sha256_match
                    and task_id_match
                    and filing_id_match
                    and parse_run_id_match
                    and company_id_match
                )
                cleanup_assertions = {
                    "database": target.database,
                    "schema": target.schema,
                    "parse_run_id": str(row[0]),
                    "company_id": str(observed_company_id),
                    "filing_id": str(row[1]),
                    "wiki_package_path": safe_path,
                    "document_full_sha256": observed_sha256,
                    "task_id": observed_task_id,
                    "task_id_must_be_absent": observed_task_id is None,
                }
                rows.append({
                    "parse_run_id": str(row[0]),
                    "filing_id": str(row[1]),
                    "wiki_package_path": safe_path,
                    "status": str(row[3]),
                    "completed_at": row[4].isoformat() if hasattr(row[4], "isoformat") else row[4],
                    "fixture_case_id": reference.get("case_id"),
                    "fixture_version": reference.get("fixture_version"),
                    "expected_parse_run_id": reference.get("parse_run_id"),
                    "parse_run_id_match": parse_run_id_match,
                    "expected_company_id": reference.get("company_id"),
                    "observed_company_id": str(observed_company_id),
                    "company_id_match": company_id_match,
                    "expected_filing_id": reference.get("filing_id"),
                    "filing_id_match": filing_id_match,
                    "expected_document_full_sha256": reference.get("document_full_sha256"),
                    "observed_document_full_sha256": observed_sha256,
                    "document_full_sha256_match": sha256_match,
                    "expected_task_id": reference.get("task_id"),
                    "observed_task_id": observed_task_id,
                    "task_id_match": task_id_match,
                    "exact_match": exact_match,
                    "cleanup_candidate": exact_match,
                    "cleanup_assertions": cleanup_assertions,
                    "cleanup_action": (
                        "controlled_transactional_retirement_after_dependency_snapshot"
                        if exact_match
                        else "manual_assessment_required"
                    ),
                })
    except Exception as exc:
        errors.append(_safe_error(exc))
    finally:
        if conn is not None:
            try:
                conn.rollback()
                rolled_back = True
            except Exception as exc:
                errors.append(f"read-only transaction rollback failed: {_safe_error(exc)}")
            try:
                conn.close()
            except Exception as exc:
                errors.append(f"connection close failed: {_safe_error(exc)}")
    return {
        "market": market,
        "database": target.database,
        "schema": target.schema,
        "observed_database": database_name,
        "transaction_read_only": transaction_read_only,
        "relation_present": relation_present,
        "transaction_rolled_back": rolled_back,
        "contaminated_run_count": len(rows),
        "contaminated_runs": rows,
        "errors": errors,
        "passed": not errors and not rows,
    }


def audit_fixture_contamination(
    *,
    marker: str = FIXTURE_PATH_MARKER,
    connect: Callable[[str], Any] | None = None,
    url_for_market: Callable[[str | None, str], str] = database_url,
    explicit_database_url: str | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    if not marker.strip():
        raise ValueError("fixture path marker must not be empty")
    connector = connect or _connect_factory()
    fixture_catalog = _fixture_catalog(marker)
    market_results = [
        _audit_market(
            market,
            marker=marker,
            connect=connector,
            url_for_market=(
                lambda _unused, selected_market: url_for_market(
                    explicit_database_url, selected_market
                )
            ),
            fixture_catalog=fixture_catalog,
        )
        for market in MARKET_TARGETS
    ]
    contaminated_count = sum(result["contaminated_run_count"] for result in market_results)
    error_count = sum(len(result["errors"]) for result in market_results)
    exact_match_count = sum(
        1
        for result in market_results
        for run in result["contaminated_runs"]
        if run.get("exact_match") is True
    )
    cleanup_plan = [
        {
            "market": result["market"],
            "database": result["database"],
            "schema": result["schema"],
            "fixture_version": run.get("fixture_version"),
            "assertions": run.get("cleanup_assertions"),
            "action": run.get("cleanup_action"),
            "execute": False,
        }
        for result in market_results
        for run in result["contaminated_runs"]
        if run.get("cleanup_candidate") is True
    ]
    passed = contaminated_count == 0 and error_count == 0
    dirty_lines = [line for line in _git_output("status", "--porcelain").splitlines() if line]
    failures = [
        {
            "market": result["market"],
            "code": "audit_error",
            "count": len(result["errors"]),
        }
        for result in market_results
        if result["errors"]
    ] + [
        {
            "market": result["market"],
            "code": "fixture_rows_present",
            "count": result["contaminated_run_count"],
        }
        for result in market_results
        if result["contaminated_run_count"]
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_commit": _git_output("rev-parse", "HEAD"),
        "worktree_dirty": bool(dirty_lines),
        "worktree_summary": {"changed_path_count": len(dirty_lines)},
        "task_id": "T12",
        "environment_profile": "local-five-market-postgres-read-only",
        "command": (
            "python scripts/maintenance/audit_market_postgres_fixture_contamination.py "
            "--json-output <artifact.json> --markdown-output <artifact.md>"
        ),
        "result": "pass" if passed else "fail",
        "duration_seconds": round(time.monotonic() - started, 6),
        "failures": failures,
        "artifact_checksums": {
            "eval_datasets/market_document_full_postgres/cases.json": _sha256(CASES_PATH),
            **{
                path: str(expected["document_full_sha256"])
                for path, expected in sorted(fixture_catalog.items())
            },
        },
        "read_only": True,
        "fixture_path_marker": marker,
        "market_count": len(market_results),
        "contaminated_run_count": contaminated_count,
        "exact_match_count": exact_match_count,
        "non_exact_match_count": contaminated_count - exact_match_count,
        "cleanup_candidate_count": len(cleanup_plan),
        "cleanup_plan": cleanup_plan,
        "error_count": error_count,
        "passed": passed,
        "markets": market_results,
        "note": "Audit and cleanup assessment only. No database row is inserted, updated, or deleted; every transaction is rolled back. A cleanup candidate still requires a controlled dependency snapshot and transactional post-check before retirement.",
    }


def render_markdown(report: dict[str, Any]) -> str:
    worktree_summary = report.get("worktree_summary") or {}
    lines = [
        "# Market PostgreSQL Fixture Contamination Audit",
        "",
        f"Status: **{'PASS' if report.get('passed') else 'FAIL'}**",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Base commit: `{report.get('base_commit')}`",
        f"- Worktree dirty: `{report.get('worktree_dirty')}` "
        f"(changed paths={worktree_summary.get('changed_path_count', 'unknown')})",
        f"- Task: `{report.get('task_id')}`",
        f"- Environment: `{report.get('environment_profile')}`",
        f"- Command: `{report.get('command')}`",
        f"- Result: `{report.get('result')}`",
        f"- Duration: `{report.get('duration_seconds', 0):.3f}s`",
        f"- Read only: {report.get('read_only')}",
        f"- Fixture marker: `{report.get('fixture_path_marker')}`",
        f"- Contaminated parse runs: {report.get('contaminated_run_count', 0)}",
        f"- Exact fixture matches: {report.get('exact_match_count', 0)}",
        f"- Non-exact fixture matches: {report.get('non_exact_match_count', 0)}",
        f"- Cleanup candidates: {report.get('cleanup_candidate_count', 0)}",
        f"- Audit errors: {report.get('error_count', 0)}",
        "",
        "| Market | Database | Read only | Contaminated runs | Errors |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    for result in report.get("markets") or []:
        lines.append(
            f"| {result.get('market')} | {result.get('database')} | "
            f"{result.get('transaction_read_only') == 'on'} | "
            f"{result.get('contaminated_run_count', 0)} | {len(result.get('errors') or [])} |"
        )
        for run in result.get("contaminated_runs") or []:
            lines.append(
                f"| {result.get('market')} fixture | {run.get('filing_id')} | "
                f"`{run.get('parse_run_id')}` (version={run.get('fixture_version')}, "
                f"exact={run.get('exact_match')}, cleanup={run.get('cleanup_candidate')}) |  |  |"
            )
        for error in result.get("errors") or []:
            lines.append(f"| {result.get('market')} error |  | `{error}` |  |  |")
    lines.extend(["", "## Failures", ""])
    failures = report.get("failures") or []
    lines.extend(
        [f"- `{json.dumps(failure, ensure_ascii=False, sort_keys=True)}`" for failure in failures]
        or ["- None"]
    )
    lines.extend(
        [
            "",
            "## Artifact Checksums",
            "",
            "| Artifact | SHA-256 |",
            "| --- | --- |",
        ]
    )
    for artifact, checksum in sorted((report.get("artifact_checksums") or {}).items()):
        lines.append(f"| `{artifact}` | `{checksum}` |")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only audit for document_full fixture rows in five fixed market databases."
    )
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument("--json", action="store_true", help="Print the full JSON report.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = audit_fixture_contamination()
    markdown = render_markdown(report)
    if args.markdown_output:
        try:
            markdown_key = (
                args.markdown_output.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
            )
        except ValueError:
            markdown_key = f"<external>/{args.markdown_output.name}"
        report["artifact_checksums"][markdown_key] = hashlib.sha256(
            markdown.encode("utf-8")
        ).hexdigest()
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(markdown, encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        status = "PASS" if report["passed"] else "FAIL"
        print(
            f"{status} five-market fixture contamination audit: "
            f"runs={report['contaminated_run_count']} errors={report['error_count']}"
        )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
