from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
from services.security_redaction import (
    REDACTED_CONNECTION_URL,
    redact_connection_url,
)


@pytest.mark.parametrize(
    ("connection_url", "forbidden"),
    [
        ("postgresql://alice:plain-secret@db:5432/siq", ("alice", "plain-secret")),
        (
            "postgresql+psycopg://alice:p%40ss%3Aword@[2001:db8::1]:5432/siq?sslmode=require&token=query-secret",
            ("alice", "p%40ss%3Aword", "query-secret"),
        ),
        ("mysql://service:secret@db/app?password=query-secret", ("service", "secret", "query-secret")),
    ],
)
def test_redact_connection_url_removes_userinfo_and_sensitive_query_values(
    connection_url: str,
    forbidden: tuple[str, ...],
):
    redacted = redact_connection_url(connection_url)

    for secret in forbidden:
        assert secret not in redacted
    assert redacted.startswith(connection_url.split(":", 1)[0] + "://")


def test_redact_connection_url_preserves_non_secret_connection_location():
    assert redact_connection_url("postgresql://db.internal:5432/siq") == "postgresql://db.internal:5432/siq"


@pytest.mark.parametrize(
    "connection_url",
    [
        "",
        "not a connection URL password=secret",
        "postgresql://user:secret@[broken-host/siq",
    ],
)
def test_redact_connection_url_fails_closed_for_empty_or_malformed_values(connection_url: str):
    assert redact_connection_url(connection_url) == REDACTED_CONNECTION_URL


def test_init_auth_system_uses_shared_connection_url_redaction(monkeypatch, capsys):
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "init_auth_system.py"
    spec = importlib.util.spec_from_file_location("siq_init_auth_system_for_redaction_test", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    original_cwd = Path.cwd()
    try:
        spec.loader.exec_module(module)
    finally:
        os.chdir(original_cwd)

    monkeypatch.setenv("SIQ_APP_DATABASE_URL", "postgresql://alice:plain-secret@db:5432/siq")

    def _stop_after_connection_log(_engine):
        raise RuntimeError("stop after connection log")

    monkeypatch.setattr("sqlmodel.SQLModel.metadata.create_all", _stop_after_connection_log)
    monkeypatch.setattr("sqlmodel.create_engine", lambda database_url, echo=False: object())

    assert module.init_database() is False
    captured = capsys.readouterr()
    assert "plain-secret" not in captured.out
    assert "plain-secret" not in captured.err
    assert "db:5432/siq" in captured.out
