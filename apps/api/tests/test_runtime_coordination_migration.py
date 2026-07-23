from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import database
import pytest
from services.durable_job_service import DurableBackgroundJob
from services.ic_task_lease import ICTaskLeaseRecord
from services.runtime_coordination import ActiveRunLease
from services.usage_service import QuotaLedger, QuotaReservation
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "006_create_runtime_coordination_tables.sql"
MODELS = (ActiveRunLease, QuotaLedger, QuotaReservation, DurableBackgroundJob, ICTaskLeaseRecord)


def _migration_columns(sql: str, table_name: str) -> set[str]:
    match = re.search(
        rf"CREATE TABLE IF NOT EXISTS {re.escape(table_name)}\s*\((.*?)\n\);",
        sql,
        flags=re.DOTALL,
    )
    assert match, f"missing migration table: {table_name}"
    columns: set[str] = set()
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip().rstrip(",")
        if not line or line.startswith(("CONSTRAINT ", "PRIMARY ", "UNIQUE ", "CHECK ")):
            continue
        columns.add(line.split()[0])
    return columns


def _sync_postgres_url(value: str) -> str:
    return value.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1).replace(
        "postgresql://", "postgresql+psycopg://", 1
    )


def test_runtime_coordination_migration_covers_model_columns_and_forward_fix_policy():
    sql = MIGRATION.read_text(encoding="utf-8")

    for model in MODELS:
        assert _migration_columns(sql, model.__tablename__) == set(model.__table__.columns.keys())

    assert "ALTER TABLE quota_reservations ADD COLUMN IF NOT EXISTS expires_at" in sql
    assert "ALTER TABLE active_run_leases ADD COLUMN IF NOT EXISTS pool_tenant_id" in sql
    assert "ALTER TABLE active_run_leases ADD COLUMN IF NOT EXISTS pool_user_id" in sql
    assert "ck_quota_ledger_nonnegative" in sql
    assert "ck_quota_reservation_amount" in sql
    assert "ck_durable_background_job_attempt" in sql
    assert "ck_ic_task_lease_attempt" in sql
    assert "runtime coordination migration blocked" in sql
    assert "Operational rollback" in sql
    assert "must not drop active leases or reservation history" in sql
    assert "DROP TABLE" not in sql.upper()


def test_runtime_coordination_migration_is_repeatable_on_optional_postgres():
    database_url = os.getenv("SIQ_TEST_POSTGRES_URL", "").strip()
    if not database_url:
        pytest.skip("SIQ_TEST_POSTGRES_URL is not configured")
    admin_engine = create_engine(_sync_postgres_url(database_url))
    schema = f"siq_runtime_migration_{uuid.uuid4().hex}"
    engine = None
    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(
            _sync_postgres_url(database_url),
            connect_args={"options": f"-csearch_path={schema}"},
        )
        with engine.begin() as connection:
            for model in MODELS:
                model.__table__.create(connection)
        original_engine = database.engine
        database.engine = engine
        try:
            before = database._postgres_runtime_schema_gaps()
            assert {f"{table}.{constraint}" for table, constraint in database.RUNTIME_COORDINATION_CHECKS} <= set(before)
            assert any(item.endswith("<server_default>") for item in before)

            migration_sql = MIGRATION.read_text(encoding="utf-8")
            with engine.begin() as connection:
                connection.exec_driver_sql(migration_sql)
                connection.exec_driver_sql(migration_sql)

            assert not any(
                item.partition(".")[0] in {model.__tablename__ for model in MODELS}
                for item in database._postgres_runtime_schema_gaps()
            )
        finally:
            database.engine = original_engine

        with engine.connect() as connection:
            tables = set(
                connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = :schema"
                    ),
                    {"schema": schema},
                ).scalars()
            )
            assert {model.__tablename__ for model in MODELS} <= tables
    finally:
        if engine is not None:
            engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


def test_runtime_coordination_migration_rejects_invalid_legacy_rows():
    database_url = os.getenv("SIQ_TEST_POSTGRES_URL", "").strip()
    if not database_url:
        pytest.skip("SIQ_TEST_POSTGRES_URL is not configured")
    admin_engine = create_engine(_sync_postgres_url(database_url))
    schema = f"siq_runtime_migration_invalid_{uuid.uuid4().hex}"
    engine = None
    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(
            _sync_postgres_url(database_url),
            connect_args={"options": f"-csearch_path={schema}"},
        )
        with engine.begin() as connection:
            for model in MODELS:
                model.__table__.create(connection)
            connection.execute(
                text(
                    "INSERT INTO quota_ledgers "
                    "(user_id, event_type, event_date, used_count, reserved_count, updated_at) "
                    "VALUES (1, 'agent_question', '2026-07-14', -1, 0, CURRENT_TIMESTAMP)"
                )
            )

        with pytest.raises(IntegrityError, match="quota_ledgers contains negative counters"):
            with engine.begin() as connection:
                connection.exec_driver_sql(MIGRATION.read_text(encoding="utf-8"))

        with engine.connect() as connection:
            assert connection.execute(text("SELECT used_count FROM quota_ledgers")).scalar_one() == -1
            assert connection.execute(
                text(
                    "SELECT count(*) FROM pg_constraint "
                    "WHERE conrelid = 'quota_ledgers'::regclass "
                    "AND conname = 'ck_quota_ledger_nonnegative'"
                )
            ).scalar_one() == 0
    finally:
        if engine is not None:
            engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()
