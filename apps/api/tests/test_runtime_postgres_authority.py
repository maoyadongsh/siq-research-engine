from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from services.durable_job_service import DurableBackgroundJob, DurableJobCoordinator, utcnow_naive
from services.ic_task_lease import (
    ICTaskAlreadyClaimedError,
    ICTaskLeaseRecord,
    ICTaskOwnerReuseError,
    PostgresICTaskLeaseStore,
)
from sqlalchemy import create_engine, text
from sqlmodel import Session


def _sync_postgres_url(value: str) -> str:
    return value.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1).replace(
        "postgresql://", "postgresql+psycopg://", 1
    )


@pytest.fixture()
def postgres_runtime_engine():
    database_url = os.getenv("SIQ_TEST_POSTGRES_URL", "").strip()
    if not database_url:
        pytest.skip("SIQ_TEST_POSTGRES_URL is not configured")
    url = _sync_postgres_url(database_url)
    schema = f"siq_runtime_authority_{uuid.uuid4().hex}"
    admin_engine = create_engine(url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        yield engine
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


def test_real_postgres_durable_job_claim_reclaim_and_attempt_fence(postgres_runtime_engine):
    DurableBackgroundJob.__table__.create(postgres_runtime_engine)
    coordinator = DurableJobCoordinator(
        session_factory=lambda: Session(postgres_runtime_engine),
        lease_seconds=60,
    )
    coordinator.create_job(job_id="pg-job", kind="market-package")
    barrier = Barrier(2)

    def compete(owner: str):
        barrier.wait(timeout=5)
        return coordinator.claim("pg-job", owner=owner)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(compete, ("pg-worker-a", "pg-worker-b")))
    assert sum(claim is not None for claim in claims) == 1
    first = next(claim for claim in claims if claim is not None)

    with Session(postgres_runtime_engine) as session:
        row = session.get(DurableBackgroundJob, "pg-job")
        assert row is not None
        row.lease_until = utcnow_naive() - timedelta(seconds=1)
        session.add(row)
        session.commit()

    reclaimed = coordinator.claim("pg-job", owner="pg-worker-recovery")
    assert reclaimed is not None and reclaimed["attempt"] == 2
    assert coordinator.finish(
        "pg-job",
        owner=first["owner"],
        attempt=first["attempt"],
        status="succeeded",
    ) is False
    assert coordinator.finish(
        "pg-job",
        owner="pg-worker-recovery",
        attempt=reclaimed["attempt"],
        status="succeeded",
        result={"ok": True},
    ) is True


def test_real_postgres_ic_claim_expiry_and_owner_fence(postgres_runtime_engine):
    ICTaskLeaseRecord.__table__.create(postgres_runtime_engine)
    store = PostgresICTaskLeaseStore(
        session_factory=lambda: Session(postgres_runtime_engine)
    )
    scope = "DEAL-PG/phases/ic_task_leases.json"
    task = "DEAL-PG:R1:siq_ic_strategist"
    now = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)

    def iso(minutes: int) -> str:
        return (now + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")

    barrier = Barrier(2)

    def compete(owner: str):
        barrier.wait(timeout=5)
        try:
            return store.claim(
                scope_key=scope,
                task_key=task,
                owner=owner,
                now=iso(0),
                lease_seconds=60,
            )
        except ICTaskAlreadyClaimedError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(compete, ("pg-ic-a", "pg-ic-b")))
    assert sum(claim is not None for claim in claims) == 1
    first = next(claim for claim in claims if claim is not None)

    with pytest.raises(ICTaskOwnerReuseError, match="fresh owner"):
        store.claim(
            scope_key=scope,
            task_key=task,
            owner=first["owner"],
            now=iso(2),
            lease_seconds=60,
        )
    reclaimed = store.claim(
        scope_key=scope,
        task_key=task,
        owner="pg-ic-recovery",
        now=iso(2),
        lease_seconds=60,
    )
    assert reclaimed["attempt"] == 2
    assert reclaimed["recovery_reason"] == "lease_expired"
    assert store.finish(
        scope_key=scope,
        task_key=task,
        owner=first["owner"],
        now=iso(2),
        status="succeeded",
    ) is None
    finished = store.finish(
        scope_key=scope,
        task_key=task,
        owner="pg-ic-recovery",
        now=iso(2),
        status="succeeded",
    )
    assert finished is not None and finished["status"] == "succeeded"
