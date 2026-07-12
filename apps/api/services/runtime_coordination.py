"""Durable coordination primitives for agent runs.

The in-process ``ACTIVE_RUNS`` map remains the fast event-stream cache.  This
module is the ownership authority: a unique profile/session row is claimed in
the application database and every release is conditional on the run and
owner that acquired it.  SQLite is deliberately supported for local/dev
single-process use; production PostgreSQL uses row locks and the same schema.
"""

from __future__ import annotations

import os
import socket
import uuid
from datetime import UTC, datetime, timedelta
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Field, Session, SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ActiveRunLease(SQLModel, table=True):
    __tablename__ = "active_run_leases"
    __table_args__ = (UniqueConstraint("profile", "session_id", name="uq_active_run_profile_session"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    profile: str = Field(max_length=80, index=True)
    session_id: str = Field(max_length=255, index=True)
    run_id: str = Field(max_length=255, unique=True, index=True)
    owner_id: str = Field(max_length=255)
    status: str = Field(default="running", max_length=20, index=True)
    lease_until: datetime = Field(index=True)
    created_at: datetime = Field(default_factory=utcnow_naive)
    updated_at: datetime = Field(default_factory=utcnow_naive)


class ActiveRunClaimed(RuntimeError):
    """Raised when another non-expired owner holds the session lease."""


def runtime_owner_id() -> str:
    configured = os.getenv("SIQ_RUNTIME_OWNER_ID", "").strip()
    if configured:
        return configured
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"


def _lease_seconds() -> int:
    try:
        # The default exceeds the API's 30-minute Hermes deadline. A renewal
        # loop can be added later, but expiry must never permit overlap during
        # the current bounded request lifetime.
        return max(30, min(int(os.getenv("SIQ_ACTIVE_RUN_LEASE_SECONDS", "3600")), 86_400))
    except ValueError:
        return 3600


def lease_seconds() -> int:
    """Public lease duration for runtime heartbeat scheduling."""
    return _lease_seconds()


def _is_live(row: ActiveRunLease, now: datetime) -> bool:
    return row.status == "running" and row.lease_until > now


def _claim_row(row: ActiveRunLease, *, run_id: str, owner_id: str, expires: datetime, now: datetime) -> None:
    row.run_id = run_id
    row.owner_id = owner_id
    row.status = "running"
    row.lease_until = expires
    row.updated_at = now


def claim_active_run_sync(
    session: Session,
    *,
    profile: str,
    session_id: str,
    run_id: str,
    owner_id: str,
    lease_seconds: int | None = None,
) -> bool:
    now = utcnow_naive()
    expires = now + timedelta(seconds=lease_seconds or _lease_seconds())
    statement = select(ActiveRunLease).where(
        ActiveRunLease.profile == profile,
        ActiveRunLease.session_id == session_id,
    ).with_for_update()
    row = session.exec(statement).first()
    if row and _is_live(row, now):
        return False
    if row:
        _claim_row(row, run_id=run_id, owner_id=owner_id, expires=expires, now=now)
        session.add(row)
    else:
        session.add(
            ActiveRunLease(
                profile=profile,
                session_id=session_id,
                run_id=run_id,
                owner_id=owner_id,
                lease_until=expires,
                created_at=now,
                updated_at=now,
            )
        )
    try:
        session.commit()
    except (IntegrityError, OperationalError):
        session.rollback()
        return False
    return True


async def claim_active_run(
    session: AsyncSession,
    *,
    profile: str,
    session_id: str,
    run_id: str,
    owner_id: str,
    lease_seconds: int | None = None,
) -> bool:
    now = utcnow_naive()
    expires = now + timedelta(seconds=lease_seconds or _lease_seconds())
    statement = select(ActiveRunLease).where(
        ActiveRunLease.profile == profile,
        ActiveRunLease.session_id == session_id,
    ).with_for_update()
    row = (await session.exec(statement)).first()
    if row and _is_live(row, now):
        return False
    if row:
        _claim_row(row, run_id=run_id, owner_id=owner_id, expires=expires, now=now)
        session.add(row)
    else:
        session.add(
            ActiveRunLease(
                profile=profile,
                session_id=session_id,
                run_id=run_id,
                owner_id=owner_id,
                lease_until=expires,
                created_at=now,
                updated_at=now,
            )
        )
    try:
        await session.commit()
    except (IntegrityError, OperationalError):
        await session.rollback()
        return False
    return True


def bind_active_run_sync(session: Session, *, profile: str, session_id: str, provisional_run_id: str, run_id: str, owner_id: str) -> bool:
    row = session.exec(
        select(ActiveRunLease).where(
            ActiveRunLease.profile == profile,
            ActiveRunLease.session_id == session_id,
            ActiveRunLease.run_id == provisional_run_id,
            ActiveRunLease.owner_id == owner_id,
            ActiveRunLease.status == "running",
        ).with_for_update()
    ).first()
    if not row:
        return False
    row.run_id = run_id
    row.updated_at = utcnow_naive()
    session.add(row)
    try:
        session.commit()
    except (IntegrityError, OperationalError):
        session.rollback()
        return False
    return True


async def bind_active_run(session: AsyncSession, *, profile: str, session_id: str, provisional_run_id: str, run_id: str, owner_id: str) -> bool:
    row = (await session.exec(
        select(ActiveRunLease).where(
            ActiveRunLease.profile == profile,
            ActiveRunLease.session_id == session_id,
            ActiveRunLease.run_id == provisional_run_id,
            ActiveRunLease.owner_id == owner_id,
            ActiveRunLease.status == "running",
        ).with_for_update()
    )).first()
    if not row:
        return False
    row.run_id = run_id
    row.updated_at = utcnow_naive()
    session.add(row)
    try:
        await session.commit()
    except (IntegrityError, OperationalError):
        await session.rollback()
        return False
    return True


def release_active_run_sync(session: Session, *, profile: str, session_id: str, run_id: str, owner_id: str, status: str = "completed") -> bool:
    row = session.exec(
        select(ActiveRunLease).where(
            ActiveRunLease.profile == profile,
            ActiveRunLease.session_id == session_id,
            ActiveRunLease.run_id == run_id,
            ActiveRunLease.owner_id == owner_id,
            ActiveRunLease.status == "running",
        ).with_for_update()
    ).first()
    if not row:
        return False
    row.status = status
    row.lease_until = utcnow_naive()
    row.updated_at = utcnow_naive()
    session.add(row)
    session.commit()
    return True


async def release_active_run(session: AsyncSession, *, profile: str, session_id: str, run_id: str, owner_id: str, status: str = "completed") -> bool:
    row = (await session.exec(
        select(ActiveRunLease).where(
            ActiveRunLease.profile == profile,
            ActiveRunLease.session_id == session_id,
            ActiveRunLease.run_id == run_id,
            ActiveRunLease.owner_id == owner_id,
            ActiveRunLease.status == "running",
        ).with_for_update()
    )).first()
    if not row:
        return False
    row.status = status
    row.lease_until = utcnow_naive()
    row.updated_at = utcnow_naive()
    session.add(row)
    await session.commit()
    return True


def renew_active_run_sync(session: Session, *, profile: str, session_id: str, run_id: str, owner_id: str, lease_seconds: int | None = None) -> bool:
    row = session.exec(select(ActiveRunLease).where(
        ActiveRunLease.profile == profile, ActiveRunLease.session_id == session_id,
        ActiveRunLease.run_id == run_id, ActiveRunLease.owner_id == owner_id,
        ActiveRunLease.status == "running",
    ).with_for_update()).first()
    if not row:
        return False
    now = utcnow_naive()
    row.lease_until = now + timedelta(seconds=lease_seconds or _lease_seconds())
    row.updated_at = now
    session.add(row)
    session.commit()
    return True


async def renew_active_run(session: AsyncSession, *, profile: str, session_id: str, run_id: str, owner_id: str, lease_seconds: int | None = None) -> bool:
    row = (await session.exec(select(ActiveRunLease).where(
        ActiveRunLease.profile == profile, ActiveRunLease.session_id == session_id,
        ActiveRunLease.run_id == run_id, ActiveRunLease.owner_id == owner_id,
        ActiveRunLease.status == "running",
    ).with_for_update())).first()
    if not row:
        return False
    now = utcnow_naive()
    row.lease_until = now + timedelta(seconds=lease_seconds or _lease_seconds())
    row.updated_at = now
    session.add(row)
    await session.commit()
    return True


__all__ = [
    "ActiveRunClaimed",
    "ActiveRunLease",
    "bind_active_run",
    "bind_active_run_sync",
    "claim_active_run",
    "claim_active_run_sync",
    "release_active_run",
    "release_active_run_sync",
    "renew_active_run",
    "renew_active_run_sync",
    "runtime_owner_id",
    "lease_seconds",
]
