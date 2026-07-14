from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from meeting_speech_service.adapters.pipeline import SpeakerAssignment


@dataclass(slots=True)
class _Track:
    prototypes: list[np.ndarray]
    observations: int


@dataclass(slots=True)
class _Candidate:
    prototypes: list[np.ndarray]
    observations: int
    last_end_ms: int


class AnonymousSpeakerCluster:
    """Session-local bounded cosine clustering; it never stores identity or audio.

    A new track needs repeated evidence. Existing tracks use a lower assignment
    threshold than their update threshold, so borderline segments cannot drag a
    stable prototype toward another speaker.
    """

    def __init__(
        self,
        *,
        encoder: Callable[[bytes], np.ndarray],
        threshold: float,
        max_tracks: int,
        min_segment_ms: int,
        sample_rate: int = 16_000,
        update_threshold: float | None = None,
        candidate_threshold: float | None = None,
        candidate_confirmations: int = 1,
        candidate_max_gap_ms: int = 30_000,
        new_track_min_segment_ms: int | None = None,
        min_margin: float = 0.0,
        max_prototypes: int = 8,
        min_rms: float = 0.0,
        max_clipping_ratio: float = 1.0,
        track_namespace: str | None = None,
    ) -> None:
        resolved_update_threshold = threshold if update_threshold is None else update_threshold
        resolved_candidate_threshold = threshold if candidate_threshold is None else candidate_threshold
        resolved_new_track_min_ms = (
            min_segment_ms if new_track_min_segment_ms is None else new_track_min_segment_ms
        )
        if resolved_update_threshold < threshold:
            raise ValueError("update_threshold cannot be lower than threshold")
        if resolved_candidate_threshold < threshold:
            raise ValueError("candidate_threshold cannot be lower than threshold")
        if resolved_new_track_min_ms < min_segment_ms:
            raise ValueError("new_track_min_segment_ms cannot be lower than min_segment_ms")
        if candidate_confirmations < 1 or candidate_max_gap_ms < 0 or max_prototypes < 1:
            raise ValueError("speaker clustering bounds are invalid")
        if not 0.0 <= min_margin <= 1.0:
            raise ValueError("min_margin must be between zero and one")
        if not 0.0 <= min_rms <= 1.0 or not 0.0 <= max_clipping_ratio <= 1.0:
            raise ValueError("speaker signal quality bounds are invalid")
        self._encoder = encoder
        self._threshold = threshold
        self._update_threshold = resolved_update_threshold
        self._candidate_threshold = resolved_candidate_threshold
        self._candidate_confirmations = candidate_confirmations
        self._candidate_max_gap_ms = candidate_max_gap_ms
        self._max_tracks = max_tracks
        self._min_segment_ms = min_segment_ms
        self._new_track_min_segment_ms = resolved_new_track_min_ms
        self._min_margin = min_margin
        self._max_prototypes = max_prototypes
        self._min_rms = min_rms
        self._max_clipping_ratio = max_clipping_ratio
        self._sample_rate = sample_rate
        self._track_namespace = track_namespace.strip() if track_namespace else None
        self._tracks: list[_Track] = []
        self._candidates: list[_Candidate] = []

    def assign(self, pcm: bytes, *, start_ms: int, end_ms: int) -> SpeakerAssignment | None:
        pcm_duration_ms = len(pcm) * 1_000 // (self._sample_rate * 2)
        timeline_duration_ms = max(0, end_ms - start_ms)
        duration_ms = min(pcm_duration_ms, timeline_duration_ms) if timeline_duration_ms else pcm_duration_ms
        if duration_ms < self._min_segment_ms:
            return None
        if not self._usable_signal(pcm):
            return None
        embedding = _normalized(self._encoder(pcm))
        if embedding.size == 0:
            return None
        self._expire_candidates(start_ms)

        if self._tracks:
            similarities = [_similarity(track.prototypes, embedding) for track in self._tracks]
            best_index, best_similarity, margin = _best_match(similarities)
            if best_similarity >= self._threshold:
                if margin is not None and margin < self._min_margin:
                    return None
                track = self._tracks[best_index]
                track.observations = min(track.observations + 1, 1_000_000)
                if best_similarity >= self._update_threshold:
                    _remember_prototype(track.prototypes, embedding, self._max_prototypes)
                return SpeakerAssignment(
                    track_key=self._track_key(best_index),
                    confidence=best_similarity,
                    track_result="reused",
                )

        if duration_ms < self._new_track_min_segment_ms or len(self._tracks) >= self._max_tracks:
            return None

        if self._candidates:
            candidate_similarities = [
                _similarity(candidate.prototypes, embedding) for candidate in self._candidates
            ]
            candidate_index, candidate_similarity, margin = _best_match(candidate_similarities)
            if candidate_similarity >= self._candidate_threshold:
                if margin is not None and margin < self._min_margin:
                    return None
                candidate = self._candidates[candidate_index]
                candidate.observations += 1
                candidate.last_end_ms = end_ms
                _remember_prototype(candidate.prototypes, embedding, self._max_prototypes)
                if candidate.observations >= self._candidate_confirmations:
                    self._candidates.pop(candidate_index)
                    next_index = len(self._tracks)
                    self._tracks.append(
                        _Track(
                            prototypes=list(candidate.prototypes),
                            observations=candidate.observations,
                        )
                    )
                    return SpeakerAssignment(
                        track_key=self._track_key(next_index),
                        confidence=candidate_similarity,
                        track_result="created",
                    )
                return None

        candidate = _Candidate(prototypes=[embedding], observations=1, last_end_ms=end_ms)
        if self._candidate_confirmations == 1:
            next_index = len(self._tracks)
            self._tracks.append(_Track(prototypes=[embedding], observations=1))
            return SpeakerAssignment(
                track_key=self._track_key(next_index),
                confidence=None,
                track_result="created",
            )
        if len(self._candidates) >= self._max_tracks:
            self._candidates.pop(0)
        self._candidates.append(candidate)
        return None

    def _expire_candidates(self, start_ms: int) -> None:
        self._candidates = [
            candidate
            for candidate in self._candidates
            if start_ms <= candidate.last_end_ms
            or start_ms - candidate.last_end_ms <= self._candidate_max_gap_ms
        ]

    def _track_key(self, index: int) -> str:
        local_key = f"speaker-{index}"
        return f"{self._track_namespace}:{local_key}" if self._track_namespace else local_key

    def _usable_signal(self, pcm: bytes) -> bool:
        if not pcm or len(pcm) % 2:
            return False
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        if not samples.size:
            return False
        rms = float(np.sqrt(np.mean(np.square(samples), dtype=np.float64)))
        if not np.isfinite(rms) or rms < self._min_rms:
            return False
        clipping_ratio = float(np.count_nonzero(np.abs(samples) >= 0.999) / samples.size)
        return clipping_ratio <= self._max_clipping_ratio


