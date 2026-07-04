#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

IMPORTS_DIR = Path(__file__).resolve().parent
if str(IMPORTS_DIR) not in sys.path:
    sys.path.insert(0, str(IMPORTS_DIR))

from import_market_xbrl_package_to_postgres import database_url, import_package, psycopg, run_ddl

REPO_ROOT = Path(__file__).resolve().parents[2]
DDL_PATH = REPO_ROOT / "db" / "ddl" / "030_create_edinet_jp_schema.sql"


def validate_schema(schema: str) -> None:
    if schema != "edinet_jp":
        raise SystemExit("JP imports must target schema edinet_jp")


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a JP EDINET evidence package into PostgreSQL siq_jp/edinet_jp.")
    parser.add_argument("package", type=Path, nargs="?")
    parser.add_argument("--package", dest="package_opt", type=Path)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--schema", default=os.environ.get("SIQ_JP_SCHEMA", "edinet_jp"))
    parser.add_argument("--ddl", "--run-ddl", action="store_true")
    parser.add_argument("--ddl-only", action="store_true")
    args = parser.parse_args()
    package_dir = args.package_opt or args.package
    validate_schema(args.schema)
    with psycopg.connect(database_url(args.database_url, market="JP", default_database="siq_jp"), autocommit=False) as conn:
        if args.ddl or args.ddl_only:
            run_ddl(conn, DDL_PATH)
            conn.commit()
        if args.ddl_only:
            print("DDL applied")
            return
        if not package_dir:
            raise SystemExit("package path is required")
        parse_run_id = import_package(conn, package_dir.resolve(), schema=args.schema, market="JP")
        conn.commit()
    print(parse_run_id)


if __name__ == "__main__":
    main()
