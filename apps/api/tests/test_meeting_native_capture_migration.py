import sqlite3
from pathlib import Path

import database
from services.auth_service import User
from services.meeting_contracts import MEETING_TABLES
from services.meeting_native_capture_contracts import (
    MEETING_NATIVE_CAPTURE_FINALIZATION_TABLES,
    MEETING_NATIVE_CAPTURE_MANIFEST_TABLES,
    MEETING_NATIVE_CAPTURE_V1_TABLES,
)
from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def test_native_capture_migration_is_repeatable_and_preserves_existing_meeting_tables():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    connection.executescript((MIGRATIONS / "002_create_meeting_tables.sql").read_text(encoding="utf-8"))
    before = {
        row[1]: (row[2], row[3], row[4], row[5]) for row in connection.execute("PRAGMA table_info(meeting_sessions)")
    }
    sql = (MIGRATIONS / "004_create_meeting_native_capture_tables.sql").read_text(encoding="utf-8")
    connection.executescript(sql)
    connection.executescript(sql)
    after = {
        row[1]: (row[2], row[3], row[4], row[5]) for row in connection.execute("PRAGMA table_info(meeting_sessions)")
    }
    migration_columns = {
        table: {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for (table,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'meeting_native_capture_%'"
        )
    }

    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            *[model.__table__ for model in MEETING_TABLES],
            *[model.__table__ for model in MEETING_NATIVE_CAPTURE_V1_TABLES],
        ],
    )
    inspector = inspect(engine)
    model_columns = {
        table: {column["name"] for column in inspector.get_columns(table)}
        for table in inspector.get_table_names()
        if table == "meeting_native_captures" or table.startswith("meeting_native_capture_")
    }

    assert before == after
    assert set(migration_columns) == {
        "meeting_native_captures",
        "meeting_native_capture_epochs",
        "meeting_native_capture_batches",
        "meeting_native_capture_tokens",
    }
    assert migration_columns == model_columns


def test_native_capture_finalization_migration_upgrades_004_repeatably():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    connection.executescript((MIGRATIONS / "002_create_meeting_tables.sql").read_text(encoding="utf-8"))
    connection.executescript((MIGRATIONS / "004_create_meeting_native_capture_tables.sql").read_text(encoding="utf-8"))
    sql = (MIGRATIONS / "005_create_meeting_native_capture_finalization_tables.sql").read_text(encoding="utf-8")
    connection.executescript(sql)
    connection.executescript(sql)
    migration_columns = {
        table: {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for (table,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'meeting_native_capture_%'"
        )
    }

    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            *[model.__table__ for model in MEETING_TABLES],
            *[model.__table__ for model in MEETING_NATIVE_CAPTURE_V1_TABLES],
            *[model.__table__ for model in MEETING_NATIVE_CAPTURE_FINALIZATION_TABLES],
        ],
    )
    inspector = inspect(engine)
    model_columns = {
        table: {column["name"] for column in inspector.get_columns(table)}
        for table in inspector.get_table_names()
        if table == "meeting_native_captures" or table.startswith("meeting_native_capture_")
    }

    assert set(migration_columns) == {
        "meeting_native_captures",
        "meeting_native_capture_epochs",
        "meeting_native_capture_batches",
        "meeting_native_capture_tokens",
        "meeting_native_capture_gaps",
        "meeting_native_capture_finalizations",
        "meeting_native_capture_audio_links",
    }
    assert migration_columns == model_columns


