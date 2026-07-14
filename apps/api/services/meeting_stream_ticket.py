"""One-time, origin-bound meeting stream tickets and producer leases."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import text, update
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from services.meeting_config import MeetingSettings
from services.meeting_contracts import (
    MeetingSession,
    MeetingState,
    MeetingStreamLease,
    MeetingStreamTicket,
    utcnow,
)
from services.meeting_repository import MeetingResourceNotFound, MeetingVersionConflict


class StreamTicketError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _hash_ticket(ticket: str) -> str:
    try:
        encoded = ticket.encode("ascii")
    except (AttributeError, UnicodeEncodeError) as exc:
        raise StreamTicketError("STREAM_TICKET_INVALID", "stream ticket is invalid") from exc
    return hashlib.sha256(encoded).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def normalize_origin(origin: str) -> str:
    value = origin.strip().rstrip("/")
    if not value.startswith(("http://", "https://")) or len(value) > 500:
        raise StreamTicketError("STREAM_ORIGIN_INVALID", "a valid HTTP Origin is required")
    return value


class MeetingStreamTicketService:
    def __init__(self, session: AsyncSession, settings: MeetingSettings | None = None) -> None:
        self.session = session
        self.settings = settings or MeetingSettings.from_env()

    async def _serialize_capacity_check(self) -> None:
        bind = self.session.get_bind()
        if bind.dialect.name == "postgresql":
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": 7_418_221_903_117_021_021},
            )
            return
        # SQLite ignores FOR UPDATE, but its writer serialization still protects
        # the subsequent lease upsert. Other supported databases lock the same
        # ordered session rows for every capacity decision.
        await self.session.exec(select(MeetingSession.id).order_by(MeetingSession.id).with_for_update())

    async def _enforce_capacity(
        self,
        *,
        meeting_id: str,
        owner_user_id: int,
        now: datetime,
    ) -> None:
        active = list(
            (
                await self.session.exec(
                    select(
                        MeetingStreamLease.owner_user_id,
                        MeetingStreamLease.meeting_id,
                    )
                    .join(
                        MeetingSession,
                        MeetingSession.id == MeetingStreamLease.meeting_id,
                    )
                    .where(
                        MeetingStreamLease.lease_until > now,
                        MeetingStreamLease.meeting_id != meeting_id,
                        MeetingSession.state.in_(
                            [
                                MeetingState.CONNECTING.value,
                                MeetingState.LIVE.value,
                                MeetingState.RECONNECTING.value,
                            ]
                        ),
                    )
                )
            ).all()
        )
        owner_active = sum(1 for active_owner, _ in active if active_owner == owner_user_id)
        if owner_active + 1 > self.settings.max_active_per_user:
            raise StreamTicketError(
                "MEETING_ACTIVE_LIMIT_PER_USER",
                "the active meeting limit for this user was reached",
            )
        if len(active) + 1 > self.settings.max_active_total:
            raise StreamTicketError(
                "MEETING_ACTIVE_LIMIT_TOTAL",
                "the global active meeting limit was reached",
            )

    async def issue(
        self,
        meeting_id: str,
        owner_user_id: int,
        *,
        origin: str,
    ) -> tuple[str, MeetingStreamTicket, MeetingSession]:
        normalized_origin = normalize_origin(origin)
        meeting = (
            await self.session.exec(
                select(MeetingSession)
                .where(
                    MeetingSession.id == meeting_id,
                    MeetingSession.owner_user_id == owner_user_id,
                    MeetingSession.state.in_(
                        [
                            MeetingState.CONNECTING.value,
                            MeetingState.LIVE.value,
                            MeetingState.RECONNECTING.value,
                        ]
                    ),
                )
                .with_for_update()
            )
        ).first()
        if meeting is None:
            raise MeetingResourceNotFound("meeting resource not found")
        if meeting.stream_epoch < 1:
            meeting.stream_epoch = 1
            meeting.last_audio_sequence = -1
            meeting.updated_at = utcnow()
            self.session.add(meeting)
        raw_ticket = secrets.token_urlsafe(32)
        ticket = MeetingStreamTicket(
            token_hash=_hash_ticket(raw_ticket),
            meeting_id=meeting.id,
            owner_user_id=owner_user_id,
            stream_epoch=meeting.stream_epoch,
            origin=normalized_origin,
            expires_at=utcnow() + timedelta(seconds=self.settings.stream_ticket_ttl_seconds),
        )
        self.session.add(ticket)
        await self.session.commit()
        # The API dependency uses SQLAlchemy's default expire_on_commit=True.
        # Refresh before returning so response serialization never performs
        # implicit async IO through a synchronous attribute access.
        await self.session.refresh(ticket)
        await self.session.refresh(meeting)
        return raw_ticket, ticket, meeting

    async def consume(
        self,
        meeting_id: str,
        raw_ticket: str,
        *,
        origin: str,
        connection_id: str,
    ) -> tuple[MeetingStreamTicket, MeetingSession]:
        normalized_origin = normalize_origin(origin)
        ticket = (
            await self.session.exec(
                select(MeetingStreamTicket)
                .where(
                    MeetingStreamTicket.meeting_id == meeting_id,
                    MeetingStreamTicket.token_hash == _hash_ticket(raw_ticket),
                    MeetingStreamTicket.origin == normalized_origin,
                    MeetingStreamTicket.purpose == "meeting_audio_producer",
                )
                .with_for_update()
            )
        ).first()
        now = utcnow()
        if ticket is None or ticket.consumed_at is not None or _aware(ticket.expires_at) <= _aware(now):
            raise StreamTicketError("STREAM_TICKET_INVALID", "stream ticket is invalid or expired")
        await self._serialize_capacity_check()
        meeting = (
            await self.session.exec(
                select(MeetingSession)
                .where(
                    MeetingSession.id == meeting_id,
                    MeetingSession.owner_user_id == ticket.owner_user_id,
                    MeetingSession.stream_epoch == ticket.stream_epoch,
                    MeetingSession.state.in_(
                        [
                            MeetingState.CONNECTING.value,
                            MeetingState.LIVE.value,
                            MeetingState.RECONNECTING.value,
                        ]
                    ),
                )
                .with_for_update()
            )
        ).first()
        if meeting is None:
            raise StreamTicketError("STREAM_TICKET_INVALID", "stream ticket is invalid or expired")
        lease = (
            await self.session.exec(
                select(MeetingStreamLease).where(MeetingStreamLease.meeting_id == meeting_id).with_for_update()
            )
        ).first()
        lease_until = now + timedelta(seconds=self.settings.stream_lease_ttl_seconds)
        reconnect_takeover = (
            lease is not None
            and meeting.state == MeetingState.RECONNECTING.value
            and lease.stream_epoch == ticket.stream_epoch
        )
        if (
            lease is not None
            and _aware(lease.lease_until) > _aware(now)
            and lease.connection_id != connection_id
            and not reconnect_takeover
        ):
            raise MeetingVersionConflict(
                "another audio producer holds the meeting lease",
                current={"stream_epoch": lease.stream_epoch},
            )
        await self._enforce_capacity(
            meeting_id=meeting_id,
            owner_user_id=ticket.owner_user_id,
            now=now,
        )
        if lease is None:
            lease = MeetingStreamLease(
                meeting_id=meeting_id,
                stream_epoch=ticket.stream_epoch,
                connection_id=connection_id,
                owner_user_id=ticket.owner_user_id,
                lease_until=lease_until,
                last_acked_sequence=meeting.last_audio_sequence,
            )
        else:
            lease.stream_epoch = ticket.stream_epoch
            lease.connection_id = connection_id
            lease.owner_user_id = ticket.owner_user_id
            lease.lease_until = lease_until
            lease.last_acked_sequence = meeting.last_audio_sequence
            lease.updated_at = now
        ticket.consumed_at = now
        ticket.connection_id = connection_id
        self.session.add(ticket)
        self.session.add(lease)
        await self.session.commit()
        return ticket, meeting

    async def issue_playback(
        self,
        meeting_id: str,
        owner_user_id: int,
        *,
        origin: str,
    ) -> tuple[str, MeetingStreamTicket, MeetingSession]:
        normalized_origin = normalize_origin(origin)
        meeting = (
            await self.session.exec(
                select(MeetingSession).where(
                    MeetingSession.id == meeting_id,
                    MeetingSession.owner_user_id == owner_user_id,
                    MeetingSession.state.in_([MeetingState.STOPPED.value, MeetingState.ARCHIVED.value]),
                )
            )
        ).first()
        if meeting is None:
            raise MeetingResourceNotFound("meeting resource not found")
        raw_ticket = secrets.token_urlsafe(32)
        ticket = MeetingStreamTicket(
            token_hash=_hash_ticket(raw_ticket),
            meeting_id=meeting_id,
            owner_user_id=owner_user_id,
            stream_epoch=max(1, meeting.stream_epoch),
            purpose="meeting_audio_playback",
            origin=normalized_origin,
            expires_at=utcnow() + timedelta(seconds=self.settings.playback_ticket_ttl_seconds),
        )
        self.session.add(ticket)
        await self.session.commit()
        await self.session.refresh(ticket)
        await self.session.refresh(meeting)
        return raw_ticket, ticket, meeting

    async def validate_playback(
        self,
        meeting_id: str,
        raw_ticket: str,
    ) -> tuple[MeetingStreamTicket, MeetingSession]:
        ticket = (
            await self.session.exec(
                select(MeetingStreamTicket).where(
                    MeetingStreamTicket.meeting_id == meeting_id,
                    MeetingStreamTicket.token_hash == _hash_ticket(raw_ticket),
                    MeetingStreamTicket.purpose == "meeting_audio_playback",
                )
            )
        ).first()
        if ticket is None or _aware(ticket.expires_at) <= _aware(utcnow()):
            raise StreamTicketError("AUDIO_PLAYBACK_TICKET_INVALID", "audio playback ticket is invalid")
        meeting = (
            await self.session.exec(
                select(MeetingSession).where(
                    MeetingSession.id == meeting_id,
                    MeetingSession.owner_user_id == ticket.owner_user_id,
                    MeetingSession.state.in_([MeetingState.STOPPED.value, MeetingState.ARCHIVED.value]),
                )
            )
        ).first()
        if meeting is None:
            raise StreamTicketError("AUDIO_PLAYBACK_TICKET_INVALID", "audio playback ticket is invalid")
        return ticket, meeting

    async def renew_lease(self, meeting_id: str, connection_id: str, ack_sequence: int) -> None:
        lease = (
            await self.session.exec(
                select(MeetingStreamLease).where(
                    MeetingStreamLease.meeting_id == meeting_id,
                    MeetingStreamLease.connection_id == connection_id,
                )
            )
        ).first()
        if lease is None:
            raise StreamTicketError("STREAM_LEASE_LOST", "audio producer lease was lost")
        lease.lease_until = utcnow() + timedelta(seconds=self.settings.stream_lease_ttl_seconds)
        lease.last_acked_sequence = max(lease.last_acked_sequence, ack_sequence)
        lease.updated_at = utcnow()
        self.session.add(lease)
        await self.session.commit()

    async def release_lease(self, meeting_id: str, connection_id: str) -> None:
        now = utcnow()
        await self.session.exec(
            update(MeetingStreamLease)
            .where(
                MeetingStreamLease.meeting_id == meeting_id,
                MeetingStreamLease.connection_id == connection_id,
            )
            .values(lease_until=now, updated_at=now)
        )
        await self.session.commit()
