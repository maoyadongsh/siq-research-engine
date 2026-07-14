from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import anyio
import httpx
import pytest
from services.meeting_contracts import (
    MeetingAudioChunk,
    MeetingSession,
    MeetingSpeakerTrack,
    MeetingTranscriptSegment,
)
from services.meeting_speaker_recluster import (
    DIARIZATION_REPORT_GATE_KEYS,
    DIARIZATION_REPORT_LIMITS,
    DIARIZATION_REPORT_MINIMUM_SAMPLE,
    DIARIZATION_REPORT_PRIVACY_BOUNDARY,
    DIARIZATION_REPORT_SCORING_PROTOCOL,
    DiarizationEmbedding,
    HttpDiarizationEmbeddingClient,
    MeetingSpeakerReclusterError,
    MeetingSpeakerReclusterService,
    SpeakerReclusterPolicy,
    TrackEmbedding,
    plan_track_merges,
    select_sample_windows,
)


class _FakeAudioStore:
    def __init__(self) -> None:
        self.calls = []

    def read_pcm_range(self, owner_id, meeting_id, chunks, start_ms, end_ms, max_bytes):
        self.calls.append((owner_id, meeting_id, len(chunks), start_ms, end_ms, max_bytes))
        return (1000).to_bytes(2, "little", signed=True) * ((end_ms - start_ms) * 16)


