import pytest
from meeting_speech_service.protocol import AudioFlags, AudioFrame, ProtocolError
from meeting_speech_service.sequencing import FrameSequencer


def _sequencer(**overrides) -> FrameSequencer:
    values = {
        "last_acked_sequence": -1,
        "max_pending_frames": 4,
        "max_pending_bytes": 100,
        "max_gap_frames": 4,
        "recent_checksums": 16,
    }
    values.update(overrides)
    return FrameSequencer(**values)


def _frame(sequence: int, payload: bytes = b"\x01\x00") -> AudioFrame:
    return AudioFrame(1, sequence, sequence * 100, AudioFlags.NONE, payload)


def test_out_of_order_frames_are_bounded_and_drained_contiguously() -> None:
    sequencer = _sequencer()

    gap = sequencer.offer(_frame(2))
    assert gap.gap == (0, 1)
    assert gap.ready == ()
    assert sequencer.ack_sequence == -1

    first = sequencer.offer(_frame(0))
    assert [frame.sequence for frame in first.ready] == [0]
    assert sequencer.ack_sequence == 0

    drained = sequencer.offer(_frame(1))
    assert [frame.sequence for frame in drained.ready] == [1, 2]
    assert sequencer.ack_sequence == 2
    assert sequencer.pending_bytes == 0


def test_duplicate_is_idempotent_but_content_conflict_is_rejected() -> None:
    sequencer = _sequencer()
    sequencer.offer(_frame(0))

    assert sequencer.offer(_frame(0)).duplicate is True
    with pytest.raises(ProtocolError) as exc_info:
        sequencer.offer(_frame(0, b"\x02\x00"))
    assert exc_info.value.code == "AUDIO_SEQUENCE_CONFLICT"


def test_gap_and_byte_limits_are_hard_failures() -> None:
    with pytest.raises(ProtocolError) as gap_error:
        _sequencer(max_gap_frames=2).offer(_frame(3))
    assert gap_error.value.code == "AUDIO_GAP_TOO_LARGE"

    sequencer = _sequencer(max_pending_bytes=2)
    sequencer.offer(_frame(1))
    with pytest.raises(ProtocolError) as full_error:
        sequencer.offer(_frame(2))
    assert full_error.value.code == "AUDIO_REORDER_BUFFER_FULL"
