#!/usr/bin/env python3
"""Validate the live Wiki-to-agent core fact path across supported markets."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "apps" / "api"
DEFAULT_WIKI_ROOT = REPO_ROOT / "data" / "wiki"
DEFAULT_CASES = {
    "CN": "分析A股美的集团营业收入",
    "HK": "HK TENCENT revenue",
    "US": "US Apple Inc revenue",
    "JP": "日本 Toyota Motor revenue",
    "KR": "韩国 Samsung Electronics total assets",
    "EU": "欧洲 SAP SE total assets",
}
EXPECTED_STATEMENTS = {"income_statement", "cash_flow_statement", "balance_sheet"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wiki-root", type=Path, default=DEFAULT_WIKI_ROOT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true", help="Print the full JSON report.")
    return parser.parse_args()


def _has_locator(row: dict[str, Any]) -> bool:
    pdf_locator = bool(row.get("task_id") and (row.get("pdf_page") or row.get("table_index") not in (None, "")))
    external_locator = bool(row.get("source_url") and (row.get("source_anchor") or row.get("xbrl_tag")))
    return pdf_locator or external_locator


def evaluate_case(runtime: Any, market: str, question: str) -> dict[str, Any]:
    result = runtime._three_statement_core_result(question)
    if not result:
        return {"market": market, "question": question, "passed": False, "errors": ["core_fact_miss"]}
    rows = [row for row in (result.get("rows") or []) if isinstance(row, dict)]
    statement_types = {str(row.get("statement_type") or "") for row in rows}
    located_rows = sum(1 for row in rows if _has_locator(row))
    evidence_coverage = located_rows / len(rows) if rows else 0.0
    validation_status = str((result.get("validation") or {}).get("status") or "not_available").casefold()
    errors: list[str] = []
    if not rows:
        errors.append("no_core_rows")
    if statement_types != EXPECTED_STATEMENTS:
        errors.append("incomplete_three_statement_coverage")
    if validation_status in {"fail", "not_available"}:
        errors.append(f"validation_{validation_status}")
    if evidence_coverage < 1.0:
        errors.append("incomplete_structured_evidence")
    if not result.get("company_id") or not result.get("report_id"):
        errors.append("incomplete_company_report_identity")
    if market != "CN" and (not result.get("filing_id") or not result.get("parse_run_id")):
        errors.append("incomplete_non_cn_research_identity")
    return {
        "market": market,
        "question": question,
        "passed": not errors,
        "company_id": result.get("company_id"),
        "report_id": result.get("report_id"),
        "filing_id": result.get("filing_id"),
        "parse_run_id": result.get("parse_run_id"),
        "row_count": len(rows),
        "statement_types": sorted(statement_types),
        "validation_status": validation_status,
        "evidence_coverage": evidence_coverage,
        "errors": errors,
    }


def main() -> int:
    args = parse_args()
    os.environ["SIQ_WIKI_ROOT"] = str(args.wiki_root.expanduser().resolve())
    if str(API_ROOT) not in sys.path:
        sys.path.insert(0, str(API_ROOT))
    from services import agent_chat_runtime as runtime

    results = [evaluate_case(runtime, market, question) for market, question in DEFAULT_CASES.items()]
    report = {
        "schema_version": "siq_live_market_qa_smoke_v1",
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "wiki_root": str(args.wiki_root.expanduser().resolve()),
        "passed": all(result["passed"] for result in results),
        "summary": {
            "markets": len(results),
            "passed_markets": sum(1 for result in results if result["passed"]),
            "total_rows": sum(int(result.get("row_count") or 0) for result in results),
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
            f"rows={report['summary']['total_rows']}"
        )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
