from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from meeting_speech_service.protocol import AudioFrame, ProtocolError


@dataclass(frozen=True, slots=True)
class SequenceOffer:
    ready: tuple[AudioFrame, ...]
    duplicate: bool
    expected_before: int
    received_sequence: int
    pending_frames: int
    pending_bytes: int

    @property
    def gap(self) -> tuple[int, int] | None:
        if self.received_sequence <= self.expected_before:
            return None
        return self.expected_before, self.received_sequence - 1


class FrameSequencer:
    """Bounded in-memory reorder buffer with replay conflict detection."""

    def __init__(
        self,
        *,
        last_acked_sequence: int,
        max_pending_frames: int,
        max_pending_bytes: int,
        max_gap_frames: int,
        recent_checksums: int,
    ) -> None:
        self._expected = last_acked_sequence + 1
        self._max_pending_frames = max_pending_frames
        self._max_pending_bytes = max_pending_bytes
        self._max_gap_frames = max_gap_frames
        self._recent_checksums = recent_checksums
        self._pending: dict[int, tuple[AudioFrame, bytes]] = {}
        self._pending_bytes = 0
        self._processed: OrderedDict[int, bytes] = OrderedDict()

    @property
    def ack_sequence(self) -> int:
        return self._expected - 1

    @property
    def pending_frames(self) -> int:
        return len(self._pending)

    @property
    def pending_bytes(self) -> int:
        return self._pending_bytes

    def offer(self, frame: AudioFrame) -> SequenceOffer:
        expected_before = self._expected
        checksum = frame.checksum
        if frame.sequence < self._expected:
            known = self._processed.get(frame.sequence)
            if known is not None and known != checksum:
                raise ProtocolError("AUDIO_SEQUENCE_CONFLICT", "replayed sequence has different frame content")
            return self._result((), True, expected_before, frame.sequence)

        pending = self._pending.get(frame.sequence)
        if pending is not None:
            if pending[1] != checksum:
                raise ProtocolError("AUDIO_SEQUENCE_CONFLICT", "buffered sequence has different frame content")
            return self._result((), True, expected_before, frame.sequence)

        if frame.sequence - self._expected > self._max_gap_frames:
            raise ProtocolError(
                "AUDIO_GAP_TOO_LARGE", "audio sequence gap exceeds the bounded reorder window", close_code=1013
            )
        if len(self._pending) >= self._max_pending_frames:
            raise ProtocolError("AUDIO_REORDER_BUFFER_FULL", "audio reorder frame limit reached", close_code=1013)
        if self._pending_bytes + len(frame.payload) > self._max_pending_bytes:
            raise ProtocolError("AUDIO_REORDER_BUFFER_FULL", "audio reorder byte limit reached", close_code=1013)

        self._pending[frame.sequence] = (frame, checksum)
        self._pending_bytes += len(frame.payload)
        ready: list[AudioFrame] = []
        while self._expected in self._pending:
            next_frame, next_checksum = self._pending.pop(self._expected)
            self._pending_bytes -= len(next_frame.payload)
            ready.append(next_frame)
            self._remember_processed(self._expected, next_checksum)
            self._expected += 1
        return self._result(tuple(ready), False, expected_before, frame.sequence)

    def validate_resume(self, client_last_acked_sequence: int) -> int:
        if client_last_acked_sequence > self.ack_sequence:
            raise ProtocolError("RESUME_ACK_AHEAD", "client ACK is ahead of retained server state")
        return self.ack_sequence

    def _remember_processed(self, sequence: int, checksum: bytes) -> None:
        self._processed[sequence] = checksum
        self._processed.move_to_end(sequence)
        while len(self._processed) > self._recent_checksums:
            self._processed.popitem(last=False)

    def _result(
        self,
        ready: tuple[AudioFrame, ...],
        duplicate: bool,
        expected_before: int,
        received_sequence: int,
    ) -> SequenceOffer:
        return SequenceOffer(
            ready=ready,
            duplicate=duplicate,
            expected_before=expected_before,
            received_sequence=received_sequence,
            pending_frames=len(self._pending),
            pending_bytes=self._pending_bytes,
        )