class _FakeEmbeddingClient:
    def __init__(self) -> None:
        self.calls = []

    async def embed(self, pcm, *, meeting_id, run_id):
        self.calls.append((len(pcm), meeting_id, run_id))
        return DiarizationEmbedding("encoder-v1", (1.0, 0.0), len(pcm) // 32)


class _ChangingDimensionEmbeddingClient(_FakeEmbeddingClient):
    async def embed(self, pcm, *, meeting_id, run_id):
        result = await super().embed(pcm, meeting_id=meeting_id, run_id=run_id)
        if len(self.calls) == 1:
            return result
        return DiarizationEmbedding("encoder-v1", (1.0, 0.0, 0.0), len(pcm) // 32)


def _segment(
    ordinal: int,
    track_id: str,
    *,
    duration_ms: int = 2_000,
    noise_level: float | None = 0.1,
    asr_confidence: float | None = 0.9,
    overlap: bool = False,
) -> MeetingTranscriptSegment:
    start = (ordinal - 1) * 3_000
    return MeetingTranscriptSegment(
        meeting_id="meeting-1",
        ordinal=ordinal,
        utterance_id=f"utterance-{ordinal}",
        provider_segment_key=f"provider-{ordinal}",
        start_ms=start,
        end_ms=start + duration_ms,
        speaker_track_id=track_id,
        raw_text="测试",
        asr_final_text="测试",
        asr_provider="test",
        asr_model="test",
        asr_version="v1",
        noise_level=noise_level,
        asr_confidence=asr_confidence,
        overlap=overlap,
    )


def _policy(**overrides) -> SpeakerReclusterPolicy:
    return SpeakerReclusterPolicy(
        version="speaker-recluster.validated.calibration.v1",
        final_diarizer_ref="final-diarizer-test-v1",
        auto_apply_validated=True,
        validation_artifact_sha256="a" * 64,
        operator_enabled=True,
        **overrides,
    )


def test_sample_windows_reject_short_noisy_and_overlapping_segments() -> None:
    selected = select_sample_windows(
        [
            _segment(1, "track-a", duration_ms=900),
            _segment(2, "track-a", noise_level=0.9),
            _segment(3, "track-a", overlap=True),
            _segment(4, "track-a", duration_ms=10_000),
            _segment(5, "track-b", asr_confidence=0.2),
        ],
        policy=_policy(max_segment_ms=8_000),
    )
    assert [(value.track_id, value.duration_ms) for value in selected] == [("track-a", 8_000)]


def test_sample_window_cap_round_robins_tracks_before_taking_second_samples() -> None:
    selected = select_sample_windows(
        [
            _segment(ordinal, track_id)
            for ordinal, track_id in (
                (1, "track-a"),
                (2, "track-a"),
                (3, "track-a"),
                (4, "track-b"),
                (5, "track-b"),
                (6, "track-b"),
            )
        ],
        policy=_policy(max_samples_per_track=3, max_total_samples=4, max_tracks=2),
    )
    assert [value.track_id for value in selected] == ["track-a", "track-b", "track-a", "track-b"]


def test_service_reads_bounded_samples_and_returns_only_ephemeral_track_mapping() -> None:
    async def scenario() -> None:
        meeting = MeetingSession(owner_user_id=7, title="speaker recluster")
        first = MeetingSpeakerTrack(
            meeting_id=meeting.id,
            track_key="epoch-1:speaker-0",
            anonymous_label="发言人 1",
        )
        second = MeetingSpeakerTrack(
            meeting_id=meeting.id,
            track_key="epoch-2:speaker-0",
            anonymous_label="发言人 2",
        )
        segments = [
            _segment(1, first.id),
            _segment(2, first.id),
            _segment(3, second.id),
            _segment(4, second.id),
        ]
        audio_store = _FakeAudioStore()
        embedding_client = _FakeEmbeddingClient()
        service = MeetingSpeakerReclusterService(
            policy=_policy(
                review_min_score=0.8,
                merge_min_score=0.9,
                singleton_merge_min_score=0.9,
                max_samples_per_track=2,
            ),
            embedding_client=embedding_client,
            audio_store=audio_store,
        )
        run_id = str(uuid4())
        chunks = [
            MeetingAudioChunk(
                meeting_id=meeting.id,
                stream_epoch=1,
                sequence=index,
                start_ms=index * 1_000,
                duration_ms=1_000,
                storage_key=f"chunk-{index}",
                sha256="0" * 64,
                byte_size=32_000,
            )
            for index in range(12)
        ]
        plan = await service.plan(
            meeting=meeting,
            run_id=run_id,
            tracks=[first, second],
            segments=segments,
            chunks=chunks,
        )

        assert len(audio_store.calls) == 4
        assert all(call[2] == 2 for call in audio_store.calls)
        assert all(call[-1] <= 8_000 * 32 for call in audio_store.calls)
        assert all(call[1] == meeting.id for call in audio_store.calls)
        assert all(
            scoped_meeting_id == meeting.id and scoped_run_id == run_id
            for _, scoped_meeting_id, scoped_run_id in embedding_client.calls
        )
        assert plan.embedded_track_count == 2
        assert plan.selected_sample_count == 4
        assert len(plan.track_targets) == 1
        assert set(plan.track_targets) | set(plan.track_targets.values()) == {first.id, second.id}
        assert not hasattr(plan, "embeddings")

    anyio.run(scenario)


def test_service_converts_encoder_dimension_change_to_safe_recluster_error() -> None:
    async def scenario() -> None:
        meeting = MeetingSession(owner_user_id=7, title="speaker recluster")
        track = MeetingSpeakerTrack(
            meeting_id=meeting.id,
            track_key="epoch-1:speaker-0",
            anonymous_label="发言人 1",
        )
        service = MeetingSpeakerReclusterService(
            policy=_policy(max_samples_per_track=2),
            embedding_client=_ChangingDimensionEmbeddingClient(),
            audio_store=_FakeAudioStore(),
        )
        with pytest.raises(MeetingSpeakerReclusterError) as raised:
            await service.plan(
                meeting=meeting,
                run_id=str(uuid4()),
                tracks=[track],
                segments=[_segment(1, track.id), _segment(2, track.id)],
                chunks=[],
            )
        assert raised.value.code == "SPEAKER_RECLUSTER_ENCODER_CHANGED"

    anyio.run(scenario)


def test_unvalidated_policy_only_emits_review_proposal() -> None:
    policy = SpeakerReclusterPolicy(review_min_score=0.8, merge_min_score=0.85)
    plan = plan_track_merges(
        [
            TrackEmbedding("track-a", (1.0, 0.0), 2, 4_000),
            TrackEmbedding("track-b", (0.99, 0.1), 2, 4_000),
        ],
        protected_track_ids=set(),
        policy=policy,
    )
    assert plan.track_targets == {}
    assert len(plan.proposals) == 1
    assert plan.proposals[0].auto_apply is False
    assert plan.proposals[0].reason_code == "POLICY_NOT_VALIDATED"


def test_validated_policy_merges_two_tracks_with_complete_link() -> None:
    plan = plan_track_merges(
        [
            TrackEmbedding("track-a", (1.0, 0.0), 3, 6_000),
            TrackEmbedding("track-b", (0.995, 0.1), 3, 6_000),
            TrackEmbedding("track-c", (0.0, 1.0), 3, 6_000),
        ],
        protected_track_ids=set(),
        policy=_policy(review_min_score=0.8, merge_min_score=0.9, singleton_merge_min_score=0.9),
    )
    assert plan.track_targets == {"track-b": "track-a"}
    assert any(item.auto_apply for item in plan.proposals)


def test_validated_policy_merges_three_fragments_without_pairwise_margin_deadlock() -> None:
    plan = plan_track_merges(
        [
            TrackEmbedding("track-a", (1.0, 0.0), 3, 9_000),
            TrackEmbedding("track-b", (0.995, 0.1), 3, 6_000),
            TrackEmbedding("track-c", (0.99, 0.12), 3, 6_000),
        ],
        protected_track_ids=set(),
        policy=_policy(review_min_score=0.8, merge_min_score=0.9, singleton_merge_min_score=0.9),
    )
    assert plan.track_targets == {"track-b": "track-a", "track-c": "track-a"}
    assert len(plan.proposals) == 1
    assert plan.proposals[0].auto_apply is True
    assert plan.proposals[0].reason_code == "AUTO_MERGE"


def test_component_margin_blocks_ambiguous_nearest_neighbour_merge() -> None:
    plan = plan_track_merges(
        [
            TrackEmbedding("track-a", (1.0, 0.0, 0.0), 3, 6_000),
            TrackEmbedding("track-b", (0.95, 0.3122499, 0.0), 3, 6_000),
            TrackEmbedding("track-c", (0.94, 0.0, 0.3411744), 3, 6_000),
        ],
        protected_track_ids=set(),
        policy=_policy(
            review_min_score=0.8,
            merge_min_score=0.9,
            singleton_merge_min_score=0.9,
            min_top2_margin=0.04,
        ),
    )
    assert plan.track_targets == {}
    assert any(item.reason_code == "LOW_TOP2_MARGIN" for item in plan.proposals)


def test_protected_tracks_never_auto_merge_with_each_other() -> None:
    plan = plan_track_merges(
        [
            TrackEmbedding("manual-a", (1.0, 0.0), 3, 6_000),
            TrackEmbedding("manual-b", (0.995, 0.1), 3, 6_000),
        ],
        protected_track_ids={"manual-a", "manual-b"},
        policy=_policy(review_min_score=0.8, merge_min_score=0.9, singleton_merge_min_score=0.9),
    )
    assert plan.track_targets == {}
    assert plan.proposals[0].reason_code == "PROTECTED_TRACK_CONFLICT"


def test_protected_identity_requires_review_before_attributing_anonymous_audio() -> None:
    plan = plan_track_merges(
        [
            TrackEmbedding("voiceprint-auto", (1.0, 0.0), 3, 4_000),
            TrackEmbedding("anonymous", (0.995, 0.1), 3, 8_000),
        ],
        protected_track_ids={"voiceprint-auto"},
        policy=_policy(review_min_score=0.8, merge_min_score=0.9, singleton_merge_min_score=0.9),
    )
    assert plan.track_targets == {}
    assert plan.proposals[0].target_track_id == "voiceprint-auto"
    assert plan.proposals[0].auto_apply is False
    assert plan.proposals[0].reason_code == "PROTECTED_IDENTITY_REVIEW_REQUIRED"


def test_policy_rejects_unvalidated_auto_apply() -> None:
    with pytest.raises(ValueError, match="unvalidated"):
        SpeakerReclusterPolicy(auto_apply_validated=True)


def test_policy_requires_independent_operator_gate_and_evidence_hash() -> None:
    with pytest.raises(ValueError, match="operator gate"):
        SpeakerReclusterPolicy(
            version="speaker-recluster.validated.v1",
            final_diarizer_ref="final-diarizer-test-v1",
            auto_apply_validated=True,
            validation_artifact_sha256="a" * 64,
        )
    with pytest.raises(ValueError, match="artifact hash"):
        SpeakerReclusterPolicy(
            version="speaker-recluster.validated.v1",
            final_diarizer_ref="final-diarizer-test-v1",
            auto_apply_validated=True,
            operator_enabled=True,
        )


def test_policy_from_env_requires_matching_passing_validation_report(monkeypatch, tmp_path) -> None:
    thresholds = {
        "review_min_score": 0.72,
        "merge_min_score": 0.82,
        "singleton_merge_min_score": 0.92,
        "min_top2_margin": 0.04,
        "min_segment_ms": 1_500,
        "max_segment_ms": 8_000,
        "max_samples_per_track": 4,
        "max_total_samples": 256,
        "max_tracks": 64,
        "max_noise_level": 0.65,
        "min_asr_confidence": 0.45,
        "min_rms": 0.003,
        "max_clipping_ratio": 0.01,
    }
    policy_payload = {
        "schema_version": "siq.meeting.speaker_recluster_policy.v1",
        "version": "speaker-recluster.validated.v1",
        "final_diarizer_ref": "final-diarizer-test-v1",
        "auto_apply_validated": True,
    }
    report = {
        "schema_version": "siq.meeting.diarization-release-evaluation.v1",
        "input_schema_version": "siq.meeting.diarization-annotation.v1",
        "evidence_manifest_schema_version": "siq.meeting.diarization-release-evidence.v1",
        "scoring_policy_version": "siq.meeting.diarization-scoring.v1",
        "source_sha256": "b" * 64,
        "evidence_manifest_sha256": "c" * 64,
        "candidate": {"commit_sha": "d" * 40, "environment_profile": "release-test"},
        "policy": {
            "schema_version": "siq.meeting.speaker_recluster_policy.v1",
            "version": "speaker-recluster.validated.v1",
            "final_diarizer_ref": "final-diarizer-test-v1",
            "encoder_ref": "iic/speech_eres2netv2_sv_zh-cn_16k-common",
            "thresholds": thresholds,
        },
        "scoring_protocol": dict(DIARIZATION_REPORT_SCORING_PROTOCOL),
        "limits": dict(DIARIZATION_REPORT_LIMITS),
        "minimum_sample": dict(DIARIZATION_REPORT_MINIMUM_SAMPLE),
        "coverage": {"reference_speaker_counts_covered": list(range(2, 9))},
        "metrics": {
            "recording_count": 14,
            "reference_speaker_count": 70,
            "unique_reference_speaker_count": 70,
            "hypothesis_track_count": 70,
            "unapproved_production_or_historical_recordings": 0,
            "speaker_split_overlap_count": 0,
            "recording_split_overlap_count": 0,
            "reference_speaker_time_ms": 4_200_000,
            "hypothesis_speaker_time_ms": 4_200_000,
            "missed_speech_ms": 0,
            "false_alarm_speech_ms": 0,
            "speaker_confusion_ms": 0,
            "diarization_error_ms": 0,
            "missed_speech_rate": 0,
            "false_alarm_speech_rate": 0,
            "speaker_confusion_rate": 0,
            "diarization_error_rate": 0,
            "fragmented_reference_speakers": 0,
            "fragmentation_excess_tracks": 0,
            "fragmentation_rate": 0,
            "predicted_tracks_per_reference_histogram": {"1": 70},
            "over_merged_hypothesis_tracks": 0,
            "over_merge_excess_speakers": 0,
            "references_on_over_merged_tracks": 0,
            "over_merge_rate": 0,
            "purity_numerator_ms": 4_200_000,
            "purity_denominator_ms": 4_200_000,
            "track_purity": 1,
        },
        "gates": {key: True for key in DIARIZATION_REPORT_GATE_KEYS},
        "failures": [],
        "passed": True,
        "privacy_boundary": dict(DIARIZATION_REPORT_PRIVACY_BOUNDARY),
    }
    raw = json.dumps(report, sort_keys=True).encode()
    path = tmp_path / "diarization-release.json"
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    monkeypatch.setenv("SIQ_MEETING_SPEAKER_RECLUSTER_AUTO_APPLY_ENABLED", "1")
    monkeypatch.setenv("SIQ_MEETING_SPEAKER_RECLUSTER_VALIDATION_REPORT", str(path))
    monkeypatch.setenv("SIQ_MEETING_SPEAKER_RECLUSTER_FINAL_DIARIZER_REF", "final-diarizer-test-v1")
    policy_payload["validation_artifact_sha256"] = digest
    monkeypatch.setenv("SIQ_MEETING_SPEAKER_RECLUSTER_POLICY_JSON", json.dumps(policy_payload))
    policy = SpeakerReclusterPolicy.from_env()
    assert policy.auto_apply_validated is True
    assert policy.validation_artifact_sha256 == digest

    monkeypatch.setenv("SIQ_MEETING_SPEAKER_RECLUSTER_VALIDATION_REPORT", str(tmp_path / "missing.json"))
    with pytest.raises(ValueError, match="not a bounded regular file"):
        SpeakerReclusterPolicy.from_env()

    forged = json.dumps(
        {"schema_version": "siq.meeting.diarization-release-evaluation.v1", "passed": True},
        sort_keys=True,
    ).encode()
    path.write_bytes(forged)
    monkeypatch.setenv("SIQ_MEETING_SPEAKER_RECLUSTER_VALIDATION_REPORT", str(path))
    policy_payload["validation_artifact_sha256"] = hashlib.sha256(forged).hexdigest()
    monkeypatch.setenv("SIQ_MEETING_SPEAKER_RECLUSTER_POLICY_JSON", json.dumps(policy_payload))
    with pytest.raises(ValueError, match="not a passing diarization evaluation"):
        SpeakerReclusterPolicy.from_env()


def test_http_diarization_embedding_sends_scoped_headers_and_validates_response() -> None:
    meeting_id = str(uuid4())
    run_id = str(uuid4())
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content
        payload = {
            "schema_version": "siq.meeting.speaker_embedding.v1",
            "encoder_ref": "encoder-v1",
            "dimension": 2,
            "embedding": [1.0, 0.0],
            "duration_ms": 2_000,
            "purpose": "diarization",
            "persisted": False,
            "scope": {"meeting_id": meeting_id, "run_id": run_id},
        }
        return httpx.Response(200, json=payload)

    async def scenario() -> None:
        client = HttpDiarizationEmbeddingClient(
            endpoint="http://127.0.0.1:8901/v1/speaker/embedding",
            service_token="service-token",
            expected_encoder_ref="encoder-v1",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        result = await client.embed(b"\x00\x00" * 32_000, meeting_id=meeting_id, run_id=run_id)
        assert result.values == (1.0, 0.0)
        assert captured["headers"]["x-siq-speaker-purpose"] == "diarization"
        assert captured["headers"]["x-siq-meeting-id"] == meeting_id
        assert captured["headers"]["x-siq-diarization-run-id"] == run_id
        assert "service-token" not in captured["body"].decode("latin1")
        await client.client.aclose()

    anyio.run(scenario)
