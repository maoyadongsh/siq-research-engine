import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PG_QUERY_PATH = (
    PROJECT_ROOT
    / "agents"
    / "hermes"
    / "profiles"
    / "shared"
    / "scripts"
    / "pg_query.py"
)
SPEC = importlib.util.spec_from_file_location("hermes_pg_query", PG_QUERY_PATH)
assert SPEC and SPEC.loader
pg_query = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pg_query)


def test_normalize_sql_allows_selected_market_schema_and_literals():
    sql = pg_query.normalize_sql(
        "SELECT f.value FROM sec_us.v_agent_financial_facts f "
        "WHERE f.metric_name = 'drop schema private'",
        schema="sec_us",
    )

    assert sql.startswith("SELECT")


@pytest.mark.parametrize(
    ("sql", "schema", "code"),
    [
        ("SELECT 1; SELECT 2", "pdf2md", "multiple_statements_blocked"),
        ("DELETE FROM facts", "pdf2md", "statement_type_blocked"),
        ("SELECT * FROM facts FOR UPDATE", "pdf2md", "write_keyword_blocked"),
        (
            "SELECT * FROM sec_us.v_agent_financial_facts",
            "pdf2md",
            "cross_schema_query_blocked",
        ),
    ],
)
def test_normalize_sql_returns_stable_policy_error_codes(sql, schema, code):
    with pytest.raises(pg_query.QueryPolicyError) as exc_info:
        pg_query.normalize_sql(sql, schema=schema)

    assert exc_info.value.code == code


def test_schema_allowlist_rejects_system_and_unknown_schemas():
    for schema in ("pg_catalog", "information_schema", "private"):
        with pytest.raises(pg_query.QueryPolicyError) as exc_info:
            pg_query.validate_schema(schema)
        assert exc_info.value.code == "schema_not_allowed"


@pytest.mark.parametrize(
    ("row_limit", "timeout_ms", "code"),
    [
        (0, 5_000, "row_limit_out_of_range"),
        (501, 5_000, "row_limit_out_of_range"),
        (50, 0, "timeout_out_of_range"),
        (50, 30_001, "timeout_out_of_range"),
    ],
)
def test_query_limits_are_bounded(row_limit, timeout_ms, code):
    with pytest.raises(pg_query.QueryPolicyError) as exc_info:
        pg_query.validate_query_limits(row_limit=row_limit, timeout_ms=timeout_ms)

    assert exc_info.value.code == code


def test_connection_url_decodes_credentials_without_exposing_them():
    config = pg_query.connection_kwargs_from_url(
        "postgresql+psycopg://reader:p%40ss@db.internal:15432/app",
        database="siq",
    )

    assert config == {
        "host": "db.internal",
        "port": "15432",
        "dbname": "siq",
        "user": "reader",
        "password": "p@ss",
    }


def test_cli_policy_failure_is_structured_and_does_not_require_database():
    result = subprocess.run(
        [
            sys.executable,
            str(PG_QUERY_PATH),
            "--sql",
            "SELECT * FROM private.facts",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    payload = json.loads(result.stderr)
    assert payload["ok"] is False
    assert payload["error_code"] == "cross_schema_query_blocked"


@pytest.mark.parametrize(
    "profile_rule",
    [
        "agents/hermes/profiles/siq_assistant/SOUL.md",
        "agents/hermes/profiles/siq_assistant/rules/OPERATING_RULES.md",
        "agents/hermes/profiles/siq_factchecker/SOUL.md",
        "agents/hermes/profiles/siq_tracking/SOUL.md",
    ],
)
def test_profile_query_guidance_uses_bounded_helper_without_legacy_database(profile_rule):
    text = (PROJECT_ROOT / profile_rule).read_text(encoding="utf-8")

    assert "--schema pdf2md" in text
    assert "--limit 50" in text
    assert "--timeout-ms 5000" in text
    assert "127.0.0.1:5432" not in text
    assert "ai_platform" not in text
    assert "dgx" not in text
