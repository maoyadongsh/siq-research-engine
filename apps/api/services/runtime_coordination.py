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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Field, Session, SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

_POOL_PRINCIPAL_MAX_BYTES = 512
_PROCESS_RUNTIME_OWNER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"


def _pool_principal(
    tenant_id: str | None,
    user_id: str | None,
) -> tuple[str, str] | None:
    """Validate an optional, all-or-nothing pool principal."""

    if tenant_id is None and user_id is None:
        return None
    if not isinstance(tenant_id, str) or not isinstance(user_id, str):
        raise ValueError("pool principal must be complete")
    if (
        not tenant_id
        or not user_id
        or tenant_id != tenant_id.strip()
        or user_id != user_id.strip()
        or len(tenant_id.encode("utf-8")) > _POOL_PRINCIPAL_MAX_BYTES
        or len(user_id.encode("utf-8")) > _POOL_PRINCIPAL_MAX_BYTES
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in tenant_id)
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in user_id)
    ):
        raise ValueError("pool principal is invalid")
    return tenant_id, user_id


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
    pool_lease_id: Optional[str] = Field(default=None, max_length=80)
    pool_scope_id: Optional[str] = Field(default=None, max_length=32)
    pool_binding_run_id: Optional[str] = Field(default=None, max_length=80)
    pool_owner_generation: Optional[int] = Field(default=None)
    pool_tenant_id: Optional[str] = Field(default=None, max_length=512)
    pool_user_id: Optional[str] = Field(default=None, max_length=512)
    lease_until: datetime = Field(index=True)
    created_at: datetime = Field(default_factory=utcnow_naive)
    updated_at: datetime = Field(default_factory=utcnow_naive)


class ActiveRunClaimed(RuntimeError):
    """Raised when another non-expired owner holds the session lease."""


@dataclass(frozen=True)
class ActiveRunLeaseSnapshot:
    profile: str
    session_id: str
    run_id: str
    owner_id: str
    pool_lease_id: str | None
    pool_scope_id: str | None
    pool_binding_run_id: str | None
    pool_owner_generation: int | None
    pool_tenant_id: str | None
    pool_user_id: str | None
    lease_until: datetime


def runtime_owner_id() -> str:
    configured = os.getenv("SIQ_RUNTIME_OWNER_ID", "").strip()
    if configured:
        return configured
    return _PROCESS_RUNTIME_OWNER_ID


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
    row.pool_lease_id = None
    row.pool_scope_id = None
    row.pool_binding_run_id = None
    row.pool_owner_generation = None
    row.pool_tenant_id = None
    row.pool_user_id = None
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


async def attach_active_run_pool_lease(
    session: AsyncSession,
    *,
    profile: str,
    session_id: str,
    provisional_run_id: str,
    owner_id: str,
    pool_lease_id: str,
    pool_scope_id: str,
    pool_binding_run_id: str,
    pool_owner_generation: int,
    pool_tenant_id: str | None = None,
    pool_user_id: str | None = None,
) -> bool:
    """Bind exact pool ownership to a provisional DB claim before run creation."""

    try:
        principal = _pool_principal(pool_tenant_id, pool_user_id)
    except ValueError:
        return False
    if (
        not pool_lease_id
        or not pool_scope_id
        or not pool_binding_run_id
        or isinstance(pool_owner_generation, bool)
        or pool_owner_generation < 1
    ):
        return False
    row = (
        await session.exec(
            select(ActiveRunLease)
            .where(
                ActiveRunLease.profile == profile,
                ActiveRunLease.session_id == session_id,
                ActiveRunLease.run_id == provisional_run_id,
                ActiveRunLease.owner_id == owner_id,
                ActiveRunLease.status == "running",
                ActiveRunLease.pool_lease_id.is_(None),
                ActiveRunLease.pool_scope_id.is_(None),
                ActiveRunLease.pool_binding_run_id.is_(None),
                ActiveRunLease.pool_owner_generation.is_(None),
                ActiveRunLease.pool_tenant_id.is_(None),
                ActiveRunLease.pool_user_id.is_(None),
            )
            .with_for_update()
        )
    ).first()
    if row is None:
        return False
    row.pool_lease_id = pool_lease_id
    row.pool_scope_id = pool_scope_id
    row.pool_binding_run_id = pool_binding_run_id
    row.pool_owner_generation = pool_owner_generation
    if principal is not None:
        row.pool_tenant_id, row.pool_user_id = principal
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


