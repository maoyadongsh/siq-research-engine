"""Browser-to-gateway protocol validation for meeting audio streams."""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

STREAM_SCHEMA_VERSION = "siq.meeting.stream.v1"
SPEECH_EVENT_SCHEMA_VERSION = "siq.meeting.speech.event.v1"
PUBLIC_EVENT_SCHEMA_VERSION = "siq.meeting.event.v1"
AUDIO_HEADER = struct.Struct("!4sBBHIQQI")
AUDIO_MAGIC = b"SIQA"
AUDIO_VERSION = 1
END_OF_STREAM = 1
DISCONTINUITY = 2
KNOWN_FLAGS = END_OF_STREAM | DISCONTINUITY


class MeetingStreamProtocolError(ValueError):
    def __init__(self, code: str, message: str, *, close_code: int = 1008) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.close_code = close_code


@dataclass(frozen=True, slots=True)
class GatewayAudioFrame:
    stream_epoch: int
    sequence: int
    capture_time_ms: int
    flags: int
    payload: bytes
    raw: bytes


class GatewayAudioFormat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    encoding: Literal["pcm_s16le"]
    sample_rate: Literal[16_000]
    channels: Literal[1]
    chunk_ms: int = Field(ge=100, le=1_000)


class GatewayStreamStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["stream.start"]
    schema_version: Literal[STREAM_SCHEMA_VERSION]
    meeting_id: UUID | None = None
    client_stream_id: UUID
    stream_epoch: int = Field(ge=1, le=0xFFFFFFFF)
    audio: GatewayAudioFormat
    last_server_cursor: int | None = Field(default=None, ge=0)
    last_acked_sequence: int = Field(default=-1, ge=-1)
    language: str | None = Field(default=None, min_length=1, max_length=32)
    hotwords: list[str] = Field(default_factory=list, max_length=1_000)
    hotword_version: int | None = Field(default=None, ge=1)
    trace_id: UUID | None = None


class GatewayHotwordUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["stream.hotwords.update"]
    schema_version: Literal[STREAM_SCHEMA_VERSION]
    request_id: UUID
    hotword_version: int = Field(ge=1)
    effective_sequence: int = Field(ge=0)
    hotwords: list[str] = Field(default_factory=list, max_length=1_000)

    @field_validator("hotwords")
    @classmethod
    def validate_hotwords(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 256 for value in normalized):
            raise ValueError("hotwords must be non-empty and bounded")
        if len(normalized) != len(set(normalized)):
            raise ValueError("hotwords must be unique")
        return normalized


_CONTROLS = {
    "stream.pause",
    "stream.resume",
    "stream.stop",
    "stream.heartbeat",
    "stream.resume_request",
    "stream.hotwords.update",
}


def decode_audio_frame(data: bytes, *, max_payload_bytes: int) -> GatewayAudioFrame:
    if len(data) < AUDIO_HEADER.size:
        raise MeetingStreamProtocolError("AUDIO_HEADER_TRUNCATED", "audio frame header is truncated")
    magic, version, flags, header_size, epoch, sequence, capture_ms, payload_size = AUDIO_HEADER.unpack_from(data)
    if magic != AUDIO_MAGIC:
        raise MeetingStreamProtocolError("AUDIO_MAGIC_INVALID", "audio frame magic is invalid")
    if version != AUDIO_VERSION:
        raise MeetingStreamProtocolError("AUDIO_VERSION_UNSUPPORTED", "audio frame version is unsupported")
    if header_size != AUDIO_HEADER.size:
        raise MeetingStreamProtocolError("AUDIO_HEADER_SIZE_INVALID", "audio header size is invalid")
    if flags & ~KNOWN_FLAGS:
        raise MeetingStreamProtocolError("AUDIO_FLAGS_UNSUPPORTED", "audio flags are unsupported")
    if payload_size > max_payload_bytes:
        raise MeetingStreamProtocolError("AUDIO_FRAME_TOO_LARGE", "audio frame is too large", close_code=1009)
    if len(data) != AUDIO_HEADER.size + payload_size:
        raise MeetingStreamProtocolError("AUDIO_PAYLOAD_SIZE_MISMATCH", "audio payload size is invalid")
    payload = data[AUDIO_HEADER.size :]
    if len(payload) % 2:
        raise MeetingStreamProtocolError("AUDIO_PCM_ALIGNMENT_INVALID", "PCM16 payload is misaligned")
    if not payload and not flags & END_OF_STREAM:
        raise MeetingStreamProtocolError("AUDIO_PAYLOAD_EMPTY", "audio payload is empty")
    return GatewayAudioFrame(epoch, sequence, capture_ms, flags, payload, data)


