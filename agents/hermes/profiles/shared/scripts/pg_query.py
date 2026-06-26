#!/usr/bin/env python3
"""Read-only PostgreSQL query helper for SIQ Hermes agents."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from decimal import Decimal
from pathlib import Path

try:
    import sqlparse
except Exception:  # pragma: no cover - optional runtime dependency
    sqlparse = None

try:
    import psycopg2
    import psycopg2.extras
except Exception:  # pragma: no cover - optional runtime dependency
    psycopg2 = None

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:  # pragma: no cover - optional runtime dependency
    psycopg = None
    dict_row = None


DEFAULTS = {
    "host": "127.0.0.1",
    "port": "15432",
    "dbname": "siq",
    "user": "postgres",
    "schema": "pdf2md",
}

BLOCKED_KEYWORDS = {
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "truncate",
    "grant",
    "revoke",
    "copy",
    "vacuum",
    "call",
    "merge",
    "execute",
    "prepare",
}
ALLOWED_STATEMENTS = {"select", "with", "show"}
SQL_LINE_COMMENT_RE = re.compile(r"--[^\n]*(?=\n|$)")
SQL_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
SQL_SINGLE_QUOTED_RE = re.compile(r"'(?:''|[^'])*'")
SQL_DOUBLE_QUOTED_RE = re.compile(r'"(?:""|[^"])*"')
SQL_DOLLAR_QUOTED_RE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$.*?\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$.*?\$\$", re.DOTALL)


def json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def strip_sql_comments(sql: str) -> str:
    no_block = SQL_BLOCK_COMMENT_RE.sub(" ", sql)
    return SQL_LINE_COMMENT_RE.sub(" ", no_block)


def strip_sql_comments_and_literals(sql: str) -> str:
    no_comments = strip_sql_comments(sql)
    no_dollar_strings = SQL_DOLLAR_QUOTED_RE.sub(" ", no_comments)
    no_single_strings = SQL_SINGLE_QUOTED_RE.sub(" ", no_dollar_strings)
    return SQL_DOUBLE_QUOTED_RE.sub(" ", no_single_strings)


def split_sql_statements(sql: str) -> list[str]:
    if sqlparse:
        return [item.strip().rstrip(";").strip() for item in sqlparse.split(sql) if item.strip()]

    statements: list[str] = []
    chunk: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(sql):
        char = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""
        if quote == "'":
            chunk.append(char)
            if char == "'" and nxt == "'":
                chunk.append(nxt)
                i += 2
                continue
            if char == "'":
                quote = None
            i += 1
            continue
        if quote == '"':
            chunk.append(char)
            if char == '"' and nxt == '"':
                chunk.append(nxt)
                i += 2
                continue
            if char == '"':
                quote = None
            i += 1
            continue
        if char == "-" and nxt == "-":
            end = sql.find("\n", i + 2)
            if end == -1:
                chunk.append(sql[i:])
                break
            chunk.append(sql[i:end])
            i = end
            continue
        if char == "/" and nxt == "*":
            end = sql.find("*/", i + 2)
            if end == -1:
                chunk.append(sql[i:])
                break
            chunk.append(sql[i : end + 2])
            i = end + 2
            continue
        if char in {"'", '"'}:
            quote = char
            chunk.append(char)
            i += 1
            continue
        if char == ";":
            statement = "".join(chunk).strip()
            if statement:
                statements.append(statement)
            chunk = []
            i += 1
            continue
        chunk.append(char)
        i += 1

    statement = "".join(chunk).strip()
    if statement:
        statements.append(statement)
    return statements


def first_sql_keyword(sql: str) -> str:
    if sqlparse:
        parsed = sqlparse.parse(sql)
        if parsed:
            first = parsed[0].token_first(skip_cm=True, skip_ws=True)
            if first:
                return first.normalized.lower()
    cleaned = strip_sql_comments(sql).strip()
    return cleaned.split(None, 1)[0].lower() if cleaned else ""


def has_blocked_keyword(sql: str) -> str | None:
    cleaned = strip_sql_comments_and_literals(sql).lower()
    tokens = re.findall(r"[a-z_][a-z0-9_]*", cleaned)
    for token in tokens:
        if token in BLOCKED_KEYWORDS:
            return token
    return None


def normalize_sql(sql: str) -> str:
    statements = split_sql_statements(sql)
    if len(statements) != 1:
        raise SystemExit("Only one read-only SQL statement is allowed.")

    stripped = statements[0].strip().rstrip(";")
    first = first_sql_keyword(stripped)
    if first not in ALLOWED_STATEMENTS:
        raise SystemExit("Only read-only SELECT/WITH/SHOW queries are allowed.")

    blocked = has_blocked_keyword(stripped)
    if blocked:
        raise SystemExit(f"Blocked non-read-only token: {blocked}")
    return stripped


def connect_readonly(cfg: dict[str, str]):
    if psycopg2:
        conn = psycopg2.connect(connect_timeout=5, **cfg)
        conn.set_session(readonly=True, autocommit=True)
        return "psycopg2", conn

    if psycopg:
        conn = psycopg.connect(connect_timeout=5, row_factory=dict_row, **cfg)
        conn.autocommit = True
        conn.execute("SET default_transaction_read_only = on")
        return "psycopg3", conn

    raise RuntimeError("Neither psycopg2 nor psycopg is installed.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a read-only query against SIQ PostgreSQL.")
    parser.add_argument("--sql", required=True, help="SELECT/WITH/SHOW SQL to execute")
    parser.add_argument("--profile-env", help="Optional Hermes profile .env path")
    parser.add_argument("--limit", type=int, default=50, help="Maximum returned rows")
    args = parser.parse_args()

    env_file = load_env_file(Path(args.profile_env)) if args.profile_env else {}
    password = (
        env_file.get("DB_PASSWORD")
        or env_file.get("PGPASSWORD")
        or os.environ.get("DB_PASSWORD")
        or os.environ.get("PGPASSWORD")
    )
    if not password:
        raise SystemExit(
            "DB_PASSWORD not found. Provide via --profile-env <profile>/.env or DB_PASSWORD env var."
        )
    cfg = {
        "host": env_file.get("DB_HOST") or env_file.get("PGHOST") or os.environ.get("DB_HOST") or os.environ.get("PGHOST") or DEFAULTS["host"],
        "port": env_file.get("DB_PORT") or env_file.get("PGPORT") or os.environ.get("DB_PORT") or os.environ.get("PGPORT") or DEFAULTS["port"],
        "dbname": env_file.get("DB_NAME") or env_file.get("PGDATABASE") or os.environ.get("DB_NAME") or os.environ.get("PGDATABASE") or DEFAULTS["dbname"],
        "user": env_file.get("DB_USER") or env_file.get("PGUSER") or os.environ.get("DB_USER") or os.environ.get("PGUSER") or DEFAULTS["user"],
        "password": password,
    }
    sql = normalize_sql(args.sql)
    first = first_sql_keyword(sql)
    lowered_without_literals = strip_sql_comments_and_literals(sql).lower()
    if args.limit > 0 and first in {"select", "with"} and not re.search(r"\blimit\b", lowered_without_literals):
        sql = f"{sql} LIMIT {args.limit}"

    driver, conn = connect_readonly(cfg)
    try:
        if driver == "psycopg2":
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SET search_path TO pdf2md, public")
                cur.execute(sql)
                rows = cur.fetchall() if cur.description else []
        else:
            with conn.cursor() as cur:
                cur.execute("SET search_path TO pdf2md, public")
                cur.execute(sql)
                rows = cur.fetchall() if cur.description else []
        print(json.dumps({"ok": True, "row_count": len(rows), "rows": rows}, ensure_ascii=False, default=json_default))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
