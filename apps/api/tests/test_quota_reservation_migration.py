from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace

import database
import pytest
from sqlalchemy import create_engine, inspect, text


def test_postgres_schema_initialization_lock_releases_after_error(monkeypatch):
    events: list[str] = []

    class FakeResult:
        def scalar(self):
            return True

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, statement, parameters):
            events.append(f"{statement}:{parameters['lock_key']}")
            return FakeResult()

    class FakeLockEngine:
        def connect(self):
            return FakeConnection()

        def dispose(self):
            events.append("disposed")

    monkeypatch.setattr(database, "engine", SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
    monkeypatch.setattr(database, "_new_app_schema_lock_engine", lambda: FakeLockEngine())

    with pytest.raises(RuntimeError, match="startup failed"):
        with database._app_schema_initialization_lock():
            events.append("schema-convergence")
            raise RuntimeError("startup failed")

    assert events == [
        f"SELECT pg_try_advisory_lock(:lock_key):{database.APP_SCHEMA_INIT_ADVISORY_LOCK_KEY}",
        "schema-convergence",
        f"SELECT pg_advisory_unlock(:lock_key):{database.APP_SCHEMA_INIT_ADVISORY_LOCK_KEY}",
        "disposed",
    ]


def test_postgres_schema_initialization_lock_times_out(monkeypatch):
    events: list[str] = []

    class FakeResult:
        def scalar(self):
            return False

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, statement, _parameters):
            events.append(str(statement))
            return FakeResult()

    class FakeLockEngine:
        def connect(self):
            return FakeConnection()

        def dispose(self):
            events.append("disposed")

    monotonic_values = iter((10.0, 10.02))
    monkeypatch.setattr(database, "engine", SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
    monkeypatch.setattr(database, "_new_app_schema_lock_engine", lambda: FakeLockEngine())
    monkeypatch.setattr(database, "APP_SCHEMA_INIT_LOCK_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(database.time, "monotonic", lambda: next(monotonic_values))

    with pytest.raises(TimeoutError, match="schema initialization lock"):
        with database._app_schema_initialization_lock():
            pytest.fail("timed-out lock must not enter schema convergence")

    assert events == ["SELECT pg_try_advisory_lock(:lock_key)", "disposed"]


def test_sqlite_schema_initialization_does_not_create_lock_engine(monkeypatch):
    monkeypatch.setattr(database, "engine", SimpleNamespace(dialect=SimpleNamespace(name="sqlite")))
    monkeypatch.setattr(
        database,
        "_new_app_schema_lock_engine",
        lambda: pytest.fail("SQLite must not create a PostgreSQL lock engine"),
    )

    with database._app_schema_initialization_lock():
        pass


def test_create_db_and_tables_keeps_all_schema_steps_inside_lock(monkeypatch):
    events: list[str] = []

    @contextmanager
    def fake_lock():
        events.append("lock-enter")
        try:
            yield
        finally:
            events.append("lock-exit")

    monkeypatch.setattr(database, "_app_schema_initialization_lock", fake_lock)
    monkeypatch.setattr(
        database.SQLModel.metadata,
        "create_all",
        lambda _engine, **_kwargs: events.append("create-all"),
    )
    for name in (
        "_ensure_auth_columns",
        "_ensure_chat_message_columns",
        "_ensure_quota_reservation_columns",
        "_ensure_meeting_columns",
        "_ensure_app_indexes",
        "_validate_app_schema",
        "_ensure_agent_memory_schema",
    ):
        monkeypatch.setattr(database, name, lambda name=name: events.append(name))

    database.create_db_and_tables()

    assert events == [
        "lock-enter",
        "create-all",
        "_ensure_auth_columns",
        "_ensure_chat_message_columns",
        "_ensure_quota_reservation_columns",
        "_ensure_meeting_columns",
        "_ensure_app_indexes",
        "_validate_app_schema",
        "_ensure_agent_memory_schema",
        "lock-exit",
    ]


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

        with pytest.raises(RuntimeError, match=r"quota_reservations\.expires_at") as exc_info:
            database._validate_app_schema()
        assert database.RUNTIME_COORDINATION_MIGRATION in str(exc_info.value)
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
