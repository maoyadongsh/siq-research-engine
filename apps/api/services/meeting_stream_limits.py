"""Per-connection audio rate limits for the meeting stream gateway."""

from __future__ import annotations

import time
from collections.abc import Callable

from services.meeting_stream_protocol import MeetingStreamProtocolError


class MeetingAudioRateLimiter:
    """A dual token bucket for audio frame count and PCM payload bytes."""

    def __init__(
        self,
        *,
        max_frames_per_second: int,
        max_bytes_per_second: int,
        burst_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_frames_per_second <= 0 or max_bytes_per_second <= 0 or burst_seconds <= 0:
            raise ValueError("audio rate limits must be positive")
        self._frame_rate = float(max_frames_per_second)
        self._byte_rate = float(max_bytes_per_second)
        self._frame_capacity = self._frame_rate * burst_seconds
        self._byte_capacity = self._byte_rate * burst_seconds
        self._frame_tokens = self._frame_capacity
        self._byte_tokens = self._byte_capacity
        self._clock = clock
        self._last_refill = clock()

    def check(self, payload_bytes: int) -> None:
        if payload_bytes < 0:
            raise ValueError("payload_bytes must not be negative")
        now = self._clock()
        elapsed = max(0.0, now - self._last_refill)
        self._last_refill = now
        self._frame_tokens = min(
            self._frame_capacity,
            self._frame_tokens + elapsed * self._frame_rate,
        )
        self._byte_tokens = min(
            self._byte_capacity,
            self._byte_tokens + elapsed * self._byte_rate,
        )
        if self._frame_tokens + 1e-9 < 1.0:
            raise MeetingStreamProtocolError(
                "AUDIO_FRAME_RATE_LIMIT",
                "audio frame rate limit was exceeded",
                close_code=1008,
            )
        if self._byte_tokens + 1e-9 < payload_bytes:
            raise MeetingStreamProtocolError(
                "AUDIO_BYTE_RATE_LIMIT",
                "audio byte rate limit was exceeded",
                close_code=1008,
            )
        self._frame_tokens -= 1.0
        self._byte_tokens -= payload_bytes