def _best_match(similarities: list[float]) -> tuple[int, float, float | None]:
    best_index = int(np.argmax(similarities))
    best_similarity = similarities[best_index]
    if len(similarities) == 1:
        return best_index, best_similarity, None
    second_similarity = max(value for index, value in enumerate(similarities) if index != best_index)
    return best_index, best_similarity, best_similarity - second_similarity


def _similarity(prototypes: list[np.ndarray], embedding: np.ndarray) -> float:
    centroid = _robust_centroid(prototypes)
    return float(np.dot(centroid, embedding)) if centroid.size else -1.0


def _robust_centroid(prototypes: list[np.ndarray]) -> np.ndarray:
    if not prototypes:
        return np.empty(0, dtype=np.float32)
    return _normalized(np.median(np.stack(prototypes), axis=0))


def _remember_prototype(prototypes: list[np.ndarray], embedding: np.ndarray, limit: int) -> None:
    prototypes.append(embedding)
    if len(prototypes) > limit:
        del prototypes[: len(prototypes) - limit]


def _normalized(value: np.ndarray) -> np.ndarray:
    vector = np.asarray(value, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm <= 1e-12:
        return np.empty(0, dtype=np.float32)
    normalized = vector / norm
    if not np.all(np.isfinite(normalized)):
        return np.empty(0, dtype=np.float32)
    return normalized
