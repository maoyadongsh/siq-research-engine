#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

IMPORTS_DIR = Path(__file__).resolve().parent
if str(IMPORTS_DIR) not in sys.path:
    sys.path.insert(0, str(IMPORTS_DIR))

from market_document_full_rules.base import MarketDocumentFullContext
from market_document_full_rules.common import as_decimal, read_json, sha256_file
from market_document_full_rules.registry import rule_for_market
from market_ingestion_contract import normalize_market
from market_document_full_writer import MARKET_CONFIG, MarketDocumentFullWriter, connect, database_url, run_market_ddl


PDF_MARKETS = {"HK", "JP", "KR", "EU"}
IMPORT_MARKETS = {*PDF_MARKETS, "US"}
LEGACY_MARKETS = {"CN", "A", "ASHARE", "A_SHARE"}
KNOWN_MARKETS = {*IMPORT_MARKETS, *LEGACY_MARKETS}
FINANCIAL_STATEMENT_TYPES = {
    "balance_sheet",
    "statement_of_financial_position",
    "income_statement",
    "profit_or_loss",
    "cash_flow_statement",
    "cash_flows",
    "key_metrics",
}


class MarketMismatchError(ValueError):
    pass


def infer_market(document_full: dict[str, Any], path: Path) -> str:
    for source in (
        document_full.get("metadata") if isinstance(document_full.get("metadata"), dict) else {},
        document_full.get("financial_data") if isinstance(document_full.get("financial_data"), dict) else {},
        document_full.get("filing") if isinstance(document_full.get("filing"), dict) else {},
        document_full.get("task", {}).get("submit_config") if isinstance(document_full.get("task"), dict) and isinstance(document_full.get("task", {}).get("submit_config"), dict) else {},
    ):
        market = normalize_market(source.get("market"))
        if market in KNOWN_MARKETS:
            return market
    path_text = str(path).upper()
    if "/US-SEC/" in path_text or "/US/" in path_text:
        return "US"
    for market in (*PDF_MARKETS, "CN"):
        if f"/{market}/" in path_text or f"_{market}_" in path_text:
            return market
    raise SystemExit("Unable to infer market; pass --market")


def selected_market(document_full: dict[str, Any], path: Path, requested_market: str | None) -> str:
    requested = normalize_market(requested_market) if requested_market else None
    try:
        inferred = infer_market(document_full, path)
    except SystemExit:
        if requested:
            inferred = requested
        else:
            raise
    if requested and requested != inferred:
        raise MarketMismatchError(f"Requested market {requested} does not match document_full market {inferred}: {path}")
    if inferred in LEGACY_MARKETS:
        raise SystemExit("CN/A-share document_full imports must use db/imports/import_document_full_to_postgres.py")
    if inferred not in IMPORT_MARKETS:
        raise SystemExit(f"Unsupported market: {inferred}")
    return inferred


def import_document_full(
    document_full_path: Path,
    *,
    market: str | None = None,
    database_url_value: str | None = None,
    schema: str | None = None,
    run_ddl_flag: bool = False,
    allow_empty: bool = False,
) -> str:
    document_full_path = document_full_path.expanduser().resolve()
    if document_full_path.is_dir():
        candidate = document_full_path / "document_full.json"
        if candidate.is_file():
            document_full_path = candidate
        else:
            raise SystemExit(
                f"{document_full_path} is a directory; pass a document_full.json file or use --results-root/--recursive"
            )
    document_full = read_json(document_full_path)
    market_code = selected_market(document_full, document_full_path, market)
    digest = sha256_file(document_full_path)
    context = MarketDocumentFullContext(
        market=market_code,
        document_full_path=document_full_path,
        document_full_sha256=digest,
        source_root=document_full_path.parent,
    )
    rows = rule_for_market(market_code).build_rows(document_full, context)
    if not allow_empty:
        numeric_fact_count = sum(
            1
            for item in [*rows.statement_items, *rows.key_metrics]
            if as_decimal(item.get("value")) is not None
            and (
                str(item.get("statement_type") or "") in FINANCIAL_STATEMENT_TYPES
                or bool(item.get("canonical_name"))
            )
        )
        if numeric_fact_count < 1:
            raise SystemExit(
                f"{market_code} document_full import produced zero numeric financial facts; "
                "pass --allow-empty only for explicit metadata-only backfills."
            )
        if not rows.chunks:
            raise SystemExit(f"{market_code} document_full import produced zero retrieval chunks")
        if not rows.citations:
            raise SystemExit(
                f"{market_code} document_full import produced zero evidence citations; "
                "core financial facts must remain traceable to document_full evidence."
            )
    with connect(database_url(database_url_value, market=market_code)) as conn:
        if run_ddl_flag:
            run_market_ddl(conn, market_code)
            conn.commit()
        writer = MarketDocumentFullWriter(conn, market=market_code, schema=schema or str(MARKET_CONFIG[market_code]["schema"]))
        parse_run_id = writer.import_rows(rows)
        conn.commit()
    return parse_run_id


def iter_document_full_paths(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return sorted(root.rglob("document_full.json"))


def main(default_market: str | None = None) -> None:
    parser = argparse.ArgumentParser(description="Import market document_full.json into the market PostgreSQL schema.")
    parser.add_argument("document_full", type=Path, nargs="?", help="Path to document_full.json or a results root")
    parser.add_argument("--document-full", dest="document_full_opt", type=Path)
    parser.add_argument("--results-root", type=Path)
    parser.add_argument("--market", choices=sorted({*IMPORT_MARKETS, "US_SEC", "US-SEC", "US SEC"}), default=default_market)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--schema", default=None)
    parser.add_argument("--ddl", "--run-ddl", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Compatibility flag; document_full imports are already idempotent and replace rows for the same parse_run_id.",
    )
    parser.add_argument("--allow-empty", action="store_true", help="Allow metadata-only imports that produce zero financial facts.")
    parser.add_argument("--recursive", action="store_true")
    args = parser.parse_args()

    input_path = args.document_full_opt or args.results_root or args.document_full
    if input_path is None:
        raise SystemExit("document_full path or --results-root is required")
    paths = iter_document_full_paths(input_path.expanduser()) if args.recursive or input_path.is_dir() else [input_path.expanduser()]
    last_parse_run_id = ""
    skipped = 0
    for path in paths:
        try:
            last_parse_run_id = import_document_full(
                path,
                market=args.market,
                database_url_value=args.database_url,
                schema=args.schema,
                run_ddl_flag=args.ddl,
                allow_empty=args.allow_empty,
            )
        except MarketMismatchError as exc:
            if args.recursive or input_path.is_dir():
                skipped += 1
                print(f"skip: {exc}", file=sys.stderr)
                continue
            raise SystemExit(str(exc)) from exc
        print(last_parse_run_id)
    if skipped:
        print(f"skipped {skipped} document_full.json file(s) outside requested market", file=sys.stderr)
    if not last_parse_run_id:
        raise SystemExit("No document_full.json files found")


if __name__ == "__main__":
    main()
