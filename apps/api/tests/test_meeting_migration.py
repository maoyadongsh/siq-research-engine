import sqlite3
from pathlib import Path

from services.auth_service import User
from services.meeting_contracts import MEETING_TABLES
from sqlalchemy import create_engine, inspect
from sqlmodel import SQLModel

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "002_create_meeting_tables.sql"


def test_meeting_migration_is_repeatable_and_matches_sqlmodel_columns():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    sql = MIGRATION.read_text(encoding="utf-8")
    connection.executescript(sql)
    connection.executescript(sql)
    migration_columns = {
        table: {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for (table,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'meeting_%'"
        )
    }

    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(
        engine,
        tables=[User.__table__, *[model.__table__ for model in MEETING_TABLES]],
    )
    inspector = inspect(engine)
    model_columns = {
        table: {column["name"] for column in inspector.get_columns(table)}
        for table in inspector.get_table_names()
        if table.startswith("meeting_")
    }

    assert len(migration_columns) == 21
    assert migration_columns == model_columns
