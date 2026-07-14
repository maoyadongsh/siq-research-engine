import concurrent.futures
from datetime import datetime, timedelta, timezone

import pytest
from services.ic_task_lease import (
    ICTaskAlreadyClaimedError,
    ICTaskOwnerReuseError,
    claim_ic_task,
    finish_ic_task,
    heartbeat_ic_task,
    load_ic_task_claims,
)


def _iso(minutes: int = 0) -> str:
    return (datetime(2026, 7, 12, 9, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes)).isoformat().replace(
        "+00:00", "Z"
    )


def test_ic_task_claim_allows_only_one_worker_for_same_task(tmp_path):
    store_path = tmp_path / "ic-task-leases.json"

    def claim(owner: str):
        try:
            return claim_ic_task(
                store_path,
                task_key="DEAL-001:R1:siq_ic_strategist",
                owner=owner,
                now=_iso(),
                lease_seconds=120,
            )
        except ICTaskAlreadyClaimedError:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ["worker-a", "worker-b"]))

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert winners[0]["attempt"] == 1
    assert winners[0]["status"] == "running"


def test_ic_task_expired_lease_can_be_reclaimed_with_auditable_attempt(tmp_path):
    store_path = tmp_path / "ic-task-leases.json"
    first = claim_ic_task(
        store_path,
        task_key="DEAL-001:R1:siq_ic_strategist",
        owner="worker-a",
        now=_iso(),
        lease_seconds=120,
    )

    second = claim_ic_task(
        store_path,
        task_key=first["task_key"],
        owner="worker-b",
        now=_iso(2),
        lease_seconds=60,
    )

    assert second["attempt"] == 2
    assert second["owner"] == "worker-b"
    assert second["recovery_reason"] == "lease_expired"
    assert second["history"][-1]["attempt"] == 1
    assert second["history"][-1]["owner"] == "worker-a"


def test_ic_task_expired_lease_rejects_reused_owner_capability(tmp_path):
    store_path = tmp_path / "ic-task-leases.json"
    first = claim_ic_task(
        store_path,
        task_key="DEAL-001:R1:siq_ic_legal_scanner",
        owner="worker-reused",
        now=_iso(),
        lease_seconds=60,
    )

    with pytest.raises(ICTaskOwnerReuseError, match="fresh owner"):
        claim_ic_task(
            store_path,
            task_key=first["task_key"],
            owner="worker-reused",
            now=_iso(2),
            lease_seconds=60,
        )


def test_ic_task_heartbeat_and_finish_require_current_owner(tmp_path):
    store_path = tmp_path / "ic-task-leases.json"
    claim = claim_ic_task(
        store_path,
        task_key="DEAL-001:R1:siq_ic_strategist",
        owner="worker-a",
        now=_iso(),
        lease_seconds=120,
    )

    assert heartbeat_ic_task(
        store_path,
        task_key=claim["task_key"],
        owner="stale-worker",
        now=_iso(1),
        lease_seconds=60,
    ) is None
    renewed = heartbeat_ic_task(
        store_path,
        task_key=claim["task_key"],
        owner="worker-a",
        now=_iso(1),
        lease_seconds=60,
    )
    assert renewed["heartbeat_at"] == _iso(1)
    assert renewed["lease_expires_at"] == _iso(2)

    assert finish_ic_task(
        store_path,
        task_key=claim["task_key"],
        owner="stale-worker",
        now=_iso(1),
        status="failed",
    ) is None
    finished = finish_ic_task(
        store_path,
        task_key=claim["task_key"],
        owner="worker-a",
        now=_iso(1),
        status="succeeded",
    )
    assert finished["status"] == "succeeded"
    assert finished["finished_at"] == _iso(1)
    assert finished["lease_expires_at"] is None
    assert load_ic_task_claims(store_path)[claim["task_key"]]["status"] == "succeeded"


def test_ic_task_expired_owner_cannot_renew_or_publish_before_reclaim(tmp_path):
    store_path = tmp_path / "ic-task-leases.json"
    claim = claim_ic_task(
        store_path,
        task_key="DEAL-001:R1:siq_ic_finance_auditor",
        owner="worker-expired",
        now=_iso(),
        lease_seconds=60,
    )

    assert heartbeat_ic_task(
        store_path,
        task_key=claim["task_key"],
        owner="worker-expired",
        now=_iso(2),
        lease_seconds=60,
    ) is None
    assert finish_ic_task(
        store_path,
        task_key=claim["task_key"],
        owner="worker-expired",
        now=_iso(2),
        status="succeeded",
    ) is None
    persisted = load_ic_task_claims(store_path)[claim["task_key"]]
    assert persisted["status"] == "running"
    assert persisted["heartbeat_at"] == _iso()


def test_ic_task_active_claim_reports_current_lease(tmp_path):
    store_path = tmp_path / "ic-task-leases.json"
    claim_ic_task(
        store_path,
        task_key="DEAL-001:R1:siq_ic_strategist",
        owner="worker-a",
        now=_iso(),
        lease_seconds=120,
    )

    with pytest.raises(ICTaskAlreadyClaimedError) as exc_info:
        claim_ic_task(
            store_path,
            task_key="DEAL-001:R1:siq_ic_strategist",
            owner="worker-b",
            now=_iso(1),
            lease_seconds=120,
        )

    assert exc_info.value.claim["owner"] == "worker-a"
    assert exc_info.value.claim["attempt"] == 1
