from collections import deque

import numpy as np
from meeting_speech_service.adapters.speaker import AnonymousSpeakerCluster


def test_anonymous_speaker_cluster_reuses_and_bounds_tracks() -> None:
    vectors = deque(
        [
            np.array([1.0, 0.0], dtype=np.float32),
            np.array([0.99, 0.01], dtype=np.float32),
            np.array([0.0, 1.0], dtype=np.float32),
            np.array([-1.0, 0.0], dtype=np.float32),
        ]
    )
    cluster = AnonymousSpeakerCluster(
        encoder=lambda _pcm: vectors.popleft(),
        threshold=0.8,
        max_tracks=2,
        min_segment_ms=1_000,
    )
    pcm = b"\x00\x00" * 16_000

    first = cluster.assign(pcm, start_ms=0, end_ms=1_000)
    same = cluster.assign(pcm, start_ms=1_000, end_ms=2_000)
    second = cluster.assign(pcm, start_ms=2_000, end_ms=3_000)
    overflow = cluster.assign(pcm, start_ms=3_000, end_ms=4_000)

    assert first is not None and first.track_key == "speaker-0"
    assert same is not None and same.track_key == "speaker-0"
    assert same.confidence is not None and same.confidence > 0.99
    assert second is not None and second.track_key == "speaker-1"
    assert overflow is None


def test_short_segment_stays_anonymous_without_calling_encoder() -> None:
    cluster = AnonymousSpeakerCluster(
        encoder=lambda _pcm: (_ for _ in ()).throw(AssertionError("encoder must not run")),
        threshold=0.8,
        max_tracks=2,
        min_segment_ms=1_000,
    )

    assert cluster.assign(b"\x00\x00" * 8_000, start_ms=0, end_ms=500) is None
