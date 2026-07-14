from __future__ import annotations

from uuid import uuid4

import numpy as np
from fastapi.testclient import TestClient
from meeting_speech_service.app import create_app
from meeting_speech_service.config import Settings
from meeting_speech_service.protocol import AudioFlags, AudioFrame, encode_audio_frame

MEETING_ID = uuid4()
CLIENT_STREAM_ID = uuid4()
TOKEN = "test-internal-token"


def _settings(**overrides) -> Settings:
    values = {
        "enabled": True,
        "adapter": "mock",
        "allow_degraded_mock": True,
        "internal_service_token": TOKEN,
        "speaker_adapter": "none",
        "vad_min_speech_ms": 100,
        "vad_endpoint_silence_ms": 200,
        "rate_burst_seconds": 10,
        "resume_ttl_seconds": 60,
    }
    values.update(overrides)
    return Settings(**values, _env_file=None)


def _start(last_acked_sequence: int = -1, *, stream_epoch: int = 1) -> dict[str, object]:
    return {
        "type": "stream.start",
        "schema_version": "siq.meeting.stream.v1",
        "meeting_id": str(MEETING_ID),
        "client_stream_id": str(CLIENT_STREAM_ID),
        "stream_epoch": stream_epoch,
        "audio": {"encoding": "pcm_s16le", "sample_rate": 16_000, "channels": 1, "chunk_ms": 200},
        "last_acked_sequence": last_acked_sequence,
    }


