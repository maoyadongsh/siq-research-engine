from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from enum import IntFlag
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator

STREAM_SCHEMA_VERSION = "siq.meeting.stream.v1"
SPEECH_EVENT_SCHEMA_VERSION = "siq.meeting.speech.event.v1"
AUDIO_MAGIC = b"SIQA"
AUDIO_FRAME_VERSION = 1
AUDIO_HEADER = struct.Struct("!4sBBHIQQI")
AUDIO_HEADER_BYTES = AUDIO_HEADER.size


class ProtocolError(Exception):
    def __init__(self, code: str, message: str, *, close_code: int = 1008) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.close_code = close_code


class AudioFlags(IntFlag):
    NONE = 0
    END_OF_STREAM = 1 << 0
    DISCONTINUITY = 1 << 1


KNOWN_AUDIO_FLAGS = AudioFlags.END_OF_STREAM | AudioFlags.DISCONTINUITY


@dataclass(frozen=True, slots=True)
class AudioFrame:
    stream_epoch: int
    sequence: int
    capture_time_ms: int
    flags: AudioFlags
    payload: bytes

    @property
    def checksum(self) -> bytes:
        digest = hashlib.blake2s(digest_size=16)
        digest.update(self.stream_epoch.to_bytes(4, "big"))
        digest.update(self.sequence.to_bytes(8, "big"))
        digest.update(self.capture_time_ms.to_bytes(8, "big"))
        digest.update(int(self.flags).to_bytes(1, "big"))
        digest.update(self.payload)
        return digest.digest()


def decode_audio_frame(data: bytes, *, max_payload_bytes: int) -> AudioFrame:
    if len(data) < AUDIO_HEADER_BYTES:
        raise ProtocolError("AUDIO_HEADER_TRUNCATED", "binary frame is shorter than the v1 header")
    magic, version, raw_flags, header_size, epoch, sequence, capture_time_ms, payload_size = AUDIO_HEADER.unpack_from(
        data
    )
    if magic != AUDIO_MAGIC:
        raise ProtocolError("AUDIO_MAGIC_INVALID", "binary frame magic is invalid")
    if version != AUDIO_FRAME_VERSION:
        raise ProtocolError("AUDIO_VERSION_UNSUPPORTED", "binary frame version is unsupported")
    if header_size != AUDIO_HEADER_BYTES:
        raise ProtocolError("AUDIO_HEADER_SIZE_INVALID", "binary frame header size is invalid")
    if raw_flags & ~int(KNOWN_AUDIO_FLAGS):
        raise ProtocolError("AUDIO_FLAGS_UNSUPPORTED", "binary frame contains unsupported flags")
    if payload_size > max_payload_bytes:
        raise ProtocolError("AUDIO_FRAME_TOO_LARGE", "PCM payload exceeds the configured limit", close_code=1009)
    if len(data) != AUDIO_HEADER_BYTES + payload_size:
        raise ProtocolError("AUDIO_PAYLOAD_SIZE_MISMATCH", "binary frame payload size does not match its header")
    if payload_size % 2:
        raise ProtocolError("AUDIO_PCM_ALIGNMENT_INVALID", "PCM16LE payload must contain complete samples")
    flags = AudioFlags(raw_flags)
    if payload_size == 0 and not flags & AudioFlags.END_OF_STREAM:
        raise ProtocolError("AUDIO_PAYLOAD_EMPTY", "empty payload is only valid on an end-of-stream frame")
    return AudioFrame(
        stream_epoch=epoch,
        sequence=sequence,
        capture_time_ms=capture_time_ms,
        flags=flags,
        payload=data[AUDIO_HEADER_BYTES:],
    )


def encode_audio_frame(frame: AudioFrame) -> bytes:
    payload = bytes(frame.payload)
    return (
        AUDIO_HEADER.pack(
            AUDIO_MAGIC,
            AUDIO_FRAME_VERSION,
            int(frame.flags),
            AUDIO_HEADER_BYTES,
            frame.stream_epoch,
            frame.sequence,
            frame.capture_time_ms,
            len(payload),
        )
        + payload
    )


class AudioFormat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    encoding: Literal["pcm_s16le"]
    sample_rate: Literal[16_000]
    channels: Literal[1]
    chunk_ms: int = Field(ge=20, le=2_000)


Hotword = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]


class StreamStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["stream.start"]
    schema_version: Literal[STREAM_SCHEMA_VERSION]
    meeting_id: UUID | None = None
    client_stream_id: UUID
    stream_epoch: int = Field(ge=1, le=0xFFFFFFFF)
    audio: AudioFormat
    last_server_cursor: int | None = Field(default=None, ge=0)
    last_acked_sequence: int = Field(default=-1, ge=-1)
    language: str | None = Field(default=None, min_length=1, max_length=32)
    hotwords: list[Hotword] = Field(default_factory=list, max_length=1_000)
    trace_id: UUID | None = None

    @field_validator("hotwords")
    @classmethod
    def unique_hotwords(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("hotwords must be unique")
        return values


class BaseControl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[STREAM_SCHEMA_VERSION]


class StreamPause(BaseControl):
    type: Literal["stream.pause"]


class StreamResume(BaseControl):
    type: Literal["stream.resume"]


class StreamStop(BaseControl):
    type: Literal["stream.stop"]


class StreamHeartbeat(BaseControl):
    type: Literal["stream.heartbeat"]


class StreamResumeRequest(BaseControl):
    type: Literal["stream.resume_request"]
    last_acked_sequence: int = Field(ge=-1)


ControlMessage = StreamPause | StreamResume | StreamStop | StreamHeartbeat | StreamResumeRequest
CONTROL_MODELS: dict[str, type[ControlMessage]] = {
    "stream.pause": StreamPause,
    "stream.resume": StreamResume,
    "stream.stop": StreamStop,
    "stream.heartbeat": StreamHeartbeat,
    "stream.resume_request": StreamResumeRequest,
}


def parse_stream_start(raw: str) -> StreamStart:
    value = _decode_json_object(raw)
    try:
        return StreamStart.model_validate(value)
    except ValidationError as exc:
        raise ProtocolError("STREAM_START_INVALID", "stream.start does not satisfy the v1 schema") from exc


def parse_control_message(raw: str) -> ControlMessage:
    value = _decode_json_object(raw)
    message_type = value.get("type")
    model = CONTROL_MODELS.get(message_type)
    if model is None:
        raise ProtocolError("CONTROL_TYPE_UNSUPPORTED", "control message type is unsupported")
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        raise ProtocolError("CONTROL_MESSAGE_INVALID", "control message does not satisfy the v1 schema") from exc


def _decode_json_object(raw: str) -> dict[str, object]:
    if len(raw) > 65_536:
        raise ProtocolError(
            "CONTROL_MESSAGE_TOO_LARGE", "control message exceeds the configured limit", close_code=1009
        )
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolError("CONTROL_JSON_INVALID", "control message is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ProtocolError("CONTROL_JSON_INVALID", "control message must be a JSON object")
    return value
