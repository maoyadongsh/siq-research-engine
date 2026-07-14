from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier

import pytest
from services.durable_job_service import (
    DurableBackgroundJob,
    DurableJobCoordinator,
    DurableJobService,
    utcnow_naive,
)
from sqlmodel import Session, SQLModel, create_engine, select

from services import job_service


@pytest.fixture()
def db_engine(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'durable-jobs.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def _coordinator(engine) -> DurableJobCoordinator:
    return DurableJobCoordinator(session_factory=lambda: Session(engine), lease_seconds=60)


def test_two_independent_sessions_can_only_claim_a_queued_job_once(db_engine):
    coordinator = _coordinator(db_engine)
    created = coordinator.create_job(job_id="job-1", kind="demo", created_by={"user_id": 7})

    assert created["status"] == "queued"
    barrier = Barrier(2)

    def compete(owner: str):
        barrier.wait(timeout=2)
        return coordinator.claim("job-1", owner=owner)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(compete, ("worker-a", "worker-b")))

    assert sum(claim is not None for claim in claims) == 1
    winning_owner = next(claim["owner"] for claim in claims if claim is not None)

    with Session(db_engine) as first, Session(db_engine) as second:
        first_row = first.exec(select(DurableBackgroundJob).where(DurableBackgroundJob.job_id == "job-1")).one()
        second_row = second.exec(select(DurableBackgroundJob).where(DurableBackgroundJob.job_id == "job-1")).one()
        assert first_row.owner == second_row.owner == winning_owner
        assert first_row.attempt == second_row.attempt == 1


def test_expired_running_job_can_be_reclaimed_and_attempt_is_audited(db_engine):
    coordinator = _coordinator(db_engine)
    coordinator.create_job(job_id="job-expired", kind="demo")
    assert coordinator.claim("job-expired", owner="worker-old") is not None

    with Session(db_engine) as session:
        row = session.exec(
            select(DurableBackgroundJob).where(DurableBackgroundJob.job_id == "job-expired")
        ).one()
        row.lease_until = utcnow_naive() - timedelta(seconds=1)
        session.add(row)
        session.commit()

    reclaimed = coordinator.claim("job-expired", owner="worker-new")

    assert reclaimed is not None
    assert reclaimed["status"] == "running"
    assert reclaimed["owner"] == "worker-new"
    assert reclaimed["attempt"] == 2
    assert reclaimed["interrupted_reason"] == "lease_expired_reclaimed"


def test_old_owner_cannot_heartbeat_or_publish_terminal_state_after_reclaim(db_engine):
    coordinator = _coordinator(db_engine)
    coordinator.create_job(job_id="job-fenced", kind="demo")
    old_claim = coordinator.claim("job-fenced", owner="worker-old")
    assert old_claim is not None

    with Session(db_engine) as session:
        row = session.exec(
            select(DurableBackgroundJob).where(DurableBackgroundJob.job_id == "job-fenced")
        ).one()
        row.lease_until = utcnow_naive() - timedelta(seconds=1)
        session.add(row)
        session.commit()

    new_claim = coordinator.claim("job-fenced", owner="worker-new")
    assert new_claim is not None

    assert coordinator.heartbeat(
        "job-fenced",
        owner="worker-old",
        attempt=old_claim["attempt"],
    ) is False
    assert coordinator.finish(
        "job-fenced",
        owner="worker-old",
        attempt=old_claim["attempt"],
        status="succeeded",
        result={"ok": True, "source": "stale"},
    ) is False
    assert coordinator.finish(
        "job-fenced",
        owner="worker-new",
        attempt=new_claim["attempt"],
        status="succeeded",
        result={"ok": True, "source": "current"},
    ) is True

    snapshot = coordinator.get("job-fenced")
    assert snapshot["status"] == "succeeded"
    assert snapshot["result"] == {"ok": True, "source": "current"}


def test_reused_owner_id_is_fenced_by_attempt_after_reclaim(db_engine):
    coordinator = _coordinator(db_engine)
    coordinator.create_job(job_id="job-attempt-fenced", kind="demo")
    first = coordinator.claim("job-attempt-fenced", owner="stable-worker-name")
    assert first is not None

    with Session(db_engine) as session:
        row = session.get(DurableBackgroundJob, "job-attempt-fenced")
        assert row is not None
        row.lease_until = utcnow_naive() - timedelta(seconds=1)
        session.add(row)
        session.commit()

    second = coordinator.claim("job-attempt-fenced", owner="stable-worker-name")
    assert second is not None
    assert second["attempt"] == first["attempt"] + 1
    assert coordinator.finish(
        "job-attempt-fenced",
        owner="stable-worker-name",
        attempt=first["attempt"],
        status="succeeded",
    ) is False
    assert coordinator.finish(
        "job-attempt-fenced",
        owner="stable-worker-name",
        attempt=second["attempt"],
        status="succeeded",
    ) is True


