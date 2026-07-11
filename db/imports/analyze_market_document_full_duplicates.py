#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path
from typing import Any

IMPORTS_DIR = Path(__file__).resolve().parent
if str(IMPORTS_DIR) not in sys.path:
    sys.path.insert(0, str(IMPORTS_DIR))

from market_document_full_writer import connect, database_url
from market_ingestion_contract import normalize_market, quote_ident, target_for_market


DUPLICATE_ROW_COLUMNS = (
    "parse_run_id",
    "filing_id",
    "company_id",
    "ticker",
    "report_type",
    "fiscal_year",
    "status",
    "started_at",
    "completed_at",
    "wiki_package_path",
    "package_path",
    "document_full_sha256",
)


def _is_a_share_market(market: str) -> bool:
    key = str(market or "").strip().upper().replace("-", "_").replace(" ", "_")
    compact = key.replace("_", "")
    return key in {"CN", "A", "A_SHARE"} or compact in {"ASHARE", "CNASHARE", "AGU", "AGUPIAO"}


def _row_to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return {key: row.get(key) for key in DUPLICATE_ROW_COLUMNS}
    return dict(zip(DUPLICATE_ROW_COLUMNS, row))


def _cleanup_dry_run_argv(market: str, parse_run_ids: list[str]) -> list[str]:
    argv = ["python3", "db/imports/cleanup_market_document_full_parse_runs.py", "--market", market]
    for parse_run_id in parse_run_ids:
        argv.extend(["--parse-run-id", parse_run_id])
    return argv


def _cleanup_dry_run_command(market: str, parse_run_ids: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in _cleanup_dry_run_argv(market, parse_run_ids))


def _duplicate_reason(rows: list[dict[str, Any]]) -> str:
    hashes = {str(row.get("document_full_sha256") or "") for row in rows if row.get("document_full_sha256")}
    if len(hashes) > 1:
        return "same_filing_multiple_parse_runs_different_document_hash"
    if len(hashes) == 1:
        return "same_filing_multiple_parse_runs_same_document_hash"
    return "same_filing_multiple_parse_runs_unknown_document_hash"


def analyze_duplicate_parse_runs(
    market: str,
    *,
    company_id: str | None = None,
    filing_id: str | None = None,
    database_url_value: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    if _is_a_share_market(market):
        raise SystemExit("Refusing to analyze A-share/CN data; use A-share maintenance tools for siq.pdf2md.")
    market_code = normalize_market(market)
    if _is_a_share_market(market_code):
        raise SystemExit("Refusing to analyze A-share/CN data; use A-share maintenance tools for siq.pdf2md.")
    target = target_for_market(market_code)
    schema_sql = quote_ident(target.schema)
    where_parts: list[str] = []
    params: list[Any] = []
    if company_id:
        where_parts.append("f.company_id = %s")
        params.append(company_id)
    if filing_id:
        where_parts.append("pr.filing_id = %s")
        params.append(filing_id)
    where_sql = "where " + " and ".join(where_parts) if where_parts else ""

    with connect(database_url(database_url_value, market=market_code)) as conn:
        rows = conn.execute(
            f"""
            select
                pr.parse_run_id,
                pr.filing_id,
                f.company_id,
                f.ticker,
                f.report_type,
                f.fiscal_year,
                pr.status,
                pr.started_at,
                pr.completed_at,
                pr.wiki_package_path,
                pr.package_path,
                coalesce(
                    pr.artifact_hashes->>'document_full.json',
                    pr.artifact_hashes->>'document_full',
                    pr.raw->>'document_full_sha256',
                    pr.raw->'artifact_hashes'->>'document_full.json'
                ) as document_full_sha256
            from {schema_sql}.parse_runs pr
            join {schema_sql}.filings f on f.filing_id = pr.filing_id
            {where_sql}
            order by
                pr.filing_id,
                coalesce(pr.completed_at, pr.started_at) desc nulls last,
                pr.parse_run_id desc
            """,
            tuple(params),
        ).fetchall()

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        item = _row_to_dict(row)
        grouped.setdefault(str(item.get("filing_id") or ""), []).append(item)

    duplicate_groups: list[dict[str, Any]] = []
    for current_filing_id, group_rows in grouped.items():
        if not current_filing_id or len(group_rows) < 2:
            continue
        latest = group_rows[0]
        obsolete_rows = group_rows[1:]
        obsolete_ids = [str(row["parse_run_id"]) for row in obsolete_rows if row.get("parse_run_id")]
        duplicate_groups.append(
            {
                "market": market_code,
                "database": target.database,
                "schema": target.schema,
                "filing_id": current_filing_id,
                "company_id": latest.get("company_id"),
                "ticker": latest.get("ticker"),
                "report_type": latest.get("report_type"),
                "fiscal_year": latest.get("fiscal_year"),
                "parse_run_count": len(group_rows),
                "latest_parse_run_id": latest.get("parse_run_id"),
                "candidate_obsolete_parse_run_ids": obsolete_ids,
                "reason": _duplicate_reason(group_rows),
                "runs": group_rows,
                "cleanup_dry_run_argv": _cleanup_dry_run_argv(market_code, obsolete_ids),
                "cleanup_dry_run_command": _cleanup_dry_run_command(market_code, obsolete_ids),
            }
        )

    duplicate_groups.sort(key=lambda item: (-int(item["parse_run_count"]), str(item["filing_id"])))
    if limit >= 0:
        duplicate_groups = duplicate_groups[:limit]
    return {
        "market": market_code,
        "database": target.database,
        "schema": target.schema,
        "selectors": {"company_id": company_id, "filing_id": filing_id},
        "duplicate_group_count": len(duplicate_groups),
        "candidate_obsolete_parse_run_count": sum(
            len(group["candidate_obsolete_parse_run_ids"]) for group in duplicate_groups
        ),
        "duplicate_groups": duplicate_groups,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze duplicate non-A-share document_full parse runs.")
    parser.add_argument("--market", required=True, help="One of HK/JP/KR/EU/US. CN/A-share is refused.")
    parser.add_argument("--company-id", help="Restrict analysis to one company_id.")
    parser.add_argument("--filing-id", help="Restrict analysis to one filing_id.")
    parser.add_argument("--database-url", default=None, help="Connection URL; database path is rewritten to the market DB.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum duplicate filing groups to print; -1 for all.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = analyze_duplicate_parse_runs(
        args.market,
        company_id=args.company_id,
        filing_id=args.filing_id,
        database_url_value=args.database_url,
        limit=args.limit,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(
            "DUPLICATE RUN ANALYSIS "
            f"{result['market']} {result['database']}.{result['schema']} "
            f"groups={result['duplicate_group_count']} obsolete_runs={result['candidate_obsolete_parse_run_count']}"
        )
        for group in result["duplicate_groups"]:
            print(
                f"- {group['filing_id']}: runs={group['parse_run_count']} "
                f"latest={group['latest_parse_run_id']} obsolete={','.join(group['candidate_obsolete_parse_run_ids'])}"
            )
            print(f"  reason={group['reason']}")
            if group["candidate_obsolete_parse_run_ids"]:
                print(f"  dry-run: {group['cleanup_dry_run_command']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