def test_native_capture_manifest_migration_is_repeatable_and_matches_models():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    connection.executescript((MIGRATIONS / "002_create_meeting_tables.sql").read_text(encoding="utf-8"))
    connection.executescript((MIGRATIONS / "004_create_meeting_native_capture_tables.sql").read_text(encoding="utf-8"))
    sql = (MIGRATIONS / "007_create_meeting_native_capture_manifest_entries.sql").read_text(encoding="utf-8")
    connection.executescript(sql)
    connection.executescript(sql)
    migration_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(meeting_native_capture_manifest_entries)")
    }

    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            *[model.__table__ for model in MEETING_TABLES],
            *[model.__table__ for model in MEETING_NATIVE_CAPTURE_V1_TABLES],
            *[model.__table__ for model in MEETING_NATIVE_CAPTURE_MANIFEST_TABLES],
        ],
    )
    model_columns = {
        column["name"] for column in inspect(engine).get_columns("meeting_native_capture_manifest_entries")
    }
    assert migration_columns == model_columns


def test_native_capture_epoch_digest_has_an_additive_postgresql_upgrade():
    sql = (MIGRATIONS / "008_add_meeting_native_capture_epoch_manifest_digest.sql").read_text(
        encoding="utf-8"
    )
    normalized = " ".join(sql.lower().split())
    assert (
        "alter table meeting_native_capture_epochs "
        "add column if not exists manifest_sha256 varchar(64)"
    ) in normalized
    assert "drop " not in normalized


def test_sqlite_startup_adds_epoch_digest_to_an_existing_native_schema(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'native-startup-upgrade.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE meeting_native_capture_epochs ("
                    "id VARCHAR(36) PRIMARY KEY, capture_id VARCHAR(36) NOT NULL, "
                    "stream_epoch INTEGER NOT NULL)"
                )
            )
        monkeypatch.setattr(database, "engine", engine)

        monkeypatch.delenv("SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED", raising=False)
        database._ensure_meeting_columns()
        columns = {column["name"] for column in inspect(engine).get_columns("meeting_native_capture_epochs")}
        assert "manifest_sha256" not in columns

        monkeypatch.setenv("SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED", "1")
        database._ensure_meeting_columns()
        database._ensure_meeting_columns()

        columns = {column["name"] for column in inspect(engine).get_columns("meeting_native_capture_epochs")}
        assert "manifest_sha256" in columns
    finally:
        engine.dispose()


def test_native_schema_is_excluded_from_startup_when_feature_is_off(monkeypatch):
    monkeypatch.delenv("SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED", raising=False)
    disabled = {table.name for table in database._app_schema_tables()}
    assert disabled.isdisjoint(database.NATIVE_CAPTURE_TABLE_NAMES)

    monkeypatch.setenv("SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED", "true")
    enabled = {table.name for table in database._app_schema_tables()}
    assert database.NATIVE_CAPTURE_TABLE_NAMES <= enabled


def test_flag_off_never_reflects_native_tables(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'flag-off-schema.db'}")
    real_inspector = inspect(engine)

    class NativeReflectionGuard:
        def has_table(self, table_name):
            if table_name in database.NATIVE_CAPTURE_TABLE_NAMES:
                raise AssertionError("flag-off startup reflected a native table")
            return real_inspector.has_table(table_name)

        def get_columns(self, table_name):
            if table_name in database.NATIVE_CAPTURE_TABLE_NAMES:
                raise AssertionError("flag-off startup reflected native columns")
            return real_inspector.get_columns(table_name)

    try:
        monkeypatch.delenv("SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED", raising=False)
        monkeypatch.setattr(database, "engine", engine)
        monkeypatch.setattr(database, "inspect", lambda _engine: NativeReflectionGuard())
        database._ensure_meeting_columns()
    finally:
        engine.dispose()


def test_flag_off_create_and_validation_ignore_native_schema(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'flag-off-create.db'}")
    try:
        monkeypatch.delenv("SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED", raising=False)
        monkeypatch.setattr(database, "engine", engine)
        SQLModel.metadata.create_all(engine, tables=database._app_schema_tables())

        database._validate_app_schema()

        table_names = set(inspect(engine).get_table_names())
        assert table_names.isdisjoint(database.NATIVE_CAPTURE_TABLE_NAMES)
    finally:
        engine.dispose()
