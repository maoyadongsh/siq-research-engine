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
    assert first.track_result == "created"
    assert same is not None and same.track_key == "speaker-0"
    assert same.track_result == "reused"
    assert same.confidence is not None and same.confidence > 0.99
    assert second is not None and second.track_key == "speaker-1"
    assert second.track_result == "created"
    assert overflow is None


def test_short_segment_stays_anonymous_without_calling_encoder() -> None:
    cluster = AnonymousSpeakerCluster(
        encoder=lambda _pcm: (_ for _ in ()).throw(AssertionError("encoder must not run")),
        threshold=0.8,
        max_tracks=2,
        min_segment_ms=1_000,
    )

    assert cluster.assign(b"\x00\x00" * 8_000, start_ms=0, end_ms=500) is None


def test_new_track_requires_repeated_candidate_evidence() -> None:
    vectors = deque(
        [
            np.array([1.0, 0.0], dtype=np.float32),
            np.array([0.99, 0.01], dtype=np.float32),
        ]
    )
    cluster = AnonymousSpeakerCluster(
        encoder=lambda _pcm: vectors.popleft(),
        threshold=0.72,
        update_threshold=0.82,
        candidate_threshold=0.78,
        candidate_confirmations=2,
        max_tracks=4,
        min_segment_ms=1_000,
        new_track_min_segment_ms=1_500,
        track_namespace="epoch-7",
    )
    pcm = (8_000).to_bytes(2, "little", signed=True) * 24_000

    assert cluster.assign(pcm, start_ms=0, end_ms=1_500) is None
    confirmed = cluster.assign(pcm, start_ms=2_000, end_ms=3_500)

    assert confirmed is not None
    assert confirmed.track_key == "epoch-7:speaker-0"
    assert confirmed.track_result == "created"
    assert confirmed.confidence is not None and confirmed.confidence > 0.99


def test_low_quality_signal_does_not_call_encoder_or_create_candidate() -> None:
    cluster = AnonymousSpeakerCluster(
        encoder=lambda _pcm: (_ for _ in ()).throw(AssertionError("encoder must not run")),
        threshold=0.72,
        max_tracks=4,
        min_segment_ms=1_000,
        new_track_min_segment_ms=1_500,
        min_rms=0.01,
        max_clipping_ratio=0.1,
    )
    quiet = (10).to_bytes(2, "little", signed=True) * 24_000
    clipped = (32_767).to_bytes(2, "little", signed=True) * 24_000

    assert cluster.assign(quiet, start_ms=0, end_ms=1_500) is None
    assert cluster.assign(clipped, start_ms=2_000, end_ms=3_500) is None


def test_borderline_assignment_does_not_poison_robust_prototypes() -> None:
    borderline = np.array([0.75, np.sqrt(1.0 - 0.75**2)], dtype=np.float32)
    vectors = deque(
        [
            np.array([1.0, 0.0], dtype=np.float32),
            np.array([0.99, 0.01], dtype=np.float32),
            *[borderline.copy() for _ in range(12)],
            np.array([1.0, 0.0], dtype=np.float32),
        ]
    )
    cluster = AnonymousSpeakerCluster(
        encoder=lambda _pcm: vectors.popleft(),
        threshold=0.72,
        update_threshold=0.82,
        candidate_threshold=0.78,
        candidate_confirmations=2,
        max_tracks=4,
        min_segment_ms=1_000,
        new_track_min_segment_ms=1_000,
        max_prototypes=4,
    )
    pcm = (8_000).to_bytes(2, "little", signed=True) * 16_000

    assert cluster.assign(pcm, start_ms=0, end_ms=1_000) is None
    assert cluster.assign(pcm, start_ms=1_000, end_ms=2_000) is not None
    for index in range(12):
        assignment = cluster.assign(
            pcm,
            start_ms=(index + 2) * 1_000,
            end_ms=(index + 3) * 1_000,
        )
        assert assignment is not None and assignment.track_key == "speaker-0"
    stable = cluster.assign(pcm, start_ms=14_000, end_ms=15_000)

    assert stable is not None
    assert stable.track_result == "reused"
    assert stable.confidence is not None and stable.confidence > 0.99


def test_ambiguous_top_two_match_stays_anonymous() -> None:
    diagonal = np.array([1.0, 1.0], dtype=np.float32)
    vectors = deque(
        [
            np.array([1.0, 0.0], dtype=np.float32),
            np.array([0.99, 0.01], dtype=np.float32),
            np.array([0.0, 1.0], dtype=np.float32),
            np.array([0.01, 0.99], dtype=np.float32),
            diagonal,
        ]
    )
    cluster = AnonymousSpeakerCluster(
        encoder=lambda _pcm: vectors.popleft(),
        threshold=0.7,
        update_threshold=0.85,
        candidate_threshold=0.8,
        candidate_confirmations=2,
        min_margin=0.05,
        max_tracks=4,
        min_segment_ms=1_000,
        new_track_min_segment_ms=1_000,
    )
    pcm = (8_000).to_bytes(2, "little", signed=True) * 16_000

    assert cluster.assign(pcm, start_ms=0, end_ms=1_000) is None
    assert cluster.assign(pcm, start_ms=1_000, end_ms=2_000) is not None
    assert cluster.assign(pcm, start_ms=2_000, end_ms=3_000) is None
    assert cluster.assign(pcm, start_ms=3_000, end_ms=4_000) is not None
    assert cluster.assign(pcm, start_ms=4_000, end_ms=5_000) is None


def test_candidate_expires_after_configured_gap() -> None:
    vectors = deque([np.array([1.0, 0.0], dtype=np.float32) for _ in range(3)])
    cluster = AnonymousSpeakerCluster(
        encoder=lambda _pcm: vectors.popleft(),
        threshold=0.72,
        candidate_threshold=0.78,
        candidate_confirmations=2,
        candidate_max_gap_ms=5_000,
        max_tracks=4,
        min_segment_ms=1_000,
        new_track_min_segment_ms=1_000,
    )
    pcm = (8_000).to_bytes(2, "little", signed=True) * 16_000

    assert cluster.assign(pcm, start_ms=0, end_ms=1_000) is None
    assert cluster.assign(pcm, start_ms=10_000, end_ms=11_000) is None
    assert cluster.assign(pcm, start_ms=11_500, end_ms=12_500) is not None
