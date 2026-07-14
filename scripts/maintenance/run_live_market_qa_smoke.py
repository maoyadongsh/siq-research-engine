#!/usr/bin/env python3
"""Validate the live Wiki-to-agent core fact path across supported markets."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "apps" / "api"
DEFAULT_WIKI_ROOT = REPO_ROOT / "data" / "wiki"
DEFAULT_CASES = {
    "CN": {
        "metric_question": "分析A股美的集团营业收入",
        "package_question": "分析A股美的集团财务表现",
    },
    "HK": {
        "metric_question": "HK TENCENT revenue",
        "package_question": "分析 HK TENCENT 财务表现",
    },
    "US": {
        "metric_question": "US Apple Inc revenue",
        "package_question": "分析 US Apple Inc 财务表现",
    },
    "JP": {
        "metric_question": "日本 Toyota Motor revenue",
        "package_question": "分析日本 Toyota Motor 财务表现",
    },
    "KR": {
        "metric_question": "韩国 Samsung Electronics total assets",
        "package_question": "分析韩国 Samsung Electronics 财务表现",
    },
    "EU": {
        "metric_question": "欧洲 SAP SE total assets",
        "package_question": "分析欧洲 SAP SE 财务表现",
    },
}
EXPECTED_STATEMENTS = {"income_statement", "cash_flow_statement", "balance_sheet"}
IDENTITY_FIELDS = ("company_id", "report_id", "filing_id", "parse_run_id")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wiki-root", type=Path, default=DEFAULT_WIKI_ROOT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true", help="Print the full JSON report.")
    return parser.parse_args()


def _has_locator(row: dict[str, Any]) -> bool:
    pdf_locator = bool(
        row.get("task_id")
        and (row.get("pdf_page") or row.get("table_index") not in (None, "") or row.get("md_line") not in (None, ""))
    )
    external_locator = bool(row.get("source_url") and (row.get("source_anchor") or row.get("xbrl_tag")))
    return pdf_locator or external_locator


def _rows(result: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [row for row in ((result or {}).get("rows") or []) if isinstance(row, dict)]


def _coverage(rows: list[dict[str, Any]]) -> float:
    return sum(1 for row in rows if _has_locator(row)) / len(rows) if rows else 0.0


def _research_context(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not result:
        return None
    identity = {
        key: result.get(key)
        for key in ("market", "company_id", "filing_id", "parse_run_id")
        if result.get(key) not in (None, "")
    }
    return {"research_identity": identity} if identity else None


def _structured_metric_errors(result: dict[str, Any] | None, market: str) -> list[str]:
    if not result:
        return ["structured_metric_miss"]
    rows = _rows(result)
    errors: list[str] = []
    validation_status = str((result.get("validation") or {}).get("status") or "not_available").casefold()
    if not rows:
        errors.append("no_metric_rows")
    if validation_status in {"fail", "not_available"}:
        errors.append(f"metric_validation_{validation_status}")
    if _coverage(rows) < 1.0:
        errors.append("incomplete_metric_evidence")
    if not result.get("company_id") or not result.get("report_id"):
        errors.append("incomplete_metric_company_report_identity")
    if market != "CN" and (not result.get("filing_id") or not result.get("parse_run_id")):
        errors.append("incomplete_metric_research_identity")
    return errors


def _fulltext_metric_errors(result: dict[str, Any] | None) -> list[str]:
    if not result:
        return ["fulltext_metric_miss"]
    rows = _rows(result)
    errors: list[str] = []
    if not rows:
        errors.append("no_fulltext_rows")
    if _coverage(rows) < 1.0:
        errors.append("incomplete_fulltext_evidence")
    if not result.get("report_id"):
        errors.append("incomplete_fulltext_report_identity")
    if not result.get("company_id"):
        errors.append("incomplete_fulltext_company_identity")
    if not result.get("filing_id") or not result.get("parse_run_id"):
        errors.append("incomplete_fulltext_research_identity")
    numeric_rows = 0
    for row in rows:
        snippet = str(row.get("snippet") or "")
        has_number = bool(re.search(r"(?<![A-Za-z0-9])[+-]?(?:\d[\d,]*(?:\.\d+)?|\.\d+)", snippet))
        has_unit = bool(
            row.get("unit")
            or row.get("currency")
            or re.search(r"(?i)(?:RMB|CNY|人民币|人民幣|HKD|HK\$|港币|港幣|USD|US\$|美元|EUR|JPY|KRW|%|million|百万元|亿元)", snippet)
        )
        if has_number and has_unit:
            numeric_rows += 1
    if numeric_rows == 0:
        errors.append("no_numeric_fulltext_evidence")
    return errors


def _package_errors(result: dict[str, Any] | None, market: str) -> list[str]:
    if not result:
        return ["three_statement_package_miss"]
    rows = _rows(result)
    statement_types = {str(row.get("statement_type") or "") for row in rows}
    validation_status = str((result.get("validation") or {}).get("status") or "not_available").casefold()
    errors: list[str] = []
    if not rows:
        errors.append("no_package_rows")
    if statement_types != EXPECTED_STATEMENTS:
        errors.append("incomplete_three_statement_coverage")
    if validation_status in {"fail", "not_available"}:
        errors.append(f"package_validation_{validation_status}")
    if _coverage(rows) < 1.0:
        errors.append("incomplete_package_evidence")
    if not result.get("company_id") or not result.get("report_id"):
        errors.append("incomplete_package_company_report_identity")
    if market != "CN" and (not result.get("filing_id") or not result.get("parse_run_id")):
        errors.append("incomplete_package_research_identity")
    return errors


def _scope_errors(
    metric_result: dict[str, Any] | None,
    package_result: dict[str, Any] | None,
) -> list[str]:
    if not metric_result or not package_result:
        return []
    errors: list[str] = []
    metric_company_dir = str(metric_result.get("company_dir") or "").strip()
    package_company_dir = str(package_result.get("company_dir") or "").strip()
    if metric_company_dir and package_company_dir and metric_company_dir != package_company_dir:
        errors.append("metric_package_company_mismatch")
    return errors


def _identity_evidence(
    metric_result: dict[str, Any] | None,
    package_result: dict[str, Any] | None,
    *,
    metric_source: str,
    market: str,
) -> dict[str, Any]:
    metric_identity = {field: (metric_result or {}).get(field) for field in IDENTITY_FIELDS}
    package_identity = {field: (package_result or {}).get(field) for field in IDENTITY_FIELDS}
    compared_fields: list[str] = []
    unavailable_fields: list[str] = []
    errors: list[str] = []
    required_package_fields = ("company_id", "report_id") if market == "CN" else IDENTITY_FIELDS
    for field in required_package_fields:
        if not str(package_identity.get(field) or "").strip():
            errors.append(f"package_identity_{field}_missing")
    if metric_source == "structured":
        for field in IDENTITY_FIELDS:
            metric_value = str(metric_identity.get(field) or "").strip()
            package_value = str(package_identity.get(field) or "").strip()
            if not metric_value and not package_value:
                unavailable_fields.append(field)
                continue
            compared_fields.append(field)
            if metric_value != package_value:
                errors.append(f"metric_package_{field}_mismatch")
    else:
        metric_report_id = str(metric_identity.get("report_id") or "").strip()
        package_report_id = str(package_identity.get("report_id") or "").strip()
        compared_fields.append("report_id")
        if not metric_report_id or metric_report_id != package_report_id:
            errors.append("metric_package_report_id_mismatch")
        unavailable_fields.extend(
            field for field in ("company_id", "filing_id", "parse_run_id") if not metric_identity.get(field)
        )
    return {
        "passed": not errors,
        "metric_identity": metric_identity,
        "package_identity": package_identity,
        "compared_fields": compared_fields,
        "unavailable_metric_fields": unavailable_fields,
        "errors": errors,
    }


def _task_ids(rows: list[dict[str, Any]]) -> list[str]:
    return sorted({str(row.get("task_id")) for row in rows if row.get("task_id") not in (None, "")})


def _artifact_path(value: Any) -> str:
    if value in (None, ""):
        return ""
    path = Path(str(value))
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def _package_path(result: dict[str, Any] | None) -> str:
    company_dir = (result or {}).get("company_dir")
    report_id = str((result or {}).get("report_id") or "").strip()
    if not company_dir or not report_id:
        return ""
    return _artifact_path(Path(str(company_dir)) / "reports" / report_id)


def evaluate_case(runtime: Any, market: str, case: dict[str, str]) -> dict[str, Any]:
    metric_question = case["metric_question"]
    package_question = case["package_question"]
    structured_metric = runtime._three_statement_core_result(metric_question)
    structured_metric_errors = _structured_metric_errors(structured_metric, market)
    fulltext_metric = None
    fulltext_metric_errors: list[str] = []
    metric_source = "structured"
    if structured_metric_errors:
        fulltext_metric = runtime._wiki_fulltext_fallback_result(
            metric_question,
            _research_context(structured_metric),
        )
        fulltext_metric_errors = _fulltext_metric_errors(fulltext_metric)
        metric_source = "fulltext" if not fulltext_metric_errors else "none"
    metric_evidence_pass = not structured_metric_errors or not fulltext_metric_errors

    package_context = _research_context(structured_metric)
    package = runtime._three_statement_core_result(package_question, package_context)
    package_errors = _package_errors(package, market)
    metric_result = structured_metric if metric_source == "structured" else fulltext_metric
    identity_evidence = _identity_evidence(
        metric_result,
        package,
        metric_source=metric_source,
        market=market,
    )
    package_errors.extend(identity_evidence["errors"])
    package_errors.extend(_scope_errors(metric_result, package))
    three_statement_package_pass = not package_errors
    errors = [
        *([] if metric_evidence_pass else structured_metric_errors + fulltext_metric_errors),
        *package_errors,
    ]
    metric_rows = _rows(metric_result)
    package_rows = _rows(package)
    package_statement_types = {str(row.get("statement_type") or "") for row in package_rows}
    validation_status = str((package or {}).get("validation", {}).get("status") or "not_available").casefold()
    return {
        "market": market,
        "question": metric_question,
        "metric_question": metric_question,
        "package_question": package_question,
        "metric_evidence_pass": metric_evidence_pass,
        "metric_evidence_source": metric_source,
        "structured_metric_errors": structured_metric_errors,
        "fulltext_metric_errors": fulltext_metric_errors,
        "three_statement_package_pass": three_statement_package_pass,
        "package_identity_pass": identity_evidence["passed"],
        "metric_identity": identity_evidence["metric_identity"],
        "package_identity": identity_evidence["package_identity"],
        "identity_compared_fields": identity_evidence["compared_fields"],
        "identity_unavailable_metric_fields": identity_evidence["unavailable_metric_fields"],
        "passed": not errors,
        "company_id": (package or structured_metric or {}).get("company_id"),
        "report_id": (package or structured_metric or {}).get("report_id"),
        "filing_id": (package or structured_metric or {}).get("filing_id"),
        "parse_run_id": (package or structured_metric or {}).get("parse_run_id"),
        "row_count": len(metric_rows),
        "statement_types": sorted({str(row.get("statement_type") or "") for row in metric_rows}),
        "metric_row_count": len(metric_rows),
        "metric_statement_types": sorted({str(row.get("statement_type") or "") for row in metric_rows}),
        "metric_evidence_coverage": _coverage(metric_rows),
        "package_row_count": len(package_rows),
        "package_statement_types": sorted(package_statement_types),
        "package_evidence_coverage": _coverage(package_rows),
        "package_path": _package_path(package),
        "metrics_file": _artifact_path((package or {}).get("metrics_file")),
        "metric_task_ids": _task_ids(metric_rows),
        "package_task_ids": _task_ids(package_rows),
        "validation_status": validation_status,
        "evidence_coverage": _coverage(metric_rows),
        "errors": errors,
    }


def main() -> int:
    args = parse_args()
    os.environ["SIQ_WIKI_ROOT"] = str(args.wiki_root.expanduser().resolve())
    if str(API_ROOT) not in sys.path:
        sys.path.insert(0, str(API_ROOT))
    from services import agent_chat_runtime as runtime

    results = [evaluate_case(runtime, market, case) for market, case in DEFAULT_CASES.items()]
    report = {
        "schema_version": "siq_live_market_qa_smoke_v2",
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        # The root is an execution detail; persisting it would leak a host-local path
        # into release evidence and make otherwise portable artifacts fail closed.
        "wiki_root": "<redacted>",
        "passed": all(result["passed"] for result in results),
        "summary": {
            "markets": len(results),
            "passed_markets": sum(1 for result in results if result["passed"]),
            "metric_evidence_passed_markets": sum(1 for result in results if result["metric_evidence_pass"]),
            "three_statement_package_passed_markets": sum(
                1 for result in results if result["three_statement_package_pass"]
            ),
            "package_identity_passed_markets": sum(
                1 for result in results if result["package_identity_pass"]
            ),
            "metric_total_rows": sum(int(result.get("metric_row_count") or 0) for result in results),
            "package_total_rows": sum(int(result.get("package_row_count") or 0) for result in results),
            "total_rows": sum(int(result.get("metric_row_count") or 0) for result in results),
        },
        "results": results,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"live_market_qa_smoke passed={report['passed']} "
            f"markets={report['summary']['passed_markets']}/{report['summary']['markets']} "
            f"metric={report['summary']['metric_evidence_passed_markets']}/{report['summary']['markets']} "
            f"packages={report['summary']['three_statement_package_passed_markets']}/{report['summary']['markets']}"
        )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
