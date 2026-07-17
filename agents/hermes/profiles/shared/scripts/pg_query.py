#!/usr/bin/env python3
"""Read-only PostgreSQL query helper for SIQ Hermes agents."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen

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
DEFAULT_ROW_LIMIT = 50
MAX_ROW_LIMIT = 500
DEFAULT_TIMEOUT_MS = 5_000
MAX_TIMEOUT_MS = 30_000
DEFAULT_BROKER_PORT = 18_793
MAX_BROKER_RESPONSE_BYTES = 4 * 1024 * 1024
ALLOWED_BROKER_HOSTS = {"127.0.0.1", "::1", "host.openshell.internal", "localhost"}
ALLOWED_SCHEMAS = {
    "pdf2md",
    "pdf2md_hk",
    "sec_us",
    "edinet_jp",
    "dart_kr",
    "eu_ifrs",
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
QUALIFIED_RELATION_RE = re.compile(
    r"\b(?:from|join)\s+(?:only\s+)?(?P<schema>[a-z_][a-z0-9_]*)\s*\.\s*[a-z_][a-z0-9_]*",
    re.IGNORECASE,
)


class QueryPolicyError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


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


def connection_kwargs_from_url(url: str | None, *, database: str = "siq") -> dict[str, str]:
    if not url:
        return {}
    parsed = urlsplit(url.replace("postgresql+psycopg://", "postgresql://"))
    if parsed.scheme not in {"postgresql", "postgres"}:
        return {}
    return {
        "host": parsed.hostname or DEFAULTS["host"],
        "port": str(parsed.port or DEFAULTS["port"]),
        "dbname": database,
        "user": unquote(parsed.username or DEFAULTS["user"]),
        "password": unquote(parsed.password or ""),
    }


def project_pdf2md_config(env_file: dict[str, str]) -> dict[str, str]:
    database = (
        env_file.get("SIQ_PDF2MD_PGDATABASE")
        or env_file.get("SIQ_PGDATABASE")
        or env_file.get("PGDATABASE")
        or os.environ.get("SIQ_PDF2MD_PGDATABASE")
        or os.environ.get("SIQ_PGDATABASE")
        or os.environ.get("PGDATABASE")
        or DEFAULTS["dbname"]
    )
    explicit_url = (
        env_file.get("SIQ_PDF2MD_DATABASE_URL")
        or env_file.get("SIQ_CN_DATABASE_URL")
        or os.environ.get("SIQ_PDF2MD_DATABASE_URL")
        or os.environ.get("SIQ_CN_DATABASE_URL")
    )
    explicit = connection_kwargs_from_url(explicit_url, database=database)
    if explicit:
        return explicit
    app_url = env_file.get("SIQ_APP_DATABASE_URL") or os.environ.get("SIQ_APP_DATABASE_URL")
    app = connection_kwargs_from_url(app_url, database=database)
    return {
        "host": env_file.get("SIQ_PGHOST") or env_file.get("PGHOST") or os.environ.get("SIQ_PGHOST") or os.environ.get("PGHOST") or app.get("host") or env_file.get("DB_HOST") or os.environ.get("DB_HOST") or DEFAULTS["host"],
        "port": env_file.get("SIQ_PGPORT") or env_file.get("PGPORT") or os.environ.get("SIQ_PGPORT") or os.environ.get("PGPORT") or app.get("port") or env_file.get("DB_PORT") or os.environ.get("DB_PORT") or DEFAULTS["port"],
        "dbname": database,
        "user": env_file.get("SIQ_PGUSER") or env_file.get("PGUSER") or os.environ.get("SIQ_PGUSER") or os.environ.get("PGUSER") or app.get("user") or env_file.get("DB_USER") or os.environ.get("DB_USER") or DEFAULTS["user"],
        "password": (
            env_file.get("SIQ_PGPASSWORD")
            or env_file.get("PGPASSWORD")
            or env_file.get("POSTGRES_PASSWORD")
            or os.environ.get("SIQ_PGPASSWORD")
            or os.environ.get("PGPASSWORD")
            or os.environ.get("POSTGRES_PASSWORD")
            or app.get("password")
            or env_file.get("DB_PASSWORD")
            or os.environ.get("DB_PASSWORD")
            or ""
        ),
    }


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


def validate_schema(schema: str) -> str:
    normalized = schema.strip().lower()
    if normalized not in ALLOWED_SCHEMAS:
        raise QueryPolicyError(
            "schema_not_allowed",
            f"Schema {schema!r} is not in the SIQ market fact allowlist.",
        )
    return normalized


def validate_query_limits(*, row_limit: int, timeout_ms: int) -> tuple[int, int]:
    if row_limit < 1 or row_limit > MAX_ROW_LIMIT:
        raise QueryPolicyError(
            "row_limit_out_of_range",
            f"Row limit must be between 1 and {MAX_ROW_LIMIT}.",
        )
    if timeout_ms < 1 or timeout_ms > MAX_TIMEOUT_MS:
        raise QueryPolicyError(
            "timeout_out_of_range",
            f"Statement timeout must be between 1 and {MAX_TIMEOUT_MS} milliseconds.",
        )
    return row_limit, timeout_ms


def validate_relation_schemas(sql: str, *, schema: str) -> None:
    cleaned = strip_sql_comments_and_literals(sql)
    referenced_schemas = {
        match.group("schema").lower()
        for match in QUALIFIED_RELATION_RE.finditer(cleaned)
    }
    foreign_schemas = sorted(referenced_schemas - {schema})
    if foreign_schemas:
        raise QueryPolicyError(
            "cross_schema_query_blocked",
            "Query references relations outside the selected schema: "
            + ", ".join(foreign_schemas),
        )


def normalize_sql(sql: str, *, schema: str = DEFAULTS["schema"]) -> str:
    selected_schema = validate_schema(schema)
    statements = split_sql_statements(sql)
    if len(statements) != 1:
        raise QueryPolicyError(
            "multiple_statements_blocked",
            "Only one read-only SQL statement is allowed.",
        )

    stripped = statements[0].strip().rstrip(";")
    first = first_sql_keyword(stripped)
    if first not in ALLOWED_STATEMENTS:
        raise QueryPolicyError(
            "statement_type_blocked",
            "Only read-only SELECT/WITH/SHOW queries are allowed.",
        )

    blocked = has_blocked_keyword(stripped)
    if blocked:
        raise QueryPolicyError(
            "write_keyword_blocked",
            f"Blocked non-read-only token: {blocked}",
        )
    validate_relation_schemas(stripped, schema=selected_schema)
    return stripped


def connect_readonly(cfg: dict[str, str], *, timeout_ms: int):
    connect_timeout = max(1, math.ceil(timeout_ms / 1_000))
    if psycopg2:
        conn = psycopg2.connect(connect_timeout=connect_timeout, **cfg)
        conn.set_session(readonly=True, autocommit=True)
        return "psycopg2", conn

    if psycopg:
        conn = psycopg.connect(connect_timeout=connect_timeout, row_factory=dict_row, **cfg)
        conn.autocommit = True
        conn.execute("SET default_transaction_read_only = on")
        return "psycopg3", conn

    raise RuntimeError("Neither psycopg2 nor psycopg is installed.")


def normalize_broker_url(url: str) -> str:
    try:
        parsed = urlsplit(url.strip())
        port = parsed.port or DEFAULT_BROKER_PORT
    except ValueError as exc:
        raise QueryPolicyError("broker_url_invalid", "The configured query broker URL is invalid.") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in ALLOWED_BROKER_HOSTS
        or port != DEFAULT_BROKER_PORT
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise QueryPolicyError(
            "broker_url_not_allowed",
            "The query broker must use the fixed SIQ broker host and port.",
        )
    hostname = str(parsed.hostname)
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    return f"http://{rendered_host}:{port}/v1/postgresql/query"


BROKER_IDENTITY_TOKEN_ENV = "SIQ_OPENSHELL_DATA_IDENTITY_TOKEN"
LEGACY_BROKER_IDENTITY_TOKEN_ENV = "SIQ_OPENSHELL_BROKER_IDENTITY_TOKEN"
BROKER_IDENTITY_HEADER = "X-SIQ-OpenShell-Identity"
BROKER_IDENTITY_TOKEN_RE = re.compile(r"v1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\Z")
MAX_BROKER_IDENTITY_TOKEN_BYTES = 4096


def broker_identity_headers(env: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if env is None else env
    token = str(source.get(BROKER_IDENTITY_TOKEN_ENV) or source.get(LEGACY_BROKER_IDENTITY_TOKEN_ENV) or "").strip()
    if not token:
        return {}
    if (
        len(token.encode("ascii", errors="ignore")) > MAX_BROKER_IDENTITY_TOKEN_BYTES
        or BROKER_IDENTITY_TOKEN_RE.fullmatch(token) is None
    ):
        raise QueryPolicyError("broker_identity_token_invalid", "The broker request identity token is invalid.")
    return {BROKER_IDENTITY_HEADER: token}


def broker_postgresql_query(
    broker_url: str,
    *,
    sql: str,
    schema: str,
    row_limit: int,
    timeout_ms: int,
) -> dict[str, object]:
    endpoint = normalize_broker_url(broker_url)
    content = json.dumps(
        {
            "sql": sql,
            "schema": schema,
            "limit": row_limit,
            "timeout_ms": timeout_ms,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    headers.update(broker_identity_headers())
    request = Request(
        endpoint,
        data=content,
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=max(2.0, timeout_ms / 1_000 + 2.0)) as response:
            response_content = response.read(MAX_BROKER_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        response_content = exc.read(MAX_BROKER_RESPONSE_BYTES + 1)
        try:
            error_payload = json.loads(response_content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            error_payload = {}
        error_code = str(error_payload.get("error_code") or "broker_request_rejected")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,95}", error_code):
            error_code = "broker_request_rejected"
        raise QueryPolicyError(error_code, "The read-only query broker rejected the request.") from exc
    except (OSError, URLError) as exc:
        raise RuntimeError("read_only_query_broker_unavailable") from exc
    if len(response_content) > MAX_BROKER_RESPONSE_BYTES:
        raise RuntimeError("read_only_query_broker_response_too_large")
    try:
        payload = json.loads(response_content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("read_only_query_broker_response_invalid") from exc
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("ok") is not True
        or payload.get("schema") != schema
        or payload.get("row_limit") != row_limit
        or not isinstance(rows, list)
        or payload.get("row_count") != len(rows)
    ):
        raise RuntimeError("read_only_query_broker_response_invalid")
    return {
        "ok": True,
        "schema": schema,
        "row_limit": row_limit,
        "row_count": len(rows),
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a read-only query against SIQ PostgreSQL.")
    parser.add_argument("--sql", required=True, help="SELECT/WITH/SHOW SQL to execute")
    parser.add_argument("--profile-env", help="Optional Hermes profile .env path")
    parser.add_argument("--schema", default=DEFAULTS["schema"], help="Allowed SIQ market fact schema")
    parser.add_argument("--limit", type=int, default=DEFAULT_ROW_LIMIT, help="Maximum returned rows")
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=DEFAULT_TIMEOUT_MS,
        help="PostgreSQL statement and connection timeout in milliseconds",
    )
    args = parser.parse_args(argv)

    schema = validate_schema(args.schema)
    row_limit, timeout_ms = validate_query_limits(
        row_limit=args.limit,
        timeout_ms=args.timeout_ms,
    )
    sql = normalize_sql(args.sql, schema=schema)
    broker_url = str(os.environ.get("SIQ_PG_QUERY_BROKER_URL") or "").strip()
    if broker_url:
        print(
            json.dumps(
                broker_postgresql_query(
                    broker_url,
                    sql=sql,
                    schema=schema,
                    row_limit=row_limit,
                    timeout_ms=timeout_ms,
                ),
                ensure_ascii=False,
                default=json_default,
            )
        )
        return 0

    env_file = load_env_file(Path(args.profile_env)) if args.profile_env else {}
    cfg = project_pdf2md_config(env_file)
    if not cfg.get("password"):
        raise QueryPolicyError(
            "database_credentials_missing",
            "PostgreSQL password not found. Provide SIQ_PGPASSWORD/PGPASSWORD/POSTGRES_PASSWORD "
            "or SIQ_APP_DATABASE_URL in the process environment or profile .env.",
        )
    first = first_sql_keyword(sql)
    lowered_without_literals = strip_sql_comments_and_literals(sql).lower()
    if first in {"select", "with"} and not re.search(r"\b(?:limit|fetch\s+first)\b", lowered_without_literals):
        sql = f"{sql} LIMIT {row_limit}"

    driver, conn = connect_readonly(cfg, timeout_ms=timeout_ms)
    try:
        if driver == "psycopg2":
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(f"SET statement_timeout = {timeout_ms}")
                cur.execute(f"SET search_path TO {schema}, public")
                cur.execute(sql)
                rows = cur.fetchall() if cur.description else []
        else:
            with conn.cursor() as cur:
                cur.execute(f"SET statement_timeout = {timeout_ms}")
                cur.execute(f"SET search_path TO {schema}, public")
                cur.execute(sql)
                rows = cur.fetchall() if cur.description else []
        print(
            json.dumps(
                {
                    "ok": True,
                    "schema": schema,
                    "row_limit": row_limit,
                    "row_count": len(rows),
                    "rows": rows,
                },
                ensure_ascii=False,
                default=json_default,
            )
        )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except QueryPolicyError as exc:
        print(
            json.dumps(
                {"ok": False, "error_code": exc.code, "error": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2) from exc
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_code": "database_query_failed",
                    "error": f"{type(exc).__name__}: {exc}",
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
