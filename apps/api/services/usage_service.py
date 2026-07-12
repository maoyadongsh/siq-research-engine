from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import Index, UniqueConstraint, func
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Field, Session, SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class UsageEvent(SQLModel, table=True):
    __tablename__ = "usage_events"
    __table_args__ = (
        Index(
            "idx_usage_events_user_type_date",
            "user_id",
            "event_type",
            "event_date",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    event_type: str = Field(max_length=40, index=True)
    event_date: str = Field(max_length=10, index=True)
    count: int = Field(default=1)
    source: Optional[str] = Field(default=None, max_length=80)
    metadata_json: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow_naive)


class QuotaLedger(SQLModel, table=True):
    """Per-user/day counter used for atomic quota reservations."""

    __tablename__ = "quota_ledgers"
    __table_args__ = (
        UniqueConstraint("user_id", "event_type", "event_date", name="uq_quota_ledger_user_event_day"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    event_type: str = Field(max_length=40, index=True)
    event_date: str = Field(max_length=10, index=True)
    used_count: int = Field(default=0)
    reserved_count: int = Field(default=0)
    updated_at: datetime = Field(default_factory=utcnow_naive)


class QuotaReservation(SQLModel, table=True):
    __tablename__ = "quota_reservations"

    id: str = Field(primary_key=True, max_length=80)
    user_id: int = Field(index=True)
    event_type: str = Field(max_length=40, index=True)
    event_date: str = Field(max_length=10, index=True)
    amount: int = Field(default=1)
    status: str = Field(default="reserved", max_length=20, index=True)
    run_id: Optional[str] = Field(default=None, max_length=255, index=True)
    created_at: datetime = Field(default_factory=utcnow_naive)
    updated_at: datetime = Field(default_factory=utcnow_naive)
    expires_at: datetime = Field(default_factory=lambda: utcnow_naive() + timedelta(minutes=15), index=True)


class UserArtifact(SQLModel, table=True):
    __tablename__ = "user_artifacts"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    artifact_type: str = Field(max_length=40, index=True)
    artifact_key: str = Field(max_length=255, index=True)
    title: str = Field(max_length=255)
    path: str = Field(max_length=500)
    source: Optional[str] = Field(default=None, max_length=80)
    global_artifact_id: Optional[str] = Field(default=None, max_length=255, index=True)
    created_at: datetime = Field(default_factory=utcnow_naive)


class WorkspaceProject(SQLModel, table=True):
    __tablename__ = "workspace_projects"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    name: str = Field(max_length=255)
    company_code: Optional[str] = Field(default=None, max_length=50, index=True)
    company_name: Optional[str] = Field(default=None, max_length=255)
    status: str = Field(default="active", max_length=20, index=True)
    created_at: datetime = Field(default_factory=utcnow_naive)
    updated_at: datetime = Field(default_factory=utcnow_naive)


class WorkspaceProjectLink(SQLModel, table=True):
    __tablename__ = "workspace_project_links"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    project_id: int = Field(index=True)
    artifact_type: str = Field(max_length=40, index=True)
    artifact_key: str = Field(max_length=255, index=True)
    created_at: datetime = Field(default_factory=utcnow_naive)


AGENT_QUESTION_EVENT = "agent_question"
PARSE_EVENT = "parse_job"
DOCUMENT_PARSE_EVENT = "document_parse"


def _now_shanghai() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def current_day_key() -> str:
    return _now_shanghai().strftime("%Y-%m-%d")


def next_midnight_shanghai() -> datetime:
    now = _now_shanghai()
    tomorrow = (now + timedelta(days=1)).date()
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=SHANGHAI_TZ)


def _quota_limit_for_user(user_role: str, event_type: str) -> Optional[int]:
    if user_role in {"admin", "super_admin"}:
        return None
    if event_type == AGENT_QUESTION_EVENT:
        return 20
    if event_type == PARSE_EVENT:
        return 2
    if event_type == DOCUMENT_PARSE_EVENT:
        return 5
    return None


def get_usage_count(session: Session, user_id: int, event_type: str, day_key: Optional[str] = None) -> int:
    target_day = day_key or current_day_key()
    statement = select(func.coalesce(func.sum(UsageEvent.count), 0)).where(
        UsageEvent.user_id == user_id,
        UsageEvent.event_type == event_type,
        UsageEvent.event_date == target_day,
    )
    return int(session.exec(statement).one())


async def get_usage_count_async(
    session: AsyncSession,
    user_id: int,
    event_type: str,
    day_key: Optional[str] = None,
) -> int:
    target_day = day_key or current_day_key()
    statement = select(func.coalesce(func.sum(UsageEvent.count), 0)).where(
        UsageEvent.user_id == user_id,
        UsageEvent.event_type == event_type,
        UsageEvent.event_date == target_day,
    )
    result = await session.exec(statement)
    return int(result.one())


def get_reserved_count(session: Session, user_id: int, event_type: str, day_key: Optional[str] = None) -> int:
    target_day = day_key or current_day_key()
    statement = select(func.coalesce(func.sum(QuotaReservation.amount), 0)).where(
        QuotaReservation.user_id == user_id,
        QuotaReservation.event_type == event_type,
        QuotaReservation.event_date == target_day,
        QuotaReservation.status == "reserved",
    )
    return int(session.exec(statement).one())


async def get_reserved_count_async(session: AsyncSession, user_id: int, event_type: str, day_key: Optional[str] = None) -> int:
    target_day = day_key or current_day_key()
    statement = select(func.coalesce(func.sum(QuotaReservation.amount), 0)).where(
        QuotaReservation.user_id == user_id,
        QuotaReservation.event_type == event_type,
        QuotaReservation.event_date == target_day,
        QuotaReservation.status == "reserved",
    )
    result = await session.exec(statement)
    return int(result.one())


def record_usage(
    session: Session,
    *,
    user_id: int,
    event_type: str,
    count: int = 1,
    source: Optional[str] = None,
    metadata_json: Optional[str] = None,
) -> UsageEvent:
    event = UsageEvent(
        user_id=user_id,
        event_type=event_type,
        event_date=current_day_key(),
        count=count,
        source=source,
        metadata_json=metadata_json,
    )
    session.add(event)
    session.flush()
    consumed_reservation = _consume_pending_reservation_sync(session, user_id=user_id, event_type=event_type, amount=count)
    if not consumed_reservation:
        _increment_existing_ledger_sync(session, user_id=user_id, event_type=event_type, amount=count)
    session.commit()
    session.refresh(event)
    return event


async def record_usage_async(
    session: AsyncSession,
    *,
    user_id: int,
    event_type: str,
    count: int = 1,
    source: Optional[str] = None,
    metadata_json: Optional[str] = None,
) -> UsageEvent:
    event = UsageEvent(
        user_id=user_id,
        event_type=event_type,
        event_date=current_day_key(),
        count=count,
        source=source,
        metadata_json=metadata_json,
    )
    session.add(event)
    await session.flush()
    consumed_reservation = await _consume_pending_reservation_async(
        session,
        user_id=user_id,
        event_type=event_type,
        amount=count,
    )
    if not consumed_reservation:
        await _increment_existing_ledger_async(session, user_id=user_id, event_type=event_type, amount=count)
    await session.commit()
    await session.refresh(event)
    return event


def _session_info(session: object) -> dict:
    info = getattr(session, "info", None)
    if isinstance(info, dict):
        return info
    sync_session = getattr(session, "sync_session", None)
    info = getattr(sync_session, "info", None)
    return info if isinstance(info, dict) else {}


def _pending_key(user_id: int, event_type: str) -> str:
    return f"quota_reservation:{user_id}:{event_type}"


def _ledger_limit_error(event_type: str, limit: int, used: int) -> ValueError:
    return ValueError(f"daily_quota_exceeded:{event_type}:{limit}:{used}")


def _ledger_available(ledger: QuotaLedger, limit: int, increment: int) -> bool:
    return ledger.used_count + ledger.reserved_count + increment <= limit


def reserve_quota(
    session: Session,
    *,
    user_id: int,
    user_role: str,
    event_type: str,
    increment: int = 1,
    run_id: str | None = None,
) -> tuple[int, Optional[int], str | None]:
    """Atomically reserve quota and return ``(used, limit, reservation_id)``.

    The reservation row is intentionally separate from usage events.  Callers
    can consume it through ``record_usage`` or release it after a failed
    upstream call.  The DB critical section contains only counter writes.
    """
    if increment <= 0:
        raise ValueError("quota increment must be positive")
    limit = _quota_limit_for_user(user_role, event_type)
    current = get_usage_count(session, user_id, event_type)
    if limit is None:
        return current, None, None
    reconcile_expired_reservations(session)
    day_key = current_day_key()
    for _ in range(3):
        ledger = session.exec(select(QuotaLedger).where(
            QuotaLedger.user_id == user_id,
            QuotaLedger.event_type == event_type,
            QuotaLedger.event_date == day_key,
        ).with_for_update()).first()
        if ledger is None:
            ledger = QuotaLedger(
                user_id=user_id,
                event_type=event_type,
                event_date=day_key,
                used_count=current,
            )
            session.add(ledger)
            try:
                session.flush()
            except (IntegrityError, OperationalError):
                session.rollback()
                current = get_usage_count(session, user_id, event_type)
                continue
        if not _ledger_available(ledger, limit, increment):
            used_before = ledger.used_count
            session.rollback()
            raise _ledger_limit_error(event_type, limit, used_before)
        reservation_id = uuid.uuid4().hex
        used_before = ledger.used_count
        ledger.reserved_count += increment
        ledger.updated_at = utcnow_naive()
        session.add(QuotaReservation(
            id=reservation_id,
            user_id=user_id,
            event_type=event_type,
            event_date=day_key,
            amount=increment,
            run_id=run_id,
        ))
        session.commit()
        pending = _session_info(session).setdefault(_pending_key(user_id, event_type), [])
        pending.append(reservation_id)
        return used_before, limit, reservation_id
    raise RuntimeError("quota ledger contention; retry request")


async def reserve_quota_async(
    session: AsyncSession,
    *,
    user_id: int,
    user_role: str,
    event_type: str,
    increment: int = 1,
    run_id: str | None = None,
) -> tuple[int, Optional[int], str | None]:
    if increment <= 0:
        raise ValueError("quota increment must be positive")
    limit = _quota_limit_for_user(user_role, event_type)
    current = await get_usage_count_async(session, user_id, event_type)
    if limit is None:
        return current, None, None
    await reconcile_expired_reservations_async(session)
    day_key = current_day_key()
    for _ in range(3):
        ledger = (await session.exec(select(QuotaLedger).where(
            QuotaLedger.user_id == user_id,
            QuotaLedger.event_type == event_type,
            QuotaLedger.event_date == day_key,
        ).with_for_update())).first()
        if ledger is None:
            ledger = QuotaLedger(
                user_id=user_id,
                event_type=event_type,
                event_date=day_key,
                used_count=current,
            )
            session.add(ledger)
            try:
                await session.flush()
            except (IntegrityError, OperationalError):
                await session.rollback()
                current = await get_usage_count_async(session, user_id, event_type)
                continue
        if not _ledger_available(ledger, limit, increment):
            used_before = ledger.used_count
            await session.rollback()
            raise _ledger_limit_error(event_type, limit, used_before)
        reservation_id = uuid.uuid4().hex
        used_before = ledger.used_count
        ledger.reserved_count += increment
        ledger.updated_at = utcnow_naive()
        session.add(QuotaReservation(
            id=reservation_id,
            user_id=user_id,
            event_type=event_type,
            event_date=day_key,
            amount=increment,
            run_id=run_id,
        ))
        await session.commit()
        pending = _session_info(session).setdefault(_pending_key(user_id, event_type), [])
        pending.append(reservation_id)
        return used_before, limit, reservation_id
    raise RuntimeError("quota ledger contention; retry request")


def release_quota(session: Session, reservation_id: str) -> bool:
    reservation = session.get(QuotaReservation, reservation_id)
    if not reservation or reservation.status != "reserved":
        return False
    ledger = session.exec(select(QuotaLedger).where(
        QuotaLedger.user_id == reservation.user_id,
        QuotaLedger.event_type == reservation.event_type,
        QuotaLedger.event_date == reservation.event_date,
    ).with_for_update()).first()
    if not ledger:
        return False
    ledger.reserved_count = max(0, ledger.reserved_count - reservation.amount)
    reservation.status = "released"
    reservation.updated_at = utcnow_naive()
    ledger.updated_at = utcnow_naive()
    session.add(ledger)
    session.add(reservation)
    session.commit()
    return True


def _increment_existing_ledger_sync(session: Session, *, user_id: int, event_type: str, amount: int) -> None:
    ledger = session.exec(select(QuotaLedger).where(
        QuotaLedger.user_id == user_id,
        QuotaLedger.event_type == event_type,
        QuotaLedger.event_date == current_day_key(),
    ).with_for_update()).first()
    if ledger:
        ledger.used_count += amount
        ledger.updated_at = utcnow_naive()
        session.add(ledger)


def _consume_pending_reservation_sync(session: Session, *, user_id: int, event_type: str, amount: int) -> bool:
    pending = _session_info(session).get(_pending_key(user_id, event_type), [])
    reservation_id = pending.pop(0) if pending else None
    if not pending:
        _session_info(session).pop(_pending_key(user_id, event_type), None)
    if not reservation_id:
        return False
    reservation = session.get(QuotaReservation, reservation_id)
    if not reservation or reservation.status != "reserved":
        return False
    ledger = session.exec(select(QuotaLedger).where(
        QuotaLedger.user_id == user_id,
        QuotaLedger.event_type == event_type,
        QuotaLedger.event_date == reservation.event_date,
    ).with_for_update()).first()
    if ledger:
        ledger.reserved_count = max(0, ledger.reserved_count - reservation.amount)
        ledger.used_count += amount
        ledger.updated_at = utcnow_naive()
        session.add(ledger)
    reservation.status = "consumed"
    reservation.updated_at = utcnow_naive()
    session.add(reservation)
    return True


async def release_quota_async(session: AsyncSession, reservation_id: str) -> bool:
    reservation = await session.get(QuotaReservation, reservation_id)
    if not reservation or reservation.status != "reserved":
        return False
    ledger = (await session.exec(select(QuotaLedger).where(
        QuotaLedger.user_id == reservation.user_id,
        QuotaLedger.event_type == reservation.event_type,
        QuotaLedger.event_date == reservation.event_date,
    ).with_for_update())).first()
    if not ledger:
        return False
    ledger.reserved_count = max(0, ledger.reserved_count - reservation.amount)
    reservation.status = "released"
    reservation.updated_at = utcnow_naive()
    ledger.updated_at = utcnow_naive()
    session.add(ledger)
    session.add(reservation)
    await session.commit()
    return True


async def _increment_existing_ledger_async(session: AsyncSession, *, user_id: int, event_type: str, amount: int) -> None:
    ledger = (await session.exec(select(QuotaLedger).where(
        QuotaLedger.user_id == user_id,
        QuotaLedger.event_type == event_type,
        QuotaLedger.event_date == current_day_key(),
    ).with_for_update())).first()
    if ledger:
        ledger.used_count += amount
        ledger.updated_at = utcnow_naive()
        session.add(ledger)


async def _consume_pending_reservation_async(session: AsyncSession, *, user_id: int, event_type: str, amount: int) -> bool:
    pending = _session_info(session).get(_pending_key(user_id, event_type), [])
    reservation_id = pending.pop(0) if pending else None
    if not pending:
        _session_info(session).pop(_pending_key(user_id, event_type), None)
    if not reservation_id:
        return False
    reservation = await session.get(QuotaReservation, reservation_id)
    if not reservation or reservation.status != "reserved":
        return False
    ledger = (await session.exec(select(QuotaLedger).where(
        QuotaLedger.user_id == user_id,
        QuotaLedger.event_type == event_type,
        QuotaLedger.event_date == reservation.event_date,
    ).with_for_update())).first()
    if ledger:
        ledger.reserved_count = max(0, ledger.reserved_count - reservation.amount)
        ledger.used_count += amount
        ledger.updated_at = utcnow_naive()
        session.add(ledger)
    reservation.status = "consumed"
    reservation.updated_at = utcnow_naive()
    session.add(reservation)
    return True


def reconcile_expired_reservations(session: Session, *, now: datetime | None = None) -> int:
    """Release abandoned reservations; safe to run from a periodic worker."""
    cutoff = now or utcnow_naive()
    rows = session.exec(select(QuotaReservation).where(
        QuotaReservation.status == "reserved",
        QuotaReservation.expires_at <= cutoff,
    )).all()
    count = 0
    for reservation in rows:
        if release_quota(session, reservation.id):
            count += 1
    return count


async def reconcile_expired_reservations_async(session: AsyncSession, *, now: datetime | None = None) -> int:
    cutoff = now or utcnow_naive()
    rows = (await session.exec(select(QuotaReservation).where(
        QuotaReservation.status == "reserved",
        QuotaReservation.expires_at <= cutoff,
    ))).all()
    count = 0
    for reservation in rows:
        if await release_quota_async(session, reservation.id):
            count += 1
    return count


async def release_pending_quota_async(session: AsyncSession, *, user_id: int, event_type: str) -> int:
    """Release reservations made on this request when its upstream fails."""
    pending = _session_info(session).pop(_pending_key(user_id, event_type), [])
    if isinstance(pending, str):
        pending = [pending]
    released = 0
    for reservation_id in list(pending):
        if await release_quota_async(session, reservation_id):
            released += 1
    return released


def ensure_within_quota(session: Session, *, user_id: int, user_role: str, event_type: str, increment: int = 1) -> tuple[int, Optional[int]]:
    used, limit, _ = reserve_quota(
        session,
        user_id=user_id,
        user_role=user_role,
        event_type=event_type,
        increment=increment,
    )
    return used, limit


async def ensure_within_quota_async(
    session: AsyncSession,
    *,
    user_id: int,
    user_role: str,
    event_type: str,
    increment: int = 1,
) -> tuple[int, Optional[int]]:
    used, limit, _ = await reserve_quota_async(
        session,
        user_id=user_id,
        user_role=user_role,
        event_type=event_type,
        increment=increment,
    )
    return used, limit


def usage_response_payload(session: Session, *, user_id: int, user_role: str, event_type: str) -> dict:
    reconcile_expired_reservations(session)
    used = get_usage_count(session, user_id, event_type)
    reserved = get_reserved_count(session, user_id, event_type)
    limit = _quota_limit_for_user(user_role, event_type)
    remaining = None if limit is None else max(0, limit - used - reserved)
    payload = {
        "eventType": event_type,
        "used": used,
        "reserved": reserved,
        "limit": limit,
        "remaining": remaining,
        "resetAt": next_midnight_shanghai().isoformat(),
    }
    return payload


async def usage_response_payload_async(
    session: AsyncSession,
    *,
    user_id: int,
    user_role: str,
    event_type: str,
) -> dict:
    await reconcile_expired_reservations_async(session)
    used = await get_usage_count_async(session, user_id, event_type)
    reserved = await get_reserved_count_async(session, user_id, event_type)
    limit = _quota_limit_for_user(user_role, event_type)
    remaining = None if limit is None else max(0, limit - used - reserved)
    payload = {
        "eventType": event_type,
        "used": used,
        "reserved": reserved,
        "limit": limit,
        "remaining": remaining,
        "resetAt": next_midnight_shanghai().isoformat(),
    }
    return payload
