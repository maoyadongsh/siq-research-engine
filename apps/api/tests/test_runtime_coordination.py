from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from services.runtime_coordination import (
    ActiveRunLease,
    bind_active_run,
    claim_active_run,
    release_active_run,
    renew_active_run,
)
from services.usage_service import (
    QuotaLedger,
    QuotaReservation,
    reconcile_expired_reservations_async,
    record_usage_async,
    release_quota_async,
    reserve_quota_async,
    usage_response_payload_async,
    utcnow_naive,
)
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession


@pytest.mark.asyncio
async def test_active_run_claim_is_single_owner_and_old_release_cannot_clear_new(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'coordination.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)

    async def claim(run_id: str, owner_id: str) -> bool:
        async with AsyncSession(engine) as session:
            return await claim_active_run(
                session,
                profile="siq_assistant",
                session_id="session-1",
                run_id=run_id,
                owner_id=owner_id,
                lease_seconds=300,
            )

    results = await asyncio.gather(claim("run-a", "owner-a"), claim("run-b", "owner-b"))
    assert sum(results) == 1

    async with AsyncSession(engine) as session:
        row = await session.get(ActiveRunLease, 1)
        assert row is not None
        active_run = row.run_id
        active_owner = row.owner_id
        assert await release_active_run(
            session,
            profile="siq_assistant",
            session_id="session-1",
            run_id="stale-run",
            owner_id="stale-owner",
            status="cancelled",
        ) is False
        assert await release_active_run(
            session,
            profile="siq_assistant",
            session_id="session-1",
            run_id=active_run,
            owner_id=active_owner,
            status="cancelled",
        ) is True

    await engine.dispose()


@pytest.mark.asyncio
async def test_multiple_pending_reservations_are_consumed_and_direct_usage_syncs_ledger(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'quota-consume.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    monkeypatch.setattr("services.usage_service._quota_limit_for_user", lambda *_args: 4)
    async with AsyncSession(engine) as session:
        await reserve_quota_async(session, user_id=9, user_role="user", event_type="agent_question")
        await reserve_quota_async(session, user_id=9, user_role="user", event_type="agent_question")
        await record_usage_async(session, user_id=9, event_type="agent_question")
        await record_usage_async(session, user_id=9, event_type="agent_question")
        await record_usage_async(session, user_id=9, event_type="agent_question")
        ledger = await session.get(QuotaLedger, 1)
        assert ledger is not None
        assert ledger.used_count == 3
        assert ledger.reserved_count == 0
        reservations = (await session.exec(select(QuotaReservation))).all()
        assert [item.status for item in reservations] == ["consumed", "consumed"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_provisional_claim_blocks_second_start_then_binds_real_run(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'provisional.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    async with AsyncSession(engine) as first, AsyncSession(engine) as second:
        assert await claim_active_run(
            first,
            profile="siq_assistant",
            session_id="session-provisional",
            run_id="claim-1",
            owner_id="owner-1",
        ) is True
        assert await claim_active_run(
            second,
            profile="siq_assistant",
            session_id="session-provisional",
            run_id="claim-2",
            owner_id="owner-2",
        ) is False
        assert await bind_active_run(
            first,
            profile="siq_assistant",
            session_id="session-provisional",
            provisional_run_id="claim-1",
            run_id="hermes-run-1",
            owner_id="owner-1",
        ) is True
        assert await release_active_run(
            second,
            profile="siq_assistant",
            session_id="session-provisional",
            run_id="claim-1",
            owner_id="owner-1",
        ) is False
        assert await renew_active_run(
            first,
            profile="siq_assistant",
            session_id="session-provisional",
            run_id="hermes-run-1",
            owner_id="stale-owner",
        ) is False
    await engine.dispose()


@pytest.mark.asyncio
async def test_quota_reservation_can_be_released_and_expired_reservations_reconciled(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'quota-release.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    monkeypatch.setattr("services.usage_service._quota_limit_for_user", lambda *_args: 1)

    async with AsyncSession(engine) as session:
        _, _, reservation_id = await reserve_quota_async(
            session,
            user_id=8,
            user_role="user",
            event_type="agent_question",
        )
        assert reservation_id
        assert await release_quota_async(session, reservation_id) is True

        _, _, expired_id = await reserve_quota_async(
            session,
            user_id=8,
            user_role="user",
            event_type="agent_question",
        )
        assert expired_id
        reservation = await session.get(QuotaReservation, expired_id)
        assert reservation is not None
        reservation.expires_at = utcnow_naive() - timedelta(seconds=1)
        session.add(reservation)
        await session.commit()
        payload = await usage_response_payload_async(
            session,
            user_id=8,
            user_role="user",
            event_type="agent_question",
        )
        assert payload["reserved"] == 0
        assert await reconcile_expired_reservations_async(session) == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_quota_reservation_allows_only_one_concurrent_request_with_balance_one(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'quota.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    monkeypatch.setattr("services.usage_service._quota_limit_for_user", lambda *_args: 1)

    async def reserve() -> tuple[int, int, str | None]:
        async with AsyncSession(engine) as session:
            return await reserve_quota_async(
                session,
                user_id=7,
                user_role="user",
                event_type="agent_question",
            )

    results = await asyncio.gather(reserve(), reserve(), return_exceptions=True)
    successes = [item for item in results if not isinstance(item, Exception)]
    failures = [item for item in results if isinstance(item, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    async with AsyncSession(engine) as session:
        reservations = (await session.exec(QuotaReservation.__table__.select())).all()
        assert len(reservations) == 1
        assert reservations[0].status == "reserved"
        ledger = await session.get(QuotaLedger, 1)
        assert ledger is not None
        assert ledger.reserved_count == 1
    await engine.dispose()
