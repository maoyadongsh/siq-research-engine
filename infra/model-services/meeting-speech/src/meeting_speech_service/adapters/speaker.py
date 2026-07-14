from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from meeting_speech_service.adapters.pipeline import SpeakerAssignment


@dataclass(slots=True)
class _Track:
    centroid: np.ndarray
    observations: int


class AnonymousSpeakerCluster:
    """Session-local bounded cosine clustering; it never stores identity or audio."""

    def __init__(
        self,
        *,
        encoder: Callable[[bytes], np.ndarray],
        threshold: float,
        max_tracks: int,
        min_segment_ms: int,
        sample_rate: int = 16_000,
    ) -> None:
        self._encoder = encoder
        self._threshold = threshold
        self._max_tracks = max_tracks
        self._min_segment_ms = min_segment_ms
        self._sample_rate = sample_rate
        self._tracks: list[_Track] = []

    def assign(self, pcm: bytes, *, start_ms: int, end_ms: int) -> SpeakerAssignment | None:
        duration_ms = len(pcm) * 1_000 // (self._sample_rate * 2)
        if duration_ms < self._min_segment_ms:
            return None
        embedding = _normalized(self._encoder(pcm))
        if embedding.size == 0:
            return None
        if not self._tracks:
            self._tracks.append(_Track(centroid=embedding, observations=1))
            return SpeakerAssignment(track_key="speaker-0", confidence=None)

        similarities = [float(np.dot(track.centroid, embedding)) for track in self._tracks]
        best_index = int(np.argmax(similarities))
        best_similarity = similarities[best_index]
        if best_similarity >= self._threshold:
            track = self._tracks[best_index]
            weight = min(track.observations, 100)
            track.centroid = _normalized((track.centroid * weight + embedding) / (weight + 1))
            track.observations = min(track.observations + 1, 1_000)
            return SpeakerAssignment(track_key=f"speaker-{best_index}", confidence=best_similarity)
        if len(self._tracks) >= self._max_tracks:
            return None
        next_index = len(self._tracks)
        self._tracks.append(_Track(centroid=embedding, observations=1))
        return SpeakerAssignment(track_key=f"speaker-{next_index}", confidence=None)


def _normalized(value: np.ndarray) -> np.ndarray:
    vector = np.asarray(value, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm <= 1e-12:
        return np.empty(0, dtype=np.float32)
    normalized = vector / norm
    if not np.all(np.isfinite(normalized)):
        return np.empty(0, dtype=np.float32)
    return normalized