def _json_object(raw: str) -> dict[str, Any]:
    if len(raw) > 65_536:
        raise MeetingStreamProtocolError("CONTROL_MESSAGE_TOO_LARGE", "control message is too large", close_code=1009)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MeetingStreamProtocolError("CONTROL_JSON_INVALID", "control message is invalid JSON") from exc
    if not isinstance(value, dict):
        raise MeetingStreamProtocolError("CONTROL_JSON_INVALID", "control message must be an object")
    return value

def parse_stream_start(raw: str) -> GatewayStreamStart:
    try:
        return GatewayStreamStart.model_validate(_json_object(raw))
    except ValidationError as exc:
        raise MeetingStreamProtocolError("STREAM_START_INVALID", "stream.start is invalid") from exc


def parse_control(raw: str) -> dict[str, Any]:
    value = _json_object(raw)
    if value.get("schema_version") != STREAM_SCHEMA_VERSION or value.get("type") not in _CONTROLS:
        raise MeetingStreamProtocolError("CONTROL_MESSAGE_INVALID", "control message is invalid")
    if value["type"] == "stream.hotwords.update":
        try:
            return GatewayHotwordUpdate.model_validate(value).model_dump(mode="json")
        except ValidationError as exc:
            raise MeetingStreamProtocolError("CONTROL_MESSAGE_INVALID", "hotword update is invalid") from exc
    if value["type"] == "stream.resume_request":
        sequence = value.get("last_acked_sequence")
        if not isinstance(sequence, int) or sequence < -1:
            raise MeetingStreamProtocolError("CONTROL_MESSAGE_INVALID", "resume sequence is invalid")
    if value["type"] == "stream.heartbeat" and "next_sequence" in value:
        next_sequence = value.get("next_sequence")
        if not isinstance(next_sequence, int) or isinstance(next_sequence, bool) or next_sequence < 0:
            raise MeetingStreamProtocolError("CONTROL_MESSAGE_INVALID", "heartbeat sequence is invalid")
    allowed = {"type", "schema_version"}
    if value["type"] == "stream.resume_request":
        allowed.add("last_acked_sequence")
    elif value["type"] == "stream.heartbeat":
        allowed.add("next_sequence")
    if set(value) - allowed:
        raise MeetingStreamProtocolError("CONTROL_MESSAGE_INVALID", "control message has unsupported fields")
    return value


def parse_speech_event(raw: str, *, meeting_id: str, stream_epoch: int) -> dict[str, Any]:
    value = _json_object(raw)
    if value.get("schema_version") != SPEECH_EVENT_SCHEMA_VERSION:
        raise MeetingStreamProtocolError("SPEECH_EVENT_SCHEMA_INVALID", "speech event schema is invalid", close_code=1011)
    if str(value.get("meeting_id")) != meeting_id or value.get("stream_epoch") != stream_epoch:
        raise MeetingStreamProtocolError("SPEECH_EVENT_SCOPE_INVALID", "speech event scope is invalid", close_code=1011)
    if not isinstance(value.get("type"), str) or not isinstance(value.get("payload"), dict):
        raise MeetingStreamProtocolError("SPEECH_EVENT_INVALID", "speech event is invalid", close_code=1011)
    return value
