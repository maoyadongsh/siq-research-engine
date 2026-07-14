from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "meeting" / "evaluate_diarization_release.py"
SPEC = importlib.util.spec_from_file_location("evaluate_diarization_release", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _segment(start_ms: int, end_ms: int, speaker_id: str):
    return MODULE.Segment(start_ms=start_ms, end_ms=end_ms, speaker_id=speaker_id)


def _json_annotation(recordings: dict[str, list[tuple[int, int, str]]]) -> dict:
    return {
        "schema_version": MODULE.ANNOTATION_SCHEMA_VERSION,
        "recordings": [
            {
                "recording_id": recording_id,
                "segments": [
                    {"start_ms": start_ms, "end_ms": end_ms, "speaker_id": speaker_id}
                    for start_ms, end_ms, speaker_id in segments
                ],
            }
            for recording_id, segments in recordings.items()
        ],
    }


def _policy() -> dict:
    return {
        "schema_version": MODULE.RECLUSTER_POLICY_SCHEMA_VERSION,
        "version": "speaker-recluster.validated.release-test.v1",
        "final_diarizer_ref": "final-diarizer-release-test-v1",
        "encoder_ref": "encoder-release-test-v1",
        "thresholds": {
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
        },
    }


def _manifest_payload(
    reference: dict,
    *,
    reference_raw: bytes = b"reference-annotation",
    hypothesis_raw: bytes = b"hypothesis-annotation",
) -> dict:
    speaker_counts = sorted({len({segment.speaker_id for segment in segments}) for segments in reference.values()})
    unique_speakers = {segment.speaker_id for segments in reference.values() for segment in segments}
    reference_time_ms = sum(
        segment.end_ms - segment.start_ms for segments in reference.values() for segment in segments
    )
    return {
        "schema_version": MODULE.EVIDENCE_MANIFEST_SCHEMA_VERSION,
        "authorization": {
            "approved": True,
            "all_recordings_authorized": True,
            "unapproved_production_or_historical_recordings": 0,
            "reference": "private-approval-reference",
        },
        "candidate": {"commit_sha": "a" * 40, "environment_profile": "release-test"},
        "split": {
            "kind": "independent_holdout",
            "independent_from_training": True,
            "independent_from_threshold_tuning": True,
            "speaker_overlap_count": 0,
            "recording_overlap_count": 0,
        },
        "inputs": {
            "reference_sha256": hashlib.sha256(reference_raw).hexdigest(),
            "hypothesis_sha256": hashlib.sha256(hypothesis_raw).hexdigest(),
        },
        "dataset": {
            "recording_count": len(reference),
            "unique_reference_speaker_count": len(unique_speakers),
            "reference_speaker_time_ms": reference_time_ms,
            "speaker_counts_covered": speaker_counts,
        },
        "policy": _policy(),
    }


def _evaluate(
    reference: dict,
    hypothesis: dict,
    *,
    manifest_payload: dict | None = None,
    reference_raw: bytes = b"reference-annotation",
    hypothesis_raw: bytes = b"hypothesis-annotation",
) -> dict:
    payload = manifest_payload or _manifest_payload(
        reference,
        reference_raw=reference_raw,
        hypothesis_raw=hypothesis_raw,
    )
    manifest_raw = json.dumps(payload, sort_keys=True).encode()
    manifest = MODULE.parse_evidence_manifest(manifest_raw)
    return MODULE.evaluate(
        reference,
        hypothesis,
        evidence_manifest=manifest,
        evidence_manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
        reference_sha256=hashlib.sha256(reference_raw).hexdigest(),
        hypothesis_sha256=hashlib.sha256(hypothesis_raw).hexdigest(),
        source_sha256=MODULE._source_digest(reference_raw, hypothesis_raw),
    )


def _release_sized_annotations():
    reference = {}
    hypothesis = {}
    for recording_index, speaker_count in enumerate((*range(2, 9), *range(2, 9)), start=1):
        recording_id = f"recording-{recording_index:02d}"
        reference_segments = []
        hypothesis_segments = []
        for speaker_index in range(speaker_count):
            start_ms = speaker_index * 60_000
            end_ms = start_ms + 60_000
            reference_segments.append(_segment(start_ms, end_ms, f"ref-{recording_index}-{speaker_index}"))
            hypothesis_segments.append(_segment(start_ms, end_ms, f"track-{recording_index}-{speaker_index}"))
        reference[recording_id] = tuple(reference_segments)
        hypothesis[recording_id] = tuple(hypothesis_segments)
    return reference, hypothesis


def _rttm_annotation(recordings: dict) -> str:
    rows = []
    for recording_id, segments in recordings.items():
        for segment in segments:
            rows.append(
                " ".join(
                    (
                        "SPEAKER",
                        recording_id,
                        "1",
                        f"{segment.start_ms / 1000:.3f}",
                        f"{(segment.end_ms - segment.start_ms) / 1000:.3f}",
                        "<NA>",
                        "<NA>",
                        segment.speaker_id,
                        "<NA>",
                    )
                )
            )
    return "\n".join(rows) + "\n"


def test_permuted_speaker_labels_score_perfectly_and_report_is_redacted():
    reference = {
        "private-recording-a": (
            _segment(0, 1_000, "private-person-alice"),
            _segment(1_000, 2_000, "private-person-bob"),
        )
    }
    hypothesis = {
        "private-recording-a": (
            _segment(0, 1_000, "cluster-91"),
            _segment(1_000, 2_000, "cluster-27"),
        )
    }

    report = _evaluate(reference, hypothesis)

    assert report["passed"] is False
    assert report["metrics"]["diarization_error_rate"] == 0
    assert report["metrics"]["speaker_confusion_rate"] == 0
    assert report["metrics"]["fragmentation_rate"] == 0
    assert report["metrics"]["over_merge_rate"] == 0
    assert report["metrics"]["track_purity"] == 1
    assert "reference_speaker_tracks" not in report
    assert report["metrics"]["unique_reference_speaker_count"] == 2
    assert report["gates"]["sample_recordings_at_least_14"] is False
    assert report["gates"]["sample_reference_speaker_time_at_least_1h"] is False

    serialized = json.dumps(report, sort_keys=True)
    assert "private-recording-a" not in serialized
    assert "private-person-alice" not in serialized
    assert "private-person-bob" not in serialized
    assert "cluster-91" not in serialized
    assert "cluster-27" not in serialized
    assert "private-approval-reference" not in serialized


def test_release_sized_perfect_annotations_pass_fixed_sample_and_quality_gates():
    reference, hypothesis = _release_sized_annotations()

    report = _evaluate(reference, hypothesis)

    assert report["passed"] is True
    assert set(report) == {
        "schema_version",
        "input_schema_version",
        "evidence_manifest_schema_version",
        "scoring_policy_version",
        "source_sha256",
        "evidence_manifest_sha256",
        "candidate",
        "policy",
        "scoring_protocol",
        "limits",
        "minimum_sample",
        "coverage",
        "metrics",
        "gates",
        "failures",
        "passed",
        "privacy_boundary",
    }
    assert report["minimum_sample"] == {
        "recordings": 14,
        "unique_reference_speakers": 14,
        "reference_speaker_time_ms": 3_600_000,
    }
    assert report["coverage"]["reference_speaker_counts_covered"] == list(range(2, 9))
    assert report["metrics"]["recording_count"] == 14
    assert report["metrics"]["unique_reference_speaker_count"] == 70
    assert report["metrics"]["reference_speaker_time_ms"] == 4_200_000
    assert report["candidate"] == {"commit_sha": "a" * 40, "environment_profile": "release-test"}
    assert report["policy"] == _policy()
    assert report["privacy_boundary"] == MODULE.PRIVACY_BOUNDARY
    assert report["failures"] == []
    assert all(report["gates"].values())


def test_evidence_manifest_rejects_sensitive_extra_fields_without_echoing_values():
    reference, _ = _release_sized_annotations()
    manifest = _manifest_payload(reference)
    manifest["audio_path"] = "/protected/PRIVATE-RECORDING.wav"

    with pytest.raises(MODULE.EvaluationInputError) as raised:
        MODULE.parse_evidence_manifest(json.dumps(manifest).encode())

    assert "PRIVATE-RECORDING" not in str(raised.value)


def test_authorization_and_independent_holdout_gates_fail_closed_without_echoing_reference():
    reference, hypothesis = _release_sized_annotations()
    manifest = _manifest_payload(reference)
    manifest["authorization"].update(
        {
            "approved": False,
            "all_recordings_authorized": False,
            "unapproved_production_or_historical_recordings": 1,
            "reference": "private-denied-approval-reference",
        }
    )
    manifest["split"].update(
        {
            "kind": "other",
            "independent_from_training": False,
            "independent_from_threshold_tuning": False,
            "speaker_overlap_count": 1,
            "recording_overlap_count": 1,
        }
    )

    report = _evaluate(reference, hypothesis, manifest_payload=manifest)

    assert report["passed"] is False
    for gate in (
        "authorization_approved",
        "all_recordings_authorized",
        "no_unapproved_production_or_historical_recordings",
        "independent_holdout_kind",
        "independent_from_training",
        "independent_from_threshold_tuning",
        "no_speaker_split_overlap",
        "no_recording_split_overlap",
    ):
        assert report["gates"][gate] is False
    assert "private-denied-approval-reference" not in json.dumps(report, sort_keys=True)


def test_manifest_input_hash_and_declared_dataset_mismatches_fail_closed():
    reference, hypothesis = _release_sized_annotations()
    manifest = _manifest_payload(reference)
    manifest["inputs"]["reference_sha256"] = "f" * 64
    manifest["dataset"]["recording_count"] -= 1
    manifest["dataset"]["unique_reference_speaker_count"] -= 1
    manifest["dataset"]["reference_speaker_time_ms"] -= 1
    manifest["dataset"]["speaker_counts_covered"] = [2, 3]

    report = _evaluate(reference, hypothesis, manifest_payload=manifest)

    assert report["passed"] is False
    for gate in (
        "reference_annotation_sha256_matches_manifest",
        "manifest_recording_count_matches",
        "manifest_unique_reference_speaker_count_matches",
        "manifest_reference_speaker_time_matches",
        "manifest_speaker_counts_match",
    ):
        assert report["gates"][gate] is False


def test_empty_reference_recording_is_rejected_instead_of_counting_toward_sample():
    reference = {
        "recording-1": (_segment(0, 1_000, "ref-a"),),
        "recording-empty": (),
    }
    hypothesis = {"recording-1": (_segment(0, 1_000, "track-a"),)}
    manifest = _manifest_payload(reference)
    manifest["dataset"]["speaker_counts_covered"] = [1]

    with pytest.raises(MODULE.EvaluationInputError, match="every reference recording"):
        _evaluate(reference, hypothesis, manifest_payload=manifest)


def test_der_components_and_fragmentation_are_exact():
    reference = (
        _segment(0, 1_000, "ref-a"),
        _segment(1_000, 2_000, "ref-b"),
    )
    hypothesis = (
        _segment(0, 500, "track-x"),
        _segment(500, 1_000, "track-y"),
        _segment(1_000, 1_800, "track-z"),
        _segment(2_000, 2_200, "track-fa"),
    )

    report = _evaluate({"recording-1": reference}, {"recording-1": hypothesis})

    assert report["metrics"]["reference_speaker_time_ms"] == 2_000
    assert report["metrics"]["missed_speech_ms"] == 200
    assert report["metrics"]["false_alarm_speech_ms"] == 200
    assert report["metrics"]["speaker_confusion_ms"] == 500
    assert report["metrics"]["diarization_error_ms"] == 900
    assert report["metrics"]["diarization_error_rate"] == 0.45
    assert report["metrics"]["fragmented_reference_speakers"] == 1
    assert report["metrics"]["fragmentation_excess_tracks"] == 1
    assert report["metrics"]["fragmentation_rate"] == 0.5
    assert report["metrics"]["predicted_tracks_per_reference_histogram"] == {"1": 1, "2": 1}
    assert report["metrics"]["over_merge_rate"] == 0
    assert report["metrics"]["track_purity"] == 1
    assert report["gates"]["diarization_error_rate_at_most_15_percent"] is False
    assert report["gates"]["fragmentation_rate_at_most_25_percent"] is False


def test_one_hypothesis_track_for_two_people_is_counted_as_over_merge():
    reference = {
        "recording-1": (
            _segment(0, 1_000, "ref-a"),
            _segment(1_000, 2_000, "ref-b"),
        )
    }
    hypothesis = {"recording-1": (_segment(0, 2_000, "merged-track"),)}

    report = _evaluate(reference, hypothesis)

    assert report["metrics"]["over_merged_hypothesis_tracks"] == 1
    assert report["metrics"]["over_merge_excess_speakers"] == 1
    assert report["metrics"]["references_on_over_merged_tracks"] == 2
    assert report["metrics"]["over_merge_rate"] == 1
    assert report["metrics"]["track_purity"] == 0.5
    assert report["metrics"]["speaker_confusion_ms"] == 1_000
    assert report["gates"]["over_merge_rate_at_most_5_percent"] is False
    assert report["gates"]["track_purity_at_least_90_percent"] is False


def test_overlap_speaker_time_is_scored_instead_of_dropped():
    reference = {
        "recording-overlap": (
            _segment(0, 2_000, "ref-a"),
            _segment(1_000, 3_000, "ref-b"),
        )
    }
    hypothesis = {
        "recording-overlap": (
            _segment(0, 2_000, "track-a"),
            _segment(1_000, 3_000, "track-b"),
        )
    }

    report = _evaluate(reference, hypothesis)

    assert report["metrics"]["reference_speaker_time_ms"] == 4_000
    assert report["metrics"]["hypothesis_speaker_time_ms"] == 4_000
    assert report["metrics"]["diarization_error_rate"] == 0
    assert report["scoring_protocol"]["overlap_scored"] is True


def test_rttm_and_json_parsers_produce_equivalent_segments():
    rttm = b"\n".join(
        (
            b"SPEAKER meeting-1 1 0.000 1.250 <NA> <NA> speaker-a <NA>",
            b"SPEAKER meeting-1 1 1.250 0.750 <NA> <NA> speaker-b <NA>",
        )
    )
    payload = _json_annotation({"meeting-1": [(0, 1_250, "speaker-a"), (1_250, 2_000, "speaker-b")]})

    parsed_rttm = MODULE.parse_rttm_annotation(rttm)
    parsed_json = MODULE.parse_json_annotation(json.dumps(payload).encode())

    assert parsed_rttm == parsed_json


def test_missing_hypothesis_recording_is_all_missed_speech():
    reference = {"recording-1": (_segment(0, 1_000, "ref-a"),)}

    report = _evaluate(reference, {})

    assert report["metrics"]["missed_speech_ms"] == 1_000
    assert report["metrics"]["diarization_error_rate"] == 1
    assert report["metrics"]["predicted_tracks_per_reference_histogram"] == {"0": 1}
    assert report["metrics"]["track_purity"] is None
    assert report["passed"] is False


@pytest.mark.parametrize(
    "segments",
    [
        [(0, 1_000, "same"), (900, 1_100, "same")],
        [(1_000, 1_000, "same")],
    ],
)
def test_invalid_same_speaker_intervals_are_rejected(segments):
    payload = _json_annotation({"meeting-1": segments})

    with pytest.raises(MODULE.EvaluationInputError):
        MODULE.parse_json_annotation(json.dumps(payload).encode())


def test_cli_rejects_sensitive_extra_fields_without_echoing_values(tmp_path):
    logical_reference = {"meeting-1": (_segment(0, 1_000, "ref-a"),)}
    reference_payload = _json_annotation({"meeting-1": [(0, 1_000, "ref-a")]})
    reference_payload["audio_path"] = "/protected/PRIVATE-MEETING.wav"
    reference_payload["transcript"] = "PRIVATE-TRANSCRIPT-TEXT"
    reference_payload["embedding"] = ["PRIVATE-EMBEDDING"]
    reference_path = tmp_path / "private-reference.json"
    hypothesis_path = tmp_path / "hypothesis.json"
    manifest_path = tmp_path / "evidence-manifest.json"
    output_path = tmp_path / "report.json"
    reference_raw = json.dumps(reference_payload).encode()
    hypothesis_raw = json.dumps(_json_annotation({"meeting-1": [(0, 1_000, "track-a")]})).encode()
    reference_path.write_bytes(reference_raw)
    hypothesis_path.write_bytes(hypothesis_raw)
    manifest_path.write_text(
        json.dumps(
            _manifest_payload(
                logical_reference,
                reference_raw=reference_raw,
                hypothesis_raw=hypothesis_raw,
            )
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--evidence-manifest",
            str(manifest_path),
            "--reference",
            str(reference_path),
            "--hypothesis",
            str(hypothesis_path),
            "--output",
            str(output_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert not output_path.exists()
    diagnostic = result.stdout + result.stderr
    assert "/protected/PRIVATE-MEETING.wav" not in diagnostic
    assert "PRIVATE-TRANSCRIPT-TEXT" not in diagnostic
    assert "PRIVATE-EMBEDDING" not in diagnostic
    assert "private-reference.json" not in diagnostic
    assert "private-approval-reference" not in diagnostic


def test_cli_supports_rttm_and_require_passing_exit_status(tmp_path):
    reference_path = tmp_path / "reference.rttm"
    passing_path = tmp_path / "passing.rttm"
    failing_path = tmp_path / "failing.rttm"
    passing_manifest_path = tmp_path / "passing-manifest.json"
    failing_manifest_path = tmp_path / "failing-manifest.json"
    passing_report = tmp_path / "passing-report.json"
    failing_report = tmp_path / "failing-report.json"
    reference, hypothesis = _release_sized_annotations()
    reference_raw = _rttm_annotation(reference).encode()
    passing_raw = _rttm_annotation(hypothesis).encode()
    failing_raw = b""
    reference_path.write_bytes(reference_raw)
    passing_path.write_bytes(passing_raw)
    failing_path.write_bytes(failing_raw)
    passing_manifest_path.write_text(
        json.dumps(
            _manifest_payload(
                reference,
                reference_raw=reference_raw,
                hypothesis_raw=passing_raw,
            )
        ),
        encoding="utf-8",
    )
    failing_manifest_path.write_text(
        json.dumps(
            _manifest_payload(
                reference,
                reference_raw=reference_raw,
                hypothesis_raw=failing_raw,
            )
        ),
        encoding="utf-8",
    )

    passing = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--evidence-manifest",
            str(passing_manifest_path),
            "--reference",
            str(reference_path),
            "--hypothesis",
            str(passing_path),
            "--output",
            str(passing_report),
            "--require-passing",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    failing = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--evidence-manifest",
            str(failing_manifest_path),
            "--reference",
            str(reference_path),
            "--hypothesis",
            str(failing_path),
            "--output",
            str(failing_report),
            "--require-passing",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert passing.returncode == 0
    assert json.loads(passing_report.read_text())["passed"] is True
    assert failing.returncode == 1
    failed_payload = json.loads(failing_report.read_text())
    assert failed_payload["passed"] is False
    assert "private-meeting" not in failing_report.read_text()
    assert "private-ref" not in failing_report.read_text()


def test_hypothesis_recording_not_present_in_reference_is_rejected():
    with pytest.raises(MODULE.EvaluationInputError, match="absent from the reference"):
        _evaluate(
            {"recording-1": (_segment(0, 1_000, "ref-a"),)},
            {"recording-2": (_segment(0, 1_000, "track-a"),)},
        )