def test_completion_crash_before_terminal_publish_recovers_as_interrupted(db_engine):
    coordinator = _coordinator(db_engine)
    coordinator.create_job(job_id="job-before-terminal-write", kind="demo")
    claim = coordinator.claim("job-before-terminal-write", owner="worker-dead-after-result")
    assert claim is not None

    with Session(db_engine) as session:
        row = session.get(DurableBackgroundJob, "job-before-terminal-write")
        assert row is not None
        row.lease_until = utcnow_naive() - timedelta(seconds=1)
        session.add(row)
        session.commit()

    assert coordinator.finish(
        "job-before-terminal-write",
        owner="worker-dead-after-result",
        attempt=claim["attempt"],
        status="succeeded",
        result={"ok": True},
    ) is False
    assert coordinator.recover_expired_active_jobs() == 1
    recovered = coordinator.get("job-before-terminal-write")
    assert recovered is not None
    assert recovered["status"] == "interrupted"
    assert recovered["result"] is None
    assert recovered["interrupted_reason"] == "process_restart_lease_expired"


@pytest.mark.parametrize("status", ["queued", "running"])
def test_restart_recovery_marks_expired_active_jobs_interrupted_and_auditable(db_engine, status):
    coordinator = _coordinator(db_engine)
    coordinator.create_job(job_id=f"job-{status}", kind="demo")
    if status == "running":
        coordinator.claim(f"job-{status}", owner="worker-dead")

    with Session(db_engine) as session:
        row = session.exec(
            select(DurableBackgroundJob).where(DurableBackgroundJob.job_id == f"job-{status}")
        ).one()
        row.lease_until = utcnow_naive() - timedelta(seconds=1)
        session.add(row)
        session.commit()

    recovered = coordinator.recover_expired_active_jobs()
    snapshot = coordinator.get(f"job-{status}")

    assert recovered == 1
    assert snapshot["status"] == "interrupted"
    assert snapshot["finished_at"]
    assert snapshot["interrupted_reason"] == "process_restart_lease_expired"
    assert snapshot["owner"] == ("worker-dead" if status == "running" else None)


def test_production_factory_fails_closed_without_postgresql(monkeypatch, tmp_path):
    monkeypatch.setenv("SIQ_DEPLOYMENT_PROFILE", "production")
    monkeypatch.delenv("SIQ_BACKGROUND_JOB_BACKEND", raising=False)
    monkeypatch.setattr(job_service, "_app_database_url", lambda: "sqlite:///local.db")

    with pytest.raises(RuntimeError, match="PostgreSQL"):
        job_service.create_job_service(store_path=tmp_path / "jobs.json")


def test_local_factory_keeps_file_backed_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("SIQ_DEPLOYMENT_PROFILE", "local")
    monkeypatch.delenv("SIQ_BACKGROUND_JOB_BACKEND", raising=False)

    service = job_service.create_job_service(store_path=tmp_path / "jobs.json")

    assert isinstance(service, job_service.FileBackedJobService)


def test_two_service_instances_read_database_authoritative_terminal_result(db_engine, tmp_path):
    first = DurableJobService(
        coordinator=_coordinator(db_engine),
        owner="worker-first",
        heartbeat_seconds=1,
    )
    second = DurableJobService(
        coordinator=_coordinator(db_engine),
        owner="worker-second",
        heartbeat_seconds=1,
    )
    artifact = tmp_path / "report.json"

    started = first.start(
        "market-package-build",
        lambda: {"ok": True, "artifact_path": artifact},
        created_by={"user_id": 9},
    )
    deadline = time.monotonic() + 2
    snapshot = None
    while time.monotonic() < deadline:
        snapshot = second.get(started["job_id"])
        if snapshot and snapshot["status"] == "succeeded":
            break
        time.sleep(0.01)

    assert snapshot is not None
    assert snapshot["status"] == "succeeded"
    assert snapshot["result"] == {"ok": True, "artifact_path": str(artifact)}
    assert snapshot["artifact_refs"] == [str(artifact)]
    assert snapshot["created_by"] == {"user_id": 9}

    with Session(db_engine) as session:
        row = session.exec(
            select(DurableBackgroundJob).where(DurableBackgroundJob.job_id == started["job_id"])
        ).one()
        assert not hasattr(row, "target")


def test_service_status_poll_recovers_lease_that_expires_after_startup(db_engine):
    coordinator = _coordinator(db_engine)
    service = DurableJobService(coordinator=coordinator, owner="worker-live")
    service._ensure_recovered()
    coordinator.create_job(job_id="job-late-expiry", kind="demo")
    coordinator.claim("job-late-expiry", owner="worker-live")

    with Session(db_engine) as session:
        row = session.exec(
            select(DurableBackgroundJob).where(DurableBackgroundJob.job_id == "job-late-expiry")
        ).one()
        row.lease_until = utcnow_naive() - timedelta(seconds=1)
        session.add(row)
        session.commit()

    snapshot = service.get("job-late-expiry")

    assert snapshot["status"] == "interrupted"
    assert snapshot["interrupted_reason"] == "lease_expired_without_terminal_update"
    assert snapshot["finished_at"]
