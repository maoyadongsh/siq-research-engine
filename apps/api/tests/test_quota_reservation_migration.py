from datetime import datetime

import database
import pytest
from sqlalchemy import create_engine, inspect, text


def test_startup_migration_adds_and_backfills_quota_expiry(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-quota.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(text("""
                CREATE TABLE quota_reservations (
                    id VARCHAR(80) PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    event_type VARCHAR(40) NOT NULL,
                    event_date VARCHAR(10) NOT NULL,
                    amount INTEGER NOT NULL,
                    status VARCHAR(20) NOT NULL,
                    run_id VARCHAR(255),
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
            """))
            connection.execute(text("""
                INSERT INTO quota_reservations (
                    id, user_id, event_type, event_date, amount, status,
                    run_id, created_at, updated_at
                ) VALUES (
                    'legacy-reservation', 1, 'agent_question', '2026-07-13', 1,
                    'reserved', NULL, '2026-07-13 00:00:00', '2026-07-13 00:02:00'
                )
            """))
        monkeypatch.setattr(database, "engine", engine)

        database._ensure_quota_reservation_columns()
        database._ensure_app_indexes()
        database._ensure_quota_reservation_columns()

        inspector = inspect(engine)
        columns = {column["name"] for column in inspector.get_columns("quota_reservations")}
        indexes = {index["name"] for index in inspector.get_indexes("quota_reservations")}
        with engine.connect() as connection:
            expires_at = connection.execute(text(
                "SELECT expires_at FROM quota_reservations WHERE id = 'legacy-reservation'"
            )).scalar_one()

        assert "expires_at" in columns
        assert "ix_quota_reservations_expires_at" in indexes
        assert datetime.fromisoformat(str(expires_at)) == datetime(2026, 7, 13, 0, 17)
    finally:
        engine.dispose()


def test_startup_schema_validation_reports_unmigrated_model_column(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'schema-drift.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(text("""
                CREATE TABLE quota_reservations (
                    id VARCHAR(80) PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    event_type VARCHAR(40) NOT NULL,
                    event_date VARCHAR(10) NOT NULL,
                    amount INTEGER NOT NULL,
                    status VARCHAR(20) NOT NULL,
                    run_id VARCHAR(255),
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
            """))
        monkeypatch.setattr(database, "engine", engine)

        with pytest.raises(RuntimeError, match=r"quota_reservations\.expires_at"):
            database._validate_app_schema()
    finally:
        engine.dispose()


def test_startup_migration_recovers_partially_added_expiry_column(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'partial-quota.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(text("""
                CREATE TABLE quota_reservations (
                    id VARCHAR(80) PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    event_type VARCHAR(40) NOT NULL,
                    event_date VARCHAR(10) NOT NULL,
                    amount INTEGER NOT NULL,
                    status VARCHAR(20) NOT NULL,
                    run_id VARCHAR(255),
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    expires_at DATETIME
                )
            """))
            connection.execute(text("""
                INSERT INTO quota_reservations (
                    id, user_id, event_type, event_date, amount, status,
                    run_id, created_at, updated_at, expires_at
                ) VALUES (
                    'partial-reservation', 1, 'parse_job', '2026-07-13', 1,
                    'reserved', NULL, '2026-07-13 01:00:00',
                    '2026-07-13 01:04:00', NULL
                )
            """))
        monkeypatch.setattr(database, "engine", engine)

        database._ensure_quota_reservation_columns()

        with engine.connect() as connection:
            expires_at = connection.execute(text(
                "SELECT expires_at FROM quota_reservations WHERE id = 'partial-reservation'"
            )).scalar_one()
        assert datetime.fromisoformat(str(expires_at)) == datetime(2026, 7, 13, 1, 19)
    finally:
        engine.dispose()