async def list_recoverable_active_runs(
    session: AsyncSession,
    *,
    profile: str,
) -> list[ActiveRunLeaseSnapshot]:
    """Return a detached snapshot of durable rows still marked as running."""

    rows = (
        await session.exec(
            select(ActiveRunLease)
            .where(
                ActiveRunLease.profile == profile,
                ActiveRunLease.status == "running",
            )
            .order_by(ActiveRunLease.id)
        )
    ).all()
    return [
        ActiveRunLeaseSnapshot(
            profile=row.profile,
            session_id=row.session_id,
            run_id=row.run_id,
            owner_id=row.owner_id,
            pool_lease_id=row.pool_lease_id,
            pool_scope_id=row.pool_scope_id,
            pool_binding_run_id=row.pool_binding_run_id,
            pool_owner_generation=row.pool_owner_generation,
            pool_tenant_id=row.pool_tenant_id,
            pool_user_id=row.pool_user_id,
            lease_until=row.lease_until,
        )
        for row in rows
    ]


async def takeover_active_run(
    session: AsyncSession,
    *,
    profile: str,
    session_id: str,
    run_id: str,
    expected_owner_id: str,
    expected_pool_lease_id: str,
    expected_pool_scope_id: str,
    expected_pool_binding_run_id: str,
    expected_pool_owner_generation: int,
    owner_id: str,
    pool_owner_generation: int,
    expected_pool_tenant_id: str | None = None,
    expected_pool_user_id: str | None = None,
    lease_seconds: int | None = None,
) -> bool:
    """CAS one pre-restart DB owner so competing recovery processes are fenced."""

    try:
        principal = _pool_principal(
            expected_pool_tenant_id,
            expected_pool_user_id,
        )
    except ValueError:
        return False
    if (
        not expected_owner_id
        or not owner_id
        or expected_owner_id == owner_id
        or not expected_pool_lease_id
        or not expected_pool_scope_id
        or not expected_pool_binding_run_id
        or isinstance(expected_pool_owner_generation, bool)
        or expected_pool_owner_generation < 1
        or isinstance(pool_owner_generation, bool)
        or pool_owner_generation <= expected_pool_owner_generation
    ):
        return False
    principal_conditions = (
        (
            ActiveRunLease.pool_tenant_id.is_(None),
            ActiveRunLease.pool_user_id.is_(None),
        )
        if principal is None
        else (
            ActiveRunLease.pool_tenant_id == principal[0],
            ActiveRunLease.pool_user_id == principal[1],
        )
    )
    row = (
        await session.exec(
            select(ActiveRunLease)
            .where(
                ActiveRunLease.profile == profile,
                ActiveRunLease.session_id == session_id,
                ActiveRunLease.run_id == run_id,
                ActiveRunLease.owner_id == expected_owner_id,
                ActiveRunLease.status == "running",
                ActiveRunLease.pool_lease_id == expected_pool_lease_id,
                ActiveRunLease.pool_scope_id == expected_pool_scope_id,
                ActiveRunLease.pool_binding_run_id == expected_pool_binding_run_id,
                ActiveRunLease.pool_owner_generation == expected_pool_owner_generation,
                *principal_conditions,
            )
            .with_for_update()
        )
    ).first()
    if row is None:
        return False
    now = utcnow_naive()
    row.owner_id = owner_id
    row.pool_owner_generation = pool_owner_generation
    row.lease_until = now + timedelta(seconds=lease_seconds or _lease_seconds())
    row.updated_at = now
    session.add(row)
    try:
        await session.commit()
    except (IntegrityError, OperationalError):
        await session.rollback()
        return False
    return True


__all__ = [
    "ActiveRunClaimed",
    "ActiveRunLease",
    "ActiveRunLeaseSnapshot",
    "attach_active_run_pool_lease",
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
    "list_recoverable_active_runs",
    "takeover_active_run",
]
