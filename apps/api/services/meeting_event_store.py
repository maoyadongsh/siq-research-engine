"""Durable meeting event/outbox helpers."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from services.meeting_contracts import (
    MEETING_EVENT_SCHEMA_VERSION,
    MeetingEvent,
    MeetingEventResponse,
    MeetingSession,
)


def encode_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def decode_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def event_response(event: MeetingEvent) -> MeetingEventResponse:
    return MeetingEventResponse(
        meeting_id=event.meeting_id,
        cursor=event.cursor,
        event_id=event.event_id,
        event_type=event.event_type,
        type=event.event_type,
        schema_version=event.schema_version,
        payload=decode_json(event.payload_json, {}),
        trace_id=event.trace_id,
        created_at=event.created_at,
        emitted_at=event.created_at,
    )


class MeetingEventStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def append(
        self,
        meeting_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        trace_id: str | None = None,
        cursor: int | None = None,
    ) -> MeetingEvent:
        if cursor is None:
            # Serialize cursor allocation per meeting. PostgreSQL row locks make
            # max(cursor)+1 safe across API, stream, and worker transactions.
            locked_meeting_id = (
                await self.session.exec(
                    select(MeetingSession.id)
                    .where(MeetingSession.id == meeting_id)
                    .with_for_update()
                )
            ).first()
            if locked_meeting_id is None:
                raise ValueError("meeting event target does not exist")
            result = await self.session.exec(
                select(func.max(MeetingEvent.cursor)).where(MeetingEvent.meeting_id == meeting_id)
            )
            cursor = int(result.one() or 0) + 1
        event = MeetingEvent(
            meeting_id=meeting_id,
            cursor=cursor,
            event_type=event_type,
            schema_version=MEETING_EVENT_SCHEMA_VERSION,
            payload_json=encode_json(payload),
            trace_id=trace_id,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def list_after(
        self,
        meeting_id: str,
        *,
        after_cursor: int = 0,
        limit: int = 200,
    ) -> list[MeetingEvent]:
        result = await self.session.exec(
            select(MeetingEvent)
            .where(MeetingEvent.meeting_id == meeting_id)
            .where(MeetingEvent.cursor > after_cursor)
            .order_by(MeetingEvent.cursor)
            .limit(limit)
        )
        return list(result.all())


async def append_meeting_event(
    session: AsyncSession,
    meeting_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    trace_id: str | None = None,
) -> MeetingEvent:
    return await MeetingEventStore(session).append(
        meeting_id,
        event_type,
        payload,
        trace_id=trace_id,
    )
