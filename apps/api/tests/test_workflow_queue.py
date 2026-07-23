from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier

import pytest
from services.workflow_queue import (
    EXHAUSTED_REASON,
    WorkflowLeaseLostError,
    WorkflowQueueCoordinator,
    WorkflowQueueJob,
    utcnow_naive,
)
from sqlmodel import Session, create_engine


@pytest.fixture()
def queue_engine(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'workflow-queue.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    WorkflowQueueJob.__table__.create(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def _coordinator(engine, *, lease_seconds: int = 60, max_attempts: int = 3):
    return WorkflowQueueCoordinator(
        session_factory=lambda: Session(engine),
        lease_seconds=lease_seconds,
        max_attempts=max_attempts,
    )


def _snapshot(job_id: str = "job-1", key: str = "key-1") -> dict:
    return {
        "jobId": job_id,
        "taskId": "task-1",
        "retryScope": "semantic",
        "idempotencyKey": key,
        "status": "queued",
        "steps": [],
    }


def test_enqueue_reuses_one_active_idempotency_key(queue_engine):
    queue = _coordinator(queue_engine)
    first, reused = queue.enqueue(snapshot=_snapshot())
    duplicate, duplicate_reused = queue.enqueue(snapshot=_snapshot("job-2"))

    assert reused is False
    assert duplicate_reused is True
    assert duplicate["jobId"] == first["jobId"] == "job-1"


def test_competing_workers_claim_a_queued_job_once(queue_engine):
    queue = _coordinator(queue_engine)
    queue.enqueue(snapshot=_snapshot())
    barrier = Barrier(2)

    def claim(owner: str):
        barrier.wait(timeout=2)
        return queue.claim_next(owner=owner)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, ("worker-a", "worker-b")))

    assert sum(item is not None for item in claims) == 1
    winner = next(item for item in claims if item is not None)
    assert winner["attempt"] == 1
    assert winner["ownerId"] in {"worker-a", "worker-b"}


def test_expired_attempt_is_reclaimed_and_old_worker_is_fenced(queue_engine):
    queue = _coordinator(queue_engine)
    queue.enqueue(snapshot=_snapshot())
    old = queue.claim_next(owner="worker-old")
    assert old is not None

    with Session(queue_engine) as session:
        row = session.get(WorkflowQueueJob, "job-1")
        assert row is not None
        row.lease_until = utcnow_naive() - timedelta(seconds=1)
        session.add(row)
        session.commit()

    current = queue.claim_next(owner="worker-new")
    assert current is not None
    assert current["attempt"] == 2
    assert current["interruptedReason"] == "lease_expired_reclaimed"

    assert queue.heartbeat("job-1", owner="worker-old", attempt=1) is False
    assert queue.finish(
        "job-1",
        owner="worker-old",
        attempt=1,
        status="succeeded",
        snapshot={**old, "status": "succeeded"},
    ) is False
    assert queue.finish(
        "job-1",
        owner="worker-new",
        attempt=2,
        status="succeeded",
        snapshot={**current, "status": "succeeded"},
        result={"ok": True},
    ) is True
    assert queue.get("job-1")["result"] == {"ok": True}


def test_snapshot_updates_require_current_lease(queue_engine):
    queue = _coordinator(queue_engine)
    queue.enqueue(snapshot=_snapshot())
    claim = queue.claim_next(owner="worker-a")
    assert claim is not None

    updated = queue.mutate_snapshot(
        "job-1",
        owner="worker-a",
        attempt=1,
        mutate=lambda snapshot: snapshot.update({"currentStep": "semantic"}),
    )
    assert updated["currentStep"] == "semantic"

    with pytest.raises(WorkflowLeaseLostError):
        queue.mutate_snapshot(
            "job-1",
            owner="worker-b",
            attempt=1,
            mutate=lambda snapshot: snapshot.update({"currentStep": "forged"}),
        )


def test_expired_final_attempt_becomes_interrupted(queue_engine):
    queue = _coordinator(queue_engine, max_attempts=1)
    queue.enqueue(snapshot=_snapshot(), max_attempts=1)
    assert queue.claim_next(owner="worker-only") is not None
    with Session(queue_engine) as session:
        row = session.get(WorkflowQueueJob, "job-1")
        assert row is not None
        row.lease_until = utcnow_naive() - timedelta(seconds=1)
        session.add(row)
        session.commit()

    assert queue.claim_next(owner="worker-late") is None
    snapshot = queue.get("job-1")
    assert snapshot["status"] == "interrupted"
    assert snapshot["interruptedReason"] == EXHAUSTED_REASON
    assert snapshot["attempt"] == snapshot["maxAttempts"] == 1
