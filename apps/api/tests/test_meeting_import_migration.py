import sqlite3
from pathlib import Path

from services.auth_service import User
from services.meeting_contracts import MEETING_TABLES
from services.meeting_import_contracts import MEETING_IMPORT_TABLES
from sqlalchemy import create_engine, inspect
from sqlmodel import SQLModel

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def test_meeting_import_migration_is_repeatable_and_matches_sqlmodel_columns():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    connection.executescript((MIGRATIONS / "002_create_meeting_tables.sql").read_text(encoding="utf-8"))
    sql = (MIGRATIONS / "003_create_meeting_import_tables.sql").read_text(encoding="utf-8")
    connection.executescript(sql)
    connection.executescript(sql)
    migration_columns = {
        table: {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for (table,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'meeting_import_%'"
        )
    }

    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            *[model.__table__ for model in MEETING_TABLES],
            *[model.__table__ for model in MEETING_IMPORT_TABLES],
        ],
    )
    inspector = inspect(engine)
    model_columns = {
        table: {column["name"] for column in inspector.get_columns(table)}
        for table in inspector.get_table_names()
        if table.startswith("meeting_import_")
    }

    assert set(migration_columns) == {"meeting_import_uploads", "meeting_import_chunks"}
    assert migration_columns == model_columns
