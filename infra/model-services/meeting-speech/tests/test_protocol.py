import struct

import pytest
from meeting_speech_service.protocol import (
    AUDIO_HEADER_BYTES,
    AudioFlags,
    AudioFrame,
    ProtocolError,
    decode_audio_frame,
    encode_audio_frame,
    parse_control_message,
    parse_stream_start,
)


def test_v1_audio_header_is_fixed_32_bytes_and_round_trips() -> None:
    assert AUDIO_HEADER_BYTES == 32
    frame = AudioFrame(
        stream_epoch=7,
        sequence=42,
        capture_time_ms=12_500,
        flags=AudioFlags.DISCONTINUITY,
        payload=b"\x01\x00\xff\x7f",
    )

    assert decode_audio_frame(encode_audio_frame(frame), max_payload_bytes=100) == frame


@pytest.mark.parametrize(
    ("mutator", "code"),
    [
        (lambda raw: raw[:4] + b"\x02" + raw[5:], "AUDIO_VERSION_UNSUPPORTED"),
        (lambda raw: raw[:-1], "AUDIO_PAYLOAD_SIZE_MISMATCH"),
        (lambda raw: raw + b"\x00", "AUDIO_PAYLOAD_SIZE_MISMATCH"),
    ],
)
def test_invalid_audio_frames_are_rejected(mutator, code: str) -> None:
    raw = encode_audio_frame(AudioFrame(1, 0, 0, AudioFlags.NONE, b"\x01\x00"))
    with pytest.raises(ProtocolError) as exc_info:
        decode_audio_frame(mutator(raw), max_payload_bytes=100)
    assert exc_info.value.code == code


def test_odd_pcm_payload_is_rejected() -> None:
    raw = struct.pack("!4sBBHIQQI", b"SIQA", 1, 0, 32, 1, 0, 0, 1) + b"\x00"
    with pytest.raises(ProtocolError, match="complete samples"):
        decode_audio_frame(raw, max_payload_bytes=100)


def test_stream_start_schema_is_strict() -> None:
    start = parse_stream_start(
        """{
          "type":"stream.start",
          "schema_version":"siq.meeting.stream.v1",
          "client_stream_id":"4da63e17-30d0-443f-937f-d5da3ac36313",
          "stream_epoch":1,
          "audio":{"encoding":"pcm_s16le","sample_rate":16000,"channels":1,"chunk_ms":500}
        }"""
    )
    assert start.last_acked_sequence == -1

    with pytest.raises(ProtocolError) as exc_info:
        parse_stream_start(
            """{
              "type":"stream.start",
              "schema_version":"siq.meeting.stream.v2",
              "client_stream_id":"4da63e17-30d0-443f-937f-d5da3ac36313",
              "stream_epoch":1,
              "audio":{"encoding":"pcm_s16le","sample_rate":16000,"channels":1,"chunk_ms":500}
            }"""
        )
    assert exc_info.value.code == "STREAM_START_INVALID"


def test_heartbeat_carries_the_browser_next_sequence_for_hotword_boundaries() -> None:
    heartbeat = parse_control_message(
        '{"type":"stream.heartbeat","schema_version":"siq.meeting.stream.v1","next_sequence":9}'
    )
    assert heartbeat.next_sequence == 9
