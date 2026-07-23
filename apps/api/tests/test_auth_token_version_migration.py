from pathlib import Path
from types import SimpleNamespace

import database
from sqlalchemy import create_engine, inspect, text

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "009_add_user_token_version.sql"


def test_postgres_token_version_migration_is_additive_and_idempotent():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "add column if not exists token_version integer not null default 0" in sql
    assert "update users" in sql
    assert "alter column token_version set not null" in sql
    assert "drop table" not in sql


def test_startup_auth_migration_adds_and_backfills_token_version(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-auth.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE users (
                        id INTEGER PRIMARY KEY,
                        username VARCHAR(50) NOT NULL,
                        email VARCHAR(255) NOT NULL,
                        hashed_password VARCHAR(255) NOT NULL,
                        full_name VARCHAR(100) NOT NULL,
                        role VARCHAR(20) NOT NULL,
                        is_active BOOLEAN DEFAULT TRUE,
                        created_at DATETIME,
                        last_login DATETIME
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO users (
                        id, username, email, hashed_password, full_name, role, is_active
                    ) VALUES (1, 'legacy', 'legacy@example.test', 'x', 'Legacy', 'viewer', 1)
                    """
                )
            )
        monkeypatch.setattr(database, "engine", engine)

        database._ensure_auth_columns()
        database._ensure_auth_columns()

        columns = {column["name"]: column for column in inspect(engine).get_columns("users")}
        with engine.connect() as connection:
            token_version = connection.execute(
                text("SELECT token_version FROM users WHERE id = 1")
            ).scalar_one()

        assert "token_version" in columns
        assert columns["token_version"]["nullable"] is False
        assert token_version == 0
    finally:
        engine.dispose()


def test_postgres_startup_does_not_apply_token_version_ddl(monkeypatch):
    class FakeInspector:
        def has_table(self, table_name):
            return table_name == "users"

        def get_columns(self, _table_name):
            return [
                {"name": "approval_status", "nullable": True},
                {"name": "approval_note", "nullable": True},
                {"name": "approved_by", "nullable": True},
                {"name": "approved_at", "nullable": True},
            ]

    fake_engine = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    monkeypatch.setattr(database, "engine", fake_engine)
    monkeypatch.setattr(database, "inspect", lambda _engine: FakeInspector())

    # Missing token_version is intentionally left for strict schema validation
    # to report with migration 009, rather than mutating production at startup.
    database._ensure_auth_columns()