def _pcm(amplitude: int, duration_ms: int = 200) -> bytes:
    return np.full(16_000 * duration_ms // 1_000, amplitude, dtype="<i2").tobytes()


def _frame(
    sequence: int,
    amplitude: int,
    *,
    flags: AudioFlags = AudioFlags.NONE,
    capture_time_ms: int | None = None,
    stream_epoch: int = 1,
) -> bytes:
    return encode_audio_frame(
        AudioFrame(
            stream_epoch=stream_epoch,
            sequence=sequence,
            capture_time_ms=sequence * 200 if capture_time_ms is None else capture_time_ms,
            flags=flags,
            payload=_pcm(amplitude),
        )
    )


def _receive_through_ack(websocket) -> list[dict[str, object]]:
    events = []
    while True:
        event = websocket.receive_json()
        events.append(event)
        if event["type"] == "audio.ack":
            return events


def test_health_exposes_explicit_mock_degradation() -> None:
    with TestClient(create_app(_settings())) as client:
        response = client.get("/health/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "degraded"
        assert body["core_ready"] is True
        assert body["production_capable"] is False
        assert body["reason_code"] == "EXPLICIT_MOCK_ADAPTER"
        assert body["diarizer_ref"].startswith("siq.meeting.diarizer.v1/none/")


def test_websocket_emits_partial_final_ack_and_idempotent_duplicate() -> None:
    with TestClient(create_app(_settings())) as client:
        with client.websocket_connect(f"/v1/stream/{MEETING_ID}", headers={"x-siq-service-token": TOKEN}) as websocket:
            websocket.send_json(_start())
            assert websocket.receive_json()["type"] == "stream.ready"
            assert websocket.receive_json()["type"] == "pipeline.degraded"

            websocket.send_bytes(_frame(0, 12_000))
            first = _receive_through_ack(websocket)
            assert [event["type"] for event in first] == ["asr.partial", "audio.ack"]
            assert first[-1]["payload"]["ack_sequence"] == 0

            websocket.send_bytes(_frame(1, 0))
            second = _receive_through_ack(websocket)
            assert [event["type"] for event in second] == ["asr.partial", "asr.final", "audio.ack"]
            final = next(event for event in second if event["type"] == "asr.final")
            assert final["payload"]["durability"] == "gateway_pending"
            assert final["payload"]["text"] == "[mock speech]"
            assert final["payload"]["speaker_track_key"] is None
            assert final["payload"]["source_speaker_hints"] == []

            websocket.send_bytes(_frame(1, 0))
            duplicate = _receive_through_ack(websocket)
            assert [event["type"] for event in duplicate] == ["audio.ack"]
            assert duplicate[0]["payload"]["duplicate"] is True

            websocket.send_json({"type": "stream.stop", "schema_version": "siq.meeting.stream.v1"})
            assert websocket.receive_json()["type"] == "stream.stopped"


def test_speaker_track_keys_are_isolated_between_stream_epochs() -> None:
    settings = _settings(speaker_adapter="mock")

    def final_track(client: TestClient, epoch: int) -> str:
        with client.websocket_connect(
            f"/v1/stream/{MEETING_ID}",
            headers={"x-siq-service-token": TOKEN},
        ) as websocket:
            websocket.send_json(_start(stream_epoch=epoch))
            assert websocket.receive_json()["type"] == "stream.ready"
            assert websocket.receive_json()["type"] == "pipeline.degraded"
            websocket.send_bytes(_frame(0, 12_000, stream_epoch=epoch))
            _receive_through_ack(websocket)
            websocket.send_bytes(_frame(1, 0, stream_epoch=epoch))
            events = _receive_through_ack(websocket)
            final = next(event for event in events if event["type"] == "asr.final")
            websocket.send_json({"type": "stream.stop", "schema_version": "siq.meeting.stream.v1"})
            assert websocket.receive_json()["type"] == "stream.stopped"
            return str(final["payload"]["speaker_track_key"])

    with TestClient(create_app(settings)) as client:
        assert final_track(client, 1) == "epoch-1:mock-speaker-0"
        assert final_track(client, 2) == "epoch-2:mock-speaker-0"
        metrics = client.get("/metrics").text
        assert 'meeting_speech_speaker_assignment_total{result="assigned"} 2' in metrics
        assert 'meeting_speech_speaker_track_total{result="created"} 2' in metrics
        assert 'meeting_speech_speaker_track_total{result="reused"} 0' in metrics


def test_gap_is_reported_and_reordered_frames_advance_ack() -> None:
    with TestClient(create_app(_settings())) as client:
        with client.websocket_connect(f"/v1/stream/{MEETING_ID}", headers={"x-siq-service-token": TOKEN}) as websocket:
            websocket.send_json(_start())
            websocket.receive_json()
            websocket.receive_json()

            websocket.send_bytes(_frame(1, 0))
            gap_events = _receive_through_ack(websocket)
            assert [event["type"] for event in gap_events] == ["audio.gap.detected", "audio.ack"]
            assert gap_events[-1]["payload"]["ack_sequence"] == -1

            websocket.send_bytes(_frame(0, 0))
            drained = _receive_through_ack(websocket)
            assert drained[-1]["payload"]["ack_sequence"] == 1


def test_overlapping_capture_timestamp_returns_stable_protocol_error() -> None:
    with TestClient(create_app(_settings())) as client:
        with client.websocket_connect(
            f"/v1/stream/{MEETING_ID}",
            headers={"x-siq-service-token": TOKEN},
        ) as websocket:
            websocket.send_json(_start())
            websocket.receive_json()
            websocket.receive_json()

            websocket.send_bytes(_frame(0, 0, capture_time_ms=481))
            assert _receive_through_ack(websocket)[-1]["payload"]["ack_sequence"] == 0

            websocket.send_bytes(_frame(1, 0, capture_time_ms=680))
            error = websocket.receive_json()
            assert error["type"] == "error"
            assert error["payload"] == {
                "scope": "asr",
                "code": "AUDIO_CAPTURE_TIME_REGRESSION",
                "message": "audio capture timestamps must be monotonic and non-overlapping",
                "retryable": False,
            }


def test_disconnected_session_can_resume_with_retained_ack() -> None:
    with TestClient(create_app(_settings())) as client:
        with client.websocket_connect(f"/v1/stream/{MEETING_ID}", headers={"x-siq-service-token": TOKEN}) as websocket:
            websocket.send_json(_start())
            websocket.receive_json()
            websocket.receive_json()
            websocket.send_bytes(_frame(0, 0))
            assert _receive_through_ack(websocket)[-1]["payload"]["ack_sequence"] == 0

        with client.websocket_connect(f"/v1/stream/{MEETING_ID}", headers={"x-siq-service-token": TOKEN}) as resumed:
            resumed.send_json(_start(last_acked_sequence=0))
            ready = resumed.receive_json()
            assert ready["type"] == "stream.ready"
            assert ready["payload"]["resumed"] is True
            assert ready["payload"]["ack_sequence"] == 0
            assert resumed.receive_json()["type"] == "pipeline.degraded"
            resumed.send_json({"type": "stream.stop", "schema_version": "siq.meeting.stream.v1"})
            assert resumed.receive_json()["type"] == "stream.stopped"


def test_metrics_do_not_include_meeting_or_user_identifiers() -> None:
    application = create_app(_settings())
    with TestClient(application) as client:
        application.state.runtime.metrics.record_speaker_assignment(str(MEETING_ID), "created")
        application.state.runtime.metrics.record_speaker_assignment("assigned", str(MEETING_ID))
        body = client.get("/metrics").text
        assert "meeting_speech_active_sessions" in body
        assert "meeting_speech_speaker_assignment_total" in body
        assert "meeting_speech_speaker_track_total" in body
        assert str(MEETING_ID) not in body
        assert "user_id" not in body


def test_embedding_endpoint_requires_internal_token_consent_and_purpose() -> None:
    settings = _settings(embedding_endpoint_enabled=True)
    pcm = _pcm(0, duration_ms=1_000)
    consent_id = uuid4()
    headers = {
        "x-siq-service-token": TOKEN,
        "x-siq-voiceprint-consent": str(consent_id),
        "x-siq-voiceprint-purpose": "enrollment",
        "x-siq-audio-encoding": "pcm_s16le",
        "content-type": "application/octet-stream",
    }
    with TestClient(create_app(settings)) as client:
        assert client.post("/v1/speaker/embedding", content=pcm).status_code == 401
        missing_consent = dict(headers)
        missing_consent.pop("x-siq-voiceprint-consent")
        assert client.post("/v1/speaker/embedding", content=pcm, headers=missing_consent).status_code == 400

        response = client.post("/v1/speaker/embedding", content=pcm, headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["schema_version"] == "siq.meeting.speaker_embedding.v1"
        assert body["encoder_ref"] == "mock"
        assert body["dimension"] == 2
        assert body["persisted"] is False


def test_embedding_endpoint_supports_isolated_diarization_scope_without_voiceprint_consent() -> None:
    settings = _settings(embedding_endpoint_enabled=True)
    meeting_id = uuid4()
    run_id = uuid4()
    headers = {
        "x-siq-service-token": TOKEN,
        "x-siq-speaker-purpose": "diarization",
        "x-siq-meeting-id": str(meeting_id),
        "x-siq-diarization-run-id": str(run_id),
        "x-siq-audio-encoding": "pcm_s16le",
        "content-type": "application/octet-stream",
    }
    with TestClient(create_app(settings)) as client:
        response = client.post("/v1/speaker/embedding", content=_pcm(1, duration_ms=1_000), headers=headers)

        assert response.status_code == 200
        body = response.json()
        assert body == {
            "schema_version": "siq.meeting.speaker_embedding.v1",
            "encoder_ref": "mock",
            "dimension": 2,
            "embedding": [1.0, 0.0],
            "duration_ms": 1_000,
            "purpose": "diarization",
            "persisted": False,
            "scope": {"meeting_id": str(meeting_id), "run_id": str(run_id)},
        }

        missing_scope = dict(headers)
        missing_scope.pop("x-siq-diarization-run-id")
        invalid = client.post(
            "/v1/speaker/embedding",
            content=_pcm(1, duration_ms=1_000),
            headers=missing_scope,
        )
        assert invalid.status_code == 400
        assert invalid.json()["code"] == "DIARIZATION_SCOPE_INVALID"

        mixed_scope = {**headers, "x-siq-voiceprint-consent": str(uuid4())}
        mixed = client.post(
            "/v1/speaker/embedding",
            content=_pcm(1, duration_ms=1_000),
            headers=mixed_scope,
        )
        assert mixed.status_code == 400
        assert mixed.json()["code"] == "EMBEDDING_SCOPE_CONFLICT"


def test_finalize_window_is_bounded_authenticated_and_idempotent() -> None:
    settings = _settings(speaker_adapter="mock", finalization_max_window_seconds=2)
    run_id = uuid4()

    def headers(index: int, start_ms: int, *, final: bool = False) -> dict[str, str]:
        return {
            "x-siq-service-token": TOKEN,
            "x-siq-finalization-id": str(run_id),
            "x-siq-window-index": str(index),
            "x-siq-window-start-ms": str(start_ms),
            "x-siq-final-window": "true" if final else "false",
            "x-siq-discontinuity": "false",
            "x-siq-language": "zh-CN",
            "x-siq-hotwords": '["Nemotron"]',
            "content-type": "application/octet-stream",
        }

    with TestClient(create_app(settings)) as client:
        assert client.post("/v1/finalize-window", content=_pcm(12_000)).status_code == 401

        first = client.post(
            "/v1/finalize-window",
            content=_pcm(12_000),
            headers=headers(0, 0),
        )
        assert first.status_code == 200
        diarizer_ref = first.json()["diarizer_ref"]
        assert diarizer_ref.startswith("siq.meeting.diarizer.v1/mock/")
        assert len(diarizer_ref) < 192
        assert first.json()["segments"] == []

        second = client.post(
            "/v1/finalize-window",
            content=_pcm(0),
            headers=headers(1, 200, final=True),
        )
        assert second.status_code == 200
        body = second.json()
        assert body["schema_version"] == "siq.meeting.final_asr_window.v1"
        assert body["diarizer_ref"] == diarizer_ref
        assert body["segments"][0]["text"] == "[mock speech]"
        assert body["segments"][0]["speaker_track_key"] == f"finalization-{run_id}:mock-speaker-0"
        assert "embedding" not in second.text

        replay = client.post(
            "/v1/finalize-window",
            content=_pcm(0),
            headers=headers(1, 200, final=True),
        )
        assert replay.status_code == 200
        assert replay.json() == body

        missing_state_headers = headers(1, 200, final=True)
        missing_state_headers["x-siq-finalization-id"] = str(uuid4())
        missing_state = client.post(
            "/v1/finalize-window",
            content=_pcm(0),
            headers=missing_state_headers,
        )
        assert missing_state.status_code == 409
        assert missing_state.json()["code"] == "FINALIZATION_STATE_NOT_FOUND"

        too_large = client.post(
            "/v1/finalize-window",
            content=_pcm(1, duration_ms=2_200),
            headers={**headers(0, 0), "x-siq-finalization-id": str(uuid4())},
        )
        assert too_large.status_code == 400
        assert too_large.json()["code"] == "FINALIZATION_WINDOW_TOO_LARGE"

    with TestClient(create_app(settings)) as second_client:
        repeated = second_client.post(
            "/v1/finalize-window",
            content=_pcm(12_000),
            headers=headers(0, 0),
        )
        assert repeated.status_code == 200
        assert repeated.json()["diarizer_ref"] == diarizer_ref
