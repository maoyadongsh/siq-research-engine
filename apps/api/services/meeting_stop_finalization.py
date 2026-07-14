"""Lease-aware fallback for completing meeting capture stops.

The WebSocket gateway remains the normal owner while its producer lease is
live. Once that lease is absent or expired, a repeated REST stop may validate
and pack the durable PCM manifest before applying the existing mark_stopped
transition.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from services.meeting_audio_store import MeetingAudioStore, MeetingAudioStoreError
from services.meeting_contracts import MeetingEvent, MeetingSession, MeetingState, MeetingStreamLease, utcnow
from services.meeting_repository import MeetingInvalidOperation, MeetingRepository


class MeetingStopFinalizationConflict(MeetingInvalidOperation):
    code = "MEETING_STOP_FINALIZATION_CONFLICT"

    def __init__(self, audio_code: str) -> None:
        super().__init__(f"meeting audio finalization failed ({audio_code})")
        self.audio_code = audio_code


@dataclass(frozen=True, slots=True)
class MeetingStopFinalizationResult:
    session: MeetingSession
    idempotent: bool
    event: MeetingEvent | None
    finalization_path: str
    audio_status: str


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def lease_is_active(
    lease: MeetingStreamLease | None,
    *,
    now: datetime | None = None,
    owner_user_id: int | None = None,
    stream_epoch: int | None = None,
) -> bool:
    return bool(
        lease is not None
        and (owner_user_id is None or lease.owner_user_id == owner_user_id)
        and (stream_epoch is None or lease.stream_epoch == stream_epoch)
        and _aware(lease.lease_until) > _aware(now or utcnow())
    )


def stream_lease_for_update_statement(meeting_id: str):
    return (
        select(MeetingStreamLease)
        .where(MeetingStreamLease.meeting_id == meeting_id)
        .with_for_update()
    )


class MeetingStopFinalizationService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        audio_store: MeetingAudioStore | None = None,
    ) -> None:
        self.session = session
        self.repository = MeetingRepository(session)
        self.audio_store = audio_store or MeetingAudioStore()

    async def stop(
        self,
        meeting_id: str,
        owner_user_id: int,
    ) -> MeetingStopFinalizationResult:
        meeting, stop_idempotent, stop_event = await self.repository.transition_session(
            meeting_id,
            owner_user_id,
            "stop",
        )
        if meeting.state in {MeetingState.STOPPED.value, MeetingState.ARCHIVED.value}:
            chunks = await self.repository.list_audio_chunks(meeting_id, owner_user_id)
            await self.session.commit()
            return MeetingStopFinalizationResult(
                session=meeting,
                idempotent=True,
                event=stop_event,
                finalization_path="already_stopped",
                audio_status="available" if chunks else "unavailable",
            )

        lease = (
            await self.session.exec(stream_lease_for_update_statement(meeting_id))
        ).first()
        if lease_is_active(
            lease,
            owner_user_id=owner_user_id,
            stream_epoch=meeting.stream_epoch,
        ):
            # Release the read/row lock promptly; the live gateway owns packing
            # and mark_stopped until this bounded lease expires.
            await self.session.commit()
            return MeetingStopFinalizationResult(
                session=meeting,
                idempotent=stop_idempotent,
                event=stop_event,
                finalization_path="stream_gateway",
                audio_status="pending",
            )

        chunks = await self.repository.list_audio_chunks(meeting_id, owner_user_id)
        if chunks:
            try:
                await asyncio.to_thread(
                    self.audio_store.pack_wav,
                    owner_user_id,
                    meeting_id,
                    chunks,
                )
            except MeetingAudioStoreError as exc:
                await self.session.rollback()
                raise MeetingStopFinalizationConflict(exc.code) from exc

        completed, mark_idempotent, mark_event = await self.repository.transition_session(
            meeting_id,
            owner_user_id,
            "mark_stopped",
        )
        return MeetingStopFinalizationResult(
            session=completed,
            idempotent=stop_idempotent and mark_idempotent,
            event=mark_event or stop_event,
            finalization_path="already_stopped" if mark_idempotent else "rest_fallback",
            audio_status="available" if chunks else "unavailable",
        )


__all__ = [
    "MeetingStopFinalizationConflict",
    "MeetingStopFinalizationResult",
    "MeetingStopFinalizationService",
    "lease_is_active",
    "stream_lease_for_update_statement",
]
