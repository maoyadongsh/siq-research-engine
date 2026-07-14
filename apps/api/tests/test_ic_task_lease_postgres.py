from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest
from services.ic_task_lease import (
    ICTaskLeaseRecord,
    ICTaskOwnerReuseError,
    PostgresICTaskLeaseStore,
)
from sqlmodel import Session, SQLModel, create_engine, select

from services import ic_task_lease


def _iso(minutes: int = 0) -> str:
    value = datetime(2026, 7, 13, 9, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes)
    return value.isoformat().replace("+00:00", "Z")


@pytest.fixture()
def db_engine(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ic-leases.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def _store(engine) -> PostgresICTaskLeaseStore:
    return PostgresICTaskLeaseStore(session_factory=lambda: Session(engine))


def test_database_store_allows_only_one_independent_session_claim(db_engine):
    store = _store(db_engine)
    barrier = Barrier(2)

    def compete(owner: str):
        barrier.wait(timeout=2)
        try:
            return store.claim(
                scope_key="DEAL-001/phases/ic_task_leases.json",
                task_key="DEAL-001:R1:siq_ic_strategist",
                owner=owner,
                now=_iso(),
                lease_seconds=120,
            )
        except ic_task_lease.ICTaskAlreadyClaimedError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(compete, ("worker-a", "worker-b")))

    assert sum(claim is not None for claim in claims) == 1
    with Session(db_engine) as first, Session(db_engine) as second:
        first_row = first.exec(select(ICTaskLeaseRecord)).one()
        second_row = second.exec(select(ICTaskLeaseRecord)).one()
        assert first_row.owner == second_row.owner
        assert first_row.attempt == second_row.attempt == 1


def test_database_store_reclaims_expired_lease_with_history(db_engine):
    store = _store(db_engine)
    first = store.claim(
        scope_key="DEAL-001/phases/ic_task_leases.json",
        task_key="DEAL-001:R1:siq_ic_strategist",
        owner="worker-old",
        now=_iso(),
        lease_seconds=60,
    )

    second = store.claim(
        scope_key="DEAL-001/phases/ic_task_leases.json",
        task_key=first["task_key"],
        owner="worker-new",
        now=_iso(2),
        lease_seconds=60,
    )

    assert second["owner"] == "worker-new"
    assert second["attempt"] == 2
    assert second["recovery_reason"] == "lease_expired"
    assert second["history"][-1]["owner"] == "worker-old"
    assert second["history"][-1]["attempt"] == 1


def test_database_store_rejects_same_owner_reuse_after_expiry(db_engine):
    store = _store(db_engine)
    claim = store.claim(
        scope_key="DEAL-001/phases/ic_task_leases.json",
        task_key="DEAL-001:R1:siq_ic_legal_scanner",
        owner="worker-reused",
        now=_iso(),
        lease_seconds=60,
    )

    with pytest.raises(ICTaskOwnerReuseError, match="fresh owner"):
        store.claim(
            scope_key="DEAL-001/phases/ic_task_leases.json",
            task_key=claim["task_key"],
            owner="worker-reused",
            now=_iso(2),
            lease_seconds=60,
        )


def test_database_store_fences_old_owner_heartbeat_and_terminal_update(db_engine):
    store = _store(db_engine)
    claim = store.claim(
        scope_key="DEAL-001/phases/ic_task_leases.json",
        task_key="DEAL-001:R1:siq_ic_strategist",
        owner="worker-old",
        now=_iso(),
        lease_seconds=60,
    )
    store.claim(
        scope_key="DEAL-001/phases/ic_task_leases.json",
        task_key=claim["task_key"],
        owner="worker-new",
        now=_iso(2),
        lease_seconds=60,
    )

    assert store.heartbeat(
        scope_key="DEAL-001/phases/ic_task_leases.json",
        task_key=claim["task_key"],
        owner="worker-old",
        now=_iso(2),
        lease_seconds=60,
    ) is None
    assert store.finish(
        scope_key="DEAL-001/phases/ic_task_leases.json",
        task_key=claim["task_key"],
        owner="worker-old",
        now=_iso(2),
        status="failed",
    ) is None
    finished = store.finish(
        scope_key="DEAL-001/phases/ic_task_leases.json",
        task_key=claim["task_key"],
        owner="worker-new",
        now=_iso(2),
        status="succeeded",
    )
    assert finished["status"] == "succeeded"
    assert store.load("DEAL-001/phases/ic_task_leases.json")[claim["task_key"]]["status"] == "succeeded"


def test_database_store_rejects_expired_owner_before_reclaim(db_engine):
    store = _store(db_engine)
    claim = store.claim(
        scope_key="DEAL-001/phases/ic_task_leases.json",
        task_key="DEAL-001:R1:siq_ic_strategist",
        owner="worker-expired",
        now=_iso(),
        lease_seconds=60,
    )

    assert store.heartbeat(
        scope_key="DEAL-001/phases/ic_task_leases.json",
        task_key=claim["task_key"],
        owner="worker-expired",
        now=_iso(2),
        lease_seconds=60,
    ) is None
    assert store.finish(
        scope_key="DEAL-001/phases/ic_task_leases.json",
        task_key=claim["task_key"],
        owner="worker-expired",
        now=_iso(2),
        status="succeeded",
    ) is None
    assert store.load("DEAL-001/phases/ic_task_leases.json")[claim["task_key"]]["status"] == "running"


@pytest.mark.parametrize(
    ("backend", "database_url", "message"),
    [
        ("file", "postgresql+psycopg://example/siq", "PostgreSQL backend"),
        ("postgres", "sqlite:///local.db", "PostgreSQL SIQ_APP_DATABASE_URL"),
    ],
)
def test_production_backend_fails_closed(monkeypatch, backend, database_url, message):
    monkeypatch.setenv("SIQ_DEPLOYMENT_PROFILE", "production")
    monkeypatch.setenv("SIQ_IC_TASK_LEASE_BACKEND", backend)
    monkeypatch.setattr(ic_task_lease, "_app_database_url", lambda: database_url)

    with pytest.raises(RuntimeError, match=message):
        ic_task_lease._selected_backend()


def test_local_backend_remains_file(monkeypatch):
    monkeypatch.setenv("SIQ_DEPLOYMENT_PROFILE", "local")
    monkeypatch.delenv("SIQ_IC_TASK_LEASE_BACKEND", raising=False)

    assert ic_task_lease._selected_backend() == "file"
