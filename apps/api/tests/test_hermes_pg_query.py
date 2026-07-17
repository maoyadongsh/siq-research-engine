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
    ("url", "expected"),
    [
        ("http://127.0.0.1:18793", "http://127.0.0.1:18793/v1/postgresql/query"),
        ("http://localhost", "http://localhost:18793/v1/postgresql/query"),
        (
            "http://host.openshell.internal:18793/",
            "http://host.openshell.internal:18793/v1/postgresql/query",
        ),
    ],
)
def test_broker_url_is_restricted_to_fixed_siq_hosts_and_port(url, expected):
    assert pg_query.normalize_broker_url(url) == expected

    for blocked in (
        "https://127.0.0.1:18793",
        "http://example.com:18793",
        "http://127.0.0.1:18081",
        "http://user:password@127.0.0.1:18793",
        "http://127.0.0.1:18793/other",
    ):
        with pytest.raises(pg_query.QueryPolicyError):
            pg_query.normalize_broker_url(blocked)


def test_broker_identity_header_is_optional_and_strictly_bounded():
    assert pg_query.broker_identity_headers({}) == {}
    token = "v1." + "cGF5bG9hZA" + ".c2lnbmF0dXJl"
    assert pg_query.broker_identity_headers({pg_query.BROKER_IDENTITY_TOKEN_ENV: token}) == {
        pg_query.BROKER_IDENTITY_HEADER: token
    }

    with pytest.raises(pg_query.QueryPolicyError) as exc_info:
        pg_query.broker_identity_headers({pg_query.BROKER_IDENTITY_TOKEN_ENV: "Bearer secret"})
    assert exc_info.value.code == "broker_identity_token_invalid"
    assert pg_query.broker_identity_headers({pg_query.LEGACY_BROKER_IDENTITY_TOKEN_ENV: token}) == {
        pg_query.BROKER_IDENTITY_HEADER: token
    }
    assert pg_query.broker_identity_headers(
        {
            pg_query.BROKER_IDENTITY_TOKEN_ENV: token,
            pg_query.LEGACY_BROKER_IDENTITY_TOKEN_ENV: "invalid",
        }
    ) == {pg_query.BROKER_IDENTITY_HEADER: token}


def test_main_uses_broker_without_reading_profile_env(monkeypatch, capsys):
    seen = {}
    monkeypatch.setenv("SIQ_PG_QUERY_BROKER_URL", "http://host.openshell.internal:18793")

    def profile_env_must_not_be_read(_path):
        raise AssertionError("broker mode must not read --profile-env")

    def fake_broker(url, **kwargs):
        seen.update({"url": url, **kwargs})
        return {
            "ok": True,
            "schema": kwargs["schema"],
            "row_limit": kwargs["row_limit"],
            "row_count": 1,
            "rows": [{"value": 7}],
        }

    monkeypatch.setattr(pg_query, "load_env_file", profile_env_must_not_be_read)
    monkeypatch.setattr(pg_query, "broker_postgresql_query", fake_broker)

    assert pg_query.main(
        [
            "--profile-env",
            "/sandbox/credentials-must-not-be-read.env",
            "--schema",
            "pdf2md",
            "--limit",
            "12",
            "--timeout-ms",
            "3000",
            "--sql",
            "SELECT 7 AS value",
        ]
    ) == 0

    assert seen == {
        "url": "http://host.openshell.internal:18793",
        "sql": "SELECT 7 AS value",
        "schema": "pdf2md",
        "row_limit": 12,
        "timeout_ms": 3000,
    }
    assert json.loads(capsys.readouterr().out)["rows"] == [{"value": 7}]


def test_main_without_broker_keeps_existing_profile_env_path(monkeypatch, tmp_path):
    profile_env = tmp_path / ".env"
    profile_env.write_text("SIQ_PGUSER=reader\n", encoding="utf-8")
    seen = {}
    monkeypatch.delenv("SIQ_PG_QUERY_BROKER_URL", raising=False)

    def fake_load(path):
        seen["path"] = path
        return {"SIQ_PGUSER": "reader"}

    monkeypatch.setattr(pg_query, "load_env_file", fake_load)
    monkeypatch.setattr(
        pg_query,
        "project_pdf2md_config",
        lambda env: {"password": "", "user": env["SIQ_PGUSER"]},
    )

    with pytest.raises(pg_query.QueryPolicyError) as exc_info:
        pg_query.main(
            [
                "--profile-env",
                str(profile_env),
                "--sql",
                "SELECT 1",
            ]
        )

    assert exc_info.value.code == "database_credentials_missing"
    assert seen["path"] == profile_env


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


def test_analysis_query_guidance_is_portable_and_never_reads_profile_credentials():
    text = (
        PROJECT_ROOT / "agents/hermes/profiles/siq_analysis/rules/data_sources.md"
    ).read_text(encoding="utf-8")

    assert "python3 /home/maoyd/siq-research-engine/data/hermes/home/profiles/shared/scripts/pg_query.py" in text
    assert "--schema pdf2md" in text
    assert "--limit 50" in text
    assert "--timeout-ms 5000" in text
    assert "--profile-env" not in text
    assert "/home/maoyd/.hermes" not in text
