import pytest
from services.meeting_state_machine import (
    MeetingTransitionError,
    resolve_capture_transition,
    resolve_finalize_transition,
    validate_voice_match_transition,
    validate_voice_profile_transition,
)


def test_meeting_capture_state_machine_rejects_illegal_transitions():
    assert resolve_capture_transition("draft", "start").current == "connecting"
    assert resolve_capture_transition("connecting", "mark_live").current == "live"
    assert resolve_capture_transition("live", "pause").current == "paused"
    resumed = resolve_capture_transition("paused", "resume")
    assert resumed.current == "reconnecting"
    assert resumed.increment_stream_epoch is True
    assert resolve_capture_transition("reconnecting", "mark_live").current == "live"
    reconnecting = resolve_capture_transition("live", "mark_reconnecting")
    assert reconnecting.current == "reconnecting"
    assert reconnecting.increment_stream_epoch is False
    assert resolve_capture_transition("live", "stop").current == "stopping"
    assert resolve_capture_transition("stopping", "mark_stopped").current == "stopped"
    assert resolve_capture_transition("stopped", "archive").current == "archived"

    with pytest.raises(MeetingTransitionError):
        resolve_capture_transition("draft", "pause")
    with pytest.raises(MeetingTransitionError):
        resolve_capture_transition("stopped", "resume")


def test_meeting_actions_are_idempotent_in_their_terminal_state():
    assert resolve_capture_transition("connecting", "start").idempotent is True
    assert resolve_capture_transition("paused", "pause").idempotent is True
    assert resolve_capture_transition("stopped", "stop").idempotent is True
    assert resolve_capture_transition("deleted", "delete").idempotent is True


def test_finalize_is_independent_from_capture_state():
    transition = resolve_finalize_transition("stopped", "not_started")
    assert transition.current == "queued"
    assert resolve_finalize_transition("stopped", "queued").idempotent is True
    with pytest.raises(MeetingTransitionError):
        resolve_finalize_transition("live", "not_started")


def test_voiceprint_state_and_decision_rules_are_explicit():
    assert validate_voice_profile_transition("active", "paused") is False
    assert validate_voice_profile_transition("paused", "active") is False
    assert validate_voice_match_transition("suggested", "confirmed") is False
    assert validate_voice_match_transition("auto_applied", "undone") is False
    with pytest.raises(MeetingTransitionError):
        validate_voice_match_transition("rejected", "confirmed")
