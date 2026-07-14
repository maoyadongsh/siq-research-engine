"""Async repository for additive meeting-domain persistence.

All object lookups include the owner predicate in the database query. Methods
that write a transcript revision, outbox event, model setting, or lexicon
version commit those records atomically.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func, inspect, or_, text, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from services.auth_service import User
from services.meeting_contracts import (
    ArtifactResponse,
    ArtifactState,
    ArtifactType,
    AudioManifestResponse,
    CorrectionFeedbackResponse,
    CorrectionStatus,
    JobResponse,
    LexiconEntryCreateRequest,
    LexiconEntryResponse,
    LexiconEntryStatus,
    LexiconEntryUpdateRequest,
    LexiconResponse,
    LexiconScope,
    LexiconVersionResponse,
    MeetingArtifact,
    MeetingASRCorrectionEvent,
    MeetingAudioChunk,
    MeetingCreateRequest,
    MeetingEvent,
    MeetingExportCreateRequest,
    MeetingExportResponse,
    MeetingIdempotencyRecord,
    MeetingJob,
    MeetingJobKind,
    MeetingJobState,
    MeetingLexiconEntry,
    MeetingLexiconVersion,
    MeetingModelSetting,
    MeetingModelSnapshot,
    MeetingSegmentRevision,
    MeetingSession,
    MeetingSessionResponse,
    MeetingSpeakerTrack,
    MeetingState,
    MeetingTermCandidate,
    MeetingTermCandidateSource,
    MeetingTranscriptSegment,
    MeetingUpdateRequest,
    MeetingVoiceprintConsent,
    MeetingVoiceprintMatch,
    MeetingVoiceProfile,
    ModelSelectionInput,
    ModelSelectionMode,
    ModelSettingResponse,
    SegmentCorrectionRequest,
    SegmentCorrectionResponse,
    SegmentRevisionResponse,
    SpeakerLabelSource,
    SpeakerMappingResponse,
    SpeakerMergeRequest,
    SpeakerRenameRequest,
    SpeakerSplitRequest,
    SpeakerTrackResponse,
    StableSegmentInput,
    TermCandidateResponse,
    TermCandidateStatus,
    TranscriptSegmentResponse,
    VoiceMatchDecision,
    VoiceMatchResponse,
    VoiceprintEnrollmentRequest,
    VoiceprintEnrollmentResponse,
    VoiceProfileResponse,
    VoiceProfileStatus,
    utcnow,
)
from services.meeting_correction_feedback import (
    calculate_diff,
    classify_correction,
    contribution_is_eligible,
    validated_candidate_terms,
)
from services.meeting_event_store import MeetingEventStore, decode_json, encode_json, event_response
from services.meeting_lexicon_service import assert_unambiguous_confusions, entries_hash
from services.meeting_native_capture_contracts import (
    MeetingNativeCapture,
    MeetingNativeCaptureFinalization,
    MeetingNativeCaptureToken,
    NativeCaptureFinalizationState,
    NativeCaptureState,
)
from services.meeting_state_machine import (
    MeetingTransitionError,
    resolve_capture_transition,
    resolve_finalize_transition,
    validate_voice_match_transition,
    validate_voice_profile_transition,
)
from services.meeting_voiceprint_tombstone import (
    VoiceprintTombstoneError,
    VoiceprintTombstoneLedger,
)


class MeetingRepositoryError(RuntimeError):
    code = "MEETING_REPOSITORY_ERROR"


class MeetingResourceNotFound(MeetingRepositoryError):
    code = "MEETING_RESOURCE_NOT_FOUND"


class MeetingVersionConflict(MeetingRepositoryError):
    code = "MEETING_VERSION_CONFLICT"

    def __init__(self, message: str, *, current: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.current = current or {}


class MeetingInvalidOperation(MeetingRepositoryError):
    code = "MEETING_INVALID_OPERATION"


class MeetingIdempotencyConflict(MeetingRepositoryError):
    code = "MEETING_IDEMPOTENCY_CONFLICT"


def _request_hash(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def _aware_datetime(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class MeetingSessionSummary:
    session: MeetingSession
    speaker_count: int
    model_label: str | None
    model_locality: str | None


def session_response(
    value: MeetingSession,
    *,
    speaker_count: int | None = None,
    model_label: str | None = None,
    model_locality: str | None = None,
) -> MeetingSessionResponse:
    response = MeetingSessionResponse.model_validate(value)
    return response.model_copy(
        update={
            "speaker_count": speaker_count,
            "model_label": model_label,
            "model_locality": model_locality,
        }
    )


def speaker_response(value: MeetingSpeakerTrack) -> SpeakerTrackResponse:
    return SpeakerTrackResponse(
        id=value.id,
        meeting_id=value.meeting_id,
        track_key=value.track_key,
        anonymous_label=value.anonymous_label,
        display_name=value.display_name,
        resolved_label=value.display_name or value.anonymous_label,
        label_source=value.label_source,
        voice_profile_id=value.voice_profile_id,
        match_confidence=value.match_confidence,
        version=value.version,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def transcript_event_payload(value: TranscriptSegmentResponse) -> dict[str, Any]:
    segment = value.model_dump(mode="json")
    segment["revision_no"] = value.current_revision_no
    segment["speaker_display_name"] = value.speaker_label
    segment["text_state"] = (
        "human_verified"
        if value.human_locked
        else "optimized"
        if value.display_layer == "llm_corrected"
        else "stable"
    )
    return segment


def revision_response(value: MeetingSegmentRevision) -> SegmentRevisionResponse:
    return SegmentRevisionResponse(
        id=value.id,
        segment_id=value.segment_id,
        revision_no=value.revision_no,
        revision_type=value.revision_type,
        text=value.text,
        base_revision_no=value.base_revision_no,
        reason_codes=decode_json(value.reason_codes_json, []),
        created_by=value.created_by,
        created_at=value.created_at,
    )


def correction_response(value: MeetingASRCorrectionEvent) -> CorrectionFeedbackResponse:
    diff = decode_json(value.diff_ops_json, {})
    return CorrectionFeedbackResponse(
        id=value.id,
        meeting_id=value.meeting_id,
        segment_id=value.segment_id,
        speaker_track_id=value.speaker_track_id,
        voice_profile_id=value.voice_profile_id,
        base_revision_no=value.base_revision_no,
        result_revision_no=value.result_revision_no,
        original_text=value.original_text,
        corrected_text=value.corrected_text,
        diff_ops=diff.get("operations", []),
        edit_intent=value.edit_intent,
        error_class=value.error_class,
        contribute_to_accuracy=value.contribute_to_accuracy,
        status=value.status,
        asr_provider=value.asr_provider,
        asr_model=value.asr_model,
        asr_version=value.asr_version,
        hotword_version=value.hotword_version,
        audio_start_ms=value.audio_start_ms,
        audio_end_ms=value.audio_end_ms,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def term_candidate_response(value: MeetingTermCandidate) -> TermCandidateResponse:
    return TermCandidateResponse.model_validate(value)


def lexicon_entry_response(value: MeetingLexiconEntry) -> LexiconEntryResponse:
    return LexiconEntryResponse(
        id=value.id,
        language=value.language,
        canonical_term=value.canonical_term,
        aliases=decode_json(value.aliases_json, []),
        misrecognitions=decode_json(value.misrecognitions_json, []),
        entry_type=value.entry_type,
        weight=value.weight,
        scope=value.scope,
        meeting_id=value.meeting_id,
        status=value.status,
        source_candidate_id=value.source_candidate_id,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def lexicon_version_response(value: MeetingLexiconVersion) -> LexiconVersionResponse:
    return LexiconVersionResponse.model_validate(value)


def voice_profile_response(
    value: MeetingVoiceProfile,
    *,
    consent_active: bool = False,
) -> VoiceProfileResponse:
    return VoiceProfileResponse(
        id=value.id,
        display_name=value.display_name,
        scope=value.scope,
        status=value.status,
        encoder_name=value.encoder_name,
        encoder_version=value.encoder_version,
        sample_count=value.sample_count,
        effective_duration_ms=value.effective_duration_ms,
        quality_summary=decode_json(value.quality_summary_json, {}),
        consent_active=consent_active,
        created_at=value.created_at,
        updated_at=value.updated_at,
        deleted_at=value.deleted_at,
    )


def model_setting_response(value: MeetingModelSetting) -> ModelSettingResponse:
    return ModelSettingResponse.model_validate(value)


def artifact_response(value: MeetingArtifact) -> ArtifactResponse:
    return ArtifactResponse(
        id=value.id,
        meeting_id=value.meeting_id,
        artifact_type=value.artifact_type,
        version=value.version,
        state=value.state,
        content_json=decode_json(value.content_json, None),
        content_text=value.content_text,
        input_from_ordinal=value.input_from_ordinal,
        input_to_ordinal=value.input_to_ordinal,
        transcript_revision=value.transcript_revision,
        model_snapshot_id=value.model_snapshot_id,
        supersedes_id=value.supersedes_id,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def job_response(value: MeetingJob) -> JobResponse:
    return JobResponse.model_validate(value)


def voice_match_response(value: MeetingVoiceprintMatch) -> VoiceMatchResponse:
    return VoiceMatchResponse.model_validate(value)


def export_response(value: MeetingArtifact, job: MeetingJob) -> MeetingExportResponse:
    request = decode_json(job.input_json, {})
    content = decode_json(value.content_json, {})
    if value.state == ArtifactState.READY.value:
        state = "ready"
    elif value.state == ArtifactState.FAILED.value or job.state == MeetingJobState.FAILED.value:
        state = "failed"
    elif job.state in {MeetingJobState.LEASED.value, MeetingJobState.RUNNING.value}:
        state = "running"
    else:
        state = "queued"
    return MeetingExportResponse(
        id=value.id,
        meeting_id=value.meeting_id,
        format=request.get("format", "txt"),
        content=request.get("content", "transcript"),
        transcript_source=request.get("transcript_source", "display"),
        state=state,
        job_id=job.id,
        artifact_id=request.get("source_artifact_id"),
        artifact_version=request.get("source_artifact_version"),
        filename=content.get("filename") if isinstance(content, dict) else None,
        media_type=content.get("media_type") if isinstance(content, dict) else None,
        byte_size=content.get("byte_size") if isinstance(content, dict) else None,
        sha256=content.get("sha256") if isinstance(content, dict) else None,
        error_code=job.public_error_code,
        created_at=value.created_at,
        updated_at=max(value.updated_at, job.updated_at),
    )


class MeetingRepository:
    def __init__(
        self,
        session: AsyncSession,
        *,
        voiceprint_tombstones: VoiceprintTombstoneLedger | None = None,
    ) -> None:
        self.session = session
        self.events = MeetingEventStore(session)
        self._voiceprint_tombstones = voiceprint_tombstones

    async def _commit(self) -> None:
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise MeetingVersionConflict("meeting write conflicted with a concurrent update") from exc

    async def _voiceprint_owner_lock(self, owner_user_id: int) -> None:
        bind = self.session.get_bind()
        if getattr(getattr(bind, "dialect", None), "name", None) != "postgresql":
            return
        lock_key = int.from_bytes(
            hashlib.sha256(f"siq:meeting:voiceprint-owner:{owner_user_id}".encode()).digest()[:8],
            byteorder="big",
            signed=True,
        )
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )

    def _configured_voiceprint_tombstones(
        self,
        *,
        required: bool = False,
    ) -> VoiceprintTombstoneLedger | None:
        if self._voiceprint_tombstones is not None:
            return self._voiceprint_tombstones
        enabled = os.getenv("SIQ_MEETINGS_VOICEPRINT_ENABLED", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        configured = bool(
            os.getenv("SIQ_MEETING_VOICEPRINT_TOMBSTONE_HMAC_KEY")
            or os.getenv("SIQ_MEETING_VOICEPRINT_TOMBSTONE_PATH")
        )
        if not enabled and not configured:
            if required:
                raise MeetingInvalidOperation("voiceprint tombstone ledger is required")
            return None
        try:
            self._voiceprint_tombstones = VoiceprintTombstoneLedger.from_env()
        except VoiceprintTombstoneError as exc:
            raise MeetingInvalidOperation("voiceprint tombstone ledger is unavailable") from exc
        return self._voiceprint_tombstones

    async def _append_voiceprint_tombstone(
        self,
        profile: MeetingVoiceProfile,
        owner_user_id: int,
        reason: str,
    ) -> None:
        ledger = self._configured_voiceprint_tombstones(required=True)
        assert ledger is not None
        profile_id = profile.id
        await self.session.rollback()
        try:
            await asyncio.to_thread(
                ledger.append,
                owner_user_id=owner_user_id,
                profile_id=profile_id,
                deleted_at=_aware_datetime(utcnow()),
                reason=reason,
            )
        except VoiceprintTombstoneError as exc:
            raise MeetingInvalidOperation("voiceprint tombstone could not be persisted") from exc

    async def reconcile_voiceprint_tombstones(
        self,
        *,
        owner_user_id: int | None = None,
    ) -> dict[str, int]:
        ledger = self._configured_voiceprint_tombstones()
        if ledger is None:
            return {"seen": 0, "purged": 0, "remaining": 0}
        try:
            latest = await asyncio.to_thread(ledger.latest)
        except VoiceprintTombstoneError as exc:
            raise MeetingInvalidOperation("voiceprint tombstone ledger is unreadable") from exc
        entries = [
            entry
            for entry in latest.values()
            if owner_user_id is None or entry.owner_user_id == owner_user_id
        ]
        if not entries:
            return {"seen": 0, "purged": 0, "remaining": 0}
        purged = 0
        remaining = 0
        entries_by_owner: dict[int, list[Any]] = {}
        for entry in entries:
            entries_by_owner.setdefault(entry.owner_user_id, []).append(entry)
        for owner_id in sorted(entries_by_owner):
            owner_entries = entries_by_owner[owner_id]
            for offset in range(0, len(owner_entries), 500):
                batch = owner_entries[offset : offset + 500]
                entry_by_id = {entry.profile_id: entry for entry in batch}
                await self._voiceprint_owner_lock(owner_id)
                profiles = list(
                    (
                        await self.session.exec(
                            select(MeetingVoiceProfile)
                            .where(col(MeetingVoiceProfile.id).in_(list(entry_by_id)))
                            .with_for_update()
                        )
                    ).all()
                )
                if any(profile.owner_user_id != owner_id for profile in profiles):
                    await self.session.rollback()
                    raise MeetingInvalidOperation("voiceprint tombstone ownership does not match")
                profile_ids = [profile.id for profile in profiles]
                consents = (
                    list(
                        (
                            await self.session.exec(
                                select(MeetingVoiceprintConsent)
                                .where(
                                    col(MeetingVoiceprintConsent.voice_profile_id).in_(profile_ids),
                                    MeetingVoiceprintConsent.revoked_at.is_(None),
                                )
                                .with_for_update()
                            )
                        ).all()
                    )
                    if profile_ids
                    else []
                )
                consent_counts: dict[str, int] = {}
                for consent in consents:
                    entry = entry_by_id[consent.voice_profile_id]
                    consent.revoked_at = entry.deleted_at
                    consent_counts[consent.voice_profile_id] = (
                        consent_counts.get(consent.voice_profile_id, 0) + 1
                    )
                    self.session.add(consent)
                now = utcnow()
                for profile in profiles:
                    entry = entry_by_id[profile.id]
                    desired_status = (
                        VoiceProfileStatus.DELETED.value
                        if entry.reason == "deleted"
                        or profile.status == VoiceProfileStatus.DELETED.value
                        else VoiceProfileStatus.REVOKED.value
                    )
                    changed = (
                        profile.status != desired_status
                        or profile.encrypted_embedding is not None
                        or profile.key_id is not None
                        or (
                            desired_status == VoiceProfileStatus.DELETED.value
                            and profile.deleted_at is None
                        )
                    )
                    profile.status = desired_status
                    profile.encrypted_embedding = None
                    profile.key_id = None
                    if desired_status == VoiceProfileStatus.DELETED.value and profile.deleted_at is None:
                        profile.deleted_at = entry.deleted_at
                    if changed or consent_counts.get(profile.id, 0):
                        profile.updated_at = now
                        purged += 1
                    self.session.add(profile)
                await self.session.flush()
                active_consents = 0
                if profile_ids:
                    active_consents = int(
                        (
                            await self.session.exec(
                                select(func.count())
                                .select_from(MeetingVoiceprintConsent)
                                .where(
                                    col(MeetingVoiceprintConsent.voice_profile_id).in_(profile_ids),
                                    MeetingVoiceprintConsent.revoked_at.is_(None),
                                )
                            )
                        ).one()
                        or 0
                    )
                remaining += active_consents
                remaining += sum(
                    1
                    for profile in profiles
                    if profile.encrypted_embedding is not None
                    or profile.key_id is not None
                    or profile.status
                    not in {VoiceProfileStatus.REVOKED.value, VoiceProfileStatus.DELETED.value}
                )
                await self._commit()
        return {"seen": len(entries), "purged": purged, "remaining": remaining}

    async def _voiceprint_enrollment_owner(self, job_id: str) -> int:
        owner_user_id = (
            await self.session.exec(
                select(MeetingSession.owner_user_id)
                .join(MeetingJob, MeetingJob.meeting_id == MeetingSession.id)
                .where(
                    MeetingJob.id == job_id,
                    MeetingJob.job_kind == MeetingJobKind.VOICEPRINT_ENROLL.value,
                )
            )
        ).first()
        if owner_user_id is None:
            raise MeetingResourceNotFound("meeting resource not found")
        return int(owner_user_id)

    async def _owned_session(
        self,
        meeting_id: str,
        owner_user_id: int,
        *,
        lock: bool = False,
        include_deleted: bool = False,
    ) -> MeetingSession:
        statement = select(MeetingSession).where(
            MeetingSession.id == meeting_id,
            MeetingSession.owner_user_id == owner_user_id,
        )
        if not include_deleted:
            statement = statement.where(MeetingSession.state != MeetingState.DELETED.value)
        if lock:
            statement = statement.with_for_update()
        value = (await self.session.exec(statement)).first()
        if value is None:
            raise MeetingResourceNotFound("meeting resource not found")
        return value

    async def _owned_segment(
        self,
        meeting_id: str,
        segment_id: str,
        owner_user_id: int,
        *,
        lock: bool = False,
    ) -> MeetingTranscriptSegment:
        statement = (
            select(MeetingTranscriptSegment)
            .join(MeetingSession, MeetingSession.id == MeetingTranscriptSegment.meeting_id)
            .where(
                MeetingTranscriptSegment.id == segment_id,
                MeetingTranscriptSegment.meeting_id == meeting_id,
                MeetingSession.owner_user_id == owner_user_id,
                MeetingSession.state != MeetingState.DELETED.value,
            )
        )
        if lock:
            statement = statement.with_for_update()
        value = (await self.session.exec(statement)).first()
        if value is None:
            raise MeetingResourceNotFound("meeting resource not found")
        return value

    async def _owned_track(
        self,
        meeting_id: str,
        track_id: str,
        owner_user_id: int,
        *,
        lock: bool = False,
    ) -> MeetingSpeakerTrack:
        statement = (
            select(MeetingSpeakerTrack)
            .join(MeetingSession, MeetingSession.id == MeetingSpeakerTrack.meeting_id)
            .where(
                MeetingSpeakerTrack.id == track_id,
                MeetingSpeakerTrack.meeting_id == meeting_id,
                MeetingSession.owner_user_id == owner_user_id,
                MeetingSession.state != MeetingState.DELETED.value,
            )
        )
        if lock:
            statement = statement.with_for_update()
        value = (await self.session.exec(statement)).first()
        if value is None:
            raise MeetingResourceNotFound("meeting resource not found")
        return value

    async def _idempotent_resource(
        self,
        *,
        owner_user_id: int,
        idempotency_key: str | None,
        operation: str,
        request: Any,
    ) -> str | None:
        if not idempotency_key:
            return None
        existing = (
            await self.session.exec(
                select(MeetingIdempotencyRecord).where(
                    MeetingIdempotencyRecord.owner_user_id == owner_user_id,
                    MeetingIdempotencyRecord.idempotency_key == idempotency_key,
                )
            )
        ).first()
        if existing is None:
            return None
        if existing.operation != operation or existing.request_hash != _request_hash(request):
            raise MeetingIdempotencyConflict("idempotency key was already used for a different request")
        return existing.resource_id

    def _remember_idempotency(
        self,
        *,
        owner_user_id: int,
        idempotency_key: str | None,
        operation: str,
        request: Any,
        resource_id: str,
        response_status: int,
    ) -> None:
        if not idempotency_key:
            return
        self.session.add(
            MeetingIdempotencyRecord(
                owner_user_id=owner_user_id,
                idempotency_key=idempotency_key,
                operation=operation,
                request_hash=_request_hash(request),
                resource_id=resource_id,
                response_status=response_status,
                response_json=encode_json({"resource_id": resource_id}),
            )
        )

    async def create_session(
        self,
        owner_user_id: int,
        request: MeetingCreateRequest,
        *,
        idempotency_key: str | None = None,
    ) -> tuple[MeetingSession, bool, MeetingEvent | None]:
        replay_id = await self._idempotent_resource(
            owner_user_id=owner_user_id,
            idempotency_key=idempotency_key,
            operation="sessions.create",
            request=request,
        )
        if replay_id:
            return await self._owned_session(replay_id, owner_user_id), True, None

        selection = request.model_selection
        value = MeetingSession(
            owner_user_id=owner_user_id,
            title=request.title.strip(),
            language=request.language,
            audio_source=_value(request.audio_source),
            voiceprint_enabled=request.voiceprint_enabled,
            ai_enabled=request.ai_enabled,
            selection_mode=_value(selection.mode),
            requested_model_ref=selection.model_ref,
            fallback_policy=_value(selection.fallback_policy),
        )
        self.session.add(value)
        await self.session.flush()
        lexicon_version = await self._active_lexicon_version(
            owner_user_id,
            request.language,
            meeting_id=None,
        )
        if lexicon_version is None:
            lexicon_version = await self._publish_lexicon(
                owner_user_id,
                request.language,
                meeting_id=None,
                change_reason="initial_owner_snapshot",
                created_by=str(owner_user_id),
            )
        value.active_lexicon_version = lexicon_version.version
        self.session.add(value)
        self.session.add(
            MeetingModelSetting(
                meeting_id=value.id,
                settings_version=1,
                selection_mode=value.selection_mode,
                requested_model_ref=value.requested_model_ref,
                fallback_policy=value.fallback_policy,
                effective_after_segment_ordinal=0,
                cloud_data_boundary_confirmed_at=(
                    utcnow() if selection.cloud_data_boundary_confirmed else None
                ),
                changed_by=str(owner_user_id),
            )
        )
        event = await self.events.append(
            value.id,
            "session.created",
            {"session_id": value.id, "state": value.state, "settings_version": 1},
        )
        self._remember_idempotency(
            owner_user_id=owner_user_id,
            idempotency_key=idempotency_key,
            operation="sessions.create",
            request=request,
            resource_id=value.id,
            response_status=201,
        )
        await self._commit()
        await self.session.refresh(value)
        return value, False, event

    async def list_sessions(
        self,
        owner_user_id: int,
        *,
        offset: int = 0,
        limit: int = 50,
        state: str | None = None,
        q: str | None = None,
        sort: str = "started_at_desc",
    ) -> tuple[list[MeetingSessionSummary], int]:
        filters = [
            MeetingSession.owner_user_id == owner_user_id,
            MeetingSession.state != MeetingState.DELETED.value,
        ]
        if state:
            filters.append(MeetingSession.state == state)
        if q:
            escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            if escaped:
                filters.append(MeetingSession.title.ilike(f"%{escaped}%", escape="\\"))
        total = int(
            (
                await self.session.exec(
                    select(func.count()).select_from(MeetingSession).where(*filters)
                )
            ).one()
            or 0
        )
        started = func.coalesce(MeetingSession.started_at, MeetingSession.created_at)
        order_by = {
            "started_at_desc": (started.desc(), col(MeetingSession.id).desc()),
            "started_at_asc": (started.asc(), col(MeetingSession.id).asc()),
            "updated_at_desc": (col(MeetingSession.updated_at).desc(), col(MeetingSession.id).desc()),
        }.get(sort)
        if order_by is None:
            raise MeetingInvalidOperation("meeting session sort is unsupported")
        speaker_count = (
            select(func.count(func.distinct(MeetingTranscriptSegment.speaker_track_id)))
            .where(
                MeetingTranscriptSegment.meeting_id == MeetingSession.id,
                MeetingTranscriptSegment.speaker_track_id.is_not(None),
            )
            .correlate(MeetingSession)
            .scalar_subquery()
        )
        latest_model_label = (
            select(MeetingModelSnapshot.resolved_model)
            .where(MeetingModelSnapshot.meeting_id == MeetingSession.id)
            .order_by(
                col(MeetingModelSnapshot.resolved_at).desc(),
                col(MeetingModelSnapshot.id).desc(),
            )
            .limit(1)
            .correlate(MeetingSession)
            .scalar_subquery()
        )
        latest_model_locality = (
            select(MeetingModelSnapshot.provider_locality)
            .where(MeetingModelSnapshot.meeting_id == MeetingSession.id)
            .order_by(
                col(MeetingModelSnapshot.resolved_at).desc(),
                col(MeetingModelSnapshot.id).desc(),
            )
            .limit(1)
            .correlate(MeetingSession)
            .scalar_subquery()
        )
        values = (
            await self.session.exec(
                select(
                    MeetingSession,
                    speaker_count.label("speaker_count"),
                    func.coalesce(
                        latest_model_label,
                        MeetingSession.requested_model_ref,
                    ).label("model_label"),
                    latest_model_locality.label("model_locality"),
                )
                .where(*filters)
                .order_by(*order_by)
                .offset(offset)
                .limit(limit)
            )
        ).all()
        return [
            MeetingSessionSummary(
                session=value,
                speaker_count=int(count or 0),
                model_label=model_label,
                model_locality=model_locality,
            )
            for value, count, model_label, model_locality in values
        ], total

    async def get_session(self, meeting_id: str, owner_user_id: int) -> MeetingSession:
        return await self._owned_session(meeting_id, owner_user_id)

    async def update_session(
        self,
        meeting_id: str,
        owner_user_id: int,
        request: MeetingUpdateRequest,
    ) -> tuple[MeetingSession, MeetingEvent]:
        value = await self._owned_session(meeting_id, owner_user_id, lock=True)
        if value.version != request.expected_version:
            raise MeetingVersionConflict(
                "meeting version does not match",
                current={"version": value.version, "title": value.title},
            )
        value.title = request.title.strip()
        value.version += 1
        value.updated_at = utcnow()
        self.session.add(value)
        event = await self.events.append(
            value.id,
            "session.updated",
            {"session_id": value.id, "version": value.version},
        )
        await self._commit()
        return value, event

    async def transition_session(
        self,
        meeting_id: str,
        owner_user_id: int,
        action: str,
    ) -> tuple[MeetingSession, bool, MeetingEvent | None]:
        if action == "delete":
            await self.session.exec(select(User.id).where(User.id == owner_user_id).with_for_update())
        value = await self._owned_session(
            meeting_id,
            owner_user_id,
            lock=True,
            include_deleted=action == "delete",
        )
        try:
            transition = resolve_capture_transition(value.state, action)
        except MeetingTransitionError as exc:
            raise MeetingInvalidOperation(str(exc)) from exc
        if transition.idempotent:
            return value, True, None

        value.state = transition.current
        value.version += 1
        transition_at = utcnow()
        value.updated_at = transition_at
        if action == "start" and value.started_at is None:
            value.started_at = utcnow()
        if transition.increment_stream_epoch:
            value.stream_epoch += 1
            value.last_audio_sequence = -1
        if action == "mark_stopped":
            value.stopped_at = transition_at
        connection = await self.session.connection() if action == "delete" else None
        native_tables_present = bool(
            connection is not None
            and await connection.run_sync(lambda sync: inspect(sync).has_table("meeting_native_captures"))
        )
        if action == "delete" and native_tables_present:
            # Deletion must stop native uploads in the same transaction that
            # makes the meeting unavailable. The retention worker removes the
            # durable rows and files asynchronously after this fail-closed cut.
            await self.session.exec(
                update(MeetingNativeCaptureFinalization)
                .where(MeetingNativeCaptureFinalization.meeting_id == value.id)
                .values(
                    state=NativeCaptureFinalizationState.FAILED.value,
                    lease_owner=None,
                    lease_until=None,
                    retry_after=None,
                    public_error_code="MEETING_DELETED",
                    internal_diagnostic=None,
                    updated_at=transition_at,
                )
            )
            await self.session.exec(
                update(MeetingNativeCapture)
                .where(MeetingNativeCapture.meeting_id == value.id)
                .values(
                    state=NativeCaptureState.REVOKED.value,
                    revoked_at=transition_at,
                    updated_at=transition_at,
                )
            )
            await self.session.exec(
                update(MeetingNativeCaptureToken)
                .where(
                    MeetingNativeCaptureToken.meeting_id == value.id,
                    MeetingNativeCaptureToken.revoked_at.is_(None),
                )
                .values(revoked_at=transition_at)
            )
        self.session.add(value)
        event = await self.events.append(
            value.id,
            "session.state.changed",
            {
                "session_id": value.id,
                "action": action,
                "previous_state": transition.previous,
                "state": transition.current,
                "stream_epoch": value.stream_epoch,
                "version": value.version,
            },
        )
        if action == "mark_stopped" and value.voiceprint_enabled:
            await self._queue_voiceprint_match_jobs(value)
        await self._commit()
        return value, False, event

    async def _queue_voiceprint_match_jobs(self, meeting: MeetingSession) -> list[MeetingJob]:
        tracks = list(
            (
                await self.session.exec(
                    select(MeetingSpeakerTrack)
                    .where(
                        MeetingSpeakerTrack.meeting_id == meeting.id,
                        MeetingSpeakerTrack.label_source == SpeakerLabelSource.ANONYMOUS.value,
                        MeetingSpeakerTrack.display_name.is_(None),
                        MeetingSpeakerTrack.voice_profile_id.is_(None),
                    )
                    .order_by(MeetingSpeakerTrack.created_at, MeetingSpeakerTrack.id)
                    .with_for_update()
                )
            ).all()
        )
        jobs: list[MeetingJob] = []
        for track in tracks:
            job = MeetingJob(
                meeting_id=meeting.id,
                job_kind=MeetingJobKind.VOICEPRINT_MATCH.value,
                idempotency_key=f"{meeting.id}:voiceprint-match:{track.id}:v1",
                input_watermark=meeting.last_segment_ordinal,
                settings_version=meeting.settings_version,
                input_json=encode_json(
                    {
                        "schema_version": "meeting.voiceprint_match.input.v1",
                        "task": "voiceprint_match",
                        "speaker_track_id": track.id,
                    }
                ),
            )
            self.session.add(job)
            await self.session.flush()
            jobs.append(job)
            await self.events.append(
                meeting.id,
                "voiceprint.match.queued",
                {"job_id": job.id, "speaker_track_id": track.id},
            )
        return jobs

    async def finalize_session(
        self,
        meeting_id: str,
        owner_user_id: int,
    ) -> tuple[MeetingSession, bool, MeetingJob | None, MeetingEvent | None]:
        value = await self._owned_session(meeting_id, owner_user_id, lock=True)
        try:
            transition = resolve_finalize_transition(value.state, value.postprocess_state)
        except MeetingTransitionError as exc:
            raise MeetingInvalidOperation(str(exc)) from exc
        if transition.idempotent:
            existing = (
                await self.session.exec(
                    select(MeetingJob).where(
                        MeetingJob.meeting_id == meeting_id,
                        MeetingJob.idempotency_key == f"{meeting_id}:finalize:v1",
                    )
                )
            ).first()
            return value, True, existing, None

        value.postprocess_state = transition.current
        value.updated_at = utcnow()
        job = MeetingJob(
            meeting_id=meeting_id,
            job_kind=MeetingJobKind.FINAL_TRANSCRIPT.value,
            idempotency_key=f"{meeting_id}:finalize:v1",
            input_watermark=value.last_segment_ordinal,
            settings_version=value.settings_version,
        )
        self.session.add(value)
        self.session.add(job)
        event = await self.events.append(
            meeting_id,
            "postprocess.queued",
            {"session_id": meeting_id, "job_id": job.id, "input_watermark": job.input_watermark},
        )
        await self._commit()
        return value, False, job, event

    async def request_delete(
        self,
        meeting_id: str,
        owner_user_id: int,
    ) -> tuple[MeetingSession, bool, MeetingJob | None]:
        value, idempotent, _ = await self.transition_session(meeting_id, owner_user_id, "delete")
        if idempotent:
            job = (
                await self.session.exec(
                    select(MeetingJob).where(
                        MeetingJob.meeting_id == meeting_id,
                        MeetingJob.job_kind == MeetingJobKind.DELETE.value,
                    )
                )
            ).first()
            return value, True, job
        job = MeetingJob(
            meeting_id=meeting_id,
            job_kind=MeetingJobKind.DELETE.value,
            idempotency_key=f"{meeting_id}:delete:v1",
            input_watermark=value.last_segment_ordinal,
            settings_version=value.settings_version,
        )
        self.session.add(job)
        await self._commit()
        return value, False, job

    async def append_stable_segment(
        self,
        meeting_id: str,
        owner_user_id: int,
        request: StableSegmentInput,
        *,
        trace_id: str | None = None,
        speaker_track_key: str | None = None,
        speaker_confidence: float | None = None,
    ) -> tuple[MeetingTranscriptSegment, bool, MeetingEvent | None]:
        meeting = await self._owned_session(meeting_id, owner_user_id, lock=True)
        existing = (
            await self.session.exec(
                select(MeetingTranscriptSegment).where(
                    MeetingTranscriptSegment.meeting_id == meeting_id,
                    MeetingTranscriptSegment.provider_segment_key == request.provider_segment_key,
                )
            )
        ).first()
        if existing is not None:
            same = (
                existing.start_ms == request.start_ms
                and existing.end_ms == request.end_ms
                and existing.raw_text == request.raw_text
                and existing.asr_final_text == request.asr_final_text
            )
            if not same:
                raise MeetingIdempotencyConflict("provider segment key was reused with different content")
            return existing, True, None

        track: MeetingSpeakerTrack | None = None
        speaker_track_id = request.speaker_track_id
        if speaker_track_id is not None:
            track = await self._owned_track(meeting_id, speaker_track_id, owner_user_id)
        elif speaker_track_key:
            track = (
                await self.session.exec(
                    select(MeetingSpeakerTrack).where(
                        MeetingSpeakerTrack.meeting_id == meeting_id,
                        MeetingSpeakerTrack.track_key == speaker_track_key,
                    )
                )
            ).first()
            if track is None:
                track_count = int(
                    (
                        await self.session.exec(
                            select(func.count()).select_from(MeetingSpeakerTrack).where(
                                MeetingSpeakerTrack.meeting_id == meeting_id
                            )
                        )
                    ).one()
                    or 0
                )
                track = MeetingSpeakerTrack(
                    meeting_id=meeting_id,
                    track_key=speaker_track_key,
                    anonymous_label=f"发言人 {track_count + 1}",
                    match_confidence=speaker_confidence,
                )
                self.session.add(track)
                await self.session.flush()
                await self.events.append(
                    meeting_id,
                    "speaker.track.created",
                    {
                        "track_id": track.id,
                        "track_key": track.track_key,
                        "anonymous_label": track.anonymous_label,
                        "speaker": speaker_response(track).model_dump(mode="json"),
                    },
                    trace_id=trace_id,
                )
            speaker_track_id = track.id

        ordinal = meeting.last_segment_ordinal + 1
        segment = MeetingTranscriptSegment(
            meeting_id=meeting_id,
            ordinal=ordinal,
            utterance_id=request.utterance_id,
            provider_segment_key=request.provider_segment_key,
            start_ms=request.start_ms,
            end_ms=request.end_ms,
            speaker_track_id=speaker_track_id,
            raw_text=request.raw_text,
            asr_final_text=request.asr_final_text,
            normalized_text=request.normalized_text,
            asr_confidence=request.asr_confidence,
            asr_provider=request.asr_provider,
            asr_model=request.asr_model,
            asr_version=request.asr_version,
            hotword_version=request.hotword_version,
            word_timestamps_json=encode_json(request.word_timestamps),
            asr_metadata_json=encode_json(request.asr_metadata),
            overlap=request.overlap,
            noise_level=request.noise_level,
        )
        meeting.last_segment_ordinal = ordinal
        meeting.updated_at = utcnow()
        self.session.add(meeting)
        self.session.add(segment)
        await self.session.flush()
        segment_snapshot = transcript_event_payload(self._segment_response(segment, None, track))
        event = await self.events.append(
            meeting_id,
            "transcript.segment.stable",
            {
                "segment_id": segment.id,
                "ordinal": ordinal,
                "utterance_id": segment.utterance_id,
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
                "speaker_track_id": segment.speaker_track_id,
                "text": segment.normalized_text or segment.asr_final_text,
                "display_layer": "normalized" if segment.normalized_text else "stable_asr",
                "word_timestamps": request.word_timestamps,
                "segment": segment_snapshot,
            },
            trace_id=trace_id,
        )
        await self._commit()
        return segment, False, event

    async def _latest_revisions(
        self,
        segment_ids: list[str],
    ) -> dict[str, MeetingSegmentRevision]:
        if not segment_ids:
            return {}
        values = (
            await self.session.exec(
                select(MeetingSegmentRevision)
                .where(col(MeetingSegmentRevision.segment_id).in_(segment_ids))
                .order_by(MeetingSegmentRevision.segment_id, col(MeetingSegmentRevision.revision_no).desc())
            )
        ).all()
        latest: dict[str, MeetingSegmentRevision] = {}
        for value in values:
            latest.setdefault(value.segment_id, value)
        return latest

    @staticmethod
    def _segment_response(
        segment: MeetingTranscriptSegment,
        revision: MeetingSegmentRevision | None,
        speaker: MeetingSpeakerTrack | None,
    ) -> TranscriptSegmentResponse:
        if revision is not None:
            display_text = revision.text
            display_layer = (
                "human_verified"
                if revision.revision_type in {"manual", "revert"}
                else "llm_corrected"
                if revision.revision_type == "llm_correction"
                else "normalized"
            )
            revision_no = revision.revision_no
        elif segment.normalized_text is not None:
            display_text = segment.normalized_text
            display_layer = "normalized"
            revision_no = 0
        else:
            display_text = segment.asr_final_text
            display_layer = "stable_asr"
            revision_no = 0
        return TranscriptSegmentResponse(
            id=segment.id,
            meeting_id=segment.meeting_id,
            ordinal=segment.ordinal,
            utterance_id=segment.utterance_id,
            provider_segment_key=segment.provider_segment_key,
            start_ms=segment.start_ms,
            end_ms=segment.end_ms,
            speaker_track_id=segment.speaker_track_id,
            speaker_label=(speaker.display_name or speaker.anonymous_label) if speaker else None,
            raw_text=segment.raw_text,
            asr_final_text=segment.asr_final_text,
            normalized_text=segment.normalized_text,
            display_text=display_text,
            current_revision_no=revision_no,
            display_layer=display_layer,
            asr_confidence=segment.asr_confidence,
            asr_provider=segment.asr_provider,
            asr_model=segment.asr_model,
            asr_version=segment.asr_version,
            hotword_version=segment.hotword_version,
            word_timestamps=decode_json(segment.word_timestamps_json, []),
            overlap=segment.overlap,
            noise_level=segment.noise_level,
            human_locked=segment.human_locked,
            created_at=segment.created_at,
            updated_at=segment.updated_at,
        )

    async def transcript_page(
        self,
        meeting_id: str,
        owner_user_id: int,
        *,
        after_ordinal: int = 0,
        limit: int = 200,
    ) -> tuple[list[TranscriptSegmentResponse], int | None]:
        await self._owned_session(meeting_id, owner_user_id)
        segments = list(
            (
                await self.session.exec(
                    select(MeetingTranscriptSegment)
                    .where(
                        MeetingTranscriptSegment.meeting_id == meeting_id,
                        MeetingTranscriptSegment.ordinal > after_ordinal,
                    )
                    .order_by(MeetingTranscriptSegment.ordinal)
                    .limit(limit + 1)
                )
            ).all()
        )
        has_more = len(segments) > limit
        page = segments[:limit]
        revisions = await self._latest_revisions([item.id for item in page])
        track_ids = {item.speaker_track_id for item in page if item.speaker_track_id}
        tracks: dict[str, MeetingSpeakerTrack] = {}
        if track_ids:
            track_values = (
                await self.session.exec(
                    select(MeetingSpeakerTrack).where(col(MeetingSpeakerTrack.id).in_(track_ids))
                )
            ).all()
            tracks = {item.id: item for item in track_values}
        responses = [
            self._segment_response(item, revisions.get(item.id), tracks.get(item.speaker_track_id or ""))
            for item in page
        ]
        return responses, page[-1].ordinal if has_more and page else None

    async def list_speakers(
        self,
        meeting_id: str,
        owner_user_id: int,
    ) -> list[MeetingSpeakerTrack]:
        await self._owned_session(meeting_id, owner_user_id)
        return list(
            (
                await self.session.exec(
                    select(MeetingSpeakerTrack)
                    .where(MeetingSpeakerTrack.meeting_id == meeting_id)
                    .order_by(MeetingSpeakerTrack.created_at)
                )
            ).all()
        )

    async def rename_speaker(
        self,
        meeting_id: str,
        track_id: str,
        owner_user_id: int,
        request: SpeakerRenameRequest,
    ) -> tuple[MeetingSpeakerTrack, MeetingEvent]:
        track = await self._owned_track(meeting_id, track_id, owner_user_id, lock=True)
        if track.version != request.expected_version:
            raise MeetingVersionConflict(
                "speaker track version does not match",
                current={"version": track.version, "display_name": track.display_name},
            )
        track.display_name = request.display_name.strip()
        track.label_source = SpeakerLabelSource.MANUAL.value
        track.version += 1
        track.updated_at = utcnow()
        self.session.add(track)
        await self._mark_minutes_artifacts_stale(
            meeting_id,
            reason="speaker_rename",
        )
        event = await self.events.append(
            meeting_id,
            "speaker.label.changed",
            {"speaker": speaker_response(track).model_dump(mode="json")},
        )
        await self._commit()
        return track, event

    async def merge_speakers(
        self,
        meeting_id: str,
        target_track_id: str,
        owner_user_id: int,
        request: SpeakerMergeRequest,
    ) -> SpeakerMappingResponse:
        meeting = await self._owned_session(meeting_id, owner_user_id, lock=True)
        self._require_post_meeting_speaker_edit(meeting)
        source_ids = list(request.source_track_ids)
        if target_track_id in source_ids:
            raise MeetingInvalidOperation("target speaker cannot also be a merge source")
        track_ids = [target_track_id, *source_ids]
        if set(request.expected_versions) != set(track_ids):
            raise MeetingInvalidOperation("expected_versions must cover the target and every source")
        tracks = list(
            (
                await self.session.exec(
                    select(MeetingSpeakerTrack)
                    .where(
                        MeetingSpeakerTrack.meeting_id == meeting_id,
                        col(MeetingSpeakerTrack.id).in_(track_ids),
                    )
                    .with_for_update()
                )
            ).all()
        )
        by_id = {track.id: track for track in tracks}
        if len(by_id) != len(track_ids):
            raise MeetingResourceNotFound("meeting resource not found")
        for track_id in track_ids:
            track = by_id[track_id]
            expected = request.expected_versions[track_id]
            if track.version != expected:
                raise MeetingVersionConflict(
                    "speaker track version does not match",
                    current={"track_id": track.id, "version": track.version},
                )

        segments = list(
            (
                await self.session.exec(
                    select(MeetingTranscriptSegment)
                    .where(
                        MeetingTranscriptSegment.meeting_id == meeting_id,
                        col(MeetingTranscriptSegment.speaker_track_id).in_(source_ids),
                    )
                    .order_by(MeetingTranscriptSegment.ordinal)
                    .with_for_update()
                )
            ).all()
        )
        if not segments:
            raise MeetingInvalidOperation("merge sources do not contain any transcript segments")
        target = by_id[target_track_id]
        for segment in segments:
            segment.speaker_track_id = target.id
            segment.updated_at = utcnow()
            self.session.add(segment)
        now = utcnow()
        for track in tracks:
            track.version += 1
            track.updated_at = now
            self.session.add(track)
        segment_ids = [segment.id for segment in segments]
        event = await self.events.append(
            meeting_id,
            "speaker.track.merged",
            {
                "schema_version": "siq.meeting.speaker_mapping.v1",
                "operation": "merge",
                "automatic": False,
                "source_track_ids": source_ids,
                "target_track_id": target.id,
                "segment_ids": segment_ids,
                "changed_by": str(owner_user_id),
            },
        )
        await self._commit()
        return SpeakerMappingResponse(
            operation="merge",
            meeting_id=meeting_id,
            source_track_ids=source_ids,
            target_track_ids=[target.id],
            segment_ids=segment_ids,
            event_id=event.event_id,
            event_cursor=event.cursor,
            tracks=[speaker_response(by_id[track_id]) for track_id in track_ids],
        )

    async def split_speaker(
        self,
        meeting_id: str,
        source_track_id: str,
        owner_user_id: int,
        request: SpeakerSplitRequest,
    ) -> SpeakerMappingResponse:
        meeting = await self._owned_session(meeting_id, owner_user_id, lock=True)
        self._require_post_meeting_speaker_edit(meeting)
        source = await self._owned_track(meeting_id, source_track_id, owner_user_id, lock=True)
        if source.version != request.expected_version:
            raise MeetingVersionConflict(
                "speaker track version does not match",
                current={"track_id": source.id, "version": source.version},
            )

        selected: list[MeetingTranscriptSegment] = []
        for offset in range(0, len(request.segment_ids), 500):
            batch = request.segment_ids[offset : offset + 500]
            selected.extend(
                list(
                    (
                        await self.session.exec(
                            select(MeetingTranscriptSegment)
                            .where(
                                MeetingTranscriptSegment.meeting_id == meeting_id,
                                col(MeetingTranscriptSegment.id).in_(batch),
                            )
                            .with_for_update()
                        )
                    ).all()
                )
            )
        if len(selected) != len(request.segment_ids):
            raise MeetingResourceNotFound("meeting resource not found")
        if any(segment.speaker_track_id != source.id for segment in selected):
            raise MeetingInvalidOperation("split segments must currently belong to the source speaker")
        source_segment_count = int(
            (
                await self.session.exec(
                    select(func.count())
                    .select_from(MeetingTranscriptSegment)
                    .where(
                        MeetingTranscriptSegment.meeting_id == meeting_id,
                        MeetingTranscriptSegment.speaker_track_id == source.id,
                    )
                )
            ).one()
        )
        if source_segment_count <= len(selected):
            raise MeetingInvalidOperation("split must leave at least one segment on the source speaker")

        track_count = int(
            (
                await self.session.exec(
                    select(func.count())
                    .select_from(MeetingSpeakerTrack)
                    .where(MeetingSpeakerTrack.meeting_id == meeting_id)
                )
            ).one()
        )
        display_name = request.display_name.strip() if request.display_name else None
        target = MeetingSpeakerTrack(
            meeting_id=meeting_id,
            track_key=f"manual-split-{uuid4().hex}",
            anonymous_label=f"发言人 {track_count + 1}",
            display_name=display_name,
            label_source=(
                SpeakerLabelSource.MANUAL.value if display_name else SpeakerLabelSource.ANONYMOUS.value
            ),
        )
        self.session.add(target)
        await self.session.flush()
        for segment in selected:
            segment.speaker_track_id = target.id
            segment.updated_at = utcnow()
            self.session.add(segment)
        source.version += 1
        source.updated_at = utcnow()
        self.session.add(source)
        segment_ids = [segment.id for segment in sorted(selected, key=lambda item: item.ordinal)]
        event = await self.events.append(
            meeting_id,
            "speaker.track.split",
            {
                "schema_version": "siq.meeting.speaker_mapping.v1",
                "operation": "split",
                "automatic": False,
                "source_track_id": source.id,
                "target_track_ids": [target.id],
                "segment_ids": segment_ids,
                "changed_by": str(owner_user_id),
            },
        )
        await self._commit()
        return SpeakerMappingResponse(
            operation="split",
            meeting_id=meeting_id,
            source_track_ids=[source.id],
            target_track_ids=[target.id],
            segment_ids=segment_ids,
            event_id=event.event_id,
            event_cursor=event.cursor,
            tracks=[speaker_response(source), speaker_response(target)],
        )

    @staticmethod
    def _require_post_meeting_speaker_edit(meeting: MeetingSession) -> None:
        if meeting.state not in {
            MeetingState.STOPPED.value,
            MeetingState.ARCHIVED.value,
            MeetingState.INTERRUPTED.value,
        }:
            raise MeetingInvalidOperation("speaker merge and split are only available after capture stops")

    async def correct_segment(
        self,
        meeting_id: str,
        segment_id: str,
        owner_user_id: int,
        request: SegmentCorrectionRequest,
        *,
        idempotency_key: str | None = None,
        correction_learning_enabled: bool = False,
    ) -> SegmentCorrectionResponse:
        if idempotency_key:
            existing_feedback = (
                await self.session.exec(
                    select(MeetingASRCorrectionEvent).where(
                        MeetingASRCorrectionEvent.owner_user_id == owner_user_id,
                        MeetingASRCorrectionEvent.idempotency_key == idempotency_key,
                    )
                )
            ).first()
            if existing_feedback is not None:
                if existing_feedback.meeting_id != meeting_id or existing_feedback.segment_id != segment_id:
                    raise MeetingIdempotencyConflict(
                        "idempotency key was already used for another correction"
                    )
                segment = await self._owned_segment(meeting_id, segment_id, owner_user_id)
                revision = (
                    await self.session.exec(
                        select(MeetingSegmentRevision).where(
                            MeetingSegmentRevision.segment_id == segment_id,
                            MeetingSegmentRevision.revision_no == existing_feedback.result_revision_no,
                        )
                    )
                ).first()
                if revision is None or revision.text != request.text:
                    raise MeetingIdempotencyConflict(
                        "idempotency key was already used for a different correction"
                    )
                latest = (await self._latest_revisions([segment.id])).get(segment.id)
                return SegmentCorrectionResponse(
                    segment=self._segment_response(segment, latest, None),
                    revision=revision_response(revision),
                    feedback=correction_response(existing_feedback),
                    candidate_ids=[],
                )

        segment = await self._owned_segment(
            meeting_id,
            segment_id,
            owner_user_id,
            lock=True,
        )
        latest = (await self._latest_revisions([segment.id])).get(segment.id)
        current_revision = latest.revision_no if latest else 0
        current_text = latest.text if latest else (segment.normalized_text or segment.asr_final_text)
        if current_revision != request.expected_revision:
            raise MeetingVersionConflict(
                "segment revision does not match",
                current={"revision": current_revision, "text": current_text},
            )
        corrected_text = request.text.strip()
        if corrected_text == current_text:
            raise MeetingInvalidOperation("corrected text is unchanged")

        candidates = (
            validated_candidate_terms(current_text, corrected_text, request.candidate_terms)
            if correction_learning_enabled
            else []
        )
        error_class = classify_correction(
            current_text,
            corrected_text,
            request.edit_intent,
            has_candidate_terms=bool(candidates),
        )
        eligible = correction_learning_enabled and contribution_is_eligible(
            original=current_text,
            corrected=corrected_text,
            intent=request.edit_intent,
            requested=request.contribute_to_accuracy,
            error_class=error_class,
        )
        next_revision = current_revision + 1
        revision = MeetingSegmentRevision(
            segment_id=segment.id,
            revision_no=next_revision,
            revision_type="manual",
            text=corrected_text,
            base_revision_no=current_revision,
            reason_codes_json=encode_json([_value(request.edit_intent), _value(error_class)]),
            created_by=str(owner_user_id),
        )
        feedback = MeetingASRCorrectionEvent(
            owner_user_id=owner_user_id,
            meeting_id=meeting_id,
            segment_id=segment.id,
            speaker_track_id=segment.speaker_track_id,
            base_revision_no=current_revision,
            result_revision_no=next_revision,
            original_text=current_text,
            corrected_text=corrected_text,
            diff_ops_json=encode_json(calculate_diff(current_text, corrected_text)),
            edit_intent=_value(request.edit_intent),
            error_class=_value(error_class),
            contribute_to_accuracy=eligible,
            status=(
                CorrectionStatus.ACTIVE.value
                if correction_learning_enabled
                else CorrectionStatus.EXCLUDED.value
            ),
            asr_provider=segment.asr_provider,
            asr_model=segment.asr_model,
            asr_version=segment.asr_version,
            hotword_version=segment.hotword_version,
            audio_start_ms=segment.start_ms,
            audio_end_ms=segment.end_ms,
            idempotency_key=idempotency_key,
            created_by=str(owner_user_id),
        )
        segment.human_locked = True
        segment.updated_at = utcnow()
        self.session.add(segment)
        self.session.add(revision)
        self.session.add(feedback)
        await self.session.flush()

        candidate_ids: list[str] = []
        created_candidates: list[MeetingTermCandidate] = []
        if eligible:
            for submitted in candidates:
                candidate = (
                    await self.session.exec(
                        select(MeetingTermCandidate).where(
                            MeetingTermCandidate.owner_user_id == owner_user_id,
                            MeetingTermCandidate.language == "zh-CN",
                            MeetingTermCandidate.canonical_term == submitted.canonical_term,
                            MeetingTermCandidate.misrecognition == submitted.misrecognition,
                        )
                    )
                ).first()
                if candidate is None:
                    candidate = MeetingTermCandidate(
                        owner_user_id=owner_user_id,
                        canonical_term=submitted.canonical_term,
                        misrecognition=submitted.misrecognition,
                        language="zh-CN",
                        candidate_type=(
                            "confusion_pair" if submitted.misrecognition else "hotword"
                        ),
                        confidence=0.5,
                    )
                    self.session.add(candidate)
                    await self.session.flush()
                    created_candidates.append(candidate)
                else:
                    existing_for_meeting = (
                        await self.session.exec(
                            select(MeetingTermCandidateSource)
                            .where(
                                MeetingTermCandidateSource.candidate_id == candidate.id,
                                MeetingTermCandidateSource.meeting_id == meeting_id,
                            )
                            .limit(1)
                        )
                    ).first()
                    candidate.source_count += 1
                    if existing_for_meeting is None:
                        candidate.distinct_meeting_count += 1
                    candidate.confidence = min(0.95, candidate.confidence + 0.1)
                    candidate.updated_at = utcnow()
                    self.session.add(candidate)
                self.session.add(
                    MeetingTermCandidateSource(
                        candidate_id=candidate.id,
                        correction_event_id=feedback.id,
                        meeting_id=meeting_id,
                    )
                )
                candidate_ids.append(candidate.id)

        speaker = (
            await self.session.get(MeetingSpeakerTrack, segment.speaker_track_id)
            if segment.speaker_track_id
            else None
        )
        segment_snapshot = transcript_event_payload(
            self._segment_response(segment, revision, speaker)
        )
        await self.events.append(
            meeting_id,
            "transcript.segment.human_edited",
            {
                "segment_id": segment.id,
                "revision_no": next_revision,
                "text": corrected_text,
                "display_layer": "human_verified",
                "feedback_id": feedback.id,
                "segment": segment_snapshot,
            },
        )
        await self.events.append(
            meeting_id,
            "asr.feedback.recorded",
            {
                "feedback_id": feedback.id,
                "segment_id": segment.id,
                "error_class": feedback.error_class,
                "contribute_to_accuracy": feedback.contribute_to_accuracy,
            },
        )
        for candidate in created_candidates:
            await self.events.append(
                meeting_id,
                "lexicon.candidate.created",
                {
                    "candidate_id": candidate.id,
                    "candidate_type": candidate.candidate_type,
                    "status": candidate.status,
                },
            )
        await self._mark_minutes_artifacts_stale(
            meeting_id,
            reason="segment_correction",
            from_ordinal=segment.ordinal,
        )
        await self._commit()
        return SegmentCorrectionResponse(
            segment=self._segment_response(segment, revision, speaker),
            revision=revision_response(revision),
            feedback=correction_response(feedback),
            candidate_ids=candidate_ids,
        )

    async def revert_segment(
        self,
        meeting_id: str,
        segment_id: str,
        owner_user_id: int,
        *,
        revision_no: int,
        expected_revision: int,
    ) -> TranscriptSegmentResponse:
        segment = await self._owned_segment(meeting_id, segment_id, owner_user_id, lock=True)
        latest = (await self._latest_revisions([segment.id])).get(segment.id)
        current_revision = latest.revision_no if latest else 0
        if current_revision != expected_revision:
            raise MeetingVersionConflict(
                "segment revision does not match",
                current={"revision": current_revision, "text": latest.text if latest else segment.asr_final_text},
            )
        if revision_no >= current_revision:
            raise MeetingInvalidOperation("revert target must be older than the current revision")
        target: MeetingSegmentRevision | None = None
        if revision_no > 0:
            target = (
                await self.session.exec(
                    select(MeetingSegmentRevision).where(
                        MeetingSegmentRevision.segment_id == segment_id,
                        MeetingSegmentRevision.revision_no == revision_no,
                    )
                )
            ).first()
            if target is None:
                raise MeetingResourceNotFound("meeting resource not found")
        target_text = target.text if target else (segment.normalized_text or segment.asr_final_text)
        revert_revision = MeetingSegmentRevision(
            segment_id=segment_id,
            revision_no=current_revision + 1,
            revision_type="revert",
            text=target_text,
            base_revision_no=current_revision,
            reason_codes_json=encode_json(["user_revert", f"target:{revision_no}"]),
            created_by=str(owner_user_id),
        )
        affected_feedback = list(
            (
                await self.session.exec(
                    select(MeetingASRCorrectionEvent).where(
                        MeetingASRCorrectionEvent.owner_user_id == owner_user_id,
                        MeetingASRCorrectionEvent.segment_id == segment_id,
                        MeetingASRCorrectionEvent.result_revision_no > revision_no,
                        MeetingASRCorrectionEvent.status.in_([
                            CorrectionStatus.ACTIVE.value,
                            CorrectionStatus.PROMOTED.value,
                        ]),
                    )
                )
            ).all()
        )
        for feedback in affected_feedback:
            feedback.status = CorrectionStatus.REVERTED.value
            feedback.updated_at = utcnow()
            self.session.add(feedback)
            source_links = (
                await self.session.exec(
                    select(MeetingTermCandidateSource).where(
                        MeetingTermCandidateSource.correction_event_id == feedback.id
                    )
                )
            ).all()
            for source in source_links:
                candidate = await self.session.get(MeetingTermCandidate, source.candidate_id)
                if candidate is not None:
                    candidate.reverted_count += 1
                    candidate.confidence = max(0.0, candidate.confidence - 0.2)
                    if candidate.source_count <= candidate.reverted_count:
                        candidate.status = TermCandidateStatus.DEPRECATED.value
                    candidate.updated_at = utcnow()
                    self.session.add(candidate)
        segment.human_locked = revision_no > 0
        segment.updated_at = utcnow()
        self.session.add(segment)
        self.session.add(revert_revision)
        speaker = (
            await self.session.get(MeetingSpeakerTrack, segment.speaker_track_id)
            if segment.speaker_track_id
            else None
        )
        segment_snapshot = transcript_event_payload(
            self._segment_response(segment, revert_revision, speaker)
        )
        await self.events.append(
            meeting_id,
            "transcript.segment.human_edited",
            {
                "segment_id": segment_id,
                "revision_no": revert_revision.revision_no,
                "target_revision_no": revision_no,
                "text": target_text,
                "display_layer": "human_verified" if revision_no > 0 else "stable_asr",
                "segment": segment_snapshot,
            },
        )
        await self._mark_minutes_artifacts_stale(
            meeting_id,
            reason="segment_revert",
            from_ordinal=segment.ordinal,
        )
        await self._commit()
        return self._segment_response(segment, revert_revision, speaker)

    async def list_correction_feedback(
        self,
        owner_user_id: int,
        *,
        status: str | None = None,
        meeting_id: str | None = None,
        limit: int = 100,
    ) -> list[MeetingASRCorrectionEvent]:
        statement = select(MeetingASRCorrectionEvent).where(
            MeetingASRCorrectionEvent.owner_user_id == owner_user_id
        )
        if status:
            statement = statement.where(MeetingASRCorrectionEvent.status == status)
        if meeting_id:
            await self._owned_session(meeting_id, owner_user_id, include_deleted=True)
            statement = statement.where(MeetingASRCorrectionEvent.meeting_id == meeting_id)
        return list(
            (
                await self.session.exec(
                    statement.order_by(col(MeetingASRCorrectionEvent.created_at).desc()).limit(limit)
                )
            ).all()
        )

    async def get_correction_feedback(
        self,
        feedback_id: str,
        owner_user_id: int,
    ) -> MeetingASRCorrectionEvent:
        value = (
            await self.session.exec(
                select(MeetingASRCorrectionEvent).where(
                    MeetingASRCorrectionEvent.id == feedback_id,
                    MeetingASRCorrectionEvent.owner_user_id == owner_user_id,
                )
            )
        ).first()
        if value is None:
            raise MeetingResourceNotFound("meeting resource not found")
        return value

    async def set_correction_feedback_excluded(
        self,
        feedback_id: str,
        owner_user_id: int,
        *,
        excluded: bool,
        correction_learning_enabled: bool = False,
    ) -> MeetingASRCorrectionEvent:
        value = await self.get_correction_feedback(feedback_id, owner_user_id)
        if excluded:
            if value.status in {CorrectionStatus.REVERTED.value, CorrectionStatus.EXCLUDED.value}:
                return value
            value.status = CorrectionStatus.EXCLUDED.value
            value.contribute_to_accuracy = False
        else:
            if value.status != CorrectionStatus.EXCLUDED.value:
                return value
            if not correction_learning_enabled:
                value.contribute_to_accuracy = False
                return value
            eligible = contribution_is_eligible(
                original=value.original_text,
                corrected=value.corrected_text,
                intent=value.edit_intent,
                requested=True,
                error_class=value.error_class,
            )
            value.status = CorrectionStatus.ACTIVE.value
            value.contribute_to_accuracy = eligible
        value.updated_at = utcnow()
        self.session.add(value)
        await self._commit()
        return value

    async def list_term_candidates(
        self,
        owner_user_id: int,
        *,
        status: str | None = None,
    ) -> list[MeetingTermCandidate]:
        statement = select(MeetingTermCandidate).where(
            MeetingTermCandidate.owner_user_id == owner_user_id
        )
        if status:
            statement = statement.where(MeetingTermCandidate.status == status)
        return list(
            (
                await self.session.exec(
                    statement.order_by(col(MeetingTermCandidate.updated_at).desc())
                )
            ).all()
        )

    async def _owned_candidate(
        self,
        candidate_id: str,
        owner_user_id: int,
        *,
        lock: bool = False,
    ) -> MeetingTermCandidate:
        statement = select(MeetingTermCandidate).where(
            MeetingTermCandidate.id == candidate_id,
            MeetingTermCandidate.owner_user_id == owner_user_id,
        )
        if lock:
            statement = statement.with_for_update()
        value = (await self.session.exec(statement)).first()
        if value is None:
            raise MeetingResourceNotFound("meeting resource not found")
        return value

    async def reject_term_candidate(
        self,
        candidate_id: str,
        owner_user_id: int,
    ) -> MeetingTermCandidate:
        value = await self._owned_candidate(candidate_id, owner_user_id, lock=True)
        if value.status == TermCandidateStatus.REJECTED.value:
            return value
        if value.status == TermCandidateStatus.PROMOTED.value:
            raise MeetingInvalidOperation("promoted candidates must be managed through the lexicon")
        value.status = TermCandidateStatus.REJECTED.value
        value.updated_at = utcnow()
        self.session.add(value)
        await self._commit()
        return value

    async def _active_lexicon_entries(
        self,
        owner_user_id: int,
        language: str,
        *,
        meeting_id: str | None = None,
    ) -> list[MeetingLexiconEntry]:
        future_scope = (
            (MeetingLexiconEntry.scope == LexiconScope.USER_FUTURE_MEETINGS.value)
            & MeetingLexiconEntry.meeting_id.is_(None)
        )
        scope_filter = (
            future_scope
            if meeting_id is None
            else or_(
                future_scope,
                (
                    (MeetingLexiconEntry.scope == LexiconScope.CURRENT_MEETING.value)
                    & (MeetingLexiconEntry.meeting_id == meeting_id)
                ),
            )
        )
        return list(
            (
                await self.session.exec(
                    select(MeetingLexiconEntry)
                    .where(
                        MeetingLexiconEntry.owner_user_id == owner_user_id,
                        MeetingLexiconEntry.language == language,
                        MeetingLexiconEntry.status == LexiconEntryStatus.ACTIVE.value,
                        scope_filter,
                    )
                    .order_by(MeetingLexiconEntry.canonical_term, MeetingLexiconEntry.id)
                )
            ).all()
        )

    async def _active_lexicon_version(
        self,
        owner_user_id: int,
        language: str,
        *,
        meeting_id: str | None,
        lock: bool = False,
    ) -> MeetingLexiconVersion | None:
        statement = select(MeetingLexiconVersion).where(
            MeetingLexiconVersion.owner_user_id == owner_user_id,
            MeetingLexiconVersion.language == language,
            MeetingLexiconVersion.is_active.is_(True),
            (
                MeetingLexiconVersion.meeting_id.is_(None)
                if meeting_id is None
                else MeetingLexiconVersion.meeting_id == meeting_id
            ),
        ).order_by(col(MeetingLexiconVersion.version).desc())
        if lock:
            statement = statement.with_for_update()
        return (await self.session.exec(statement)).first()

    async def _publish_lexicon(
        self,
        owner_user_id: int,
        language: str,
        *,
        meeting_id: str | None,
        change_reason: str,
        created_by: str,
    ) -> MeetingLexiconVersion:
        if meeting_id is not None:
            await self._owned_session(meeting_id, owner_user_id, include_deleted=False)
        entries = await self._active_lexicon_entries(
            owner_user_id,
            language,
            meeting_id=meeting_id,
        )
        entry_payload = [lexicon_entry_response(item).model_dump(mode="json") for item in entries]
        assert_unambiguous_confusions(entry_payload)
        digest, snapshot_json = entries_hash(entry_payload)
        current = await self._active_lexicon_version(
            owner_user_id,
            language,
            meeting_id=meeting_id,
            lock=True,
        )
        max_version = int(
            (
                await self.session.exec(
                    select(func.max(MeetingLexiconVersion.version)).where(
                        MeetingLexiconVersion.owner_user_id == owner_user_id,
                        MeetingLexiconVersion.language == language,
                    )
                )
            ).one()
            or 0
        )
        if current is not None:
            current.is_active = False
            self.session.add(current)
        version = MeetingLexiconVersion(
            owner_user_id=owner_user_id,
            meeting_id=meeting_id,
            version=max_version + 1,
            language=language,
            entries_hash=digest,
            entry_count=len(entries),
            entries_json=snapshot_json,
            change_reason=change_reason,
            created_by=created_by,
            supersedes_version=current.version if current else None,
            is_active=True,
        )
        self.session.add(version)
        await self.session.flush()
        return version

    async def _refresh_active_meeting_lexicons(
        self,
        owner_user_id: int,
        language: str,
        owner_version: MeetingLexiconVersion,
    ) -> None:
        meeting_ids = list(
            (
                await self.session.exec(
                    select(MeetingSession.id).where(
                        MeetingSession.owner_user_id == owner_user_id,
                        MeetingSession.language == language,
                        col(MeetingSession.state).in_(
                            [
                                MeetingState.DRAFT.value,
                                MeetingState.CONNECTING.value,
                                MeetingState.LIVE.value,
                                MeetingState.PAUSED.value,
                                MeetingState.RECONNECTING.value,
                                MeetingState.INTERRUPTED.value,
                            ]
                        ),
                    )
                )
            ).all()
        )
        for meeting_id in meeting_ids:
            has_current_entry = (
                await self.session.exec(
                    select(MeetingLexiconEntry.id)
                    .where(
                        MeetingLexiconEntry.owner_user_id == owner_user_id,
                        MeetingLexiconEntry.language == language,
                        MeetingLexiconEntry.meeting_id == meeting_id,
                        MeetingLexiconEntry.scope == LexiconScope.CURRENT_MEETING.value,
                        MeetingLexiconEntry.status == LexiconEntryStatus.ACTIVE.value,
                    )
                    .limit(1)
                )
            ).first()
            version = owner_version
            if has_current_entry is not None:
                version = await self._publish_lexicon(
                    owner_user_id,
                    language,
                    meeting_id=meeting_id,
                    change_reason="owner_lexicon_changed",
                    created_by=str(owner_user_id),
                )
            await self._append_lexicon_version_events(
                owner_user_id,
                version,
                {meeting_id},
            )

    async def _append_lexicon_version_events(
        self,
        owner_user_id: int,
        version: MeetingLexiconVersion,
        meeting_ids: set[str],
    ) -> None:
        for meeting_id in sorted(meeting_ids):
            meeting = (
                await self.session.exec(
                    select(MeetingSession)
                    .where(
                        MeetingSession.id == meeting_id,
                        MeetingSession.owner_user_id == owner_user_id,
                        MeetingSession.state != MeetingState.DELETED.value,
                    )
                    .with_for_update()
                )
            ).first()
            if meeting is None:
                continue
            if version.meeting_id is not None and version.meeting_id != meeting.id:
                raise MeetingInvalidOperation("meeting lexicon version belongs to another meeting")
            meeting.active_lexicon_version = version.version
            meeting.updated_at = utcnow()
            self.session.add(meeting)
            await self.events.append(
                meeting.id,
                "lexicon.version.activated",
                {
                    "version": version.version,
                    "language": version.language,
                    "entries_hash": version.entries_hash,
                    "entry_count": version.entry_count,
                    "effective_after_segment_ordinal": meeting.last_segment_ordinal,
                },
            )

    async def confirm_term_candidate(
        self,
        candidate_id: str,
        owner_user_id: int,
        *,
        correction_learning_enabled: bool = False,
    ) -> tuple[MeetingTermCandidate, MeetingLexiconEntry, MeetingLexiconVersion]:
        if not correction_learning_enabled:
            raise MeetingInvalidOperation("correction learning is disabled")
        candidate = await self._owned_candidate(candidate_id, owner_user_id, lock=True)
        existing = (
            await self.session.exec(
                select(MeetingLexiconEntry).where(
                    MeetingLexiconEntry.owner_user_id == owner_user_id,
                    MeetingLexiconEntry.source_candidate_id == candidate_id,
                    MeetingLexiconEntry.status != LexiconEntryStatus.DELETED.value,
                )
            )
        ).first()
        if candidate.status == TermCandidateStatus.PROMOTED.value and existing is not None:
            current_version = await self._active_lexicon_version(
                owner_user_id,
                candidate.language,
                meeting_id=None,
            )
            if current_version is None:
                current_version = await self._publish_lexicon(
                    owner_user_id,
                    candidate.language,
                    meeting_id=None,
                    change_reason="candidate_confirmed",
                    created_by=str(owner_user_id),
                )
                await self._commit()
            return candidate, existing, current_version
        if candidate.status in {TermCandidateStatus.REJECTED.value, TermCandidateStatus.DEPRECATED.value}:
            raise MeetingInvalidOperation("rejected or deprecated candidates cannot be confirmed")

        entry = existing or MeetingLexiconEntry(
            owner_user_id=owner_user_id,
            language=candidate.language,
            canonical_term=candidate.canonical_term,
            aliases_json="[]",
            misrecognitions_json=encode_json(
                [candidate.misrecognition] if candidate.misrecognition else []
            ),
            entry_type="confusion_pair" if candidate.misrecognition else "hotword",
            weight=min(10.0, 4.0 + candidate.confidence * 4.0),
            scope="user_future_meetings",
            status=LexiconEntryStatus.ACTIVE.value,
            source_candidate_id=candidate.id,
            created_by=str(owner_user_id),
        )
        candidate.status = TermCandidateStatus.PROMOTED.value
        candidate.confirmed_count += 1
        candidate.updated_at = utcnow()
        self.session.add(candidate)
        self.session.add(entry)
        await self.session.flush()
        version = await self._publish_lexicon(
            owner_user_id,
            candidate.language,
            meeting_id=None,
            change_reason="candidate_confirmed",
            created_by=str(owner_user_id),
        )
        source_feedback = (
            await self.session.exec(
                select(MeetingASRCorrectionEvent)
                .join(
                    MeetingTermCandidateSource,
                    MeetingTermCandidateSource.correction_event_id == MeetingASRCorrectionEvent.id,
                )
                .where(MeetingTermCandidateSource.candidate_id == candidate.id)
            )
        ).all()
        for feedback in source_feedback:
            if feedback.status == CorrectionStatus.ACTIVE.value:
                feedback.status = CorrectionStatus.PROMOTED.value
                feedback.updated_at = utcnow()
                self.session.add(feedback)
        await self._refresh_active_meeting_lexicons(owner_user_id, candidate.language, version)
        await self._commit()
        return candidate, entry, version

    async def create_lexicon_entry(
        self,
        owner_user_id: int,
        request: LexiconEntryCreateRequest,
    ) -> tuple[MeetingLexiconEntry, MeetingLexiconVersion]:
        if request.meeting_id:
            await self._owned_session(request.meeting_id, owner_user_id)
        canonical = request.canonical_term.strip()
        duplicate = (
            await self.session.exec(
                select(MeetingLexiconEntry).where(
                    MeetingLexiconEntry.owner_user_id == owner_user_id,
                    MeetingLexiconEntry.language == request.language,
                    MeetingLexiconEntry.canonical_term == canonical,
                    MeetingLexiconEntry.scope == _value(request.scope),
                    MeetingLexiconEntry.meeting_id == request.meeting_id,
                    MeetingLexiconEntry.status != LexiconEntryStatus.DELETED.value,
                )
            )
        ).first()
        if duplicate is not None:
            raise MeetingVersionConflict(
                "lexicon entry already exists",
                current={"entry_id": duplicate.id, "status": duplicate.status},
            )
        entry = MeetingLexiconEntry(
            owner_user_id=owner_user_id,
            language=request.language,
            canonical_term=canonical,
            aliases_json=encode_json(sorted({value.strip() for value in request.aliases if value.strip()})),
            misrecognitions_json=encode_json(
                sorted({value.strip() for value in request.misrecognitions if value.strip()})
            ),
            entry_type=_value(request.entry_type),
            weight=request.weight,
            scope=_value(request.scope),
            meeting_id=request.meeting_id,
            created_by=str(owner_user_id),
        )
        self.session.add(entry)
        await self.session.flush()
        version = await self._publish_lexicon(
            owner_user_id,
            request.language,
            meeting_id=request.meeting_id,
            change_reason="manual_entry_created",
            created_by=str(owner_user_id),
        )
        if request.meeting_id:
            await self._append_lexicon_version_events(
                owner_user_id,
                version,
                {request.meeting_id},
            )
        else:
            await self._refresh_active_meeting_lexicons(owner_user_id, request.language, version)
        await self._commit()
        return entry, version

    async def _owned_lexicon_entry(
        self,
        entry_id: str,
        owner_user_id: int,
        *,
        lock: bool = False,
    ) -> MeetingLexiconEntry:
        statement = select(MeetingLexiconEntry).where(
            MeetingLexiconEntry.id == entry_id,
            MeetingLexiconEntry.owner_user_id == owner_user_id,
        )
        if lock:
            statement = statement.with_for_update()
        value = (await self.session.exec(statement)).first()
        if value is None:
            raise MeetingResourceNotFound("meeting resource not found")
        return value

    async def update_lexicon_entry(
        self,
        entry_id: str,
        owner_user_id: int,
        request: LexiconEntryUpdateRequest,
    ) -> tuple[MeetingLexiconEntry, MeetingLexiconVersion]:
        value = await self._owned_lexicon_entry(entry_id, owner_user_id, lock=True)
        if value.status == LexiconEntryStatus.DELETED.value:
            raise MeetingResourceNotFound("meeting resource not found")
        if request.weight is not None:
            value.weight = request.weight
        if request.status is not None:
            value.status = _value(request.status)
        previous_meeting_id = value.meeting_id
        if request.scope is not None:
            if _value(request.scope) == "current_meeting" and not value.meeting_id:
                raise MeetingInvalidOperation("current meeting scope requires a meeting_id")
            value.scope = _value(request.scope)
            if value.scope == LexiconScope.USER_FUTURE_MEETINGS.value:
                value.meeting_id = None
        value.updated_at = utcnow()
        self.session.add(value)
        version = await self._publish_lexicon(
            owner_user_id,
            value.language,
            meeting_id=value.meeting_id,
            change_reason="entry_updated",
            created_by=str(owner_user_id),
        )
        if value.meeting_id:
            await self._append_lexicon_version_events(
                owner_user_id,
                version,
                {value.meeting_id},
            )
        else:
            await self._refresh_active_meeting_lexicons(owner_user_id, value.language, version)
            if previous_meeting_id:
                await self._append_lexicon_version_events(
                    owner_user_id,
                    version,
                    {previous_meeting_id},
                )
        await self._commit()
        return value, version

    async def delete_lexicon_entry(
        self,
        entry_id: str,
        owner_user_id: int,
    ) -> tuple[MeetingLexiconEntry, MeetingLexiconVersion]:
        value = await self._owned_lexicon_entry(entry_id, owner_user_id, lock=True)
        if value.status != LexiconEntryStatus.DELETED.value:
            value.status = LexiconEntryStatus.DELETED.value
            value.updated_at = utcnow()
            self.session.add(value)
        version = await self._publish_lexicon(
            owner_user_id,
            value.language,
            meeting_id=value.meeting_id,
            change_reason="entry_deleted",
            created_by=str(owner_user_id),
        )
        if value.meeting_id:
            await self._append_lexicon_version_events(
                owner_user_id,
                version,
                {value.meeting_id},
            )
        else:
            await self._refresh_active_meeting_lexicons(owner_user_id, value.language, version)
        await self._commit()
        return value, version

    async def get_lexicon(
        self,
        owner_user_id: int,
        *,
        language: str = "zh-CN",
        meeting_id: str | None = None,
    ) -> LexiconResponse:
        version: MeetingLexiconVersion | None
        if meeting_id is not None:
            meeting = await self._owned_session(meeting_id, owner_user_id)
            if meeting.language != language:
                raise MeetingInvalidOperation("meeting lexicon language does not match")
            version = None
            if meeting.active_lexicon_version is not None:
                version = (
                    await self.session.exec(
                        select(MeetingLexiconVersion).where(
                            MeetingLexiconVersion.owner_user_id == owner_user_id,
                            MeetingLexiconVersion.language == language,
                            MeetingLexiconVersion.version == meeting.active_lexicon_version,
                            or_(
                                MeetingLexiconVersion.meeting_id.is_(None),
                                MeetingLexiconVersion.meeting_id == meeting_id,
                            ),
                        )
                    )
                ).first()
        else:
            version = await self._active_lexicon_version(
                owner_user_id,
                language,
                meeting_id=None,
            )
        entries = await self._active_lexicon_entries(
            owner_user_id,
            language,
            meeting_id=meeting_id,
        )
        return LexiconResponse(
            language=language,
            active_version=lexicon_version_response(version) if version else None,
            entries=[lexicon_entry_response(item) for item in entries],
        )

    async def get_meeting_lexicon_snapshot(
        self,
        meeting_id: str,
        owner_user_id: int,
    ) -> tuple[int | None, list[dict[str, Any]]]:
        meeting = await self._owned_session(meeting_id, owner_user_id)
        if meeting.active_lexicon_version is None:
            return None, []
        version = (
            await self.session.exec(
                select(MeetingLexiconVersion).where(
                    MeetingLexiconVersion.owner_user_id == owner_user_id,
                    MeetingLexiconVersion.language == meeting.language,
                    MeetingLexiconVersion.version == meeting.active_lexicon_version,
                    or_(
                        MeetingLexiconVersion.meeting_id.is_(None),
                        MeetingLexiconVersion.meeting_id == meeting_id,
                    ),
                )
            )
        ).first()
        if version is None:
            raise MeetingInvalidOperation("meeting lexicon version is unavailable")
        snapshot = decode_json(version.entries_json, [])
        if not isinstance(snapshot, list):
            raise MeetingInvalidOperation("meeting lexicon snapshot is invalid")
        entries = [
            item
            for item in snapshot
            if isinstance(item, dict)
            and (
                item.get("scope") == LexiconScope.USER_FUTURE_MEETINGS.value
                or (
                    item.get("scope") == LexiconScope.CURRENT_MEETING.value
                    and item.get("meeting_id") == meeting_id
                )
            )
        ]
        return version.version, entries

    async def list_lexicon_versions(
        self,
        owner_user_id: int,
        *,
        language: str = "zh-CN",
        meeting_id: str | None = None,
    ) -> list[MeetingLexiconVersion]:
        if meeting_id is not None:
            await self._owned_session(meeting_id, owner_user_id)
        return list(
            (
                await self.session.exec(
                    select(MeetingLexiconVersion)
                    .where(
                        MeetingLexiconVersion.owner_user_id == owner_user_id,
                        MeetingLexiconVersion.language == language,
                        (
                            MeetingLexiconVersion.meeting_id.is_(None)
                            if meeting_id is None
                            else MeetingLexiconVersion.meeting_id == meeting_id
                        ),
                    )
                    .order_by(col(MeetingLexiconVersion.version).desc())
                )
            ).all()
        )

    async def activate_lexicon_version(
        self,
        owner_user_id: int,
        version: int,
        *,
        language: str = "zh-CN",
        meeting_id: str | None = None,
    ) -> MeetingLexiconVersion:
        if meeting_id is not None:
            await self._owned_session(meeting_id, owner_user_id)
        selected = (
            await self.session.exec(
                select(MeetingLexiconVersion).where(
                    MeetingLexiconVersion.owner_user_id == owner_user_id,
                    MeetingLexiconVersion.language == language,
                    MeetingLexiconVersion.version == version,
                    (
                        MeetingLexiconVersion.meeting_id.is_(None)
                        if meeting_id is None
                        else MeetingLexiconVersion.meeting_id == meeting_id
                    ),
                )
            )
        ).first()
        if selected is None:
            raise MeetingResourceNotFound("meeting resource not found")
        await self.session.exec(
            update(MeetingLexiconVersion)
            .where(
                MeetingLexiconVersion.owner_user_id == owner_user_id,
                MeetingLexiconVersion.language == language,
                (
                    MeetingLexiconVersion.meeting_id.is_(None)
                    if meeting_id is None
                    else MeetingLexiconVersion.meeting_id == meeting_id
                ),
            )
            .values(is_active=False)
        )
        selected.is_active = True
        self.session.add(selected)
        if meeting_id is None:
            await self._refresh_active_meeting_lexicons(owner_user_id, language, selected)
        else:
            await self._append_lexicon_version_events(owner_user_id, selected, {meeting_id})
        await self._commit()
        return selected

    async def create_voice_profile(
        self,
        owner_user_id: int,
        display_name: str,
    ) -> MeetingVoiceProfile:
        value = MeetingVoiceProfile(
            owner_user_id=owner_user_id,
            display_name=display_name.strip(),
        )
        self.session.add(value)
        await self._commit()
        return value

    async def _owned_voice_profile(
        self,
        profile_id: str,
        owner_user_id: int,
        *,
        lock: bool = False,
        include_deleted: bool = False,
    ) -> MeetingVoiceProfile:
        statement = select(MeetingVoiceProfile).where(
            MeetingVoiceProfile.id == profile_id,
            MeetingVoiceProfile.owner_user_id == owner_user_id,
        )
        if not include_deleted:
            statement = statement.where(MeetingVoiceProfile.status != VoiceProfileStatus.DELETED.value)
        if lock:
            statement = statement.with_for_update()
        value = (await self.session.exec(statement)).first()
        if value is None:
            raise MeetingResourceNotFound("meeting resource not found")
        return value

    async def _active_consent(
        self,
        profile_id: str,
        owner_user_id: int,
        *,
        lock: bool = False,
    ) -> MeetingVoiceprintConsent | None:
        statement = (
            select(MeetingVoiceprintConsent)
            .where(
                MeetingVoiceprintConsent.voice_profile_id == profile_id,
                MeetingVoiceprintConsent.actor_user_id == owner_user_id,
                MeetingVoiceprintConsent.revoked_at.is_(None),
            )
            .order_by(col(MeetingVoiceprintConsent.granted_at).desc())
        )
        if lock:
            statement = statement.with_for_update()
        return (await self.session.exec(statement)).first()

    async def list_voice_profiles(
        self,
        owner_user_id: int,
    ) -> list[tuple[MeetingVoiceProfile, bool]]:
        await self.reconcile_voiceprint_tombstones(owner_user_id=owner_user_id)
        profiles = list(
            (
                await self.session.exec(
                    select(MeetingVoiceProfile)
                    .where(
                        MeetingVoiceProfile.owner_user_id == owner_user_id,
                        MeetingVoiceProfile.status != VoiceProfileStatus.DELETED.value,
                    )
                    .order_by(MeetingVoiceProfile.display_name)
                )
            ).all()
        )
        values: list[tuple[MeetingVoiceProfile, bool]] = []
        for profile in profiles:
            values.append((profile, await self._active_consent(profile.id, owner_user_id) is not None))
        return values

    async def set_voice_profile_status(
        self,
        profile_id: str,
        owner_user_id: int,
        target: VoiceProfileStatus,
    ) -> tuple[MeetingVoiceProfile, bool]:
        if target in {VoiceProfileStatus.REVOKED, VoiceProfileStatus.DELETED}:
            preflight = await self._owned_voice_profile(
                profile_id,
                owner_user_id,
                include_deleted=target == VoiceProfileStatus.DELETED,
            )
            await self._append_voiceprint_tombstone(
                preflight,
                owner_user_id,
                "deleted" if target == VoiceProfileStatus.DELETED else "revoked",
            )
        else:
            await self.reconcile_voiceprint_tombstones(owner_user_id=owner_user_id)
        await self._voiceprint_owner_lock(owner_user_id)
        value = await self._owned_voice_profile(
            profile_id,
            owner_user_id,
            lock=True,
            include_deleted=target == VoiceProfileStatus.DELETED,
        )
        try:
            idempotent = validate_voice_profile_transition(value.status, target.value)
        except MeetingTransitionError as exc:
            raise MeetingInvalidOperation(str(exc)) from exc
        destructive = target in {VoiceProfileStatus.REVOKED, VoiceProfileStatus.DELETED}
        if idempotent and not destructive:
            return value, True
        now = utcnow()
        if not idempotent:
            value.status = target.value
        if destructive:
            value.encrypted_embedding = None
            value.key_id = None
            consents = list(
                (
                    await self.session.exec(
                        select(MeetingVoiceprintConsent)
                        .where(
                            MeetingVoiceprintConsent.voice_profile_id == profile_id,
                            MeetingVoiceprintConsent.revoked_at.is_(None),
                        )
                        .with_for_update()
                    )
                ).all()
            )
            for consent in consents:
                consent.revoked_at = now
                self.session.add(consent)
        if target == VoiceProfileStatus.DELETED and value.deleted_at is None:
            value.deleted_at = now
        value.updated_at = now
        self.session.add(value)
        await self._commit()
        return value, idempotent

    async def enroll_voiceprint(
        self,
        meeting_id: str,
        track_id: str,
        owner_user_id: int,
        request: VoiceprintEnrollmentRequest,
        *,
        idempotency_key: str,
    ) -> VoiceprintEnrollmentResponse:
        await self.reconcile_voiceprint_tombstones(owner_user_id=owner_user_id)
        await self._voiceprint_owner_lock(owner_user_id)
        meeting = await self._owned_session(meeting_id, owner_user_id)
        if not meeting.voiceprint_enabled:
            raise MeetingInvalidOperation("voiceprint is not enabled for this meeting")
        track = await self._owned_track(meeting_id, track_id, owner_user_id)
        if request.source_track_id != track_id:
            raise MeetingInvalidOperation("source_track_id must match the route track")
        profile = await self._owned_voice_profile(request.voice_profile_id, owner_user_id, lock=True)
        existing_job = (
            await self.session.exec(
                select(MeetingJob).where(MeetingJob.idempotency_key == idempotency_key)
            )
        ).first()
        if existing_job is not None:
            payload = decode_json(existing_job.input_json, {})
            if (
                existing_job.meeting_id != meeting_id
                or existing_job.job_kind != MeetingJobKind.VOICEPRINT_ENROLL.value
                or payload.get("voice_profile_id") != profile.id
                or payload.get("speaker_track_id") != track.id
                or not payload.get("consent_id")
            ):
                raise MeetingIdempotencyConflict("idempotency key belongs to another meeting")
            consent = (
                await self.session.exec(
                    select(MeetingVoiceprintConsent).where(
                        MeetingVoiceprintConsent.id == str(payload["consent_id"]),
                        MeetingVoiceprintConsent.voice_profile_id == profile.id,
                        MeetingVoiceprintConsent.actor_user_id == owner_user_id,
                        MeetingVoiceprintConsent.source_meeting_id == meeting_id,
                        MeetingVoiceprintConsent.policy_version == request.policy_version,
                        MeetingVoiceprintConsent.revoked_at.is_(None),
                    )
                )
            ).first()
            if consent is None:
                raise MeetingIdempotencyConflict("enrollment replay no longer has active consent")
            return VoiceprintEnrollmentResponse(
                voice_profile=voice_profile_response(profile, consent_active=True),
                consent_id=consent.id,
                job_id=existing_job.id,
            )

        consent = MeetingVoiceprintConsent(
            voice_profile_id=profile.id,
            actor_user_id=owner_user_id,
            subject_label=profile.display_name,
            policy_version=request.policy_version,
            source_meeting_id=meeting_id,
            metadata_json=encode_json({"source_track_id": track.id, "schema_version": "meeting.consent.v1"}),
        )
        job = MeetingJob(
            meeting_id=meeting_id,
            job_kind=MeetingJobKind.VOICEPRINT_ENROLL.value,
            idempotency_key=idempotency_key,
            input_watermark=0,
            settings_version=meeting.settings_version,
            input_json=encode_json(
                {
                    "schema_version": "meeting.voiceprint_enroll.input.v1",
                    "voice_profile_id": profile.id,
                    "consent_id": consent.id,
                    "speaker_track_id": track.id,
                }
            ),
        )
        consent.metadata_json = encode_json(
            {
                "schema_version": "meeting.voiceprint_consent.metadata.v1",
                "source_track_id": track.id,
                "job_id": job.id,
            }
        )
        self.session.add(consent)
        self.session.add(job)
        await self.events.append(
            meeting_id,
            "voiceprint.enrollment.queued",
            {"voice_profile_id": profile.id, "track_id": track.id, "job_id": job.id},
        )
        await self._commit()
        return VoiceprintEnrollmentResponse(
            voice_profile=voice_profile_response(profile, consent_active=True),
            consent_id=consent.id,
            job_id=job.id,
        )

    async def revoke_voiceprint_consent(
        self,
        profile_id: str,
        owner_user_id: int,
    ) -> MeetingVoiceProfile:
        preflight = await self._owned_voice_profile(profile_id, owner_user_id)
        await self._append_voiceprint_tombstone(preflight, owner_user_id, "revoked")
        await self._voiceprint_owner_lock(owner_user_id)
        profile = await self._owned_voice_profile(profile_id, owner_user_id, lock=True)
        consents = list(
            (
                await self.session.exec(
                    select(MeetingVoiceprintConsent)
                    .where(
                        MeetingVoiceprintConsent.voice_profile_id == profile_id,
                        MeetingVoiceprintConsent.revoked_at.is_(None),
                    )
                    .with_for_update()
                )
            ).all()
        )
        revoked_at = utcnow()
        for consent in consents:
            consent.revoked_at = revoked_at
            self.session.add(consent)
        if profile.status != VoiceProfileStatus.REVOKED.value:
            try:
                validate_voice_profile_transition(profile.status, VoiceProfileStatus.REVOKED.value)
            except MeetingTransitionError as exc:
                raise MeetingInvalidOperation(str(exc)) from exc
            profile.status = VoiceProfileStatus.REVOKED.value
            profile.updated_at = utcnow()
        profile.encrypted_embedding = None
        profile.key_id = None
        self.session.add(profile)
        for consent in consents:
            await self.events.append(
                consent.source_meeting_id,
                "voiceprint.consent.revoked",
                {"voice_profile_id": profile.id, "consent_id": consent.id},
            )
        await self._commit()
        return profile

    async def decide_voice_match(
        self,
        meeting_id: str,
        match_id: str,
        owner_user_id: int,
        decision: VoiceMatchDecision,
    ) -> tuple[MeetingVoiceprintMatch, bool]:
        await self.reconcile_voiceprint_tombstones(owner_user_id=owner_user_id)
        await self._owned_session(meeting_id, owner_user_id)
        value = (
            await self.session.exec(
                select(MeetingVoiceprintMatch)
                .join(MeetingVoiceProfile, MeetingVoiceProfile.id == MeetingVoiceprintMatch.voice_profile_id)
                .where(
                    MeetingVoiceprintMatch.id == match_id,
                    MeetingVoiceprintMatch.meeting_id == meeting_id,
                    MeetingVoiceProfile.owner_user_id == owner_user_id,
                )
                .with_for_update()
            )
        ).first()
        if value is None:
            raise MeetingResourceNotFound("meeting resource not found")
        requested_decision = VoiceMatchDecision(_value(decision))
        try:
            idempotent = validate_voice_match_transition(value.decision, requested_decision.value)
        except MeetingTransitionError as exc:
            raise MeetingInvalidOperation(str(exc)) from exc
        if idempotent:
            return value, True
        profile: MeetingVoiceProfile | None = None
        if requested_decision == VoiceMatchDecision.CONFIRMED:
            profile = await self._owned_voice_profile(value.voice_profile_id, owner_user_id, lock=True)
            consent = await self._active_consent(profile.id, owner_user_id, lock=True)
            if profile.status != VoiceProfileStatus.ACTIVE.value or consent is None:
                raise MeetingInvalidOperation("voice profile consent is no longer valid")
        value.decision = requested_decision.value
        value.decided_by = str(owner_user_id)
        value.decided_at = utcnow()
        self.session.add(value)
        track = await self._owned_track(meeting_id, value.speaker_track_id, owner_user_id, lock=True)
        speaker_changed = False
        if (
            requested_decision == VoiceMatchDecision.CONFIRMED
            and track.label_source != SpeakerLabelSource.MANUAL.value
        ):
            assert profile is not None
            track.display_name = profile.display_name
            track.voice_profile_id = profile.id
            track.match_confidence = value.top1_score
            track.label_source = SpeakerLabelSource.VOICEPRINT_CONFIRMED.value
            track.version += 1
            track.updated_at = utcnow()
            self.session.add(track)
            speaker_changed = True
        elif (
            requested_decision == VoiceMatchDecision.UNDONE
            and track.label_source != SpeakerLabelSource.MANUAL.value
        ):
            track.display_name = None
            track.voice_profile_id = None
            track.match_confidence = None
            track.label_source = SpeakerLabelSource.ANONYMOUS.value
            track.version += 1
            track.updated_at = utcnow()
            self.session.add(track)
            speaker_changed = True
        event_type = {
            VoiceMatchDecision.CONFIRMED: "voiceprint.match.applied",
            VoiceMatchDecision.REJECTED: "voiceprint.match.rejected",
            VoiceMatchDecision.UNDONE: "voiceprint.match.undone",
        }[requested_decision]
        await self.events.append(
            meeting_id,
            event_type,
            {
                "match_id": value.id,
                "speaker_track_id": value.speaker_track_id,
                "decision": requested_decision.value,
            },
        )
        if speaker_changed:
            await self.events.append(
                meeting_id,
                "speaker.label.changed",
                {"speaker": speaker_response(track).model_dump(mode="json")},
            )
        await self._commit()
        return value, False

    async def update_model_selection(
        self,
        meeting_id: str,
        owner_user_id: int,
        selection: ModelSelectionInput,
        *,
        expected_settings_version: int,
        provider_locality: str | None = None,
    ) -> tuple[MeetingSession, MeetingModelSetting, MeetingEvent]:
        meeting = await self._owned_session(meeting_id, owner_user_id, lock=True)
        if meeting.settings_version != expected_settings_version:
            raise MeetingVersionConflict(
                "model settings version does not match",
                current={
                    "settings_version": meeting.settings_version,
                    "selection_mode": meeting.selection_mode,
                    "requested_model_ref": meeting.requested_model_ref,
                    "fallback_policy": meeting.fallback_policy,
                },
            )
        if provider_locality == "cloud" and not selection.cloud_data_boundary_confirmed:
            raise MeetingInvalidOperation("cloud model selection requires explicit data-boundary confirmation")
        meeting.settings_version += 1
        meeting.selection_mode = _value(selection.mode)
        meeting.requested_model_ref = selection.model_ref
        meeting.fallback_policy = _value(selection.fallback_policy)
        meeting.ai_enabled = selection.mode != ModelSelectionMode.NONE
        meeting.updated_at = utcnow()
        setting = MeetingModelSetting(
            meeting_id=meeting.id,
            settings_version=meeting.settings_version,
            selection_mode=meeting.selection_mode,
            requested_model_ref=meeting.requested_model_ref,
            fallback_policy=meeting.fallback_policy,
            effective_after_segment_ordinal=meeting.last_segment_ordinal,
            cloud_data_boundary_confirmed_at=(
                utcnow() if selection.cloud_data_boundary_confirmed else None
            ),
            changed_by=str(owner_user_id),
        )
        self.session.add(meeting)
        self.session.add(setting)
        event = await self.events.append(
            meeting_id,
            "model.selection.changed",
            {
                "settings_version": setting.settings_version,
                "selection_mode": setting.selection_mode,
                "model_ref": setting.requested_model_ref,
                "fallback_policy": setting.fallback_policy,
                "effective_after_segment_ordinal": setting.effective_after_segment_ordinal,
            },
        )
        await self._commit()
        return meeting, setting, event

    async def _mark_minutes_artifacts_stale(
        self,
        meeting_id: str,
        *,
        reason: str,
        from_ordinal: int | None = None,
    ) -> list[str]:
        filters = [
            MeetingArtifact.meeting_id == meeting_id,
            col(MeetingArtifact.artifact_type).in_(
                [
                    ArtifactType.ROLLING_MINUTES.value,
                    ArtifactType.FINAL_MINUTES.value,
                ]
            ),
            MeetingArtifact.state == ArtifactState.READY.value,
        ]
        if from_ordinal is not None:
            filters.append(MeetingArtifact.input_to_ordinal >= from_ordinal)
        values = list(
            (
                await self.session.exec(
                    select(MeetingArtifact)
                    .where(*filters)
                    .order_by(MeetingArtifact.artifact_type, MeetingArtifact.version)
                    .with_for_update()
                )
            ).all()
        )
        if not values:
            return []
        now = utcnow()
        for artifact in values:
            artifact.state = ArtifactState.STALE.value
            artifact.updated_at = now
            self.session.add(artifact)
        artifact_ids = [artifact.id for artifact in values]
        await self.events.append(
            meeting_id,
            "minutes.artifacts.stale",
            {
                "artifact_ids": artifact_ids,
                "reason": reason,
                "from_ordinal": from_ordinal,
            },
        )
        return artifact_ids

    async def list_artifacts(
        self,
        meeting_id: str,
        owner_user_id: int,
    ) -> list[MeetingArtifact]:
        await self._owned_session(meeting_id, owner_user_id)
        return list(
            (
                await self.session.exec(
                    select(MeetingArtifact)
                    .where(MeetingArtifact.meeting_id == meeting_id)
                    .order_by(MeetingArtifact.artifact_type, col(MeetingArtifact.version).desc())
                )
            ).all()
        )

    async def get_artifact(
        self,
        meeting_id: str,
        artifact_id: str,
        owner_user_id: int,
    ) -> MeetingArtifact:
        value = (
            await self.session.exec(
                select(MeetingArtifact)
                .join(MeetingSession, MeetingSession.id == MeetingArtifact.meeting_id)
                .where(
                    MeetingArtifact.id == artifact_id,
                    MeetingArtifact.meeting_id == meeting_id,
                    MeetingSession.owner_user_id == owner_user_id,
                    MeetingSession.state != MeetingState.DELETED.value,
                )
            )
        ).first()
        if value is None:
            raise MeetingResourceNotFound("meeting resource not found")
        return value

    async def regenerate_artifact(
        self,
        meeting_id: str,
        artifact_id: str,
        owner_user_id: int,
        *,
        expected_settings_version: int,
    ) -> tuple[MeetingArtifact, MeetingJob]:
        previous = await self.get_artifact(meeting_id, artifact_id, owner_user_id)
        if previous.artifact_type not in {
            ArtifactType.ROLLING_MINUTES.value,
            ArtifactType.FINAL_MINUTES.value,
        }:
            raise MeetingInvalidOperation("only meeting minutes artifacts can be regenerated")
        meeting = await self._owned_session(meeting_id, owner_user_id, lock=True)
        if meeting.settings_version != expected_settings_version:
            raise MeetingVersionConflict(
                "model settings version does not match",
                current={"settings_version": meeting.settings_version},
            )
        max_version = int(
            (
                await self.session.exec(
                    select(func.max(MeetingArtifact.version)).where(
                        MeetingArtifact.meeting_id == meeting_id,
                        MeetingArtifact.artifact_type == previous.artifact_type,
                    )
                )
            ).one()
            or 0
        )
        previous.state = ArtifactState.STALE.value
        previous.updated_at = utcnow()
        artifact = MeetingArtifact(
            meeting_id=meeting_id,
            artifact_type=previous.artifact_type,
            version=max_version + 1,
            state=ArtifactState.GENERATING.value,
            input_from_ordinal=1,
            input_to_ordinal=meeting.last_segment_ordinal,
            transcript_revision=previous.transcript_revision,
            supersedes_id=previous.id,
        )
        job_kind = (
            MeetingJobKind.FINAL_MINUTES.value
            if previous.artifact_type != ArtifactType.ROLLING_MINUTES.value
            else MeetingJobKind.ROLLING_MINUTES.value
        )
        job = MeetingJob(
            meeting_id=meeting_id,
            job_kind=job_kind,
            idempotency_key=(
                f"{meeting_id}:artifact:{previous.artifact_type}:{artifact.version}:"
                f"settings:{meeting.settings_version}"
            ),
            input_watermark=meeting.last_segment_ordinal,
            settings_version=meeting.settings_version,
        )
        self.session.add(previous)
        self.session.add(artifact)
        self.session.add(job)
        await self.events.append(
            meeting_id,
            "artifact.regeneration.queued",
            {"artifact_id": artifact.id, "supersedes_id": previous.id, "job_id": job.id},
        )
        await self._commit()
        return artifact, job

    async def create_export(
        self,
        meeting_id: str,
        owner_user_id: int,
        request: MeetingExportCreateRequest,
        *,
        idempotency_key: str | None = None,
    ) -> tuple[MeetingArtifact, MeetingJob, bool, MeetingEvent | None]:
        meeting = await self._owned_session(meeting_id, owner_user_id, lock=True)
        replay_id = await self._idempotent_resource(
            owner_user_id=owner_user_id,
            idempotency_key=idempotency_key,
            operation=f"exports.create:{meeting_id}",
            request=request,
        )
        if replay_id is not None:
            artifact, job = await self.get_export(meeting_id, replay_id, owner_user_id)
            return artifact, job, True, None
        max_version = int(
            (
                await self.session.exec(
                    select(func.max(MeetingArtifact.version)).where(
                        MeetingArtifact.meeting_id == meeting_id,
                        MeetingArtifact.artifact_type == ArtifactType.EXPORT.value,
                    )
                )
            ).one()
            or 0
        )
        artifact = MeetingArtifact(
            meeting_id=meeting_id,
            artifact_type=ArtifactType.EXPORT.value,
            version=max_version + 1,
            state=ArtifactState.GENERATING.value,
            content_json=encode_json({"schema_version": "meeting.export.v1"}),
            input_from_ordinal=1,
            input_to_ordinal=meeting.last_segment_ordinal,
            transcript_revision=0,
        )
        job = MeetingJob(
            meeting_id=meeting_id,
            job_kind=MeetingJobKind.EXPORT.value,
            idempotency_key=f"{meeting_id}:export:{artifact.id}",
            input_watermark=meeting.last_segment_ordinal,
            settings_version=meeting.settings_version,
            input_json=encode_json(
                {
                    "schema_version": "meeting.export.input.v1",
                    "export_id": artifact.id,
                    "format": _value(request.format),
                    "content": _value(request.content),
                    "transcript_source": _value(request.transcript_source),
                    "source_artifact_id": request.artifact_id,
                    "source_artifact_version": request.artifact_version,
                }
            ),
        )
        self.session.add(artifact)
        self.session.add(job)
        await self.session.flush()
        self._remember_idempotency(
            owner_user_id=owner_user_id,
            idempotency_key=idempotency_key,
            operation=f"exports.create:{meeting_id}",
            request=request,
            resource_id=artifact.id,
            response_status=202,
        )
        event = await self.events.append(
            meeting_id,
            "export.queued",
            {
                "export_id": artifact.id,
                "job_id": job.id,
                "format": _value(request.format),
                "input_to_ordinal": artifact.input_to_ordinal,
            },
        )
        await self._commit()
        return artifact, job, False, event

    async def get_export(
        self,
        meeting_id: str,
        export_id: str,
        owner_user_id: int,
    ) -> tuple[MeetingArtifact, MeetingJob]:
        artifact = (
            await self.session.exec(
                select(MeetingArtifact)
                .join(MeetingSession, MeetingSession.id == MeetingArtifact.meeting_id)
                .where(
                    MeetingArtifact.id == export_id,
                    MeetingArtifact.meeting_id == meeting_id,
                    MeetingArtifact.artifact_type == ArtifactType.EXPORT.value,
                    MeetingSession.owner_user_id == owner_user_id,
                    MeetingSession.state != MeetingState.DELETED.value,
                )
            )
        ).first()
        if artifact is None:
            raise MeetingResourceNotFound("meeting resource not found")
        job = (
            await self.session.exec(
                select(MeetingJob).where(
                    MeetingJob.meeting_id == meeting_id,
                    MeetingJob.job_kind == MeetingJobKind.EXPORT.value,
                    MeetingJob.idempotency_key == f"{meeting_id}:export:{artifact.id}",
                )
            )
        ).first()
        if job is None:
            raise MeetingInvalidOperation("meeting export job is unavailable")
        return artifact, job

    async def claim_job(
        self,
        worker_id: str,
        kinds: set[str] | list[str] | tuple[str, ...],
        lease_seconds: int,
        retry_delay_seconds: int = 30,
    ) -> MeetingJob | None:
        normalized_kinds = {str(_value(kind)) for kind in kinds}
        if (
            not worker_id.strip()
            or not normalized_kinds
            or lease_seconds < 5
            or lease_seconds > 3_600
            or retry_delay_seconds < 1
            or retry_delay_seconds > 3_600
        ):
            raise MeetingInvalidOperation("job claim parameters are invalid")
        now = utcnow()
        retry_before = now - timedelta(seconds=retry_delay_seconds)
        statement = (
            select(MeetingJob)
            .where(
                col(MeetingJob.job_kind).in_(normalized_kinds),
                MeetingJob.attempt < MeetingJob.max_attempts,
                or_(
                    MeetingJob.state == MeetingJobState.QUEUED.value,
                    (
                        (MeetingJob.state == MeetingJobState.RETRY_WAIT.value)
                        & (MeetingJob.updated_at <= retry_before)
                    ),
                    (
                        (MeetingJob.state == MeetingJobState.LEASED.value)
                        & (MeetingJob.lease_until.is_not(None))
                        & (MeetingJob.lease_until < now)
                    ),
                ),
            )
            .order_by(MeetingJob.created_at, MeetingJob.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        job = (await self.session.exec(statement)).first()
        if job is None:
            await self.session.rollback()
            return None
        job.state = MeetingJobState.LEASED.value
        job.lease_owner = worker_id
        job.lease_until = now + timedelta(seconds=lease_seconds)
        job.attempt += 1
        job.updated_at = now
        self.session.add(job)
        await self._commit()
        return job

    async def voiceprint_enrollment_context(
        self,
        job_id: str,
        worker_id: str,
        *,
        lock: bool = False,
    ) -> dict[str, Any]:
        now = utcnow()
        job_statement = select(MeetingJob).where(
            MeetingJob.id == job_id,
            MeetingJob.job_kind == MeetingJobKind.VOICEPRINT_ENROLL.value,
            MeetingJob.lease_owner == worker_id,
            col(MeetingJob.state).in_([MeetingJobState.LEASED.value, MeetingJobState.RUNNING.value]),
        )
        if lock:
            job_statement = job_statement.with_for_update()
        job = (
            await self.session.exec(job_statement)
        ).first()
        if (
            job is None
            or job.lease_until is None
            or _aware_datetime(job.lease_until) <= _aware_datetime(now)
        ):
            raise MeetingVersionConflict("voiceprint job lease is no longer owned by this worker")
        payload = decode_json(job.input_json, {})
        profile_id = str(payload.get("voice_profile_id") or "")
        consent_id = str(payload.get("consent_id") or "")
        track_id = str(payload.get("speaker_track_id") or "")
        if not profile_id or not consent_id or not track_id:
            raise MeetingInvalidOperation("voiceprint enrollment job input is incomplete")
        meeting_statement = select(MeetingSession).where(MeetingSession.id == job.meeting_id)
        if lock:
            meeting_statement = meeting_statement.with_for_update()
        meeting = (await self.session.exec(meeting_statement)).first()
        if meeting is None or not meeting.voiceprint_enabled or meeting.state == MeetingState.DELETED.value:
            raise MeetingInvalidOperation("voiceprint enrollment meeting is no longer eligible")
        profile_statement = select(MeetingVoiceProfile).where(
            MeetingVoiceProfile.id == profile_id,
            MeetingVoiceProfile.owner_user_id == meeting.owner_user_id,
            MeetingVoiceProfile.status != VoiceProfileStatus.DELETED.value,
        )
        consent_statement = select(MeetingVoiceprintConsent).where(
            MeetingVoiceprintConsent.id == consent_id,
            MeetingVoiceprintConsent.voice_profile_id == profile_id,
            MeetingVoiceprintConsent.actor_user_id == meeting.owner_user_id,
            MeetingVoiceprintConsent.source_meeting_id == meeting.id,
            MeetingVoiceprintConsent.revoked_at.is_(None),
        )
        track_statement = select(MeetingSpeakerTrack).where(
            MeetingSpeakerTrack.id == track_id,
            MeetingSpeakerTrack.meeting_id == meeting.id,
        )
        if lock:
            profile_statement = profile_statement.with_for_update()
            consent_statement = consent_statement.with_for_update()
            track_statement = track_statement.with_for_update()
        profile = (await self.session.exec(profile_statement)).first()
        consent = (await self.session.exec(consent_statement)).first()
        track = (await self.session.exec(track_statement)).first()
        if profile is None or consent is None or track is None:
            raise MeetingInvalidOperation("voiceprint enrollment authorization is no longer valid")
        consent_metadata = decode_json(consent.metadata_json, {})
        if consent_metadata.get("job_id") != job.id or consent_metadata.get("source_track_id") != track.id:
            raise MeetingInvalidOperation("voiceprint consent is not bound to this job and track")
        segments = list(
            (
                await self.session.exec(
                    select(MeetingTranscriptSegment)
                    .where(
                        MeetingTranscriptSegment.meeting_id == meeting.id,
                        MeetingTranscriptSegment.speaker_track_id == track.id,
                    )
                    .order_by(MeetingTranscriptSegment.start_ms, MeetingTranscriptSegment.ordinal)
                )
            ).all()
        )
        segment_ranges = [(segment.start_ms, segment.end_ms) for segment in segments]
        all_chunks = list(
            (
                await self.session.exec(
                    select(MeetingAudioChunk)
                    .where(
                        MeetingAudioChunk.meeting_id == meeting.id,
                        MeetingAudioChunk.state != "deleted",
                    )
                    .order_by(MeetingAudioChunk.start_ms, MeetingAudioChunk.sequence)
                )
            ).all()
        )
        chunks = [
            chunk
            for chunk in all_chunks
            if any(
                chunk.start_ms < end_ms
                and chunk.start_ms + chunk.duration_ms > start_ms
                for start_ms, end_ms in segment_ranges
            )
        ]
        return {
            "job": job,
            "owner_user_id": meeting.owner_user_id,
            "meeting": meeting,
            "profile": profile,
            "consent": consent,
            "track": track,
            "segments": [
                {
                    "id": segment.id,
                    "start_ms": segment.start_ms,
                    "end_ms": segment.end_ms,
                    "overlap": segment.overlap,
                    "noise_level": segment.noise_level,
                    "asr_confidence": segment.asr_confidence,
                }
                for segment in segments
            ],
            "chunks": chunks,
        }

    async def complete_voiceprint_enrollment(
        self,
        job_id: str,
        worker_id: str,
        *,
        encoder_name: str,
        encoder_version: str,
        encrypted_embedding: str,
        key_id: str,
        sample_count: int,
        effective_duration_ms: int,
        quality_summary: dict[str, Any],
    ) -> tuple[MeetingVoiceProfile, MeetingEvent]:
        if (
            not encoder_name.strip()
            or not encoder_version.strip()
            or not encrypted_embedding
            or len(encrypted_embedding) > 1_000_000
            or not key_id.strip()
            or sample_count <= 0
            or effective_duration_ms <= 0
        ):
            raise MeetingInvalidOperation("voiceprint enrollment result is invalid")
        owner_user_id = await self._voiceprint_enrollment_owner(job_id)
        await self.reconcile_voiceprint_tombstones(owner_user_id=owner_user_id)
        await self._voiceprint_owner_lock(owner_user_id)
        context = await self.voiceprint_enrollment_context(job_id, worker_id, lock=True)
        job: MeetingJob = context["job"]
        profile: MeetingVoiceProfile = context["profile"]
        consent: MeetingVoiceprintConsent = context["consent"]
        track: MeetingSpeakerTrack = context["track"]
        if (
            consent.revoked_at is not None
            or profile.status
            not in {VoiceProfileStatus.COLLECTING.value, VoiceProfileStatus.ACTIVE.value}
        ):
            raise MeetingInvalidOperation("voiceprint enrollment authorization changed before publication")
        profile.encoder_name = encoder_name
        profile.encoder_version = encoder_version
        profile.encrypted_embedding = encrypted_embedding
        profile.key_id = key_id
        profile.sample_count = sample_count
        profile.effective_duration_ms = effective_duration_ms
        profile.quality_summary_json = encode_json(quality_summary)
        profile.status = VoiceProfileStatus.ACTIVE.value
        profile.updated_at = utcnow()
        track.voice_profile_id = profile.id
        if track.label_source != SpeakerLabelSource.MANUAL.value:
            track.display_name = profile.display_name
            track.label_source = SpeakerLabelSource.VOICEPRINT_CONFIRMED.value
        track.version += 1
        track.updated_at = utcnow()
        job.state = MeetingJobState.SUCCEEDED.value
        job.lease_owner = None
        job.lease_until = None
        job.public_error_code = None
        job.internal_diagnostic = None
        job.updated_at = utcnow()
        self.session.add(profile)
        self.session.add(track)
        self.session.add(job)
        event = await self.events.append(
            job.meeting_id,
            "voiceprint.profile.activated",
            {
                "voice_profile_id": profile.id,
                "consent_id": consent.id,
                "speaker_track_id": track.id,
                "encoder_version": encoder_version,
                "sample_count": sample_count,
                "effective_duration_ms": effective_duration_ms,
            },
        )
        await self.events.append(
            job.meeting_id,
            "speaker.label.changed",
            {"speaker": speaker_response(track).model_dump(mode="json")},
        )
        await self._commit()
        return profile, event

    async def voiceprint_match_job_context(
        self,
        job_id: str,
        worker_id: str,
        *,
        lock: bool = False,
    ) -> dict[str, Any]:
        now = utcnow()
        job_statement = select(MeetingJob).where(
            MeetingJob.id == job_id,
            MeetingJob.job_kind == MeetingJobKind.VOICEPRINT_MATCH.value,
            MeetingJob.lease_owner == worker_id,
            col(MeetingJob.state).in_([MeetingJobState.LEASED.value, MeetingJobState.RUNNING.value]),
        )
        if lock:
            job_statement = job_statement.with_for_update()
        job = (await self.session.exec(job_statement)).first()
        if (
            job is None
            or job.lease_until is None
            or _aware_datetime(job.lease_until) <= _aware_datetime(now)
        ):
            raise MeetingVersionConflict("voiceprint match job lease is no longer owned by this worker")
        payload = decode_json(job.input_json, {})
        if (
            payload.get("schema_version") != "meeting.voiceprint_match.input.v1"
            or payload.get("task") != "voiceprint_match"
            or not payload.get("speaker_track_id")
        ):
            raise MeetingInvalidOperation("voiceprint match job input is invalid")
        meeting_statement = select(MeetingSession).where(MeetingSession.id == job.meeting_id)
        if lock:
            meeting_statement = meeting_statement.with_for_update()
        meeting = (await self.session.exec(meeting_statement)).first()
        if meeting is None or not meeting.voiceprint_enabled or meeting.state == MeetingState.DELETED.value:
            raise MeetingInvalidOperation("voiceprint match meeting is no longer eligible")
        track_statement = select(MeetingSpeakerTrack).where(
            MeetingSpeakerTrack.id == str(payload["speaker_track_id"]),
            MeetingSpeakerTrack.meeting_id == meeting.id,
        )
        if lock:
            track_statement = track_statement.with_for_update()
        track = (await self.session.exec(track_statement)).first()
        if track is None:
            raise MeetingInvalidOperation("voiceprint match track no longer exists")
        context = await self.voiceprint_match_context(meeting.id, track.id, meeting.owner_user_id)
        return {"job": job, **context}

    async def _mark_voiceprint_match_job_succeeded(
        self,
        context: dict[str, Any],
        *,
        reason_code: str,
        effective_duration_ms: int,
        quality_grade: str,
        match_id: str | None = None,
    ) -> MeetingJob:
        job: MeetingJob = context["job"]
        job.state = MeetingJobState.SUCCEEDED.value
        job.lease_owner = None
        job.lease_until = None
        job.public_error_code = None
        job.internal_diagnostic = None
        job.updated_at = utcnow()
        self.session.add(job)
        await self.events.append(
            job.meeting_id,
            "voiceprint.match.completed",
            {
                "job_id": job.id,
                "speaker_track_id": context["track"].id,
                "reason_code": reason_code[:64],
                "effective_duration_ms": effective_duration_ms,
                "quality_grade": quality_grade[:32],
                "match_id": match_id,
            },
        )
        return job

    async def complete_voiceprint_match_job(
        self,
        job_id: str,
        worker_id: str,
        *,
        reason_code: str,
        effective_duration_ms: int,
        quality_grade: str,
    ) -> MeetingJob:
        context = await self.voiceprint_match_job_context(job_id, worker_id, lock=True)
        if (
            not reason_code.strip()
            or effective_duration_ms < 0
            or not quality_grade.strip()
        ):
            raise MeetingInvalidOperation("voiceprint match outcome is invalid")
        job = await self._mark_voiceprint_match_job_succeeded(
            context,
            reason_code=reason_code,
            effective_duration_ms=effective_duration_ms,
            quality_grade=quality_grade,
        )
        await self._commit()
        return job

    async def fail_job(
        self,
        job_id: str,
        worker_id: str,
        *,
        public_error_code: str,
        internal_diagnostic: str | None = None,
        retryable: bool = False,
    ) -> MeetingJob:
        job = (
            await self.session.exec(
                select(MeetingJob)
                .where(
                    MeetingJob.id == job_id,
                    MeetingJob.lease_owner == worker_id,
                    col(MeetingJob.state).in_(
                        [MeetingJobState.LEASED.value, MeetingJobState.RUNNING.value]
                    ),
                )
                .with_for_update()
            )
        ).first()
        if job is None:
            raise MeetingVersionConflict("job lease is no longer owned by this worker")
        job.state = (
            MeetingJobState.RETRY_WAIT.value
            if retryable and job.attempt < job.max_attempts
            else MeetingJobState.FAILED.value
        )
        job.lease_owner = None
        job.lease_until = None
        job.public_error_code = public_error_code[:64]
        job.internal_diagnostic = internal_diagnostic[:2_000] if internal_diagnostic else None
        job.updated_at = utcnow()
        self.session.add(job)
        await self.events.append(
            job.meeting_id,
            "job.failed" if job.state == MeetingJobState.FAILED.value else "job.retry_wait",
            {
                "job_id": job.id,
                "job_kind": job.job_kind,
                "state": job.state,
                "public_error_code": job.public_error_code,
            },
        )
        await self._commit()
        return job

    async def active_voiceprint_profiles(
        self,
        owner_user_id: int,
        encoder_name: str,
        encoder_version: str,
    ) -> list[dict[str, Any]]:
        await self.reconcile_voiceprint_tombstones(owner_user_id=owner_user_id)
        profiles = await self._eligible_voiceprint_profiles(
            owner_user_id,
            encoder_name,
            encoder_version,
        )
        fingerprint = self._voiceprint_candidate_set_fingerprint(profiles)
        for item in profiles:
            item["candidate_set_fingerprint"] = fingerprint
        return profiles

    async def _eligible_voiceprint_profiles(
        self,
        owner_user_id: int,
        encoder_name: str,
        encoder_version: str,
    ) -> list[dict[str, Any]]:
        rows = (
            await self.session.exec(
                select(MeetingVoiceProfile, MeetingVoiceprintConsent)
                .join(
                    MeetingVoiceprintConsent,
                    MeetingVoiceprintConsent.voice_profile_id == MeetingVoiceProfile.id,
                )
                .where(
                    MeetingVoiceProfile.owner_user_id == owner_user_id,
                    MeetingVoiceProfile.status == VoiceProfileStatus.ACTIVE.value,
                    MeetingVoiceProfile.encoder_name == encoder_name,
                    MeetingVoiceProfile.encoder_version == encoder_version,
                    MeetingVoiceProfile.encrypted_embedding.is_not(None),
                    MeetingVoiceProfile.key_id.is_not(None),
                    MeetingVoiceprintConsent.actor_user_id == owner_user_id,
                    MeetingVoiceprintConsent.revoked_at.is_(None),
                )
                .order_by(col(MeetingVoiceprintConsent.granted_at).desc())
            )
        ).all()
        profiles: dict[str, dict[str, Any]] = {}
        for profile, consent in rows:
            profiles.setdefault(
                profile.id,
                {
                    "profile": profile,
                    "consent": consent,
                    "owner_user_id": owner_user_id,
                    "encrypted_embedding": profile.encrypted_embedding,
                    "key_id": profile.key_id,
                },
            )
        return list(profiles.values())

    @staticmethod
    def _voiceprint_candidate_set_fingerprint(profiles: list[dict[str, Any]]) -> str:
        payload = []
        for item in sorted(profiles, key=lambda value: value["profile"].id):
            profile: MeetingVoiceProfile = item["profile"]
            consent: MeetingVoiceprintConsent = item["consent"]
            encrypted_embedding = profile.encrypted_embedding or ""
            payload.append(
                {
                    "profile_id": profile.id,
                    "profile_updated_at": _aware_datetime(profile.updated_at).isoformat(),
                    "key_id": profile.key_id,
                    "encrypted_embedding_sha256": hashlib.sha256(
                        encrypted_embedding.encode("utf-8")
                    ).hexdigest(),
                    "consent_id": consent.id,
                    "consent_granted_at": _aware_datetime(consent.granted_at).isoformat(),
                }
            )
        return hashlib.sha256(encode_json(payload).encode("utf-8")).hexdigest()

    async def voiceprint_match_context(
        self,
        meeting_id: str,
        track_id: str,
        owner_user_id: int,
    ) -> dict[str, Any]:
        meeting = await self._owned_session(meeting_id, owner_user_id)
        if not meeting.voiceprint_enabled:
            raise MeetingInvalidOperation("voiceprint is not enabled for this meeting")
        track = await self._owned_track(meeting_id, track_id, owner_user_id)
        segments = list(
            (
                await self.session.exec(
                    select(MeetingTranscriptSegment)
                    .where(
                        MeetingTranscriptSegment.meeting_id == meeting_id,
                        MeetingTranscriptSegment.speaker_track_id == track_id,
                    )
                    .order_by(MeetingTranscriptSegment.start_ms, MeetingTranscriptSegment.ordinal)
                )
            ).all()
        )
        ranges = [(segment.start_ms, segment.end_ms) for segment in segments]
        all_chunks = list(
            (
                await self.session.exec(
                    select(MeetingAudioChunk)
                    .where(
                        MeetingAudioChunk.meeting_id == meeting_id,
                        MeetingAudioChunk.state != "deleted",
                    )
                    .order_by(MeetingAudioChunk.start_ms, MeetingAudioChunk.sequence)
                )
            ).all()
        )
        chunks = [
            chunk
            for chunk in all_chunks
            if any(
                chunk.start_ms < end_ms
                and chunk.start_ms + chunk.duration_ms > start_ms
                for start_ms, end_ms in ranges
            )
        ]
        return {
            "owner_user_id": owner_user_id,
            "meeting": meeting,
            "track": track,
            "segments": [
                {
                    "id": segment.id,
                    "start_ms": segment.start_ms,
                    "end_ms": segment.end_ms,
                    "overlap": segment.overlap,
                    "noise_level": segment.noise_level,
                    "asr_confidence": segment.asr_confidence,
                }
                for segment in segments
            ],
            "chunks": chunks,
        }

    async def record_voiceprint_match(
        self,
        meeting_id: str,
        owner_user_id: int,
        *,
        speaker_track_id: str,
        voice_profile_id: str,
        encoder_version: str,
        threshold_version: str,
        top1_score: float,
        top1_top2_margin: float,
        effective_duration_ms: int,
        quality_grade: str,
        encoder_name: str | None = None,
        decision: str | VoiceMatchDecision = VoiceMatchDecision.SUGGESTED,
        validated_auto_match_gate: bool = False,
        expected_profile_updated_at: datetime | str | None = None,
        expected_key_id: str | None = None,
        expected_encrypted_embedding_sha256: str | None = None,
        expected_candidate_set_fingerprint: str | None = None,
        job_id: str | None = None,
        worker_id: str | None = None,
    ) -> tuple[MeetingVoiceprintMatch, MeetingEvent]:
        await self.reconcile_voiceprint_tombstones(owner_user_id=owner_user_id)
        await self._voiceprint_owner_lock(owner_user_id)
        if (job_id is None) != (worker_id is None):
            raise MeetingInvalidOperation("voiceprint match job identity is incomplete")
        requested_decision = VoiceMatchDecision(_value(decision))
        if requested_decision == VoiceMatchDecision.AUTO_APPLIED and not validated_auto_match_gate:
            raise MeetingInvalidOperation("automatic voiceprint matching has not passed its release gate")
        if requested_decision == VoiceMatchDecision.AUTO_APPLIED and not all(
            (
                encoder_name,
                expected_profile_updated_at,
                expected_key_id,
                expected_encrypted_embedding_sha256,
                expected_candidate_set_fingerprint,
            )
        ):
            raise MeetingInvalidOperation("automatic voiceprint matching lacks concurrency evidence")
        if requested_decision not in {
            VoiceMatchDecision.SUGGESTED,
            VoiceMatchDecision.AUTO_APPLIED,
        }:
            raise MeetingInvalidOperation("worker may only publish suggested or validated auto-applied matches")
        match_job_context: dict[str, Any] | None = None
        if job_id is not None and worker_id is not None:
            match_job_context = await self.voiceprint_match_job_context(job_id, worker_id, lock=True)
            if (
                match_job_context["meeting"].id != meeting_id
                or match_job_context["meeting"].owner_user_id != owner_user_id
                or match_job_context["track"].id != speaker_track_id
            ):
                raise MeetingInvalidOperation("voiceprint match job is bound to another track")
            meeting = match_job_context["meeting"]
            track = match_job_context["track"]
        else:
            meeting = await self._owned_session(meeting_id, owner_user_id, lock=True)
            track = await self._owned_track(meeting_id, speaker_track_id, owner_user_id, lock=True)
        if not meeting.voiceprint_enabled:
            raise MeetingInvalidOperation("voiceprint is not enabled for this meeting")
        if track.label_source == SpeakerLabelSource.MANUAL.value:
            raise MeetingInvalidOperation("manual speaker labels cannot be replaced by voiceprint matching")
        profile = await self._owned_voice_profile(voice_profile_id, owner_user_id, lock=True)
        consent = await self._active_consent(profile.id, owner_user_id, lock=True)
        if (
            profile.status != VoiceProfileStatus.ACTIVE.value
            or consent is None
            or profile.encoder_version != encoder_version
            or (encoder_name is not None and profile.encoder_name != encoder_name)
        ):
            raise MeetingInvalidOperation("voice profile is not eligible for matching")
        if expected_profile_updated_at is not None:
            if isinstance(expected_profile_updated_at, str):
                try:
                    expected_updated_at = datetime.fromisoformat(
                        expected_profile_updated_at.replace("Z", "+00:00")
                    )
                except ValueError as exc:
                    raise MeetingInvalidOperation(
                        "voiceprint profile concurrency evidence is invalid"
                    ) from exc
            else:
                expected_updated_at = expected_profile_updated_at
            if _aware_datetime(profile.updated_at) != _aware_datetime(expected_updated_at):
                raise MeetingVersionConflict("voice profile changed after matching")
        if expected_key_id is not None and profile.key_id != expected_key_id:
            raise MeetingVersionConflict("voice profile key changed after matching")
        if expected_encrypted_embedding_sha256 is not None:
            current_ciphertext_hash = hashlib.sha256(
                (profile.encrypted_embedding or "").encode("utf-8")
            ).hexdigest()
            if current_ciphertext_hash != expected_encrypted_embedding_sha256:
                raise MeetingVersionConflict("voice profile embedding changed after matching")
        if expected_candidate_set_fingerprint is not None:
            if encoder_name is None:
                raise MeetingInvalidOperation("voiceprint encoder name is required for candidate validation")
            candidates = await self._eligible_voiceprint_profiles(
                owner_user_id,
                encoder_name,
                encoder_version,
            )
            fingerprint = self._voiceprint_candidate_set_fingerprint(candidates)
            if fingerprint != expected_candidate_set_fingerprint:
                raise MeetingVersionConflict("voiceprint candidate set changed after matching")
        rejected = (
            await self.session.exec(
                select(MeetingVoiceprintMatch)
                .where(
                    MeetingVoiceprintMatch.meeting_id == meeting_id,
                    MeetingVoiceprintMatch.speaker_track_id == track.id,
                    MeetingVoiceprintMatch.voice_profile_id == profile.id,
                    MeetingVoiceprintMatch.decision == VoiceMatchDecision.REJECTED.value,
                )
                .order_by(col(MeetingVoiceprintMatch.created_at).desc())
                .limit(1)
            )
        ).first()
        if rejected is not None:
            event = await self.events.append(
                meeting_id,
                "voiceprint.match.suppressed",
                {
                    "match_id": rejected.id,
                    "speaker_track_id": track.id,
                    "voice_profile_id": profile.id,
                    "reason": "previously_rejected",
                },
            )
            if match_job_context is not None:
                await self._mark_voiceprint_match_job_succeeded(
                    match_job_context,
                    reason_code="VOICEPRINT_PREVIOUSLY_REJECTED",
                    effective_duration_ms=effective_duration_ms,
                    quality_grade=quality_grade,
                    match_id=rejected.id,
                )
            await self._commit()
            return rejected, event
        value = MeetingVoiceprintMatch(
            meeting_id=meeting_id,
            speaker_track_id=track.id,
            voice_profile_id=profile.id,
            encoder_version=encoder_version,
            threshold_version=threshold_version,
            top1_score=top1_score,
            top1_top2_margin=top1_top2_margin,
            effective_duration_ms=effective_duration_ms,
            quality_grade=quality_grade,
            decision=requested_decision.value,
        )
        self.session.add(value)
        await self.session.flush()
        if requested_decision == VoiceMatchDecision.AUTO_APPLIED:
            track.display_name = profile.display_name
            track.voice_profile_id = profile.id
            track.match_confidence = top1_score
            track.label_source = SpeakerLabelSource.VOICEPRINT_AUTO.value
            track.version += 1
            track.updated_at = utcnow()
            self.session.add(track)
        event = await self.events.append(
            meeting_id,
            (
                "voiceprint.match.applied"
                if requested_decision == VoiceMatchDecision.AUTO_APPLIED
                else "voiceprint.match.suggested"
            ),
            {
                "match_id": value.id,
                "speaker_track_id": track.id,
                "voice_profile_id": profile.id,
                "decision": value.decision,
            },
        )
        if requested_decision == VoiceMatchDecision.AUTO_APPLIED:
            await self.events.append(
                meeting_id,
                "speaker.label.changed",
                {"speaker": speaker_response(track).model_dump(mode="json")},
            )
        if match_job_context is not None:
            await self._mark_voiceprint_match_job_succeeded(
                match_job_context,
                reason_code=(
                    "VOICEPRINT_AUTO_MATCH_APPLIED"
                    if requested_decision == VoiceMatchDecision.AUTO_APPLIED
                    else "VOICEPRINT_MATCH_SUGGESTED"
                ),
                effective_duration_ms=effective_duration_ms,
                quality_grade=quality_grade,
                match_id=value.id,
            )
        await self._commit()
        return value, event

    async def list_jobs(
        self,
        meeting_id: str,
        owner_user_id: int,
    ) -> list[MeetingJob]:
        await self._owned_session(meeting_id, owner_user_id)
        return list(
            (
                await self.session.exec(
                    select(MeetingJob)
                    .where(MeetingJob.meeting_id == meeting_id)
                    .order_by(col(MeetingJob.created_at).desc())
                )
            ).all()
        )

    async def retry_job(
        self,
        meeting_id: str,
        job_id: str,
        owner_user_id: int,
    ) -> MeetingJob:
        await self._owned_session(meeting_id, owner_user_id)
        job = (
            await self.session.exec(
                select(MeetingJob)
                .where(MeetingJob.id == job_id, MeetingJob.meeting_id == meeting_id)
                .with_for_update()
            )
        ).first()
        if job is None:
            raise MeetingResourceNotFound("meeting resource not found")
        if job.state not in {MeetingJobState.FAILED.value, MeetingJobState.RETRY_WAIT.value}:
            raise MeetingInvalidOperation("job is not retryable")
        if job.attempt >= job.max_attempts:
            raise MeetingInvalidOperation("job retry limit has been reached")
        job.state = MeetingJobState.QUEUED.value
        job.lease_owner = None
        job.lease_until = None
        job.public_error_code = None
        job.internal_diagnostic = None
        job.updated_at = utcnow()
        self.session.add(job)
        if job.job_kind == MeetingJobKind.EXPORT.value:
            export_id = str(decode_json(job.input_json, {}).get("export_id") or "")
            artifact = await self.session.get(MeetingArtifact, export_id)
            if (
                artifact is not None
                and artifact.meeting_id == meeting_id
                and artifact.artifact_type == ArtifactType.EXPORT.value
            ):
                artifact.state = ArtifactState.GENERATING.value
                artifact.updated_at = utcnow()
                self.session.add(artifact)
        await self.events.append(
            meeting_id,
            "job.retry.queued",
            {"job_id": job.id, "job_kind": job.job_kind, "attempt": job.attempt},
        )
        await self._commit()
        return job

    async def list_events(
        self,
        meeting_id: str,
        owner_user_id: int,
        *,
        after_cursor: int = 0,
        limit: int = 200,
    ) -> tuple[list[MeetingEvent], int | None]:
        await self._owned_session(meeting_id, owner_user_id)
        values = await self.events.list_after(
            meeting_id,
            after_cursor=after_cursor,
            limit=limit + 1,
        )
        has_more = len(values) > limit
        page = values[:limit]
        return page, page[-1].cursor if has_more and page else None

    async def register_audio_chunk(
        self,
        meeting_id: str,
        owner_user_id: int,
        *,
        stream_epoch: int,
        sequence: int,
        start_ms: int,
        duration_ms: int,
        storage_key: str,
        sha256: str,
        byte_size: int,
        codec: str = "pcm_s16le",
        sample_rate: int = 16_000,
        channels: int = 1,
    ) -> tuple[MeetingAudioChunk, bool]:
        meeting = await self._owned_session(meeting_id, owner_user_id, lock=True)
        if meeting.state not in {
            MeetingState.CONNECTING.value,
            MeetingState.LIVE.value,
            MeetingState.RECONNECTING.value,
            MeetingState.STOPPING.value,
        }:
            raise MeetingInvalidOperation("meeting is not accepting audio")
        if stream_epoch != meeting.stream_epoch:
            raise MeetingVersionConflict(
                "audio stream epoch does not match",
                current={"stream_epoch": meeting.stream_epoch},
            )
        existing = (
            await self.session.exec(
                select(MeetingAudioChunk).where(
                    MeetingAudioChunk.meeting_id == meeting_id,
                    MeetingAudioChunk.stream_epoch == stream_epoch,
                    MeetingAudioChunk.sequence == sequence,
                )
            )
        ).first()
        if existing is not None:
            if (
                existing.sha256 != sha256
                or existing.byte_size != byte_size
                or existing.start_ms != start_ms
                or existing.duration_ms != duration_ms
            ):
                raise MeetingIdempotencyConflict(
                    "audio sequence was reused with different content"
                )
            return existing, True
        value = MeetingAudioChunk(
            meeting_id=meeting_id,
            stream_epoch=stream_epoch,
            sequence=sequence,
            start_ms=start_ms,
            duration_ms=duration_ms,
            storage_key=storage_key,
            sha256=sha256,
            byte_size=byte_size,
            codec=codec,
            sample_rate=sample_rate,
            channels=channels,
            state="verified",
        )
        self.session.add(value)
        await self._commit()
        return value, False

    async def acknowledge_audio_sequence(
        self,
        meeting_id: str,
        owner_user_id: int,
        *,
        stream_epoch: int,
        ack_sequence: int,
    ) -> MeetingSession:
        meeting = await self._owned_session(meeting_id, owner_user_id, lock=True)
        if stream_epoch != meeting.stream_epoch:
            raise MeetingVersionConflict(
                "audio ACK epoch does not match",
                current={"stream_epoch": meeting.stream_epoch},
            )
        if ack_sequence <= meeting.last_audio_sequence:
            return meeting
        count = int(
            (
                await self.session.exec(
                    select(func.count()).select_from(MeetingAudioChunk).where(
                        MeetingAudioChunk.meeting_id == meeting_id,
                        MeetingAudioChunk.stream_epoch == stream_epoch,
                        MeetingAudioChunk.sequence > meeting.last_audio_sequence,
                        MeetingAudioChunk.sequence <= ack_sequence,
                    )
                )
            ).one()
            or 0
        )
        expected = ack_sequence - meeting.last_audio_sequence
        if count != expected:
            raise MeetingInvalidOperation("audio ACK includes chunks that are not durably registered")
        meeting.last_audio_sequence = ack_sequence
        meeting.updated_at = utcnow()
        self.session.add(meeting)
        await self._commit()
        return meeting

    async def list_audio_chunks(
        self,
        meeting_id: str,
        owner_user_id: int,
    ) -> list[MeetingAudioChunk]:
        await self._owned_session(meeting_id, owner_user_id)
        return list(
            (
                await self.session.exec(
                    select(MeetingAudioChunk)
                    .where(
                        MeetingAudioChunk.meeting_id == meeting_id,
                        MeetingAudioChunk.state != "deleted",
                    )
                    .order_by(MeetingAudioChunk.start_ms, MeetingAudioChunk.stream_epoch, MeetingAudioChunk.sequence)
                )
            ).all()
        )

    async def audio_manifest(
        self,
        meeting_id: str,
        owner_user_id: int,
    ) -> AudioManifestResponse:
        await self._owned_session(meeting_id, owner_user_id)
        chunks = list(
            (
                await self.session.exec(
                    select(MeetingAudioChunk)
                    .where(MeetingAudioChunk.meeting_id == meeting_id)
                    .order_by(MeetingAudioChunk.stream_epoch, MeetingAudioChunk.sequence)
                )
            ).all()
        )
        gaps: list[dict[str, int]] = []
        previous_by_epoch: dict[int, int] = {}
        for chunk in chunks:
            previous = previous_by_epoch.get(chunk.stream_epoch, -1)
            if chunk.sequence > previous + 1:
                gaps.append(
                    {
                        "stream_epoch": chunk.stream_epoch,
                        "from_sequence": previous + 1,
                        "to_sequence": chunk.sequence - 1,
                    }
                )
            previous_by_epoch[chunk.stream_epoch] = chunk.sequence
        formats = sorted(
            {
                (item.codec, item.sample_rate, item.channels)
                for item in chunks
            }
        )
        return AudioManifestResponse(
            meeting_id=meeting_id,
            chunk_count=len(chunks),
            byte_size=sum(item.byte_size for item in chunks),
            duration_ms=max((item.start_ms + item.duration_ms for item in chunks), default=0),
            last_sequence=max((item.sequence for item in chunks), default=-1),
            gaps=gaps,
            formats=[
                {"codec": codec, "sample_rate": sample_rate, "channels": channels}
                for codec, sample_rate, channels in formats
            ],
        )


__all__ = [
    "MeetingIdempotencyConflict",
    "MeetingInvalidOperation",
    "MeetingRepository",
    "MeetingRepositoryError",
    "MeetingResourceNotFound",
    "MeetingVersionConflict",
    "artifact_response",
    "correction_response",
    "event_response",
    "export_response",
    "job_response",
    "lexicon_entry_response",
    "lexicon_version_response",
    "model_setting_response",
    "revision_response",
    "session_response",
    "speaker_response",
    "term_candidate_response",
    "voice_match_response",
    "voice_profile_response",
]
