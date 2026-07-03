from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from sqlmodel import SQLModel, Field, Session, select
from sqlmodel.ext.asyncio.session import AsyncSession


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class UsageEvent(SQLModel, table=True):
    __tablename__ = "usage_events"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    event_type: str = Field(max_length=40, index=True)
    event_date: str = Field(max_length=10, index=True)
    count: int = Field(default=1)
    source: Optional[str] = Field(default=None, max_length=80)
    metadata_json: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow_naive)


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
    statement = select(UsageEvent).where(
        UsageEvent.user_id == user_id,
        UsageEvent.event_type == event_type,
        UsageEvent.event_date == target_day,
    )
    total = 0
    for item in session.exec(statement).all():
        total += int(item.count or 0)
    return total


async def get_usage_count_async(
    session: AsyncSession,
    user_id: int,
    event_type: str,
    day_key: Optional[str] = None,
) -> int:
    target_day = day_key or current_day_key()
    statement = select(UsageEvent).where(
        UsageEvent.user_id == user_id,
        UsageEvent.event_type == event_type,
        UsageEvent.event_date == target_day,
    )
    total = 0
    result = await session.exec(statement)
    for item in result.all():
        total += int(item.count or 0)
    return total


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
    await session.commit()
    await session.refresh(event)
    return event


def ensure_within_quota(session: Session, *, user_id: int, user_role: str, event_type: str, increment: int = 1) -> tuple[int, Optional[int]]:
    limit = _quota_limit_for_user(user_role, event_type)
    if limit is None:
        return get_usage_count(session, user_id, event_type), None

    used = get_usage_count(session, user_id, event_type)
    if used + increment > limit:
        raise ValueError(f"daily_quota_exceeded:{event_type}:{limit}:{used}")
    return used, limit


async def ensure_within_quota_async(
    session: AsyncSession,
    *,
    user_id: int,
    user_role: str,
    event_type: str,
    increment: int = 1,
) -> tuple[int, Optional[int]]:
    limit = _quota_limit_for_user(user_role, event_type)
    if limit is None:
        return await get_usage_count_async(session, user_id, event_type), None

    used = await get_usage_count_async(session, user_id, event_type)
    if used + increment > limit:
        raise ValueError(f"daily_quota_exceeded:{event_type}:{limit}:{used}")
    return used, limit


def usage_response_payload(session: Session, *, user_id: int, user_role: str, event_type: str) -> dict:
    used = get_usage_count(session, user_id, event_type)
    limit = _quota_limit_for_user(user_role, event_type)
    remaining = None if limit is None else max(0, limit - used)
    payload = {
        "eventType": event_type,
        "used": used,
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
    used = await get_usage_count_async(session, user_id, event_type)
    limit = _quota_limit_for_user(user_role, event_type)
    remaining = None if limit is None else max(0, limit - used)
    payload = {
        "eventType": event_type,
        "used": used,
        "limit": limit,
        "remaining": remaining,
        "resetAt": next_midnight_shanghai().isoformat(),
    }
    return payload
