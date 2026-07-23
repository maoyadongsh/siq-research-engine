from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pytest
from services.workflow_queue import WorkflowQueueJob
from sqlalchemy import create_engine, text

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "010_create_workflow_queue_jobs.sql"


def test_workflow_queue_migration_covers_model_and_fencing_invariants():
    sql = MIGRATION.read_text(encoding="utf-8")
    match = re.search(
        r"CREATE TABLE IF NOT EXISTS workflow_queue_jobs\s*\((.*?)\n\);",
        sql,
        flags=re.DOTALL,
    )
    assert match
    columns = set()
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip().rstrip(",")
        if not line or line.startswith(("CONSTRAINT ", "CHECK ", "PRIMARY ", "UNIQUE ")):
            continue
        columns.add(line.split()[0])
    assert columns == set(WorkflowQueueJob.__table__.columns.keys())
    assert "ck_workflow_queue_job_attempts" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS ix_workflow_queue_jobs_active_idempotency" in sql
    assert "WHERE status IN ('queued', 'running')" in sql
    assert "DROP TABLE" not in sql.upper()


def test_workflow_queue_migration_is_repeatable_on_optional_postgres():
    database_url = os.getenv("SIQ_TEST_POSTGRES_URL", "").strip()
    if not database_url:
        pytest.skip("SIQ_TEST_POSTGRES_URL is not configured")
    url = database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1).replace(
        "postgresql://", "postgresql+psycopg://", 1
    )
    schema = f"siq_workflow_queue_migration_{uuid.uuid4().hex}"
    admin_engine = create_engine(url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        sql = MIGRATION.read_text(encoding="utf-8")
        with engine.begin() as connection:
            connection.exec_driver_sql(sql)
            connection.exec_driver_sql(sql)
        with engine.connect() as connection:
            constraints = set(
                connection.execute(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid = 'workflow_queue_jobs'::regclass AND convalidated"
                    )
                ).scalars()
            )
            assert "ck_workflow_queue_job_attempts" in constraints
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()
