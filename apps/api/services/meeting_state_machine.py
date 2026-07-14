"""Pure state-transition rules for meetings and their independent pipelines."""

from __future__ import annotations

from dataclasses import dataclass

from services.meeting_contracts import (
    MeetingPostprocessState,
    MeetingState,
    SegmentRevisionType,
    VoiceMatchDecision,
    VoiceProfileStatus,
)


class MeetingTransitionError(ValueError):
    def __init__(self, current: str, action: str) -> None:
        super().__init__(f"meeting action {action!r} is not allowed from state {current!r}")
        self.current = current
        self.action = action


@dataclass(frozen=True)
class StateTransition:
    previous: str
    current: str
    action: str
    idempotent: bool
    increment_stream_epoch: bool = False


_CAPTURE_TRANSITIONS: dict[str, dict[MeetingState, MeetingState]] = {
    "start": {MeetingState.DRAFT: MeetingState.CONNECTING},
    "mark_live": {
        MeetingState.CONNECTING: MeetingState.LIVE,
        MeetingState.RECONNECTING: MeetingState.LIVE,
    },
    "mark_reconnecting": {
        MeetingState.CONNECTING: MeetingState.RECONNECTING,
        MeetingState.LIVE: MeetingState.RECONNECTING,
    },
    "pause": {
        MeetingState.LIVE: MeetingState.PAUSED,
        MeetingState.RECONNECTING: MeetingState.PAUSED,
    },
    "resume": {
        MeetingState.PAUSED: MeetingState.RECONNECTING,
        MeetingState.INTERRUPTED: MeetingState.RECONNECTING,
    },
    "stop": {
        MeetingState.DRAFT: MeetingState.STOPPING,
        MeetingState.CONNECTING: MeetingState.STOPPING,
        MeetingState.LIVE: MeetingState.STOPPING,
        MeetingState.PAUSED: MeetingState.STOPPING,
        MeetingState.RECONNECTING: MeetingState.STOPPING,
        MeetingState.INTERRUPTED: MeetingState.STOPPING,
    },
    "mark_stopped": {MeetingState.STOPPING: MeetingState.STOPPED},
    "archive": {MeetingState.STOPPED: MeetingState.ARCHIVED},
    "interrupt": {
        MeetingState.CONNECTING: MeetingState.INTERRUPTED,
        MeetingState.LIVE: MeetingState.INTERRUPTED,
        MeetingState.RECONNECTING: MeetingState.INTERRUPTED,
        MeetingState.STOPPING: MeetingState.INTERRUPTED,
    },
    "delete": {
        state: MeetingState.DELETED
        for state in MeetingState
        if state != MeetingState.DELETED
    },
}

_IDEMPOTENT_STATES: dict[str, set[MeetingState]] = {
    "start": {MeetingState.CONNECTING, MeetingState.LIVE},
    "mark_live": {MeetingState.LIVE},
    "mark_reconnecting": {MeetingState.RECONNECTING},
    "pause": {MeetingState.PAUSED},
    "resume": {MeetingState.RECONNECTING, MeetingState.LIVE},
    "stop": {MeetingState.STOPPING, MeetingState.STOPPED, MeetingState.ARCHIVED},
    "mark_stopped": {MeetingState.STOPPED, MeetingState.ARCHIVED},
    "archive": {MeetingState.ARCHIVED},
    "interrupt": {MeetingState.INTERRUPTED},
    "delete": {MeetingState.DELETED},
}


def resolve_capture_transition(current: str | MeetingState, action: str) -> StateTransition:
    try:
        state = MeetingState(current)
    except ValueError as exc:
        raise MeetingTransitionError(str(current), action) from exc

    if action not in _CAPTURE_TRANSITIONS:
        raise MeetingTransitionError(state.value, action)
    if state in _IDEMPOTENT_STATES[action]:
        return StateTransition(state.value, state.value, action, True)

    target = _CAPTURE_TRANSITIONS[action].get(state)
    if target is None:
        raise MeetingTransitionError(state.value, action)
    return StateTransition(
        previous=state.value,
        current=target.value,
        action=action,
        idempotent=False,
        increment_stream_epoch=action == "resume",
    )


def resolve_finalize_transition(
    capture_state: str | MeetingState,
    postprocess_state: str | MeetingPostprocessState,
) -> StateTransition:
    capture = MeetingState(capture_state)
    postprocess = MeetingPostprocessState(postprocess_state)
    if capture not in {MeetingState.STOPPED, MeetingState.ARCHIVED}:
        raise MeetingTransitionError(capture.value, "finalize")
    if postprocess in {
        MeetingPostprocessState.QUEUED,
        MeetingPostprocessState.RUNNING,
        MeetingPostprocessState.SUCCEEDED,
    }:
        return StateTransition(postprocess.value, postprocess.value, "finalize", True)
    return StateTransition(
        postprocess.value,
        MeetingPostprocessState.QUEUED.value,
        "finalize",
        False,
    )


def ensure_revision_write_allowed(human_locked: bool, revision_type: str | SegmentRevisionType) -> None:
    kind = SegmentRevisionType(revision_type)
    if human_locked and kind != SegmentRevisionType.MANUAL and kind != SegmentRevisionType.REVERT:
        raise MeetingTransitionError("human_locked", f"append_{kind.value}")


_VOICE_PROFILE_TRANSITIONS: dict[VoiceProfileStatus, set[VoiceProfileStatus]] = {
    VoiceProfileStatus.COLLECTING: {VoiceProfileStatus.ACTIVE, VoiceProfileStatus.DELETED},
    VoiceProfileStatus.ACTIVE: {
        VoiceProfileStatus.PAUSED,
        VoiceProfileStatus.REVOKED,
        VoiceProfileStatus.DELETED,
    },
    VoiceProfileStatus.PAUSED: {
        VoiceProfileStatus.ACTIVE,
        VoiceProfileStatus.REVOKED,
        VoiceProfileStatus.DELETED,
    },
    VoiceProfileStatus.REVOKED: {VoiceProfileStatus.COLLECTING, VoiceProfileStatus.DELETED},
    VoiceProfileStatus.DELETED: set(),
}


def validate_voice_profile_transition(current: str, target: str) -> bool:
    source_state = VoiceProfileStatus(current)
    target_state = VoiceProfileStatus(target)
    if source_state == target_state:
        return True
    if target_state not in _VOICE_PROFILE_TRANSITIONS[source_state]:
        raise MeetingTransitionError(source_state.value, f"voice_profile->{target_state.value}")
    return False


_VOICE_MATCH_TRANSITIONS: dict[VoiceMatchDecision, set[VoiceMatchDecision]] = {
    VoiceMatchDecision.SUGGESTED: {
        VoiceMatchDecision.CONFIRMED,
        VoiceMatchDecision.REJECTED,
    },
    VoiceMatchDecision.AUTO_APPLIED: {
        VoiceMatchDecision.CONFIRMED,
        VoiceMatchDecision.UNDONE,
    },
    VoiceMatchDecision.CONFIRMED: {VoiceMatchDecision.UNDONE},
    VoiceMatchDecision.REJECTED: set(),
    VoiceMatchDecision.UNDONE: {VoiceMatchDecision.CONFIRMED, VoiceMatchDecision.REJECTED},
}


def validate_voice_match_transition(current: str, target: str) -> bool:
    source_state = VoiceMatchDecision(current)
    target_state = VoiceMatchDecision(target)
    if source_state == target_state:
        return True
    if target_state not in _VOICE_MATCH_TRANSITIONS[source_state]:
        raise MeetingTransitionError(source_state.value, f"voice_match->{target_state.value}")
    return False
