from __future__ import annotations

import asyncio
import os
import uuid
from datetime import timedelta

import pytest
from services.usage_service import (
    QuotaLedger,
    QuotaReservation,
    reconcile_expired_reservations_async,
    release_quota_async,
    utcnow_naive,
)
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession


def _sync_postgres_url(value: str) -> str:
    return value.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1).replace(
        "postgresql://", "postgresql+psycopg://", 1
    )


def _async_postgres_url(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1).replace(
        "postgresql://", "postgresql+asyncpg://", 1
    )


@pytest.mark.asyncio
async def test_postgres_release_and_reconcile_use_single_conditional_transition():
    database_url = os.getenv("SIQ_TEST_POSTGRES_URL", "").strip()
    if not database_url:
        pytest.skip("SIQ_TEST_POSTGRES_URL is not configured")

    schema = f"siq_quota_atomicity_{uuid.uuid4().hex}"
    setup_engine = create_engine(_sync_postgres_url(database_url))
    async_engine = None
    try:
        with setup_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            QuotaLedger.__table__.create(connection)
            QuotaReservation.__table__.create(connection)

        async_engine = create_async_engine(
            _async_postgres_url(database_url),
            connect_args={"server_settings": {"search_path": schema}},
        )
        now = utcnow_naive()
        async with AsyncSession(async_engine) as session:
            session.add(
                QuotaLedger(
                    id=1,
                    user_id=77,
                    event_type="agent_question",
                    event_date="2026-07-14",
                    reserved_count=1,
                )
            )
            session.add(
                QuotaReservation(
                    id="release-race",
                    user_id=77,
                    event_type="agent_question",
                    event_date="2026-07-14",
                    amount=1,
                    expires_at=now + timedelta(minutes=5),
                )
            )
            await session.commit()

        async def release() -> bool:
            async with AsyncSession(async_engine) as session:
                return await release_quota_async(session, "release-race")

        release_results = await asyncio.gather(release(), release())
        assert sorted(release_results) == [False, True]

        async with AsyncSession(async_engine) as session:
            ledger = await session.get(QuotaLedger, 1)
            released = await session.get(QuotaReservation, "release-race")
            assert ledger is not None and ledger.reserved_count == 0
            assert released is not None and released.status == "released"

            ledger.reserved_count = 1
            session.add(ledger)
            session.add(
                QuotaReservation(
                    id="reconcile-race",
                    user_id=77,
                    event_type="agent_question",
                    event_date="2026-07-14",
                    amount=1,
                    expires_at=now - timedelta(seconds=1),
                )
            )
            await session.commit()

        async def reconcile() -> int:
            async with AsyncSession(async_engine) as session:
                return await reconcile_expired_reservations_async(session, now=now)

        reconcile_results = await asyncio.gather(reconcile(), reconcile())
        assert sum(reconcile_results) == 1

        async with AsyncSession(async_engine) as session:
            ledger = await session.get(QuotaLedger, 1)
            reconciled = await session.get(QuotaReservation, "reconcile-race")
            assert ledger is not None and ledger.reserved_count == 0
            assert reconciled is not None and reconciled.status == "released"
    finally:
        if async_engine is not None:
            await async_engine.dispose()
        with setup_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        setup_engine.dispose()
