#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

IMPORTS_DIR = Path(__file__).resolve().parent
if str(IMPORTS_DIR) not in sys.path:
    sys.path.insert(0, str(IMPORTS_DIR))

from market_document_full_writer import MarketDocumentFullWriter, connect, database_url
from market_ingestion_contract import quote_ident
from market_ingestion_contract import normalize_market, target_for_market


def _parse_older_than(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(f"--older-than must be an ISO date/datetime; got {value!r}") from exc


def _selector_provided(
    parse_run_ids: list[str],
    *,
    company_id: str | None,
    filing_id: str | None,
    older_than: datetime | None,
) -> bool:
    return bool(parse_run_ids or company_id or filing_id or older_than)


def _is_a_share_market(market: str) -> bool:
    key = str(market or "").strip().upper().replace("-", "_").replace(" ", "_")
    compact = key.replace("_", "")
    return key in {"CN", "A", "A_SHARE"} or compact in {"ASHARE", "CNASHARE", "AGU", "AGUPIAO"}


def _resolve_parse_run_ids(
    conn: Any,
    schema: str,
    parse_run_ids: list[str],
    *,
    company_id: str | None = None,
    filing_id: str | None = None,
    older_than: datetime | None = None,
) -> list[str]:
    schema_sql = quote_ident(schema)
    where_parts: list[str] = []
    params: list[Any] = []

    if parse_run_ids:
        where_parts.append("pr.parse_run_id = any(%s)")
        params.append(parse_run_ids)
    if company_id:
        where_parts.append("f.company_id = %s")
        params.append(company_id)
    if filing_id:
        where_parts.append("pr.filing_id = %s")
        params.append(filing_id)
    if older_than:
        where_parts.append("coalesce(pr.completed_at, pr.started_at) < %s")
        params.append(older_than)

    if not where_parts:
        return []

    rows = conn.execute(
        f"""
        select pr.parse_run_id
        from {schema_sql}.parse_runs pr
        join {schema_sql}.filings f on f.filing_id = pr.filing_id
        where {' and '.join(where_parts)}
        order by coalesce(pr.completed_at, pr.started_at) asc nulls first, pr.parse_run_id asc
        """,
        tuple(params),
    ).fetchall()
    return [str(row[0]) for row in rows if row and row[0]]


def _relation_exists(conn: Any, schema: str, relation: str) -> bool:
    row = conn.execute(
        """
        select 1
        from information_schema.tables
        where table_schema = %s
          and table_name = %s
          and table_type in ('BASE TABLE', 'VIEW')
        union all
        select 1
        from information_schema.views
        where table_schema = %s
          and table_name = %s
        limit 1
        """,
        (schema, relation, schema, relation),
    ).fetchone()
    return bool(row)


def _count_parse_run_rows(conn: Any, writer: MarketDocumentFullWriter, parse_run_ids: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in writer._parse_run_child_tables():
        if table == "parse_runs":
            continue
        row = conn.execute(
            f"select count(*) from {writer.schema_sql}.{quote_ident(table)} where parse_run_id = any(%s)",
            (parse_run_ids,),
        ).fetchone()
        counts[table] = int(row[0] if row else 0)
    row = conn.execute(
        f"select count(*) from {writer.schema_sql}.parse_runs where parse_run_id = any(%s)",
        (parse_run_ids,),
    ).fetchone()
    counts["parse_runs"] = int(row[0] if row else 0)
    return counts


def _post_cleanup_probe(conn: Any, writer: MarketDocumentFullWriter, parse_run_ids: list[str]) -> dict[str, Any]:
    remaining_counts = _count_parse_run_rows(conn, writer, parse_run_ids) if parse_run_ids else {"parse_runs": 0}
    probe: dict[str, Any] = {
        "parse_run_ids": parse_run_ids,
        "remaining_counts": remaining_counts,
        "cleaned": all(count == 0 for count in remaining_counts.values()),
    }
    if parse_run_ids and _relation_exists(conn, writer.schema, "v_agent_financial_facts"):
        row = conn.execute(
            f"select count(*) from {writer.schema_sql}.v_agent_financial_facts where parse_run_id = any(%s)",
            (parse_run_ids,),
        ).fetchone()
        agent_view_rows = int(row[0] if row else 0)
        probe["agent_view_rows"] = agent_view_rows
        probe["agent_view_cleaned"] = agent_view_rows == 0
        probe["cleaned"] = bool(probe["cleaned"] and agent_view_rows == 0)
    return probe


def cleanup_parse_runs(
    market: str,
    parse_run_ids: list[str],
    *,
    company_id: str | None = None,
    filing_id: str | None = None,
    older_than: str | None = None,
    allow_market_wide_older_than: bool = False,
    database_url_value: str | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    if _is_a_share_market(market):
        raise SystemExit("Refusing to clean A-share/CN data; use A-share maintenance tools for siq.pdf2md.")
    market_code = normalize_market(market)
    if _is_a_share_market(market_code):
        raise SystemExit("Refusing to clean A-share/CN data; use A-share maintenance tools for siq.pdf2md.")
    target = target_for_market(market_code)
    parse_run_ids = list(dict.fromkeys(item.strip() for item in parse_run_ids if item and item.strip()))
    older_than_value = _parse_older_than(older_than)
    company_id = company_id.strip() if company_id and company_id.strip() else None
    filing_id = filing_id.strip() if filing_id and filing_id.strip() else None
    if not _selector_provided(parse_run_ids, company_id=company_id, filing_id=filing_id, older_than=older_than_value):
        raise SystemExit("At least one selector is required: --parse-run-id, --company-id, --filing-id, or --older-than.")
    if older_than_value and not (parse_run_ids or company_id or filing_id or allow_market_wide_older_than):
        raise SystemExit(
            "Refusing market-wide --older-than cleanup without another selector; "
            "add --company-id/--filing-id/--parse-run-id or pass --allow-market-wide-older-than."
        )

    with connect(database_url(database_url_value, market=market_code)) as conn:
        writer = MarketDocumentFullWriter(conn, market=market_code, schema=target.schema)
        matched_parse_run_ids = _resolve_parse_run_ids(
            conn,
            writer.schema,
            parse_run_ids,
            company_id=company_id,
            filing_id=filing_id,
            older_than=older_than_value,
        )
        counts = _count_parse_run_rows(conn, writer, matched_parse_run_ids) if matched_parse_run_ids else {"parse_runs": 0}
        post_cleanup_probe: dict[str, Any] | None = None

        if apply:
            if matched_parse_run_ids:
                with conn.transaction():
                    for parse_run_id in matched_parse_run_ids:
                        writer._delete_run_rows(parse_run_id)
                    conn.execute(
                        f"delete from {writer.schema_sql}.parse_runs where parse_run_id = any(%s)",
                        (matched_parse_run_ids,),
                    )
                conn.commit()
            post_cleanup_probe = _post_cleanup_probe(conn, writer, matched_parse_run_ids)

    return {
        "market": market_code,
        "database": target.database,
        "schema": target.schema,
        "selectors": {
            "parse_run_ids": parse_run_ids,
            "company_id": company_id,
            "filing_id": filing_id,
            "older_than": older_than,
            "allow_market_wide_older_than": allow_market_wide_older_than,
        },
        "parse_run_ids": matched_parse_run_ids,
        "apply": apply,
        "counts": counts,
        "post_cleanup_probe": post_cleanup_probe,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Clean duplicate non-A-share market document_full parse runs by parse_run_id."
    )
    parser.add_argument("--market", required=True, help="One of HK/JP/KR/EU/US. CN/A-share is refused.")
    parser.add_argument("--parse-run-id", action="append", default=[], help="Obsolete parse_run_id to remove.")
    parser.add_argument("--parse-run-id-file", type=Path, help="Optional newline-delimited parse_run_id file.")
    parser.add_argument("--company-id", help="Restrict cleanup to parse runs for this company_id.")
    parser.add_argument("--filing-id", help="Restrict cleanup to parse runs for this filing_id.")
    parser.add_argument("--older-than", help="Restrict cleanup to parse runs completed/started before this ISO date/datetime.")
    parser.add_argument(
        "--allow-market-wide-older-than",
        action="store_true",
        help="Allow --older-than without company_id/filing_id/parse_run_id. Use only after reviewing dry-run counts.",
    )
    parser.add_argument("--database-url", default=None, help="Connection URL; database path is rewritten to the market DB.")
    parser.add_argument("--apply", action="store_true", help="Actually delete rows. Default is dry-run.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    parse_run_ids = list(args.parse_run_id)
    if args.parse_run_id_file:
        parse_run_ids.extend(
            line.strip()
            for line in args.parse_run_id_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
    result = cleanup_parse_runs(
        args.market,
        parse_run_ids,
        company_id=args.company_id,
        filing_id=args.filing_id,
        older_than=args.older_than,
        allow_market_wide_older_than=args.allow_market_wide_older_than,
        database_url_value=args.database_url,
        apply=args.apply,
    )
    action = "DELETE" if result["apply"] else "DRY-RUN"
    print(f"{action} {result['market']} {result['database']}.{result['schema']}")
    selectors = {key: value for key, value in result["selectors"].items() if value}
    print(f"selectors={selectors}")
    print(f"matched_parse_run_ids={', '.join(result['parse_run_ids']) or '<none>'}")
    for table, count in sorted(result["counts"].items()):
        print(f"{table}: {count}")
    if result.get("post_cleanup_probe") is not None:
        print(f"post_cleanup_probe={result['post_cleanup_probe']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
